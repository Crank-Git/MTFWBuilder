"""Tests for the registry that the project generates from each firmware source (feature 10, FR 1-7, 14 and 16)."""

import asyncio
import importlib.util
import json
import re
import zipfile

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.auth import CSRF_HEADER
from mtfwbuilder.database import delete_firmware_source, init_db
from mtfwbuilder.services import firmware_updater
from mtfwbuilder.services.registry_generator import (
    RegistryFailed,
    flash_method,
    generate_registry,
    registry_entries,
)
from tests.conftest import REPO_ROOT, sign_in_admin

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "firmware"
PROJECT_CONFIG = json.loads((FIXTURE_DIR / "project_config.json").read_text())
STABLE = "v2.7.26.54e0d8d"


def _fixture_envs():
    """Return the env names of the `[env:…]` sections in the variant files of the firmware fixture."""
    names = set()
    for path in (FIXTURE_DIR / "variants").rglob("*.ini"):
        names.update(re.findall(r"^\[env:([^\]]+)\]", path.read_text(), flags=re.MULTILINE))
    return names


def _env(name, **options):
    """Return one `[env:<name>]` section in the shape of `pio project config --json-output`."""
    options.setdefault("build_flags", ["-Wno-format", f"-I variants/esp32s3/{name}"])
    return [f"env:{name}", list(options.items())]


def _by_env(entries):
    return {entry["env"]: entry for entry in entries}


# FR-generated-registry-2 to 6: the fields of each registry entry


def test_recorded_project_config_holds_every_env_of_the_fixture_variant_files():
    recorded = {section for section, _options in PROJECT_CONFIG if section.startswith("env:")}

    assert recorded == {f"env:{name}" for name in _fixture_envs()}


def test_registry_holds_every_env_of_the_fixture():
    entries = registry_entries(PROJECT_CONFIG)

    assert sorted(entry["env"] for entry in entries) == sorted(_fixture_envs())


def test_heltec_v4_entry_holds_the_metadata_of_its_variant_file():
    entry = _by_env(registry_entries(PROJECT_CONFIG))["heltec-v4"]

    assert entry == {
        "env": "heltec-v4",
        "display_name": "Heltec V4",
        "manufacturer": "Heltec",
        "architecture": "esp32s3",
        "support_level": "release",
        "hw_model": 110,
        "hw_model_slug": "HELTEC_V4",
        "image": "heltec_v4.svg",
        "requires_dfu": True,
        "partition_scheme": "16MB",
        "has_mui": True,
        "has_ink_hud": False,
        "actively_supported": True,
    }


def test_support_level_is_the_board_level():
    entries = _by_env(registry_entries(PROJECT_CONFIG))

    assert entries["heltec-v3"]["support_level"] == "pr"
    assert entries["rak4631_dbg"]["support_level"] == "extra"


def test_architecture_comes_from_an_include_flag_without_a_space():
    # The t-echo variant file writes `-Ivariants/nrf52840/t-echo`.
    assert _by_env(registry_entries(PROJECT_CONFIG))["t-echo"]["architecture"] == "nrf52840"


def test_manufacturer_is_the_first_tag():
    assert _by_env(registry_entries(PROJECT_CONFIG))["pico"]["manufacturer"] == "RPi"


def test_env_without_tags_gets_manufacturer_other():
    [entry] = registry_entries([_env("bare")])

    assert entry["manufacturer"] == "Other"


def test_env_without_a_display_name_gets_the_env_name():
    assert _by_env(registry_entries(PROJECT_CONFIG))["t-echo-inkhud"]["display_name"] == "t-echo-inkhud"


def test_env_without_metadata_gets_the_empty_values():
    [entry] = registry_entries([_env("bare")])

    assert entry == {
        "env": "bare",
        "display_name": "bare",
        "manufacturer": "Other",
        "architecture": "esp32s3",
        "support_level": "release",
        "hw_model": None,
        "hw_model_slug": None,
        "image": None,
        "requires_dfu": False,
        "partition_scheme": None,
        "has_mui": False,
        "has_ink_hud": False,
        "actively_supported": True,
    }


def test_env_with_actively_supported_false_is_not_actively_supported():
    [entry] = registry_entries([_env("old", custom_meshtastic_actively_supported="false")])

    assert entry["actively_supported"] is False


def test_two_envs_that_share_a_display_name_both_stay_in_the_registry():
    config = [
        _env("thinknode_m1", custom_meshtastic_display_name="Elecrow ThinkNode M1"),
        _env("thinknode_m1-inkhud", custom_meshtastic_display_name="Elecrow ThinkNode M1"),
    ]

    entries = registry_entries(config)

    assert [(e["env"], e["display_name"]) for e in entries] == [
        ("thinknode_m1", "Elecrow ThinkNode M1"),
        ("thinknode_m1-inkhud", "Elecrow ThinkNode M1"),
    ]


