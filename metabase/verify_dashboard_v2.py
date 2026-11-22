"""Check the V2 dashboard against BigQuery, and optionally screenshot it.

    uv run python metabase/verify_dashboard_v2.py
    uv run --with playwright python metabase/verify_dashboard_v2.py <screenshot-folder>

Runs the dashboard's cards through Metabase under several filter settings and
compares them with independent queries on forecast_comparison. The map tab has
its own browser test: metabase/verify_map.py.
"""

import sys
from pathlib import Path
from urllib.parse import quote

from build_dashboard_v2 import (
    COLLECTION,
    DASHBOARD,
    DATABASE_NAME,
    DATE_PARAMETER_ID,
    LEAD_PARAMETER_ID,
    STATION_PARAMETER_ID,
)
from client import METABASE_URL, Metabase

SOUPETARD = "11 - Toulouse Soupetard"
PARAMETER_TYPES = {DATE_PARAMETER_ID: "date/all-options", STATION_PARAMETER_ID: "string/=", LEAD_PARAMETER_ID: "number/="}
failures = []


def check(condition: bool, message: str) -> None:
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


class Dashboard:
    def __init__(self, mb: Metabase) -> None:
        self.mb = mb
        collection = next(c for c in mb.get("collection") if c.get("name") == COLLECTION)
        item = next(
            i
            for i in mb.get(f"collection/{collection['id']}/items", models="dashboard")["data"]
            if i["name"] == DASHBOARD
        )
        self.data = mb.get(f"dashboard/{item['id']}")
        self.id = self.data["id"]
        self.cards = {d["card"]["name"]: d for d in self.data["dashcards"] if d["card_id"]}
        self.database = next(d for d in mb.get("database")["data"] if d["name"] == DATABASE_NAME)

    def run(self, name: str, date: str | None = None, station: str | None = None, lead: int = 1) -> list[list]:
        dashcard = self.cards[name]
        values = {DATE_PARAMETER_ID: date, STATION_PARAMETER_ID: [station] if station else None, LEAD_PARAMETER_ID: [lead]}
        parameters = [
            {"id": m["parameter_id"], "type": PARAMETER_TYPES[m["parameter_id"]], "value": values[m["parameter_id"]], "target": m["target"]}
            for m in dashcard["parameter_mappings"]
            if values[m["parameter_id"]] is not None
        ]
        result = self.mb.post(
            f"dashboard/{self.id}/dashcard/{dashcard['id']}/card/{dashcard['card_id']}/query",
            {"parameters": parameters},
        )
        if result.get("status") != "completed":
            raise RuntimeError(f"{name}: {result.get('error')}")
        return result["data"]["rows"]

    def sql(self, query: str) -> list[list]:
        result = self.mb.post("dataset", {"database": self.database["id"], "type": "native", "native": {"query": query}})
        if result.get("status") != "completed":
            raise RuntimeError(result.get("error"))
        return result["data"]["rows"]

    def reference(self, where: str) -> dict:
        """The same figures from a query written independently of the cards."""
        scores = self.sql(f"""
            SELECT c.lead_days, COUNT(c.error_c), ROUND(AVG(c.absolute_error_c), 3), ROUND(AVG(c.error_c), 3)
            FROM analytics.forecast_comparison AS c JOIN analytics.stg_stations AS s USING (station_id)
            WHERE c.lead_days BETWEEN 1 AND 7 AND {where}
            GROUP BY 1 HAVING COUNT(c.error_c) > 0 ORDER BY 1""")
        hours = self.sql(f"""
            SELECT COUNTIF(c.observation_status = 'observed'), COUNTIF(c.observation_status = 'missing_observation'),
                   COUNTIF(c.observation_status = 'future_at_ingestion')
            FROM analytics.forecast_comparison AS c JOIN analytics.stg_stations AS s USING (station_id)
            WHERE c.lead_days = 3 AND {where}""")[0]
        comparable, missing, future = hours
        return {
            "scores": [[f"J{r[0]}", r[1], r[2], r[3]] for r in scores],
            "comparable": comparable,
            "missing": missing,
            "future": future,
            "rate": comparable / (comparable + missing) if comparable + missing else None,
        }

    def compare(self, label: str, where: str, date: str | None, station: str | None) -> dict:
        want = self.reference(where)
        scores = self.run("Scores by lead time (selection)", date, station)
        chart = self.run("Mean absolute error by lead time (J1-J7)", date, station)
        comparable = self.run("Valid comparisons per lead", date, station)[0][0]
        rate_rows = self.run("Coverage rate", date, station)
        rate = rate_rows[0][0] if rate_rows else None
        missing = self.run("Missing hours", date, station)[0][0]
        future = self.run("Future hours (excluded)", date, station)[0][0]
        by_station = self.run("Scores and coverage by station (selection)", date, station)
        check(scores == want["scores"] and chart == want["scores"], f"{label}: MAE, bias and counts per lead match BigQuery ({len(scores)} leads)")
        check([comparable, missing, future] == [want["comparable"], want["missing"], want["future"]], f"{label}: comparable {comparable}, missing {missing}, future {future} match BigQuery")
        check((rate is None and want["rate"] is None) or (rate is not None and abs(rate - want["rate"]) < 1e-9), f"{label}: coverage rate {rate if rate is None else round(rate, 4)} = comparable / (comparable + missing)")
        check(all(row[1] == comparable for row in scores), f"{label}: every lead has {comparable} valid comparisons, the same as the comparable-hours tile")
        check(sum(row[1] for row in by_station) == comparable, f"{label}: station table's comparable hours add up to {comparable}")
        return {"scores": scores, "comparable": comparable, "rate": rate}


