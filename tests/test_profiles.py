"""Tests for the profiles table and the profile routes (feature fleet-builds, FR 1 to 4)."""

import json

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.auth import CSRF_HEADER
from mtfwbuilder.database import init_db
from mtfwbuilder.services import profiles
from tests.conftest import sign_in_admin

CHANNEL_PSK = "deadbeef" * 8
OTHER_CHANNEL_PSK = "0123abcd" * 4
WIFI_PSK = "wifi-secret-1234"
MQTT_PASSWORD = "mqtt-secret-5678"
ADMIN_KEY = "cafebabe" * 8
BEACON_PSK = "feedface" * 4
SECRETS = (CHANNEL_PSK, OTHER_CHANNEL_PSK, WIFI_PSK, MQTT_PASSWORD, ADMIN_KEY, BEACON_PSK)

CONFIG = {
    "owner_short_name": "EVT",
    "owner_long_name": "Event Node",
    "channels_to_write": "2",
    "channel_0[name]": "Primary",
    "channel_0[psk]": CHANNEL_PSK,
    "channel_1[name]": "Ops",
    "channel_1[psk]": OTHER_CHANNEL_PSK,
    "wifi_ssid": "event",
    "wifi_psk": WIFI_PSK,
    "mqtt_username": "node",
    "mqtt_password": MQTT_PASSWORD,
    "admin_key_0": ADMIN_KEY,
    "mesh_beacon_offer_channel_psk": BEACON_PSK,
}
MUST_REENTER = [
    "admin_key_0",
    "channel_0[psk]",
    "channel_1[psk]",
    "mesh_beacon_offer_channel_psk",
    "mqtt_password",
    "wifi_psk",
]


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _save(client, name="Event 2026", config=CONFIG):
    return await client.post("/api/v1/profiles", json={"name": name, "config": config})


async def _rows(database_path):
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM profiles")
        return [dict(row) for row in await cursor.fetchall()]


async def test_saved_profile_holds_no_psk_in_the_database(client, settings):
    response = await _save(client)

    assert response.status_code == 201
    rows = await _rows(settings.database_path)
    assert len(rows) == 1
    stored = json.dumps(rows[0])
    for secret in SECRETS:
        assert secret not in stored


async def test_save_marks_each_removed_secret_must_reenter(client):
    body = (await _save(client)).json()

    assert body["must_reenter"] == MUST_REENTER
    assert body["stores_secrets"] is False
    for field in MUST_REENTER:
        assert field not in body["config"]
    assert body["config"]["channel_0[name]"] == "Primary"
    assert body["config"]["wifi_ssid"] == "event"


async def test_save_keeps_an_empty_psk_because_it_holds_no_secret(client):
    body = (await _save(client, config={"channel_0[psk]": ""})).json()

    assert body["config"]["channel_0[psk]"] == ""
    assert body["must_reenter"] == []


async def test_save_removes_a_secret_in_an_extra_field(client, settings):
    body = (await _save(client, config={"oem_admin_key": ADMIN_KEY})).json()

    assert body["must_reenter"] == ["oem_admin_key"]
    assert ADMIN_KEY not in json.dumps(await _rows(settings.database_path))


async def test_save_with_a_duplicate_name_returns_409(client):
    await _save(client)

    response = await _save(client)

    assert response.status_code == 409


async def test_save_with_an_invalid_config_returns_422_with_the_field(client, settings):
    response = await _save(client, config={"channel_0[psk]": "abc"})

    assert response.status_code == 422
    assert ["body", "config", "channel_0", "psk"] in [error["loc"] for error in response.json()["detail"]]
    assert await _rows(settings.database_path) == []


async def test_save_with_an_empty_name_returns_422(client):
    response = await _save(client, name="  ")

    assert response.status_code == 422


async def test_list_returns_each_profile_in_name_order(client):
    await _save(client, name="b")
    await _save(client, name="a")

    response = await client.get("/api/v1/profiles")

    assert response.status_code == 200
    assert [profile["name"] for profile in response.json()] == ["a", "b"]


async def test_get_returns_the_saved_profile(client):
    saved = (await _save(client)).json()

    response = await client.get(f"/api/v1/profiles/{saved['id']}")

    assert response.status_code == 200
    assert response.json() == saved


async def test_get_of_an_unknown_profile_returns_404(client):
    response = await client.get("/api/v1/profiles/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile 999 does not exist"


async def test_put_with_a_name_renames_the_profile_and_keeps_the_config(client):
    saved = (await _save(client)).json()

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"name": "Event 2027"})

    assert response.status_code == 200
    assert response.json()["name"] == "Event 2027"
    assert response.json()["config"] == saved["config"]
    assert response.json()["must_reenter"] == saved["must_reenter"]


