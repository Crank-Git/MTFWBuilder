"""E2E tests of the device picker in a browser (generated-registry criteria 3, 4 and 5)."""

import pytest
from playwright.sync_api import Page, expect

from tests.conftest import FIRMWARE_VERSION
from tests.e2e.conftest import ALPHA_VERSION
from tests.e2e.test_smoke_e2e import fill_config_form

pytestmark = pytest.mark.e2e

ROWS = "#device-results [data-env]"


def test_picker_without_the_toggle_shows_no_extra_variant(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    expect(page.locator(f'{ROWS}[data-env="rak4631"]')).to_be_visible()
    expect(page.locator(f'{ROWS}[data-env="rak4631_dbg"]')).to_have_count(0)


def test_picker_with_the_toggle_shows_extra_variants(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    expect(page.locator(f'{ROWS}[data-env="rak4631"]')).to_be_visible()

    page.check("#device-experimental")

    expect(page.locator(f'{ROWS}[data-env="rak4631_dbg"]')).to_be_visible()


def test_picker_architecture_chip_keeps_only_that_architecture(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    # The chip input is visually hidden, so the operator clicks its label.
    page.click('label[for="device-arch-rp2040"]')

    expect(page.locator(ROWS)).to_have_count(2)
    expect(page.locator(f'{ROWS}[data-env="pico"]')).to_be_visible()
    expect(page.locator("#device-arch-rp2040")).to_be_checked()


def test_picker_search_without_a_match_clears_with_the_clear_control(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    page.fill("#device-search", "zzz")
    expect(page.locator("#device-results")).to_contain_text("No device matches 'zzz'.")
    page.click("#device-clear")

    expect(page.locator("#device-search")).to_have_value("")
    expect(page.locator(f'{ROWS}[data-env="heltec-v4"]')).to_be_visible()


def test_picker_follows_the_firmware_version_selector(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    expect(page.locator(f'{ROWS}[data-env="heltec-v4"]')).to_be_visible()

    page.select_option("#firmware_version", ALPHA_VERSION)

    expect(page.locator(f'{ROWS}[data-env="heltec-v4-r2"]')).to_be_visible()
    expect(page.locator(f'{ROWS}[data-env="heltec-v4"]')).to_have_count(0)


def test_search_heltec_v4_then_choose_heltec_v4_builds_to_complete(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    fill_config_form(page)
    expect(page.locator("#buildBtn")).to_be_disabled()

    page.fill("#device-search", "heltec v4")
    row = page.locator(f'{ROWS}[data-env="heltec-v4"]')
    expect(row).to_be_visible()
    row.click()

    expect(page.locator("#variant")).to_have_value("heltec-v4")
    expect(row).to_have_attribute("aria-pressed", "true")
    page.click("#buildBtn")

    expect(page.locator('[data-step="complete"]')).to_have_attribute("data-state", "done", timeout=15_000)
    expect(page.locator("#build-state tr", has_text=".factory.bin")).to_contain_text("heltec-v4")
    expect(page.locator("#build-state tr", has_text=".factory.bin")).to_contain_text(FIRMWARE_VERSION.removeprefix("v"))


def test_version_switch_clears_a_chosen_env_that_the_new_version_lacks(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    fill_config_form(page)
    page.locator(f'{ROWS}[data-env="heltec-v4"]').click()
    expect(page.locator("#variant")).to_have_value("heltec-v4")

    page.select_option("#firmware_version", ALPHA_VERSION)

    expect(page.locator("#variant")).to_have_value("")
    expect(page.locator(f'{ROWS}[data-env="heltec-v4-r2"]')).to_be_visible()
    expect(page.locator(f'{ROWS}[aria-pressed="true"]')).to_have_count(0)
    expect(page.locator("#buildBtn")).to_be_disabled()


def test_version_switch_keeps_a_chosen_env_that_the_new_version_holds(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    page.locator(f'{ROWS}[data-env="rak4631"]').click()

    page.select_option("#firmware_version", ALPHA_VERSION)

    expect(page.locator(f'{ROWS}[data-env="heltec-v4-r2"]')).to_be_visible()
    expect(page.locator("#variant")).to_have_value("rak4631")
    expect(page.locator(f'{ROWS}[data-env="rak4631"]')).to_have_attribute("aria-pressed", "true")
