"""Archived temperature forecasts from the Open-Meteo Previous Runs API."""

import time
from datetime import date, timedelta

import requests

from .config import LEAD_DAYS, OPEN_METEO_PREVIOUS_RUNS_API


def _variable(lead_days: int) -> str:
    if lead_days == 0:
        return "temperature_2m"
    return f"temperature_2m_previous_day{lead_days}"


# Days per API request. A long window is fetched in several requests.
CHUNK_DAYS = 31


def fetch_forecasts(stations: list[dict], start: date, end: date) -> list[dict]:
    """Hourly forecasts at each station's coordinates, one row per lead time.

    For every hour between start and end (inclusive, UTC) the API returns the
    temperature that was forecast 0 to 7 days ahead of that hour.
    """
    rows = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        rows += _fetch_chunk(stations, chunk_start, chunk_end)
        chunk_start = chunk_end + timedelta(days=1)
        if chunk_start <= end:
            time.sleep(2)  # stay well under the API's per-minute limit
    return rows


def _fetch_chunk(stations: list[dict], start: date, end: date) -> list[dict]:
    response = requests.get(
        OPEN_METEO_PREVIOUS_RUNS_API,
        params={
            "latitude": ",".join(str(s["latitude"]) for s in stations),
            "longitude": ",".join(str(s["longitude"]) for s in stations),
            "hourly": ",".join(_variable(lead) for lead in LEAD_DAYS),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "UTC",
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    # The API returns a bare object instead of a list for a single location.
    locations = payload if isinstance(payload, list) else [payload]

    rows = []
    for station, location in zip(stations, locations, strict=True):
        hourly = location["hourly"]
        for lead in LEAD_DAYS:
            for valid_at, temperature in zip(hourly["time"], hourly[_variable(lead)]):
                if temperature is None:
                    continue
                rows.append(
                    {
                        "station_id": station["station_id"],
                        "valid_at": f"{valid_at}:00+00:00",
                        "lead_days": lead,
                        "temperature_c": temperature,
                        "grid_latitude": location["latitude"],
                        "grid_longitude": location["longitude"],
                        "grid_elevation_m": location["elevation"],
                    }
                )
    return rows
