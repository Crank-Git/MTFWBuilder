"""Tests for the Firmware sources section of the admin page (feature 09, FR 13 and § Screens & states)."""

import asyncio
import io
import json
import logging
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.auth import CSRF_HEADER
from mtfwbuilder.database import record_firmware_source
from mtfwbuilder.main import lifespan
from mtfwbuilder.services import firmware_updater
from mtfwbuilder.services.firmware_catalog import CatalogUnavailable
from mtfwbuilder.services.firmware_updater import Install, ReleaseNotFound
from tests.conftest import REPO_ROOT, sign_in_admin

STABLE = "v2.7.26.54e0d8d"
ALPHA = "v2.8.2.d5f1619"
OLD_STABLE = "v2.7.15.567b8ea"
HTMX = {"HX-Request": "true"}

AVAILABLE = {
    "source": "api.meshtastic.org",
    "stable": [{"tag": STABLE, "title": "Meshtastic Firmware 2.7.26.54e0d8d Beta", "date": None, "revoked": False}],
    "alpha": [{"tag": ALPHA, "title": "Meshtastic Firmware 2.8.2.d5f1619 Alpha", "date": None, "revoked": False}],
}


class FakeCatalog:
    """A firmware catalog that gives a fixed list, or raises `CatalogUnavailable` when `result` is None."""

    def __init__(self, result=None):
        self.result = result

    def available(self):
        if self.result is None:
            raise CatalogUnavailable("both upstreams failed")
        return self.result


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def admin(client, settings):
    """Return the client with an admin session, and with the CSRF token in the header of each request."""
    client.headers[CSRF_HEADER] = sign_in_admin(client, settings)
    return client


@pytest.fixture
def installs(app, fake_pio, monkeypatch):
    """Return the install service, with fixed channel tags and a download that writes a local zip archive."""

    def fake_download(tag, zip_path):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("top/platformio.ini", "[platformio]\n")
        Path(zip_path).write_bytes(buffer.getvalue())

    monkeypatch.setattr(firmware_updater, "download_zipball", fake_download)
    service = app.state.firmware_installs
    service.resolver = {"stable": STABLE, "alpha": ALPHA}.__getitem__
    return service


def _record(settings, version, channel="stable", installed_at="2026-11-01T10:00:00+00:00", size_bytes=1024):
    path = settings.firmware_dir / version
    path.mkdir(parents=True, exist_ok=True)
    record = {
        "version": version,
        "channel": channel,
        "path": str(path),
        "installed_at": installed_at,
        "size_bytes": size_bytes,
        "status": "ready",
    }
    record_firmware_source(settings.database_path, record)


def _section(page: str) -> str:
    return page.split('id="firmware-sources"', 1)[1].split('id="cleanup"', 1)[0]


def _add_install(app, status="downloading", channel="stable", version=STABLE, error=""):
    install = Install("a1b2c3", version, channel, status=status, error=error)
    app.state.firmware_installs._installs[install.install_id] = install
    return install


# FR-firmware-versions-13


async def test_admin_without_a_firmware_source_shows_no_firmware_version_and_install_stable_first(admin):
    section = _section((await admin.get("/admin")).text)

    assert "No firmware version is installed." in section
    first_install = re.search(r'<button[^>]*hx-post="/api/v1/firmware/install"[^>]*>', section, re.S)[0]
    assert "&#34;stable&#34;" in first_install or '"stable"' in first_install
    assert "btn-success" in first_install
    assert section.index("Install stable") < section.index("Install alpha")


async def test_admin_with_a_firmware_source_shows_no_empty_card(admin, settings):
    _record(settings, STABLE)

    section = _section((await admin.get("/admin")).text)

    assert "No firmware version is installed." not in section


# Screens & states: Ready


async def test_admin_lists_each_installed_firmware_version_with_channel_date_size_and_remove(admin, settings):
    _record(settings, STABLE, "stable", "2026-11-02T08:00:00+00:00", 1_400_000_000)
    _record(settings, ALPHA, "alpha", "2026-11-05T08:00:00+00:00", 1_600_000_000)

    section = _section((await admin.get("/admin")).text)

    for version, channel, date, size in (
        (STABLE, "stable", "2026-11-02", "1.4 GB"),
        (ALPHA, "alpha", "2026-11-05", "1.6 GB"),
    ):
        row = re.search(rf'<tr data-version="{version}">.*?</tr>', section, re.S)[0]
        assert channel in row
        assert "badge" in row
        assert date in row
        assert size in row
        assert f'hx-delete="/api/v1/firmware/{version}"' in row


