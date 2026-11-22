"""Create or update the V2 dashboard (four tabs) in the local Metabase.

    uv run python metabase/build_map.py            # the Map tab's page
    uv run python metabase/build_dashboard_v2.py

Safe to run again: questions and the dashboard are matched by name inside the
"Toulouse Data V2" collection and updated in place. The V1 dashboard and its
collection are not touched.
"""

import uuid

from build_dashboard import BLUE, DEGREES, PERCENT, STATION_FILTER, column, columns, find_or_create
from client import METABASE_URL, Metabase

COLLECTION = "Toulouse Data V2"
DASHBOARD = "Toulouse forecast reliability V2"
DATABASE_NAME = "Toulouse Data (BigQuery)"
MAP_URL = "http://localhost:3001/index.html"
MAP_HOST = "localhost:3001"

DATE_PARAMETER_ID = "date02"
HOUR_FROM_PARAMETER_ID = "hourfrom02"
HOUR_TO_PARAMETER_ID = "hourto02"
STATION_PARAMETER_ID = "station02"
LEAD_PARAMETER_ID = "lead02"
STATUS_PARAMETER_ID = "status02"

ORANGE = "#eb6834"
HIGHLIGHT = "#fab219"

# Question names, shared with verify_dashboard_v2.py.
AVAILABLE = "Available data"
SELECTION = "Your selection"
MAE_BY_LEAD = "Average forecast error by lead time (mean absolute error)"
COVERAGE = "Comparison coverage"
COMPARISONS = "Valid comparisons per lead"
MISSING = "Hours with no observation"
FUTURE = "Future hours (not counted)"
TIME_SERIES = "Forecast vs observed temperature"
BIAS_BY_LEAD = "Bias by lead time (mean signed error)"
SCORES_BY_LEAD = "Scores by lead time"
SCORES_BY_STATION = "Scores by station (blank = nothing to compare)"
SNAPSHOT = "Snapshot"
TO_CHECK = "Stations to check"
STATIONS_OK = "Stations OK"
STATUS_TABLE = "Station status at collection time"
REPORTING_7D = "Reporting coverage, 7 days before collection"
REPORTING_DAILY = "Daily reporting coverage (selected stations)"
FLAGGED_DAILY = "Flagged readings per day (-50 °C)"
GAPS = "Gaps of 1 hour or more"

# Reliability filters. {{target_date}} filters on the local date of the target
# hour, {{hour_from}} and {{hour_to}} on its local hour of day, {{station}} on the station
# label; with no selection each becomes "1 = 1". BigQuery field filters need
# the table written as `dataset.table`, unaliased.
COMPARISON = "`analytics.forecast_comparison`"
STATION_HOURS = "`analytics.int_station_hour_observations`"
PERIOD = f"{{{{target_date}}}}\n  AND {{{{hour_from}}}}\n  AND {{{{hour_to}}}}\n  AND {STATION_FILTER}"

# Station monitoring filters: station, plus an optional status.
MONITORING_FILTER = (
    f"{STATION_FILTER}\n"
    "  [[AND station_id IN (SELECT station_id FROM analytics.station_monitoring WHERE status = {{status}})]]"
)

LEAD_METRICS_SQL = f"""
-- Selected days, hours of day and stations. AVG runs over individual
-- comparisons, so each valid comparison has equal weight. Leads 1 to 7 only.
SELECT
  CONCAT('J', CAST(lead_days AS STRING)) AS lead_time,
  COUNT(error_c) AS valid_comparisons,
  ROUND(AVG(absolute_error_c), 3) AS mean_absolute_error_c,
  ROUND(AVG(error_c), 3) AS mean_signed_error_c
FROM {COMPARISON}
WHERE forecast_kind = 'forecast'
  AND {PERIOD}
GROUP BY lead_days
HAVING COUNT(error_c) > 0
ORDER BY lead_days
"""

RELIABILITY = ("target_date", "hour_from", "hour_to", "station")
MONITORING = ("station", "status")

DATE_COLUMN = {"date_abbreviate": True, "time_style": "HH:mm"}


def coverage_scalar(expression: str, hide_when_empty: bool = False) -> str:
    having = "HAVING COUNT(*) > 0\n" if hide_when_empty else ""
    return f"SELECT {expression} AS value\nFROM {STATION_HOURS}\nWHERE {PERIOD}\n{having}"


