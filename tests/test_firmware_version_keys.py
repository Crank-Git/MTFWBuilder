"""Tests for the userPrefs keys of each firmware version (FR-config-coverage-16, criterion 8, edge case 1).

Two firmware sources are installed. Each is a copy of the fixture firmware source:
- `v2.7.20.6658ec2` (stable) reads the keys of the fixture, and no mesh beacon key.
- `v2.8.2.d5f1619` (alpha) also has a source file that reads channels 0 to 7, the mesh beacon keys and the event keys.
"""

import json
import os
import re
import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.database import record_firmware_source
from mtfwbuilder.models import CHANNEL_SLOTS
from mtfwbuilder.services import userprefs_keys
from mtfwbuilder.services.jsonc_generator import (
    KEY_FIELDS,
    VERSION_GATED_KEYS,
    generate_jsonc,
    version_errors,
)
from mtfwbuilder.services.userprefs_keys import SCAN_FILE, KeyScan, ensure_key_scan, read_keys
from tests.conftest import FIRMWARE_VERSION
from tests.test_config_mesh_beacon import UPSTREAM_KEYS as MESH_BEACON_KEYS
from tests.test_userprefs_contract import full_config

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "firmware"
OLD = "v2.7.20.6658ec2"
NEW = "v2.8.2.d5f1619"
REGION = "meshtastic_Config_LoRaConfig_RegionCode_"
EVENT_KEYS = ("USERPREFS_BLOCK_POSITION_ON_EVENT_CHANNEL", "USERPREFS_CANNED_MESSAGES")
NEW_KEYS = (
    MESH_BEACON_KEYS
    | set(EVENT_KEYS)
    | {f"USERPREFS_CHANNEL_{n}_{s}" for n in range(CHANNEL_SLOTS) for s in ("NAME", "PSK", "IS_MUTED")}
)
PSK_16 = "00" * 16


def _install_copy(settings, version: str, channel: str, extra_keys=()) -> Path:
    """Return a firmware source that copies the fixture, with one more source file that reads `extra_keys`."""
    source_dir = settings.firmware_dir / version
    source_dir.mkdir(parents=True)
    shutil.copy(FIXTURE_DIR / "userPrefs.jsonc", source_dir)
    shutil.copytree(FIXTURE_DIR / "src", source_dir / "src")
    (source_dir / "src" / "newer.cpp").write_text("".join(f"#ifdef {key}\n#endif\n" for key in sorted(extra_keys)))
    record = {
        "version": version,
        "channel": channel,
        "path": str(source_dir),
        "installed_at": "2026-11-01T00:00:00+00:00",
        "size_bytes": 0,
        "status": "ready",
    }
    record_firmware_source(settings.database_path, record)
    return source_dir


@pytest.fixture
def sources(app, settings):
    _install_copy(settings, OLD, "stable")
    _install_copy(settings, NEW, "alpha", NEW_KEYS)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _fieldsets(html: str, key_prefix: str) -> list[str]:
    return re.findall(rf'<fieldset[^>]*data-userprefs-key="{key_prefix}[^"]*"[^>]*>', html)


# The key scan


def test_scan_file_holds_the_keys_and_the_enum_values_of_the_firmware_source(tmp_path):
    source_dir = tmp_path / OLD
    shutil.copytree(FIXTURE_DIR, source_dir)

    scan = ensure_key_scan(source_dir)

    stored = json.loads((source_dir / SCAN_FILE).read_text())
    assert set(stored["keys"]) == read_keys(FIXTURE_DIR) == scan.keys
    assert f"{REGION}US" in scan.enum_values
    assert scan.version == OLD


def test_scan_file_stays_the_key_scan_after_a_build_replaces_userprefs_jsonc(tmp_path):
    source_dir = tmp_path / OLD
    shutil.copytree(FIXTURE_DIR, source_dir)
    first = ensure_key_scan(source_dir)
    (source_dir / "userPrefs.jsonc").write_text('{"USERPREFS_MESH_BEACON_MESSAGE": "x"}')

    assert ensure_key_scan(source_dir) == first


