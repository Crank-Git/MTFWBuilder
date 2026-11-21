"""Tests for the flash panel of a complete build (FR-browser-flashing-1, -2, -11, -12 and -13)."""

import asyncio
import json
import re
import shutil
import subprocess
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import REPO_ROOT

HX = {"HX-Request": "true"}
CONFIG = json.dumps({"owner_long_name": "FlashNode"})
ESPTOOL_DIR = REPO_ROOT / "static" / "vendor" / "esptool-js-0.7.1"
ESPTOOL_URL = "/static/vendor/esptool-js-0.7.1/bundle.js"
UNSUPPORTED = "This browser cannot flash. Use Chrome or Edge on a desktop."
INSECURE = "Web Serial needs HTTPS. Open this page through the TLS proxy or on localhost."

# One variant of each pio platform in the ESP32 family. The registry puts all of them under `esp32`.
ESP32_FAMILY = ["heltec-v3", "heltec-ht62-esp32c3-sx1262", "tbeam"]
# One variant of each other architecture.
OTHER = ["rak4631", "rak11310", "rak3172"]


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def workers(app, settings, fake_pio):
    settings.firmware_dir.mkdir(parents=True)
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


class _Panel(HTMLParser):
    """Collect the start tags with their attributes, and the text inside each element that has an id."""

    def __init__(self):
        super().__init__()
        self.elements = []
        self.text = {}
        self._open = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag not in ("br", "img", "input", "link", "meta"):
            self._open.append(attrs.get("id"))

    def handle_endtag(self, tag):
        if self._open:
            self._open.pop()

    def handle_data(self, data):
        for element_id in self._open:
            if element_id:
                self.text[element_id] = self.text.get(element_id, "") + data

    def by_id(self, element_id):
        return next((attrs for _, attrs in self.elements if attrs.get("id") == element_id), None)

    def text_of(self, element_id):
        return " ".join(self.text.get(element_id, "").split())


async def _complete_panel(app, client, variant):
    """Return the parsed build update of a build of `variant`, after the build is complete."""
    resp = await client.post(
        "/api/v1/build-firmware",
        data={"variant": variant, "config_source": "current", "config_json": CONFIG},
    )
    assert resp.status_code == 202, resp.text
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    deadline = time.monotonic() + 10
    while not ctx.ended:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)
    assert ctx.status == "complete", ctx.error
    panel = _Panel()
    panel.feed((await client.get(f"/api/v1/builds/{ctx.build_id}", headers=HX)).text)
    return ctx, panel


# FR-browser-flashing-2


@pytest.mark.usefixtures("workers")
@pytest.mark.parametrize("variant", ESP32_FAMILY)
async def test_complete_esp32_family_build_shows_a_flash_control(app, client, variant):
    ctx, panel = await _complete_panel(app, client, variant)

    assert ctx.variant.architecture == "esp32"
    assert panel.by_id("flash-panel") is not None
    assert panel.by_id("flash-start") is not None
    assert panel.text_of("flash-start") == "Flash"


@pytest.mark.usefixtures("workers")
@pytest.mark.parametrize("variant", OTHER)
async def test_complete_build_of_another_architecture_shows_no_flash_panel(app, client, variant):
    _, panel = await _complete_panel(app, client, variant)

    assert panel.by_id("flash-panel") is None
    assert panel.by_id("flash-start") is None


@pytest.mark.usefixtures("workers")
async def test_running_esp32_build_shows_no_flash_panel(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "5")
    data = {"variant": "heltec-v3", "config_source": "current", "config_json": CONFIG}

    resp = await client.post("/api/v1/build-firmware", data=data, headers=HX)

    assert resp.status_code == 202, resp.text
    assert 'id="build-state"' in resp.text
    assert 'id="flash-panel"' not in resp.text


# FR-browser-flashing-11 and -12


@pytest.mark.usefixtures("workers")
async def test_flash_panel_holds_the_unsupported_and_https_messages_hidden(app, client):
    _, panel = await _complete_panel(app, client, "heltec-v3")

    assert panel.text_of("flash-unsupported") == UNSUPPORTED
    assert panel.text_of("flash-insecure") == INSECURE
    for element_id in ("flash-ready", "flash-unsupported", "flash-insecure", "flash-downloads"):
        assert "hidden" in panel.by_id(element_id), element_id


# FR-browser-flashing-13


@pytest.mark.usefixtures("workers")
async def test_flash_panel_download_links_use_the_artifact_route(app, client):
    ctx, panel = await _complete_panel(app, client, "heltec-v3")

    downloads = panel.by_id("flash-downloads")
    assert downloads is not None
    start = panel.elements.index(("div", downloads))
    links = [attrs["href"] for tag, attrs in panel.elements[start:] if tag == "a" and "data-flash-download" in attrs]
    assert sorted(links) == sorted(f"/api/v1/builds/{ctx.build_id}/artifacts/{a.name}" for a in ctx.artifacts)
    assert panel.by_id("flash-panel")["data-artifacts-url"] == f"/api/v1/builds/{ctx.build_id}/artifacts"


# FR-browser-flashing-1


