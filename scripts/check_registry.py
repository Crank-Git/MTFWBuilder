"""Check the registry that the project generates from a firmware source, and report its counts.

The weekly upstream check runs it on a clone of upstream `develop`:

    uv run python scripts/check_registry.py <firmware source>

The script runs `pio project config --json-output` in the firmware source and checks the registry:

- The registry holds at least 200 envs.
- Each env of the config names its architecture with a `-I variants/<arch>` build flag.
- The env `heltec-v4` has the display name "Heltec V4".
- The registry holds no native env.

When `GITHUB_STEP_SUMMARY` is set, the script appends the counts to that file. The exit code is 1 for a failed check.
"""

import logging
import os
import sys
from collections import Counter
from pathlib import Path

from mtfwbuilder.services.registry_generator import (
    ARCHITECTURE_FLAG,
    RegistryFailed,
    read_project_config,
    registry_entries,
)

# Upstream `develop` had 266 non-native envs on 2026-11-21, so a drop below 200 means lost envs.
MINIMUM_ENVS = 200

log = logging.getLogger("check_registry")


def _envs_without_architecture(config: list) -> list[str]:
    """Return the env names of `config` that have no `-I variants/<arch>` build flag."""
    return [
        section.removeprefix("env:")
        for section, options in config
        if section.startswith("env:")
        and not any(ARCHITECTURE_FLAG.search(flag) for flag in dict(options).get("build_flags", []))
    ]


def failures(config: list, entries: list[dict]) -> list[str]:
    """Return one line for each failed check of the registry `entries` that the generator made from `config`."""
    found = []
    if len(entries) < MINIMUM_ENVS:
        found.append(f"The registry holds {len(entries)} envs, fewer than {MINIMUM_ENVS}.")
    missing = _envs_without_architecture(config)
    if missing:
        found.append(f"Envs with no architecture: {', '.join(missing)}.")
    heltec = next((entry for entry in entries if entry["env"] == "heltec-v4"), None)
    if heltec is None:
        found.append("The registry holds no env heltec-v4.")
    elif heltec["display_name"] != "Heltec V4":
        found.append(f"The display name of heltec-v4 is {heltec['display_name']!r}, not 'Heltec V4'.")
    native = [entry["env"] for entry in entries if entry["architecture"] == "native"]
    if native:
        found.append(f"The registry holds native envs: {', '.join(native)}.")
    return found


def summary(entries: list[dict], found: list[str]) -> str:
    """Return the Markdown job summary: the env count, the counts by architecture and support level, the result."""
    lines = ["### Generated registry", "", f"Registry envs: {len(entries)}", ""]
    for field, heading in (("architecture", "Architecture"), ("support_level", "Support level")):
        lines += [f"| {heading} | Envs |", "|---|---|"]
        lines += [f"| {value} | {count} |" for value, count in Counter(e[field] for e in entries).most_common()]
        lines.append("")
    lines += [f"- {line}" for line in found] or ["All registry checks pass."]
    return "\n".join(lines) + "\n"


def main(source_dir: Path) -> int:
    """Return 0 when the registry of the firmware source in `source_dir` passes each check, else 1."""
    try:
        config = read_project_config(source_dir)
        entries = registry_entries(config)
    except (RegistryFailed, TypeError, ValueError) as e:
        log.error(f"The registry check cannot run: {e}")
        return 1
    found = failures(config, entries)
    text = summary(entries, found)
    log.info(text)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a") as file:
            file.write(text)
    for line in found:
        log.error(line)
    return 1 if found else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main(Path(sys.argv[1])))
