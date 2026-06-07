"""
Generate all Plotly charts from the processed data.

Returns a list of dicts: {"title": str, "id": str, "json": str}
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_PALETTE = [
    "#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3",
    "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD",
]

# Shifted DOY: July 1 = 0, so winter sits in the centre (~day 153–215)
_DOY_ORIGIN = 182   # calendar day of July 1 (non-leap year)
_MONTH_TICKVALS = [0,  31,  62,  92, 123, 153, 184, 215, 243, 274, 304, 335]
_MONTH_TICKTEXT = ["Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun"]


def _shift_doy(doy) -> np.ndarray:
    return (np.asarray(doy, dtype=float) - _DOY_ORIGIN) % 365


def _smooth_circular(arr, window: int = 14) -> np.ndarray:
    """Rolling mean that wraps correctly at the year boundary."""
    arr = np.asarray(arr, dtype=float)
    n = len(arr)
    padded = np.concatenate([arr[-window:], arr, arr[:window]])
    smoothed = pd.Series(padded).rolling(window, center=True, min_periods=window // 2).mean().to_numpy()
    return smoothed[window: window + n]


def _yr_colour(idx: int) -> str:
    return _PALETTE[idx % len(_PALETTE)]


def _smooth(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, center=True, min_periods=window // 2).mean()


# ── Individual chart functions ────────────────────────────────────────────────

def chart_annual_gas(daily: pd.DataFrame, hot_water_kwh: float) -> go.Figure:
    fig = go.Figure()

    # Primary axis: heating efficiency kWh/m²/yr
    eff1 = daily["efficiency_kwh_m2"].dropna()
    eff3 = daily["efficiency_3yr_kwh_m2"].dropna()
    fig.add_trace(go.Scatter(x=eff1.index, y=eff1.values,
                             name="Efficiency 1yr",
                             line=dict(color=_PALETTE[0], width=1, dash="dot"),
                             opacity=0.6))
    fig.add_trace(go.Scatter(x=eff3.index, y=eff3.values,
                             name="Efficiency 3yr",
                             line=dict(color=_PALETTE[0], width=2.5)))

    # Secondary axis: absolute gas use MWh/yr
    yr1 = daily["gas_annual_kwh"].dropna() / 1000
    yr3 = daily["use_gas_kwh"].rolling(3 * 365, min_periods=365).sum().dropna() / 3000
    fig.add_trace(go.Scatter(x=yr1.index, y=yr1.values,
                             name="Gas 1yr",
                             line=dict(color=_PALETTE[1], width=1, dash="dot"),
                             yaxis="y2", opacity=0.6))
    fig.add_trace(go.Scatter(x=yr3.index, y=yr3.values,
                             name="Gas 3yr avg",
                             line=dict(color=_PALETTE[1], width=2),
                             yaxis="y2"))

    fig.update_layout(
        title="Annual Gas Use & Heating Efficiency (rolling)",
        xaxis_title="Date",
        yaxis=dict(title="kWh / m² / year"),
        yaxis2=dict(title="MWh / year", overlaying="y", side="right",
                    range=[0, 25], showgrid=False),
    )
    return fig


def chart_electricity(daily: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    elec_ann = daily["elec_annual_kwh"].dropna() / 1000

    fig.add_trace(go.Scatter(x=daily.index, y=daily["elec_ma7"],
                             name="7-day avg", line=dict(color=_PALETTE[2], width=1),
                             opacity=0.6))
    fig.add_trace(go.Scatter(x=elec_ann.index, y=elec_ann.values,
                             name="Annual (rolling)", line=dict(color=_PALETTE[0], width=2),
                             yaxis="y2"))

    fig.update_layout(
        title="Electricity Use",
        xaxis_title="Date",
        yaxis=dict(title="kWh / day", range=[0, 30]),
        yaxis2=dict(title="MWh / year", overlaying="y", side="right",
                    range=[0, 10], showgrid=False),
    )
    return fig


def chart_water(daily: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily.index, y=daily["water_ma7"],
                             name="7-day avg", line=dict(color=_PALETTE[2], width=1)))
    water_ann = (daily["use_water_m3"].rolling(365, min_periods=180).sum()).dropna()
    fig.add_trace(go.Scatter(x=water_ann.index, y=water_ann.values,
                             name="Annual (rolling)", line=dict(color=_PALETTE[0], width=2),
                             yaxis="y2"))
    fig.update_layout(
        title="Water Use",
        xaxis_title="Date",
        yaxis=dict(title="m³ / day", range=[0, 1.5]),
        yaxis2=dict(title="m³ / year", overlaying="y", side="right",
                    range=[0, 300], showgrid=False),
    )
    return fig


def chart_gas_vs_temp(daily: pd.DataFrame, hot_water_kwh: float) -> go.Figure:
    fig = go.Figure()
    cutoff = daily.index[-1] - pd.Timedelta(days=365)
    old    = daily[daily.index <= cutoff]
    recent = daily[daily.index > cutoff]

    fig.add_trace(go.Scatter(
        x=old["tmin"], y=old["use_gas_kwh"],
        mode="markers", name="Historical",
        marker=dict(color=_PALETTE[8], size=3, opacity=0.3)))
    fig.add_trace(go.Scatter(
        x=recent["tmin"], y=recent["use_gas_kwh"],
        mode="markers", name="Last 12 months",
        marker=dict(color=_PALETTE[3], size=5, opacity=0.8)))

    # Hockey-stick fit on recent data: linear below breakpoint, flat at hot_water above
    fit = recent[["tmin", "use_gas_kwh"]].dropna()
    fit = fit[(fit["use_gas_kwh"] > 0) & (fit["tmin"] < 20)]
    heating = fit[fit["use_gas_kwh"] > hot_water_kwh * 1.15]
    if len(heating) >= 8:
        coeffs = np.polyfit(heating["tmin"], heating["use_gas_kwh"], 1)
        slope, intercept = coeffs
        if slope < 0:
            t_break = (hot_water_kwh - intercept) / slope   # where line meets baseline
            xs = np.linspace(-25, min(25, t_break + 8), 300)
            ys = np.maximum(hot_water_kwh, np.polyval(coeffs, xs))
            fig.add_trace(go.Scatter(
                x=xs, y=ys, name="Fit (last 12 months)",
                line=dict(color=_PALETTE[0], width=2, dash="dash")))

    # Hot water baseline
    fig.add_hline(y=hot_water_kwh, line_dash="dot", line_color=_PALETTE[3],
                  annotation_text=f"Hot water {hot_water_kwh:.0f} kWh/d",
                  annotation_position="top left")

    fig.update_layout(title="Gas Use vs. Min Temperature",
                      xaxis_title="Min Temperature (°C)",
                      yaxis_title="Gas Use (kWh/day)",
                      xaxis=dict(range=[-25, 25]),
                      yaxis=dict(range=[0, 250]))
    return fig


def chart_gas_day_of_year(daily: pd.DataFrame,
                           median_doy: pd.Series,
                           recent_doy: pd.Series) -> go.Figure:
    fig = go.Figure()

    # All historical data as dots, shifted so July=0
    x_all = _shift_doy(daily.index.dayofyear)
    fig.add_trace(go.Scatter(
        x=x_all, y=daily["use_gas_kwh"].values,
        mode="markers", name="All days",
        marker=dict(color=_PALETTE[8], size=2, opacity=0.25)))

    # Median seasonal profile — shift and circularly smooth
    if len(median_doy):
        shifted_idx = _shift_doy(median_doy.index)
        order = np.argsort(shifted_idx)
        sm = _smooth_circular(median_doy.values[order], 14)
        fig.add_trace(go.Scatter(
            x=shifted_idx[order], y=sm,
            mode="lines", name="Median (all years, 14d)",
            line=dict(color=_PALETTE[0], width=2)))

    # Recent 3-year mean — same treatment
    if len(recent_doy):
        shifted_idx = _shift_doy(recent_doy.index)
        order = np.argsort(shifted_idx)
        sm = _smooth_circular(recent_doy.values[order], 14)
        fig.add_trace(go.Scatter(
            x=shifted_idx[order], y=sm,
            mode="lines", name="Mean (last 3 years, 14d)",
            line=dict(color=_PALETTE[3], width=2)))

    # Current heating year (Jul 1 → today), labelled "YYYY/YY"
    last = daily.index[-1]
    hy_start_yr = last.year if last.month >= 7 else last.year - 1
    hy_start = pd.Timestamp(year=hy_start_yr, month=7, day=1)
    curr = daily[daily.index >= hy_start]
    hy_label = f"{hy_start_yr}/{str(hy_start_yr + 1)[-2:]}"
    fig.add_trace(go.Scatter(
        x=_shift_doy(curr.index.dayofyear), y=curr["use_gas_kwh"].values,
        mode="lines", name=hy_label,
        line=dict(color=_PALETTE[2], width=1.5)))

    fig.update_layout(
        title="Gas Use by Day-of-Year",
        xaxis=dict(range=[0, 364], tickvals=_MONTH_TICKVALS, ticktext=_MONTH_TICKTEXT),
        yaxis=dict(title="Gas Use (kWh/day)", range=[0, 250]),
    )
    return fig


def _heating_year_xaxis():
    """Shared x-axis config for heating-year charts (Jul=day 1)."""
    return dict(
        range=[1, 365],
        tickvals=[1, 32, 63, 93, 124, 154, 185, 216, 244, 275, 305, 336],
        ticktext=["Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun"],
    )


def _current_heating_label(daily: pd.DataFrame) -> str:
    """Label of the current (in-progress) heating year."""
    last = daily.index[-1]
    yr = last.year if last.month >= 7 else last.year - 1
    return f"{yr}/{str(yr + 1)[-2:]}"


def chart_cumulative_gas_by_year(daily: pd.DataFrame, year_groups: dict) -> go.Figure:
    fig = go.Figure()
    current_label = _current_heating_label(daily)
    for i, (label, ydf) in enumerate(sorted(year_groups.items())):
        is_current = (label == current_label)
        fig.add_trace(go.Scatter(
            x=ydf["doy"], y=ydf["cum_gas_kwh"],
            name=label,
            line=dict(color=_yr_colour(i), width=2.5 if is_current else 1),
            opacity=1.0 if is_current else 0.6,
        ))
    fig.update_layout(
        title="Cumulative Gas Use by Heating Year (Jul → Jun)",
        xaxis=_heating_year_xaxis(),
        yaxis=dict(title="Cumulative Gas (kWh)"),
    )
    return fig


def chart_cumulative_degree_days(daily: pd.DataFrame, year_groups: dict) -> go.Figure:
    fig = go.Figure()
    current_label = _current_heating_label(daily)
    for i, (label, ydf) in enumerate(sorted(year_groups.items())):
        if label == "2015/16":
            continue
        is_current = (label == current_label)
        fig.add_trace(go.Scatter(
            x=ydf["doy"], y=ydf["cum_dd"],
            name=label,
            line=dict(color=_yr_colour(i), width=2.5 if is_current else 1),
            opacity=1.0 if is_current else 0.6,
        ))
    fig.update_layout(
        title="Cumulative Heating Degree Days by Heating Year (Jul → Jun)",
        xaxis=_heating_year_xaxis(),
        yaxis=dict(title="Cumulative Degree Days"),
    )
    return fig



def chart_efficiency_trend(daily: pd.DataFrame) -> go.Figure:
    """Long-term heating efficiency: rolling annual and 3-year gas kWh per degree-day."""
    fig = go.Figure()

    gas = daily["use_gas_kwh"].fillna(0)
    dd  = daily["degree_days"].fillna(0)

    eff1 = (gas.rolling(365, min_periods=180).sum() /
            dd.rolling(365, min_periods=180).sum().replace(0, np.nan)).dropna()
    eff3 = (gas.rolling(3 * 365, min_periods=365).sum() /
            dd.rolling(3 * 365, min_periods=365).sum().replace(0, np.nan)).dropna()

    fig.add_trace(go.Scatter(x=eff1.index, y=eff1.values,
                             name="1-year mean", line=dict(color=_PALETTE[0], width=1.5)))
    fig.add_trace(go.Scatter(x=eff3.index, y=eff3.values,
                             name="3-year mean", line=dict(color=_PALETTE[1], width=2.5)))

    fig.update_layout(
        title="Heating Efficiency Development (kWh gas per degree-day)",
        xaxis_title="Date",
        yaxis_title="kWh / °C·day",
        yaxis=dict(range=[0, 20]),
    )
    return fig


def chart_annual_cost(daily: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    gas_cost  = daily["cost_gas_annual"].dropna() / 1000   # kEUR
    elec_cost = daily["cost_elec_annual"].dropna() / 1000
    total     = daily["cost_total_annual"].dropna() / 1000

    fig.add_trace(go.Scatter(x=total.index, y=total.values,
                             name="Total", line=dict(color=_PALETTE[0], width=2)))
    fig.add_trace(go.Scatter(x=gas_cost.index, y=gas_cost.values,
                             name="Gas", line=dict(color=_PALETTE[3], width=1.5, dash="dot")))
    fig.add_trace(go.Scatter(x=elec_cost.index, y=elec_cost.values,
                             name="Electricity", line=dict(color=_PALETTE[2], width=1.5, dash="dot")))

    fig.update_layout(title="Annual Energy Cost (rolling)",
                      xaxis_title="Date", yaxis_title="EUR thousands / year",
                      yaxis=dict(range=[0, 6]))
    return fig


def chart_weather_sources(lmu: pd.DataFrame, dwd: pd.DataFrame | None) -> go.Figure:
    """Compare LMU Garching vs DWD München-Flughafen: sunshine and temperature."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=("Annual sunshine (rolling sum, hours/year)",
                                        "Annual mean temperature (rolling avg, °C)"))
    roll = dict(window=365, min_periods=180)

    def add_pair(series_lmu, series_dwd, row, name_lmu, name_dwd, col_lmu, col_dwd):
        s = series_lmu.rolling(**roll)
        agg = s.sum() if row == 1 else s.mean()
        agg = agg.dropna()
        if len(agg):
            fig.add_trace(go.Scatter(x=agg.index, y=agg.values, name=name_lmu,
                                     line=dict(color=col_lmu, width=1.5)), row=row, col=1)
        if series_dwd is not None:
            s2 = series_dwd.rolling(**roll)
            agg2 = s2.sum() if row == 1 else s2.mean()
            agg2 = agg2.dropna()
            if len(agg2):
                fig.add_trace(go.Scatter(x=agg2.index, y=agg2.values, name=name_dwd,
                                         line=dict(color=col_dwd, width=1.5, dash="dot")),
                              row=row, col=1)

    dwd_sun  = dwd["sunshine"] if dwd is not None else None
    dwd_tmit = dwd["tmit"]     if dwd is not None else None

    add_pair(lmu["sunshine"], dwd_sun,  1,
             "Sunshine LMU Garching", "Sunshine DWD Airport",
             _PALETTE[0], _PALETTE[1])
    add_pair(lmu["tmit"],     dwd_tmit, 2,
             "Temp LMU Garching",     "Temp DWD Airport",
             _PALETTE[2], _PALETTE[3])

    fig.update_layout(title="Weather Source Comparison: LMU Garching vs DWD München-Flughafen",
                      height=500)
    return fig


