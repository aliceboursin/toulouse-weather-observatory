"""Check the V2 dashboard against BigQuery, and optionally review it in a browser.

    uv run python metabase/verify_dashboard_v2.py
    uv run --with playwright python metabase/verify_dashboard_v2.py <screenshot-folder>

Without a folder: runs the dashboard's cards through Metabase under several
filter settings and compares them with independent queries on
forecast_comparison and station_monitoring.

With a folder (needs Playwright and Microsoft Edge): also opens the dashboard,
follows every link on the Overview tab, checks where each one lands, and saves
a screenshot of each tab. The map has its own test: metabase/verify_map.py.
"""

import sys
from pathlib import Path
from urllib.parse import quote

import build_dashboard_v2 as v2
from client import METABASE_URL, Metabase

SOUPETARD = "11 - Toulouse Soupetard"
PARAMETER_TYPES = {
    v2.DATE_PARAMETER_ID: "date/all-options",
    v2.HOUR_FROM_PARAMETER_ID: "number/>=",
    v2.HOUR_TO_PARAMETER_ID: "number/<=",
    v2.STATION_PARAMETER_ID: "string/=",
    v2.LEAD_PARAMETER_ID: "number/=",
    v2.STATUS_PARAMETER_ID: "string/=",
}
failures = []


def check(condition: bool, message: str) -> None:
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


class Dashboard:
    def __init__(self, mb: Metabase) -> None:
        self.mb = mb
        collection = next(c for c in mb.get("collection") if c.get("name") == v2.COLLECTION)
        item = next(
            i
            for i in mb.get(f"collection/{collection['id']}/items", models="dashboard")["data"]
            if i["name"] == v2.DASHBOARD
        )
        self.data = mb.get(f"dashboard/{item['id']}")
        self.id = self.data["id"]
        self.tabs = {t["name"]: t["id"] for t in self.data["tabs"]}
        self.cards = {d["card"]["name"]: d for d in self.data["dashcards"] if d["card_id"]}
        self.database = next(d for d in mb.get("database")["data"] if d["name"] == v2.DATABASE_NAME)

    def run(self, name, date=None, hours=None, station=None, lead=1, status=None) -> list[list]:
        dashcard = self.cards[name]
        values = {
            v2.DATE_PARAMETER_ID: date,
            v2.HOUR_FROM_PARAMETER_ID: [hours[0]] if hours else None,
            v2.HOUR_TO_PARAMETER_ID: [hours[1]] if hours else None,
            v2.STATION_PARAMETER_ID: [station] if station else None,
            v2.LEAD_PARAMETER_ID: [lead],
            v2.STATUS_PARAMETER_ID: [status] if status else None,
        }
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
        comparable, missing, future = self.sql(f"""
            SELECT COUNTIF(c.observation_status = 'observed'), COUNTIF(c.observation_status = 'missing_observation'),
                   COUNTIF(c.observation_status = 'future_at_ingestion')
            FROM analytics.forecast_comparison AS c JOIN analytics.stg_stations AS s USING (station_id)
            WHERE c.lead_days = 3 AND {where}""")[0]
        return {
            "scores": [[f"J{r[0]}", r[1], r[2], r[3]] for r in scores],
            "comparable": comparable,
            "missing": missing,
            "future": future,
            "rate": comparable / (comparable + missing) if comparable + missing else None,
        }

    def compare(self, label: str, where: str, date=None, hours=None, station=None) -> dict:
        want = self.reference(where)
        args = {"date": date, "hours": hours, "station": station}
        scores = self.run(v2.SCORES_BY_LEAD, **args)
        chart = self.run(v2.MAE_BY_LEAD, **args)
        bias = self.run(v2.BIAS_BY_LEAD, **args)
        comparable = self.run(v2.COMPARISONS, **args)[0][0]
        rate_rows = self.run(v2.COVERAGE, **args)
        rate = rate_rows[0][0] if rate_rows else None
        missing = self.run(v2.MISSING, **args)[0][0]
        future = self.run(v2.FUTURE, **args)[0][0]
        by_station = self.run(v2.SCORES_BY_STATION, **args)
        check(scores == want["scores"] and chart == want["scores"] and bias == want["scores"], f"{label}: error, bias and counts per lead match BigQuery ({len(scores)} leads)")
        check([comparable, missing, future] == [want["comparable"], want["missing"], want["future"]], f"{label}: comparable {comparable}, no observation {missing}, future {future} match BigQuery")
        check((rate is None and want["rate"] is None) or (rate is not None and abs(rate - want["rate"]) < 1e-9), f"{label}: comparison coverage {rate if rate is None else round(rate, 4)} = comparable / (comparable + no observation)")
        check(all(row[1] == comparable for row in scores), f"{label}: every lead has {comparable} valid comparisons, the same as the tile")
        check(sum(row[1] for row in by_station) == comparable, f"{label}: station table's hours compared add up to {comparable}")
        return {"scores": scores, "comparable": comparable, "rate": rate}


