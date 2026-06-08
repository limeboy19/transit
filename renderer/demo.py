"""Sample departures for layout QA without live APIs or keys."""

from __future__ import annotations

from fetcher.base import Departure, FeedResult

# CTA brand colors (subset) for realistic badges
_CTA = {"Blue": "#00A1DE", "Brown": "#62361B", "Green": "#009B3A", "Red": "#C60C30"}
_MTA = {"R": "#FCCC0A", "Q": "#FCCC0A", "4": "#00933C", "A": "#0039A6"}


def sample_results(feed_type: str = "cta") -> list[FeedResult]:
    """One stop's worth of believable departures for the given agency."""
    if feed_type == "mta":
        return [FeedResult("Atlantic Av–Barclays · Uptown", feed_type="mta", departures=[
            Departure("R", "Uptown", 1, _MTA["R"], detail="", feed_type="mta"),
            Departure("4", "Uptown", 4, _MTA["4"], detail="", feed_type="mta"),
            Departure("Q", "Uptown", 9, _MTA["Q"], detail="", feed_type="mta"),
            Departure("R", "Uptown", 14, _MTA["R"], detail="", feed_type="mta"),
        ])]
    if feed_type == "njt":
        return [FeedResult("Hoboken Terminal", feed_type="njt", error=None, departures=[
            Departure("NEC", "New York Penn", 6, "#F44", feed_type="njt"),
            Departure("MOBO", "Bay Head", 18, "#06C", feed_type="njt"),
        ])]
    return [FeedResult("Clark/Lake", feed_type="cta", departures=[
        Departure("Blue", "O'Hare", 0, _CTA["Blue"], feed_type="cta"),
        Departure("Brown", "Kimball", 6, _CTA["Brown"], feed_type="cta"),
        Departure("Green", "Harlem/Lake", 9, _CTA["Green"], feed_type="cta"),
        Departure("Red", "Howard", 12, _CTA["Red"], delayed=True, feed_type="cta"),
    ])]


def sample_stacked() -> list[FeedResult]:
    """A CTA train stop + bus stop merged — what stacking looks like."""
    train = FeedResult("Clark/Lake", feed_type="cta", departures=[
        Departure("Blue", "O'Hare", 2, _CTA["Blue"], detail="Run 124", feed_type="cta"),
        Departure("Brown", "Kimball", 7, _CTA["Brown"], detail="Run 412", feed_type="cta"),
    ])
    bus = FeedResult("Washington & Dearborn", feed_type="cta", departures=[
        Departure("20", "Austin", 1, "#009B3A", detail="Bus 1282", feed_type="cta"),
        Departure("J14", "Jeffery", 5, "#009B3A", detail="Bus 1627", feed_type="cta"),
        Departure("60", "Larrabee", 11, "#009B3A", detail="Bus 6010", feed_type="cta"),
    ])
    return [train, bus]
