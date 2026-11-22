"""Create or update the Toulouse Data questions and dashboard in the local Metabase.

    uv run python metabase/build_dashboard.py

Safe to run again: questions and the dashboard are matched by name inside the
"Toulouse Data" collection and updated in place.
"""

import uuid

from client import METABASE_URL, Metabase

COLLECTION = "Toulouse Data"
DASHBOARD = "Toulouse forecast reliability"
DATABASE_NAME = "Toulouse Data (BigQuery)"
STATION_PARAMETER_ID = "station01"

# Station filter. {{station}} is a Metabase field filter on
# stg_stations.station_label; with no selection it becomes "1 = 1".
# BigQuery field filters need the table written as `dataset.table`, unaliased.
STATION_FILTER = (
    "station_id IN (SELECT station_id FROM `analytics.stg_stations` WHERE {{station}})"
)

LEAD_METRICS_SQL = """
-- All stations pooled. AVG runs over individual comparisons, so each valid
-- comparison has equal weight. Leads 1 to 7 only.
SELECT
  CONCAT('J', CAST(lead_days AS STRING)) AS lead_time,
  COUNT(error_c) AS valid_comparisons,
  ROUND(AVG(absolute_error_c), 3) AS mean_absolute_error_c,
  ROUND(AVG(error_c), 3) AS mean_signed_error_c
FROM analytics.forecast_comparison
WHERE forecast_kind = 'forecast'
GROUP BY lead_days
ORDER BY lead_days
"""

STATION_POOLED_SQL = f"""
-- Leads 1 to 7 pooled per station, each valid comparison weighted equally.
-- The axis label carries the number of comparable hours behind the score.
SELECT
  CONCAT(CAST(COUNT(DISTINCT IF(c.error_c IS NOT NULL, c.valid_at, NULL)) AS STRING), ' h | ', s.station_label) AS station,
  COUNT(c.error_c) AS valid_comparisons,
  ROUND(AVG(c.absolute_error_c), 3) AS mean_absolute_error_c,
  ROUND(AVG(c.error_c), 3) AS mean_signed_error_c
FROM analytics.forecast_comparison AS c
JOIN analytics.stg_stations AS s ON s.station_id = c.station_id
WHERE c.forecast_kind = 'forecast'
  AND c.{STATION_FILTER}
GROUP BY s.station_label
ORDER BY s.station_label
"""


def scalar_sql(expression: str, table: str, where: str = "") -> str:
    extra = f"  AND {where}\n" if where else ""
    return f"SELECT {expression} AS value\nFROM analytics.{table}\nWHERE {STATION_FILTER}\n{extra}"


def column(name: str, **settings) -> tuple[str, dict]:
    return f'["name","{name}"]', settings


def columns(*pairs: tuple[str, dict]) -> dict:
    return {"column_settings": dict(pairs)}


DEGREES = {"suffix": " °C", "decimals": 2}

# Categorical colours, in fixed order, from a palette checked for
# colour-blind separation. A category keeps its colour under any filter.
BLUE = "#2a78d6"
CATEGORY_COLORS = {
    "Centered 30 min": "#2a78d6",
    "Preceding 15 min": "#eb6834",
    "Following 15 min": "#1baf7a",
    "Missing observation": "#eda100",
    "Future at ingestion": "#e87ba4",
}
PERCENT = {"number_style": "percent", "decimals": 2}