def api_checks(board: Dashboard) -> None:
    print(f"Dashboard {board.id}: {board.data['name']}")
    print(f"Tabs: {list(board.tabs)} | filters: {[p['name'] for p in board.data['parameters']]}\n")
    check(list(board.tabs) == v2.TAB_NAMES, "Overview is the first tab, followed by Reliability, Map and Station monitoring")

    available = board.run(v2.AVAILABLE)[0]
    check("20 Oct 2026" in available[0] and "20 Nov 2026" in available[0] and "19 Nov 2026, 11:16 UTC" in available[1], f"Overview: period '{available[0].strip()}', collected '{available[1].strip()}' ({available[2]})")

    print()
    everything = board.compare("All data", "TRUE")
    check(everything["comparable"] == 5314, "All data: 5,314 valid comparisons per lead (same as V1)")
    check([everything["scores"][0][2], everything["scores"][-1][2]] == [1.16, 2.473], "All data: mean absolute error 1.16 °C at J1 and 2.47 °C at J7 (same as V1)")

    print()
    one = board.compare("Station = Soupetard", f"s.station_label = '{SOUPETARD}'", station=SOUPETARD)
    check(one["scores"] != everything["scores"], "Selecting one station changes the J1-J7 error chart")
    check(one["comparable"] == 3 and abs(one["rate"] - 3 / 732) < 1e-9, "Soupetard: 3 comparable hours, coverage 3 / 732 = 0.41%")

    print()
    days = "c.valid_date_local BETWEEN '2026-11-10' AND '2026-11-12'"
    narrow = board.compare("10-12 Nov", days, date="2026-11-10~2026-11-12")
    check(narrow["scores"] != everything["scores"] and narrow["rate"] != everything["rate"], "Narrowing the date range changes both the scores and the coverage")

    print()
    afternoons = board.compare(
        "10-12 Nov, 14:00-18:00", f"{days} AND c.valid_hour_local BETWEEN 14 AND 18", date="2026-11-10~2026-11-12", hours=(14, 18)
    )
    check(afternoons["scores"] != narrow["scores"] and afternoons["comparable"] < narrow["comparable"], f"The hour filter changes scores and counts: {narrow['comparable']} -> {afternoons['comparable']} valid comparisons per lead")
    selection = board.run(v2.SELECTION, date="2026-11-10~2026-11-12", hours=(14, 18))[0]
    check(selection[:3] == ["Data available", "2026-11-10 to 2026-11-12", "14:00 to 18:00"], f"'Your selection' shows the days and hours: {selection[1]}, {selection[2]}")
    series = board.run(v2.TIME_SERIES, date="2026-11-10~2026-11-12", hours=(14, 18))
    check(len(series) == 15 and {r[0][11:13] for r in series} == {"14", "15", "16", "17", "18"}, "The hour filter applies to the line chart too: 3 days x 5 hours = 15 points, hours 14 to 18 inclusive")
    one_hour = board.compare("18 Nov at 16:00", "c.valid_date_local = '2026-11-18' AND c.valid_hour_local = 16", date="2026-11-18", hours=(16, 16))
    check(one_hour["comparable"] == 9, "18 Nov at 16:00: 9 stations compared (Soupetard has no observation)")

    print()
    board.compare("Single day 18 Nov", "c.valid_date_local = '2026-11-18'", date="2026-11-18")
    board.compare("Month Nov 2026", "c.valid_date_local BETWEEN '2026-11-01' AND '2026-11-30'", date="2026-11")
    board.compare("Soupetard on 18-19 Nov (low coverage)", f"s.station_label = '{SOUPETARD}' AND c.valid_date_local BETWEEN '2026-11-18' AND '2026-11-19'", date="2026-11-18~2026-11-19", station=SOUPETARD)

    print()
    empty = board.run(v2.SELECTION, date="2026-07-15")[0]
    check(empty[0] == "No data for this selection" and "2026-10-20 to 2026-11-20" in empty[4], f"Outside the data: status '{empty[0]}', available range shown ({empty[4]})")
    check(board.run(v2.MAE_BY_LEAD, date="2026-07-15") == [], "Outside the data: the error chart has no rows")
    check(board.run(v2.COMPARISONS, date="2026-07-15")[0][0] == 0, "Outside the data: 0 valid comparisons")
    check(board.run(v2.COVERAGE, date="2026-07-15") == [], "Outside the data: the coverage tile has no value (not 0%)")
    check(board.run(v2.TIME_SERIES, date="2026-07-15") == [], "Outside the data: the line chart has no rows")
    scores = board.run(v2.SCORES_BY_LEAD, date="2026-10-25", station=SOUPETARD)
    check(board.run(v2.SELECTION, date="2026-10-25", station=SOUPETARD)[0][0] == "Data available" and scores == [], "Soupetard on 25 Oct: hours exist but none is comparable, so no score is shown")
    check(board.run(v2.COVERAGE, date="2026-10-25", station=SOUPETARD)[0][0] == 0, "Soupetard on 25 Oct: comparison coverage 0%")
    blank = board.run(v2.SCORES_BY_STATION, date="2026-10-25", station=SOUPETARD)[0]
    check(blank[1] == 0 and blank[3] is None, "Soupetard on 25 Oct: the station table shows 0 hours compared and a blank score, not 0 °C")

    print()
    lead1 = board.run(v2.TIME_SERIES, date="2026-11-18", lead=1)
    lead7 = board.run(v2.TIME_SERIES, date="2026-11-18", lead=7)
    check(len(lead1) == 24 and [r[1] for r in lead1] == [r[1] for r in lead7] and [r[2] for r in lead1] != [r[2] for r in lead7], "Line chart: changing the lead changes the forecast line and keeps the observed line (24 hours on 18 Nov)")
    want = board.sql("""
        SELECT ROUND(AVG(observed_temperature_c), 2), ROUND(AVG(forecast_temperature_c), 2)
        FROM analytics.forecast_comparison
        WHERE lead_days = 7 AND error_c IS NOT NULL AND valid_date_local = '2026-11-18' AND valid_hour_local = 16""")[0]
    at_16 = next(r for r in lead7 if r[0].startswith("2026-11-18T16"))
    check(at_16[1:] == want, f"Line chart at 16:00, J7: observed {at_16[1]} °C, forecast {at_16[2]} °C match BigQuery")

    print()
    order = "ORDER BY status_rank, hours_since_latest_valid_reading DESC, station_label"
    status = board.run(v2.STATUS_TABLE)
    want = board.sql(f"SELECT station_label, status, status_reason FROM analytics.station_monitoring {order}")
    check([r[:3] for r in status] == want, f"Monitoring: status table matches station_monitoring ({len(status)} stations)")
    flagged = board.run(v2.STATUS_TABLE, status="Check station")
    check(flagged == [r for r in status if r[1] == "Check station"] and len(flagged) == 4, f"Status = Check station shows exactly the {len(flagged)} rows marked 'Check station' in the full table")
    check(all(r[2] and r[2] != "Reporting normally" for r in flagged), "Every 'Check station' row has a reason: " + "; ".join(f"{r[0][:2]}: {r[2][:32]}" for r in flagged))
    ok = board.run(v2.STATUS_TABLE, status="OK")
    check(len(ok) == 6 and all(r[1] == "OK" for r in ok), "Status = OK shows the 6 other stations")
    check([board.run(v2.TO_CHECK, status="Check station")[0][0], board.run(v2.STATIONS_OK, status="Check station")[0][0]] == [4, 0], "Status = Check station: tiles read 4 to check, 0 OK")
    stations = ", ".join(f"'{r[0]}'" for r in flagged)
    want = board.sql(f"SELECT COUNT(*) FROM analytics.station_observation_gaps WHERE missing_readings >= 4 AND station_label IN ({stations})")[0][0]
    gaps = board.run(v2.GAPS, status="Check station")
    check(len(gaps) == want and {g[0] for g in gaps} <= {r[0] for r in flagged}, f"Status = Check station: the gaps table lists only those stations ({len(gaps)} gaps, matches BigQuery)")
    check(board.run(v2.REPORTING_DAILY) != board.run(v2.REPORTING_DAILY, status="Check station"), "Status = Check station changes the daily reporting chart")
    one = board.run(v2.STATUS_TABLE, station=SOUPETARD)
    check(len(one) == 1 and one[0][1] == "Check station" and "4.3 h" in one[0][2] and one[0][4] == 4.27, f"Soupetard alone -> {one[0][1]}: {one[0][2]}")
    snapshot = board.run(v2.SNAPSHOT)[0]
    check(snapshot[0].startswith("2026-11-19 11:16") and "days ago" in snapshot[1], f"Monitoring: collected {snapshot[0]}, {snapshot[1]}")


