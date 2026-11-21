"""Tests for the firmware source records, the per-tag directories and the list route (feature 09)."""

import asyncio
import json
import time

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import find_firmware_source, init_db, record_firmware_source
from mtfwbuilder.main import lifespan
from mtfwbuilder.services.firmware_catalog import CatalogUnavailable, FirmwareCatalog
from mtfwbuilder.services.firmware_updater import import_old_firmware_source, remove_unrecorded_sources
from mtfwbuilder.services.userprefs_keys import SCAN_FILE
from tests.conftest import FIXTURE_REGISTRY

STABLE = "v2.7.26.54e0d8d"
ALPHA = "v2.8.2.d5f1619"
OLD_STABLE = "v2.7.15.567b8ea"
CONFIG = json.dumps({"owner_long_name": "TestNode"})

# The startup import runs `pio project config`, so no test here reaches a real PlatformIO.
pytestmark = pytest.mark.usefixtures("fake_pio")


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
    # The startup import asks the firmware catalog, so the lifespan and a direct call get the same answer.
    monkeypatch.setattr(FirmwareCatalog, "resolve", lambda _self, _channel: version)


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


async def test_default_is_the_stable_firmware_source_with_the_highest_version_number(settings):
    await init_db(settings)
    # The older version is the newer install, so an order by `installed_at` would choose it.
    record_firmware_source(settings.database_path, _record(settings, STABLE, installed_at="2026-01-01T00:00:00"))
    record_firmware_source(settings.database_path, _record(settings, OLD_STABLE, installed_at="2026-06-01T00:00:00"))

    source = await find_firmware_source(settings.database_path)

    assert source["version"] == STABLE


async def test_default_compares_version_numbers_as_numbers(settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, "v2.10.0.aaaaaaa"))
    record_firmware_source(settings.database_path, _record(settings, "v2.9.9.bbbbbbb"))

    source = await find_firmware_source(settings.database_path)

    assert source["version"] == "v2.10.0.aaaaaaa"


async def test_default_without_a_stable_source_is_the_highest_version_number_of_any_release_channel(settings):
    await init_db(settings)
    record_firmware_source(
        settings.database_path, _record(settings, ALPHA, channel="alpha", installed_at="2026-01-01T00:00:00")
    )
    record_firmware_source(
        settings.database_path, _record(settings, OLD_STABLE, channel="pinned", installed_at="2026-06-01T00:00:00")
    )

    source = await find_firmware_source(settings.database_path)

    assert source["version"] == ALPHA


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
    # The size counts the scan file that the import writes.
    scan_size = (source_dir / SCAN_FILE).stat().st_size
    assert row["size_bytes"] == len("[platformio]\n") + len("int main() {}\n") + scan_size


async def test_import_stores_the_userprefs_keys_of_the_old_firmware_source(settings, monkeypatch):
    await init_db(settings)
    _old_layout(settings, STABLE)
    (settings.firmware_dir / "userPrefs.jsonc").write_text('{\n  // "USERPREFS_EVENT_MODE": "1",\n}\n')
    _newest_stable(monkeypatch, STABLE)

    import_old_firmware_source(settings, FirmwareCatalog().resolve)

    scan = json.loads((settings.firmware_dir / STABLE / SCAN_FILE).read_text())
    assert scan["keys"] == ["USERPREFS_EVENT_MODE"]


async def test_import_gives_channel_pinned_when_the_tag_is_not_the_newest_stable(settings, monkeypatch):
    await init_db(settings)
    _old_layout(settings, OLD_STABLE)
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings, FirmwareCatalog().resolve) == OLD_STABLE

    [row] = await _rows(settings)
    assert row["channel"] == "pinned"


async def test_import_gives_channel_pinned_when_the_upstream_firmware_list_is_unreachable(settings, monkeypatch):
    await init_db(settings)
    _old_layout(settings, STABLE)

    def unreachable(_self, _channel):
        raise CatalogUnavailable("both upstreams failed")

    monkeypatch.setattr(FirmwareCatalog, "resolve", unreachable)

    import_old_firmware_source(settings, FirmwareCatalog().resolve)

    [row] = await _rows(settings)
    assert row["channel"] == "pinned"


