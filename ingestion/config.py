"""What we ingest and where it lands."""

from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

TOULOUSE_API = "https://data.toulouse-metropole.fr/api/explore/v2.1/catalog/datasets"
STATIONS_DATASET = "stations-meteo-en-place"

# Stations still reporting as of 2026-11-19 (11 and 58 are intermittent).
# The other ~40 station datasets stopped between 2020 and 2026.
LIVE_STATIONS = {
    2: "02-station-meteo-toulouse-marengo",
    11: "11-station-meteo-toulouse-soupetard",
    12: "12-station-meteo-toulouse-montaudran",
    13: "13-station-meteo-toulouse-pech-david",
    23: "23-station-meteo-pibrac-bouconne-centre-equestre",
    33: "33-station-meteo-saint-jory-chapelle-beldou",
    37: "37-station-meteo-toulouse-universite-paul-sabatier",
    42: "42-station-meteo-toulouse-parc-compans-cafarelli",
    58: "58-station-meteo-toulouse-fondeyre",
    62: "62-station-meteo-toulouse-parc-maourine",
}

OPEN_METEO_PREVIOUS_RUNS_API = "https://previous-runs-api.open-meteo.com/v1/forecast"

# Lead 0 is the most recent forecast for that hour; lead N is the forecast
# issued N days earlier. The API keeps at most 7 previous days.
LEAD_DAYS = range(0, 8)
