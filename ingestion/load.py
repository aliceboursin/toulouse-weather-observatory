"""Load one ingested window into BigQuery, safely and repeatably.

    uv run python -m ingestion.load --start 2026-04-01 --end 2026-10-19
    uv run python -m ingestion.load --start 2026-04-01 --end 2026-10-19 --validate-only

Reads data/raw/observations/<start>_<end>.ndjson and the matching forecasts
file, written by `python -m ingestion`. Then:

1. loads both files into staging tables (raw.observations_incoming and
   raw.forecasts_incoming), replacing their contents;
2. validates the staging tables; stops here on any failure, or with
   --validate-only, leaving raw.observations and raw.forecasts untouched;
3. in one transaction, deletes the rows of that window from raw.observations
   and raw.forecasts and inserts the staging rows.

Step 3 only touches rows dated inside the window, so other windows are kept,
and running the same window again replaces it instead of duplicating it.
Uses your application-default credentials (gcloud auth application-default login).
"""

import argparse
import sys
from datetime import date, timedelta

from google.cloud import bigquery

from .config import LIVE_STATIONS, RAW_DIR

PROJECT = "toulouse-weather-demo"
DATASET = "raw"

# table -> (timestamp column, schema)
TABLES = {
    "observations": (
        "observed_at",
        [
            bigquery.SchemaField("station_id", "INTEGER"),
            bigquery.SchemaField("observed_at", "TIMESTAMP"),
            bigquery.SchemaField("temperature_c", "FLOAT"),
            bigquery.SchemaField("ingested_at", "TIMESTAMP"),
        ],
    ),
    "forecasts": (
        "valid_at",
        [
            bigquery.SchemaField("station_id", "INTEGER"),
            bigquery.SchemaField("valid_at", "TIMESTAMP"),
            bigquery.SchemaField("lead_days", "INTEGER"),
            bigquery.SchemaField("temperature_c", "FLOAT"),
            bigquery.SchemaField("grid_latitude", "FLOAT"),
            bigquery.SchemaField("grid_longitude", "FLOAT"),
            bigquery.SchemaField("grid_elevation_m", "FLOAT"),
            bigquery.SchemaField("ingested_at", "TIMESTAMP"),
        ],
    ),
}


def one_row(client: bigquery.Client, sql: str) -> dict:
    return dict(next(iter(client.query(sql).result())))


def load_staging(client: bigquery.Client, table: str, window: str) -> int:
    path = RAW_DIR / table / f"{window}.ndjson"
    if not path.exists():
        sys.exit(f"Missing {path}. Run: uv run python -m ingestion --start ... --end ...")
    with path.open("rb") as file:
        lines = sum(1 for _ in file)
        file.seek(0)
        job = client.load_table_from_file(
            file,
            f"{PROJECT}.{DATASET}.{table}_incoming",
            job_config=bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                schema=TABLES[table][1],
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            ),
        )
    job.result()
    print(f"staged {lines:>7} rows from {path.name} into {DATASET}.{table}_incoming")
    return lines


