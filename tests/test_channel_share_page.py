"""Tests for the Import and Export panels of the build page (feature channel-share, Screens & states)."""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import REPO_ROOT

CHANNEL_SHARE_JS = REPO_ROOT / "static" / "js" / "channel-share.js"


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def build_page(client) -> str:
    response = await client.get("/")
    assert response.status_code == 200
    return response.text


async def test_import_panel_has_the_url_field_the_file_field_and_an_example_url_shape(client):
    page = await build_page(client)

    assert 'id="channelShareUrl"' in page
    assert 'id="channelShareFile"' in page
    assert 'accept=".yaml,.yml' in page
    assert "https://meshtastic.org/e/#" in page
    assert "https://meshtastic.org/e/?add=true#" in page


async def test_import_and_export_panels_are_outside_the_config_form(client):
    page = await build_page(client)

    # A field inside the config form goes into each preview, download and build request.
    panels_end = page.index("<!-- end channel share -->")
    assert page.index('id="channelShare"') < panels_end < page.index('id="configForm"')


async def test_build_page_loads_the_channel_share_script(client):
    page = await build_page(client)

    assert "/static/js/channel-share.js" in page


def test_channel_share_script_uses_no_browser_storage_and_no_inner_html():
    script = CHANNEL_SHARE_JS.read_text()

    # The import holds PSKs, and browser storage keeps them after the page closes.
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "innerHTML" not in script
