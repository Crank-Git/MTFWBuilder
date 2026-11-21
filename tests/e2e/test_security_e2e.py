"""E2E tests of the managed-mode control and the canned messages field (FR-config-coverage-12, -14)."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import open_tab

pytestmark = pytest.mark.e2e

MANAGED_MODE_MESSAGE = "Managed mode needs an admin key, or the device cannot be changed again."


def test_managed_mode_notice_shows_only_while_managed_mode_is_on(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "security")
    notice = page.locator("#managedModeNotice .managed-mode-notice")
    expect(notice).to_be_hidden()

    page.select_option("#securityIsManaged", "true")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("Managed mode is on.")

    page.select_option("#securityIsManaged", "false")
    expect(notice).to_be_hidden()


def test_managed_mode_live_region_stays_displayed_while_its_notice_shows_and_hides(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "security")
    region = page.locator("#managedModeNotice")
    displays = []

    for value in ("", "true", ""):
        page.select_option("#securityIsManaged", value)
        displays.append(region.evaluate("element => getComputedStyle(element).display"))

    expect(region).to_have_attribute("role", "status")
    expect(page.locator("#securityIsManaged")).to_have_attribute("aria-describedby", "managedModeNotice")
    assert "none" not in displays


def test_preview_with_managed_mode_and_no_admin_key_shows_the_message_under_managed_mode(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "security")
    page.select_option("#securityIsManaged", "true")

    page.click("#previewBtn")

    expect(page.locator("#error-security_is_managed")).to_contain_text(MANAGED_MODE_MESSAGE)


def test_preview_with_a_canned_message_that_contains_a_pipe_shows_a_field_error(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "advanced")
    page.click("#otherSettingsHeading button")
    page.fill("#cannedMessages", "OK\nYes|No")

    page.click("#previewBtn")

    expect(page.locator("#error-canned_messages")).to_contain_text("a message contains |")