async def test_put_with_a_config_replaces_the_config_without_secrets(client, settings):
    saved = (await _save(client)).json()

    response = await client.put(
        f"/api/v1/profiles/{saved['id']}", json={"config": {"owner_short_name": "NEW", "wifi_psk": WIFI_PSK}}
    )

    assert response.status_code == 200
    assert response.json()["name"] == saved["name"]
    assert response.json()["config"]["owner_short_name"] == "NEW"
    assert "channel_0[name]" not in response.json()["config"]
    assert response.json()["must_reenter"] == ["wifi_psk"]
    assert WIFI_PSK not in json.dumps(await _rows(settings.database_path))


async def test_put_to_the_name_of_another_profile_returns_409(client):
    await _save(client, name="a")
    saved = (await _save(client, name="b")).json()

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"name": "a"})

    assert response.status_code == 409


async def test_put_without_a_name_or_a_config_returns_422(client):
    saved = (await _save(client)).json()

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={})

    assert response.status_code == 422


async def test_put_of_an_unknown_profile_returns_404(client):
    response = await client.put("/api/v1/profiles/999", json={"name": "x"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile 999 does not exist"


async def test_delete_removes_the_profile(client):
    saved = (await _save(client)).json()

    response = await client.delete(f"/api/v1/profiles/{saved['id']}")

    assert response.status_code == 204
    assert (await client.get(f"/api/v1/profiles/{saved['id']}")).status_code == 404


async def test_delete_of_an_unknown_profile_returns_404(client):
    response = await client.delete("/api/v1/profiles/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile 999 does not exist"


async def test_export_returns_a_json_file_without_secrets(client):
    saved = (await _save(client)).json()

    response = await client.get(f"/api/v1/profiles/{saved['id']}/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == 'attachment; filename="profile-Event_2026.json"'
    assert response.json() == {"name": saved["name"], "config": saved["config"], "must_reenter": MUST_REENTER}
    for secret in SECRETS:
        assert secret not in response.text


async def test_export_of_an_unknown_profile_returns_404(client):
    response = await client.get("/api/v1/profiles/999/export")

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile 999 does not exist"


async def test_init_db_drops_the_unused_config_profiles_table(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("CREATE TABLE config_profiles (id INTEGER PRIMARY KEY, name TEXT, config_json TEXT)")
        await db.commit()

    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = {row[0] for row in await cursor.fetchall()}
    assert "config_profiles" not in tables
    assert "profiles" in tables


def _sign_in(client, settings):
    client.headers[CSRF_HEADER] = sign_in_admin(client, settings)


def _sign_out(client):
    client.cookies.clear()
    client.headers.pop(CSRF_HEADER, None)


async def _save_with_secrets(client, config=CONFIG):
    return await client.post("/api/v1/profiles", json={"name": "Event 2026", "config": config, "stores_secrets": True})


async def _saved_with_secrets(client, settings):
    _sign_in(client, settings)
    saved = (await _save_with_secrets(client)).json()
    _sign_out(client)
    return saved


async def test_profile_that_stores_secrets_holds_no_psk_as_plain_text(client, settings):
    _sign_in(client, settings)

    response = await _save_with_secrets(client)

    assert response.status_code == 201
    rows = await _rows(settings.database_path)
    assert rows[0]["stores_secrets"] == 1
    assert rows[0]["secrets_ciphertext"]
    stored = json.dumps(rows[0])
    for secret in SECRETS:
        assert secret not in stored


async def test_save_with_store_secrets_without_an_admin_session_returns_403(client, settings):
    response = await _save_with_secrets(client)

    assert response.status_code == 403
    assert await _rows(settings.database_path) == []


async def test_save_with_store_secrets_without_a_csrf_token_returns_403(client, settings):
    _sign_in(client, settings)
    client.headers.pop(CSRF_HEADER)

    response = await _save_with_secrets(client)

    assert response.status_code == 403
    assert await _rows(settings.database_path) == []


async def test_load_with_an_admin_session_returns_the_stored_secrets(client, settings):
    saved = await _saved_with_secrets(client, settings)
    _sign_in(client, settings)

    body = (await client.get(f"/api/v1/profiles/{saved['id']}")).json()

    assert body["stores_secrets"] is True
    assert body["must_reenter"] == []
    assert body["warning"] is None
    assert "secrets_ciphertext" not in body
    for field in MUST_REENTER:
        assert body["config"][field] == CONFIG[field]
    assert body["config"]["channel_0[name]"] == "Primary"


async def test_load_without_an_admin_session_marks_the_stored_secrets_must_reenter(client, settings):
    saved = await _saved_with_secrets(client, settings)

    response = await client.get(f"/api/v1/profiles/{saved['id']}")

    assert response.status_code == 200
    assert response.json()["must_reenter"] == MUST_REENTER
    assert response.json()["warning"] == profiles.SIGN_IN_FOR_SECRETS
    for secret in SECRETS:
        assert secret not in response.text


async def test_list_never_returns_the_stored_secrets(client, settings):
    await _saved_with_secrets(client, settings)
    _sign_in(client, settings)

    response = await client.get("/api/v1/profiles")

    assert response.json()[0]["must_reenter"] == MUST_REENTER
    for secret in SECRETS:
        assert secret not in response.text


async def test_changed_secret_key_marks_the_stored_secrets_must_reenter(app, client, settings):
    saved = await _saved_with_secrets(client, settings)
    changed = settings.model_copy(update={"secret_key": "a-different-secret-key"})
    app.state.settings = changed
    _sign_in(client, changed)

    response = await client.get(f"/api/v1/profiles/{saved['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["must_reenter"] == MUST_REENTER
    assert body["warning"] == profiles.UNREADABLE_SECRETS
    for field in MUST_REENTER:
        assert field not in body["config"]


async def test_export_of_a_profile_that_stores_secrets_holds_no_secret(client, settings):
    saved = await _saved_with_secrets(client, settings)
    ciphertext = (await _rows(settings.database_path))[0]["secrets_ciphertext"]
    _sign_in(client, settings)

    response = await client.get(f"/api/v1/profiles/{saved['id']}/export")

    assert response.json()["must_reenter"] == MUST_REENTER
    assert ciphertext not in response.text
    for secret in SECRETS:
        assert secret not in response.text


async def test_put_with_a_config_keeps_the_store_secrets_option(client, settings):
    saved = await _saved_with_secrets(client, settings)
    _sign_in(client, settings)

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"config": {"wifi_psk": WIFI_PSK}})

    body = response.json()
    assert body["stores_secrets"] is True
    assert body["config"]["wifi_psk"] == WIFI_PSK
    assert body["must_reenter"] == []


async def test_put_with_a_config_to_a_profile_that_stores_secrets_without_an_admin_session_returns_403(
    client, settings
):
    saved = await _saved_with_secrets(client, settings)

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"config": {"wifi_psk": "other-psk"}})

    assert response.status_code == 403
    assert "other-psk" not in json.dumps(await _rows(settings.database_path))


async def test_put_with_a_name_without_an_admin_session_returns_no_stored_secret(client, settings):
    saved = await _saved_with_secrets(client, settings)

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"name": "Event 2027"})

    assert response.status_code == 200
    assert response.json()["name"] == "Event 2027"
    assert response.json()["warning"] == profiles.SIGN_IN_FOR_SECRETS
    for secret in SECRETS:
        assert secret not in response.text


