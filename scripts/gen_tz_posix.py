"""Write `mtfwbuilder/data/tz_posix.json`, the IANA time zone to POSIX TZ string table.

Run it once when the tz database changes, then commit the table:

    .venv/bin/python scripts/gen_tz_posix.py

The script reads the footer of each TZif file in `zoneinfo.TZPATH`. RFC 8536 section 3.3
defines the footer: a new line, a POSIX TZ string, a new line, in version 2 and later files.
"""

import json
import logging
import zoneinfo
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "mtfwbuilder" / "data" / "tz_posix.json"

log = logging.getLogger("gen_tz_posix")


def _tzif_file(zone: str) -> Path | None:
    """Return the first TZif file for the zone in `zoneinfo.TZPATH`, or None."""
    for root in zoneinfo.TZPATH:
        path = Path(root) / zone
        if path.is_file():
            return path
    return None


def _footer(data: bytes) -> str:
    """Return the POSIX TZ string in the TZif footer, or an empty string for a version 1 file."""
    # Byte 4 is the version: NUL for version 1, which has no footer.
    if not data.startswith(b"TZif") or data[4:5] == b"\0":
        return ""
    return data.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("ascii")


def _tzdata_version() -> str:
    for root in zoneinfo.TZPATH:
        version = Path(root) / "+VERSION"
        if version.is_file():
            return version.read_text(encoding="ascii").strip()
    return "unknown"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    zones: dict[str, str] = {}
    for zone in sorted(zoneinfo.available_timezones()):
        path = _tzif_file(zone)
        posix = _footer(path.read_bytes()) if path else ""
        if posix:
            zones[zone] = posix
        else:
            log.warning("skip %s: no POSIX TZ string in the TZif footer", zone)
    table = {"tzdata": _tzdata_version(), "zones": zones}
    OUTPUT.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    log.info("wrote %d zones from tzdata %s to %s", len(zones), table["tzdata"], OUTPUT)


if __name__ == "__main__":
    main()