def test_scan_writes_the_whole_scan_file_in_one_rename(tmp_path, monkeypatch):
    source_dir = tmp_path / OLD
    shutil.copytree(FIXTURE_DIR, source_dir)
    renames = []
    real_replace = os.replace

    def record_replace(src, dst):
        # The temporary file holds the whole scan file before the rename, so a reader sees all of it or nothing.
        renames.append((Path(dst), json.loads(Path(src).read_text())))
        real_replace(src, dst)

    monkeypatch.setattr(userprefs_keys.os, "replace", record_replace)

    scan = ensure_key_scan(source_dir)

    assert [(dst, set(stored["keys"])) for dst, stored in renames] == [(source_dir / SCAN_FILE, scan.keys)]
    assert sorted(path.name for path in source_dir.iterdir() if path.name.startswith(".")) == [SCAN_FILE]


def test_firmware_source_without_src_has_no_key_scan(tmp_path):
    assert ensure_key_scan(tmp_path) is None


def test_firmware_source_without_userprefs_jsonc_gives_the_keys_of_src(tmp_path):
    source_dir = tmp_path / OLD
    shutil.copytree(FIXTURE_DIR / "src", source_dir / "src")

    assert ensure_key_scan(source_dir).keys == read_keys(FIXTURE_DIR)


def test_fixture_config_header_has_no_region_that_protobufs_v2_8_3_adds_after_v2_7_26(tmp_path):
    source_dir = tmp_path / OLD
    shutil.copytree(FIXTURE_DIR, source_dir)

    scan = ensure_key_scan(source_dir)

    assert f"{REGION}EU_866" in scan.enum_values
    assert f"{REGION}ITU1_70CM" not in scan.enum_values


# GET /api/v1/userprefs/keys


@pytest.mark.usefixtures("sources")
async def test_keys_route_returns_the_keys_that_the_firmware_version_reads(client):
    resp = await client.get("/api/v1/userprefs/keys", params={"version": NEW})

    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == NEW
    assert set(body["keys"]) == read_keys(FIXTURE_DIR) | NEW_KEYS
    assert f"{REGION}US" in body["enum_values"]


@pytest.mark.usefixtures("sources")
async def test_keys_route_without_a_version_returns_the_default_firmware_version(client):
    body = (await client.get("/api/v1/userprefs/keys")).json()

    assert body["version"] == OLD
    assert not MESH_BEACON_KEYS & set(body["keys"])


@pytest.mark.usefixtures("sources")
async def test_keys_route_returns_404_for_a_firmware_version_that_is_not_installed(client):
    resp = await client.get("/api/v1/userprefs/keys", params={"version": "v2.6.11.60ec05e"})

    assert resp.status_code == 404


@pytest.mark.usefixtures("firmware_source")
async def test_keys_route_gives_null_keys_for_a_firmware_source_without_src(client):
    body = (await client.get("/api/v1/userprefs/keys")).json()

    assert body["keys"] is None
    assert body["enum_values"] is None


# The build page


@pytest.mark.usefixtures("sources")
async def test_with_firmware_version_v2_7_20_chosen_no_mesh_beacon_control_shows(client):
    html = (await client.get("/")).text

    beacon = _fieldsets(html, "USERPREFS_MESH_BEACON_")
    assert len(beacon) == len(MESH_BEACON_KEYS)
    assert all(" hidden disabled" in tag for tag in beacon)


async def test_with_firmware_version_v2_8_2_chosen_each_mesh_beacon_control_shows(app, settings, client):
    _install_copy(settings, NEW, "stable", NEW_KEYS)

    html = (await client.get("/")).text

    assert not any(" hidden" in tag for tag in _fieldsets(html, "USERPREFS_MESH_BEACON_"))


@pytest.mark.usefixtures("sources")
async def test_build_page_shows_a_control_that_the_firmware_version_reads(client):
    html = (await client.get("/")).text

    (owner,) = _fieldsets(html, "USERPREFS_CONFIG_OWNER_SHORT_NAME")
    assert " hidden" not in owner


@pytest.mark.usefixtures("sources")
async def test_build_page_hides_the_channel_slots_that_the_firmware_version_does_not_read(client):
    html = (await client.get("/")).text

    tab = {n: re.search(rf'<li [^>]*data-channel-slot="{n}"[^>]*>', html)[0] for n in range(CHANNEL_SLOTS)}
    assert " hidden" not in tab[2]
    assert " hidden" in tab[3]
    assert re.search(r'<option value="4"[^>]* hidden disabled>', html)
    assert " hidden" in _fieldsets(html, "USERPREFS_CHANNEL_0_IS_MUTED")[0]


