"""The secret mask for the build log: each secret of a userPrefs file becomes `***`."""

import base64
import json
import re
from collections.abc import Callable

MASK = "***"
# The generator and upstream `userPrefs.jsonc` use different names for one secret, so the match is on a part.
SECRET_KEY = re.compile(r"_PSK|PASSWORD|ADMIN_KEY|OEM_IMAGE_DATA")
# One `"<key>": "<value>"` pair. The value keeps its JSON escapes.
PREF_PAIR = re.compile(r'"(USERPREFS_\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"')
BYTE = re.compile(r"0x([0-9a-fA-F]{1,2})")
# The hex form of a short PSK, such as `01`, also matches ordinary log text.
MIN_DERIVED_LENGTH = 8


def secret_masker(userprefs: str) -> Callable[[str], str]:
    """Return a function that replaces each secret value of `userprefs` with `***` in one log line.

    The function also replaces the hex and base64 forms of a `{ 0x.. }` value, and the form
    that PlatformIO `StringifyMacro` prints in a `-D` flag. The scan reads the text, not the
    JSON, so a userPrefs file that is not valid JSON still gives a mask.
    """
    forms = set()
    for key, raw in PREF_PAIR.findall(userprefs):
        if raw and SECRET_KEY.search(key):
            forms |= _forms(raw)
    if not forms:
        return lambda line: line
    # The longest form goes first, so a form that holds a shorter one is replaced in full.
    pattern = re.compile("|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True)))
    return lambda line: pattern.sub(MASK, line)


def _forms(raw: str) -> set[str]:
    try:
        value = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        value = raw
    # `StringifyMacro` in platformio/builder/tools/piobuild.py writes `"` as `\\\"`.
    forms = {raw, value, value.replace('"', '\\\\\\"')}
    if value.lstrip().startswith("{"):
        data = bytes(int(b, 16) for b in BYTE.findall(value))
        if not data:
            # An empty `{}` holds no secret, and a mask of it hides C++ text in the build log.
            return set()
        derived = {data.hex(), data.hex().upper(), base64.b64encode(data).decode()}
        forms |= {f for f in derived if len(f) >= MIN_DERIVED_LENGTH}
    return forms
