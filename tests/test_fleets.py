"""Tests for the fleet tables and the fleet routes (feature fleet-builds, FR 10, 11 and 14)."""

import json

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.config import load_settings
from mtfwbuilder.database import init_db
from mtfwbuilder.meshtastic_enums import ROUTER_ROLE_MESSAGE
from tests.conftest import sign_in_admin

ADMIN_KEY = "cafebabe" * 8
OTHER_ADMIN_KEY = "0123abcd" * 8
FLEET_TABLES = ("fleets", "fleet_devices", "fleet_runs", "fleet_run_builds")

DEVICE = {
    "label": "gate-1",
    "variant": "heltec-v3",
    "long_name": "Gate One",
    "short_name": "G1",
    "role": "TRACKER",
    "lat": 52.5,
    "lon": 13.4,
    "alt": 34,
    "admin_key": ADMIN_KEY,
}


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def profile_id(client):
    response = await client.post("/api/v1/profiles", json={"name": "Event", "config": {"owner_short_name": "EVT"}})
    return response.json()["id"]


async def _create(client, profile_id, devices=(DEVICE,), **fields):
    body = {"name": "Field day", "profile_id": profile_id, "devices": list(devices), **fields}
    return await client.post("/api/v1/fleets", json=body)


async def _device_rows(database_path):
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM fleet_devices")
        return [dict(row) for row in await cursor.fetchall()]


def _errors(response, field):
    return [error["msg"] for error in response.json()["detail"] if field in error["loc"]]


async def test_a_fleet_of_51_devices_returns_422(client, profile_id, settings):
    devices = [{"label": f"node-{n}"} for n in range(51)]

    response = await _create(client, profile_id, devices)

    assert response.status_code == 422
    assert _errors(response, "devices") == ["a fleet holds at most 50 devices"]
    assert await _device_rows(settings.database_path) == []


async def test_a_fleet_of_50_devices_returns_201(client, profile_id):
    response = await _create(client, profile_id, [{"label": f"node-{n}"} for n in range(50)])

    assert response.status_code == 201
    assert len(response.json()["devices"]) == 50


async def test_max_fleet_size_setting_sets_the_limit(client, profile_id, settings):
    settings.max_fleet_size = 2

    response = await _create(client, profile_id, [{"label": f"node-{n}"} for n in range(3)])

    assert response.status_code == 422
    assert _errors(response, "devices") == ["a fleet holds at most 2 devices"]


def test_max_fleet_size_reads_the_environment_variable(settings_env, monkeypatch):
    monkeypatch.setenv("MTFW_MAX_FLEET_SIZE", "7")

    assert load_settings().max_fleet_size == 7


async def test_create_returns_the_fleet_with_its_devices(client, profile_id):
    response = await _create(client, profile_id, firmware_version="v2.7.26.54e0d8d")

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Field day"
    assert body["profile_id"] == profile_id
    assert body["firmware_version"] == "v2.7.26.54e0d8d"
    [device] = body["devices"]
    expected = {key: value for key, value in DEVICE.items() if key != "admin_key"}
    assert {key: device[key] for key in expected} == {**expected, "role": "meshtastic_Config_DeviceConfig_Role_TRACKER"}


async def test_a_fleet_device_needs_only_a_label(client, profile_id):
    response = await _create(client, profile_id, [{"label": "spare"}])

    [device] = response.json()["devices"]
    assert device["variant"] is None
    assert device["role"] is None
    assert device["has_admin_key"] is False


async def test_create_with_an_unknown_profile_returns_422(client, profile_id):
    response = await _create(client, profile_id + 1)

    assert response.status_code == 422
    assert _errors(response, "profile_id") == [f"profile {profile_id + 1} does not exist"]


async def test_list_returns_every_fleet(client, profile_id):
    await _create(client, profile_id, name="A")
    await _create(client, profile_id, name="B")

    response = await client.get("/api/v1/fleets")

    assert response.status_code == 200
    assert [fleet["name"] for fleet in response.json()] == ["A", "B"]


async def test_get_returns_404_for_an_unknown_fleet(client):
    response = await client.get("/api/v1/fleets/999")

    assert response.json()["detail"] == "Fleet 999 does not exist"


async def test_put_renames_the_fleet_and_keeps_its_devices(client, profile_id):
    fleet = (await _create(client, profile_id)).json()

    response = await client.put(f"/api/v1/fleets/{fleet['id']}", json={"name": "Night run"})

    assert response.status_code == 200
    assert response.json()["name"] == "Night run"
    assert [device["label"] for device in response.json()["devices"]] == ["gate-1"]


