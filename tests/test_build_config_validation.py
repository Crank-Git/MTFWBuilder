"""Tests for the config validation of the build route (FR-userprefs-correctness-16, issue #94)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from mtfwbuilder.models import ConfigRequest

PSK_15_BYTES_HEX = "ab" * 15
ARABIC_INDIC_12 = "١٢"


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def submitted(app, monkeypatch):
    """Return the list of config contents that reach the build queue."""
    calls = []
    submit = app.state.build_queue.submit

    def record(variant, config_content):
        calls.append(config_content)
        return submit(variant, config_content)

    monkeypatch.setattr(app.state.build_queue, "submit", record)
    return calls


def error_fields(resp) -> list[str]:
    """Return the dotted body location of each field error in a 422 response."""
    return [".".join(str(part) for part in err["loc"][1:]) for err in resp.json()["detail"]]


async def _post_config(client, config, field="config_json"):
    return await client.post(
        "/api/v1/build-firmware",
        data={"variant": "heltec-v3", "config_source": "current", field: json.dumps(config)},
    )


async def _post_upload(client, content: bytes):
    return await client.post(
        "/api/v1/build-firmware",
        data={"variant": "heltec-v3", "config_source": "upload"},
        files={"userPrefs": ("userPrefs.jsonc", content, "application/octet-stream")},
    )


async def test_15_byte_psk_returns_422_for_channel_0_psk_and_queues_no_build(client, submitted):
    resp = await _post_config(client, {"channels_to_write": "1", "channel_0[psk]": PSK_15_BYTES_HEX})

    assert resp.status_code == 422
    assert error_fields(resp) == ["channel_0.psk"]
    assert submitted == []


@pytest.mark.parametrize("field", ["button_pin", "event_mode"])
async def test_non_ascii_digits_in_button_pin_or_event_mode_return_422(client, submitted, field):
    resp = await _post_config(client, {field: ARABIC_INDIC_12, "button_pin_enabled": "true"})

    assert resp.status_code == 422
    assert error_fields(resp) == [field]
    assert submitted == []


async def test_value_with_double_slash_returns_422_for_that_field(client, submitted):
    resp = await _post_config(client, {"mqtt_address": "mqtt://broker.example"})

    assert resp.status_code == 422
    assert error_fields(resp) == ["mqtt_address"]
    assert submitted == []


@pytest.mark.parametrize("config_json", ["[]", "not json"])
async def test_config_json_that_is_not_a_json_object_returns_422(client, submitted, config_json):
    resp = await client.post(
        "/api/v1/build-firmware",
        data={"variant": "heltec-v3", "config_source": "current", "config_json": config_json},
    )

    assert resp.status_code == 422
    assert submitted == []


async def test_valid_config_returns_202_and_queues_the_generated_userprefs_file(client, submitted):
    config = {"owner_long_name": "TestNode", "button_pin": "36", "button_pin_enabled": "true", "event_mode": "1"}

    resp = await _post_config(client, config)

    assert resp.status_code == 202
    assert json.loads(submitted[0])["USERPREFS_BUTTON_PIN"] == "36"


async def test_stored_config_field_is_not_a_config_source(client, submitted):
    config = {"owner_long_name": "TestNode"}

    resp = await _post_config(client, config, "stored_config")

    assert resp.status_code == 400
    assert resp.json() == {"detail": "No configuration data provided"}
    assert submitted == []


async def test_upload_that_is_not_utf8_returns_422_for_userprefs(client, submitted):
    resp = await _post_upload(client, b'{"USERPREFS_CONFIG_OWNER_LONG_NAME": "\xff"}')

    assert resp.status_code == 422
    assert error_fields(resp) == ["userPrefs"]
    assert submitted == []


async def test_upload_with_double_slash_in_a_value_returns_422_for_userprefs(client, submitted):
    content = b'{\n  "USERPREFS_MQTT_PRIMARY_ADDRESS": "mqtt://broker.example"\n}\n'

    resp = await _post_upload(client, content)

    assert resp.status_code == 422
    assert error_fields(resp) == ["userPrefs"]
    assert "line 2" in resp.json()["detail"][0]["msg"]
    assert submitted == []


async def test_upload_with_comment_lines_and_trailing_comments_returns_202(client, submitted):
    content = (
        b"{\n"
        b'  // "USERPREFS_CONFIG_LORA_REGION": "meshtastic_Config_LoRaConfig_RegionCode_US",\n'
        b'  "USERPREFS_CONFIG_OWNER_LONG_NAME": "TestNode", // the node name\n'
        b'  "USERPREFS_CHANNEL_0_NAME": "a \\" b"\n'
        b"}\n"
    )

    resp = await _post_upload(client, content)

    assert resp.status_code == 202
    assert submitted == [content.decode()]


@pytest.mark.parametrize("field", ["button_pin", "event_mode"])
@pytest.mark.parametrize("value", [ARABIC_INDIC_12, "²", "１"])
def test_config_request_rejects_non_ascii_digits(field, value):
    with pytest.raises(ValidationError):
        ConfigRequest.model_validate({field: value})
