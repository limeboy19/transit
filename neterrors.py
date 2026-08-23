"""Turn noisy fetch/network exceptions into short, readable, reportable messages.

The raw exception from ``requests`` (and friends) is great for a log but useless
on a by-the-door display: it dumps the full request URL and a "(Caused by ...)"
chain that overflows the screen. :func:`classify_error` maps an exception to a
stable ``code`` (easy to report: "it says code NET") plus a friendly ``headline``
and ``hint`` for the board.
"""

from __future__ import annotations

import re


def classify_error(exc: Exception) -> tuple[str, str, str]:
    """Return ``(code, headline, hint)`` for an exception raised while fetching.

    ``code``      short + stable, safe to read aloud/report (e.g. "NET").
    ``headline``  one friendly line for the board.
    ``hint``      a short next-step for whoever is looking at it.
    """
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()

    # --- no internet / DNS / connection refused -------------------------------
    if (name in ("ConnectionError", "NewConnectionError", "ConnectionResetError",
                 "ConnectionAbortedError")
            or "failed to establish a new connection" in low
            or "name or service not known" in low
            or "temporary failure in name resolution" in low
            or "nodename nor servname" in low
            or "network is unreachable" in low
            or "connection refused" in low
            or "connection aborted" in low
            or "max retries exceeded" in low):
        return ("NET", "No internet connection", "Check the board's Wi-Fi")

    # --- slow / timed out -----------------------------------------------------
    if name in ("ConnectTimeout", "ReadTimeout", "Timeout") or "timed out" in low:
        return ("SLOW", "Transit service timed out", "Usually temporary — it will retry")

    # --- TLS / clock ----------------------------------------------------------
    if name == "SSLError" or "ssl" in low or "certificate" in low:
        return ("SSL", "Secure connection failed", "Check the board's date/time & Wi-Fi")

    # --- HTTP status errors (from raise_for_status) ---------------------------
    if name == "HTTPError" or "status code" in low or "for url" in low:
        m = re.search(r"\b([45]\d\d)\b", text)
        code = m.group(1) if m else ""
        if code.startswith("4"):
            return (f"HTTP{code}", f"Transit API refused the request ({code})",
                    "Emil may need to check the key or stops")
        return (f"HTTP{code or '5xx'}", f"Transit service is down ({code or '5xx'})",
                "It's on their end — it will retry")

    # --- credentials ----------------------------------------------------------
    if "api key" in low or "apikey" in low or "unauthorized" in low or "forbidden" in low:
        return ("KEY", "Transit API key was rejected", "Tell Emil to re-check the key")

    # --- fallback: still short + reportable -----------------------------------
    return ("DATA", "Couldn't read transit data", name)
