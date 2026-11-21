"""Tests for the artifact collection and the artifact routes (feature 06)."""

import asyncio
import json
import re
import time

import pytest
from httpx import ASGITransport, AsyncClient

VERSION = "2.7.26.54e0d8d"
CONFIG = json.dumps({"device_name": "TestNode"})


@pytest.fixture
def firmware_source(settings):
    settings.firmware_dir.mkdir(parents=True)
    return settings.firmware_dir


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def workers(app):
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


async def _complete_build(app, client, variant):
    """Return the build id of a build of `variant`, after the build is complete."""
    resp = await client.post(
        "/api/v1/build-firmware",
        data={"variant": variant, "config_source": "current", "config_json": CONFIG},
    )
    build_id = resp.json()["build_id"]
    ctx = app.state.build_queue.get(build_id)
    deadline = time.monotonic() + 10
    while not ctx.ended:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)
    assert ctx.status == "complete", ctx.error
    return build_id


def _kinds(artifacts):
    return {a["name"]: a["kind"] for a in artifacts}


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_esp32_artifact_list_has_kind_factory_and_kind_app(app, client):
    build_id = await _complete_build(app, client, "heltec-v3")

    resp = await client.get(f"/api/v1/builds/{build_id}/artifacts")

    assert resp.status_code == 200
    assert _kinds(resp.json()) == {
        f"firmware-heltec-v3-{VERSION}.factory.bin": "factory",
        f"firmware-heltec-v3-{VERSION}.bin": "app",
        f"firmware-heltec-v3-{VERSION}.mt.json": "manifest",
        f"littlefs-heltec-v3-{VERSION}.bin": "littlefs",
    }


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_nrf52_artifact_list_has_kind_uf2_and_kind_ota_zip(app, client):
    build_id = await _complete_build(app, client, "rak4631")

    resp = await client.get(f"/api/v1/builds/{build_id}/artifacts")

    assert _kinds(resp.json()) == {
        f"firmware-rak4631-{VERSION}.uf2": "uf2",
        f"firmware-rak4631-{VERSION}.hex": "hex",
        f"firmware-rak4631-{VERSION}-ota.zip": "ota-zip",
        f"firmware-rak4631-{VERSION}.mt.json": "manifest",
    }


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_artifact_list_gives_the_size_of_each_artifact(app, client):
    build_id = await _complete_build(app, client, "pico")

    resp = await client.get(f"/api/v1/builds/{build_id}/artifacts")

    for artifact in resp.json():
        assert artifact["size"] == len(b"fake " + artifact["name"].encode())


async def test_artifact_list_of_an_unknown_build_returns_404(client):
    resp = await client.get("/api/v1/builds/0123456789abcdef0123456789abcdef/artifacts")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Build not found"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_artifact_route_sends_the_named_artifact(app, client):
    build_id = await _complete_build(app, client, "rak4631")
    name = f"firmware-rak4631-{VERSION}-ota.zip"

    resp = await client.get(f"/api/v1/builds/{build_id}/artifacts/{name}")

    assert resp.status_code == 200
    assert resp.content == b"fake " + name.encode()
    assert name in resp.headers["content-disposition"]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
@pytest.mark.parametrize("name", ["firmware.bin", "userPrefs.jsonc"])
async def test_artifact_route_returns_404_for_a_name_not_in_the_artifact_list(app, client, name):
    build_id = await _complete_build(app, client, "heltec-v3")
    (app.state.settings.temp_dir / build_id / "userPrefs.jsonc").write_text("{}")

    resp = await client.get(f"/api/v1/builds/{build_id}/artifacts/{name}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Artifact not found"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
@pytest.mark.parametrize(
    ("variant", "primary"),
    [
        ("heltec-v3", f"firmware-heltec-v3-{VERSION}.factory.bin"),
        ("rak4631", f"firmware-rak4631-{VERSION}.uf2"),
        ("pico", f"firmware-pico-{VERSION}.uf2"),
    ],
)
async def test_old_download_route_returns_308_to_the_primary_artifact(app, client, variant, primary):
    build_id = await _complete_build(app, client, variant)

    resp = await client.get(f"/api/v1/download-firmware/{build_id}?variant={variant}")

    assert resp.status_code == 308
    assert resp.headers["location"] == f"/api/v1/builds/{build_id}/artifacts/{primary}"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_complete_build_status_links_to_the_primary_artifact(app, client):
    build_id = await _complete_build(app, client, "heltec-v3")

    ctx = app.state.build_queue.get(build_id)

    assert ctx.download_url == f"/api/v1/builds/{build_id}/artifacts/firmware-heltec-v3-{VERSION}.factory.bin"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_progress_stream_sends_the_artifact_list_before_the_complete_status(app, client):
    build_id = await _complete_build(app, client, "rak4631")

    stream = await client.get(f"/api/v1/build-progress/{build_id}")

    events = []
    for block in re.split(r"\r?\n\r?\n", stream.text):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    assert [kind for kind, _ in events] == ["artifacts", "status"]
    assert events[1][1]["status"] == "complete"
    assert {a["kind"] for a in events[0][1]} == {"uf2", "hex", "ota-zip", "manifest"}
