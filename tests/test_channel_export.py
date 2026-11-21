"""Tests for the channel URL export (FR-channel-share-9 and -10).

The round-trip tests import a URL with `POST /api/v1/import/channel-url`, fill a config from the import, and export it.
"""

import base64
import logging
import re
import xml.etree.ElementTree as ET

import pytest
import segno
import zxingcpp
from httpx import ASGITransport, AsyncClient
from meshtastic.protobuf import apponly_pb2, channel_pb2, config_pb2

IMPORT_URL = "/api/v1/import/channel-url"
EXPORT_URL = "/api/v1/export/channel-url"
URL_PREFIX = "https://meshtastic.org/e/#"
# The default `LongFast` channel: PSK byte 1, no region, hop limit 3, TX on.
LONG_FAST_URL = "https://meshtastic.org/e/#CgMSAQESBggBQANIAQ"
NO_REGION_WARNING = "The config sets no LoRa region, so the channel URL holds the region UNSET."
QR_OVERFLOW_MESSAGE = "the channel URL is too long for a QR code"

LoRaConfig = config_pb2.Config.LoRaConfig


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def channel_url(channel_set: apponly_pb2.ChannelSet) -> str:
    """Return the channel URL of `channel_set`, in the form of upstream `Node.getURL`."""
    return URL_PREFIX + base64.urlsafe_b64encode(channel_set.SerializeToString()).decode("ascii").replace("=", "")


def decoded_set(url: str) -> apponly_pb2.ChannelSet:
    """Return the `ChannelSet` in the fragment of a channel URL."""
    fragment = url.removeprefix(URL_PREFIX)
    return apponly_pb2.ChannelSet.FromString(base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4)))


def _true_false(value: bool) -> str:
    return "true" if value else "false"


def config_from_import(imported: dict) -> dict:
    """Return the config that the form holds after it applies an import response."""
    config = {"channels_to_write": str(len(imported["channels"]))}
    for channel in imported["channels"]:
        config[f"channel_{channel['index']}"] = {
            "name": channel["name"],
            # The form reads an empty PSK field as default key 1, so the page fills `{ }` for an empty PSK.
            "psk": channel["psk"] or "{ }",
            "precision": str(channel["position_precision"]),
            "uplink_enabled": _true_false(channel["uplink_enabled"]),
            "downlink_enabled": _true_false(channel["downlink_enabled"]),
        }
    lora = imported["lora"]
    config |= {
        "lora_enabled": "true",
        "lora_modem_preset": lora["modem_preset"],
        "lora_channel_num": str(lora["channel_num"]),
        "lora_tx_power": str(lora["tx_power"]),
        "lora_tx_disabled": _true_false(not lora["tx_enabled"]),
        "lora_ignore_mqtt": _true_false(lora["ignore_mqtt"]),
        "lora_config_ok_to_mqtt": _true_false(lora["config_ok_to_mqtt"]),
    }
    # The form has no `UNSET` region: an empty region field leaves the firmware at `UNSET`.
    if lora["region"] != "UNSET":
        config["lora_region"] = lora["region"]
    return config


async def export_of_import(client, url: str) -> str:
    imported = await client.post(IMPORT_URL, json={"url": url})
    assert imported.status_code == 200
    exported = await client.post(EXPORT_URL, json=config_from_import(imported.json()))
    assert exported.status_code == 200
    return exported.json()["url"]


async def test_export_of_the_imported_long_fast_url_gives_the_same_url(client):
    assert await export_of_import(client, LONG_FAST_URL) == LONG_FAST_URL


async def test_export_of_an_imported_url_with_three_channels_gives_the_same_url(client):
    channel_set = apponly_pb2.ChannelSet()
    channel_set.settings.add(
        name="Event",
        psk=bytes(range(32)),
        uplink_enabled=True,
        module_settings=channel_pb2.ModuleSettings(position_precision=13),
    )
    channel_set.settings.add(name="Admin", psk=bytes(range(16, 32)), downlink_enabled=True)
    channel_set.settings.add(name="Spare", psk=b"\x02")
    # TINY_FAST (14) is in protobufs v2.8.3, but not in `meshtastic` 2.7.11.
    channel_set.lora_config.CopyFrom(
        LoRaConfig(
            use_preset=True,
            modem_preset=14,
            region=LoRaConfig.RegionCode.EU_868,
            hop_limit=3,
            tx_enabled=True,
            tx_power=20,
            channel_num=5,
            ignore_mqtt=True,
            config_ok_to_mqtt=True,
        )
    )
    url = channel_url(channel_set)

    assert await export_of_import(client, url) == url


async def test_default_key_psk_exports_as_one_byte_with_the_key_number(client):
    response = await client.post(EXPORT_URL, json={"channel_0": {"psk": "AQ=="}})

    assert response.status_code == 200
    settings = decoded_set(response.json()["url"]).settings
    assert [bytes(channel.psk) for channel in settings] == [b"\x01"]


async def test_channel_without_fields_exports_with_the_firmware_defaults(client):
    response = await client.post(EXPORT_URL, json={"channels_to_write": "2"})

    assert response.status_code == 200
    channel_set = decoded_set(response.json()["url"])
    # Upstream `Channels::initDefaultChannel` gives each written slot PSK byte 1 and position precision 13.
    assert [bytes(channel.psk) for channel in channel_set.settings] == [b"\x01", b"\x01"]
    assert [channel.name for channel in channel_set.settings] == ["", ""]
    assert [channel.module_settings.position_precision for channel in channel_set.settings] == [13, 13]


async def test_lora_fields_without_lora_enabled_export_as_the_firmware_defaults(client):
    config = {"lora_region": "US", "lora_modem_preset": "SHORT_FAST", "lora_tx_power": "10"}

    response = await client.post(EXPORT_URL, json=config)

    assert response.status_code == 200
    lora = decoded_set(response.json()["url"]).lora_config
    assert lora == LoRaConfig(use_preset=True, hop_limit=3, tx_enabled=True)


