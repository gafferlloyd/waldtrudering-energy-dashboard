"""Export ICE (internal combustion engine) fuel-log spreadsheets to a
committed CSV snapshot.

Source spreadsheets live outside this repo (/home/gareth/Documents/solaranlage/ice/),
covering three vehicles in sequence: Honda CR-V (2016-2021), Volvo XC60
(2021-2024), Volvo V60CC (2024-present). Only (date, cost) pairs are pulled --
the litres/consumption-rate columns exist for MPG tracking, not needed here.

GitHub Actions CI can't reach that path, so this snapshot is committed to
data/ice_fuel_archive.csv and re-read by process.py::load_ice_fuel_snapshot().
Run this manually whenever a new fill-up is logged (fuel spend is episodic,
not daily, so this doesn't need a systemd timer like the goodwe snapshot).

This is a placeholder data source (see process.py's fuel_annual_eur) staged
separately from the main annual-cost chart until the EV comes online in
spring, at which point ICE fuel cost folds into the primary chart instead.
"""
from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).parent.parent
ICE_DIR = Path("/home/gareth/Documents/solaranlage/ice")

# (file, sheet, date_col, cost_col) -- 0-indexed columns confirmed by direct
# inspection 2026-09-17. Honda's cost is "total price" (col 4); the two
# Volvo sheets share a layout with cost in col 3.
SOURCES = [
    (ICE_DIR / "honda_gas_miles.xlsx", "fuel_km", 0, 4),
    (ICE_DIR / "Volvo_costing_XC60.xlsx", "Sheet1", 0, 3),
    (ICE_DIR / "Volvo_costing_V60CC.xlsx", "Sheet1", 0, 3),
]

# Known transposed-cell error in the V60CC source: 2025-06-21's litres/cost
# were entered swapped (24.4/42.2 instead of 42.2/24.4). Confirmed still
# present in the raw file 2026-09-17 -- corrected here rather than in the
# source, which isn't ours to edit.
V60CC_CORRECTIONS = {
    pd.Timestamp("2025-06-21"): 42.2,
}


# The V60CC log is now also kept in Google Sheets (link-viewable; the local
# xlsx went stale after 2026-09-17). New fills are picked up from there. 2026-11-02.
V60CC_SHEET_CSV = ("https://docs.google.com/spreadsheets/d/"
                   "1R7orjdWxxtr5dMRc0O5SiNYR-D659DLoxGI79NmIBxQ/export?format=csv")


def extract_sheet_csv(url: str, date_col: int, cost_col: int) -> list[tuple[pd.Timestamp, float]]:
    import csv, io, urllib.request
    text = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    rows = []
    for row in list(csv.reader(io.StringIO(text)))[1:]:
        try:
            d = pd.to_datetime(row[date_col], format="%d-%b-%Y")
            c = float(row[cost_col])
        except (ValueError, IndexError):
            continue
        rows.append((pd.Timestamp(d.date()), c))
    return rows


def extract(path: Path, sheet: str, date_col: int, cost_col: int) -> list[tuple[pd.Timestamp, float]]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        d, c = row[date_col], row[cost_col]
        if d is None or not hasattr(d, "year") or c is None:
            continue
        rows.append((pd.Timestamp(d.date()), float(c)))
    wb.close()
    return rows


def main():
    all_rows = []
    for path, sheet, date_col, cost_col in SOURCES:
        rows = extract(path, sheet, date_col, cost_col) if path.exists() else []
        if path.name == "Volvo_costing_V60CC.xlsx":
            # Local xlsx = history; Google Sheet only contributes fills NEWER than
            # the local file's last row. (The sheet copy lacks 5 fills the local
            # file has -- 2024-03-31, 2024-04-05, 2025-02-08, 2025-03-15,
            # 2025-12-27, ~EUR 383 -- so it must not replace the local history.)
            try:
                last = max((d for d, _ in rows), default=pd.Timestamp.min)
                new = [(d, c) for d, c in extract_sheet_csv(V60CC_SHEET_CSV, date_col, cost_col) if d > last]
                rows = rows + new
                print(f"  V60CC: +{len(new)} fill(s) newer than {last.date()} from Google Sheet")
            except Exception as e:
                print(f"  V60CC: Google Sheet unavailable ({e}); local xlsx only")
        if not rows:
            print(f"  Skipping (no data): {path}")
            continue
        if path.name == "Volvo_costing_V60CC.xlsx":
            rows = [(d, V60CC_CORRECTIONS.get(d, c)) for d, c in rows]
        print(f"  {path.name}: {len(rows)} fill-ups, "
              f"{rows[0][0].date()} to {rows[-1][0].date()}, "
              f"total EUR {sum(c for _, c in rows):,.2f}")
        all_rows.extend(rows)

    if not all_rows:
        print("No ICE fuel data found -- nothing to export")
        return

    df = pd.DataFrame(all_rows, columns=["date", "cost_eur"])
    # Multiple fills on the same day (rare) sum together.
    df = df.groupby("date", as_index=False)["cost_eur"].sum().sort_values("date")

    out_path = ROOT / "data" / "ice_fuel_archive.csv"
    df.to_csv(out_path, index=False)
    print(f"Exported {len(df)} rows to {out_path}")


if __name__ == "__main__":
    main()
