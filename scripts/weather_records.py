"""
Weather records/streaks panel for the weather page.

Reuses exactly the same category definitions and rolling-annual methodology
as generate_charts.py::chart_weather (same sunny-day formula, same 365-day
rolling window), so the numbers here always agree with what that chart
plots — this is a records summary of that chart's own data, not a second
independent calculation.
"""
import numpy as np
import pandas as pd

MOVAR = 365
ROLL_KW = dict(window=MOVAR, min_periods=MOVAR // 2)


def _sunny_mask(w: pd.DataFrame) -> pd.Series:
    doy = w.index.dayofyear.to_numpy()
    sun_pot = 6.5 + 3.5 * np.sin(-np.pi / 2 - 3 * np.pi / 32 + doy * 2 * np.pi / 365)
    return w["sunshine"] > sun_pot


def _longest_streak(mask: pd.Series) -> tuple[int, pd.Timestamp | None, pd.Timestamp | None]:
    """Longest run of consecutive True values over a *contiguous daily index*
    (NaN/False both break a run). Returns (length, start, end)."""
    best_len, best_start, best_end = 0, None, None
    cur_len, cur_start, prev_date = 0, None, None
    for date, val in mask.items():
        contiguous = prev_date is None or (date - prev_date) == pd.Timedelta(days=1)
        if not contiguous:
            cur_len = 0
        if val:
            if cur_len == 0:
                cur_start = date
            cur_len += 1
            if cur_len > best_len:
                best_len, best_start, best_end = cur_len, cur_start, date
        else:
            cur_len = 0
        prev_date = date
    return best_len, best_start, best_end


def _current_streak(mask: pd.Series) -> int:
    """Length of the run ending at the mask's last date (0 if that day is False)."""
    n = 0
    prev_date = None
    for date, val in mask.items():
        contiguous = prev_date is None or (date - prev_date) == pd.Timedelta(days=1)
        if not contiguous or not val:
            n = 0
        else:
            n += 1
        prev_date = date
    return n if mask.iloc[-1] else 0


def _annual_count_series(mask: pd.Series, chart_start: pd.Timestamp) -> pd.Series:
    # Clip to chart_start: min_periods=182 lets the rolling window start
    # producing values from a partial (e.g. summer-only) first year, which
    # reads as a spurious extreme — same ramp-up guard as chart_weather.
    return (mask.astype(float).rolling(**ROLL_KW).mean() * MOVAR).dropna().loc[chart_start:]


def _fmt_date(d: pd.Timestamp | None) -> str | None:
    return d.strftime("%d %b %Y") if d is not None else None


def _day_category(name: str, mask: pd.Series, chart_start: pd.Timestamp) -> dict:
    streak_len, streak_start, streak_end = _longest_streak(mask)
    cur_streak = _current_streak(mask)
    is_streak_record = cur_streak > 0 and cur_streak >= streak_len

    annual = _annual_count_series(mask, chart_start)
    record_count = annual.max() if len(annual) else None
    record_count_date = annual.idxmax() if len(annual) else None
    current_count = annual.iloc[-1] if len(annual) else None
    is_count_record = (
        current_count is not None and record_count is not None
        and current_count >= record_count
    )

    return {
        "name": name,
        "record_streak_days": streak_len,
        "record_streak_range": (
            f"{_fmt_date(streak_start)} – {_fmt_date(streak_end)}" if streak_len else None
        ),
        "current_streak_days": cur_streak,
        "current_streak_is_record": is_streak_record,
        "record_count": round(record_count) if record_count is not None else None,
        "record_count_date": _fmt_date(record_count_date),
        "current_count": round(current_count) if current_count is not None else None,
        "current_count_is_record": is_count_record,
    }


def _continuous_metric(name: str, series: pd.Series, unit: str, agg: str,
                        chart_start: pd.Timestamp) -> dict:
    """agg: 'mean' for temperature-style annual averages, 'sum' for totals
    (rainfall, sunshine) — both rolled here over the same 365-day window as
    chart_weather. 'prerolled' skips rolling entirely — for a series (like
    the sun:rain ratio) that's already a rolling-annual value by the time
    it's passed in, so rolling it again here would double-smooth it.

    Clipped to chart_start for the same reason as _annual_count_series: the
    rolling window's min_periods lets it start on a partial first year,
    which otherwise shows up as a spurious all-time high/low."""
    if agg == "prerolled":
        roll = series.dropna().loc[chart_start:]
    else:
        roll = series.rolling(**ROLL_KW).mean()
        if agg == "sum":
            roll = roll * MOVAR
        roll = roll.dropna().loc[chart_start:]
    if len(roll) == 0:
        return {"name": name, "unit": unit, "highest": None, "lowest": None, "current": None}
    return {
        "name": name,
        "unit": unit,
        "highest": round(roll.max(), 1),
        "highest_date": _fmt_date(roll.idxmax()),
        "lowest": round(roll.min(), 1),
        "lowest_date": _fmt_date(roll.idxmin()),
        "current": round(roll.iloc[-1], 1),
    }


def compute(weather_dwd: pd.DataFrame) -> dict:
    w = weather_dwd
    chart_start = w.index[0] + pd.Timedelta(days=MOVAR)

    day_categories = [
        _day_category("Ice days (Tmax < 0°C)",     w["tmax"] <= 0,        chart_start),
        _day_category("Frost days (Tmin ≤ 0°C)",    w["tmin"] <= 0,        chart_start),
        _day_category("Days ≥ 20°C",                w["tmax"] >= 20,       chart_start),
        _day_category("Days ≥ 25°C",                w["tmax"] >= 25,       chart_start),
        _day_category("Days ≥ 30°C",                w["tmax"] >= 30,       chart_start),
        _day_category("Sunny days",                 _sunny_mask(w),       chart_start),
        _day_category("Rainy days (> 5mm)",          w["rain"] > 5,        chart_start),
    ]

    rain_r = w["rain"].rolling(**ROLL_KW).mean().replace(0, np.nan)
    sun_rain_ratio = (w["sunshine"].rolling(**ROLL_KW).mean() / rain_r)

    continuous_metrics = [
        _continuous_metric("Annual min. temperature",  w["tmin"], "°C", "mean", chart_start),
        _continuous_metric("Annual mean temperature",  w["tmit"], "°C", "mean", chart_start),
        _continuous_metric("Annual max. temperature",  w["tmax"], "°C", "mean", chart_start),
        _continuous_metric("Annual rainfall",           w["rain"], "mm", "sum", chart_start),
        _continuous_metric("Annual sunshine",           w["sunshine"], "h", "sum", chart_start),
        _continuous_metric("Sunshine : Rain ratio",     sun_rain_ratio, "", "prerolled", chart_start),
    ]

    return {"day_categories": day_categories, "continuous_metrics": continuous_metrics}
