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
    emblem: str = ""                 # header emblem: "stars" | "nycflag" | "image" | ""
    stars: int = 0                   # # of Chicago flag stars to draw
    star_color: str = "#D2202E"
    emblem_img: str = ""             # filename in renderer/assets/ (for emblem="image")
    rail_color: str = ""             # badge color for rail/light-rail (blank = accent)
    bus_color: str = ""              # badge color for bus (blank = accent)
    weather_icon: str = ""           # force weather-glyph color (blank = amber/grey default)
    fit_destination: bool = False    # prioritize the destination text over the stop label
    flat_badges: bool = False        # ignore feed line-colors; use theme badge colors
    lr_color: str = ""               # badge color for light-rail rows (blank = rail_color)
    moose_head: bool = False         # draw a tiny moose (antlers/ears) over the "oo" in the title
    band_stripe: str = ""            # 2nd color for the header divider (blank = single accent line)
    temp_color: str = ""             # weather temperature text color (blank = ink default)
    header_h: int = 84               # header band height in px
    antler_img: str = ""             # assets/<file> silhouette to recolor + place over the "oo"
    gameday_team: str = ""           # ESPN NFL team id (e.g. "9" = Packers); on game day, show a banner
    gameday_img: str = ""            # assets/<file> graphic shown on game day (e.g. GO PACK GO)


THEMES: dict[str, Theme] = {
    # Chicago flag: sky-blue accent, with Maya the cat as the header emblem
    # (swapped in for the four Chicago flag stars).
    "cta": Theme(
        key="cta", title="Chicago Transit", tz="America/Chicago", tz_label="CST",
        accent="#1DA1DC", band="#E9F6FC",
        row_alt="#EFF8FC", emblem="image", emblem_img="maya.png",
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
    # NJ Transit bus + light rail. A neutral NJT starting look; each device's
    # config overrides colors/logo via its "theme" block.
    "njtbus": Theme(
        key="njtbus", title="NJ Transit", tz="America/New_York", tz_label="EST",
        accent="#0B5CAB", band="#EAF1F8", row_alt="#EEF4FA",
    ),
    # PATH (Port Authority Trans-Hudson). Neutral base; per-device config
    # overrides colors/logo. PATH trains keep their own line colors on the badge.
    "path": Theme(
        key="path", title="PATH", tz="America/New_York", tz_label="EST",
        accent="#003DA5", band="#E8EEF7", row_alt="#EDF2FA",
    ),
    "default": Theme(
        key="default", title="Transit Departures",
        accent="#374151", band="#F1F2F4", row_alt="#F4F4F2",
    ),
}


def resolve_theme(config: dict, results) -> Theme:
    """Base agency theme, then layer this device's per-board customization on top.

    A device's config may carry a top-level ``"theme"`` block to override colors,
    title, and the logo/emblem -- so the look is per-DEVICE. Configs with no
    ``"theme"`` block render exactly as before (fully backward-compatible).
    """
    return _apply_overrides(_base_theme(config, results), config.get("theme") or {})


def _base_theme(config: dict, results) -> Theme:
    """Pick the agency theme: explicit config override, else the first feed's type."""
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


def _apply_overrides(base: Theme, ov: dict) -> Theme:
    """Layer a device's per-board customization (colors, title, logo) over base."""
    if not ov:
        return base
    import dataclasses
    changes = {k: ov[k] for k in ("title", "accent", "band", "bg", "ink", "muted",
                                  "row_alt", "tz", "tz_label", "rail_color", "bus_color",
                                  "weather_icon", "fit_destination", "flat_badges",
                                  "lr_color", "moose_head", "band_stripe",
                                  "temp_color", "header_h", "antler_img",
                                  "gameday_team", "gameday_img") if ov.get(k)}
    logo = ov.get("logo") or ov.get("emblem_img")
    if logo:                       # a device logo replaces the agency stars/flag
        changes["emblem"] = "image"
        changes["emblem_img"] = logo
    return dataclasses.replace(base, **changes) if changes else base
