"""Tests for `scripts/check_registry.py`, the registry step of the weekly upstream check (issue #144)."""

import json
import os
import subprocess
import sys

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "check_registry.py"
PROJECT_CONFIG = json.loads((REPO_ROOT / "tests" / "fixtures" / "firmware" / "project_config.json").read_text())


def _env(name, architecture="esp32s3", **options):
    """Return one `[env:<name>]` section in the shape of `pio project config --json-output`."""
    options.setdefault("build_flags", ["-Wno-format", f"-I variants/{architecture}/{name}"])
    return [f"env:{name}", list(options.items())]


def _upstream_size_config(count=200):
    """Return a config with `count` non-native envs, `heltec-v4` as one of them."""
    envs = [_env("heltec-v4", custom_meshtastic_display_name="Heltec V4", board_level="release")]
    envs += [_env(f"board-{n}", board_level="extra") for n in range(count - 1)]
    return [["platformio", [["default_envs", ["heltec-v4"]]]], *envs]


def _run(tmp_path, config):
    """Run the script on a firmware source whose fake `pio project config` prints `config`."""
    source = tmp_path / "firmware"
    source.mkdir()
    (source / ".fake-pio-config.json").write_text(json.dumps(config))
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source)],
        capture_output=True,
        text=True,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary)},
        check=False,
    )
    return result, summary.read_text() if summary.exists() else ""


def test_check_fails_for_a_truncated_config_with_fewer_than_200_envs(tmp_path, fake_pio):
    result, summary = _run(tmp_path, PROJECT_CONFIG)

    assert result.returncode == 1
    assert "fewer than 200" in result.stderr
    assert "fewer than 200" in summary


def test_check_passes_for_a_config_of_upstream_size(tmp_path, fake_pio):
    result, summary = _run(tmp_path, _upstream_size_config())

    assert result.returncode == 0, result.stderr
    assert "Registry envs: 200" in summary
    assert "| esp32s3 | 200 |" in summary
    assert "| extra | 199 |" in summary
    assert "| release | 1 |" in summary


def test_check_does_not_count_a_native_env(tmp_path, fake_pio):
    config = [*_upstream_size_config(199), _env("native", architecture="native")]

    result, summary = _run(tmp_path, config)

    assert result.returncode == 1
    assert "Registry envs: 199" in summary


def test_check_fails_for_an_env_without_an_architecture(tmp_path, fake_pio):
    config = [*_upstream_size_config(), _env("mystery", build_flags=["-Wno-format"])]

    result, summary = _run(tmp_path, config)

    assert result.returncode == 1
    assert "no architecture: mystery" in summary


def test_check_fails_when_heltec_v4_has_another_display_name(tmp_path, fake_pio):
    config = _upstream_size_config(201)
    config[1] = _env("heltec-v4", custom_meshtastic_display_name="Heltec v4")

    result, summary = _run(tmp_path, config)

    assert result.returncode == 1
    assert "heltec-v4" in summary


def test_check_fails_when_pio_project_config_fails(tmp_path, fake_pio, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_CONFIG_FAIL", "1")

    result, _summary = _run(tmp_path, _upstream_size_config())

    assert result.returncode == 1
    assert "pio project config exited with code 1" in result.stderr
