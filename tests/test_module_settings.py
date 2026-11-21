"""Tests for the module settings fields of `ConfigRequest` (feature module-settings, FR 5, 6, 11 and 12)."""

import base64
import re
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2
from httpx import ASGITransport, AsyncClient

from mtfwbuilder import models
from mtfwbuilder.models import ConfigRequest, FleetDevice
from mtfwbuilder.services.fleet_runs import _device_fields

CORE_FILE = Path(__file__).resolve().parent.parent / "static" / "vendor" / "meshtastic" / "meshtastic-core-2.6.7.mjs"
# The core file embeds each proto file as a base64 `FileDescriptorProto`, and this prefix encodes the file name.
MODULE_CONFIG_DESCRIPTOR = re.compile(r'"(ChNtb2R1bGVfY29uZmlnLnByb3Rv[A-Za-z0-9+/]*)"')

NEIGHBOR_INFO_MESSAGE = "The firmware uses at least 14400 seconds (4 hours)."
MAX_UINT32 = 4294967295
# Upstream `module_config.options` at protobufs v2.7.8 gives these fields `int_size:8`.
MAX_UINT8 = 255
MIN_INT32 = -2147483648
MAX_INT32 = 2147483647

# The form fields of the Behaviour rules table of feature module-settings, by type.
BOOL_FIELDS = (
    "serial_enabled",
    "serial_echo",
    "serial_override_console_serial_port",
    "store_forward_enabled",
    "store_forward_heartbeat",
    "store_forward_is_server",
    "range_test_enabled",
    "range_test_save",
    "ext_notify_enabled",
    "ext_notify_active",
    "ext_notify_alert_message",
    "ext_notify_alert_message_vibra",
    "ext_notify_alert_message_buzzer",
    "ext_notify_alert_bell",
    "ext_notify_alert_bell_vibra",
    "ext_notify_alert_bell_buzzer",
    "ext_notify_use_pwm",
    "ext_notify_use_i2s_as_buzzer",
    "neighbor_info_enabled",
    "neighbor_info_transmit_over_lora",
    "detection_sensor_enabled",
    "detection_sensor_send_bell",
    "detection_sensor_use_pullup",
    "paxcounter_enabled",
)
NUMBER_LIMITS = {
    "serial_rxd": (0, MAX_UINT32),
    "serial_txd": (0, MAX_UINT32),
    "serial_timeout": (0, MAX_UINT32),
    "store_forward_records": (0, MAX_UINT32),
    "store_forward_history_return_max": (0, MAX_UINT32),
    "store_forward_history_return_window": (0, MAX_UINT32),
    "range_test_sender": (0, MAX_UINT32),
    "ext_notify_output_ms": (0, MAX_UINT32),
    "ext_notify_output": (0, MAX_UINT32),
    "ext_notify_output_vibra": (0, MAX_UINT8),
    "ext_notify_output_buzzer": (0, MAX_UINT8),
    "neighbor_info_update_interval": (14400, MAX_UINT32),
    "detection_sensor_minimum_broadcast_secs": (0, MAX_UINT32),
    "detection_sensor_state_broadcast_secs": (0, MAX_UINT32),
    "detection_sensor_monitor_pin": (0, MAX_UINT8),
    "paxcounter_update_interval": (0, MAX_UINT32),
    "paxcounter_wifi_threshold": (MIN_INT32, MAX_INT32),
    "paxcounter_ble_threshold": (MIN_INT32, MAX_INT32),
}
ENUM_VALUES = {
    "serial_baud": ("BAUD_DEFAULT", "BAUD_921600"),
    "serial_mode": ("DEFAULT", "VE_DIRECT"),
    "detection_sensor_trigger_type": ("LOGIC_LOW", "EITHER_EDGE_ACTIVE_HIGH"),
}
MODULE_FIELDS = (*BOOL_FIELDS, *NUMBER_LIMITS, *ENUM_VALUES, "detection_sensor_name")

# The proto enum of each enum form field, as `ModuleConfig.<message>.<enum>`.
PROTO_ENUMS = {
    "serial_baud": ("SerialConfig", "Serial_Baud"),
    "serial_mode": ("SerialConfig", "Serial_Mode"),
    "detection_sensor_trigger_type": ("DetectionSensorConfig", "TriggerType"),
}


def module_config() -> dict[str, str]:
    """Return form data that sets every module field to a valid value."""
    data = dict.fromkeys(BOOL_FIELDS, "true")
    data |= {field: str(high) for field, (_, high) in NUMBER_LIMITS.items()}
    data |= {field: values[-1] for field, values in ENUM_VALUES.items()}
    data["detection_sensor_name"] = "Door"
    return data


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def errors_at(resp, field: str) -> list[str]:
    """Return the messages of the field errors at `field` in a 422 response."""
    return [err["msg"] for err in resp.json()["detail"] if err["loc"][1:] == [field]]


async def assert_rejected(client, field: str, value: str) -> list[str]:
    resp = await client.post("/api/v1/generate", json={field: value})
    assert resp.status_code == 422, f"{field}={value!r} gave {resp.status_code}"
    messages = errors_at(resp, field)
    assert messages, resp.text
    return messages


async def assert_accepted(client, field: str, value: str) -> None:
    resp = await client.post("/api/v1/generate", json={field: value})
    assert resp.status_code == 200, f"{field}={value!r} gave {resp.status_code}: {resp.text}"


# FR-module-settings-5


