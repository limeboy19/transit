#!/usr/bin/env python3
"""Transit display main loop.

Fetch all enabled feeds -> render to the Inky panel (or preview.png) -> sleep.
The Flask config UI is started in a background thread so the whole app is a
single process you can run from systemd.

Run:
    python3 main.py            # loop + web UI on :5000
    python3 main.py --once     # render a single frame and exit (handy for dev)
    python3 main.py --no-web   # loop only, no web UI
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from appconfig import load_config
from fetcher import fetch_all
from renderer import Display


def run_once(display: Display) -> None:
    config = load_config()
    results = fetch_all(config)
    display.show(results, config)
    enabled = [f for f in config.get("feeds", []) if f.get("enabled")]
    print(f"[loop] rendered {len(results)} feed(s) "
          f"({sum(len(r.departures) for r in results)} departures) "
          f"from {len(enabled)} enabled")


def loop(display: Display) -> None:
    while True:
        try:
            run_once(display)
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            print(f"[loop] error: {exc}")
        refresh = max(15, int(load_config().get("refresh_seconds", 60)))
        time.sleep(refresh)


def start_web(port: int = 5000) -> None:
    from web.app import app  # imported lazily so --no-web has no Flask dep need
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raspberry Pi transit display")
    parser.add_argument("--once", action="store_true", help="render one frame and exit")
    parser.add_argument("--no-web", action="store_true", help="don't start the web UI")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TRANSIT_PORT", 5000)),
                        help="web UI port (default 5000; macOS often needs e.g. 8080)")
    args = parser.parse_args(argv)

    display = Display(load_config())
    print(f"[startup] running on {'Raspberry Pi (Inky)' if display.on_pi else 'dev (preview.png)'}")

    if args.once:
        run_once(display)
        return 0

    if not args.no_web:
        threading.Thread(target=start_web, args=(args.port,), daemon=True, name="web").start()
        print(f"[startup] web UI on http://localhost:{args.port}")

    try:
        loop(display)
    except KeyboardInterrupt:
        print("\n[shutdown] bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