def chart_weather(daily: pd.DataFrame) -> go.Figure:
    movar = 365
    fig = make_subplots(rows=2, cols=3, shared_xaxes=False,
                        subplot_titles=(
                            "Temperatures (annual avg)",
                            "Sunny & Rainy Days",
                            "Annual Rainfall & Sunshine",
                            "Warm / Hot Days",
                            "Frost & Ice Days",
                            "Sunshine:Rain Ratio",
                        ))

    # Compute sunny-day potential
    doy_arr  = daily.index.dayofyear.to_numpy()
    sun_pot  = 6.5 + 3.5 * np.sin(-np.pi / 2 - 3 * np.pi / 32 + doy_arr * 2 * np.pi / 365)
    sunny    = daily["sunshine"] > sun_pot

    roll_kw  = dict(window=movar, min_periods=movar // 2)

    def roll(s): return s.rolling(**roll_kw).mean()

    # Row 1 col 1 — temperatures
    for col, name, clr in [("tmin","Min","#64B5CD"),("tmit","Mean","#55A868"),("tmax","Max","#C44E52")]:
        s = roll(daily[col]).dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name,
                                 line=dict(color=clr), showlegend=True), row=1, col=1)

    # Row 1 col 2 — sunny & heavy rain days
    for series, name, clr in [(sunny.astype(float), "Sunny", "#CCB974"),
                               ((daily["rain"] > 5).astype(float), "Hvy Rain", "#4C72B0")]:
        s = (roll(series) * movar).dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name,
                                 line=dict(color=clr), showlegend=True), row=1, col=2)

    # Row 1 col 3 — annual rainfall & sunshine
    for series, name, clr in [(daily["rain"], "Rainfall mm", "#4C72B0"),
                               (daily["sunshine"], "Sunshine hrs", "#CCB974")]:
        s = (roll(series) * movar).dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name,
                                 line=dict(color=clr), showlegend=True), row=1, col=3)

    # Row 2 col 1 — warm/hot days
    for thr, name, clr in [(20, ">20°C", "#64B5CD"), (25, ">25°C", "#55A868"), (30, ">30°C", "#C44E52")]:
        s = (roll((daily["tmax"] >= thr).astype(float)) * movar).dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name,
                                 line=dict(color=clr), showlegend=True), row=2, col=1)

    # Row 2 col 2 — frost & ice
    for series, name, clr in [((daily["tmin"] <= 0).astype(float), "Frost days", "#64B5CD"),
                               ((daily["tmax"] <= 0).astype(float), "Ice days",   "#4C72B0")]:
        s = (roll(series) * movar).dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name,
                                 line=dict(color=clr), showlegend=True), row=2, col=2)

    # Row 2 col 3 — sunshine:rain ratio
    rain_r = roll(daily["rain"]).replace(0, np.nan)
    ratio  = (roll(daily["sunshine"]) / rain_r).dropna()
    fig.add_trace(go.Scatter(x=ratio.index, y=ratio.values, name="Sun:Rain",
                             line=dict(color=_PALETTE[4]), showlegend=True), row=2, col=3)

    fig.update_layout(title="Weather Overview (rolling annual)", height=600)
    return fig


