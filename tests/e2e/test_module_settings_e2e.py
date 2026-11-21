"""E2E tests of the Modules tab of the build form (feature module-settings, FR 1, 3 and 7)."""

import re

import pytest
from playwright.sync_api import Page, expect

from mtfwbuilder.models import MODULE_FIELDS
from tests.e2e.conftest import open_tab

pytestmark = pytest.mark.e2e

NEIGHBOR_INFO_MESSAGE = "The firmware uses at least 14400 seconds (4 hours)."
IS_INVALID = re.compile(r"\bis-invalid\b")
# One field of each control kind: a toggle, an enum, a number, a negative number, a pin and the text field.
MODULE_VALUES = {
    "serial_enabled": "true",
    "serial_mode": "NMEA",
    "serial_rxd": "44",
    "neighbor_info_update_interval": "14400",
    "paxcounter_wifi_threshold": "-80",
    "detection_sensor_name": "Door",
}


def _remove_every_profile(page: Page, base_url: str) -> None:
    """Delete each profile, because the tests of this module share one server."""
    for profile in page.request.get(f"{base_url}/api/v1/profiles").json():
        page.request.delete(f"{base_url}/api/v1/profiles/{profile['id']}")


def _field(page: Page, name: str):
    return page.locator(f'#configForm [name="{name}"]')


def _fill_module_fields(page: Page, values: dict) -> None:
    open_tab(page, "modules")
    for name, value in values.items():
        field = _field(page, name)
        if field.evaluate("element => element.tagName") == "SELECT":
            field.select_option(value)
        else:
            field.fill(value)


def test_profile_with_module_fields_loads_them_after_a_reload(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    page.goto(f"{live_server}/")
    _fill_module_fields(page, MODULE_VALUES)
    page.fill("#profileName", "Module profile")

    page.click("#saveProfileBtn")

    expect(page.locator("#profile-status")).to_contain_text("Saved the profile Module profile.")
    page.reload()
    for name in MODULE_VALUES:
        expect(_field(page, name)).to_have_value("")
    page.select_option("#profileSelect", label="Module profile")

    page.click("#loadProfileBtn")

    expect(page.locator("#profile-status")).to_contain_text("Loaded the profile Module profile.")
    for name, value in MODULE_VALUES.items():
        expect(_field(page, name)).to_have_value(value)
    expect(page.locator("#config-tab-modules .tab-count")).to_have_text(str(len(MODULE_VALUES)))


def test_profile_saved_before_the_modules_tab_leaves_every_module_field_empty(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    response = page.request.post(
        f"{live_server}/api/v1/profiles", data={"name": "Old profile", "config": {"owner_long_name": "Ridge Node"}}
    )
    assert response.status == 201
    page.goto(f"{live_server}/")
    _fill_module_fields(page, MODULE_VALUES)
    page.select_option("#profileSelect", label="Old profile")

    page.click("#loadProfileBtn")

    expect(page.locator("#ownerLongName")).to_have_value("Ridge Node")
    for name in MODULE_FIELDS:
        expect(_field(page, name)).to_have_value("")
    expect(page.locator("#config-tab-modules .tab-count")).to_have_text("0")


def test_a_wrong_module_field_shows_its_error_and_opens_the_modules_tab(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    _fill_module_fields(page, {"neighbor_info_update_interval": "3600"})
    open_tab(page, "device")

    page.click("#previewBtn")

    # The build form shows each field error with the "Value error, " prefix of pydantic.
    expect(page.locator("#error-neighbor_info_update_interval")).to_contain_text(NEIGHBOR_INFO_MESSAGE)
    expect(page.locator("#config-tab-modules")).to_have_attribute("aria-selected", "true")
    expect(_field(page, "neighbor_info_update_interval")).to_have_class(IS_INVALID)
    expect(page.locator("#config-tab-modules .tab-error-dot")).to_be_visible()
