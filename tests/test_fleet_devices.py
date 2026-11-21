"""Tests for the fleet device table form and the CSV import, with row errors (feature fleet-builds, FR 12 and 13)."""

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.meshtastic_enums import ROUTER_ROLE_MESSAGE
from tests.conftest import FIRMWARE_VERSION, sign_in_admin

HEADER = "label,variant,long_name,short_name,role,lat,lon,alt,admin_key"
ADMIN_KEY = "cafebabe" * 8


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def fleet_id(client, firmware_source):
    response = await client.post("/api/v1/profiles", json={"name": "Event", "config": {"owner_short_name": "EVT"}})
    body = {"name": "Field day", "profile_id": response.json()["id"], "firmware_version": FIRMWARE_VERSION}
    response = await client.post("/api/v1/fleets", json=body)
    return response.json()["id"]


def _csv(*rows: str) -> dict:
    return {"csv": "\n".join((HEADER, *rows)) + "\n"}


def _row_errors(response) -> list[tuple[int, str | None, str]]:
    return [(error["row"], error["field"], error["msg"]) for error in response.json()["detail"]]


async def _labels(client, fleet_id) -> list[str]:
    response = await client.get(f"/api/v1/fleets/{fleet_id}")
    return [device["label"] for device in response.json()["devices"]]


async def test_a_csv_of_3_rows_with_one_bad_role_shows_one_error_that_names_row_2(client, fleet_id):
    response = await client.post(
        f"/api/v1/fleets/{fleet_id}/devices/csv",
        json=_csv("gate-1,heltec-v3,,,CLIENT,,,,", "gate-2,heltec-v3,,,ROUTER,,,,", "gate-3,t-echo,,,TRACKER,,,,"),
    )

    assert response.status_code == 422
    assert _row_errors(response) == [(2, "role", ROUTER_ROLE_MESSAGE)]


async def test_a_csv_with_an_error_saves_no_fleet_device(client, fleet_id):
    response = await client.post(
        f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("gate-1,heltec-v3", "gate-2,,,,ROUTER")
    )

    assert _row_errors(response) == [(2, "role", ROUTER_ROLE_MESSAGE)]
    assert await _labels(client, fleet_id) == []


async def test_a_valid_csv_adds_each_row_as_a_fleet_device(client, fleet_id):
    response = await client.post(
        f"/api/v1/fleets/{fleet_id}/devices/csv",
        json=_csv("gate-1,heltec-v3,Gate One,G1,TRACKER,52.5,13.4,34,", "gate-2,,,,,,,,"),
    )

    assert response.status_code == 200
    first, second = response.json()["devices"]
    assert first["label"] == "gate-1"
    assert first["variant"] == "heltec-v3"
    assert first["long_name"] == "Gate One"
    assert first["role"] == "meshtastic_Config_DeviceConfig_Role_TRACKER"
    assert (first["lat"], first["lon"], first["alt"]) == (52.5, 13.4, 34)
    assert second["variant"] is None
    assert second["lat"] is None


async def test_a_csv_adds_its_rows_after_the_saved_fleet_devices(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "saved"}]})

    await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("new"))

    assert await _labels(client, fleet_id) == ["saved", "new"]


async def test_a_csv_shows_every_row_error_at_once(client, fleet_id):
    response = await client.post(
        f"/api/v1/fleets/{fleet_id}/devices/csv",
        json=_csv("a,,,TOOLONG,,,,,", "b,,,,,91,,,", "c,,,,LOST_AND_FOUND,,,,"),
    )

    errors = _row_errors(response)
    assert [(row, field) for row, field, _ in errors] == [(1, "short_name"), (2, "lat"), (3, "role")]


async def test_two_fleet_devices_with_the_same_label_give_a_row_error(client, fleet_id):
    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("gate-1", "gate-2", "gate-1"))

    assert response.status_code == 422
    assert _row_errors(response) == [(3, "label", "the label gate-1 is in row 1 too")]


async def test_a_csv_label_that_a_saved_fleet_device_has_gives_a_row_error(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "gate-1"}]})

    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("gate-1"))

    assert _row_errors(response) == [(1, "label", "the fleet has a fleet device with the label gate-1")]


async def test_a_variant_that_is_not_in_the_chosen_firmware_version_gives_a_row_error(client, fleet_id):
    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("ok,heltec-v3", "bad,rak4631x"))

    assert response.status_code == 422
    assert _row_errors(response) == [
        (2, "variant", f"the variant rak4631x is not in firmware version {FIRMWARE_VERSION}")
    ]


async def test_a_variant_of_a_firmware_version_that_is_not_installed_gives_a_row_error(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}", json={"firmware_version": "v9.9.9.0000000"})

    response = await client.put(
        f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a", "variant": "pico"}]}
    )

    assert _row_errors(response) == [(1, "variant", "the variant pico is not in firmware version v9.9.9.0000000")]


async def test_a_csv_with_an_unknown_column_returns_422(client, fleet_id):
    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json={"csv": "label,fixed_lat\na,1\n"})

    assert response.status_code == 422
    assert _row_errors(response) == [
        (None, "csv", f"the header has the unknown column fixed_lat. Use the header {HEADER}.")
    ]


async def test_a_csv_that_starts_with_a_byte_order_mark_reads_the_label_column(client, fleet_id):
    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json={"csv": "﻿label\ngate-1\n"})

    assert response.status_code == 200
    assert await _labels(client, fleet_id) == ["gate-1"]


async def test_a_csv_without_a_label_column_returns_422(client, fleet_id):
    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json={"csv": "variant\npico\n"})

    assert _row_errors(response) == [(None, "csv", f"the header has no label column. Use the header {HEADER}.")]


