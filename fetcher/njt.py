"""NJ Transit Rail fetcher (raildata.njtransit.com Rail Data Web API).

Unlike CTA/MTA, NJ Transit uses a username/password -> daily-token model:
  1. POST getToken with username+password -> a token (valid ~24h).
     ** Hard limit of 10 getToken calls/day **, so the token is cached to disk
     and reused until it's ~23h old (survives app restarts).
  2. POST getTrainSchedule19Rec with the token + a 2-char station code ->
     the next ~19 departures (DepartureVision data).

Credentials: the feed's ``api_key`` is "username:password" (split on the first
colon, so the password may contain colons). Store it in the vault as
``njt-key`` = "youruser:yourpass" and reference it as ``${njt_key}``.

``stop_id`` is a 2-character station code (e.g. "NP" = Newark Penn, "NY" = NY
Penn) — see Appendix V of the NJT Rail Data API doc. Comma-separated for
several stations on one board. Use the admin "Find stop" search to look them up.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

from .base import BaseFetcher, Departure, StopMatch

BASE = "https://raildata.njtransit.com/api/TrainData"
_TOKEN_TTL = 23 * 3600  # refresh a bit before the 24h expiry
_TOKEN_FILE = Path(__file__).resolve().parent.parent / ".njt_token.json"
_TOKEN_MEM: dict[str, tuple[float, str]] = {}  # user -> (fetched_at, token)
_STATIONS_CACHE: list[dict] = []

_MIN_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)


def _split_creds(api_key: str) -> tuple[str, str]:
    user, _, pw = (api_key or "").partition(":")
    return user.strip(), pw  # password kept verbatim (may contain ':')


def _cached_token(user: str) -> str | None:
    hit = _TOKEN_MEM.get(user)
    if hit and (time.time() - hit[0]) < _TOKEN_TTL:
        return hit[1]
    # fall back to the on-disk cache (survives restarts -> protects the 10/day cap)
    try:
        data = json.loads(_TOKEN_FILE.read_text())
        rec = data.get(user)
        if rec and (time.time() - rec["ts"]) < _TOKEN_TTL:
            _TOKEN_MEM[user] = (rec["ts"], rec["token"])
            return rec["token"]
    except (OSError, ValueError, KeyError):
        pass
    return None


def _store_token(user: str, token: str) -> None:
    now = time.time()
    _TOKEN_MEM[user] = (now, token)
    try:
        data = {}
        if _TOKEN_FILE.exists():
            data = json.loads(_TOKEN_FILE.read_text())
        data[user] = {"token": token, "ts": now}
        _TOKEN_FILE.write_text(json.dumps(data))
    except (OSError, ValueError):
        pass


def _get_token(api_key: str, timeout: int = 10, force: bool = False) -> str:
    user, pw = _split_creds(api_key)
    if not user or not pw:
        raise ValueError('NJT needs api_key = "username:password" (NJ Transit API login)')
    if not force:
        cached = _cached_token(user)
        if cached:
            return cached
    resp = requests.post(f"{BASE}/getToken", timeout=timeout,
                         files={"username": (None, user), "password": (None, pw)})
    resp.raise_for_status()
    data = resp.json() or {}
    if data.get("errorMessage"):
        raise RuntimeError(f"NJT getToken: {data['errorMessage']} (10 token calls/day max)")
    token = data.get("UserToken") or ""
    if str(data.get("Authenticated")).lower() != "true" or not token:
        raise RuntimeError("NJT getToken: authentication failed (check username/password)")
    _store_token(user, token)
    return token


class NJTFetcher(BaseFetcher):
    feed_type = "njt"
    supports_stop_search = True
    stop_search_by_name = True  # NJT's API has no coordinates — search by name

    def fetch(self) -> list[Departure]:
        if not self.stop_ids:
            raise ValueError("NJT feed requires a station code (e.g. NP)")
        token = _get_token(self.api_key, self.timeout)
        departures: list[Departure] = []
        for station in self.stop_ids:
            departures += self._station_departures(token, station)
        return departures

    def _station_departures(self, token: str, station: str) -> list[Departure]:
        def call(tok):
            return requests.post(
                f"{BASE}/getTrainSchedule19Rec", timeout=self.timeout,
                files={"token": (None, tok), "station": (None, station), "line": (None, "")},
            )

        resp = call(token)
        resp.raise_for_status()
        data = resp.json() or {}
        # token expired between cache windows -> refresh once and retry
        if isinstance(data, dict) and data.get("errorMessage") == "Invalid token.":
            data = call(_get_token(self.api_key, self.timeout, force=True)).json() or {}
        if not isinstance(data, dict) or data.get("errorMessage"):
            raise RuntimeError(f"NJT: {(data or {}).get('errorMessage', 'no data')}")

        station_name = data.get("STATIONNAME") or station
        out: list[Departure] = []
        for item in data.get("ITEMS") or []:
            status = (item.get("STATUS") or "").strip()
            minutes, delayed = self._eta(status, item)
            if minutes is None:
                continue
            out.append(Departure(
                route=(item.get("LINEABBREVIATION") or item.get("LINE") or "NJT").strip(),
                destination=(item.get("DESTINATION") or "").strip(),
                minutes=minutes,
                color=(item.get("BACKCOLOR") or "").strip() or None,
                delayed=delayed,
                mode="train",
                stop_name=station_name,
            ))
        return out

    @staticmethod
    def _eta(status: str, item: dict) -> tuple[int | None, bool]:
        delayed = "delay" in status.lower()
        m = _MIN_RE.search(status)
        if m:
            return int(m.group(1)), delayed
        if status:  # "Boarding" / "All Aboard" / "Now" / "Delayed" with no number
            return 0, delayed
        # no live status yet — fall back to the scheduled departure time
        raw = item.get("SCHED_DEP_DATE")
        if raw:
            try:
                from datetime import datetime
                try:
                    from zoneinfo import ZoneInfo
                    now = datetime.now(ZoneInfo("America/New_York"))
                    sched = datetime.strptime(raw, "%d-%b-%Y %I:%M:%S %p").replace(tzinfo=ZoneInfo("America/New_York"))
                except Exception:  # noqa: BLE001
                    now = datetime.now()
                    sched = datetime.strptime(raw, "%d-%b-%Y %I:%M:%S %p")
                mins = round((sched - now).total_seconds() / 60)
                return (mins, delayed) if mins >= 0 else (None, delayed)
            except ValueError:
                return None, delayed
        return None, delayed

    # --- stop search (by station name; NJT's API has no coordinates) ---------

    @classmethod
    def find_stops(cls, lat: float, lon: float, limit: int = 8,
                   api_key: str = "", mode: str = "", query: str = "") -> list[StopMatch]:
        if mode == "bus" or not query:
            return []  # rail only; need a name to match against
        try:
            stations = cls._load_stations(api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"[njt] station list failed: {exc}")
            return []
        q = query.lower().strip()
        hits = [s for s in stations if q in (s.get("STATIONNAME") or "").lower()]
        return [StopMatch(id=s.get("STATION_2CHAR", ""),
                          name=s.get("STATIONNAME", ""),
                          detail="NJT rail", mode="train")
                for s in hits[:limit]]

    @classmethod
    def _load_stations(cls, api_key: str) -> list[dict]:
        global _STATIONS_CACHE
        if _STATIONS_CACHE:
            return _STATIONS_CACHE
        token = _get_token(api_key)
        resp = requests.post(f"{BASE}/getStationList", timeout=12,
                            files={"token": (None, token)})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            _STATIONS_CACHE = data
        return _STATIONS_CACHE
