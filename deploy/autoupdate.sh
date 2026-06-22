#!/usr/bin/env bash
# Auto-update with rollback. Pulls the latest code and only adopts it if the
# new commit actually (a) imports cleanly and (b) serves the board after a
# restart. If either check fails, it rolls back to the previous commit so a bad
# push can never brick an unattended device. Wire into cron (hourly).
set -uo pipefail   # NOTE: not -e; we handle failures explicitly so we can roll back.

# Derive everything from this script's location + the running user -- no
# hardcoded /home/pi, so it works whatever the Pi's username is.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="transit.service"
PORT="${TRANSIT_PORT:-5000}"
PY="$REPO_DIR/.venv/bin/python"
KEY="$HOME/.ssh/id_ed25519"
HEALTH_TIMEOUT=60   # seconds to wait for the board to come back up after restart

# cron has a minimal env, so point git at the read-only deploy key explicitly.
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

cd "$REPO_DIR" || { echo "$(date -Is) repo dir missing: $REPO_DIR"; exit 1; }

log() { echo "$(date -Is) $*"; }

# config.json is git-ignored, so reset updates code only and leaves local
# config (keys, stop ids) untouched.
BEFORE=$(git rev-parse HEAD)
if ! git fetch --quiet origin; then
    log "fetch failed (network?); keeping $BEFORE"
    exit 0
fi
git reset --hard --quiet origin/main
AFTER=$(git rev-parse HEAD)

[ "$BEFORE" = "$AFTER" ] && { log "up to date ($AFTER)"; exit 0; }
log "fetched $BEFORE -> $AFTER, validating"

rollback() {
    log "ROLLBACK: $AFTER rejected ($1); restoring $BEFORE"
    git reset --hard --quiet "$BEFORE"
    sudo systemctl restart "$SERVICE"
}

# (a) static check: does the new code even import?
if ! "$PY" -c "import main, fetcher, renderer, web.app, appconfig, schedule" 2>/tmp/transit_update_err; then
    rollback "import error: $(tr '\n' ' ' </tmp/transit_update_err | tail -c 300)"
    exit 1
fi

# adopt it
log "imports OK, restarting"
sudo systemctl restart "$SERVICE"

# (b) runtime check: does the board actually serve within the timeout?
for _ in $(seq 1 "$HEALTH_TIMEOUT"); do
    if curl -fsS -o /dev/null "http://localhost:$PORT/display/0"; then
        log "updated and healthy at $AFTER"
        exit 0
    fi
    sleep 1
done

rollback "health check failed (no /display/0 within ${HEALTH_TIMEOUT}s)"
exit 1
