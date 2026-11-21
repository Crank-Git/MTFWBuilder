"""Pydantic models for request/response validation."""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from mtfwbuilder.key_bytes import ADMIN_KEY_LENGTH, decode_key
from mtfwbuilder.time_zones import to_posix_tz

CHANNEL_SLOTS = 8
PSK_LENGTHS = (0, 1, 16, 32)

# The form posts each channel field as a flat key, for example `channel_0[psk]`.
_FLAT_CHANNEL_KEY = re.compile(r"(channel_[0-7])\[(\w+)\]")

# The generator writes these fields as `{ 0x.. }` byte arrays, so a `//` in base64 input never reaches the file.
_BYTE_ARRAY_FIELDS = frozenset({"psk", "admin_key_0", "admin_key_1", "admin_key_2"})


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

    @field_validator("button_pin", "event_mode")
    @classmethod
    def _check_digits(cls, value: str | None) -> str | None:
        # The upstream parser quotes a value that is not all ASCII digits, and the firmware build then fails.
        # `isdecimal()` alone accepts other scripts' digits, for example "١٢".
        if value and not (value.isascii() and value.isdigit()):
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


class FirmwareSource(BaseModel):
    """One installed firmware version, as `GET /api/v1/firmware` lists it."""

    version: str
    channel: Literal["stable", "alpha", "pinned"]
    installed_at: datetime
    size_bytes: int
    status: str


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
