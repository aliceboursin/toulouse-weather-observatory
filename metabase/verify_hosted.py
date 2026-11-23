"""Check the hosted demo from a fresh browser session, with no login.

    $env:METABASE_URL = terraform -chdir=infra/terraform output -raw metabase_url
    $env:MAP_URL = terraform -chdir=infra/terraform output -raw map_url
    uv run python metabase/verify_hosted.py [screenshot-folder]

Opens the dashboard's public link in a new Microsoft Edge profile (no cookies,
no Metabase session) and checks:

- the Overview page and each of its links, including the June heatwave example;
- Reliability for a June period and a November period;
- the map inside the dashboard, in Forecast, Observed and Error modes, for a
  June hour and a November hour.

Expected values come from BigQuery through the Metabase API, which this script
calls separately as the admin.
"""

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import build_dashboard as build
from client import METABASE_URL, Metabase
from verify_dashboard import Dashboard

failures = []
JUNE = ("2026-06-20", "2026-06-25")
NOVEMBER = ("2026-11-10", "2026-11-12")
MAP_HOURS = [("2026-06-23", 18, "June heatwave peak"), ("2026-11-18", 16, "November")]


def check(condition: bool, message: str) -> None:
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # messages contain °C and arrows; Windows defaults to cp1252
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    board = Dashboard(Metabase().login())
    public = f"{METABASE_URL}/public/dashboard/{board.data['public_uuid']}"
    print(f"Public link: {public}\n")

    def period(dates):
        return board.reference(f"c.valid_date_local BETWEEN '{dates[0]}' AND '{dates[1]}'")

    june, november = period(JUNE), period(NOVEMBER)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(viewport={"width": 1500, "height": 1500})  # fresh: no cookies
        page = context.new_page()

        def settle(target=None, wait=6000):
            target = target or page
            target.wait_for_load_state("networkidle")
            target.wait_for_timeout(wait)

        def shot(name: str, target=None) -> None:
            if folder:
                (target or page).screenshot(path=str(folder / f"hosted_{name}.png"))

        def follow(link_text: str):
            """Click a link on Overview; it may open in the same tab or a new one."""
            page.goto(public)
            settle(wait=4000)
            before = len(context.pages)
            page.get_by_role("link", name=link_text).first.click()
            page.wait_for_timeout(2500)
            target = context.pages[-1] if len(context.pages) > before else page
            settle(target)
            return target

        def done(target) -> None:
            if target is not page:
                target.close()

        check(not context.cookies(METABASE_URL), "fresh browser session: no Metabase cookie before opening the link")

        # ------------------------------------------------------------ Overview
        page.goto(public)
        settle()
        body = page.inner_text("body")
        check("How reliable are temperature forecasts for Toulouse?" in body and "Sign in" not in body, "the public link opens on Overview without a login")
        check(build.DASHBOARD in page.title() and "V2" not in body, f"the page title is '{build.DASHBOARD}', with no 'V2'")
        check("Data available: 1 April 2026 to 19 November 2026" in body, "Overview shows the available date range in one line")
        check("UTC" not in body and "snapshot" not in body.lower(), "Overview carries no UTC timestamps or build details")
        check("Start with the June 2026 heatwave" in body and "What you find" not in body, "Overview has three action links and the June heatwave as its example")
        shot("overview")

        for link, tab, expect in [
            ("Assess forecast reliability →", "Reliability", "Reading the scores"),
            ("Explore an hour on the map →", "Temperature map", None),
            ("Check station reporting →", "Station monitoring", "Reporting coverage"),
        ]:
            target = follow(link)
            landed = f"tab={board.tabs[tab]}" in target.url and "/public/dashboard/" in target.url
            check(landed and (expect is None or expect in target.inner_text("body")), f"Overview link '{link}' opens the {tab} tab without a login")
            done(target)

        target = follow("Open 20–25 June in Reliability")
        body = target.inner_text("body")
        rate = f"{june['rate'] * 100:.1f}%"
        check(f"{JUNE[0]} to {JUNE[1]}" in body and "June 20, 2026 - June 25, 2026" in body, f"the June heatwave example opens Reliability on {JUNE[0]} to {JUNE[1]}")
        check(re.search(r"Lead in line charts \(J\):\s*7", body) is not None, "the June example sets the line chart to the 7-day forecast")
        check("hour by hour" in body and "daily means" not in body, "the June example shows the hourly chart, not the daily one")
        check(f"{june['comparable']:,}" in body and rate in body, f"June 20-25: {june['comparable']:,} valid comparisons per lead, coverage {rate}, as in BigQuery")
        j1, j7 = f"{june['scores'][0][2]:.2f}", f"{june['scores'][-1][2]:.2f}"
        check(j1 in body and j7 in body, f"June 20-25: mean absolute error {j1} °C at J1 and {j7} °C at J7, as in BigQuery")
        shot("reliability_june", target)
        done(target)

        # -------------------------------------------- Reliability, whole period
        page.goto(f"{public}?tab={board.tabs['Reliability']}")
        settle()
        body = page.inner_text("body")
        check("daily means" in body and "hour by hour" not in body, "with no date selected, Reliability shows the daily chart, not the hourly one")
        shot("reliability_all")

        # ------------------------------------------------- Monitoring, filtered
        page.goto(f"{public}?tab={board.tabs['Station monitoring']}&status=Check%20station")
        settle()
        body = page.inner_text("body")
        check("Reporting normally" not in body and "limit 2 h" in body, "Station monitoring with Status = Check station shows only those rows")

        # ------------------------------------------------- Reliability, November
        page.goto(f"{public}?tab={board.tabs['Reliability']}&target_date={NOVEMBER[0]}~{NOVEMBER[1]}")
        settle()
        body = page.inner_text("body")
        rate = f"{november['rate'] * 100:.1f}%"
        j1 = f"{november['scores'][0][2]:.2f}"
        check(f"{NOVEMBER[0]} to {NOVEMBER[1]}" in body and str(november["comparable"]) in body and rate in body and j1 in body, f"November 10-12: {november['comparable']} valid comparisons per lead, coverage {rate}, J1 error {j1} °C, as in BigQuery")
        check(november["comparable"] == 505 and j1 == "0.69", "November 10-12 is unchanged by the backfill (505 comparisons per lead, J1 error 0.69 °C)")
        shot("reliability_november")

        # ---------------------------------------------------------------- map
        page.goto(f"{public}?tab={board.tabs['Temperature map']}")
        settle()
        frame = next((f for f in page.frames if f.url.startswith(build.MAP_URL)), None)
        check(frame is not None, f"the Temperature map tab embeds the hosted map page ({build.MAP_URL})")
        if frame:
            frame.wait_for_selector("#rows tr")
            check(frame.get_attribute("#date", "min") == "2026-04-01", "the map's date picker starts on 1 April 2026")

            def rows():
                return {
                    r.locator("td").nth(0).inner_text(): [c.inner_text() for c in r.locator("td").all()[1:]]
                    for r in frame.locator("#rows tr").all()
                }

            def close(text: str, value) -> bool:
                if value is None:
                    return text in ("no reading", "unavailable")
                return abs(float(text.replace(" °C", "")) - value) <= 0.051

            for day, hour, label in MAP_HOURS:
                frame.fill("#date", day)
                frame.dispatch_event("#date", "change")
                frame.select_option("#hour", str(hour))
                frame.click("[data-lead='7']")
                want = {
                    r[0]: r[1:]
                    for r in board.sql(f"""
                        SELECT s.station_label, c.forecast_temperature_c, c.observed_temperature_c, c.error_c
                        FROM analytics.forecast_comparison AS c JOIN analytics.stg_stations AS s USING (station_id)
                        WHERE c.valid_date_local = '{day}' AND c.valid_hour_local = {hour} AND c.lead_days = 7""")
                }
                results = []
                for mode, title, column in [("forecast", "Forecast temperature", 0), ("observed", "Observed temperature", 1), ("error", "Signed error", 2)]:
                    frame.click(f"[data-mode='{mode}']")
                    shown = rows()
                    results.append(title in frame.inner_text("#summary") and all(close(shown[s][column], want[s][column]) for s in want))
                    page.wait_for_timeout(1200)
                state = frame.evaluate("() => `${__state.date} ${__state.hour}:00 J${__state.lead}`")
                compared = sum(1 for v in want.values() if v[2] is not None)
                check(all(results) and state == f"{day} {hour}:00 J7", f"map, {label} ({day} {hour}:00, J7): Forecast, Observed and Error match BigQuery for {len(want)} stations ({compared} with an observation); selection kept across modes")
                shot(f"map_{day}")
                if day.startswith("2026-06"):
                    errors = [v[2] for v in want.values() if v[2] is not None]
                    frame.click("[data-lead='1']")
                    check(sum(errors) / len(errors) < -4 and frame.evaluate("() => __state.date") == day, f"map, June peak: at J7 forecasts were {sum(errors) / len(errors):.1f} °C off on average; switching to J1 keeps the date and hour")
            check("data collected 2026-11-19 11:16 UTC" in frame.inner_text("#snapshot"), "the map's snapshot line gives the latest data collection (19 Nov), not the backfill run")
        browser.close()

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
