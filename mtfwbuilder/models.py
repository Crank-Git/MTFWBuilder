"""Pydantic models for request/response validation."""

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from mtfwbuilder import meshtastic_enums
from mtfwbuilder.key_bytes import ADMIN_KEY_LENGTH, decode_key
from mtfwbuilder.time_zones import to_posix_tz

CHANNEL_SLOTS = 8
PSK_LENGTHS = (0, 1, 16, 32)
MAX_TX_POWER_DBM = 30
# Upstream `channel.options` gives `ChannelSettings.name` a `max_size` of 12, and the C string ends with a 0 byte.
MAX_CHANNEL_NAME_BYTES = 11
# A `ChannelSet` of 8 full channels gives a URL of about 670 characters, so the limit leaves room for new fields.
MAX_CHANNEL_URL_LENGTH = 4096

# Spec feature config-coverage: an interval field is 0 or 60 to 2,592,000 seconds (30 days).
MIN_INTERVAL_SECS = 60
MAX_INTERVAL_SECS = 2_592_000

MANAGED_MODE_MESSAGE = "Managed mode needs an admin key, or the device cannot be changed again."
# Upstream `src/mesh/Default.h` fails the firmware build with a `static_assert` above 7 hops.
MAX_EVENT_HOP_LIMIT = 7
# Upstream `src/mesh/NodeDB.cpp` changes a beacon interval under 3600 seconds to 3600.
MIN_BEACON_INTERVAL_SECS = 3600
MESH_BEACON_TARGETS = 4
# Upstream `src/modules/PositionModule.cpp` keeps the top 1 to 31 bits of a 32-bit position, and 32 keeps them all.
MAX_POSITION_PRECISION = 32
# Protobufs `meshtastic/config.proto` declares `LoRaConfig.channel_num` as a `uint32`.
MAX_UINT32 = 2**32 - 1

# The form posts each channel field as a flat key, for example `channel_0[psk]`.
_FLAT_CHANNEL_KEY = re.compile(r"(channel_[0-7])\[(\w+)\]")

# The generator writes these fields as `{ 0x.. }` byte arrays, so a `//` in base64 input never reaches the file.
_BYTE_ARRAY_FIELDS = frozenset(
    {"psk", "admin_key", "admin_key_0", "admin_key_1", "admin_key_2", "mesh_beacon_offer_channel_psk"}
)


# Upstream writes each enum value into the build unchanged, so only a value with its C prefix compiles.
_ENUM_FIELDS = (
    {
        "lora_region": meshtastic_enums.REGIONS,
        "lora_modem_preset": meshtastic_enums.MODEM_PRESETS,
        "gps_mode": meshtastic_enums.GPS_MODES,
        "device_rebroadcast_mode": meshtastic_enums.REBROADCAST_MODES,
        "security_packet_signature_policy": meshtastic_enums.PACKET_SIGNATURE_POLICIES,
        "mesh_beacon_offer_preset": meshtastic_enums.MODEM_PRESETS,
        "mesh_beacon_offer_region": meshtastic_enums.REGIONS,
    }
    | {f"mesh_beacon_target_{n}_preset": meshtastic_enums.MODEM_PRESETS for n in range(MESH_BEACON_TARGETS)}
    | {f"mesh_beacon_target_{n}_region": meshtastic_enums.REGIONS for n in range(MESH_BEACON_TARGETS)}
)


