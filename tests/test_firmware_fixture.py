"""Tests for the upstream firmware fixture in tests/fixtures/firmware/ (FR-foundation-9)."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "firmware"

VARIANT_FILES = [
    "variants/esp32s3/heltec_v4/platformio.ini",
    "variants/esp32s3/heltec_v3/platformio.ini",
    "variants/nrf52840/rak4631/platformio.ini",
    "variants/nrf52840/t-echo/platformio.ini",
    "variants/rp2040/rpipico/platformio.ini",
    "variants/rp2040/rak11310/platformio.ini",
]

ARCHITECTURE_FILES = [
    "variants/esp32/esp32-common.ini",
    "variants/esp32s3/esp32s3.ini",
    "variants/nrf52840/nrf52.ini",
    "variants/nrf52840/nrf52840.ini",
    "variants/rp2040/rp2040.ini",
    "src/graphics/niche/InkHUD/PlatformioConfig.ini",
]

ROOT_FILES = [
    "platformio.ini",
    "userPrefs.jsonc",
    "bin/platformio-custom.py",
    "SOURCE.md",
    "project_config.json",
]


@pytest.mark.parametrize("relpath", ROOT_FILES + VARIANT_FILES + ARCHITECTURE_FILES)
def test_fixture_file_exists(relpath):
    assert (FIXTURE_DIR / relpath).is_file()


@pytest.mark.parametrize("relpath", ["platformio.ini", "userPrefs.jsonc"])
def test_gitignore_does_not_ignore_the_fixture(relpath):
    # `git check-ignore` exits 1 when no pattern matches the path.
    result = subprocess.run(
        ["git", "check-ignore", "-q", f"tests/fixtures/firmware/{relpath}"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 1


def test_gitignore_still_ignores_a_generated_userprefs_file():
    result = subprocess.run(
        ["git", "check-ignore", "-q", "userPrefs.jsonc"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0


def test_gitignore_still_ignores_the_downloaded_firmware_source():
    result = subprocess.run(
        ["git", "check-ignore", "-q", "firmware/platformio.ini"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0
