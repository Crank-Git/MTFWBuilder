"""Tests for the device picker on the build page (FR-generated-registry-8 to -12, feature 10 screens and states)."""

import json
import re
from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import record_firmware_source
from mtfwbuilder.services.registry_generator import registry_entries
from tests.conftest import FIRMWARE_VERSION, REPO_ROOT

PROJECT_CONFIG = json.loads((REPO_ROOT / "tests" / "fixtures" / "firmware" / "project_config.json").read_text())
REGISTRY = registry_entries(PROJECT_CONFIG)
HX = {"HX-Request": "true"}
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'


def _install(settings, version, entries, status="ready"):
    """Return the directory of a firmware source of `version`, after its record and registry are written."""
    source_dir = settings.firmware_dir / version
    source_dir.mkdir(parents=True)
    record = {
        "version": version,
        "channel": "stable",
        "path": str(source_dir),
        "installed_at": "2026-11-01T00:00:00+00:00",
        "size_bytes": 0,
        "status": status,
    }
    record_firmware_source(settings.database_path, record, entries)
    return source_dir


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def stable_source(app, settings):
    return _install(settings, FIRMWARE_VERSION, REGISTRY)


class _Parser(HTMLParser):
    """Collect the elements with their attributes, and the text of the fragment without its tags."""

    def __init__(self):
        super().__init__()
        self.elements = []
        self.text = ""

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text += data

    def by_id(self, element_id):
        return next((attrs for _, attrs in self.elements if attrs.get("id") == element_id), None)

    def envs(self):
        return [attrs["data-env"] for _, attrs in self.elements if "data-env" in attrs]


async def _fragment(client, **params):
    resp = await client.get("/api/v1/variants", params=params, headers=HX)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    parser = _Parser()
    parser.feed(resp.text)
    parser.text = " ".join(parser.text.split())
    return resp.text, parser


# FR-generated-registry-8 and -9


async def test_picker_without_the_toggle_shows_no_extra_variant(client, stable_source):
    _, parser = await _fragment(client)

    assert "rak4631" in parser.envs()
    assert "rak4631_dbg" not in parser.envs()


async def test_picker_with_the_toggle_shows_extra_variants(client, stable_source):
    _, parser = await _fragment(client, experimental="true")

    assert "rak4631_dbg" in parser.envs()


async def test_picker_row_shows_the_support_level_badge(client, stable_source):
    html, _ = await _fragment(client, experimental="true", q="rak4631_dbg")

    assert re.search(r'class="[^"]*support-extra[^"]*"[^>]*>\s*extra\s*<', html)


# FR-generated-registry-10


async def test_picker_search_heltec_v4_lists_heltec_v4(client, stable_source):
    _, parser = await _fragment(client, q="heltec v4")

    assert parser.envs() == ["heltec-v4", "heltec-v4-tft"]


async def test_picker_search_without_a_match_says_so_with_a_clear_control(client, stable_source):
    _, parser = await _fragment(client, q="zzz")

    assert parser.envs() == []
    assert "No device matches 'zzz'." in parser.text
    assert parser.by_id("device-clear") is not None


async def test_picker_no_match_message_shows_the_search_text_as_text(client, stable_source):
    html, _ = await _fragment(client, q="<b>x</b>")

    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


# FR-generated-registry-11


async def test_picker_has_one_chip_for_each_architecture_of_the_firmware_version(client, stable_source):
    _, parser = await _fragment(client, q="heltec")

    chips = [attrs["value"] for tag, attrs in parser.elements if tag == "input" and attrs.get("name") == "arch"]
    assert chips == ["esp32s3", "nrf52840", "rp2040"]


async def test_picker_chip_keeps_only_its_architecture_and_shows_as_checked(client, stable_source):
    _, parser = await _fragment(client, arch="rp2040")

    assert sorted(parser.envs()) == ["pico", "rak11310"]
    checked = [attrs["value"] for tag, attrs in parser.elements if attrs.get("name") == "arch" and "checked" in attrs]
    assert checked == ["rp2040"]


# FR-generated-registry-12


async def test_picker_groups_the_rows_by_manufacturer(client, stable_source):
    _, parser = await _fragment(client)

    groups = [attrs["data-manufacturer"] for _, attrs in parser.elements if "data-manufacturer" in attrs]
    # The group "Other" holds the variants without a manufacturer, so it comes last.
    assert groups == ["Heltec", "LilyGo", "RAK", "RPi", "Other"]


