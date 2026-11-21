"""E2E tests of the module settings on the push config page (feature module-settings, FR 8, 9 and 10).

Each test feeds the profile through the profile API and pushes to the fake device of `test_push_config_e2e`.
"""

import json
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_push_config_diff_e2e import DEVICE, create_profile, diff_sections
from tests.e2e.test_push_config_e2e import open_push_config
from tests.e2e.test_push_config_push_e2e import (
    ENCODE_JS,
    READ_JS,
    admin_calls,
    connect,
    not_pushed,
    push,
    sent,
    wait_for_commit,
)

pytestmark = pytest.mark.e2e

PIN_WARNING = "This changes a pin. Check that the profile fits this board."
NAG_TOO_LARGE = "The firmware field holds at most 65535."

# A device with each module section of feature module-settings, as core 2.6.7 sends it.
# The enum numbers come from `meshtastic/module_config.proto` at protobufs v2.7.8, the version in core 2.6.7.
MODULE_DEVICE = {
    **DEVICE,
    "moduleConfig": {
        **DEVICE["moduleConfig"],
        "serial": {"enabled": False, "echo": False, "rxd": 16, "txd": 17, "baud": 0, "timeout": 0, "mode": 0},
        "storeForward": {"enabled": False, "heartbeat": False, "records": 0, "isServer": False},
        "rangeTest": {"enabled": False, "sender": 0, "save": False},
        "externalNotification": {"enabled": False, "outputMs": 1000, "output": 13, "nagTimeout": 15},
        "neighborInfo": {"enabled": False, "updateInterval": 21600, "transmitOverLora": False},
        "detectionSensor": {"enabled": False, "name": "", "monitorPin": 5, "detectionTriggerType": 0},
        "paxcounter": {"enabled": False, "paxcounterUpdateInterval": 0, "wifiThreshold": 0, "bleThreshold": 0},
    },
}

# The profile changes fields in each module section, and `range_test_save` matches the device.
MODULE_PROFILE = {
    "serial_enabled": "true",
    "serial_baud": "BAUD_9600",
    "serial_mode": "NMEA",
    "store_forward_enabled": "true",
    "store_forward_records": "50",
    "range_test_enabled": "true",
    "range_test_sender": "300",
    "range_test_save": "false",
    "ext_notify_enabled": "true",
    "neighbor_info_enabled": "true",
    "neighbor_info_update_interval": "14400",
    "detection_sensor_enabled": "true",
    "detection_sensor_name": "Door",
    "detection_sensor_trigger_type": "RISING_EDGE",
    "paxcounter_enabled": "true",
    "paxcounter_wifi_threshold": "-80",
}

# The message name in `meshtastic/module_config.proto` of each module section case.
TYPE_NAMES = {
    "serial": "SerialConfig",
    "storeForward": "StoreForwardConfig",
    "externalNotification": "ExternalNotificationConfig",
    "neighborInfo": "NeighborInfoConfig",
    "detectionSensor": "DetectionSensorConfig",
    "paxcounter": "PaxcounterConfig",
}

WARNINGS_JS ="""() => [...document.querySelectorAll('#push-diff-sections tr[data-warning]')]
  .map(row => [row.closest('[data-section]').dataset.section, row.dataset.warning, row.textContent])"""


def module_values(page: Page) -> dict:
    """Return the value of each `setModuleConfig` call that the page sent, by its section case, with no `$typeName`.

    Fails when a value is not a message of the `ModuleConfig` schema of its case.
    """
    values = {}
    for call in sent(page, "setModuleConfig"):
        case, value = call["payloadVariant"]["case"], dict(call["payloadVariant"]["value"])
        assert value.pop("$typeName") == f"meshtastic.ModuleConfig.{TYPE_NAMES[case]}"
        values[case] = value
    return values


def test_module_profile_shows_a_row_for_each_changed_field_and_sends_each_selected_section(
    page: Page, live_server: str
):
    create_profile(page, live_server, "Module check", MODULE_PROFILE)
    connect(page, live_server, "Module check", read=MODULE_DEVICE)

    assert [(s["section"], s["label"], s["rows"]) for s in diff_sections(page)] == [
        (
            "moduleConfig.serial",
            "Serial",
            [["enabled", "false", "true"], ["baud", "BAUD_DEFAULT", "BAUD_9600"], ["mode", "DEFAULT", "NMEA"]],
        ),
        ("moduleConfig.storeForward", "Store and forward", [["enabled", "false", "true"], ["records", "0", "50"]]),
        ("moduleConfig.rangeTest", "Range test", [["enabled", "false", "true"], ["sender", "0", "300"]]),
        ("moduleConfig.externalNotification", "External notification", [["enabled", "false", "true"]]),
        (
            "moduleConfig.neighborInfo",
            "Neighbor info",
            [["enabled", "false", "true"], ["update_interval", "21600", "14400"]],
        ),
        (
            "moduleConfig.detectionSensor",
            "Detection sensor",
            [
                ["enabled", "false", "true"],
                ["name", "", "Door"],
                ["detection_trigger_type", "LOGIC_LOW", "RISING_EDGE"],
            ],
        ),
        ("moduleConfig.paxcounter", "Paxcounter", [["enabled", "false", "true"], ["wifi_threshold", "0", "-80"]]),
    ]
    # The operator clears the Range test section, so the page sends no set message for it.
    page.uncheck("#push-section-moduleConfig\\.rangeTest")

    push(page)

    wait_for_commit(page)
    assert admin_calls(page) == ["beginEditSettings", *["setModuleConfig"] * 6, "commitEditSettings"]
    device = MODULE_DEVICE["moduleConfig"]
    assert module_values(page) == {
        "serial": {**device["serial"], "enabled": True, "baud": 7, "mode": 4},
        "storeForward": {**device["storeForward"], "enabled": True, "records": 50},
        "externalNotification": {**device["externalNotification"], "enabled": True},
        "neighborInfo": {**device["neighborInfo"], "enabled": True, "updateInterval": 14400},
        "detectionSensor": {**device["detectionSensor"], "enabled": True, "name": "Door", "detectionTriggerType": 3},
        "paxcounter": {**device["paxcounter"], "enabled": True, "wifiThreshold": -80},
    }


