"""Bike-share availability near a point (Divvy, via its public GBFS feed).

Free, no API key:
  * station_information.json  station names + coordinates (rarely changes)
  * station_status.json       live bikes / e-bikes per station

Enabled per device with a top-level ``"bikes"`` block in the config:

    "bikes": {"enabled": true, "near": "${bike_location}",
              "count": 5, "rotate_seconds": 120}

``near`` is "lat,lon". It resolves ${...} like the feed API keys do (config vars
-> env var -> Key Vault), so a home location never has to sit in git.

The display page then alternates the transit board with a bike screen (see
web/app.py). Configs without the block are untouched. Results are cached
in-process so the display refresh doesn't hammer the feed.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

import requests

GBFS_BASE = "https://gbfs.lyft.com/gbfs/1.1/chi/en/"
_INFO_TTL = 12 * 3600   # station list: names/coordinates barely change
_STATUS_TTL = 45        # live counts: the feed itself updates every 60s
_STALE_OK = 300         # on a failed refresh, reuse counts up to this old

_INFO: tuple[float, list] = (0.0, [])
_STATUS: tuple[float, dict] = (0.0, {})


@dataclass
class BikeStation:
    name: str
    miles: float     # straight-line distance from the configured point
    walk_min: int    # rough walking time
    classic: int     # pedal bikes available
    ebikes: int      # e-bikes available


def bikes_config(config: dict) -> dict | None:
    """The device's ``bikes`` block if it is enabled, else None."""
    cfg = config.get("bikes") or {}
    return cfg if cfg.get("enabled") and cfg.get("near") else None


def _location(config: dict, cfg: dict) -> tuple[float, float]:
    """Resolve ``near`` ("lat,lon", possibly a ${...} reference) to floats."""
    from fetcher import _resolve_str

    vault = config.get("key_vault_url") or os.environ.get("AZURE_KEYVAULT_URL", "")
    near = _resolve_str(str(cfg.get("near", "")), config.get("vars") or {}, vault)
    try:
        lat, lon = (float(p) for p in near.split(","))
    except ValueError:
        raise ValueError("bikes: 'near' must be \"lat,lon\"") from None
    return lat, lon


def _miles(lat1, lon1, lat2, lon2) -> float:
    p = math.radians
    a = (math.sin(p(lat2 - lat1) / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lon2 - lon1) / 2) ** 2)
    return 2 * 3958.8 * math.asin(math.sqrt(a))


def _get(name: str) -> list:
    resp = requests.get(GBFS_BASE + name, timeout=8)
    resp.raise_for_status()
    return resp.json()["data"]["stations"]


def _station_info() -> list:
    global _INFO
    now = time.time()
    if not _INFO[1] or now - _INFO[0] > _INFO_TTL:
        try:
            _INFO = (now, _get("station_information.json"))
        except Exception:  # noqa: BLE001 - keep the old list if we have one
            if not _INFO[1]:
                raise
    return _INFO[1]


def _station_status() -> dict:
    global _STATUS
    now = time.time()
    if not _STATUS[1] or now - _STATUS[0] > _STATUS_TTL:
        try:
            _STATUS = (now, {s["station_id"]: s for s in _get("station_status.json")})
        except Exception:  # noqa: BLE001 - a blip shouldn't blank the screen
            if not _STATUS[1] or now - _STATUS[0] > _STALE_OK:
                raise
    return _STATUS[1]


def get_bikes(config: dict) -> list[BikeStation]:
    """The nearest in-service stations to the configured point, closest first.

    Raises on a fetch failure with nothing usable cached; the caller turns that
    into the same readable error card the transit feeds use.
    """
    cfg = bikes_config(config)
    if cfg is None:
        return []
    lat, lon = _location(config, cfg)
    count = max(1, min(6, int(cfg.get("count", 5))))

    status = _station_status()
    out = []
    for info in _station_info():
        st = status.get(info["station_id"])
        if not st or not st.get("is_installed") or not st.get("is_renting"):
            continue
        miles = _miles(lat, lon, info["lat"], info["lon"])
        total = int(st.get("num_bikes_available", 0))
        ebikes = int(st.get("num_ebikes_available", 0))
        out.append(BikeStation(
            name=info.get("name", ""),
            miles=miles,
            # street grid, not a straight line: pad the distance, ~3 mph on foot
            walk_min=max(1, round(miles * 1.3 * 20)),
            classic=max(0, total - ebikes),
            ebikes=ebikes,
        ))
    out.sort(key=lambda s: s.miles)
    return out[:count]