async def test_put_with_devices_replaces_the_devices(client, profile_id):
    fleet = (await _create(client, profile_id)).json()

    response = await client.put(f"/api/v1/fleets/{fleet['id']}", json={"devices": [{"label": "a"}, {"label": "b"}]})

    assert [device["label"] for device in response.json()["devices"]] == ["a", "b"]


async def test_put_with_51_devices_returns_422(client, profile_id):
    fleet = (await _create(client, profile_id)).json()

    response = await client.put(
        f"/api/v1/fleets/{fleet['id']}", json={"devices": [{"label": f"n{n}"} for n in range(51)]}
    )

    assert response.status_code == 422


async def test_put_with_an_empty_body_returns_422(client, profile_id):
    fleet = (await _create(client, profile_id)).json()

    response = await client.put(f"/api/v1/fleets/{fleet['id']}", json={})

    assert response.status_code == 422


async def test_put_returns_404_for_an_unknown_fleet(client):
    response = await client.put("/api/v1/fleets/999", json={"name": "X"})

    assert response.json()["detail"] == "Fleet 999 does not exist"


async def test_delete_removes_the_fleet_and_its_devices(client, profile_id, settings):
    fleet = (await _create(client, profile_id)).json()

    response = await client.delete(f"/api/v1/fleets/{fleet['id']}")

    assert response.status_code == 204
    assert (await client.get(f"/api/v1/fleets/{fleet['id']}")).status_code == 404
    assert await _device_rows(settings.database_path) == []


async def test_delete_returns_404_for_an_unknown_fleet(client):
    response = await client.delete("/api/v1/fleets/999")

    assert response.json()["detail"] == "Fleet 999 does not exist"


@pytest.mark.parametrize(
    "role", ["ROUTER", "ROUTER_LATE", "LOST_AND_FOUND", "meshtastic_Config_DeviceConfig_Role_ROUTER"]
)
async def test_a_router_role_returns_the_router_role_message(client, profile_id, role):
    response = await _create(client, profile_id, [{"label": "r", "role": role}])

    assert response.status_code == 422
    assert any(ROUTER_ROLE_MESSAGE in msg for msg in _errors(response, "role"))


@pytest.mark.parametrize("role", ["REPEATER", "ROUTER_CLIENT", "BOSS"])
async def test_a_role_that_the_form_does_not_offer_returns_422(client, profile_id, role):
    response = await _create(client, profile_id, [{"label": "r", "role": role}])

    assert response.status_code == 422
    assert _errors(response, "role")


async def test_a_short_name_of_5_characters_returns_422(client, profile_id):
    response = await _create(client, profile_id, [{"label": "x", "short_name": "ABCDE"}])

    assert response.status_code == 422
    assert _errors(response, "short_name")


async def test_a_short_name_of_4_characters_returns_201(client, profile_id):
    response = await _create(client, profile_id, [{"label": "x", "short_name": "ÄÖÜß"}])

    assert response.status_code == 201


async def test_a_long_name_of_26_bytes_returns_422(client, profile_id):
    # Each "é" is 2 bytes in UTF-8, so 13 of them are 13 characters and 26 bytes.
    response = await _create(client, profile_id, [{"label": "x", "long_name": "é" * 13}])

    assert response.status_code == 422
    assert _errors(response, "long_name")


async def test_a_long_name_of_25_bytes_returns_201(client, profile_id):
    response = await _create(client, profile_id, [{"label": "x", "long_name": "a" * 25}])

    assert response.status_code == 201


@pytest.mark.parametrize(
    ("field", "bad", "good"),
    [
        ("lat", 90.1, -90),
        ("lat", -90.1, 90),
        ("lon", 180.1, -180),
        ("lon", -180.1, 180),
        ("alt", 10001, -1000),
        ("alt", -1001, 10000),
    ],
)
async def test_a_coordinate_out_of_range_returns_422(client, profile_id, field, bad, good):
    rejected = await _create(client, profile_id, [{"label": "x", field: bad}])
    accepted = await _create(client, profile_id, [{"label": "x", field: good}])

    assert rejected.status_code == 422
    assert _errors(rejected, field)
    assert accepted.status_code == 201


async def test_an_empty_label_returns_422(client, profile_id):
    response = await _create(client, profile_id, [{"label": "  "}])

    assert response.status_code == 422
    assert _errors(response, "label")


