"""Renders feed results to the Inky Impression, or to a PNG for local dev.

On a Raspberry Pi (detected via /proc/device-tree/model) we import the
``inky`` library and push to the panel. Anywhere else we skip the hardware
import entirely and just write ``preview.png``.

The layout is a light, friendly "what's next" departure board: a themed header
(agency identity + weather + clock) over big, glanceable rows — one per
upcoming train/bus with a colored route badge, destination, and ETA.

Two body modes:
  * "sections"  — a labeled section per feed (stop), departures under each.
  * "stacked"   — all feeds merged into one chronological list (MTA-style),
                  so the very next thing to leave is always on top.

The panel is only 6 colors (black/white/red/green/blue/yellow); the RGB
preview is optimistic, so :func:`simulate_eink` can quantize+dither an image
to show what the hardware will actually render.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from PIL import Image, ImageDraw, ImageFont

from .themes import Theme, resolve_theme

WIDTH, HEIGHT = 800, 480
PREVIEW_PATH = Path(__file__).resolve().parent.parent / "preview.png"

# The 6 colors the Inky Impression 7.3" can actually display.
EINK_PALETTE = [
    (0, 0, 0), (255, 255, 255), (255, 0, 0),
    (0, 200, 0), (0, 0, 255), (255, 220, 0),
]

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

_FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
}
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# Font Awesome (bundled) for crisp bus/train glyphs in any theme color.
ICON_FONT_PATH = Path(__file__).resolve().parent.parent / "fonts" / "fa-solid-900.ttf"

# Header emblem images (e.g. a pet photo standing in for the city flag).
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_EMBLEM_CACHE: dict = {}


def _emblem_image(name: str, height: int):
    """Load renderer/assets/<name> as RGBA, scaled to `height` px (cached)."""
    key = (name, height)
    if key in _EMBLEM_CACHE:
        return _EMBLEM_CACHE[key]
    img = None
    try:
        im = Image.open(ASSETS_DIR / name).convert("RGBA")
        bbox = im.getbbox() or (0, 0, im.width, im.height)  # trim transparent margin
        im = im.crop(bbox)
        w = max(1, round(im.width * height / im.height))
        img = im.resize((w, height), Image.LANCZOS)
    except Exception as exc:  # noqa: BLE001 - missing/corrupt asset -> no emblem
        print(f"[display] emblem '{name}' failed: {exc}")
    _EMBLEM_CACHE[key] = img
    return img


_SIL_CACHE: dict = {}


def _silhouette_image(name: str, color, height: int):
    """Load assets/<name>, recolor its shape to `color` on a transparent background,
    scaled to `height` px. Works for black-on-white art or already-transparent PNGs."""
    key = (name, tuple(color), height)
    if key in _SIL_CACHE:
        return _SIL_CACHE[key]
    out = None
    try:
        im = Image.open(ASSETS_DIR / name).convert("RGBA")
        r, g, b, a = im.split()
        if a.getextrema()[0] == 255:      # opaque (black-on-white) -> mask by darkness
            mask = Image.merge("RGB", (r, g, b)).convert("L").point(lambda v: 255 - v)
        else:                             # already transparent -> keep its own alpha
            mask = a
        solid = Image.new("RGBA", im.size, tuple(color) + (255,))
        solid.putalpha(mask)
        solid = solid.crop(solid.getbbox() or (0, 0, im.width, im.height))
        w = max(1, round(solid.width * height / solid.height))
        out = solid.resize((w, height), Image.LANCZOS)
    except Exception as exc:  # noqa: BLE001 - missing/corrupt asset -> no silhouette
        print(f"[display] silhouette '{name}' failed: {exc}")
    _SIL_CACHE[key] = out
    return out


def _splayed_antlers(name: str, color, height: int, deg: int = 12, overlap: int = 18,
                     center_clear: int = 14):
    """Recolor the antler art, split it down the middle and rotate each half
    outward, so the rack splays like a real moose and lifts off the title text.
    ``center_clear`` erases a central strip to drop the inner base curls."""
    key = ("splay", name, tuple(color), height, deg, overlap, center_clear)
    if key in _SIL_CACHE:
        return _SIL_CACHE[key]
    base = _silhouette_image(name, color, height)
    out = None
    if base is not None:
        w, h = base.size
        m = w // 2
        left = base.crop((0, 0, m, h)).rotate(deg, expand=True, resample=Image.BICUBIC)
        right = base.crop((m, 0, w, h)).rotate(-deg, expand=True, resample=Image.BICUBIC)
        cw, ch = left.width + right.width - overlap, max(left.height, right.height)
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        canvas.alpha_composite(left, (0, ch - left.height))
        canvas.alpha_composite(right, (cw - right.width, ch - right.height))
        if center_clear > 0:          # erase the inner base curls at the center
            px = canvas.load()
            cx = canvas.width // 2
            for x in range(max(0, cx - center_clear), min(canvas.width, cx + center_clear)):
                for y in range(canvas.height):
                    r, g, b, _ = px[x, y]
                    px[x, y] = (r, g, b, 0)
        out = canvas.crop(canvas.getbbox() or (0, 0, cw, ch))
    _SIL_CACHE[key] = out
    return out


def _knockout_image(name: str, height: int):
    """Load assets/<name> keeping its colors but making a near-white background
    transparent, scaled to `height` px. Cached in _SIL_CACHE under a distinct key."""
    key = ("knock", name, height)
    if key in _SIL_CACHE:
        return _SIL_CACHE[key]
    out = None
    try:
        im = Image.open(ASSETS_DIR / name).convert("RGBA")
        data = [(r, g, b, 0) if (r > 238 and g > 238 and b > 238) else (r, g, b, a)
                for (r, g, b, a) in im.getdata()]
        im.putdata(data)
        im = im.crop(im.getbbox() or (0, 0, im.width, im.height))
        w = max(1, round(im.width * height / im.height))
        out = im.resize((w, height), Image.LANCZOS)
    except Exception as exc:  # noqa: BLE001
        print(f"[display] knockout '{name}' failed: {exc}")
    _SIL_CACHE[key] = out
    return out


_GAMEDAY_CACHE: dict = {}


def _is_gameday(team_id: str, tz: str) -> bool:
    """True if the ESPN NFL team plays today (its local calendar day). Best-effort,
    cached ~3h so renders don't hit the network each time; any failure -> False."""
    import time
    now = time.time()
    hit = _GAMEDAY_CACHE.get(team_id)
    if hit and now - hit[0] < 3 * 3600:
        dates = hit[1]
    else:
        dates = set()
        try:
            import requests
            url = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/"
                   f"{team_id}/schedule")
            data = requests.get(url, timeout=6).json()
            for ev in data.get("events", []):
                raw = ev.get("date")
                if not raw:
                    continue
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if ZoneInfo and tz:
                    dt = dt.astimezone(ZoneInfo(tz))
                dates.add(dt.date().isoformat())
        except Exception as exc:  # noqa: BLE001
            print(f"[display] gameday check failed: {exc}")
        _GAMEDAY_CACHE[team_id] = (now, dates)
    today = (datetime.now(ZoneInfo(tz)) if (ZoneInfo and tz) else datetime.now()).date()
    return today.isoformat() in dates
