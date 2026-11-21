"""Tests for the 10 rulings of the epic #65 batch review (feature fleet-builds).

Each section names its ruling from the PM decision comment on issue #65.
"""

import asyncio
import csv
import io
import json
import re
import zipfile

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import init_db
from mtfwbuilder.services import fleet_runs
from mtfwbuilder.services.build_service import BuildQueueFull
from tests.conftest import fixture_variant, sign_in_admin
from tests.test_fleet_runs import (
    DEVICES,
    PSK,
    PSK_CONFIG,
    _fleet,
    _prefs,
    _run,
    _start,
    _wait_until_ended,
)

OTHER_PSK = "0badc0de" * 8
TWO_PSK_CONFIG = {
    **PSK_CONFIG,
    "channels_to_write": "2",
    "channel_1[name]": "Second",
    "channel_1[psk]": OTHER_PSK,
}
BOTH_SECRETS = {"channel_0[psk]": PSK, "channel_1[psk]": OTHER_PSK}


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


async def _profile(client, name, config) -> int:
    response = await client.post("/api/v1/profiles", json={"name": name, "config": config})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _partial_run(client, monkeypatch, config=None, secrets=None) -> tuple[int, str]:
    """Return the fleet id and the fleet run id of a fleet run whose gate-2 build fails."""
    monkeypatch.setenv("FAKE_PIO_FAIL_MATCH", "Gate Two")
    fleet_id = await _fleet(client, config=config)
    run_id = (await _start(client, fleet_id, secrets)).json()["run_id"]
    assert (await _wait_until_ended(client, run_id))["status"] == "partial"
    monkeypatch.delenv("FAKE_PIO_FAIL_MATCH")
    return fleet_id, run_id


def _held_secrets(app) -> str:
    """Return the JSON of every secret that the process holds for Retry failed."""
    return json.dumps([held.secrets for held in app.state.fleet_runs._held.values()])


def _manifest(response) -> list[dict]:
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return list(csv.DictReader(io.StringIO(archive.read("manifest.csv").decode())))


# Ruling 1: fleet run ids are not guessable.


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_fleet_run_has_a_random_public_id_and_its_row_id_does_not_resolve(client):
    response = await _start(client, await _fleet(client))

    run_id = response.json()["run_id"]
    assert re.fullmatch("[0-9a-f]{32}", run_id)
    assert response.json()["progress_url"] == f"/api/v1/fleet-runs/{run_id}"
    assert (await _wait_until_ended(client, run_id))["id"] == run_id
    for url in ("/api/v1/fleet-runs/1", "/api/v1/fleet-runs/1/download", "/fleet-runs/1"):
        assert (await client.get(url)).status_code == 404, url
    assert (await client.post("/api/v1/fleet-runs/1/retry", json={})).status_code == 404
    assert (await client.get(f"/fleet-runs/{run_id}")).status_code == 200


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_fleet_run_of_an_admin_session_needs_the_csrf_token(client, settings):
    fleet_id = await _fleet(client)
    token = sign_in_admin(client, settings)

    refused = await _start(client, fleet_id)
    accepted = await client.post(f"/api/v1/fleets/{fleet_id}/runs", json={}, headers={"X-CSRF-Token": token})

    assert refused.status_code == 403
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run_id"]
    await _wait_until_ended(client, run_id)
    assert (await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})).status_code == 403


async def test_init_db_gives_an_old_fleet_run_a_public_id_and_a_profile_id(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "CREATE TABLE fleet_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, fleet_id INTEGER NOT NULL, "
            "firmware_version TEXT, status TEXT NOT NULL DEFAULT 'queued', created_at TIMESTAMP)"
        )
        await db.execute("INSERT INTO fleet_runs (fleet_id, status) VALUES (1, 'complete')")
        await db.commit()

    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT public_id, profile_id FROM fleet_runs")
        public_id, profile_id = await cursor.fetchone()
    assert re.fullmatch("[0-9a-f]{32}", public_id)
    assert profile_id is None


# Ruling 2: fleet runs obey the limits.


