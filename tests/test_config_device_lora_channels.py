"""Tests for channels 0–7 and the device, LoRa and network keys (FR-config-coverage-5, -6, -7 and -10)."""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.models import CHANNEL_SLOTS, ConfigRequest
from mtfwbuilder.services.jsonc_generator import DEFAULT_FIRMWARE_KEYS, VERSION_GATED_KEYS, generate_jsonc
from mtfwbuilder.services.userprefs_keys import read_keys
from tests.test_config_enums import _SelectParser

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "firmware"
PSK_HEX = "deadbeef" * 8
CHANNEL_FIELDS = ("NAME", "PSK", "PRECISION", "UPLINK_ENABLED", "DOWNLINK_ENABLED", "IS_MUTED")

# Upstream v2.8.2.d5f1619 reads these keys, and the fixture firmware v2.7.26.54e0d8d does not:
# `src/mesh/Channels.cpp` (USERPREFS_APPLY_CHANNEL, `USERPREFS_LORACONFIG_TX_POWER`) and
# `src/mesh/NodeDB.cpp` (`USERPREFS_CONFIG_DEVICE_REBROADCAST_MODE`, `..._NODE_INFO_BROADCAST_SECS`,
# `USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT`).
NEWER_FIRMWARE_KEYS = (
    read_keys(FIXTURE_DIR)
    | {f"USERPREFS_CHANNEL_{n}_{field}" for n in range(CHANNEL_SLOTS) for field in CHANNEL_FIELDS}
    | {
        "USERPREFS_CONFIG_DEVICE_REBROADCAST_MODE",
        "USERPREFS_CONFIG_DEVICE_NODE_INFO_BROADCAST_SECS",
        "USERPREFS_LORACONFIG_TX_POWER",
        "USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT",
    }
)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def generate(form: dict[str, str], firmware_keys: frozenset[str] | None = None) -> dict[str, str]:
    """Return the userPrefs keys that the generator writes for validated form data."""
    data = ConfigRequest(**form).generator_input()
    content = generate_jsonc(data) if firmware_keys is None else generate_jsonc(data, firmware_keys)
    return json.loads(content)


def field_errors(resp, field: str) -> list[str]:
    """Return the messages of the field errors at `field` in a 422 response."""
    return [err["msg"] for err in resp.json()["detail"] if err["loc"][1:] == [field]]


# Acceptance criteria


def test_role_tracker_writes_the_role_key_with_its_c_prefix():
    keys = generate({"device_role": "TRACKER"})
    assert keys["USERPREFS_CONFIG_DEVICE_ROLE"] == "meshtastic_Config_DeviceConfig_Role_TRACKER"


def test_channel_5_writes_its_psk_where_the_firmware_reads_it():
    form = {"channels_to_write": "6", "channel_5[name]": "Ops", "channel_5[psk]": PSK_HEX}
    keys = generate(form, NEWER_FIRMWARE_KEYS)
    assert keys["USERPREFS_CHANNEL_5_PSK"].startswith("{ 0xde, 0xad, 0xbe, 0xef")
    assert keys["USERPREFS_CHANNEL_5_NAME"] == "Ops"


@pytest.mark.asyncio
async def test_preview_of_role_tracker_shows_the_role_key(client):
    resp = await client.post("/api/v1/preview", json={"device_role": "TRACKER"})
    assert resp.status_code == 200
    keys = json.loads(resp.json()["content"])
    assert keys["USERPREFS_CONFIG_DEVICE_ROLE"] == "meshtastic_Config_DeviceConfig_Role_TRACKER"


# Channels 0–7


@pytest.mark.parametrize("channel", range(CHANNEL_SLOTS))
def test_each_channel_writes_every_field_where_the_firmware_reads_it(channel):
    form = {
        "channels_to_write": str(CHANNEL_SLOTS),
        f"channel_{channel}[name]": "Chan",
        f"channel_{channel}[psk]": PSK_HEX,
        f"channel_{channel}[precision]": "14",
        f"channel_{channel}[uplink_enabled]": "true",
        f"channel_{channel}[downlink_enabled]": "false",
        f"channel_{channel}[muted]": "true",
    }
    keys = generate(form, NEWER_FIRMWARE_KEYS)
    assert {key for key in keys if key.startswith(f"USERPREFS_CHANNEL_{channel}_")} == {
        f"USERPREFS_CHANNEL_{channel}_{field}" for field in CHANNEL_FIELDS
    }
    assert keys[f"USERPREFS_CHANNEL_{channel}_IS_MUTED"] == "true"
    assert keys[f"USERPREFS_CHANNEL_{channel}_DOWNLINK_ENABLED"] == "false"


