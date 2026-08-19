"""Export goodwe_solar's daily_rollup table to a committed CSV snapshot.

GitHub Actions CI builds this dashboard on a cloud runner with no access to
the local-only /home/gareth/goodwe_solar/data.db, so actual PV/export/hot-water
data would otherwise be permanently NaN on GitHub Pages. Run locally (from
daily_update.sh, alongside the live-DB build) to keep data/goodwe_rollup_snapshot.csv
at most a day stale; process.py::run() falls back to it when the live DB isn't
reachable. See load_goodwe_snapshot()/load_goodwe_rollup() in process.py.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from process import ROOT, load_config, load_goodwe_rollup


def main():
    cfg = load_config()
    db_path = Path(cfg.get("goodwe_db_path", "/home/gareth/goodwe_solar/data.db"))
    df = load_goodwe_rollup(db_path)
    if df.empty:
        print(f"No goodwe_rollup data found at {db_path} — nothing to export")
        return
    out_path = ROOT / "data" / "goodwe_rollup_snapshot.csv"
    df.reset_index().to_csv(out_path, index=False)
    print(f"Exported {len(df)} rows to {out_path}")


if __name__ == "__main__":
    main()