# Feature module-settings: these fields hold `ModuleConfig` values, which push config sends and no build writes.
# The types come from the `ModuleConfig` descriptor of the vendored `@meshtastic/core` 2.6.7 (protobufs 2.7.8).
MODULE_BOOL_FIELDS = (
    "serial_enabled",
    "serial_echo",
    "serial_override_console_serial_port",
    "store_forward_enabled",
    "store_forward_heartbeat",
    "store_forward_is_server",
    "range_test_enabled",
    "range_test_save",
    "ext_notify_enabled",
    "ext_notify_active",
    "ext_notify_alert_message",
    "ext_notify_alert_message_vibra",
    "ext_notify_alert_message_buzzer",
    "ext_notify_alert_bell",
    "ext_notify_alert_bell_vibra",
    "ext_notify_alert_bell_buzzer",
    "ext_notify_use_pwm",
    "ext_notify_use_i2s_as_buzzer",
    "neighbor_info_enabled",
    "neighbor_info_transmit_over_lora",
    "detection_sensor_enabled",
    "detection_sensor_send_bell",
    "detection_sensor_use_pullup",
    "paxcounter_enabled",
)
# Protobufs v2.7.8 `module_config.options` gives `output_vibra`, `output_buzzer` and `monitor_pin` `int_size:8`.
MAX_UINT8 = 2**8 - 1
MIN_INT32 = -(2**31)
MAX_INT32 = 2**31 - 1
# Upstream `src/modules/AdminModule.cpp` replaces a neighbor info interval below 14400 seconds with 21600.
MIN_NEIGHBOR_INFO_INTERVAL_SECS = 14400
NEIGHBOR_INFO_INTERVAL_MESSAGE = "The firmware uses at least 14400 seconds (4 hours)."
# The lowest and the highest value of each number field.
MODULE_NUMBER_LIMITS = {
    "serial_rxd": (0, MAX_UINT32),
    "serial_txd": (0, MAX_UINT32),
    "serial_timeout": (0, MAX_UINT32),
    "store_forward_records": (0, MAX_UINT32),
    "store_forward_history_return_max": (0, MAX_UINT32),
    "store_forward_history_return_window": (0, MAX_UINT32),
    "range_test_sender": (0, MAX_UINT32),
    "ext_notify_output_ms": (0, MAX_UINT32),
    "ext_notify_output": (0, MAX_UINT32),
    "ext_notify_output_vibra": (0, MAX_UINT8),
    "ext_notify_output_buzzer": (0, MAX_UINT8),
    "neighbor_info_update_interval": (MIN_NEIGHBOR_INFO_INTERVAL_SECS, MAX_UINT32),
    "detection_sensor_minimum_broadcast_secs": (0, MAX_UINT32),
    "detection_sensor_state_broadcast_secs": (0, MAX_UINT32),
    "detection_sensor_monitor_pin": (0, MAX_UINT8),
    "paxcounter_update_interval": (0, MAX_UINT32),
    "paxcounter_wifi_threshold": (MIN_INT32, MAX_INT32),
    "paxcounter_ble_threshold": (MIN_INT32, MAX_INT32),
}
# A number field below its lowest value gives this message instead of the range message.
_BELOW_LIMIT_MESSAGES = {"neighbor_info_update_interval": NEIGHBOR_INFO_INTERVAL_MESSAGE}
# The value names of each enum field, in proto order. Core 2.6.7 has no newer value, for example `Serial_Mode` MODBUS.
MODULE_ENUMS = {
    "serial_baud": (
        "BAUD_DEFAULT",
        "BAUD_110",
        "BAUD_300",
        "BAUD_600",
        "BAUD_1200",
        "BAUD_2400",
        "BAUD_4800",
        "BAUD_9600",
        "BAUD_19200",
        "BAUD_38400",
        "BAUD_57600",
        "BAUD_115200",
        "BAUD_230400",
        "BAUD_460800",
        "BAUD_576000",
        "BAUD_921600",
    ),
    "serial_mode": ("DEFAULT", "SIMPLE", "PROTO", "TEXTMSG", "NMEA", "CALTOPO", "WS85", "VE_DIRECT"),
    "detection_sensor_trigger_type": (
        "LOGIC_LOW",
        "LOGIC_HIGH",
        "FALLING_EDGE",
        "RISING_EDGE",
        "EITHER_EDGE_ACTIVE_LOW",
        "EITHER_EDGE_ACTIVE_HIGH",
    ),
}
# Upstream `module_config.pb.h` declares `char name[20]`, and nanopb keeps one byte for the null terminator.
MAX_DETECTION_SENSOR_NAME_BYTES = 19
MODULE_FIELDS = (*MODULE_BOOL_FIELDS, *MODULE_NUMBER_LIMITS, *MODULE_ENUMS, "detection_sensor_name")


def _check_role(value: str | None) -> str | None:
    """Return an offered role with its C prefix, and reject a router role with the router-role message."""
    if value and value.removeprefix(meshtastic_enums.ROLES.c_prefix) in meshtastic_enums.ROUTER_ROLES:
        raise ValueError(meshtastic_enums.ROUTER_ROLE_MESSAGE)
    return meshtastic_enums.ROLES.c_value(value) if value else value


def _is_whole_number(value: str) -> bool:
    """Return True if every character of `value` is an ASCII digit."""
    # `isdigit()` alone accepts other scripts' digits, and upstream quotes a value that is not all ASCII digits.
    return value.isascii() and value.isdigit()


class _UserPrefsFields(BaseModel):
    """Base model that rejects a string value with `//` in any field."""

    @field_validator("*")
    @classmethod
    def _reject_double_slash(cls, value: Any, info: ValidationInfo) -> Any:
        # The upstream parser removes all text after // on a line, so the device gets a cut value.
        if isinstance(value, str) and "//" in value and info.field_name not in _BYTE_ARRAY_FIELDS:
            raise ValueError("the value contains //, which the firmware build reads as a comment")
        return value


class ChannelConfig(_UserPrefsFields):
    """Configuration for a single channel."""

    name: str | None = None
    psk: str | None = None
    precision: str | None = None
    uplink_enabled: str | None = None
    downlink_enabled: str | None = None
    muted: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name_length(cls, value: str | None) -> str | None:
        # Upstream copies the name into a 12-byte array with `strcpy`, so a longer name overflows the array.
        if value and len(value.encode("utf-8")) > MAX_CHANNEL_NAME_BYTES:
            raise ValueError(f"the channel name is longer than {MAX_CHANNEL_NAME_BYTES} bytes")
        return value

    @field_validator("psk")
    @classmethod
    def _check_psk_length(cls, value: str | None) -> str | None:
        """Accept a PSK in hex, `{ 0x.. }` or base64 form that is 0, 1, 16 or 32 bytes."""
        length = len(decode_key(value or ""))
        if length not in PSK_LENGTHS:
            raise ValueError("the PSK is not 0, 1, 16 or 32 bytes")
        return value

    @field_validator("precision")
    @classmethod
    def _check_precision(cls, value: str | None) -> str | None:
        """Accept a position precision from 0 to 32 bits, and return None for an empty value.

        The generator writes an empty precision unchanged, and the firmware build then fails.
        """
        if not value:
            return None
        if not (_is_whole_number(value) and int(value) <= MAX_POSITION_PRECISION):
            raise ValueError(f"the position precision is not a whole number from 0 to {MAX_POSITION_PRECISION}")
        return value


