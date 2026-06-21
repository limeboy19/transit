#!/usr/bin/env bash
# Reproducible kiosk setup for the transit display.
# Target: Raspberry Pi 5 + official Touch Display 2, Raspberry Pi OS (labwc/Wayland).
#
# Run once on a Pi that already has the app cloned + venv built.
# Pass the device id so it loads that device's git-tracked config:
#     bash ~/transit-display/deploy/setup_kiosk.sh advait
#     sudo reboot
#
# Idempotent — safe to re-run. It sets up:
#   * device mode (TRANSIT_DEVICE in .env -> loads devices/<id>.json from git)
#   * the systemd service (app auto-starts on boot)
#   * a kiosk launcher (fullscreen Chromium, auto-respawn, skips the keyring)
#   * labwc autostart (rotate to landscape + launch the kiosk)
#   * screen blanking disabled (always-on display)
#   * auto-update cron (pull latest code every 5 min)
set -euo pipefail

DEVICE="${1:-}"  # e.g. "advait" -> loads devices/advait.json from git
APP_DIR="$HOME/transit-display"
PORT=5000
OUTPUT="DSI-2"   # Touch Display 2 connector
TRANSFORM=90     # rotate the portrait panel to landscape

if [ -n "$DEVICE" ]; then
  echo ">> setting device id '$DEVICE' in .env (loads devices/$DEVICE.json from git)"
  ENV_FILE="$APP_DIR/.env"; touch "$ENV_FILE"
  if grep -q '^TRANSIT_DEVICE=' "$ENV_FILE"; then
    sed -i "s/^TRANSIT_DEVICE=.*/TRANSIT_DEVICE=$DEVICE/" "$ENV_FILE"
  else
    echo "TRANSIT_DEVICE=$DEVICE" >> "$ENV_FILE"
  fi
  if [ ! -f "$APP_DIR/devices/$DEVICE.json" ]; then
    echo "   WARNING: devices/$DEVICE.json not found in the repo yet."
  fi
fi

echo ">> installing systemd service (app auto-starts on boot)"
sudo cp "$APP_DIR/transit.service" /etc/systemd/system/transit.service
sudo systemctl daemon-reload
sudo systemctl enable transit.service

echo ">> writing kiosk launcher (~/kiosk.sh)"
cat > "$HOME/kiosk.sh" <<EOF
#!/bin/bash
export XDG_RUNTIME_DIR=/run/user/\$(id -u)
export WAYLAND_DISPLAY=wayland-0
# wait until the app is serving before opening the browser
for i in \$(seq 1 30); do curl -s -o /dev/null http://localhost:$PORT/display/0 && break; sleep 2; done
# respawn the browser if it ever exits/crashes
while true; do
  chromium --kiosk --ozone-platform=wayland --password-store=basic \\
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \\
    --check-for-update-interval=31536000 \\
    --app=http://localhost:$PORT/display/0
  sleep 3
done
EOF
chmod +x "$HOME/kiosk.sh"

echo ">> writing labwc autostart (rotate landscape + launch kiosk)"
mkdir -p "$HOME/.config/labwc"
cat > "$HOME/.config/labwc/autostart" <<EOF
wlr-randr --output $OUTPUT --transform $TRANSFORM &
$HOME/kiosk.sh &
EOF

echo ">> disabling screen blanking (always-on display)"
sudo raspi-config nonint do_blanking 1 || echo "   (raspi-config blanking toggle skipped; non-fatal)"

echo ">> installing cron (hourly auto-update + per-minute screen schedule)"
chmod +x "$APP_DIR/deploy/autoupdate.sh" "$APP_DIR/deploy/screen_schedule.sh"
( crontab -l 2>/dev/null | grep -v -e 'deploy/autoupdate.sh' -e 'deploy/screen_schedule.sh' ; \
  echo "0 * * * * $APP_DIR/deploy/autoupdate.sh >> $HOME/autoupdate.log 2>&1" ; \
  echo "* * * * * $APP_DIR/deploy/screen_schedule.sh" ) | crontab -

echo
echo ">> kiosk setup complete. Reboot to apply:  sudo reboot"
