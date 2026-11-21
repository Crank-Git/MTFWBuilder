"""Pydantic models for request/response validation."""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from mtfwbuilder import meshtastic_enums
from mtfwbuilder.key_bytes import ADMIN_KEY_LENGTH, decode_key
from mtfwbuilder.time_zones import to_posix_tz

CHANNEL_SLOTS = 8
PSK_LENGTHS = (0, 1, 16, 32)

MANAGED_MODE_MESSAGE = "Managed mode needs an admin key, or the device cannot be changed again."
MAX_INTERVAL_SECS = 2_592_000
MIN_INTERVAL_SECS = 60
# Upstream `src/mesh/Default.h` fails the firmware build with a `static_assert` above 7 hops.
MAX_EVENT_HOP_LIMIT = 7

# The form posts each channel field as a flat key, for example `channel_0[psk]`.
_FLAT_CHANNEL_KEY = re.compile(r"(channel_[0-7])\[(\w+)\]")

# The generator writes these fields as `{ 0x.. }` byte arrays, so a `//` in base64 input never reaches the file.
_BYTE_ARRAY_FIELDS = frozenset({"psk", "admin_key_0", "admin_key_1", "admin_key_2"})


# Upstream writes each enum value into the build unchanged, so only a value with its C prefix compiles.
_ENUM_FIELDS = {
    "lora_region": meshtastic_enums.REGIONS,
    "lora_modem_preset": meshtastic_enums.MODEM_PRESETS,
    "gps_mode": meshtastic_enums.GPS_MODES,
    "security_packet_signature_policy": meshtastic_enums.PACKET_SIGNATURE_POLICIES,
}


def _is_ascii_whole_number(value: str) -> bool:
    """Return True when `value` holds only ASCII digits.

    The upstream parser quotes a value that is not all ASCII digits, and the firmware build then fails.
    `isdecimal()` alone accepts other scripts' digits, for example "١٢".
    """
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

    @field_validator("psk")
    @classmethod
    def _check_psk_length(cls, value: str | None) -> str | None:
        """Accept a PSK in hex, `{ 0x.. }` or base64 form that is 0, 1, 16 or 32 bytes."""
        length = len(decode_key(value or ""))
        if length not in PSK_LENGTHS:
            raise ValueError("the PSK is not 0, 1, 16 or 32 bytes")
        return value


class ConfigRequest(_UserPrefsFields):
    """Request body for generating a userPrefs.jsonc configuration."""

    # Basic device info
    owner_short_name: str | None = None
    owner_long_name: str | None = None
    tz_string: str | None = None
    bluetooth_fixed_pin: str | None = None
    device_role: str | None = None

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
        """Return an offered role with its C prefix, and reject a router role with the router-role message."""
        if value and value.removeprefix(meshtastic_enums.ROLES.c_prefix) in meshtastic_enums.ROUTER_ROLES:
            raise ValueError(meshtastic_enums.ROUTER_ROLE_MESSAGE)
        return meshtastic_enums.ROLES.c_value(value) if value else value

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
    )
    @classmethod
    def _check_true_or_false(cls, value: str | None) -> str | None:
        # Upstream passes only `true` and `false` unquoted, and the firmware reads each toggle as a C++ value.
        if value and value not in ("true", "false"):
            raise ValueError("the value is not true or false")
        return value

    @field_validator("telemetry_device_interval", "telemetry_environment_interval", "telemetry_air_quality_interval")
    @classmethod
    def _check_interval(cls, value: str | None) -> str | None:
        """Accept an interval of 0, or of 60 to 2,592,000 seconds."""
        if value and not (
            _is_ascii_whole_number(value) and (int(value) == 0 or MIN_INTERVAL_SECS <= int(value) <= MAX_INTERVAL_SECS)
        ):
            raise ValueError(f"the interval is not 0 or {MIN_INTERVAL_SECS} to {MAX_INTERVAL_SECS} seconds")
        return value

    @field_validator("event_mode_hop_limit")
    @classmethod
    def _check_event_hop_limit(cls, value: str | None) -> str | None:
        """Accept a hop limit from 0 to 7."""
        if value and not (_is_ascii_whole_number(value) and int(value) <= MAX_EVENT_HOP_LIMIT):
            raise ValueError(f"the hop limit is not a whole number from 0 to {MAX_EVENT_HOP_LIMIT}")
        return value

    @field_validator("canned_messages")
    @classmethod
    def _check_canned_messages(cls, value: str | None) -> str | None:
        # Upstream separates the messages with `|`, so a `|` in a message splits it in two.
        if value and "|" in value:
            raise ValueError("a message contains |, which separates the messages")
        return value

    @field_validator("lora_region", "lora_modem_preset", "gps_mode", "security_packet_signature_policy")
    @classmethod
    def _check_enum_value(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Return the value with its C prefix, if the protobufs enum of the field holds it."""
        return _ENUM_FIELDS[info.field_name].c_value(value) if value else value

    @field_validator("button_pin", "event_mode", "ringtone_nag_secs")
    @classmethod
    def _check_digits(cls, value: str | None) -> str | None:
        if value and not _is_ascii_whole_number(value):
            raise ValueError("the value is not a whole number")
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
        data = self.model_dump(exclude_none=True)
        for slot in range(CHANNEL_SLOTS):
            for field, value in data.pop(f"channel_{slot}", {}).items():
                data[f"channel_{slot}[{field}]"] = value
        return data


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