# name -> (description, display, sql, uses the station filter, visualization settings)
QUESTIONS: dict[str, tuple[str, str, str, bool, dict]] = {
    "Data window": (
        "First and last forecast target hour in the data, the last hour with an observation, "
        "and when the forecasts were ingested. All times are UTC.",
        "table",
        """
SELECT
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', MIN(valid_at)) AS first_target_hour_utc,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', MAX(valid_at)) AS last_target_hour_utc,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', MAX(IF(observation_status = 'observed', valid_at, NULL))) AS last_comparable_hour_utc,
  (SELECT FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', MAX(ingested_at)) FROM analytics.stg_forecasts) AS forecasts_ingested_utc,
  COUNT(DISTINCT station_id) AS stations,
  COUNT(DISTINCT valid_at) AS target_hours_per_station
FROM analytics.int_station_hour_observations
""",
        False,
        columns(
            column("first_target_hour_utc", column_title="First target hour (UTC)"),
            column("last_target_hour_utc", column_title="Last target hour (UTC)"),
            column("last_comparable_hour_utc", column_title="Last hour with an observation (UTC)"),
            column("forecasts_ingested_utc", column_title="Forecasts ingested (UTC)"),
            column("stations", column_title="Stations"),
            column("target_hours_per_station", column_title="Target hours per station"),
        ),
    ),
    "Mean absolute error by lead time (J1-J7, all stations)": (
        "Global mean absolute error per lead time. All stations pooled; each valid comparison has equal weight.",
        "bar",
        LEAD_METRICS_SQL,
        False,
        {
            "graph.dimensions": ["lead_time"],
            "graph.metrics": ["mean_absolute_error_c"],
            "graph.show_values": True,
            "graph.x_axis.title_text": "Lead time (days ahead)",
            "graph.y_axis.title_text": "Mean absolute error (°C)",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": 0,
            "graph.y_axis.max": 3,
            "series_settings": {"mean_absolute_error_c": {"color": BLUE}},
            **columns(column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES)),
        },
    ),
    "Signed bias by lead time (J1-J7, all stations)": (
        "Global mean signed error (forecast minus observation) per lead time. "
        "Above zero: forecasts too warm. Below zero: too cold.",
        "bar",
        LEAD_METRICS_SQL,
        False,
        {
            "graph.dimensions": ["lead_time"],
            "graph.metrics": ["mean_signed_error_c"],
            "graph.show_values": True,
            "graph.show_goal": True,
            "graph.goal_value": 0,
            "graph.goal_label": "No bias",
            "graph.x_axis.title_text": "Lead time (days ahead)",
            "graph.y_axis.title_text": "Mean signed error (°C)",
            "graph.y_axis.auto_range": False,
            "graph.y_axis.min": -1,
            "graph.y_axis.max": 1,
            "series_settings": {"mean_signed_error_c": {"color": BLUE}},
            **columns(column("mean_signed_error_c", column_title="Mean signed error", **DEGREES)),
        },
    ),
    "Global metrics by lead time (J1-J7)": (
        "Valid comparison count, mean absolute error and mean signed error per lead time, all stations pooled.",
        "table",
        LEAD_METRICS_SQL,
        False,
        columns(
            column("lead_time", column_title="Lead time"),
            column("valid_comparisons", column_title="Valid comparisons"),
            column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES),
            column("mean_signed_error_c", column_title="Mean signed error", **DEGREES),
        ),
    ),
    "Mean absolute error (°C) by station and lead time": (
        "From forecast_reliability. Read each error next to the number of valid comparisons "
        "per lead time and the station's coverage rate.",
        "table",
        f"""
SELECT
  station_label AS station,
  MAX(valid_comparisons) AS valid_comparisons_per_lead,
  MAX(observation_coverage_rate) AS coverage_rate,
  MAX(IF(lead_days = 1, mean_absolute_error_c, NULL)) AS J1,
  MAX(IF(lead_days = 2, mean_absolute_error_c, NULL)) AS J2,
  MAX(IF(lead_days = 3, mean_absolute_error_c, NULL)) AS J3,
  MAX(IF(lead_days = 4, mean_absolute_error_c, NULL)) AS J4,
  MAX(IF(lead_days = 5, mean_absolute_error_c, NULL)) AS J5,
  MAX(IF(lead_days = 6, mean_absolute_error_c, NULL)) AS J6,
  MAX(IF(lead_days = 7, mean_absolute_error_c, NULL)) AS J7
FROM analytics.forecast_reliability
WHERE {STATION_FILTER}
GROUP BY station_label
ORDER BY station_label
""",
        True,
        {
            **columns(
                column("station", column_title="Station"),
                column("valid_comparisons_per_lead", column_title="Comparisons / lead", show_mini_bar=True),
                column("coverage_rate", column_title="Coverage rate", **PERCENT),
                *[column(f"J{d}", column_title=f"J{d}", **DEGREES) for d in range(1, 8)],
            ),
            "table.column_formatting": [
                {
                    "columns": [f"J{d}" for d in range(1, 8)],
                    "type": "range",
                    "colors": ["#FFFFFF", "#509EE3"],
                    "min_type": "custom",
                    "min_value": 0,
                    "max_type": "custom",
                    "max_value": 3,
                }
            ],
        },
    ),
    "Mean absolute error by station (J1-J7 pooled)": (
        "Leads 1 to 7 pooled per station, each valid comparison weighted equally. "
        "The label shows the comparable hours behind each score.",
        "row",
        STATION_POOLED_SQL,
        True,
        {
            "graph.dimensions": ["station"],
            "graph.metrics": ["mean_absolute_error_c"],
            "graph.show_values": True,
            "graph.x_axis.title_text": "Comparable hours | station",
            "graph.y_axis.title_text": "Mean absolute error (°C)",
            "series_settings": {"mean_absolute_error_c": {"color": BLUE}},
            **columns(column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES)),
        },
    ),
    "Signed bias by station (J1-J7 pooled)": (
        "Mean signed error (forecast minus observation) per station, leads 1 to 7 pooled. "
        "Right of zero: forecasts too warm. Left of zero: too cold.",
        "row",
        STATION_POOLED_SQL,
        True,
        {
            "graph.dimensions": ["station"],
            "graph.metrics": ["mean_signed_error_c"],
            "graph.show_values": True,
            "graph.show_goal": True,
            "graph.goal_value": 0,
            "graph.goal_label": "No bias",
            "graph.x_axis.title_text": "Comparable hours | station",
            "graph.y_axis.title_text": "Mean signed error (°C)",
            "series_settings": {"mean_signed_error_c": {"color": BLUE}},
            **columns(column("mean_signed_error_c", column_title="Mean signed error", **DEGREES)),
        },
    ),
    "Lead 0 MAE (reference)": (
        "Lead 0 is the most recent model run, not a forecast issued in advance. Pooled over the selected stations.",
        "scalar",
        scalar_sql("ROUND(AVG(absolute_error_c), 3)", "forecast_comparison", "forecast_kind = 'current_run_reference'"),
        True,
        columns(column("value", **DEGREES)),
    ),
    "Lead 0 comparisons": (
        "Number of lead 0 values with a computed error, for the selected stations.",
        "scalar",
        scalar_sql("COUNT(error_c)", "forecast_comparison", "forecast_kind = 'current_run_reference'"),
        True,
        {},
    ),
    "Lead 0 error by station (current-run reference)": (
        "From current_run_reference_error. Not part of the J1-J7 reliability metrics.",
        "table",
        f"""
SELECT
  station_label AS station,
  valid_comparisons,
  mean_absolute_error_c,
  mean_signed_error_c
FROM analytics.current_run_reference_error
WHERE {STATION_FILTER}
ORDER BY station_label
""",
        True,
        columns(
            column("station", column_title="Station"),
            column("valid_comparisons", column_title="Valid comparisons", show_mini_bar=True),
            column("mean_absolute_error_c", column_title="Mean absolute error", **DEGREES),
            column("mean_signed_error_c", column_title="Mean signed error", **DEGREES),
        ),
    ),
    "Coverage rate": (
        "Comparable station-hours divided by non-future station-hours, for the selected stations. "
        "From station_observation_coverage.",
        "scalar",
        scalar_sql("SAFE_DIVIDE(SUM(comparable_hours), SUM(non_future_hours))", "station_observation_coverage"),
        True,
        columns(column("value", **PERCENT)),
    ),
    "Comparable hours": (
        "Target hours with a usable observation. The numerator of the coverage rate.",
        "scalar",
        scalar_sql("SUM(comparable_hours)", "station_observation_coverage"),
        True,
        {},
    ),
    "Non-future hours": (
        "Target hours that were in the past at ingestion. The denominator of the coverage rate.",
        "scalar",
        scalar_sql("SUM(non_future_hours)", "station_observation_coverage"),
        True,
        {},
    ),
    "Missing hours": (
        "Non-future target hours with no usable observation.",
        "scalar",
        scalar_sql("SUM(missing_hours)", "station_observation_coverage"),
        True,
        {},
    ),
    "Future hours (not in rate)": (
        "Target hours after the forecast ingestion time. Reported separately; not in the coverage rate.",
        "scalar",
        scalar_sql("SUM(future_hours)", "station_observation_coverage"),
        True,
        {},
    ),
    "Coverage by station": (
        "From station_observation_coverage. Coverage rate = comparable hours / non-future hours.",
        "table",
        f"""
SELECT
  station_label AS station,
  observation_coverage_rate AS coverage_rate,
  comparable_hours,
  missing_hours,
  non_future_hours,
  future_hours,
  target_hours
FROM analytics.station_observation_coverage
WHERE {STATION_FILTER}
ORDER BY station_label
""",
        True,
        columns(
            column("station", column_title="Station"),
            column("coverage_rate", column_title="Coverage rate", show_mini_bar=True, **PERCENT),
            column("comparable_hours", column_title="Comparable hours"),
            column("missing_hours", column_title="Missing hours"),
            column("non_future_hours", column_title="Non-future hours"),
            column("future_hours", column_title="Future hours"),
            column("target_hours", column_title="Target hours"),
        ),
    ),
    "Station-hours by coverage category": (
        "From observation_coverage. Each station and target hour is counted once. The first three "
        "categories are comparable hours. Future hours are shown as their own category.",
        "row",
        f"""
SELECT
  station_label AS station,
  CASE coverage_category
    WHEN 'centered_30min' THEN 'Centered 30 min'
    WHEN 'preceding_15min' THEN 'Preceding 15 min'
    WHEN 'following_15min' THEN 'Following 15 min'
    WHEN 'missing_observation' THEN 'Missing observation'
    WHEN 'future_at_ingestion' THEN 'Future at ingestion'
  END AS category,
  station_hours
FROM analytics.observation_coverage
WHERE {STATION_FILTER}
ORDER BY
  station_label,
  CASE coverage_category
    WHEN 'centered_30min' THEN 1
    WHEN 'preceding_15min' THEN 2
    WHEN 'following_15min' THEN 3
    WHEN 'missing_observation' THEN 4
    ELSE 5
  END
""",
        True,
        {
            "graph.dimensions": ["station", "category"],
            "graph.metrics": ["station_hours"],
            "stackable.stack_type": "stacked",
            "series_settings": {name: {"color": color} for name, color in CATEGORY_COLORS.items()},
            "graph.x_axis.title_text": "Station",
            "graph.y_axis.title_text": "Station-hours",
        },
    ),
    "Coverage category breakdown": (
        "From observation_coverage, summed over the selected stations. The share divides by all target "
        "hours, future hours included: it is a distribution, not the coverage rate.",
        "table",
        f"""
SELECT
  coverage_category,
  SUM(station_hours) AS station_hours,
  SAFE_DIVIDE(SUM(station_hours), SUM(SUM(station_hours)) OVER ()) AS share_of_all_target_hours
FROM analytics.observation_coverage
WHERE {STATION_FILTER}
GROUP BY coverage_category
ORDER BY station_hours DESC
""",
        True,
        columns(
            column("coverage_category", column_title="Category"),
            column("station_hours", column_title="Station-hours"),
            column("share_of_all_target_hours", column_title="Share of all target hours", show_mini_bar=True, **PERCENT),
        ),
    ),
}

