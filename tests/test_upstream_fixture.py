"""Tests for the upstream fixture install that the weekly upstream check runs (FR-platform-modernization-14)."""

import json

import pytest

from tests.upstream_fixture import install, main


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def clone(tmp_path):
    """Return a directory with the layout of an upstream firmware clone."""
    root = tmp_path / "clone"
    _write(root / "platformio.ini", "[platformio]\nupstream\n")
    _write(root / "userPrefs.jsonc", '{\n  "USERPREFS_UPSTREAM": "1"\n}\n')
    _write(root / "bin" / "platformio-custom.py", "# upstream\n")
    _write(root / "src" / "mesh" / "NodeDB.cpp", "USERPREFS_UPSTREAM\n")
    _write(root / "variants" / "esp32s3" / "heltec_v4" / "platformio.ini", "[env:heltec-v4]\n[env:heltec-v4-tft]\n")
    _write(root / "variants" / "nrf52840" / "diy" / "nrf52_promicro_diy_tcxo" / "platformio.ini", "[env:promicro]\n")
    _write(root / "variants" / "rp2040" / "rp2040.ini", "[rp2040_base]\n")
    _write(root / ".git" / "HEAD", "ref: refs/heads/develop\n")
    return root


@pytest.fixture
def fixture_dir(tmp_path):
    """Return a directory with the layout of `tests/fixtures/firmware/`."""
    root = tmp_path / "fixture"
    _write(root / "SOURCE.md", "# Firmware fixture source\n")
    _write(root / "platformio.ini", "[platformio]\nfixture\n")
    _write(root / "userPrefs.jsonc", "{\n}\n")
    _write(root / "src" / "graphics" / "Screen.cpp", "USERPREFS_OLD\n")
    _write(root / "variants" / "rp2040" / "rpipico" / "platformio.ini", "[env:pico]\n")
    return root


def test_install_copies_the_clone_files_into_the_fixture(clone, fixture_dir):
    install(clone, fixture_dir)
    for relpath in ("platformio.ini", "userPrefs.jsonc", "bin/platformio-custom.py", "src/mesh/NodeDB.cpp"):
        assert (fixture_dir / relpath).read_text() == (clone / relpath).read_text(), relpath


def test_install_removes_a_fixture_file_that_the_clone_does_not_have(clone, fixture_dir):
    install(clone, fixture_dir)
    assert not (fixture_dir / "src" / "graphics" / "Screen.cpp").exists()
    assert not (fixture_dir / "variants" / "rp2040" / "rpipico").exists()


def test_install_keeps_the_fixture_source_record(clone, fixture_dir):
    install(clone, fixture_dir)
    assert (fixture_dir / "SOURCE.md").read_text() == "# Firmware fixture source\n"


def test_install_does_not_copy_the_clone_git_directory(clone, fixture_dir):
    install(clone, fixture_dir)
    assert not (fixture_dir / ".git").exists()


def test_install_writes_each_variant_env_with_its_architecture_folder(clone, fixture_dir):
    install(clone, fixture_dir)
    assert json.loads((fixture_dir / "envs.json").read_text()) == {
        "heltec-v4": "esp32s3",
        "heltec-v4-tft": "esp32s3",
        "promicro": "nrf52840",
    }


def test_install_fails_when_the_clone_has_no_userprefs_file(clone, fixture_dir):
    (clone / "userPrefs.jsonc").unlink()
    with pytest.raises(FileNotFoundError, match="userPrefs.jsonc"):
        install(clone, fixture_dir)


def test_main_installs_the_clone_into_the_given_fixture_directory(clone, fixture_dir):
    main([str(clone), str(fixture_dir)])
    assert (fixture_dir / "src" / "mesh" / "NodeDB.cpp").exists()
