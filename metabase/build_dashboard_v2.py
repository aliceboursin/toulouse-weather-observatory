"""Create or update the V2 dashboard (three tabs) in the local Metabase.

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
STATION_PARAMETER_ID = "station02"
LEAD_PARAMETER_ID = "lead02"

ORANGE = "#eb6834"

# Field filters. {{target_date}} filters on the local date of the target hour
# and {{station}} on the station label; with no selection each becomes "1 = 1".
# BigQuery field filters need the table written as `dataset.table`, unaliased.
COMPARISON = "`analytics.forecast_comparison`"
STATION_HOURS = "`analytics.int_station_hour_observations`"
PERIOD = f"{{{{target_date}}}}\n  AND {STATION_FILTER}"

LEAD_METRICS_SQL = f"""
-- Selected period and stations. AVG runs over individual comparisons, so each
-- valid comparison has equal weight. Leads 1 to 7 only.
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


def coverage_scalar(expression: str, hide_when_empty: bool = False) -> str:
    having = "HAVING COUNT(*) > 0\n" if hide_when_empty else ""
    return f"SELECT {expression} AS value\nFROM {STATION_HOURS}\nWHERE {PERIOD}\n{having}"


def monitoring_sql(select: str, table: str, tail: str = "") -> str:
    return f"SELECT\n{select}\nFROM analytics.{table}\nWHERE {STATION_FILTER}\n{tail}"


