#!/usr/bin/env bash
# Runs on Agando (where goodwe_solar/data.db actually lives), NOT on XPS —
# unlike daily_update.sh, this needs local access to the SQLite DB, which XPS
# doesn't have. Scheduled via goodwe-snapshot-push.timer, shortly after
# goodwe-daily-rollup.timer (00:10 Europe/Berlin) writes that day's rollup row.

REPO=/home/gareth/garching-energy-dashboard
LOG=$REPO/logs/goodwe_snapshot_push.log

mkdir -p "$(dirname "$LOG")"
exec >> "$LOG" 2>&1

echo ""
echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

cd "$REPO"

if git pull --ff-only 2>&1; then
    echo "git pull: ok"
else
    echo "WARNING: git pull failed — aborting to avoid a diverged push"
    exit 1
fi

if /usr/bin/python3 scripts/export_goodwe_snapshot.py; then
    echo "Snapshot export: ok"
else
    echo "ERROR: snapshot export failed — aborting"
    exit 1
fi

if ! git diff --quiet -- data/goodwe_rollup_snapshot.csv; then
    git add data/goodwe_rollup_snapshot.csv
    if git commit -m "Update goodwe rollup snapshot" 2>&1 && git push 2>&1; then
        echo "Snapshot commit+push: ok"
    else
        echo "WARNING: snapshot commit/push failed"
    fi
else
    echo "Snapshot unchanged — nothing to commit"
fi

echo "=== Done ==="
