"""Tests for the CLI export YAML import (FR-channel-share-4 to -6, -11 and -12).

Each test makes its file with upstream `export_config` of the `meshtastic` package, so the file has the real format.
"""

import base64
import logging
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from meshtastic.__main__ import export_config
from meshtastic.protobuf import localonly_pb2

from mtfwbuilder import meshtastic_enums as enums
from mtfwbuilder.models import ConfigRequest

IMPORT_URL = "/api/v1/import/cli-yaml"
INVALID_FILE_MESSAGE = "This is not a valid Meshtastic export file."
# The `LongFast` default channel: PSK byte 1, no region (the URL that `test_channel_share.py` also uses).
LONG_FAST_URL = "https://meshtastic.org/e/#CgMSAQESBggBQANIAQ"
ADMIN_KEY = bytes(range(32))


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def export_yaml(
    local_config: localonly_pb2.LocalConfig | None = None,
    module_config: localonly_pb2.LocalModuleConfig | None = None,
    **node: str | dict,
) -> str:
    """Return the `meshtastic --export-config` text of a device, from upstream `export_config`.

    Args:
        local_config: The `config` part of the device.
        module_config: The `module_config` part of the device.
        node: `owner`, `owner_short`, `channel_url`, `canned_messages`, `ringtone` and `position`.
    """
    local_node = SimpleNamespace(
        localConfig=local_config or localonly_pb2.LocalConfig(),
        moduleConfig=module_config or localonly_pb2.LocalModuleConfig(),
        getURL=lambda: node.get("channel_url", ""),
    )
    interface = SimpleNamespace(
        localNode=local_node,
        getLongName=lambda: node.get("owner", ""),
        getShortName=lambda: node.get("owner_short", ""),
        getMyNodeInfo=lambda: {"position": node.get("position")},
        getCannedMessage=lambda: node.get("canned_messages", ""),
        getRingtone=lambda: node.get("ringtone", ""),
    )
    return export_config(interface)


async def upload(client, text: str | bytes):
    return await client.post(IMPORT_URL, files={"file": ("config.yaml", text, "application/yaml")})


async def imported(client, text: str) -> dict:
    """Return the body of a successful import of `text`."""
    response = await upload(client, text)
    assert response.status_code == 200, response.text
    body = response.json()
    # The page fills the form from `config`, so the config must pass the form validation unchanged.
    ConfigRequest.model_validate(body["config"])
    return body


async def test_owner_alice_fills_the_long_name_alice(client):
    body = await imported(client, export_yaml(owner="Alice", owner_short="ALC"))

    assert body["config"]["owner_long_name"] == "Alice"
    assert body["config"]["owner_short_name"] == "ALC"
    assert body["not_applied"] == []


async def test_module_config_serial_is_listed_as_not_applied(client):
    module_config = localonly_pb2.LocalModuleConfig()
    module_config.serial.enabled = True
    module_config.serial.baud = 5

    body = await imported(client, export_yaml(module_config=module_config, owner="Alice"))

    assert "module_config.serial" in body["not_applied"]
    assert "owner_long_name" in body["config"]


async def test_yaml_with_a_python_object_tag_returns_422(client):
    response = await upload(client, "owner: !!python/object/apply:os.system ['echo hi']\n")

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == INVALID_FILE_MESSAGE
    assert response.json()["detail"][0]["loc"] == ["body", "file"]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(b"owner: [unclosed\n", id="yaml-error"),
        pytest.param(b"- owner\n- Alice\n", id="not-a-mapping"),
        pytest.param(b"", id="empty"),
        pytest.param(b"owner: \xff\xfe\n", id="not-utf8"),
        pytest.param(b"[" * 5000, id="nesting-deeper-than-the-parser-stack"),
    ],
)
async def test_file_that_is_not_an_export_returns_422(client, text):
    response = await upload(client, text)

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == INVALID_FILE_MESSAGE


async def test_file_larger_than_64_kb_returns_413(client):
    response = await upload(client, "owner: Alice\n" + "#" * (64 * 1024))

    assert response.status_code == 413


async def test_file_of_exactly_64_kb_imports(client):
    text = "owner: Alice\n"
    response = await upload(client, text + "#" * (64 * 1024 - len(text)))

    assert response.status_code == 200


