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


def _weather_icon(draw, x, y, size, icon, theme: Theme):
    """Draw a tiny weather glyph in a size×size box at (x, y)."""
    sun = _rgb("#F4B400")
    cloud = (170, 180, 190)
    blue = _rgb(theme.accent)
    cx, cy = x + size // 2, y + size // 2
    if icon == "clear":
        r = size * 0.28
        for i in range(8):
            import math
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
                      fill=blue, width=2)
    elif icon == "snow":
        for dx in (0.35, 0.55, 0.75):
            draw.ellipse([x + size * dx - 2, y + size - 8, x + size * dx + 2, y + size - 4],
                         fill=blue)
    elif icon == "storm":
        draw.line([(cx, cy + size * 0.30), (cx - 4, y + size - 2)], fill=_rgb("#F4B400"), width=3)


def _draw_header(draw, theme: Theme, title: str, weather) -> int:
    head_h = 84
    mid = head_h // 2
    draw.rectangle([0, 0, WIDTH, head_h], fill=_rgb(theme.band))
    accent = _rgb(theme.accent)

    # title (left, vertically centered in the header band)
    f_title = _font("bold", 34)
    title = _truncate(draw, title, f_title, 480)
    draw.text((22, mid), title, font=f_title, fill=accent, anchor="lm")

    # city emblem next to the title (same vertical center)
    emblem_x = 22 + _tw(draw, title, f_title) + 22
    if theme.emblem == "stars" and theme.stars:
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
    f_temp = _font("bold", 32)
    f_small = _font("regular", 17)
    clock = _clock(theme.tz, theme.tz_label)

    right = WIDTH - 22
    if weather is not None:
        temp = f"{weather.temp_f}°"
        draw.text((right, mid), temp, font=f_temp, fill=_rgb(theme.ink), anchor="rm")
        icon_x = right - _tw(draw, temp, f_temp) - 12 - 32
        _weather_icon(draw, icon_x, mid - 16, 32, weather.icon, theme)
        text_right = icon_x - 12
        if weather.condition:
            draw.text((text_right, mid - 11), weather.condition, font=f_small,
                      fill=_rgb(theme.muted), anchor="rm")
        draw.text((text_right, mid + 11), clock, font=f_small,
                  fill=_rgb(theme.muted), anchor="rm")
    else:
        draw.text((right, mid), clock, font=f_temp, fill=_rgb(theme.muted), anchor="rm")

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


def _draw_row(draw, dep, x, y, w, h, theme: Theme, alt: bool, show_line: bool,
              route_font=None, show_stop=False):
    pad = 16
    if alt:
        draw.rectangle([x, y, x + w, y + h], fill=_rgb(theme.row_alt))

    cy = y + h // 2

    # route badge (shared font across the board for consistent sizing)
    badge_w, badge_h = 92, h - 18
    badge_color = _rgb(dep.color) if dep.color else _rgb(theme.accent)
    draw.rounded_rectangle([x + pad, y + 9, x + pad + badge_w, y + 9 + badge_h],
                           radius=10, fill=badge_color)
    f_route = route_font or _fit_font(draw, dep.route, "bold", badge_w - 14,
                                      min(40, badge_h - 4), 16)
    draw.text((x + pad + badge_w // 2, cy), dep.route, font=f_route,
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
    if dep.mode == "bus":
        _draw_bus_icon(draw, dest_x, cy, _rgb(theme.ink))
        dest_x += 34
    f_to = _font("regular", 18)
    f_dest = _font("bold", 34)
    content_right = x + w - pad - 170        # reserve room for the ETA on the right
    to_w = _tw(draw, "to ", f_to)

    if sub_text:  # split the room: destination gets ~half, the stop label the rest
        dest_budget = max(110, int((content_right - dest_x) * 0.5)) - to_w
    else:
        dest_budget = (content_right - dest_x) - to_w
    draw.text((dest_x, cy), "to", font=f_to, fill=_rgb(theme.muted), anchor="lm")
    dest = _truncate(draw, dep.destination or "—", f_dest, max(20, dest_budget))
    draw.text((dest_x + to_w, cy), dest, font=f_dest, fill=_rgb(theme.ink), anchor="lm")
    if sub_text:
        sx = dest_x + to_w + _tw(draw, dest, f_dest) + 16
        if content_right - sx > 36:
            draw.text((sx, cy + 1), _truncate(draw, sub_text, f_to, content_right - sx),
                      font=f_to, fill=_rgb(theme.muted), anchor="lm")

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


def _draw_bus_icon(draw, x, cy, color):
    """Tiny bus glyph (~26px) vertically centered at cy, left edge at x."""
    w, h = 26, 18
    top = cy - h // 2
    draw.rounded_rectangle([x, top, x + w, top + h], radius=4, outline=color, width=2)
    # windows
    draw.line([(x + 4, top + 6), (x + w - 4, top + 6)], fill=color, width=2)
    # wheels
    draw.ellipse([x + 4, top + h - 3, x + 9, top + h + 2], fill=color)
    draw.ellipse([x + w - 9, top + h - 3, x + w - 4, top + h + 2], fill=color)


def _empty(draw, theme, text, x, y, w, h):
    draw.text((x + w // 2, y + h // 2), text, font=_font("regular", 22),
              fill=_rgb(theme.muted), anchor="mm")


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
    body_top = _draw_header(draw, theme, title, weather)
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
        msg = errors[0].error if errors else "No upcoming departures"
        _empty(draw, theme, msg, 0, top, WIDTH, height)
        return

    badge_font = _badge_font(draw, merged, 92, 40)
    multi_stop = len({d.stop_name for d in merged if d.stop_name}) > 1
    for i, dep in enumerate(merged):
        _draw_row(draw, dep, 0, top + i * row_h, WIDTH, row_h, theme, route_font=badge_font,
                  alt=(i % 2 == 1), show_line=True, show_stop=multi_stop)


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
            _empty(draw, theme, f"⚠ {result.error}", 18, rows_top, WIDTH - 36, rows_h)
            continue
        if not result.departures:
            _empty(draw, theme, "No upcoming departures", 18, rows_top, WIDTH - 36, rows_h)
            continue

        # grow rows to fill the section (clamped) instead of leaving a gap
        n = max(1, min(len(result.departures), rows_h // 50))
        row_h = max(50, min(92, rows_h // n))
        shown = result.departures[:n]
        badge_font = _badge_font(draw, shown, 92, 40)
        multi_stop = len({d.stop_name for d in shown if d.stop_name}) > 1
        for j, dep in enumerate(shown):
            _draw_row(draw, dep, 0, rows_top + j * row_h, WIDTH, row_h, theme,
                      route_font=badge_font, alt=(j % 2 == 1), show_line=False,
                      show_stop=multi_stop)


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
