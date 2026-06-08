"""Fetcher registry + factory.

To add a new transit system: implement a ``BaseFetcher`` subclass in this
package and add it to ``FETCHERS`` below. Nothing else in the app needs to
change.
"""

from __future__ import annotations

import os
import re

from .base import BaseFetcher, Departure, FeedResult
from .cta import CTAFetcher
from .mta import MTAFetcher
from .njt import NJTFetcher

_VAR_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def _resolve_str(value: str, variables: dict, vault_url: str = "") -> str:
    """Replace ${name}, in order: config 'vars' -> env var -> Azure Key Vault.

    An empty/blank vars entry is treated as "not set" so it falls through to the
    env var / Key Vault (lets you leave the key out of config.json entirely).
    """
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if variables.get(name):
            return str(variables[name])
        env = os.environ.get(name)
        if env:
            return env
        if vault_url:
            from secrets_azure import get_secret

            secret = get_secret(name, vault_url)
            if secret is not None:
                return secret
        return match.group(0)
    return _VAR_RE.sub(repl, value)


def _resolve_feed(feed: dict, variables: dict, vault_url: str = "") -> dict:
    """Return a copy of a feed config with ${...} references resolved."""
    return {
        k: (_resolve_str(v, variables, vault_url) if isinstance(v, str) else v)
        for k, v in feed.items()
    }

FETCHERS: dict[str, type[BaseFetcher]] = {
    CTAFetcher.feed_type: CTAFetcher,
    MTAFetcher.feed_type: MTAFetcher,
    NJTFetcher.feed_type: NJTFetcher,
}


def create_fetcher(feed_config: dict) -> BaseFetcher:
    """Build a fetcher instance from a single feed entry in config.json."""
    feed_type = str(feed_config.get("type", "")).lower().strip()
    if feed_type not in FETCHERS:
        raise ValueError(
            f"Unknown feed type {feed_type!r}. Known types: {sorted(FETCHERS)}"
        )
    return FETCHERS[feed_type](feed_config)


def fetch_all(config: dict) -> list[FeedResult]:
    """Fetch every enabled feed in config, returning normalized results."""
    variables = config.get("vars") or {}
    vault_url = config.get("key_vault_url") or os.environ.get("AZURE_KEYVAULT_URL", "")
    results: list[FeedResult] = []
    for feed in config.get("feeds", []):
        if not feed.get("enabled", False):
            continue
        try:
            fetcher = create_fetcher(_resolve_feed(feed, variables, vault_url))
        except Exception as exc:  # noqa: BLE001
            results.append(
                FeedResult(label=feed.get("label", "?"), error=str(exc))
            )
            continue
        results.append(fetcher.safe_fetch())
    return results


__all__ = [
    "BaseFetcher",
    "Departure",
    "FeedResult",
    "FETCHERS",
    "create_fetcher",
    "fetch_all",
]
