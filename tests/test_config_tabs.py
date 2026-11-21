"""Tests for the tab layout of the config form (feature 11, Screens & states, FR-config-coverage-15 and -17)."""

import json
import re
from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient

TABS = (
    ("device", "Device"),
    ("channels", "Channels"),
    ("lora", "LoRa"),
    ("position", "Position"),
    ("network", "Network"),
    ("telemetry", "Telemetry"),
    ("security", "Security"),
    ("event", "Event"),
    ("mesh-beacon", "Mesh beacon"),
    ("advanced", "Advanced"),
)

# One key of each tab, so a test can find out if an include is in the correct tab pane.
KEY_OF_TAB = {
    "device": "USERPREFS_CONFIG_OWNER_SHORT_NAME",
    "channels": "USERPREFS_CHANNELS_TO_WRITE",
    "lora": "USERPREFS_CONFIG_LORA_REGION",
    "position": "USERPREFS_CONFIG_GPS_MODE",
    "network": "USERPREFS_NETWORK_WIFI_SSID",
    "telemetry": "USERPREFS_CONFIG_DEVICE_TELEM_UPDATE_INTERVAL",
    "security": "USERPREFS_CONFIG_SECURITY_IS_MANAGED",
    "event": "USERPREFS_EVENT_MODE",
    "mesh-beacon": "USERPREFS_MESH_BEACON_MESSAGE",
    "advanced": "USERPREFS_OEM_TEXT",
}

BANNER = "These values are factory defaults. The device applies them on first boot and on factory reset only."


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _Tree(HTMLParser):
    """Record each element with its attributes, its text and the ids of the elements that hold it."""

    VOID = {"input", "br", "hr", "img", "meta", "link", "source", "col", "area", "base", "wbr"}

    def __init__(self):
        super().__init__()
        self.elements = []
        self._open = []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "text": "", "parents": [e["attrs"].get("id") for e in self._open]}
        self.elements.append(element)
        if tag not in self.VOID:
            self._open.append(element)

    def handle_endtag(self, tag):
        while self._open:
            if self._open.pop()["tag"] == tag:
                break

    def handle_data(self, data):
        for element in self._open:
            element["text"] += data

    def by_id(self, element_id):
        return next(e for e in self.elements if e["attrs"].get("id") == element_id)

    def where(self, **attrs):
        return [e for e in self.elements if all(e["attrs"].get(k) == v for k, v in attrs.items())]


async def _page(client) -> tuple[str, _Tree]:
    resp = await client.get("/")
    assert resp.status_code == 200
    tree = _Tree()
    tree.feed(resp.text)
    return resp.text, tree


@pytest.mark.asyncio
async def test_config_form_has_the_ten_tabs_of_the_spec_in_order(client):
    _, tree = await _page(client)
    tablist = tree.by_id("configTabList")
    assert tablist["attrs"]["role"] == "tablist"
    tabs = [e for e in tree.where(role="tab") if "configTabList" in e["parents"]]
    assert [e["attrs"]["id"] for e in tabs] == [f"config-tab-{tab_id}" for tab_id, _ in TABS]
    for element, (_, label) in zip(tabs, TABS, strict=True):
        assert element["text"].strip().startswith(label)


@pytest.mark.asyncio
async def test_only_the_first_tab_is_selected_and_in_the_tab_order(client):
    _, tree = await _page(client)
    tabs = [e for e in tree.where(role="tab") if "configTabList" in e["parents"]]
    assert [e["attrs"]["aria-selected"] for e in tabs] == ["true"] + ["false"] * 9
    assert [e["attrs"]["tabindex"] for e in tabs] == ["0"] + ["-1"] * 9


@pytest.mark.asyncio
async def test_each_tab_controls_a_tab_pane_that_the_tab_labels(client):
    _, tree = await _page(client)
    for tab_id, _ in TABS:
        tab = tree.by_id(f"config-tab-{tab_id}")
        pane = tree.by_id(tab["attrs"]["aria-controls"])
        assert pane["attrs"]["role"] == "tabpanel"
        assert pane["attrs"]["aria-labelledby"] == f"config-tab-{tab_id}"
        assert "config-pane" in pane["attrs"]["class"].split()


@pytest.mark.asyncio
@pytest.mark.parametrize(("tab_id", "key"), KEY_OF_TAB.items())
async def test_each_tab_pane_holds_the_controls_of_its_tab(client, tab_id, key):
    _, tree = await _page(client)
    [fieldset] = tree.where(**{"data-userprefs-key": key})
    assert f"config-pane-{tab_id}" in fieldset["parents"]


@pytest.mark.asyncio
async def test_every_control_is_in_a_tab_pane(client):
    _, tree = await _page(client)
    panes = {f"config-pane-{tab_id}" for tab_id, _ in TABS}
    outside = [
        e["attrs"]["data-userprefs-key"]
        for e in tree.elements
        if "data-userprefs-key" in e["attrs"] and not panes & set(e["parents"])
    ]
    assert outside == []


@pytest.mark.asyncio
async def test_each_tab_label_has_a_count_and_a_hidden_error_dot(client):
    _, tree = await _page(client)
    for tab_id, _ in TABS:
        inside = [e for e in tree.elements if f"config-tab-{tab_id}" in e["parents"]]
        classes = [e["attrs"].get("class", "").split() for e in inside]
        assert any("tab-count" in c for c in classes)
        [dot] = [e for e, c in zip(inside, classes, strict=True) if "tab-error-dot" in c]
        assert "hidden" in dot["attrs"]


@pytest.mark.asyncio
async def test_config_form_shows_the_factory_default_banner(client):
    _, tree = await _page(client)
    banner = tree.by_id("factoryDefaultBanner")
    assert " ".join(banner["text"].split()) == BANNER
    assert "configForm" in banner["parents"]


@pytest.mark.asyncio
async def test_every_control_shows_help_text_and_its_userprefs_key(client):
    text, _ = await _page(client)
    fieldsets = re.findall(r'<fieldset[^>]*data-userprefs-key="([^"]+)"[^>]*>(.*?)</fieldset>', text, re.S)
    assert len(fieldsets) > 100
    missing = [
        key
        for key, body in fieldsets
        if "form-text" not in body or not re.search(rf'<code class="small">{re.escape(key)}</code>', body)
    ]
    assert missing == []


@pytest.mark.asyncio
async def test_time_zone_control_has_the_posix_string_of_each_iana_time_zone(client):
    _, tree = await _page(client)
    table = json.loads(tree.by_id("tzPosixTable")["text"])
    assert table["America/New_York"] == "EST5EDT,M3.2.0,M11.1.0"
    assert tree.by_id("tzPosix")["tag"] == "code"
    assert "USERPREFS_TZ_STRING" in tree.by_id("tzStringHelp")["text"]
