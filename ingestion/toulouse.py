"""Observations and station reference data from data.toulouse-metropole.fr."""

from datetime import date, timedelta

import requests

from .config import LIVE_STATIONS, STATIONS_DATASET, TOULOUSE_API


def fetch_stations() -> list[dict]:
    """Coordinates and metadata for the live stations."""
    response = requests.get(
        f"{TOULOUSE_API}/{STATIONS_DATASET}/exports/json", timeout=60
    )
    response.raise_for_status()
    return [
        {
            "station_id": row["id_numero"],
            "dataset_id": LIVE_STATIONS[row["id_numero"]],
            "city": row["ville"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "altitude_m": row["altitude"],
            "station_type": row["type_stati"],
            "local_climate_zone": row["lcz"],
        }
        for row in response.json()
        # Some ids appear several times: the transmitting station ("V") plus
        # non-transmitting sub-sensors ("N") a few hundred metres away.
        if row["id_numero"] in LIVE_STATIONS and row["emission"] == "V"
    ]


def fetch_observations(station_id: int, start: date, end: date) -> list[dict]:
    """Raw 15-minute temperature records for one station, start and end inclusive (UTC).

    Rows are returned as published: readings of exactly -50 °C (flagged as
    invalid during data profiling) and gaps are left for the transformation
    layer to handle.
    """
    # heure_utc is typed as text on most datasets, so we filter on
    # heure_de_paris (a real datetime) and ask the API to render it in UTC.
    where = (
        f"heure_de_paris >= date'{start.isoformat()}' "
        f"and heure_de_paris < date'{(end + timedelta(days=1)).isoformat()}'"
    )
    response = requests.get(
        # /records is capped at 10,000 rows; /exports is not.
        f"{TOULOUSE_API}/{LIVE_STATIONS[station_id]}/exports/json",
        params={
            "select": "heure_de_paris,temperature_en_degre_c",
            "where": where,
            "order_by": "heure_de_paris",
            "timezone": "UTC",
        },
        timeout=120,
    )
    response.raise_for_status()
    return [
        {
            "station_id": station_id,
            "observed_at": row["heure_de_paris"],
            "temperature_c": row["temperature_en_degre_c"],
        }
        for row in response.json()
    ]
