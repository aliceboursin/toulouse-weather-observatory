"""Generate the interactive temperature map page from the BigQuery views.

    uv run python metabase/build_map.py

Writes .metabase/map/index.html: the page in map_template.html with a snapshot
of forecast_comparison embedded in it. metabase/start.ps1 serves that folder
on http://localhost:3001, and the V2 dashboard shows it in its Map tab.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from client import DATA_DIR, Metabase

DATABASE_NAME = "Toulouse Data (BigQuery)"
TEMPLATE = Path(__file__).with_name("map_template.html")
OUTPUT = DATA_DIR / "map" / "index.html"

STATIONS_SQL = """
SELECT station_id, station_label, latitude, longitude
FROM analytics.stg_stations
ORDER BY station_id
"""

COLLECTED_SQL = """
SELECT FORMAT_TIMESTAMP('%Y-%m-%d %H:%M UTC', MAX(ingested_at)) AS collected
FROM analytics.stg_forecasts
"""

# One row per station and target hour, with the 8 lead times side by side.
HOURS_SQL = """
SELECT
  station_id,
  FORMAT_TIMESTAMP('%Y-%m-%dT%H:%MZ', valid_at) AS valid_at_utc,
  CAST(valid_date_local AS STRING) AS local_date,
  valid_hour_local AS local_hour,
  MAX(observed_temperature_c) AS observed,
  MAX(CASE
    WHEN observation_method = 'centered_30min' THEN 'c'
    WHEN observation_method = 'preceding_15min' THEN 'p'
    WHEN observation_method = 'following_15min' THEN 'f'
    WHEN observation_status = 'future_at_ingestion' THEN 'u'
    ELSE 'm'
  END) AS code,
  MAX(IF(lead_days = 0, forecast_temperature_c, NULL)) AS f0,
  MAX(IF(lead_days = 1, forecast_temperature_c, NULL)) AS f1,
  MAX(IF(lead_days = 2, forecast_temperature_c, NULL)) AS f2,
  MAX(IF(lead_days = 3, forecast_temperature_c, NULL)) AS f3,
  MAX(IF(lead_days = 4, forecast_temperature_c, NULL)) AS f4,
  MAX(IF(lead_days = 5, forecast_temperature_c, NULL)) AS f5,
  MAX(IF(lead_days = 6, forecast_temperature_c, NULL)) AS f6,
  MAX(IF(lead_days = 7, forecast_temperature_c, NULL)) AS f7
FROM analytics.forecast_comparison
GROUP BY station_id, valid_at, valid_date_local, valid_hour_local
ORDER BY valid_at, station_id
"""


def export(mb: Metabase, database_id: int, sql: str) -> list[dict]:
    """Run a query through Metabase and return every row (no 2,000-row cap)."""
    query = {"database": database_id, "type": "native", "native": {"query": sql}}
    response = mb.http.post(
        f"{mb.url}/api/dataset/json", data={"query": json.dumps(query)}, timeout=600
    )
    response.raise_for_status()
    return response.json()


def build(mb: Metabase) -> dict:
    database = next(d for d in mb.get("database")["data"] if d["name"] == DATABASE_NAME)
    stations = export(mb, database["id"], STATIONS_SQL)
    rows = export(mb, database["id"], HOURS_SQL)
    collected = export(mb, database["id"], COLLECTED_SQL)[0]["collected"]

    station_index = {s["station_id"]: i for i, s in enumerate(stations)}
    hours, hour_index = [], {}
    for row in rows:
        if row["valid_at_utc"] not in hour_index:
            hour_index[row["valid_at_utc"]] = len(hours)
            hours.append({"utc": row["valid_at_utc"], "d": row["local_date"], "h": row["local_hour"]})

    def grid():
        return [[None] * len(hours) for _ in stations]

    forecast = {lead: grid() for lead in range(8)}
    observed, method = grid(), grid()
    for row in rows:
        s, h = station_index[row["station_id"]], hour_index[row["valid_at_utc"]]
        observed[s][h] = row["observed"]
        method[s][h] = row["code"]
        for lead in range(8):
            forecast[lead][s][h] = row[f"f{lead}"]

    # Default view: 16:00 on the latest local date with an observation at 16:00.
    with_observation = [
        hours[h]["d"]
        for h in range(len(hours))
        if hours[h]["h"] == 16 and any(observed[s][h] is not None for s in range(len(stations)))
    ]
    return {
        "stations": [
            {"id": s["station_id"], "label": s["station_label"], "lat": s["latitude"], "lon": s["longitude"]}
            for s in stations
        ],
        "hours": hours,
        "forecast": forecast,
        "observed": observed,
        "method": method,
        "range": {
            "first": f"{hours[0]['d']} {hours[0]['h']:02d}:00",
            "last": f"{hours[-1]['d']} {hours[-1]['h']:02d}:00",
        },
        "default": {"date": max(with_observation), "hour": 16},
        # When this page was generated, and which data collection it shows.
        "snapshot": {
            "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "collected": collected,
        },
    }


def main() -> None:
    data = build(Metabase().login())
    page = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*DATA*/", json.dumps(data, separators=(",", ":"))
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    print(
        f"{len(data['stations'])} stations x {len(data['hours'])} hours x 8 leads "
        f"-> {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)"
    )


if __name__ == "__main__":
    main()