def monitoring_sql(select: str, table: str, tail: str = "") -> str:
    return f"SELECT\n{select}\nFROM analytics.{table}\nWHERE {MONITORING_FILTER}\n{tail}"


# name -> (description, display, sql, template tags used, visualization settings)
QUESTIONS: dict[str, tuple[str, str, str, tuple[str, ...], dict]] = {
    # ------------------------------------------------------------------- Overview
    AVAILABLE: (
        "The period the loaded data covers and when it was collected. Nothing here updates on its own.",
        "table",
        """
SELECT
  CONCAT(
    FORMAT_DATETIME('%e %b %Y', MIN(DATETIME(valid_at, 'Europe/Paris'))), ' to ',
    FORMAT_DATETIME('%e %b %Y', MAX(DATETIME(valid_at, 'Europe/Paris')))
  ) AS period,
  FORMAT_TIMESTAMP('%e %b %Y, %H:%M UTC', MAX(ingested_at)) AS collected,
  CONCAT(CAST(ROUND(TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(ingested_at), HOUR) / 24, 1) AS STRING), ' days ago') AS age,
  COUNT(DISTINCT station_id) AS stations
FROM analytics.stg_forecasts
""",
        (),
        columns(
            column("period", column_title="Period covered (local dates)"),
            column("collected", column_title="Data last collected"),
            column("age", column_title="How long ago"),
            column("stations", column_title="Stations"),
        ),
    ),
    # ---------------------------------------------------------------- Reliability
    SELECTION: (
        "What the filters select, next to the data that is available. Dates and hours are local time.",
        "table",
        f"""
WITH selection AS (
  SELECT
    COUNT(*) AS station_hours,
    COUNT(DISTINCT station_id) AS stations,
    MIN(valid_date_local) AS first_day,
    MAX(valid_date_local) AS last_day,
    MIN(valid_hour_local) AS first_hour,
    MAX(valid_hour_local) AS last_hour
  FROM {STATION_HOURS}
  WHERE {PERIOD}
),
loaded AS (
  SELECT
    MIN(DATE(valid_at, 'Europe/Paris')) AS first_day,
    MAX(DATE(valid_at, 'Europe/Paris')) AS last_day,
    MAX(ingested_at) AS collected_at
  FROM analytics.stg_forecasts
)
SELECT
  IF(selection.station_hours = 0, 'No data for this selection', 'Data available') AS status,
  IF(selection.station_hours = 0, 'none', CONCAT(CAST(selection.first_day AS STRING), ' to ', CAST(selection.last_day AS STRING))) AS selected_days,
  IF(selection.station_hours = 0, 'none', FORMAT('%02d:00 to %02d:00', selection.first_hour, selection.last_hour)) AS selected_hours,
  selection.stations AS stations_selected,
  CONCAT(CAST(loaded.first_day AS STRING), ' to ', CAST(loaded.last_day AS STRING)) AS loaded_days,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M UTC', loaded.collected_at) AS data_collected
FROM selection CROSS JOIN loaded
""",
        RELIABILITY,
        {
            **columns(
                column("status", column_title="Status"),
                column("selected_days", column_title="Days selected"),
                column("selected_hours", column_title="Hours of day selected"),
                column("stations_selected", column_title="Stations selected"),
                column("loaded_days", column_title="Data available for"),
                column("data_collected", column_title="Data last collected"),
            ),
            "table.column_formatting": [
                {
                    "columns": ["status"],
                    "type": "single",
                    "operator": "starts-with",
                    "value": "No data",
                    "color": HIGHLIGHT,
                    "highlight_row": True,
                }
            ],
        },
    ),
    MAE_BY_LEAD: (
        "Average size of the forecast error, in °C, for each lead time. Lower is better. "
        "Each valid comparison has equal weight.",
        "bar",
        LEAD_METRICS_SQL,
        RELIABILITY,
        {
            "graph.dimensions": ["lead_time"],
            "graph.metrics": ["mean_absolute_error_c"],
            "graph.show_values": True,
            "graph.x_axis.title_text": "Lead time: days between the forecast and the target hour",
            "graph.y_axis.title_text": "Mean absolute error (°C)",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": 0,
            "graph.y_axis.max": 4,
            "series_settings": {"mean_absolute_error_c": {"color": BLUE}},
            **columns(column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES)),
        },
    ),
    COVERAGE: (
        "Share of the selected past hours for which the station had a reading to compare with the forecast "
        "(at the target hour or 15 minutes later). Future hours are not counted.",
        "scalar",
        coverage_scalar(
            "SAFE_DIVIDE(COUNTIF(observation_status = 'observed'), COUNTIF(observation_status != 'future_at_ingestion'))",
            hide_when_empty=True,
        ),
        RELIABILITY,
        columns(column("value", **PERCENT)),
    ),
    COMPARISONS: (
        "Number of forecast-observation pairs behind each lead time's score: one per station and hour "
        "that has an observation.",
        "scalar",
        coverage_scalar("COUNTIF(observation_status = 'observed')"),
        RELIABILITY,
        {},
    ),
    MISSING: (
        "Selected past hours where the station had no usable reading, so no error could be computed.",
        "scalar",
        coverage_scalar("COUNTIF(observation_status = 'missing_observation')"),
        RELIABILITY,
        {},
    ),
    FUTURE: (
        "Selected hours that had not happened yet when the data was collected. "
        "They are left out of the coverage.",
        "scalar",
        coverage_scalar("COUNTIF(observation_status = 'future_at_ingestion')"),
        RELIABILITY,
        {},
    ),
    TIME_SERIES: (
        "Hourly mean over the selected stations, for the lead chosen in the 'Lead in line chart' filter. "
        "Only hours where a station has both values are averaged, so the two lines cover the same stations. "
        "A break in the lines means no comparable hour, not a temperature of zero.",
        "line",
        f"""
SELECT
  valid_at_local AS target_hour_local,
  ROUND(AVG(observed_temperature_c), 2) AS observed_c,
  ROUND(AVG(forecast_temperature_c), 2) AS forecast_c
FROM {COMPARISON}
WHERE lead_days = {{{{lead}}}}
  AND error_c IS NOT NULL
  AND {PERIOD}
GROUP BY valid_at_local
ORDER BY valid_at_local
""",
        (*RELIABILITY, "lead"),
        {
            "graph.dimensions": ["target_hour_local"],
            "graph.metrics": ["observed_c", "forecast_c"],
            "graph.x_axis.title_text": "Target hour (local time)",
            "graph.y_axis.title_text": "Temperature (°C)",
            "series_settings": {
                "observed_c": {"color": ORANGE, "title": "Observed (measured by the stations)", "line.missing": "none", "line.marker_enabled": True},
                "forecast_c": {"color": BLUE, "title": "Forecast, at the lead chosen in the filter", "line.missing": "none", "line.marker_enabled": True},
            },
            **columns(
                column("target_hour_local", column_title="Target hour (local time)", **DATE_COLUMN),
                column("observed_c", column_title="Observed", **DEGREES),
                column("forecast_c", column_title="Forecast", **DEGREES),
            ),
        },
    ),
    BIAS_BY_LEAD: (
        "Average error with its direction kept (forecast minus observation), in °C. "
        "Above zero: forecasts were too warm on average. Below zero: too cold.",
        "bar",
        LEAD_METRICS_SQL,
        RELIABILITY,
        {
            "graph.dimensions": ["lead_time"],
            "graph.metrics": ["mean_signed_error_c"],
            "graph.show_values": True,
            "graph.show_goal": True,
            "graph.goal_value": 0,
            "graph.goal_label": "0 = no bias",
            "graph.x_axis.title_text": "Lead time: days between the forecast and the target hour",
            "graph.y_axis.title_text": "Mean signed error (°C): above 0 = too warm",
            "series_settings": {"mean_signed_error_c": {"color": BLUE}},
            **columns(column("mean_signed_error_c", column_title="Mean signed error", **DEGREES)),
        },
    ),
    SCORES_BY_LEAD: (
        "The numbers behind the two charts, with the number of valid comparisons each score rests on.",
        "table",
        LEAD_METRICS_SQL,
        RELIABILITY,
        columns(
            column("lead_time", column_title="Lead time"),
            column("valid_comparisons", column_title="Valid comparisons"),
            column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES),
            column("mean_signed_error_c", column_title="Bias (mean signed error)", **DEGREES),
        ),
    ),
    SCORES_BY_STATION: (
        "Per station, for the selection. A blank score means the station had no observation to compare, "
        "not an error of zero. Hours are counted on the lead 1 rows, one per station and target hour.",
        "table",
        f"""
SELECT
  s.station_label AS station,
  COUNTIF(c.lead_days = 1 AND c.observation_status = 'observed') AS comparable_hours,
  SAFE_DIVIDE(
    COUNTIF(c.lead_days = 1 AND c.observation_status = 'observed'),
    COUNTIF(c.lead_days = 1 AND c.observation_status != 'future_at_ingestion')
  ) AS coverage_rate,
  ROUND(AVG(c.absolute_error_c), 3) AS mean_absolute_error_c,
  ROUND(AVG(c.error_c), 3) AS mean_signed_error_c,
  ROUND(AVG(IF(c.lead_days = 1, c.absolute_error_c, NULL)), 3) AS j1_mae_c,
  ROUND(AVG(IF(c.lead_days = 7, c.absolute_error_c, NULL)), 3) AS j7_mae_c
FROM (
  -- The field filters need the table unaliased, hence the subquery.
  SELECT *
  FROM {COMPARISON}
  WHERE forecast_kind = 'forecast'
    AND {PERIOD}
) AS c
JOIN analytics.stg_stations AS s ON s.station_id = c.station_id
GROUP BY s.station_label
ORDER BY s.station_label
""",
        RELIABILITY,
        columns(
            column("station", column_title="Station"),
            column("comparable_hours", column_title="Hours compared", show_mini_bar=True),
            column("coverage_rate", column_title="Comparison coverage", **PERCENT),
            column("mean_absolute_error_c", column_title="Mean abs. error, J1-J7", **DEGREES),
            column("mean_signed_error_c", column_title="Bias, J1-J7", **DEGREES),
            column("j1_mae_c", column_title="Mean abs. error, J1", **DEGREES),
            column("j7_mae_c", column_title="Mean abs. error, J7", **DEGREES),
        ),
    ),
    # ---------------------------------------------------------- Station monitoring
    SNAPSHOT: (
        "When the observations were collected. The monitoring figures describe the stations at that time; "
        "they are not live.",
        "table",
        """
SELECT
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M UTC', MAX(collected_at)) AS data_collected,
  CONCAT(
    CAST(ROUND(TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(collected_at), HOUR) / 24, 1) AS STRING),
    ' days ago'
  ) AS snapshot_age,
  IF(
    TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(collected_at), HOUR) > 2,
    'Old snapshot, not live monitoring. Statuses describe the stations at collection time.',
    'Recent snapshot'
  ) AS note
FROM analytics.station_monitoring
""",
        (),
        {
            **columns(
                column("data_collected", column_title="Data last collected"),
                column("snapshot_age", column_title="How long ago"),
                column("note", column_title="Note"),
            ),
            "table.column_formatting": [
                {
                    "columns": ["note"],
                    "type": "single",
                    "operator": "starts-with",
                    "value": "Old snapshot",
                    "color": HIGHLIGHT,
                    "highlight_row": True,
                }
            ],
        },
    ),
    TO_CHECK: (
        "Number of stations, among those selected, marked 'Check station' at collection time.",
        "scalar",
        monitoring_sql("  COUNTIF(needs_check) AS value", "station_monitoring"),
        MONITORING,
        {},
    ),
    STATIONS_OK: (
        "Number of stations, among those selected, marked 'OK' at collection time.",
        "scalar",
        monitoring_sql("  COUNTIF(NOT needs_check) AS value", "station_monitoring"),
        MONITORING,
        {},
    ),
    STATUS_TABLE: (
        "Most severe first. The 'Why' column gives the measured value next to its limit.",
        "table",
        monitoring_sql(
            """  station_label AS station,
  status,
  status_reason,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', latest_valid_reading_at) AS latest_valid_reading_utc,
  hours_since_latest_valid_reading,
  coverage_rate_24h,
  coverage_rate_7d,
  flagged_readings_24h,
  flagged_readings_7d""",
            "station_monitoring",
            "ORDER BY status_rank, hours_since_latest_valid_reading DESC, station_label",
        ),
        MONITORING,
        {
            **columns(
                column("station", column_title="Station"),
                column("status", column_title="Status"),
                column("status_reason", column_title="Why"),
                column("latest_valid_reading_utc", column_title="Last valid reading (UTC)"),
                column("hours_since_latest_valid_reading", column_title="Age of last reading (h)", decimals=1),
                column("coverage_rate_24h", column_title="Reporting, 24 h", number_style="percent", decimals=0),
                column("coverage_rate_7d", column_title="Reporting, 7 d", number_style="percent", decimals=0),
                column("flagged_readings_24h", column_title="Flagged, 24 h"),
                column("flagged_readings_7d", column_title="Flagged, 7 d"),
            ),
            "table.column_formatting": [
                {
                    "columns": ["status"],
                    "type": "single",
                    "operator": "=",
                    "value": "Check station",
                    "color": HIGHLIGHT,
                    "highlight_row": False,
                }
            ],
        },
    ),
    REPORTING_7D: (
        "15-minute readings received divided by the 672 expected in 7 days, per station.",
        "row",
        monitoring_sql(
            "  station_label AS station,\n  coverage_rate_7d",
            "station_monitoring",
            "ORDER BY station_label",
        ),
        MONITORING,
        {
            "graph.dimensions": ["station"],
            "graph.metrics": ["coverage_rate_7d"],
            "graph.show_values": True,
            "graph.x_axis.title_text": "Station",
            "graph.y_axis.title_text": "Share of expected 15-minute readings received",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": 0,
            "graph.y_axis.max": 1,
            "series_settings": {"coverage_rate_7d": {"color": BLUE}},
            **columns(column("coverage_rate_7d", column_title="Reporting, 7 d", number_style="percent", decimals=0)),
        },
    ),
    REPORTING_DAILY: (
        "Reporting history: 15-minute readings received divided by readings expected, per UTC day, over the "
        "selected stations. Select one station to see its own history. A bar at 0% is a day with no reading.",
        "bar",
        monitoring_sql(
            "  day,\n  SAFE_DIVIDE(SUM(valid_readings), SUM(expected_readings)) AS daily_coverage_rate",
            "station_daily_observations",
            "GROUP BY day\nORDER BY day",
        ),
        MONITORING,
        {
            "graph.dimensions": ["day"],
            "graph.metrics": ["daily_coverage_rate"],
            "graph.x_axis.title_text": "Day (UTC)",
            "graph.y_axis.title_text": "Share of expected readings received",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": 0,
            "graph.y_axis.max": 1,
            "series_settings": {"daily_coverage_rate": {"color": BLUE}},
            **columns(
                column("day", column_title="Day (UTC)", date_abbreviate=True),
                column("daily_coverage_rate", column_title="Reporting coverage", number_style="percent", decimals=0),
            ),
        },
    ),
    FLAGGED_DAILY: (
        "Readings of exactly -50 °C per UTC day, over the selected stations. These are removed before any "
        "comparison. A day with no bar has none.",
        "bar",
        monitoring_sql(
            "  day,\n  SUM(flagged_readings) AS flagged_readings",
            "station_daily_observations",
            "GROUP BY day\nORDER BY day",
        ),
        MONITORING,
        {
            "graph.dimensions": ["day"],
            "graph.metrics": ["flagged_readings"],
            "graph.x_axis.title_text": "Day (UTC)",
            "graph.y_axis.title_text": "Number of flagged readings",
            "series_settings": {"flagged_readings": {"color": ORANGE}},
            **columns(
                column("day", column_title="Day (UTC)", date_abbreviate=True),
                column("flagged_readings", column_title="Flagged readings"),
            ),
        },
    ),
    GAPS: (
        "Periods of 1 hour or more with no valid reading, most recent first. "
        "'Still ongoing' means the station had not reported again when the data was collected.",
        "table",
        monitoring_sql(
            """  station_label AS station,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', gap_start) AS first_missing_slot_utc,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', gap_end) AS last_missing_slot_utc,
  gap_hours,
  IF(is_ongoing, 'Still ongoing at collection', 'Ended') AS state""",
            "station_observation_gaps",
            "  AND missing_readings >= 4\nORDER BY gap_end DESC, station_label\nLIMIT 500",
        ),
        MONITORING,
        columns(
            column("station", column_title="Station"),
            column("first_missing_slot_utc", column_title="From (UTC)"),
            column("last_missing_slot_utc", column_title="To (UTC)"),
            column("gap_hours", column_title="Length (h)", decimals=1, show_mini_bar=True),
            column("state", column_title="State"),
        ),
    ),
}

