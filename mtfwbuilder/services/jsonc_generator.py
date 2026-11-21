"""JSONC configuration generator for Meshtastic userPrefs files.

Each key name is a name that the firmware source reads at `v2.7.26.54e0d8d`.
The firmware ignores an unknown key without an error.
"""

import json
from typing import Any

from mtfwbuilder.key_bytes import ADMIN_KEY_LENGTH, byte_array, decode_key
from mtfwbuilder.meshtastic_enums import ENUM_C_VALUES
from mtfwbuilder.models import CHANNEL_SLOTS, MESH_BEACON_TARGETS
from mtfwbuilder.services.userprefs_keys import KeyScan
from mtfwbuilder.time_zones import to_posix_tz

ADMIN_KEY_SLOTS = 3

# The suffix of the `USERPREFS_CHANNEL_<n>_*` key for each channel field of the form.
_CHANNEL_KEY_SUFFIXES = {
    "name": "NAME",
    "psk": "PSK",
    "precision": "PRECISION",
    "uplink_enabled": "UPLINK_ENABLED",
    "downlink_enabled": "DOWNLINK_ENABLED",
    "muted": "IS_MUTED",
}

# Each mesh beacon form field that the generator writes unchanged, with its userPrefs key.
# The form has no `USERPREFS_MESH_BEACON_LEGACY_SPLIT` field, because the spec lists the key in Non-goals.
_MESH_BEACON_KEYS = {
    f"mesh_beacon_{suffix.lower()}": f"USERPREFS_MESH_BEACON_{suffix}"
    for suffix in (
        "LISTEN_ENABLED",
        "BROADCAST_ENABLED",
        "MESSAGE",
        "INTERVAL_SECS",
        "OFFER_CHANNEL_NAME",
        "OFFER_FREQUENCY_SLOT",
        "OFFER_PRESET",
        "OFFER_REGION",
        *(
            f"TARGET_{n}_{field}"
            for n in range(MESH_BEACON_TARGETS)
            for field in ("CHANNEL_INDEX", "FREQUENCY_SLOT", "PRESET", "REGION")
        ),
    )
}
_MESH_BEACON_PSK_KEY = "USERPREFS_MESH_BEACON_OFFER_CHANNEL_PSK"

# Some firmware versions do not read these keys, so the generator writes one only if the firmware key set holds it.
VERSION_GATED_KEYS = frozenset(
    {f"USERPREFS_CHANNEL_{n}_{suffix}" for n in range(CHANNEL_SLOTS) for suffix in _CHANNEL_KEY_SUFFIXES.values()}
    | {
        "USERPREFS_CONFIG_DEVICE_REBROADCAST_MODE",
        "USERPREFS_CONFIG_DEVICE_NODE_INFO_BROADCAST_SECS",
        "USERPREFS_LORACONFIG_TX_POWER",
        "USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT",
        "USERPREFS_CONFIG_ENVIRONMENT_MEASUREMENT_ENABLED",
        "USERPREFS_CONFIG_ENV_TELEM_UPDATE_INTERVAL",
        "USERPREFS_CONFIG_ENV_SCREEN_SCREEN_ENABLED",
        "USERPREFS_CONFIG_AQ_MEASUREMENT_ENABLED",
        "USERPREFS_CONFIG_AQ_SCREEN_ENABLED",
        "USERPREFS_CONFIG_AQ_TELEM_UPDATE_INTERVAL",
        "USERPREFS_CONFIG_SECURITY_IS_MANAGED",
        "USERPREFS_CONFIG_SECURITY_PACKET_SIGNATURE_POLICY",
        "USERPREFS_EVENT_MODE_HOP_LIMIT",
        "USERPREFS_BLOCK_POSITION_ON_EVENT_CHANNEL",
        "USERPREFS_CANNED_MESSAGES",
        _MESH_BEACON_PSK_KEY,
    }
    | set(_MESH_BEACON_KEYS.values())
)

# The gated keys that firmware v2.7.26.54e0d8d reads: `src/mesh/Channels.cpp` reads channels 0 to 2, without muted.
# The routes pass the key scan of the chosen firmware version. This default applies when no key scan exists.
DEFAULT_FIRMWARE_KEYS = frozenset(
    f"USERPREFS_CHANNEL_{n}_{suffix}"
    for n in range(3)
    for suffix in ("NAME", "PSK", "PRECISION", "UPLINK_ENABLED", "DOWNLINK_ENABLED")
)