class ConfigRequest(_UserPrefsFields):
    """Request body for generating a userPrefs.jsonc configuration."""

    # Basic device info
    owner_short_name: str | None = None
    owner_long_name: str | None = None
    tz_string: str | None = None
    bluetooth_fixed_pin: str | None = None
    device_role: str | None = None
    device_rebroadcast_mode: str | None = None
    node_info_broadcast_secs: str | None = None
    node_info_reply_suppress_secs: str | None = None

    # Channels
    channels_to_write: str = "1"
    channel_0: ChannelConfig | None = None
    channel_1: ChannelConfig | None = None
    channel_2: ChannelConfig | None = None
    channel_3: ChannelConfig | None = None
    channel_4: ChannelConfig | None = None
    channel_5: ChannelConfig | None = None
    channel_6: ChannelConfig | None = None
    channel_7: ChannelConfig | None = None

    # LoRa
    lora_enabled: str | None = None
    lora_region: str | None = None
    lora_modem_preset: str | None = None
    lora_channel_num: str | None = None
    lora_ignore_mqtt: str | None = None
    lora_tx_power: str | None = None
    lora_tx_disabled: str | None = None
    lora_config_ok_to_mqtt: str | None = None

    # GPS
    gps_enabled: str | None = None
    gps_mode: str | None = None
    gps_update_interval: str | None = None
    position_broadcast_interval: str | None = None
    fixed_position: str | None = None
    fixed_lat: str | None = None
    fixed_lon: str | None = None
    fixed_alt: str | None = None
    smart_position_enabled: str | None = None

    # Network
    network_enabled: str | None = None
    network_protocols: str | None = None
    network_ipv6_enabled: str | None = None
    wifi_enabled: str | None = None
    wifi_ssid: str | None = None
    wifi_psk: str | None = None
    mqtt_enabled: str | None = None
    mqtt_address: str | None = None
    mqtt_root_topic: str | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_encryption_enabled: str | None = None
    mqtt_tls_enabled: str | None = None

    # Admin keys
    admin_key_0: str | None = None
    admin_key_1: str | None = None
    admin_key_2: str | None = None

    # Security. The managed-mode check reads the admin keys, so these fields come after them.
    security_is_managed: str | None = None
    security_packet_signature_policy: str | None = None

    # Telemetry
    telemetry_device_interval: str | None = None
    telemetry_environment_enabled: str | None = None
    telemetry_environment_interval: str | None = None
    telemetry_environment_screen_enabled: str | None = None
    telemetry_air_quality_enabled: str | None = None
    telemetry_air_quality_screen_enabled: str | None = None
    telemetry_air_quality_interval: str | None = None

    # OEM
    oem_text: str | None = None
    oem_font_size: str | None = None
    oem_image_width: str | None = None
    oem_image_height: str | None = None
    oem_image_data: str | None = None

    # Other settings
    button_pin: str | None = None
    button_pin_enabled: str | None = None
    event_mode: str | None = None
    event_mode_hop_limit: str | None = None
    event_block_position: str | None = None

    # Canned messages, one message on each line, and the ringtone
    canned_messages: str | None = None
    ringtone_rtttl: str | None = None
    ringtone_nag_secs: str | None = None

    # Mesh beacon
    mesh_beacon_listen_enabled: str | None = None
    mesh_beacon_broadcast_enabled: str | None = None
    mesh_beacon_message: str | None = None
    mesh_beacon_interval_secs: str | None = None
    mesh_beacon_offer_channel_name: str | None = None
    mesh_beacon_offer_channel_psk: str | None = None
    mesh_beacon_offer_frequency_slot: str | None = None
    mesh_beacon_offer_preset: str | None = None
    mesh_beacon_offer_region: str | None = None
    mesh_beacon_target_0_channel_index: str | None = None
    mesh_beacon_target_0_frequency_slot: str | None = None
    mesh_beacon_target_0_preset: str | None = None
    mesh_beacon_target_0_region: str | None = None
    mesh_beacon_target_1_channel_index: str | None = None
    mesh_beacon_target_1_frequency_slot: str | None = None
    mesh_beacon_target_1_preset: str | None = None
    mesh_beacon_target_1_region: str | None = None
    mesh_beacon_target_2_channel_index: str | None = None
    mesh_beacon_target_2_frequency_slot: str | None = None
    mesh_beacon_target_2_preset: str | None = None
    mesh_beacon_target_2_region: str | None = None
    mesh_beacon_target_3_channel_index: str | None = None
    mesh_beacon_target_3_frequency_slot: str | None = None
    mesh_beacon_target_3_preset: str | None = None
    mesh_beacon_target_3_region: str | None = None

    # Module settings. The generator writes no userPrefs key for them.
    serial_enabled: str | None = None
    serial_echo: str | None = None
    serial_rxd: str | None = None
    serial_txd: str | None = None
    serial_baud: str | None = None
    serial_timeout: str | None = None
    serial_mode: str | None = None
    serial_override_console_serial_port: str | None = None
    store_forward_enabled: str | None = None
    store_forward_heartbeat: str | None = None
    store_forward_records: str | None = None
    store_forward_history_return_max: str | None = None
    store_forward_history_return_window: str | None = None
    store_forward_is_server: str | None = None
    range_test_enabled: str | None = None
    range_test_sender: str | None = None
    range_test_save: str | None = None
    ext_notify_enabled: str | None = None
    ext_notify_output_ms: str | None = None
    ext_notify_output: str | None = None
    ext_notify_output_vibra: str | None = None
    ext_notify_output_buzzer: str | None = None
    ext_notify_active: str | None = None
    ext_notify_alert_message: str | None = None
    ext_notify_alert_message_vibra: str | None = None
    ext_notify_alert_message_buzzer: str | None = None
    ext_notify_alert_bell: str | None = None
    ext_notify_alert_bell_vibra: str | None = None
    ext_notify_alert_bell_buzzer: str | None = None
    ext_notify_use_pwm: str | None = None
    ext_notify_use_i2s_as_buzzer: str | None = None
    neighbor_info_enabled: str | None = None
    neighbor_info_update_interval: str | None = None
    neighbor_info_transmit_over_lora: str | None = None
    detection_sensor_enabled: str | None = None
    detection_sensor_minimum_broadcast_secs: str | None = None
    detection_sensor_state_broadcast_secs: str | None = None
    detection_sensor_send_bell: str | None = None
    detection_sensor_name: str | None = None
    detection_sensor_monitor_pin: str | None = None
    detection_sensor_trigger_type: str | None = None
    detection_sensor_use_pullup: str | None = None
    paxcounter_enabled: str | None = None
    paxcounter_update_interval: str | None = None
    paxcounter_wifi_threshold: str | None = None
    paxcounter_ble_threshold: str | None = None

    # Preview flag
    preview_only: bool = False

    model_config = {"extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def _nest_flat_channel_keys(cls, data: Any) -> Any:
        """Move each flat `channel_N[field]` key into a `channel_N` object.

        The nested shape gives a field error the location `channel_N.field`.
        """
        if not isinstance(data, dict):
            return data
        nested: dict[str, Any] = {}
        for key, value in data.items():
            match = _FLAT_CHANNEL_KEY.fullmatch(key)
            if match:
                nested.setdefault(match[1], {})[match[2]] = value
            else:
                nested[key] = value
        return nested

    @field_validator("admin_key_0", "admin_key_1", "admin_key_2")
    @classmethod
    def _check_admin_key_length(cls, value: str | None) -> str | None:
        """Accept an admin key in hex, `{ 0x.. }` or base64 form that is 32 bytes."""
        if value and len(decode_key(value)) != ADMIN_KEY_LENGTH:
            raise ValueError(f"the admin key is not {ADMIN_KEY_LENGTH} bytes")
        return value

    @field_validator("tz_string")
    @classmethod
    def _check_tz_string(cls, value: str | None) -> str | None:
        """Reject a value that is not an IANA time zone or a POSIX TZ string.

        The value stays unchanged, because the generator also converts input that no model checks.
        """
        if value:
            to_posix_tz(value)
        return value

    @field_validator("device_role")
    @classmethod
    def _check_device_role(cls, value: str | None) -> str | None:
        return _check_role(value)

    @field_validator("security_is_managed")
    @classmethod
    def _check_managed_mode(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Reject managed mode without an admin key, with the managed-mode message.

        Upstream `src/mesh/NodeDB.cpp` notes that a managed device without an admin key accepts no config change.
        Only a factory reset then recovers it.
        """
        if value == "true" and not any(info.data.get(f) for f in ("admin_key_0", "admin_key_1", "admin_key_2")):
            raise ValueError(MANAGED_MODE_MESSAGE)
        return value

    @field_validator(
        "security_is_managed",
        "telemetry_environment_enabled",
        "telemetry_environment_screen_enabled",
        "telemetry_air_quality_enabled",
        "telemetry_air_quality_screen_enabled",
        "event_block_position",
        "mesh_beacon_listen_enabled",
        "mesh_beacon_broadcast_enabled",
        *MODULE_BOOL_FIELDS,
    )
    @classmethod
    def _check_true_or_false(cls, value: str | None) -> str | None:
        # Upstream passes only `true` and `false` unquoted, and the firmware reads each toggle as a C++ value.
        if value and value not in ("true", "false"):
            raise ValueError("the value is not true or false")
        return value

    @field_validator("event_mode_hop_limit")
    @classmethod
    def _check_event_hop_limit(cls, value: str | None) -> str | None:
        """Accept a hop limit from 0 to 7."""
        if value and not (_is_whole_number(value) and int(value) <= MAX_EVENT_HOP_LIMIT):
            raise ValueError(f"the hop limit is not a whole number from 0 to {MAX_EVENT_HOP_LIMIT}")
        return value

    @field_validator("canned_messages")
    @classmethod
    def _check_canned_messages(cls, value: str | None) -> str | None:
        # Upstream separates the messages with `|`, so a `|` in a message splits it in two.
        if value and "|" in value:
            raise ValueError("a message contains |, which separates the messages")
        return value

    @field_validator(*_ENUM_FIELDS)
    @classmethod
    def _check_enum_value(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Return the value with its C prefix, if the protobufs enum of the field holds it."""
        return _ENUM_FIELDS[info.field_name].c_value(value) if value else value

    @field_validator("button_pin", "event_mode", "node_info_reply_suppress_secs", "ringtone_nag_secs")
    @classmethod
    def _check_digits(cls, value: str | None) -> str | None:
        if value and not _is_whole_number(value):
            raise ValueError("the value is not a whole number")
        return value

    @field_validator("lora_tx_power")
    @classmethod
    def _check_tx_power(cls, value: str | None) -> str | None:
        """Accept a TX power of 0 to 30 dBm, where 0 selects the region maximum."""
        if value and not (_is_whole_number(value) and int(value) <= MAX_TX_POWER_DBM):
            raise ValueError(f"the TX power is not a whole number from 0 to {MAX_TX_POWER_DBM}")
        return value

    @field_validator("lora_channel_num")
    @classmethod
    def _check_channel_num(cls, value: str | None) -> str | None:
        """Accept a frequency slot from 0 to the `uint32` maximum, where 0 lets the region pick the slot."""
        if value and not (_is_whole_number(value) and int(value) <= MAX_UINT32):
            raise ValueError(f"the frequency slot is not a whole number from 0 to {MAX_UINT32}")
        return value

    @field_validator(
        "node_info_broadcast_secs",
        "telemetry_device_interval",
        "telemetry_environment_interval",
        "telemetry_air_quality_interval",
    )
    @classmethod
    def _check_interval(cls, value: str | None) -> str | None:
        """Accept an interval of 0, or of 60 to 2592000 seconds."""
        if value and not (
            _is_whole_number(value) and (int(value) == 0 or MIN_INTERVAL_SECS <= int(value) <= MAX_INTERVAL_SECS)
        ):
            raise ValueError(
                f"the interval is not 0 or a whole number of seconds from {MIN_INTERVAL_SECS} to {MAX_INTERVAL_SECS}"
            )
        return value

    @field_validator("mesh_beacon_interval_secs")
    @classmethod
    def _check_beacon_interval(cls, value: str | None) -> str | None:
        """Accept a beacon interval of 0, or of 3600 to 2592000 seconds."""
        if value and not (
            _is_whole_number(value) and (int(value) == 0 or MIN_BEACON_INTERVAL_SECS <= int(value) <= MAX_INTERVAL_SECS)
        ):
            raise ValueError(
                "the interval is not 0 or a whole number of seconds "
                f"from {MIN_BEACON_INTERVAL_SECS} to {MAX_INTERVAL_SECS}"
            )
        return value

    @field_validator("mesh_beacon_offer_channel_psk")
    @classmethod
    def _check_beacon_psk(cls, value: str | None) -> str | None:
        """Accept an offer PSK of the same forms and lengths as a channel PSK."""
        return ChannelConfig._check_psk_length(value)

    @field_validator(*(f"mesh_beacon_target_{n}_channel_index" for n in range(MESH_BEACON_TARGETS)))
    @classmethod
    def _check_beacon_channel_index(cls, value: str | None) -> str | None:
        """Accept a channel index from 0 to 7, the slots of the device channel table."""
        if value and not (_is_whole_number(value) and int(value) < CHANNEL_SLOTS):
            raise ValueError(f"the channel index is not a whole number from 0 to {CHANNEL_SLOTS - 1}")
        return value

    @field_validator(
        "mesh_beacon_offer_frequency_slot",
        *(f"mesh_beacon_target_{n}_frequency_slot" for n in range(MESH_BEACON_TARGETS)),
    )
    @classmethod
    def _check_frequency_slot(cls, value: str | None) -> str | None:
        # The protobufs number the frequency slot from 1, and slot 0 means unset.
        if value and not (_is_whole_number(value) and int(value) >= 1):
            raise ValueError("the frequency slot is not a whole number of 1 or more")
        return value

    @field_validator(*MODULE_NUMBER_LIMITS)
    @classmethod
    def _check_module_number(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Accept a whole number in the range of the proto type of the field, with a minus sign if the range has one."""
        if not value:
            return value
        low, high = MODULE_NUMBER_LIMITS[info.field_name]
        digits = value.removeprefix("-") if low < 0 else value
        if not _is_whole_number(digits):
            raise ValueError(f"the value is not a whole number from {low} to {high}")
        if int(value) < low and info.field_name in _BELOW_LIMIT_MESSAGES:
            raise ValueError(_BELOW_LIMIT_MESSAGES[info.field_name])
        if not low <= int(value) <= high:
            raise ValueError(f"the value is not a whole number from {low} to {high}")
        return value

    @field_validator(*MODULE_ENUMS)
    @classmethod
    def _check_module_enum(cls, value: str | None, info: ValidationInfo) -> str | None:
        # Push config gives the value name to `@meshtastic/core` 2.6.7, which knows only the names of its version.
        if value and value not in MODULE_ENUMS[info.field_name]:
            raise ValueError(f"the value is not one of {', '.join(MODULE_ENUMS[info.field_name])}")
        return value

    @field_validator("detection_sensor_name")
    @classmethod
    def _check_detection_sensor_name(cls, value: str | None) -> str | None:
        if value and len(value.encode("utf-8")) > MAX_DETECTION_SENSOR_NAME_BYTES:
            raise ValueError(f"the detection sensor name is longer than {MAX_DETECTION_SENSOR_NAME_BYTES} bytes")
        return value

    @field_validator("channels_to_write")
    @classmethod
    def _check_channels_to_write(cls, value: str) -> str:
        # isdigit() alone accepts other scripts, and upstream quotes a value that is not all ASCII digits.
        if not (value.isascii() and value.isdigit()) or int(value) > CHANNEL_SLOTS:
            raise ValueError(f"channels_to_write is not an integer from 0 to {CHANNEL_SLOTS}")
        return value

    def generator_input(self) -> dict[str, Any]:
        """Return the flat dict that `generate_jsonc` reads, with `channel_N[field]` keys."""
        return flat_channel_keys(self.model_dump(exclude_none=True))


def flat_channel_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Return a `ConfigRequest` dump with each `channel_N` object as flat `channel_N[field]` keys, the form names."""
    for slot in range(CHANNEL_SLOTS):
        for field, value in data.pop(f"channel_{slot}", {}).items():
            data[f"channel_{slot}[{field}]"] = value
    return data


class UserPrefsKeys(BaseModel):
    """The body of `GET /api/v1/userprefs/keys`: what one firmware version reads, from a scan of its firmware source.

    `keys` is None when the firmware source cannot be scanned, and then the form shows every control.
    `enum_values` holds the C name of each `config.proto` enum value in the firmware source. It is None when the
    firmware source has no `config.pb.h`, and then the form offers every enum value.
    """

    version: str
    keys: list[str] | None
    enum_values: list[str] | None


ProfileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ProfileRequest(BaseModel):
    """The body of `POST /api/v1/profiles`: a unique profile name, the config to save, and the store secrets option.

    When `stores_secrets` is true, the project keeps the secrets of the config encrypted (FR-fleet-builds-3).
    """

    name: ProfileName
    config: ConfigRequest
    stores_secrets: bool = False


class ProfileUpdate(BaseModel):
    """The body of `PUT /api/v1/profiles/{id}`: a new name, a new config, a new store secrets option, or more of them.

    A new config replaces the saved config. When `stores_secrets` is false, the project deletes the stored secrets.
    """

    name: ProfileName | None = None
    config: ConfigRequest | None = None
    stores_secrets: bool | None = None

    @model_validator(mode="after")
    def _check_not_empty(self) -> "ProfileUpdate":
        if self.name is None and self.config is None and self.stores_secrets is None:
            raise ValueError("the body holds neither a name, a config nor a stores_secrets value")
        return self


class Profile(BaseModel):
    """A saved profile.

    `config` holds a secret only when `stores_secrets` is true and the secret decrypts. `must_reenter` names each
    secret field that `config` does not hold. `warning` is set when the stored secrets do not decrypt.
    """

    id: int
    name: str
    config: dict[str, Any]
    must_reenter: list[str]
    stores_secrets: bool
    warning: str | None = None
    created_at: str
    updated_at: str


# Firmware 2.8 holds a long name of at most 25 bytes and a short name of at most 4 characters.
MAX_LONG_NAME_BYTES = 25
MAX_SHORT_NAME_LENGTH = 4

FleetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# A label names a zip folder and a manifest row, so it stays short.
MAX_LABEL_LENGTH = 64
# The same 64 KB limit as the userPrefs upload of the build route.
MAX_FLEET_CSV_LENGTH = 65536


class FleetDevice(_UserPrefsFields):
    """One fleet device in a fleet request (FR-fleet-builds-11).

    Each field except `label` is optional and overrides the profile. `role` comes back with its C prefix.
    `id` names the saved fleet device that the row changes. Without it, the saved fleet device with the same label
    is the one. `admin_key` is None to keep the saved admin key, and "" for no admin key.
    """

    id: int | None = None
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_LABEL_LENGTH)]
    variant: str | None = None
    long_name: str | None = None
    short_name: Annotated[str, StringConstraints(max_length=MAX_SHORT_NAME_LENGTH)] | None = None
    role: str | None = None
    lat: Annotated[float, Field(ge=-90, le=90)] | None = None
    lon: Annotated[float, Field(ge=-180, le=180)] | None = None
    alt: Annotated[int, Field(ge=-1000, le=10000)] | None = None
    admin_key: str | None = None

    @field_validator("long_name")
    @classmethod
    def _check_long_name(cls, value: str | None) -> str | None:
        if value and len(value.encode()) > MAX_LONG_NAME_BYTES:
            raise ValueError(f"the long name is more than {MAX_LONG_NAME_BYTES} bytes")
        return value

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str | None) -> str | None:
        return _check_role(value)

    @field_validator("admin_key")
    @classmethod
    def _check_admin_key_length(cls, value: str | None) -> str | None:
        """Accept an admin key in hex, `{ 0x.. }` or base64 form that is 32 bytes."""
        if value and len(decode_key(value)) != ADMIN_KEY_LENGTH:
            raise ValueError(f"the admin key is not {ADMIN_KEY_LENGTH} bytes")
        return value

    @model_validator(mode="after")
    def _check_position(self) -> "FleetDevice":
        # The generator writes the fixed position keys together, so one coordinate alone gives a wrong position.
        if (self.lat is None) != (self.lon is None):
            raise ValueError("a fleet device position needs both lat and lon")
        return self