async def test_the_fleet_run_routes_use_the_build_rate_limit(client, settings):
    settings.build_rate_limit = "1/minute"

    # The limiter counts each route on its own.
    assert (await _start(client, 999)).status_code == 404
    assert (await _start(client, 999)).status_code == 429
    assert (await client.post("/api/v1/fleet-runs/none/retry", json={})).status_code == 404
    assert (await client.post("/api/v1/fleet-runs/none/retry", json={})).status_code == 429


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_second_fleet_run_of_a_fleet_with_a_running_fleet_run_returns_409(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    fleet_id = await _fleet(client)
    assert (await _start(client, fleet_id)).status_code == 202

    response = await _start(client, fleet_id)

    assert response.status_code == 409
    assert response.json()["detail"] == "The fleet has a running fleet run."


@pytest.mark.usefixtures("firmware_source")
async def test_a_fleet_run_that_does_not_fit_in_the_build_queue_returns_503(app, client, settings):
    settings.max_queue_size = 1
    fleet_id = await _fleet(client)
    await app.state.build_queue.submit(fixture_variant("heltec-v3"), '{"a": "1"}')

    response = await _start(client, fleet_id)

    assert response.status_code == 503
    assert response.json()["detail"] == "Build queue is full. Try again later."
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM fleet_runs")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.usefixtures("firmware_source")
async def test_a_fleet_build_counts_toward_the_build_queue_limit(app, settings):
    settings.max_queue_size = 1
    build_queue = app.state.build_queue

    await build_queue.submit(fixture_variant("heltec-v3"), "{}", fleet_run_id=1)

    with pytest.raises(BuildQueueFull):
        await build_queue.submit(fixture_variant("heltec-v3"), '{"a": "1"}')


# Ruling 3: no duplicate labels.


async def test_a_fleet_request_with_a_duplicate_label_returns_422(client):
    profile_id = await _profile(client, "Event", {})
    devices = [{"label": "gate-1"}, {"label": "gate-1"}]

    created = await client.post("/api/v1/fleets", json={"name": "F", "profile_id": profile_id, "devices": devices})
    fleet = await client.post("/api/v1/fleets", json={"name": "F", "profile_id": profile_id})
    updated = await client.put(f"/api/v1/fleets/{fleet.json()['id']}", json={"devices": devices})

    for response in (created, updated):
        assert response.status_code == 422
        assert "the label gate-1 is in the fleet two times" in response.text


@pytest.mark.usefixtures("firmware_source")
async def test_the_csv_size_check_counts_the_saved_fleet_devices_not_their_labels(client, settings):
    fleet_id = await _fleet(client, devices=[{"label": "a"}, {"label": "b"}])
    # A database from before the label check can hold one label two times.
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("UPDATE fleet_devices SET label = 'a'")
        await db.commit()
    settings.max_fleet_size = 2

    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json={"csv": "label\nc\n"})

    assert response.status_code == 422
    assert "a fleet holds at most 2 devices" in response.text


# Ruling 4: no CSV formula injection.


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_manifest_cell_that_starts_a_formula_gets_a_quote_prefix(client):
    labels = ["=HYPERLINK(1)", "+1", "-2", "@SUM", "safe"]
    devices = [{"label": label, "variant": "heltec-v3"} for label in labels]
    run_id = (await _start(client, await _fleet(client, devices=devices))).json()["run_id"]
    await _wait_until_ended(client, run_id)

    rows = _manifest(await client.get(f"/api/v1/fleet-runs/{run_id}/download"))

    assert [row["label"] for row in rows] == ["'=HYPERLINK(1)", "'+1", "'-2", "'@SUM", "safe"]


def test_a_cell_that_starts_with_a_tab_or_a_carriage_return_gets_a_quote_prefix():
    assert [fleet_runs._manifest_cell(value) for value in ("\tx", "\rx", "x=1", "")] == ["'\tx", "'\rx", "x=1", ""]


# Ruling 5: folder names are unique on any filesystem.


def test_folder_names_differ_without_regard_to_case():
    used: set[str] = set()

    assert [fleet_runs._folder(label, used) for label in ("Gate", "gate", "GATE")] == ["Gate", "gate_", "GATE__"]


def test_a_folder_name_has_no_trailing_dot():
    used: set[str] = set()

    assert [fleet_runs._folder(label, used) for label in ("a.", "a..", "...")] == ["a", "a_", "_"]


@pytest.mark.parametrize("label", ["CON", "con", "nul.txt", "COM1", "lpt9", "AUX", "PRN"])
def test_a_windows_reserved_name_gets_a_prefix(label):
    assert fleet_runs._folder(label, set()) == f"_{label}"


# Ruling 6: input is capped.


