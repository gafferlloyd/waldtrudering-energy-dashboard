"""Scrape daily weather data from the LMU Garching meteorological station.

Source: https://www.meteo.physik.uni-muenchen.de/~quicklooks/klimadaten/garching/{yyyy}/{mm}{yyyy}_h.html
Resolution: hourly-based daily aggregates (T_max, T_mean, T_min, rain, sunshine)

Output CSV columns match weather_archive.csv: date,tmax,tmit,tmin,rain,sunshine
"""
import sys
import csv
import argparse
from datetime import date, datetime
from html.parser import HTMLParser
import urllib.request
from pathlib import Path

BASE_URL = "https://www.meteo.physik.uni-muenchen.de/~quicklooks/klimadaten/garching"


class _TableParser(HTMLParser):
    """Extract cell data from the first <table> in an HTML page."""

    def __init__(self):
        super().__init__()
        self._in_table = False
        self._in_cell = False
        self._cell = ""
        self._row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = True
            self._cell = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
        elif tag in ("td", "th") and self._in_cell:
            self._row.append(self._cell.strip())
            self._in_cell = False
        elif tag == "tr" and self._in_table:
            if self._row:
                self.rows.append(self._row[:])
            self._row = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell += data


def _fetch_month(year: int, month: int) -> list[dict]:
    mm = f"{month:02d}"
    yyyy = str(year)
    url = f"{BASE_URL}/{yyyy}/{mm}{yyyy}_h.html"

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"WARNING: could not fetch {yyyy}-{mm}: {exc}", file=sys.stderr)
        return []

    parser = _TableParser()
    parser.feed(html)

    records = []
    for row in parser.rows:
        # Need at least 10 columns; skip header rows (date cell starts with a digit)
        if len(row) < 10 or not row[0][:1].isdigit():
            continue
        try:
            day, mon, yr = row[0].split(".")
            records.append({
                "date":     f"{yr}-{mon}-{day}",
                "tmax":     row[1],   # T_max
                "tmit":     row[2],   # T_mean
                "tmin":     row[3],   # T_min
                "rain":     row[4],   # Regen
                "sunshine": row[9],   # Sonne (cols 5-8 are pressure/wind)
            })
        except (ValueError, IndexError):
            continue
    return records


def scrape(from_ym: str = "2015-09", to_ym: str | None = None) -> list[dict]:
    """Return all daily rows from *from_ym* through *to_ym* (inclusive, YYYY-MM)."""
    start = datetime.strptime(from_ym, "%Y-%m").date()
    end = (datetime.strptime(to_ym, "%Y-%m").date()
           if to_ym else date.today().replace(day=1))

    all_rows: list[dict] = []
    current = start
    while current <= end:
        print(f"  {current.year}-{current.month:02d} … ", file=sys.stderr, end="", flush=True)
        rows = _fetch_month(current.year, current.month)
        all_rows.extend(rows)
        print(f"{len(rows)} rows", file=sys.stderr)
        current = (current.replace(year=current.year + 1, month=1)
                   if current.month == 12
                   else current.replace(month=current.month + 1))
    return all_rows


def main():
    ap = argparse.ArgumentParser(
        description="Scrape LMU Garching hourly-based weather data to CSV."
    )
    ap.add_argument("--from", dest="from_ym", default="2015-09",
                    metavar="YYYY-MM", help="First month to fetch (default: 2015-09)")
    ap.add_argument("--to", dest="to_ym", default=None,
                    metavar="YYYY-MM", help="Last month to fetch (default: current month)")
    ap.add_argument("-o", "--output", default=None,
                    help="Output CSV file path (default: stdout)")
    args = ap.parse_args()

    rows = scrape(args.from_ym, args.to_ym)

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    try:
        writer = csv.DictWriter(out, fieldnames=["date", "tmax", "tmit", "tmin", "rain", "sunshine"])
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.output:
            out.close()

    print(f"Done — {len(rows)} rows written.", file=sys.stderr)


if __name__ == "__main__":
    main()
