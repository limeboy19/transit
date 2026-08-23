"""Tiny error log for the display.

Keeps the last N events in memory (for the /health page) and appends a
size-capped ``error.log`` next to the app (survives ``git reset --hard`` because
it is gitignored). Everything here is best-effort and never raises — logging a
problem must not itself break the render loop.
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "error.log"
_MAX_MEM = 50
_MAX_BYTES = 200_000
_recent: "deque[dict]" = deque(maxlen=_MAX_MEM)


def log_error(label: str, feed_type: str, code: str, headline: str, detail: str) -> None:
    """Record one feed error to memory + file. Best-effort; swallows its own errors."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    _recent.append({
        "time": ts, "label": label, "feed": feed_type,
        "code": code, "headline": headline, "detail": detail,
    })
    try:
        line = f"{ts}  [{code}] {label} ({feed_type}): {headline}  |  {detail}\n"
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_BYTES:
            # keep the most recent half so the file can't grow without bound
            tail = _LOG_PATH.read_text(errors="ignore")[-_MAX_BYTES // 2:]
            _LOG_PATH.write_text(tail)
        with _LOG_PATH.open("a") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001 - logging must never break the caller
        pass


def recent_errors(limit: int = 50) -> "list[dict]":
    """Most-recent-first list of recent error events (for the /health page)."""
    return list(_recent)[-limit:][::-1]
