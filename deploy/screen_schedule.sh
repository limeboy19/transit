#!/usr/bin/env bash
# Turn the display off during the configured off_hours, on otherwise.
# Run from cron every minute. Only acts on a state change (no flicker).
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export WAYLAND_DISPLAY=wayland-0
OUTPUT="DSI-2"
STATE_FILE="/tmp/transit_screen_state"

cd "$HOME/transit-display" 2>/dev/null || exit 0

WANT=$(.venv/bin/python -c "from appconfig import load_config; from schedule import screen_state; print(screen_state(load_config()))" 2>/dev/null)
[ "$WANT" = "off" ] || WANT="on"   # default to on if the check failed

LAST=$(cat "$STATE_FILE" 2>/dev/null || echo "")
if [ "$WANT" != "$LAST" ]; then
    wlr-randr --output "$OUTPUT" --"$WANT" 2>/dev/null && echo "$WANT" > "$STATE_FILE"
fi
