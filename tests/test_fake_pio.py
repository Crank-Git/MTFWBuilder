"""Tests for the fake `pio` executable in tests/fakes/ (FR-build-pipeline-repair-19)."""

import json
import os
import shutil
import subprocess

import pytest

VERSION = "2.7.26.54e0d8d"


def _run(firmware_dir, env_name, **extra_env):
    """Return the result of `pio run -e <env_name>` in `firmware_dir`, as the build service calls it."""
    return subprocess.run(
        [shutil.which("pio"), "run", "-e", env_name, "--jobs", "1", "--disable-auto-clean"],
        cwd=firmware_dir,
        env={**os.environ, **extra_env},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def firmware_dir(tmp_path, fake_pio):
    """Return a firmware source directory that holds a userPrefs file."""
    prefs = {"USERPREFS_OWNER_LONG_NAME": "Test Node", "USERPREFS_CONFIG_LORA_HOP_LIMIT": "3"}
    (tmp_path / "userPrefs.jsonc").write_text("// comment\n" + json.dumps(prefs))
    return tmp_path


def _artifacts(firmware_dir, env_name):
    return sorted(p.name for p in (firmware_dir / ".pio" / "build" / env_name).iterdir())


def test_fixture_puts_the_fake_first_on_path(fake_pio):
    assert shutil.which("pio") == str(fake_pio)


@pytest.mark.parametrize(
    ("env_name", "suffixes"),
    [
        ("heltec-v3", [".bin", ".factory.bin", ".mt.json"]),
        ("rak4631", ["-ota.zip", ".hex", ".mt.json", ".uf2"]),
        ("pico", [".mt.json", ".uf2"]),
        ("pico2", [".mt.json", ".uf2"]),
        ("CDEBYTE_E77-MBL", [".bin", ".mt.json"]),
    ],
)
def test_fake_writes_the_upstream_artifact_names(firmware_dir, env_name, suffixes):
    result = _run(firmware_dir, env_name)
    assert result.returncode == 0, result.stdout
    expected = [f"firmware-{env_name}-{VERSION}{suffix}" for suffix in suffixes]
    if env_name == "heltec-v3":
        expected.append(f"littlefs-{env_name}-{VERSION}.bin")
    assert _artifacts(firmware_dir, env_name) == sorted(expected)


def test_fake_prints_each_userprefs_key_as_a_define_flag(firmware_dir):
    result = _run(firmware_dir, "heltec-v3")
    lines = result.stdout.splitlines()
    assert '-DUSERPREFS_OWNER_LONG_NAME="Test Node"' in lines
    assert "-DUSERPREFS_CONFIG_LORA_HOP_LIMIT=3" in lines


def test_fake_prints_the_lines_the_progress_parser_reads(firmware_dir):
    stdout = _run(firmware_dir, "rak4631").stdout
    for text in ("Compiling .pio/build/rak4631/", "Linking .pio/build/rak4631/", "Checking size", "[SUCCESS]"):
        assert text in stdout


def test_fake_fail_variable_gives_exit_code_1_and_no_artifacts(firmware_dir):
    result = _run(firmware_dir, "heltec-v3", FAKE_PIO_FAIL="1")
    assert result.returncode == 1
    assert "[FAILED]" in result.stdout
    assert not (firmware_dir / ".pio" / "build" / "heltec-v3").exists()


def test_fake_sleep_variable_holds_the_build_in_compile(firmware_dir):
    process = subprocess.Popen(
        [shutil.which("pio"), "run", "-e", "pico"],
        cwd=firmware_dir,
        env={**os.environ, "FAKE_PIO_SLEEP": "30"},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = ""
        while "Compiling" not in line:
            line = process.stdout.readline()
            assert line, "the fake exited before it printed a compile line"
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()
        process.stdout.close()


def test_fake_rejects_an_unknown_env_name(firmware_dir):
    result = _run(firmware_dir, "heltec_v3")
    assert result.returncode == 1
    assert "Unknown environment names 'heltec_v3'" in result.stdout


def test_fake_writes_the_version_of_fake_pio_version_in_the_artifact_names(firmware_dir):
    # Upstream reads the firmware version from the firmware source, so each firmware source gives its own names.
    (firmware_dir / ".fake-pio-version").write_text("2.8.2.d5f1619\n")
    result = _run(firmware_dir, "pico")
    assert result.returncode == 0, result.stdout
    assert _artifacts(firmware_dir, "pico") == [
        "firmware-pico-2.8.2.d5f1619.mt.json",
        "firmware-pico-2.8.2.d5f1619.uf2",
    ]
