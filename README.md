# Garching Energy Dashboard

Interactive web dashboard tracking gas, electricity, and water consumption for a house in Garching, Germany. Automatically updated daily via GitHub Actions and published to GitHub Pages.

## What it does

- Reads cumulative meter readings from a Google Sheet (entered manually)
- Reads daily weather observations from the LMU Munich Google Sheet
- Calculates daily consumption, heating degree days, efficiency metrics, and annual costs
- Generates 11 interactive Plotly charts covering ~10 years of data
- Deploys to GitHub Pages as a single self-contained HTML file

## Charts

1. Annual Gas Use (rolling 12-month and 3-year)
2. Electricity Use (7-day MA + rolling annual)
3. Water Use
4. Gas Use vs. Minimum Temperature (scatter)
5. Gas Use by Day-of-Year (seasonal profile)
6. Cumulative Gas Use by Year
7. Cumulative Heating Degree Days by Year
8. Heating Efficiency (kWh per degree day)
9. Annual Energy Cost in EUR
10. Weather Overview (6-panel: temps, sunshine, rain, warm/cold days)
11. Cumulative Gas Energy vs. Degree Days (year-by-year)

## Data sources

| Source | How updated |
|--------|-------------|
| Meter readings (gas, electricity, water) | Manual entry in Google Sheets |
| Weather (Garching / LMU Munich) | Manual entry in Google Sheets |

## Local development

```bash
pip install -r requirements.txt
python scripts/build.py          # fetch + build
python scripts/build.py --skip-fetch  # use cached data
# output/index.html is the dashboard
```

## Configuration

Edit `config/settings.yaml` to update:
- Floor area (m²)
- Base temperature and degree-day offset
- **Energy prices per year** — add a new entry each time prices change

```yaml
energy_prices:
  - year: 2024
    gas: 0.18    # EUR/kWh
    elec: 0.50   # EUR/kWh
  - year: 2025
    gas: 0.17
    elec: 0.48
```

## GitHub Pages setup

1. Push this repository to GitHub
2. Go to Settings → Pages → Source: **Deploy from a branch** → `gh-pages`
3. The Actions workflow runs automatically at 05:30 UTC every day
4. Trigger a manual rebuild from the Actions tab at any time

## Historical data

Archived meter readings (2008–2021) are in `data/meter_archive.csv`.  
Archived weather data (2015–2026) is in `data/weather_archive.csv`.  
Both were migrated from the original MATLAB/Octave project.