@pytest.mark.usefixtures("sources")
async def test_build_page_hides_the_regions_that_the_firmware_version_lacks(client):
    html = (await client.get("/")).text

    assert re.search(rf'<option value="{REGION}ITU1_70CM" hidden disabled>', html)
    assert re.search(rf'<option value="{REGION}US" selected>', html)


@pytest.mark.usefixtures("firmware_source")
async def test_build_page_shows_every_control_when_the_firmware_source_has_no_key_scan(client):
    html = (await client.get("/")).text

    assert " hidden disabled" not in html


# The 422 field errors of a firmware version


CHANNELS_TO_WRITE = ("body", "channels_to_write")


def _locations(resp) -> list[tuple]:
    return [tuple(error["loc"]) for error in resp.json()["detail"]]


@pytest.mark.usefixtures("sources")
@pytest.mark.parametrize(
    ("fields", "locations"),
    [
        ({"channels_to_write": "8", "channel_3": {"name": "Four"}}, [("body", "channel_3", "name"), CHANNELS_TO_WRITE]),
        ({"channels_to_write": "8", "channel_7": {"psk": PSK_16}}, [("body", "channel_7", "psk"), CHANNELS_TO_WRITE]),
        ({"channels_to_write": "4"}, [CHANNELS_TO_WRITE]),
        ({"mesh_beacon_message": "Join us"}, [("body", "mesh_beacon_message")]),
        ({"lora_enabled": "true", "lora_region": "ITU1_70CM"}, [("body", "lora_region")]),
    ],
)
@pytest.mark.parametrize("route", ["/api/v1/preview", "/api/v1/download", "/api/v1/generate"])
async def test_config_route_rejects_a_field_that_firmware_version_v2_7_20_does_not_read(
    client, route, fields, locations
):
    resp = await client.post(route, json={**fields, "firmware_version": OLD})

    assert resp.status_code == 422
    assert _locations(resp) == locations


@pytest.mark.usefixtures("sources")
async def test_preview_names_the_channel_slots_that_the_firmware_version_reads(client):
    resp = await client.post("/api/v1/preview", json={"channels_to_write": "4", "firmware_version": OLD})

    assert resp.json()["detail"][0]["msg"] == f"firmware version {OLD} reads 3 channel slots"


@pytest.mark.usefixtures("sources")
@pytest.mark.parametrize(("version", "count"), [(OLD, "3"), (NEW, "8")])
async def test_preview_accepts_each_channel_slot_that_the_firmware_version_reads(client, version, count):
    resp = await client.post("/api/v1/preview", json={"channels_to_write": count, "firmware_version": version})

    assert resp.status_code == 200


@pytest.mark.usefixtures("sources")
async def test_preview_names_the_firmware_version_and_the_key_in_the_message(client):
    resp = await client.post("/api/v1/preview", json={"mesh_beacon_message": "Join us", "firmware_version": OLD})

    assert resp.json()["detail"][0]["msg"] == f"firmware version {OLD} does not read USERPREFS_MESH_BEACON_MESSAGE"


@pytest.mark.usefixtures("sources")
async def test_download_writes_channel_5_for_a_firmware_version_that_reads_it(client):
    body = {"channels_to_write": "6", "channel_5": {"psk": PSK_16}, "firmware_version": NEW}

    resp = await client.post("/api/v1/download", json=body)

    assert resp.status_code == 200
    assert "USERPREFS_CHANNEL_5_PSK" in json.loads(resp.text)


@pytest.mark.usefixtures("sources")
async def test_preview_returns_422_for_a_firmware_version_that_is_not_installed(client):
    resp = await client.post("/api/v1/preview", json={"firmware_version": "v2.6.11.60ec05e"})

    assert resp.status_code == 422
    assert _locations(resp) == [("body", "firmware_version")]


@pytest.mark.usefixtures("sources")
async def test_build_rejects_a_field_that_the_firmware_version_does_not_read(app, client):
    form = {
        "variant": "heltec-v3",
        "config_source": "form",
        "firmware_version": OLD,
        "channels_to_write": "8",
        "channel_5[psk]": PSK_16,
    }

    resp = await client.post("/api/v1/build-firmware", data=form)

    assert resp.status_code == 422
    assert _locations(resp) == [("body", "channel_5", "psk"), CHANNELS_TO_WRITE]
    assert not app.state.build_queue._builds


