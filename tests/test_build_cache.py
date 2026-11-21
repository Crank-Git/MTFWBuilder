"""Tests for the build cache (feature 14, FR-fleet-builds-5 to FR-fleet-builds-9, criterion 3)."""

import asyncio
import json
import shutil
import time

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.config import load_settings
from mtfwbuilder.services import build_cache
from mtfwbuilder.services.cleanup_service import cleanup_every_interval
from tests.conftest import FIRMWARE_VERSION, fixture_variant

PSK = "deadbeef" * 8
CONFIG = json.dumps({"USERPREFS_CONFIG_OWNER_LONG_NAME": "Cache Node", "USERPREFS_CHANNEL_0_PSK": PSK})
ENDED = {"complete", "failed", "cancelled"}


@pytest.fixture
async def workers(app):
    """Start the build workers of the app, and stop them after the test."""
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _wait_for(ctx, statuses, timeout=10.0):
    deadline = time.monotonic() + timeout
    while ctx.status not in statuses:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)


async def _complete_build(build_queue, env="heltec-v3", config=CONFIG):
    ctx = await build_queue.submit(fixture_variant(env), config)
    await _wait_for(ctx, ENDED)
    assert ctx.status == "complete", ctx.error
    return ctx


async def _entries(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM cache_entries")
        return [dict(row) for row in await cursor.fetchall()]


async def _set_last_use(settings, config_hash, last_used_at):
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("UPDATE cache_entries SET last_used_at = ? WHERE config_hash = ?", (last_used_at, config_hash))
        await db.commit()


# FR-fleet-builds-5


def test_config_hash_is_the_same_for_the_same_keys_in_another_order():
    first = json.dumps({"USERPREFS_A": "1", "USERPREFS_B": "2"})
    second = json.dumps({"USERPREFS_B": "2", "USERPREFS_A": "1"}, indent=4)

    assert build_cache.config_hash("key", FIRMWARE_VERSION, "heltec-v3", first) == build_cache.config_hash(
        "key", FIRMWARE_VERSION, "heltec-v3", second
    )


@pytest.mark.parametrize(
    "other",
    [
        ("key", "v2.8.2.d5f1619", "heltec-v3", CONFIG),
        ("key", FIRMWARE_VERSION, "rak4631", CONFIG),
        ("key", FIRMWARE_VERSION, "heltec-v3", json.dumps({"USERPREFS_CONFIG_OWNER_LONG_NAME": "Other"})),
        ("other-key", FIRMWARE_VERSION, "heltec-v3", CONFIG),
    ],
    ids=["firmware version", "variant", "userPrefs file", "secret key"],
)
def test_config_hash_changes_when_one_input_changes(other):
    assert build_cache.config_hash("key", FIRMWARE_VERSION, "heltec-v3", CONFIG) != build_cache.config_hash(*other)


def test_config_hash_of_a_userprefs_file_that_is_not_json_uses_the_text():
    assert build_cache.config_hash("key", FIRMWARE_VERSION, "heltec-v3", "{ not json") != build_cache.config_hash(
        "key", FIRMWARE_VERSION, "heltec-v3", "{ not json either"
    )


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_build_record_holds_the_config_hash_of_version_variant_and_userprefs_file(app, settings):
    ctx = await _complete_build(app.state.build_queue)

    expected = build_cache.config_hash(settings.secret_key, FIRMWARE_VERSION, "heltec-v3", CONFIG)
    assert ctx.record()["config_hash"] == expected


# FR-fleet-builds-6, FR-fleet-builds-7, criterion 3


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_second_build_with_the_same_config_completes_in_under_1_second(app, monkeypatch):
    # The fake `pio` waits 1.5 seconds, so only a build from the build cache completes in under 1 second.
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1.5")
    first = await _complete_build(app.state.build_queue)

    start = time.monotonic()
    second = await app.state.build_queue.submit(fixture_variant("heltec-v3"), CONFIG)
    elapsed = time.monotonic() - start

    assert second.status == "complete"
    assert elapsed < 1
    assert second.build_id != first.build_id
    assert [a.model_dump() for a in second.artifacts] == [a.model_dump() for a in first.artifacts]
    assert second.download_url.startswith(f"/api/v1/builds/{second.build_id}/artifacts/")


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_cache_hit_artifact_downloads_from_the_directory_of_its_own_build(app, client):
    first = await _complete_build(app.state.build_queue)
    second = await app.state.build_queue.submit(fixture_variant("heltec-v3"), CONFIG)

    resp = await client.get(second.download_url)

    assert resp.status_code == 200
    assert resp.content == (first.build_dir / resp.headers["content-disposition"].split('"')[1]).read_bytes()
    assert {p.name for p in second.build_dir.iterdir()} == {a.name for a in second.artifacts}


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_build_of_another_variant_misses_the_build_cache(app):
    await _complete_build(app.state.build_queue)

    other = await app.state.build_queue.submit(fixture_variant("rak4631"), CONFIG)

    assert other.status == "queued"
    await _wait_for(other, ENDED)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_cache_entry_without_its_directory_is_a_miss(app, settings):
    first = await _complete_build(app.state.build_queue)
    shutil.rmtree(build_cache.cache_dir(settings) / first.record()["config_hash"])

    second = await app.state.build_queue.submit(fixture_variant("heltec-v3"), CONFIG)

    assert second.status == "queued"
    await _wait_for(second, ENDED)
    assert second.status == "complete"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_failed_build_stores_no_cache_entry(app, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL", "1")
    ctx = await app.state.build_queue.submit(fixture_variant("heltec-v3"), CONFIG)
    await _wait_for(ctx, ENDED)

    assert ctx.status == "failed"
    assert await _entries(settings) == []


# FR-fleet-builds-8


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_build_cache_stores_only_the_artifacts(app, settings):
    ctx = await _complete_build(app.state.build_queue)

    entry_dir = build_cache.cache_dir(settings) / ctx.record()["config_hash"]
    assert {p.name for p in entry_dir.iterdir()} == {a.name for a in ctx.artifacts}
    [entry] = await _entries(settings)
    assert entry["config_hash"] == ctx.record()["config_hash"]
    assert entry["firmware_version"] == FIRMWARE_VERSION
    assert entry["variant"] == "heltec-v3"
    assert not any(PSK in str(value) or CONFIG in str(value) for value in entry.values())


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_cache_hit_sets_the_last_use_of_the_cache_entry(app, settings):
    ctx = await _complete_build(app.state.build_queue)
    await _set_last_use(settings, ctx.record()["config_hash"], 1000.0)

    await app.state.build_queue.submit(fixture_variant("heltec-v3"), CONFIG)

    [entry] = await _entries(settings)
    assert entry["last_used_at"] > time.time() - 60


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_cleanup_removes_a_cache_entry_after_the_ttl_since_its_last_use(app, settings):
    old = await _complete_build(app.state.build_queue)
    recent = await _complete_build(app.state.build_queue, env="rak4631")
    old_hash = old.record()["config_hash"]
    await _set_last_use(settings, old_hash, time.time() - settings.cache_ttl_seconds - 10)

    removed = await build_cache.remove_expired_entries(settings)

    assert removed == 1
    assert [e["config_hash"] for e in await _entries(settings)] == [recent.record()["config_hash"]]
    assert not (build_cache.cache_dir(settings) / old_hash).exists()
    assert (build_cache.cache_dir(settings) / recent.record()["config_hash"]).is_dir()


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_cleanup_task_removes_expired_cache_entries(app, settings):
    ctx = await _complete_build(app.state.build_queue)
    await _set_last_use(settings, ctx.record()["config_hash"], time.time() - settings.cache_ttl_seconds - 10)

    task = asyncio.create_task(cleanup_every_interval(settings, set))
    try:
        deadline = time.monotonic() + 5
        while await _entries(settings):
            assert time.monotonic() < deadline, "the cleanup task kept the expired cache entry"
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_cache_ttl_is_one_day_by_default(settings):
    assert settings.cache_ttl_seconds == 86400
    assert settings.cache_enabled is True


def test_cache_ttl_comes_from_the_environment(settings_env, monkeypatch):
    monkeypatch.setenv("MTFW_CACHE_TTL_SECONDS", "60")

    assert load_settings().cache_ttl_seconds == 60


# FR-fleet-builds-9


@pytest.fixture
def disabled(settings_env, monkeypatch):
    monkeypatch.setenv("MTFW_CACHE_ENABLED", "false")


@pytest.mark.usefixtures("disabled", "fake_pio", "firmware_source", "workers")
async def test_disabled_build_cache_stores_nothing_and_builds_the_same_config_again(app, settings):
    assert settings.cache_enabled is False
    await _complete_build(app.state.build_queue)

    second = await app.state.build_queue.submit(fixture_variant("heltec-v3"), CONFIG)

    assert second.status == "queued"
    await _wait_for(second, ENDED)
    assert await _entries(settings) == []
    assert not build_cache.cache_dir(settings).exists()