def test_sections_that_are_not_envs_give_no_entry():
    config = [["esp32s3_base", [["build_flags", "-I variants/esp32s3"]]], _env("heltec-v4")]

    assert [entry["env"] for entry in registry_entries(config)] == ["heltec-v4"]


# FR-generated-registry-7: no native env


def test_registry_excludes_every_native_env():
    config = [_env("native", build_flags=["-I variants/native/portduino"]), _env("heltec-v4")]

    assert [entry["env"] for entry in registry_entries(config)] == ["heltec-v4"]


def test_env_without_an_architecture_flag_gives_no_entry(caplog):
    config = [_env("odd", build_flags=["-Wno-format"]), _env("heltec-v4")]

    assert [entry["env"] for entry in registry_entries(config)] == ["heltec-v4"]
    assert "odd" in caplog.text


# FR-generated-registry-14: the flash method of each architecture


@pytest.mark.parametrize(
    ("architecture", "method"),
    [
        ("esp32", "web-serial"),
        ("esp32s3", "web-serial"),
        ("esp32c3", "web-serial"),
        ("esp32c6", "web-serial"),
        ("esp32s2", "web-serial"),
        ("nrf52840", "uf2"),
        ("rp2040", "uf2"),
        ("rp2350", "uf2"),
        ("nrf54l15", "dfu-zip"),
        ("stm32", "download"),
    ],
)
def test_flash_method_of_each_architecture(architecture, method):
    assert flash_method(architecture) == method


def test_flash_method_of_an_unknown_architecture_is_download():
    assert flash_method("riscv99") == "download"


# FR-generated-registry-1: `pio project config --json-output` in the firmware source


def test_generate_registry_reads_the_config_of_pio_in_the_firmware_source(tmp_path, fake_pio):
    entries = generate_registry(tmp_path)

    assert sorted(entry["env"] for entry in entries) == sorted(_fixture_envs())