@pytest.mark.usefixtures("workers")
async def test_flash_panel_names_the_vendored_esptool_js_on_its_own_host(app, client):
    _, panel = await _complete_panel(app, client, "heltec-v3")

    url = panel.by_id("flash-panel")["data-esptool-url"]
    assert url == ESPTOOL_URL
    assert urlsplit(url).netloc == ""
    assert (await client.get(url)).status_code == 200


def test_vendored_esptool_js_exports_esploader_and_transport():
    bundle = (ESPTOOL_DIR / "bundle.js").read_text()

    exports = re.search(r"export\{([^}]*)\};?\s*$", bundle)
    assert exports is not None
    names = {item.split(" as ")[-1].strip() for item in exports.group(1).split(",")}
    assert {"ESPLoader", "Transport"} <= names


def test_vendored_esptool_js_imports_no_other_module_and_names_no_cdn():
    bundle = (ESPTOOL_DIR / "bundle.js").read_text()

    assert re.search(r"\bimport\s*[\"'({]", bundle) is None
    assert re.search(r"https?://(?!github\.com/nodeca/pako)", bundle) is None


def test_vendored_esptool_js_keeps_its_apache_license_and_readme_row():
    assert "Apache License" in (ESPTOOL_DIR / "LICENSE").read_text()
    readme = (REPO_ROOT / "static" / "vendor" / "README.md").read_text()
    assert "`esptool-js-0.7.1/bundle.js`" in readme
    assert "493dccb5f33f35336e9217c38ed838611471e5a4" in readme


async def test_build_page_loads_flash_js_from_static_js(client):
    resp = await client.get("/")

    assert '<script src="http://test/static/js/flash.js"></script>' in resp.text


# flash.js runs in Node with a fake browser.

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const { pathToFileURL } = require('url');
const [script, bundle, scenario] = process.argv.slice(1);
const { secure, serial } = JSON.parse(scenario);

// As in flash_panel.html, the Flash control has no `hidden` attribute: its container flash-ready hides it.
function element(id, attrs = {}) {
  return {
    id, hidden: id !== 'flash-start', disabled: false, dataset: attrs, listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
  };
}
const ids = ['flash-ready', 'flash-start', 'flash-unsupported', 'flash-insecure', 'flash-downloads'];
// The flow elements stay hidden in these checks; flash.js binds their listeners after esptool-js loads.
const flow = ['flash-mode-update', 'flash-confirm-erase', 'flash-cancel', 'flash-retry'];
const parts = Object.fromEntries([...ids, ...flow].map(id => [id, element(id)]));
const panel = element('flash-panel', { esptoolUrl: '/static/vendor/esptool-js-0.7.1/bundle.js' });
panel.querySelector = selector => parts[selector.replace('#', '')] || null;
panel.closest = () => null;
const listeners = {};
const imported = [];
const navigator = serial ? { serial: {} } : {};
const sandbox = {
  navigator,
  isSecureContext: secure,
  console,
  document: {
    readyState: 'complete',
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    querySelectorAll: selector => (selector === '#flash-panel' ? [panel] : []),
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
new vm.Script(fs.readFileSync(script, 'utf8'), {
  filename: script,
  importModuleDynamically: async specifier => {
    imported.push(specifier);
    return import(pathToFileURL(bundle).href);
  },
}).runInContext(sandbox);
// A second htmx swap of the same panel must not start a second check.
(listeners['htmx:load'] || []).forEach(fn => fn({ detail: { elt: panel } }));
setTimeout(() => {
  const shown = ids.filter(id => !parts[id].hidden && (id !== 'flash-start' || !parts['flash-ready'].hidden));
  console.log(JSON.stringify({
    shown,
    imported,
    disabled: parts['flash-start'].disabled,
    loaded: Boolean(panel.esptool && panel.esptool.ESPLoader && panel.esptool.Transport),
  }));
}, 500);
"""


def _run_flash_js(secure, serial):
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH; the flash.js tests need it")
    flash_js = REPO_ROOT / "static" / "js" / "flash.js"
    scenario = json.dumps({"secure": secure, "serial": serial})
    command = [
        node,
        "--experimental-vm-modules",
        "--no-warnings",
        "-e",
        _HARNESS,
        str(flash_js),
        str(ESPTOOL_DIR / "bundle.js"),
        scenario,
    ]
    out = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_flash_js_shows_the_flash_control_and_loads_esptool_js_in_a_secure_context_with_web_serial():
    result = _run_flash_js(secure=True, serial=True)

    assert result["shown"] == ["flash-ready", "flash-start"]
    assert result["imported"] == [ESPTOOL_URL]
    assert result["loaded"] is True
    assert result["disabled"] is False


def test_flash_js_shows_the_unsupported_message_and_downloads_without_web_serial():
    result = _run_flash_js(secure=True, serial=False)

    assert result["shown"] == ["flash-unsupported", "flash-downloads"]
    assert result["imported"] == []


def test_flash_js_shows_the_https_message_and_downloads_outside_a_secure_context():
    # Chrome removes navigator.serial outside a secure context, so the HTTPS message has priority.
    result = _run_flash_js(secure=False, serial=False)

    assert result["shown"] == ["flash-insecure", "flash-downloads"]
    assert result["imported"] == []
