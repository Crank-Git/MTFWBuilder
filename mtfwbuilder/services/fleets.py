"""Fleets: named groups of fleet devices that share one profile (feature fleet-builds, FR 10, 11 and 14).

An admin key is a secret, so a `fleet_devices` row holds it only as a Fernet token (ADR 0007). Only an admin
session gets the decrypted admin key on a load, the same as the stored secrets of a profile.
"""

import csv
import io
import logging
from collections.abc import Collection
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.fernet import InvalidToken
from pydantic import ValidationError

from mtfwbuilder.database import find_firmware_source, list_variants
from mtfwbuilder.models import FleetDevice, FleetRequest, FleetUpdate
from mtfwbuilder.services.profiles import fernet

logger = logging.getLogger("mtfwbuilder.fleets")

INSERT_DEVICE = """
INSERT INTO fleet_devices (
    fleet_id, label, variant, long_name, short_name, role, fixed_lat, fixed_lon, fixed_alt, admin_key_ciphertext
) VALUES (
    :fleet_id, :label, :variant, :long_name, :short_name, :role, :lat, :lon, :alt, :admin_key_ciphertext
)
"""


async def profile_exists(database_path: Path, profile_id: int) -> bool:
    """Return True when a profile has the id `profile_id`."""
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,))
        return await cursor.fetchone() is not None


def _admin_key(row: aiosqlite.Row, secret_key: str | None) -> str | None:
    ciphertext = row["admin_key_ciphertext"]
    if ciphertext is None or secret_key is None:
        return None
    try:
        return fernet(secret_key).decrypt(ciphertext).decode()
    except InvalidToken:
        # A change of `secret_key` makes the token unreadable, and the operator then enters the admin key again.
        logger.warning("The admin key of fleet device %s does not decrypt with the current secret_key", row["id"])
        return None


def _device(row: aiosqlite.Row, secret_key: str | None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "label": row["label"],
        "variant": row["variant"],
        "long_name": row["long_name"],
        "short_name": row["short_name"],
        "role": row["role"],
        "lat": row["fixed_lat"],
        "lon": row["fixed_lon"],
        "alt": row["fixed_alt"],
        "admin_key": _admin_key(row, secret_key),
        "has_admin_key": row["admin_key_ciphertext"] is not None,
    }


async def _fleet(db: aiosqlite.Connection, row: aiosqlite.Row, secret_key: str | None) -> dict[str, Any]:
    cursor = await db.execute("SELECT * FROM fleet_devices WHERE fleet_id = ? ORDER BY id", (row["id"],))
    return {**dict(row), "devices": [_device(device, secret_key) for device in await cursor.fetchall()]}


