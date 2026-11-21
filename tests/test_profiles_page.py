"""Tests for the profiles page and the profile controls of the build page (feature fleet-builds, FR 1 and 4)."""

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.models import ProfileRequest
from mtfwbuilder.routers import pages

EMPTY_STATE = "No profiles yet. Save one from the build page."
STORES_SECRETS_NOTICE = "This profile stores secrets on the server."


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _save(client, name, config=None):
    config = config or {"owner_long_name": "Event Node", "channel_0[psk]": "deadbeef" * 8}
    response = await client.post("/api/v1/profiles", json={"name": name, "config": config})
    assert response.status_code == 201
    return response.json()


async def test_profiles_page_without_profiles_shows_the_empty_state(client):
    response = await client.get("/profiles")

    assert response.status_code == 200
    assert EMPTY_STATE in response.text


async def test_profiles_page_lists_each_profile_in_name_order(client):
    await _save(client, "Ridge SAR base")
    await _save(client, "Aid station")

    text = (await client.get("/profiles")).text

    assert EMPTY_STATE not in text
    assert text.index("Aid station") < text.index("Ridge SAR base")


async def test_profiles_page_gives_each_profile_load_export_rename_and_delete(client):
    profile = await _save(client, "Ridge SAR base")

    text = (await client.get("/profiles")).text

    assert f'href="/?profile={profile["id"]}"' in text
    assert f'href="/api/v1/profiles/{profile["id"]}/export"' in text
    assert f'data-rename="{profile["id"]}"' in text
    assert f'data-delete="{profile["id"]}"' in text


async def test_profiles_page_has_a_delete_confirm_state_for_each_profile(client):
    await _save(client, "Ridge SAR base")

    text = (await client.get("/profiles")).text

    assert "Delete the profile Ridge SAR base? You cannot undo this." in text
    assert "data-confirm-delete" in text


async def test_profiles_page_names_the_must_reenter_fields(client):
    await _save(client, "Ridge SAR base")

    text = (await client.get("/profiles")).text

    assert "Must re-enter: channel_0[psk]" in text


async def test_profiles_page_escapes_the_profile_name(client):
    await _save(client, "<script>alert(1)</script>")

    text = (await client.get("/profiles")).text

    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


async def test_profiles_page_shows_the_notice_only_for_a_profile_that_stores_secrets(client, settings):
    stored = await _save(client, "Stored")
    await _save(client, "Plain")
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("UPDATE profiles SET stores_secrets = 1 WHERE id = ?", (stored["id"],))
        await db.commit()

    text = (await client.get("/profiles")).text

    assert text.count(STORES_SECRETS_NOTICE) == 1


async def test_profiles_page_for_htmx_returns_the_profile_list_only(client):
    await _save(client, "Ridge SAR base")

    response = await client.get("/profiles", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="profile-list"' in response.text
    assert "Ridge SAR base" in response.text


@pytest.mark.parametrize("path", ["/", "/profiles"])
async def test_page_header_links_to_the_profiles_page_and_the_build_page(client, path):
    text = (await client.get(path)).text

    assert 'href="/profiles"' in text
    assert 'href="/"' in text


async def test_build_page_has_save_as_profile(client):
    text = (await client.get("/")).text

    assert 'id="profileName"' in text
    assert 'id="saveProfileBtn"' in text
    assert "Save as profile" in text


async def test_build_page_lists_each_profile_to_load(client):
    profile = await _save(client, "Ridge SAR base")

    text = (await client.get("/")).text

    assert 'id="loadProfileBtn"' in text
    assert f'<option value="{profile["id"]}">Ridge SAR base</option>' in text


async def test_build_page_profile_controls_are_outside_the_config_form(client):
    text = (await client.get("/")).text

    form = text[text.index('<form id="configForm"') : text.index("</form>")]
    assert 'id="profileName"' not in form
    assert 'id="profileSelect"' not in form


async def test_build_page_without_stored_secret_support_omits_the_store_secrets_option(client, monkeypatch):
    class WithoutStoredSecrets(ProfileRequest):
        pass

    monkeypatch.setattr(pages, "ProfileRequest", WithoutStoredSecrets)

    text = (await client.get("/")).text

    assert 'id="profileStoresSecrets"' not in text


async def test_build_page_with_stored_secret_support_offers_the_store_secrets_option(client, monkeypatch):
    class WithStoredSecrets(ProfileRequest):
        stores_secrets: bool = False

    monkeypatch.setattr(pages, "ProfileRequest", WithStoredSecrets)

    text = (await client.get("/")).text

    assert 'id="profileStoresSecrets"' in text