def test_profile_with_serial_rxd_shows_the_pin_warning_on_its_diff_row_only(page: Page, live_server: str):
    create_profile(page, live_server, "Pin check", {"serial_enabled": "true", "serial_rxd": "21"})

    connect(page, live_server, "Pin check", read=MODULE_DEVICE)

    assert {s["section"]: s["rows"] for s in diff_sections(page)} == {
        "moduleConfig.serial": [["enabled", "false", "true"], ["rxd", "16", "21"]],
    }
    assert page.evaluate(WARNINGS_JS) == [["moduleConfig.serial", "serial_rxd", PIN_WARNING]]
    expect(page.locator("#push-diff-sections tr[data-warning=serial_rxd]")).to_be_visible()


def test_empty_module_field_gives_no_diff_row_and_the_push_keeps_the_device_value(page: Page, live_server: str):
    create_profile(page, live_server, "Empty pin check", {"serial_enabled": "true", "detection_sensor_name": "Gate"})
    connect(page, live_server, "Empty pin check", read=MODULE_DEVICE)

    assert {s["section"]: s["rows"] for s in diff_sections(page)} == {
        "moduleConfig.serial": [["enabled", "false", "true"]],
        "moduleConfig.detectionSensor": [["name", "", "Gate"]],
    }
    assert page.evaluate(WARNINGS_JS) == []

    push(page)

    wait_for_commit(page)
    device = MODULE_DEVICE["moduleConfig"]
    assert module_values(page) == {
        "serial": {**device["serial"], "enabled": True},
        "detectionSensor": {**device["detectionSensor"], "name": "Gate"},
    }
    kept = page.evaluate(READ_JS)["moduleConfig"]
    assert (kept["serial"]["rxd"], kept["serial"]["txd"], kept["detectionSensor"]["monitorPin"]) == (16, 17, 5)


def test_ringtone_nag_secs_reaches_the_device_as_nag_timeout(page: Page, live_server: str):
    create_profile(page, live_server, "Nag check", {"ringtone_nag_secs": "45"})
    connect(page, live_server, "Nag check", read=MODULE_DEVICE)

    assert [(s["section"], s["label"], s["rows"]) for s in diff_sections(page)] == [
        ("moduleConfig.externalNotification", "External notification", [["nag_timeout", "15", "45"]]),
    ]
    assert not_pushed(page) == {}

    push(page)

    wait_for_commit(page)
    assert module_values(page) == {
        "externalNotification": {**MODULE_DEVICE["moduleConfig"]["externalNotification"], "nagTimeout": 45},
    }


def test_profile_with_a_button_pin_shows_the_pin_warning(page: Page, live_server: str):
    create_profile(page, live_server, "Button pin check", {"button_pin_enabled": "true", "button_pin": "12"})

    connect(page, live_server, "Button pin check", read=MODULE_DEVICE)

    assert {s["section"]: s["rows"] for s in diff_sections(page)} == {"config.device": [["button_gpio", "0", "12"]]}
    assert page.evaluate(WARNINGS_JS) == [["config.device", "button_pin", PIN_WARNING]]


def test_saved_nag_secs_above_65535_shows_under_not_pushed_and_gives_no_diff_row(page: Page, live_server: str):
    profile = create_profile(page, live_server, "Old nag check", {"ringtone_nag_secs": "45"})

    # The profile API now rejects the value, so the route gives the page a profile that was saved before the check.
    def old_profile(route) -> None:
        response = route.fetch()
        body = response.json()
        body["config"]["ringtone_nag_secs"] = "70000"
        route.fulfill(response=response, body=json.dumps(body))

    page.route(lambda url: urlsplit(url).path == f"/api/v1/profiles/{profile['id']}", old_profile)
    connect(page, live_server, "Old nag check", read=MODULE_DEVICE)

    assert diff_sections(page) == []
    assert not_pushed(page) == {"ringtone_nag_secs": f"ringtone_nag_secs: {NAG_TOO_LARGE}"}


