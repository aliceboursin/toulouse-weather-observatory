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
| Local Metabase dashboard on the dbt views (V1) | works |
| V2 dashboard: reliability over a selected period, temperature map, station monitoring | works |

Everything has been run on one window so far, 2026-10-20 to 2026-11-19. See [Limits](#limits).

## Requirements

- [uv](https://docs.astral.sh/uv/). Python 3.12 or later.
- The Google Cloud CLI (`gcloud` and `bq`), logged in with `gcloud auth login`.
- Java 21, for the Metabase dashboard only (step 4).
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
Done. PASS=88 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=88
```

The first run creates the `analytics` dataset in the EU. All models are views in that dataset, defined under `transform/models/`:

| View | One row per | Purpose |
|---|---|---|
| `stg_stations` | station | station label and coordinates |
| `stg_observations` | station and 15-minute slot | cleaned observations |
| `stg_forecasts` | station, target hour and lead time | forecasts, with lead 0 labelled |
| `int_station_hour_observations` | station and target hour | the observation that forecasts for that hour are compared against |
| `forecast_comparison` | station, target hour and lead time | every forecast with its observation and error |
| `station_observation_coverage` | station | observation coverage rate |
| `observation_coverage` | station and coverage category | distribution of target hours across categories |
| `forecast_reliability` | station and lead time (1 to 7) | default reliability metrics |
| `current_run_reference_error` | station | the same metrics for lead 0 |
| `station_monitoring` | station | latest valid reading, recent coverage, flagged readings and a status |
| `station_observation_gaps` | station and missing interval | every run of 15-minute slots with no valid reading |
| `station_daily_observations` | station and UTC day | valid, flagged and expected readings per day |

The four station-level views (`forecast_reliability`, `current_run_reference_error`, `station_observation_coverage`, `observation_coverage`) carry a `station_label` column from `stg_stations`, for example `11 - Toulouse Soupetard`. The source has no display name, so the name is derived from the source dataset id and has no accents. `forecast_reliability` also repeats the station's `non_future_hours` and `observation_coverage_rate` on each row, so a score can be read next to its coverage.

`forecast_comparison` and `int_station_hour_observations` also have `valid_at_local`, `valid_date_local` and `valid_hour_local`: the target hour in Toulouse local time (Europe/Paris). The V2 date filter and the map use them.

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

## 4. Dashboard in Metabase

The dashboard runs in a local Metabase (v0.65.6.3 at the time of writing). It reads the dbt views in the `analytics` dataset.

### Open the dashboard

Start Metabase and leave the terminal open:

```
powershell -ExecutionPolicy Bypass -File metabase\start.ps1
```

When the log shows `Metabase Initialization COMPLETE`, open http://localhost:3000/dashboard/2 and log in. The email and password of the local admin account are in `.metabase/admin-credentials.json`.

Metabase stops when the terminal closes or the machine restarts. Run the same command to start it again; the dashboard, the questions and the BigQuery connection are kept in `.metabase/metabase.db.mv.db`.

### What is on it

The dashboard is called "Toulouse forecast reliability". Its 18 saved questions are in the "Toulouse Data" collection.

| Section | Cards | Station filter |
|---|---|---|
| Header | data window (first and last target hour, last hour with an observation, ingestion time) | no |
| 1. Accuracy from J1 to J7 | mean absolute error by lead time, signed bias by lead time with a zero line, table with the valid comparison count | no |
| 2. Accuracy and availability by station | mean absolute error by station and lead time, next to comparisons per lead and coverage rate; mean absolute error and signed bias by station | yes |
| Current-run reference (lead 0) | lead 0 mean absolute error and comparison count, lead 0 error by station | yes |
| 3. Unavailable station-hours | coverage rate, comparable, non-future, missing and future hours; coverage by station; station-hours by coverage category | yes |

The section 1 metrics pool all stations and average over individual comparisons, so each valid comparison has equal weight. Lead 0 appears only in its own section.

The Station filter accepts one or several stations. The coverage rate tile recomputes as comparable hours divided by non-future hours for the selected stations.

There is no date filter. The reliability and coverage views are aggregated over the whole loaded window, so a date selection could not be applied to them consistently.

### Rebuild it from scratch

Everything under `.metabase/` is ignored by Git. To recreate it:

1. Download the Metabase JAR to `.metabase/metabase.jar` from https://downloads.metabase.com/latest/metabase.jar, then start Metabase as above.
2. Connect BigQuery. Metabase needs a service-account key. The project has a service account for this, `metabase-reader@toulouse-weather-demo.iam.gserviceaccount.com`, with the roles BigQuery Data Viewer and BigQuery Job User. Create a key for it, then run:

   ```
   uv run python metabase/connect_bigquery.py <path-to-key.json>
   ```

   The first run also creates the local admin account. The key is stored in Metabase's database; delete the key file afterwards.
3. Create the questions and the dashboard:

   ```
   uv run python metabase/build_dashboard.py
   ```

   The script can be run again after a change: it updates the questions and the dashboard in place.

### Check it against BigQuery

```
uv run python metabase/verify_dashboard.py
```

This runs every card through Metabase, with and without `Station = 11 - Toulouse Soupetard`, prints the results and lists which cards the filter changes. For the window 2026-10-20 to 2026-11-19 it shows 5,314 valid comparisons per lead time, a mean absolute error of 1.16 °C at J1 and 2.47 °C at J7, and for station 11 a coverage rate of 3 / 732 (0.41%). The filter changes the 14 cards of sections 2, lead 0 and 3, and leaves the 4 header and section 1 cards unchanged.

## 5. V2 dashboard

V2 is a separate dashboard, "Toulouse forecast reliability V2", in its own collection "Toulouse Data V2". V1 is unchanged. Start Metabase as in step 4, then open http://localhost:3000/dashboard/3. It opens on the Overview tab and has four tabs.

### Overview

One screen for a first visit: what the project compares, the period the data covers, when it was last collected, and a link to each of the other tabs with one example. Two of the examples are links that open the tab with filters already set (afternoons of 10 to 12 November on Reliability; stations marked "Check station" on Station monitoring).

### Reliability

Filters:

| Filter | Effect |
|---|---|
| Target date | a single day, a month or a custom range, on the local date of the target hour |
| From hour, To hour | local hour of day, 0 to 23, both inclusive. From 14 and To 18 keep target hours 14:00 to 18:00 |
| Station | one or several stations; all when empty |
| Lead in line chart | J1 to J7, default J1. Changes the line chart only |

Target date, From hour, To hour and Station apply to every card on the tab. An hour range that crosses midnight (22 to 02) is not supported.

- Mean absolute error, bias, valid comparison counts and comparison coverage are recomputed from `forecast_comparison` and `int_station_hour_observations` for the selection, with the same definitions as the dbt summary views.
- "Forecast vs observed temperature" plots the hourly mean over the selected stations at the chosen lead. Only hours where a station has both values are averaged. The lines break where there is no comparable hour.
- "Your selection" shows the selected days and hours next to the available data range. Outside that range, its status reads "No data for this selection", the charts show "No results" and the coverage tile has no value.
- In "Scores by station", a blank score means the station had no observation to compare, not an error of zero.
- A "How to read this tab" card next to the main chart defines target hour, lead time, error (with an example of a positive error), mean absolute error, bias, valid comparisons and comparison coverage.

### Map

The map is a page generated by `metabase/build_map.py` and shown in the tab through an iframe. Metabase's own maps cannot colour a station by a value on a fixed scale, which the error view needs.

- Controls: target date, local time, forecast lead (J7 to J1, with J0 apart as the current-run reference) and display mode (Forecast, Error vs observed, Observed). Each control changes one setting and keeps the others. They are independent of the dashboard filters.
- Error mode shows forecast − observed on a diverging scale centred on zero and fixed at ±6 °C, so colours are comparable across leads. Forecast and Observed use a fixed 0 to 25 °C scale.
- A station with no observation stays on the map with a dashed outline and "no obs.", and its error is listed as unavailable.
- Clicking a station shows its forecast, observed reference, signed error and observation method.
- A "How to read" panel explains how the observed reference is built from the 15-minute readings and how it differs from J0.
- A line under the map gives when the page was generated and which data collection it shows.

The page holds a snapshot of `forecast_comparison` taken when `build_map.py` ran. Run it again after loading new data. `metabase/start.ps1` serves the page on http://localhost:3001.

### Station monitoring

Everything on this tab is measured against the time the observations were collected, shown at the top with how long ago that was. It is not live monitoring.

Filters: Station, and Status ("Check station" or "OK"). Both apply to every card except the snapshot line.

A station's status is "Check station" when, at collection time, the first of these is true:

1. no valid reading in the previous 24 hours;
2. the latest valid reading is more than 2 hours old;
3. at least one flagged (-50 °C) reading in the previous 24 hours;
4. fewer than 80% of the 96 expected readings in the previous 24 hours.

Otherwise the status is "OK". The "Why" column gives the measured value next to its limit, for example "Latest valid reading 4.3 h before collection (limit 2 h)". The thresholds are set at the top of `transform/models/marts/station_monitoring.sql`.

The tab uses reporting coverage: 15-minute readings received divided by readings expected (96 a day). The Reliability tab uses comparison coverage: target hours with a reading usable against a forecast, divided by non-future target hours. A station can report 90% of its readings and still have lower comparison coverage if the missing ones fall on the hour.

The tab also shows 7-day reporting coverage by station, daily reporting coverage of the selected stations (select one station for its own history), gaps of 1 hour or more, and flagged readings per day.

### Rebuild and check V2

```
uv run python metabase/build_map.py
uv run python metabase/build_dashboard_v2.py
```

Both can be run again after a change. To check the result:

```
uv run python metabase/verify_dashboard_v2.py
uv run --with playwright python metabase/verify_map.py
```

`verify_dashboard_v2.py` runs the cards through Metabase under several filter settings and compares them with separate queries on `forecast_comparison` and `station_monitoring`. The settings include all data, one station, a date range, the same range restricted to 14:00 to 18:00, one date at one hour, a date outside the data, and Soupetard on a day with no comparable hour. It also checks that Status = "Check station" returns exactly the rows marked "Check station" in the full status table. It takes several minutes.

With a folder as an argument and `--with playwright`, it also opens the dashboard in Microsoft Edge, follows every link on the Overview tab, checks where each one lands, and saves a screenshot of each tab:

```
uv run --with playwright python metabase/verify_dashboard_v2.py <screenshot-folder>
```

`verify_map.py` drives the map page in Microsoft Edge: it selects a date and 16:00, chooses J7, switches from Forecast to Error vs observed, changes to J1, and checks that the date and hour never change and that the values match BigQuery.

### Not built yet

- A heatwave example. The loaded window is 20 October to 19 November 2026, with no hot spell. It needs a summer window: observations from the Toulouse Métropole API (available back to 2019) and forecasts from the Previous Runs API (most models archived from January 2024), for the stations that were reporting at the time. Loading a second window also needs loads that append; `bq load --replace` keeps one window per table.
- Automated alerts. They need fresh data first: a scheduled ingestion and load (which requires billing on the GCP project) so `collected_at` stays recent. Then a Metabase alert on a question that returns the stations to check, which requires an email (SMTP) or Slack connection in Metabase and Metabase running continuously.

## Limits

- One window only. The results above come from a single 31-day load in autumn. `bq load --replace` keeps one window per table, and loading several windows is not supported yet.
- Uneven station coverage. Station 11 has 3 valid comparisons per lead time, station 33 has 120 and station 58 has 345, against 590 to 730 for the other seven. All stations stay in `forecast_reliability`, with no minimum number of comparisons, so their rows rest on few values; check `valid_comparisons` before reading a score.
- Fallback observations are not centred. A `preceding_15min` observation is centred 7.5 minutes before the forecast time and a `following_15min` one 7.5 minutes after.
- The future/missing split relies on `ingested_at` from the forecasts. If observations and forecasts are ever ingested at different times, hours near the cutoff can be put in the wrong category.
- Forecasts are for a model grid cell, not the station itself. The cell centre is 380 to 1,330 m from the station.
- Lead time is in whole days. The Previous Runs API does not give the hour a forecast was issued.
- The map page is a snapshot and needs an internet connection for the Leaflet library and the OpenStreetMap background.
- On the night clocks go back (25 October 2026), 02:00 local time occurs twice. The map shows the first occurrence.
- Monitoring statuses describe the stations at collection time. With a snapshot several days old they say nothing about the stations now.
- The dashboard is local. Metabase listens on this machine only (`127.0.0.1`) and keeps its data in a local H2 file, which Metabase does not recommend for production use.
- The models are views, and in the BigQuery sandbox they expire after 60 days. Running the dbt build again recreates them.
