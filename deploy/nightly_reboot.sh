#!/usr/bin/env bash
# Nightly clean slate. A kiosk that runs for weeks can accumulate gremlins
# (Chromium memory creep, stuck Wayland state). Reboot once a night -- but
# ONLY while the screen is in its configured off-hours window, so we never
# flicker a board someone is actually looking at. Devices with no off_hours
# set simply never auto-reboot (they're seen daily by their owner anyway).
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 0

STATE=$(.venv/bin/python -c \
  "from appconfig import load_config; from schedule import screen_state; print(screen_state(load_config()))" \
  2>/dev/null)

if [ "$STATE" = "off" ]; then
    echo "$(date -Is) off-hours: nightly reboot"
    sudo systemctl reboot
fi
exit 0