def test_config_request_has_each_module_field():
    assert set(MODULE_FIELDS) <= set(ConfigRequest.model_fields)


@pytest.mark.parametrize("field", MODULE_FIELDS)
def test_an_empty_module_field_is_valid_and_stays_unset(field):
    assert getattr(ConfigRequest(), field) is None


@pytest.mark.parametrize("field", BOOL_FIELDS)
async def test_a_module_toggle_accepts_true_and_false(client, field):
    await assert_accepted(client, field, "true")
    await assert_accepted(client, field, "false")


@pytest.mark.parametrize("field", BOOL_FIELDS)
@pytest.mark.parametrize("value", ["yes", "True", "1"])
async def test_a_module_toggle_rejects_a_value_that_is_not_true_or_false(client, field, value):
    await assert_rejected(client, field, value)


@pytest.mark.parametrize(("field", "limits"), NUMBER_LIMITS.items())
async def test_a_module_number_accepts_each_limit_of_its_range(client, field, limits):
    for value in limits:
        await assert_accepted(client, field, str(value))


@pytest.mark.parametrize(("field", "limits"), NUMBER_LIMITS.items())
async def test_a_module_number_rejects_a_value_just_outside_each_limit(client, field, limits):
    low, high = limits
    await assert_rejected(client, field, str(low - 1))
    await assert_rejected(client, field, str(high + 1))


@pytest.mark.parametrize("field", NUMBER_LIMITS)
@pytest.mark.parametrize("value", ["x", "1.5", "١٤", "--5", " 5"])
async def test_a_module_number_rejects_a_value_that_is_not_a_whole_number(client, field, value):
    await assert_rejected(client, field, value)


async def test_a_paxcounter_threshold_accepts_a_negative_value(client):
    await assert_accepted(client, "paxcounter_wifi_threshold", "-80")


@pytest.mark.parametrize(("field", "values"), ENUM_VALUES.items())
async def test_a_module_enum_accepts_its_first_and_last_value(client, field, values):
    for value in values:
        await assert_accepted(client, field, value)


@pytest.mark.parametrize("field", ENUM_VALUES)
@pytest.mark.parametrize("value", ["NOT_A_VALUE", "1"])
async def test_a_module_enum_rejects_a_name_that_its_enum_does_not_hold(client, field, value):
    await assert_rejected(client, field, value)


async def test_a_module_enum_rejects_a_lowercase_name(client):
    await assert_rejected(client, "serial_mode", "nmea")


@pytest.mark.parametrize("value", ["MS_CONFIG", "LOG", "LOGTEXT", "MODBUS"])
async def test_serial_mode_rejects_a_mode_that_core_2_6_7_does_not_hold(client, value):
    await assert_rejected(client, "serial_mode", value)


@pytest.mark.parametrize("value", ["a" * 19, "é" * 9 + "a"])
async def test_detection_sensor_name_accepts_19_bytes(client, value):
    await assert_accepted(client, "detection_sensor_name", value)


@pytest.mark.parametrize("value", ["a" * 20, "é" * 10])
async def test_detection_sensor_name_rejects_20_bytes(client, value):
    await assert_rejected(client, "detection_sensor_name", value)


async def test_module_field_accepts_a_double_slash_because_no_module_field_goes_into_a_build(client):
    await assert_accepted(client, "detection_sensor_name", "Gate//2")


def test_module_enums_hold_the_enum_values_of_the_vendored_core():
    encoded = MODULE_CONFIG_DESCRIPTOR.search(CORE_FILE.read_text())[1]
    descriptor = descriptor_pb2.FileDescriptorProto.FromString(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
    module_config = next(m for m in descriptor.message_type if m.name == "ModuleConfig")
    for field, (message, enum) in PROTO_ENUMS.items():
        nested = next(m for m in module_config.nested_type if m.name == message)
        values = next(e for e in nested.enum_type if e.name == enum).value
        assert models.MODULE_ENUMS[field] == tuple(v.name for v in values), field


# FR-module-settings-11


async def test_neighbor_info_interval_of_14399_gives_the_firmware_minimum_error(client):
    assert await assert_rejected(client, "neighbor_info_update_interval", "14399") == [
        f"Value error, {NEIGHBOR_INFO_MESSAGE}"
    ]


async def test_neighbor_info_interval_of_0_gives_the_firmware_minimum_error(client):
    assert await assert_rejected(client, "neighbor_info_update_interval", "0") == [
        f"Value error, {NEIGHBOR_INFO_MESSAGE}"
    ]


async def test_neighbor_info_interval_of_14400_is_valid(client):
    await assert_accepted(client, "neighbor_info_update_interval", "14400")


# FR-module-settings-6


async def test_a_build_with_every_module_field_writes_the_same_file_as_a_build_without_them(client):
    base = {"owner_short_name": "TST"}
    without = await client.post("/api/v1/generate", json=base)
    with_modules = await client.post("/api/v1/generate", json=base | module_config())
    assert with_modules.status_code == 200, with_modules.text
    assert with_modules.json()["content"] == without.json()["content"]


# FR-module-settings-12


def test_a_fleet_device_has_no_module_field():
    assert not set(FleetDevice.model_fields) & set(MODULE_FIELDS)


def test_each_fleet_device_gets_the_module_fields_of_the_profile():
    device = {name: None for name in FleetDevice.model_fields} | {"long_name": "Gate One"}
    fields = _device_fields(module_config(), device)
    assert {field: fields[field] for field in MODULE_FIELDS} == module_config()