# name -> (description, display, sql, template tags used, visualization settings)
QUESTIONS: dict[str, tuple[str, str, str, tuple[str, ...], dict]] = {
    # ---------------------------------------------------------------- Reliability
    "Selection and loaded data": (
        "What the filters select, next to the data that is loaded. Dates are local dates of the target hour.",
        "table",
        f"""
WITH selection AS (
  SELECT
    COUNT(*) AS station_hours,
    COUNT(DISTINCT station_id) AS stations,
    MIN(valid_date_local) AS first_day,
    MAX(valid_date_local) AS last_day
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
  selection.stations AS stations_selected,
  CONCAT(CAST(loaded.first_day AS STRING), ' to ', CAST(loaded.last_day AS STRING)) AS loaded_days,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M UTC', loaded.collected_at) AS data_collected
FROM selection CROSS JOIN loaded
""",
        ("target_date", "station"),
        {
            **columns(
                column("status", column_title="Status"),
                column("selected_days", column_title="Selected days with data"),
                column("stations_selected", column_title="Stations selected"),
                column("loaded_days", column_title="Loaded data range"),
                column("data_collected", column_title="Data collected"),
            ),
            "table.column_formatting": [
                {
                    "columns": ["status"],
                    "type": "single",
                    "operator": "starts-with",
                    "value": "No data",
                    "color": "#fab219",
                    "highlight_row": True,
                }
            ],
        },
    ),
    "Mean absolute error by lead time (J1-J7)": (
        "Mean absolute error per lead time for the selected period and stations. Each valid comparison has equal weight.",
        "bar",
        LEAD_METRICS_SQL,
        ("target_date", "station"),
        {
            "graph.dimensions": ["lead_time"],
            "graph.metrics": ["mean_absolute_error_c"],
            "graph.show_values": True,
            "graph.x_axis.title_text": "Lead time (days ahead)",
            "graph.y_axis.title_text": "Mean absolute error (°C)",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": 0,
            "graph.y_axis.max": 4,
            "series_settings": {"mean_absolute_error_c": {"color": BLUE}},
            **columns(column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES)),
        },
    ),
    "Coverage rate": (
        "Comparable station-hours divided by non-future station-hours, for the selected period and stations.",
        "scalar",
        coverage_scalar(
            "SAFE_DIVIDE(COUNTIF(observation_status = 'observed'), COUNTIF(observation_status != 'future_at_ingestion'))",
            hide_when_empty=True,
        ),
        ("target_date", "station"),
        columns(column("value", **PERCENT)),
    ),
    "Valid comparisons per lead": (
        "Comparable station-hours in the selection. Each one gives one valid comparison per lead time.",
        "scalar",
        coverage_scalar("COUNTIF(observation_status = 'observed')"),
        ("target_date", "station"),
        {},
    ),
    "Missing hours": (
        "Non-future station-hours with no usable observation, in the selection.",
        "scalar",
        coverage_scalar("COUNTIF(observation_status = 'missing_observation')"),
        ("target_date", "station"),
        {},
    ),
    "Future hours (excluded)": (
        "Station-hours after the forecast ingestion time, in the selection. Not in the coverage rate.",
        "scalar",
        coverage_scalar("COUNTIF(observation_status = 'future_at_ingestion')"),
        ("target_date", "station"),
        {},
    ),
    "Forecast vs observed temperature": (
        "Hourly mean over the selected stations, at the lead chosen in the 'Time-series lead' filter. "
        "Only hours where a station has both values are averaged, so the two lines cover the same stations.",
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
        ("target_date", "station", "lead"),
        {
            "graph.dimensions": ["target_hour_local"],
            "graph.metrics": ["observed_c", "forecast_c"],
            "graph.x_axis.title_text": "Target hour (local time)",
            "graph.y_axis.title_text": "Temperature (°C)",
            "series_settings": {
                "observed_c": {"color": ORANGE, "title": "Observed"},
                "forecast_c": {"color": BLUE, "title": "Forecast at the selected lead"},
            },
            **columns(
                column("observed_c", column_title="Observed", **DEGREES),
                column("forecast_c", column_title="Forecast at the selected lead", **DEGREES),
            ),
        },
    ),
    "Signed bias by lead time (J1-J7)": (
        "Mean signed error (forecast minus observation) per lead time, for the selection. "
        "Above zero: forecasts too warm. Below zero: too cold.",
        "bar",
        LEAD_METRICS_SQL,
        ("target_date", "station"),
        {
            "graph.dimensions": ["lead_time"],
            "graph.metrics": ["mean_signed_error_c"],
            "graph.show_values": True,
            "graph.show_goal": True,
            "graph.goal_value": 0,
            "graph.goal_label": "No bias",
            "graph.x_axis.title_text": "Lead time (days ahead)",
            "graph.y_axis.title_text": "Mean signed error (°C)",
            "series_settings": {"mean_signed_error_c": {"color": BLUE}},
            **columns(column("mean_signed_error_c", column_title="Mean signed error", **DEGREES)),
        },
    ),
    "Scores by lead time (selection)": (
        "Valid comparison count, mean absolute error and mean signed error per lead time, for the selection.",
        "table",
        LEAD_METRICS_SQL,
        ("target_date", "station"),
        columns(
            column("lead_time", column_title="Lead time"),
            column("valid_comparisons", column_title="Valid comparisons"),
            column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES),
            column("mean_signed_error_c", column_title="Mean signed error", **DEGREES),
        ),
    ),
    "Scores and coverage by station (selection)": (
        "Per station, for the selected period: comparable hours, coverage rate, and J1-J7 pooled error. "
        "Hours are counted on the lead 1 rows, one per station and target hour.",
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
  -- The date field filter needs the table unaliased, hence the subquery.
  SELECT *
  FROM {COMPARISON}
  WHERE forecast_kind = 'forecast'
    AND {PERIOD}
) AS c
JOIN analytics.stg_stations AS s ON s.station_id = c.station_id
GROUP BY s.station_label
ORDER BY s.station_label
""",
        ("target_date", "station"),
        columns(
            column("station", column_title="Station"),
            column("comparable_hours", column_title="Comparable hours", show_mini_bar=True),
            column("coverage_rate", column_title="Coverage rate", **PERCENT),
            column("mean_absolute_error_c", column_title="MAE J1-J7", **DEGREES),
            column("mean_signed_error_c", column_title="Bias J1-J7", **DEGREES),
            column("j1_mae_c", column_title="MAE J1", **DEGREES),
            column("j7_mae_c", column_title="MAE J7", **DEGREES),
        ),
    ),
    # ---------------------------------------------------------- Station monitoring
    "Snapshot": (
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
                column("data_collected", column_title="Data collected"),
                column("snapshot_age", column_title="Snapshot age"),
                column("note", column_title="Note"),
            ),
            "table.column_formatting": [
                {
                    "columns": ["note"],
                    "type": "single",
                    "operator": "starts-with",
                    "value": "Old snapshot",
                    "color": "#fab219",
                    "highlight_row": True,
                }
            ],
        },
    ),
    "Stations to check": (
        "Number of selected stations whose status is not OK at collection time.",
        "scalar",
        monitoring_sql("  COUNTIF(needs_check) AS value", "station_monitoring"),
        ("station",),
        {},
    ),
    "Stations OK": (
        "Number of selected stations with status OK at collection time.",
        "scalar",
        monitoring_sql("  COUNTIF(NOT needs_check) AS value", "station_monitoring"),
        ("station",),
        {},
    ),
    "Station status at collection time": (
        "Most severe first. 'Check station' when: no valid reading in 24 h; latest valid reading older than 2 h; "
        "a flagged (-50 °C) reading in 24 h; or 24 h coverage below 80%.",
        "table",
        monitoring_sql(
            """  station_label AS station,
  status,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', latest_valid_reading_at) AS latest_valid_reading_utc,
  hours_since_latest_valid_reading,
  latest_valid_temperature_c,
  coverage_rate_24h,
  coverage_rate_7d,
  flagged_readings_24h,
  flagged_readings_7d,
  longest_gap_hours""",
            "station_monitoring",
            "ORDER BY status_rank, hours_since_latest_valid_reading DESC, station_label",
        ),
        ("station",),
        {
            **columns(
                column("station", column_title="Station"),
                column("status", column_title="Status"),
                column("latest_valid_reading_utc", column_title="Latest valid reading (UTC)"),
                column("hours_since_latest_valid_reading", column_title="Age at collection (h)", decimals=1),
                column("latest_valid_temperature_c", column_title="Last temp.", suffix=" °C", decimals=1),
                column("coverage_rate_24h", column_title="Cov. 24 h", show_mini_bar=True, number_style="percent", decimals=0),
                column("coverage_rate_7d", column_title="Cov. 7 d", show_mini_bar=True, number_style="percent", decimals=0),
                column("flagged_readings_24h", column_title="Flagged 24 h"),
                column("flagged_readings_7d", column_title="Flagged 7 d"),
                column("longest_gap_hours", column_title="Max gap (h)", decimals=1),
            ),
            "table.column_formatting": [
                {
                    "columns": ["status"],
                    "type": "single",
                    "operator": "starts-with",
                    "value": "Check station",
                    "color": "#fab219",
                    "highlight_row": False,
                }
            ],
        },
    ),
    "Coverage in the 7 days before collection": (
        "Valid readings divided by the 672 expected in 7 days, per station.",
        "row",
        monitoring_sql(
            "  station_label AS station,\n  coverage_rate_7d",
            "station_monitoring",
            "ORDER BY station_label",
        ),
        ("station",),
        {
            "graph.dimensions": ["station"],
            "graph.metrics": ["coverage_rate_7d"],
            "graph.show_values": True,
            "graph.x_axis.title_text": "Station",
            "graph.y_axis.title_text": "Coverage, last 7 days before collection",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": 0,
            "graph.y_axis.max": 1,
            "series_settings": {"coverage_rate_7d": {"color": BLUE}},
            **columns(column("coverage_rate_7d", column_title="Coverage 7 d", number_style="percent", decimals=0)),
        },
    ),
    "Daily coverage of the selected stations": (
        "Reporting history: valid readings divided by expected readings per UTC day, over the selected stations. "
        "Select one station to see its own history.",
        "bar",
        monitoring_sql(
            "  day,\n  SAFE_DIVIDE(SUM(valid_readings), SUM(expected_readings)) AS daily_coverage_rate",
            "station_daily_observations",
            "GROUP BY day\nORDER BY day",
        ),
        ("station",),
        {
            "graph.dimensions": ["day"],
            "graph.metrics": ["daily_coverage_rate"],
            "graph.x_axis.title_text": "Day (UTC)",
            "graph.y_axis.title_text": "Daily coverage",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": 0,
            "graph.y_axis.max": 1,
            "series_settings": {"daily_coverage_rate": {"color": BLUE}},
            **columns(column("daily_coverage_rate", column_title="Daily coverage", number_style="percent", decimals=0)),
        },
    ),
    "Flagged readings per day": (
        "Readings of exactly -50 °C per UTC day, over the selected stations.",
        "bar",
        monitoring_sql(
            "  day,\n  SUM(flagged_readings) AS flagged_readings",
            "station_daily_observations",
            "GROUP BY day\nORDER BY day",
        ),
        ("station",),
        {
            "graph.dimensions": ["day"],
            "graph.metrics": ["flagged_readings"],
            "graph.x_axis.title_text": "Day (UTC)",
            "graph.y_axis.title_text": "Flagged readings (-50 °C)",
            "series_settings": {"flagged_readings": {"color": ORANGE}},
            **columns(column("flagged_readings", column_title="Flagged readings")),
        },
    ),
    "Missing intervals of 1 hour or more": (
        "Runs of consecutive 15-minute slots with no valid reading, most recent first. "
        "'Ongoing' means the station had not reported again at collection time.",
        "table",
        monitoring_sql(
            """  station_label AS station,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', gap_start) AS first_missing_slot_utc,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', gap_end) AS last_missing_slot_utc,
  gap_hours,
  missing_readings,
  IF(is_ongoing, 'Ongoing at collection', 'Ended') AS state""",
            "station_observation_gaps",
            "  AND missing_readings >= 4\nORDER BY gap_end DESC, station_label\nLIMIT 500",
        ),
        ("station",),
        columns(
            column("station", column_title="Station"),
            column("first_missing_slot_utc", column_title="First missing slot (UTC)"),
            column("last_missing_slot_utc", column_title="Last missing slot (UTC)"),
            column("gap_hours", column_title="Length (h)", decimals=1, show_mini_bar=True),
            column("missing_readings", column_title="Missing readings"),
            column("state", column_title="State"),
        ),
    ),
}

THRESHOLDS_TEXT = (
    "**Check station** when, at collection time: no valid reading in 24 h · latest valid reading older than 2 h · "
    "a flagged (−50 °C) reading in 24 h · 24 h coverage below 80%."
)

# tab name -> [(row, col, width, height, question name | text | iframe url, kind)]
TABS = {
    "Reliability": [
        (0, 0, 24, 3, "Selection and loaded data", None),
        (3, 0, 16, 8, "Mean absolute error by lead time (J1-J7)", None),
        (3, 16, 8, 2, "Coverage rate", None),
        (5, 16, 8, 2, "Valid comparisons per lead", None),
        (7, 16, 8, 2, "Missing hours", None),
        (9, 16, 8, 2, "Future hours (excluded)", None),
        (11, 0, 24, 8, "Forecast vs observed temperature", None),
        (19, 0, 12, 7, "Signed bias by lead time (J1-J7)", None),
        (19, 12, 12, 7, "Scores by lead time (selection)", None),
        (26, 0, 24, 11, "Scores and coverage by station (selection)", None),
    ],
    "Map": [
        (0, 0, 24, 20, MAP_URL, "iframe"),
    ],
    "Station monitoring": [
        (0, 0, 16, 3, "Snapshot", None),
        (0, 16, 4, 3, "Stations to check", None),
        (0, 20, 4, 3, "Stations OK", None),
        (3, 0, 24, 1, THRESHOLDS_TEXT, "text"),
        (4, 0, 24, 9, "Station status at collection time", None),
        (13, 0, 12, 9, "Coverage in the 7 days before collection", None),
        (13, 12, 12, 9, "Daily coverage of the selected stations", None),
        (22, 0, 12, 10, "Missing intervals of 1 hour or more", None),
        (22, 12, 12, 10, "Flagged readings per day", None),
    ],
}


def template_tags(used: tuple[str, ...], fields: dict[str, int], table_for_date: str) -> dict:
    tags = {}
    if "station" in used:
        tags["station"] = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "toulouse-data-v2/station")),
            "name": "station",
            "display-name": "Station",
            "type": "dimension",
            "dimension": ["field", fields["stg_stations.station_label"], None],
            "widget-type": "string/=",
        }
    if "target_date" in used:
        tags["target_date"] = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"toulouse-data-v2/date/{table_for_date}")),
            "name": "target_date",
            "display-name": "Target date",
            "type": "dimension",
            "dimension": ["field", fields[f"{table_for_date}.valid_date_local"], None],
            "widget-type": "date/all-options",
        }
    if "lead" in used:
        tags["lead"] = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "toulouse-data-v2/lead")),
            "name": "lead",
            "display-name": "Lead",
            "type": "number",
            "default": "1",
            "required": True,
        }
    return tags


def allow_map_iframe(mb: Metabase) -> None:
    """Add the local map server to the hosts Metabase may show in an iframe."""
    setting = next(s for s in mb.get("setting") if s["key"] == "allowed-iframe-hosts")
    hosts = setting.get("value") or setting.get("default") or ""
    if MAP_HOST not in hosts:
        mb.put("setting/allowed-iframe-hosts", {"value": f"{hosts}\n{MAP_HOST}".strip()})


def main() -> None:
    mb = Metabase().login()

    database = next(d for d in mb.get("database")["data"] if d["name"] == DATABASE_NAME)
    tables = {t["name"]: t for t in mb.get(f"database/{database['id']}/metadata")["tables"]}
    fields = {
        f"{table}.{name}": next(f["id"] for f in tables[table]["fields"] if f["name"] == name)
        for table, name in [
            ("stg_stations", "station_label"),
            ("forecast_comparison", "valid_date_local"),
            ("int_station_hour_observations", "valid_date_local"),
        ]
    }

    collection = find_or_create(mb, "collection", COLLECTION, None, {"name": COLLECTION})
    allow_map_iframe(mb)

    card_ids = {}
    for name, (description, display, sql, used, visualization) in QUESTIONS.items():
        table_for_date = (
            "int_station_hour_observations" if f"FROM {STATION_HOURS}" in sql else "forecast_comparison"
        )
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
                    "native": {
                        "query": sql.strip(),
                        "template-tags": template_tags(used, fields, table_for_date),
                    },
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
            "description": "V2: reliability over a selected period, temperature map, station monitoring.",
            "collection_id": collection["id"],
        },
    )
    existing_tabs = {t["name"]: t["id"] for t in mb.get(f"dashboard/{dashboard['id']}").get("tabs", [])}
    tab_ids = {name: existing_tabs.get(name, -(i + 1)) for i, name in enumerate(TABS)}

    targets = {
        "station": (STATION_PARAMETER_ID, ["dimension", ["template-tag", "station"]]),
        "target_date": (DATE_PARAMETER_ID, ["dimension", ["template-tag", "target_date"]]),
        "lead": (LEAD_PARAMETER_ID, ["variable", ["template-tag", "lead"]]),
    }
    dashcards = []
    for tab, layout in TABS.items():
        for row, col, width, height, content, kind in layout:
            dashcard = {
                "id": -(len(dashcards) + 1),
                "dashboard_tab_id": tab_ids[tab],
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
                    ("text" if kind == "text" else "iframe"): content,
                }
            else:
                dashcard["card_id"] = card_ids[content]
                dashcard["parameter_mappings"] = [
                    {"parameter_id": targets[tag][0], "card_id": card_ids[content], "target": targets[tag][1]}
                    for tag in QUESTIONS[content][3]
                ]
            dashcards.append(dashcard)

    mb.put(
        f"dashboard/{dashboard['id']}",
        {
            "width": "full",
            "parameters": [
                {
                    "id": DATE_PARAMETER_ID,
                    "name": "Target date",
                    "slug": "target_date",
                    "type": "date/all-options",
                    "sectionId": "date",
                },
                {
                    "id": STATION_PARAMETER_ID,
                    "name": "Station",
                    "slug": "station",
                    "type": "string/=",
                    "sectionId": "string",
                },
                {
                    "id": LEAD_PARAMETER_ID,
                    "name": "Time-series lead (J)",
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
            ],
            "tabs": [{"id": tab_ids[name], "name": name} for name in TABS],
            "dashcards": dashcards,
        },
    )
    dashboard = mb.get(f"dashboard/{dashboard['id']}")
    print(f"{len(card_ids)} questions in collection '{COLLECTION}'")
    for tab in dashboard["tabs"]:
        print(f"{tab['name']}: {METABASE_URL}/dashboard/{dashboard['id']}?tab={tab['id']}")


if __name__ == "__main__":
    main()