async def test_channel_url_fills_the_channels_and_the_lora_preset(client):
    body = await imported(client, export_yaml(channel_url=LONG_FAST_URL))

    config = body["config"]
    assert config["channels_to_write"] == "1"
    assert config["channel_0[name]"] == ""
    assert config["channel_0[psk]"] == "AQ=="
    assert config["lora_enabled"] == "true"
    assert config["lora_modem_preset"] == "meshtastic_Config_LoRaConfig_ModemPreset_LONG_FAST"
    # The URL holds no region, and the form has no `UNSET` value.
    assert "lora_region" not in config


async def test_channel_url_that_does_not_decode_is_listed_as_not_applied(client):
    body = await imported(client, export_yaml(channel_url="https://meshtastic.org/e/#%%%"))

    assert body["not_applied"] == ["channel_url"]
    assert body["warnings"] == ["channel_url: This is not a valid Meshtastic channel URL."]


async def test_lora_section_maps_each_field_with_a_userprefs_key(client):
    local_config = localonly_pb2.LocalConfig()
    lora = local_config.lora
    lora.region = lora.RegionCode.EU_868
    lora.modem_preset = lora.ModemPreset.MEDIUM_FAST
    lora.use_preset = True
    lora.tx_enabled = False
    lora.tx_power = 20
    lora.channel_num = 5
    lora.ignore_mqtt = True
    lora.config_ok_to_mqtt = True
    lora.hop_limit = 4

    body = await imported(client, export_yaml(local_config))

    config = body["config"]
    assert config["lora_enabled"] == "true"
    assert config["lora_region"] == "meshtastic_Config_LoRaConfig_RegionCode_EU_868"
    assert config["lora_modem_preset"] == "meshtastic_Config_LoRaConfig_ModemPreset_MEDIUM_FAST"
    assert config["lora_tx_disabled"] == "true"
    assert config["lora_tx_power"] == "20"
    assert config["lora_channel_num"] == "5"
    assert config["lora_ignore_mqtt"] == "true"
    assert config["lora_config_ok_to_mqtt"] == "true"
    assert "config.lora.hopLimit" in body["not_applied"]
    assert "config.lora.usePreset" in body["not_applied"]


async def test_lora_preset_newer_than_the_meshtastic_package_maps_by_its_proto_number(client):
    local_config = localonly_pb2.LocalConfig()
    # The package stops at NARROW_SLOW = 13, so `MessageToDict` writes TINY_FAST as the number 14.
    local_config.lora.modem_preset = list(enums.MODEM_PRESETS.labels).index("TINY_FAST")

    body = await imported(client, export_yaml(local_config))

    assert body["config"]["lora_modem_preset"] == "meshtastic_Config_LoRaConfig_ModemPreset_TINY_FAST"


async def test_device_section_maps_the_role_and_the_node_info_fields(client):
    local_config = localonly_pb2.LocalConfig()
    device = local_config.device
    device.role = device.Role.TRACKER
    device.rebroadcast_mode = device.RebroadcastMode.LOCAL_ONLY
    device.node_info_broadcast_secs = 3600
    device.tzdef = "EST5EDT,M3.2.0,M11.1.0"
    device.button_gpio = 36
    device.led_heartbeat_disabled = True

    body = await imported(client, export_yaml(local_config))

    config = body["config"]
    assert config["device_role"] == "meshtastic_Config_DeviceConfig_Role_TRACKER"
    assert config["device_rebroadcast_mode"] == "meshtastic_Config_DeviceConfig_RebroadcastMode_LOCAL_ONLY"
    assert config["node_info_broadcast_secs"] == "3600"
    assert config["tz_string"] == "EST5EDT,M3.2.0,M11.1.0"
    assert config["button_pin"] == "36"
    assert config["button_pin_enabled"] == "true"
    assert "config.device.ledHeartbeatDisabled" in body["not_applied"]


async def test_router_role_is_listed_as_not_applied_with_the_router_role_message(client):
    local_config = localonly_pb2.LocalConfig()
    local_config.device.role = local_config.device.Role.ROUTER

    body = await imported(client, export_yaml(local_config, owner="Alice"))

    assert "device_role" not in body["config"]
    assert "config.device.role" in body["not_applied"]
    assert body["warnings"] == [f"config.device.role: {enums.ROUTER_ROLE_MESSAGE}"]
    assert body["config"]["owner_long_name"] == "Alice"


