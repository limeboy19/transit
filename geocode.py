"""Turn a ZIP code or address into latitude/longitude (free, keyless).

  * 5-digit ZIP  -> zippopotam.us
  * anything else -> OpenStreetMap Nominatim (handles addresses & place names)

Used by the admin "find nearest stop" feature.
"""

from __future__ import annotations

import math
import re

import requests

_ZIP_RE = re.compile(r"^\d{5}$")
_UA = {"User-Agent": "transit-display/1.0 (raspberry-pi departure board)"}


def geocode(query: str):
    """Return (lat, lon, label) for a ZIP/address, or None if not found."""
    query = (query or "").strip()
    if not query:
        return None
    try:
        if _ZIP_RE.match(query):
            r = requests.get(f"https://api.zippopotam.us/us/{query}", timeout=8)
            if r.status_code != 200:
                return None
            p = r.json()["places"][0]
            label = f"{p.get('place name','')}, {p.get('state abbreviation','')} {query}"
            return float(p["latitude"]), float(p["longitude"]), label.strip(", ")

        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers=_UA, timeout=10,
        )
        r.raise_for_status()
        hits = r.json()
        if not hits:
            return None
        h = hits[0]
        return float(h["lat"]), float(h["lon"]), h.get("display_name", query)[:60]
    except Exception:  # noqa: BLE001 - geocoding is best-effort
        return None


def haversine_mi(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in miles."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))