@pytest.mark.parametrize("version_text", ["Unknown", "v2.7.26", "../v2.7.26.54e0d8d"])
async def test_import_changes_nothing_when_the_version_file_holds_no_tag(settings, monkeypatch, version_text):
    await init_db(settings)
    _old_layout(settings, version_text)
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings, FirmwareCatalog().resolve) is None

    assert (settings.firmware_dir / "platformio.ini").is_file()
    assert await _rows(settings) == []


async def test_import_changes_nothing_when_the_firmware_dir_holds_tag_directories(settings, monkeypatch):
    await init_db(settings)
    (settings.firmware_dir / STABLE).mkdir(parents=True)
    (settings.firmware_dir / STABLE / "platformio.ini").write_text("[platformio]\n")
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings, FirmwareCatalog().resolve) is None

    assert await _rows(settings) == []


async def test_import_changes_nothing_when_the_old_tree_holds_the_tag_directory(settings, monkeypatch):
    await init_db(settings)
    _old_layout(settings, STABLE)
    (settings.firmware_dir / STABLE).mkdir()
    _newest_stable(monkeypatch, STABLE)

    assert import_old_firmware_source(settings, FirmwareCatalog().resolve) is None

    assert (settings.firmware_dir / "platformio.ini").is_file()
    assert await _rows(settings) == []


async def test_startup_scrubs_an_old_firmware_source_that_the_import_leaves_in_place(app, settings, monkeypatch):
    _old_layout(settings, "Unknown")
    (settings.firmware_dir / "userPrefs.jsonc").write_text(CONFIG)
    env_build = settings.firmware_dir / ".pio" / "build" / "heltec-v3"
    env_build.mkdir(parents=True)
    _newest_stable(monkeypatch, STABLE)

    async with lifespan(app):
        pass

    assert (settings.firmware_dir / "platformio.ini").is_file()
    assert not (settings.firmware_dir / "userPrefs.jsonc").exists()
    assert not env_build.exists()


async def test_startup_scrubs_each_firmware_source(app, settings):
    env_builds = []
    for version in (STABLE, ALPHA):
        source_dir = settings.firmware_dir / version
        (source_dir / "userPrefs.jsonc").parent.mkdir(parents=True)
        (source_dir / "userPrefs.jsonc").write_text(CONFIG)
        env_builds.append(source_dir / ".pio" / "build" / "heltec-v3")
        env_builds[-1].mkdir(parents=True)

    async with lifespan(app):
        pass

    assert not any((settings.firmware_dir / v / "userPrefs.jsonc").exists() for v in (STABLE, ALPHA))
    assert not any(env_build.exists() for env_build in env_builds)


@pytest.mark.usefixtures("fake_pio")
async def test_build_runs_in_the_tag_directory_and_records_its_firmware_version(app, client, settings):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, STABLE), FIXTURE_REGISTRY)
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


# Startup: a stopped process between the rename and the record write


async def test_startup_removes_a_tag_directory_without_a_record_and_logs_it(settings, caplog):
    await init_db(settings)
    record_firmware_source(settings.database_path, _record(settings, STABLE))
    orphan = settings.firmware_dir / ALPHA
    (orphan / "src").mkdir(parents=True)

    await remove_unrecorded_sources(settings)

    assert sorted(p.name for p in settings.firmware_dir.iterdir()) == [STABLE]
    assert f"Removed the firmware source {orphan}, which has no record" in caplog.text


async def test_startup_keeps_a_directory_that_is_not_a_tag(settings):
    await init_db(settings)
    (settings.firmware_dir / "src").mkdir(parents=True)

    await remove_unrecorded_sources(settings)

    assert (settings.firmware_dir / "src").is_dir()


async def test_startup_removes_a_leftover_import_directory_and_logs_it(settings, caplog):
    await init_db(settings)
    leftover = settings.firmware_dir.with_name(f"{settings.firmware_dir.name}.import")
    (leftover / "src").mkdir(parents=True)

    await remove_unrecorded_sources(settings)

    assert not leftover.exists()
    assert f"Removed the directory {leftover} of a stopped import" in caplog.text


async def test_lifespan_removes_a_tag_directory_without_a_record(app, settings, monkeypatch):
    _newest_stable(monkeypatch, STABLE)
    (settings.firmware_dir / ALPHA).mkdir(parents=True)

    async with lifespan(app):
        pass

    assert not (settings.firmware_dir / ALPHA).exists()
