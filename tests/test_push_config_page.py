"""Tests for the push config page and its vendored Meshtastic modules (feature push-config, FR 2, 3 and 11)."""

import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.config import load_settings
from tests.conftest import REPO_ROOT

ADR = REPO_ROOT / "docs" / "adr" / "0008-meshtastic-js-library.md"
VENDOR_DIR = REPO_ROOT / "static" / "vendor" / "meshtastic"
# A row of the ADR "Vendor layout" table: the file name, the source URL, the size and the SHA-256.
VENDOR_ROW = re.compile(r"^\| `(meshtastic-[\w.-]+\.mjs)` \| (\S+) \| ([\d,]+) \| `([0-9a-f]{64})` \|$", re.MULTILINE)
MANAGED = "This device is managed. Only an admin key holder can change it remotely."


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class _PageParser(HTMLParser):
    """Collect the import map, the head elements in order, the module scripts and the link targets."""

    def __init__(self):
        super().__init__()
        self.head = []
        self.modules = []
        self.links = []
        self.import_map = None
        self._in_head = False
        self._in_import_map = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "head":
            self._in_head = True
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag != "script":
            return
        if self._in_head:
            self.head.append(attrs.get("type") or "classic")
        if attrs.get("type") == "importmap":
            self._in_import_map = True
            self.import_map = ""
        elif attrs.get("type") == "module":
            self.modules.append(attrs["src"])

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        elif tag == "script":
            self._in_import_map = False

    def handle_data(self, data):
        if self._in_import_map:
            self.import_map += data


async def _parse(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200
    parser = _PageParser()
    parser.feed(resp.text)
    return parser, resp.text


def test_adr_vendor_layout_names_three_modules():
    assert [row[0] for row in VENDOR_ROW.findall(ADR.read_text())] == [
        "meshtastic-core-2.6.7.mjs",
        "meshtastic-transport-web-serial-0.2.5.mjs",
        "meshtastic-transport-web-bluetooth-0.1.5.mjs",
    ]


@pytest.mark.parametrize("row", VENDOR_ROW.findall(ADR.read_text()), ids=lambda row: row[0])
def test_vendored_module_has_the_size_and_sha256_of_the_adr(row):
    name, _url, size, sha256 = row
    data = (VENDOR_DIR / name).read_bytes()
    assert len(data) == int(size.replace(",", ""))
    assert hashlib.sha256(data).hexdigest() == sha256


def test_vendor_readme_names_each_module_with_its_source_hash_and_license():
    readme = (VENDOR_DIR / "README.md").read_text()
    for name, url, _size, sha256 in VENDOR_ROW.findall(ADR.read_text()):
        assert name in readme
        assert url in readme
        assert sha256 in readme
    assert "GPL-3.0-only" in readme


@pytest.mark.asyncio
async def test_push_config_page_has_the_title_and_both_connect_controls(client):
    _, html = await _parse(client, "/push-config")
    assert "<title>Push config — MTFWBuilder</title>" in html
    assert 'id="push-connect-usb"' in html
    assert ">Connect via USB<" in html
    assert 'id="push-connect-bluetooth"' in html
    assert ">Connect via Bluetooth<" in html
    assert MANAGED in html


@pytest.mark.asyncio
async def test_push_config_import_map_is_in_head_and_maps_each_package_to_a_vendored_file(client):
    parser, _ = await _parse(client, "/push-config")
    # A browser ignores an import map that comes after the first module script.
    assert "importmap" in parser.head
    assert "module" not in parser.head[: parser.head.index("importmap")]
    imports = json.loads(parser.import_map)["imports"]
    assert sorted(imports) == [
        "@meshtastic/core",
        "@meshtastic/transport-web-bluetooth",
        "@meshtastic/transport-web-serial",
    ]
    for url in imports.values():
        path = urlsplit(url).path
        assert path.startswith("/static/vendor/meshtastic/meshtastic-")
        resp = await client.get(path)
        assert resp.status_code == 200
        # A browser refuses a module script that has no JavaScript MIME type.
        assert resp.headers["content-type"].startswith("text/javascript")


@pytest.mark.asyncio
async def test_push_config_page_loads_push_config_js_as_a_module(client):
    parser, _ = await _parse(client, "/push-config")
    assert [urlsplit(src).path for src in parser.modules] == ["/static/js/push-config.js"]
    assert (await client.get("/static/js/push-config.js")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/profiles"])
async def test_page_links_to_the_push_config_page(client, path):
    parser, _ = await _parse(client, path)
    assert "/push-config" in parser.links


# Each package that the vendored builds hold, with its version and its license. The npm core 2.6.7 `dist/mod.js`
# names the first five in `//#region` comments. esm.sh adds `crc` and the `unenv` stand-ins of `os`, `path` and `util`.
BUNDLED = [
    ("`@meshtastic/protobufs`", "2.7.8", "`GPLV3`"),
    ("`@bufbuild/protobuf`", "2.8.0", "Apache-2.0 AND BSD-3-Clause"),
    ("`ste-core`", "3.0.11", "MIT"),
    ("`ste-simple-events`", "3.0.11", "MIT"),
    ("`tslog`", "4.9.3", "MIT"),
    ("`crc`", "4.3.2", "MIT"),
    ("`unenv`", None, "MIT"),
]


@pytest.mark.parametrize(("package", "version", "license_id"), BUNDLED, ids=[row[0].strip("`") for row in BUNDLED])
def test_vendor_readme_lists_each_bundled_dependency_with_its_license(package, version, license_id):
    readme = (VENDOR_DIR / "README.md").read_text()
    [row] = [line for line in readme.splitlines() if line.startswith(f"| {package} |")]

    assert license_id in row
    assert version is None or f"| {version} |" in row


@pytest.mark.asyncio
async def test_push_config_page_renders_no_test_hook_flag_by_default(client, settings):
    _, html = await _parse(client, "/push-config")

    assert settings.push_test_hooks is False
    assert 'id="push-test-hooks"' not in html


@pytest.mark.asyncio
async def test_push_config_page_renders_the_test_hook_flag_when_the_setting_is_on(app, client):
    app.state.settings.push_test_hooks = True

    _, html = await _parse(client, "/push-config")

    assert 'id="push-test-hooks"' in html


def test_push_test_hooks_setting_reads_the_environment(settings_env, monkeypatch):
    monkeypatch.setenv("MTFW_PUSH_TEST_HOOKS", "true")

    assert load_settings().push_test_hooks is True