OVERVIEW_INTRO = """\
## Toulouse Data
Compares **Open-Meteo temperature forecasts** with **temperatures measured by Toulouse Métropole weather stations**, \
hour by hour, at 10 stations. A forecast's **error** is the forecast minus the measured temperature, in °C.

The data is a snapshot loaded by hand, not a live feed. The table below shows what it covers."""

RELIABILITY_HELP = """\
**How to read this tab**

- **Target hour**: the hour a forecast is for. Dates and hours are Toulouse local time.
- **Lead time (J1 to J7)**: how many days before the target hour the forecast was made. J1 is the day before.
- **Error** = forecast − observed. *Example: forecast 18 °C, observed 16 °C → error **+2 °C**, the forecast was too warm.*
- **Mean absolute error**: average size of the error, direction ignored. Lower is better.
- **Bias (mean signed error)**: average error with its direction. Above 0 = too warm, below 0 = too cold.
- **Valid comparisons**: number of forecast–observation pairs behind a score. A score from a handful of pairs says little.
- **Comparison coverage**: share of past hours with an observation to compare.

**Filters**: Target date, From hour, To hour and Station apply to every card on this tab. \
*Lead in line chart* only changes the line chart. From hour 14 and To hour 18 keep target hours 14:00 to 18:00 inclusive."""