def chart_gas_degree_day_scatter(daily: pd.DataFrame, year_groups: dict) -> go.Figure:
    """Cumulative gas energy vs cumulative degree days, year-by-year."""
    fig = go.Figure()
    for i, (yr, ydf) in enumerate(sorted(year_groups.items())):
        is_current = (yr == daily.index[-1].year)
        fig.add_trace(go.Scatter(
            x=ydf["cum_dd"],
            y=ydf["cum_gas_kwh"],
            name=str(yr),
            mode="lines",
            line=dict(color=_yr_colour(i), width=2.5 if is_current else 1),
            opacity=1.0 if is_current else 0.65,
        ))
    fig.update_layout(title="Cumulative Gas Energy vs. Cumulative Degree Days",
                      xaxis_title="Cumulative Degree Days (°C·days)",
                      yaxis_title="Cumulative Gas Energy (kWh)")
    return fig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _add_season_vlines(fig: go.Figure):
    """Add Northern Hemisphere calendar season boundary lines."""
    boundaries = [
        (79,  "Spring"),
        (172, "Summer"),
        (266, "Autumn"),
        (355, "Winter"),
    ]
    for day, _ in boundaries:
        fig.add_vline(x=day, line_dash="dot", line_color="#45475a", line_width=1)


# ── Entry point ───────────────────────────────────────────────────────────────

