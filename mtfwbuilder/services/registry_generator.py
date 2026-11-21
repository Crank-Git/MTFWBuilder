"""The registry of a firmware version, generated from the PlatformIO config of its firmware source (feature 10).

Upstream variant `platformio.ini` files carry `custom_meshtastic_*` metadata. `pio project config --json-output`
resolves `extends` and `extra_configs`, so the registry reads the resolved values and not the files.
"""

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("mtfwbuilder.registry_generator")

# `pio project config` only reads the config files, so it ends in seconds also for a full firmware source.
PIO_CONFIG_TIMEOUT_SECONDS = 300

# Upstream `bin/generate_ci_matrix.py` (v2.7.26.54e0d8d) reads the architecture with this pattern.
ARCHITECTURE_FLAG = re.compile(r"-I\s?variants/([^/]+)")

ESP32_FAMILY = ("esp32", "esp32s2", "esp32s3", "esp32c3", "esp32c6")
FLASH_METHODS = {
    **dict.fromkeys(ESP32_FAMILY, "web-serial"),
    "nrf52840": "uf2",
    "rp2040": "uf2",
    "rp2350": "uf2",
    "nrf54l15": "dfu-zip",
    "stm32": "download",
}


class RegistryFailed(Exception):
    """The registry of a firmware source cannot be generated. The message names the cause."""


def flash_method(architecture: str) -> str:
    """Return the flash method of the architecture: `web-serial`, `uf2`, `dfu-zip` or `download`.

    An architecture that FR-generated-registry-14 does not name gets `download`, because the operator can always
    download the artifact.
    """
    return FLASH_METHODS.get(architecture, "download")


def generate_registry(source_dir: Path) -> list[dict]:
    """Return the registry entries of the firmware source in `source_dir`.

    Raises `RegistryFailed` when `pio project config --json-output` fails or times out, or when its output does not
    have the shape of a project config.
    """
    config = read_project_config(source_dir)
    try:
        return registry_entries(config)
    except (TypeError, ValueError) as e:
        # The startup import also calls this, so an upstream value of a wrong type must not stop the process.
        raise RegistryFailed(f"The output of pio project config has an unexpected value: {e}") from e


def read_project_config(source_dir: Path) -> list:
    """Return the output of `pio project config --json-output` in `source_dir`: a list of `[section, options]`.

    Each `options` value is a list of `[option, value]`. Raises `RegistryFailed` for a non-zero exit code, the
    timeout, a missing `pio` or output that is not JSON.
    """
    try:
        result = subprocess.run(
            ["pio", "project", "config", "--json-output"],
            cwd=source_dir,
            capture_output=True,
            text=True,
            timeout=PIO_CONFIG_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RegistryFailed(f"pio project config did not run: {e}") from e
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-20:]
        logger.error("pio project config output:\n" + "\n".join(tail))
        raise RegistryFailed(f"pio project config exited with code {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RegistryFailed(f"The output of pio project config is not valid JSON: {e}") from e


def registry_entries(config: list) -> list[dict]:
    """Return one registry entry for each env section of `config`, in config order, without native envs.

    `config` has the shape of the `read_project_config` result. An env without a `-I variants/<arch>` flag gets
    no entry and a log line, because no flash method and no artifact name fits it.
    """
    entries = []
    for section, options in config:
        if not section.startswith("env:"):
            continue
        env = section.removeprefix("env:")
        values = dict(options)
        architecture = _architecture(values.get("build_flags", []))
        if architecture is None:
            logger.warning(f"The registry skips env {env}: no build flag names its architecture")
        elif architecture != "native":
            entries.append(_entry(env, architecture, values))
    return entries


def _entry(env: str, architecture: str, values: dict) -> dict:
    hw_model = values.get("custom_meshtastic_hw_model")
    return {
        "env": env,
        "display_name": values.get("custom_meshtastic_display_name") or env,
        "manufacturer": _first(values.get("custom_meshtastic_tags")) or "Other",
        "architecture": architecture,
        # Upstream `bin/generate_ci_matrix.py` builds an env without `board_level` in each release build.
        "support_level": values.get("board_level") or "release",
        "hw_model": int(hw_model) if hw_model else None,
        "hw_model_slug": values.get("custom_meshtastic_hw_model_slug") or None,
        "image": _first(values.get("custom_meshtastic_images")),
        "requires_dfu": _flag(values.get("custom_meshtastic_requires_dfu"), default=False),
        "partition_scheme": values.get("custom_meshtastic_partition_scheme") or None,
        "has_mui": _flag(values.get("custom_meshtastic_has_mui"), default=False),
        "has_ink_hud": _flag(values.get("custom_meshtastic_has_ink_hud"), default=False),
        # The spec shows a badge only for `custom_meshtastic_actively_supported = false`.
        "actively_supported": _flag(values.get("custom_meshtastic_actively_supported"), default=True),
    }


def _architecture(build_flags: list[str]) -> str | None:
    for flag in build_flags:
        match = ARCHITECTURE_FLAG.search(flag)
        if match:
            return match.group(1)
    return None


def _first(value: str | list[str] | None) -> str | None:
    """Return the first item of a comma-separated value, or None when the value is empty."""
    text = ", ".join(value) if isinstance(value, list) else value or ""
    return next((item.strip() for item in text.split(",") if item.strip()), None)


def _flag(value: str | None, default: bool) -> bool:
    return default if value is None else value.strip().lower() == "true"
