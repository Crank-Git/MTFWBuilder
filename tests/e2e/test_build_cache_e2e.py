"""E2E test of the build cache in a browser (fleet-builds criterion 3)."""

import time

import pytest
from playwright.sync_api import Page, expect

from mtfwbuilder.services.build_service import CACHE_HIT_LOG
from tests.e2e.test_smoke_e2e import choose_variant, fill_config_form

pytestmark = pytest.mark.e2e


def _build(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/")
    fill_config_form(page)
    choose_variant(page, "heltec-v3")
    page.click("#buildBtn")


def test_second_build_with_the_same_config_shows_complete_in_under_1_second(page: Page, live_server: str):
    _build(page, live_server)
    expect(page.locator('[data-step="complete"]')).to_have_attribute("data-state", "done", timeout=15_000)

    _build(page, live_server)
    start = time.monotonic()
    expect(page.locator('[data-step="complete"]')).to_have_attribute("data-state", "done", timeout=1_000)

    assert time.monotonic() - start < 1
    expect(page.locator("#build-log-region")).to_contain_text(CACHE_HIT_LOG)
    expect(page.locator("#build-state tr", has_text=".factory.bin").locator("a[download]")).to_be_visible()
