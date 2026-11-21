"""E2E test of a fleet run: create a fleet, add 3 fleet devices, run it and download all (feature fleet-builds)."""

import csv
import io
import re
import zipfile

import pytest
from playwright.sync_api import Page, expect

from tests.conftest import ADMIN_PASSWORD

pytestmark = pytest.mark.e2e

PSK = "deadbeef" * 8
PROFILE_CONFIG = {"channels_to_write": "1", "channel_0[name]": "Primary", "channel_0[psk]": PSK}
LABELS = ("hill-1", "hill-2", "team-a")
DEVICE = {"label": "admin-1", "variant": "heltec-v3"}


def test_a_fleet_run_of_3_devices_shows_3_complete_builds_and_downloads_one_zip(page: Page, live_server: str):
    profile = page.request.post(
        f"{live_server}/api/v1/profiles", data={"name": "Ridge SAR base", "config": PROFILE_CONFIG}
    )
    assert profile.status == 201
    page.goto(f"{live_server}/fleets")
    page.fill("#fleetName", "Ridge SAR 2026")
    page.select_option("#fleetProfile", label="Ridge SAR base")

    page.click("#createFleetBtn")

    expect(page.locator("h3", has_text="Fleet: Ridge SAR 2026")).to_be_visible()
    for label in LABELS:
        page.click("#addRowBtn")
        row = page.locator("#fleet-devices tbody tr.fleet-row").last
        row.locator("[name=label]").fill(label)
        row.locator("[name=variant]").fill("heltec-v3")
    page.click("#saveDevicesBtn")
    expect(page.locator("#fleet-status")).to_contain_text("Saved 3 fleet devices.")
    # The Start control counts the saved fleet devices, so a reload shows the new count.
    page.reload()
    page.fill("input[data-secret='channel_0[psk]']", PSK)
    expect(page.locator("#startRunBtn")).to_have_text("Start fleet run (3 builds)")

    page.click("#startRunBtn")

    page.wait_for_url("**/fleet-runs/*")
    statuses = page.locator("#fleet-run-devices .fleet-run-build-status")
    expect(statuses).to_have_text(["complete"] * 3, timeout=30_000)
    expect(page.locator("#fleet-run-status")).to_have_text("complete")
    expect(page.locator("#fleet-run-devices .fleet-run-row td:first-child")).to_have_text(list(LABELS))
    stored = page.evaluate("JSON.stringify([{...window.localStorage}, {...window.sessionStorage}])")
    assert PSK not in stored

    with page.expect_download() as download_info:
        page.click("#downloadAllBtn")

    archive = zipfile.ZipFile(io.BytesIO(download_info.value.path().read_bytes()))
    assert {name.split("/")[0] for name in archive.namelist() if "/" in name} == set(LABELS)
    manifest = archive.read("manifest.csv").decode()
    assert [row["status"] for row in csv.DictReader(io.StringIO(manifest))] == ["complete"] * 3
    assert PSK not in manifest


def test_an_admin_starts_a_fleet_run_with_the_csrf_token_and_gets_a_random_run_url(page: Page, live_server: str):
    page.goto(f"{live_server}/admin")
    page.fill("#adminKey", ADMIN_PASSWORD)
    page.click("button[type=submit]:has-text('Sign in')")
    profile = page.request.post(f"{live_server}/api/v1/profiles", data={"name": "Admin run base", "config": {}})
    body = {"name": "Admin fleet", "profile_id": profile.json()["id"], "devices": [DEVICE]}
    fleet = page.request.post(f"{live_server}/api/v1/fleets", data=body)
    assert fleet.status == 201
    page.goto(f"{live_server}/fleets/{fleet.json()['id']}")

    page.click("#startRunBtn")

    page.wait_for_url(re.compile(r".*/fleet-runs/[0-9a-f]{32}$"))
    expect(page.locator("#fleet-run-status")).to_have_text("complete", timeout=30_000)