# Each form field that the generator writes unchanged, with its userPrefs key.
_TELEMETRY_SECURITY_EVENT_KEYS = {
    "telemetry_device_interval": "USERPREFS_CONFIG_DEVICE_TELEM_UPDATE_INTERVAL",
    "telemetry_environment_enabled": "USERPREFS_CONFIG_ENVIRONMENT_MEASUREMENT_ENABLED",
    "telemetry_environment_interval": "USERPREFS_CONFIG_ENV_TELEM_UPDATE_INTERVAL",
    "telemetry_environment_screen_enabled": "USERPREFS_CONFIG_ENV_SCREEN_SCREEN_ENABLED",
    "telemetry_air_quality_enabled": "USERPREFS_CONFIG_AQ_MEASUREMENT_ENABLED",
    "telemetry_air_quality_screen_enabled": "USERPREFS_CONFIG_AQ_SCREEN_ENABLED",
    "telemetry_air_quality_interval": "USERPREFS_CONFIG_AQ_TELEM_UPDATE_INTERVAL",
    "security_is_managed": "USERPREFS_CONFIG_SECURITY_IS_MANAGED",
    "security_packet_signature_policy": "USERPREFS_CONFIG_SECURITY_PACKET_SIGNATURE_POLICY",
    "event_mode_hop_limit": "USERPREFS_EVENT_MODE_HOP_LIMIT",
    "event_block_position": "USERPREFS_BLOCK_POSITION_ON_EVENT_CHANNEL",
    "ringtone_rtttl": "USERPREFS_RINGTONE_RTTTL",
    "ringtone_nag_secs": "USERPREFS_RINGTONE_NAG_SECS",
}