MONITORING_HELP = """\
**Check station** means, when the data was collected, the first of: no valid reading in 24 h · last valid reading \
more than 2 h old · a flagged reading (exactly −50 °C) in 24 h · under 80% of expected readings in 24 h. \
The **Why** column gives the measured value.

**Age of last reading** = hours between the station's last valid reading and the collection. \
**Reporting coverage** here = 15-minute readings received ÷ readings expected (96 a day). It is not the \
*comparison coverage* of the Reliability tab, which counts hours with a reading usable against a forecast."""


def overview_links(base: str, tabs: dict[str, int]) -> list[str]:
    reliability = f"{base}?tab={tabs['Reliability']}"
    monitoring = f"{base}?tab={tabs['Station monitoring']}"
    return [
        f"""\
### [Reliability →]({reliability})
How accurate were the forecasts over a period, and how does accuracy drop with lead time?

**Example:** [afternoons (14:00–18:00) of 10–12 November]({reliability}&target_date=2026-11-10~2026-11-12&hour_from=14&hour_to=18)""",
        f"""\
### [Map →]({base}?tab={tabs['Map']})
Forecast and measured temperature at one hour, station by station.

**Example:** choose 18 November, 16:00 and lead J7, then switch Display to *Error vs observed*.""",
        f"""\
### [Station monitoring →]({monitoring})
Which stations had stopped reporting or had gaps when the data was collected?

**Example:** [show only stations marked "Check station"]({monitoring}&status=Check%20station)""",
    ]


