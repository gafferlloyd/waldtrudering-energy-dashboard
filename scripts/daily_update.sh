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

# Export a fresh goodwe_solar snapshot and push it, so GitHub Actions CI (which
# can't reach the local-only goodwe_solar/data.db) has a same-day fallback for
# actual PV/export/hot-water data. See scripts/export_goodwe_snapshot.py.
if /usr/bin/python3 scripts/export_goodwe_snapshot.py; then
    echo "Snapshot export: ok"
    if ! git diff --quiet -- data/goodwe_rollup_snapshot.csv; then
        git add data/goodwe_rollup_snapshot.csv
        if git commit -m "Update goodwe rollup snapshot" 2>&1 && git push 2>&1; then
            echo "Snapshot commit+push: ok"
        else
            echo "WARNING: snapshot commit/push failed — continuing"
        fi
    else
        echo "Snapshot unchanged — nothing to commit"
    fi
else
    echo "WARNING: snapshot export failed — continuing"
fi

echo "=== Done ==="
