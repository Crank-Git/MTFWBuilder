"""Contract test: the generator writes only userPrefs keys the fixture firmware reads (FR-userprefs-correctness-15)."""

import json
import re
from pathlib import Path

import pytest

from mtfwbuilder.models import CHANNEL_SLOTS, ChannelConfig, ConfigRequest
from mtfwbuilder.services.jsonc_generator import generate_jsonc
from mtfwbuilder.services.userprefs_keys import read_keys

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "firmware"

ADMIN_KEY = "ab" * 32

# `preview_only` selects the response shape and never reaches the generator.
NOT_A_USERPREFS_FIELD = {"preview_only"}


def firmware_version() -> str:
    """Return the firmware version that `SOURCE.md` records for the fixture."""
    text = (FIXTURE_DIR / "SOURCE.md").read_text()
    return re.search(r"\| Firmware version \| `([^`]+)` \|", text)[1]


def full_config() -> dict[str, str]:
    """Return form data that sets every `ConfigRequest` field, with every toggle on."""
    data = {
        "owner_short_name": "TST",
        "owner_long_name": "Test Node",
        "tz_string": "Europe/Berlin",
        "bluetooth_fixed_pin": "123456",
        "channels_to_write": str(CHANNEL_SLOTS),
        "lora_enabled": "true",
        "lora_region": "US",
        "lora_modem_preset": "meshtastic_Config_LoRaConfig_ModemPreset_LONG_FAST",
        "lora_channel_num": "20",
        "lora_ignore_mqtt": "true",
        "gps_enabled": "true",
        "gps_mode": "meshtastic_Config_PositionConfig_GpsMode_ENABLED",
        "gps_update_interval": "600",
        "position_broadcast_interval": "1800",
        "fixed_position": "true",
        "fixed_lat": "48.8587",
        "fixed_lon": "2.2945",
        "fixed_alt": "35",
        "smart_position_enabled": "true",
        "network_enabled": "true",
        "network_protocols": "1",
        "wifi_enabled": "true",
        "wifi_ssid": "mesh-wifi",
        "wifi_psk": "wifi-secret",
        "mqtt_enabled": "true",
        "mqtt_address": "mqtt.example.org",
        "mqtt_root_topic": "msh/test",
        "mqtt_username": "meshdev",
        "mqtt_password": "mqtt-secret",
        "mqtt_encryption_enabled": "true",
        "mqtt_tls_enabled": "true",
        "admin_key_0": ADMIN_KEY,
        "admin_key_1": ADMIN_KEY,
        "admin_key_2": ADMIN_KEY,
        "oem_text": "Test OEM",
        "oem_font_size": "1",
        "oem_image_width": "8",
        "oem_image_height": "8",
        "oem_image_data": "{ 0x00, 0xff }",
        "button_pin": "36",
        "button_pin_enabled": "true",
        "event_mode": "1",
    }
    for slot in range(CHANNEL_SLOTS):
        data[f"channel_{slot}[name]"] = f"Chan{slot}"
        data[f"channel_{slot}[psk]"] = "deadbeef" * 8
        data[f"channel_{slot}[precision]"] = "14"
        data[f"channel_{slot}[uplink_enabled]"] = "true"
        data[f"channel_{slot}[downlink_enabled]"] = "true"
    return data


def written_keys(config_data: dict[str, str]) -> set[str]:
    """Return the userPrefs keys that the generator writes for validated form data."""
    request = ConfigRequest(**config_data)
    return set(json.loads(generate_jsonc(request.generator_input())))


def assert_firmware_reads(keys: set[str]) -> None:
    """Fail with each key that the fixture firmware does not read, and the firmware version."""
    unread = sorted(keys - read_keys(FIXTURE_DIR))
    assert not unread, f"firmware {firmware_version()} does not read these userPrefs keys: {', '.join(unread)}"


def test_full_config_sets_every_request_field():
    request = ConfigRequest(**full_config())
    assert set(ConfigRequest.model_fields) - NOT_A_USERPREFS_FIELD <= request.model_fields_set
    for slot in range(CHANNEL_SLOTS):
        assert getattr(request, f"channel_{slot}").model_fields_set == set(ChannelConfig.model_fields)


def test_generator_writes_only_keys_the_firmware_reads():
    assert_firmware_reads(written_keys(full_config()))


def test_contract_fails_on_the_device_name_key():
    keys = written_keys(full_config()) | {"USERPREFS_CONFIG_DEVICE_NAME"}
    with pytest.raises(AssertionError, match=r"firmware v2\.7\.26\.54e0d8d .*USERPREFS_CONFIG_DEVICE_NAME"):
        assert_firmware_reads(keys)


def test_fixture_version_is_the_stable_version():
    assert firmware_version() == "v2.7.26.54e0d8d"


def test_read_keys_reads_the_fixture_source():
    keys = read_keys(FIXTURE_DIR)
    assert {"USERPREFS_USE_ADMIN_KEY_0", "USERPREFS_CHANNEL_2_PSK", "USERPREFS_TZ_STRING"} <= keys


def test_read_keys_ignores_a_key_that_only_a_source_comment_names():
    # Upstream `src/mesh/NodeDB.cpp` names `USERPREFS_ADMIN_KEY_0` only in a comment.
    assert "USERPREFS_ADMIN_KEY_0" not in read_keys(FIXTURE_DIR)


def test_read_keys_ignores_line_and_block_comments(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.cpp").write_text(
        "#ifdef USERPREFS_READ\n"
        "// USERPREFS_LINE\n"
        "/* USERPREFS_BLOCK\n USERPREFS_BLOCK_2 */\n"
        'auto u = "http://x"; USERPREFS_AFTER_URL\n'
    )
    (tmp_path / "userPrefs.jsonc").write_text("{\n}\n")
    assert read_keys(tmp_path) == {"USERPREFS_READ", "USERPREFS_AFTER_URL"}


def test_read_keys_reads_each_example_key_in_userprefs_jsonc(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "userPrefs.jsonc").write_text(
        '{\n  // "USERPREFS_EXAMPLE": "1", // USERPREFS_IN_A_NOTE\n  "USERPREFS_SET": "2"\n}\n'
    )
    assert read_keys(tmp_path) == {"USERPREFS_EXAMPLE", "USERPREFS_SET"}
