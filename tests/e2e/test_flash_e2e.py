"""E2E tests of the flash panel in Chromium (feature browser-flashing, criteria 1, 2, 3, 4 and 7).

A test that selects the Flash control replaces esptool-js and the port picker, so no test opens a serial port.
"""

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_smoke_e2e import fill_config_form

pytestmark = pytest.mark.e2e

UNSUPPORTED = "This browser cannot flash. Use Chrome or Edge on a desktop."
INSECURE = "Web Serial needs HTTPS. Open this page through the TLS proxy or on localhost."
# Chromium resolves this name to the server, so the page has a LAN-style origin that is not a secure context.
LAN_HOST = "flash.lan"


def build_esp32(page: Page, url: str) -> None:
    page.goto(f"{url}/")
    fill_config_form(page)
    page.select_option("#variant", "heltec-v3")
    page.click("#buildBtn")
    expect(page.locator('[data-step="complete"]')).to_have_attribute("data-state", "done", timeout=15_000)


def test_esp32_build_on_localhost_shows_an_enabled_flash_control(page: Page, live_server: str):
    requests = []
    page.on("request", lambda request: requests.append(request.url))

    build_esp32(page, live_server)

    flash = page.locator("#flash-start")
    expect(flash).to_be_visible()
    expect(flash).to_have_text("Flash")
    # The control becomes enabled only after esptool-js loads.
    expect(flash).to_be_enabled()
    expect(page.locator("#flash-unsupported")).to_be_hidden()
    expect(page.locator("#flash-insecure")).to_be_hidden()
    assert page.evaluate("() => typeof document.getElementById('flash-panel').esptool.ESPLoader") == "function"
    esptool = [url for url in requests if url.endswith("/esptool-js-0.7.1/bundle.js")]
    assert esptool == [f"{live_server}/static/vendor/esptool-js-0.7.1/bundle.js"]
    origin = urlsplit(live_server).netloc
    assert [url for url in requests if urlsplit(url).netloc != origin] == []


def test_esp32_build_without_web_serial_shows_the_unsupported_message_and_downloads(page: Page, live_server: str):
    # The script removes Web Serial before the page scripts run, as in a browser without it.
    page.add_init_script("delete Navigator.prototype.serial")

    build_esp32(page, live_server)

    expect(page.locator("#flash-unsupported")).to_have_text(UNSUPPORTED)
    expect(page.locator("#flash-unsupported")).to_be_visible()
    expect(page.locator("#flash-start")).to_be_hidden()
    links = page.locator("#flash-downloads a[download]")
    expect(links.first).to_be_visible()
    assert all("/artifacts/" in href for href in links.evaluate_all("els => els.map(a => a.getAttribute('href'))"))


def test_esp32_build_over_plain_http_on_a_lan_address_shows_the_https_message(browser_type, live_server: str):
    port = urlsplit(live_server).port
    browser = browser_type.launch(args=[f"--host-resolver-rules=MAP {LAN_HOST} 127.0.0.1"])
    try:
        page = browser.new_page()

        build_esp32(page, f"http://{LAN_HOST}:{port}")

        assert page.evaluate("() => window.isSecureContext") is False
        expect(page.locator("#flash-insecure")).to_have_text(INSECURE)
        expect(page.locator("#flash-insecure")).to_be_visible()
        expect(page.locator("#flash-downloads a[download]").first).to_be_visible()
        expect(page.locator("#flash-start")).to_be_hidden()
    finally:
        browser.close()


# The route replaces esptool-js with this module, so the full install flow runs with no device and no port.
FAKE_ESPTOOL = """
const log = (window.flashLog = []);
export class Transport {
  constructor(port) { log.push('transport:new'); }
  async disconnect() { log.push('transport:disconnect'); }
}
export class ESPLoader {
  constructor(options) { log.push('loader:new:' + options.baudrate); }
  async main() { log.push('loader:main'); return 'ESP32-S3'; }
  async eraseFlash() { log.push('loader:eraseFlash'); }
  async writeFlash(options) {
    const file = options.fileArray[0];
    log.push('loader:writeFlash:' + file.address + ':' + file.data.length);
    options.reportProgress(0, file.data.length, file.data.length);
  }
  async after(mode) { log.push('loader:after:' + mode); }
}
"""
# The script replaces the port picker, because a test cannot answer the browser's own dialog.
FAKE_PORT_PICKER = (
    "Serial.prototype.requestPort = async () => { window.portRequests = (window.portRequests || 0) + 1; return {}; };"
)
ERASE_WARNING = "This erases the device. Saved settings, messages and keys are lost."
DONE_FULL = "Done. The device restarts with your factory defaults."


def test_full_install_shows_the_erase_warning_before_any_write_then_flashes_the_factory_artifact(
    page: Page, live_server: str
):
    page.route(
        "**/esptool-js-0.7.1/bundle.js",
        lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_ESPTOOL),
    )
    page.add_init_script(FAKE_PORT_PICKER)
    build_esp32(page, live_server)
    expect(page.locator("#flash-start")).to_be_enabled()
    expect(page.locator("#flash-mode-full")).to_be_checked()

    page.click("#flash-start")

    expect(page.locator("#flash-erase-warning")).to_have_text(ERASE_WARNING)
    expect(page.locator("#flash-erase-warning")).to_be_visible()
    assert page.evaluate("() => window.portRequests") == 1
    assert page.evaluate("() => window.flashLog") == []

    page.click("#flash-confirm-erase")

    expect(page.locator("#flash-done")).to_have_text(DONE_FULL)
    expect(page.locator("#flash-bar")).to_have_text("100%")
    # The Done state hides the progress block, so a role query cannot find the bar.
    expect(page.locator("#flash-progressbar")).to_have_attribute("role", "progressbar")
    expect(page.locator("#flash-progressbar")).to_have_attribute("aria-valuenow", "100")
    log = page.evaluate("() => window.flashLog")
    factory = page.locator("#flash-downloads a[href$='.factory.bin']").get_attribute("href")
    size = len(page.request.get(f"{live_server}{factory}").body())
    assert log == [
        "transport:new",
        "loader:new:921600",
        "loader:main",
        "loader:eraseFlash",
        f"loader:writeFlash:0:{size}",
        "loader:after:hard_reset",
        "transport:disconnect",
    ]