def api_checks(board: Dashboard) -> None:
    print(f"Dashboard {board.id}: {board.data['name']}")
    print(f"Tabs: {[t['name'] for t in board.data['tabs']]} | filters: {[p['name'] for p in board.data['parameters']]}\n")

    everything = board.compare("All data", "TRUE", None, None)
    check(everything["comparable"] == 5314, "All data: 5,314 valid comparisons per lead (same as V1)")
    check([everything["scores"][0][2], everything["scores"][-1][2]] == [1.16, 2.473], "All data: MAE 1.16 °C at J1 and 2.47 °C at J7 (same as V1)")

    print()
    one = board.compare("Station = Soupetard", f"s.station_label = '{SOUPETARD}'", None, SOUPETARD)
    check(one["scores"] != everything["scores"], "Selecting one station changes the J1-J7 error chart")
    check(one["comparable"] == 3 and abs(one["rate"] - 3 / 732) < 1e-9, "Soupetard: 3 comparable hours, coverage 3 / 732 = 0.41%")

    print()
    period = "c.valid_date_local BETWEEN '2026-11-10' AND '2026-11-12'"
    narrow = board.compare("10-12 Nov", period, "2026-11-10~2026-11-12", None)
    check(narrow["scores"] != everything["scores"] and narrow["rate"] != everything["rate"], "Narrowing the date range changes both the scores and the coverage")

    print()
    board.compare("Single day 18 Nov", "c.valid_date_local = '2026-11-18'", "2026-11-18", None)
    board.compare("Month Nov 2026", "c.valid_date_local BETWEEN '2026-11-01' AND '2026-11-30'", "2026-11", None)
    board.compare("Soupetard on 18-19 Nov (low coverage)", f"s.station_label = '{SOUPETARD}' AND c.valid_date_local BETWEEN '2026-11-18' AND '2026-11-19'", "2026-11-18~2026-11-19", SOUPETARD)

    print()
    empty_status = board.run("Selection and loaded data", "2026-07-15", None)[0]
    check(empty_status[0] == "No data for this selection" and "2026-10-20 to 2026-11-20" in empty_status[3], f"Outside the data: status '{empty_status[0]}', loaded range shown ({empty_status[3]})")
    check(board.run("Mean absolute error by lead time (J1-J7)", "2026-07-15", None) == [], "Outside the data: the error chart has no rows")
    check(board.run("Valid comparisons per lead", "2026-07-15", None)[0][0] == 0, "Outside the data: 0 valid comparisons")
    check(board.run("Coverage rate", "2026-07-15", None) == [], "Outside the data: the coverage tile has no value")
    check(board.run("Forecast vs observed temperature", "2026-07-15", None) == [], "Outside the data: the time series has no rows")
    soupetard_empty = board.run("Selection and loaded data", "2026-10-25", SOUPETARD)[0]
    scores = board.run("Scores by lead time (selection)", "2026-10-25", SOUPETARD)
    check(soupetard_empty[0] == "Data available" and scores == [], "Soupetard on 25 Oct: hours exist but none is comparable, so no score is shown")
    check(board.run("Coverage rate", "2026-10-25", SOUPETARD)[0][0] == 0, "Soupetard on 25 Oct: coverage rate 0%")

    print()
    lead1 = board.run("Forecast vs observed temperature", "2026-11-18", None, lead=1)
    lead7 = board.run("Forecast vs observed temperature", "2026-11-18", None, lead=7)
    check(len(lead1) == 24 and [r[1] for r in lead1] == [r[1] for r in lead7] and [r[2] for r in lead1] != [r[2] for r in lead7], "Time series: changing the lead changes the forecast line and keeps the observed line (24 hours on 18 Nov)")
    want = board.sql("""
        SELECT ROUND(AVG(observed_temperature_c), 2), ROUND(AVG(forecast_temperature_c), 2)
        FROM analytics.forecast_comparison
        WHERE lead_days = 7 AND error_c IS NOT NULL AND valid_date_local = '2026-11-18' AND valid_hour_local = 16""")[0]
    at_16 = next(r for r in lead7 if r[0].startswith("2026-11-18T16"))
    check(at_16[1:] == want, f"Time series at 16:00, J7: observed {at_16[1]} °C, forecast {at_16[2]} °C match BigQuery")

    print()
    status = board.run("Station status at collection time")
    status_one = board.run("Station status at collection time", station=SOUPETARD)
    want = board.sql("SELECT station_label, status FROM analytics.station_monitoring ORDER BY status_rank, hours_since_latest_valid_reading DESC, station_label")
    check([r[:2] for r in status] == want, f"Monitoring: status table matches station_monitoring ({len(status)} stations)")
    check(len(status_one) == 1 and status_one[0][1].startswith("Check station"), f"Monitoring: Soupetard alone -> '{status_one[0][1]}'")
    to_check = board.run("Stations to check")[0][0]
    check(to_check == sum(1 for r in want if r[1] != "OK"), f"Monitoring: {to_check} stations to check")
    check(board.run("Daily coverage of the selected stations") != board.run("Daily coverage of the selected stations", station=SOUPETARD), "Monitoring: the station filter changes the reporting-history chart")
    snapshot = board.run("Snapshot")[0]
    check(snapshot[0].startswith("2026-11-19 11:16") and "days ago" in snapshot[1], f"Monitoring: collected {snapshot[0]}, {snapshot[1]} -> '{snapshot[2][:40]}...'")
    gaps = board.run("Missing intervals of 1 hour or more", station=SOUPETARD)
    check(len(gaps) == 6 and gaps[0][5] == "Ongoing at collection", f"Monitoring: Soupetard has {len(gaps)} missing intervals of 1 h or more, the latest still ongoing")


