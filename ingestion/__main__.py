"""Pull stations, observations and forecasts for a date window into data/raw.

    uv run python -m ingestion                      # last 30 days
    uv run python -m ingestion --start 2026-11-01 --end 2026-11-18
"""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .config import LIVE_STATIONS, RAW_DIR
from .openmeteo import fetch_forecasts
from .toulouse import fetch_observations, fetch_stations


def write_ndjson(path: Path, rows: list[dict], ingested_at: str) -> None:
    """Newline-delimited JSON, the format BigQuery loads natively."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps({**row, "ingested_at": ingested_at}) + "\n")
    print(f"{len(rows):>7} rows -> {path.relative_to(RAW_DIR.parent.parent)}")


def main() -> None:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, default=today - timedelta(days=30))
    parser.add_argument("--end", type=date.fromisoformat, default=today)
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must not be after --end")

    ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
    window = f"{args.start}_{args.end}"

    stations = fetch_stations()
    write_ndjson(RAW_DIR / "stations" / "stations.ndjson", stations, ingested_at)

    observations = []
    for station_id in LIVE_STATIONS:
        observations += fetch_observations(station_id, args.start, args.end)
    write_ndjson(RAW_DIR / "observations" / f"{window}.ndjson", observations, ingested_at)

    forecasts = fetch_forecasts(stations, args.start, args.end)
    write_ndjson(RAW_DIR / "forecasts" / f"{window}.ndjson", forecasts, ingested_at)


if __name__ == "__main__":
    main()
