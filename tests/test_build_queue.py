"""Tests for the build queue, the build workers and the progress stream (feature 06)."""

import asyncio
import json
import re
import time

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.rate_limit import limiter
from mtfwbuilder.services import build_service
from mtfwbuilder.services.build_service import (
    FIRMWARE_SOURCE_MISSING,
    BuildQueue,
    BuildQueueFull,
    generate_build_id,
    progress_events,
)
from mtfwbuilder.services.device_registry import DeviceRegistry

CONFIG = json.dumps({"owner_long_name": "TestNode"})


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def workers(app):
    """Start the build workers of the app, and stop them after the test."""
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


async def _post_build(client, variant="heltec-v3", config_json=CONFIG):
    return await client.post(
        "/api/v1/build-firmware",
        data={"variant": variant, "config_source": "current", "config_json": config_json},
    )


async def _wait_for(ctx, statuses, timeout=10.0):
    """Return when the build status is in `statuses`; fail the test after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while ctx.status not in statuses:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)


def _sse_events(body):
    """Return the (event, data) pairs of an SSE response body."""
    events = []
    for block in re.split(r"\r?\n\r?\n", body):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


async def _collect(ctx):
    return [event async for event in progress_events(ctx)]


def _statuses(events):
    return [json.loads(e["data"])["status"] for e in events if e["event"] == "status"]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_build_returns_202_and_reaches_complete_with_no_progress_stream_open(app, client):
    resp = await _post_build(client)

    assert resp.status_code == 202
    body = resp.json()
    assert body["progress_url"] == f"/api/v1/build-progress/{body['build_id']}"
    ctx = app.state.build_queue.get(body["build_id"])
    await _wait_for(ctx, {"complete", "failed", "cancelled"})
    assert ctx.status == "complete", ctx.error
    download = await client.get(ctx.download_url)
    assert download.status_code == 200
    assert download.content == b"fake firmware-heltec-v3-2.7.26.54e0d8d.factory.bin"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_nrf52_build_sends_the_uf2_artifact(app, client):
    resp = await _post_build(client, variant="rak4631")

    ctx = app.state.build_queue.get(resp.json()["build_id"])
    await _wait_for(ctx, {"complete", "failed", "cancelled"})
    assert ctx.status == "complete", ctx.error
    download = await client.get(ctx.download_url)
    assert download.content == b"fake firmware-rak4631-2.7.26.54e0d8d.uf2"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_reconnect_during_a_build_gets_the_current_build_status(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    resp = await _post_build(client)
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    await _wait_for(ctx, {"compiling"})

    stream = await client.get(resp.json()["progress_url"])

    events = _sse_events(stream.text)
    assert events[0][0] == "status"
    assert events[0][1]["status"] == "compiling"
    assert events[-1] == ("status", events[-1][1])
    assert events[-1][1]["status"] == "complete"
    assert any(kind == "log" and data["line"].startswith("Linking") for kind, data in events)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_connect_after_the_build_ends_gets_the_final_build_status(app, client):
    resp = await _post_build(client)
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    await _wait_for(ctx, {"complete", "failed", "cancelled"})

    stream = await client.get(resp.json()["progress_url"])

    events = _sse_events(stream.text)
    assert [(kind, data["status"]) for kind, data in events if kind == "status"] == [("status", "complete")]


@pytest.mark.usefixtures("fake_pio", "firmware_source")
async def test_closed_progress_stream_does_not_cancel_the_build(app, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "0.5")
    build_queue = app.state.build_queue
    ctx = await build_queue.submit(app.state.device_registry.get("heltec-v3"), "{}")
    stream = progress_events(ctx)
    first = await anext(stream)
    build_queue.start()
    await _wait_for(ctx, {"compiling"})

    await stream.aclose()

    await _wait_for(ctx, {"complete", "failed", "cancelled"})
    await build_queue.stop()
    assert json.loads(first["data"])["status"] == "queued"
    assert ctx.status == "complete"


@pytest.mark.usefixtures("fake_pio", "firmware_source")
async def test_failed_pio_reports_one_failed_event_with_the_error_message(app, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL", "1")
    build_queue = app.state.build_queue
    ctx = await build_queue.submit(app.state.device_registry.get("heltec-v3"), "{}")
    collector = asyncio.create_task(_collect(ctx))
    await asyncio.sleep(0)
    build_queue.start()

    events = await asyncio.wait_for(collector, 10)
    await build_queue.stop()

    statuses = _statuses(events)
    assert statuses.count("failed") == 1
    assert statuses[-1] == "failed"
    assert "linking" not in statuses
    assert "error: FAKE_PIO_FAIL is set" in json.loads(events[-1]["data"])["error"]


async def test_build_fails_at_once_when_the_firmware_source_is_missing(app, client):
    resp = await _post_build(client)

    assert resp.status_code == 202
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    assert ctx.status == "failed"
    assert ctx.error == "Firmware source not installed. An admin must install a firmware version."
    assert ctx.error == FIRMWARE_SOURCE_MISSING


async def test_sixth_queued_build_returns_503_with_build_queue_size_5(app, client, firmware_source, monkeypatch):
    # The rate limit of 5 builds a minute for one client answers the sixth request before the build queue.
    monkeypatch.setattr(limiter, "enabled", False)
    assert app.state.settings.max_queue_size == 5
    for _ in range(5):
        assert (await _post_build(client)).status_code == 202

    resp = await _post_build(client)

    assert resp.status_code == 503


async def test_submit_raises_when_the_build_queue_is_full(settings, firmware_source):
    variant = DeviceRegistry(settings.devices_file).get("pico")
    build_queue = BuildQueue(settings)
    for _ in range(settings.max_queue_size):
        await build_queue.submit(variant, "{}")

    with pytest.raises(BuildQueueFull):
        await build_queue.submit(variant, "{}")


async def test_build_id_is_32_hex_characters(client, firmware_source):
    resp = await _post_build(client)

    assert re.fullmatch(r"[0-9a-f]{32}", resp.json()["build_id"])
    assert re.fullmatch(r"[0-9a-f]{32}", generate_build_id())
    assert generate_build_id() != generate_build_id()


async def test_cancel_of_a_queued_build_gives_cancelled(app, client, firmware_source):
    build_id = (await _post_build(client)).json()["build_id"]

    resp = await client.post(f"/api/v1/builds/{build_id}/cancel")

    assert resp.status_code == 200
    assert resp.json() == {"build_id": build_id, "status": "cancelled"}
    assert app.state.build_queue.get(build_id).status == "cancelled"


async def test_cancel_of_an_ended_build_returns_409(client, firmware_source):
    build_id = (await _post_build(client)).json()["build_id"]
    await client.post(f"/api/v1/builds/{build_id}/cancel")

    resp = await client.post(f"/api/v1/builds/{build_id}/cancel")

    assert resp.status_code == 409


async def test_cancel_of_an_unknown_build_returns_404(client):
    resp = await client.post(f"/api/v1/builds/{'0' * 32}/cancel")

    assert resp.status_code == 404


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_cancel_during_compiling_stops_the_pio_process(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    build_id = (await _post_build(client)).json()["build_id"]
    ctx = app.state.build_queue.get(build_id)
    await _wait_for(ctx, {"compiling"})

    resp = await client.post(f"/api/v1/builds/{build_id}/cancel")

    assert resp.status_code == 200
    await asyncio.wait({ctx.task}, timeout=5)
    assert ctx.task.done()
    assert ctx.status == "cancelled"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_build_worker_runs_the_next_build_after_a_cancel(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    first = app.state.build_queue.get((await _post_build(client)).json()["build_id"])
    await _wait_for(first, {"compiling"})
    monkeypatch.setenv("FAKE_PIO_SLEEP", "0")
    second = app.state.build_queue.get((await _post_build(client)).json()["build_id"])

    await client.post(f"/api/v1/builds/{first.build_id}/cancel")

    await _wait_for(second, {"complete", "failed", "cancelled"})
    assert second.status == "complete", second.error


@pytest.mark.parametrize("config_json", ["[]", "not json", json.dumps({"channels_to_write": "x"})])
async def test_invalid_config_json_returns_422(client, config_json):
    resp = await _post_build(client, config_json=config_json)

    assert resp.status_code == 422


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_unexpected_build_error_gives_failed(app, client, monkeypatch):
    def broken_extract(ctx):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(build_service, "_collect_artifacts", broken_extract)
    ctx = app.state.build_queue.get((await _post_build(client)).json()["build_id"])

    await _wait_for(ctx, {"complete", "failed", "cancelled"})

    assert ctx.status == "failed"
    assert ctx.error == "The build stopped because of an internal error."
