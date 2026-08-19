#!/usr/bin/env bash

REPO=/home/gareth/garching-energy-dashboard
LOG=$REPO/logs/update.log

mkdir -p "$(dirname "$LOG")"
exec >> "$LOG" 2>&1

echo ""
echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

cd "$REPO"

# Pull any manually-pushed config/settings changes
if git pull --ff-only 2>&1; then
    echo "git pull: ok"
else
    echo "WARNING: git pull failed — continuing with local state"
fi

# Fetch fresh data (Google Sheets + LMU weather) and rebuild HTML
if /usr/bin/python3 scripts/build.py; then
    echo "Build: ok"
else
    echo "ERROR: build failed — aborting"
    exit 1
fi

# Restart local HTTP service
if systemctl --user restart energy-dashboard.service 2>&1; then
    echo "Service restarted: ok"
else
    echo "WARNING: service restart failed"
fi

echo "=== Done ==="