def _check_unique_labels(devices: list[FleetDevice]) -> list[FleetDevice]:
    """Return `devices`, or raise a ValueError that names the first label that two fleet devices have."""
    seen: set[str] = set()
    for device in devices:
        if device.label in seen:
            raise ValueError(f"the label {device.label} is in the fleet two times")
        seen.add(device.label)
    return devices


FleetDevices = Annotated[list[FleetDevice], AfterValidator(_check_unique_labels)]


class FleetRequest(BaseModel):
    """The body of `POST /api/v1/fleets`: a fleet name, the profile of the fleet, and the fleet devices.

    A `firmware_version` of None selects the default firmware source at fleet run time.
    """

    name: FleetName
    profile_id: int
    firmware_version: str | None = None
    devices: FleetDevices = []


class FleetUpdate(BaseModel):
    """The body of `PUT /api/v1/fleets/{id}`. A None field keeps the saved value, and `devices` replaces all of them."""

    name: FleetName | None = None
    profile_id: int | None = None
    firmware_version: str | None = None
    devices: FleetDevices | None = None

    @model_validator(mode="after")
    def _check_not_empty(self) -> "FleetUpdate":
        if not self.model_fields_set:
            raise ValueError("the body holds no field")
        return self


class FleetDeviceRows(BaseModel):
    """The body of `PUT /api/v1/fleets/{id}/devices`: the rows of the fleet device table, in table order.

    Each row holds the fields of a `FleetDevice`. The route validates each row on its own, so it reports every
    row error with its row number (FR-fleet-builds-13).
    """

    devices: list[dict[str, str | int | float | None]]


