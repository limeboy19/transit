#!/usr/bin/env bash
# Auto-update: pull latest code and restart the service only if something
# changed. Wire this into cron (see the snippet in README / crontab.txt).
set -euo pipefail

REPO_DIR="/home/pi/transit-display"
SERVICE="transit.service"

cd "$REPO_DIR"

# NOTE: config.json is git-ignored, so this hard reset updates code only and
# leaves your local config (API keys, stop ids) untouched.
BEFORE=$(git rev-parse HEAD)
git fetch --quiet origin
git reset --hard --quiet origin/main
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" != "$AFTER" ]; then
    echo "$(date -Is) updated $BEFORE -> $AFTER, restarting"
    sudo systemctl restart "$SERVICE"
else
    echo "$(date -Is) up to date ($AFTER)"
fi
