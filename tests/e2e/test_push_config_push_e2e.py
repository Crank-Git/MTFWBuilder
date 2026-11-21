"""E2E tests of Push on the push config page (feature push-config, FR 7, 8, 9 and 12, and the edge cases).

Each test pushes to the fake device of `test_push_config_e2e`. The fake keeps each set message until the commit,
so a test can check the config that the device keeps.
"""

import base64
import json
import math
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_push_config_diff_e2e import (
    CHANNEL_0_KEY,
    DEVICE,
    OTHER_KEY,
    PROFILE,
    create_profile,
    record_requests,
)
from tests.e2e.test_push_config_e2e import FAKE_DEVICE, MANAGED, device_calls, open_push_config

pytestmark = pytest.mark.e2e

DROPPED = "The connection dropped before the commit. The device keeps its old config."
RESTART = "The device saved the config and restarts now. The page connects again when the device returns."
NO_CHANGES = "The device already matches this profile."
UNKNOWN = "The result is unknown. Read the device again."
MANAGED_NOT_PUSHED = "Set managed mode with a build. Push config does not set it."
NO_ALTITUDE = "setFixedPosition of core 2.6.7 takes no altitude."
BUILD_ONLY = "The firmware reads this value only from a build."
NOT_MAPPED = "Push config does not send this value."
BLUETOOTH = "#push-connect-bluetooth"
# The protobufs 2.7.8 numbers: `Config.LoRaConfig.ModemPreset.LONG_FAST`, `Channel.Role.PRIMARY` and `SECONDARY`,
# and `Config.BluetoothConfig.PairingMode.FIXED_PIN`.
# The profile has no stored secret, so Push needs no secret that the operator enters again.
PUSH_PROFILE = {key: value for key, value in PROFILE.items() if key != "channel_0[psk]"}
LONG_FAST = 0
PRIMARY = 1
SECONDARY = 2
FIXED_PIN = 1

# Playwright returns a Uint8Array as an object, so the script writes each one as `{"$bytes": [..]}`.
SENT_JS = """() => JSON.parse(JSON.stringify(window.__pushConfigTestDevice.sent,
  (key, value) => (value instanceof Uint8Array ? { $bytes: [...value] } : value)))"""
READ_JS = """() => JSON.parse(JSON.stringify(window.__pushConfigTestDevice.read,
  (key, value) => (value instanceof Uint8Array ? { $bytes: [...value] } : value)))"""
# Headless Chromium on Linux has no `navigator.bluetooth`, so the page hides Connect via Bluetooth (FR 3).
# The fake device replaces the transport, so the stub needs no working method.
WEB_BLUETOOTH = "Object.defineProperty(Navigator.prototype, 'bluetooth', { configurable: true, get: () => ({}) })"


def connect(page: Page, url: str, profile_name: str, options: dict | None = None, read: dict = DEVICE) -> None:
    """Open the page with the fake device and `options`, choose the profile, and connect.

    The option `control` names the connect control. The default is Connect via USB.
    """
    options = dict(options or {})
    control = options.pop("control", "#push-connect-usb")
    if control == BLUETOOTH:
        page.add_init_script(WEB_BLUETOOTH)
    page.add_init_script(FAKE_DEVICE % json.dumps({"managed": False, "read": read, **options}))
    open_push_config(page, url)
    page.select_option("#push-profile", label=profile_name)
    page.click(control)
    expect(page.locator("#push-connected")).to_be_visible()


def push(page: Page) -> None:
    expect(page.locator("#push-send")).to_be_enabled()
    page.click("#push-send")


def admin_calls(page: Page) -> list[str]:
    return [name for name in device_calls(page) if name not in ("configure", "disconnect")]


def sent(page: Page, name: str) -> list:
    return [call["value"] for call in page.evaluate(SENT_JS) if call["name"] == name]


def not_pushed(page: Page) -> dict:
    return page.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('#push-not-pushed [data-not-pushed]')]"
        ".map(item => [item.dataset.notPushed, item.textContent]))"
    )


def results(page: Page) -> dict:
    return page.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('#push-results [data-result]')]"
        ".map(item => [item.dataset.result, item.textContent]))"
    )