ICON_BUS = ""     # fa-bus
ICON_TRAIN = ""   # fa-train-subway


# ---------------------------------------------------------------- helpers ----

def is_raspberry_pi() -> bool:
    model = Path("/proc/device-tree/model")
    try:
        return model.exists() and "raspberry pi" in model.read_text().lower()
    except OSError:
        return False


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default()
    for path in _FONT_CANDIDATES.get(weight, []):
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
    _FONT_CACHE[key] = font  # type: ignore[assignment]
    return font  # type: ignore[return-value]


def _icon_font(size: int) -> ImageFont.FreeTypeFont:
    key = ("__icon__", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        font = ImageFont.truetype(str(ICON_FONT_PATH), size)
    except OSError:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font  # type: ignore[assignment]
    return font  # type: ignore[return-value]


def _tw(draw, text, font) -> int:
    l, _, r, _ = draw.textbbox((0, 0), text, font=font)
    return r - l


def _truncate(draw, text, font, max_w) -> str:
    if _tw(draw, text, font) <= max_w:
        return text
    while text and _tw(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def _fit_font(draw, text, weight, max_w, max_size, min_size=16):
    """Largest font (down to min_size) at which text fits within max_w."""
    size = max_size
    while size > min_size and _tw(draw, text, _font(weight, size)) > max_w:
        size -= 2
    return _font(weight, size)


def _rgb(value, fallback=(0, 0, 0)):
    if not value:
        return fallback
    v = value.lstrip("#")
    if len(v) != 6:
        return fallback
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _contrast(bg) -> tuple[int, int, int]:
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return BLACK if lum > 150 else WHITE


def _clock(tz: str, label: str = "") -> str:
    """Current time in the theme's timezone with a fixed label, e.g. '4:33 PM CST'.

    The label is fixed (not %Z) so it always reads "CST"/"EST" rather than
    flipping to CDT/EDT during daylight saving.
    """
    now = datetime.now()
    if tz and ZoneInfo is not None:
        try:
            now = datetime.now(ZoneInfo(tz))
        except Exception:  # noqa: BLE001 - bad/missing tz db -> local time
            now = datetime.now()
    suffix = label or (now.strftime("%Z"))
    return f"{now.strftime('%-I:%M %p')} {suffix}".strip()


def simulate_eink(img: Image.Image) -> Image.Image:
    """Quantize + Floyd-Steinberg dither to the panel's 6 colors (for QA)."""
    pal = Image.new("P", (1, 1))
    flat = [c for rgb in EINK_PALETTE for c in rgb]
    flat += [0, 0, 0] * (256 - len(EINK_PALETTE))
    pal.putpalette(flat)
    return img.convert("RGB").quantize(palette=pal, dither=Image.FLOYDSTEINBERG).convert("RGB")


# ------------------------------------------------------------------ header ---

def _draw_star(draw, cx, cy, r, color):
    """A small filled 6-pointed star (Chicago flag)."""
    import math
    pts = []
    for i in range(12):
        ang = math.pi / 2 + i * math.pi / 6
        rad = r if i % 2 == 0 else r * 0.5
        pts.append((cx + rad * math.cos(ang), cy - rad * math.sin(ang)))
    draw.polygon(pts, fill=color)


def _draw_nyc_flag(draw, x, cy, w, h):
    """Small NYC flag: blue | white | orange vertical tricolor, centered at cy."""
    top = cy - h // 2
    blue, orange = (0, 40, 104), (255, 99, 25)
    third = w // 3
    draw.rectangle([x, top, x + third, top + h], fill=blue)
    draw.rectangle([x + third, top, x + 2 * third, top + h], fill=(255, 255, 255))
    draw.rectangle([x + 2 * third, top, x + w, top + h], fill=orange)
    draw.rectangle([x, top, x + w, top + h], outline=(180, 184, 190), width=1)


def _weather_icon(draw, x, y, size, icon, theme: Theme, is_day: bool = True):
    """Draw a tiny weather glyph. Defaults (amber sun, grey cloud) read on light
    bands; a theme may set ``weather_icon`` to force ONE dark color where the
    amber sun would vanish (e.g. a gold band) — that override is per-device, so
    only boards that ask for it change. At night a clear sky shows a moon."""
    override = _rgb(theme.weather_icon) if theme.weather_icon else None
    sun = override or _rgb("#F4B400")
    cloud = override or (170, 180, 190)
    accent = override or _rgb(theme.accent)
    cx, cy = x + size // 2, y + size // 2
    if icon == "clear" and not is_day:      # clear night -> crescent moon
        r = size * 0.32
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=sun)
        bg = _rgb(theme.band)               # carve the crescent with the band color
        draw.ellipse([cx - r + r * 0.62, cy - r - r * 0.15,
                      cx + r + r * 0.62, cy + r - r * 0.15], fill=bg)
        return
    if icon == "clear":
        import math
        r = size * 0.28
        for i in range(8):
            a = i * math.pi / 4
            draw.line([(cx + math.cos(a) * r * 1.3, cy + math.sin(a) * r * 1.3),
                       (cx + math.cos(a) * r * 1.9, cy + math.sin(a) * r * 1.9)],
                      fill=sun, width=2)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=sun)
        return
    # cloud base for the rest
    draw.ellipse([x + 2, cy - 2, x + size * 0.55, cy + size * 0.32], fill=cloud)
    draw.ellipse([x + size * 0.30, y + 4, x + size * 0.85, cy + size * 0.30], fill=cloud)
    draw.ellipse([x + size * 0.45, cy - 4, x + size - 2, cy + size * 0.32], fill=cloud)
    if icon == "rain":
        for dx in (0.35, 0.55, 0.75):
            draw.line([(x + size * dx, cy + size * 0.34), (x + size * dx - 3, y + size - 2)],
                      fill=accent, width=2)
    elif icon == "snow":
        for dx in (0.35, 0.55, 0.75):
            draw.ellipse([x + size * dx - 2, y + size - 8, x + size * dx + 2, y + size - 4],
                         fill=accent)
    elif icon == "storm":
        draw.line([(cx, cy + size * 0.30), (cx - 4, y + size - 2)], fill=sun, width=3)


