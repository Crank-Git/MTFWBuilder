"""Tests for the install and the removal of firmware versions (feature 09, FR 3, 5, 6, 7, 8).

No test reaches the network: a fake downloader writes a local zip archive, and the fake `pio` acts as
`pio pkg install`.
"""

import asyncio
import errno
import io
import json
import re
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import aiosqlite
import pytest
import requests
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.auth import CSRF_HEADER
from mtfwbuilder.config import load_settings
from mtfwbuilder.services import firmware_updater
from mtfwbuilder.services.firmware_catalog import CatalogUnavailable
from mtfwbuilder.services.firmware_updater import (
    Install,
    InstallFailed,
    extract_zipball,
    install_events,
)
from mtfwbuilder.services.userprefs_keys import SCAN_FILE
from tests.conftest import sign_in_admin

STABLE = "v2.7.26.54e0d8d"
ALPHA = "v2.8.2.d5f1619"
PINNED = "v2.7.15.567b8ea"
CONFIG = json.dumps({"owner_long_name": "TestNode"})
REVOKED = "v2.8.2.4c89b69"


def _zip_bytes(entries=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in (
            entries or {"top/platformio.ini": "[platformio]\n", "top/src/main.cpp": "int main() {}\n"}
        ).items():
            zf.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture
def downloads(monkeypatch):
    """Replace the GitHub download with a local zip archive, and return the list of downloaded tags.

    A test sets `downloads.body` to other bytes, or `downloads.error` to an exception that the download raises.
    """

    class Downloads(list):
        body = _zip_bytes()
        error = None

    tags = Downloads()

    def fake_download(tag, zip_path):
        tags.append(tag)
        if tags.error is not None:
            raise tags.error
        Path(zip_path).write_bytes(tags.body)

    monkeypatch.setattr(firmware_updater, "download_zipball", fake_download)
    return tags


class FakeCatalog:
    """A firmware catalog that lists `REVOKED` as a revoked alpha, or raises `CatalogUnavailable` when `down` is set."""

    down = False

    def available(self):
        if self.down:
            raise CatalogUnavailable("both upstreams failed")
        alpha = [
            {"tag": ALPHA, "title": "Meshtastic Firmware 2.8.2.d5f1619 Alpha", "date": None, "revoked": False},
            {
                "tag": REVOKED,
                "title": "Meshtastic Firmware 2.8.2.4c89b69 Alpha (Revoked)",
                "date": None,
                "revoked": True,
            },
        ]
        return {"source": "github", "stable": [], "alpha": alpha}


@pytest.fixture
def installs(app, fake_pio, downloads):
    """Return the install service of the app, with a resolver that gives fixed firmware versions."""
    app.state.firmware_catalog = FakeCatalog()
    service = app.state.firmware_installs
    service.resolver = {"stable": STABLE, "alpha": ALPHA}.__getitem__
    return service


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
async def workers(app):
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


async def _rows(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM firmware_sources ORDER BY version")
        return [dict(row) for row in await cursor.fetchall()]


async def _install(admin, app, body):
    """Return the install after it ends, from a request to the install route."""
    resp = await admin.post("/api/v1/firmware/install", json=body)
    assert resp.status_code == 202, resp.text
    install = app.state.firmware_installs.get(resp.json()["install_id"])
    await asyncio.wait_for(install.wait(), 10)
    return install


async def _until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "the condition stayed false"
        await asyncio.sleep(0.02)


def _sse_events(text):
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


# FR-firmware-versions-3: install by channel or by exact tag


@pytest.mark.usefixtures("installs")
async def test_admin_installs_stable_and_alpha_and_the_list_shows_two_firmware_versions(app, admin, settings):
    stable = await _install(admin, app, {"channel": "stable"})
    alpha = await _install(admin, app, {"channel": "alpha"})

    listed = (await admin.get("/api/v1/firmware")).json()

    assert (stable.status, alpha.status) == ("ready", "ready")
    assert {(v["version"], v["channel"]) for v in listed} == {(STABLE, "stable"), (ALPHA, "alpha")}
    assert sorted(p.name for p in settings.firmware_dir.iterdir()) == [STABLE, ALPHA]
    for version in (STABLE, ALPHA):
        assert (settings.firmware_dir / version / "platformio.ini").is_file()
        # The fake `pio pkg install` writes this file, so the package install ran in this tree.
        assert (settings.firmware_dir / version / ".pio" / "libdeps" / "fake" / "installed").is_file()


@pytest.mark.usefixtures("installs")
async def test_install_writes_a_ready_record_with_the_path_and_the_disk_size(app, admin, settings):
    await _install(admin, app, {"channel": "stable"})

    [row] = await _rows(settings)

    assert row["path"] == str(settings.firmware_dir / STABLE)
    assert row["status"] == "ready"
    # The size counts the scan file that the install writes.
    scan_size = (settings.firmware_dir / STABLE / SCAN_FILE).stat().st_size
    assert row["size_bytes"] == len("[platformio]\n") + len("int main() {}\n") + len("fake\n") + scan_size
    assert row["installed_at"]


@pytest.mark.usefixtures("installs")
async def test_install_stores_the_userprefs_keys_that_the_firmware_source_reads(app, admin, settings, downloads):
    downloads.body = _zip_bytes(
        {
            "top/platformio.ini": "[platformio]\n",
            "top/userPrefs.jsonc": '{\n  // "USERPREFS_EVENT_MODE": "1",\n}\n',
            "top/src/mesh/NodeDB.cpp": "#ifdef USERPREFS_MESH_BEACON_MESSAGE\n#endif\n",
        }
    )

    await _install(admin, app, {"channel": "stable"})

    scan = json.loads((settings.firmware_dir / STABLE / SCAN_FILE).read_text())
    assert scan["keys"] == ["USERPREFS_EVENT_MODE", "USERPREFS_MESH_BEACON_MESSAGE"]


@pytest.mark.usefixtures("installs")
async def test_install_by_exact_tag_downloads_that_tag_with_channel_pinned(app, admin, downloads):
    install = await _install(admin, app, {"tag": PINNED})

    [listed] = (await admin.get("/api/v1/firmware")).json()

    assert install.status == "ready"
    assert downloads == [PINNED]
    assert (listed["version"], listed["channel"]) == (PINNED, "pinned")


# Spec 09 edge case: a pinned install of a revoked tag


@pytest.mark.usefixtures("installs")
async def test_pinned_install_of_a_revoked_tag_gives_a_warning_in_the_json_response(app, admin):
    resp = await admin.post("/api/v1/firmware/install", json={"tag": REVOKED})

    assert resp.status_code == 202
    assert resp.json()["warning"] == f"The upstream firmware list marks {REVOKED} as revoked."
    await asyncio.wait_for(app.state.firmware_installs.get(resp.json()["install_id"]).wait(), 10)


@pytest.mark.usefixtures("installs")
async def test_pinned_install_of_a_revoked_tag_shows_the_warning_in_the_install_panel(app, admin):
    resp = await admin.post("/api/v1/firmware/install", json={"tag": REVOKED}, headers={"HX-Request": "true"})
    install = app.state.firmware_installs.get(re.search(r'id="install-([0-9a-f]{32})"', resp.text)[1])
    await asyncio.wait_for(install.wait(), 10)
    ended = await admin.get(f"/api/v1/firmware/install/{install.install_id}", headers={"HX-Request": "true"})

    warning = (
        rf'class="alert alert-warning[^"]*" role="alert"[^>]*>The upstream firmware list marks {REVOKED} as revoked.<'
    )
    for panel in (resp.text, ended.text):
        assert re.search(warning, panel)


@pytest.mark.usefixtures("installs")
@pytest.mark.parametrize("down", [False, True], ids=["not_revoked", "catalog_down"])
async def test_pinned_install_without_a_revoked_mark_gives_no_warning(app, admin, down):
    app.state.firmware_catalog.down = down

    resp = await admin.post("/api/v1/firmware/install", json={"tag": PINNED})

    assert resp.status_code == 202
    assert resp.json()["warning"] == ""
    await asyncio.wait_for(app.state.firmware_installs.get(resp.json()["install_id"]).wait(), 10)


@pytest.mark.usefixtures("installs")
async def test_install_response_gives_the_install_id_and_the_progress_url(admin):
    resp = await admin.post("/api/v1/firmware/install", json={"channel": "stable"})

    body = resp.json()
    assert resp.status_code == 202
    assert re.fullmatch(r"[0-9a-f]{32}", body["install_id"])
    assert body["progress_url"] == f"/api/v1/firmware/install/{body['install_id']}/progress"


@pytest.mark.usefixtures("installs")
@pytest.mark.parametrize(
    "body",
    [
        {"tag": "v2.7.26"},
        {"tag": "v2.7.26.54E0D8D"},
        {"tag": "../v2.7.26.54e0d8d"},
        {"tag": "v2.7.26.54e0d8d\n"},
        {"channel": "beta"},
        {},
        {"channel": "stable", "tag": STABLE},
    ],
)
async def test_install_returns_422_for_a_body_without_one_valid_channel_or_tag(admin, settings, body):
    resp = await admin.post("/api/v1/firmware/install", json=body)

    assert resp.status_code == 422
    assert not settings.firmware_dir.exists() or not any(settings.firmware_dir.iterdir())


@pytest.mark.usefixtures("installs")
async def test_tag_without_hash_returns_422_with_a_field_error_for_tag(admin):
    resp = await admin.post("/api/v1/firmware/install", json={"tag": "v2.7.26"})

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "tag"]


@pytest.mark.usefixtures("installs")
async def test_install_requires_an_admin_session(client):
    resp = await client.post("/api/v1/firmware/install", json={"channel": "stable"})

    assert resp.status_code == 401


# FR-frontend-htmx-14: every admin route that changes state requires the CSRF token


@pytest.mark.usefixtures("installs")
@pytest.mark.parametrize("headers", [{}, {CSRF_HEADER: "wrong"}], ids=["no_token", "wrong_token"])
async def test_install_without_a_valid_csrf_token_returns_403_and_starts_no_install(
    app, client, settings, downloads, headers
):
    sign_in_admin(client, settings)

    resp = await client.post("/api/v1/firmware/install", json={"channel": "stable"}, headers=headers)

    assert resp.status_code == 403
    assert downloads == []
    assert app.state.firmware_installs._installs == {}


@pytest.mark.usefixtures("installs")
async def test_install_accepts_the_csrf_token_in_the_header(app, admin):
    install = await _install(admin, app, {"channel": "stable"})

    assert install.status == "ready"


@pytest.mark.usefixtures("installs")
async def test_install_of_an_installed_firmware_version_returns_409(app, admin, downloads):
    await _install(admin, app, {"channel": "stable"})

    resp = await admin.post("/api/v1/firmware/install", json={"tag": STABLE})

    assert resp.status_code == 409
    assert downloads == [STABLE]


@pytest.mark.usefixtures("installs")
async def test_second_install_of_a_firmware_version_returns_409_while_the_first_runs(app, admin, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_SLEEP", "1")
    first = await admin.post("/api/v1/firmware/install", json={"channel": "stable"})

    second = await admin.post("/api/v1/firmware/install", json={"tag": STABLE})

    assert second.status_code == 409
    await asyncio.wait_for(app.state.firmware_installs.get(first.json()["install_id"]).wait(), 10)


@pytest.mark.usefixtures("installs")
async def test_install_returns_502_when_github_gives_no_firmware_version(app, admin, settings):
    def unreachable(_channel):
        raise requests.ConnectionError("no route to host")

    app.state.firmware_installs.resolver = unreachable

    resp = await admin.post("/api/v1/firmware/install", json={"channel": "alpha"})

    assert resp.status_code == 502
    assert await _rows(settings) == []


@pytest.mark.usefixtures("installs")
async def test_install_refuses_a_channel_tag_that_is_not_a_firmware_version(app, admin, settings, downloads):
    app.state.firmware_installs.resolver = lambda _channel: "../escape"

    resp = await admin.post("/api/v1/firmware/install", json={"channel": "stable"})

    assert resp.status_code == 502
    assert downloads == []
    assert not (settings.firmware_dir.parent / "escape").exists()


# FR-firmware-versions-6: a failed install leaves no directory and no record


def _fail_by_pio(monkeypatch, downloads, settings):
    monkeypatch.setenv("FAKE_PIO_PKG_FAIL", "1")


def _fail_by_download(monkeypatch, downloads, settings):
    downloads.error = requests.ConnectionError("connection reset")


def _fail_by_zip_slip(monkeypatch, downloads, settings):
    downloads.body = _zip_bytes({"top/platformio.ini": "", "top/../../escape.txt": "x"})


def _fail_by_absolute_entry(monkeypatch, downloads, settings):
    downloads.body = _zip_bytes({"top/platformio.ini": "", "/tmp/escape.txt": "x"})


def _fail_by_damaged_archive(monkeypatch, downloads, settings):
    downloads.body = b"not a zip archive"


def _fail_by_full_disk(monkeypatch, downloads, settings):
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", full)


def _fail_by_timeout(monkeypatch, downloads, settings):
    monkeypatch.setenv("FAKE_PIO_PKG_SLEEP", "5")
    settings.install_timeout_seconds = 1


def _fail_by_directory_creation(monkeypatch, downloads, settings):
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(firmware_updater.tempfile, "mkdtemp", full)


def _fail_by_record_write(monkeypatch, downloads, settings):
    def locked(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(firmware_updater, "record_firmware_source", locked)


@pytest.mark.usefixtures("installs")
@pytest.mark.parametrize(
    "cause",
    [
        _fail_by_pio,
        _fail_by_download,
        _fail_by_zip_slip,
        _fail_by_absolute_entry,
        _fail_by_damaged_archive,
        _fail_by_full_disk,
        _fail_by_timeout,
        _fail_by_directory_creation,
        _fail_by_record_write,
    ],
    ids=lambda f: f.__name__.removeprefix("_fail_by_"),
)
async def test_failed_install_leaves_no_firmware_directory_and_no_record(app, admin, settings, request, cause):
    cause(request.getfixturevalue("monkeypatch"), request.getfixturevalue("downloads"), settings)

    install = await _install(admin, app, {"channel": "stable"})

    assert install.status == "failed"
    assert install.error
    assert not (settings.firmware_dir / STABLE).exists()
    # The staging directory of the install is gone too.
    assert list(settings.firmware_dir.iterdir()) == []
    assert await _rows(settings) == []
    assert (await admin.get("/api/v1/firmware")).json() == []


@pytest.mark.usefixtures("installs")
async def test_database_error_at_the_record_write_is_logged_and_ends_the_install_failed(
    app, admin, monkeypatch, caplog
):
    _fail_by_record_write(monkeypatch, None, None)

    install = await _install(admin, app, {"channel": "stable"})

    assert (install.status, install.error) == ("failed", "database is locked")
    assert f"Install of {STABLE} failed: database is locked" in caplog.text


@pytest.mark.usefixtures("installs")
async def test_failed_install_keeps_the_other_firmware_versions(app, admin, settings, monkeypatch):
    await _install(admin, app, {"channel": "stable"})
    monkeypatch.setenv("FAKE_PIO_PKG_FAIL", "1")

    await _install(admin, app, {"channel": "alpha"})

    assert [row["version"] for row in await _rows(settings)] == [STABLE]
    assert sorted(p.name for p in settings.firmware_dir.iterdir()) == [STABLE]


@pytest.mark.usefixtures("installs")
async def test_install_after_a_failed_install_of_the_same_version_succeeds(app, admin, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_FAIL", "1")
    await _install(admin, app, {"channel": "stable"})
    monkeypatch.delenv("FAKE_PIO_PKG_FAIL")

    install = await _install(admin, app, {"channel": "stable"})

    assert install.status == "ready"


@pytest.mark.usefixtures("installs")
async def test_pio_error_names_the_exit_code(app, admin, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_FAIL", "1")

    install = await _install(admin, app, {"channel": "stable"})

    assert install.error == "pio pkg install exited with code 1"


@pytest.mark.usefixtures("installs")
async def test_timeout_error_names_the_install_timeout(app, admin, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_SLEEP", "5")
    settings.install_timeout_seconds = 1

    install = await _install(admin, app, {"channel": "stable"})

    assert install.error == "pio pkg install timed out after 1 seconds"


def test_zip_slip_check_refuses_an_entry_outside_the_target(tmp_path):
    zip_path = tmp_path / "source.zip"
    zip_path.write_bytes(_zip_bytes({"top/../../escape.txt": "x"}))

    with pytest.raises(InstallFailed, match="escape"):
        extract_zipball(zip_path, tmp_path / "tree")

    assert not (tmp_path / "escape.txt").exists()


def test_zip_slip_check_refuses_a_sibling_directory_with_the_same_prefix(tmp_path):
    # A plain prefix check of `tree` accepts `tree-x`, so the check compares path parts.
    zip_path = tmp_path / "source.zip"
    zip_path.write_bytes(_zip_bytes({"../tree-x/file": "x"}))

    with pytest.raises(InstallFailed):
        extract_zipball(zip_path, tmp_path / "tree")


def test_extract_gives_the_single_top_directory(tmp_path):
    zip_path = tmp_path / "source.zip"
    zip_path.write_bytes(_zip_bytes())

    top = extract_zipball(zip_path, tmp_path / "tree")

    assert top == (tmp_path / "tree" / "top").resolve()
    assert (top / "src" / "main.cpp").read_text() == "int main() {}\n"


# FR-firmware-versions-5: the install runs as a background task with its own progress stream


@pytest.mark.usefixtures("installs")
async def test_install_goes_through_each_install_status_in_order(app, admin, monkeypatch):
    statuses = []
    set_status = Install.set_status

    def spy(self, status, error=""):
        # An ended install keeps its install status, so the spy records only the changes.
        if not self.ended:
            statuses.append(status)
        set_status(self, status, error)

    monkeypatch.setattr(Install, "set_status", spy)

    await _install(admin, app, {"channel": "stable"})

    assert statuses == ["downloading", "extracting", "installing packages", "ready"]


@pytest.mark.usefixtures("installs")
async def test_install_continues_after_the_request_ends(app, admin, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_SLEEP", "0.5")

    resp = await admin.post("/api/v1/firmware/install", json={"channel": "stable"})
    install = app.state.firmware_installs.get(resp.json()["install_id"])
    status_after_response = install.status
    await asyncio.wait_for(install.wait(), 10)

    assert status_after_response != "ready"
    assert install.status == "ready"


async def test_progress_events_send_each_install_status_and_end_after_ready():
    install = Install("a" * 32, STABLE, "stable")
    events = install_events(install)

    first = await anext(events)
    install.set_status("extracting")
    second = await anext(events)
    install.set_status("installing packages")
    install.set_status("ready")
    rest = [event async for event in events]

    statuses = [json.loads(e["data"])["status"] for e in [first, second, *rest]]
    assert statuses == ["queued", "extracting", "ready"]
    assert {e["event"] for e in [first, second, *rest]} == {"status"}


async def test_progress_event_of_a_failed_install_holds_the_error():
    install = Install("a" * 32, STABLE, "stable")
    install.set_status("failed", error="pio pkg install exited with code 1")

    [event] = [event async for event in install_events(install)]

    assert json.loads(event["data"]) == {
        "install_id": "a" * 32,
        "version": STABLE,
        "status": "failed",
        "error": "pio pkg install exited with code 1",
    }


@pytest.mark.usefixtures("installs")
async def test_progress_route_streams_the_install_status_until_the_install_ends(app, admin):
    install = await _install(admin, app, {"channel": "stable"})

    stream = await admin.get(f"/api/v1/firmware/install/{install.install_id}/progress")

    assert _sse_events(stream.text) == [
        ("status", {"install_id": install.install_id, "version": STABLE, "status": "ready", "error": ""})
    ]


async def test_progress_route_returns_404_for_an_unknown_install(admin):
    resp = await admin.get(f"/api/v1/firmware/install/{'0' * 32}/progress")

    assert resp.status_code == 404


@pytest.mark.usefixtures("installs")
@pytest.mark.parametrize("suffix", ["", "/progress"], ids=["status", "progress"])
async def test_install_status_routes_require_an_admin_session(app, admin, client, suffix):
    install = await _install(admin, app, {"channel": "stable"})
    client.cookies.clear()

    resp = await client.get(f"/api/v1/firmware/install/{install.install_id}{suffix}")

    assert resp.status_code == 401


# FR-firmware-versions-3: one install runs at a time


@pytest.mark.usefixtures("installs")
async def test_second_install_waits_in_queued_until_the_first_install_ends(app, admin, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_SLEEP", "0.5")
    first = app.state.firmware_installs.get(
        (await admin.post("/api/v1/firmware/install", json={"channel": "stable"})).json()["install_id"]
    )
    await _until(lambda: first.status == "installing packages")

    resp = await admin.post("/api/v1/firmware/install", json={"channel": "alpha"})
    second = app.state.firmware_installs.get(resp.json()["install_id"])
    await asyncio.sleep(0.1)
    second_while_first_runs = second.status
    progress = await admin.get(f"/api/v1/firmware/install/{second.install_id}")
    await asyncio.wait_for(second.wait(), 10)

    assert second_while_first_runs == "queued"
    assert progress.json()["status"] == "queued"
    assert first.status == "ready"
    assert second.status == "ready"


@pytest.mark.usefixtures("installs")
async def test_cancel_of_a_queued_install_ends_it_failed(app, admin, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_SLEEP", "0.5")
    await admin.post("/api/v1/firmware/install", json={"channel": "stable"})
    resp = await admin.post("/api/v1/firmware/install", json={"channel": "alpha"})
    queued = app.state.firmware_installs.get(resp.json()["install_id"])
    await asyncio.sleep(0.1)

    queued.task.cancel()
    await asyncio.wait_for(queued.wait(), 5)

    assert queued.status == "failed"


@pytest.mark.usefixtures("installs")
async def test_installs_never_run_pio_pkg_install_at_the_same_time(app, admin, monkeypatch):
    running = []
    overlaps = []
    run = firmware_updater.FirmwareInstalls._pio_pkg_install

    async def counted(self, tree):
        running.append(tree)
        overlaps.append(len(running))
        try:
            await run(self, tree)
        finally:
            running.remove(tree)

    monkeypatch.setattr(firmware_updater.FirmwareInstalls, "_pio_pkg_install", counted)
    monkeypatch.setenv("FAKE_PIO_PKG_SLEEP", "0.3")
    responses = [await admin.post("/api/v1/firmware/install", json={"channel": c}) for c in ("stable", "alpha")]
    for resp in responses:
        await asyncio.wait_for(app.state.firmware_installs.get(resp.json()["install_id"]).wait(), 10)

    assert overlaps == [1, 1]


# FR-firmware-versions-7: the install timeout


def test_install_timeout_defaults_to_3600_seconds(settings):
    assert settings.install_timeout_seconds == 3600


def test_install_timeout_reads_mtfw_install_timeout_seconds(settings_env, monkeypatch):
    monkeypatch.setenv("MTFW_INSTALL_TIMEOUT_SECONDS", "120")

    assert load_settings().install_timeout_seconds == 120


# FR-firmware-versions-8: removal


@pytest.mark.usefixtures("installs")
async def test_remove_deletes_the_directory_and_the_record(app, admin, settings):
    await _install(admin, app, {"channel": "stable"})
    await _install(admin, app, {"channel": "alpha"})

    resp = await admin.delete(f"/api/v1/firmware/{ALPHA}")

    assert resp.status_code == 204
    assert not (settings.firmware_dir / ALPHA).exists()
    assert [row["version"] for row in await _rows(settings)] == [STABLE]


async def test_remove_returns_404_for_a_firmware_version_that_is_not_installed(admin):
    resp = await admin.delete(f"/api/v1/firmware/{STABLE}")

    assert resp.status_code == 404


@pytest.mark.usefixtures("firmware_source")
async def test_remove_requires_an_admin_session(client, settings):
    resp = await client.delete(f"/api/v1/firmware/{STABLE}")

    assert resp.status_code == 401
    assert (settings.firmware_dir / STABLE).is_dir()


@pytest.mark.usefixtures("firmware_source")
@pytest.mark.parametrize("headers", [{}, {CSRF_HEADER: "wrong"}], ids=["no_token", "wrong_token"])
async def test_remove_without_a_valid_csrf_token_returns_403_and_keeps_the_firmware_source(client, settings, headers):
    sign_in_admin(client, settings)

    resp = await client.delete(f"/api/v1/firmware/{STABLE}", headers=headers)

    assert resp.status_code == 403
    assert (settings.firmware_dir / STABLE).is_dir()
    assert [row["version"] for row in await _rows(settings)] == [STABLE]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_remove_request_during_a_build_of_that_version_returns_409(app, admin, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    ctx = await app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await _until(lambda: ctx.status == "compiling")

    resp = await asyncio.wait_for(admin.delete(f"/api/v1/firmware/{STABLE}"), 0.5)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "A build uses this firmware version. Try again when it ends."
    assert (settings.firmware_dir / STABLE).is_dir()
    assert [row["version"] for row in await _rows(settings)] == [STABLE]
    await _until(lambda: ctx.ended)
    assert ctx.status == "complete", ctx.error


@pytest.mark.usefixtures("firmware_source")
async def test_remove_request_returns_409_while_a_build_of_the_default_version_is_queued(app, admin, settings):
    # No build worker runs, so the build stays queued, and it reads the default firmware version when it starts.
    ctx = await app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)

    resp = await admin.delete(f"/api/v1/firmware/{STABLE}")

    assert ctx.status == "queued"
    assert resp.status_code == 409


@pytest.mark.usefixtures("installs", "firmware_source")
async def test_remove_of_another_version_succeeds_while_a_build_is_queued(app, admin, settings):
    await _install(admin, app, {"channel": "alpha"})
    await app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)

    resp = await admin.delete(f"/api/v1/firmware/{ALPHA}")

    assert resp.status_code == 204
    assert not (settings.firmware_dir / ALPHA).exists()


@pytest.mark.usefixtures("firmware_source")
async def test_remove_waits_for_the_firmware_lock(app, admin, settings):
    lock = app.state.build_queue.firmware_lock
    await lock.acquire()
    remove = asyncio.create_task(admin.delete(f"/api/v1/firmware/{STABLE}"))
    await asyncio.sleep(0.3)
    exists_while_locked = (settings.firmware_dir / STABLE).is_dir()
    lock.release()
    resp = await asyncio.wait_for(remove, 5)

    assert exists_while_locked is True
    assert resp.status_code == 204
    assert not (settings.firmware_dir / STABLE).exists()


# FR-build-pipeline-repair-16: the move into `<tag>/` waits for the firmware lock


@pytest.mark.usefixtures("installs")
async def test_install_moves_the_firmware_source_only_after_the_firmware_lock_is_free(app, admin, settings):
    lock = app.state.build_queue.firmware_lock
    await lock.acquire()
    resp = await admin.post("/api/v1/firmware/install", json={"channel": "stable"})
    install = app.state.firmware_installs.get(resp.json()["install_id"])
    # The fake `pio pkg install` writes its marker last, so the install waits for the lock after it.
    await _until(lambda: list(settings.firmware_dir.glob(".install-*/tree/*/.pio/libdeps/fake/installed")))
    await asyncio.sleep(0.2)
    state_while_locked = (install.status, (settings.firmware_dir / STABLE).exists(), await _rows(settings))
    lock.release()
    await asyncio.wait_for(install.wait(), 10)

    assert state_while_locked == ("installing packages", False, [])
    assert install.status == "ready"


@pytest.mark.usefixtures("installs", "firmware_source", "workers")
async def test_install_moves_the_firmware_source_only_after_the_running_build_ends(app, admin, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    statuses_at_move = []
    record = firmware_updater.record_firmware_source

    def record_after_status(*args):
        statuses_at_move.append(ctx.status)
        record(*args)

    monkeypatch.setattr(firmware_updater, "record_firmware_source", record_after_status)
    ctx = await app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await _until(lambda: ctx.status == "compiling")

    install = await _install(admin, app, {"channel": "alpha"})

    assert install.status == "ready", install.error
    assert statuses_at_move == ["complete"]


@pytest.mark.usefixtures("installs", "firmware_source", "workers")
async def test_remove_waits_until_the_running_build_ends(app, admin, settings, monkeypatch):
    await _install(admin, app, {"channel": "alpha"})
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    ctx = await app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await _until(lambda: ctx.status == "compiling")

    resp = await admin.delete(f"/api/v1/firmware/{ALPHA}")

    assert resp.status_code == 204
    assert ctx.status == "complete", ctx.error


@pytest.mark.usefixtures("installs", "firmware_source", "workers")
async def test_no_build_starts_while_an_install_writes_its_record(app, admin, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    record = firmware_updater.record_firmware_source

    def slow_record(*args):
        started.set()
        release.wait(5)
        record(*args)

    monkeypatch.setattr(firmware_updater, "record_firmware_source", slow_record)
    resp = await admin.post("/api/v1/firmware/install", json={"channel": "alpha"})
    await asyncio.to_thread(started.wait, 5)
    ctx = await app.state.build_queue.submit(app.state.device_registry.get("heltec-v3"), CONFIG)
    await asyncio.sleep(0.3)
    status_during_install = ctx.status
    release.set()
    await asyncio.wait_for(app.state.firmware_installs.get(resp.json()["install_id"]).wait(), 10)
    await _until(lambda: ctx.ended)

    assert status_during_install == "queued"
    assert ctx.status == "complete", ctx.error


# The old update route


@pytest.mark.usefixtures("installs")
async def test_update_route_installs_the_newest_stable_version_and_waits_for_it(admin, settings):
    resp = await admin.post("/api/v1/update-firmware")

    assert resp.json() == {"success": True, "message": f"Firmware version {STABLE} is installed."}
    [row] = await _rows(settings)
    assert (row["version"], row["channel"]) == (STABLE, "stable")


@pytest.mark.usefixtures("installs", "firmware_source")
async def test_update_route_succeeds_without_an_install_when_stable_is_installed(admin, downloads):
    resp = await admin.post("/api/v1/update-firmware")

    assert resp.json()["success"] is True
    assert downloads == []


@pytest.mark.usefixtures("installs")
async def test_update_route_gives_the_install_error_when_the_install_fails(admin, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_PKG_FAIL", "1")

    resp = await admin.post("/api/v1/update-firmware")

    assert resp.json() == {"success": False, "error": "pio pkg install exited with code 1"}


@pytest.mark.usefixtures("installs")
async def test_update_route_requires_an_admin_session(client):
    resp = await client.post("/api/v1/update-firmware")

    assert resp.status_code == 401


# The GitHub request headers


def test_github_requests_send_the_user_agent_and_the_api_version(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    headers = firmware_updater.github_headers()

    assert headers["User-Agent"] == "MTFWBuilder"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert "Authorization" not in headers


def test_github_requests_send_the_token_of_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    assert firmware_updater.github_headers()["Authorization"] == "Bearer test-token"


def test_download_writes_the_zipball_of_the_tag(tmp_path, monkeypatch):
    calls = []

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"zip "
            yield b"bytes"

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Stream()

    monkeypatch.setattr(firmware_updater.requests, "get", fake_get)

    firmware_updater.download_zipball(STABLE, tmp_path / "source.zip")

    assert (tmp_path / "source.zip").read_bytes() == b"zip bytes"
    assert calls[0][0] == f"https://api.github.com/repos/meshtastic/firmware/zipball/{STABLE}"
    assert calls[0][1]["headers"]["User-Agent"] == "MTFWBuilder"
