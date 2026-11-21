"""IANA time zone to POSIX TZ string conversion for `USERPREFS_TZ_STRING`.

The firmware passes the value to `setenv("TZ", …)`, which reads only a POSIX TZ string.
`scripts/gen_tz_posix.py` writes the table from the tz database.
"""

import json
import re
from importlib import resources

_TABLE = json.loads(resources.files("mtfwbuilder.data").joinpath("tz_posix.json").read_text(encoding="utf-8"))

POSIX_TZ_BY_ZONE: dict[str, str] = _TABLE["zones"]

# The grammar is POSIX TZ plus the RFC 8536 extensions: signed rule hours up to 167.
_NAME = r"(?:[A-Za-z]{3,}|<[A-Za-z0-9+-]{3,}>)"
_OFFSET = r"[+-]?\d{1,2}(?::\d{2}){0,2}"
_RULE = r"(?:M\d{1,2}\.[1-5]\.[0-6]|J\d{1,3}|\d{1,3})(?:/[+-]?\d{1,3}(?::\d{2}){0,2})?"
POSIX_TZ_PATTERN = re.compile(rf"{_NAME}{_OFFSET}(?:{_NAME}(?:{_OFFSET})?(?:,{_RULE},{_RULE})?)?")


def to_posix_tz(value: str) -> str:
    """Return the POSIX TZ string for an IANA time zone or for a POSIX TZ string.

    Args:
        value: An IANA time zone, for example `America/New_York`, or a POSIX TZ string.

    Raises:
        ValueError: The value is not an IANA time zone in the table and not a POSIX TZ string.
    """
    if value in POSIX_TZ_BY_ZONE:
        return POSIX_TZ_BY_ZONE[value]
    if POSIX_TZ_PATTERN.fullmatch(value):
        return value
    raise ValueError("the value is not an IANA time zone or a POSIX TZ string")