def layout(base: str, tabs: dict[str, int]) -> dict[str, list[tuple]]:
    """tab name -> [(row, col, width, height, question name | text | iframe url, kind)]"""
    links = overview_links(base, tabs)
    return {
        "Overview": [
            (0, 0, 24, 3, OVERVIEW_INTRO, "text"),
            (3, 0, 24, 3, AVAILABLE, None),
            (6, 0, 8, 4, links[0], "text"),
            (6, 8, 8, 4, links[1], "text"),
            (6, 16, 8, 4, links[2], "text"),
        ],
        "Reliability": [
            (0, 0, 24, 3, SELECTION, None),
            (3, 0, 11, 9, MAE_BY_LEAD, None),
            (3, 11, 4, 2, COVERAGE, None),
            (5, 11, 4, 2, COMPARISONS, None),
            (7, 11, 4, 2, MISSING, None),
            (9, 11, 4, 3, FUTURE, None),
            (3, 15, 9, 9, RELIABILITY_HELP, "text"),
            (12, 0, 24, 8, TIME_SERIES, None),
            (20, 0, 12, 7, BIAS_BY_LEAD, None),
            (20, 12, 12, 7, SCORES_BY_LEAD, None),
            (27, 0, 24, 11, SCORES_BY_STATION, None),
        ],
        "Map": [
            (0, 0, 24, 20, MAP_URL, "iframe"),
        ],
        "Station monitoring": [
            (0, 0, 16, 3, SNAPSHOT, None),
            (0, 16, 4, 3, TO_CHECK, None),
            (0, 20, 4, 3, STATIONS_OK, None),
            (3, 0, 24, 3, MONITORING_HELP, "text"),
            (6, 0, 24, 9, STATUS_TABLE, None),
            (15, 0, 12, 9, REPORTING_7D, None),
            (15, 12, 12, 9, REPORTING_DAILY, None),
            (24, 0, 12, 10, GAPS, None),
            (24, 12, 12, 10, FLAGGED_DAILY, None),
        ],
    }


