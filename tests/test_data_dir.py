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