def test_muted_off_writes_false():
    keys = generate({"channels_to_write": "1", "channel_0[muted]": "false"}, NEWER_FIRMWARE_KEYS)
    assert keys["USERPREFS_CHANNEL_0_IS_MUTED"] == "false"


def test_channel_5_writes_no_key_for_the_fixture_firmware():
    keys = generate({"channels_to_write": "6", "channel_5[psk]": PSK_HEX})
    assert not any(key.startswith("USERPREFS_CHANNEL_5_") for key in keys)


def test_muted_writes_no_key_for_the_fixture_firmware():
    keys = generate({"channels_to_write": "1", "channel_0[name]": "Primary", "channel_0[muted]": "true"})
    assert keys["USERPREFS_CHANNEL_0_NAME"] == "Primary"
    assert "USERPREFS_CHANNEL_0_IS_MUTED" not in keys


def test_default_key_set_is_the_gated_keys_that_the_fixture_firmware_reads():
    assert VERSION_GATED_KEYS & read_keys(FIXTURE_DIR) == DEFAULT_FIRMWARE_KEYS


def test_every_gated_key_is_a_key_that_newer_firmware_reads():
    assert VERSION_GATED_KEYS <= NEWER_FIRMWARE_KEYS


# Device keys


def test_rebroadcast_mode_writes_the_key_with_its_c_prefix():
    keys = generate({"device_rebroadcast_mode": "LOCAL_ONLY"}, NEWER_FIRMWARE_KEYS)
    assert (
        keys["USERPREFS_CONFIG_DEVICE_REBROADCAST_MODE"] == "meshtastic_Config_DeviceConfig_RebroadcastMode_LOCAL_ONLY"
    )


def test_node_info_broadcast_secs_writes_the_key():
    keys = generate({"node_info_broadcast_secs": "10800"}, NEWER_FIRMWARE_KEYS)
    assert keys["USERPREFS_CONFIG_DEVICE_NODE_INFO_BROADCAST_SECS"] == "10800"


def test_node_info_reply_suppress_secs_writes_the_key():
    keys = generate({"node_info_reply_suppress_secs": "43200"})
    assert keys["USERPREFS_NODEINFO_REPLY_SUPPRESS_SECS"] == "43200"


def test_device_keys_that_the_fixture_firmware_does_not_read_write_no_key():
    keys = generate({"device_rebroadcast_mode": "LOCAL_ONLY", "node_info_broadcast_secs": "10800"})
    assert "USERPREFS_CONFIG_DEVICE_REBROADCAST_MODE" not in keys
    assert "USERPREFS_CONFIG_DEVICE_NODE_INFO_BROADCAST_SECS" not in keys


def test_empty_device_fields_write_no_key():
    keys = generate(
        {"device_role": "", "device_rebroadcast_mode": "", "node_info_broadcast_secs": ""}, NEWER_FIRMWARE_KEYS
    )
    assert not any(key.startswith("USERPREFS_CONFIG_DEVICE_") for key in keys)


# LoRa keys


def test_lora_tx_keys_write_their_values():
    form = {
        "lora_enabled": "true",
        "lora_tx_power": "0",
        "lora_tx_disabled": "true",
        "lora_config_ok_to_mqtt": "false",
    }
    keys = generate(form, NEWER_FIRMWARE_KEYS)
    assert keys["USERPREFS_LORACONFIG_TX_POWER"] == "0"
    assert keys["USERPREFS_LORA_TX_DISABLED"] == "true"
    assert keys["USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT"] == "false"


def test_lora_tx_keys_write_nothing_when_lora_config_is_off():
    form = {
        "lora_enabled": "false",
        "lora_tx_power": "20",
        "lora_tx_disabled": "true",
        "lora_config_ok_to_mqtt": "true",
    }
    keys = generate(form, NEWER_FIRMWARE_KEYS)
    assert not {"USERPREFS_LORACONFIG_TX_POWER", "USERPREFS_LORA_TX_DISABLED"} & set(keys)
    assert "USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT" not in keys


