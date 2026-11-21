"""Pydantic models for request/response validation."""

from pydantic import BaseModel, Field


class ChannelConfig(BaseModel):
    """Configuration for a single channel."""

    name: str | None = None
    psk: str | None = None
    precision: str | None = None
    uplink_enabled: str | None = None
    downlink_enabled: str | None = None


class ConfigRequest(BaseModel):
    """Request body for generating a userPrefs.jsonc configuration."""

    # Basic device info
    device_name: str | None = None
    owner_short_name: str | None = None
    owner_long_name: str | None = None
    tz_string: str | None = None
    bluetooth_fixed_pin: str | None = None

    # Channels
    channels_to_write: str = "1"

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

    # Preview flag
    preview_only: bool = False

    model_config = {"extra": "allow"}


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


class BuildResult(BaseModel):
    """Result of a completed build."""

    build_id: str
    success: bool
    download_url: str | None = None
    error: str | None = None


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