# The location of the `ConfigRequest` field that writes each userPrefs key, for a field error.
KEY_FIELDS: dict[str, tuple[str, ...]] = (
    {
        "USERPREFS_CHANNELS_TO_WRITE": ("channels_to_write",),
        "USERPREFS_CONFIG_OWNER_SHORT_NAME": ("owner_short_name",),
        "USERPREFS_CONFIG_OWNER_LONG_NAME": ("owner_long_name",),
        "USERPREFS_TZ_STRING": ("tz_string",),
        "USERPREFS_FIXED_BLUETOOTH": ("bluetooth_fixed_pin",),
        "USERPREFS_CONFIG_DEVICE_ROLE": ("device_role",),
        "USERPREFS_CONFIG_DEVICE_REBROADCAST_MODE": ("device_rebroadcast_mode",),
        "USERPREFS_CONFIG_DEVICE_NODE_INFO_BROADCAST_SECS": ("node_info_broadcast_secs",),
        "USERPREFS_NODEINFO_REPLY_SUPPRESS_SECS": ("node_info_reply_suppress_secs",),
        "USERPREFS_CONFIG_LORA_REGION": ("lora_region",),
        "USERPREFS_LORACONFIG_MODEM_PRESET": ("lora_modem_preset",),
        "USERPREFS_LORACONFIG_CHANNEL_NUM": ("lora_channel_num",),
        "USERPREFS_CONFIG_LORA_IGNORE_MQTT": ("lora_ignore_mqtt",),
        "USERPREFS_LORACONFIG_TX_POWER": ("lora_tx_power",),
        "USERPREFS_LORA_TX_DISABLED": ("lora_tx_disabled",),
        "USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT": ("lora_config_ok_to_mqtt",),
        "USERPREFS_CONFIG_GPS_MODE": ("gps_mode",),
        "USERPREFS_CONFIG_GPS_UPDATE_INTERVAL": ("gps_update_interval",),
        "USERPREFS_CONFIG_POSITION_BROADCAST_INTERVAL": ("position_broadcast_interval",),
        "USERPREFS_FIXED_GPS": ("fixed_position",),
        "USERPREFS_FIXED_GPS_LAT": ("fixed_lat",),
        "USERPREFS_FIXED_GPS_LON": ("fixed_lon",),
        "USERPREFS_FIXED_GPS_ALT": ("fixed_alt",),
        "USERPREFS_CONFIG_SMART_POSITION_ENABLED": ("smart_position_enabled",),
        "USERPREFS_NETWORK_ENABLED_PROTOCOLS": ("network_protocols",),
        "USERPREFS_NETWORK_WIFI_ENABLED": ("wifi_enabled",),
        "USERPREFS_NETWORK_WIFI_SSID": ("wifi_ssid",),
        "USERPREFS_NETWORK_WIFI_PSK": ("wifi_psk",),
        "USERPREFS_NETWORK_IPV6_ENABLED": ("network_ipv6_enabled",),
        "USERPREFS_MQTT_ENABLED": ("mqtt_enabled",),
        "USERPREFS_MQTT_ADDRESS": ("mqtt_address",),
        "USERPREFS_MQTT_ROOT_TOPIC": ("mqtt_root_topic",),
        "USERPREFS_MQTT_USERNAME": ("mqtt_username",),
        "USERPREFS_MQTT_PASSWORD": ("mqtt_password",),
        "USERPREFS_MQTT_ENCRYPTION_ENABLED": ("mqtt_encryption_enabled",),
        "USERPREFS_MQTT_TLS_ENABLED": ("mqtt_tls_enabled",),
        "USERPREFS_OEM_TEXT": ("oem_text",),
        "USERPREFS_OEM_FONT_SIZE": ("oem_font_size",),
        "USERPREFS_OEM_IMAGE_WIDTH": ("oem_image_width",),
        "USERPREFS_OEM_IMAGE_HEIGHT": ("oem_image_height",),
        "USERPREFS_OEM_IMAGE_DATA": ("oem_image_data",),
        "USERPREFS_BUTTON_PIN": ("button_pin",),
        "USERPREFS_EVENT_MODE": ("event_mode",),
        "USERPREFS_CANNED_MESSAGES": ("canned_messages",),
        _MESH_BEACON_PSK_KEY: ("mesh_beacon_offer_channel_psk",),
    }
    | {f"USERPREFS_USE_ADMIN_KEY_{n}": (f"admin_key_{n}",) for n in range(ADMIN_KEY_SLOTS)}
    | {
        f"USERPREFS_CHANNEL_{n}_{suffix}": (f"channel_{n}", field)
        for n in range(CHANNEL_SLOTS)
        for field, suffix in _CHANNEL_KEY_SUFFIXES.items()
    }
    | {key: (field,) for field, key in _TELEMETRY_SECURITY_EVENT_KEYS.items()}
    | {key: (field,) for field, key in _MESH_BEACON_KEYS.items()}
)

# Upstream `src/mesh/generated/meshtastic/cannedmessages.pb.h` gives `char messages[201]`.
# `CannedMessageModule.cpp` puts a terminator in the last byte, so 200 bytes of text remain.
MAX_CANNED_MESSAGES_BYTES = 200
# Upstream `src/mesh/Channels.cpp` fails the build with a `static_assert` for any other channel 0 PSK length.
EVENT_CHANNEL_PSK_LENGTHS = (16, 32)


def version_errors(config_data: dict[str, Any], scan: KeyScan | None) -> list[tuple[tuple[str, ...], str]]:
    """Return a `(field location, message)` pair for each part of the config that the firmware version rejects.

    Args:
        config_data: The flat form data, with `channel_N[field]` keys.
        scan: The key scan of the chosen firmware version. None skips the checks of keys and enum values.

    The checks are:
    - The firmware version reads each key that the generator writes.
    - The firmware version reads each channel slot that `channels_to_write` counts.
    - The firmware source has each enum value that the generator writes.
    - With block position on, the channel 0 PSK is 16 or 32 bytes.
    - The joined canned messages are 200 bytes or fewer.
    """
    written = json.loads(generate_jsonc(config_data, firmware_keys=VERSION_GATED_KEYS))
    errors = [] if scan is None else _scan_errors(written, scan) + _channel_slot_errors(config_data, scan)
    firmware_keys = scan.keys if scan is not None else DEFAULT_FIRMWARE_KEYS
    kept = {k: v for k, v in written.items() if k not in VERSION_GATED_KEYS or k in firmware_keys}
    psk = kept.get("USERPREFS_CHANNEL_0_PSK")
    block_position = kept.get("USERPREFS_BLOCK_POSITION_ON_EVENT_CHANNEL") == "true"
    if block_position and psk is not None and len(decode_key(psk)) not in EVENT_CHANNEL_PSK_LENGTHS:
        errors.append((("channel_0", "psk"), "with Block Position on, the channel 0 PSK must be 16 or 32 bytes"))
    if len(kept.get("USERPREFS_CANNED_MESSAGES", "").encode()) > MAX_CANNED_MESSAGES_BYTES:
        errors.append((("canned_messages",), f"the messages are more than {MAX_CANNED_MESSAGES_BYTES} bytes"))
    return errors


