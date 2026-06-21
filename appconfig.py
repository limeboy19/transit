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
DEVICES_DIR = Path(__file__).resolve().parent / "devices"


def _active_config_path() -> Path:
    """Where this machine's config lives.

    If TRANSIT_DEVICE is set (e.g. "advait"), the config is the git-tracked
    ``devices/<id>.json`` — so you can change a deployed device's stops by
    editing that file and pushing; the auto-update cron pulls it. The config
    holds no secrets (keys resolve from Key Vault via ${...}), so it's safe in
    git. Without TRANSIT_DEVICE, fall back to the local ``config.json``.
    """
    dev = os.environ.get("TRANSIT_DEVICE", "").strip()
    if dev:
        return DEVICES_DIR / f"{dev}.json"
    return CONFIG_PATH


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
    """Load the active config (device file in git, or local config.json)."""
    path = _active_config_path()
    # seed only the legacy local config.json from the example on first run;
    # device files are expected to live in git.
    if path == CONFIG_PATH and not path.exists() and EXAMPLE_PATH.exists():
        try:
            path.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    try:
        with open(path, "r", encoding="utf-8") as fh:
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
    """Atomically write the active config (temp file + rename)."""
    path = _active_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
