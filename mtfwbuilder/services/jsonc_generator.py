"""JSONC configuration generator for Meshtastic userPrefs files.

Migrated from utils/jsonc_generator.py — logic preserved exactly.
"""

import json
from typing import Any


def generate_jsonc(config_data: dict[str, Any]) -> str:
    """Generate a userPrefs.jsonc string from form data."""
    output: dict[str, str] = {}

    channels_to_write = config_data.get("channels_to_write", "1")
    output["USERPREFS_CHANNELS_TO_WRITE"] = channels_to_write

    _set_if_present(config_data, output, "device_name", "USERPREFS_CONFIG_DEVICE_NAME")
    _set_if_present(config_data, output, "owner_short_name", "USERPREFS_CONFIG_OWNER_SHORT_NAME")
    _set_if_present(config_data, output, "owner_long_name", "USERPREFS_CONFIG_OWNER_LONG_NAME")
    _set_if_present(config_data, output, "tz_string", "USERPREFS_TZ_STRING")
    _set_if_present(config_data, output, "bluetooth_fixed_pin", "USERPREFS_FIXED_BLUETOOTH")

    channel_count = int(channels_to_write)
    for i in range(channel_count):
        _process_channel_config(config_data, output, i)

    _process_lora_config(config_data, output)
    _process_gps_config(config_data, output)
    _process_admin_keys(config_data, output)
    _process_network_config(config_data, output)
    _process_oem_config(config_data, output)

    return json.dumps(output, indent=2)


def _set_if_present(data: dict, output: dict, key: str, output_key: str) -> None:
    """Set output_key if key exists and is non-empty in data."""
    val = data.get(key)
    if val:
        output[output_key] = val


def _process_channel_config(config_data: dict, output: dict, channel_index: int) -> None:
    """Process configuration for a specific channel."""
    channel_data: dict[str, str] = {}
    prefix = f"channel_{channel_index}["
    for key in config_data:
        if key.startswith(prefix) and key.endswith("]"):
            prop = key[key.find("[") + 1 : key.find("]")]
            channel_data[prop] = config_data[key]

    if not channel_data:
        return

    if "name" in channel_data:
        output[f"USERPREFS_CHANNEL_{channel_index}_NAME"] = channel_data["name"]
    if "precision" in channel_data:
        output[f"USERPREFS_CHANNEL_{channel_index}_PRECISION"] = channel_data["precision"]

    if channel_data.get("psk"):
        _process_channel_psk(channel_data["psk"], output, channel_index)

    for setting in ("uplink_enabled", "downlink_enabled"):
        if setting in channel_data:
            key = f"USERPREFS_CHANNEL_{channel_index}_{setting.upper()}"
            output[key] = "true" if channel_data[setting] == "true" else "false"


def _process_channel_psk(psk_value: str, output: dict, channel_index: int) -> None:
    """Process and format Pre-Shared Key for a channel."""
    psk = psk_value.strip()
    if psk.startswith("{") and psk.endswith("}"):
        output[f"USERPREFS_CHANNEL_{channel_index}_PSK"] = psk
    elif psk:
        psk_bytes = [f"0x{psk[i:i+2]}" for i in range(0, len(psk), 2)]
        output[f"USERPREFS_CHANNEL_{channel_index}_PSK"] = "{ " + ", ".join(psk_bytes) + " }"


def _process_lora_config(config_data: dict, output: dict) -> None:
    """Process LoRa radio configuration settings."""
    if config_data.get("lora_enabled") != "true":
        return

    region = config_data.get("lora_region")
    if region:
        if region.startswith("meshtastic_Config_LoRaConfig_RegionCode_"):
            output["USERPREFS_CONFIG_LORA_REGION"] = region
        else:
            output["USERPREFS_CONFIG_LORA_REGION"] = f"meshtastic_Config_LoRaConfig_RegionCode_{region}"

    _set_if_present(config_data, output, "lora_modem_preset", "USERPREFS_LORACONFIG_MODEM_PRESET")
    _set_if_present(config_data, output, "lora_channel_num", "USERPREFS_LORACONFIG_CHANNEL_NUM")

    if "lora_ignore_mqtt" in config_data:
        output["USERPREFS_CONFIG_LORA_IGNORE_MQTT"] = "true" if config_data["lora_ignore_mqtt"] == "true" else "false"


