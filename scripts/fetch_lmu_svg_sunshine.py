"""Targeted gap-fill: extract daily sunshine totals directly from LMU's
hourly Zeitreihen SVG archive (~quicklooks/zeitreihen/stadt/{yyyy}/{mm}/
{yyyymmdd}_stra.svg), for whichever specific dates are still missing sunshine
after the normal DWD + LMU-city-table tiers (fetch_dwd_weather.py,
fetch_lmu_sunshine.py) have both run.

Why a separate tier, not a replacement: the Zeitreihen SVG archive and the
daily "klimadaten" aggregate table are independently maintained LMU systems
and can have different outages. Confirmed 2026-10-11: the daily table had a
2026-09-24..09-30 gap for the Stadt station that the hourly SVG archive did
NOT have. SVG parsing is heavier than the table scrape (one HTTP fetch +
regex parse per date, vs. one fetch per month), so this only runs for the
specific dates still missing, not every date on every build.

The "Sonnendauer"/"Sonnenminuten" line in each day's _stra.svg is a
cumulative step function — minutes of sunshine accumulated since midnight
UTC — so the day's total is just its final (maximum) value. Extracting it
means reading the plotted line's own SVG path coordinates and converting
pixel-space back to data values via the chart's axis tick calibration.
Matplotlib's SVG backend draws tick labels as vector glyph paths (not
<text> elements) but leaves the original label text as an adjacent HTML
comment — e.g. <!-- 100 --> right before a <g id="text_7"> block — which is
what makes this parseable without any font/glyph recognition.

This axis-calibration + path-extraction technique was first built for
goodwe_solar/tools/parse_meteo_svg.py (GHI/DHI extraction for PV yield
estimation, a different project/repo on this machine). Reimplemented here
(not imported) because this module must run standalone on GitHub Actions'
runner, which never checks out goodwe_solar — see fetch_openmeteo_historical
import comment in process.py for the same same-repo-only constraint.

Writes cache/lmu_svg_sunshine.csv (date,sunshine_h). process.py reads it as
a third sunshine tier, after the LMU city table and before Open-Meteo's
modeled estimate.
"""
import csv
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent

SVG_URL = "https://www.meteo.physik.uni-muenchen.de/~quicklooks/zeitreihen/{station}/{yyyy}/{mm}/{yyyymmdd}_stra.svg"
STATION = "stadt"  # matches fetch_lmu_sunshine.py's choice — never Garching, different microclimate
SUNSHINE_COLOR = "#1773cc"  # consistent across station svgs even though the label text differs ("Sonnendauer" Garching vs "Sonnenminuten" Stadt)
MAX_DATES_PER_RUN = 30  # safety cap — a gap this large points at something worth investigating, not silently fetching 30+ SVGs every build
WINDOW_DAYS = 400  # same horizon as fetch_lmu_sunshine.py; older DWD sunshine isn't missing


def _axis_calibration(svg: str):
    """(label_value, svg_pixel_coord) pairs for x (hour) and y (minutes) axes,
    read from gray (#808080) tick-mark lines paired with their neighbouring
    <!-- LABEL --> comment."""
    x_calib, y_calib = [], []
    for m in re.finditer(
        r'<g id="(xtick|ytick)_\d+">\s*<g id="line2d_\d+">\s*'
        r'<path d="M ([\d.-]+) ([\d.-]+)\s*\nL ([\d.-]+) ([\d.-]+)\s*\n"',
        svg,
    ):
        kind, x1, y1, x2, y2 = m.groups()
        tail = svg[m.end():m.end() + 600]
        cm = re.search(r'<!--\s*([\d.]+)\s*-->', tail)
        if not cm:
            continue
        label = float(cm.group(1))
        if kind == "xtick":
            x_calib.append((label, float(x1)))
        else:
            y_calib.append((label, float(y1)))
    return x_calib, y_calib


def _linear_fit(calib):
    """calib: [(value, svg_coord), ...] -> (slope, intercept) for svg_coord = slope*value + intercept."""
    vals = [c[0] for c in calib]
    coords = [c[1] for c in calib]
    n = len(vals)
    mean_v, mean_c = sum(vals) / n, sum(coords) / n
    num = sum((v - mean_v) * (c - mean_c) for v, c in zip(vals, coords))
    den = sum((v - mean_v) ** 2 for v in vals)
    if den == 0:
        return None
    slope = num / den
    return slope, mean_c - slope * mean_v