TAB_NAMES = ["Overview", "Reliability", "Map", "Station monitoring"]

PARAMETERS = [
    {"id": DATE_PARAMETER_ID, "name": "Target date", "slug": "target_date", "type": "date/all-options", "sectionId": "date"},
    {"id": HOUR_FROM_PARAMETER_ID, "name": "From hour (0-23, local)", "slug": "hour_from", "type": "number/>=", "sectionId": "number"},
    {"id": HOUR_TO_PARAMETER_ID, "name": "To hour (0-23, local)", "slug": "hour_to", "type": "number/<=", "sectionId": "number"},
    {"id": STATION_PARAMETER_ID, "name": "Station", "slug": "station", "type": "string/=", "sectionId": "string"},
    {
        "id": LEAD_PARAMETER_ID,
        "name": "Lead in line chart (J)",
        "slug": "lead",
        "type": "number/=",
        "sectionId": "number",
        "default": [1],
        "required": True,
        "isMultiSelect": False,
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": [1, 2, 3, 4, 5, 6, 7]},
    },
    {
        "id": STATUS_PARAMETER_ID,
        "name": "Status",
        "slug": "status",
        "type": "string/=",
        "sectionId": "string",
        "isMultiSelect": False,
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": ["Check station", "OK"]},
    },
]