async def test_a_label_of_65_characters_returns_422(client):
    profile_id = await _profile(client, "Event", {})

    long = await client.post(
        "/api/v1/fleets", json={"name": "F", "profile_id": profile_id, "devices": [{"label": "a" * 65}]}
    )
    limit = await client.post(
        "/api/v1/fleets", json={"name": "F", "profile_id": profile_id, "devices": [{"label": "a" * 64}]}
    )

    assert long.status_code == 422
    assert limit.status_code == 201, limit.text


async def test_a_csv_text_over_the_size_limit_returns_422(client):
    profile_id = await _profile(client, "Event", {})
    fleet = await client.post("/api/v1/fleets", json={"name": "F", "profile_id": profile_id})

    response = await client.post(
        f"/api/v1/fleets/{fleet.json()['id']}/devices/csv", json={"csv": "label\n" + "a" * 70_000}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "csv"]


@pytest.mark.usefixtures("firmware_source")
async def test_a_csv_with_too_many_rows_returns_the_size_error_before_any_row_error(client, settings):
    settings.max_fleet_size = 2
    fleet_id = await _fleet(client, devices=[])

    response = await client.post(
        f"/api/v1/fleets/{fleet_id}/devices/csv", json={"csv": "label,role\na,BAD\nb,BAD\nc,BAD\n"}
    )

    assert response.status_code == 422
    assert [(error["row"], error["msg"]) for error in response.json()["detail"]] == [
        (None, "a fleet holds at most 2 devices")
    ]


# Ruling 7: held secrets expire.


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_the_held_secrets_expire_after_build_max_age_seconds(client, settings, monkeypatch):
    settings.build_max_age_seconds = 0
    _, run_id = await _partial_run(client, monkeypatch, PSK_CONFIG, {"channel_0[psk]": PSK})
    await asyncio.sleep(0.05)

    response = await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})

    assert response.status_code == 422
    assert ["body", "secrets", "channel_0[psk]"] in [error["loc"] for error in response.json()["detail"]]


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_fleet_delete_clears_the_held_secrets_of_its_fleet_runs(app, client, monkeypatch):
    fleet_id, _ = await _partial_run(client, monkeypatch, PSK_CONFIG, {"channel_0[psk]": PSK})
    assert PSK in _held_secrets(app)

    assert (await client.delete(f"/api/v1/fleets/{fleet_id}")).status_code == 204

    assert PSK not in _held_secrets(app)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_an_internal_error_clears_the_held_secrets(app, client, monkeypatch):
    async def broken_submit(*args, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(app.state.build_queue, "submit", broken_submit)
    run_id = (await _start(client, await _fleet(client, config=PSK_CONFIG), {"channel_0[psk]": PSK})).json()["run_id"]

    await _wait_until_ended(client, run_id)

    assert PSK not in _held_secrets(app)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_retry_merges_the_entered_secrets_with_the_held_secrets(app, client, monkeypatch):
    _, run_id = await _partial_run(client, monkeypatch, TWO_PSK_CONFIG, BOTH_SECRETS)

    response = await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={"secrets": {"channel_0[psk]": PSK}})

    assert response.status_code == 202, response.text
    assert (await _wait_until_ended(client, run_id))["status"] == "complete"


# Ruling 8: position overrides are complete.


@pytest.mark.parametrize("position", [{"lat": 52.5}, {"lon": 13.4}])
async def test_a_fleet_device_with_only_one_of_lat_and_lon_gives_a_row_error(client, position):
    profile_id = await _profile(client, "Event", {})
    fleet = await client.post("/api/v1/fleets", json={"name": "F", "profile_id": profile_id})

    response = await client.put(
        f"/api/v1/fleets/{fleet.json()['id']}/devices", json={"devices": [{"label": "a", **position}]}
    )

    assert response.status_code == 422
    assert [(error["row"], error["msg"]) for error in response.json()["detail"]] == [
        (1, "a fleet device position needs both lat and lon")
    ]


def test_a_device_position_with_gps_off_writes_only_the_fixed_position_keys():
    profile = {
        "gps_enabled": "false",
        "gps_mode": "ENABLED",
        "gps_update_interval": "120",
        "position_broadcast_interval": "900",
        "smart_position_enabled": "true",
    }
    device = {name: None for name in fleet_runs.OVERRIDES} | {"lat": 52.5, "lon": 13.4, "alt": 34}

    content, errors = fleet_runs._config_errors(fleet_runs._device_fields(profile, device), None)

    assert errors == []
    prefs = json.loads(re.sub("//.*", "", content, flags=re.MULTILINE))
    gps_keys = {key for key in prefs if "GPS" in key or "POSITION" in key}
    assert gps_keys == {
        "USERPREFS_FIXED_GPS",
        "USERPREFS_FIXED_GPS_LAT",
        "USERPREFS_FIXED_GPS_LON",
        "USERPREFS_FIXED_GPS_ALT",
    }


