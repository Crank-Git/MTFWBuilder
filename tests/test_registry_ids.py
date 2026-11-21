"""Tests that every registry id is a PlatformIO env name (FR-build-pipeline-repair-20, -21)."""

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "firmware"
ENV_SECTION = re.compile(r"^\[env:([^\]]+)\]", re.MULTILINE)


def _env_names() -> dict[str, str]:
    """Return each env name of the fixture firmware version with its architecture folder."""
    return json.loads((FIXTURE_DIR / "envs.json").read_text())


def _unknown_ids(registry_ids: list[str], env_names: dict[str, str]) -> list[str]:
    """Return the registry ids that are not env names, in registry order."""
    return [variant_id for variant_id in registry_ids if variant_id not in env_names]


def test_every_registry_id_is_an_env_name(variants_path):
    entries = yaml.safe_load(variants_path.read_text())
    unknown = _unknown_ids([entry["id"] for entry in entries], _env_names())
    assert unknown == []


def test_check_reports_a_directory_name_as_unknown():
    # `heltec_v3` is the variant directory, and `heltec-v3` is the env in it.
    assert _unknown_ids(["heltec-v3", "heltec_v3"], _env_names()) == ["heltec_v3"]


def test_registry_platform_is_the_env_architecture_folder(variants_path):
    entries = yaml.safe_load(variants_path.read_text())
    env_names = _env_names()
    wrong = [entry["id"] for entry in entries if env_names.get(entry["id"]) != entry["pio_platform"]]
    assert wrong == []


def test_env_list_holds_each_env_of_the_fixture_variant_files():
    env_names = _env_names()
    for ini in (FIXTURE_DIR / "variants").rglob("platformio.ini"):
        architecture = ini.relative_to(FIXTURE_DIR / "variants").parts[0]
        for name in ENV_SECTION.findall(ini.read_text()):
            assert env_names.get(name) == architecture, name
