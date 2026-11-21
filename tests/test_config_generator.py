"""Tests for JSONC configuration generator."""

import base64
import json
import re
from pathlib import Path

import pytest

from mtfwbuilder.services.jsonc_generator import generate_jsonc

ADMIN_KEY = bytes(range(32))
ADMIN_KEY_ARRAY = "{ " + ", ".join(f"0x{b:02x}" for b in ADMIN_KEY) + " }"
TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "includes"
GPS_TEMPLATE = TEMPLATES / "gps_config.html"
ADVANCED_TEMPLATE = TEMPLATES / "advanced_config.html"


def byte_count(byte_array: str) -> int:
    """Return the count of `0x..` items in a `{ 0x.. }` byte array."""
    return len(byte_array.strip("{ }").split(","))


class TestBasicFields:
    """Tests for basic device info fields."""

    def test_minimal_config(self):
        """Empty config produces just channel count."""
        result = json.loads(generate_jsonc({}))
        assert result["USERPREFS_CHANNELS_TO_WRITE"] == "1"
        assert len(result) == 1

    def test_device_name_writes_no_key(self):
        """Upstream reads no device name key, so the generator writes none."""
        result = json.loads(generate_jsonc({"device_name": "TestNode"}))
        assert "USERPREFS_CONFIG_DEVICE_NAME" not in result

    def test_owner_short_name(self):
        result = json.loads(generate_jsonc({"owner_short_name": "TST"}))
        assert result["USERPREFS_CONFIG_OWNER_SHORT_NAME"] == "TST"

    def test_owner_long_name(self):
        result = json.loads(generate_jsonc({"owner_long_name": "Test Node"}))
        assert result["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "Test Node"

    def test_iana_time_zone_gives_its_posix_tz_string(self):
        result = json.loads(generate_jsonc({"tz_string": "America/New_York"}))
        assert result["USERPREFS_TZ_STRING"] == "EST5EDT,M3.2.0,M11.1.0"

    @pytest.mark.parametrize("posix", ["CET-1CEST,M3.5.0,M10.5.0/3", "<+0545>-5:45", "AEST-10AEDT,M10.1.0,M4.1.0/3"])
    def test_posix_tz_string_passes_through_unchanged(self, posix):
        result = json.loads(generate_jsonc({"tz_string": posix}))
        assert result["USERPREFS_TZ_STRING"] == posix

    def test_unknown_time_zone_raises_value_error(self):
        with pytest.raises(ValueError, match="time zone"):
            generate_jsonc({"tz_string": "Mars/Olympus_Mons"})

    def test_bluetooth_pin(self):
        result = json.loads(generate_jsonc({"bluetooth_fixed_pin": "123456"}))
        assert result["USERPREFS_FIXED_BLUETOOTH"] == "123456"

    def test_empty_string_fields_omitted(self):
        """Fields with empty string values should be omitted."""
        result = json.loads(generate_jsonc({"owner_short_name": "", "owner_long_name": ""}))
        assert "USERPREFS_CONFIG_OWNER_SHORT_NAME" not in result
        assert "USERPREFS_CONFIG_OWNER_LONG_NAME" not in result

    def test_full_basic_config(self, sample_config_data):
        """Full basic config with all fields set."""
        result = json.loads(generate_jsonc(sample_config_data))
        assert result["USERPREFS_CONFIG_OWNER_SHORT_NAME"] == "TST"
        assert result["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "Test Node"


class TestChannelConfig:
    """Tests for channel configuration."""

    def test_single_channel_name(self):
        data = {"channels_to_write": "1", "channel_0[name]": "Primary"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_NAME"] == "Primary"

    def test_channel_precision(self):
        data = {"channels_to_write": "1", "channel_0[precision]": "32"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_PRECISION"] == "32"

    def test_multiple_channels(self):
        data = {
            "channels_to_write": "2",
            "channel_0[name]": "Primary",
            "channel_1[name]": "Secondary",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_NAME"] == "Primary"
        assert result["USERPREFS_CHANNEL_1_NAME"] == "Secondary"

    def test_channel_after_slot_2_writes_no_channel_key(self):
        data = {"channels_to_write": "4", "channel_2[name]": "Third", "channel_3[name]": "Fourth"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_2_NAME"] == "Third"
        assert "USERPREFS_CHANNEL_3_NAME" not in result

    def test_channel_uplink_downlink(self):
        data = {
            "channels_to_write": "1",
            "channel_0[uplink_enabled]": "true",
            "channel_0[downlink_enabled]": "false",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_UPLINK_ENABLED"] == "true"
        assert result["USERPREFS_CHANNEL_0_DOWNLINK_ENABLED"] == "false"

    def test_no_channel_data_skipped(self):
        """If no channel_N[...] fields exist, no channel keys are emitted."""
        result = json.loads(generate_jsonc({"channels_to_write": "1"}))
        assert not any(k.startswith("USERPREFS_CHANNEL_") for k in result)


class TestPSK:
    """Tests for Pre-Shared Key handling."""

    def test_hex_string_to_byte_array(self):
        data = {"channels_to_write": "1", "channel_0[psk]": "deadbeef"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_PSK"] == "{ 0xde, 0xad, 0xbe, 0xef }"

    def test_byte_array_passthrough(self):
        psk = "{ 0xde, 0xad, 0xbe, 0xef }"
        data = {"channels_to_write": "1", "channel_0[psk]": psk}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_PSK"] == psk

    def test_full_32_byte_psk(self):
        hex_str = "aa" * 32
        data = {"channels_to_write": "1", "channel_0[psk]": hex_str}
        result = json.loads(generate_jsonc(data))
        psk = result["USERPREFS_CHANNEL_0_PSK"]
        assert psk.startswith("{ 0xaa")
        assert psk.count("0xaa") == 32

    def test_empty_psk_omitted(self):
        data = {"channels_to_write": "1", "channel_0[psk]": ""}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_CHANNEL_0_PSK" not in result

    def test_whitespace_psk_trimmed(self):
        data = {"channels_to_write": "1", "channel_0[psk]": "  deadbeef  "}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_PSK"] == "{ 0xde, 0xad, 0xbe, 0xef }"

    def test_base64_psk_gives_byte_array(self):
        data = {"channels_to_write": "1", "channel_0[psk]": "AQ=="}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CHANNEL_0_PSK"] == "{ 0x01 }"

    def test_base64_psk_with_double_slash_gives_array_without_double_slash(self):
        psk = base64.b64encode(bytes([0xFF] * 16)).decode()
        assert "//" in psk
        result = json.loads(generate_jsonc({"channels_to_write": "1", "channel_0[psk]": psk}))
        assert result["USERPREFS_CHANNEL_0_PSK"] == "{ " + ", ".join(["0xff"] * 16) + " }"


class TestLoRaConfig:
    """Tests for LoRa radio configuration."""

    def test_lora_disabled_skipped(self):
        data = {"lora_enabled": "false", "lora_region": "US"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_CONFIG_LORA_REGION" not in result

    def test_lora_region_auto_prefix(self):
        data = {"lora_enabled": "true", "lora_region": "US"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_LORA_REGION"] == "meshtastic_Config_LoRaConfig_RegionCode_US"

    def test_lora_region_already_prefixed(self):
        full = "meshtastic_Config_LoRaConfig_RegionCode_EU_868"
        data = {"lora_enabled": "true", "lora_region": full}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_LORA_REGION"] == full

    def test_lora_modem_preset(self):
        data = {"lora_enabled": "true", "lora_modem_preset": "LONG_FAST"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_LORACONFIG_MODEM_PRESET"] == "LONG_FAST"

    def test_lora_ignore_mqtt(self):
        data = {"lora_enabled": "true", "lora_ignore_mqtt": "true"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_LORA_IGNORE_MQTT"] == "true"


class TestGPSConfig:
    """Tests for GPS configuration."""

    def test_gps_disabled_skipped(self):
        data = {"gps_enabled": "false", "gps_mode": "ENABLED"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_CONFIG_GPS_MODE" not in result

    def test_gps_mode(self):
        data = {"gps_enabled": "true", "gps_mode": "ENABLED"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_GPS_MODE"] == "ENABLED"

    def test_gps_mode_list_matches_the_gps_mode_enum(self):
        """The form offers the three `GpsMode` values that the firmware source names (FR-userprefs-correctness-13)."""
        prefix = "meshtastic_Config_PositionConfig_GpsMode_"
        select = re.search(r'<select[^>]*name="gps_mode".*?</select>', GPS_TEMPLATE.read_text(), re.DOTALL)
        values = re.findall(r'<option value="([^"]+)"', select[0])
        assert values == [prefix + mode for mode in ("DISABLED", "ENABLED", "NOT_PRESENT")]

    def test_fixed_position(self):
        data = {
            "gps_enabled": "true",
            "fixed_position": "true",
            "fixed_lat": "37.7749",
            "fixed_lon": "-122.4194",
            "fixed_alt": "10",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_FIXED_GPS"] == "true"
        assert result["USERPREFS_FIXED_GPS_LAT"] == "37.7749"
        assert result["USERPREFS_FIXED_GPS_LON"] == "-122.4194"
        assert result["USERPREFS_FIXED_GPS_ALT"] == "10"
        assert not any(key.startswith("USERPREFS_CONFIG_POSITION_FIXED") for key in result)

    def test_fixed_position_disabled_skips_coords(self):
        data = {"gps_enabled": "true", "fixed_position": "false", "fixed_lat": "37.7749"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_FIXED_GPS" not in result
        assert "USERPREFS_FIXED_GPS_LAT" not in result

    def test_smart_position(self):
        data = {"gps_enabled": "true", "smart_position_enabled": "true"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_SMART_POSITION_ENABLED"] == "true"
        assert "USERPREFS_CONFIG_POSITION_SMART_ENABLED" not in result


class TestNetworkConfig:
    """Tests for network and MQTT configuration."""

    def test_network_disabled_skipped(self):
        data = {"network_enabled": "false", "wifi_ssid": "test"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_NETWORK_WIFI_SSID" not in result

    def test_network_protocols(self):
        data = {"network_enabled": "true", "network_protocols": "1"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_NETWORK_ENABLED_PROTOCOLS"] == "1"
        assert "USERPREFS_CONFIG_NETWORK_ENABLED_PROTOCOLS" not in result

    def test_wifi_config(self):
        data = {
            "network_enabled": "true",
            "wifi_enabled": "true",
            "wifi_ssid": "MyNetwork",
            "wifi_psk": "password123",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_NETWORK_WIFI_ENABLED"] == "true"
        assert result["USERPREFS_NETWORK_WIFI_SSID"] == "MyNetwork"
        assert result["USERPREFS_NETWORK_WIFI_PSK"] == "password123"
        assert not any(key.startswith("USERPREFS_CONFIG_WIFI") for key in result)

    def test_wifi_disabled_skipped(self):
        data = {"network_enabled": "true", "wifi_enabled": "false", "wifi_ssid": "MyNetwork"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_NETWORK_WIFI_ENABLED" not in result
        assert "USERPREFS_NETWORK_WIFI_SSID" not in result

    def test_mqtt_config(self):
        data = {
            "network_enabled": "true",
            "mqtt_enabled": "true",
            "mqtt_address": "mqtt.example.com",
            "mqtt_root_topic": "msh",
            "mqtt_username": "user",
            "mqtt_password": "pass",
            "mqtt_encryption_enabled": "true",
            "mqtt_tls_enabled": "false",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_MQTT_ENABLED"] == "true"
        assert result["USERPREFS_MQTT_ADDRESS"] == "mqtt.example.com"
        assert result["USERPREFS_MQTT_ROOT_TOPIC"] == "msh"
        assert result["USERPREFS_MQTT_USERNAME"] == "user"
        assert result["USERPREFS_MQTT_PASSWORD"] == "pass"
        assert result["USERPREFS_MQTT_ENCRYPTION_ENABLED"] == "true"
        assert result["USERPREFS_MQTT_TLS_ENABLED"] == "false"
        assert not any(key.startswith("USERPREFS_CONFIG_MQTT") for key in result)

    def test_mqtt_disabled_skipped(self):
        data = {"network_enabled": "true", "mqtt_enabled": "false", "mqtt_address": "x"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_MQTT_ENABLED" not in result
        assert "USERPREFS_MQTT_ADDRESS" not in result


class TestAdminKeys:
    """Tests for admin key configuration (FR-userprefs-correctness-3)."""

    def test_base64_admin_key_gives_use_admin_key_0_with_32_byte_array(self):
        data = {"admin_key_0": base64.b64encode(ADMIN_KEY).decode()}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_USE_ADMIN_KEY_0"] == ADMIN_KEY_ARRAY
        assert byte_count(result["USERPREFS_USE_ADMIN_KEY_0"]) == 32
        assert "USERPREFS_ADMIN_KEY_0" not in result

    def test_hex_admin_key_gives_32_byte_array(self):
        result = json.loads(generate_jsonc({"admin_key_0": ADMIN_KEY.hex()}))
        assert result["USERPREFS_USE_ADMIN_KEY_0"] == ADMIN_KEY_ARRAY

    def test_byte_array_admin_key_gives_the_same_array(self):
        result = json.loads(generate_jsonc({"admin_key_0": ADMIN_KEY_ARRAY}))
        assert result["USERPREFS_USE_ADMIN_KEY_0"] == ADMIN_KEY_ARRAY

    def test_base64_admin_key_with_double_slash_gives_array_without_double_slash(self):
        key = base64.b64encode(bytes([0xFF] * 32)).decode()
        assert "//" in key
        result = json.loads(generate_jsonc({"admin_key_0": key}))
        assert result["USERPREFS_USE_ADMIN_KEY_0"] == "{ " + ", ".join(["0xff"] * 32) + " }"

    def test_three_admin_keys_give_three_use_admin_keys(self):
        data = {f"admin_key_{i}": bytes([i] * 32).hex() for i in range(3)}
        result = json.loads(generate_jsonc(data))
        for i in range(3):
            assert result[f"USERPREFS_USE_ADMIN_KEY_{i}"] == "{ " + ", ".join([f"0x{i:02x}"] * 32) + " }"
            assert f"USERPREFS_ADMIN_KEY_{i}" not in result

    def test_empty_admin_key_omitted(self):
        result = json.loads(generate_jsonc({"admin_key_0": ""}))
        assert "USERPREFS_USE_ADMIN_KEY_0" not in result

    @pytest.mark.parametrize("key", [bytes(31).hex(), base64.b64encode(bytes(16)).decode(), "not a key"])
    def test_admin_key_that_is_not_32_bytes_raises_value_error(self, key):
        with pytest.raises(ValueError):
            generate_jsonc({"admin_key_0": key})


class TestOtherAdvancedSettings:
    """Tests for the button pin and event mode (FR-userprefs-correctness-9)."""

    def test_enabled_button_pin_gives_button_pin_key(self):
        result = json.loads(generate_jsonc({"button_pin": "36", "button_pin_enabled": "true"}))
        assert result["USERPREFS_BUTTON_PIN"] == "36"

    @pytest.mark.parametrize("enabled", ["false", None])
    def test_button_pin_without_enable_box_writes_no_key(self, enabled):
        data = {"button_pin": "36"} | ({"button_pin_enabled": enabled} if enabled else {})
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_BUTTON_PIN" not in result

    def test_enabled_empty_button_pin_writes_no_key(self):
        result = json.loads(generate_jsonc({"button_pin": "", "button_pin_enabled": "true"}))
        assert "USERPREFS_BUTTON_PIN" not in result

    @pytest.mark.parametrize("mode", ["0", "1"])
    def test_event_mode_gives_event_mode_key(self, mode):
        result = json.loads(generate_jsonc({"event_mode": mode}))
        assert result["USERPREFS_EVENT_MODE"] == mode

    def test_default_event_mode_of_the_form_keeps_event_mode_off(self):
        """The form always posts the select value, so the default writes `"0"` and upstream `#if 0` is false."""
        select = re.search(r'<select[^>]*name="event_mode".*?</select>', ADVANCED_TEMPLATE.read_text(), re.DOTALL)
        default = re.search(r'<option value="([^"]+)" selected>', select[0])[1]
        result = json.loads(generate_jsonc({"event_mode": default}))
        assert result["USERPREFS_EVENT_MODE"] == "0"


class TestOEMConfig:
    """Tests for OEM/branding configuration."""

    def test_oem_text(self):
        data = {"oem_text": "Custom Mesh"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_OEM_TEXT"] == "Custom Mesh"

    def test_oem_image_and_font(self):
        data = {
            "oem_font_size": "0",
            "oem_image_width": "50",
            "oem_image_height": "28",
            "oem_image_data": "{ 0x00, 0xFF }",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_OEM_FONT_SIZE"] == "0"
        assert result["USERPREFS_OEM_IMAGE_WIDTH"] == "50"
        assert result["USERPREFS_OEM_IMAGE_HEIGHT"] == "28"
        assert result["USERPREFS_OEM_IMAGE_DATA"] == "{ 0x00, 0xFF }"
        assert not any(key.startswith("USERPREFS_CONFIG_OEM") for key in result)

    def test_oem_empty_omitted(self):
        data = {"oem_text": "", "oem_font_size": ""}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_OEM_TEXT" not in result
        assert "USERPREFS_OEM_FONT_SIZE" not in result


class TestOutputFormat:
    """Tests for output formatting."""

    def test_output_is_valid_json(self, sample_config_data):
        output = generate_jsonc(sample_config_data)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_output_is_indented(self):
        output = generate_jsonc({"owner_long_name": "Test"})
        assert "\n" in output
        assert "  " in output
