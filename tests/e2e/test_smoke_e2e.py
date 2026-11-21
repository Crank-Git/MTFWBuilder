"""E2E smoke tests of the build page in a real browser (feature frontend-htmx, criteria 1 and 2)."""

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

PSK_HEX = "deadbeef" * 8


def fill_config_form(page: Page) -> None:
    """Fill the fields of the config form that a valid userPrefs file needs."""
    page.fill("#ownerShortName", "E2E")
    page.fill("#ownerLongName", "E2E Node")
    page.fill("#channel0Name", "Primary")
    page.fill("#channel0PSK", PSK_HEX)


def test_preview_shows_the_userprefs_file(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    fill_config_form(page)

    page.click("#previewBtn")

    expect(page.locator("#preview-container")).to_contain_text("USERPREFS_CHANNELS_TO_WRITE")


def test_preview_with_a_15_byte_psk_shows_the_message_under_the_psk_field(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    fill_config_form(page)
    page.fill("#channel0PSK", "ab" * 15)

    page.click("#previewBtn")

    expect(page.locator('[id="error-channel_0.psk"]')).to_contain_text("the PSK is not 0, 1, 16 or 32 bytes")
    expect(page.locator("#channel0PSK")).to_have_class(re.compile(r"\bis-invalid\b"))
    expect(page.locator("#channel0PSK")).to_be_focused()


def test_build_completes_and_downloads_the_factory_artifact(page: Page, live_server: str, tmp_path):
    page.goto(f"{live_server}/")
    fill_config_form(page)
    page.select_option("#variant", "heltec-v3")

    page.click("#buildBtn")

    expect(page.locator('[data-step="complete"]')).to_have_attribute("data-state", "done", timeout=15_000)
    factory = page.locator("#build-state tr", has_text=".factory.bin").locator("a[download]")
    with page.expect_download() as info:
        factory.click()
    download = info.value
    assert download.suggested_filename.endswith(".factory.bin")
    saved = tmp_path / download.suggested_filename
    download.save_as(saved)
    assert saved.read_bytes().startswith(b"fake firmware-heltec-v3-")