def _draw_antler(draw, cx, by, d, color, sx, sy):
    """One broad palmate antler (a filled paddle with finger tines) sweeping out
    from the center. d=+1 is the right side, d=-1 the left; sx/sy scale it."""
    def p(ux, uy):                     # units -> pixels (x mirrors on d, y is up)
        return (cx + d * ux * sx, by - uy * sy)
    # outline: inner base -> smooth lower edge outward -> tined top edge back in
    pts = [p(1.5, 0), p(7, 1.5), p(13, 3), p(18.5, 4.5), p(22, 8.5),
           p(20.5, 9.5), p(21.5, 15.5),      # outer finger
           p(18, 9.5), p(18, 16.5),          # finger
           p(14.5, 9), p(13.5, 17.5),        # tall middle finger
           p(10, 8.5), p(8.5, 14.5),         # finger
           p(5.5, 8), p(4, 12),              # inner finger
           p(2, 6.5)]                        # inner top
    draw.polygon(pts, fill=color)


def _draw_moose(draw, x_before, wo, mid, color):
    """Turn the 'oo' of a title into a moose: the two o's are the eyes, and a wide
    symmetric antler rack sits above them."""
    cx = x_before + wo                 # center between the two 'o' eyes
    by = mid - 10                      # antlers rise from just above the eyes
    sx, sy = wo / 15.0 * 1.1, wo / 15.0
    _draw_antler(draw, cx, by, -1, color, sx, sy)
    _draw_antler(draw, cx, by, +1, color, sx, sy)


