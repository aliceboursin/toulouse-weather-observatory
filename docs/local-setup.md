# Local setup

Back to the [README](../README.md).

This page details the steps of the [quick start](../README.md#quick-start): what each command does, what it writes, and how to check the result. Commands run from the repository root, in PowerShell on Windows. The Python and dbt steps work the same in any shell; `metabase\start.ps1` is the only Windows-specific script.

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.12 or later.
- The Google Cloud CLI (`gcloud` and `bq`), with `gcloud auth login` and `gcloud auth application-default login` done.
- A GCP project with billing enabled, set as the default: `gcloud config set project toulouse-weather-demo`. The loader uses `DELETE` and `INSERT`, which BigQuery refuses in the free sandbox.
- Java 21, for the local Metabase.
- Microsoft Edge, for the browser checks.

The project ID `toulouse-weather-demo` is written in `transform/profiles.yml`, `ingestion/load.py`, `metabase/connect_bigquery.py` and `infra/terraform/variables.tf`. Change it there to use another project.

Install the Python dependencies into the local `.venv`:

```
uv sync
```

## 1. Ingest to local files

```
uv run python -m ingestion --start 2026-11-01 --end 2026-11-18
```

Both dates are inclusive. Without them, the command fetches the last 30 days. It writes newline-delimited JSON under `data/`, which Git ignores:

```
     10 rows -> data\raw\stations\stations.ndjson
  12868 rows -> data\raw\observations\2026-11-01_2026-11-18.ndjson
  34560 rows -> data\raw\forecasts\2026-11-01_2026-11-18.ndjson
```

| File | One row per | Columns |
|---|---|---|
| `data/raw/stations/stations.ndjson` | station | `station_id`, `dataset_id`, `city`, `latitude`, `longitude`, `altitude_m`, `station_type`, `local_climate_zone` |
| `data/raw/observations/<start>_<end>.ndjson` | reading published by the source | `station_id`, `observed_at`, `temperature_c` |
| `data/raw/forecasts/<start>_<end>.ndjson` | station, hour and lead time | `station_id`, `valid_at`, `lead_days`, `temperature_c`, `grid_latitude`, `grid_longitude`, `grid_elevation_m` |

Every row also has `ingested_at`. All timestamps are UTC. Observations are written as the source publishes them, including the readings of -50 °C and the duplicates.

## 2. Load into BigQuery

### First run

Create the `raw` dataset and load the stations file. These two commands are the only manual initialisation:

```
bq --location=EU mk --dataset raw
bq load --source_format=NEWLINE_DELIMITED_JSON --autodetect --replace raw.stations data/raw/stations/stations.ndjson
```

`raw.observations` and `raw.forecasts` do not need to exist. The loader creates them, with their schema, the first time it merges a window. It needs the `raw` dataset to exist and fails with "Not found: Dataset" otherwise.

### Load a window

Use the same dates as the ingestion:

```
uv run python -m ingestion.load --start 2026-11-01 --end 2026-11-18
```

The loader (`ingestion/load.py`) works in three steps:

1. It loads the two files into staging tables, `raw.observations_incoming` and `raw.forecasts_incoming`, creating them or replacing their contents.
2. It validates the staging tables: row counts equal to the files, no row dated outside the window, no missing key, no duplicate forecast, and a full forecast grid (stations × hours × 8 lead times). On any failure it stops, and `raw.observations` and `raw.forecasts` are not changed.
3. It creates `raw.observations` and `raw.forecasts` if they do not exist. Then, in one transaction, it deletes the rows of that window from both tables and inserts the staging rows.

Step 3 only touches rows dated inside the window. Loading the same window again replaces it without creating duplicates. The loader prints the row counts inside and outside the window before and after, and stops if the count outside changed. On a first run into empty tables, the output ends with:

```
merged into raw.observations and raw.forecasts:
  observations: rows in the window 0 -> 12868, rows outside the window 0 -> 0
  forecasts: rows in the window 0 -> 34560, rows outside the window 0 -> 0
```

To stage and validate without merging, add `--validate-only`.

## 3. Build and test the dbt models

```
uv run dbt build --project-dir transform --profiles-dir transform
```

The run ends with:

```
Done. PASS=58 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=58
```

The first run creates the `analytics` dataset in the EU. All models are views.

## 4. Build the dashboard in a local Metabase

1. Download the Metabase JAR (v0.65.6.3 was used here) to `.metabase/metabase.jar` from https://downloads.metabase.com/latest/metabase.jar. Everything under `.metabase/` is ignored by Git.
2. Start Metabase and leave the terminal open:

   ```
   powershell -ExecutionPolicy Bypass -File metabase\start.ps1
   ```

   It listens on http://localhost:3000 and serves the map page on http://localhost:3001.
3. Connect BigQuery. Metabase needs the key of a service account with the roles BigQuery Data Viewer and BigQuery Job User:

   ```
   uv run python metabase/connect_bigquery.py <path-to-key.json>
   ```

   The first run also creates the local admin account and writes its email and password to `.metabase/admin-credentials.json`. The key is stored in Metabase's database; delete the key file afterwards.
4. Generate the map page, then create the questions and the dashboard:

   ```
   uv run python metabase/build_map.py
   uv run python metabase/build_dashboard.py
   ```

   The second command prints the address of each tab. Both can be run again after a change: they update the map page and the dashboard in place. The map page is a snapshot of the data, so run `build_map.py` again after loading new data. If `start.ps1` was started before the page existed, restart it so that it serves the page.

## 5. Verify

```
uv run python metabase/verify_dashboard.py
uv run python metabase/verify_map.py
```

Both scripts compare against figures from the demo's data, 1 April to 19 November 2026, so they pass in full only on that data.

`verify_dashboard.py` runs the dashboard's cards through Metabase under several filter settings and compares them with separate queries on `forecast_comparison` and `station_monitoring`. The settings are:

- all data, and one station;
- the June heatwave, 20 to 25 June;
- a date range, and the same range restricted to 14:00 to 18:00;
- one date at one hour;
- a date outside the data, and a station with no comparable hour.

It also checks that the hourly chart appears for selections of up to 10 days and the daily one beyond. It takes about 10 minutes. With a folder as an argument, it also opens the dashboard in Microsoft Edge, follows every link on the Overview tab and saves a screenshot of each tab:

```
uv run python metabase/verify_dashboard.py <screenshot-folder>
```

`verify_map.py` drives the map page in Microsoft Edge: it selects a date and an hour, changes the lead and the display mode, and checks that each control changes one setting only and that the values match BigQuery.

## Backfill a period

The three commands below reload 1 April to 19 October 2026. The ingestion fetches forecasts in requests of 31 days and takes a few minutes.

```
uv run python -m ingestion --start 2026-04-01 --end 2026-10-19
uv run python -m ingestion.load --start 2026-04-01 --end 2026-10-19
uv run dbt build --project-dir transform --profiles-dir transform
```

Then regenerate the map page and the dashboard: step 4 above for the local Metabase, or step 4 of [Deploy or update](hosted-demo.md#deploy-or-update) for the hosted demo.