# Each expected byte string is the protobuf encoding of the section, with the tag of its `ModuleConfig` field first.
# The field numbers come from `meshtastic/module_config.proto` at protobufs v2.7.8, the version in core 2.6.7.
@pytest.mark.parametrize(
    ("section", "config", "expected"),
    [
        # AdminMessage set_module_config = 35, then serial = 2: enabled = 1, rxd = 3, baud = 5 (BAUD_9600 = 7) and
        # mode = 7 (NMEA = 4).
        (
            "moduleConfig.serial",
            {"serial_enabled": "true", "serial_rxd": "16", "serial_baud": "BAUD_9600", "serial_mode": "NMEA"},
            bytes([0x9A, 0x02, 0x0A, 0x12, 0x08, 0x08, 0x01, 0x18, 0x10, 0x28, 0x07, 0x38, 0x04]),
        ),
        # external_notification = 3: nag_timeout = 14.
        ("moduleConfig.externalNotification", {"ringtone_nag_secs": "45"}, bytes([0x1A, 0x02, 0x70, 0x2D])),
        # paxcounter = 13: wifi_threshold = 3, an int32 that the varint encodes in ten bytes when it is negative.
        (
            "moduleConfig.paxcounter",
            {"paxcounter_wifi_threshold": "-80"},
            bytes([0x6A, 0x0B, 0x18, 0xB0, *[0xFF] * 8, 0x01]),
        ),
    ],
)
def test_core_encodes_a_valid_set_module_config_for_a_module_section(
    page: Page, live_server: str, section, config, expected
):
    open_push_config(page, live_server)

    result = page.evaluate(ENCODE_JS, [{"moduleConfig": {}}, config, section])

    assert result["failure"] is None
    assert expected in bytes(result["written"])


# Protobufs v2.7.8 has no field 4 in `RangeTestConfig`, so core 2.6.7 keeps a field 4 of the read in `$unknown`.
# A newer firmware sends `clear_on_reboot` as field 4. Core keeps the value bytes after the tag, so true is 0x01.
CLEAR_ON_REBOOT = [{"no": 4, "wireType": 0, "data": {"$bytes": [1]}}]
# `LoRaConfig` of protobufs v2.7.8 has no field 106, so the read also keeps that field in `$unknown`.
LORA_UNKNOWN = [{"no": 106, "wireType": 0, "data": {"$bytes": [1]}}]
UNKNOWN_DEVICE = {
    **MODULE_DEVICE,
    "config": {**MODULE_DEVICE["config"], "lora": {**DEVICE["config"]["lora"], "$unknown": LORA_UNKNOWN}},
    "moduleConfig": {
        **MODULE_DEVICE["moduleConfig"],
        "rangeTest": {**MODULE_DEVICE["moduleConfig"]["rangeTest"], "$unknown": CLEAR_ON_REBOOT},
    },
}


def test_push_keeps_the_device_fields_that_core_does_not_know(page: Page, live_server: str):
    profile = {"range_test_enabled": "true", "lora_enabled": "true", "lora_modem_preset": "LONG_FAST"}
    create_profile(page, live_server, "Unknown field check", profile)
    connect(page, live_server, "Unknown field check", read=UNKNOWN_DEVICE)

    push(page)

    wait_for_commit(page)
    [range_test] = sent(page, "setModuleConfig")
    assert range_test["payloadVariant"]["value"] == {
        **UNKNOWN_DEVICE["moduleConfig"]["rangeTest"],
        "$typeName": "meshtastic.ModuleConfig.RangeTestConfig",
        "enabled": True,
    }
    [lora] = sent(page, "setConfig")
    assert lora["payloadVariant"]["value"]["$typeName"] == "meshtastic.Config.LoRaConfig"
    assert lora["payloadVariant"]["value"]["$unknown"] == LORA_UNKNOWN


# Core writes the tag of each unknown field, then its value bytes. Both unknown fields are varints.
@pytest.mark.parametrize(
    ("section", "config", "expected"),
    [
        # AdminMessage set_module_config = 35, then range_test = 5: enabled = 1, then the unknown field 4.
        (
            "moduleConfig.rangeTest",
            {"range_test_enabled": "true"},
            bytes([0x9A, 0x02, 0x06, 0x2A, 0x04, 0x08, 0x01, 0x20, 0x01]),
        ),
        # The tag of field 106 is the varint of 106 << 3, 0xD0 0x06.
        ("config.lora", {"lora_enabled": "true", "lora_modem_preset": "LONG_FAST"}, bytes([0xD0, 0x06, 0x01])),
    ],
)
def test_core_encodes_the_unknown_device_fields_in_the_set_message(
    page: Page, live_server: str, section, config, expected
):
    open_push_config(page, live_server)

    result = page.evaluate(ENCODE_JS, [UNKNOWN_DEVICE, config, section])

    assert result["failure"] is None
    assert expected in bytes(result["written"])
