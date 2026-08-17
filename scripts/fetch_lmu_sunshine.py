"""Fetch recent LMU Munich-city (stadt) daily data, used only to backfill
DWD sunshine hours while the MUC airport sunshine sensor is out (SDK -999
since 2026-05-01).

Writes cache/lmu_city_weather.csv. process.py fills DWD's sunshine column
from this file wherever DWD's own value is missing — a plain NaN-fill, so
it stops touching anything automatically once DWD sunshine resumes.

Only a rolling recent window is needed (older DWD sunshine isn't missing),
kept modest to stay fast on every build.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

import scrape_weather as sw

WINDOW_DAYS = 400


def fetch(cache_dir: Path) -> bool:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / "lmu_city_weather.csv"

    from_ym = (date.today() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m")
    try:
        rows = sw.scrape(from_ym=from_ym, station="stadt")
    except Exception as exc:
        print(f"  LMU city: WARNING — fetch failed: {exc}", file=sys.stderr)
        return False

    if not rows:
        print("  LMU city: no rows fetched", file=sys.stderr)
        return False

    import csv
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "tmax", "tmit", "tmin", "rain", "sunshine"])
        w.writeheader()
        w.writerows(rows)

    print(f"  LMU city: {len(rows)} rows → {out_path.name}")
    return True


if __name__ == "__main__":
    fetch(ROOT / "cache")