class FleetCsv(BaseModel):
    """The body of `POST /api/v1/fleets/{id}/devices/csv`: CSV text whose first line is the header."""

    csv: Annotated[str, StringConstraints(max_length=MAX_FLEET_CSV_LENGTH)]


class FleetDeviceRecord(BaseModel):
    """A saved fleet device. `admin_key` is set only for an admin session, and `has_admin_key` tells if one is saved."""

    id: int
    label: str
    variant: str | None
    long_name: str | None
    short_name: str | None
    role: str | None
    lat: float | None
    lon: float | None
    alt: int | None
    admin_key: str | None = None
    has_admin_key: bool


class Fleet(BaseModel):
    """A saved fleet with its fleet devices in the order of the request."""

    id: int
    name: str
    profile_id: int
    firmware_version: str | None
    devices: list[FleetDeviceRecord]
    created_at: str
    updated_at: str


class FleetRunRequest(BaseModel):
    """The body of `POST /api/v1/fleets/{id}/runs` and of the retry: the "must re-enter" secrets of the profile.

    Each key is a name from `must_reenter` of the profile, for example `channel_0[psk]`. The project holds the
    secrets in memory for the fleet run only.
    """

    secrets: dict[str, str] = {}


class FleetRunDevice(BaseModel):
    """One fleet device of a fleet run with its build status (FR-fleet-builds-16).

    `build_id` is None until the fleet run gives the fleet device to the build queue. `label` and `variant` are None
    when the fleet device is no longer in the fleet.
    """

    fleet_device_id: int
    label: str | None
    variant: str | None
    build_id: str | None
    status: str
    error: str | None


