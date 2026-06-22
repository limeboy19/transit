"""WiFi onboarding helpers (Raspberry Pi / NetworkManager).

The display is an appliance: when it boots with no internet (e.g. it was moved
to a new home), the kiosk shows a touchscreen WiFi setup page instead of the
board. These helpers wrap `nmcli` so that page can list nearby networks and
join one.

The app runs headless (a systemd service, no graphical/polkit session), so
changing connections needs root -- we `sudo -n nmcli`, and setup_kiosk.sh
grants a scoped NOPASSWD entry. On a non-Pi dev machine (no nmcli) every
function degrades gracefully so the rest of the app still runs.
"""

from __future__ import annotations

import shutil
import socket
import subprocess

NMCLI = shutil.which("nmcli")


def wifi_supported() -> bool:
    """True only where we can actually drive WiFi (i.e. nmcli is present)."""
    return NMCLI is not None


def has_internet(timeout: float = 2.0) -> bool:
    """True if a TCP connection to a well-known host succeeds (no DNS needed)."""
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _run(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo", "-n", NMCLI, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _split_terse(line: str) -> list[str]:
    """Split an `nmcli -t` line on unescaped ':' and unescape '\\:' / '\\\\'."""
    fields: list[str] = []
    cur: list[str] = []
    esc = False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def scan(rescan: bool = False) -> list[dict]:
    """Visible networks as [{ssid, signal, secure}], strongest signal first."""
    if not wifi_supported():
        return []
    try:
        _run(["radio", "wifi", "on"], timeout=10)  # best effort: make sure it's on
    except subprocess.SubprocessError:
        pass
    args = ["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
    if rescan:
        args += ["--rescan", "yes"]
    try:
        res = _run(args, timeout=30)
    except subprocess.SubprocessError:
        return []

    best: dict[str, dict] = {}
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        parts = _split_terse(line)
        if len(parts) < 3:
            continue
        ssid, signal, security = parts[0], parts[1], parts[2]
        if not ssid:
            continue  # hidden / blank SSID
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        secure = bool(security.strip()) and security.strip() != "--"
        if ssid not in best or sig > best[ssid]["signal"]:
            best[ssid] = {"ssid": ssid, "signal": sig, "secure": secure}
    return sorted(best.values(), key=lambda n: n["signal"], reverse=True)


def connect(ssid: str, password: str = "") -> tuple[bool, str]:
    """Join a network. Creates/activates a persistent, autoconnecting profile."""
    if not wifi_supported():
        return False, "WiFi control isn't available on this machine."
    if not ssid:
        return False, "No network selected."
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    try:
        res = _run(args, timeout=45)
    except subprocess.TimeoutExpired:
        return False, "Timed out trying to connect. Try again."
    except subprocess.SubprocessError as exc:
        return False, f"Couldn't run WiFi setup: {exc}"
    if res.returncode == 0:
        return True, "Connected."
    return False, _friendly((res.stderr or res.stdout or "").strip())


def _friendly(err: str) -> str:
    low = err.lower()
    if any(s in low for s in ("secrets were required", "no key available",
                              "802-11-wireless-security", "activation failed")):
        return "Couldn't connect — check the password and try again."
    if "no network with ssid" in low or "not found" in low:
        return "Network not found — move closer or rescan."
    return (err[:200] or "Couldn't connect.")
