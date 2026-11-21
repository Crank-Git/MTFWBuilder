"""Tests for the variants API, the device image route and the build check against the registry (feature 10)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import record_firmware_source
from mtfwbuilder.services.registry_generator import registry_entries
from tests.conftest import FIRMWARE_VERSION, REPO_ROOT

PROJECT_CONFIG = json.loads((REPO_ROOT / "tests" / "fixtures" / "firmware" / "project_config.json").read_text())
REGISTRY = registry_entries(PROJECT_CONFIG)
ALPHA = "v2.8.2.d5f1619"
CONFIG_JSON = json.dumps({"owner_long_name": "TestNode"})
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
# The detail of the image route, which differs from the detail of a path that has no route.
IMAGE_NOT_FOUND = {"detail": "Image not found"}


def _install(settings, version, entries, channel="stable", installed_at="2026-11-01T00:00:00+00:00"):
    """Return the directory of a ready firmware source of `version`, after its record and registry are written."""
    source_dir = settings.firmware_dir / version
    source_dir.mkdir(parents=True)
    record = {
        "version": version,
        "channel": channel,
        "path": str(source_dir),
        "installed_at": installed_at,
        "size_bytes": 0,
        "status": "ready",
    }
    record_firmware_source(settings.database_path, record, entries)
    return source_dir


def _renamed(entries, old, new):
    """Return a copy of `entries` in which the env `old` has the name `new`."""
    return [{**entry, "env": new} if entry["env"] == old else entry for entry in entries]


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def stable_source(app, settings):
    return _install(settings, FIRMWARE_VERSION, REGISTRY)


@pytest.fixture
def two_versions(stable_source, settings):
    """Install the alpha firmware version, in which upstream renamed `heltec-v4` to `heltec-v4-r2`."""
    entries = _renamed(REGISTRY, "heltec-v4", "heltec-v4-r2")
    _install(settings, ALPHA, entries, channel="alpha", installed_at="2026-11-10T00:00:00+00:00")


async def _envs(client, **params):
    resp = await client.get("/api/v1/variants", params=params)
    assert resp.status_code == 200
    return {entry["env"] for entry in resp.json()}


# GET /api/v1/variants


async def test_variants_of_the_default_firmware_version_hold_no_extra_variant(client, two_versions):
    envs = await _envs(client)

    # The default firmware version is the stable one, which still has the old env name.
    assert "heltec-v4" in envs
    assert "rak4631_dbg" not in envs
    assert envs == {e["env"] for e in REGISTRY if e["support_level"] != "extra"}


async def test_variants_with_experimental_hold_the_extra_variants(client, stable_source):
    envs = await _envs(client, experimental="true")

    assert envs == {e["env"] for e in REGISTRY}


async def test_variants_of_a_chosen_firmware_version_hold_its_registry(client, two_versions):
    envs = await _envs(client, version=ALPHA)

    assert "heltec-v4-r2" in envs
    assert "heltec-v4" not in envs


async def test_search_heltec_v4_lists_heltec_v4(client, stable_source):
    envs = await _envs(client, q="heltec v4")

    assert envs == {"heltec-v4", "heltec-v4-tft"}


async def test_search_ignores_case_and_matches_the_manufacturer(client, stable_source):
    assert await _envs(client, q="LILYGO") == {"t-echo"}
    assert await _envs(client, q="rak-4631") == {"rak4631"}


async def test_architecture_filter_keeps_only_that_architecture(client, stable_source):
    assert await _envs(client, arch="rp2040") == {"pico", "rak11310"}
    assert await _envs(client, arch=["rp2040", "esp32s3"]) == {
        "pico",
        "rak11310",
        "heltec-v4",
        "heltec-v4-tft",
        "heltec-v3",
    }


async def test_variant_entry_holds_the_registry_fields(client, stable_source):
    resp = await client.get("/api/v1/variants", params={"q": "heltec-v4-tft"})

    entry = next(e for e in REGISTRY if e["env"] == "heltec-v4-tft")
    assert resp.json() == [{**entry, "image_url": None}]
    assert resp.json()[0]["display_name"] == "Heltec V4 TFT"
    assert resp.json()[0]["has_ink_hud"] is False


async def test_variant_entry_gives_the_image_url_when_the_firmware_source_holds_the_image(client, stable_source):
    (stable_source / "pico.svg").write_bytes(SVG)

    entries = (await client.get("/api/v1/variants", params={"arch": "rp2040"})).json()

    urls = {entry["env"]: entry["image_url"] for entry in entries}
    assert urls == {"pico": f"/api/v1/variants/pico/image?version={FIRMWARE_VERSION}", "rak11310": None}


async def test_no_native_env_appears_in_the_variants(client, stable_source):
    entries = (await client.get("/api/v1/variants", params={"experimental": "true"})).json()

    assert entries
    assert all(entry["architecture"] != "native" for entry in entries)


async def test_variants_without_a_firmware_source_are_an_empty_list(client):
    resp = await client.get("/api/v1/variants")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_variants_of_a_firmware_version_that_is_not_installed_give_404(client, stable_source):
    resp = await client.get("/api/v1/variants", params={"version": "v9.9.9.0000000"})

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Firmware version v9.9.9.0000000 is not installed"}


# GET /api/v1/variants/{env}/image (FR-generated-registry-13)


async def test_image_route_sends_the_svg_from_the_firmware_source(client, stable_source):
    (stable_source / "heltec_v4.svg").write_bytes(SVG)

    resp = await client.get("/api/v1/variants/heltec-v4/image")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert resp.content == SVG


async def test_image_route_sends_a_png_with_the_png_content_type(app, client, settings):
    source_dir = _install(settings, FIRMWARE_VERSION, [{**REGISTRY[0], "image": "pico.png"}])
    (source_dir / "pico.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    resp = await client.get(f"/api/v1/variants/{REGISTRY[0]['env']}/image", params={"version": FIRMWARE_VERSION})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


async def test_image_route_gives_404_when_the_firmware_source_does_not_hold_the_image(client, stable_source):
    resp = await client.get("/api/v1/variants/heltec-v4/image")

    assert resp.status_code == 404
    assert resp.json() == IMAGE_NOT_FOUND


async def test_image_route_gives_404_for_an_env_with_no_image(client, stable_source):
    resp = await client.get("/api/v1/variants/t-echo-inkhud/image")

    assert resp.status_code == 404
    assert resp.json() == IMAGE_NOT_FOUND


async def test_image_route_gives_404_for_an_env_not_in_the_firmware_version(client, two_versions):
    resp = await client.get("/api/v1/variants/heltec-v4/image", params={"version": ALPHA})

    assert resp.status_code == 404
    assert resp.json() == IMAGE_NOT_FOUND


@pytest.mark.parametrize("image", ["../outside.svg", "/etc/outside.svg", "sub/../../outside.svg"])
async def test_image_route_never_sends_a_file_outside_the_firmware_source(app, client, settings, image):
    source_dir = _install(settings, FIRMWARE_VERSION, [{**REGISTRY[0], "image": image}])
    (source_dir.parent / "outside.svg").write_bytes(SVG)

    resp = await client.get(f"/api/v1/variants/{REGISTRY[0]['env']}/image")

    assert resp.status_code == 404
    assert resp.json() == IMAGE_NOT_FOUND


async def test_image_route_never_sends_a_file_that_is_not_svg_or_png(app, client, settings):
    source_dir = _install(settings, FIRMWARE_VERSION, [{**REGISTRY[0], "image": "platformio.ini"}])
    (source_dir / "platformio.ini").write_text("[env]")

    resp = await client.get(f"/api/v1/variants/{REGISTRY[0]['env']}/image")

    assert resp.status_code == 404
    assert resp.json() == IMAGE_NOT_FOUND


# FR-generated-registry-15: the build route checks the variant against the registry of the chosen firmware version


async def _build(client, variant, firmware_version=None, headers=None):
    data = {"variant": variant, "config_source": "current", "config_json": CONFIG_JSON}
    if firmware_version:
        data["firmware_version"] = firmware_version
    return await client.post("/api/v1/build-firmware", data=data, headers=headers)


async def test_build_of_an_env_not_in_the_chosen_firmware_version_returns_422_on_variant(client, two_versions):
    resp = await _build(client, "heltec-v4", ALPHA)

    assert resp.status_code == 422
    assert resp.json()["detail"] == [
        {"type": "value_error", "loc": ["body", "variant"], "msg": f"Variant not in {ALPHA}", "input": None}
    ]


async def test_build_of_an_env_renamed_upstream_is_accepted_in_the_older_firmware_version(app, client, two_versions):
    resp = await _build(client, "heltec-v4", FIRMWARE_VERSION)

    assert resp.status_code == 202
    ctx = app.state.build_queue.get(resp.json()["build_id"])
    assert ctx.variant.id == "heltec-v4"
    assert ctx.variant.name == "Heltec V4"
    assert ctx.variant.architecture == "esp32s3"
    assert ctx.variant.firmware_format == "bin"


async def test_build_of_the_renamed_env_is_accepted_in_the_newer_firmware_version(client, two_versions):
    resp = await _build(client, "heltec-v4-r2", ALPHA)

    assert resp.status_code == 202


async def test_build_without_a_firmware_version_checks_the_registry_of_the_default_version(client, two_versions):
    resp = await _build(client, "heltec-v4-r2")

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["msg"] == f"Variant not in {FIRMWARE_VERSION}"


async def test_build_page_shows_the_variant_message_for_an_env_not_in_the_version(client, two_versions):
    resp = await _build(client, "heltec-v4", ALPHA, headers={"HX-Request": "true"})

    assert resp.status_code == 422
    assert f"Variant not in {ALPHA}" in resp.text


async def test_build_of_a_ready_firmware_version_without_a_registry_returns_422_on_variant(app, client, settings):
    # A firmware source imported before feature 10 is ready and has no registry rows.
    _install(settings, FIRMWARE_VERSION, [])

    resp = await _build(client, "heltec-v3")

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "variant"]
    assert resp.json()["detail"][0]["msg"] == f"Variant not in {FIRMWARE_VERSION}"


async def test_build_of_a_firmware_version_that_is_not_installed_returns_422_on_firmware_version(client, stable_source):
    resp = await _build(client, "heltec-v4", "v9.9.9.0000000")

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "firmware_version"]