class FleetRun(BaseModel):
    """A fleet run: `running`, `complete`, `partial` (a fleet device has no complete build) or `failed` (none has).

    `id` is the random public id of the fleet run, so a person who knows one fleet run cannot find the others.
    """

    id: str
    fleet_id: int
    firmware_version: str | None
    status: Literal["running", "complete", "partial", "failed"]
    created_at: str
    devices: list[FleetRunDevice]


class BuildRequest(BaseModel):
    """Request to build firmware."""

    variant: str = Field(..., description="PlatformIO environment name")
    custom_filename: str | None = None


class BuildStatus(BaseModel):
    """SSE event for build progress."""

    status: str
    message: str | None = None
    progress: int | None = None
    download_url: str | None = None
    error: str | None = None


class Artifact(BaseModel):
    """One file of a complete build, in the artifact list."""

    name: str
    size: int
    kind: Literal["factory", "app", "uf2", "ota-zip", "hex", "littlefs", "manifest"]


class AdminLoginRequest(BaseModel):
    """Admin login form data."""

    admin_key: str


class PreviewResponse(BaseModel):
    """Response for config preview."""

    success: bool
    content: str | None = None
    error: str | None = None


class FilePreviewResponse(BaseModel):
    """Response for uploaded file preview."""

    success: bool
    content: str | None = None
    line_count: int | None = None
    checks: dict | None = None
    filename: str | None = None
    error: str | None = None


