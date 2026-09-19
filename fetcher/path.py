"""PATH (Port Authority Trans-Hudson) fetcher.

PATH real-time arrivals come from the Port Authority's own RidePATH feed (the
same data the community "razza" API re-hosts, but straight from the source so we
don't depend on a third-party server staying up):

    https://www.panynj.gov/bin/portauthority/ridepath.json

One request returns every station's upcoming trains, so we fetch once (cached a
few seconds) and filter. No key needed.

``stop_id`` is a PATH station code: NWK, HAR, JSQ, GRV (Grove St), NEW (Newport),
EXP (Exchange Pl), HOB, WTC, CHR, 09S, 14S, 23S, 33S. Comma-separated for several.
Use the admin "Find stop" (by ZIP/address) to look them up.
"""

from __future__ import annotations

import re
import time

import requests

from .base import BaseFetcher, Departure, StopMatch

FEED_URL = "https://www.panynj.gov/bin/portauthority/ridepath.json"
_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 20  # seconds (the feed refreshes ~every 20-30s)

STATION_NAMES = {
    "NWK": "Newark", "HAR": "Harrison", "JSQ": "Journal Square",
    "GRV": "Grove Street", "NEW": "Newport", "EXP": "Exchange Place",
    "HOB": "Hoboken", "WTC": "World Trade Center", "CHR": "Christopher St",
    "09S": "9th Street", "14S": "14th Street", "23S": "23rd Street",
    "33S": "33rd Street",
}
# approximate station coordinates for the admin "Find stop" search
STATION_COORDS = {
    "NWK": (40.7343, -74.1636), "HAR": (40.7394, -74.1557),
    "JSQ": (40.7324, -74.0625), "GRV": (40.7196, -74.0424),
    "NEW": (40.7267, -74.0338), "EXP": (40.7166, -74.0329),
    "HOB": (40.7343, -74.0279), "WTC": (40.7126, -74.0099),
    "CHR": (40.7331, -74.0071), "09S": (40.7343, -73.9998),
    "14S": (40.7373, -73.9968), "23S": (40.7429, -73.9926),
    "33S": (40.7485, -73.9880),
}

_MIN_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)


def _minutes(m: dict) -> int | None:
    secs = m.get("secondsToArrival")
    try:
        return max(0, round(int(secs) / 60))
    except (ValueError, TypeError):
        pass
    txt = str(m.get("arrivalTimeMessage") or "").strip()
    hit = _MIN_RE.search(txt)
    if hit:
        return int(hit.group(1))
    if re.search(r"now|arriv|board", txt, re.IGNORECASE):
        return 0
    return None


def _feed(timeout: int = 15) -> dict:
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]
    resp = requests.get(FEED_URL, timeout=timeout)
    resp.raise_for_status()
    _CACHE["data"], _CACHE["ts"] = resp.json(), now
    return _CACHE["data"]


class PATHFetcher(BaseFetcher):
    feed_type = "path"
    supports_stop_search = True

    def fetch(self) -> list[Departure]:
        if not self.stop_ids:
            raise ValueError("PATH feed requires a station code (e.g. GRV)")
        wanted = {s.upper() for s in self.stop_ids}
        out: list[Departure] = []
        for st in _feed(self.timeout).get("results", []):
            code = str(st.get("consideredStation", "")).upper()
            if code not in wanted:
                continue
            name = STATION_NAMES.get(code, code)
            for grp in st.get("destinations", []):
                for m in grp.get("messages", []):
                    minutes = _minutes(m)
                    if minutes is None:
                        continue
                    color = str(m.get("lineColor") or "").strip().lstrip("#")
                    out.append(Departure(
                        route=str(m.get("target") or "PATH").strip(),
                        destination=str(m.get("headSign") or "").strip(),
                        minutes=minutes,
                        color=("#" + color) if color else None,
                        mode="train",
                        stop_name=name,
                    ))
        return out

    @classmethod
    def find_stops(cls, lat: float, lon: float, limit: int = 8,
                   api_key: str = "", mode: str = "", query: str = "") -> list[StopMatch]:
        if mode == "bus":
            return []  # PATH is trains only
        from geocode import haversine_mi
        scored = sorted(
            ((haversine_mi(lat, lon, slat, slon), code)
             for code, (slat, slon) in STATION_COORDS.items()),
            key=lambda t: t[0],
        )
        return [StopMatch(id=code, name=f"{STATION_NAMES.get(code, code)} (PATH)",
                          detail=f"{dist:.1f} mi · PATH", mode="train")
                for dist, code in scored[:limit]]