HEADER_TEXT = """\
## Toulouse forecast reliability

How accurate are Open-Meteo temperature forecasts for Toulouse, compared with Toulouse Métropole weather stations?

**Error** is the forecast minus the observation, in °C. **J1 to J7** are forecasts issued 1 to 7 days ahead. \
The table below shows the data window. There is no date filter: the reliability and coverage views are \
computed over the whole loaded window, so a date selection could not be applied to them consistently."""

SECTION_1 = """\
## 1. How does accuracy change from J1 to J7?

All stations pooled. Each valid comparison has equal weight. The station filter does not apply to this section."""

SECTION_2 = """\
## 2. How do accuracy and data availability vary by station?

The **Station** filter at the top applies from here down. Read each score next to its number of valid \
comparisons and the station's coverage rate: a score based on a few hours says little."""

SECTION_LEAD0 = """\
## Current-run reference (lead 0)

Lead 0 is the most recent model run, **not a forecast issued in advance**. It is a reference point and is \
excluded from the J1-J7 charts above."""

SECTION_3 = """\
## 3. Why are some station-hours unavailable for comparison?

**Coverage rate** = comparable station-hours / non-future station-hours. A station-hour is comparable when the \
station has a reading at the target time or 15 minutes later. **Future** hours had not happened yet when the \
forecasts were ingested; they are shown separately and are not in the rate."""

