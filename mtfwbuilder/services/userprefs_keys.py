"""Read the userPrefs keys that a Meshtastic firmware source reads.

`bin/platformio-custom.py` turns each `userPrefs.jsonc` entry into a `-D` flag.
The firmware reads a key only where a preprocessor line or code in `src/` names it.
"""

import re
from pathlib import Path

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
    keys = set(_JSONC_ENTRY.findall((firmware_dir / "userPrefs.jsonc").read_text()))
    for path in (firmware_dir / "src").rglob("*"):
        if path.is_file():
            keys.update(_KEY.findall(_without_comments(path.read_text(errors="replace"))))
    return frozenset(keys)


def _without_comments(source: str) -> str:
    """Return C++ source with each `//` and `/* */` comment replaced by a space."""
    return _COMMENT_OR_LITERAL.sub(lambda m: m[0] if m[0][0] in "\"'" else " ", source)