def screenshots(board: Dashboard, folder: Path) -> None:
    from playwright.sync_api import sync_playwright

    tabs = {t["name"]: t["id"] for t in board.data["tabs"]}
    views = [
        ("reliability_all", "Reliability", ""),
        ("reliability_soupetard", "Reliability", f"&station={quote(SOUPETARD)}"),
        ("reliability_10_12_nov", "Reliability", "&target_date=2026-11-10~2026-11-12&lead=7"),
        ("reliability_empty", "Reliability", "&target_date=2026-07-15"),
        ("map", "Map", ""),
        ("monitoring_all", "Station monitoring", ""),
        ("monitoring_soupetard", "Station monitoring", f"&station={quote(SOUPETARD)}"),
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(viewport={"width": 1500, "height": 2300})
        context.add_cookies([{"name": "metabase.SESSION", "value": board.mb.http.headers["X-Metabase-Session"], "url": METABASE_URL}])
        page = context.new_page()
        for name, tab, query in views:
            page.goto(f"{METABASE_URL}/dashboard/{board.id}?tab={tabs[tab]}{query}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(4000)
            page.screenshot(path=str(folder / f"{name}.png"))
            print(f"screenshot {name}.png")
        browser.close()


def main() -> None:
    board = Dashboard(Metabase().login())
    api_checks(board)
    if len(sys.argv) > 1:
        screenshots(board, Path(sys.argv[1]))
    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