class FirmwareSource(BaseModel):
    """One installed firmware version, as `GET /api/v1/firmware` lists it."""

    version: str
    channel: Literal["stable", "alpha", "pinned"]
    installed_at: datetime
    size_bytes: int
    status: str


class RegistryEntry(BaseModel):
    """One variant in the registry of a firmware version, as `GET /api/v1/variants` lists it.

    `image_url` is the URL of the device image, or None when the firmware source does not hold the image file.
    """

    env: str
    display_name: str
    manufacturer: str
    architecture: str
    support_level: str
    hw_model: int | None
    hw_model_slug: str | None
    image: str | None
    image_url: str | None
    requires_dfu: bool
    partition_scheme: str | None
    has_mui: bool
    has_ink_hud: bool
    actively_supported: bool


class AvailableFirmwareVersion(BaseModel):
    """One upstream firmware version. `date` is None when the list comes from api.meshtastic.org."""

    tag: str
    title: str
    date: str | None
    revoked: bool


class AvailableFirmwareVersions(BaseModel):
    """The body of `GET /api/v1/firmware/available`: the upstream lists, each newest first."""

    source: Literal["api.meshtastic.org", "github"]
    stable: list[AvailableFirmwareVersion]
    alpha: list[AvailableFirmwareVersion]


class InstallRequest(BaseModel):
    """The body of `POST /api/v1/firmware/install`: one channel or one exact tag."""

    channel: Literal["stable", "alpha"] | None = None
    # The tag is also a directory name, so the pattern keeps a path separator out of it.
    tag: str | None = Field(default=None, pattern=r"^v\d+\.\d+\.\d+\.[0-9a-f]{7}$")

    @model_validator(mode="after")
    def _one_of_channel_and_tag(self):
        if (self.channel is None) == (self.tag is None):
            raise ValueError("Give one of channel and tag")
        return self


