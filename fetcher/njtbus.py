"""NJ Transit BUS + Light Rail fetcher (pcsdata.njtransit.com BUSDV2 Web API).

Like the rail fetcher, this uses a username/password -> daily-token model, but
on a different server and auth endpoint:
  1. POST authenticateUser (username+password) -> a UserToken (~24h life).
     Token minting is rate-limited, so it's cached to disk and reused.
  2. POST getBusDV with the token + a stop number -> the trips active in the
     next hour. Works for buses AND the light rails (Hudson-Bergen / Newark /
     River Line), which share this API.

Credentials: feed ``api_key`` = "username:password" -- the SAME NJT API login as
the rail feed. Store as ``${njt_key}`` in the vault.

``stop_id`` is a numeric stop number: a bus stop (e.g. "20645") or a light-rail
stop (e.g. "30833" = Exchange Place). Comma-separated for several. Use the admin
"Find stop" (by ZIP/address) to look them up -- it calls getBusLocationsData
around your coordinates.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from .base import BaseFetcher, Departure, StopMatch

BASE = "https://pcsdata.njtransit.com/api/BUSDV2"
_TOKEN_TTL = 18 * 3600  # re-auth well before the ~24h expiry (minting is rate-limited)
_TOKEN_FILE = Path(__file__).resolve().parent.parent / ".njtbus_token.json"
_TOKEN_MEM: dict[str, tuple[float, str]] = {}
_STOPNAME_CACHE: dict[str, str] = {}

_IN_MIN_RE = re.compile(r"in\s+(\d+)\s*min", re.IGNORECASE)
_CLOCK_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.IGNORECASE)


def _split_creds(api_key: str) -> tuple[str, str]:
    user, _, pw = (api_key or "").partition(":")
    return user.strip(), pw


def _cap(text: str) -> str:
    """NJT data is ALL CAPS; title-case it for the board (leave mixed case as-is)."""
    if not (text and text.isupper()):
        return text
    # fix ordinals that .title() mangles: "8TH" -> "8Th" -> "8th", "3RD" -> "3rd"
    return re.sub(r"(\d)(St|Nd|Rd|Th)\b",
                  lambda m: m.group(1) + m.group(2).lower(), text.title())


# --- daily token cache (protects the mint rate-limit; survives restarts) ------
def _cached_token(user: str) -> str | None:
    hit = _TOKEN_MEM.get(user)
    if hit and (time.time() - hit[0]) < _TOKEN_TTL:
        return hit[1]
    try:
        rec = json.loads(_TOKEN_FILE.read_text()).get(user)
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
        data = json.loads(_TOKEN_FILE.read_text()) if _TOKEN_FILE.exists() else {}
        data[user] = {"token": token, "ts": now}
        _TOKEN_FILE.write_text(json.dumps(data))
    except (OSError, ValueError):
        pass


def _get_token(api_key: str, timeout: int = 15, force: bool = False) -> str:
    user, pw = _split_creds(api_key)
    if not user or not pw:
        raise ValueError('NJT bus needs api_key = "username:password" (NJ Transit API login)')
    if not force:
        cached = _cached_token(user)
        if cached:
            return cached
    resp = requests.post(f"{BASE}/authenticateUser", timeout=timeout,
                         files={"username": (None, user), "password": (None, pw)})
    resp.raise_for_status()
    data = resp.json() or {}
    token = data.get("UserToken") or ""
    if str(data.get("Authenticated")).lower() != "true" or not token:
        raise RuntimeError("NJT bus auth failed (check NJT API credentials / BUSDATA access)")
    _store_token(user, token)
    return token


def _stop_name(token: str, stop: str, timeout: int) -> str:
    if stop in _STOPNAME_CACHE:
        return _STOPNAME_CACHE[stop]
    name = ""
    try:
        r = requests.post(f"{BASE}/getStopName", timeout=timeout,
                          files={"token": (None, token), "stopnum": (None, stop)})
        if r.ok:
            raw = str((r.json() or {}).get("stopName", "")).strip()
            # drop the "LIGHT RAIL STATION" suffix (the row icon caption says so)
            raw = re.sub(r"\s*LIGHT RAIL STATION\s*$", "", raw, flags=re.IGNORECASE)
            name = _cap(raw)
    except (requests.RequestException, ValueError):
        pass
    _STOPNAME_CACHE[stop] = name
    return name


class NJTBusFetcher(BaseFetcher):
    feed_type = "njtbus"
    supports_stop_search = True

    def fetch(self) -> list[Departure]:
        if not self.stop_ids:
            raise ValueError("NJT bus feed requires a stop number (e.g. 20645)")
        token = _get_token(self.api_key, self.timeout)
        out: list[Departure] = []
        for stop in self.stop_ids:
            out += self._stop_departures(token, stop)
        return out

    def _stop_departures(self, token: str, stop: str) -> list[Departure]:
        def call(tok):
            return requests.post(f"{BASE}/getBusDV", timeout=self.timeout, files={
                "token": (None, tok), "stop": (None, stop),
                "direction": (None, ""), "route": (None, ""), "IP": (None, ""),
            })

        resp = call(token)
        resp.raise_for_status()
        data = resp.json() or {}
        # token expired between cache windows -> refresh once and retry
        if isinstance(data, dict) and data.get("errorMessage") == "Invalid token.":
            data = call(_get_token(self.api_key, self.timeout, force=True)).json() or {}
        if not isinstance(data, dict):
            return []

        name = _stop_name(token, stop, self.timeout)
        out: list[Departure] = []
        for t in data.get("DVTrip") or []:
            minutes = self._minutes(t.get("departuretime"))
            if minutes is None:
                continue
            route = (t.get("public_route") or "").strip()
            header = (t.get("header") or "").strip()
            is_lr = "LIGHT RAIL" in header.upper()
            out.append(Departure(
                route=route,
                destination=self._dest(header, route),
                minutes=minutes,
                delayed="delay" in str(t.get("remarks", "")).lower(),
                mode="train" if is_lr else "bus",
                stop_name=name,
                icon_label="Light Rail" if is_lr else "",
            ))
        return out

    @staticmethod
    def _dest(header: str, route: str) -> str:
        d = header.strip()
        if route:  # drop a leading route code that duplicates the badge (e.g. "80S ...")
            d = re.sub(r"^\s*" + re.escape(route) + r"[A-Za-z]?\s+", "", d)
        d = re.sub(r"\s*LIGHT RAIL STATION\s*$", "", d, flags=re.IGNORECASE)
        return _cap(d.strip()) or _cap(header)

    @staticmethod
    def _minutes(txt: str) -> int | None:
        """Parse departuretime: 'in N mins', a clock time '2:40 PM', or DUE/NOW."""
        s = (txt or "").strip()
        if not s:
            return None
        m = _IN_MIN_RE.search(s)
        if m:
            return int(m.group(1))
        if re.search(r"\b(due|now|boarding|approach|arriv)", s, re.IGNORECASE):
            return 0
        m = _CLOCK_RE.search(s)
        if m:
            try:
                from zoneinfo import ZoneInfo
                now = datetime.now(ZoneInfo("America/New_York"))
            except Exception:  # noqa: BLE001
                now = datetime.now()
            hh, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
            if ap == "PM" and hh != 12:
                hh += 12
            if ap == "AM" and hh == 12:
                hh = 0
            dep = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            mins = round((dep - now).total_seconds() / 60)
            if mins < -180:  # clock wrapped past midnight
                mins += 24 * 60
            return mins if mins >= -1 else None
        return None

    # --- stop search by coordinate (getBusLocationsData) ---------------------
    @classmethod
    def find_stops(cls, lat: float, lon: float, limit: int = 8,
                   api_key: str = "", mode: str = "", query: str = "") -> list[StopMatch]:
        try:
            token = _get_token(api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"[njtbus] auth failed for stop search: {exc}")
            return []
        try:
            resp = requests.post(f"{BASE}/getBusLocationsData", timeout=15, files={
                "token": (None, token), "route": (None, ""), "direction": (None, ""),
                "lat": (None, str(lat)), "lon": (None, str(lon)),
                "radius": (None, "2500"), "mode": (None, "ALL"),
            })
            resp.raise_for_status()
            rows = resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[njtbus] stop search failed: {exc}")
            return []
        if not isinstance(rows, list):
            return []

        out: list[StopMatch] = []
        for s in sorted(rows, key=lambda r: float(r.get("distance", 9e9))):
            is_bus = (s.get("modetype") or "").upper() == "BUS"
            if mode == "bus" and not is_bus:
                continue
            if mode == "train" and is_bus:
                continue
            sid = str(s.get("busstopnumber") or "").strip()
            if not sid:
                continue
            try:
                detail = f"{float(s.get('distance', 0)) / 5280:.1f} mi · {s.get('modetype')}"
            except (ValueError, TypeError):
                detail = str(s.get("modetype", ""))
            out.append(StopMatch(id=sid, name=_cap(str(s.get("busstopdescription", ""))),
                                 detail=detail, mode="bus" if is_bus else "train"))
            if len(out) >= limit:
                break
        return out
