"""Drive the map page in a real browser and check it against BigQuery.

    uv run --with playwright python metabase/verify_map.py [screenshot-folder]

Uses the installed Microsoft Edge. Walks through the workflow: pick a date and
16:00, choose J7, switch from Forecast to Error vs observed, change to J1, and
checks that the date and hour never change. Then compares the values shown for
every station with forecast_comparison, and tests a missing observation, the
J0 reference and a date outside the loaded range.
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from client import DATABASE_NAME, Metabase

MAP_URL = os.environ.get("MAP_URL", "http://localhost:3001/index.html")
DATE, HOUR = "2026-11-18", 16


def expected_rows(mb: Metabase, lead: int) -> dict[str, dict]:
    database = next(d for d in mb.get("database")["data"] if d["name"] == DATABASE_NAME)
    sql = f"""
        SELECT s.station_label, c.forecast_temperature_c, c.observed_temperature_c, c.error_c, c.observation_method
        FROM analytics.forecast_comparison AS c
        JOIN analytics.stg_stations AS s USING (station_id)
        WHERE c.valid_date_local = '{DATE}' AND c.valid_hour_local = {HOUR} AND c.lead_days = {lead}
    """
    result = mb.post("dataset", {"database": database["id"], "type": "native", "native": {"query": sql}})
    return {
        r[0]: {"forecast": r[1], "observed": r[2], "error": r[3], "method": r[4]}
        for r in result["data"]["rows"]
    }


def table(page) -> dict[str, list[str]]:
    rows = page.locator("#rows tr").all()
    return {r.locator("td").nth(0).inner_text(): [c.inner_text() for c in r.locator("td").all()[1:]] for r in rows}


def compare_errors(shown: dict, expected: dict, lead: int) -> None:
    """The page rounds to 0.1 °C, so compare numerically, not as text."""
    for station, values in expected.items():
        text = shown[station][2]
        if values["error"] is None:
            ok = text == "unavailable"
        else:
            ok = text != "unavailable" and abs(float(text.replace(" °C", "")) - values["error"]) <= 0.051
        if not ok:
            check(False, f"J{lead} error for {station}: page {text} vs BigQuery {values['error']}")


def state(page) -> dict:
    return page.evaluate("() => ({date: __state.date, hour: __state.hour, lead: __state.lead, mode: __state.mode})")


def check(condition: bool, message: str) -> None:
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        raise SystemExit(1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # messages contain °C and arrows; Windows defaults to cp1252
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    mb = Metabase().login()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1300, "height": 1000})
        page.goto(MAP_URL)
        page.wait_for_selector("#rows tr")

        def shot(name: str) -> None:
            if shots:
                page.wait_for_timeout(1200)  # let map tiles load
                page.screenshot(path=str(shots / f"map_{name}.png"), full_page=True)

        # 1. date and 16:00
        page.fill("#date", DATE)
        page.dispatch_event("#date", "change")
        page.select_option("#hour", str(HOUR))
        check(state(page) == {"date": DATE, "hour": HOUR, "lead": 1, "mode": "forecast"}, f"date {DATE} and {HOUR}:00 selected")

        # 2. J7, still Forecast
        page.click("[data-lead='7']")
        check(state(page) == {"date": DATE, "hour": HOUR, "lead": 7, "mode": "forecast"}, "J7 chosen; date and hour unchanged")
        shot("1_forecast_j7")

        # 3. switch to Error vs observed
        page.click("[data-mode='error']")
        check(state(page) == {"date": DATE, "hour": HOUR, "lead": 7, "mode": "error"}, "switched to Error vs observed; date, hour and lead unchanged")
        expected = expected_rows(mb, 7)
        shown = table(page)
        compare_errors(shown, expected, 7)
        check(True, f"J7 errors match BigQuery for {len(expected)} stations")
        legend_j7 = page.inner_text("#legend-ticks")
        shot("2_error_j7")

        # 4. change to J1
        page.click("[data-lead='1']")
        check(state(page) == {"date": DATE, "hour": HOUR, "lead": 1, "mode": "error"}, "changed to J1; date, hour and mode unchanged")
        check(page.input_value("#date") == DATE and page.input_value("#hour") == str(HOUR), "date and hour controls still show the selection")
        check(page.inner_text("#legend-ticks") == legend_j7, "error colour scale identical at J7 and J1")
        expected = expected_rows(mb, 1)
        shown = table(page)
        compare_errors(shown, expected, 1)
        check(True, f"J1 errors match BigQuery for {len(expected)} stations")

        # 5. station with no observation stays visible, error unavailable
        missing = [s for s, v in expected.items() if v["error"] is None]
        check(len(missing) > 0, f"stations without an observation at this hour: {missing}")
        check(page.locator("#rows tr").count() == len(expected), "every station is still listed")
        station = missing[0]
        page.locator("#rows tr", has_text=station).click()
        details = page.inner_text("#details")
        check("unavailable" in details and station in details, f"clicking {station} shows the error as unavailable")
        shot("3_error_j1_missing_selected")

        # 6. station with an observation: details panel
        station = next(s for s, v in expected.items() if v["error"] is not None)
        page.locator("#rows tr", has_text=station).click()
        details = page.inner_text("#details")
        check(all(word in details for word in ("Forecast (J1)", "Observed reference", "Signed error", "Observation method")), f"clicking {station} shows forecast, observed, error and method")
        check(state(page) == {"date": DATE, "hour": HOUR, "lead": 1, "mode": "error"}, "selecting a station does not change date, hour, lead or mode")

        # 7. Observed and back to Forecast keep everything else
        page.click("[data-mode='observed']")
        page.click("[data-mode='forecast']")
        check(state(page) == {"date": DATE, "hour": HOUR, "lead": 1, "mode": "forecast"}, "switching modes back and forth preserves date, hour and lead")

        # 8. J0 is labelled as the reference
        page.click("[data-lead='0']")
        check("current-run reference" in page.inner_text("#notice") and "current-run reference" in page.inner_text("#summary"), "J0 is labelled as the current-run reference")
        shot("4_forecast_j0")

        # 8b. explanations and snapshot information
        help_text = page.inner_text(".help")
        check("15 minutes later" in help_text and "J0" in help_text and "never 0" in help_text, "the page explains the observed reference, distinguishes it from J0, and says a missing error is not 0")
        snapshot = page.inner_text("#snapshot")
        check("snapshot generated" in snapshot and "data collected 2026-11-19 11:16 UTC" in snapshot, f"snapshot line: '{snapshot}'")

        # 9. outside the loaded range
        page.click("[data-lead='1']")
        page.fill("#date", "2026-01-15")
        page.dispatch_event("#date", "change")
        check("No data" in page.inner_text("#notice") and "Loaded range" in page.inner_text("#notice"), "a date outside the data shows the empty state with the loaded range")
        check(state(page)["hour"] == HOUR, "hour still 16:00 after picking an empty date")
        shot("5_empty")
        browser.close()


if __name__ == "__main__":
    main()