def test_push_sends_begin_then_one_set_message_per_selected_section_then_commit(page: Page, live_server: str):
    create_profile(page, live_server, "Order check", PUSH_PROFILE)
    connect(page, live_server, "Order check")
    # The operator clears the Device section, so the page sends no set message for it.
    page.uncheck("#push-section-config\\.device")

    push(page)

    page.wait_for_function("() => window.__pushConfigTestDevice.calls.includes('commitEditSettings')")
    assert admin_calls(page) == [
        "beginEditSettings",
        "setConfig",
        "setChannel",
        "setChannel",
        "commitEditSettings",
    ]
    [lora] = sent(page, "setConfig")
    assert lora["payloadVariant"]["case"] == "lora"
    # The set message holds the whole section: the device values, with the profile values on top.
    assert lora["payloadVariant"]["value"] == {**DEVICE["config"]["lora"], "modemPreset": LONG_FAST}
    channel_0, channel_1 = sent(page, "setChannel")
    assert channel_0 == {
        "index": 0,
        "role": PRIMARY,
        "settings": {"name": "RidgeNet", "psk": {"$bytes": list(CHANNEL_0_KEY)}},
    }
    # Slot 1 was disabled, so it gets the firmware defaults of a new channel and the SECONDARY role.
    assert channel_1 == {
        "index": 1,
        "role": SECONDARY,
        "settings": {"name": "Ops", "psk": {"$bytes": [1]}, "moduleSettings": {"positionPrecision": 13}},
    }


def test_rejected_section_shows_the_device_error_and_the_other_sections_apply(page: Page, live_server: str):
    create_profile(page, live_server, "Reject check", PUSH_PROFILE)
    connect(page, live_server, "Reject check", {"rejects": ["config.lora"]})

    push(page)

    expect(page.locator("#push-restart")).to_have_text(RESTART)
    assert results(page) == {
        "config.device": "Device: applied",
        "config.lora": "LoRa: failed, BAD_REQUEST",
        "channels.0": "Channel 0: applied",
        "channels.1": "Channel 1: applied",
    }
    assert "commitEditSettings" in device_calls(page)
    read = page.evaluate(READ_JS)
    assert read["config"]["lora"] == DEVICE["config"]["lora"]
    assert [channel["settings"]["name"] for channel in read["channels"]] == ["RidgeNet", "Ops"]


def test_drop_before_commit_sends_no_commit_and_leaves_the_device_channels_unchanged(page: Page, live_server: str):
    create_profile(page, live_server, "Drop check", PUSH_PROFILE)
    connect(page, live_server, "Drop check", {"dropOn": "setChannel"})

    push(page)

    expect(page.locator("#push-dropped")).to_have_text(DROPPED)
    expect(page.locator("#push-dropped")).to_be_visible()
    assert "commitEditSettings" not in device_calls(page)
    expect(page.locator("#push-restart")).to_be_hidden()
    expect(page.locator("#push-results")).to_be_hidden()
    assert page.evaluate(READ_JS)["channels"] == [
        {"index": 0, "role": 1, "settings": {"name": "LongFast", "psk": {"$bytes": list(CHANNEL_0_KEY)}}},
        {"index": 1, "role": 0, "settings": {"name": "", "psk": {"$bytes": []}}},
    ]


def test_fixed_pin_push_also_sets_the_fixed_pin_pairing_mode(page: Page, live_server: str):
    read = {**DEVICE, "config": {**DEVICE["config"], "bluetooth": {"enabled": True, "mode": 0, "fixedPin": 0}}}
    create_profile(page, live_server, "Pin check", {"bluetooth_fixed_pin": "123456"})
    connect(page, live_server, "Pin check", read=read)

    push(page)

    page.wait_for_function("() => window.__pushConfigTestDevice.calls.includes('commitEditSettings')")
    [bluetooth] = sent(page, "setConfig")
    assert bluetooth["payloadVariant"] == {
        "case": "bluetooth",
        "value": {"enabled": True, "mode": FIXED_PIN, "fixedPin": 123456},
    }


def test_commit_shows_the_restart_notice_and_the_page_connects_again(page: Page, live_server: str):
    create_profile(page, live_server, "Restart check", PUSH_PROFILE)
    connect(page, live_server, "Restart check")

    push(page)

    expect(page.locator("#push-restart")).to_have_text(RESTART)
    # The fake restarts after the commit, and the page reads the committed config on the next connection.
    expect(page.locator("#push-no-changes")).to_be_visible()
    expect(page.locator("#push-no-changes")).to_have_text(NO_CHANGES)
    assert device_calls(page).count("configure") == 2
    calls = device_calls(page)
    assert calls.index("disconnect") > calls.index("commitEditSettings")
    expect(page.locator("#push-restart")).to_be_visible()
    assert results(page)["channels.1"] == "Channel 1: applied"