def test_generate_registry_raises_registry_failed_when_pio_fails(tmp_path, fake_pio, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_CONFIG_FAIL", "1")

    with pytest.raises(RegistryFailed, match="exited with code 1"):
        generate_registry(tmp_path)


def test_generate_registry_raises_registry_failed_for_output_that_is_not_json(tmp_path, fake_pio):
    (tmp_path / ".fake-pio-config.json").write_text("not json")

    with pytest.raises(RegistryFailed, match="not valid JSON"):
        generate_registry(tmp_path)


def test_env_with_a_hw_model_that_is_not_a_number_gets_no_hw_model(caplog):
    [entry] = registry_entries([_env("odd", custom_meshtastic_hw_model="ten")])

    assert entry["env"] == "odd"
    assert entry["hw_model"] is None
    assert "odd" in caplog.text
    assert "custom_meshtastic_hw_model" in caplog.text


def test_env_with_a_value_of_a_wrong_type_gives_no_entry(caplog):
    config = [_env("odd", custom_meshtastic_requires_dfu=["true", "false"]), _env("heltec-v4")]

    assert [entry["env"] for entry in registry_entries(config)] == ["heltec-v4"]
    assert "odd" in caplog.text


def test_generate_registry_with_bad_values_in_two_envs_holds_every_other_env(tmp_path, fake_pio):
    config = [
        *PROJECT_CONFIG,
        _env("odd-hw-model", custom_meshtastic_hw_model="ten"),
        _env("odd-flag", custom_meshtastic_has_mui=["true"]),
    ]
    (tmp_path / ".fake-pio-config.json").write_text(json.dumps(config))

    entries = _by_env(generate_registry(tmp_path))

    assert set(entries) == _fixture_envs() | {"odd-hw-model"}
    assert entries["odd-hw-model"]["hw_model"] is None


def test_generate_registry_raises_registry_failed_for_json_that_is_not_a_section_list(tmp_path, fake_pio):
    (tmp_path / ".fake-pio-config.json").write_text(json.dumps({"env:odd": {}}))

    with pytest.raises(RegistryFailed, match="unexpected value"):
        generate_registry(tmp_path)


# The registry of a firmware version, in the `variants` table


async def _variant_rows(settings, version=STABLE):
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM variants WHERE version = ? ORDER BY env", (version,))
        return [dict(row) for row in await cursor.fetchall()]


async def _source_rows(settings):
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM firmware_sources")
        return [dict(row) for row in await cursor.fetchall()]


@pytest.fixture
def installs(app, fake_pio, monkeypatch):
    """Return the install service of the app, with a local archive in place of the GitHub download."""

    def fake_download(_tag, zip_path):
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("top/platformio.ini", "[platformio]\n")

    monkeypatch.setattr(firmware_updater, "download_zipball", fake_download)
    service = app.state.firmware_installs
    service.resolver = {"stable": STABLE}.__getitem__
    return service


async def _install_stable(installs):
    install = await installs.start(channel="stable")
    await asyncio.wait_for(install.wait(), 10)
    return install


async def test_init_db_creates_the_variants_table_with_the_registry_fields(settings):
    await init_db(settings)

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("PRAGMA table_info(variants)")
        columns = {row[1]: row[5] for row in await cursor.fetchall()}

    assert set(columns) == {
        "version",
        "env",
        "display_name",
        "manufacturer",
        "architecture",
        "support_level",
        "hw_model",
        "hw_model_slug",
        "image",
        "requires_dfu",
        "partition_scheme",
        "has_mui",
        "has_ink_hud",
        "actively_supported",
    }
    # `pk` is the position of the column in the primary key.
    assert (columns["version"], columns["env"]) == (1, 2)


async def test_after_an_install_the_registry_holds_every_non_native_env_of_the_firmware_source(installs, settings):
    install = await _install_stable(installs)

    rows = await _variant_rows(settings)

    assert install.status == "ready"
    assert [row["env"] for row in rows] == sorted(_fixture_envs())
    heltec = next(row for row in rows if row["env"] == "heltec-v4")
    assert (heltec["display_name"], heltec["architecture"], heltec["support_level"]) == (
        "Heltec V4",
        "esp32s3",
        "release",
    )


async def test_install_with_a_native_env_stores_no_native_env(installs, settings, monkeypatch):
    config = [*PROJECT_CONFIG, _env("native", build_flags=["-I variants/native/portduino"])]
    monkeypatch.setattr("mtfwbuilder.services.registry_generator.read_project_config", lambda _source_dir: config)

    await _install_stable(installs)

    envs = [row["env"] for row in await _variant_rows(settings)]
    assert "native" not in envs
    assert len(envs) == len(_fixture_envs())


async def test_failed_registry_marks_the_firmware_source_registry_failed(installs, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_CONFIG_FAIL", "1")

    install = await _install_stable(installs)

    [row] = await _source_rows(settings)
    assert row["status"] == "registry failed"
    assert install.status == "failed"
    assert "registry" in install.error
    assert await _variant_rows(settings) == []
    # The record names the directory, so the directory stays for the removal.
    assert (settings.firmware_dir / STABLE).is_dir()


async def test_admin_sees_and_removes_a_firmware_source_with_a_failed_registry(app, installs, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_CONFIG_FAIL", "1")
    await _install_stable(installs)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.headers[CSRF_HEADER] = sign_in_admin(client, settings)
        listed = (await client.get("/api/v1/firmware")).json()
        resp = await client.delete(f"/api/v1/firmware/{STABLE}")

    assert [(v["version"], v["status"]) for v in listed] == [(STABLE, "registry failed")]
    assert resp.status_code == 204
    assert await _source_rows(settings) == []
    assert not (settings.firmware_dir / STABLE).exists()


async def test_registry_of_a_firmware_version_goes_with_its_firmware_source(installs, settings):
    await _install_stable(installs)

    await delete_firmware_source(settings.database_path, STABLE)

    assert await _variant_rows(settings) == []


async def test_startup_import_stores_the_registry_of_the_old_firmware_source(settings, fake_pio):
    await init_db(settings)
    settings.firmware_dir.mkdir(parents=True)
    (settings.firmware_dir / "platformio.ini").write_text("[platformio]\n")
    (settings.base_dir / "firmware_version.txt").write_text(f"Version: {STABLE}\n")

    firmware_updater.import_old_firmware_source(settings, lambda _channel: STABLE)

    [row] = await _source_rows(settings)
    assert row["status"] == "ready"
    assert [r["env"] for r in await _variant_rows(settings)] == sorted(_fixture_envs())


async def test_startup_import_marks_the_firmware_source_registry_failed_when_pio_fails(settings, fake_pio, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_CONFIG_FAIL", "1")
    await init_db(settings)
    settings.firmware_dir.mkdir(parents=True)
    (settings.firmware_dir / "platformio.ini").write_text("[platformio]\n")
    (settings.base_dir / "firmware_version.txt").write_text(f"Version: {STABLE}\n")

    firmware_updater.import_old_firmware_source(settings, lambda _channel: STABLE)

    [row] = await _source_rows(settings)
    assert row["status"] == "registry failed"
    assert await _variant_rows(settings) == []


# FR-generated-registry-16


def test_the_yaml_registry_file_does_not_exist():
    assert not (REPO_ROOT / "devices" / "variants.yaml").exists()


def test_the_yaml_registry_loader_does_not_exist():
    assert importlib.util.find_spec("mtfwbuilder.services.device_registry") is None
