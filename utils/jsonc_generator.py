"""
JSONC Configuration Generator

This module handles the generation of userPrefs.jsonc files for Meshtastic devices.
It takes form data from the web interface and converts it into the proper JSONC format
that can be used by the Meshtastic firmware build system.

Author: Meshtastic Configuration Generator Contributors
License: MIT
"""

import json
from typing import Dict, Any, Optional

def generate_jsonc(config_data: Dict[str, Any]) -> str:
    """
    Generate a userPrefs.jsonc file content from the provided configuration data.
    
    This function processes form data from the web interface and converts it into
    a properly formatted JSONC configuration file that can be used by the Meshtastic
    firmware build system to compile custom firmware with pre-configured settings.
    
    Args:
        config_data (Dict[str, Any]): Dictionary containing configuration parameters
                                     from the web form, including device settings,
                                     channel configuration, GPS settings, etc.
    
    Returns:
        str: JSON-formatted string containing the userPrefs configuration
        
    Example:
        >>> config = {
        ...     'device_name': 'My Device',
        ...     'owner_short_name': 'USER',
        ...     'channels_to_write': '1',
        ...     'channel_0[name]': 'Primary'
        ... }
        >>> jsonc = generate_jsonc(config)
        >>> print(jsonc)
        {
          "USERPREFS_CONFIG_DEVICE_NAME": "My Device",
          "USERPREFS_CONFIG_OWNER_SHORT_NAME": "USER",
          ...
        }
    """
    output = {}
    
    # Process number of channels
    channels_to_write = config_data.get('channels_to_write', '1')
    output['USERPREFS_CHANNELS_TO_WRITE'] = channels_to_write
    
    # Basic device and owner information
    if 'device_name' in config_data and config_data['device_name']:
        output['USERPREFS_CONFIG_DEVICE_NAME'] = config_data['device_name']
    
    if 'owner_short_name' in config_data and config_data['owner_short_name']:
        output['USERPREFS_CONFIG_OWNER_SHORT_NAME'] = config_data['owner_short_name']
        
    if 'owner_long_name' in config_data and config_data['owner_long_name']:
        output['USERPREFS_CONFIG_OWNER_LONG_NAME'] = config_data['owner_long_name']
    
    # Timezone configuration
    if 'tz_string' in config_data and config_data['tz_string']:
        output['USERPREFS_TZ_STRING'] = config_data['tz_string']
    
    # Bluetooth PIN configuration
    if 'bluetooth_fixed_pin' in config_data and config_data['bluetooth_fixed_pin']:
        output['USERPREFS_FIXED_BLUETOOTH'] = config_data['bluetooth_fixed_pin']
    
    # Process channel information - handle multiple channels with nested field names
    channel_count = int(channels_to_write)
    for i in range(channel_count):
        _process_channel_config(config_data, output, i)
    
    # LoRa radio configuration
    _process_lora_config(config_data, output)
    
    # GPS and positioning configuration  
    _process_gps_config(config_data, output)
    
    # Process admin keys (up to 3 keys supported)
    _process_admin_keys(config_data, output)
    
    # Network and connectivity configuration
    _process_network_config(config_data, output)
    
    # OEM and branding settings
    _process_oem_config(config_data, output)
    
    # Format the output as a JSON string with 2-space indentation
    return json.dumps(output, indent=2)

