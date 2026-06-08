"""Per-agency visual themes for the tracker layout.

The layout is identical for every agency; only the colors and the city-identity
header change. Colors are chosen to read as light/friendly/warm on screen and
to survive the panel's 6-color palette (black, white, red, green, blue, yellow)
reasonably well.

Chicago uses the Chicago flag motif: light-blue accent with four red
six-pointed stars in the header. Other cities get their own accent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Theme:
    key: str
    title: str                       # default header title
    accent: str                      # strong brand color (title, underline, ETA)
    band: str                        # light header background tint
    tz: str = ""                     # IANA timezone for the clock ("" = local)
    tz_label: str = ""               # fixed suffix shown after time (e.g. "CST")
    bg: str = "#FBFBF8"              # warm off-white page background
    ink: str = "#1C1C1C"            # primary text
    muted: str = "#6B7280"          # secondary text
    row_alt: str = "#F2F4F3"        # subtle alternating row shade
    emblem: str = ""                 # header emblem: "stars" | "nycflag" | ""
    stars: int = 0                   # # of Chicago flag stars to draw
    star_color: str = "#D2202E"


THEMES: dict[str, Theme] = {
    # Chicago flag: sky-blue + red stars on white.
    "cta": Theme(
        key="cta", title="Chicago Transit", tz="America/Chicago", tz_label="CST",
        accent="#1DA1DC", band="#E9F6FC",
        row_alt="#EFF8FC", emblem="stars", stars=4, star_color="#D2202E",
    ),
    # MTA: subway navy blue, with a small NYC tricolor flag.
    "mta": Theme(
        key="mta", title="NYC Subway", tz="America/New_York", tz_label="EST",
        accent="#0B57A4", band="#EAF0F8", row_alt="#EEF3FA", emblem="nycflag",
    ),
    # NJ Transit: deep blue.
    "njt": Theme(
        key="njt", title="NJ Transit", tz="America/New_York", tz_label="EST",
        accent="#14508C", band="#EAF1F7", row_alt="#EDF3F8",
    ),
    "default": Theme(
        key="default", title="Transit Departures",
        accent="#374151", band="#F1F2F4", row_alt="#F4F4F2",
    ),
}


def resolve_theme(config: dict, results) -> Theme:
    """Pick the theme: explicit config override, else the first feed's agency."""
    override = str((config.get("display", {}) or {}).get("theme", "")).lower().strip()
    if override and override in THEMES:
        return THEMES[override]

    # derive from the first result/feed that has a known type
    for result in results:
        ft = getattr(result, "feed_type", "") or ""
        if ft in THEMES:
            return THEMES[ft]
    for feed in config.get("feeds", []):
        if feed.get("enabled") and str(feed.get("type", "")).lower() in THEMES:
            return THEMES[str(feed["type"]).lower()]
    return THEMES["default"]
