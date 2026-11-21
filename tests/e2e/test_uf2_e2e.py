"""E2E test of the UF2 steps in Chromium (feature browser-flashing, criterion 6)."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_smoke_e2e import choose_variant, fill_config_form

pytestmark = pytest.mark.e2e


def test_nrf52_build_shows_the_uf2_steps_and_no_flash_control(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    fill_config_form(page)
    choose_variant(page, "rak4631")
    page.click("#buildBtn")
    expect(page.locator('[data-step="complete"]')).to_have_attribute("data-state", "done", timeout=15_000)

    expect(page.locator("#uf2-panel")).to_be_visible()
    expect(page.locator("#uf2-steps li")).to_have_count(4)
    expect(page.locator("#uf2-step-3")).to_have_text("Press the reset button two times quickly. A USB drive appears.")
    download = page.locator("#uf2-download")
    expect(download).to_be_visible()
    expect(download).to_have_attribute(
        "href", re.compile(r"^/api/v1/builds/[^/]+/artifacts/firmware-rak4631-[^/]+\.uf2$")
    )
    assert page.request.get(live_server + download.get_attribute("href")).ok
    expect(page.locator("#flash-start")).to_have_count(0)
    expect(page.locator("#flash-panel")).to_have_count(0)