def build_all(data: dict) -> list[dict]:
    daily       = data["daily"]
    hot_water   = data["hot_water_kwh"]
    year_groups = data["year_groups"]
    median_doy  = data["median_gas_doy"]
    recent_doy  = data["recent_gas_doy"]
    weather_lmu = data.get("weather_lmu")
    weather_dwd = data.get("weather_dwd")

    charts = [
        ("Annual Gas Use",          "chart_gas",      chart_annual_gas(daily, hot_water)),
        ("Electricity Use",         "chart_elec",     chart_electricity(daily)),
        ("Water Use",               "chart_water",    chart_water(daily)),
        ("Gas vs Temperature",      "chart_gas_temp", chart_gas_vs_temp(daily, hot_water)),
        ("Gas by Day-of-Year",      "chart_doy",      chart_gas_day_of_year(daily, median_doy, recent_doy)),
        ("Cumulative Gas by Year",  "chart_cum_gas",  chart_cumulative_gas_by_year(daily, year_groups)),
        ("Cumulative Degree Days",  "chart_cum_dd",   chart_cumulative_degree_days(daily, year_groups)),
        ("Efficiency Development",  "chart_eff_trend",chart_efficiency_trend(daily)),
        ("Annual Energy Cost",      "chart_cost",     chart_annual_cost(daily)),
        ("Gas Energy vs Degree Days", "chart_gdd",    chart_gas_degree_day_scatter(daily, year_groups)),
        ("Weather Overview",        "chart_weather",  chart_weather(daily)),
        ("Weather Sources",         "chart_weather_sources",
                                    chart_weather_sources(weather_lmu, weather_dwd)),
    ]

    result = []
    for title, chart_id, fig in charts:
        fig.update_layout(
            paper_bgcolor="#1e1e2e",
            plot_bgcolor="#1e1e2e",
            font=dict(color="#cdd6f4", size=12),
            legend=dict(bgcolor="rgba(0,0,0,0.3)", bordercolor="#45475a"),
            margin=dict(l=55, r=20, t=50, b=50),
        )
        # Apply dark grid to all axes
        for axis in ["xaxis", "yaxis", "xaxis2", "yaxis2"]:
            if hasattr(fig.layout, axis):
                getattr(fig.layout, axis).update(gridcolor="#313244", zerolinecolor="#45475a")
        result.append({"title": title, "id": chart_id, "json": fig.to_json()})

    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from process import run
    data = run()
    charts = build_all(data)
    print(f"Generated {len(charts)} charts")
    for c in charts:
        print(f"  {c['id']}: {len(c['json'])} bytes")