async def test_picker_row_shows_the_display_name_the_env_and_the_architecture(client, stable_source):
    _, parser = await _fragment(client, q="heltec-v4-tft")

    assert "Heltec V4 TFT" in parser.text
    assert "heltec-v4-tft" in parser.text
    assert "esp32s3" in parser.text


# FR-generated-registry-13 and the edge case "no image file"


async def test_picker_row_shows_the_image_when_the_firmware_source_holds_it(client, stable_source):
    (stable_source / "pico.svg").write_bytes(SVG)

    _, parser = await _fragment(client, q="pico")

    images = [attrs["src"] for tag, attrs in parser.elements if tag == "img"]
    assert images == [f"/api/v1/variants/pico/image?version={FIRMWARE_VERSION}"]


async def test_picker_row_without_an_image_file_shows_a_placeholder_and_no_img(client, stable_source):
    _, parser = await _fragment(client, q="heltec v4")

    assert [tag for tag, _ in parser.elements if tag == "img"] == []
    placeholders = [attrs for _, attrs in parser.elements if "device-image-placeholder" in attrs.get("class", "")]
    assert len(placeholders) == 2


async def test_picker_shows_not_actively_supported_for_such_a_variant(app, client, settings):
    entries = [{**entry, "actively_supported": entry["env"] != "pico"} for entry in REGISTRY]
    _install(settings, FIRMWARE_VERSION, entries)

    _, pico = await _fragment(client, q="pico")
    _, rak = await _fragment(client, q="rak11310")

    assert "Not actively supported" in pico.text
    assert "Not actively supported" not in rak.text


# Feature 10, screens and states


async def test_picker_without_a_firmware_version_says_no_firmware_version_is_installed(client):
    _, parser = await _fragment(client)

    assert parser.text == "No firmware version is installed."


async def test_picker_of_a_firmware_version_with_registry_failed_says_an_admin_must_reinstall_it(app, client, settings):
    _install(settings, FIRMWARE_VERSION, [], status="registry failed")

    _, parser = await _fragment(client, version=FIRMWARE_VERSION)

    assert parser.text == f"The device list for {FIRMWARE_VERSION} failed to load. An admin must reinstall it."


async def test_picker_of_a_ready_firmware_version_without_a_registry_says_an_admin_must_reinstall_it(
    app, client, settings
):
    # A firmware source imported before feature 10 is ready and has no registry rows.
    _install(settings, FIRMWARE_VERSION, [])

    _, parser = await _fragment(client, version=FIRMWARE_VERSION)

    assert parser.text == f"The device list for {FIRMWARE_VERSION} failed to load. An admin must reinstall it."


async def test_variants_without_htmx_stay_json(client, stable_source):
    resp = await client.get("/api/v1/variants", params={"q": "heltec v4"})

    assert resp.headers["content-type"] == "application/json"
    assert [entry["env"] for entry in resp.json()] == ["heltec-v4", "heltec-v4-tft"]


# The build page


async def _page(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    parser = _Parser()
    parser.feed(resp.text)
    return parser


async def test_build_page_has_the_picker_controls_and_no_variant_select(client, stable_source):
    parser = await _page(client)

    assert parser.by_id("variant") == {"type": "hidden", "id": "variant", "name": "variant", "value": ""}
    assert [tag for tag, attrs in parser.elements if attrs.get("name") == "variant"] == ["input"]
    assert parser.by_id("device-search")["name"] == "q"
    assert parser.by_id("device-experimental")["name"] == "experimental"
    assert parser.by_id("device-results") is not None


async def test_build_page_picker_reloads_from_the_variants_route_on_a_firmware_version_change(client, stable_source):
    parser = await _page(client)

    picker = parser.by_id("device-picker")
    assert picker["hx-get"] == "/api/v1/variants"
    assert picker["hx-target"] == "#device-results"
    triggers = [trigger.strip() for trigger in picker["hx-trigger"].split(",")]
    assert "load" in triggers
    assert "change from:#firmware_version" in triggers
    assert "change from:#device-experimental" in triggers


async def test_build_page_has_an_error_slot_for_the_variant(client, stable_source):
    parser = await _page(client)

    assert parser.by_id("error-variant") is not None


async def test_build_page_selector_lists_a_firmware_version_with_registry_failed(app, client, settings):
    _install(settings, FIRMWARE_VERSION, [], status="registry failed")

    parser = await _page(client)

    options = [attrs.get("value") for tag, attrs in parser.elements if tag == "option"]
    assert FIRMWARE_VERSION in options


async def test_build_page_shows_the_loading_state_before_the_list_arrives(client, stable_source):
    parser = await _page(client)

    assert parser.by_id("device-loading")["aria-busy"] == "true"
