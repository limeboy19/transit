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
# CTA Bus Tracker (separate API + separate key from Train Tracker).
BUS_ENDPOINT = "https://www.ctabustracker.com/bustime/api/v2/getpredictions"
CTA_BUS_COLOR = "#2E6E8E"
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
                   api_key: str = "", mode: str = "", query: str = "") -> list[StopMatch]:
        if mode == "bus":
            return []  # CTA integration is trains ('L') only
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

    @staticmethod
    def _classify(raw: str) -> tuple[str, str]:
        """('train'|'bus', stop_id). Train ids are 5 digits starting 3/4 (L
        platform/station). Anything else is a bus stop. 'bus:'/'train:' prefixes
        force the choice if the heuristic ever guesses wrong."""
        s = raw.strip()
        low = s.lower()
        if low.startswith("bus:"):
            return "bus", s[4:].strip()
        if low.startswith("train:"):
            return "train", s[6:].strip()
        return ("train" if (len(s) == 5 and s[0] in "34") else "bus"), s

    def fetch(self) -> list[Departure]:
        if not self.stop_ids:
            raise ValueError("CTA feed requires a stop_id (L station or bus stop)")
        train_ids, bus_ids = [], []
        for raw in self.stop_ids:
            kind, sid = self._classify(raw)
            (train_ids if kind == "train" else bus_ids).append(sid)

        departures: list[Departure] = []
        if train_ids:
            if not self.api_key:
                raise ValueError("CTA L trains need an api_key (Train Tracker)")
            departures += self._fetch_trains(train_ids)
        if bus_ids:
            bus_key = str(self.config.get("bus_key", "")).strip()
            if not bus_key:
                raise ValueError("CTA bus stops need a 'bus_key' (CTA Bus Tracker API key)")
            departures += self._fetch_buses(bus_ids, bus_key)
        return departures

    def _params(self, ids: list[str]) -> dict:
        params = {"key": self.api_key, "max": max(20, self.limit * 4), "outputType": "XML"}
        mapids, stpids = [], []
        for sid in ids:
            if len(sid) == 5 and sid.startswith("4"):
                mapids.append(sid)   # station map id (both directions)
            elif sid:
                stpids.append(sid)   # platform-level stop id
        if mapids:
            params["mapid"] = mapids
        if stpids:
            params["stpid"] = stpids
        return params

    def _fetch_trains(self, train_ids: list[str]) -> list[Departure]:
        resp = requests.get(ENDPOINT, params=self._params(train_ids), timeout=self.timeout)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        err_code = (root.findtext("errCd") or "0").strip()
        if err_code != "0":
            raise RuntimeError(f"CTA API error {err_code}: {root.findtext('errNm') or 'unknown'}")

        out: list[Departure] = []
        for eta in root.findall("eta"):
            rt = (eta.findtext("rt") or "").strip()
            minutes = self._minutes(eta)
            if minutes is None:
                continue
            if (eta.findtext("isApp") or "0").strip() == "1":
                minutes = 0
            out.append(Departure(
                route=ROUTE_NAMES.get(rt, rt),
                destination=(eta.findtext("destNm") or "").strip(),
                minutes=minutes,
                color=ROUTE_COLORS.get(rt),
                delayed=(eta.findtext("isDly") or "0").strip() == "1",
                mode="train",
                stop_name=(eta.findtext("staNm") or "").strip(),
            ))
        return out

    def _fetch_buses(self, bus_ids: list[str], bus_key: str) -> list[Departure]:
        resp = requests.get(BUS_ENDPOINT, timeout=self.timeout, params={
            "key": bus_key, "stpid": ",".join(bus_ids), "format": "json",
        })
        resp.raise_for_status()
        data = resp.json().get("bustime-response", {}) or {}
        out: list[Departure] = []
        for prd in data.get("prd", []) or []:
            cd = str(prd.get("prdctdn", "")).strip().upper()
            delayed = cd == "DLY"
            if cd in ("DUE", "DLY"):
                minutes = 0
            else:
                try:
                    minutes = int(cd)
                except ValueError:
                    continue
            out.append(Departure(
                route=str(prd.get("rt", "")).strip(),
                destination=str(prd.get("des", "")).strip(),
                minutes=minutes,
                color=CTA_BUS_COLOR,
                delayed=delayed,
                mode="bus",
                stop_name=str(prd.get("stpnm", "")).strip(),
            ))
        # only surface an error if we got nothing usable
        if not out and data.get("error"):
            raise RuntimeError("CTA Bus: " + "; ".join(
                e.get("msg", "") for e in data["error"]))
        return out

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