async def test_tx_disabled_exports_with_tx_off(client):
    response = await client.post(EXPORT_URL, json={"lora_enabled": "true", "lora_tx_disabled": "true"})

    assert decoded_set(response.json()["url"]).lora_config.tx_enabled is False


async def test_muted_channel_exports_with_the_muted_flag(client):
    response = await client.post(EXPORT_URL, json={"channel_0": {"muted": "true", "precision": "0"}})

    assert decoded_set(response.json()["url"]).settings[0].module_settings.is_muted is True


async def test_export_with_no_channel_returns_422(client):
    response = await client.post(EXPORT_URL, json={"channels_to_write": "0"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "channels_to_write"]


async def test_export_rejects_a_config_that_fails_validation(client):
    response = await client.post(EXPORT_URL, json={"channel_0": {"psk": "AAAA"}})

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("config", "location"),
    [
        ({"channel_0": {"precision": "high"}}, ["body", "channel_0", "precision"]),
        ({"lora_enabled": "true", "lora_channel_num": "4294967296"}, ["body", "lora_channel_num"]),
    ],
)
async def test_export_of_a_number_field_that_is_not_a_uint32_returns_422(client, config, location):
    response = await client.post(EXPORT_URL, json=config)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == location


def _svg_modules(svg: str) -> set[tuple[int, int]]:
    """Return the `(column, row)` of each dark module that the `path` of a segno SVG draws."""
    path = ET.fromstring(svg).find("{http://www.w3.org/2000/svg}path")
    modules, x, y = set(), 0.0, 0.0
    for command, first, second in re.findall(r"([Mmh])(-?[\d.]+)(?:\s(-?[\d.]+))?", path.get("d")):
        if command == "M":
            x, y = float(first), float(second)
        elif command == "m":
            x, y = x + float(first), y + float(second)
        else:
            modules.update((int(x) + offset, int(y)) for offset in range(int(first)))
            x += float(first)
    return modules


def _decode_qr(svg: str) -> list[str]:
    """Return the text of each QR code in the image that the SVG draws."""
    modules = _svg_modules(svg)
    scale, size = 4, max(max(column, row) for column, row in modules) + 5
    pixels = bytearray(b"\xff" * (size * scale) ** 2)
    for column, row in modules:
        for dy in range(scale):
            start = (row * scale + dy) * size * scale + column * scale
            pixels[start : start + scale] = b"\x00" * scale
    image = memoryview(bytes(pixels)).cast("B", (size * scale, size * scale))
    return [barcode.text for barcode in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)]


async def test_export_svg_holds_a_qr_code_that_decodes_to_the_channel_url(client):
    config = {"channels_to_write": "2", "channel_0": {"name": "Event", "psk": base64.b64encode(bytes(32)).decode()}}

    response = await client.post(EXPORT_URL, json=config)

    body = response.json()
    assert body["qr_svg"].startswith("<svg")
    assert _decode_qr(body["qr_svg"]) == [body["url"]]


async def test_export_writes_no_part_of_the_url_or_the_psk_to_the_log(client, caplog):
    key = bytes(range(16))
    caplog.set_level(logging.DEBUG)

    response = await client.post(EXPORT_URL, json={"channel_0": {"psk": key.hex()}})

    fragment = response.json()["url"].removeprefix(URL_PREFIX)
    for secret in (fragment, base64.b64encode(key).decode("ascii"), key.hex()):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({}, id="lora-switch-off"),
        pytest.param({"lora_enabled": "true", "lora_modem_preset": "LONG_FAST"}, id="no-region"),
    ],
)
async def test_export_without_a_region_returns_the_url_and_a_warning(client, config):
    response = await client.post(EXPORT_URL, json=config)

    assert response.status_code == 200
    assert response.json()["warnings"] == [NO_REGION_WARNING]


async def test_export_with_a_region_returns_no_warning(client):
    response = await client.post(EXPORT_URL, json={"lora_enabled": "true", "lora_region": "EU_868"})

    assert response.json()["warnings"] == []


async def test_empty_psk_byte_list_exports_a_channel_without_encryption(client):
    response = await client.post(EXPORT_URL, json={"channels_to_write": "1", "channel_0": {"psk": "{ }"}})

    assert response.status_code == 200
    assert bytes(decoded_set(response.json()["url"]).settings[0].psk) == b""


async def test_export_of_an_imported_url_without_encryption_gives_the_same_url(client):
    channel_set = apponly_pb2.ChannelSet()
    channel_set.settings.add(name="Open", module_settings=channel_pb2.ModuleSettings(position_precision=13))
    channel_set.lora_config.CopyFrom(LoRaConfig(use_preset=True, hop_limit=3, tx_enabled=True))
    url = channel_url(channel_set)

    assert await export_of_import(client, url) == url


@pytest.mark.parametrize("name", ["A" * 12, "Ä" * 6])
async def test_channel_name_longer_than_11_bytes_returns_422(client, name):
    response = await client.post(EXPORT_URL, json={"channel_0": {"name": name}})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "channel_0", "name"]


async def test_channel_name_of_11_bytes_exports(client):
    response = await client.post(EXPORT_URL, json={"channel_0": {"name": "A" * 11}})

    assert response.status_code == 200


async def test_url_too_long_for_a_qr_code_returns_422(client, monkeypatch):
    def overflow(*_args, **_kwargs):
        raise segno.DataOverflowError("too much data")

    monkeypatch.setattr(segno, "make", overflow)

    response = await client.post(EXPORT_URL, json={})

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == QR_OVERFLOW_MESSAGE