def browser_review(board: Dashboard, folder: Path) -> None:
    from playwright.sync_api import sync_playwright

    base = f"{METABASE_URL}/dashboard/{board.id}"
    print()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(viewport={"width": 1500, "height": 2300})
        context.add_cookies([{"name": "metabase.SESSION", "value": board.mb.http.headers["X-Metabase-Session"], "url": METABASE_URL}])
        page = context.new_page()

        def settle(target=None):
            target = target or page
            target.wait_for_load_state("networkidle")
            target.wait_for_timeout(3500)

        def follow(link_text: str):
            """Click an Overview link; it may open in the same tab or a new one."""
            page.goto(f"{base}?tab={board.tabs['Overview']}")
            settle()
            before = len(context.pages)
            page.get_by_role("link", name=link_text).first.click()
            page.wait_for_timeout(1500)
            target = context.pages[-1] if len(context.pages) > before else page
            settle(target)
            return target

        # Overview is the landing tab and fits on one screen.
        page.goto(base)
        settle()
        check("Compares Open-Meteo temperature forecasts" in page.inner_text("body"), "Opening the dashboard without a tab lands on Overview")
        page.set_viewport_size({"width": 1500, "height": 900})
        page.goto(f"{base}?tab={board.tabs['Overview']}")
        settle()
        last_card = page.get_by_text("show only stations marked").bounding_box()
        check(last_card is not None and last_card["y"] + last_card["height"] < 900, "Overview fits on one 1500 x 900 screen without scrolling")
        page.screenshot(path=str(folder / "overview.png"))
        page.set_viewport_size({"width": 1500, "height": 2300})

        for link, tab, expect in [
            ("Reliability →", "Reliability", "How to read this tab"),
            ("Map →", "Map", None),
            ("Station monitoring →", "Station monitoring", "Reporting coverage"),
        ]:
            target = follow(link)
            landed = f"tab={board.tabs[tab]}" in target.url and (expect is None or expect in target.inner_text("body"))
            check(landed, f"Overview link '{link}' opens the {tab} tab")
            if target is not page:
                target.close()

        target = follow("afternoons (14:00–18:00) of 10–12 November")
        body = target.inner_text("body")
        check("14:00 to 18:00" in body and "2026-11-10 to 2026-11-12" in body, "Overview example link opens Reliability with 10-12 Nov and 14:00-18:00 already applied")
        target.screenshot(path=str(folder / "reliability_example.png"))
        if target is not page:
            target.close()

        target = follow('show only stations marked "Check station"')
        body = target.inner_text("body")
        check("Reporting normally" not in body and "limit 2 h" in body, "Overview example link opens Station monitoring showing only 'Check station' rows")
        target.screenshot(path=str(folder / "monitoring_check_only.png"))
        if target is not page:
            target.close()

        for name, tab, query in [
            ("reliability_all", "Reliability", ""),
            ("reliability_soupetard", "Reliability", f"&station={quote(SOUPETARD)}"),
            ("reliability_empty", "Reliability", "&target_date=2026-07-15"),
            ("map", "Map", ""),
            ("monitoring_all", "Station monitoring", ""),
        ]:
            page.goto(f"{base}?tab={board.tabs[tab]}{query}")
            settle()
            page.screenshot(path=str(folder / f"{name}.png"))
        print(f"screenshots saved in {folder}")
        browser.close()


def main() -> None:
    board = Dashboard(Metabase().login())
    api_checks(board)
    if len(sys.argv) > 1:
        browser_review(board, Path(sys.argv[1]))
    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
