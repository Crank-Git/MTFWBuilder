"""Tests for the fleet pages: create a fleet, start a fleet run, and the fleet run page (fleet-builds, FR 10 and 16)."""

import re

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_fleet_runs import PSK, PSK_CONFIG, _fleet, _start, _wait_until_ended


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


HX = {"HX-Request": "true"}


def _rows(html: str) -> list[str]:
    return re.findall(r'<tr class="fleet-run-row".*?</tr>', html, flags=re.DOTALL)


async def test_the_fleets_page_has_a_form_that_creates_a_fleet_from_a_profile(client):
    await client.post("/api/v1/profiles", json={"name": "Ridge SAR base", "config": {}})

    response = await client.get("/fleets")

    assert response.status_code == 200
    form = re.search(r'<form id="create-fleet-form".*?</form>', response.text, flags=re.DOTALL).group(0)
    for name in ("name", "profile_id", "firmware_version"):
        assert f'name="{name}"' in form
    assert "Ridge SAR base" in form


async def test_the_fleets_page_without_a_profile_says_to_save_one_first(client):
    response = await client.get("/fleets")

    assert 'id="create-fleet-form"' not in response.text
    assert "Save a profile on the build page first." in response.text


async def test_the_profiles_page_links_to_the_fleets_page(client):
    response = await client.get("/profiles")

    assert re.search(r'Create a fleet from a profile on the <a href="/fleets">', response.text)


async def test_the_fleet_page_asks_once_for_each_must_reenter_secret_of_the_profile(client):
    fleet_id = await _fleet(client, config=PSK_CONFIG)

    response = await client.get(f"/fleets/{fleet_id}")

    secret_inputs = re.findall(r'<input type="password"[^>]*data-secret="([^"]+)"', response.text)
    assert secret_inputs == ["channel_0[psk]"]
    assert re.search(r'id="startRunBtn"[^>]*>\s*Start fleet run \(3 builds\)', response.text)


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_the_fleet_run_page_shows_one_row_per_fleet_device_with_its_build_status(client):
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]
    await _wait_until_ended(client, run_id)

    response = await client.get(f"/fleet-runs/{run_id}")

    assert response.status_code == 200
    rows = _rows(response.text)
    assert len(rows) == 3
    for label, row in zip(("gate-1", "gate-2", "gate-3"), rows, strict=True):
        assert label in row
        assert "complete" in row
    assert f'href="/api/v1/fleet-runs/{run_id}/download"' in response.text
    assert "every 1s" not in response.text


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_an_htmx_request_of_a_running_fleet_run_gets_the_state_that_polls_again(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "30")
    run_id = (await _start(client, await _fleet(client))).json()["run_id"]

    response = await client.get(f"/fleet-runs/{run_id}", headers=HX)

    assert response.status_code == 200
    assert "<html" not in response.text
    assert re.search(rf'hx-get="/fleet-runs/{run_id}"[^>]*hx-trigger="every 1s"', response.text)
    assert len(_rows(response.text)) == 3
    assert "/download" not in response.text


@pytest.mark.usefixtures("fake_pio", "firmware_source", "workers")
async def test_the_page_of_a_partial_fleet_run_offers_retry_failed_with_the_secret_inputs(client, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL_MATCH", "Gate Two")
    fleet_id = await _fleet(client, config=PSK_CONFIG)
    run_id = (await _start(client, fleet_id, {"channel_0[psk]": PSK})).json()["run_id"]
    assert (await _wait_until_ended(client, run_id))["status"] == "partial"

    response = await client.get(f"/fleet-runs/{run_id}")

    assert 'id="retryBtn"' in response.text
    assert re.findall(r'<input type="password"[^>]*data-secret="([^"]+)"', response.text) == ["channel_0[psk]"]
    assert PSK not in response.text


async def test_the_page_of_an_unknown_fleet_run_returns_404(client):
    response = await client.get("/fleet-runs/999")

    assert response.status_code == 404
