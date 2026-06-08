"""Abstract base for transit feed fetchers.

Every transit system (CTA, MTA, NJT, ...) implements a subclass of
``BaseFetcher`` that knows how to talk to one upstream API and normalize its
response into a list of :class:`Departure` objects. The renderer and main loop
only ever deal with ``Departure`` objects, so adding a new transit system is
just: write a fetcher, register it in ``fetcher/__init__.py``.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Departure:
    """A single upcoming departure/arrival, normalized across all feeds."""

    route: str                      # short route name, e.g. "Red", "R", "4"
    destination: str                # human-readable headsign / direction
    minutes: int                    # whole minutes until departure (0 == "Due")
    color: Optional[str] = None     # optional hex/route color for the badge
    delayed: bool = False           # agency flagged this run as delayed
    detail: str = ""                # small secondary text (run/bus #)
    feed_type: str = ""             # stamped by the fetcher ("cta"/"mta"/...)
    mode: str = "train"             # "train" or "bus" (drives the row icon)
    stop_name: str = ""             # which stop this came from (for multi-stop boards)

    @property
    def eta_label(self) -> str:
        if self.delayed:
            return "Delayed"
        if self.minutes <= 0:
            return "Due"
        return f"{self.minutes} min"

    @property
    def sort_key(self) -> int:
        # Delayed trains have unreliable minutes; sort them to the back.
        return self.minutes + (10_000 if self.delayed else 0)


@dataclass
class StopMatch:
    """A candidate stop returned by a stop search, for the admin UI."""

    id: str               # value to put in the feed's stop_id
    name: str             # human-readable station/stop name
    detail: str = ""      # e.g. "0.4 mi · Red, Blue"
    mode: str = "train"   # "train" or "bus" (drives the icon in search results)


@dataclass
class FeedResult:
    """Outcome of one fetch: the departures plus any error for display."""

    label: str
    departures: list[Departure] = field(default_factory=list)
    error: Optional[str] = None
    feed_type: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None


class BaseFetcher(ABC):
    """Base class for a single configured feed.

    Subclasses must set :attr:`feed_type` and implement :meth:`fetch`.
    """

    #: short identifier used in config.json ("cta", "mta", "njt")
    feed_type: str = "base"

    def __init__(self, config: dict):
        self.config = config
        self.label: str = config.get("label") or self.feed_type.upper()
        self.stop_id: str = str(config.get("stop_id", "")).strip()
        # a feed may watch several stops at once (comma-separated); departures
        # across them are merged and trimmed to the soonest `limit`.
        self.stop_ids: list[str] = [s.strip() for s in self.stop_id.split(",") if s.strip()]
        # api_key may reference an env var, e.g. "${CTA_KEY}", resolved here so
        # secrets can live outside config.json (in systemd Environment / .env).
        self.api_key: str = os.path.expandvars(str(config.get("api_key", "")).strip())
        self.limit: int = int(config.get("limit", 5))
        self.timeout: int = int(config.get("timeout", 10))

    #: whether this agency supports "find nearest stop" in the admin UI
    supports_stop_search: bool = False

    @classmethod
    def find_stops(cls, lat: float, lon: float, limit: int = 8,
                   api_key: str = "") -> "list[StopMatch]":
        """Return nearest stops to a coordinate. Override per agency.

        ``api_key`` is supplied for agencies whose stop search needs auth
        (e.g. NYC bus). Default: unsupported.
        """
        return []

    @abstractmethod
    def fetch(self) -> list[Departure]:
        """Fetch and return upcoming departures (sorted soonest-first).

        Implementations may raise; use :meth:`safe_fetch` to get a
        non-raising wrapper that captures errors for on-screen display.
        """
        raise NotImplementedError

    def safe_fetch(self) -> FeedResult:
        """Run :meth:`fetch`, catching everything so the loop never dies."""
        try:
            departures = self.fetch()
            for dep in departures:
                dep.feed_type = self.feed_type
            departures = sorted(departures, key=lambda d: d.sort_key)[: self.limit]
            return FeedResult(label=self.label, departures=departures,
                              feed_type=self.feed_type)
        except Exception as exc:  # noqa: BLE001 - surface to display, never crash
            return FeedResult(label=self.label, feed_type=self.feed_type,
                              error=f"{type(exc).__name__}: {exc}")