async def test_admin_reads_the_installed_versions_from_the_table_and_not_from_the_version_file(admin, settings):
    (settings.base_dir / "firmware_version.txt").write_text(f"Version: {OLD_STABLE}\nUpdated: 2026-11-21 10:00:00\n")

    page = (await admin.get("/admin")).text

    assert OLD_STABLE not in page
    assert "No firmware version is installed." in page


async def test_firmware_sources_section_sends_the_csrf_token_in_the_header(client, settings):
    token = sign_in_admin(client, settings)

    page = (await client.get("/admin")).text

    assert re.search(r'id="firmware-sources"[^>]*hx-headers=\'\{"X-CSRF-Token": "' + re.escape(token) + "\"}'", page)


async def test_admin_offers_an_install_by_exact_tag_with_an_error_slot(admin):
    section = _section((await admin.get("/admin")).text)

    form = re.search(r'<form[^>]*hx-post="/api/v1/firmware/install".*?</form>', section, re.S)[0]
    assert 'name="tag"' in form
    assert 'id="error-tag"' in form


# Screens & states: Installing


@pytest.mark.usefixtures("installs")
async def test_htmx_install_returns_202_with_a_progress_panel_on_the_install_progress_stream(app, admin):
    resp = await admin.post("/api/v1/firmware/install", json={"channel": "stable"}, headers=HTMX)

    assert resp.status_code == 202
    assert resp.headers["content-type"].startswith("text/html")
    [install_id] = re.findall(r'id="install-([0-9a-f]+)"', resp.text)
    assert f'sse-connect="/api/v1/firmware/install/{install_id}/progress"' in resp.text
    assert (resp.headers["HX-Retarget"], resp.headers["HX-Reswap"]) == ("#firmware-installs", "afterbegin")
    await asyncio.wait_for(app.state.firmware_installs.get(install_id).wait(), 10)


@pytest.mark.usefixtures("installs")
async def test_install_without_htmx_still_returns_the_json_install_id(app, admin):
    resp = await admin.post("/api/v1/firmware/install", json={"channel": "stable"})

    assert set(resp.json()) == {"install_id", "progress_url"}
    await asyncio.wait_for(app.state.firmware_installs.get(resp.json()["install_id"]).wait(), 10)


@pytest.mark.usefixtures("installs")
async def test_htmx_install_of_an_installed_version_shows_the_message_in_the_message_area(admin, settings):
    _record(settings, STABLE)

    resp = await admin.post("/api/v1/firmware/install", json={"tag": STABLE}, headers=HTMX)

    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("text/html")
    assert f"Firmware version {STABLE} is installed" in resp.text
    assert resp.headers["HX-Retarget"] == "#firmware-message"


async def test_htmx_install_without_a_channel_tag_shows_the_message_in_the_message_area(app, admin):
    def no_release(channel):
        raise ReleaseNotFound(channel)

    app.state.firmware_installs.resolver = no_release

    resp = await admin.post("/api/v1/firmware/install", json={"channel": "alpha"}, headers=HTMX)

    assert resp.status_code == 502
    assert "Cannot get the firmware version list from GitHub" in resp.text
    assert resp.headers["HX-Retarget"] == "#firmware-message"


async def test_admin_shows_the_progress_panel_of_an_install_that_has_not_ended(app, admin):
    install = _add_install(app, status="extracting")

    page = (await admin.get("/admin")).text

    assert f'sse-connect="/api/v1/firmware/install/{install.install_id}/progress"' in page


async def test_htmx_install_status_of_a_running_install_shows_the_current_step(app, admin):
    install = _add_install(app, status="extracting")

    resp = await admin.get(f"/api/v1/firmware/install/{install.install_id}", headers=HTMX)

    assert resp.status_code == 200
    assert re.search(r'data-step="extracting"[^>]*data-state="current"', resp.text)
    assert re.search(r'data-step="downloading"[^>]*data-state="done"', resp.text)
    assert 'role="progressbar"' in resp.text
    assert "HX-Retarget" not in resp.headers


async def test_install_status_without_htmx_returns_the_install_status_json(app, client):
    install = _add_install(app, status="installing packages")

    resp = await client.get(f"/api/v1/firmware/install/{install.install_id}")

    assert resp.json() == {
        "install_id": install.install_id,
        "version": STABLE,
        "status": "installing packages",
        "error": "",
    }


async def test_install_status_returns_404_for_an_unknown_install(client):
    resp = await client.get("/api/v1/firmware/install/unknown")

    assert resp.status_code == 404