def extract_daily_sunshine_hours(svg: str) -> float | None:
    """The cumulative sunshine-minutes curve's final (= max, since it's
    monotonically non-decreasing) value, converted to hours to match the
    rest of the pipeline's convention. None if the curve or enough tick
    labels to calibrate the y-axis aren't found."""
    pattern = re.compile(r'<path d="(M [\dMLz.\s-]+?)"[^/]*stroke:\s*' + re.escape(SUNSHINE_COLOR))
    m = pattern.search(svg)
    if not m:
        return None
    nums = re.findall(r'-?[\d.]+', m.group(1))
    if len(nums) < 4:
        return None
    coords_y = [float(nums[i + 1]) for i in range(0, len(nums) - 1, 2)]

    _x_calib, y_calib = _axis_calibration(svg)
    if len(y_calib) < 2:
        return None
    fit = _linear_fit(y_calib)
    if fit is None:
        return None
    y_slope, y_intercept = fit

    minutes = [(svg_y - y_intercept) / y_slope for svg_y in coords_y]
    return round(max(minutes) / 60.0, 2) if minutes else None


def fetch_day(d: date, station: str = STATION) -> float | None:
    url = SVG_URL.format(station=station, yyyy=d.year, mm=f"{d.month:02d}", yyyymmdd=d.strftime("%Y%m%d"))
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            svg = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"  LMU SVG {d}: WARNING — fetch failed: {exc}", file=sys.stderr)
        return None
    return extract_daily_sunshine_hours(svg)


def _dates_still_missing(cache_dir: Path) -> list[date]:
    """Dates within WINDOW_DAYS where DWD's own sunshine is missing AND the
    LMU city daily table also doesn't cover it -- the only dates worth the
    extra SVG fetch."""
    import csv as _csv

    since = date.today() - timedelta(days=WINDOW_DAYS)
    dwd_missing = set()
    dwd_path = cache_dir / "dwd_weather.csv"
    if dwd_path.exists():
        with open(dwd_path, newline="") as f:
            for row in _csv.DictReader(f):
                d = date.fromisoformat(row["date"])
                if d < since:
                    continue
                if not row.get("sunshine", "").strip():
                    dwd_missing.add(d)
    else:
        return []  # no DWD cache at all yet — nothing to compare gaps against

    lmu_covered = set()
    lmu_path = cache_dir / "lmu_city_weather.csv"
    if lmu_path.exists():
        with open(lmu_path, newline="") as f:
            for row in _csv.DictReader(f):
                if row.get("sunshine", "").strip():
                    lmu_covered.add(date.fromisoformat(row["date"]))

    return sorted(dwd_missing - lmu_covered)


def fetch(cache_dir: Path) -> bool:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / "lmu_svg_sunshine.csv"

    missing = _dates_still_missing(cache_dir)
    if not missing:
        print("  LMU SVG sunshine: no gap dates to fill")
        # Still write an empty (header-only) file so process.py's loader has
        # something consistent to read, rather than treating a from a
        # previous run's now-stale rows as current.
        with open(out_path, "w", newline="") as f:
            csv.writer(f).writerow(["date", "sunshine"])
        return True

    if len(missing) > MAX_DATES_PER_RUN:
        print(f"  LMU SVG sunshine: WARNING — {len(missing)} gap dates, "
              f"only fetching the most recent {MAX_DATES_PER_RUN} (a gap this "
              f"large is worth investigating, not silently absorbing)", file=sys.stderr)
        missing = missing[-MAX_DATES_PER_RUN:]

    rows = []
    for d in missing:
        hours = fetch_day(d)
        if hours is not None:
            rows.append({"date": d.isoformat(), "sunshine": hours})

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "sunshine"])
        w.writeheader()
        w.writerows(rows)

    print(f"  LMU SVG sunshine: {len(rows)}/{len(missing)} gap dates filled → {out_path.name}")
    return True


if __name__ == "__main__":
    fetch(ROOT / "cache")
