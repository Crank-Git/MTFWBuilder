"""Tests for the firmware source records, the per-tag directories and the list route (feature 09)."""

import asyncio
import io
import json
import subprocess
import time
import zipfile
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import find_firmware_source, init_db, record_firmware_source
from mtfwbuilder.main import lifespan
from mtfwbuilder.services import firmware_updater
from mtfwbuilder.services.firmware_updater import import_old_firmware_source, update_firmware

STABLE = "v2.7.26.54e0d8d"
ALPHA = "v2.8.2.d5f1619"
OLD_STABLE = "v2.7.15.567b8ea"
CONFIG = json.dumps({"owner_long_name": "TestNode"})


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _record(settings, version, channel="stable", installed_at="2026-11-01T10:00:00+00:00", size_bytes=1024):
    path = settings.firmware_dir / version
    path.mkdir(parents=True, exist_ok=True)
    return {
        "version": version,
        "channel": channel,
        "path": str(path),
        "installed_at": installed_at,
        "size_bytes": size_bytes,
        "status": "ready",
    }


async def _rows(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM firmware_sources ORDER BY version")
        return [dict(row) for row in await cursor.fetchall()]


def _newest_stable(monkeypatch, version):
    monkeypatch.setattr(firmware_updater, "get_latest_release_info", lambda: {"version": version})


def _old_layout(settings, version_text):
    """Write the firmware source layout from before feature 09: the source is `<firmware_dir>` itself."""
    settings.firmware_dir.mkdir(parents=True)
    (settings.firmware_dir / "platformio.ini").write_text("[platformio]\n")
    (settings.firmware_dir / "src").mkdir()
    (settings.firmware_dir / "src" / "main.cpp").write_text("int main() {}\n")
    (settings.base_dir / "firmware_version.txt").write_text(
        f"Version: {version_text}\nUpdated: 2026-10-01T12:00:00+00:00\n"
    )


# The firmware_sources table


async def test_init_db_creates_the_firmware_sources_table_with_the_spec_columns(settings):
    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("PRAGMA table_info(firmware_sources)")
        columns = {row[1]: (row[2], row[5]) for row in await cursor.fetchall()}

    assert columns == {
        "version": ("TEXT", 1),
        "channel": ("TEXT", 0),
        "path": ("TEXT", 0),
        "installed_at": ("TIMESTAMP", 0),
        "size_bytes": ("INTEGER", 0),
        "status": ("TEXT", 0),
    }


async def test_record_of_an_installed_firmware_version_replaces_its_old_record(settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, STABLE, size_bytes=1))

    record_firmware_source(settings.database_path, _record(settings, STABLE, size_bytes=2))

    assert [row["size_bytes"] for row in await _rows(settings)] == [2]


# The firmware source helper


async def test_helper_returns_the_only_installed_firmware_source_by_default(settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, ALPHA, channel="alpha"))

    source = await find_firmware_source(settings.database_path)

    assert source["version"] == ALPHA
    assert source["path"] == str(settings.firmware_dir / ALPHA)


async def test_helper_returns_the_newest_stable_firmware_source_by_default(settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, OLD_STABLE, installed_at="2026-01-01T00:00:00"))
    record_firmware_source(settings.database_path, _record(settings, STABLE, installed_at="2026-06-01T00:00:00"))
    record_firmware_source(
        settings.database_path, _record(settings, ALPHA, channel="alpha", installed_at="2026-09-01T00:00:00")
    )

    source = await find_firmware_source(settings.database_path)

    assert source["version"] == STABLE


async def test_helper_returns_the_firmware_source_of_the_requested_firmware_version(settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, STABLE))
    record_firmware_source(settings.database_path, _record(settings, ALPHA, channel="alpha"))

    source = await find_firmware_source(settings.database_path, ALPHA)

    assert source["path"] == str(settings.firmware_dir / ALPHA)


async def test_helper_returns_none_when_no_firmware_version_is_installed(settings):
    await init_db(settings)

    assert await find_firmware_source(settings.database_path) is None
    assert await find_firmware_source(settings.database_path, STABLE) is None


# FR-firmware-versions-4


