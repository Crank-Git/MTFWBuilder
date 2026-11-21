"""The build cache: the artifacts of each complete build, keyed by the config hash (feature 14).

A cache entry is a directory `<data_dir>/cache/<config_hash>/` and a row of the `cache_entries` table.
The directory holds only artifacts, never the userPrefs file or the build log. An artifact holds the
secrets of its config, so a config with a secret never goes into the build cache (PM decision on #60).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

import mtfwbuilder
from mtfwbuilder.key_bytes import decode_key
from mtfwbuilder.services.secret_mask import PREF_PAIR

if TYPE_CHECKING:
    # config.py imports cleanup_service, and cleanup_service imports this module.
    from mtfwbuilder.config import Settings

logger = logging.getLogger("mtfwbuilder.cache")
# Upstream `bin/platformio-custom.py` removes all text after // on a line before it parses the userPrefs file.
COMMENT = re.compile("//.*")
# The keys of a channel PSK, the WiFi PSK, the MQTT password, an admin key and the mesh beacon offer PSK.
SECRET_PREF = re.compile(r"_PSK|PASSWORD|ADMIN_KEY")
CHANNEL_PSK = re.compile(r"USERPREFS_CHANNEL_\d+_PSK")
# Upstream `src/mesh/Channels.cpp` reads a 1-byte channel PSK as the index of a published default key.
DEFAULT_KEY_INDEX_LENGTH = 1
ENTRY_NAME = re.compile(r"[0-9a-f]{64}")
# `_copy_into_entry` names a staging directory `.<config_hash>.<8 hex characters>`.
STAGING_NAME = re.compile(r"\.[0-9a-f]{64}\.[0-9a-f]{8}")

SAVE_ENTRY = """
INSERT INTO cache_entries
    (config_hash, firmware_version, variant, artifact_dir, artifact_names, created_at, last_used_at)
VALUES (:config_hash, :firmware_version, :variant, :artifact_dir, :artifact_names, :now, :now)
ON CONFLICT(config_hash) DO UPDATE SET last_used_at = excluded.last_used_at
"""


def config_hash(secret_key: str, firmware_version: str | None, variant: str, userprefs: str) -> str:
    """Return the config hash of a build: an HMAC-SHA-256 with `secret_key`.

    The hash covers the app version, the firmware version, the variant and the userPrefs file with its keys
    sorted. So two userPrefs files with the same keys in another order give one config hash. A userPrefs file
    that is not JSON goes into the hash as text.
    """
    try:
        prefs = json.dumps(json.loads(COMMENT.sub("", userprefs)), sort_keys=True)
    except json.JSONDecodeError:
        prefs = userprefs
    # A new app version can write other artifacts from one userPrefs file, so its cache entries must not match.
    message = json.dumps([mtfwbuilder.__version__, firmware_version, variant, prefs])
    # The userPrefs file holds the secrets, so a hash without a key lets a reader test a guessed PSK.
    return hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def holds_secret(userprefs: str) -> bool:
    """Return True when the userPrefs file holds a secret, so its artifacts must not go into the build cache.

    An empty value holds no secret. A 1-byte channel PSK holds no secret, because it selects a published key.
    """
    for key, raw in PREF_PAIR.findall(userprefs):
        if raw.strip() and SECRET_PREF.search(key) and not _is_default_key_index(key, raw):
            return True
    return False


def _is_default_key_index(key: str, raw: str) -> bool:
    if not CHANNEL_PSK.fullmatch(key):
        return False
    try:
        return len(decode_key(raw)) <= DEFAULT_KEY_INDEX_LENGTH
    except ValueError:
        return False


def cache_dir(settings: Settings) -> Path:
    """Return the directory of the build cache, `<data_dir>/cache`."""
    return settings.data_dir / "cache"


async def find_entry(settings: Settings, key: str) -> Path | None:
    """Return the directory of the cache entry of the config hash `key`, after its last use changes to now.

    Returns None when no cache entry matches. A cache entry without each of its artifacts also gives None,
    and its row and its directory go.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            "SELECT artifact_dir, artifact_names FROM cache_entries WHERE config_hash = ?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        entry_dir = Path(row[0])
        names = json.loads(row[1] or "[]")
        found = bool(names) and all((entry_dir / name).is_file() for name in names)
        if found:
            await db.execute("UPDATE cache_entries SET last_used_at = ? WHERE config_hash = ?", (time.time(), key))
        else:
            await db.execute("DELETE FROM cache_entries WHERE config_hash = ?", (key,))
        await db.commit()
    if not found:
        await asyncio.to_thread(shutil.rmtree, entry_dir, ignore_errors=True)
        return None
    return entry_dir


