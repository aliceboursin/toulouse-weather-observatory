"""Connect Metabase to the BigQuery `analytics` dataset.

    uv run python metabase/connect_bigquery.py <path-to-service-account-key.json>

Targets the local Metabase unless METABASE_URL is set. The key is stored in
Metabase's own database; the key file is not needed afterwards.
"""

import sys
import time
from pathlib import Path

from client import DATABASE_NAME, Metabase

PROJECT_ID = "toulouse-weather-demo"
DATASET = "analytics"


def main() -> None:
    key = Path(sys.argv[1]).read_text(encoding="utf-8")
    mb = Metabase().login()

    details = {
        "project-id": PROJECT_ID,
        "service-account-json": key,
        "dataset-filters-type": "inclusion",
        "dataset-filters-patterns": DATASET,
    }
    existing = [d for d in mb.get("database")["data"] if d["name"] == DATABASE_NAME]
    if existing:
        database = mb.put(f"database/{existing[0]['id']}", {"details": details})
    else:
        database = mb.post(
            "database",
            {"name": DATABASE_NAME, "engine": "bigquery-cloud-sdk", "details": details},
        )
    database_id = database["id"]
    mb.post(f"database/{database_id}/sync_schema")

    # The first sync runs in the background; wait until the views show up.
    for _ in range(60):
        tables = mb.get(f"database/{database_id}/metadata")["tables"]
        if any(t["name"] == "forecast_comparison" and t["fields"] for t in tables):
            break
        time.sleep(5)
    print(f"database id {database_id}: {sorted(t['name'] for t in tables)}")


if __name__ == "__main__":
    main()