# (row, col, width, height, question name or text, text kind)
LAYOUT = [
    (0, 0, 24, 4, HEADER_TEXT, "text"),
    (4, 0, 24, 3, "Data window", None),
    (7, 0, 24, 2, SECTION_1, "text"),
    (9, 0, 12, 7, "Mean absolute error by lead time (J1-J7, all stations)", None),
    (9, 12, 12, 7, "Signed bias by lead time (J1-J7, all stations)", None),
    (16, 0, 24, 8, "Global metrics by lead time (J1-J7)", None),
    (24, 0, 24, 3, SECTION_2, "text"),
    (27, 0, 24, 11, "Mean absolute error (°C) by station and lead time", None),
    (38, 0, 12, 9, "Mean absolute error by station (J1-J7 pooled)", None),
    (38, 12, 12, 9, "Signed bias by station (J1-J7 pooled)", None),
    (47, 0, 24, 2, SECTION_LEAD0, "text"),
    (49, 0, 6, 5, "Lead 0 MAE (reference)", None),
    (54, 0, 6, 6, "Lead 0 comparisons", None),
    (49, 6, 18, 11, "Lead 0 error by station (current-run reference)", None),
    (60, 0, 24, 3, SECTION_3, "text"),
    (63, 0, 8, 3, "Coverage rate", None),
    (63, 8, 8, 3, "Comparable hours", None),
    (63, 16, 8, 3, "Non-future hours", None),
    (66, 0, 12, 3, "Missing hours", None),
    (66, 12, 12, 3, "Future hours (not in rate)", None),
    (69, 0, 24, 11, "Coverage by station", None),
    (80, 0, 24, 9, "Station-hours by coverage category", None),
    (89, 0, 24, 7, "Coverage category breakdown", None),
]


