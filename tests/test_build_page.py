"""Tests for the build page on htmx (FR-frontend-htmx-1 to -6 and -18)."""

import base64
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import REPO_ROOT

HX = {"HX-Request": "true"}

PSK_15_BYTES_HEX = "ab" * 15


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _FormParser(HTMLParser):
    """Collect the elements of the page with their attributes, in document order."""

    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def by_id(self, element_id):
        return next((attrs for _, attrs in self.elements if attrs.get("id") == element_id), None)


async def _page(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    parser = _FormParser()
    parser.feed(resp.text)
    return resp.text, parser


def _default_form_body(parser) -> list[tuple[str, str]]:
    """Return the fields that a browser posts for the config form with its default values.

    A browser posts a hidden field, a checked checkbox, an input, and the selected option
    of a select, or its first option when no option is selected.
    """
    body = []
    select = None
    for tag, attrs in parser.elements:
        if attrs.get("form") is not None or attrs.get("name") == "variant":
            continue
        if tag == "select":
            select = {"name": attrs["name"], "value": None}
            body.append(select)
        elif tag == "option" and select is not None:
            if select["value"] is None or "selected" in attrs:
                select["value"] = attrs.get("value", "")
        elif tag in ("input", "textarea") and attrs.get("name"):
            select = None
            if attrs.get("type") != "checkbox" or "checked" in attrs:
                body.append({"name": attrs["name"], "value": attrs.get("value", "")})
    return [(field["name"], field["value"]) for field in body]


def _loc(name: str) -> str:
    """Return the error location of a form field name: `channel_0[psk]` gives `channel_0.psk`."""
    return re.sub(r"\[(\w+)\]", r".\1", name)


# FR-frontend-htmx-4


@pytest.mark.asyncio
async def test_firmware_builder_returns_308_to_the_build_page(client):
    resp = await client.get("/firmware-builder")
    assert resp.status_code == 308
    assert resp.headers["location"] == "/"


@pytest.mark.asyncio
async def test_build_page_has_the_variant_picker_the_build_control_and_the_build_panel_target(client):
    _, parser = await _page(client)
    variant = parser.by_id("variant")
    assert variant is not None
    assert variant["name"] == "variant"
    options = [attrs.get("value") for tag, attrs in parser.elements if tag == "option"]
    assert "feather_diy" in options
    assert parser.by_id("buildBtn") is not None
    assert parser.by_id("build-panel") is not None


@pytest.mark.asyncio
async def test_build_page_says_in_a_noscript_block_that_the_build_needs_javascript(client):
    text, _ = await _page(client)
    noscript = re.search(r"<noscript>(.*?)</noscript>", text, re.S)
    assert noscript
    assert "JavaScript" in noscript[1]


# FR-frontend-htmx-1


@pytest.mark.asyncio
async def test_preview_control_posts_the_form_to_api_v1_preview_with_htmx(client):
    _, parser = await _page(client)
    button = parser.by_id("previewBtn")
    assert button["hx-post"] == "/api/v1/preview"
    assert parser.by_id(button["hx-target"].removeprefix("#")) is not None


@pytest.mark.asyncio
async def test_htmx_preview_returns_the_userprefs_file_in_a_pre_element(client):
    resp = await client.post("/api/v1/preview", data={"owner_long_name": "PreviewNode"}, headers=HX)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<pre" in resp.text
    assert "USERPREFS_CHANNELS_TO_WRITE" in resp.text


@pytest.mark.asyncio
async def test_htmx_preview_of_channel_name_b_x_shows_the_literal_text(client):
    form = {"channels_to_write": "1", "channel_0[name]": "<b>x</b>"}
    resp = await client.post("/api/v1/preview", data=form, headers=HX)
    assert resp.status_code == 200
    assert "&lt;b&gt;x&lt;/b&gt;" in resp.text
    assert "<b>x</b>" not in resp.text


@pytest.mark.asyncio
async def test_htmx_preview_of_the_default_form_returns_the_userprefs_file(client):
    _, parser = await _page(client)
    body = _default_form_body(parser)
    assert ("mqtt_encryption_enabled", "true") in body
    resp = await client.post(
        "/api/v1/preview",
        content="&".join(f"{quote(k)}={quote(v)}" for k, v in body),
        headers={**HX, "content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert "USERPREFS_CHANNELS_TO_WRITE" in resp.text
    assert "USERPREFS_CONFIG_LORA_REGION" in resp.text


@pytest.mark.asyncio
async def test_preview_of_a_form_body_without_htmx_returns_json(client):
    resp = await client.post("/api/v1/preview", data={"owner_long_name": "PreviewNode"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert "PreviewNode" in resp.json()["content"]


# FR-frontend-htmx-3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form", "field", "name"),
    [
        ({"channels_to_write": "1", "channel_0[psk]": PSK_15_BYTES_HEX}, "channel_0.psk", "channel_0[psk]"),
        ({"mqtt_address": "mqtt://host"}, "mqtt_address", "mqtt_address"),
    ],
)
async def test_htmx_preview_with_an_invalid_field_returns_422_with_the_message_for_that_field(
    client, form, field, name
):
    resp = await client.post("/api/v1/preview", data=form, headers=HX)
    assert resp.status_code == 422
    assert "text/html" in resp.headers["content-type"]
    parser = _FormParser()
    parser.feed(resp.text)
    slot = parser.by_id(f"error-{field}")
    assert slot is not None
    assert slot["hx-swap-oob"] == "true"
    assert slot["data-field"] == name


@pytest.mark.asyncio
async def test_htmx_422_fragment_shows_the_validation_message_as_text(client):
    form = {"channels_to_write": "1", "channel_0[psk]": PSK_15_BYTES_HEX}
    resp = await client.post("/api/v1/preview", data=form, headers=HX)
    assert "the PSK is not 0, 1, 16 or 32 bytes" in resp.text


@pytest.mark.asyncio
async def test_preview_422_without_htmx_stays_json(client):
    resp = await client.post("/api/v1/preview", data={"channels_to_write": "9"})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "channels_to_write"]


@pytest.mark.asyncio
async def test_every_text_field_of_the_build_page_has_an_error_slot_for_its_location(client):
    _, parser = await _page(client)
    names = [
        attrs["name"]
        for tag, attrs in parser.elements
        if tag in ("input", "select", "textarea")
        and attrs.get("form") is None
        and attrs.get("type") not in ("checkbox", "hidden")
        and attrs.get("name") not in (None, "variant")
    ]
    assert "channel_0[psk]" in names
    missing = [name for name in names if parser.by_id(f"error-{_loc(name)}") is None]
    assert missing == []


# FR-frontend-htmx-2 and the edge case "JavaScript disabled"


@pytest.mark.asyncio
async def test_config_form_posts_to_api_v1_download_without_javascript(client):
    _, parser = await _page(client)
    form = parser.by_id("configForm")
    assert form["method"] == "post"
    assert form["action"] == "/api/v1/download"
    download = parser.by_id("downloadBtn")
    assert download.get("type") == "submit"


@pytest.mark.asyncio
async def test_download_of_flat_form_fields_returns_the_userprefs_file(client):
    form = {"owner_long_name": "FormNode", "channels_to_write": "1", "channel_0[name]": "Primary"}
    resp = await client.post("/api/v1/download", data=form)
    assert resp.status_code == 200
    assert "userPrefs.jsonc" in resp.headers["content-disposition"]
    parsed = json.loads(resp.text)
    assert parsed["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "FormNode"
    assert parsed["USERPREFS_CHANNEL_0_NAME"] == "Primary"


@pytest.mark.asyncio
async def test_every_checkbox_of_the_build_page_follows_a_hidden_false_field_of_the_same_name(client):
    _, parser = await _page(client)
    inputs = [attrs for tag, attrs in parser.elements if tag == "input" and attrs.get("name")]
    checkboxes = [i for i, attrs in enumerate(inputs) if attrs.get("type") == "checkbox"]
    assert checkboxes
    for i in checkboxes:
        before = inputs[i - 1]
        assert (before.get("type"), before.get("name"), before.get("value")) == (
            "hidden",
            inputs[i]["name"],
            "false",
        ), inputs[i]["name"]


@pytest.mark.asyncio
async def test_unchecked_mqtt_encryption_box_writes_false_to_the_userprefs_file(client):
    # The browser sends the hidden "false" field only, because the box is unchecked.
    form = [
        ("network_enabled", "false"),
        ("network_enabled", "true"),
        ("mqtt_enabled", "false"),
        ("mqtt_enabled", "true"),
        ("mqtt_encryption_enabled", "false"),
    ]
    resp = await client.post(
        "/api/v1/download",
        content="&".join(f"{k}={v}" for k, v in form),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert json.loads(resp.text)["USERPREFS_MQTT_ENCRYPTION_ENABLED"] == "false"


# PSK help text (Epic 1 batch-review finding)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["/api/v1/preview", "/api/v1/build-firmware"])
async def test_htmx_422_message_fits_a_preview_and_a_build(client, url):
    form = {"variant": "heltec-v3", "config_source": "form", "channels_to_write": "1", "channel_0[psk]": "abc"}
    resp = await client.post(url, data=form, headers=HX)
    assert resp.status_code == 422
    assert "The config has errors. Correct the fields marked below." in resp.text
    assert "Preview again" not in resp.text


@pytest.mark.asyncio
async def test_every_label_of_the_build_page_names_a_form_control(client):
    _, parser = await _page(client)
    tags = {attrs["id"]: tag for tag, attrs in parser.elements if "id" in attrs}
    targets = {attrs["for"]: tags.get(attrs["for"]) for tag, attrs in parser.elements if tag == "label"}
    assert {name: tag for name, tag in targets.items() if tag not in ("input", "select", "textarea")} == {}


@pytest.mark.asyncio
async def test_preview_region_takes_its_name_from_the_preview_heading(client):
    _, parser = await _page(client)
    region = parser.by_id("preview-container")
    heading = next(tag for tag, attrs in parser.elements if attrs.get("id") == region["aria-labelledby"])
    assert re.fullmatch(r"h[1-6]", heading)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", range(8))
async def test_psk_placeholder_of_each_channel_is_a_32_byte_base64_key(client, channel):
    _, parser = await _page(client)
    placeholder = parser.by_id(f"channel{channel}PSK")["placeholder"]
    assert len(base64.b64decode(placeholder, validate=True)) == 32


@pytest.mark.asyncio
async def test_psk_help_text_of_each_channel_names_base64_or_hex_and_the_valid_lengths(client):
    text, _ = await _page(client)
    assert "C-style hex" not in text
    assert text.count("Base64 or hex. The key is 0, 1, 16 or 32 bytes.") == 8


# FR-frontend-htmx-5, -6 and -18: main.js runs in Node with a fake browser.

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const store = new Map([['meshtastic_config', '{"wifi_psk":"secret"}'], ['theme', 'dark']]);
const listeners = {};
const sandbox = {
  localStorage: {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k),
  },
  document: {
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    getElementById: () => null,
    querySelectorAll: selector => (selector === '.field-error' ? [slot] : []),
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
const swap = { detail: { xhr: { status: 422 }, shouldSwap: false, isError: true } };
fire('htmx:beforeSwap', swap);
// Each request comes from one control: the id names it, and `inForm` says if the config form holds it.
const slot = { textContent: '' };
const errorsAfter = {};
[
  ['build-state', false],
  ['build-cancel', false],
  ['previewBtn', true],
  ['buildBtn', false],
  ['build-retry', false],
].forEach(([id, inForm]) => {
  slot.textContent = 'the PSK is not 0, 1, 16 or 32 bytes';
  const elt = { id, closest: sel => (inForm && sel === '#configForm' ? {} : null) };
  fire('htmx:beforeRequest', { detail: { elt } });
  errorsAfter[id] = slot.textContent;
});
console.log(JSON.stringify({
  keys: [...store.keys()],
  psk: [vm.runInContext('generatePSK()', sandbox), vm.runInContext('generatePSK()', sandbox)],
  swap: swap.detail,
  errorsAfter,
}));
"""


@pytest.fixture(scope="module")
def main_js_run():
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH; the main.js tests need it")
    main_js = REPO_ROOT / "static" / "js" / "main.js"
    out = subprocess.run([node, "-e", _HARNESS, str(main_js)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_main_js_deletes_localstorage_meshtastic_config_on_load(main_js_run):
    assert "meshtastic_config" not in main_js_run["keys"]
    assert "theme" in main_js_run["keys"]


def test_psk_generator_fills_32_random_bytes_as_base64(main_js_run):
    first, second = main_js_run["psk"]
    assert len(base64.b64decode(first, validate=True)) == 32
    assert first != second


def test_main_js_swaps_a_422_response_so_the_field_errors_show(main_js_run):
    assert main_js_run["swap"]["shouldSwap"] is True
    assert main_js_run["swap"]["isError"] is False


def test_main_js_keeps_the_field_errors_during_a_build_status_refresh_or_a_cancel(main_js_run):
    assert main_js_run["errorsAfter"]["build-state"] == "the PSK is not 0, 1, 16 or 32 bytes"
    assert main_js_run["errorsAfter"]["build-cancel"] == "the PSK is not 0, 1, 16 or 32 bytes"


@pytest.mark.parametrize("control", ["previewBtn", "buildBtn", "build-retry"])
def test_main_js_clears_the_field_errors_when_a_preview_or_a_build_starts(main_js_run, control):
    assert main_js_run["errorsAfter"][control] == ""


def test_one_psk_generator_exists_in_the_pages():
    sources = [*(REPO_ROOT / "templates").rglob("*.html"), *(REPO_ROOT / "static" / "js").rglob("*.js")]
    hits = [src.name for src in sources for _ in re.finditer(r"getRandomValues", src.read_text())]
    assert hits == ["main.js"]


def test_pages_write_no_config_to_localstorage():
    sources = [*(REPO_ROOT / "templates").rglob("*.html"), *(REPO_ROOT / "static" / "js").rglob("*.js")]
    writes = [
        f"{src.relative_to(REPO_ROOT)}: {m[0]}"
        for src in sources
        for m in re.finditer(r"localStorage\.setItem\(\s*('[^']*'|\"[^\"]*\")", src.read_text())
        if m[1].strip("'\"") != "theme"
    ]
    assert writes == []