def test_lora_keys_that_the_fixture_firmware_does_not_read_write_no_key():
    form = {"lora_enabled": "true", "lora_tx_power": "20", "lora_tx_disabled": "true", "lora_config_ok_to_mqtt": "true"}
    keys = generate(form)
    assert keys["USERPREFS_LORA_TX_DISABLED"] == "true"
    assert "USERPREFS_LORACONFIG_TX_POWER" not in keys
    assert "USERPREFS_CONFIG_LORA_CONFIG_OK_TO_MQTT" not in keys


# Network key


def test_ipv6_writes_the_key_when_network_config_is_on():
    keys = generate({"network_enabled": "true", "network_ipv6_enabled": "true"})
    assert keys["USERPREFS_NETWORK_IPV6_ENABLED"] == "true"


def test_ipv6_writes_no_key_when_network_config_is_off():
    keys = generate({"network_enabled": "false", "network_ipv6_enabled": "true"})
    assert "USERPREFS_NETWORK_IPV6_ENABLED" not in keys


# Validation


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "30"])
async def test_tx_power_from_0_to_30_is_valid(client, value):
    resp = await client.post("/api/v1/preview", json={"lora_tx_power": value})
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["31", "-1", "abc", "1.5"])
async def test_tx_power_outside_0_to_30_returns_422(client, value):
    resp = await client.post("/api/v1/preview", json={"lora_tx_power": value})
    assert resp.status_code == 422
    assert field_errors(resp, "lora_tx_power") == ["Value error, the TX power is not a whole number from 0 to 30"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "60", "2592000"])
async def test_node_info_broadcast_secs_of_0_or_60_to_2592000_is_valid(client, value):
    resp = await client.post("/api/v1/preview", json={"node_info_broadcast_secs": value})
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["59", "2592001", "1h"])
async def test_node_info_broadcast_secs_outside_the_interval_range_returns_422(client, value):
    resp = await client.post("/api/v1/preview", json={"node_info_broadcast_secs": value})
    assert resp.status_code == 422
    assert field_errors(resp, "node_info_broadcast_secs") == [
        "Value error, the interval is not 0 or a whole number of seconds from 60 to 2592000"
    ]


@pytest.mark.asyncio
async def test_node_info_reply_suppress_secs_that_is_not_a_whole_number_returns_422(client):
    resp = await client.post("/api/v1/preview", json={"node_info_reply_suppress_secs": "12h"})
    assert resp.status_code == 422
    assert field_errors(resp, "node_info_reply_suppress_secs") == ["Value error, the value is not a whole number"]


@pytest.mark.asyncio
async def test_unknown_rebroadcast_mode_returns_422(client):
    resp = await client.post("/api/v1/preview", json={"device_rebroadcast_mode": "SOMETIMES"})
    assert resp.status_code == 422
    assert field_errors(resp, "device_rebroadcast_mode")


# Form controls


async def page_html(client) -> str:
    resp = await client.get("/")
    assert resp.status_code == 200
    return resp.text


@pytest.mark.asyncio
async def test_channel_count_control_offers_0_to_8(client):
    parser = _SelectParser()
    parser.feed(await page_html(client))
    assert parser.options["channels_to_write"] == [str(n) for n in range(CHANNEL_SLOTS + 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", range(CHANNEL_SLOTS))
async def test_each_channel_tab_has_every_channel_control(client, channel):
    html = await page_html(client)
    for field in ("name", "psk", "precision", "uplink_enabled", "downlink_enabled", "muted"):
        assert f'name="channel_{channel}[{field}]"' in html


@pytest.mark.asyncio
async def test_rebroadcast_mode_control_lists_the_six_modes(client):
    parser = _SelectParser()
    parser.feed(await page_html(client))
    assert parser.options["device_rebroadcast_mode"] == [""] + [
        f"meshtastic_Config_DeviceConfig_RebroadcastMode_{mode}"
        for mode in ("ALL", "ALL_SKIP_DECODING", "LOCAL_ONLY", "KNOWN_ONLY", "NONE", "CORE_PORTNUMS_ONLY")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "node_info_broadcast_secs",
        "node_info_reply_suppress_secs",
        "lora_tx_power",
        "lora_tx_disabled",
        "lora_config_ok_to_mqtt",
        "network_ipv6_enabled",
    ],
)
async def test_form_has_a_control_for_each_new_key(client, name):
    assert f'name="{name}"' in await page_html(client)
