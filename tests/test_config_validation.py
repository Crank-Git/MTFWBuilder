"""Tests for the ConfigRequest validation on the config routes (FR-userprefs-correctness-11, -12, -14, -16)."""

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient

CONFIG_ROUTES = ["/api/v1/generate", "/api/v1/preview", "/api/v1/download"]

PSK_15_BYTES_HEX = "ab" * 15


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def error_fields(resp) -> list[str]:
    """Return the dotted body location of each field error in a 422 response."""
    return [".".join(str(part) for part in err["loc"][1:]) for err in resp.json()["detail"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", CONFIG_ROUTES)
async def test_15_byte_psk_returns_422_for_channel_0_psk(client, route):
    resp = await client.post(route, json={"channels_to_write": "1", "channel_0[psk]": PSK_15_BYTES_HEX})
    assert resp.status_code == 422
    assert "channel_0.psk" in error_fields(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", CONFIG_ROUTES)
async def test_channels_to_write_9_returns_422(client, route):
    resp = await client.post(route, json={"channels_to_write": "9"})
    assert resp.status_code == 422
    assert "channels_to_write" in error_fields(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", CONFIG_ROUTES)
async def test_mqtt_address_with_double_slash_returns_422_for_mqtt_address(client, route):
    resp = await client.post(route, json={"mqtt_address": "mqtt://x"})
    assert resp.status_code == 422
    assert "mqtt_address" in error_fields(resp)


@pytest.mark.asyncio
async def test_download_form_field_config_returns_422_for_invalid_body(client):
    resp = await client.post("/api/v1/download", data={"config": json.dumps({"channels_to_write": "9"})})
    assert resp.status_code == 422
    assert "channels_to_write" in error_fields(resp)


@pytest.mark.asyncio
async def test_download_form_field_config_that_is_not_json_returns_422(client):
    resp = await client.post("/api/v1/download", data={"config": "{not json"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_channel_name_with_double_slash_returns_422_for_channel_name(client):
    resp = await client.post("/api/v1/preview", json={"channel_0[name]": "a//b"})
    assert resp.status_code == 422
    assert "channel_0.name" in error_fields(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["-1", "x", "1.5"])
async def test_channels_to_write_outside_0_to_8_returns_422(client, value):
    resp = await client.post("/api/v1/preview", json={"channels_to_write": value})
    assert resp.status_code == 422
    assert "channels_to_write" in error_fields(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["٣", "١٢"])
async def test_channels_to_write_with_non_ascii_digits_returns_422(client, value):
    # The upstream parser quotes a value that is not all ASCII digits.
    resp = await client.post("/api/v1/preview", json={"channels_to_write": value})
    assert resp.status_code == 422
    assert "channels_to_write" in error_fields(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "3", "8"])
async def test_channels_to_write_at_the_range_limits_returns_200(client, value):
    resp = await client.post("/api/v1/preview", json={"channels_to_write": value})
    assert resp.status_code == 200
    assert json.loads(resp.json()["content"])["USERPREFS_CHANNELS_TO_WRITE"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "psk",
    [
        "",
        "01",
        "ab" * 16,
        "ab" * 32,
        "{ " + ", ".join(["0x01"] * 32) + " }",
        base64.b64encode(bytes(16)).decode(),
        "AQ==",
    ],
)
async def test_psk_of_0_1_16_or_32_bytes_returns_200(client, psk):
    resp = await client.post("/api/v1/preview", json={"channel_0[psk]": psk})
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "psk",
    [
        "ab" * 2,
        "ab" * 31,
        "{ " + ", ".join(["0x01"] * 15) + " }",
        base64.b64encode(bytes(15)).decode(),
        "not a key",
    ],
)
async def test_psk_of_another_length_returns_422_for_channel_0_psk(client, psk):
    resp = await client.post("/api/v1/preview", json={"channel_0[psk]": psk})
    assert resp.status_code == 422
    assert "channel_0.psk" in error_fields(resp)


@pytest.mark.asyncio
async def test_psk_in_a_higher_channel_slot_returns_422_for_that_slot(client):
    resp = await client.post("/api/v1/preview", json={"channels_to_write": "3", "channel_2[psk]": PSK_15_BYTES_HEX})
    assert resp.status_code == 422
    assert "channel_2.psk" in error_fields(resp)


@pytest.mark.asyncio
async def test_valid_channel_fields_reach_the_userprefs_file(client):
    resp = await client.post(
        "/api/v1/preview",
        json={
            "channels_to_write": "1",
            "channel_0[name]": "Primary",
            "channel_0[psk]": "01",
            "channel_0[uplink_enabled]": "true",
        },
    )
    assert resp.status_code == 200
    parsed = json.loads(resp.json()["content"])
    assert parsed["USERPREFS_CHANNEL_0_NAME"] == "Primary"
    assert parsed["USERPREFS_CHANNEL_0_PSK"] == "{ 0x01 }"
    assert parsed["USERPREFS_CHANNEL_0_UPLINK_ENABLED"] == "true"


@pytest.mark.asyncio
async def test_iana_time_zone_reaches_the_userprefs_file_as_a_posix_tz_string(client):
    resp = await client.post("/api/v1/preview", json={"tz_string": "America/New_York"})
    assert resp.status_code == 200
    assert json.loads(resp.json()["content"])["USERPREFS_TZ_STRING"] == "EST5EDT,M3.2.0,M11.1.0"


DOUBLE_SLASH_ADMIN_KEY = base64.b64encode(bytes([0xFF] * 32)).decode()
DOUBLE_SLASH_PSK = base64.b64encode(bytes([0xFF] * 16)).decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", CONFIG_ROUTES)
@pytest.mark.parametrize("key", [bytes(31).hex(), base64.b64encode(bytes(16)).decode(), "{}", "not a key"])
async def test_admin_key_that_is_not_32_bytes_returns_422_for_that_field(client, route, key):
    resp = await client.post(route, json={"admin_key_1": key})
    assert resp.status_code == 422
    assert "admin_key_1" in error_fields(resp)


@pytest.mark.asyncio
async def test_base64_admin_key_with_double_slash_reaches_the_file_as_a_byte_array(client):
    assert "//" in DOUBLE_SLASH_ADMIN_KEY
    resp = await client.post("/api/v1/preview", json={"admin_key_0": DOUBLE_SLASH_ADMIN_KEY})
    assert resp.status_code == 200
    parsed = json.loads(resp.json()["content"])
    assert parsed["USERPREFS_USE_ADMIN_KEY_0"] == "{ " + ", ".join(["0xff"] * 32) + " }"


@pytest.mark.asyncio
async def test_base64_psk_with_double_slash_reaches_the_file_as_a_byte_array(client):
    assert "//" in DOUBLE_SLASH_PSK
    resp = await client.post("/api/v1/preview", json={"channels_to_write": "1", "channel_0[psk]": DOUBLE_SLASH_PSK})
    assert resp.status_code == 200
    parsed = json.loads(resp.json()["content"])
    assert parsed["USERPREFS_CHANNEL_0_PSK"] == "{ " + ", ".join(["0xff"] * 16) + " }"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", CONFIG_ROUTES)
async def test_unknown_time_zone_returns_422_for_tz_string(client, route):
    resp = await client.post(route, json={"tz_string": "Mars/Olympus_Mons"})
    assert resp.status_code == 422
    assert "tz_string" in error_fields(resp)


@pytest.mark.asyncio
async def test_empty_time_zone_returns_200_and_writes_no_tz_key(client):
    resp = await client.post("/api/v1/preview", json={"tz_string": ""})
    assert resp.status_code == 200
    assert "USERPREFS_TZ_STRING" not in json.loads(resp.json()["content"])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", CONFIG_ROUTES)
@pytest.mark.parametrize(("field", "value"), [("button_pin", "GPIO36"), ("button_pin", "-1"), ("event_mode", "on")])
async def test_button_pin_or_event_mode_that_is_not_digits_returns_422_for_that_field(client, route, field, value):
    resp = await client.post(route, json={field: value, "button_pin_enabled": "true"})
    assert resp.status_code == 422
    assert field in error_fields(resp)


@pytest.mark.asyncio
async def test_digit_button_pin_and_event_mode_reach_the_userprefs_file(client):
    resp = await client.post(
        "/api/v1/preview", json={"button_pin": "36", "button_pin_enabled": "true", "event_mode": "1"}
    )
    assert resp.status_code == 200
    parsed = json.loads(resp.json()["content"])
    assert parsed["USERPREFS_BUTTON_PIN"] == "36"
    assert parsed["USERPREFS_EVENT_MODE"] == "1"
