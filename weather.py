"""Tiny weather lookup by US ZIP code.

Uses two free, no-API-key services:
  * zippopotam.us   ZIP -> latitude/longitude (+ place name)
  * open-meteo.com  lat/lon -> current temperature + WMO weather code

Results are cached in-process so the 60s render loop doesn't hammer the APIs.
Everything is best-effort: any failure returns None and the display simply
omits the weather widget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

_CACHE: dict[str, tuple[float, "Weather | None"]] = {}
_TTL = 600  # seconds (10 min) — weather doesn't change that fast


@dataclass
class Weather:
    temp_f: int
    condition: str       # short human label, e.g. "Partly cloudy"
    icon: str            # icon group: clear/clouds/fog/rain/snow/storm
    place: str = ""      # city name from the ZIP lookup


# WMO weather code -> (label, icon group). https://open-meteo.com/en/docs
_WMO = {
    0: ("Clear", "clear"),
    1: ("Mostly clear", "clear"),
    2: ("Partly cloudy", "clouds"),
    3: ("Cloudy", "clouds"),
    45: ("Fog", "fog"), 48: ("Fog", "fog"),
    51: ("Drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Drizzle", "rain"),
    56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
    61: ("Rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm", "storm"), 99: ("Thunderstorm", "storm"),
}


def get_weather(config: dict) -> Weather | None:
    """Return current weather for the configured ZIP, or None if unavailable."""
    wcfg = config.get("weather", {}) or {}
    if not wcfg.get("enabled"):
        return None
    zip_code = str(wcfg.get("zip", "")).strip()
    if not zip_code:
        return None

    now = time.time()
    cached = _CACHE.get(zip_code)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    weather = None
    try:
        weather = _fetch(zip_code)
    except Exception as exc:  # noqa: BLE001 - weather is optional
        print(f"[weather] lookup failed for {zip_code}: {exc}")
    _CACHE[zip_code] = (now, weather)
    return weather


def _fetch(zip_code: str) -> Weather | None:
    geo = requests.get(f"https://api.zippopotam.us/us/{zip_code}", timeout=8)
    if geo.status_code != 200:
        return None
    place = geo.json()["places"][0]
    lat, lon = place["latitude"], place["longitude"]
    city = place.get("place name", "")

    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code",
            "temperature_unit": "fahrenheit",
        },
        timeout=8,
    )
    resp.raise_for_status()
    cur = resp.json()["current"]
    label, icon = _WMO.get(int(cur["weather_code"]), ("", "clouds"))
    return Weather(
        temp_f=round(cur["temperature_2m"]),
        condition=label,
        icon=icon,
        place=city,
    )
