"""Shared config.json load/save helpers used by the loop and the web UI."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("TRANSIT_CONFIG",
                                  Path(__file__).resolve().parent / "config.json"))
EXAMPLE_PATH = Path(__file__).resolve().parent / "config.example.json"
ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a local .env into the environment (if present).

    Real environment variables win (setdefault), so a systemd EnvironmentFile or
    shell export overrides the file. Used to supply the Azure service-principal
    creds (AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_SECRET).
    """
    if not ENV_PATH.exists():
        return
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()

DEFAULT_CONFIG = {
    "vars": {},
    "key_vault_url": "",     # optional Azure Key Vault for ${...} secrets
    "refresh_seconds": 60,   # background-loop cadence (fastest display wins)
    "feeds": [],             # each feed is one self-contained display
}


def load_config() -> dict:
    """Load config.json, seeding it from the example on first run."""
    # config.json is git-ignored (it holds keys). On a fresh checkout, seed it
    # from the committed template so the app has something to run with.
    if not CONFIG_PATH.exists() and EXAMPLE_PATH.exists():
        try:
            CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"),
                                   encoding="utf-8")
        except OSError:
            pass
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    merged.setdefault("feeds", [])
    merged.setdefault("vars", {})
    merged.setdefault("key_vault_url", "")
    return merged


def save_config(config: dict) -> None:
    """Atomically write config.json (temp file + rename)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
        os.replace(tmp, CONFIG_PATH)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