async def test_a_csv_row_with_more_cells_than_the_header_gives_a_row_error(client, fleet_id):
    response = await client.post(
        f"/api/v1/fleets/{fleet_id}/devices/csv", json={"csv": "label,variant\na,pico,extra\n"}
    )

    assert _row_errors(response) == [(1, None, "the row has more cells than the header")]


async def test_a_csv_that_puts_the_fleet_over_the_size_limit_returns_422(client, fleet_id, settings):
    settings.max_fleet_size = 2

    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("a", "b", "c"))

    assert response.status_code == 422
    assert [error["msg"] for error in response.json()["detail"]] == ["a fleet holds at most 2 devices"]
    assert await _labels(client, fleet_id) == []


async def test_a_csv_for_an_unknown_fleet_returns_404(client, fleet_id):
    response = await client.post(f"/api/v1/fleets/{fleet_id + 1}/devices/csv", json=_csv("a"))

    assert response.status_code == 404
    assert response.json() == {"detail": f"Fleet {fleet_id + 1} does not exist"}


async def test_put_devices_replaces_every_fleet_device(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "old"}]})

    response = await client.put(
        f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a", "variant": "pico"}, {"label": "b"}]}
    )

    assert response.status_code == 200
    assert await _labels(client, fleet_id) == ["a", "b"]


async def _ids(client, fleet_id) -> dict[str, int]:
    response = await client.get(f"/api/v1/fleets/{fleet_id}")
    return {device["label"]: device["id"] for device in response.json()["devices"]}


async def test_put_devices_keeps_the_id_of_each_fleet_device_with_a_saved_label(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a"}, {"label": "b"}]})
    before = await _ids(client, fleet_id)

    rows = [{"label": "b", "long_name": "Bee"}, {"label": "c"}, {"label": "a"}]
    response = await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": rows})

    assert response.status_code == 200, response.text
    after = await _ids(client, fleet_id)
    assert (after["a"], after["b"]) == (before["a"], before["b"])
    assert after["c"] not in before.values()
    assert await _labels(client, fleet_id) == ["b", "c", "a"]
    assert response.json()["devices"][0]["long_name"] == "Bee"


async def test_put_devices_removes_the_fleet_device_of_a_label_that_is_not_in_the_rows(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a"}, {"label": "b"}]})
    before = await _ids(client, fleet_id)

    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "b"}]})

    assert await _ids(client, fleet_id) == {"b": before["b"]}


async def test_a_csv_import_keeps_the_ids_of_the_saved_fleet_devices(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a"}]})
    before = await _ids(client, fleet_id)

    await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("b"))

    assert (await _ids(client, fleet_id))["a"] == before["a"]


async def test_put_devices_numbers_the_rows_of_the_table_from_1(client, fleet_id):
    devices = [{"label": "a"}, {"label": "b", "role": "ROUTER_LATE"}, {"label": ""}, {"label": "a"}]

    response = await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": devices})

    assert response.status_code == 422
    assert [(row, field) for row, field, _ in _row_errors(response)] == [(2, "role"), (3, "label"), (4, "label")]
    assert await _labels(client, fleet_id) == []


async def test_put_devices_keeps_the_saved_admin_key_of_a_row_without_one(client, fleet_id, settings):
    sign_in_admin(client, settings)
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a", "admin_key": ADMIN_KEY}]})

    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a", "admin_key": None}]})

    response = await client.get(f"/api/v1/fleets/{fleet_id}")
    assert response.json()["devices"][0]["admin_key"] == ADMIN_KEY


async def test_a_row_error_never_holds_the_admin_key(client, fleet_id):
    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("a,,,,,,,,abcd1234"))

    assert _row_errors(response) == [(1, "admin_key", "the admin key is not 32 bytes")]
    assert "abcd1234" not in response.text


async def test_a_fleet_without_a_firmware_version_checks_the_variant_against_the_default(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}", json={"firmware_version": None})

    response = await client.post(f"/api/v1/fleets/{fleet_id}/devices/csv", json=_csv("a,nope"))

    assert _row_errors(response) == [(1, "variant", f"the variant nope is not in firmware version {FIRMWARE_VERSION}")]


async def test_fleets_page_links_each_fleet_to_its_editor(client, fleet_id):
    response = await client.get("/fleets")

    assert response.status_code == 200
    assert f'href="/fleets/{fleet_id}"' in response.text
    assert "Field day" in response.text


async def test_fleets_page_without_a_fleet_shows_the_empty_state(client):
    response = await client.get("/fleets")

    assert "No fleets yet." in response.text


async def test_fleet_page_shows_the_table_form_and_the_csv_box(client, fleet_id):
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "gate-1", "variant": "pico"}]})

    response = await client.get(f"/fleets/{fleet_id}")

    assert response.status_code == 200
    assert 'id="fleet-devices"' in response.text
    assert 'value="gate-1"' in response.text
    assert 'id="csvText"' in response.text
    assert HEADER in response.text


async def test_fleet_page_never_holds_a_saved_admin_key(client, fleet_id, settings):
    sign_in_admin(client, settings)
    await client.put(f"/api/v1/fleets/{fleet_id}/devices", json={"devices": [{"label": "a", "admin_key": ADMIN_KEY}]})

    response = await client.get(f"/fleets/{fleet_id}")

    assert ADMIN_KEY not in response.text
    assert 'placeholder="Saved"' in response.text


async def test_fleet_page_for_an_unknown_fleet_returns_404(client):
    response = await client.get("/fleets/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Fleet 999 does not exist"}