async def test_put_with_store_secrets_off_deletes_the_stored_secrets(client, settings):
    saved = await _saved_with_secrets(client, settings)

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"stores_secrets": False})

    assert response.status_code == 200
    assert response.json()["stores_secrets"] is False
    assert response.json()["must_reenter"] == MUST_REENTER
    assert (await _rows(settings.database_path))[0]["secrets_ciphertext"] is None


async def test_put_with_store_secrets_on_and_no_config_marks_the_secrets_must_reenter(client, settings):
    saved = (await _save(client)).json()
    _sign_in(client, settings)

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"stores_secrets": True})

    assert response.status_code == 200
    assert response.json()["stores_secrets"] is True
    assert response.json()["must_reenter"] == MUST_REENTER
    assert response.json()["warning"] is None


async def test_put_with_store_secrets_on_without_an_admin_session_returns_403(client):
    saved = (await _save(client)).json()

    response = await client.put(f"/api/v1/profiles/{saved['id']}", json={"stores_secrets": True})

    assert response.status_code == 403


async def test_init_db_adds_the_secrets_column_to_an_old_profiles_table(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "CREATE TABLE profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, config_json TEXT NOT NULL, "
            "must_reenter_json TEXT NOT NULL, stores_secrets INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        await db.commit()

    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("PRAGMA table_info(profiles)")
        columns = {row[1] for row in await cursor.fetchall()}
    assert "secrets_ciphertext" in columns