TARGETS = {
    "station": (STATION_PARAMETER_ID, ["dimension", ["template-tag", "station"]]),
    "target_date": (DATE_PARAMETER_ID, ["dimension", ["template-tag", "target_date"]]),
    "hour_from": (HOUR_FROM_PARAMETER_ID, ["dimension", ["template-tag", "hour_from"]]),
    "hour_to": (HOUR_TO_PARAMETER_ID, ["dimension", ["template-tag", "hour_to"]]),
    "lead": (LEAD_PARAMETER_ID, ["variable", ["template-tag", "lead"]]),
    "status": (STATUS_PARAMETER_ID, ["variable", ["template-tag", "status"]]),
}


def template_tags(used: tuple[str, ...], fields: dict[str, int], table: str) -> dict:
    def tag(name: str, display: str, **extra) -> dict:
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"toulouse-data-v2/{name}/{table}")),
            "name": name,
            "display-name": display,
            **extra,
        }

    available = {
        "station": lambda: tag("station", "Station", type="dimension", dimension=["field", fields["stg_stations.station_label"], None], **{"widget-type": "string/="}),
        "target_date": lambda: tag("target_date", "Target date", type="dimension", dimension=["field", fields[f"{table}.valid_date_local"], None], **{"widget-type": "date/all-options"}),
        "hour_from": lambda: tag("hour_from", "From hour", type="dimension", dimension=["field", fields[f"{table}.valid_hour_local"], None], **{"widget-type": "number/>="}),
        "hour_to": lambda: tag("hour_to", "To hour", type="dimension", dimension=["field", fields[f"{table}.valid_hour_local"], None], **{"widget-type": "number/<="}),
        "lead": lambda: tag("lead", "Lead", type="number", default="1", required=True),
        "status": lambda: tag("status", "Status", type="text"),
    }
    return {name: available[name]() for name in used}


def allow_map_iframe(mb: Metabase) -> None:
    """Add the local map server to the hosts Metabase may show in an iframe."""
    setting = next(s for s in mb.get("setting") if s["key"] == "allowed-iframe-hosts")
    hosts = setting.get("value") or setting.get("default") or ""
    if MAP_HOST not in hosts:
        mb.put("setting/allowed-iframe-hosts", {"value": f"{hosts}\n{MAP_HOST}".strip()})


