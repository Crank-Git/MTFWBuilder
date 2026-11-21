"""E2E tests of the mesh beacon controls (FR-config-coverage-13)."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_mesh_beacon_section_shows_the_offer_and_four_targets(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    expect(page.locator("#meshBeaconSection")).to_be_visible()
    expect(page.locator("#meshBeaconOfferChannelPsk")).to_be_visible()
    for target in range(4):
        expect(page.locator(f"#meshBeaconTarget{target}ChannelIndex")).to_be_visible()


def test_preview_with_target_channel_index_8_shows_a_field_error(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    page.fill("#meshBeaconTarget0ChannelIndex", "8")

    page.click("#previewBtn")

    expect(page.locator("#error-mesh_beacon_target_0_channel_index")).to_contain_text("the channel index is not")
