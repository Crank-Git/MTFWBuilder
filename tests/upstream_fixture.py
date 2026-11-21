"""Replace the firmware fixture with an upstream firmware clone, for the weekly upstream check.

The contract tests read `tests/fixtures/firmware/`. The workflow
`.github/workflows/upstream-check.yml` runs this module on a CI checkout before it runs
them, so the same tests read upstream `develop` and need no setting of their own.

Usage: `uv run python -m tests.upstream_fixture <clone> [<fixture>]`
"""

import json
import re
import shutil
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "firmware"

ROOT_FILES = ("platformio.ini", "userPrefs.jsonc")
TREES = ("bin", "src", "variants")
ENV_SECTION = re.compile(r"^\[env:([^\]]+)\]", re.MULTILINE)


def install(clone: Path, fixture: Path) -> None:
    """Copy the upstream files of `clone` into `fixture`, and write `envs.json` for them.

    `SOURCE.md` stays, because the tests compare it with the stable firmware version.

    Raises FileNotFoundError when `clone` does not have a file or tree that the fixture holds.
    """
    for name in ROOT_FILES:
        shutil.copy2(clone / name, fixture / name)
    for name in TREES:
        # A file that upstream deletes must also go from the fixture.
        shutil.rmtree(fixture / name, ignore_errors=True)
        shutil.copytree(clone / name, fixture / name)
    (fixture / "envs.json").write_text(json.dumps(_env_names(fixture / "variants"), indent=2, sort_keys=True) + "\n")


def _env_names(variants: Path) -> dict[str, str]:
    """Return each `[env:…]` name of the `.ini` files under `variants` with its architecture folder."""
    env_names: dict[str, str] = {}
    for ini in sorted(variants.rglob("*.ini")):
        architecture = ini.relative_to(variants).parts[0]
        for name in ENV_SECTION.findall(ini.read_text()):
            env_names.setdefault(name, architecture)
    return env_names


def main(argv: list[str]) -> None:
    """Install the clone named in `argv` into the fixture directory, `tests/fixtures/firmware/` by default."""
    install(Path(argv[0]), Path(argv[1]) if len(argv) > 1 else FIXTURE_DIR)


if __name__ == "__main__":
    main(sys.argv[1:])
