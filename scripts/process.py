"""
Data processing pipeline.

Loads archived + fresh data, merges onto a daily date range, interpolates
missing meter readings, and computes all derived quantities used in charts.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def load_config():
    with open(ROOT / "config" / "settings.yaml") as f:
        return yaml.safe_load(f)


def _price_series(dates: pd.DatetimeIndex, price_list: list) -> np.ndarray:
    """Build a per-day price array from a list of {from, price} records sorted by date."""
    boundaries = pd.to_datetime([p["from"] for p in price_list])
    values = np.array([p["price"] for p in price_list])
    idx = np.searchsorted(boundaries, dates, side="right") - 1
    return values[np.clip(idx, 0, len(values) - 1)]


# ── Meter data ────────────────────────────────────────────────────────────────

def _load_meter_archive(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[["date", "elec", "gas", "water"]].dropna(subset=["date"])
    return df


def _load_meter_fresh(path: Path) -> pd.DataFrame:
    """Parse current Google Sheets CSV (from 2026-07-07, new electricity meter).

    Columns: date, date_num, elec_import, elec_export, gas, water
    """
    raw = pd.read_csv(path, header=None)
    dates = pd.to_datetime(raw.iloc[:, 0], errors="coerce")
    raw = raw[dates.notna()].copy()
    df = pd.DataFrame({
        "date":        pd.to_datetime(raw.iloc[:, 0], errors="coerce"),
        "elec":        pd.to_numeric(raw.iloc[:, 2], errors="coerce"),
        "elec_export": pd.to_numeric(raw.iloc[:, 3], errors="coerce"),
        "gas":         pd.to_numeric(raw.iloc[:, 4], errors="coerce"),
        "water":       pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
    })
    return df.dropna(subset=["date"])


def load_meter_data(data_dir: Path, cache_dir: Path | None = None) -> pd.DataFrame:
    frames = [_load_meter_archive(data_dir / "meter_archive.csv")]
    if cache_dir:
        fresh_path = cache_dir / "meterreadings.csv"
        if fresh_path.exists():
            frames.append(_load_meter_fresh(fresh_path))
    # Archive is the authoritative source for any dates it covers; keep="first"
    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("date", keep="first")
        .sort_values("date")
        .set_index("date")
    )
    # Archive rows predate solar installation — fill missing export column with 0
    if "elec_export" in combined.columns:
        combined["elec_export"] = combined["elec_export"].fillna(0)
    return combined


# ── Weather data ──────────────────────────────────────────────────────────────

def _load_weather_archive(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[["date", "tmax", "tmit", "tmin", "rain", "sunshine"]].dropna(subset=["date"])
    for col in ["tmax", "tmit", "tmin", "rain", "sunshine"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_weather_fresh(path: Path) -> pd.DataFrame:
    """Parse new LMU format: DD.MM.YYYY, tmax, tmit, tmin, rain, pmax1, pmax2, pmin, wind, sunshine, excel"""
    try:
        df = pd.read_csv(path, header=None,
                         names=["date","tmax","tmit","tmin","rain","pmax1","pmax2","pmin","wind","sunshine","excel"],
                         usecols=["date","tmax","tmit","tmin","rain","sunshine"])
        df["date"] = pd.to_datetime(df["date"], format="%d.%m.%Y", errors="coerce")
        return df.dropna(subset=["date"])
    except Exception:
        # Fall back to old 10-column format
        df = pd.read_csv(path, header=None,
                         names=["date","tmax","tmit","tmin","rain","pmax","pmin","wind","sunshine","excel"],
                         usecols=["date","tmax","tmit","tmin","rain","sunshine"])
        df["date"] = pd.to_datetime(df["date"], format="%d.%m.%Y", errors="coerce")
        return df.dropna(subset=["date"])


def load_dwd_weather(cache_dir: Path | None = None) -> pd.DataFrame | None:
    """Load DWD airport weather from cache/dwd_weather.csv, or None if absent."""
    if cache_dir is None:
        return None
    path = cache_dir / "dwd_weather.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[["date", "tmax", "tmit", "tmin", "rain", "sunshine"]].dropna(subset=["date"])
    for col in ["tmax", "tmit", "tmin", "rain", "sunshine"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").set_index("date")


def load_weather_data(data_dir: Path, cache_dir: Path | None = None) -> pd.DataFrame:
    frames = [_load_weather_archive(data_dir / "weather_archive.csv")]
    if cache_dir:
        fresh_path = cache_dir / "weatherdata.csv"
        if fresh_path.exists():
            frames.append(_load_weather_fresh(fresh_path))
        lmu_path = cache_dir / "lmu_weather.csv"
        if lmu_path.exists():
            frames.append(_load_weather_archive(lmu_path))
    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("date")
        .sort_values("date")
        .set_index("date")
    )
    return combined


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(data_dir: Path | None = None, cache_dir: Path | None = None) -> dict:
    if data_dir is None:
        data_dir = ROOT / "data"
    if cache_dir is None:
        cache_dir = ROOT / "cache"

    cfg = load_config()
    gas_cv = cfg["gas_calorific_kwh_per_m3"]
    base_temp = cfg["base_temperature_c"]
    dd_offset = cfg["degree_day_offset_k"]
    gas_prices  = cfg["gas_prices"]
    elec_prices = cfg["elec_prices"]
    floor_area = cfg["floor_area_m2"]

    meter = load_meter_data(data_dir, cache_dir)
    weather_dwd = load_dwd_weather(cache_dir)

    if weather_dwd is None:
        raise RuntimeError("DWD weather data not found — run fetch_dwd_weather.py first")

    # Date range: first date with all three meter readings → latest of DWD or meter.
    # Meter data can arrive ahead of DWD (which lags ~1-2 days), so we extend
    # to whichever is later so energy stats aren't artificially delayed.
    first_complete = meter.dropna(how="any").index.min()
    data_end = max(weather_dwd.index.max(), meter.index.max())
    date_range = pd.date_range(first_complete, data_end, freq="D")

    # Reindex DWD weather; interpolate small interior gaps only (limit_area="inside"
    # prevents forward-filling fake weather beyond the last real DWD observation).
    w = weather_dwd.reindex(date_range)
    for col in ["tmax", "tmit", "tmin", "rain", "sunshine"]:
        w[col] = w[col].interpolate(method="index", limit=7, limit_area="inside")

    # Reindex meter readings and interpolate
    m = meter.reindex(date_range)
    for col in ["elec", "gas", "water"]:
        m[col] = m[col].interpolate(method="index")
    # Export meter: NaN before solar installation = 0 (no solar existed pre-2026-07)
    if "elec_export" in meter.columns:
        m["elec_export"] = meter["elec_export"].reindex(date_range).fillna(0)
    else:
        m["elec_export"] = 0.0

    # Daily consumption via diff (first row becomes NaN, drop it)
    daily = pd.DataFrame(index=date_range[1:])
    daily["use_gas_m3"]          = m["gas"].diff().iloc[1:]
    daily["use_elec_kwh"]        = m["elec"].diff().iloc[1:]
    daily["use_elec_export_kwh"] = m["elec_export"].diff().iloc[1:]
    daily["use_water_m3"]        = m["water"].diff().iloc[1:]
    daily["use_gas_kwh"]         = daily["use_gas_m3"] * gas_cv

    # Join weather (offset by 1 since diff loses first row)
    for col in ["tmax", "tmit", "tmin", "rain", "sunshine"]:
        daily[col] = w[col].iloc[1:].values

    # Sanity-clip negative consumption / export
    for col in ["use_gas_m3", "use_elec_kwh", "use_elec_export_kwh", "use_water_m3", "use_gas_kwh"]:
        daily.loc[daily[col] < 0, col] = np.nan

    # Degree days
    dd = np.maximum(0.0, base_temp - daily["tmit"])
    daily["degree_days"] = np.where(dd > 0, dd + dd_offset, 0.0)

    # Hot-water baseline: modal bin of pre-solar-thermal gas data.
    # Using only the period before solar thermal went live gives the boiler's
    # true hot-water duty (~12 kWh/day), which is still needed on winter days
    # when solar thermal cannot cover demand.
    solar_thermal_date = pd.Timestamp(cfg.get("solar_thermal_date", "2099-01-01"))
    gas_pre_solar = daily.loc[daily.index < solar_thermal_date, "use_gas_kwh"].dropna()
    gas_valid = gas_pre_solar if len(gas_pre_solar) > 10 else daily["use_gas_kwh"].dropna()
    if len(gas_valid) > 10:
        counts, edges = np.histogram(gas_valid, bins=100)
        hot_water_kwh = float(edges[np.argmax(counts)] + (edges[1] - edges[0]) / 2)
    else:
        hot_water_kwh = 0.0

    # Rolling averages
    for w_days in [7, 28, 365]:
        daily[f"gas_ma{w_days}"]         = daily["use_gas_kwh"].rolling(w_days, min_periods=w_days//2).mean()
        daily[f"elec_ma{w_days}"]        = daily["use_elec_kwh"].rolling(w_days, min_periods=w_days//2).mean()
        daily[f"elec_export_ma{w_days}"] = daily["use_elec_export_kwh"].rolling(w_days, min_periods=1).mean()
        daily[f"water_ma{w_days}"]       = daily["use_water_m3"].rolling(w_days, min_periods=w_days//2).mean()

    # Annualised rolling sums (kWh/year)
    daily["gas_annual_kwh"]         = daily["use_gas_kwh"].rolling(365, min_periods=180).sum()
    daily["elec_annual_kwh"]        = daily["use_elec_kwh"].rolling(365, min_periods=180).sum()
    daily["elec_export_annual_kwh"] = daily["use_elec_export_kwh"].rolling(365, min_periods=1).sum()


    # Per-day energy prices (date-based)
    daily["price_gas"]  = _price_series(daily.index, gas_prices)
    daily["price_elec"] = _price_series(daily.index, elec_prices)

    # Annual rolling cost (EUR)
    daily["cost_gas_annual"]   = (daily["use_gas_kwh"]  * daily["price_gas"]).rolling(365, min_periods=180).sum()
    daily["cost_elec_annual"]  = (daily["use_elec_kwh"] * daily["price_elec"]).rolling(365, min_periods=180).sum()
    daily["cost_total_annual"] = daily["cost_gas_annual"] + daily["cost_elec_annual"]

    # Efficiency per m²  (1-year rolling and 3-year rolling)
    daily["efficiency_kwh_m2"]     = daily["gas_annual_kwh"] / floor_area
    daily["efficiency_3yr_kwh_m2"] = (
        daily["use_gas_kwh"].rolling(3 * 365, min_periods=365).sum() / (floor_area * 3)
    )

    # ── Year-by-year data: heating years (Jul 1 → Jun 30) ────────────────────
    # Starting in July puts winter in the middle of the x-axis.
    year_groups = {}
    for yr in sorted(daily.index.year.unique()):
        start = pd.Timestamp(year=yr, month=7, day=1)
        end   = pd.Timestamp(year=yr + 1, month=6, day=30)
        if start > daily.index[-1]:
            continue
        ydf = daily[start:end].copy()
        if len(ydf) < 3:
            continue
        # Drop years that don't start within 30 days of Jul 1 — a late start
        # shifts the doy counter and makes the trace appear x-offset vs full years.
        if ydf.index[0] > start + pd.Timedelta(days=30):
            continue
        ydf["doy"]         = range(1, len(ydf) + 1)
        ydf["cum_gas_kwh"] = ydf["use_gas_kwh"].fillna(0).cumsum()
        ydf["cum_dd"]      = ydf["degree_days"].fillna(0).cumsum()
        label = f"{yr}/{str(yr + 1)[-2:]}"
        year_groups[label] = ydf

    # Day-of-year median pattern for "all years except last 3"
    median_gas_doy = _compute_doy_median(daily["use_gas_kwh"], exclude_last_n=3)
    recent_gas_doy = _compute_doy_recent(daily["use_gas_kwh"], n=3)

    # Metre readings (cumulative) for raw scatter
    meter_raw = meter.copy()

    return {
        "daily":            daily,
        "hot_water_kwh":    hot_water_kwh,
        "year_groups":      year_groups,
        "median_gas_doy":   median_gas_doy,
        "recent_gas_doy":   recent_gas_doy,
        "config":           cfg,
        "meter_raw":        meter_raw,
        "weather_dwd":      weather_dwd,
    }


def _compute_doy_median(series: pd.Series, exclude_last_n: int = 3) -> pd.Series:
    """Compute median daily-of-year profile, excluding the most recent N years."""
    df = pd.DataFrame({"val": series})
    df["year"] = df.index.year
    df["doy"]  = df.index.dayofyear
    cutoff_year = df["year"].max() - exclude_last_n
    subset = df[df["year"] <= cutoff_year].dropna()
    return subset.groupby("doy")["val"].median()


def _compute_doy_recent(series: pd.Series, n: int = 3) -> pd.Series:
    """Compute mean daily-of-year profile for the most recent N full years."""
    df = pd.DataFrame({"val": series})
    df["year"] = df.index.year
    df["doy"]  = df.index.dayofyear
    max_year = df["year"].max()
    subset = df[(df["year"] > max_year - n) & (df["year"] < max_year)].dropna()
    return subset.groupby("doy")["val"].mean()


if __name__ == "__main__":
    result = run()
    daily = result["daily"]
    print(f"Daily rows: {len(daily)}, {daily.index[0].date()} to {daily.index[-1].date()}")
    print(f"Hot-water baseline: {result['hot_water_kwh']:.1f} kWh/day")
    print(daily[["use_gas_kwh","use_elec_kwh","degree_days","cost_total_annual"]].tail(5))