async def list_fleets(database_path: Path) -> list[dict[str, Any]]:
    """Return every fleet in the order of creation, without admin keys."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM fleets ORDER BY id")
        return [await _fleet(db, row, None) for row in await cursor.fetchall()]


async def get_fleet(database_path: Path, fleet_id: int, secret_key: str | None) -> dict[str, Any] | None:
    """Return the fleet with the id `fleet_id`, or None when no fleet has it.

    A `secret_key` of None gives the fleet without admin keys. Otherwise each fleet device holds its admin key.
    """
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM fleets WHERE id = ?", (fleet_id,))
        row = await cursor.fetchone()
        return await _fleet(db, row, secret_key) if row else None


async def _replace_devices(
    db: aiosqlite.Connection, secret_key: str, fleet_id: int, devices: list[FleetDevice]
) -> None:
    # An operator without an admin session gets no admin key, so a device without one keeps the saved token.
    cursor = await db.execute("SELECT label, admin_key_ciphertext FROM fleet_devices WHERE fleet_id = ?", (fleet_id,))
    saved = {label: ciphertext for label, ciphertext in await cursor.fetchall()}
    cipher = fernet(secret_key)
    rows = []
    for device in devices:
        if device.admin_key is None:
            ciphertext = saved.get(device.label)
        else:
            ciphertext = cipher.encrypt(device.admin_key.encode()).decode("ascii") if device.admin_key else None
        rows.append(
            {**device.model_dump(exclude={"admin_key"}), "fleet_id": fleet_id, "admin_key_ciphertext": ciphertext}
        )
    await db.execute("DELETE FROM fleet_devices WHERE fleet_id = ?", (fleet_id,))
    await db.executemany(INSERT_DEVICE, rows)


async def create_fleet(database_path: Path, secret_key: str, fleet: FleetRequest) -> int:
    """Save the fleet with its fleet devices, and return the id of the new fleet.

    The admin key of each fleet device is encrypted with a key derived from `secret_key`.
    """
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            "INSERT INTO fleets (name, profile_id, firmware_version) VALUES (?, ?, ?)",
            (fleet.name, fleet.profile_id, fleet.firmware_version),
        )
        await _replace_devices(db, secret_key, cursor.lastrowid, fleet.devices)
        await db.commit()
        return cursor.lastrowid


async def update_fleet(database_path: Path, secret_key: str, fleet_id: int, update: FleetUpdate) -> bool:
    """Change the fields of the fleet that `update` sets, and return False when no fleet has the id `fleet_id`.

    A null `firmware_version` selects the default firmware source. A `devices` list replaces every fleet device.
    """
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM fleets WHERE id = ?", (fleet_id,))
        row = await cursor.fetchone()
        if row is None:
            return False
        fields = update.model_fields_set
        await db.execute(
            "UPDATE fleets SET name = ?, profile_id = ?, firmware_version = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (
                row["name"] if update.name is None else update.name,
                row["profile_id"] if update.profile_id is None else update.profile_id,
                update.firmware_version if "firmware_version" in fields else row["firmware_version"],
                fleet_id,
            ),
        )
        if update.devices is not None:
            await _replace_devices(db, secret_key, fleet_id, update.devices)
        await db.commit()
    return True


async def delete_fleet(database_path: Path, fleet_id: int) -> bool:
    """Delete the fleet with its fleet devices and fleet runs, and return False when no fleet has the id."""
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute("DELETE FROM fleets WHERE id = ?", (fleet_id,))
        await db.execute(
            "DELETE FROM fleet_run_builds WHERE fleet_run_id IN (SELECT id FROM fleet_runs WHERE fleet_id = ?)",
            (fleet_id,),
        )
        await db.execute("DELETE FROM fleet_runs WHERE fleet_id = ?", (fleet_id,))
        await db.execute("DELETE FROM fleet_devices WHERE fleet_id = ?", (fleet_id,))
        await db.commit()
        return cursor.rowcount > 0


CSV_HEADER = ("label", "variant", "long_name", "short_name", "role", "lat", "lon", "alt", "admin_key")


class CsvHeaderError(ValueError):
    """The first line of a fleet CSV has a column outside `CSV_HEADER`, or no `label` column."""


def row_error(row: int | None, field: str | None, msg: str) -> dict[str, Any]:
    """Return one row error. `row` counts the fleet devices from 1, and None marks an error of the whole request."""
    return {"row": row, "field": field, "msg": msg}


def read_csv(text: str) -> list[dict[str | None, Any]]:
    """Return one row for each line after the header of the CSV text, with None for an empty cell.

    The header names the columns in any order, and it can leave out each column except `label`.
    The key None of a row holds the list of cells after the last column of the header.

    Raises:
        CsvHeaderError: The header has an unknown column or no `label` column.
    """
    # A spreadsheet export can start with a byte order mark, which would join the name of the first column.
    reader = csv.DictReader(io.StringIO(text.removeprefix("﻿")))
    columns = [name.strip() for name in reader.fieldnames or ()]
    use = f"Use the header {','.join(CSV_HEADER)}."
    unknown = [name for name in columns if name not in CSV_HEADER]
    if unknown:
        raise CsvHeaderError(f"the header has the unknown column {', '.join(unknown)}. {use}")
    if "label" not in columns:
        raise CsvHeaderError(f"the header has no label column. {use}")
    reader.fieldnames = columns
    return [{key: _cell(value) for key, value in row.items()} for row in reader]


def _cell(value: str | list[str] | None) -> str | list[str] | None:
    if isinstance(value, str):
        return value.strip() or None
    return value


async def _registry(database_path: Path, firmware_version: str | None) -> tuple[str, set[str]] | None:
    """Return the firmware version that a fleet run of the fleet uses, with the envs of its registry.

    The result is None when the fleet names no firmware version and none is installed, because the fleet run then
    reports the missing firmware source. A firmware version that is not installed holds no env.
    """
    source = await find_firmware_source(database_path, firmware_version)
    if source is None:
        return (firmware_version, set()) if firmware_version else None
    return source["version"], {entry["env"] for entry in await list_variants(database_path, source["version"])}


def _label_error(
    row: dict, number: int, first_rows: dict[str, int], saved_labels: Collection[str]
) -> list[dict[str, Any]]:
    """Return the row error of a label that an earlier row or a saved fleet device has, and record a new label."""
    label = str(row.get("label") or "").strip()
    if not label:
        return []
    if label in saved_labels:
        return [row_error(number, "label", f"the fleet has a fleet device with the label {label}")]
    if label in first_rows:
        return [row_error(number, "label", f"the label {label} is in row {first_rows[label]} too")]
    first_rows[label] = number
    return []


async def check_devices(
    database_path: Path, firmware_version: str | None, rows: list[dict], saved_labels: Collection[str] = ()
) -> tuple[list[FleetDevice], list[dict[str, Any]]]:
    """Return the fleet device of each row and every row error, in row order (FR-fleet-builds-13).

    Args:
        database_path: The database that holds the registry of each firmware version.
        firmware_version: The firmware version of the fleet. None selects the default firmware source.
        rows: The fields of each fleet device. The first row is row 1.
        saved_labels: The labels of the fleet devices that the rows go after.

    A row error names a duplicate label, a variant that is not in the firmware version, or a field error.
    """
    registry = await _registry(database_path, firmware_version)
    devices, errors, first_rows = [], [], {}
    for number, row in enumerate(rows, start=1):
        if row.get(None):
            errors.append(row_error(number, None, "the row has more cells than the header"))
        errors += _label_error(row, number, first_rows, saved_labels)
        try:
            device = FleetDevice.model_validate({key: value for key, value in row.items() if key is not None})
        except ValidationError as e:
            # Pydantic puts "Value error, " before the text of a ValueError, and the operator needs only the text.
            errors += [
                row_error(number, str(err["loc"][0]), err["msg"].removeprefix("Value error, ")) for err in e.errors()
            ]
            continue
        if registry and device.variant and device.variant not in registry[1]:
            msg = f"the variant {device.variant} is not in firmware version {registry[0]}"
            errors.append(row_error(number, "variant", msg))
        devices.append(device)
    return devices, errors


async def add_devices(database_path: Path, secret_key: str, fleet_id: int, devices: list[FleetDevice]) -> None:
    """Save `devices` after the saved fleet devices of the fleet, which keep their admin keys (FR-fleet-builds-12)."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM fleet_devices WHERE fleet_id = ? ORDER BY id", (fleet_id,))
        saved = [FleetDevice.model_validate(_device(row, None)) for row in await cursor.fetchall()]
        await _replace_devices(db, secret_key, fleet_id, saved + devices)
        await db.execute("UPDATE fleets SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (fleet_id,))
        await db.commit()
