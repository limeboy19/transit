#!/usr/bin/env bash
# Reproducible kiosk setup for the transit display.
# Target: Raspberry Pi 5 + official Touch Display 2, Raspberry Pi OS (labwc/Wayland).
#
# Run once on a Pi that already has the app cloned + venv built:
#     bash ~/transit-display/deploy/setup_kiosk.sh
#     sudo reboot
#
# Idempotent — safe to re-run. It sets up:
#   * the systemd service (app auto-starts on boot)
#   * a kiosk launcher (fullscreen Chromium, auto-respawn, skips the keyring)
#   * labwc autostart (rotate to landscape + launch the kiosk)
#   * screen blanking disabled (always-on display)
#   * auto-update cron (pull latest code every 5 min)
set -euo pipefail

APP_DIR="$HOME/transit-display"
PORT=5000
OUTPUT="DSI-2"   # Touch Display 2 connector
TRANSFORM=90     # rotate the portrait panel to landscape

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

echo ">> installing auto-update cron (pull every 5 min)"
chmod +x "$APP_DIR/deploy/autoupdate.sh"
( crontab -l 2>/dev/null | grep -v 'deploy/autoupdate.sh' ; \
  echo "*/5 * * * * $APP_DIR/deploy/autoupdate.sh >> $HOME/autoupdate.log 2>&1" ) | crontab -

echo
echo ">> kiosk setup complete. Reboot to apply:  sudo reboot"
