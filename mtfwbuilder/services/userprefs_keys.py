"""Read the userPrefs keys that a Meshtastic firmware source reads.

`bin/platformio-custom.py` turns each `userPrefs.jsonc` entry into a `-D` flag.
The firmware reads a key only where a preprocessor line or code in `src/` names it.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from mtfwbuilder.database import find_firmware_source

logger = logging.getLogger("mtfwbuilder.userprefs_keys")

_KEY = re.compile(r"USERPREFS_[A-Z0-9_]+")

# A literal matches first, so a `//` in a URL string or a `'"'` character does not change the scan.
_COMMENT_OR_LITERAL = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|//[^\n]*|/\*.*?\*/", re.DOTALL)

# Upstream writes each example as a commented JSON entry, for example `// "USERPREFS_EVENT_MODE": "1",`.
_JSONC_ENTRY = re.compile(r'"(USERPREFS_[A-Z0-9_]+)"\s*:')


def read_keys(firmware_dir: Path) -> frozenset[str]:
    """Return every userPrefs key that the firmware source in `firmware_dir` reads.

    The set holds each key that code under `src/` names outside a comment.
    It also holds each entry key in `userPrefs.jsonc`, commented examples included.

    Args:
        firmware_dir: The root of a firmware source tree, with `src/` and `userPrefs.jsonc`.

    Raises:
        FileNotFoundError: `firmware_dir` holds no `userPrefs.jsonc`.
    """
    return frozenset(_JSONC_ENTRY.findall((firmware_dir / "userPrefs.jsonc").read_text())) | _source_keys(firmware_dir)


def _source_keys(firmware_dir: Path) -> frozenset[str]:
    """Return each userPrefs key that code under `src/` of `firmware_dir` names outside a comment."""
    keys = set()
    for path in (firmware_dir / "src").rglob("*"):
        if path.is_file():
            keys.update(_KEY.findall(_without_comments(path.read_text(errors="replace"))))
    return frozenset(keys)


def _without_comments(source: str) -> str:
    """Return C++ source with each `//` and `/* */` comment replaced by a space."""
    return _COMMENT_OR_LITERAL.sub(lambda m: m[0] if m[0][0] in "\"'" else " ", source)


# The scan file is inside the firmware source, so the removal of the firmware source also removes it.
SCAN_FILE = ".userprefs-keys.json"

# nanopb writes each enum value of `config.proto` into this header as `<C name> = <number>`.
_ENUM_HEADER = Path("src/mesh/generated/meshtastic/config.pb.h")
_ENUM_VALUE = re.compile(r"\b(meshtastic_\w+) = ")


@dataclass(frozen=True)
class KeyScan:
    """The userPrefs keys and the enum values that one firmware version reads.

    `enum_values` holds the C name of each `config.proto` enum value in the firmware source.
    It is None when the firmware source has no `config.pb.h`, and then the form offers every enum value.
    """

    version: str
    keys: frozenset[str]
    enum_values: frozenset[str] | None


def ensure_key_scan(source_dir: Path) -> KeyScan | None:
    """Return the key scan of `source_dir` from its scan file, after a scan writes the file when it is absent.

    The directory name of `source_dir` is the firmware version. Returns None for a firmware source without a scan
    file and without `src/`.

    The install scans each firmware source before its first build. A build writes its own `userPrefs.jsonc` into
    the firmware source, and the startup scrub deletes it. So a later scan of a firmware source without
    `userPrefs.jsonc` reads `src/` only.
    """
    scan_file = source_dir / SCAN_FILE
    if scan_file.is_file():
        data = json.loads(scan_file.read_text())
        keys, enum_values = data["keys"], data["enum_values"]
    elif (source_dir / "src").is_dir():
        has_jsonc = (source_dir / "userPrefs.jsonc").is_file()
        keys = sorted(read_keys(source_dir) if has_jsonc else _source_keys(source_dir))
        header = source_dir / _ENUM_HEADER
        enum_values = sorted(set(_ENUM_VALUE.findall(header.read_text()))) if header.is_file() else None
        scan_file.write_text(json.dumps({"keys": keys, "enum_values": enum_values}))
    else:
        logger.warning(f"The firmware source {source_dir} has no src/ directory, so the form shows every key")
        return None
    return KeyScan(source_dir.name, frozenset(keys), None if enum_values is None else frozenset(enum_values))


async def find_key_scan(database_path: Path, version: str | None = None) -> KeyScan | None:
    """Return the key scan of the ready firmware source of `version`, or of the default firmware source.

    Returns None when no firmware source matches, or when `ensure_key_scan` gives None.
    """
    record = await find_firmware_source(database_path, version)
    if record is None:
        return None
    return await asyncio.to_thread(ensure_key_scan, Path(record["path"]))