# Ruling 9: fleet runs survive fleet changes.


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_fleet_with_a_running_fleet_run_cannot_be_deleted(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    fleet_id = await _fleet(client)
    await _start(client, fleet_id)

    response = await client.delete(f"/api/v1/fleets/{fleet_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "The fleet has a running fleet run."
    assert (await client.get(f"/api/v1/fleets/{fleet_id}")).status_code == 200


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_retry_uses_the_profile_of_the_fleet_run(app, client, monkeypatch):
    fleet_id, run_id = await _partial_run(client, monkeypatch)
    other = await _profile(client, "Other", {"owner_short_name": "OTH"})
    assert (await client.put(f"/api/v1/fleets/{fleet_id}", json={"profile_id": other})).status_code == 200

    assert (await client.post(f"/api/v1/fleet-runs/{run_id}/retry", json={})).status_code == 202
    run = await _wait_until_ended(client, run_id)

    retried = next(device for device in run["devices"] if device["label"] == "gate-2")
    prefs = json.dumps(_prefs(app, retried))
    assert "EVT" in prefs
    assert "OTH" not in prefs


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_a_stopped_fleet_run_whose_devices_are_all_complete_reads_complete(client, settings):
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]
    await _wait_until_ended(client, run_id)
    # A restart after the last build ends but before the status update leaves the row at `running`.
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("UPDATE fleet_runs SET status = 'running'")
        await db.commit()

    assert (await _run(client, run_id))["status"] == "complete"


# Ruling 10: fleet device ids are stable.


async def _editor_fleet(client, profile_name="Event") -> int:
    profile_id = await _profile(client, profile_name, {})
    response = await client.post("/api/v1/fleets", json={"name": "F", "profile_id": profile_id})
    return response.json()["id"]


async def test_put_devices_matches_a_renamed_row_by_its_id_and_keeps_its_admin_key(client, settings):
    fleet_id = await _editor_fleet(client)
    rows = [{"label": "a", "admin_key": DEVICES[0]["admin_key"]}, {"label": "b"}]
    saved = (await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": rows})).json()["devices"]

    renamed = [{"id": saved[0]["id"], "label": "a-renamed"}, {"id": saved[1]["id"], "label": "b"}]
    response = await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": renamed})

    assert response.status_code == 200, response.text
    devices = response.json()["devices"]
    assert [(device["id"], device["label"]) for device in devices] == [
        (saved[0]["id"], "a-renamed"),
        (saved[1]["id"], "b"),
    ]
    assert devices[0]["has_admin_key"] is True


async def test_put_devices_matches_an_id_only_inside_its_own_fleet(client):
    first = await _editor_fleet(client)
    second = await _editor_fleet(client, "Other")
    put_first = await client.put(f"/api/v1/fleets/{first}/devices", json={"devices": [{"label": "a"}]})
    put_second = await client.put(f"/api/v1/fleets/{second}/devices", json={"devices": [{"label": "y"}]})
    other_id, own_id = put_first.json()["devices"][0]["id"], put_second.json()["devices"][0]["id"]

    rows = [{"id": own_id, "label": "y-renamed"}, {"id": other_id, "label": "x"}]
    response = await client.put(f"/api/v1/fleets/{second}/devices", json={"devices": rows})

    devices = response.json()["devices"]
    assert (devices[0]["id"], devices[0]["label"]) == (own_id, "y-renamed")
    assert devices[1]["id"] not in (own_id, other_id)
    assert [device["label"] for device in (await client.get(f"/api/v1/fleets/{first}")).json()["devices"]] == ["a"]


async def test_the_device_routes_of_the_page_return_no_admin_key_to_an_admin_session(client, settings):
    fleet_id = await _editor_fleet(client)
    sign_in_admin(client, settings)
    rows = [{"label": "a", "admin_key": DEVICES[0]["admin_key"]}]

    table = await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": rows})
    imported = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json={"csv": "label\nb\n"})

    for response in (table, imported):
        assert response.status_code == 200, response.text
        assert DEVICES[0]["admin_key"] not in response.text
        assert response.json()["devices"][0]["has_admin_key"] is True