async def test_position_section_and_location_fill_the_fixed_position(client):
    local_config = localonly_pb2.LocalConfig()
    position = local_config.position
    position.fixed_position = True
    position.gps_mode = position.GpsMode.NOT_PRESENT
    position.gps_update_interval = 120
    position.position_broadcast_secs = 900
    position.position_broadcast_smart_enabled = True
    position.broadcast_smart_minimum_distance = 100
    location = {"latitude": 48.8587392, "longitude": 2.2944813, "altitude": 35}

    body = await imported(client, export_yaml(local_config, position=location))

    config = body["config"]
    assert config["gps_enabled"] == "true"
    assert config["gps_mode"] == "meshtastic_Config_PositionConfig_GpsMode_NOT_PRESENT"
    assert config["gps_update_interval"] == "120"
    assert config["position_broadcast_interval"] == "900"
    assert config["smart_position_enabled"] == "true"
    assert config["fixed_position"] == "true"
    assert (config["fixed_lat"], config["fixed_lon"], config["fixed_alt"]) == ("48.8587392", "2.2944813", "35")
    assert "config.position.broadcastSmartMinimumDistance" in body["not_applied"]


async def test_network_section_maps_the_wifi_fields(client):
    local_config = localonly_pb2.LocalConfig()
    network = local_config.network
    network.wifi_enabled = True
    network.wifi_ssid = "meshnet"
    network.wifi_psk = "wifi-secret-1"
    network.enabled_protocols = 1
    network.ipv6_enabled = True
    network.ntp_server = "pool.ntp.org"

    body = await imported(client, export_yaml(local_config))

    config = body["config"]
    assert config["network_enabled"] == "true"
    assert config["wifi_enabled"] == "true"
    assert config["wifi_ssid"] == "meshnet"
    assert config["wifi_psk"] == "wifi-secret-1"
    assert config["network_protocols"] == "1"
    assert config["network_ipv6_enabled"] == "true"
    assert "config.network.ntpServer" in body["not_applied"]


async def test_mqtt_section_maps_the_broker_fields(client):
    module_config = localonly_pb2.LocalModuleConfig()
    mqtt = module_config.mqtt
    mqtt.enabled = True
    mqtt.address = "mqtt.example.org"
    mqtt.username = "mesh"
    mqtt.password = "mqtt-secret-1"
    mqtt.root = "msh/US"
    mqtt.encryption_enabled = True
    mqtt.tls_enabled = True
    mqtt.json_enabled = True

    body = await imported(client, export_yaml(module_config=module_config))

    config = body["config"]
    assert config["network_enabled"] == "true"
    assert config["mqtt_enabled"] == "true"
    assert config["mqtt_address"] == "mqtt.example.org"
    assert config["mqtt_username"] == "mesh"
    assert config["mqtt_password"] == "mqtt-secret-1"
    assert config["mqtt_root_topic"] == "msh/US"
    assert config["mqtt_encryption_enabled"] == "true"
    assert config["mqtt_tls_enabled"] == "true"
    assert "module_config.mqtt.jsonEnabled" in body["not_applied"]


async def test_security_section_maps_the_admin_keys_and_managed_mode(client):
    local_config = localonly_pb2.LocalConfig()
    security = local_config.security
    security.admin_key.append(ADMIN_KEY)
    security.is_managed = True
    security.private_key = bytes(32)

    body = await imported(client, export_yaml(local_config))

    config = body["config"]
    assert config["admin_key_0"] == base64.b64encode(ADMIN_KEY).decode("ascii")
    assert config["security_is_managed"] == "true"
    assert "config.security.privateKey" in body["not_applied"]


async def test_packet_signature_policy_maps_to_the_form_value(client):
    text = "config:\n  security:\n    packetSignaturePolicy: PACKET_SIGNATURE_POLICY_STRICT\n"

    body = await imported(client, text)

    expected = "meshtastic_Config_SecurityConfig_PacketSignaturePolicy_PACKET_SIGNATURE_POLICY_STRICT"
    assert body["config"]["security_packet_signature_policy"] == expected