def _process_channel_config(config_data: Dict[str, Any], output: Dict[str, str], channel_index: int) -> None:
    """
    Process configuration for a specific channel.
    
    Args:
        config_data: Raw form data
        output: Output dictionary to populate
        channel_index: Zero-based channel index
    """
    # Extract channel data from form fields like 'channel_0[name]'
    channel_data = {}
    for key in config_data:
        if key.startswith(f'channel_{channel_index}[') and key.endswith(']'):
            # Extract property name from channel_0[name] format
            prop = key[key.find('[')+1:key.find(']')]
            channel_data[prop] = config_data[key]
    
    # Only process if we have channel data
    if not channel_data:
        return
        
    if 'name' in channel_data:
        output[f'USERPREFS_CHANNEL_{channel_index}_NAME'] = channel_data['name']
    
    if 'precision' in channel_data:
        output[f'USERPREFS_CHANNEL_{channel_index}_PRECISION'] = channel_data['precision']
    
    # Process Pre-Shared Key (PSK) 
    if 'psk' in channel_data and channel_data['psk']:
        _process_channel_psk(channel_data['psk'], output, channel_index)
    
    # Handle uplink/downlink boolean settings
    if 'uplink_enabled' in channel_data:
        output[f'USERPREFS_CHANNEL_{channel_index}_UPLINK_ENABLED'] = "true" if channel_data['uplink_enabled'] == "true" else "false"
    
    if 'downlink_enabled' in channel_data:
        output[f'USERPREFS_CHANNEL_{channel_index}_DOWNLINK_ENABLED'] = "true" if channel_data['downlink_enabled'] == "true" else "false"

def _process_channel_psk(psk_value: str, output: Dict[str, str], channel_index: int) -> None:
    """
    Process and format Pre-Shared Key for a channel.
    
    Args:
        psk_value: Raw PSK value from form
        output: Output dictionary to populate  
        channel_index: Zero-based channel index
    """
    # If PSK is already in byte array format, use it directly
    if psk_value.strip().startswith('{') and psk_value.strip().endswith('}'):
        output[f'USERPREFS_CHANNEL_{channel_index}_PSK'] = psk_value.strip()
    else:
        # Otherwise, format it as a byte array
        psk_hex = psk_value.strip()
        if psk_hex:
            # Convert hex string to byte array format for the config
            psk_bytes = [f"0x{psk_hex[i:i+2]}" for i in range(0, len(psk_hex), 2)]
            output[f'USERPREFS_CHANNEL_{channel_index}_PSK'] = "{ " + ", ".join(psk_bytes) + " }"
    
def _process_lora_config(config_data: Dict[str, Any], output: Dict[str, str]) -> None:
    """Process LoRa radio configuration settings."""
    if config_data.get('lora_enabled') == "true":
        if 'lora_region' in config_data:
            # Fix the duplicate prefix issue in region code
            region = config_data['lora_region']
            if region.startswith('meshtastic_Config_LoRaConfig_RegionCode_'):
                output['USERPREFS_CONFIG_LORA_REGION'] = region
            else:
                output['USERPREFS_CONFIG_LORA_REGION'] = f"meshtastic_Config_LoRaConfig_RegionCode_{region}"
    
        if 'lora_modem_preset' in config_data:
            output['USERPREFS_LORACONFIG_MODEM_PRESET'] = config_data['lora_modem_preset']
    
        if 'lora_channel_num' in config_data:
            output['USERPREFS_LORACONFIG_CHANNEL_NUM'] = config_data['lora_channel_num']
    
        if 'lora_ignore_mqtt' in config_data:
            output['USERPREFS_CONFIG_LORA_IGNORE_MQTT'] = "true" if config_data['lora_ignore_mqtt'] == "true" else "false"
    
def _process_gps_config(config_data: Dict[str, Any], output: Dict[str, str]) -> None:
    """Process GPS and positioning configuration settings."""
    if config_data.get('gps_enabled') == "true":
        if 'gps_mode' in config_data:
            output['USERPREFS_CONFIG_GPS_MODE'] = config_data['gps_mode']
        
        if 'gps_update_interval' in config_data:
            output['USERPREFS_CONFIG_GPS_UPDATE_INTERVAL'] = config_data['gps_update_interval']
        
        if 'position_broadcast_interval' in config_data:
            output['USERPREFS_CONFIG_POSITION_BROADCAST_INTERVAL'] = config_data['position_broadcast_interval']
        
        # Handle fixed position settings
        if config_data.get('fixed_position') == "true":
            if 'fixed_lat' in config_data and config_data['fixed_lat']:
                output['USERPREFS_CONFIG_POSITION_FIXED_LAT'] = config_data['fixed_lat']
            if 'fixed_lon' in config_data and config_data['fixed_lon']:
                output['USERPREFS_CONFIG_POSITION_FIXED_LON'] = config_data['fixed_lon']
            if 'fixed_alt' in config_data and config_data['fixed_alt']:
                output['USERPREFS_CONFIG_POSITION_FIXED_ALT'] = config_data['fixed_alt']
        
        # Smart position toggle
        if 'smart_position_enabled' in config_data:
            output['USERPREFS_CONFIG_POSITION_SMART_ENABLED'] = "true" if config_data['smart_position_enabled'] == "true" else "false"

