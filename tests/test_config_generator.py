"""Tests for JSONC configuration generator."""

import json

import pytest

from mtfwbuilder.services.jsonc_generator import generate_jsonc


class TestBasicFields:
    """Tests for basic device info fields."""

    def test_minimal_config(self):
        """Empty config produces just channel count."""
        result = json.loads(generate_jsonc({}))
        assert result["USERPREFS_CHANNELS_TO_WRITE"] == "1"
        assert len(result) == 1

    def test_device_name(self):
        result = json.loads(generate_jsonc({"device_name": "TestNode"}))
        assert result["USERPREFS_CONFIG_DEVICE_NAME"] == "TestNode"

    def test_owner_short_name(self):
        result = json.loads(generate_jsonc({"owner_short_name": "TST"}))
        assert result["USERPREFS_CONFIG_OWNER_SHORT_NAME"] == "TST"

    def test_owner_long_name(self):
        result = json.loads(generate_jsonc({"owner_long_name": "Test Node"}))
        assert result["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "Test Node"

    def test_timezone(self):
        result = json.loads(generate_jsonc({"tz_string": "EST5EDT"}))
        assert result["USERPREFS_TZ_STRING"] == "EST5EDT"

    def test_bluetooth_pin(self):
        result = json.loads(generate_jsonc({"bluetooth_fixed_pin": "123456"}))
        assert result["USERPREFS_FIXED_BLUETOOTH"] == "123456"

    def test_empty_string_fields_omitted(self):
        """Fields with empty string values should be omitted."""
        result = json.loads(generate_jsonc({"device_name": "", "owner_short_name": ""}))
        assert "USERPREFS_CONFIG_DEVICE_NAME" not in result
        assert "USERPREFS_CONFIG_OWNER_SHORT_NAME" not in result

    def test_full_basic_config(self, sample_config_data):
        """Full basic config with all fields set."""
        result = json.loads(generate_jsonc(sample_config_data))
        assert result["USERPREFS_CONFIG_DEVICE_NAME"] == "TestNode"
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

    def test_fixed_position(self):
        data = {
            "gps_enabled": "true",
            "fixed_position": "true",
            "fixed_lat": "37.7749",
            "fixed_lon": "-122.4194",
            "fixed_alt": "10",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_POSITION_FIXED_LAT"] == "37.7749"
        assert result["USERPREFS_CONFIG_POSITION_FIXED_LON"] == "-122.4194"
        assert result["USERPREFS_CONFIG_POSITION_FIXED_ALT"] == "10"

    def test_fixed_position_disabled_skips_coords(self):
        data = {"gps_enabled": "true", "fixed_position": "false", "fixed_lat": "37.7749"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_CONFIG_POSITION_FIXED_LAT" not in result

    def test_smart_position(self):
        data = {"gps_enabled": "true", "smart_position_enabled": "true"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_POSITION_SMART_ENABLED"] == "true"


class TestNetworkConfig:
    """Tests for network and MQTT configuration."""

    def test_network_disabled_skipped(self):
        data = {"network_enabled": "false", "wifi_ssid": "test"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_CONFIG_WIFI_SSID" not in result

    def test_wifi_config(self):
        data = {
            "network_enabled": "true",
            "wifi_enabled": "true",
            "wifi_ssid": "MyNetwork",
            "wifi_psk": "password123",
        }
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_WIFI_SSID"] == "MyNetwork"
        assert result["USERPREFS_CONFIG_WIFI_PSK"] == "password123"

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
        assert result["USERPREFS_CONFIG_MQTT_SERVER"] == "mqtt.example.com"
        assert result["USERPREFS_CONFIG_MQTT_ROOT_TOPIC"] == "msh"
        assert result["USERPREFS_CONFIG_MQTT_USERNAME"] == "user"
        assert result["USERPREFS_CONFIG_MQTT_PASSWORD"] == "pass"
        assert result["USERPREFS_CONFIG_MQTT_ENCRYPTION_ENABLED"] == "true"
        assert result["USERPREFS_CONFIG_MQTT_TLS_ENABLED"] == "false"

    def test_mqtt_disabled_skipped(self):
        data = {"network_enabled": "true", "mqtt_enabled": "false", "mqtt_address": "x"}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_CONFIG_MQTT_SERVER" not in result


class TestAdminKeys:
    """Tests for admin key configuration."""

    def test_single_admin_key(self):
        data = {"admin_key_0": "base64key=="}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_ADMIN_KEY_0"] == "base64key=="

    def test_multiple_admin_keys(self):
        data = {"admin_key_0": "key0", "admin_key_1": "key1", "admin_key_2": "key2"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_ADMIN_KEY_0"] == "key0"
        assert result["USERPREFS_ADMIN_KEY_1"] == "key1"
        assert result["USERPREFS_ADMIN_KEY_2"] == "key2"

    def test_empty_admin_key_omitted(self):
        data = {"admin_key_0": ""}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_ADMIN_KEY_0" not in result


class TestOEMConfig:
    """Tests for OEM/branding configuration."""

    def test_oem_text(self):
        data = {"oem_text": "Custom Mesh"}
        result = json.loads(generate_jsonc(data))
        assert result["USERPREFS_CONFIG_OEM_TEXT"] == "Custom Mesh"

    def test_oem_empty_omitted(self):
        data = {"oem_text": "", "oem_font_size": ""}
        result = json.loads(generate_jsonc(data))
        assert "USERPREFS_CONFIG_OEM_TEXT" not in result


class TestOutputFormat:
    """Tests for output formatting."""

    def test_output_is_valid_json(self, sample_config_data):
        output = generate_jsonc(sample_config_data)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_output_is_indented(self):
        output = generate_jsonc({"device_name": "Test"})
        assert "\n" in output
        assert "  " in output
