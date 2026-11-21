"""Tests for the data directory that holds all state (FR-platform-modernization-6)."""

import os

import pytest
import yaml

from mtfwbuilder.config import DataDirError, Settings, load_settings
from mtfwbuilder.services.firmware_updater import get_firmware_version
from tests.conftest import SECRET_KEY


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Return a base directory and a data directory that the `MTFW_*` variables point at."""
    base_dir = tmp_path / "base"
    data_dir = tmp_path / "data"
    base_dir.mkdir()
    for name in ("MTFW_TEMP_DIR", "MTFW_DATABASE_PATH", "MTFW_FIRMWARE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MTFW_BASE_DIR", str(base_dir))
    monkeypatch.setenv("MTFW_DATA_DIR", str(data_dir))
    return base_dir, data_dir


def test_state_paths_default_under_data_dir(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert settings.firmware_dir == tmp_path / "firmware"
    assert settings.database_path == tmp_path / "mtfwbuilder.db"
    assert settings.temp_dir == tmp_path / "artifacts"


def test_data_dir_defaults_to_data_under_base_dir(tmp_path):
    settings = Settings(base_dir=tmp_path)
    assert settings.data_dir == tmp_path / "data"


def test_load_settings_writes_generated_config_yaml_to_data_dir(dirs):
    base_dir, data_dir = dirs
    settings = load_settings()
    assert settings.data_dir == data_dir
    written = yaml.safe_load((data_dir / "config.yaml").read_text())
    assert written["secret_key"] == settings.secret_key
    assert not (base_dir / "config.yaml").exists()


def test_load_settings_reads_same_secret_key_on_second_start(dirs):
    first = load_settings()
    second = load_settings()
    assert second.secret_key == first.secret_key


def test_startup_moves_old_files_into_data_dir(dirs):
    base_dir, data_dir = dirs
    (base_dir / "config.yaml").write_text(yaml.safe_dump({"secret_key": SECRET_KEY}))
    (base_dir / "mtfwbuilder.db").write_bytes(b"db")
    (base_dir / "firmware").mkdir()
    (base_dir / "firmware" / "platformio.ini").write_text("[platformio]\n")
    (base_dir / "firmware_version.txt").write_text("Version: v2.7.26.54e0d8d\nUpdated: now\n")

    settings = load_settings()

    assert settings.secret_key == SECRET_KEY
    assert (data_dir / "mtfwbuilder.db").read_bytes() == b"db"
    assert (data_dir / "firmware" / "platformio.ini").exists()
    assert get_firmware_version(settings)["version"] == "v2.7.26.54e0d8d"
    for name in ("config.yaml", "mtfwbuilder.db", "firmware", "firmware_version.txt"):
        assert not (base_dir / name).exists()


def test_startup_keeps_old_file_when_data_dir_has_one(dirs):
    base_dir, data_dir = dirs
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text(yaml.safe_dump({"secret_key": SECRET_KEY}))
    (base_dir / "config.yaml").write_text(yaml.safe_dump({"secret_key": "old-key"}))

    settings = load_settings()

    assert settings.secret_key == SECRET_KEY
    assert (base_dir / "config.yaml").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes to a read-only directory")
def test_unwritable_data_dir_raises_error_that_names_the_directory(dirs):
    _, data_dir = dirs
    data_dir.mkdir()
    data_dir.chmod(0o500)
    try:
        with pytest.raises(DataDirError, match=f"{data_dir} is not writable"):
            load_settings()
    finally:
        data_dir.chmod(0o700)


def test_firmware_version_reads_marker_in_data_dir(tmp_path):
    (tmp_path / "firmware_version.txt").write_text("Version: v2.8.2.d5f1619\nUpdated: today\n")
    info = get_firmware_version(Settings(data_dir=tmp_path))
    assert info == {"version": "v2.8.2.d5f1619", "last_updated": "today"}


def _old_firmware_with_secrets(firmware_dir):
    """Make a firmware source that holds the secret files of a build, and return its `platformio.ini`."""
    (firmware_dir / ".pio" / "build" / "tbeam").mkdir(parents=True)
    (firmware_dir / ".pio" / "build" / "tbeam" / "firmware.elf").write_bytes(b"psk")
    (firmware_dir / ".pio" / "build_cache").mkdir()
    (firmware_dir / "userPrefs.jsonc").write_text("{}")
    platformio_ini = firmware_dir / "platformio.ini"
    platformio_ini.write_text("[platformio]\n")
    return platformio_ini


def _assert_scrubbed(firmware_dir):
    assert not (firmware_dir / "userPrefs.jsonc").exists()
    assert not (firmware_dir / ".pio" / "build" / "tbeam").exists()
    assert not (firmware_dir / ".pio" / "build_cache").exists()


def test_startup_keeps_old_firmware_when_firmware_dir_is_set(dirs, tmp_path, monkeypatch):
    base_dir, _ = dirs
    monkeypatch.setenv("MTFW_FIRMWARE_DIR", str(tmp_path / "elsewhere"))
    platformio_ini = _old_firmware_with_secrets(base_dir / "firmware")
    (base_dir / "firmware_version.txt").write_text("Version: v2.7.26.54e0d8d\nUpdated: now\n")

    settings = load_settings()

    assert settings.firmware_dir == tmp_path / "elsewhere"
    assert not settings.firmware_dir.exists()
    assert platformio_ini.exists()
    assert (base_dir / "firmware_version.txt").exists()


def test_startup_scrubs_old_firmware_when_firmware_dir_is_set(dirs, tmp_path, monkeypatch, caplog):
    base_dir, _ = dirs
    monkeypatch.setenv("MTFW_FIRMWARE_DIR", str(tmp_path / "elsewhere"))
    _old_firmware_with_secrets(base_dir / "firmware")

    load_settings()

    _assert_scrubbed(base_dir / "firmware")
    assert f"Scrubbed the old firmware source {base_dir / 'firmware'}" in caplog.text


def test_startup_keeps_old_database_when_database_path_is_set(dirs, tmp_path, monkeypatch):
    base_dir, data_dir = dirs
    monkeypatch.setenv("MTFW_DATABASE_PATH", str(tmp_path / "other.db"))
    (base_dir / "mtfwbuilder.db").write_bytes(b"db")

    settings = load_settings()

    assert settings.database_path == tmp_path / "other.db"
    assert (base_dir / "mtfwbuilder.db").read_bytes() == b"db"
    assert not (data_dir / "mtfwbuilder.db").exists()
    assert not (tmp_path / "other.db").exists()


def test_startup_scrubs_and_keeps_old_firmware_when_data_dir_has_one(dirs, caplog):
    base_dir, data_dir = dirs
    old_ini = _old_firmware_with_secrets(base_dir / "firmware")
    (data_dir / "firmware").mkdir(parents=True)
    new_ini = data_dir / "firmware" / "platformio.ini"
    new_ini.write_text("[new]\n")

    load_settings()

    assert old_ini.exists()
    _assert_scrubbed(base_dir / "firmware")
    assert new_ini.read_text() == "[new]\n"
    assert f"Scrubbed the old firmware source {base_dir / 'firmware'}" in caplog.text
