"""Run every dashboard card through Metabase, with and without the station filter.

    uv run python metabase/verify_dashboard.py ["<station label>"]

Prints each card's result so it can be compared with BigQuery, and reports
which cards change when the station filter is set.
"""

import sys

from build_dashboard import COLLECTION, DASHBOARD, STATION_PARAMETER_ID
from client import Metabase


def run(mb: Metabase, dashboard_id: int, dashcard: dict, station: str | None) -> list[list]:
    parameters = []
    if station and dashcard["parameter_mappings"]:
        parameters = [
            {
                "id": STATION_PARAMETER_ID,
                "type": "string/=",
                "value": [station],
                "target": dashcard["parameter_mappings"][0]["target"],
            }
        ]
    result = mb.post(
        f"dashboard/{dashboard_id}/dashcard/{dashcard['id']}/card/{dashcard['card_id']}/query",
        {"parameters": parameters},
    )
    if result.get("status") != "completed":
        raise RuntimeError(f"{dashcard['card']['name']}: {result.get('error')}")
    return result["data"]["rows"]


def main() -> None:
    station = sys.argv[1] if len(sys.argv) > 1 else "11 - Toulouse Soupetard"
    mb = Metabase().login()
    collection = next(c for c in mb.get("collection") if c.get("name") == COLLECTION)
    item = next(
        i
        for i in mb.get(f"collection/{collection['id']}/items", models="dashboard")["data"]
        if i["name"] == DASHBOARD
    )
    dashboard = mb.get(f"dashboard/{item['id']}")
    print(f"Dashboard {dashboard['id']}: {dashboard['name']}")
    print(f"Filters: {[(p['name'], p['type']) for p in dashboard['parameters']]}")

    changed, unchanged = [], []
    for dashcard in dashboard["dashcards"]:
        if not dashcard["card_id"]:
            continue
        name = dashcard["card"]["name"]
        everything = run(mb, dashboard["id"], dashcard, None)
        filtered = run(mb, dashboard["id"], dashcard, station)
        (changed if filtered != everything else unchanged).append(name)
        mapped = "filter mapped" if dashcard["parameter_mappings"] else "no filter"
        print(f"\n--- {name} [{mapped}]")
        print(f"all stations ({len(everything)} rows): {everything[:12]}")
        if dashcard["parameter_mappings"]:
            print(f"{station} ({len(filtered)} rows): {filtered[:12]}")

    print(f"\nCards that change with Station = {station}: {len(changed)}")
    for name in changed:
        print(f"  changed    {name}")
    for name in unchanged:
        print(f"  unchanged  {name}")


if __name__ == "__main__":
    main()
