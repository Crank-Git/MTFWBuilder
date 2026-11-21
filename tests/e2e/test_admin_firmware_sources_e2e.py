"""E2E test of the Firmware sources section of the admin page in a real browser (feature 09, FR 13)."""

import pytest
from playwright.sync_api import Page, expect

from tests.conftest import ADMIN_PASSWORD

pytestmark = pytest.mark.e2e


def test_admin_without_a_firmware_source_sees_install_stable_and_the_upstream_list(page: Page, empty_server: str):
    page.goto(f"{empty_server}/admin")
    page.fill("#adminKey", ADMIN_PASSWORD)

    page.click("button[type=submit]:has-text('Sign in')")

    section = page.locator("#firmware-sources")
    expect(section).to_contain_text("No firmware version is installed.")
    expect(section.locator("#install-stable")).to_be_visible()
    expect(section.locator("#install-stable")).to_have_text("Install stable")
    expect(section.locator("#firmware-available tr[data-tag='v2.7.26.54e0d8d']")).to_be_visible()
