"""E2E tests of the tab layout of the config form (feature 11, Screens & states, FR-config-coverage-17)."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import open_tab

pytestmark = pytest.mark.e2e


def count(page: Page, tab_id: str) -> int:
    """Return the count of fields with a value that the label of a config tab shows."""
    return int(page.locator(f"#config-tab-{tab_id} .tab-count").inner_text())


def test_a_click_on_a_tab_shows_its_pane_and_hides_the_other_panes(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    expect(page.locator("#config-pane-device")).to_be_visible()
    expect(page.locator("#config-pane-security")).to_be_hidden()

    open_tab(page, "security")

    expect(page.locator("#config-pane-security")).to_be_visible()
    expect(page.locator("#config-pane-device")).to_be_hidden()
    expect(page.locator("#config-tab-security")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#config-tab-device")).to_have_attribute("aria-selected", "false")


def test_arrow_keys_home_and_end_move_the_selection_along_the_tabs(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    page.focus("#config-tab-device")

    page.keyboard.press("ArrowRight")
    expect(page.locator("#config-tab-channels")).to_be_focused()
    expect(page.locator("#config-pane-channels")).to_be_visible()

    page.keyboard.press("End")
    expect(page.locator("#config-tab-advanced")).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#config-tab-device")).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(page.locator("#config-tab-advanced")).to_be_focused()
    page.keyboard.press("Home")
    expect(page.locator("#config-tab-device")).to_be_focused()
    expect(page.locator("#config-tab-device")).to_have_attribute("tabindex", "0")
    expect(page.locator("#config-tab-advanced")).to_have_attribute("tabindex", "-1")


def test_tab_count_goes_up_by_one_for_a_field_that_gets_a_value(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    before = count(page, "device")

    page.fill("#ownerShortName", "E2E")

    expect(page.locator("#config-tab-device .tab-count")).to_have_text(str(before + 1))


def test_channel_tab_count_follows_the_number_of_channels(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "channels")
    page.select_option("#channelsToWrite", "1")
    one = count(page, "channels")

    page.select_option("#channelsToWrite", "2")

    # Each channel has a default position precision, so a second channel adds one field with a value.
    expect(page.locator("#config-tab-channels .tab-count")).to_have_text(str(one + 1))


def test_time_zone_control_shows_the_posix_string_of_america_new_york(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    page.select_option("#tzString", "America/New_York")

    expect(page.locator("#tzString")).to_have_value("America/New_York")
    expect(page.locator("#tzPosix")).to_have_text("EST5EDT,M3.2.0,M11.1.0")
    expect(page.locator("#tzPosixNote")).to_be_visible()


def test_a_field_error_opens_its_tab_focuses_the_field_and_marks_the_tab(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "security")
    page.check("#securityIsManaged")
    open_tab(page, "device")

    page.click("#previewBtn")

    expect(page.locator("#config-pane-security")).to_be_visible()
    expect(page.locator("#securityIsManaged")).to_be_focused()
    expect(page.locator("#config-tab-security .tab-error-dot")).to_be_visible()
    expect(page.locator("#config-tab-device .tab-error-dot")).to_be_hidden()


def test_a_field_error_in_a_closed_accordion_part_opens_it(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "advanced")
    page.click("#otherSettingsHeading button")
    page.fill("#cannedMessages", "Yes|No")
    page.click("#otherSettingsHeading button")
    expect(page.locator("#otherSettingsCollapse")).not_to_have_class(re.compile(r"\bshow\b"))
    open_tab(page, "device")

    page.click("#previewBtn")

    expect(page.locator("#cannedMessages")).to_be_focused()
    expect(page.locator("#cannedMessages")).to_be_visible()
