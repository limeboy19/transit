#!/usr/bin/env python3
"""Render the tracker layout with sample data — no Pi, no API keys.

Examples:
    python3 dev_preview.py                 # CTA sections -> preview.png
    python3 dev_preview.py --theme mta     # NYC subway sample
    python3 dev_preview.py --stacked       # merged chronological list
    python3 dev_preview.py --all           # write preview_<theme>.png for each
    python3 dev_preview.py --dither        # simulate the real 6-color panel
"""

from __future__ import annotations

import argparse
from pathlib import Path

from renderer.demo import sample_results, sample_stacked
from renderer.display import render_image, simulate_eink

HERE = Path(__file__).resolve().parent


class _W:  # tiny stand-in for a Weather object
    def __init__(self, t, c, i):
        self.temp_f, self.condition, self.icon = t, c, i


def _render(theme, stacked, dither, out):
    results = sample_stacked() if stacked else sample_results(theme)
    config = {
        "title": "",
        "display": {"mode": "stacked" if stacked else "sections", "theme": theme},
        "feeds": [{"type": theme, "enabled": True}],
    }
    weather = _W(39, "Partly cloudy", "clouds")
    img = render_image(results, config, weather)
    if dither:
        img = simulate_eink(img)
    img.save(out)
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="cta", choices=["cta", "mta", "njt"])
    ap.add_argument("--stacked", action="store_true")
    ap.add_argument("--dither", action="store_true", help="simulate 6-color panel")
    ap.add_argument("--all", action="store_true", help="write one PNG per theme")
    args = ap.parse_args()

    if args.all:
        for t in ("cta", "mta", "njt"):
            _render(t, False, args.dither, HERE / f"preview_{t}.png")
        _render("cta", True, args.dither, HERE / "preview_stacked.png")
        return
    _render(args.theme, args.stacked, args.dither, HERE / "preview.png")


if __name__ == "__main__":
    main()
