"""Profiles: saved configs, with secrets only when the operator opts in (feature fleet-builds, FR 1 to 4).

Each operator of the instance uses each profile. The spec trusts the instance, so a profile has no owner and a
delete needs no admin session.

`config_json` and `must_reenter_json` always hold the config without secrets. A profile that stores secrets also
holds them in `secrets_ciphertext`, encrypted with a key derived from `secret_key`.
"""

import base64
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mtfwbuilder.models import ConfigRequest, ProfileRequest, ProfileUpdate

logger = logging.getLogger("mtfwbuilder.profiles")

# The match is on a part of the field name, so `channel_N[psk]` and an extra field such as `oem_admin_key` match too.
SECRET_FIELD = re.compile(r"psk|password|admin_key", re.IGNORECASE)

# The page shows this text for a profile with `stores_secrets` true (FR-fleet-builds-3).
STORES_SECRETS_NOTICE = "This profile stores secrets on the server."

# A profile route needs no admin session, so the stored secrets reach only an admin session (PM decision on #62).
SIGN_IN_FOR_SECRETS = "Sign in as admin to load the stored secrets."

UNREADABLE_SECRETS = (
    "The stored secrets of this profile do not decrypt with the current secret_key. Enter the secrets again."
)

# The signers in `auth.py` also use `secret_key`, so a fixed context gives the profile key a separate value.
_KEY_CONTEXT = b"mtfwbuilder profile secrets v1"

INSERT_PROFILE = """
INSERT INTO profiles (name, config_json, must_reenter_json, stores_secrets, secrets_ciphertext)
VALUES (:name, :config_json, :must_reenter_json, :stores_secrets, :secrets_ciphertext)
"""
UPDATE_PROFILE = """
UPDATE profiles SET
    name = COALESCE(:name, name),
    config_json = :config_json,
    must_reenter_json = :must_reenter_json,
    stores_secrets = :stores_secrets,
    secrets_ciphertext = :secrets_ciphertext,
    updated_at = CURRENT_TIMESTAMP
WHERE id = :id
"""


class DuplicateProfileName(Exception):
    """Another profile has the name."""


def split_secrets(config: ConfigRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the flat form fields of `config` without secrets, and the secret fields.

    An empty secret field stays, because it holds no secret, and a channel PSK of zero bytes means no encryption.
    """
    fields = config.generator_input()
    # The preview flag selects a route response, so it is not part of a config.
    fields.pop("preview_only", None)
    secrets = {name: value for name, value in fields.items() if SECRET_FIELD.search(name) and value != ""}
    return {name: value for name, value in fields.items() if name not in secrets}, secrets


def _fernet(secret_key: str) -> Fernet:
    """Return the Fernet cipher with the key that HKDF-SHA256 derives from `secret_key`."""
    # `secret_key` is random text of high entropy, so HKDF needs no salt to give a uniform key.
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_KEY_CONTEXT).derive(secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _profile(row: aiosqlite.Row, secret_key: str | None) -> dict[str, Any]:
    record = dict(row)
    config = json.loads(record["config_json"])
    must_reenter = json.loads(record["must_reenter_json"])
    warning = None
    if secret_key is not None and record["secrets_ciphertext"]:
        try:
            secrets = json.loads(_fernet(secret_key).decrypt(record["secrets_ciphertext"]))
        except InvalidToken:
            logger.warning("The stored secrets of profile %s do not decrypt with the current secret_key", record["id"])
            warning = UNREADABLE_SECRETS
        else:
            config.update(secrets)
            must_reenter = [name for name in must_reenter if name not in secrets]
    return {
        "id": record["id"],
        "name": record["name"],
        "config": config,
        "must_reenter": must_reenter,
        "stores_secrets": bool(record["stores_secrets"]),
        "warning": warning,
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


def _columns(config: ConfigRequest, stores_secrets: bool, secret_key: str) -> dict[str, Any]:
    fields, secrets = split_secrets(config)
    return {
        "config_json": json.dumps(fields),
        "must_reenter_json": json.dumps(sorted(secrets)),
        "stores_secrets": int(stores_secrets),
        "secrets_ciphertext": (
            _fernet(secret_key).encrypt(json.dumps(secrets).encode()).decode("ascii") if stores_secrets else None
        ),
    }


async def list_profiles(database_path: Path) -> list[dict[str, Any]]:
    """Return every profile in name order, without secrets."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM profiles ORDER BY name")
        return [_profile(row, None) for row in await cursor.fetchall()]


async def get_profile(database_path: Path, profile_id: int, secret_key: str | None) -> dict[str, Any] | None:
    """Return the profile with the id `profile_id`, or None when no profile has it.

    A `secret_key` of None gives the profile without secrets. Otherwise the profile holds the stored secrets that
    decrypt with `secret_key`.
    """
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        row = await cursor.fetchone()
    return _profile(row, secret_key) if row else None


async def create_profile(database_path: Path, secret_key: str, profile: ProfileRequest) -> int:
    """Save the config of `profile` under its name, and return the id of the new profile.

    When `profile.stores_secrets` is true, the profile keeps the secrets encrypted with a key derived from
    `secret_key`.

    Raises:
        DuplicateProfileName: Another profile has the name.
    """
    columns = _columns(profile.config, profile.stores_secrets, secret_key)
    async with aiosqlite.connect(database_path) as db:
        try:
            cursor = await db.execute(INSERT_PROFILE, {"name": profile.name, **columns})
        except sqlite3.IntegrityError as error:
            raise DuplicateProfileName(profile.name) from error
        await db.commit()
        return cursor.lastrowid


async def update_profile(database_path: Path, secret_key: str, profile_id: int, update: ProfileUpdate) -> bool:
    """Change the name, the config or the store secrets option of the profile.

    A None field of `update` keeps the saved value. A false `stores_secrets` deletes the stored secrets. A true
    `stores_secrets` without a config stores no secret, so each secret field stays in `must_reenter`.
    Returns False when no profile has the id `profile_id`.

    Raises:
        DuplicateProfileName: Another profile has the new name.
    """
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        row = await cursor.fetchone()
        if row is None:
            return False
        stores = bool(row["stores_secrets"]) if update.stores_secrets is None else update.stores_secrets
        if update.config is None:
            columns = {
                "config_json": row["config_json"],
                "must_reenter_json": row["must_reenter_json"],
                "stores_secrets": int(stores),
                "secrets_ciphertext": row["secrets_ciphertext"] if stores else None,
            }
        else:
            columns = _columns(update.config, stores, secret_key)
        try:
            await db.execute(UPDATE_PROFILE, {"id": profile_id, "name": update.name, **columns})
        except sqlite3.IntegrityError as error:
            raise DuplicateProfileName(update.name) from error
        await db.commit()
    return True


async def delete_profile(database_path: Path, profile_id: int) -> bool:
    """Delete the profile, and return False when no profile has the id `profile_id`."""
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        await db.commit()
        return cursor.rowcount > 0