def test_push_is_disabled_until_the_operator_enters_each_secret_and_selects_a_section(page: Page, live_server: str):
    config = {"channels_to_write": "1", "channel_0[name]": "RidgeNet", "channel_0[psk]": OTHER_KEY.hex()}
    create_profile(page, live_server, "Secret gate", config)
    connect(page, live_server, "Secret gate")
    send = page.locator("#push-send")
    expect(send).to_be_disabled()

    page.locator("#push-reenter input[data-form='channel_0[psk]']").fill(OTHER_KEY.hex())
    expect(send).to_be_enabled()
    page.uncheck("#push-section-channels\\.0")
    expect(send).to_be_disabled()
    page.check("#push-section-channels\\.0")

    expect(send).to_be_enabled()


def test_no_page_request_to_the_server_holds_device_data_or_a_secret_during_a_push(page: Page, live_server: str):
    requests = record_requests(page)
    entered = base64.b64encode(OTHER_KEY).decode()
    config = {**PUSH_PROFILE, "channel_0[psk]": entered}
    create_profile(page, live_server, "Push request check", config)
    connect(page, live_server, "Push request check")
    page.locator("#push-reenter input[data-form='channel_0[psk]']").fill(entered)

    push(page)

    expect(page.locator("#push-no-changes")).to_be_visible()
    # The secret goes only to the device.
    assert sent(page, "setChannel")[0]["settings"]["psk"] == {"$bytes": list(OTHER_KEY)}
    origin = urlsplit(live_server).netloc
    to_server = [r for r in requests if urlsplit(r.url).netloc == origin]
    assert [r.method for r in to_server if r.method != "GET"] == []
    assert [r.url for r in to_server if r.post_data] == []
    device_values = ["LongFast", "MEDIUM_FAST", "10800", "fieldnet", "device-wifi", "device-mqtt", entered]
    assert [(r.url, v) for r in to_server for v in device_values if v in r.url] == []
    api = sorted({urlsplit(r.url).path for r in to_server if urlsplit(r.url).path.startswith("/api/")})
    assert len(api) == 2 and api[0] == "/api/v1/profiles" and api[1].startswith("/api/v1/profiles/"), api


# The script builds the admin call of a section with the page, and sends it through a real core 2.6.7 MeshDevice
# on a transport that keeps each written chunk. Core encodes the message, so a value that core cannot encode throws.
ENCODE_JS = """async ([read, config, section]) => {
  const page = await import('/static/js/push-config.js');
  const core = await import('@meshtastic/core');
  const chunks = [];
  const transport = {
    toDevice: new WritableStream({ write(chunk) { chunks.push(chunk); } }),
    fromDevice: new ReadableStream({ start() {} }),
    disconnect: async () => {},
  };
  const device = page.createMeshDevice(core, transport);
  const revive = value => JSON.parse(JSON.stringify(value),
    (key, item) => (item && item.$bytes ? new Uint8Array(item.$bytes) : item));
  const context = { protobuf: core.Protobuf, tzTable: page.tzTable };
  const message = page.sectionMessage(revive(read), config, section, context);
  // The transport never answers, so the promise of the library stays open and the script ignores it.
  let failure = null;
  device[message.method](...message.args).catch(error => { failure = String(error && (error.stack || error.error)); });
  for (let wait = 0; wait < 50 && chunks.length === 0 && failure === null; wait += 1) {
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  return { failure, written: chunks.map(chunk => [...chunk]).flat() };
}"""


CONFIGURE_JS = """async () => {
  const page = await import('/static/js/push-config.js');
  const core = await import('@meshtastic/core');
  const chunks = [];
  const transport = {
    toDevice: new WritableStream({ write(chunk) { chunks.push(chunk); } }),
    fromDevice: new ReadableStream({ start() {} }),
    disconnect: async () => {},
  };
  const device = page.createMeshDevice(core, transport);
  let failure = null;
  try {
    device.configure().catch(error => { failure = String(error); });
  } catch (error) {
    failure = String(error);
  }
  for (let wait = 0; wait < 50 && chunks.length === 0 && failure === null; wait += 1) {
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  return { failure, chunks: chunks.length };
}"""


def test_mesh_device_of_the_page_sends_want_config_with_no_logger_error(page: Page, live_server: str):
    open_push_config(page, live_server)

    assert page.evaluate(CONFIGURE_JS) == {"failure": None, "chunks": 1}