def _zipball(top_dir="meshtastic-firmware-54e0d8d"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{top_dir}/platformio.ini", "[platformio]\n")
        zf.writestr(f"{top_dir}/src/main.cpp", "int main() {}\n")
    return buffer.getvalue()


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.body


@pytest.fixture
def upstream(monkeypatch):
    """Replace GitHub and `pio pkg install` with fakes, and return the `pio` working directories."""
    release = {"version": STABLE, "published_date": "2026-11-01", "release_data": {"zipball_url": "https://zip"}}
    monkeypatch.setattr(firmware_updater, "get_latest_release_info", lambda: release)
    monkeypatch.setattr(firmware_updater.requests, "get", lambda *_args, **_kwargs: _Response(_zipball()))
    pio_dirs = []

    def fake_run(cmd, cwd, **_kwargs):
        pio_dirs.append(Path(cwd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(firmware_updater.subprocess, "run", fake_run)
    return pio_dirs


async def test_update_installs_the_firmware_version_into_its_tag_directory(settings, upstream):
    await init_db(settings)

    assert update_firmware(settings) is True

    source_dir = settings.firmware_dir / STABLE
    assert (source_dir / "platformio.ini").is_file()
    assert (source_dir / "src" / "main.cpp").is_file()
    assert upstream == [source_dir]
    assert not (settings.firmware_dir / "platformio.ini").exists()


async def test_update_keeps_the_directory_of_another_firmware_version(settings, upstream):
    await init_db(settings)
    other = settings.firmware_dir / ALPHA
    other.mkdir(parents=True)
    (other / "platformio.ini").write_text("[alpha]\n")

    update_firmware(settings)

    assert (other / "platformio.ini").read_text() == "[alpha]\n"
    assert (settings.firmware_dir / STABLE / "platformio.ini").is_file()


async def test_update_writes_a_ready_stable_record_with_the_disk_size(settings, upstream):
    await init_db(settings)

    update_firmware(settings)

    [row] = await _rows(settings)
    assert row["version"] == STABLE
    assert row["channel"] == "stable"
    assert row["path"] == str(settings.firmware_dir / STABLE)
    assert row["status"] == "ready"
    assert row["size_bytes"] == len("[platformio]\n") + len("int main() {}\n")
    assert row["installed_at"]


async def test_update_refuses_a_release_whose_tag_is_not_a_firmware_version(settings, upstream, monkeypatch):
    await init_db(settings)
    release = {"version": "../escape", "published_date": "", "release_data": {"zipball_url": "https://zip"}}
    monkeypatch.setattr(firmware_updater, "get_latest_release_info", lambda: release)

    assert update_firmware(settings) is False

    assert not (settings.base_dir / "escape").exists()
    assert await _rows(settings) == []


async def test_startup_moves_an_old_firmware_source_into_its_tag_directory(app, settings, monkeypatch):
    _old_layout(settings, STABLE)
    _newest_stable(monkeypatch, STABLE)

    async with lifespan(app):
        pass

    source_dir = settings.firmware_dir / STABLE
    assert (source_dir / "platformio.ini").is_file()
    assert (source_dir / "src" / "main.cpp").is_file()
    assert sorted(p.name for p in settings.firmware_dir.iterdir()) == [STABLE]
    [row] = await _rows(settings)
    assert row["version"] == STABLE
    assert row["channel"] == "stable"
    assert row["path"] == str(source_dir)
    assert row["status"] == "ready"
    assert row["installed_at"] == "2026-10-01T12:00:00+00:00"
    assert row["size_bytes"] == len("[platformio]\n") + len("int main() {}\n")


async def test_import_gives_channel_pinned_when_the_tag_is_not_the_newest_stable(settings, monkeypatch):
    await init_db(settings)
    _old_layout(settings, OLD_STABLE)
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings) == OLD_STABLE

    [row] = await _rows(settings)
    assert row["channel"] == "pinned"


async def test_import_gives_channel_pinned_when_github_is_unreachable(settings, monkeypatch):
    await init_db(settings)
    _old_layout(settings, STABLE)
    monkeypatch.setattr(firmware_updater, "get_latest_release_info", lambda: None)

    import_old_firmware_source(settings)

    [row] = await _rows(settings)
    assert row["channel"] == "pinned"


@pytest.mark.parametrize("version_text", ["Unknown", "v2.7.26", "../v2.7.26.54e0d8d"])
async def test_import_changes_nothing_when_the_version_file_holds_no_tag(settings, monkeypatch, version_text):
    await init_db(settings)
    _old_layout(settings, version_text)
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings) is None

    assert (settings.firmware_dir / "platformio.ini").is_file()
    assert await _rows(settings) == []


async def test_import_changes_nothing_when_the_firmware_dir_holds_tag_directories(settings, monkeypatch):
    await init_db(settings)
    (settings.firmware_dir / STABLE).mkdir(parents=True)
    (settings.firmware_dir / STABLE / "platformio.ini").write_text("[platformio]\n")
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings) is None

    assert await _rows(settings) == []


async def test_import_changes_nothing_when_the_old_tree_holds_the_tag_directory(settings, monkeypatch):
    await init_db(settings)
    _old_layout(settings, STABLE)
    (settings.firmware_dir / STABLE).mkdir()
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings) is None

    assert (settings.firmware_dir / "platformio.ini").is_file()
    assert await _rows(settings) == []


@pytest.mark.usefixtures("fake_pio")
async def test_build_runs_in_the_tag_directory_and_records_its_firmware_version(app, client, settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, STABLE))
    app.state.build_queue.start()

    resp = await client.post(
        "/api/v1/build-firmware",
        data={"variant": "heltec-v3", "config_source": "current", "config_json": CONFIG},
    )
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    deadline = time.monotonic() + 10
    while not ctx.ended:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)
    await app.state.build_queue.stop()

    assert ctx.status == "complete", ctx.error
    assert ctx.firmware_version == STABLE
    assert (settings.firmware_dir / STABLE / ".pio" / "build").is_dir()


# FR-firmware-versions-9


async def test_list_route_returns_tag_channel_install_date_disk_size_and_status(client, settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, STABLE, installed_at="2026-11-01T10:00:00+00:00"))
    record_firmware_source(
        settings.database_path,
        _record(settings, ALPHA, channel="alpha", installed_at="2026-11-02T10:00:00+00:00", size_bytes=2048),
    )

    resp = await client.get("/api/v1/firmware")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "version": ALPHA,
            "channel": "alpha",
            "installed_at": "2026-11-02T10:00:00Z",
            "size_bytes": 2048,
            "status": "ready",
        },
        {
            "version": STABLE,
            "channel": "stable",
            "installed_at": "2026-11-01T10:00:00Z",
            "size_bytes": 1024,
            "status": "ready",
        },
    ]


async def test_list_route_returns_an_empty_list_when_no_firmware_version_is_installed(client):
    resp = await client.get("/api/v1/firmware")

    assert resp.status_code == 200
    assert resp.json() == []
