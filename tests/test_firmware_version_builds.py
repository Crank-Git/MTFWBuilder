"""Tests for the firmware version of a build (feature 09, FR 10, 11, 12; criteria 2 and 3).

Two fixture firmware sources are installed: the stable `v2.7.26.54e0d8d` and the alpha `v2.8.2.d5f1619`.
The alpha is the newer install, so a default build that uses the stable firmware version does not use the
newest install. The fake `pio` reads `.fake-pio-version`, so the artifact names show which firmware source ran.
"""

import asyncio
import json
import time
from html.parser import HTMLParser

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import record_firmware_source
from tests.conftest import FIRMWARE_VERSION, FIXTURE_REGISTRY

STABLE = FIRMWARE_VERSION
ALPHA = "v2.8.2.d5f1619"
CONFIG = json.dumps({"owner_long_name": "TestNode"})


@pytest.fixture
def alpha_source(settings, firmware_source):
    """Return the directory of the alpha firmware source, installed after the stable one."""
    source_dir = settings.firmware_dir / ALPHA
    source_dir.mkdir(parents=True)
    (source_dir / ".fake-pio-version").write_text(ALPHA.removeprefix("v"))
    record = {
        "version": ALPHA,
        "channel": "alpha",
        "path": str(source_dir),
        "installed_at": "2026-11-10T00:00:00+00:00",
        "size_bytes": 0,
        "status": "ready",
    }
    record_firmware_source(settings.database_path, record, FIXTURE_REGISTRY)
    return source_dir


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def workers(app):
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


async def _until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "the condition stayed false"
        await asyncio.sleep(0.02)


async def _build(app, client, **fields):
    """Return the build of a build request with the extra form `fields`, after the build ends."""
    data = {"variant": "heltec-v3", "config_source": "current", "config_json": CONFIG, **fields}
    resp = await client.post("/api/v1/build-firmware", data=data)
    assert resp.status_code == 202, resp.text
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    await _until(lambda: ctx.ended)
    assert ctx.status == "complete", ctx.error
    return ctx


async def _record_version(settings, build_id):
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT firmware_version FROM builds WHERE id = ?", (build_id,))
        return (await cursor.fetchone())[0]


# Criterion 2, FR-firmware-versions-10 and -12


@pytest.mark.usefixtures("fake_pio", "alpha_source", "workers")
async def test_build_request_with_the_alpha_tag_gives_artifacts_whose_names_hold_the_alpha_version(
    app, client, settings
):
    ctx = await _build(app, client, firmware_version=ALPHA)

    assert ctx.artifacts
    assert all("2.8.2.d5f1619" in artifact.name for artifact in ctx.artifacts)
    assert ctx.firmware_version == ALPHA
    assert await _record_version(settings, ctx.build_id) == ALPHA


# Criterion 3, FR-firmware-versions-10 and -12


@pytest.mark.usefixtures("fake_pio", "alpha_source", "workers")
async def test_build_request_without_firmware_version_uses_the_newest_installed_stable_version(app, client, settings):
    ctx = await _build(app, client)

    assert all("2.7.26.54e0d8d" in artifact.name for artifact in ctx.artifacts)
    assert ctx.firmware_version == STABLE
    assert await _record_version(settings, ctx.build_id) == STABLE


@pytest.mark.usefixtures("fake_pio", "alpha_source", "workers")
async def test_build_request_with_an_empty_firmware_version_uses_the_stable_version(app, client):
    ctx = await _build(app, client, firmware_version="")

    assert ctx.firmware_version == STABLE


@pytest.mark.usefixtures("alpha_source")
async def test_queued_build_holds_its_firmware_version_from_submit(app, client):
    # No build worker runs, so the build stays queued, and a removal of its firmware version must see the version.
    data = {"variant": "heltec-v3", "config_source": "current", "config_json": CONFIG, "firmware_version": ALPHA}
    resp = await client.post("/api/v1/build-firmware", data=data)
    ctx = app.state.build_queue.get(resp.json()["build_id"])

    assert ctx.status == "queued"
    assert app.state.build_queue.unended_firmware_versions(STABLE) == {ALPHA}


@pytest.mark.parametrize("version", ["v9.9.9.0000000", "not-a-tag"])
@pytest.mark.usefixtures("alpha_source")
async def test_build_request_for_a_firmware_version_that_is_not_installed_returns_422(app, client, version):
    data = {"variant": "heltec-v3", "config_source": "current", "config_json": CONFIG, "firmware_version": version}
    resp = await client.post("/api/v1/build-firmware", data=data)

    assert resp.status_code == 422
    assert [error["loc"] for error in resp.json()["detail"]] == [["body", "firmware_version"]]
    assert not app.state.build_queue.unended_build_ids()


# FR-firmware-versions-11


class _Parser(HTMLParser):
    """Collect the options of the firmware version selector, with their attributes and text."""

    def __init__(self):
        super().__init__()
        self.elements = {}
        self.options = []
        self._in_selector = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.elements[attrs["id"]] = attrs
        if tag == "select":
            self._in_selector = attrs.get("id") == "firmware_version"
        elif tag == "option" and self._in_selector:
            self.options.append([attrs, ""])

    def handle_endtag(self, tag):
        if tag == "select":
            self._in_selector = False

    def handle_data(self, data):
        if self._in_selector and self.options:
            self.options[-1][1] += data


async def _page(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    parser = _Parser()
    parser.feed(resp.text)
    return parser


@pytest.mark.usefixtures("alpha_source")
async def test_build_page_lists_each_installed_firmware_version_with_its_release_channel(client):
    parser = await _page(client)

    assert parser.elements["firmware_version"]["name"] == "firmware_version"
    options = {attrs["value"]: " ".join(text.split()) for attrs, text in parser.options}
    assert options == {ALPHA: f"{ALPHA} (alpha)", STABLE: f"{STABLE} (stable)"}


@pytest.mark.usefixtures("alpha_source")
async def test_firmware_version_selector_selects_the_newest_installed_stable_version(client):
    parser = await _page(client)

    selected = [attrs["value"] for attrs, _ in parser.options if "selected" in attrs]
    assert selected == [STABLE]


@pytest.mark.usefixtures("alpha_source")
async def test_build_control_sends_the_firmware_version_selector(client):
    parser = await _page(client)

    included = [selector.strip() for selector in parser.elements["buildBtn"]["hx-include"].split(",")]
    assert "#firmware_version" in included


async def test_firmware_version_selector_without_an_installed_version_says_so(client):
    parser = await _page(client)

    assert "disabled" in parser.elements["firmware_version"]
    assert [" ".join(text.split()) for _, text in parser.options] == ["No firmware version is installed."]


@pytest.mark.usefixtures("alpha_source")
async def test_htmx_build_request_for_a_firmware_version_that_is_not_installed_fills_its_error_slot(client):
    parser = await _page(client)
    assert "error-firmware_version" in parser.elements
    data = {
        "variant": "heltec-v3",
        "config_source": "current",
        "config_json": CONFIG,
        "firmware_version": "v9.9.9.0000000",
    }

    resp = await client.post("/api/v1/build-firmware", data=data, headers={"HX-Request": "true"})

    assert resp.status_code == 422
    assert 'id="error-firmware_version"' in resp.text
    assert "firmware version v9.9.9.0000000 is not installed" in resp.text
