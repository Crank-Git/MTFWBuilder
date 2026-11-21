"""E2E tests of the fleet editor: CSV import and table form, with row errors (feature fleet-builds, FR 12 and 13)."""

import pytest
from playwright.sync_api import Page, expect

from mtfwbuilder.meshtastic_enums import ROUTER_ROLE_MESSAGE

pytestmark = pytest.mark.e2e

HEADER = "label,variant,long_name,short_name,role,lat,lon,alt,admin_key"


def _new_fleet(page: Page, base_url: str) -> int:
    """Return the id of a new fleet without fleet devices, after the removal of each fleet of the shared server."""
    for fleet in page.request.get(f"{base_url}/api/v1/fleets").json():
        page.request.delete(f"{base_url}/api/v1/fleets/{fleet['id']}")
    for profile in page.request.get(f"{base_url}/api/v1/profiles").json():
        page.request.delete(f"{base_url}/api/v1/profiles/{profile['id']}")
    profile = page.request.post(f"{base_url}/api/v1/profiles", data={"name": "Ridge SAR base", "config": {}}).json()
    fleet = page.request.post(f"{base_url}/api/v1/fleets", data={"name": "Ridge SAR 2026", "profile_id": profile["id"]})
    assert fleet.status == 201
    return fleet.json()["id"]


def test_a_pasted_csv_with_a_bad_role_shows_row_2_and_the_fixed_csv_adds_3_rows(page: Page, live_server: str):
    fleet_id = _new_fleet(page, live_server)
    page.goto(f"{live_server}/fleets")
    page.click("text=Ridge SAR 2026")
    rows = [
        "hill-1,heltec-v3,Ridge Hill 1,RH1,CLIENT,,,,",
        "hill-2,heltec-v3,Ridge Hill 2,RH2,{},,,,",
        "team-a,t-echo,,,,,,,",
    ]
    page.fill("#csvText", "\n".join([HEADER, *rows]).format("ROUTER"))

    page.click("#importCsvBtn")

    errors = page.locator("#csv-errors li")
    expect(errors).to_have_count(1)
    expect(errors).to_have_text(f"Row 2: role: {ROUTER_ROLE_MESSAGE}")
    expect(page.locator("#fleet-devices tbody tr.fleet-row")).to_have_count(0)
    page.fill("#csvText", "\n".join([HEADER, *rows]).format("TRACKER"))

    page.click("#importCsvBtn")

    expect(page.locator("#csv-errors li")).to_have_count(0)
    expect(page.locator("#fleet-devices tbody tr.fleet-row")).to_have_count(3)
    expect(page.locator("#fleet-status")).to_contain_text("The fleet has 3 fleet devices.")
    page.reload()
    labels = page.locator("#fleet-devices tbody tr.fleet-row [name=label]")
    expect(labels).to_have_count(3)
    assert [labels.nth(n).input_value() for n in range(3)] == ["hill-1", "hill-2", "team-a"]
    assert page.request.get(f"{live_server}/api/v1/fleets/{fleet_id}").json()["devices"][1]["role"].endswith("TRACKER")


def test_the_table_form_shows_a_duplicate_label_next_to_its_row(page: Page, live_server: str):
    fleet_id = _new_fleet(page, live_server)
    page.goto(f"{live_server}/fleets/{fleet_id}")
    for label in ("gate-1", "gate-2", "gate-1"):
        page.click("#addRowBtn")
        page.locator("#fleet-devices tbody tr.fleet-row [name=label]").last.fill(label)

    page.click("#saveDevicesBtn")

    row_errors = page.locator("#fleet-devices tbody tr.fleet-row-errors")
    expect(row_errors.nth(2)).to_be_visible()
    expect(row_errors.nth(2)).to_contain_text("Row 3: label: the label gate-1 is in row 1 too")
    expect(row_errors.nth(0)).to_be_hidden()
    expect(page.locator("#fleet-status")).to_contain_text("The fleet devices were not saved. Fix 1 error.")
    page.locator("#fleet-devices tbody tr.fleet-row [data-remove-row]").nth(2).click()

    page.click("#saveDevicesBtn")

    expect(page.locator("#fleet-status")).to_contain_text("Saved 2 fleet devices.")
    expect(page.locator("#fleet-devices tbody tr.fleet-row .row-number")).to_have_text(["1", "2"])
