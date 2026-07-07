"""
Main build orchestrator.

Usage:
    python scripts/build.py [--skip-fetch] [--output-dir OUTPUT]

Steps:
  1. Fetch fresh data from Google Sheets
  2. Process data
  3. Generate Plotly charts
  4. Render HTML template
  5. Write output/index.html
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Use cached data; skip Google Sheets download")
    parser.add_argument("--output-dir", default=str(ROOT / "output"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = ROOT / "cache"

    # ── 1. Fetch ──────────────────────────────────────────────────────────────
    if not args.skip_fetch:
        print("Step 1: Fetching data from Google Sheets…")
        import fetch_data
        ok = fetch_data.main(cache_dir)
        if not ok:
            print("  Warning: fetch failed — using cached/archive data only")
    else:
        print("Step 1: Skipped (--skip-fetch)")

    # ── 1b. Fetch DWD airport weather ────────────────────────────────────────
    if not args.skip_fetch:
        print("Step 1c: Fetching DWD München-Flughafen weather…")
        import fetch_dwd_weather
        fetch_dwd_weather.fetch(cache_dir)

    # ── 2. Process ────────────────────────────────────────────────────────────
    print("Step 2: Processing data…")
    import process
    data = process.run(data_dir=ROOT / "data", cache_dir=cache_dir)
    daily = data["daily"]
    print(f"  {len(daily):,} daily rows from {daily.index[0].date()} to {daily.index[-1].date()}")
    print(f"  Hot-water baseline: {data['hot_water_kwh']:.1f} kWh/day")

    # ── 3. Charts ─────────────────────────────────────────────────────────────
    print("Step 3: Generating charts…")
    import generate_charts
    charts = generate_charts.build_all(data)
    print(f"  {len(charts)} charts generated")

    # ── 4. Summary stats ──────────────────────────────────────────────────────
    cfg = data["config"]

    # Last non-zero day for each utility
    def last_nonzero(col):
        s = daily[col].replace(0, float("nan")).dropna()
        return float(s.iloc[-1]) if len(s) else 0.0

    def annual(col):
        s = daily[col].dropna()
        return float(s.iloc[-1]) if len(s) else 0.0

    # 7-day average (exclude trailing zeros from missing readings)
    def avg7(col):
        s = daily[col].replace(0, float("nan"))
        return float(s.rolling(7, min_periods=1).mean().dropna().iloc[-1]) if len(s.dropna()) else 0.0

    # Sum of 7 days ending `offset_days` before the latest date (skips zeros)
    def sum7_at(col, offset_days=0):
        s = daily[col].replace(0, float("nan"))
        end_date = s.index[-1] - timedelta(days=offset_days)
        window = s.loc[:end_date].iloc[-7:]
        valid = window.dropna()
        return float(valid.sum()) if len(valid) >= 4 else None

    # Value of a rolling-annual column `offset_days` before the latest date
    def annual_at(col, offset_days=0):
        s = daily[col].dropna()
        if len(s) == 0:
            return None
        if offset_days == 0:
            return float(s.iloc[-1])
        target = s.index[-1] - timedelta(days=offset_days)
        available = s[s.index <= target]
        return float(available.iloc[-1]) if len(available) > 0 else None

    def trend_pct(current, previous):
        if current is None or previous is None or previous == 0:
            return None
        return round((current - previous) / abs(previous) * 100, 1)

    _water_rolling = daily["use_water_m3"].rolling(365, min_periods=180).sum().dropna()
    _water_now  = float(_water_rolling.iloc[-1]) if len(_water_rolling) else None
    _water_prev_s = _water_rolling[_water_rolling.index <= _water_rolling.index[-1] - timedelta(days=365)] \
                    if len(_water_rolling) else None
    _water_prev = float(_water_prev_s.iloc[-1]) if _water_prev_s is not None and len(_water_prev_s) > 0 else None
    water_annual_trend = trend_pct(_water_now, _water_prev)

    stats = {
        "last_update":       daily.index[-1].strftime("%d %b %Y"),
        "build_time":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        # Daily (last non-zero reading / 7-day avg)
        "gas_daily_kwh":     round(last_nonzero("use_gas_kwh"), 1),
        "gas_daily7_kwh":    round(avg7("use_gas_kwh"), 1),
        "elec_daily_kwh":    round(last_nonzero("use_elec_kwh"), 1),
        "elec_daily7_kwh":   round(avg7("use_elec_kwh"), 1),
        "water_daily_m3":    round(last_nonzero("use_water_m3"), 3),
        "water_daily7_m3":   round(avg7("use_water_m3"), 3),
        # Annual (rolling 365-day)
        "gas_annual_mwh":    round(annual("gas_annual_kwh") / 1000, 1),
        "elec_annual_mwh":   round(annual("elec_annual_kwh") / 1000, 1),
        "water_annual_m3":   round(daily["use_water_m3"].rolling(365, min_periods=180).sum().dropna().iloc[-1], 0)
                             if daily["use_water_m3"].rolling(365, min_periods=180).sum().dropna().any() else 0,
        "annual_cost_eur":   round(annual("cost_total_annual")),
        "efficiency_kwh_m2": round(annual("efficiency_3yr_kwh_m2"), 1),
        "hot_water_kwh":     round(data["hot_water_kwh"], 1),
        # Trend vs same period 1 year ago (percentage change, None if unavailable)
        "gas_7d_trend":      trend_pct(sum7_at("use_gas_kwh"), sum7_at("use_gas_kwh", 365)),
        "elec_7d_trend":     trend_pct(sum7_at("use_elec_kwh"), sum7_at("use_elec_kwh", 365)),
        "water_7d_trend":    trend_pct(sum7_at("use_water_m3"), sum7_at("use_water_m3", 365)),
        "gas_annual_trend":  trend_pct(annual_at("gas_annual_kwh"), annual_at("gas_annual_kwh", 365)),
        "elec_annual_trend":    trend_pct(annual_at("elec_annual_kwh"), annual_at("elec_annual_kwh", 365)),
        "water_annual_trend":   water_annual_trend,
        "cost_annual_trend":    trend_pct(annual_at("cost_total_annual"), annual_at("cost_total_annual", 365)),
        # Solar export (new from 2026-07-07; trend will be None until a full year of data)
        "export_daily7_kwh":    round(avg7("use_elec_export_kwh"), 1),
        "export_annual_kwh":    round(annual("elec_export_annual_kwh"), 0),
        "export_annual_trend":  trend_pct(annual_at("elec_export_annual_kwh"), annual_at("elec_export_annual_kwh", 365)),
    }

    # ── 5. Render HTML ────────────────────────────────────────────────────────
    print("Step 4: Rendering HTML…")
    html = render_html(charts, stats)

    out_path = output_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"  Written: {out_path}  ({out_path.stat().st_size:,} bytes)")
    print("Done.")


def render_html(charts: list[dict], stats: dict) -> str:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(ROOT / "web")))
    template = env.get_template("template.html")
    return template.render(charts=charts, stats=stats)


if __name__ == "__main__":
    main()
