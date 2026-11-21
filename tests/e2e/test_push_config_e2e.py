"""E2E tests of the push config page in Chromium (feature push-config, FR 2, 3 and 11).

A test that connects sets `window.__pushConfigTestDevice` to a fake device, or replaces the browser picker,
so no test opens a serial port or a Bluetooth device.
"""

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

MANAGED = "This device is managed. Only an admin key holder can change it remotely."
UNSUPPORTED = "This browser cannot connect. Use Chrome or Edge on a desktop."
VENDORED = [
    "/static/vendor/meshtastic/meshtastic-core-2.6.7.mjs",
    "/static/vendor/meshtastic/meshtastic-transport-web-serial-0.2.5.mjs",
    "/static/vendor/meshtastic/meshtastic-transport-web-bluetooth-0.1.5.mjs",
]

# The fake device has the MeshDevice members that the page uses, and it records each call in `calls`.
# `configure()` answers with the security config, then with the end of the config, as a device does.
FAKE_DEVICE = """
(managed => {
  const dispatcher = () => {
    const handlers = [];
    return {
      handlers,
      subscribe(fn) { handlers.push(fn); return () => handlers.splice(handlers.indexOf(fn), 1); },
      dispatch(value) { handlers.slice().forEach(fn => fn(value)); },
    };
  };
  const device = {
    calls: [],
    events: { onConfigPacket: dispatcher(), onDeviceStatus: dispatcher() },
    configure() {
      device.calls.push('configure');
      setTimeout(() => {
        device.events.onConfigPacket.dispatch({ payloadVariant: { case: 'security', value: { isManaged: managed } } });
        device.events.onDeviceStatus.dispatch(7);
      });
      return Promise.resolve(0);
    },
    disconnect() { device.calls.push('disconnect'); return Promise.resolve(); },
  };
  const record = name => (...args) => { device.calls.push(name); return Promise.resolve(0); };
  ['beginEditSettings', 'commitEditSettings', 'setConfig', 'setModuleConfig', 'setChannel', 'setOwner',
   'sendPacket', 'sendRaw'].forEach(name => { device[name] = record(name); });
  window.__pushConfigTestDevice = device;
})(%s)
"""
SET_CALLS = {
    "beginEditSettings",
    "commitEditSettings",
    "setConfig",
    "setModuleConfig",
    "setChannel",
    "setOwner",
    "sendPacket",
    "sendRaw",
}


def open_push_config(page: Page, url: str) -> None:
    page.goto(f"{url}/push-config")
    # The connect controls become enabled only after the module and its imports load.
    expect(page.locator("#push-connect-usb")).to_be_enabled()


def device_calls(page: Page) -> list[str]:
    return page.evaluate("() => window.__pushConfigTestDevice.calls")


def test_page_load_loads_the_three_vendored_modules_with_no_console_error(page: Page, live_server: str):
    errors = []
    requests = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))

    open_push_config(page, live_server)

    assert errors == []
    paths = [urlsplit(url).path for url in requests]
    assert all(paths.count(path) == 1 for path in VENDORED), paths
    origin = urlsplit(live_server).netloc
    assert [url for url in requests if urlsplit(url).netloc != origin] == []


def test_managed_device_shows_the_managed_message_and_gets_no_set_message(page: Page, live_server: str):
    page.add_init_script(FAKE_DEVICE % "true")
    open_push_config(page, live_server)

    page.click("#push-connect-usb")

    expect(page.locator("#push-managed")).to_have_text(MANAGED)
    expect(page.locator("#push-managed")).to_be_visible()
    calls = device_calls(page)
    assert "configure" in calls
    assert SET_CALLS.isdisjoint(calls), calls
    expect(page.locator("#push-connected")).to_be_hidden()


def test_device_without_managed_mode_shows_connected_and_no_managed_message(page: Page, live_server: str):
    page.add_init_script(FAKE_DEVICE % "false")
    open_push_config(page, live_server)

    page.click("#push-connect-usb")

    expect(page.locator("#push-connected")).to_be_visible()
    expect(page.locator("#push-managed")).to_be_hidden()
    assert SET_CALLS.isdisjoint(device_calls(page))


def test_connect_via_usb_opens_the_web_serial_port_picker(page: Page, live_server: str):
    # The stub stands in for the browser picker, so the vendored transport runs up to the picker and stops.
    page.add_init_script("""
      window.__pickerCalls = [];
      navigator.serial.requestPort = (...args) => {
        window.__pickerCalls.push('serial');
        return Promise.reject(new DOMException('No port selected by the user.', 'NotFoundError'));
      };
    """)
    open_push_config(page, live_server)

    page.click("#push-connect-usb")

    page.wait_for_function("() => window.__pickerCalls.length === 1")
    assert page.evaluate("() => window.__pickerCalls") == ["serial"]
    # A closed picker is no error, so the page stays in the not-connected state.
    expect(page.locator("#push-error")).to_be_hidden()
    expect(page.locator("#push-connect-usb")).to_be_enabled()


def test_connect_via_bluetooth_opens_the_web_bluetooth_device_picker_for_the_meshtastic_service(
    page: Page, live_server: str
):
    page.add_init_script("""
      window.__pickerOptions = [];
      const bluetooth = { requestDevice: options => {
        window.__pickerOptions.push(options);
        return Promise.reject(new DOMException('User cancelled the requestDevice() chooser.', 'NotFoundError'));
      } };
      Object.defineProperty(Navigator.prototype, 'bluetooth', { configurable: true, get: () => bluetooth });
    """)
    open_push_config(page, live_server)

    page.click("#push-connect-bluetooth")

    page.wait_for_function("() => window.__pickerOptions.length === 1")
    options = page.evaluate("() => window.__pickerOptions[0]")
    assert options == {"filters": [{"services": ["6ba1b218-15a8-461f-9fa8-5dcae273eafd"]}]}
    expect(page.locator("#push-error")).to_be_hidden()


def test_browser_without_web_bluetooth_hides_connect_via_bluetooth(page: Page, live_server: str):
    page.add_init_script("delete Navigator.prototype.bluetooth")
    open_push_config(page, live_server)

    expect(page.locator("#push-connect-usb")).to_be_visible()
    expect(page.locator("#push-connect-bluetooth")).to_be_hidden()


def test_browser_without_web_serial_or_web_bluetooth_shows_the_unsupported_message(page: Page, live_server: str):
    page.add_init_script("delete Navigator.prototype.serial; delete Navigator.prototype.bluetooth")
    page.goto(f"{live_server}/push-config")

    expect(page.locator("#push-unsupported")).to_have_text(UNSUPPORTED)
    expect(page.locator("#push-unsupported")).to_be_visible()
    expect(page.locator("#push-connect-usb")).to_be_hidden()
    expect(page.locator("#push-connect-bluetooth")).to_be_hidden()


def test_push_config_over_plain_http_on_a_lan_address_shows_the_https_message(browser_type, live_server: str):
    port = urlsplit(live_server).port
    browser = browser_type.launch(args=["--host-resolver-rules=MAP push.lan 127.0.0.1"])
    try:
        page = browser.new_page()
        page.goto(f"http://push.lan:{port}/push-config")

        assert page.evaluate("() => window.isSecureContext") is False
        expect(page.locator("#push-insecure")).to_be_visible()
        expect(page.locator("#push-connect-usb")).to_be_hidden()
        expect(page.locator("#push-connect-bluetooth")).to_be_hidden()
    finally:
        browser.close()