@pytest.mark.parametrize(
    ("section", "config", "expected"),
    [
        ("channels.1", {"channels_to_write": "2", "channel_1[name]": "Ops"}, b"Ops"),
        ("config.lora", {"lora_enabled": "true", "lora_modem_preset": "LONG_FAST"}, b""),
        ("moduleConfig.mqtt", {"network_enabled": "true", "mqtt_enabled": "true", "mqtt_address": "mq.lan"}, b"mq.lan"),
        ("owner", {"owner_long_name": "Ridge Base"}, b"Ridge Base"),
        ("position", {"gps_enabled": "true", "fixed_position": "true", "fixed_lat": "1", "fixed_lon": "2"}, b""),
    ],
)
def test_core_encodes_the_admin_call_of_each_section_kind(page: Page, live_server: str, section, config, expected):
    read = {**DEVICE, "channels": {str(c["index"]): c for c in DEVICE["channels"]}}
    open_push_config(page, live_server)

    result = page.evaluate(ENCODE_JS, [read, config, section])

    assert result["failure"] is None
    written = bytes(result["written"])
    assert written
    assert expected in written


def wait_for_commit(page: Page) -> None:
    page.wait_for_function("() => window.__pushConfigTestDevice.calls.includes('commitEditSettings')")


def test_push_never_sends_managed_mode_and_lists_it_under_not_pushed(page: Page, live_server: str):
    key = bytes([7] * 32)
    # The server accepts managed mode only with an admin key, and the device already has that key.
    security = {"isManaged": False, "adminKey": [{"$bytes": list(key)}]}
    read = {**DEVICE, "config": {**DEVICE["config"], "security": security}}
    profile = {"security_is_managed": "true", "admin_key_0": key.hex(), "owner_short_name": "NEW"}
    create_profile(page, live_server, "Managed check", profile)
    connect(page, live_server, "Managed check", read=read)
    page.locator("#push-reenter input[data-form='admin_key_0']").fill(key.hex())

    assert not_pushed(page) == {"security_is_managed": f"security_is_managed: {MANAGED_NOT_PUSHED}"}
    expect(page.locator("[data-section='config.security']")).to_have_count(0)
    push(page)

    wait_for_commit(page)
    assert admin_calls(page) == ["beginEditSettings", "setOwner", "commitEditSettings"]


def test_admin_keys_of_the_profile_replace_the_device_keys_with_no_gap(page: Page, live_server: str):
    keys = [bytes([n] * 32) for n in (1, 2, 3)]
    profile_key = bytes([9] * 32)
    security = {"isManaged": False, "adminKey": [{"$bytes": list(key)} for key in keys]}
    read = {**DEVICE, "config": {**DEVICE["config"], "security": security}}
    create_profile(page, live_server, "Key check", {"admin_key_1": profile_key.hex(), "security_is_managed": "true"})
    connect(page, live_server, "Key check", read=read)
    page.locator("#push-reenter input[data-form='admin_key_1']").fill(profile_key.hex())

    rows = page.evaluate(
        "() => [...document.querySelectorAll(\"[data-section='config.security'] tbody tr\")]"
        ".map(row => [...row.cells].map(cell => cell.textContent))"
    )
    assert rows == [
        ["admin_key[0]", "***", "***"],
        ["admin_key[1]", "***", "(none)"],
        ["admin_key[2]", "***", "(none)"],
    ]
    push(page)

    wait_for_commit(page)
    [sent_security] = sent(page, "setConfig")
    assert sent_security["payloadVariant"]["case"] == "security"
    assert sent_security["payloadVariant"]["value"] == {"isManaged": False, "adminKey": [{"$bytes": list(profile_key)}]}


def test_device_that_is_managed_after_the_restart_shows_the_managed_message(page: Page, live_server: str):
    create_profile(page, live_server, "Managed later", PUSH_PROFILE)
    connect(page, live_server, "Managed later", {"managedFrom": 2})

    push(page)

    expect(page.locator("#push-managed")).to_have_text(MANAGED)
    expect(page.locator("#push-connected")).to_be_hidden()
    assert device_calls(page).count("configure") == 2
    assert device_calls(page)[-1] == "disconnect"