def _scan_errors(written: dict[str, str], scan: KeyScan) -> list[tuple[tuple[str, ...], str]]:
    """Return a field error for each written key that the firmware version does not read or has no value of."""
    errors = []
    for key, value in written.items():
        location = KEY_FIELDS.get(key, (key,))
        if key not in scan.keys:
            errors.append((location, f"firmware version {scan.version} does not read {key}"))
        elif scan.enum_values is not None and value in ENUM_C_VALUES and value not in scan.enum_values:
            errors.append((location, f"firmware version {scan.version} has no value {value}"))
    return errors


def _channel_slot_errors(config_data: dict[str, Any], scan: KeyScan) -> list[tuple[tuple[str, ...], str]]:
    """Return a field error when `channels_to_write` counts more channel slots than the firmware version reads.

    For a counted slot without keys, upstream `src/mesh/Channels.cpp` writes a secondary channel with the default PSK.
    """
    slots = sum(any(key.startswith(f"USERPREFS_CHANNEL_{n}_") for key in scan.keys) for n in range(CHANNEL_SLOTS))
    if int(config_data.get("channels_to_write", "1")) > slots:
        return [(("channels_to_write",), f"firmware version {scan.version} reads {slots} channel slots")]
    return []


def generate_jsonc(config_data: dict[str, Any], firmware_keys: frozenset[str] = DEFAULT_FIRMWARE_KEYS) -> str:
    """Return a userPrefs.jsonc string for form data.

    Args:
        config_data: The flat form data, with `channel_N[field]` keys.
        firmware_keys: The userPrefs keys that the firmware version reads, for example from `read_keys`.
            The generator writes a key of `VERSION_GATED_KEYS` only if this set holds it.

    Raises:
        ValueError: An admin key is not 32 bytes, or the time zone is unknown.
    """
    output: dict[str, str] = {}

    channels_to_write = config_data.get("channels_to_write", "1")
    output["USERPREFS_CHANNELS_TO_WRITE"] = channels_to_write

    _set_if_present(config_data, output, "owner_short_name", "USERPREFS_CONFIG_OWNER_SHORT_NAME")
    _set_if_present(config_data, output, "owner_long_name", "USERPREFS_CONFIG_OWNER_LONG_NAME")
    if config_data.get("tz_string"):
        output["USERPREFS_TZ_STRING"] = to_posix_tz(config_data["tz_string"])
    _set_if_present(config_data, output, "bluetooth_fixed_pin", "USERPREFS_FIXED_BLUETOOTH")

    for i in range(min(int(channels_to_write), CHANNEL_SLOTS)):
        _process_channel_config(config_data, output, i)

    _process_device_config(config_data, output)
    _process_lora_config(config_data, output)
    _process_lora_tx_config(config_data, output)
    _process_gps_config(config_data, output)
    _process_admin_keys(config_data, output)
    _process_network_config(config_data, output)
    _process_ipv6_config(config_data, output)
    _process_oem_config(config_data, output)
    _process_other_settings(config_data, output)
    _process_telemetry_security_event(config_data, output)
    _process_mesh_beacon(config_data, output)

    for key in VERSION_GATED_KEYS - firmware_keys:
        output.pop(key, None)
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
    for key, value in config_data.items():
        if key.startswith(prefix) and key.endswith("]"):
            prop = key[key.find("[") + 1 : key.find("]")]
            channel_data[prop] = value

    if "name" in channel_data:
        output[f"USERPREFS_CHANNEL_{channel_index}_NAME"] = channel_data["name"]
    if "precision" in channel_data:
        output[f"USERPREFS_CHANNEL_{channel_index}_PRECISION"] = channel_data["precision"]

    if channel_data.get("psk"):
        _process_channel_psk(channel_data["psk"], output, channel_index)

    for setting in ("uplink_enabled", "downlink_enabled", "muted"):
        if setting in channel_data:
            key = f"USERPREFS_CHANNEL_{channel_index}_{_CHANNEL_KEY_SUFFIXES[setting]}"
            output[key] = "true" if channel_data[setting] == "true" else "false"


