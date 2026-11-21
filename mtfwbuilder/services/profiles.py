"""Profiles: saved configs without secrets (feature fleet-builds, FR 1, 2 and 4).

Each operator of the instance uses each profile. The spec trusts the instance, so a profile has no owner and a
delete needs no admin session.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite

from mtfwbuilder.models import ConfigRequest

# The match is on a part of the field name, so `channel_N[psk]` and an extra field such as `oem_admin_key` match too.
SECRET_FIELD = re.compile(r"psk|password|admin_key", re.IGNORECASE)

INSERT_PROFILE = """
INSERT INTO profiles (name, config_json, must_reenter_json) VALUES (:name, :config_json, :must_reenter_json)
"""
UPDATE_PROFILE = """
UPDATE profiles SET
    name = COALESCE(:name, name),
    config_json = COALESCE(:config_json, config_json),
    must_reenter_json = COALESCE(:must_reenter_json, must_reenter_json),
    updated_at = CURRENT_TIMESTAMP
WHERE id = :id
"""


class DuplicateProfileName(Exception):
    """Another profile has the name."""


def remove_secrets(config: ConfigRequest) -> tuple[dict[str, Any], list[str]]:
    """Return the flat form fields of `config` without secrets, and the sorted names of the removed fields.

    An empty secret field stays, because it holds no secret, and a channel PSK of zero bytes means no encryption.
    """
    fields = config.generator_input()
    # The preview flag selects a route response, so it is not part of a config.
    fields.pop("preview_only", None)
    removed = sorted(name for name, value in fields.items() if SECRET_FIELD.search(name) and value != "")
    return {name: value for name, value in fields.items() if name not in removed}, removed


def _profile(row: aiosqlite.Row) -> dict[str, Any]:
    record = dict(row)
    return {
        "id": record["id"],
        "name": record["name"],
        "config": json.loads(record["config_json"]),
        "must_reenter": json.loads(record["must_reenter_json"]),
        "stores_secrets": bool(record["stores_secrets"]),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


def _columns(config: ConfigRequest | None) -> dict[str, str | None]:
    if config is None:
        return {"config_json": None, "must_reenter_json": None}
    fields, removed = remove_secrets(config)
    return {"config_json": json.dumps(fields), "must_reenter_json": json.dumps(removed)}


async def list_profiles(database_path: Path) -> list[dict[str, Any]]:
    """Return every profile in name order."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM profiles ORDER BY name")
        return [_profile(row) for row in await cursor.fetchall()]


async def get_profile(database_path: Path, profile_id: int) -> dict[str, Any] | None:
    """Return the profile with the id `profile_id`, or None when no profile has it."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        row = await cursor.fetchone()
    return _profile(row) if row else None


async def create_profile(database_path: Path, name: str, config: ConfigRequest) -> dict[str, Any]:
    """Save `config` without secrets as the profile `name`, and return the profile.

    Raises:
        DuplicateProfileName: Another profile has the name `name`.
    """
    async with aiosqlite.connect(database_path) as db:
        try:
            cursor = await db.execute(INSERT_PROFILE, {"name": name, **_columns(config)})
        except sqlite3.IntegrityError as error:
            raise DuplicateProfileName(name) from error
        await db.commit()
        profile_id = cursor.lastrowid
    return await get_profile(database_path, profile_id)


async def update_profile(
    database_path: Path, profile_id: int, name: str | None, config: ConfigRequest | None
) -> dict[str, Any] | None:
    """Rename the profile, replace its config without secrets, or both, and return the profile.

    A None argument keeps the saved value. Returns None when no profile has the id `profile_id`.

    Raises:
        DuplicateProfileName: Another profile has the name `name`.
    """
    async with aiosqlite.connect(database_path) as db:
        try:
            cursor = await db.execute(UPDATE_PROFILE, {"id": profile_id, "name": name, **_columns(config)})
        except sqlite3.IntegrityError as error:
            raise DuplicateProfileName(name) from error
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_profile(database_path, profile_id)


async def delete_profile(database_path: Path, profile_id: int) -> bool:
    """Delete the profile, and return False when no profile has the id `profile_id`."""
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        await db.commit()
        return cursor.rowcount > 0
