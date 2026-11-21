"""Tests for the progress panel of the build page (FR-frontend-htmx-7 to -10 and -20)."""

import asyncio
import json
import shutil
import subprocess
import time
from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.rate_limit import limiter
from tests.conftest import REPO_ROOT

HX = {"HX-Request": "true"}
PSK_HEX = "deadbeef" * 8
FORM = {
    "variant": "heltec-v3",
    "config_source": "form",
    "owner_long_name": "PanelNode",
    "channels_to_write": "1",
    "channel_0[name]": "Primary",
    "channel_0[psk]": PSK_HEX,
}
ENDED = {"complete", "failed", "cancelled"}


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def firmware_source(settings):
    settings.firmware_dir.mkdir(parents=True)
    return settings.firmware_dir


@pytest.fixture
async def workers(app):
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


class _Parser(HTMLParser):
    """Collect the start tags of a page with their attributes, and the text of each step."""

    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def by_id(self, element_id):
        return next((attrs for _, attrs in self.elements if attrs.get("id") == element_id), None)

    def steps(self):
        return {attrs["data-step"]: attrs["data-state"] for _, attrs in self.elements if "data-step" in attrs}


def _parse(text):
    parser = _Parser()
    parser.feed(text)
    return parser


async def _wait_for(ctx, statuses, timeout=10.0):
    deadline = time.monotonic() + timeout
    while ctx.status not in statuses:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)


async def _start(app, client, **form):
    resp = await client.post("/api/v1/build-firmware", data={**FORM, **form}, headers=HX)
    assert resp.status_code == 202, resp.text
    build_id = resp.headers["HX-Push-Url"].removeprefix("/?build=")
    return resp, app.state.build_queue.get(build_id)


# FR-frontend-htmx-7


async def test_build_control_posts_the_config_form_and_the_variant_into_the_build_panel(client):
    page = _parse((await client.get("/")).text)
    button = page.by_id("buildBtn")
    assert button["hx-post"] == "/api/v1/build-firmware"
    assert button["hx-target"] == "#build-panel"
    assert set(button["hx-include"].replace(" ", "").split(",")) == {"#configForm", "#variant"}
    assert json.loads(button["hx-vals"]) == {"config_source": "form"}


@pytest.mark.usefixtures("firmware_source")
async def test_htmx_build_returns_202_with_a_panel_on_the_progress_stream(app, client):
    resp, ctx = await _start(app, client)

    assert "text/html" in resp.headers["content-type"]
    assert resp.headers["HX-Push-Url"] == f"/?build={ctx.build_id}"
    panel = _parse(resp.text)
    stream = next(attrs for _, attrs in panel.elements if "sse-connect" in attrs)
    assert stream["hx-ext"] == "sse"
    assert stream["sse-connect"] == f"/api/v1/build-progress/{ctx.build_id}"
    state = panel.by_id("build-state")
    assert state["hx-get"] == f"/api/v1/builds/{ctx.build_id}"
    assert state["hx-trigger"] == "sse:status"
    assert panel.by_id("build-log")["sse-swap"] == "log"
    assert panel.steps()["queued"] == "current"


@pytest.mark.usefixtures("firmware_source")
async def test_build_of_the_config_form_fields_returns_202_json_without_htmx(app, client):
    resp = await client.post("/api/v1/build-firmware", data=FORM)

    assert resp.status_code == 202
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    assert '"USERPREFS_CONFIG_OWNER_LONG_NAME": "PanelNode"' in ctx.config_content


@pytest.mark.usefixtures("firmware_source")
async def test_htmx_build_of_an_invalid_config_form_returns_the_field_errors(client):
    resp = await client.post("/api/v1/build-firmware", data={**FORM, "channel_0[psk]": "abc"}, headers=HX)

    assert resp.status_code == 422
    assert 'id="error-channel_0.psk"' in resp.text