def _process_channel_psk(psk_value: str, output: dict, channel_index: int) -> None:
    """Write a hex, `{ 0x.. }` or base64 PSK as a `{ 0x.. }` byte array."""
    psk = decode_key(psk_value)
    if psk:
        output[f"USERPREFS_CHANNEL_{channel_index}_PSK"] = byte_array(psk)


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


def _process_device_config(config_data: dict, output: dict) -> None:
    """Write the role, the rebroadcast mode and the node info intervals."""
    _set_if_present(config_data, output, "device_role", "USERPREFS_CONFIG_DEVICE_ROLE")
    _set_if_present(config_data, output, "device_rebroadcast_mode", "USERPREFS_CONFIG_DEVICE_REBROADCAST_MODE")
    _set_if_present(config_data, output, "node_info_broadcast_secs", "USERPREFS_CONFIG_DEVICE_NODE_INFO_BROADCAST_SECS")
    _set_if_present(config_data, output, "node_info_reply_suppress_secs", "USERPREFS_NODEINFO_REPLY_SUPPRESS_SECS")


def _process_lora_tx_config(config_data: dict, output: dict) -> None:
    """Write the TX power, the TX switch and the MQTT permission of the LoRa config."""
    if config_data.get("lora_enabled") != "true":
        return

    _set_if_present(config_data, output, "lora_tx_power", "USERPREFS_LORACONFIG_TX_POWER")
    for field, key in (
        ("lora_tx_disabled", "USERPREFS_LORA_TX_DISABLED"),
        ("lora_config_ok_to_mqtt", "USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT"),
    ):
        if field in config_data:
            output[key] = "true" if config_data[field] == "true" else "false"


def _process_gps_config(config_data: dict, output: dict) -> None:
    """Process GPS and positioning configuration settings."""
    if config_data.get("gps_enabled") != "true":
        return

    _set_if_present(config_data, output, "gps_mode", "USERPREFS_CONFIG_GPS_MODE")
    _set_if_present(config_data, output, "gps_update_interval", "USERPREFS_CONFIG_GPS_UPDATE_INTERVAL")
    _set_if_present(config_data, output, "position_broadcast_interval", "USERPREFS_CONFIG_POSITION_BROADCAST_INTERVAL")

    if config_data.get("fixed_position") == "true":
        output["USERPREFS_FIXED_GPS"] = "true"
        _set_if_present(config_data, output, "fixed_lat", "USERPREFS_FIXED_GPS_LAT")
        _set_if_present(config_data, output, "fixed_lon", "USERPREFS_FIXED_GPS_LON")
        _set_if_present(config_data, output, "fixed_alt", "USERPREFS_FIXED_GPS_ALT")

    if "smart_position_enabled" in config_data:
        output["USERPREFS_CONFIG_SMART_POSITION_ENABLED"] = (
            "true" if config_data["smart_position_enabled"] == "true" else "false"
        )


def _process_admin_keys(config_data: dict, output: dict) -> None:
    """Write each admin key as `USERPREFS_USE_ADMIN_KEY_<n>` with a 32-byte `{ 0x.. }` array.

    Upstream copies 32 bytes from the array, so a shorter array reads past its end.

    Raises:
        ValueError: An admin key is not hex, base64 or a `{ 0x.. }` list of 32 bytes.
    """
    for i in range(ADMIN_KEY_SLOTS):
        value = config_data.get(f"admin_key_{i}")
        if not value:
            continue
        key = decode_key(value)
        if len(key) != ADMIN_KEY_LENGTH:
            raise ValueError(f"admin_key_{i} is not {ADMIN_KEY_LENGTH} bytes")
        output[f"USERPREFS_USE_ADMIN_KEY_{i}"] = byte_array(key)


