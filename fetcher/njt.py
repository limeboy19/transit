"""NJ Transit fetcher (GTFS-Realtime / protobuf) — initial stub.

NJ Transit's developer API is a little different from MTA: you authenticate
against the NJ Transit developer portal to obtain a short-lived token, then
call the GTFS-RT trip-updates endpoint with that token. The portal credentials
and exact endpoints depend on whether you're using the Bus or Rail data
product (https://developer.njtransit.com/).

This class implements the generic GTFS-RT parsing (identical in shape to the
MTA fetcher) and a pluggable auth hook. To make it live:

  * set ``feed_url`` in the feed config to the trip-updates endpoint, and
  * fill in :meth:`_auth_headers` with the token your account uses.

Until then ``fetch`` raises a clear error, so the feed shows a friendly
"not configured" message on the display instead of silently doing nothing.
"""

from __future__ import annotations

import time

import requests
from google.transit import gtfs_realtime_pb2

from .base import BaseFetcher, Departure


class NJTFetcher(BaseFetcher):
    feed_type = "njt"

    def _auth_headers(self) -> dict:
        """Return the auth headers NJ Transit expects.

        NJ Transit issues a token via a login call; once you have it, the
        GTFS-RT endpoints accept it as a header. Adjust to match your account.
        """
        if not self.api_key:
            raise ValueError(
                "NJT feed requires an api_key (NJ Transit developer token)"
            )
        # NJ Transit accepts the token as a form/header value; the exact header
        # name depends on the data product. Override here if needed.
        return {"token": self.api_key}

    def fetch(self) -> list[Departure]:
        feed_url = str(self.config.get("feed_url", "")).strip()
        if not feed_url:
            raise ValueError(
                "NJT feed not configured: set 'feed_url' to the GTFS-RT "
                "trip-updates endpoint from developer.njtransit.com"
            )
        if not self.stop_ids:
            raise ValueError("NJT feed requires a stop_id")
        stop_set = set(self.stop_ids)

        resp = requests.get(feed_url, headers=self._auth_headers(), timeout=self.timeout)
        resp.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)

        now = time.time()
        departures: list[Departure] = []
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            route_id = tu.trip.route_id or "?"
            # GTFS-RT trip_update has no headsign; use the trip's last stop as a
            # rough destination, falling back to the route id.
            terminal = tu.stop_time_update[-1].stop_id if tu.stop_time_update else ""
            for stu in tu.stop_time_update:
                if str(stu.stop_id) not in stop_set:
                    continue
                when = 0
                if stu.HasField("arrival") and stu.arrival.time:
                    when = stu.arrival.time
                elif stu.HasField("departure") and stu.departure.time:
                    when = stu.departure.time
                if not when:
                    continue
                minutes = round((when - now) / 60)
                if minutes < 0:
                    continue
                departures.append(
                    Departure(
                        route=route_id,
                        destination=terminal or route_id,
                        minutes=minutes,
                    )
                )
        return departures