def validate(client: bigquery.Client, lines: dict[str, int], start: date, end: date) -> bool:
    """Check the staging tables. Returns False if anything is wrong."""
    lower, upper = f"TIMESTAMP '{start}'", f"TIMESTAMP '{end + timedelta(days=1)}'"
    hours = ((end - start).days + 1) * 24
    ok = True

    def check(condition: bool, message: str) -> None:
        nonlocal ok
        print(("  PASS  " if condition else "  FAIL  ") + message)
        ok = ok and condition

    o = one_row(client, f"""
        SELECT COUNT(*) AS n,
          COUNTIF(station_id IS NULL OR observed_at IS NULL) AS null_keys,
          COUNTIF(observed_at < {lower} OR observed_at >= {upper}) AS outside,
          COUNTIF(temperature_c IS NULL) AS null_temperature,
          COUNTIF(temperature_c = -50) AS flagged,
          COUNT(DISTINCT station_id) AS stations,
          COUNT(DISTINCT ingested_at) AS ingestions
        FROM {DATASET}.observations_incoming""")
    print("observations:")
    check(o["n"] == lines["observations"], f"{o['n']} rows staged, {lines['observations']} lines in the file")
    check(o["null_keys"] == 0, f"{o['null_keys']} rows without a station or timestamp")
    check(o["outside"] == 0, f"{o['outside']} rows dated outside {start} to {end}")
    check(o["ingestions"] == 1, f"{o['ingestions']} ingestion run(s) in the file")
    print(f"        {o['stations']} stations; kept as published: {o['flagged']} readings of -50 °C, {o['null_temperature']} null temperatures")

    f = one_row(client, f"""
        SELECT COUNT(*) AS n,
          COUNT(DISTINCT FORMAT('%d|%t|%d', station_id, valid_at, lead_days)) AS distinct_keys,
          COUNTIF(station_id IS NULL OR valid_at IS NULL OR lead_days IS NULL) AS null_keys,
          COUNTIF(valid_at < {lower} OR valid_at >= {upper}) AS outside,
          COUNTIF(temperature_c IS NULL) AS null_temperature,
          COUNTIF(lead_days NOT BETWEEN 0 AND 7) AS bad_leads,
          COUNT(DISTINCT station_id) AS stations,
          COUNT(DISTINCT valid_at) AS hours
        FROM {DATASET}.forecasts_incoming""")
    print("forecasts:")
    check(f["n"] == lines["forecasts"], f"{f['n']} rows staged, {lines['forecasts']} lines in the file")
    check(f["n"] == f["distinct_keys"], f"{f['n'] - f['distinct_keys']} duplicate (station, hour, lead) rows")
    check(f["null_keys"] == 0 and f["bad_leads"] == 0, f"{f['null_keys']} rows without a key, {f['bad_leads']} with a lead outside 0 to 7")
    check(f["outside"] == 0, f"{f['outside']} rows dated outside {start} to {end}")
    grid = len(LIVE_STATIONS) * hours * 8
    check(f["n"] == grid, f"{f['n']} rows for a full grid of {len(LIVE_STATIONS)} stations x {hours} hours x 8 leads = {grid}")
    print(f"        {f['stations']} stations, {f['hours']} hours; {f['null_temperature']} null temperatures")
    return ok


def merge(client: bigquery.Client, start: date, end: date) -> None:
    lower, upper = f"TIMESTAMP '{start}'", f"TIMESTAMP '{end + timedelta(days=1)}'"

    def counts() -> dict:
        return one_row(client, f"""
            SELECT
              (SELECT COUNT(*) FROM {DATASET}.observations WHERE observed_at < {lower} OR observed_at >= {upper}) AS observations_outside,
              (SELECT COUNT(*) FROM {DATASET}.observations WHERE observed_at >= {lower} AND observed_at < {upper}) AS observations_inside,
              (SELECT COUNT(*) FROM {DATASET}.forecasts WHERE valid_at < {lower} OR valid_at >= {upper}) AS forecasts_outside,
              (SELECT COUNT(*) FROM {DATASET}.forecasts WHERE valid_at >= {lower} AND valid_at < {upper}) AS forecasts_inside""")

    # On a new project the destination tables do not exist yet.
    for table, (_, schema) in TABLES.items():
        client.create_table(bigquery.Table(f"{PROJECT}.{DATASET}.{table}", schema=schema), exists_ok=True)

    before = counts()
    statements = ["BEGIN TRANSACTION;"]
    for table, (column, schema) in TABLES.items():
        names = ", ".join(field.name for field in schema)
        statements += [
            f"DELETE FROM {DATASET}.{table} WHERE {column} >= {lower} AND {column} < {upper};",
            f"INSERT INTO {DATASET}.{table} ({names}) SELECT {names} FROM {DATASET}.{table}_incoming;",
        ]
    statements.append("COMMIT TRANSACTION;")
    client.query("\n".join(statements)).result()
    after = counts()

    print("merged into raw.observations and raw.forecasts:")
    for table in TABLES:
        print(
            f"  {table}: rows in the window {before[f'{table}_inside']} -> {after[f'{table}_inside']}, "
            f"rows outside the window {before[f'{table}_outside']} -> {after[f'{table}_outside']}"
        )
        if before[f"{table}_outside"] != after[f"{table}_outside"]:
            sys.exit(f"Rows outside the window changed in raw.{table}. Investigate before going further.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # messages contain °C and arrows; Windows defaults to cp1252
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--validate-only", action="store_true", help="stage and validate, but do not merge")
    args = parser.parse_args()

    client = bigquery.Client(project=PROJECT)
    window = f"{args.start}_{args.end}"
    lines = {table: load_staging(client, table, window) for table in TABLES}
    if not validate(client, lines, args.start, args.end):
        sys.exit("Validation failed. raw.observations and raw.forecasts were not changed.")
    if args.validate_only:
        print("Validation passed. Nothing merged (--validate-only).")
        return
    merge(client, args.start, args.end)


if __name__ == "__main__":
    main()