async def test_htmx_build_with_a_full_build_queue_shows_the_queue_full_message(client, firmware_source, monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    for _ in range(5):
        assert (await client.post("/api/v1/build-firmware", data=FORM)).status_code == 202

    resp = await client.post("/api/v1/build-firmware", data=FORM, headers=HX)

    assert resp.status_code == 503
    assert "The build queue is full. Try again in a few minutes." in resp.text


async def test_htmx_build_without_a_firmware_source_shows_a_link_to_the_admin_page(app, client):
    resp, ctx = await _start(app, client)

    assert ctx.status == "failed"
    assert "No firmware version is installed. An admin must install one." in resp.text
    assert 'href="/admin"' in resp.text
    assert "sse-connect" not in resp.text


# FR-frontend-htmx-8


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_complete_build_shows_every_step_done_and_the_factory_artifact_first(app, client):
    _, ctx = await _start(app, client)
    await _wait_for(ctx, ENDED)

    resp = await client.get(f"/api/v1/builds/{ctx.build_id}", headers=HX)

    assert ctx.status == "complete", ctx.error
    panel = _parse(resp.text)
    assert panel.steps() == dict.fromkeys(["queued", "compiling", "linking", "packaging", "complete"], "done")
    links = [attrs["href"] for tag, attrs in panel.elements if tag == "a" and "/artifacts/" in attrs.get("href", "")]
    assert links[0] == f"/api/v1/builds/{ctx.build_id}/artifacts/firmware-heltec-v3-2.7.26.54e0d8d.factory.bin"
    assert len(links) == len(ctx.artifacts)
    assert "sse-connect" not in resp.text


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_ended_build_update_replaces_the_panel_so_the_progress_stream_closes(app, client):
    _, ctx = await _start(app, client)
    await _wait_for(ctx, ENDED)

    resp = await client.get(f"/api/v1/builds/{ctx.build_id}", headers=HX)

    assert resp.headers["HX-Retarget"] == "#build-panel"
    assert resp.headers["HX-Reswap"] == "innerHTML"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_panel_shows_the_masked_log_in_a_details_region(app, client):
    _, ctx = await _start(app, client)
    await _wait_for(ctx, ENDED)

    resp = await client.get(f"/?build={ctx.build_id}")

    log = resp.text.split('id="build-log"', 1)[1].split("</pre>", 1)[0]
    assert "<details" in resp.text.split('id="build-log"', 1)[0].rsplit("build-panel", 1)[1]
    assert "Compiling .pio/build/heltec-v3/src/main.cpp.o" in log
    assert "***" in log
    assert PSK_HEX not in resp.text


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_failed_build_shows_the_error_the_open_log_and_a_retry_control(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL", "1")
    _, ctx = await _start(app, client)
    await _wait_for(ctx, ENDED)

    resp = await client.get(f"/api/v1/builds/{ctx.build_id}", headers=HX)

    panel = _parse(resp.text)
    assert panel.steps()["failed"] == "current"
    assert "FAKE_PIO_FAIL is set" in resp.text
    assert "open" in panel.by_id("build-log-region")
    retry = panel.by_id("build-retry")
    assert retry["hx-post"] == "/api/v1/build-firmware"
    assert retry["hx-target"] == "#build-panel"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_running_build_update_returns_the_build_status_and_the_log_out_of_band(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    _, ctx = await _start(app, client)
    await _wait_for(ctx, {"compiling"})

    resp = await client.get(f"/api/v1/builds/{ctx.build_id}", headers=HX)

    assert "HX-Retarget" not in resp.headers
    panel = _parse(resp.text)
    assert panel.steps()["compiling"] == "current"
    assert panel.by_id("build-log")["hx-swap-oob"] == "true"


async def test_build_update_returns_json_without_htmx(app, client, firmware_source):
    _, ctx = await _start(app, client)

    resp = await client.get(f"/api/v1/builds/{ctx.build_id}")

    assert resp.json()["build_id"] == ctx.build_id
    assert resp.json()["status"] == "queued"


async def test_build_update_of_an_unknown_build_returns_404(client):
    resp = await client.get(f"/api/v1/builds/{'0' * 32}", headers=HX)

    assert resp.status_code == 404


# FR-frontend-htmx-9


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_cancel_control_during_compiling_shows_cancelled(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    _, ctx = await _start(app, client)
    await _wait_for(ctx, {"compiling"})
    running = _parse((await client.get(f"/api/v1/builds/{ctx.build_id}", headers=HX)).text)
    cancel = running.by_id("build-cancel")
    assert cancel["hx-post"] == f"/api/v1/builds/{ctx.build_id}/cancel"

    assert (await client.post(cancel["hx-post"], headers=HX)).status_code == 200
    await _wait_for(ctx, ENDED)

    resp = await client.get(f"/api/v1/builds/{ctx.build_id}", headers=HX)
    panel = _parse(resp.text)
    assert panel.steps()["cancelled"] == "current"
    assert panel.by_id("build-cancel") is None
    assert panel.by_id("build-retry") is not None


async def test_queued_build_has_a_cancel_control(app, client, firmware_source):
    resp, _ = await _start(app, client)

    assert _parse(resp.text).by_id("build-cancel") is not None


# FR-frontend-htmx-10


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_reload_during_a_build_shows_the_same_build_id_and_its_current_step(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    _, ctx = await _start(app, client)
    await _wait_for(ctx, {"compiling"})

    resp = await client.get(f"/?build={ctx.build_id}")

    assert resp.status_code == 200
    page = _parse(resp.text)
    assert ctx.build_id in resp.text
    assert page.steps()["compiling"] == "current"
    stream = next(attrs for _, attrs in page.elements if "sse-connect" in attrs)
    assert stream["sse-connect"] == f"/api/v1/build-progress/{ctx.build_id}"


async def test_reload_with_an_unknown_build_id_says_the_build_is_not_found(client):
    resp = await client.get(f"/?build={'0' * 32}")

    assert resp.status_code == 200
    assert "Build not found." in resp.text
    assert "sse-connect" not in resp.text


# FR-frontend-htmx-20


def test_old_build_progress_partial_is_deleted():
    assert not (REPO_ROOT / "templates" / "partials" / "build_progress.html").exists()


# main.js runs in Node with a fake browser.

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const listeners = {};
const button = { disabled: true };
const variant = { value: '', addEventListener: (type, fn) => { variant.change = fn; } };
const sandbox = {
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    getElementById: id => ({ buildBtn: button, variant: variant })[id] || null,
    querySelectorAll: () => [],
    documentElement: { getAttribute: () => 'dark' },
  },
  crypto: globalThis.crypto,
  btoa: globalThis.btoa,
  CSS: { escape: s => s },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const fire = (type, evt) => (listeners[type] || []).forEach(fn => fn(evt));
fire('DOMContentLoaded', {});
const before = button.disabled;
variant.value = 'heltec-v3';
variant.change();
const appended = [];
let prevented = false;
const log = { id: 'build-log', append: text => appended.push(text) };
fire('htmx:sseBeforeMessage', {
  target: log,
  detail: { type: 'log', data: JSON.stringify({ line: '<b>x</b>' }) },
  preventDefault: () => { prevented = true; },
});
const swap = { detail: { xhr: { status: 503 }, shouldSwap: false, isError: true } };
fire('htmx:beforeSwap', swap);
const closed = [];
['compiling', 'complete'].forEach(status => fire('htmx:sseMessage', {
  detail: { type: 'status', data: JSON.stringify({ status }), target: { close: () => closed.push(status) } },
}));
console.log(JSON.stringify({ before, after: button.disabled, appended, prevented, swap: swap.detail, closed }));
"""


@pytest.fixture(scope="module")
def main_js_run():
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH; the main.js tests need it")
    main_js = REPO_ROOT / "static" / "js" / "main.js"
    out = subprocess.run([node, "-e", _HARNESS, str(main_js)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_main_js_enables_the_build_control_when_a_variant_is_chosen(main_js_run):
    assert main_js_run["before"] is True
    assert main_js_run["after"] is False


def test_main_js_appends_each_log_line_as_text(main_js_run):
    assert main_js_run["prevented"] is True
    assert main_js_run["appended"] == ["<b>x</b>\n"]


def test_main_js_swaps_a_503_response_so_the_queue_full_message_shows(main_js_run):
    assert main_js_run["swap"]["shouldSwap"] is True


def test_main_js_closes_the_progress_stream_on_an_ended_build_status_only(main_js_run):
    assert main_js_run["closed"] == ["complete"]
