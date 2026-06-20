"""MTA NYC Subway fetcher (GTFS-Realtime / protobuf).

As of 2023 the MTA GTFS-RT feeds are public and no longer require an API key,
but we still send ``api_key`` as an ``x-api-key`` header if one is configured,
so the same code works if MTA reinstates keys.

The subway is split across several feeds by line group. We pick the right feed
from the first character of the stop id (e.g. ``R31N`` -> N/Q/R/W feed). You
can override this with a ``"feed"`` key in the feed config (one of the suffixes
below, or ``""`` for the numbered-lines feed).

Stop ids carry a direction suffix: ``N`` (northbound) / ``S`` (southbound).
Configure the platform you care about, e.g. ``R31N`` for northbound at
Atlantic Av-Barclays Ctr on the R.

Requires: gtfs-realtime-bindings (which pulls in protobuf).
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import datetime, timezone

import requests
from google.transit import gtfs_realtime_pb2

from .base import BaseFetcher, Departure, StopMatch

FEED_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs"

# NYC buses use the separate MTA Bus Time API (OneBusAway + SIRI). Unlike the
# subway feeds, this needs a free key from bustime.mta.info. The feed's
# `api_key` is treated as that Bus Time key.
BUS_STOPS_URL = "https://bustime.mta.info/api/where/stops-for-location.json"
BUS_SIRI_URL = "https://bustime.mta.info/api/siri/stop-monitoring.json"
BUS_COLOR = "#1E5BA8"

# Static GTFS (for the stop list / "find nearest stop"). Try the current S3
# location first, then the legacy host.
GTFS_STATIC_URLS = [
    "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip",
    "http://web.mta.info/developers/data/nyct/subway/google_transit.zip",
]
_STOPS_CACHE: list[dict] = []

# All subway line-group feeds. A station shared by several lines (e.g. Queens
# Plaza on E/M/R) appears in multiple feeds, and the stop-id prefix doesn't
# reliably indicate which — so we query them all and match the stop. Each trip
# lives in exactly one feed, so the union has no duplicates.
ALL_SUBWAY_SUFFIXES = ["", "-ace", "-bdfm", "-g", "-jz", "-nqrw", "-l", "-si"]

# stop-id first character -> feed suffix appended to FEED_BASE.
FEED_BY_PREFIX = {
    "A": "-ace", "C": "-ace", "E": "-ace", "H": "-ace",
    "B": "-bdfm", "D": "-bdfm", "F": "-bdfm", "M": "-bdfm",
    "G": "-g",
    "J": "-jz", "Z": "-jz",
    "N": "-nqrw", "Q": "-nqrw", "R": "-nqrw", "W": "-nqrw",
    "L": "-l",
    "S": "-si",  # Staten Island Railway stop ids start with S
}

ROUTE_COLORS = {
    "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
    "4": "#00933C", "5": "#00933C", "6": "#00933C",
    "7": "#B933AD",
    "A": "#0039A6", "C": "#0039A6", "E": "#0039A6",
    "B": "#FF6319", "D": "#FF6319", "F": "#FF6319", "M": "#FF6319",
    "G": "#6CBE45",
    "J": "#996633", "Z": "#996633",
    "L": "#A7A9AC",
    "N": "#FCCC0A", "Q": "#FCCC0A", "R": "#FCCC0A", "W": "#FCCC0A",
}

DIRECTION_NAMES = {"N": "Uptown", "S": "Downtown"}


class MTAFetcher(BaseFetcher):
    feed_type = "mta"
    supports_stop_search = True

    @classmethod
    def find_stops(cls, lat: float, lon: float, limit: int = 8,
                   api_key: str = "", mode: str = "", query: str = "") -> list[StopMatch]:
        from geocode import haversine_mi

        cand: list[tuple[float, StopMatch]] = []

        # --- subway platforms (open static GTFS, direction-suffixed ids) ---
        if mode in ("", "train"):
            try:
                for row in cls._load_stops():
                    sid = (row.get("stop_id") or "").strip()
                    if not (sid.endswith("N") or sid.endswith("S")):
                        continue
                    try:
                        d = haversine_mi(lat, lon, float(row["stop_lat"]), float(row["stop_lon"]))
                    except (KeyError, ValueError):
                        continue
                    direction = "Uptown" if sid.endswith("N") else "Downtown"
                    cand.append((d, StopMatch(
                        id=sid, name=f"{row.get('stop_name', '').strip()} · {direction}",
                        detail=f"{d:.1f} mi", mode="train")))
            except Exception as exc:  # noqa: BLE001
                print(f"[mta] subway stop list failed: {exc}")

        # --- bus stops (MTA Bus Time, needs the key) ---
        if mode in ("", "bus") and api_key:
            try:
                cand.extend(cls._find_bus_stops(lat, lon, api_key))
            except Exception as exc:  # noqa: BLE001
                print(f"[mta] bus stop search failed: {exc}")

        cand.sort(key=lambda t: t[0])
        return [m for _, m in cand[:limit]]

    @classmethod
    def _find_bus_stops(cls, lat, lon, api_key, span=0.012):
        from geocode import haversine_mi

        resp = requests.get(BUS_STOPS_URL, params={
            "key": api_key, "lat": lat, "lon": lon,
            "latSpan": span, "lonSpan": span,
        }, timeout=12)
        resp.raise_for_status()
        payload = resp.json().get("data", {}) or {}
        stops = payload.get("stops") or payload.get("list") or []
        out: list[tuple[float, StopMatch]] = []
        for s in stops:
            sid = s.get("id") or s.get("code")
            if not sid:
                continue
            try:
                d = haversine_mi(lat, lon, float(s["lat"]), float(s["lon"]))
            except (KeyError, TypeError, ValueError):
                continue
            routes = ", ".join(r.get("shortName", "") for r in (s.get("routes") or []))[:36]
            detail = f"{d:.1f} mi" + (f" · {routes}" if routes else "") + " · Bus"
            out.append((d, StopMatch(id=str(sid), name=s.get("name", "Bus stop"),
                                     detail=detail, mode="bus")))
        return out

    @classmethod
    def _stop_names(cls) -> dict[str, str]:
        """Map of subway stop_id -> station name (for multi-stop boards)."""
        try:
            return {r.get("stop_id", ""): (r.get("stop_name") or "").strip()
                    for r in cls._load_stops()}
        except Exception:  # noqa: BLE001
            return {}

    @classmethod
    def _load_stops(cls) -> list[dict]:
        global _STOPS_CACHE
        if _STOPS_CACHE:
            return _STOPS_CACHE
        last_err: Exception | None = None
        for url in GTFS_STATIC_URLS:
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                zf = zipfile.ZipFile(io.BytesIO(resp.content))
                with zf.open("stops.txt") as fh:
                    text = io.TextIOWrapper(fh, encoding="utf-8-sig")
                    _STOPS_CACHE = list(csv.DictReader(text))
                return _STOPS_CACHE
            except Exception as exc:  # noqa: BLE001 - try next mirror
                last_err = exc
        raise RuntimeError(f"could not load MTA stop list: {last_err}")

    def _feed_url_for(self, sid: str) -> str:
        override = str(self.config.get("feed", "")).strip()
        if override:
            suffix = override if override.startswith("-") or override == "" else f"-{override}"
            return FEED_BASE + suffix
        if not sid:
            return FEED_BASE
        prefix = sid[0].upper()
        if prefix.isdigit():
            return FEED_BASE  # numbered lines 1-7 + 42 St shuttle
        return FEED_BASE + FEED_BY_PREFIX.get(prefix, "")

    def feed_url(self) -> str:
        return self._feed_url_for(self.stop_id.split(",")[0].strip())

    @staticmethod
    def _is_bus(sid: str) -> bool:
        # Bus Time stop ids look like "MTA_404179" or a bare numeric code.
        return sid.upper().startswith("MTA") or sid.isdigit()

    def fetch(self) -> list[Departure]:
        if not self.stop_ids:
            raise ValueError("MTA feed requires a stop_id (subway like R31N, or an MTA bus stop)")

        subway_ids = [s for s in self.stop_ids if not self._is_bus(s)]
        bus_ids = [s for s in self.stop_ids if self._is_bus(s)]

        departures: list[Departure] = []
        if subway_ids:
            departures += self._fetch_subway(subway_ids)
        if bus_ids:
            if not self.api_key:
                raise ValueError("NYC bus stops need an MTA Bus Time api_key (bustime.mta.info)")
            for bid in bus_ids:
                departures += self._fetch_bus(bid)
        return departures

    def _subway_feed_urls(self) -> list[str]:
        override = str(self.config.get("feed", "")).strip()
        if override:
            suffix = override if override.startswith("-") or override == "" else f"-{override}"
            return [FEED_BASE + suffix]
        return [FEED_BASE + s for s in ALL_SUBWAY_SUFFIXES]

    def _fetch_subway(self, subway_ids: list[str]) -> list[Departure]:
        # query every line-group feed and match our stops across all of them
        # (a shared station's lines are spread across multiple feeds).
        stop_set = set(subway_ids)
        names = self._stop_names()  # needed for real terminal/destination names
        now = time.time()
        out: list[Departure] = []
        for url in self._subway_feed_urls():
            try:
                resp = requests.get(url, timeout=self.timeout)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - one feed down ≠ total failure
                print(f"[mta] feed fetch failed ({url.split('gtfs')[-1] or 'main'}): {exc}")
                continue
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(resp.content)
            for entity in feed.entity:
                if not entity.HasField("trip_update"):
                    continue
                tu = entity.trip_update
                route_id = tu.trip.route_id or "?"
                # the trip's last scheduled stop is its terminal — that's the real
                # destination (e.g. "Jamaica Center"), far better than N/S which
                # only reads as Uptown/Downtown inside Manhattan.
                terminal_id = tu.stop_time_update[-1].stop_id if tu.stop_time_update else ""
                terminal = (names.get(terminal_id) or names.get(terminal_id[:-1])
                            or DIRECTION_NAMES.get(terminal_id[-1:].upper(), ""))
                for stu in tu.stop_time_update:
                    if stu.stop_id not in stop_set:
                        continue
                    when = 0
                    if stu.HasField("arrival") and stu.arrival.time:
                        when = stu.arrival.time
                    elif stu.HasField("departure") and stu.departure.time:
                        when = stu.departure.time
                    if not when:
                        continue
                    minutes = round((when - now) / 60)
                    if minutes < 0:
                        continue
                    out.append(Departure(
                        route=route_id,
                        destination=terminal,
                        minutes=minutes,
                        color=ROUTE_COLORS.get(route_id),
                        mode="train",
                        stop_name=names.get(stu.stop_id) or names.get(stu.stop_id[:-1], ""),
                    ))
        return out

    def _fetch_bus(self, stop_id: str) -> list[Departure]:
        resp = requests.get(BUS_SIRI_URL, params={
            "key": self.api_key, "MonitoringRef": stop_id, "version": 2,
        }, timeout=self.timeout)
        resp.raise_for_status()
        try:
            deliveries = resp.json()["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"]
            visits = deliveries[0].get("MonitoredStopVisit", []) if deliveries else []
        except (KeyError, IndexError, TypeError):
            return []

        now = datetime.now(timezone.utc)
        out: list[Departure] = []
        for visit in visits:
            mvj = visit.get("MonitoredVehicleJourney", {}) or {}
            route = mvj.get("PublishedLineName") or mvj.get("LineRef", "")
            if isinstance(route, list):
                route = route[0] if route else ""
            route = str(route).split("_")[-1].strip() or "Bus"
            dest = mvj.get("DestinationName", "")
            if isinstance(dest, list):
                dest = dest[0] if dest else ""
            call = mvj.get("MonitoredCall", {}) or {}
            stamp = (call.get("ExpectedArrivalTime") or call.get("ExpectedDepartureTime")
                     or call.get("AimedArrivalTime"))
            if not stamp:
                continue
            try:
                when = datetime.fromisoformat(stamp)
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            minutes = round((when - now).total_seconds() / 60)
            if minutes < 0:
                continue
            stop_name = call.get("StopPointName", "")
            if isinstance(stop_name, list):
                stop_name = stop_name[0] if stop_name else ""
            out.append(Departure(route=route, destination=str(dest), minutes=minutes,
                                  color=BUS_COLOR, mode="bus", stop_name=str(stop_name)))
        return out
