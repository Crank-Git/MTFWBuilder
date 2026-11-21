"""Tests for the mesh beacon keys (FR-config-coverage-13)."""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from mtfwbuilder.models import ConfigRequest
from mtfwbuilder.services.jsonc_generator import VERSION_GATED_KEYS, generate_jsonc
from tests.test_config_enums import _SelectParser

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "includes" / "mesh_beacon_config.html"
PSK_HEX = "38" * 32
PRESET = "meshtastic_Config_LoRaConfig_ModemPreset_"
REGION = "meshtastic_Config_LoRaConfig_RegionCode_"
TARGET_FIELDS = ("CHANNEL_INDEX", "FREQUENCY_SLOT", "PRESET", "REGION")

# The `#ifdef USERPREFS_MESH_BEACON_*` keys of upstream `src/mesh/NodeDB.cpp` at v2.8.2.d5f1619 and develop@fe89ac1.
# `USERPREFS_MESH_BEACON_LEGACY_SPLIT` is absent: the spec lists it in Non-goals.
UPSTREAM_KEYS = {
    "USERPREFS_MESH_BEACON_LISTEN_ENABLED",
    "USERPREFS_MESH_BEACON_BROADCAST_ENABLED",
    "USERPREFS_MESH_BEACON_MESSAGE",
    "USERPREFS_MESH_BEACON_INTERVAL_SECS",
    "USERPREFS_MESH_BEACON_OFFER_CHANNEL_NAME",
    "USERPREFS_MESH_BEACON_OFFER_CHANNEL_PSK",
    "USERPREFS_MESH_BEACON_OFFER_FREQUENCY_SLOT",
    "USERPREFS_MESH_BEACON_OFFER_PRESET",
    "USERPREFS_MESH_BEACON_OFFER_REGION",
} | {f"USERPREFS_MESH_BEACON_TARGET_{n}_{field}" for n in range(4) for field in TARGET_FIELDS}


def full_beacon_form() -> dict[str, str]:
    """Return form data that sets every mesh beacon field, with the values of the upstream examples."""
    form = {
        "mesh_beacon_listen_enabled": "true",
        "mesh_beacon_broadcast_enabled": "true",
        "mesh_beacon_message": "Join us on NarrowSlow!!!",
        "mesh_beacon_interval_secs": "3600",
        "mesh_beacon_offer_channel_name": "MyChannel",
        "mesh_beacon_offer_channel_psk": PSK_HEX,
        "mesh_beacon_offer_frequency_slot": "48",
        "mesh_beacon_offer_preset": "NARROW_SLOW",
        "mesh_beacon_offer_region": "EU_N_868",
    }
    for n in range(4):
        form[f"mesh_beacon_target_{n}_channel_index"] = str(n)
        form[f"mesh_beacon_target_{n}_frequency_slot"] = "48"
        form[f"mesh_beacon_target_{n}_preset"] = "LONG_FAST"
        form[f"mesh_beacon_target_{n}_region"] = "EU_868"
    return form


def generated(data: dict[str, str]) -> dict[str, str]:
    """Return the keys that a firmware version that reads every gated key gets for validated form data."""
    request = ConfigRequest(**data)
    return json.loads(generate_jsonc(request.generator_input(), firmware_keys=VERSION_GATED_KEYS))


def error_messages(exc: ValidationError, field: str) -> list[str]:
    """Return the messages of the validation errors at `field`."""
    return [err["msg"] for err in exc.errors() if err["loc"] == (field,)]


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_full_beacon_form_writes_each_upstream_mesh_beacon_key():
    keys = {key for key in generated(full_beacon_form()) if key.startswith("USERPREFS_MESH_BEACON_")}
    assert keys == UPSTREAM_KEYS


def test_generator_never_writes_the_legacy_split_key():
    form = full_beacon_form() | {"mesh_beacon_legacy_split": "true"}
    assert "USERPREFS_MESH_BEACON_LEGACY_SPLIT" not in generated(form)


def test_every_mesh_beacon_key_is_version_gated():
    assert UPSTREAM_KEYS <= VERSION_GATED_KEYS


def test_v2_7_26_output_has_no_mesh_beacon_key():
    result = json.loads(generate_jsonc(ConfigRequest(**full_beacon_form()).generator_input()))
    assert not [key for key in result if key.startswith("USERPREFS_MESH_BEACON_")]


def test_empty_mesh_beacon_fields_write_no_key():
    assert generated(dict.fromkeys(full_beacon_form(), "")) == {"USERPREFS_CHANNELS_TO_WRITE": "1"}


# Broadcast, listen, interval and message


@pytest.mark.parametrize("field", ["listen_enabled", "broadcast_enabled"])
@pytest.mark.parametrize("value", ["true", "false"])
def test_listen_and_broadcast_give_their_key_as_true_or_false(field, value):
    key = f"USERPREFS_MESH_BEACON_{field.upper()}"
    assert generated({f"mesh_beacon_{field}": value})[key] == value


@pytest.mark.parametrize("field", ["mesh_beacon_listen_enabled", "mesh_beacon_broadcast_enabled"])
def test_listen_or_broadcast_that_is_not_true_or_false_is_a_field_error(field):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(**{field: "on"})
    assert error_messages(exc.value, field)


@pytest.mark.parametrize("seconds", ["0", "3600", "86400", "2592000"])
def test_beacon_interval_gives_its_key(seconds):
    assert generated({"mesh_beacon_interval_secs": seconds})["USERPREFS_MESH_BEACON_INTERVAL_SECS"] == seconds