async def test_bluetooth_pin_maps_only_in_fixed_pin_mode(client):
    fixed = localonly_pb2.LocalConfig()
    fixed.bluetooth.mode = fixed.bluetooth.PairingMode.FIXED_PIN
    fixed.bluetooth.fixed_pin = 654321
    random = localonly_pb2.LocalConfig()
    random.bluetooth.fixed_pin = 123456

    fixed_body = await imported(client, export_yaml(fixed))
    random_body = await imported(client, export_yaml(random))

    assert fixed_body["config"]["bluetooth_fixed_pin"] == "654321"
    assert "config.bluetooth.mode" not in fixed_body["not_applied"]
    assert "bluetooth_fixed_pin" not in random_body["config"]
    assert "config.bluetooth.fixedPin" in random_body["not_applied"]


async def test_telemetry_section_maps_the_intervals_and_the_sensor_switches(client):
    module_config = localonly_pb2.LocalModuleConfig()
    telemetry = module_config.telemetry
    telemetry.device_update_interval = 1800
    telemetry.environment_measurement_enabled = True
    telemetry.environment_update_interval = 900
    telemetry.environment_screen_enabled = True
    telemetry.air_quality_enabled = True
    telemetry.air_quality_interval = 600
    telemetry.air_quality_screen_enabled = True
    telemetry.power_measurement_enabled = True

    body = await imported(client, export_yaml(module_config=module_config))

    config = body["config"]
    assert config["telemetry_device_interval"] == "1800"
    assert config["telemetry_environment_enabled"] == "true"
    assert config["telemetry_environment_interval"] == "900"
    assert config["telemetry_environment_screen_enabled"] == "true"
    assert config["telemetry_air_quality_enabled"] == "true"
    assert config["telemetry_air_quality_interval"] == "600"
    assert config["telemetry_air_quality_screen_enabled"] == "true"
    assert "module_config.telemetry.powerMeasurementEnabled" in body["not_applied"]


async def test_canned_messages_and_ringtone_fill_their_fields(client):
    module_config = localonly_pb2.LocalModuleConfig()
    module_config.external_notification.nag_timeout = 30
    module_config.external_notification.enabled = True
    text = export_yaml(module_config=module_config, canned_messages="Hi|On my way", ringtone="24:d=32,o=5:c")

    body = await imported(client, text)

    config = body["config"]
    assert config["canned_messages"] == "Hi\nOn my way"
    assert config["ringtone_rtttl"] == "24:d=32,o=5:c"
    assert config["ringtone_nag_secs"] == "30"
    assert "module_config.externalNotification.enabled" in body["not_applied"]


async def test_unknown_key_and_unmapped_section_are_listed_as_not_applied(client):
    local_config = localonly_pb2.LocalConfig()
    local_config.display.screen_on_secs = 60

    body = await imported(client, export_yaml(local_config) + "favorite_color: blue\n")

    assert "config.display" in body["not_applied"]
    assert "favorite_color" in body["not_applied"]


async def test_value_of_the_wrong_type_is_listed_as_not_applied(client):
    body = await imported(client, "owner: [Alice, Bob]\nconfig:\n  lora: 7\n")

    assert body["config"] == {}
    assert sorted(body["not_applied"]) == ["config.lora", "owner"]


async def test_value_that_the_form_rejects_is_listed_as_not_applied_with_the_reason(client):
    body = await imported(client, "config:\n  lora:\n    txPower: 40\n    region: US\n")

    assert "lora_tx_power" not in body["config"]
    assert body["config"]["lora_region"] == "meshtastic_Config_LoRaConfig_RegionCode_US"
    assert body["not_applied"] == ["config.lora.txPower"]
    assert body["warnings"] == ["config.lora.txPower: the TX power is not a whole number from 0 to 30"]


async def test_import_writes_no_part_of_the_file_to_the_log(client, caplog):
    local_config = localonly_pb2.LocalConfig()
    local_config.network.wifi_psk = "wifi-secret-1"
    text = export_yaml(local_config, channel_url=LONG_FAST_URL)

    with caplog.at_level(logging.DEBUG):
        await upload(client, text)
        await upload(client, text + "  bad: [\n")

    assert "wifi-secret-1" not in caplog.text
    assert LONG_FAST_URL not in caplog.text
