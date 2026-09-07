"""Fetch Open-Meteo historical solar irradiance (ERA5 reanalysis, actual
observed conditions -- not a forecast) for the site's full array-specific
theoretical PV model, replacing the horizontal-surface Angstrom-Prescott
approach process.py used previously (see the removed comment there for
why: it deliberately avoided a proper tilted plane-of-array model because
goodwe_solar's own irradiance model was, at the time, only ~7 weeks deep --
Open-Meteo's archive covers back to 1940, so that limitation no longer
applies).

Backtested in goodwe_solar/forecast_client.py (same underlying API, same
conversion) against 68 real days of this array's own measured generation,
2026-09-07: MAE 5.4 kWh/day, 109.8% of actual cumulative -- clearly beats
the Angstrom approach's MAE 9.6 / 117.8%.

A single API call covers the whole site history in well under a second
(confirmed live) -- no historical/recent split or chunking needed, unlike
fetch_dwd_weather.py's ZIP-based approach.

Writes cache/openmeteo_historical.csv: date,gti_kwh_m2,sunshine_h
  gti_kwh_m2: daily total global tilted irradiance (already projected onto
              this array's real tilt/azimuth by Open-Meteo itself), for
              process.py's theoretical-PV formula.
  sunshine_h: daily sunshine duration, used ONLY as the third-tier fallback
              in load_dwd_weather()'s DWD->LMU->Open-Meteo backfill chain
              (deliberately not the primary sunshine source -- DWD/LMU are
              real ground measurements, this is reanalysis-modeled; see
              process.py for the fallback logic and the reasoning).

AZIMUTH CONVENTION WARNING: Open-Meteo uses 0=South, -90=East, +90=West,
+-180=North -- NOT this project's compass bearing (0=N/90=E/180=S/270=W,
see goodwe_solar/solar_position.py). Convert with `PANEL_AZIMUTH_DEG -
180`, not PANEL_AZIMUTH_DEG directly. For this array (135 deg compass,
SE): 135 - 180 = -45, which correctly lands between East(-90) and
South(0). Getting this sign wrong would silently mirror the array
(model it as SW), shifting morning/afternoon yield estimates without any
obvious error -- verified against the live API before this was trusted,
same check already done once building goodwe_solar/forecast_client.py.
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Copied from goodwe_solar/solar_position.py, not imported -- these two
# projects deliberately don't cross-import (CI has no filesystem access to
# goodwe_solar at all; this file only works cross-project because it's an
# outbound HTTPS call, unlike goodwe_solar's local DB). Keep in sync
# manually if the array ever changes.
SITE_LAT = 48.1157
SITE_LON = 11.6848
PANEL_TILT_DEG = 40.0
PANEL_AZIMUTH_DEG = 135.0  # compass bearing, 0=N/90=E/180=S/270=W -- SE
PANEL_WIDTH_M = 1.134
PANEL_HEIGHT_M = 1.762
PANEL_COUNT = 23
PANEL_EFFICIENCY = 0.24
ARRAY_AREA_M2 = PANEL_WIDTH_M * PANEL_HEIGHT_M * PANEL_COUNT  # ~45.9 m^2

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OM_AZIMUTH = PANEL_AZIMUTH_DEG - 180  # see AZIMUTH CONVENTION WARNING above
START_DATE = "2016-08-23"  # matches weather_archive.csv's earliest date


def fetch(cache_dir: Path) -> bool:
    """Download, aggregate to daily, and write cache/openmeteo_historical.csv.
    Returns True on success."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / "openmeteo_historical.csv"

    end_date = date.today().isoformat()
    params = {
        "latitude": SITE_LAT,
        "longitude": SITE_LON,
        "hourly": "global_tilted_irradiance,sunshine_duration",
        "tilt": PANEL_TILT_DEG,
        "azimuth": OM_AZIMUTH,
        "start_date": START_DATE,
        "end_date": end_date,
        "timezone": "Europe/Berlin",
    }
    url = ARCHIVE_URL + "?" + urllib.parse.urlencode(params)

    print(f"  Open-Meteo historical ({START_DATE} to {end_date})… ", end="", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = json.loads(resp.read())
    except Exception as exc:
        print(f"WARNING: {exc}", file=sys.stderr)
        return False

    if "hourly" not in raw:
        print(f"WARNING: unexpected response shape: {raw}", file=sys.stderr)
        return False

    hourly = raw["hourly"]
    times = hourly["time"]
    gti = hourly["global_tilted_irradiance"]
    sun_s = hourly["sunshine_duration"]

    by_day_gti_wh_m2 = defaultdict(float)
    by_day_sun_s = defaultdict(float)
    for t, g, s in zip(times, gti, sun_s):
        d = t[:10]
        if g is not None:
            by_day_gti_wh_m2[d] += g  # W/m2 * 1h = Wh/m2 per hourly sample
        if s is not None:
            by_day_sun_s[d] += s

    rows = []
    for d in sorted(by_day_gti_wh_m2.keys()):
        rows.append({
            "date": d,
            "gti_kwh_m2": round(by_day_gti_wh_m2[d] / 1000.0, 3),
            "sunshine_h": round(by_day_sun_s[d] / 3600.0, 2),
        })

    if len(rows) < 3000:  # ~8+ years expected; catch a truncated/failed fetch
        print(f"WARNING: only {len(rows)} days fetched, expected 3000+. "
              f"Fetch may have failed silently.", file=sys.stderr)

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "gti_kwh_m2", "sunshine_h"])
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} rows → {out_path.name}")
    return True


if __name__ == "__main__":
    fetch(ROOT / "cache")
