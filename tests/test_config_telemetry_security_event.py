"""Tests for the telemetry, security, event and notification keys (FR-config-coverage-8, -9, -11, -12, -14)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from mtfwbuilder.models import ConfigRequest
from mtfwbuilder.services.jsonc_generator import KEYS_NOT_IN_V2_7_26, generate_jsonc

MANAGED_MODE_MESSAGE = "Managed mode needs an admin key, or the device cannot be changed again."
ADMIN_KEY = "ab" * 32
POLICY_PREFIX = "meshtastic_Config_SecurityConfig_PacketSignaturePolicy_"


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def generated(data: dict[str, str]) -> dict[str, str]:
    """Return the keys that a firmware version that reads every key gets for validated form data."""
    request = ConfigRequest(**data)
    return json.loads(generate_jsonc(request.generator_input(), unread_keys=frozenset()))


def error_messages(exc: ValidationError, field: str) -> list[str]:
    """Return the messages of the validation errors at `field`."""
    return [err["msg"] for err in exc.errors() if err["loc"] == (field,)]


# Telemetry (FR-config-coverage-8)


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("telemetry_device_interval", "USERPREFS_CONFIG_DEVICE_TELEM_UPDATE_INTERVAL"),
        ("telemetry_environment_interval", "USERPREFS_CONFIG_ENV_TELEM_UPDATE_INTERVAL"),
        ("telemetry_air_quality_interval", "USERPREFS_CONFIG_AQ_TELEM_UPDATE_INTERVAL"),
    ],
)
@pytest.mark.parametrize("seconds", ["0", "60", "1800", "2592000"])
def test_telemetry_interval_gives_its_key(field, key, seconds):
    assert generated({field: seconds})[key] == seconds


@pytest.mark.parametrize(
    "field", ["telemetry_device_interval", "telemetry_environment_interval", "telemetry_air_quality_interval"]
)
@pytest.mark.parametrize("seconds", ["1", "59", "2592001", "-60", "60.5", "abc"])
def test_telemetry_interval_outside_0_or_60_to_2592000_is_a_field_error(field, seconds):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(**{field: seconds})
    assert error_messages(exc.value, field)


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("telemetry_environment_enabled", "USERPREFS_CONFIG_ENVIRONMENT_MEASUREMENT_ENABLED"),
        ("telemetry_environment_screen_enabled", "USERPREFS_CONFIG_ENV_SCREEN_SCREEN_ENABLED"),
        ("telemetry_air_quality_enabled", "USERPREFS_CONFIG_AQ_MEASUREMENT_ENABLED"),
        ("telemetry_air_quality_screen_enabled", "USERPREFS_CONFIG_AQ_SCREEN_ENABLED"),
        ("security_is_managed", "USERPREFS_CONFIG_SECURITY_IS_MANAGED"),
        ("event_block_position", "USERPREFS_BLOCK_POSITION_ON_EVENT_CHANNEL"),
    ],
)
@pytest.mark.parametrize("value", ["true", "false"])
def test_toggle_gives_its_key_as_true_or_false(field, key, value):
    assert generated({field: value, "admin_key_0": ADMIN_KEY})[key] == value


@pytest.mark.parametrize("field", ["telemetry_environment_enabled", "security_is_managed", "event_block_position"])
def test_toggle_that_is_not_true_or_false_is_a_field_error(field):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(**{field: "yes", "admin_key_0": ADMIN_KEY})
    assert error_messages(exc.value, field)


def test_empty_telemetry_security_event_and_notification_fields_write_no_key():
    fields = [
        "telemetry_device_interval",
        "telemetry_environment_enabled",
        "telemetry_environment_interval",
        "security_is_managed",
        "security_packet_signature_policy",
        "event_mode_hop_limit",
        "event_block_position",
        "canned_messages",
        "ringtone_rtttl",
        "ringtone_nag_secs",
    ]
    assert generated(dict.fromkeys(fields, "")) == {"USERPREFS_CHANNELS_TO_WRITE": "1"}


# Security (FR-config-coverage-9, -14)


@pytest.mark.parametrize(
    "policy", ["PACKET_SIGNATURE_POLICY_BALANCED", POLICY_PREFIX + "PACKET_SIGNATURE_POLICY_STRICT"]
)
def test_packet_signature_policy_gives_the_prefixed_value(policy):
    key = generated({"security_packet_signature_policy": policy})["USERPREFS_CONFIG_SECURITY_PACKET_SIGNATURE_POLICY"]
    assert key == POLICY_PREFIX + policy.removeprefix(POLICY_PREFIX)


def test_packet_signature_policy_that_protobufs_lack_is_a_field_error():
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(security_packet_signature_policy="PACKET_SIGNATURE_POLICY_NONE")
    assert error_messages(exc.value, "security_packet_signature_policy")


@pytest.mark.parametrize("slot", [0, 1, 2])
def test_managed_mode_with_an_admin_key_gives_is_managed(slot):
    result = generated({"security_is_managed": "true", f"admin_key_{slot}": ADMIN_KEY})
    assert result["USERPREFS_CONFIG_SECURITY_IS_MANAGED"] == "true"
    assert f"USERPREFS_USE_ADMIN_KEY_{slot}" in result


def test_managed_mode_off_without_an_admin_key_is_valid():
    assert generated({"security_is_managed": "false"})["USERPREFS_CONFIG_SECURITY_IS_MANAGED"] == "false"


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_keys", [{}, {"admin_key_0": "", "admin_key_1": "", "admin_key_2": ""}])
async def test_managed_mode_without_an_admin_key_returns_422_with_the_managed_mode_message(client, admin_keys):
    resp = await client.post("/api/v1/preview", json={"security_is_managed": "true", **admin_keys})

    assert resp.status_code == 422
    messages = [err["msg"] for err in resp.json()["detail"] if err["loc"][1:] == ["security_is_managed"]]
    assert any(MANAGED_MODE_MESSAGE in msg for msg in messages)


@pytest.mark.asyncio
async def test_managed_mode_without_an_admin_key_shows_the_message_under_managed_mode(client):
    resp = await client.post("/api/v1/preview", data={"security_is_managed": "true"}, headers={"HX-Request": "true"})

    assert resp.status_code == 422
    assert 'id="error-security_is_managed"' in resp.text
    assert MANAGED_MODE_MESSAGE in resp.text


@pytest.mark.asyncio
async def test_build_page_has_the_managed_mode_notice_and_a_paste_field_for_each_admin_key(client):
    html = (await client.get("/")).text

    assert 'id="managedModeNotice"' in html
    for slot in range(3):
        assert f'name="admin_key_{slot}"' in html
        assert f'data-target="adminKey{slot}"' not in html


# Event (FR-config-coverage-11)


@pytest.mark.parametrize("hops", ["0", "3", "7"])
def test_event_mode_hop_limit_gives_its_key(hops):
    assert generated({"event_mode": "1", "event_mode_hop_limit": hops})["USERPREFS_EVENT_MODE_HOP_LIMIT"] == hops


@pytest.mark.parametrize("hops", ["8", "-1", "3.5", "x"])
def test_event_mode_hop_limit_outside_0_to_7_is_a_field_error(hops):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(event_mode_hop_limit=hops)
    assert error_messages(exc.value, "event_mode_hop_limit")


# Canned messages and ringtone (FR-config-coverage-12)


def test_canned_messages_one_per_line_give_the_pipe_separated_key():
    result = generated({"canned_messages": "OK\r\nOn my way\n\n  Need help  \n"})
    assert result["USERPREFS_CANNED_MESSAGES"] == "OK|On my way|Need help"


def test_canned_message_that_contains_a_pipe_is_a_field_error():
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(canned_messages="OK\nYes|No")
    assert error_messages(exc.value, "canned_messages")


def test_ringtone_rtttl_gives_its_key():
    rtttl = "24:d=32,o=5,b=565:f6,p,f6,4p"
    assert generated({"ringtone_rtttl": rtttl})["USERPREFS_RINGTONE_RTTTL"] == rtttl


def test_ringtone_nag_secs_gives_its_key():
    assert generated({"ringtone_nag_secs": "15"})["USERPREFS_RINGTONE_NAG_SECS"] == "15"


@pytest.mark.parametrize("seconds", ["-1", "1.5", "١٢"])
def test_ringtone_nag_secs_that_is_not_a_whole_number_is_a_field_error(seconds):
    with pytest.raises(ValidationError) as exc:
        ConfigRequest(ringtone_nag_secs=seconds)
    assert error_messages(exc.value, "ringtone_nag_secs")


# Firmware v2.7.26 output


def test_v2_7_26_output_leaves_out_each_key_that_the_version_does_not_read():
    data = {
        "telemetry_device_interval": "900",
        "telemetry_environment_enabled": "true",
        "security_is_managed": "true",
        "admin_key_0": ADMIN_KEY,
        "event_mode_hop_limit": "3",
        "canned_messages": "OK",
        "ringtone_nag_secs": "60",
    }
    result = json.loads(generate_jsonc(ConfigRequest(**data).generator_input()))

    assert not set(result) & KEYS_NOT_IN_V2_7_26
    assert result["USERPREFS_CONFIG_DEVICE_TELEM_UPDATE_INTERVAL"] == "900"
    assert result["USERPREFS_RINGTONE_NAG_SECS"] == "60"