@pytest.mark.usefixtures("sources")
async def test_build_page_form_sends_the_chosen_firmware_version_with_preview_and_download(client):
    html = (await client.get("/")).text

    assert re.search(r'<select [^>]*id="firmware_version"[^>]*form="configForm"', html)


# The rules of upstream for the event channel and the canned messages, from the v2.8.2.d5f1619 source


def _scan(*keys: str) -> KeyScan:
    return KeyScan(NEW, read_keys(FIXTURE_DIR) | NEW_KEYS | set(keys), None)


@pytest.mark.parametrize("psk", ["AQ==", "00" * 8])
def test_block_position_rejects_a_channel_0_psk_that_is_not_16_or_32_bytes(psk):
    config = {"channels_to_write": "1", "channel_0[psk]": psk, "event_block_position": "true"}

    errors = version_errors(config, _scan())

    assert [location for location, _ in errors] == [("channel_0", "psk")]
    assert "16 or 32 bytes" in errors[0][1]


@pytest.mark.parametrize("psk", [PSK_16, "00" * 32])
def test_block_position_accepts_a_channel_0_psk_of_16_or_32_bytes(psk):
    config = {"channels_to_write": "1", "channel_0[psk]": psk, "event_block_position": "true"}

    assert version_errors(config, _scan()) == []


def test_block_position_off_accepts_a_1_byte_channel_0_psk():
    config = {"channels_to_write": "1", "channel_0[psk]": "AQ==", "event_block_position": "false"}

    assert version_errors(config, _scan()) == []


def test_canned_messages_over_200_bytes_are_a_field_error():
    errors = version_errors({"canned_messages": "a" * 100 + "\n" + "b" * 100}, _scan())

    assert errors == [(("canned_messages",), "the messages are more than 200 bytes")]


def test_canned_messages_of_200_bytes_are_accepted():
    assert version_errors({"canned_messages": "a" * 99 + "\n" + "b" * 100}, _scan()) == []


def test_event_rules_do_not_apply_to_keys_that_the_default_key_set_drops():
    config = {"canned_messages": "a" * 300, "channel_0[psk]": "AQ==", "event_block_position": "true"}

    assert version_errors(config, None) == []


# The field locations


def test_each_key_of_the_field_locations_is_read_by_the_fixture_or_version_gated():
    assert set(KEY_FIELDS) - read_keys(FIXTURE_DIR) - VERSION_GATED_KEYS == set()


def test_each_key_that_the_generator_writes_has_a_field_location():
    written = json.loads(generate_jsonc(full_config(), firmware_keys=VERSION_GATED_KEYS))

    assert set(written) - set(KEY_FIELDS) == set()


# The note about the default key set, when no key scan exists

NO_VERSION_NOTE = "No firmware version is installed; keys that v2.7.26 does not read are left out."
NO_SCAN_NOTE = f"Firmware version {FIRMWARE_VERSION} has no key scan; keys that v2.7.26 does not read are left out."


async def test_preview_without_a_firmware_version_shows_the_note_about_the_left_out_keys(client):
    resp = await client.post("/api/v1/preview", data={"owner_long_name": "Node"}, headers={"HX-Request": "true"})

    assert resp.status_code == 200
    assert NO_VERSION_NOTE in resp.text


@pytest.mark.parametrize("route", ["/api/v1/download", "/api/v1/preview", "/api/v1/generate"])
async def test_config_route_without_a_firmware_version_names_the_left_out_keys_in_a_header(client, route):
    resp = await client.post(route, json={"owner_long_name": "Node"})

    assert resp.status_code == 200
    assert resp.headers["X-Userprefs-Note"] == NO_VERSION_NOTE


@pytest.mark.usefixtures("firmware_source")
async def test_download_for_a_firmware_source_without_a_key_scan_names_the_left_out_keys_in_a_header(client):
    resp = await client.post("/api/v1/download", json={"owner_long_name": "Node"})

    assert resp.headers["X-Userprefs-Note"] == NO_SCAN_NOTE


@pytest.mark.usefixtures("sources")
async def test_preview_and_download_with_a_key_scan_have_no_note(client):
    preview = await client.post("/api/v1/preview", data={"owner_long_name": "Node"}, headers={"HX-Request": "true"})
    download = await client.post("/api/v1/download", json={"owner_long_name": "Node"})

    assert "left out" not in preview.text
    assert "X-Userprefs-Note" not in download.headers
