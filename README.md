# Toulouse Data

Toulouse Data measures how accurate temperature forecasts are for Toulouse, per weather station and per lead time (1 to 7 days ahead).

It uses two public sources:

- Observations: 15-minute temperature records from the Toulouse Métropole weather stations ([data.toulouse-metropole.fr](https://data.toulouse-metropole.fr)).
- Forecasts: hourly `temperature_2m` values from the [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api), which returns what was forecast 1 to 7 days before each hour.

## Status

| Step | State |
|---|---|
| Local Python ingestion of stations, observations and forecasts | works |
| Manual load of the raw files into BigQuery | works |
| dbt staging models `stg_observations` and `stg_forecasts`, with tests | works |
| Forecast comparison, observation coverage and reliability summaries in dbt | works |

Everything has been run on one window so far, 2026-10-20 to 2026-11-19. See [Limits](#limits).

## Requirements

- [uv](https://docs.astral.sh/uv/). Python 3.12 or later.
- The Google Cloud CLI (`gcloud` and `bq`), logged in with `gcloud auth login`.
- A GCP project set as the default: `gcloud config set project toulouse-weather-demo`. The project ID `toulouse-weather-demo` is also written in `transform/profiles.yml`.

The project runs in the BigQuery sandbox (no billing account). In the sandbox, tables and views expire after 60 days.

Install the Python dependencies into the local `.venv`:

```
uv sync
```

## 1. Ingest to local files

```
uv run python -m ingestion
```

This fetches the last 30 days, up to today (UTC). To choose the window, both dates inclusive:

```
uv run python -m ingestion --start 2026-11-01 --end 2026-11-18
```

Output for the window 2026-10-20 to 2026-11-19:

```
     10 rows -> data\raw\stations\stations.ndjson
  21218 rows -> data\raw\observations\2026-10-20_2026-11-19.ndjson
  59520 rows -> data\raw\forecasts\2026-10-20_2026-11-19.ndjson
```

The files are newline-delimited JSON. `data/` is ignored by Git.

| File | One row per | Columns |
|---|---|---|
| `data/raw/stations/stations.ndjson` | station | `station_id`, `dataset_id`, `city`, `latitude`, `longitude`, `altitude_m`, `station_type`, `local_climate_zone` |
| `data/raw/observations/<start>_<end>.ndjson` | reading published by the source | `station_id`, `observed_at`, `temperature_c` |
| `data/raw/forecasts/<start>_<end>.ndjson` | station, hour and lead time | `station_id`, `valid_at`, `lead_days`, `temperature_c`, `grid_latitude`, `grid_longitude`, `grid_elevation_m` |

Every row also has `ingested_at`. All timestamps are UTC.

The 10 stations are listed in `ingestion/config.py`. They are the stations that were still reporting on 2026-11-19.

Observations are written as the source publishes them, including -50 °C readings and duplicate rows. Cleaning happens in dbt (step 3).

In the forecasts file, `lead_days` 1 to 7 is the forecast issued that many days before `valid_at`. `lead_days` 0 is the value from the most recent model run.

## 2. Load into BigQuery

Create the `raw` dataset once:

```
bq --location=EU mk --dataset raw
```

Load the three files. Replace the file names with the window you ingested:

```
bq load --source_format=NEWLINE_DELIMITED_JSON --autodetect --replace raw.stations data/raw/stations/stations.ndjson

bq load --source_format=NEWLINE_DELIMITED_JSON --replace raw.observations data/raw/observations/2026-10-20_2026-11-19.ndjson "station_id:INTEGER,observed_at:TIMESTAMP,temperature_c:FLOAT,ingested_at:TIMESTAMP"

bq load --source_format=NEWLINE_DELIMITED_JSON --replace raw.forecasts data/raw/forecasts/2026-10-20_2026-11-19.ndjson "station_id:INTEGER,valid_at:TIMESTAMP,lead_days:INTEGER,temperature_c:FLOAT,grid_latitude:FLOAT,grid_longitude:FLOAT,grid_elevation_m:FLOAT,ingested_at:TIMESTAMP"
```

`--replace` overwrites the destination table. Each load leaves the table with the contents of that one file: loading a second window into `raw.observations` deletes the first. Loading several windows into one table is not supported yet.

Check the row counts against the ingestion output:

```
bq query --use_legacy_sql=false "SELECT COUNT(*) AS row_count FROM raw.observations"
```

## 3. Build and test the dbt models

dbt needs application-default credentials. Create them once:

```
gcloud auth application-default login
```

Build the models and run their tests:

```
uv run dbt build --project-dir transform --profiles-dir transform
```

The run ends with:

```
Done. PASS=58 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=58
```

The first run creates the `analytics` dataset in the EU. All models are views in that dataset, defined under `transform/models/`:

| View | One row per | Purpose |
|---|---|---|
| `stg_observations` | station and 15-minute slot | cleaned observations |
| `stg_forecasts` | station, target hour and lead time | forecasts, with lead 0 labelled |
| `int_station_hour_observations` | station and target hour | the observation that forecasts for that hour are compared against |
| `forecast_comparison` | station, target hour and lead time | every forecast with its observation and error |
| `station_observation_coverage` | station | observation coverage rate |
| `observation_coverage` | station and coverage category | distribution of target hours across categories |
| `forecast_reliability` | station and lead time (1 to 7) | default reliability metrics |
| `current_run_reference_error` | station | the same metrics for lead 0 |

### What `stg_observations` does

The view has the columns `station_id`, `observed_at`, `temperature_c` and `reading_count`.

- It removes readings of exactly -50 °C, a value flagged during profiling. In the window 2026-10-20 to 2026-11-19, 465 readings on four stations are exactly -50 °C, and the next lowest reading is 1.8 °C.
- It averages duplicates. The source sometimes publishes several readings for the same station and timestamp, with temperatures that can differ (9.4 and 10.7 °C in one case) and no field that says which is correct. The view averages them, rounded to 2 decimals.
- `reading_count` is the number of valid readings that contribute to the row after the -50 °C filter. It is 1 for most rows and can be greater than two; in that window its maximum is 2.
- It keeps the 15-minute resolution. It does not resample to hourly values or fill gaps: a slot with no valid reading has no row.

In that window, the 21,218 raw rows become 20,732 rows: the filter removes the 465 readings and 21 duplicate pairs are averaged.

The tests check that no column is null and that no station has two rows for the same `observed_at` (`transform/tests/assert_stg_observations_unique.sql`).

### What `stg_forecasts` does

The view has the columns `station_id`, `valid_at`, `lead_days`, `forecast_kind`, `forecast_temperature_c` and `ingested_at`. It renames and labels rows. It does not filter or deduplicate: a duplicate or a gap in `raw.forecasts` fails a test.

`forecast_kind` is `forecast` for leads 1 to 7 and `current_run_reference` for lead 0. Lead 0 is the most recent model run, not a forecast issued in advance.

The tests check that:

- no column is null;
- `(station_id, valid_at, lead_days)` is unique;
- `lead_days` is between 0 and 7;
- every station and target hour has all 8 lead times (`transform/tests/assert_stg_forecasts_all_leads_present.sql`). The ingestion skips null temperatures, so a missing forecast is a missing row, and this test is what detects it. It checks the station-hours present in the data: it cannot detect a station-hour that is missing for every lead time.

For the window 2026-10-20 to 2026-11-19, the view has 59,520 rows, which is the full grid of 10 stations × 744 hours × 8 lead times.

### How forecasts are matched to observations

A forecast is the instantaneous temperature at its target time t (UTC). A station reading timestamped t is the mean over the 15 minutes ending at t. For each station and target hour, `int_station_hour_observations` looks up the cleaned readings at t and at t + 15 minutes:

| Readings found | Observation used | `observation_method` |
|---|---|---|
| both | their equal-weight average, covering t - 15 min to t + 15 min | `centered_30min` |
| only the one at t | that reading | `preceding_15min` |
| only the one at t + 15 min | that reading | `following_15min` |
| neither | none | null |

`forecast_comparison` keeps every forecast row. `error_c` is the forecast minus the observation, so a positive value means the forecast was too warm. `absolute_error_c` is its magnitude. Both are null when there is no observation.

### Coverage

`observation_status` says why a target hour has no observation:

- `future_at_ingestion`: `valid_at` is after the forecast's `ingested_at`, so the hour had not happened yet.
- `missing_observation`: the hour was in the past and the station has no valid reading at t or t + 15 minutes.

The forecast ingestion time is a proxy for the observation cutoff. It is accurate here because one ingestion run fetches observations and forecasts together.

Both coverage views count each station and target hour once, whatever the number of lead times.

#### Coverage rate

The default observation coverage rate is the number of comparable station-hours divided by the number of non-future station-hours. A station-hour is comparable when it has an observation, by any of the three methods. Future hours are reported in their own column and are not in the denominator, because no observation could exist for them yet.

`station_observation_coverage` has one row per station, with `target_hours`, `future_hours`, `non_future_hours`, `comparable_hours`, `missing_hours` and `observation_coverage_rate`.

```
bq query --use_legacy_sql=false "SELECT * FROM analytics.station_observation_coverage ORDER BY station_id"
```

For the window 2026-10-20 to 2026-11-19, every station has 744 target hours, of which 12 are future and 732 are non-future:

| Station | Comparable hours | Missing hours | Coverage rate |
|---|---|---|---|
| 2 | 724 | 8 | 98.9% |
| 11 | 3 | 729 | 0.4% |
| 12 | 710 | 22 | 97.0% |
| 13 | 730 | 2 | 99.7% |
| 23 | 659 | 73 | 90.0% |
| 33 | 120 | 612 | 16.4% |
| 37 | 725 | 7 | 99.0% |
| 42 | 590 | 142 | 80.6% |
| 58 | 345 | 387 | 47.1% |
| 62 | 708 | 24 | 96.7% |
| All | 5,314 | 2,006 | 72.6% |

The gaps are in the source. Station 11, for example, published 5 readings in this window before the ingestion ran, and 3 of them fall on a target hour.

#### Distribution across categories

`observation_coverage` has one row per station and `coverage_category`. `share_of_station_hours` is the category's hours divided by all of the station's target hours, future hours included. It shows how target hours are distributed across categories and is not the coverage rate.

```
bq query --use_legacy_sql=false "SELECT * FROM analytics.observation_coverage ORDER BY station_id, coverage_category"
```

For the same window, all stations together (7,440 station-hours):

| `coverage_category` | Station-hours | Share of all target hours |
|---|---|---|
| `centered_30min` | 5,045 | 67.8% |
| `missing_observation` | 2,006 | 27.0% |
| `preceding_15min` | 156 | 2.1% |
| `future_at_ingestion` | 120 | 1.6% |
| `following_15min` | 113 | 1.5% |

### Reliability metrics

`forecast_reliability` has one row per station and lead time, for leads 1 to 7, with:

- `valid_comparisons`: the number of forecasts with a computed error;
- `mean_absolute_error_c`: the mean of `absolute_error_c`;
- `mean_signed_error_c`: the mean of `error_c`.

`current_run_reference_error` has the same columns for lead 0, per station. Lead 0 is kept out of `forecast_reliability` because it is not a forecast issued in advance.

```
bq query --use_legacy_sql=false "SELECT * FROM analytics.forecast_reliability ORDER BY station_id, lead_days"
```

For the window 2026-10-20 to 2026-11-19, with all stations pooled (5,314 valid comparisons per lead time):

| Lead time (days) | Mean absolute error (°C) | Mean signed error (°C) |
|---|---|---|
| 0 (current-run reference) | 0.83 | -0.09 |
| 1 | 1.16 | -0.09 |
| 2 | 1.39 | -0.46 |
| 3 | 1.55 | -0.27 |
| 4 | 1.79 | 0.22 |
| 5 | 1.83 | 0.28 |
| 6 | 2.31 | 0.65 |
| 7 | 2.47 | 0.84 |

## Limits

- One window only. The results above come from a single 31-day load in autumn. `bq load --replace` keeps one window per table, and loading several windows is not supported yet.
- Uneven station coverage. Station 11 has 3 valid comparisons per lead time, station 33 has 120 and station 58 has 345, against 590 to 730 for the other seven. All stations stay in `forecast_reliability`, with no minimum number of comparisons, so their rows rest on few values; check `valid_comparisons` before reading a score.
- Fallback observations are not centred. A `preceding_15min` observation is centred 7.5 minutes before the forecast time and a `following_15min` one 7.5 minutes after.
- The future/missing split relies on `ingested_at` from the forecasts. If observations and forecasts are ever ingested at different times, hours near the cutoff can be put in the wrong category.
- Forecasts are for a model grid cell, not the station itself. The cell centre is 380 to 1,330 m from the station.
- Lead time is in whole days. The Previous Runs API does not give the hour a forecast was issued.
- The models are views, and in the BigQuery sandbox they expire after 60 days. Running the dbt build again recreates them.