def find_or_create(mb: Metabase, kind: str, name: str, collection_id: int | None, body: dict) -> dict:
    """Update the item called `name` in the collection, or create it."""
    if collection_id is None:
        items = [c for c in mb.get("collection") if c.get("name") == name and not c.get("archived")]
    else:
        items = [
            i
            for i in mb.get(f"collection/{collection_id}/items", models=kind)["data"]
            if i["name"] == name
        ]
    if items:
        return mb.put(f"{kind}/{items[0]['id']}", body)
    return mb.post(kind, body)


def main() -> None:
    mb = Metabase().login()

    database = next(d for d in mb.get("database")["data"] if d["name"] == DATABASE_NAME)
    tables = mb.get(f"database/{database['id']}/metadata")["tables"]
    stations_table = next(t for t in tables if t["name"] == "stg_stations")
    label_field = next(f for f in stations_table["fields"] if f["name"] == "station_label")

    collection = find_or_create(mb, "collection", COLLECTION, None, {"name": COLLECTION})

    station_tag = {
        "station": {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "toulouse-data/station")),
            "name": "station",
            "display-name": "Station",
            "type": "dimension",
            "dimension": ["field", label_field["id"], None],
            "widget-type": "string/=",
        }
    }

    card_ids = {}
    for name, (description, display, sql, filtered, visualization) in QUESTIONS.items():
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
                        "template-tags": station_tag if filtered else {},
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
            "description": "Forecast error by lead time and station, with observation coverage.",
            "collection_id": collection["id"],
        },
    )

    dashcards = []
    for index, (row, col, width, height, content, text_kind) in enumerate(LAYOUT):
        dashcard = {"id": -(index + 1), "row": row, "col": col, "size_x": width, "size_y": height}
        if text_kind:
            dashcard |= {
                "card_id": None,
                "visualization_settings": {
                    "virtual_card": {
                        "name": None,
                        "display": "text",
                        "visualization_settings": {},
                        "dataset_query": {},
                        "archived": False,
                    },
                    "text": content,
                },
                "parameter_mappings": [],
            }
        else:
            filtered = QUESTIONS[content][3]
            dashcard |= {
                "card_id": card_ids[content],
                "parameter_mappings": [
                    {
                        "parameter_id": STATION_PARAMETER_ID,
                        "card_id": card_ids[content],
                        "target": ["dimension", ["template-tag", "station"]],
                    }
                ]
                if filtered
                else [],
            }
        dashcards.append(dashcard)

    mb.put(
        f"dashboard/{dashboard['id']}",
        {
            "parameters": [
                {
                    "id": STATION_PARAMETER_ID,
                    "name": "Station",
                    "slug": "station",
                    "type": "string/=",
                    "sectionId": "string",
                }
            ],
            "dashcards": dashcards,
        },
    )
    print(f"{len(card_ids)} questions in collection '{COLLECTION}'")
    print(f"Dashboard: {METABASE_URL}/dashboard/{dashboard['id']}")


if __name__ == "__main__":
    main()
