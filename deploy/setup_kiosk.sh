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
#   * auto-update cron (hourly; validates + rolls back a bad commit)
#   * nightly reboot (only while the screen is in its off-hours window)
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

echo ">> installing systemd service (app auto-starts on boot, as $USER)"
# Generate the unit from the REAL user/home/paths -- never assume the username
# is 'pi'. StartLimitIntervalSec=0 means it retries forever (e.g. if it boots
# before WiFi is ready) instead of giving up after a few quick restarts.
sudo tee /etc/systemd/system/transit.service >/dev/null <<EOF
[Unit]
Description=Raspberry Pi Transit Display
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable transit.service

echo ">> granting passwordless sudo for restart + reboot (cron has no tty)"
# Without this, the hourly auto-update's `sudo systemctl restart` and the
# nightly reboot would silently fail (cron can't answer a password prompt).
# Scoped to exactly these commands -- not blanket sudo.
SYSTEMCTL="$(command -v systemctl)"; REBOOT="$(command -v reboot)"
SUDO_FILE=/etc/sudoers.d/transit
sudo tee "$SUDO_FILE" >/dev/null <<EOF
$USER ALL=(root) NOPASSWD: $SYSTEMCTL restart transit.service, $SYSTEMCTL start transit.service, $SYSTEMCTL stop transit.service, $REBOOT
EOF
sudo chmod 0440 "$SUDO_FILE"
sudo visudo -cf "$SUDO_FILE" >/dev/null || { echo "   sudoers check FAILED, removing"; sudo rm -f "$SUDO_FILE"; }

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

echo ">> installing cron (hourly auto-update + screen schedule + nightly reboot)"
chmod +x "$APP_DIR/deploy/autoupdate.sh" "$APP_DIR/deploy/screen_schedule.sh" "$APP_DIR/deploy/nightly_reboot.sh"
( crontab -l 2>/dev/null | grep -v -e 'deploy/autoupdate.sh' -e 'deploy/screen_schedule.sh' -e 'deploy/nightly_reboot.sh' ; \
  echo "0 * * * * $APP_DIR/deploy/autoupdate.sh >> $HOME/autoupdate.log 2>&1" ; \
  echo "* * * * * $APP_DIR/deploy/screen_schedule.sh" ; \
  echo "33 3 * * * $APP_DIR/deploy/nightly_reboot.sh >> $HOME/autoupdate.log 2>&1" ) | crontab -

echo
echo ">> kiosk setup complete. Reboot to apply:  sudo reboot"
