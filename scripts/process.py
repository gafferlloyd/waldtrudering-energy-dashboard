"""
Data processing pipeline.

Loads archived + fresh data, merges onto a daily date range, interpolates
missing meter readings, and computes all derived quantities used in charts.
"""
import sqlite3
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
    """Load DWD airport weather from cache/dwd_weather.csv, or None if absent.

    DWD's own sunshine sensor (SDK) has been dead since 2026-05-01. Wherever
    DWD's sunshine is missing, backfill from LMU Munich-city (cache/lmu_city_weather.csv)
    — never Garching, which runs a different microclimate. This is a plain
    NaN-fill: the moment DWD reports real sunshine again, its value wins and
    the substitute stops being touched — no date cutoff to maintain by hand.
    """
    if cache_dir is None:
        return None
    path = cache_dir / "dwd_weather.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[["date", "tmax", "tmit", "tmin", "rain", "sunshine"]].dropna(subset=["date"])
    for col in ["tmax", "tmit", "tmin", "rain", "sunshine"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").set_index("date")

    df["sunshine_is_substitute"] = False
    city_path = cache_dir / "lmu_city_weather.csv"
    if city_path.exists():
        city = pd.read_csv(city_path, parse_dates=["date"])
        city["sunshine"] = pd.to_numeric(city["sunshine"], errors="coerce")
        city_sunshine = city.dropna(subset=["date"]).set_index("date")["sunshine"]
        gap = df["sunshine"].isna()
        fill = city_sunshine.reindex(df.index)
        df.loc[gap, "sunshine_is_substitute"] = fill[gap].notna()
        df["sunshine"] = df["sunshine"].fillna(fill)

    return df


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


# ── GoodWe daily rollup (cross-project, read-only) ─────────────────────────────
# Supplementary AC-Thor/PV/battery data for a future gas DHW-vs-heating split.
# See /home/gareth/goodwe_solar/daily_rollup.py, which builds this table nightly.

GOODWE_ROLLUP_COLS = [
    "acthor_energy_kwh", "pv_energy_total_kwh", "house_energy_kwh",
    "battery_charge_total_kwh", "battery_discharge_total_kwh",
    "grid_import_total_kwh", "grid_export_total_kwh", "rollup_complete",
]


def load_goodwe_rollup(db_path: Path) -> pd.DataFrame:
    """Read goodwe_solar's daily_rollup table. Supplementary only — degrades
    gracefully (empty frame) if the source DB/table is unavailable, unlike DWD
    weather which is mandatory for the pipeline."""
    if not db_path.exists():
        return pd.DataFrame(columns=GOODWE_ROLLUP_COLS)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        df = pd.read_sql_query(
            f"SELECT date, {', '.join(GOODWE_ROLLUP_COLS)} FROM daily_rollup",
            conn, parse_dates=["date"],
        )
        conn.close()
        return df.set_index("date")
    except sqlite3.Error:
        return pd.DataFrame(columns=GOODWE_ROLLUP_COLS)


def _reconcile_cumulative_with_daily(cumulative: pd.Series, daily_delta: pd.Series) -> pd.Series:
    """Fill gaps in a cumulative meter reading using an independent measured
    daily-delta series (here: goodwe's daily import/export), instead of flat
    linear interpolation.

    For a *closed* gap (a real manual reading exists on both sides -- the
    normal case, e.g. a skipped day or two), goodwe's day-to-day shape is
    scaled so the reconstructed days sum EXACTLY to the true observed delta
    between the two real readings. This is what avoids a reconciliation-day
    spike: the whole gap is rebuilt from scratch, so the small systematic
    offset between the utility meter and the inverter's own measurement
    (calibration, rounding) gets spread proportionally across the gap
    instead of landing entirely on one day's diff.

    For a still-*open* gap (the current holiday case: away from home, no
    next reading yet), goodwe's raw daily deltas are used unscaled as the
    best available live estimate -- plain interpolation can't do this at
    all (nothing to interpolate toward), and today it silently flat-lines
    the reading instead (0 apparent usage while away), which is what
    causes the later reconciliation-day spike in the first place. This
    open-gap estimate is provisional by construction: next time this
    pipeline runs after a real reading lands, that gap is "closed" and
    gets rebuilt with the exact scaling above -- no extra state needed,
    since everything here is recomputed fresh from raw data each build.

    Falls back to leaving the gap for the caller's own linear interpolation
    wherever goodwe data itself has any missing day within the gap (e.g.
    entirely before the PV install, or a goodwe outage day) -- or, for a
    closed gap, wherever goodwe's shape has (near-)zero total (e.g. a fully
    self-sufficient stretch with ~0 grid import all gap): there's no usable
    shape signal to scale by there, and dividing by ~0 would blow the scale
    factor up and dump the whole true delta onto one day again -- exactly
    the bug this function exists to avoid. Plain linear interpolation is the
    safer, simpler answer when goodwe has nothing to distribute against.
    """
    result = cumulative.copy()
    known_dates = cumulative.dropna().index.to_list()

    for i, anchor_date in enumerate(known_dates):
        anchor_value = cumulative[anchor_date]
        close_date = known_dates[i + 1] if i + 1 < len(known_dates) else None
        gap_end = close_date if close_date is not None else cumulative.index[-1]
        gap_days = cumulative.loc[anchor_date:gap_end].index[1:]
        if len(gap_days) == 0:
            continue
        gap_delta = daily_delta.reindex(gap_days)
        if gap_delta.isna().any():
            continue  # incomplete goodwe coverage for this gap -- leave for interpolate()

        cum = gap_delta.cumsum()
        if close_date is not None:
            true_total = cumulative[close_date] - anchor_value
            goodwe_total = cum.iloc[-1]
            if abs(goodwe_total) < 0.5:
                continue  # no usable shape signal -- leave for interpolate()
            scale = true_total / goodwe_total
            estimate = (anchor_value + cum * scale).iloc[:-1]  # don't overwrite the real close_date reading
        else:
            estimate = anchor_value + cum  # open gap: unscaled, provisional
        result.loc[estimate.index] = estimate.values

    return result


def load_goodwe_snapshot(data_dir: Path) -> pd.DataFrame:
    """Read the committed CSV fallback of goodwe_solar's daily_rollup (see
    scripts/export_goodwe_snapshot.py). Used only when the live SQLite DB
    isn't reachable — e.g. GitHub Actions CI, which has no access to the
    local-only goodwe_solar/data.db. Refreshed daily by the local systemd
    build (daily_update.sh), so it's at most one day stale."""
    path = data_dir / "goodwe_rollup_snapshot.csv"
    if not path.exists():
        return pd.DataFrame(columns=GOODWE_ROLLUP_COLS)
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date")


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(data_dir: Path | None = None, cache_dir: Path | None = None,
        goodwe_db_path: Path | None = None) -> dict:
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
    pv_kwp = cfg["pv_kwp"]
    pv_performance_ratio = cfg["pv_performance_ratio"]
    pv_latitude_deg = cfg["pv_latitude_deg"]
    pv_angstrom_as = cfg["pv_angstrom_as"]
    pv_angstrom_bs = cfg["pv_angstrom_bs"]

    if goodwe_db_path is None:
        goodwe_db_path = Path(cfg.get("goodwe_db_path", "/home/gareth/goodwe_solar/data.db"))

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

    # Load goodwe_solar's daily rollup early (needed below, to reconcile meter
    # gaps) -- prefer the live DB, fall back to the committed CSV snapshot when
    # it's unreachable (see load_goodwe_rollup()/load_goodwe_snapshot()).
    goodwe = load_goodwe_rollup(goodwe_db_path)
    if goodwe.empty:
        goodwe = load_goodwe_snapshot(data_dir)
    goodwe_r = goodwe.reindex(date_range)

    # Reindex meter readings and interpolate (elec handled separately below,
    # via goodwe reconciliation)
    m = meter.reindex(date_range)
    for col in ["gas", "water"]:
        m[col] = m[col].interpolate(method="index")
    # elec/elec_export: before falling back to *flat* linear interpolation,
    # reconstruct gaps using goodwe's own measured daily import/export where
    # available -- both because it reflects real day-to-day variation instead
    # of a flat rate, and because it's the only way to estimate a still-open
    # gap (on holiday, no next reading yet) at all; plain interpolation can't
    # extrapolate past the last known reading and silently flat-lines it
    # instead (0 apparent usage while away), which is what used to cause a
    # phantom spike/dip on the day the next real reading lands. See
    # _reconcile_cumulative_with_daily() for how the reconciliation-day error
    # is avoided once that next reading arrives.
    elec_raw = meter["elec"].reindex(date_range)
    m["elec"] = _reconcile_cumulative_with_daily(elec_raw, goodwe_r["grid_import_total_kwh"]).interpolate(method="index")

    # Export meter: same reconciliation, then interpolate any still-missing
    # interior gaps (pre-goodwe-coverage), THEN fill any remaining (leading)
    # NaN with 0 -- that only covers genuinely pre-solar dates, before the
    # export register existed. Filling gaps with 0 directly (previous
    # behaviour, still needed here for the leading case) fabricated a phantom
    # zero cumulative reading on any skipped day, corrupting the *next* day's
    # diff into a huge fake spike (e.g. reported 306 kWh in one day --
    # verified 2026-07-18: sheet rows exist for 07-14/16/18 but not 07-15/17,
    # a routine every-other-day gap, not an error -- the ~31 kWh real 2-day
    # export delta got all attributed to a single day instead of split evenly
    # across the gap).
    if "elec_export" in meter.columns:
        export_raw = meter["elec_export"].reindex(date_range)
        m["elec_export"] = _reconcile_cumulative_with_daily(
            export_raw, goodwe_r["grid_export_total_kwh"]
        ).interpolate(method="index").fillna(0)
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

    # Join goodwe_solar's daily rollup (AC-Thor/PV/battery) — left join on date;
    # NaN before 2026-06-22 PV install is correct (nothing existed yet).
    # (Already loaded above as `goodwe`, to reconcile meter gaps.)
    daily = daily.join(goodwe)

    # Sanity-clip negative consumption / export
    for col in ["use_gas_m3", "use_elec_kwh", "use_elec_export_kwh", "use_water_m3", "use_gas_kwh"]:
        daily.loc[daily[col] < 0, col] = np.nan

    # Degree days
    dd = np.maximum(0.0, base_temp - daily["tmit"])
    daily["degree_days"] = np.where(dd > 0, dd + dd_offset, 0.0)

    # Theoretical PV output: FAO-56 Angström-Prescott solar-radiation-from-
    # sunshine-hours model (Allen et al. 1998, ch. 3), applied to the site's
    # 10.52 kWp array. Deliberately uses horizontal-surface radiation rather
    # than a tilted plane-of-array transposition -- self-contained (no import
    # of goodwe_solar's own, more accurate but only ~7-week-deep irradiance
    # model) at the cost of not modelling the 40°-tilt/SE-azimuth seasonal
    # skew explicitly; that skew is absorbed into pv_performance_ratio as a
    # single scalar instead of a proper POA transposition.
    lat_rad = np.radians(pv_latitude_deg)
    doy = daily.index.dayofyear.values.astype(float)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    decl = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    sunset_angle = np.arccos(np.clip(-np.tan(lat_rad) * np.tan(decl), -1.0, 1.0))
    max_daylight_hours = (24 / np.pi) * sunset_angle
    Gsc = 0.0820  # MJ m-2 min-1, solar constant
    Ra = ((24 * 60 / np.pi) * Gsc * dr *
          (sunset_angle * np.sin(lat_rad) * np.sin(decl)
           + np.cos(lat_rad) * np.cos(decl) * np.sin(sunset_angle)))
    sunshine_fraction = np.clip(daily["sunshine"].values / max_daylight_hours, 0.0, 1.0)
    Rs = (pv_angstrom_as + pv_angstrom_bs * sunshine_fraction) * Ra  # MJ/m2/day
    Rs_kwh_m2 = Rs / 3.6
    daily["pv_theoretical_kwh"] = Rs_kwh_m2 * pv_kwp * pv_performance_ratio

    # Hot-water baseline: segmented (hinge) regression of gas vs tmin.
    # gas(tmin) = baseline for tmin >= T*, baseline + slope*(T* - tmin) below T*.
    # This replaces an earlier histogram-mode heuristic (just the modal gas value
    # pre-solar): that approach ignored temperature shape entirely and (verified
    # 2026-07-16) put the baseline ~15% too high vs this fit, which cross-validates
    # cleanly against the raw mean gas use on the actual warmest days.
    #
    # A literal CP-style hyperbolic fit (gas = baseline + a/(tmin-c), directly
    # mirroring cycling's P=W'/t+CP) was tried first and rejected: it isn't
    # identifiable from this data (this dataset's tmin never approaches where a
    # true hyperbola would visibly flatten, unlike CP data spanning efforts near
    # the sustainable duration) and converges to a physically impossible negative
    # baseline. The hinge model is the right shape -- heating genuinely doesn't
    # fire above some threshold, rather than power tapering smoothly to infinity.
    #
    # Two-stage, deliberately not a single fit on one window:
    #  1. T* (balance-point temp) is a structural property of the building's
    #     heating response -- established once from the full pre-solar-thermal
    #     history (~10 years, wide temperature range), where it's well identified.
    #     Refitting it on only 12 months would be shaky (a single year may not
    #     even span the full temperature range).
    #  2. baseline is a direct mean of trailing-12-month gas use on days with
    #     tmin > 10C (comfortably above T*, so no heating contribution at all) --
    #     not a regression. Simpler and more robust than fitting slope+baseline
    #     together (tried 2026-07-16, gave 8.99 -- close, but adds model risk for
    #     no real benefit: see slope-instability note below). This baseline is
    #     the number we actually want to watch move over time: as solar
    #     increasingly covers hot water, more recent warm/mild days show near-
    #     zero gas, and this legitimately falls to reflect "how much gas is hot
    #     water actually still costing us now" -- not pinned to a fixed constant.
    #  3. slope (for the chart's descending line only, not otherwise used) is a
    #     single-parameter regression on the same trailing window, forced
    #     through (T*, baseline) so the chart's line always meets the flat
    #     segment cleanly -- not a free 2-parameter fit.
    #
    # Rejected 2026-07-16: holding BOTH T* and slope fixed at their full-history
    # structural values, refitting only the intercept on the trailing 12 months
    # -- gave a nonsensical 1.6 kWh/day. Diagnosed via residuals-by-temperature-
    # band, strongly non-flat (-12.8 at cold, +9.4 at warm): this past year's
    # actual cold-weather gas response is genuinely shallower (~1.9 vs the
    # 10yr-average 3.21 kWh/day/C) -- likely the "deliberate gas-saving
    # electrification" (infrared heaters etc.) already noted elsewhere in this
    # project's notes. Slope is not a stable structural constant; only T* is.
    solar_thermal_date = pd.Timestamp(cfg.get("solar_thermal_date", "2099-01-01"))
    struct_mask = daily.index < solar_thermal_date
    gas_struct = daily.loc[struct_mask, "use_gas_kwh"] if struct_mask.sum() > 10 else daily["use_gas_kwh"]
    tmin_struct = daily.loc[gas_struct.index, "tmin"]
    valid = gas_struct.notna() & tmin_struct.notna()
    gas_struct, tmin_struct = gas_struct[valid].values, tmin_struct[valid].values

    heating_balance_temp_c = None
    heating_slope_kwh_per_c = None
    hot_water_kwh = 0.0

    if len(gas_struct) > 10:
        n = len(gas_struct)
        t_grid = np.linspace(-5, 18, 461)
        best = None
        for t_star in t_grid:
            heat = np.maximum(0.0, t_star - tmin_struct)
            A = np.column_stack([np.ones(n), heat])
            coef, *_ = np.linalg.lstsq(A, gas_struct, rcond=None)
            ss_res = float(np.sum((gas_struct - A @ coef) ** 2))
            if best is None or ss_res < best[0]:
                best = (ss_res, t_star)
        _, heating_balance_temp_c = best

        recent_cutoff = daily.index.max() - pd.Timedelta(days=365)
        gas_recent = daily.loc[
            (daily.index > recent_cutoff) & (daily.index < solar_thermal_date),
            "use_gas_kwh"
        ]
        tmin_recent = daily.loc[gas_recent.index, "tmin"]
        rvalid = gas_recent.notna() & tmin_recent.notna()
        gas_recent, tmin_recent = gas_recent[rvalid].values, tmin_recent[rvalid].values
        src_gas, src_tmin = (gas_recent, tmin_recent) if len(gas_recent) > 20 else (gas_struct, tmin_struct)

        # Stage 2: direct mean on clearly-warm trailing days (tmin > 10C).
        warm_mask = src_tmin > 10.0
        if warm_mask.sum() >= 5:
            hot_water_kwh = max(0.0, float(src_gas[warm_mask].mean()))
        else:
            heat = np.maximum(0.0, heating_balance_temp_c - src_tmin)
            A = np.column_stack([np.ones(len(src_gas)), heat])
            coef, *_ = np.linalg.lstsq(A, src_gas, rcond=None)
            hot_water_kwh = max(0.0, float(coef[0]))

        # Stage 3: slope only, forced through (T*, hot_water_kwh), for the chart line.
        heat = np.maximum(0.0, heating_balance_temp_c - src_tmin)
        denom = float(np.sum(heat ** 2))
        heating_slope_kwh_per_c = float(np.sum(heat * (src_gas - hot_water_kwh)) / denom) if denom > 0 else 0.0

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
    median_elec_doy = _compute_doy_median(daily["use_elec_kwh"], exclude_last_n=3)
    recent_elec_doy = _compute_doy_recent(daily["use_elec_kwh"], n=3)

    # Metre readings (cumulative) for raw scatter
    meter_raw = meter.copy()

    return {
        "daily":            daily,
        "hot_water_kwh":    hot_water_kwh,
        "heating_balance_temp_c":    heating_balance_temp_c,
        "heating_slope_kwh_per_c":   heating_slope_kwh_per_c,
        "year_groups":      year_groups,
        "median_gas_doy":   median_gas_doy,
        "recent_gas_doy":   recent_gas_doy,
        "median_elec_doy":  median_elec_doy,
        "recent_elec_doy":  recent_elec_doy,
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

    overlap = daily.dropna(subset=["pv_energy_total_kwh", "pv_theoretical_kwh"])
    if len(overlap) > 5:
        ratio = overlap["pv_energy_total_kwh"].sum() / overlap["pv_theoretical_kwh"].sum()
        cfg = result["config"]
        print(f"PV calibration ratio (actual/theoretical, {len(overlap)} days): {ratio:.2f} "
              f"(current pv_performance_ratio={cfg['pv_performance_ratio']})")
