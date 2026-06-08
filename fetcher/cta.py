"""CTA Train Tracker fetcher (Chicago "L").

API docs: https://www.transitchicago.com/developers/traintracker/
Endpoint returns XML. Requires a free API key.

    http://lapi.transitchicago.com/api/1.0/ttarrivals.aspx?key=KEY&mapid=40380&max=5

``stop_id`` in config is either a station "map id" (5 digits, starts with 4,
covers the whole station / both directions) or a platform-level "stop id"
(starts with 3). We auto-detect which query parameter to use.
"""

from __future__ import annotations

from datetime import datetime
from xml.etree import ElementTree

import requests

from .base import BaseFetcher, Departure, StopMatch

ENDPOINT = "http://lapi.transitchicago.com/api/1.0/ttarrivals.aspx"
# City of Chicago open dataset: list of all 'L' stops with coordinates.
STOPS_DATASET = "https://data.cityofchicago.org/resource/8pix-ypme.json"
_STOPS_CACHE: list[dict] = []

# dataset boolean columns -> friendly line name (for the result detail)
_LINE_COLS = {"red": "Red", "blue": "Blue", "brn": "Brown", "g": "Green",
              "org": "Orange", "p": "Purple", "pnk": "Pink", "y": "Yellow"}

# CTA route code -> approximate brand color (hex). Used for the route badge.
ROUTE_COLORS = {
    "Red": "#C60C30",
    "Blue": "#00A1DE",
    "Brn": "#62361B",
    "G": "#009B3A",
    "Org": "#F9461C",
    "P": "#522398",
    "Pink": "#E27EA6",
    "Y": "#F9E300",
}
# Friendlier display names for the route badge.
ROUTE_NAMES = {
    "Red": "Red", "Blue": "Blue", "Brn": "Brown", "G": "Green",
    "Org": "Orange", "P": "Purple", "Pink": "Pink", "Y": "Yellow",
}

_TIME_FMT = "%Y%m%d %H:%M:%S"


class CTAFetcher(BaseFetcher):
    feed_type = "cta"
    supports_stop_search = True

    @classmethod
    def find_stops(cls, lat: float, lon: float, limit: int = 8,
                   api_key: str = "") -> list[StopMatch]:
        from geocode import haversine_mi

        rows = cls._load_stops()
        # collapse platform rows to one entry per station (map_id)
        stations: dict[str, dict] = {}
        for row in rows:
            map_id = str(row.get("map_id", "")).strip()
            loc = row.get("location") or {}
            slat = loc.get("latitude")
            slon = loc.get("longitude")
            if not map_id or slat is None or slon is None:
                continue
            lines = [name for col, name in _LINE_COLS.items() if str(row.get(col)).lower() == "true"]
            st = stations.setdefault(map_id, {
                "id": map_id,
                "name": row.get("station_name") or row.get("stop_name") or map_id,
                "lat": float(slat), "lon": float(slon), "lines": set(),
            })
            st["lines"].update(lines)

        scored = []
        for st in stations.values():
            dist = haversine_mi(lat, lon, st["lat"], st["lon"])
            scored.append((dist, st))
        scored.sort(key=lambda t: t[0])

        out: list[StopMatch] = []
        for dist, st in scored[:limit]:
            lines = ", ".join(sorted(st["lines"])) if st["lines"] else ""
            detail = f"{dist:.1f} mi" + (f" · {lines}" if lines else "")
            out.append(StopMatch(id=st["id"], name=st["name"], detail=detail))
        return out

    @classmethod
    def _load_stops(cls) -> list[dict]:
        global _STOPS_CACHE
        if not _STOPS_CACHE:
            resp = requests.get(STOPS_DATASET, params={"$limit": 2000}, timeout=15)
            resp.raise_for_status()
            _STOPS_CACHE = resp.json()
        return _STOPS_CACHE

    def _params(self) -> dict:
        # request extra so we have enough to merge across stops before trimming
        params = {"key": self.api_key, "max": max(20, self.limit * 4), "outputType": "XML"}
        mapids, stpids = [], []
        for sid in self.stop_ids or [self.stop_id]:
            if len(sid) == 5 and sid.startswith("4"):
                mapids.append(sid)   # station map id (both directions)
            elif sid:
                stpids.append(sid)   # platform-level stop id
        if mapids:
            params["mapid"] = mapids  # requests repeats the param: mapid=a&mapid=b
        if stpids:
            params["stpid"] = stpids
        return params

    def fetch(self) -> list[Departure]:
        if not self.api_key:
            raise ValueError("CTA feed requires an api_key")
        if not self.stop_id:
            raise ValueError("CTA feed requires a stop_id (mapid or stpid)")

        resp = requests.get(ENDPOINT, params=self._params(), timeout=self.timeout)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)

        err_code = (root.findtext("errCd") or "0").strip()
        if err_code != "0":
            raise RuntimeError(
                f"CTA API error {err_code}: {root.findtext('errNm') or 'unknown'}"
            )

        departures: list[Departure] = []
        for eta in root.findall("eta"):
            rt = (eta.findtext("rt") or "").strip()
            dest = (eta.findtext("destNm") or "").strip()
            sta = (eta.findtext("staNm") or "").strip()
            is_approaching = (eta.findtext("isApp") or "0").strip() == "1"
            is_delayed = (eta.findtext("isDly") or "0").strip() == "1"

            minutes = self._minutes(eta)
            if minutes is None:
                continue
            if is_approaching:
                minutes = 0

            departures.append(
                Departure(
                    route=ROUTE_NAMES.get(rt, rt),
                    destination=dest,
                    minutes=minutes,
                    color=ROUTE_COLORS.get(rt),
                    delayed=is_delayed,
                    stop_name=sta,
                )
            )
        return departures

    @staticmethod
    def _minutes(eta: ElementTree.Element) -> int | None:
        """Whole minutes from prediction-generated time to arrival time."""
        arr_raw = eta.findtext("arrT")
        prd_raw = eta.findtext("prdt")
        if not arr_raw or not prd_raw:
            return None
        try:
            arr = datetime.strptime(arr_raw.strip(), _TIME_FMT)
            prd = datetime.strptime(prd_raw.strip(), _TIME_FMT)
        except ValueError:
            return None
        delta = (arr - prd).total_seconds()
        return max(0, round(delta / 60))
