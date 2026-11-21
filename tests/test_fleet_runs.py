"""Tests for fleet runs (feature fleet-builds, FR 15, 17 and 18, and the restart edge case)."""

import asyncio
import csv
import io
import json
import logging
import re
import time
import zipfile

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.main import lifespan

PSK = "deadbeef" * 8
# The generator writes a key as a C byte array, so a test looks for the bytes in that form too.
PSK_BYTES = "0xde, 0xad, 0xbe, 0xef"
ADMIN_KEY = "cafebabe" * 8
ADMIN_KEY_BYTES = "0xca, 0xfe, 0xba, 0xbe"
PSK_CONFIG = {"owner_short_name": "EVT", "channels_to_write": "1", "channel_0[name]": "Primary", "channel_0[psk]": PSK}

DEVICES = [
    {
        "label": "gate-1",
        "variant": "heltec-v3",
        "long_name": "Gate One",
        "short_name": "G1",
        "role": "TRACKER",
        "lat": 52.5,
        "lon": 13.4,
        "alt": 34,
        "admin_key": ADMIN_KEY,
    },
    {"label": "gate-2", "variant": "heltec-v3", "long_name": "Gate Two"},
    {"label": "gate-3", "variant": "heltec-v3", "long_name": "Gate Three"},
]


@pytest.fixture
async def workers(app):
    """Start the build workers of the app, and stop the fleet runs and the build workers after the test."""
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.fleet_runs.stop()
    await app.state.build_queue.stop()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _fleet(client, config=None, devices=DEVICES) -> int:
    profile = await client.post(
        "/api/v1/profiles", json={"name": "Event", "config": config or {"owner_short_name": "EVT"}}
    )
    assert profile.status_code == 201, profile.text
    body = {"name": "Field day", "profile_id": profile.json()["id"], "devices": devices}
    fleet = await client.post("/api/v1/fleets", json=body)
    assert fleet.status_code == 201, fleet.text
    return fleet.json()["id"]


async def _start(client, fleet_id, secrets=None):
    return await client.post(f"/api/v1/fleets/{fleet_id}/runs", json={"secrets": secrets or {}})


