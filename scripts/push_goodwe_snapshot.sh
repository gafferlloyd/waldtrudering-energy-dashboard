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

# Measured PV-output heatmaps (all-time / 3y / 1y) rendered from goodwe_solar's data.db:
# the panel-frame polar view, the azimuth/AOI Cartesian view, and the Potential view (best
# value at any worse angle of incidence on the same bearing, propagated toward boresight,
# masked to the az/el footprint actually measured so far). Each at two colour
# scales: the default 10kW, and 7kW -- the inverter's feed-in is curtailed to roughly 7kW,
# so the 10kW scale alone compresses most real variation below that plateau into a narrow
# red band.
PV_PNGS=""
for vb in "polar pv_heatmap" "cart pv_cosaoi" "potential pv_heatmap_potential"; do
    set -- $vb; view=$1; base=$2
    for w in all 3y 1y; do
        for cs in "10000 " "7000 _7kw"; do
            set -- $cs; cap=$1; suffix=$2
            png="$REPO/data/${base}_${w}${suffix}.png"
            if /usr/bin/python3 /home/gareth/goodwe_solar/pv_heatmap.py --window "$w" --view "$view" --cap "$cap" --out "$png" >/dev/null 2>&1; then
                echo "Heatmap $w ($view, ${cap}W): ok"
            else
                echo "WARNING: heatmap $w ($view, ${cap}W) render failed"
            fi
            PV_PNGS="$PV_PNGS data/${base}_${w}${suffix}.png"
        done
    done
done
if ! git diff --quiet -- data/goodwe_rollup_snapshot.csv || [ -n "$(git status --porcelain -- $PV_PNGS)" ]; then
    git add data/goodwe_rollup_snapshot.csv $PV_PNGS
    if git commit -m "Update goodwe rollup snapshot and PV heatmaps" 2>&1 && git push 2>&1; then
        echo "Snapshot commit+push: ok"
    else
        echo "WARNING: snapshot commit/push failed"
    fi
else
    echo "Snapshot unchanged — nothing to commit"
fi

echo "=== Done ==="
