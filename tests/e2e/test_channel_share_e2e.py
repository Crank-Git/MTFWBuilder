"""E2E tests of the Import and Export panels of the build page (FR-channel-share-7 and -8, edge case 2)."""

import re

import pytest
from meshtastic.protobuf import apponly_pb2, channel_pb2, config_pb2, localonly_pb2
from playwright.sync_api import Page, expect

from tests.e2e.conftest import open_tab
from tests.test_channel_share import channel_url, long_fast_set
from tests.test_cli_yaml_import import export_yaml

pytestmark = pytest.mark.e2e

LoRaConfig = config_pb2.Config.LoRaConfig
EVENT_PSK_BASE64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
ADD_URL_PREFIX = "https://meshtastic.org/e/?add=true#"


def event_set() -> apponly_pb2.ChannelSet:
    """Return a channel set of two channels with their own PSKs, and the LoRa settings of a mesh in the EU."""
    channel_set = apponly_pb2.ChannelSet()
    channel_set.settings.add(
        name="Event",
        psk=bytes(range(32)),
        uplink_enabled=True,
        module_settings=channel_pb2.ModuleSettings(position_precision=13),
    )
    channel_set.settings.add(name="Admin", psk=bytes(range(16, 32)), downlink_enabled=True)
    channel_set.lora_config.CopyFrom(
        LoRaConfig(
            use_preset=True,
            modem_preset=LoRaConfig.ModemPreset.MEDIUM_FAST,
            region=LoRaConfig.RegionCode.EU_868,
            hop_limit=3,
            tx_enabled=True,
            tx_power=20,
            channel_num=5,
            ignore_mqtt=True,
            config_ok_to_mqtt=True,
        )
    )
    return channel_set


def decode(page: Page, url: str) -> None:
    """Paste `url` into the Import panel and decode it."""
    page.fill("#channelShareUrl", url)
    page.click("#channelShareDecode")


def drop_keys(page: Page, prefixes: list[str]) -> None:
    """Show the form of a firmware version that reads no key that starts with one of `prefixes`."""
    page.evaluate(
        """(prefixes) => {
            const keys = Array.from(document.querySelectorAll('#configForm fieldset[data-userprefs-key]'))
                .map(fieldset => fieldset.dataset.userprefsKey)
                .filter(key => !prefixes.some(prefix => key.startsWith(prefix)));
            applyKeyScan({keys, enum_values: null, version: 'v0.0.0'});
        }""",
        prefixes,
    )


def named_set(*names: str) -> apponly_pb2.ChannelSet:
    """Return a channel set with one channel for each name, each with its own 16-byte PSK."""
    channel_set = apponly_pb2.ChannelSet()
    for number, name in enumerate(names):
        channel_set.settings.add(name=name, psk=bytes([number + 1] * 16))
    return channel_set