async def _run(client, run_id) -> dict:
    response = await client.get(f"/api/v1/fleet-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_until(client, run_id, ready, timeout=20.0) -> dict:
    deadline = time.monotonic() + timeout
    while not ready(run := await _run(client, run_id)):
        assert time.monotonic() < deadline, f"fleet run stayed at {run}"
        await asyncio.sleep(0.05)
    return run


async def _wait_until_ended(client, run_id) -> dict:
    return await _wait_until(client, run_id, lambda run: run["status"] != "running")


def _by_label(run: dict) -> dict[str, dict]:
    return {device["label"]: device for device in run["devices"]}


def _prefs(app, device: dict) -> dict:
    content = app.state.build_queue.get(device["build_id"]).config_content
    return json.loads(re.sub("//.*", "", content, flags=re.MULTILINE))


async def _build_rows(database_path) -> list[dict]:
    """Return each `builds` row, with `run_public_id` for the public id of its fleet run."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT b.*, fr.public_id AS run_public_id FROM builds b LEFT JOIN fleet_runs fr ON fr.id = b.fleet_run_id"
        )
        return [dict(row) for row in await cursor.fetchall()]


# FR-fleet-builds-15


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_of_3_devices_makes_one_build_per_device(client, settings):
    fleet_id = await _fleet(client)

    response = await _start(client, fleet_id)

    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert response.json()["progress_url"] == f"/api/v1/fleet-runs/{run_id}"
    run = await _wait_until_ended(client, run_id)
    assert run["status"] == "complete"
    assert [device["status"] for device in run["devices"]] == ["complete"] * 3
    build_ids = {device["build_id"] for device in run["devices"]}
    assert len(build_ids) == 3
    rows = await _build_rows(settings.database_path)
    assert {row["id"] for row in rows if row["run_public_id"] == run_id} == build_ids


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_build_holds_the_overrides_of_its_fleet_device(app, client):
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]

    devices = _by_label(await _wait_until_ended(client, run_id))

    gate_1 = _prefs(app, devices["gate-1"])
    assert gate_1["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "Gate One"
    assert gate_1["USERPREFS_CONFIG_OWNER_SHORT_NAME"] == "G1"
    assert gate_1["USERPREFS_CONFIG_DEVICE_ROLE"] == "meshtastic_Config_DeviceConfig_Role_TRACKER"
    assert gate_1["USERPREFS_FIXED_GPS"] == "true"
    assert gate_1["USERPREFS_FIXED_GPS_LAT"] == "52.5"
    assert gate_1["USERPREFS_FIXED_GPS_LON"] == "13.4"
    assert gate_1["USERPREFS_FIXED_GPS_ALT"] == "34"
    assert ADMIN_KEY_BYTES in gate_1["USERPREFS_USE_ADMIN_KEY_0"]
    gate_2 = _prefs(app, devices["gate-2"])
    assert gate_2["USERPREFS_CONFIG_OWNER_LONG_NAME"] == "Gate Two"
    # A fleet device without a short name keeps the short name of the profile.
    assert gate_2["USERPREFS_CONFIG_OWNER_SHORT_NAME"] == "EVT"
    assert "USERPREFS_USE_ADMIN_KEY_0" not in gate_2
    assert "USERPREFS_FIXED_GPS" not in gate_2


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_status_holds_no_admin_key(client):
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]
    await _wait_until_ended(client, run_id)

    response = await client.get(f"/api/v1/fleet-runs/{run_id}")

    assert ADMIN_KEY not in response.text
    assert ADMIN_KEY_BYTES not in response.text


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_feeds_its_builds_one_at_a_time(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "1")
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]

    run = await _wait_until(client, run_id, lambda run: run["devices"][0]["status"] == "compiling")

    assert run["status"] == "running"
    assert [device["build_id"] for device in run["devices"][1:]] == [None, None]
    assert [device["status"] for device in run["devices"][1:]] == ["queued", "queued"]
    # A fleet run that runs has no failed fleet device to retry yet.
    assert (await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})).status_code == 409
    assert (await _wait_until_ended(client, run_id))["status"] == "complete"


# FR-fleet-builds-17 and FR-fleet-builds-18, criterion "partial, and Retry failed builds only that device"


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_where_one_build_fails_ends_partial_and_retry_builds_only_that_device(
    client, settings, monkeypatch
):
    monkeypatch.setenv("FAKE_PIO_FAIL_MATCH", "Gate Two")
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]

    run = await _wait_until_ended(client, run_id)

    assert run["status"] == "partial"
    first = _by_label(run)
    # The failed build of gate-2 does not stop the build of gate-3.
    assert {label: device["status"] for label, device in first.items()} == {
        "gate-1": "complete",
        "gate-2": "failed",
        "gate-3": "complete",
    }
    assert first["gate-2"]["error"]

    monkeypatch.delenv("FAKE_PIO_FAIL_MATCH")
    response = await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})

    assert response.status_code == 202, response.text
    run = await _wait_until_ended(client, run_id)
    assert run["status"] == "complete"
    second = _by_label(run)
    assert second["gate-1"]["build_id"] == first["gate-1"]["build_id"]
    assert second["gate-3"]["build_id"] == first["gate-3"]["build_id"]
    assert second["gate-2"]["build_id"] != first["gate-2"]["build_id"]
    rows = await _build_rows(settings.database_path)
    assert len([row for row in rows if row["run_public_id"] == run_id]) == 4


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_retry_of_a_complete_fleet_run_returns_409(client):
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]
    await _wait_until_ended(client, run_id)

    response = await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})

    assert response.status_code == 409


# Behaviour rule: "must re-enter" secrets, once for the whole fleet run


def _secret_errors(response) -> list[str]:
    return [error["loc"][-1] for error in response.json()["detail"] if "secrets" in error["loc"]]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_without_a_must_reenter_secret_returns_422_with_its_name(client, settings):
    fleet_id = await _fleet(client, config=PSK_CONFIG)

    response = await _start(client, fleet_id)

    assert response.status_code == 422
    assert _secret_errors(response) == ["channel_0[psk]"]
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM fleet_runs")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_with_a_secret_that_is_not_must_reenter_returns_422(client):
    fleet_id = await _fleet(client)

    response = await _start(client, fleet_id, {"wifi_psk": "not-asked-for"})

    assert response.status_code == 422
    assert _secret_errors(response) == ["wifi_psk"]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_fleet_run_builds_with_the_reentered_secret_and_stores_it_nowhere(app, client, settings, caplog):
    caplog.set_level(logging.DEBUG)
    fleet_id = await _fleet(client, config=PSK_CONFIG)

    response = await _start(client, fleet_id, {"channel_0[psk]": PSK})

    assert response.status_code == 202, response.text
    run = await _wait_until_ended(client, response.json()["run_id"])
    assert run["status"] == "complete"
    for device in run["devices"]:
        assert PSK_BYTES in _prefs(app, device)["USERPREFS_CHANNEL_0_PSK"]
    await app.state.build_queue.stop()
    database = settings.database_path.read_bytes()
    assert PSK.encode() not in database
    assert PSK_BYTES.encode() not in database
    assert PSK not in caplog.text
    assert PSK_BYTES not in caplog.text


# Edge case: the server restarts during a fleet run


@pytest.mark.usefixtures("fake_pio", "firmware_source")
async def test_after_a_restart_an_unfinished_fleet_run_shows_partial_and_retry_continues_it(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    async with lifespan(app):
        fleet_id = await _fleet(client, config=PSK_CONFIG)
        run_id = (await _start(client, fleet_id, {"channel_0[psk]": PSK})).json()["run_id"]
        await _wait_until(client, run_id, lambda run: run["devices"][0]["status"] == "compiling")
    monkeypatch.delenv("FAKE_PIO_SLEEP")

    async with lifespan(app):
        run = await _run(client, run_id)
        assert run["status"] == "partial"
        assert [device["status"] for device in run["devices"]] == ["failed", "queued", "queued"]

        # The restart removes the re-entered secrets from memory, so Retry failed asks for them again.
        response = await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})
        assert response.status_code == 422
        assert _secret_errors(response) == ["channel_0[psk]"]

        response = await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={"secrets": {"channel_0[psk]": PSK}})
        assert response.status_code == 202, response.text
        run = await _wait_until_ended(client, run_id)

    assert run["status"] == "complete"
    assert [device["status"] for device in run["devices"]] == ["complete"] * 3


# Errors


async def test_fleet_run_of_an_unknown_fleet_returns_404(client):
    response = await _start(client, 999)

    assert response.status_code == 404
    assert response.json()["detail"] == "Fleet 999 does not exist"


async def test_unknown_fleet_run_returns_404(client):
    for response in (
        await client.get("/api/v1/fleet-runs/999"),
        await client.post("/api/v1/fleet-runs/999/retry", json={}),
    ):
        assert response.status_code == 404
        assert response.json()["detail"] == "Fleet run 999 does not exist"


@pytest.mark.usefixtures("firmware_source")
async def test_fleet_run_of_a_device_without_a_variant_returns_422_with_its_row(client):
    fleet_id = await _fleet(client, devices=[DEVICES[1], {"label": "no-variant"}])

    response = await _start(client, fleet_id)

    assert response.status_code == 422
    assert [error["loc"][:4] for error in response.json()["detail"]] == [["body", "devices", 1, "variant"]]


# FR-fleet-builds-19 and -20: Download all


async def _download(client, run_id):
    return await client.get(f"/api/v1/fleet-runs/{run_id}/download")


def _zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


def _manifest(archive: zipfile.ZipFile) -> list[dict]:
    return list(csv.DictReader(io.StringIO(archive.read("manifest.csv").decode())))


def _folders(archive: zipfile.ZipFile) -> set[str]:
    return {name.split("/")[0] for name in archive.namelist() if "/" in name}


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_fleet_run_of_3_devices_gives_a_zip_with_3_folders_and_manifest_csv(client):
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]
    run = await _wait_until_ended(client, run_id)

    response = await _download(client, run_id)

    archive = _zip(response)
    assert response.headers["content-disposition"] == f'attachment; filename="fleet-run-{run_id}.zip"'
    assert _folders(archive) == {"gate-1", "gate-2", "gate-3"}
    rows = _manifest(archive)
    assert [row["label"] for row in rows] == ["gate-1", "gate-2", "gate-3"]
    for row, device in zip(rows, run["devices"], strict=True):
        assert row["variant"] == "heltec-v3"
        assert row["firmware_version"] == run["firmware_version"]
        assert row["build_id"] == device["build_id"]
        assert row["status"] == "complete"
        assert re.fullmatch("[0-9a-f]{64}", row["config_hash"])
        in_folder = [name for name in archive.namelist() if name.startswith(f"{row['folder']}/")]
        assert in_folder
        assert sorted(f"{row['folder']}/{name}" for name in row["artifact_names"].split(";")) == sorted(in_folder)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_manifest_csv_holds_no_psk_and_no_admin_key(client):
    fleet_id = await _fleet(client, config=PSK_CONFIG)
    run_id = (await _start(client, fleet_id, {"channel_0[psk]": PSK})).json()["run_id"]
    assert (await _wait_until_ended(client, run_id))["status"] == "complete"

    manifest = _zip(await _download(client, run_id)).read("manifest.csv").decode()

    for secret in (PSK, PSK_BYTES, ADMIN_KEY, ADMIN_KEY_BYTES):
        assert secret not in manifest


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_the_zip_of_a_partial_fleet_run_lists_the_failed_device_without_a_folder(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL_MATCH", "Gate Two")
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]
    assert (await _wait_until_ended(client, run_id))["status"] == "partial"

    archive = _zip(await _download(client, run_id))

    assert _folders(archive) == {"gate-1", "gate-3"}
    failed = _manifest(archive)[1]
    assert (failed["label"], failed["status"], failed["artifact_names"]) == ("gate-2", "failed", "")


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_label_with_a_path_gives_a_folder_inside_the_zip(client):
    devices = [{"label": "../team/a", "variant": "heltec-v3"}, {"label": ".hidden", "variant": "heltec-v3"}]
    run_id = (await _start(client, await _fleet(client, devices=devices))).json()["run_id"]
    await _wait_until_ended(client, run_id)

    archive = _zip(await _download(client, run_id))

    assert _folders(archive) == {"_.._team_a", "_.hidden"}
    assert [(row["label"], row["folder"]) for row in _manifest(archive)] == [
        ("../team/a", "_.._team_a"),
        (".hidden", "_.hidden"),
    ]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_the_download_of_a_running_fleet_run_returns_409(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]

    response = await _download(client, run_id)

    assert response.status_code == 409
    assert response.json()["detail"] == "The fleet run is running."


async def test_the_download_of_an_unknown_fleet_run_returns_404(client):
    response = await _download(client, 999)

    assert response.status_code == 404
    assert response.json()["detail"] == "Fleet run 999 does not exist"


# A table save keeps the fleet device ids, so Retry failed still finds the fleet devices.


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_retry_after_a_table_save_with_the_same_labels_builds_the_failed_device(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL_MATCH", "Gate Two")
    fleet_id = await _fleet(client)
    run_id = (await _start(client, fleet_id)).json()["run_id"]
    assert (await _wait_until_ended(client, run_id))["status"] == "partial"
    monkeypatch.delenv("FAKE_PIO_FAIL_MATCH")
    rows = [{**device, "long_name": "Gate 2 fixed"} if device["label"] == "gate-2" else device for device in DEVICES]
    saved = await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": rows})
    assert saved.status_code == 200, saved.text

    response = await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})

    assert response.status_code == 202, response.text
    assert (await _wait_until_ended(client, run_id))["status"] == "complete"
