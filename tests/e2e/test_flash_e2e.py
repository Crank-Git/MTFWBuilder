"""E2E tests of the flash panel in Chromium (feature browser-flashing, criteria 1, 2, 3 and 7).

No test selects the Flash control, so no test opens a serial port or writes to a device.
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