async def test_htmx_status_of_a_ready_install_replaces_the_panel_and_refreshes_the_list(app, admin, settings):
    _record(settings, STABLE)
    install = _add_install(app, status="ready")

    resp = await admin.get(f"/api/v1/firmware/install/{install.install_id}", headers=HTMX)

    assert (resp.headers["HX-Retarget"], resp.headers["HX-Reswap"]) == (f"#install-{install.install_id}", "outerHTML")
    assert "sse-connect" not in resp.text
    assert f"Firmware version {STABLE} is ready." in resp.text
    oob = re.search(r'<div id="firmware-source-list" hx-swap-oob="innerHTML">.*', resp.text, re.S)[0]
    assert f'<tr data-version="{STABLE}">' in oob


# Screens & states: Failed


@pytest.mark.parametrize(
    ("channel", "body"),
    [("stable", {"channel": "stable"}), ("pinned", {"tag": STABLE})],
    ids=["by_channel", "by_tag"],
)
async def test_htmx_status_of_a_failed_install_shows_the_error_and_a_retry_of_the_same_install(
    app, admin, channel, body
):
    install = _add_install(app, status="failed", channel=channel, error="pio pkg install exited with code 1")

    resp = await admin.get(f"/api/v1/firmware/install/{install.install_id}", headers=HTMX)

    assert "pio pkg install exited with code 1" in resp.text
    retry = re.search(r'<button[^>]*id="install-retry"[^>]*>', resp.text, re.S)[0]
    assert 'hx-post="/api/v1/firmware/install"' in retry
    vals = json.loads(re.search(r"hx-vals='([^']*)'", retry)[1])
    assert vals == body


# Available firmware versions and Screens & states: Upstream unreachable


async def test_admin_loads_the_available_firmware_versions_after_the_page(admin):
    section = _section((await admin.get("/admin")).text)

    assert re.search(r'hx-get="/api/v1/firmware/available"[^>]*hx-trigger="load"', section)


async def test_htmx_available_lists_the_stable_and_alpha_tags_and_the_source(app, admin):
    app.state.firmware_catalog = FakeCatalog(AVAILABLE)

    resp = await admin.get("/api/v1/firmware/available", headers=HTMX)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert STABLE in resp.text
    assert ALPHA in resp.text
    assert "api.meshtastic.org" in resp.text


async def test_htmx_available_from_github_shows_the_release_date_and_the_revoked_badge(app, admin):
    alpha = {"tag": "v2.8.0.47db0e3", "title": "2.8.0 (Revoked)", "date": "2026-10-01T12:00:00Z", "revoked": True}
    app.state.firmware_catalog = FakeCatalog({**AVAILABLE, "source": "github", "alpha": [alpha]})

    resp = await admin.get("/api/v1/firmware/available", headers=HTMX)

    row = re.search(r'<tr data-tag="v2.8.0.47db0e3">.*?</tr>', resp.text, re.S)[0]
    assert "2026-10-01" in row
    assert "revoked" in row


async def test_htmx_available_with_both_upstreams_down_shows_cannot_reach(app, admin):
    app.state.firmware_catalog = FakeCatalog()

    resp = await admin.get("/api/v1/firmware/available", headers=HTMX)

    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("text/html")
    assert "Cannot reach api.meshtastic.org or GitHub." in resp.text


async def test_available_without_htmx_keeps_the_json_502(app, admin):
    app.state.firmware_catalog = FakeCatalog()

    resp = await admin.get("/api/v1/firmware/available")

    assert resp.status_code == 502
    assert resp.json() == {"detail": "Cannot reach api.meshtastic.org or GitHub."}


# Removal and Screens & states: Remove blocked


async def test_htmx_remove_returns_the_list_without_the_removed_version(admin, settings):
    _record(settings, STABLE)
    _record(settings, ALPHA, "alpha")

    resp = await admin.delete(f"/api/v1/firmware/{ALPHA}", headers=HTMX)

    assert resp.status_code == 200
    assert f'data-version="{STABLE}"' in resp.text
    assert f'data-version="{ALPHA}"' not in resp.text


async def test_htmx_remove_of_the_last_version_shows_no_firmware_version(admin, settings):
    _record(settings, STABLE)

    resp = await admin.delete(f"/api/v1/firmware/{STABLE}", headers=HTMX)

    assert "No firmware version is installed." in resp.text


async def test_remove_without_htmx_still_returns_204(admin, settings):
    _record(settings, STABLE)

    resp = await admin.delete(f"/api/v1/firmware/{STABLE}")

    assert resp.status_code == 204


