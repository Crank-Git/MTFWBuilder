"""Fleets: named groups of fleet devices that share one profile (feature fleet-builds, FR 10, 11 and 14).

An admin key is a secret, so a `fleet_devices` row holds it only as a Fernet token (ADR 0007). Only an admin
session gets the decrypted admin key on a load, the same as the stored secrets of a profile.
"""

import logging
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.fernet import InvalidToken

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
