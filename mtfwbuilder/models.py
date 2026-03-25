"""Pydantic models for request/response validation."""

from typing import Optional

from pydantic import BaseModel, Field


class ChannelConfig(BaseModel):
    """Configuration for a single channel."""

    name: Optional[str] = None
    psk: Optional[str] = None
    precision: Optional[str] = None
    uplink_enabled: Optional[str] = None
    downlink_enabled: Optional[str] = None


class ConfigRequest(BaseModel):
    """Request body for generating a userPrefs.jsonc configuration."""

    # Basic device info
    device_name: Optional[str] = None
    owner_short_name: Optional[str] = None
    owner_long_name: Optional[str] = None
    tz_string: Optional[str] = None
    bluetooth_fixed_pin: Optional[str] = None

    # Channels
    channels_to_write: str = "1"

    # LoRa
    lora_enabled: Optional[str] = None
    lora_region: Optional[str] = None
    lora_modem_preset: Optional[str] = None
    lora_channel_num: Optional[str] = None
    lora_ignore_mqtt: Optional[str] = None

    # GPS
    gps_enabled: Optional[str] = None
    gps_mode: Optional[str] = None
    gps_update_interval: Optional[str] = None
    position_broadcast_interval: Optional[str] = None
    fixed_position: Optional[str] = None
    fixed_lat: Optional[str] = None
    fixed_lon: Optional[str] = None
    fixed_alt: Optional[str] = None
    smart_position_enabled: Optional[str] = None

    # Network
    network_enabled: Optional[str] = None
    network_protocols: Optional[str] = None
    wifi_enabled: Optional[str] = None
    wifi_ssid: Optional[str] = None
    wifi_psk: Optional[str] = None
    mqtt_enabled: Optional[str] = None
    mqtt_address: Optional[str] = None
    mqtt_root_topic: Optional[str] = None
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_encryption_enabled: Optional[str] = None
    mqtt_tls_enabled: Optional[str] = None

    # Admin keys
    admin_key_0: Optional[str] = None
    admin_key_1: Optional[str] = None
    admin_key_2: Optional[str] = None

    # OEM
    oem_text: Optional[str] = None
    oem_font_size: Optional[str] = None
    oem_image_width: Optional[str] = None
    oem_image_height: Optional[str] = None
    oem_image_data: Optional[str] = None

    # Preview flag
    preview_only: bool = False

    model_config = {"extra": "allow"}


class BuildRequest(BaseModel):
    """Request to build firmware."""

    variant: str = Field(..., description="PlatformIO environment name")
    custom_filename: Optional[str] = None


class BuildStatus(BaseModel):
    """SSE event for build progress."""

    status: str
    message: Optional[str] = None
    progress: Optional[int] = None
    download_url: Optional[str] = None
    error: Optional[str] = None


class BuildResult(BaseModel):
    """Result of a completed build."""

    build_id: str
    success: bool
    download_url: Optional[str] = None
    error: Optional[str] = None


class AdminLoginRequest(BaseModel):
    """Admin login form data."""

    admin_key: str


class PreviewResponse(BaseModel):
    """Response for config preview."""

    success: bool
    content: Optional[str] = None
    error: Optional[str] = None


class FilePreviewResponse(BaseModel):
    """Response for uploaded file preview."""

    success: bool
    content: Optional[str] = None
    line_count: Optional[int] = None
    checks: Optional[dict] = None
    filename: Optional[str] = None
    error: Optional[str] = None