def _process_admin_keys(config_data: Dict[str, Any], output: Dict[str, str]) -> None:
    """Process admin keys (up to 3 keys supported)."""
    for i in range(3):
        key_name = f'admin_key_{i}'
        if key_name in config_data and config_data[key_name]:
            output[f'USERPREFS_ADMIN_KEY_{i}'] = config_data[key_name]

def _process_network_config(config_data: Dict[str, Any], output: Dict[str, str]) -> None:
    """Process network and connectivity configuration."""
    if config_data.get('network_enabled') == "true":
        if 'network_protocols' in config_data:
            output['USERPREFS_CONFIG_NETWORK_ENABLED_PROTOCOLS'] = config_data['network_protocols']
        
        # WiFi configuration
        if config_data.get('wifi_enabled') == "true":
            if 'wifi_ssid' in config_data:
                output['USERPREFS_CONFIG_WIFI_SSID'] = config_data['wifi_ssid']
            if 'wifi_psk' in config_data:
                output['USERPREFS_CONFIG_WIFI_PSK'] = config_data['wifi_psk']
        
        # MQTT configuration
        if config_data.get('mqtt_enabled') == "true":
            _process_mqtt_config(config_data, output)

def _process_mqtt_config(config_data: Dict[str, Any], output: Dict[str, str]) -> None:
    """Process MQTT-specific configuration settings."""
    if 'mqtt_address' in config_data:
        output['USERPREFS_CONFIG_MQTT_SERVER'] = config_data['mqtt_address']
    if 'mqtt_root_topic' in config_data:
        output['USERPREFS_CONFIG_MQTT_ROOT_TOPIC'] = config_data['mqtt_root_topic']
    if 'mqtt_username' in config_data:
        output['USERPREFS_CONFIG_MQTT_USERNAME'] = config_data['mqtt_username']
    if 'mqtt_password' in config_data:
        output['USERPREFS_CONFIG_MQTT_PASSWORD'] = config_data['mqtt_password']
    if 'mqtt_encryption_enabled' in config_data:
        output['USERPREFS_CONFIG_MQTT_ENCRYPTION_ENABLED'] = "true" if config_data['mqtt_encryption_enabled'] == "true" else "false"
    if 'mqtt_tls_enabled' in config_data:
        output['USERPREFS_CONFIG_MQTT_TLS_ENABLED'] = "true" if config_data['mqtt_tls_enabled'] == "true" else "false"

def _process_oem_config(config_data: Dict[str, Any], output: Dict[str, str]) -> None:
    """Process OEM and branding configuration settings."""
    if 'oem_text' in config_data and config_data['oem_text']:
        output['USERPREFS_CONFIG_OEM_TEXT'] = config_data['oem_text']
    if 'oem_font_size' in config_data and config_data['oem_font_size']:
        output['USERPREFS_CONFIG_OEM_FONT_SIZE'] = config_data['oem_font_size']
    if 'oem_image_width' in config_data and config_data['oem_image_width']:
        output['USERPREFS_CONFIG_OEM_IMAGE_WIDTH'] = config_data['oem_image_width']
    if 'oem_image_height' in config_data and config_data['oem_image_height']:
        output['USERPREFS_CONFIG_OEM_IMAGE_HEIGHT'] = config_data['oem_image_height']
    if 'oem_image_data' in config_data and config_data['oem_image_data']:
        output['USERPREFS_CONFIG_OEM_IMAGE_DATA'] = config_data['oem_image_data'] 