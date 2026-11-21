"""Tests for the channel URL import (FR-channel-share-1 to -3 and -12).

Each test builds its channel URL from the `meshtastic` protobufs, so no test reads the network.
"""

import base64
import logging
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from meshtastic.protobuf import apponly_pb2, channel_pb2, config_pb2

from mtfwbuilder import meshtastic_enums as enums

IMPORT_URL = "/api/v1/import/channel-url"
INVALID_URL_MESSAGE = "This is not a valid Meshtastic channel URL."
CONFIG_PROTO = Path(__file__).resolve().parent / "fixtures" / "protobufs" / "meshtastic" / "config.proto"

LoRaConfig = config_pb2.Config.LoRaConfig


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def channel_url(channel_set: apponly_pb2.ChannelSet, prefix: str = "https://meshtastic.org/e/#") -> str:
    """Return the channel URL of `channel_set`, in the form of upstream `Node.getURL`."""
    fragment = base64.urlsafe_b64encode(channel_set.SerializeToString()).decode("ascii").replace("=", "")
    return prefix + fragment


def long_fast_set() -> apponly_pb2.ChannelSet:
    """Return the channel set of the default `LongFast` channel: PSK byte 1, preset `LONG_FAST`, region US."""
    channel_set = apponly_pb2.ChannelSet()
    channel_set.settings.add(psk=b"\x01")
    channel_set.lora_config.CopyFrom(LoRaConfig(use_preset=True, region=LoRaConfig.RegionCode.US, hop_limit=3))
    return channel_set


async def test_default_long_fast_url_imports_as_channel_0_with_default_key_1(client):
    response = await client.post(IMPORT_URL, json={"url": channel_url(long_fast_set())})

    assert response.status_code == 200
    body = response.json()
    assert [channel["index"] for channel in body["channels"]] == [0]
    channel = body["channels"][0]
    assert channel["name"] == ""
    assert channel["psk"] == "AQ=="
    assert channel["default_key"] == 1
    assert channel["role"] == "PRIMARY"
    assert body["lora"]["modem_preset"] == "LONG_FAST"
    assert body["lora"]["region"] == "US"
    assert body["lora"]["use_preset"] is True
    assert body["lora"]["hop_limit"] == 3
    assert body["add"] is False
    assert body["warnings"] == []


async def test_literal_long_fast_url_without_a_region_imports_as_channel_0_with_default_key_1(client):
    # A literal URL keeps the test independent of the `channel_url` helper: PSK byte 1, no region, hop limit 3.
    response = await client.post(IMPORT_URL, json={"url": "https://meshtastic.org/e/#CgMSAQESBggBQANIAQ"})

    assert response.status_code == 200
    channel = response.json()["channels"][0]
    assert (channel["index"], channel["name"], channel["default_key"]) == (0, "", 1)
    assert response.json()["lora"]["modem_preset"] == "LONG_FAST"
    assert response.json()["lora"]["region"] == "UNSET"


async def test_add_url_imports_with_the_add_flag(client):
    url = channel_url(long_fast_set(), prefix="https://meshtastic.org/e/?add=true#")

    response = await client.post(IMPORT_URL, json={"url": url})

    assert response.status_code == 200
    assert response.json()["add"] is True


async def test_channel_fields_import_with_a_base64_psk_and_secondary_roles(client):
    key = bytes(range(32))
    channel_set = long_fast_set()
    channel_set.settings.add(
        name="Ops",
        psk=key,
        uplink_enabled=True,
        downlink_enabled=True,
        module_settings=channel_pb2.ModuleSettings(position_precision=13),
    )

    response = await client.post(IMPORT_URL, json={"url": channel_url(channel_set)})

    assert response.status_code == 200
    second = response.json()["channels"][1]
    assert second == {
        "index": 1,
        "name": "Ops",
        "psk": base64.b64encode(key).decode("ascii"),
        "default_key": None,
        "role": "SECONDARY",
        "uses_primary_key": False,
        "uplink_enabled": True,
        "downlink_enabled": True,
        "position_precision": 13,
    }


async def test_url_with_more_than_8_channels_imports_the_first_8_and_warns(client):
    channel_set = long_fast_set()
    for number in range(1, 10):
        channel_set.settings.add(name=f"ch{number}", psk=b"\x01")

    response = await client.post(IMPORT_URL, json={"url": channel_url(channel_set)})

    assert response.status_code == 200
    body = response.json()
    assert [channel["index"] for channel in body["channels"]] == list(range(8))
    assert body["channels"][7]["name"] == "ch7"
    assert body["warnings"] == ["The URL has 10 channels. The import takes the first 8."]


async def test_modem_preset_newer_than_the_meshtastic_package_imports_by_its_proto_name(client):
    channel_set = long_fast_set()
    # TINY_FAST (14) and ITU2_125CM (37) are in protobufs v2.8.3, but not in `meshtastic` 2.7.11.
    channel_set.lora_config.modem_preset = 14
    channel_set.lora_config.region = 37

    response = await client.post(IMPORT_URL, json={"url": channel_url(channel_set)})

    assert response.json()["lora"]["modem_preset"] == "TINY_FAST"
    assert response.json()["lora"]["region"] == "ITU2_125CM"