async def test_htmx_remove_during_a_build_shows_the_remove_blocked_message(app, admin, settings, monkeypatch):
    _record(settings, STABLE)
    monkeypatch.setattr(app.state.build_queue, "unended_firmware_versions", lambda default: {STABLE})

    resp = await admin.delete(f"/api/v1/firmware/{STABLE}", headers=HTMX)

    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("text/html")
    assert "A build uses this firmware version. Try again when it ends." in resp.text
    assert resp.headers["HX-Retarget"] == "#firmware-message"
    assert (settings.firmware_dir / STABLE).is_dir()


async def test_htmx_remove_of_a_version_that_is_not_installed_shows_the_message(admin):
    resp = await admin.delete(f"/api/v1/firmware/{STABLE}", headers=HTMX)

    assert resp.status_code == 404
    assert "Firmware version not installed" in resp.text
    assert resp.headers["HX-Retarget"] == "#firmware-message"


# The system-info route reads the firmware_sources table (PM amendment).


async def test_system_info_gives_the_default_and_each_installed_firmware_version(client, settings):
    _record(settings, STABLE, "stable", "2026-11-02T08:00:00+00:00")
    _record(settings, ALPHA, "alpha", "2026-11-05T08:00:00+00:00")
    (settings.base_dir / "firmware_version.txt").write_text(f"Version: {OLD_STABLE}\nUpdated: now\n")

    data = (await client.get("/api/v1/system-info")).json()

    assert data["success"] is True
    assert data["version"] == STABLE
    assert data["last_updated"] == "2026-11-02T08:00:00+00:00"
    assert [source["version"] for source in data["firmware_versions"]] == [ALPHA, STABLE]


async def test_system_info_without_a_firmware_source_gives_not_installed(client):
    data = (await client.get("/api/v1/system-info")).json()

    assert (data["version"], data["last_updated"], data["firmware_versions"]) == ("Not installed", "Never", [])


# Startup removes the staging directories of installs that a crash stopped (PM amendment).


async def test_startup_removes_each_install_staging_directory_and_logs_it(app, settings, caplog):
    staging = settings.firmware_dir / f".install-{STABLE}-k3j2h1"
    (staging / "tree").mkdir(parents=True)
    (staging / "source.zip").write_bytes(b"partial")
    source_dir = settings.firmware_dir / ALPHA
    source_dir.mkdir()

    with caplog.at_level(logging.WARNING):
        async with lifespan(app):
            pass

    assert not staging.exists()
    assert source_dir.is_dir()
    assert any(staging.name in record.getMessage() for record in caplog.records)


# admin.js runs in Node with a fake browser and a fake htmx.

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const listeners = {};
const extensions = {};
const sandbox = {
  htmx: { defineExtension: (name, ext) => { extensions[name] = ext; } },
  document: { addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); } },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const fire = (type, evt) => (listeners[type] || []).forEach(fn => fn(evt));
const swapOf = (status, type) => {
  const evt = { detail: { xhr: { status, getResponseHeader: () => type }, shouldSwap: false, isError: true } };
  fire('htmx:beforeSwap', evt);
  return evt.detail;
};
const ext = extensions['json-body'];
const config = { detail: { headers: {} } };
ext.onEvent('htmx:configRequest', config);
const body = ext.encodeParameters({}, new Map([['channel', 'stable']]), null);
const closed = [];
['downloading', 'ready'].forEach(status => fire('htmx:sseMessage', {
  detail: { type: 'status', data: JSON.stringify({ status }), target: { close: () => closed.push(status) } },
}));
console.log(JSON.stringify({
  html409: swapOf(409, 'text/html; charset=utf-8'),
  json409: swapOf(409, 'application/json'),
  contentType: config.detail.headers['Content-Type'],
  body: JSON.parse(body),
  closed,
}));
"""


@pytest.fixture(scope="module")
def admin_js_run():
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH; the admin.js tests need it")
    admin_js = REPO_ROOT / "static" / "js" / "admin.js"
    out = subprocess.run([node, "-e", _HARNESS, str(admin_js)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_admin_js_swaps_an_html_error_fragment(admin_js_run):
    assert (admin_js_run["html409"]["shouldSwap"], admin_js_run["html409"]["isError"]) == (True, False)


def test_admin_js_keeps_the_htmx_default_for_a_json_error(admin_js_run):
    assert admin_js_run["json409"]["shouldSwap"] is False


def test_admin_js_sends_the_install_controls_as_a_json_body(admin_js_run):
    assert admin_js_run["contentType"] == "application/json"
    assert admin_js_run["body"] == {"channel": "stable"}


def test_admin_js_closes_the_install_progress_stream_after_ready(admin_js_run):
    assert admin_js_run["closed"] == ["ready"]


def test_admin_page_loads_admin_js():
    assert "js/admin.js" in (REPO_ROOT / "templates" / "admin.html").read_text()