def _process_network_config(config_data: dict, output: dict) -> None:
    """Process network and connectivity configuration."""
    if config_data.get("network_enabled") != "true":
        return

    _set_if_present(config_data, output, "network_protocols", "USERPREFS_NETWORK_ENABLED_PROTOCOLS")

    if config_data.get("wifi_enabled") == "true":
        output["USERPREFS_NETWORK_WIFI_ENABLED"] = "true"
        _set_if_present(config_data, output, "wifi_ssid", "USERPREFS_NETWORK_WIFI_SSID")
        _set_if_present(config_data, output, "wifi_psk", "USERPREFS_NETWORK_WIFI_PSK")

    if config_data.get("mqtt_enabled") == "true":
        _process_mqtt_config(config_data, output)


def _process_mqtt_config(config_data: dict, output: dict) -> None:
    """Process MQTT-specific configuration settings."""
    output["USERPREFS_MQTT_ENABLED"] = "true"
    _set_if_present(config_data, output, "mqtt_address", "USERPREFS_MQTT_ADDRESS")
    _set_if_present(config_data, output, "mqtt_root_topic", "USERPREFS_MQTT_ROOT_TOPIC")
    _set_if_present(config_data, output, "mqtt_username", "USERPREFS_MQTT_USERNAME")
    _set_if_present(config_data, output, "mqtt_password", "USERPREFS_MQTT_PASSWORD")

    for field in ("mqtt_encryption_enabled", "mqtt_tls_enabled"):
        if field in config_data:
            key = "USERPREFS_" + field.upper()
            output[key] = "true" if config_data[field] == "true" else "false"


def _process_ipv6_config(config_data: dict, output: dict) -> None:
    """Write the IPv6 switch of the network config."""
    if config_data.get("network_enabled") == "true" and "network_ipv6_enabled" in config_data:
        output["USERPREFS_NETWORK_IPV6_ENABLED"] = "true" if config_data["network_ipv6_enabled"] == "true" else "false"


def _process_oem_config(config_data: dict, output: dict) -> None:
    """Process OEM and branding configuration settings."""
    _set_if_present(config_data, output, "oem_text", "USERPREFS_OEM_TEXT")
    _set_if_present(config_data, output, "oem_font_size", "USERPREFS_OEM_FONT_SIZE")
    _set_if_present(config_data, output, "oem_image_width", "USERPREFS_OEM_IMAGE_WIDTH")
    _set_if_present(config_data, output, "oem_image_height", "USERPREFS_OEM_IMAGE_HEIGHT")
    _set_if_present(config_data, output, "oem_image_data", "USERPREFS_OEM_IMAGE_DATA")


def _process_other_settings(config_data: dict, output: dict) -> None:
    """Write the button pin and the event mode from the advanced settings."""
    if config_data.get("button_pin_enabled") == "true":
        _set_if_present(config_data, output, "button_pin", "USERPREFS_BUTTON_PIN")
    _set_if_present(config_data, output, "event_mode", "USERPREFS_EVENT_MODE")


def _process_telemetry_security_event(config_data: dict, output: dict) -> None:
    """Write the telemetry, security, event, canned message and ringtone keys."""
    for field, key in _TELEMETRY_SECURITY_EVENT_KEYS.items():
        _set_if_present(config_data, output, field, key)
    messages = [line.strip() for line in config_data.get("canned_messages", "").splitlines() if line.strip()]
    if messages:
        output["USERPREFS_CANNED_MESSAGES"] = "|".join(messages)


def _process_mesh_beacon(config_data: dict, output: dict) -> None:
    """Write the mesh beacon keys, with the offer PSK as a `{ 0x.. }` byte array."""
    for field, key in _MESH_BEACON_KEYS.items():
        _set_if_present(config_data, output, field, key)
    psk = decode_key(config_data.get("mesh_beacon_offer_channel_psk", ""))
    if psk:
        output[_MESH_BEACON_PSK_KEY] = byte_array(psk)