def dashcards_for(tabs: dict[str, int], dashboard_id: int, card_ids: dict[str, int]) -> list[dict]:
    dashcards = []
    for tab, cards in layout(f"/dashboard/{dashboard_id}", tabs).items():
        for row, col, width, height, content, kind in cards:
            dashcard = {
                "id": -(len(dashcards) + 1),
                "dashboard_tab_id": tabs[tab],
                "row": row,
                "col": col,
                "size_x": width,
                "size_y": height,
                "parameter_mappings": [],
            }
            if kind in ("text", "iframe"):
                dashcard["card_id"] = None
                dashcard["visualization_settings"] = {
                    "virtual_card": {
                        "name": None,
                        "display": kind,
                        "visualization_settings": {},
                        "dataset_query": {},
                        "archived": False,
                    },
                    kind: content,
                }
            else:
                dashcard["card_id"] = card_ids[content]
                dashcard["parameter_mappings"] = [
                    {"parameter_id": TARGETS[tag][0], "card_id": card_ids[content], "target": TARGETS[tag][1]}
                    for tag in QUESTIONS[content][3]
                ]
            dashcards.append(dashcard)
    return dashcards


def main() -> None:
    mb = Metabase().login()

    database = next(d for d in mb.get("database")["data"] if d["name"] == DATABASE_NAME)
    tables = {t["name"]: t for t in mb.get(f"database/{database['id']}/metadata")["tables"]}
    fields = {
        f"{table}.{name}": next(f["id"] for f in tables[table]["fields"] if f["name"] == name)
        for table, name in [
            ("stg_stations", "station_label"),
            ("forecast_comparison", "valid_date_local"),
            ("forecast_comparison", "valid_hour_local"),
            ("int_station_hour_observations", "valid_date_local"),
            ("int_station_hour_observations", "valid_hour_local"),
        ]
    }

    collection = find_or_create(mb, "collection", COLLECTION, None, {"name": COLLECTION})
    allow_map_iframe(mb)

    card_ids = {}
    for name, (description, display, sql, used, visualization) in QUESTIONS.items():
        table = "int_station_hour_observations" if f"FROM {STATION_HOURS}" in sql else "forecast_comparison"
        card = find_or_create(
            mb,
            "card",
            name,
            collection["id"],
            {
                "name": name,
                "description": description,
                "display": display,
                "collection_id": collection["id"],
                "visualization_settings": visualization,
                "dataset_query": {
                    "database": database["id"],
                    "type": "native",
                    "native": {"query": sql.strip(), "template-tags": template_tags(used, fields, table)},
                },
            },
        )
        card_ids[name] = card["id"]

    for item in mb.get(f"collection/{collection['id']}/items", models="card")["data"]:
        if item["name"] not in QUESTIONS:
            mb.put(f"card/{item['id']}", {"archived": True})
            print(f"archived old question: {item['name']}")

    dashboard = find_or_create(
        mb,
        "dashboard",
        DASHBOARD,
        collection["id"],
        {
            "name": DASHBOARD,
            "description": "Forecast reliability over a selected period, temperature map and station monitoring.",
            "collection_id": collection["id"],
        },
    )

    # The Overview links need the tab ids, which exist only once the tabs
    # are saved. So save twice: first to create the tabs, then with the links.
    for _ in range(2):
        existing = {t["name"]: t["id"] for t in mb.get(f"dashboard/{dashboard['id']}").get("tabs", [])}
        tabs = {name: existing.get(name, -(i + 1)) for i, name in enumerate(TAB_NAMES)}
        mb.put(
            f"dashboard/{dashboard['id']}",
            {
                "width": "full",
                "parameters": PARAMETERS,
                "tabs": [{"id": tabs[name], "name": name} for name in TAB_NAMES],
                "dashcards": dashcards_for(tabs, dashboard["id"], card_ids),
            },
        )
        if all(name in existing for name in TAB_NAMES):
            break

    dashboard = mb.get(f"dashboard/{dashboard['id']}")
    print(f"{len(card_ids)} questions in collection '{COLLECTION}'")
    for tab in dashboard["tabs"]:
        print(f"{tab['name']}: {METABASE_URL}/dashboard/{dashboard['id']}?tab={tab['id']}")


if __name__ == "__main__":
    main()
