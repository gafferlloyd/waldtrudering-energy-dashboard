"""Download fresh data from Google Sheets into the cache directory."""
import sys
from pathlib import Path
import urllib.request
import yaml

ROOT = Path(__file__).parent.parent


def load_config():
    with open(ROOT / "config" / "settings.yaml") as f:
        return yaml.safe_load(f)


def fetch(url: str, dest: Path, label: str) -> bool:
    try:
        print(f"  Fetching {label}…", end=" ", flush=True)
        urllib.request.urlretrieve(url, dest)
        size = dest.stat().st_size
        print(f"OK ({size:,} bytes)")
        return True
    except Exception as exc:
        print(f"FAILED: {exc}")
        return False


def main(cache_dir: Path) -> bool:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    ok_meter = fetch(cfg["meter_sheet_url"], cache_dir / "meterreadings.csv", "meter readings")
    ok_weather = fetch(cfg["weather_sheet_url"], cache_dir / "weatherdata.csv", "weather data")
    return ok_meter and ok_weather


if __name__ == "__main__":
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cache"
    success = main(cache)
    sys.exit(0 if success else 1)