def test_failed_commit_shows_the_error_and_closes_the_device(page: Page, live_server: str):
    create_profile(page, live_server, "Commit fails", PUSH_PROFILE)
    connect(page, live_server, "Commit fails", {"commitError": True})

    push(page)

    expect(page.locator("#push-error")).to_have_text("The device did not save the config: BAD_REQUEST")
    assert device_calls(page)[-2:] == ["commitEditSettings", "disconnect"]
    expect(page.locator("#push-connect-usb")).to_be_enabled()


def test_both_connect_controls_are_disabled_during_a_push_and_its_reconnect(page: Page, live_server: str):
    create_profile(page, live_server, "Busy check", PUSH_PROFILE)
    connect(page, live_server, "Busy check", {"holdCommit": True, "control": BLUETOOTH})
    usb, bluetooth = page.locator("#push-connect-usb"), page.locator("#push-connect-bluetooth")

    push(page)

    page.wait_for_function("() => typeof window.__releaseCommit === 'function'")
    expect(usb).to_be_disabled()
    expect(bluetooth).to_be_disabled()
    page.evaluate("() => window.__releaseCommit()")
    expect(page.locator("#push-no-changes")).to_be_visible()
    # The second read is the reconnect after the restart.
    assert page.evaluate("() => window.__pushConfigTestDevice.controls[1]") == [True, True]
    expect(usb).to_be_enabled()
    expect(bluetooth).to_be_enabled()


def test_fixed_position_push_sends_set_fixed_position_with_the_profile_lat_and_lon(page: Page, live_server: str):
    config = {**DEVICE["config"], "position": {"gpsMode": 1, "fixedPosition": False}}
    read = {**DEVICE, "config": config, "position": {"latitudeI": 0, "longitudeI": 0}}
    profile = {"gps_enabled": "true", "fixed_position": "true", "fixed_lat": "37.7749", "fixed_lon": "-122.4194"}
    create_profile(page, live_server, "Fixed check", {**profile, "fixed_alt": "16"})
    connect(page, live_server, "Fixed check", read=read)

    expect(page.locator("[data-section='position']")).to_be_visible()
    assert not_pushed(page) == {"fixed_alt": f"fixed_alt: {NO_ALTITUDE}"}
    push(page)

    wait_for_commit(page)
    # The device saves the fixed position at the commit, so the position goes inside the transaction.
    assert admin_calls(page) == ["beginEditSettings", "setConfig", "setFixedPosition", "commitEditSettings"]
    [args] = [call["args"] for call in page.evaluate(SENT_JS) if call["name"] == "setFixedPosition"]
    expected = [math.floor(37.7749 / 1e-7), math.floor(-122.4194 / 1e-7)]
    assert [math.floor(degrees / 1e-7) for degrees in args] == expected
    expect(page.locator("#push-no-changes")).to_be_visible()


def test_not_pushed_lists_each_profile_value_that_push_config_does_not_send(page: Page, live_server: str):
    profile = {"event_mode": "0", "oem_text": "ACME", "canned_messages": "On my way", "owner_short_name": "NEW"}
    create_profile(page, live_server, "Not pushed check", profile)

    connect(page, live_server, "Not pushed check")

    assert not_pushed(page) == {
        "event_mode": f"event_mode: {BUILD_ONLY}",
        "oem_text": f"oem_text: {BUILD_ONLY}",
        "canned_messages": f"canned_messages: {NOT_MAPPED}",
    }


def test_drop_during_the_commit_over_usb_shows_that_the_result_is_unknown(page: Page, live_server: str):
    create_profile(page, live_server, "USB commit drop", PUSH_PROFILE)
    connect(page, live_server, "USB commit drop", {"dropOn": "commitEditSettings"})

    push(page)

    expect(page.locator("#push-unknown")).to_have_text(UNKNOWN)
    expect(page.locator("#push-restart")).to_be_hidden()
    assert device_calls(page).count("configure") == 1
    assert device_calls(page)[-1] == "disconnect"
    expect(page.locator("#push-send")).to_be_disabled()


def test_drop_during_the_commit_over_bluetooth_counts_as_the_restart(page: Page, live_server: str):
    create_profile(page, live_server, "Bluetooth commit drop", PUSH_PROFILE)
    connect(page, live_server, "Bluetooth commit drop", {"dropOn": "commitEditSettings", "control": BLUETOOTH})

    push(page)

    expect(page.locator("#push-restart")).to_have_text(RESTART)
    page.wait_for_function("() => window.__pushConfigTestDevice.calls.filter(name => name === 'configure').length > 1")
    expect(page.locator("#push-unknown")).to_be_hidden()