async def test_enum_number_that_protobufs_v2_8_3_does_not_define_imports_as_null(client):
    channel_set = long_fast_set()
    channel_set.lora_config.modem_preset = 99

    response = await client.post(IMPORT_URL, json={"url": channel_url(channel_set)})

    assert response.status_code == 200
    assert response.json()["lora"]["modem_preset"] is None


def _proto_numbers(enum_name: str) -> list[int]:
    block = re.search(rf"enum {enum_name} \{{(.*?)\n    \}}", CONFIG_PROTO.read_text(), re.S)[1]
    return [int(number) for number in re.findall(r"^\s*[A-Z][A-Z0-9_]* = (\d+)", block, re.M)]


def test_enum_module_lists_presets_and_regions_in_proto_number_order():
    # The import reads a preset or a region number as a position in the enum module.
    assert _proto_numbers("ModemPreset") == list(range(len(enums.MODEM_PRESETS.labels)))
    assert _proto_numbers("RegionCode") == list(range(len(enums.REGIONS.labels) + 1))


def _broken_fragment_urls() -> list[str]:
    valid = channel_url(long_fast_set())
    return [
        "https://meshtastic.org/e/#",
        "https://meshtastic.org/e/#not*base64",
        "https://meshtastic.org/e/#A",
        valid[:-3],
        channel_url(apponly_pb2.ChannelSet(lora_config=LoRaConfig(use_preset=True))),
        valid.replace("/e/#", "/v/#"),
        valid.replace("meshtastic.org", "example.com"),
        valid.replace("?", "").replace("/e/#", "/e/?add=false#"),
        "meshtastic.org/e/#CgMSAQESBggBQANIAQ",
    ]


@pytest.mark.parametrize("url", _broken_fragment_urls())
async def test_url_with_a_broken_fragment_returns_422_with_the_invalid_url_message(client, url):
    response = await client.post(IMPORT_URL, json={"url": url})

    assert response.status_code == 422
    assert response.json() == {"detail": [{"loc": ["body", "url"], "msg": INVALID_URL_MESSAGE, "type": "value_error"}]}


async def test_import_writes_no_part_of_the_url_or_the_psk_to_the_log(client, caplog):
    key = bytes(range(16))
    channel_set = long_fast_set()
    channel_set.settings.add(name="Ops", psk=key)
    url = channel_url(channel_set)
    caplog.set_level(logging.DEBUG)

    await client.post(IMPORT_URL, json={"url": url})
    await client.post(IMPORT_URL, json={"url": url[:-3]})

    fragment = url.split("#")[1]
    for secret in (fragment, fragment[:-3], base64.b64encode(key).decode("ascii"), key.hex()):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://meshtastic.org/e/#CgMSAQé", id="non-ascii-fragment"),
        pytest.param("https://[meshtastic.org/e/#CgMSAQE", id="bad-ipv6-host"),
    ],
)
async def test_url_that_urlsplit_or_base64_rejects_returns_422(client, url):
    response = await client.post(IMPORT_URL, json={"url": url})

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == INVALID_URL_MESSAGE


async def test_url_longer_than_the_maximum_returns_422(client):
    response = await client.post(IMPORT_URL, json={"url": "https://meshtastic.org/e/#" + "A" * 5000})

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_long"


async def test_add_url_gives_every_channel_the_secondary_role(client):
    url = channel_url(long_fast_set(), prefix="https://meshtastic.org/e/?add=true#")

    response = await client.post(IMPORT_URL, json={"url": url})

    assert [channel["role"] for channel in response.json()["channels"]] == ["SECONDARY"]


def _empty_psk_set() -> apponly_pb2.ChannelSet:
    """Return a channel set of a primary and a secondary channel, both with an empty PSK."""
    channel_set = apponly_pb2.ChannelSet()
    channel_set.settings.add(name="Open")
    channel_set.settings.add(name="Side")
    return channel_set


async def test_primary_channel_with_an_empty_psk_does_not_use_the_primary_key(client):
    # Upstream `Channels::getKey` turns encryption off for a primary channel with a PSK of 0 bytes.
    response = await client.post(IMPORT_URL, json={"url": channel_url(_empty_psk_set())})

    channel = response.json()["channels"][0]
    assert channel["role"] == "PRIMARY"
    assert channel["uses_primary_key"] is False


async def test_secondary_channel_with_an_empty_psk_uses_the_primary_key(client):
    # Upstream `Channels::getKey` gives a secondary channel with a PSK of 0 bytes the key of the primary channel.
    response = await client.post(IMPORT_URL, json={"url": channel_url(_empty_psk_set())})

    channel = response.json()["channels"][1]
    assert channel["role"] == "SECONDARY"
    assert channel["uses_primary_key"] is True
