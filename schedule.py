"""Off-hours screen schedule.

`off_hours` in the config (e.g. "23:00-07:00") turns the display off during that
window and back on outside it — evaluated in the board's local time (the
timezone of the first enabled feed's agency, so e.g. a Chicago board uses CST).
Blank/absent = always on. Windows may wrap past midnight.

Used by deploy/screen_schedule.sh (run from cron on the Pi).
"""

from __future__ import annotations

from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def _parse_hhmm(s: str) -> int:
    """'23:00' -> minutes since midnight (0..1439). Raises ValueError if bad."""
    h, _, m = s.strip().partition(":")
    h, m = int(h), int(m or 0)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad time {s!r}")
    return h * 60 + m


def _board_tz(config: dict) -> str:
    """IANA timezone of the first enabled feed's agency (for local time)."""
    try:
        from renderer.themes import THEMES
    except Exception:  # noqa: BLE001
        return ""
    for feed in config.get("feeds", []):
        if feed.get("enabled"):
            theme = THEMES.get(str(feed.get("type", "")).lower())
            if theme and theme.tz:
                return theme.tz
    return ""


def screen_state(config: dict, now: datetime | None = None) -> str:
    """Return "off" if inside the configured off-hours window, else "on"."""
    window = str(config.get("off_hours", "")).strip()
    if not window or "-" not in window:
        return "on"
    start_s, _, end_s = window.partition("-")
    try:
        start, end = _parse_hhmm(start_s), _parse_hhmm(end_s)
    except ValueError:
        return "on"
    if start == end:
        return "on"

    if now is None:
        tz = _board_tz(config)
        now = datetime.now(ZoneInfo(tz)) if (tz and ZoneInfo) else datetime.now()
    cur = now.hour * 60 + now.minute

    if start < end:
        off = start <= cur < end
    else:  # window wraps past midnight, e.g. 23:00-07:00
        off = cur >= start or cur < end
    return "off" if off else "on"


if __name__ == "__main__":  # quick check: prints "on"/"off"
    from appconfig import load_config
    print(screen_state(load_config()))