@pytest.mark.parametrize("seconds", ["60", "3599", "2592001", "-3600", "3600.5", "abc"])
def test_beacon_interval_outside_0_or_3600_to_2592000_is_a_field_error(seconds):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(mesh_beacon_interval_secs=seconds)
    assert error_messages(exc.value, "mesh_beacon_interval_secs")


def test_beacon_message_gives_its_key_as_the_text():
    result = generated({"mesh_beacon_message": "Join us on NarrowSlow!!!"})
    assert result["USERPREFS_MESH_BEACON_MESSAGE"] == "Join us on NarrowSlow!!!"


# Offer


def test_offer_fields_give_their_keys():
    form = {
        "mesh_beacon_offer_channel_name": "MyChannel",
        "mesh_beacon_offer_channel_psk": PSK_HEX,
        "mesh_beacon_offer_frequency_slot": "48",
        "mesh_beacon_offer_preset": "NARROW_SLOW",
        "mesh_beacon_offer_region": REGION + "EU_N_868",
    }
    result = generated(form)

    assert result["USERPREFS_MESH_BEACON_OFFER_CHANNEL_NAME"] == "MyChannel"
    assert result["USERPREFS_MESH_BEACON_OFFER_CHANNEL_PSK"] == "{ " + ", ".join(["0x38"] * 32) + " }"
    assert result["USERPREFS_MESH_BEACON_OFFER_FREQUENCY_SLOT"] == "48"
    assert result["USERPREFS_MESH_BEACON_OFFER_PRESET"] == PRESET + "NARROW_SLOW"
    assert result["USERPREFS_MESH_BEACON_OFFER_REGION"] == REGION + "EU_N_868"


@pytest.mark.parametrize("psk", ["AQ==", "ESIzRFVmd4iZqrvM3e7/AA=="])
def test_offer_psk_in_base64_gives_a_byte_array(psk):
    assert generated({"mesh_beacon_offer_channel_psk": psk})["USERPREFS_MESH_BEACON_OFFER_CHANNEL_PSK"].startswith("{ 0x")


@pytest.mark.parametrize("psk", ["abcd", "zz" * 16, "38" * 33])
def test_offer_psk_that_is_not_0_1_16_or_32_bytes_is_a_field_error(psk):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(mesh_beacon_offer_channel_psk=psk)
    assert error_messages(exc.value, "mesh_beacon_offer_channel_psk")


@pytest.mark.parametrize("field", ["mesh_beacon_offer_preset", "mesh_beacon_target_2_preset"])
def test_beacon_preset_that_protobufs_lack_is_a_field_error(field):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(**{field: "VERY_LONG_SLOW"})
    assert error_messages(exc.value, field)


@pytest.mark.parametrize("field", ["mesh_beacon_offer_region", "mesh_beacon_target_3_region"])
def test_beacon_region_that_protobufs_lack_is_a_field_error(field):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(**{field: "UNSET"})
    assert error_messages(exc.value, field)


# Targets 0–3


@pytest.mark.parametrize("target", range(4))
def test_each_target_gives_its_four_keys(target):
    form = {
        f"mesh_beacon_target_{target}_channel_index": "7",
        f"mesh_beacon_target_{target}_frequency_slot": "1",
        f"mesh_beacon_target_{target}_preset": PRESET + "MEDIUM_FAST",
        f"mesh_beacon_target_{target}_region": "EU_868",
    }
    result = generated(form)

    prefix = f"USERPREFS_MESH_BEACON_TARGET_{target}_"
    assert result[prefix + "CHANNEL_INDEX"] == "7"
    assert result[prefix + "FREQUENCY_SLOT"] == "1"
    assert result[prefix + "PRESET"] == PRESET + "MEDIUM_FAST"
    assert result[prefix + "REGION"] == REGION + "EU_868"


@pytest.mark.parametrize("index", ["8", "-1", "1.5", "x"])
def test_target_channel_index_outside_0_to_7_is_a_field_error(index):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(mesh_beacon_target_1_channel_index=index)
    assert error_messages(exc.value, "mesh_beacon_target_1_channel_index")


@pytest.mark.parametrize("field", ["mesh_beacon_offer_frequency_slot", "mesh_beacon_target_0_frequency_slot"])
@pytest.mark.parametrize("slot", ["0", "-1", "2.5", "x"])
def test_frequency_slot_that_is_not_1_or_more_is_a_field_error(field, slot):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(**{field: slot})
    assert error_messages(exc.value, field)


# Build page


def test_mesh_beacon_include_marks_each_control_with_its_userprefs_key():
    html = TEMPLATE.read_text()
    for key in UPSTREAM_KEYS:
        assert f'data-userprefs-key="{key}"' in html, key
    assert "LEGACY_SPLIT" not in html


@pytest.mark.asyncio
async def test_build_page_has_a_control_for_each_mesh_beacon_field(client):
    html = (await client.get("/")).text
    for field in full_beacon_form():
        assert f'name="{field}"' in html, field


@pytest.mark.asyncio
async def test_beacon_preset_and_region_controls_list_every_enum_value(client):
    parser = _SelectParser()
    parser.feed((await client.get("/")).text)

    assert parser.options["mesh_beacon_offer_preset"][0] == ""
    assert len([v for v in parser.options["mesh_beacon_offer_preset"] if v]) == 17
    assert len([v for v in parser.options["mesh_beacon_target_0_region"] if v]) == 37


@pytest.mark.asyncio
async def test_preview_with_target_channel_index_8_returns_422_for_that_field(client):
    resp = await client.post("/api/v1/preview", json={"mesh_beacon_target_0_channel_index": "8"})

    assert resp.status_code == 422
    assert [err["loc"] for err in resp.json()["detail"]] == [["body", "mesh_beacon_target_0_channel_index"]]