async def test_an_admin_key_that_is_not_32_bytes_returns_422(client, profile_id):
    response = await _create(client, profile_id, [{"label": "x", "admin_key": "cafe"}])

    assert response.status_code == 422
    assert _errors(response, "admin_key")


async def test_a_name_with_a_double_slash_returns_422(client, profile_id):
    response = await _create(client, profile_id, [{"label": "x", "long_name": "a//b"}])

    assert response.status_code == 422
    assert _errors(response, "long_name")


async def test_the_admin_key_is_not_plain_text_in_the_database(client, profile_id, settings):
    await _create(client, profile_id)

    [row] = await _device_rows(settings.database_path)
    assert ADMIN_KEY not in json.dumps(row)
    assert row["admin_key_ciphertext"]


async def test_a_load_without_an_admin_session_gives_no_admin_key(client, profile_id):
    fleet = (await _create(client, profile_id)).json()

    for body in (
        fleet,
        (await client.get(f"/api/v1/fleets/{fleet['id']}")).json(),
        (await client.get("/api/v1/fleets")).json()[0],
    ):
        [device] = body["devices"]
        assert device["admin_key"] is None
        assert device["has_admin_key"] is True
        assert ADMIN_KEY not in json.dumps(body)


async def test_a_load_with_an_admin_session_gives_the_admin_key(client, profile_id, settings):
    fleet = (await _create(client, profile_id)).json()
    sign_in_admin(client, settings)

    response = await client.get(f"/api/v1/fleets/{fleet['id']}")

    assert response.json()["devices"][0]["admin_key"] == ADMIN_KEY
    assert response.headers["cache-control"] == "no-store"


async def test_the_list_gives_no_admin_key_to_an_admin_session(client, profile_id, settings):
    await _create(client, profile_id)
    sign_in_admin(client, settings)

    response = await client.get("/api/v1/fleets")

    assert response.json()[0]["devices"][0]["has_admin_key"] is True
    assert ADMIN_KEY not in response.text


async def test_put_keeps_the_admin_key_of_a_device_without_one(client, profile_id, settings):
    fleet = (await _create(client, profile_id)).json()
    unchanged = {**DEVICE, "admin_key": None}

    await client.put(f"/api/v1/fleets/{fleet['id']}", json={"devices": [unchanged, {"label": "new"}]})

    sign_in_admin(client, settings)
    devices = (await client.get(f"/api/v1/fleets/{fleet['id']}")).json()["devices"]
    assert [device["admin_key"] for device in devices] == [ADMIN_KEY, None]


async def test_put_with_an_empty_admin_key_removes_the_admin_key(client, profile_id):
    fleet = (await _create(client, profile_id)).json()

    response = await client.put(f"/api/v1/fleets/{fleet['id']}", json={"devices": [{**DEVICE, "admin_key": ""}]})

    assert response.json()["devices"][0]["has_admin_key"] is False


async def test_put_with_a_new_admin_key_replaces_the_admin_key(client, profile_id, settings):
    fleet = (await _create(client, profile_id)).json()

    await client.put(f"/api/v1/fleets/{fleet['id']}", json={"devices": [{**DEVICE, "admin_key": OTHER_ADMIN_KEY}]})

    sign_in_admin(client, settings)
    devices = (await client.get(f"/api/v1/fleets/{fleet['id']}")).json()["devices"]
    assert devices[0]["admin_key"] == OTHER_ADMIN_KEY


async def test_the_admin_key_is_not_in_a_log_line(client, profile_id, settings, caplog):
    caplog.set_level("DEBUG")

    fleet = (await _create(client, profile_id)).json()
    sign_in_admin(client, settings)
    await client.get(f"/api/v1/fleets/{fleet['id']}")

    assert ADMIN_KEY not in caplog.text


async def test_init_db_makes_each_fleet_table(settings):
    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = {row[0] for row in await cursor.fetchall()}
    assert set(FLEET_TABLES) <= tables


async def test_init_db_adds_fleet_run_id_to_an_old_builds_table(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "CREATE TABLE builds (id TEXT PRIMARY KEY, variant TEXT NOT NULL, architecture TEXT NOT NULL, "
            "firmware_format TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, config_hash TEXT, firmware_version TEXT)"
        )
        await db.commit()

    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("PRAGMA table_info(builds)")
        columns = {row[1] for row in await cursor.fetchall()}
    assert "fleet_run_id" in columns
