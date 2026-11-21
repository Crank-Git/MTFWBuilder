"""Tests that the env list of the firmware fixture matches its variant files (FR-build-pipeline-repair-21)."""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "firmware"
ENV_SECTION = re.compile(r"^\[env:([^\]]+)\]", re.MULTILINE)


def test_env_list_holds_each_env_of_the_fixture_variant_files():
    # The fake `pio` reads `envs.json` for the artifact names of an env.
    env_names = json.loads((FIXTURE_DIR / "envs.json").read_text())
    for ini in (FIXTURE_DIR / "variants").rglob("platformio.ini"):
        architecture = ini.relative_to(FIXTURE_DIR / "variants").parts[0]
        for name in ENV_SECTION.findall(ini.read_text()):
            assert env_names.get(name) == architecture, name