def _draw_gameday(img, draw, theme: Theme, head_h: int, accent):
    """Game-day splash across the middle of the header: the team graphic (e.g.
    GO PACK GO) with a small 'GAME DAY' label beneath it."""
    cx = 438                                   # middle band, clear of title/weather
    gold = _rgb(theme.band_stripe or theme.accent)
    gd = _knockout_image(theme.gameday_img, int(head_h * 0.5)) if theme.gameday_img else None
    if gd is not None:
        if gd.width > 300:                     # keep it inside the middle band
            gd = gd.resize((300, max(1, round(gd.height * 300 / gd.width))), Image.LANCZOS)
        gy = max(4, (head_h - gd.height - 16) // 2)
        img.paste(gd, (int(cx - gd.width / 2), gy), gd)
        draw.text((cx, gy + gd.height + 3), "GAME DAY", font=_font("bold", 14),
                  fill=gold, anchor="mm")
    else:                                      # graphic not supplied yet -> text only
        draw.text((cx, head_h // 2 - 9), "GO PACK GO", font=_font("bold", 24),
                  fill=accent, anchor="mm")
        draw.text((cx, head_h // 2 + 12), "GAME DAY", font=_font("bold", 13),
                  fill=gold, anchor="mm")


def _draw_header(img, draw, theme: Theme, title: str, weather) -> int:
    head_h = theme.header_h or 84
    s = head_h / 84.0            # scale header content up on taller headers (Advait=1.0)
    mid = head_h // 2 + round((head_h - 84) * 0.3)   # nudge content down on taller headers
    draw.rectangle([0, 0, WIDTH, head_h], fill=_rgb(theme.band))
    accent = _rgb(theme.accent)

    # title (left, vertically centered in the header band)
    f_title = _font("bold", round(34 * s))
    title = _truncate(draw, title, f_title, 480)

    # turn the "oo" into a moose: the antler rack is drawn BEHIND the text and
    # dropped down to the eyes, so the 'oo' render in front of it (flush, not
    # floating high above). Prefer the silhouette asset; fall back to drawn antlers.
    if theme.moose_head:
        i = title.lower().find("oo")
        if i != -1:
            xb = 22 + _tw(draw, title[:i], f_title)
            wo = _tw(draw, "o", f_title)
            em = (_splayed_antlers(theme.antler_img, accent, max(18, int((head_h // 2 - 4) * 0.9 * s)))
                  if theme.antler_img else None)
            if em is not None:
                ax = int(xb + wo - em.width / 2) + round(6 * s)   # nudged slightly right
                ay = (mid + 8) - em.height
                # reveal only the antlers ABOVE the top of the 'oo'; clip anything
                # behind/below the eyes so the rack emerges cleanly from the eye line
                o_top = draw.textbbox((xb, mid), "o", font=f_title, anchor="lm")[1]
                keep = max(0, min(em.height, o_top - ay))
                if keep:
                    top = em.crop((0, 0, em.width, keep))
                    # place the two antlers separately so the right one can be pulled
                    # inward while the left one stays exactly where it is
                    cxm = top.width // 2
                    pull = round(10 * s)
                    left_half = top.crop((0, 0, cxm, top.height))
                    right_half = top.crop((cxm, 0, top.width, top.height))
                    img.paste(left_half, (ax, ay), left_half)
                    img.paste(right_half, (ax + cxm - pull, ay), right_half)
            else:
                _draw_moose(draw, xb, wo, mid, accent)

    draw.text((22, mid), title, font=f_title, fill=accent, anchor="lm")

    # game day (surprise): the animated GO PACK GO graphic is overlaid live by the
    # browser display page (see web/app.py); nothing is baked into the static board.

    # city emblem next to the title (same vertical center)
    emblem_x = 22 + _tw(draw, title, f_title) + 22
    if theme.emblem == "image" and theme.emblem_img:
        em = _emblem_image(theme.emblem_img, round(66 * s))
        if em is not None and emblem_x + em.width < 520:
            img.paste(em, (emblem_x, mid - em.height // 2), em)
    elif theme.emblem == "stars" and theme.stars:
        sx = emblem_x + 4
        for _ in range(theme.stars):
            if sx + 22 > 520:
                break
            _draw_star(draw, sx, mid, 9, _rgb(theme.star_color))
            sx += 26
    elif theme.emblem == "nycflag" and emblem_x + 46 < 520:
        _draw_nyc_flag(draw, emblem_x, mid, 46, 28)

    # right cluster: [condition / time]  [icon]  [temp]  — laid out horizontally
    # so it doesn't feel crowded stacked vertically.
    f_temp = _font("bold", round(32 * s))
    f_small = _font("regular", round(17 * s))
    clock = _clock(theme.tz, theme.tz_label)
    icon_sz = round(32 * s)
    gap = round(12 * s)
    yoff = round(11 * s)

    right = WIDTH - 22
    if weather is not None:
        temp = f"{weather.temp_f}°"
        temp_fill = _rgb(theme.temp_color) if theme.temp_color else _rgb(theme.ink)
        draw.text((right, mid), temp, font=f_temp, fill=temp_fill, anchor="rm")
        icon_x = right - _tw(draw, temp, f_temp) - gap - icon_sz
        _weather_icon(draw, icon_x, mid - icon_sz // 2, icon_sz, weather.icon, theme,
                      getattr(weather, "is_day", True))
        text_right = icon_x - gap
        if weather.condition:
            draw.text((text_right, mid - yoff), weather.condition, font=f_small,
                      fill=_rgb(theme.muted), anchor="rm")
        draw.text((text_right, mid + yoff), clock, font=f_small,
                  fill=_rgb(theme.muted), anchor="rm")
    else:
        draw.text((right, mid), clock, font=f_temp, fill=_rgb(theme.muted), anchor="rm")

    if theme.band_stripe:  # two-tone divider (e.g. green + gold)
        draw.rectangle([0, head_h, WIDTH, head_h + 3], fill=accent)
        draw.rectangle([0, head_h + 3, WIDTH, head_h + 6], fill=_rgb(theme.band_stripe))
    else:
        draw.rectangle([0, head_h, WIDTH, head_h + 4], fill=accent)
    return head_h + 4


# -------------------------------------------------------------------- rows ---

def _badge_font(draw, departures, badge_w, max_size):
    """One shared route-badge font sized so the LONGEST label fits.

    Keeps every badge's text the same size (so "Red" isn't huge next to "Brown").
    """
    routes = [d.route or "" for d in departures] or [""]
    longest = max(routes, key=lambda s: _tw(draw, s, _font("bold", max_size)))
    return _fit_font(draw, longest, "bold", badge_w - 14, max_size, 16)


_COMPASS_DIRS = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}


def _draw_compass(draw, cx, cy, r, direction, ring, needle):
    """A small compass: a ring with a needle pointing the cardinal direction."""
    d = (direction or "").strip().lower()
    dx, dy = next((v for name, v in _COMPASS_DIRS.items() if d.startswith(name)), (0, -1))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ring, width=2)
    apex = (cx + dx * r * 0.78, cy + dy * r * 0.78)      # needle tip
    tail = (cx - dx * r * 0.30, cy - dy * r * 0.30)      # opposite end
    px, py = -dy, dx                                     # perpendicular for the base
    b = r * 0.34
    draw.polygon([apex, (tail[0] + px * b, tail[1] + py * b),
                  (tail[0] - px * b, tail[1] - py * b)], fill=needle)
    draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=ring)  # hub


def _draw_row(draw, dep, x, y, w, h, theme: Theme, alt: bool, show_line: bool,
              route_font=None, show_stop=False):
    pad = 16
    if alt:
        draw.rectangle([x, y, x + w, y + h], fill=_rgb(theme.row_alt))

    cy = y + h // 2

    # route badge (shared font across the board for consistent sizing)
    badge_w, badge_h = 122, h - 18
    if dep.color and not theme.flat_badges:
        badge_color = _rgb(dep.color)
    elif dep.icon_label and theme.lr_color:  # light-rail rows get their own badge color
        badge_color = _rgb(theme.lr_color)
    else:  # differentiate bus vs rail badges when the feed sets no route color
        badge_color = _rgb((theme.bus_color if dep.mode == "bus" else theme.rail_color)
                           or theme.accent)
    draw.rounded_rectangle([x + pad, y + 9, x + pad + badge_w, y + 9 + badge_h],
                           radius=10, fill=badge_color)
    badge_cx = x + pad + badge_w // 2
    if dep.icon_label:  # e.g. light rail: route on top, tiny caption inside the tab
        f_route = _fit_font(draw, dep.route, "bold", badge_w - 16,
                            min(38, badge_h - 22), 16)
        draw.text((badge_cx, cy - 9), dep.route, font=f_route,
                  fill=_contrast(badge_color), anchor="mm")
        draw.text((badge_cx, cy + 16), dep.icon_label.upper(), font=_font("bold", 11),
                  fill=_contrast(badge_color), anchor="mm")
    else:
        f_route = route_font or _fit_font(draw, dep.route, "bold", badge_w - 16,
                                          min(52, badge_h - 4), 16)
        draw.text((badge_cx, cy), dep.route, font=f_route,
                  fill=_contrast(badge_color), anchor="mm")

    # secondary label shown inline, to the right of the destination
    sub = []
    if show_stop and dep.stop_name:
        sub.append(dep.stop_name)
    elif show_line and dep.feed_type:
        sub.append(dep.feed_type.upper())
    if dep.detail:
        sub.append(dep.detail)
    sub_text = "  ·  ".join(sub)

    # one vertically-centered line: badge | to {dest}  {stop label} ......... ETA
    # gap after the badge equals the left margin (pad) so spacing is symmetric.
    dest_x = x + pad + badge_w + pad
    icon_char = ICON_BUS if dep.mode == "bus" else ICON_TRAIN
    ic_font = _icon_font(36)
    # center the icon in a FIXED-WIDTH column so bus & train glyphs line up and
    # the text after them starts at the same x on every row (their widths differ)
    icon_col = 42
    draw.text((dest_x + icon_col // 2, cy), icon_char, font=ic_font,
              fill=_rgb(theme.muted), anchor="mm")
    dest_x += icon_col + 10
    f_to = _font("regular", 18)
    # fit_destination boards trade a touch of ETA whitespace + a slightly smaller
    # destination/stop label for room, so full names fit instead of truncating.
    f_dest = _font("bold", 30 if theme.fit_destination else 34)
    f_sub = _font("regular", 16) if theme.fit_destination else f_to
    content_right = x + w - pad - (150 if theme.fit_destination else 170)

    # primary label: a compass + direction (opt-in, buses) OR "to <destination>"
    if dep.direction:
        cr = 15
        _draw_compass(draw, dest_x + cr, cy, cr, dep.direction,
                      _rgb(theme.muted), _rgb(theme.accent))
        prim_x = dest_x + 2 * cr + 12
        prefix_w = 0
    else:
        prim_x = dest_x
        prefix_w = _tw(draw, "to ", f_to)

    avail = content_right - prim_x - prefix_w
    if sub_text and not theme.fit_destination:
        # normally the stop label reserves space; the destination takes the rest
        stop_reserve = min(120, max(70, int(avail * 0.38)))
        prim_budget = avail - stop_reserve - 12
    else:
        # fit_destination: destination gets the full width; stop label takes leftovers
        prim_budget = avail

    if not dep.direction:
        draw.text((prim_x, cy), "to", font=f_to, fill=_rgb(theme.muted), anchor="lm")
    primary = dep.direction or dep.destination or "—"
    primary = _truncate(draw, primary, f_dest, max(20, prim_budget))
    draw.text((prim_x + prefix_w, cy), primary, font=f_dest, fill=_rgb(theme.ink), anchor="lm")
    if sub_text:
        sx = prim_x + prefix_w + _tw(draw, primary, f_dest) + 16
        if content_right - sx > 36:
            draw.text((sx, cy + 2), _truncate(draw, sub_text, f_sub, content_right - sx),
                      font=f_sub, fill=_rgb(theme.muted), anchor="lm")

    # ETA (right)
    eta = dep.eta_label
    if dep.delayed:
        eta_color = _rgb("#D2202E")
        f_eta = _font("bold", 30)
    elif dep.minutes <= 0:
        eta_color = _rgb("#0E8A3E")
        f_eta = _font("bold", 36)
    else:
        eta_color = _rgb(theme.ink)
        f_eta = _font("bold", 36)
    draw.text((x + w - pad, cy), eta, font=f_eta, fill=eta_color, anchor="rm")




def _empty(draw, theme, text, x, y, w, h):
    draw.text((x + w // 2, y + h // 2), text, font=_font("regular", 22),
              fill=_rgb(theme.muted), anchor="mm")


def _warning_glyph(draw, cx, cy, r, color):
    """A simple hollow warning triangle with an exclamation mark."""
    draw.polygon([(cx, cy - r), (cx - r * 0.92, cy + r * 0.62), (cx + r * 0.92, cy + r * 0.62)],
                 outline=color, width=3)
    draw.line([(cx, cy - r * 0.28), (cx, cy + r * 0.16)], fill=color, width=3)
    draw.ellipse([cx - 2, cy + r * 0.30, cx + 2, cy + r * 0.30 + 4], fill=color)


def _draw_error(draw, theme, result, x, y, w, h):
    """A readable, reportable error card: warning glyph, friendly headline, a hint,
    and a small 'code · feed' tag Advait can read off and text to Emil."""
    cx = x + w // 2
    cy = y + h // 2
    warn = _rgb("#D2932E")
    _warning_glyph(draw, cx, cy - 46, 20, warn)
    headline = _truncate(draw, result.error or "Something went wrong",
                         _font("bold", 27), w - 40)
    draw.text((cx, cy - 4), headline, font=_font("bold", 27), fill=_rgb(theme.ink), anchor="mm")
    hint = getattr(result, "hint", "")
    if hint:
        draw.text((cx, cy + 28), _truncate(draw, hint, _font("regular", 19), w - 40),
                  font=_font("regular", 19), fill=_rgb(theme.muted), anchor="mm")
    code = getattr(result, "error_code", "")
    tag = f"code {code}  ·  {result.label}" if code else result.label
    draw.text((cx, cy + 58), tag, font=_font("regular", 15), fill=_rgb(theme.muted), anchor="mm")


# ----------------------------------------------------------------- render ----

def render_image(results, config=None, weather=None) -> Image.Image:
    """Build the 800x480 board image from a list of FeedResult."""
    config = config or {}
    display_cfg = config.get("display", {}) or {}
    mode = str(display_cfg.get("mode", "sections")).lower()
    theme = resolve_theme(config, results)

    img = Image.new("RGB", (WIDTH, HEIGHT), _rgb(theme.bg))
    draw = ImageDraw.Draw(img)

    title = config.get("title") or theme.title
    body_top = _draw_header(img, draw, theme, title, weather)
    body_h = HEIGHT - body_top

    if not results:
        _empty(draw, theme, "No feeds enabled — open http://transitpi.local",
               0, body_top, WIDTH, body_h)
        return img

    if mode == "stacked":
        _render_stacked(draw, results, theme, body_top, body_h)
    else:
        _render_sections(draw, results, theme, body_top, body_h)
    return img


def _render_stacked(draw, results, theme, top, height):
    merged = []
    for r in results:
        if r.ok:
            merged.extend(r.departures)
    merged.sort(key=lambda d: d.sort_key)

    errors = [r for r in results if not r.ok]
    row_h = 74
    max_rows = max(1, height // row_h)
    merged = merged[:max_rows]

    if not merged:
        if errors:
            _draw_error(draw, theme, errors[0], 0, top, WIDTH, height)
        else:
            _empty(draw, theme, "No upcoming departures", 0, top, WIDTH, height)
        return

    badge_font = _badge_font(draw, merged, 122, 52)
    show_stop = any(d.stop_name for d in merged)
    for i, dep in enumerate(merged):
        _draw_row(draw, dep, 0, top + i * row_h, WIDTH, row_h, theme, route_font=badge_font,
                  alt=(i % 2 == 1), show_line=True, show_stop=show_stop)


def _render_sections(draw, results, theme, top, height):
    section_h = height // len(results)
    f_label = _font("bold", 22)
    # a single feed fills a board on its own — the header already names it, so
    # skip the redundant per-section label and give all the height to rows.
    show_labels = len(results) > 1
    for i, result in enumerate(results):
        sy = top + i * section_h
        if i > 0:
            draw.line([(16, sy), (WIDTH - 16, sy)], fill=_rgb("#E2E4E1"), width=1)

        label_h = 32 if show_labels else 0
        if show_labels:
            draw.text((18, sy + label_h // 2 + 4),
                      _truncate(draw, result.label, f_label, WIDTH - 36),
                      font=f_label, fill=_rgb(theme.accent), anchor="lm")

        rows_top = sy + label_h + 2
        rows_h = section_h - label_h - 4
        if not result.ok:
            _draw_error(draw, theme, result, 18, rows_top, WIDTH - 36, rows_h)
            continue
        if not result.departures:
            _empty(draw, theme, "No upcoming departures", 18, rows_top, WIDTH - 36, rows_h)
            continue

        # grow rows to fill the section (clamped) instead of leaving a gap
        n = max(1, min(len(result.departures), rows_h // 50))
        row_h = max(50, min(92, rows_h // n))
        shown = result.departures[:n]
        badge_font = _badge_font(draw, shown, 122, 52)
        show_stop = any(d.stop_name for d in shown)
        for j, dep in enumerate(shown):
            _draw_row(draw, dep, 0, rows_top + j * row_h, WIDTH, row_h, theme,
                      route_font=badge_font, alt=(j % 2 == 1), show_line=False,
                      show_stop=show_stop)


# ----------------------------------------------------------------- Display ---

class Display:
    """Renders to the Inky panel on a Pi, or to preview.png everywhere else."""

    def __init__(self, config=None, preview_path=None):
        self.config = config or {}
        self.preview_path = Path(preview_path) if preview_path else PREVIEW_PATH
        self.on_pi = is_raspberry_pi()
        self._inky = None
        if self.on_pi:
            try:
                from inky.auto import auto  # type: ignore

                self._inky = auto()
            except Exception as exc:  # noqa: BLE001
                print(f"[display] Inky init failed, falling back to PNG: {exc}")
                self.on_pi = False

    def show(self, results, config=None, weather=None) -> Image.Image:
        config = config or self.config
        if weather is None:
            try:
                from weather import get_weather

                weather = get_weather(config)
            except Exception as exc:  # noqa: BLE001
                print(f"[display] weather fetch skipped: {exc}")
        img = render_image(results, config, weather)
        try:
            img.save(self.preview_path)
        except OSError as exc:
            print(f"[display] could not write preview: {exc}")

        if self.on_pi and self._inky is not None:
            try:
                panel_img = img.resize(self._inky.resolution)
                # 7-color Impression panels accept a saturation hint; the newer
                # 6-color Spectra (E673) driver doesn't — fall back gracefully.
                try:
                    self._inky.set_image(panel_img, saturation=0.7)
                except TypeError:
                    self._inky.set_image(panel_img)
                self._inky.show()
            except Exception as exc:  # noqa: BLE001
                print(f"[display] Inky show failed: {exc}")
        return img