class InstallStatus(BaseModel):
    """SSE event for install progress."""

    install_id: str
    version: str
    status: Literal["queued", "downloading", "extracting", "installing packages", "ready", "failed"]
    error: str = ""


class ChannelUrlRequest(BaseModel):
    """Request body of `POST /api/v1/import/channel-url`."""

    url: str = Field(max_length=MAX_CHANNEL_URL_LENGTH)


class ImportedChannel(BaseModel):
    """One channel of a channel URL."""

    index: int
    name: str
    # The PSK in base64. A PSK of 1 byte is a default key, and `default_key` then holds its number.
    psk: str
    default_key: int | None
    role: Literal["PRIMARY", "SECONDARY"]
    # Upstream `Channels::getKey` gives a secondary channel with an empty PSK the primary channel key.
    uses_primary_key: bool
    uplink_enabled: bool
    downlink_enabled: bool
    position_precision: int


class ImportedLora(BaseModel):
    """The LoRa settings of a channel URL. An enum number that protobufs v2.8.3 does not define is None."""

    region: str | None
    modem_preset: str | None
    use_preset: bool
    hop_limit: int
    tx_enabled: bool
    tx_power: int
    channel_num: int
    ignore_mqtt: bool
    config_ok_to_mqtt: bool


class ChannelUrlImport(BaseModel):
    """Response of `POST /api/v1/import/channel-url`. `add` is True for an `add=true` URL."""

    channels: list[ImportedChannel]
    lora: ImportedLora
    add: bool
    warnings: list[str]


class ChannelUrlExport(BaseModel):
    """Response of `POST /api/v1/export/channel-url`.

    `qr_svg` is an SVG document of a QR code that holds `url`. `warnings` holds each problem of the URL for a mesh.
    """

    url: str
    qr_svg: str
    warnings: list[str]


class CliYamlImport(BaseModel):
    """Response of `POST /api/v1/import/cli-yaml`.

    `config` holds the form fields, with the `ConfigRequest` names and the flat `channel_N[field]` names.
    `not_applied` holds the dotted path in the file of each field that has no userPrefs key or that the form rejects.
    `warnings` holds the reason for each rejected field.
    """

    config: dict[str, str]
    not_applied: list[str]
    warnings: list[str]