def test_decoded_url_shows_a_preview_and_keeps_the_form_unchanged(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    decode(page, channel_url(event_set()))

    rows = page.locator("#channelSharePreview tbody tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("0")
    expect(rows.nth(0)).to_contain_text("Event")
    expect(rows.nth(0)).to_contain_text("Primary")
    expect(rows.nth(1)).to_contain_text("Admin")
    expect(rows.nth(1)).to_contain_text("Secondary")
    expect(page.locator("#channelSharePreview")).not_to_contain_text(EVENT_PSK_BASE64)
    expect(page.locator("#channelShareLora")).to_contain_text("EU_868")
    expect(page.locator("#channelShareLora")).to_contain_text("MEDIUM_FAST")
    open_tab(page, "channels")
    expect(page.locator("#channel0Name")).to_have_value("")
    expect(page.locator("#channelsToWrite")).to_have_value("3")


def test_show_control_reveals_the_psk_of_a_channel(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    decode(page, channel_url(event_set()))

    page.locator("#channelSharePreview tbody tr").nth(0).get_by_role("button", name="Show").click()

    expect(page.locator("#channelSharePreview tbody tr").nth(0)).to_contain_text(EVENT_PSK_BASE64)


def test_default_key_psk_shows_as_default_key_1(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    decode(page, channel_url(long_fast_set()))

    expect(page.locator("#channelSharePreview tbody tr").nth(0)).to_contain_text("Default key 1")


def test_apply_fills_the_channels_and_the_lora_settings_of_the_url(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    decode(page, channel_url(event_set()))

    page.click("#channelShareApply")

    open_tab(page, "channels")
    expect(page.locator("#channelsToWrite")).to_have_value("2")
    expect(page.locator("#channel0Name")).to_have_value("Event")
    expect(page.locator("#channel0PSK")).to_have_value(EVENT_PSK_BASE64)
    expect(page.locator("#channel0Precision")).to_have_value("13")
    expect(page.locator("#channel0Uplink")).to_be_checked()
    expect(page.locator("#channel1Name")).to_have_value("Admin")
    # A channel without module settings has position precision 0, which the select gets as an extra option.
    expect(page.locator("#channel1Precision")).to_have_value("0")
    expect(page.locator("#channel1Downlink")).to_be_checked()
    open_tab(page, "lora")
    expect(page.locator("#loraRegion")).to_have_value("meshtastic_Config_LoRaConfig_RegionCode_EU_868")
    expect(page.locator("#loraModemPreset")).to_have_value("meshtastic_Config_LoRaConfig_ModemPreset_MEDIUM_FAST")
    expect(page.locator("#loraChannelNum")).to_have_value("5")
    expect(page.locator("#loraTxPower")).to_have_value("20")


def test_add_url_puts_the_channels_into_the_first_free_indices(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "channels")
    page.fill("#channel0Name", "Home")

    decode(page, channel_url(event_set(), prefix=ADD_URL_PREFIX))
    page.click("#channelShareApply")

    expect(page.locator("#channelsToWrite")).to_have_value("5")
    expect(page.locator("#channel0Name")).to_have_value("Home")
    expect(page.locator("#channel3Name")).to_have_value("Event")
    expect(page.locator("#channel4Name")).to_have_value("Admin")


def test_add_url_with_all_8_indices_used_shows_no_free_channel_index(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "channels")
    page.select_option("#channelsToWrite", "8")

    decode(page, channel_url(event_set(), prefix=ADD_URL_PREFIX))

    expect(page.locator("#channelShareError")).to_have_text("No free channel index.")
    expect(page.locator("#channelShareApply")).to_be_hidden()
    expect(page.locator("#channelsToWrite")).to_have_value("8")
    expect(page.locator("#channel3Name")).to_have_value("")


def test_broken_url_shows_the_server_message_and_keeps_the_form_unchanged(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    decode(page, "https://meshtastic.org/e/#not*base64")

    expect(page.locator("#channelShareError")).to_have_text("This is not a valid Meshtastic channel URL.")
    expect(page.locator("#channelSharePreview")).to_be_hidden()
    open_tab(page, "channels")
    expect(page.locator("#channelsToWrite")).to_have_value("3")


def test_yaml_file_shows_the_not_applied_list_and_apply_fills_the_long_name(page: Page, live_server: str, tmp_path):
    module_config = localonly_pb2.LocalModuleConfig()
    module_config.serial.enabled = True
    export = tmp_path / "config.yaml"
    export.write_text(export_yaml(module_config=module_config, owner="Alice", channel_url=channel_url(event_set())))
    page.goto(f"{live_server}/")

    page.set_input_files("#channelShareFile", str(export))

    expect(page.locator("#channelShareNotApplied")).to_contain_text("module_config.serial")
    expect(page.locator("#channelSharePreview tbody tr").nth(0)).to_contain_text("Event")
    expect(page.locator("#ownerLongName")).to_have_value("")
    page.click("#channelShareApply")
    expect(page.locator("#ownerLongName")).to_have_value("Alice")
    open_tab(page, "channels")
    expect(page.locator("#channel1Name")).to_have_value("Admin")


def test_import_keeps_no_psk_and_no_url_in_browser_storage(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    url = channel_url(event_set())
    decode(page, url)
    page.click("#channelShareApply")

    stored = page.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])")

    assert EVENT_PSK_BASE64 not in stored
    assert url.split("#")[1] not in stored


def test_export_shows_the_channel_url_and_the_qr_code(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    page.click("#channelShareExport")

    expect(page.locator("#channelShareExportUrl")).to_have_value(re.compile(r"^https://meshtastic\.org/e/#[\w-]+$"))
    expect(page.locator("#channelShareQr svg")).to_be_visible()


def test_export_after_an_import_gives_the_imported_url(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    url = channel_url(event_set())
    decode(page, url)
    page.click("#channelShareApply")

    page.click("#channelShareExport")

    expect(page.locator("#channelShareExportUrl")).to_have_value(url)


def test_export_note_says_the_config_has_no_hop_limit(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    page.click("#channelShareExport")

    expect(page.locator("#channelShareExportNote")).to_contain_text("hop limit")


def test_apply_skips_a_control_that_the_firmware_version_does_not_read(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    drop_keys(page, ["USERPREFS_LORACONFIG_CHANNEL_NUM"])

    decode(page, channel_url(event_set()))

    expect(page.locator("#channelShareNotApplied")).to_contain_text(
        "lora_channel_num: this firmware version does not read it"
    )
    page.click("#channelShareApply")
    open_tab(page, "lora")
    expect(page.locator("#loraChannelNum")).to_have_value("31")
    expect(page.locator("#loraTxPower")).to_have_value("20")


def test_add_url_counts_only_the_slots_that_the_firmware_version_reads(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    drop_keys(page, [f"USERPREFS_CHANNEL_{slot}_" for slot in range(4, 8)])

    decode(page, channel_url(named_set("Event", "Ops"), prefix=ADD_URL_PREFIX))

    expect(page.locator("#channelShareError")).to_have_text("No free channel index.")
    decode(page, channel_url(named_set("Event"), prefix=ADD_URL_PREFIX))
    page.click("#channelShareApply")
    open_tab(page, "channels")
    expect(page.locator("#channelsToWrite")).to_have_value("4")
    expect(page.locator("#channel3Name")).to_have_value("Event")


def test_add_url_skips_a_channel_without_a_name_or_with_a_name_in_use(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "channels")
    page.fill("#channel0Name", "Home")

    decode(page, channel_url(named_set("Home", "", "Event"), prefix=ADD_URL_PREFIX))

    rows = page.locator("#channelSharePreview tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.nth(0)).to_contain_text("3")
    expect(rows.nth(0)).to_contain_text("Event")
    expect(rows.nth(0)).to_contain_text("Secondary")
    expect(page.locator("#channelShareNotApplied li")).to_have_count(2)
    page.click("#channelShareApply")
    expect(page.locator("#channelsToWrite")).to_have_value("4")
    expect(page.locator("#channel0Name")).to_have_value("Home")
    expect(page.locator("#channel0PSK")).to_have_value("")
    expect(page.locator("#channel3Name")).to_have_value("Event")


def test_add_url_never_fills_slot_0(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "channels")
    page.select_option("#channelsToWrite", "0")

    decode(page, channel_url(named_set("Event"), prefix=ADD_URL_PREFIX))
    page.click("#channelShareApply")

    expect(page.locator("#channelsToWrite")).to_have_value("2")
    expect(page.locator("#channel0Name")).to_have_value("")
    expect(page.locator("#channel1Name")).to_have_value("Event")


def test_channel_without_encryption_imports_and_exports_unchanged(page: Page, live_server: str):
    channel_set = apponly_pb2.ChannelSet()
    channel_set.settings.add(name="Open", module_settings=channel_pb2.ModuleSettings(position_precision=13))
    channel_set.lora_config.CopyFrom(LoRaConfig(use_preset=True, region=LoRaConfig.RegionCode.US, hop_limit=3))
    url = channel_url(channel_set)
    page.goto(f"{live_server}/")

    decode(page, url)

    expect(page.locator("#channelSharePreview tbody tr").nth(0)).to_contain_text("No encryption")
    page.click("#channelShareApply")
    open_tab(page, "channels")
    expect(page.locator("#channel0PSK")).to_have_value("{ }")
    page.click("#channelShareExport")
    expect(page.locator("#channelShareExportUrl")).to_have_value(url)


def test_url_with_region_unset_empties_the_region_field(page: Page, live_server: str):
    channel_set = named_set("Event")
    channel_set.lora_config.CopyFrom(LoRaConfig(use_preset=True, hop_limit=3, tx_enabled=True))
    page.goto(f"{live_server}/")

    decode(page, channel_url(channel_set))
    page.click("#channelShareApply")

    open_tab(page, "lora")
    expect(page.locator("#loraRegion")).to_have_value("")


def test_yaml_lora_section_without_tx_enabled_turns_tx_off(page: Page, live_server: str, tmp_path):
    local_config = localonly_pb2.LocalConfig()
    local_config.lora.region = LoRaConfig.RegionCode.EU_868
    export = tmp_path / "config.yaml"
    export.write_text(export_yaml(local_config))
    page.goto(f"{live_server}/")

    page.set_input_files("#channelShareFile", str(export))
    page.click("#channelShareApply")

    open_tab(page, "lora")
    expect(page.locator("#loraTxDisabled")).to_be_checked()
    expect(page.locator("#loraChannelNum")).to_have_value("0")


def test_export_without_a_region_shows_the_warning(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "lora")
    page.uncheck("#loraEnabled")

    page.click("#channelShareExport")

    expect(page.locator("#channelShareExportWarnings")).to_contain_text("UNSET")


def test_primary_channel_with_an_empty_psk_shows_no_encryption(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    decode(page, channel_url(_open_set()))

    row = page.locator("#channelSharePreview tbody tr").nth(0)
    expect(row).to_contain_text("No encryption")
    expect(row).not_to_contain_text("Uses the primary channel key")


def test_secondary_channel_with_an_empty_psk_shows_that_it_uses_the_primary_key(page: Page, live_server: str):
    page.goto(f"{live_server}/")

    decode(page, channel_url(_open_set()))

    row = page.locator("#channelSharePreview tbody tr").nth(1)
    expect(row).to_contain_text("Uses the primary channel key")
    expect(row).not_to_contain_text("No encryption")


def test_psk_help_text_of_a_secondary_slot_says_that_it_uses_the_primary_key(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    open_tab(page, "channels")

    expect(page.locator("#channel0PSKHelp")).to_contain_text("no encryption")
    page.click("#channel-1-tab")
    expect(page.locator("#channel1PSKHelp")).to_contain_text("uses the primary channel key")


def _open_set() -> apponly_pb2.ChannelSet:
    """Return a channel set of a primary and a secondary channel, both with an empty PSK."""
    channel_set = apponly_pb2.ChannelSet()
    channel_set.settings.add(name="Open")
    channel_set.settings.add(name="Side")
    return channel_set
