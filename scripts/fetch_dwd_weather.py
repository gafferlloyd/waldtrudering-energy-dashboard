"""Fetch DWD CDC daily climate observations for München-Flughafen (station 01262).

Downloads historical (1992–2024) and recent (rolling ~500 days) ZIPs, merges
them, and writes cache/dwd_weather.csv with the same column layout as
weather_archive.csv: date,tmax,tmit,tmin,rain,sunshine

Missing / invalid values (-999) become empty (NaN when read back).
"""
import csv
import io
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
import urllib.request

ROOT = Path(__file__).parent.parent
STATION = "01262"
_BASE = ("https://opendata.dwd.de/climate_environment/CDC"
         "/observations_germany/climate/daily/kl")
RECENT_URL = f"{_BASE}/recent/tageswerte_KL_{STATION}_akt.zip"


def _discover_hist_url(station: str) -> str:
    """DWD reissues the historical ZIP annually with a rolled-forward end
    date baked into the filename (e.g. ..._20241231_hist.zip becomes
    ..._20251231_hist.zip). Discover the current name instead of hardcoding
    it, since a stale filename 404s silently and truncates the record."""
    index_url = f"{_BASE}/historical/"
    with urllib.request.urlopen(index_url, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    pattern = rf"tageswerte_KL_{station}_\d{{8}}_\d{{8}}_hist\.zip"
    match = re.search(pattern, html)
    if not match:
        raise RuntimeError(f"Could not find historical zip for station {station} in {index_url}")
    return f"{_BASE}/historical/{match.group(0)}"

# DWD column → our column
_MAP = {"TXK": "tmax", "TMK": "tmit", "TNK": "tmin", "RSK": "rain", "SDK": "sunshine"}


def _parse_zip(data: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = next(n for n in zf.namelist() if n.startswith("produkt_klima_tag_"))
        text = zf.read(name).decode("latin-1")

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = []
    for raw in reader:
        rec = {k.strip(): v.strip() for k, v in raw.items()}
        ds = rec.get("MESS_DATUM", "").strip()
        if len(ds) != 8 or not ds.isdigit():
            continue
        try:
            d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        except ValueError:
            continue
        out = {"date": d.isoformat()}
        for dwd_col, col in _MAP.items():
            raw_val = rec.get(dwd_col, "").strip()
            try:
                f = float(raw_val)
                out[col] = "" if f <= -990 else f"{f:.1f}"
            except ValueError:
                out[col] = ""
        rows.append(out)
    return rows


def fetch(cache_dir: Path) -> bool:
    """Download, parse and write cache/dwd_weather.csv.  Returns True on success."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / "dwd_weather.csv"

    try:
        hist_url = _discover_hist_url(STATION)
    except Exception as exc:
        print(f"WARNING: historical URL discovery failed: {exc}", file=sys.stderr)
        hist_url = None

    all_rows: list[dict] = []
    sources = ([(hist_url, "historical")] if hist_url else []) + [(RECENT_URL, "recent")]
    for url, label in sources:
        print(f"  DWD {label}… ", end="", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            rows = _parse_zip(data)
            all_rows.extend(rows)
            print(f"{len(rows)} rows")
        except Exception as exc:
            print(f"WARNING: {exc}", file=sys.stderr)

    if not all_rows:
        print("  DWD: no data fetched", file=sys.stderr)
        return False

    # Sanity check: full history should be ~30 years (~11000+ rows). If we
    # only got the recent file, fail loudly rather than silently truncating
    # the record (this previously broke the weather chart for weeks).
    if hist_url and len(all_rows) < 5000:
        print(f"  DWD: WARNING — only {len(all_rows)} rows fetched, expected 10000+. "
              f"Historical fetch may have failed silently.", file=sys.stderr)

    # Deduplicate — recent file overlaps hist; keep recent's values
    merged: dict[str, dict] = {}
    for row in all_rows:
        merged[row["date"]] = row

    sorted_rows = sorted(merged.values(), key=lambda r: r["date"])

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "tmax", "tmit", "tmin", "rain", "sunshine"])
        w.writeheader()
        w.writerows(sorted_rows)

    print(f"  DWD: {len(sorted_rows)} total rows → {out_path.name}")
    return True


if __name__ == "__main__":
    fetch(ROOT / "cache")
