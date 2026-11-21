"""E2E tests of the channel, device, LoRa and network controls (FR-config-coverage-5, -6, -7 and -10)."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import open_tab

pytestmark = pytest.mark.e2e


def test_eight_channels_show_eight_channel_tabs_with_a_muted_control(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "channels")

    page.select_option("#channelsToWrite", "8")

    expect(page.locator("#channel-7-tab")).to_be_visible()
    page.click("#channel-7-tab")
    expect(page.locator("#channel7Muted")).to_be_visible()


def test_preview_shows_the_role_the_tx_switch_and_the_ipv6_switch(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    page.select_option("#deviceRole", "meshtastic_Config_DeviceConfig_Role_TRACKER")
    open_tab(page, "lora")
    page.check("#loraTxDisabled")
    open_tab(page, "network")
    page.check("#networkEnabled")
    page.check("#networkIpv6Enabled")

    page.click("#previewBtn")

    preview = page.locator("#preview-container")
    expect(preview).to_contain_text('"USERPREFS_CONFIG_DEVICE_ROLE": "meshtastic_Config_DeviceConfig_Role_TRACKER"')
    expect(preview).to_contain_text('"USERPREFS_LORA_TX_DISABLED": "true"')
    expect(preview).to_contain_text('"USERPREFS_NETWORK_IPV6_ENABLED": "true"')