async def remove_entry(settings: Settings, key: str) -> None:
    """Delete the row and the directory of the cache entry of the config hash `key`."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("DELETE FROM cache_entries WHERE config_hash = ?", (key,))
        await db.commit()
    await asyncio.to_thread(shutil.rmtree, cache_dir(settings) / key, ignore_errors=True)


async def store_entry(settings: Settings, key: str, firmware_version: str, variant: str, artifacts: list[Path]) -> None:
    """Copy the artifacts into the cache entry of the config hash `key`, and write the row of the cache entry.

    A cancel during the store does not stop the store, so no directory stays without its row.
    Raises `OSError` when a copy fails, and `sqlite3.Error` when the write fails.
    """
    await asyncio.shield(_store_entry(settings, key, firmware_version, variant, artifacts))


async def _store_entry(
    settings: Settings, key: str, firmware_version: str, variant: str, artifacts: list[Path]
) -> None:
    entry_dir = cache_dir(settings) / key
    await asyncio.to_thread(_copy_into_entry, artifacts, entry_dir)
    row = {
        "config_hash": key,
        "firmware_version": firmware_version,
        "variant": variant,
        "artifact_dir": str(entry_dir),
        "artifact_names": json.dumps(sorted(path.name for path in artifacts)),
        "now": time.time(),
    }
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(SAVE_ENTRY, row)
        await db.commit()


def _copy_into_entry(artifacts: list[Path], entry_dir: Path) -> None:
    # A cache hit reads the directory at any time, so the copy goes to a staging directory and then moves in one step.
    staging = entry_dir.with_name(f".{entry_dir.name}.{secrets.token_hex(4)}")
    staging.mkdir(parents=True)
    for path in artifacts:
        shutil.copy2(path, staging / path.name)
    try:
        staging.rename(entry_dir)
    except OSError:
        # Another build of the same config can move its directory in first, and its artifacts are the same.
        shutil.rmtree(staging)
        if not entry_dir.is_dir():
            raise


async def remove_expired_entries(settings: Settings) -> int:
    """Return the number of cache entries removed, after each one unused for `cache_ttl_seconds` is removed.

    The rows go in one transaction, and the directories go after the commit.
    """
    oldest = time.time() - settings.cache_ttl_seconds
    expired = []
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            "SELECT config_hash, artifact_dir FROM cache_entries WHERE last_used_at < ?", (oldest,)
        )
        for key, artifact_dir in await cursor.fetchall():
            # A cache hit after the select sets a new last use, so the delete checks the age again.
            deleted = await db.execute(
                "DELETE FROM cache_entries WHERE config_hash = ? AND last_used_at < ?", (key, oldest)
            )
            if deleted.rowcount:
                expired.append(artifact_dir)
        await db.commit()
    # A remove of many files takes long, so it runs in a thread after the commit and keeps no write lock.
    for artifact_dir in expired:
        await asyncio.to_thread(shutil.rmtree, artifact_dir, ignore_errors=True)
    if expired:
        logger.info(f"Removed {len(expired)} expired build cache entries")
    return len(expired)


async def remove_orphan_folders(settings: Settings) -> int:
    """Return the number of directories removed from the build cache directory.

    The function removes each cache entry directory without a row, and each staging directory. A directory
    goes only when its last change is older than `cache_ttl_seconds`. A directory with another name stays.
    """
    root = cache_dir(settings)
    if not root.is_dir():
        return 0
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT config_hash FROM cache_entries")
        keys = {row[0] for row in await cursor.fetchall()}
    # A store moves its directory in before it writes the row, so a young directory without a row is normal.
    oldest = time.time() - settings.cache_ttl_seconds
    orphans = [
        path
        for path in root.iterdir()
        if path.is_dir()
        and path.stat().st_mtime < oldest
        and (STAGING_NAME.fullmatch(path.name) or (ENTRY_NAME.fullmatch(path.name) and path.name not in keys))
    ]
    for path in orphans:
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
    if orphans:
        logger.info(f"Removed {len(orphans)} build cache directories without a cache entry")
    return len(orphans)