def _process_gps_config(config_data: dict, output: dict) -> None:
    """Process GPS and positioning configuration settings."""
    if config_data.get("gps_enabled") != "true":
        return

    _set_if_present(config_data, output, "gps_mode", "USERPREFS_CONFIG_GPS_MODE")
    _set_if_present(config_data, output, "gps_update_interval", "USERPREFS_CONFIG_GPS_UPDATE_INTERVAL")
    _set_if_present(config_data, output, "position_broadcast_interval", "USERPREFS_CONFIG_POSITION_BROADCAST_INTERVAL")

    if config_data.get("fixed_position") == "true":
        _set_if_present(config_data, output, "fixed_lat", "USERPREFS_CONFIG_POSITION_FIXED_LAT")
        _set_if_present(config_data, output, "fixed_lon", "USERPREFS_CONFIG_POSITION_FIXED_LON")
        _set_if_present(config_data, output, "fixed_alt", "USERPREFS_CONFIG_POSITION_FIXED_ALT")

    if "smart_position_enabled" in config_data:
        output["USERPREFS_CONFIG_POSITION_SMART_ENABLED"] = (
            "true" if config_data["smart_position_enabled"] == "true" else "false"
        )


def _process_admin_keys(config_data: dict, output: dict) -> None:
    """Process admin keys (up to 3 keys supported)."""
    for i in range(3):
        _set_if_present(config_data, output, f"admin_key_{i}", f"USERPREFS_ADMIN_KEY_{i}")


def _process_network_config(config_data: dict, output: dict) -> None:
    """Process network and connectivity configuration."""
    if config_data.get("network_enabled") != "true":
        return

    _set_if_present(config_data, output, "network_protocols", "USERPREFS_CONFIG_NETWORK_ENABLED_PROTOCOLS")

    if config_data.get("wifi_enabled") == "true":
        _set_if_present(config_data, output, "wifi_ssid", "USERPREFS_CONFIG_WIFI_SSID")
        _set_if_present(config_data, output, "wifi_psk", "USERPREFS_CONFIG_WIFI_PSK")

    if config_data.get("mqtt_enabled") == "true":
        _process_mqtt_config(config_data, output)


def _process_mqtt_config(config_data: dict, output: dict) -> None:
    """Process MQTT-specific configuration settings."""
    _set_if_present(config_data, output, "mqtt_address", "USERPREFS_CONFIG_MQTT_SERVER")
    _set_if_present(config_data, output, "mqtt_root_topic", "USERPREFS_CONFIG_MQTT_ROOT_TOPIC")
    _set_if_present(config_data, output, "mqtt_username", "USERPREFS_CONFIG_MQTT_USERNAME")
    _set_if_present(config_data, output, "mqtt_password", "USERPREFS_CONFIG_MQTT_PASSWORD")

    for field in ("mqtt_encryption_enabled", "mqtt_tls_enabled"):
        if field in config_data:
            key = "USERPREFS_CONFIG_" + field.upper()
            output[key] = "true" if config_data[field] == "true" else "false"


def _process_oem_config(config_data: dict, output: dict) -> None:
    """Process OEM and branding configuration settings."""
    _set_if_present(config_data, output, "oem_text", "USERPREFS_CONFIG_OEM_TEXT")
    _set_if_present(config_data, output, "oem_font_size", "USERPREFS_CONFIG_OEM_FONT_SIZE")
    _set_if_present(config_data, output, "oem_image_width", "USERPREFS_CONFIG_OEM_IMAGE_WIDTH")
    _set_if_present(config_data, output, "oem_image_height", "USERPREFS_CONFIG_OEM_IMAGE_HEIGHT")
    _set_if_present(config_data, output, "oem_image_data", "USERPREFS_CONFIG_OEM_IMAGE_DATA")
