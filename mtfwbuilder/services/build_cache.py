"""The build cache: the artifacts of each complete build, keyed by the config hash (feature 14).

A cache entry is a directory `<data_dir>/cache/<config_hash>/` and a row of the `cache_entries` table.
The directory holds only artifacts, never the userPrefs file or the build log. An artifact holds the
secrets of its config, as each artifact does, so the TTL limits how long the secrets stay on disk.
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

if TYPE_CHECKING:
    # config.py imports cleanup_service, and cleanup_service imports this module.
    from mtfwbuilder.config import Settings

logger = logging.getLogger("mtfwbuilder.cache")
# Upstream `bin/platformio-custom.py` removes all text after // on a line before it parses the userPrefs file.
COMMENT = re.compile("//.*")

SAVE_ENTRY = """
INSERT INTO cache_entries (config_hash, firmware_version, variant, artifact_dir, created_at, last_used_at)
VALUES (:config_hash, :firmware_version, :variant, :artifact_dir, :now, :now)
ON CONFLICT(config_hash) DO UPDATE SET last_used_at = excluded.last_used_at
"""


def config_hash(secret_key: str, firmware_version: str | None, variant: str, userprefs: str) -> str:
    """Return the config hash of a build: an HMAC-SHA-256 with `secret_key`.

    The hash covers the firmware version, the variant and the userPrefs file with its keys sorted. So two
    userPrefs files with the same keys in another order give one config hash. A userPrefs file that is not
    JSON goes into the hash as text.
    """
    try:
        prefs = json.dumps(json.loads(COMMENT.sub("", userprefs)), sort_keys=True)
    except json.JSONDecodeError:
        prefs = userprefs
    message = json.dumps([firmware_version, variant, prefs])
    # The userPrefs file holds the secrets, so a hash without a key lets a reader test a guessed PSK.
    return hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def cache_dir(settings: Settings) -> Path:
    """Return the directory of the build cache, `<data_dir>/cache`."""
    return settings.data_dir / "cache"


async def find_entry(settings: Settings, key: str) -> Path | None:
    """Return the directory of the cache entry of the config hash `key`, after its last use changes to now.

    Returns None when no cache entry matches. A cache entry without its directory also gives None, and its row goes.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT artifact_dir FROM cache_entries WHERE config_hash = ?", (key,))
        row = await cursor.fetchone()
        if row is None:
            return None
        entry_dir = Path(row[0])
        if entry_dir.is_dir():
            await db.execute("UPDATE cache_entries SET last_used_at = ? WHERE config_hash = ?", (time.time(), key))
        else:
            await db.execute("DELETE FROM cache_entries WHERE config_hash = ?", (key,))
        await db.commit()
    return entry_dir if entry_dir.is_dir() else None


async def store_entry(settings: Settings, key: str, firmware_version: str, variant: str, artifacts: list[Path]) -> None:
    """Copy the artifacts into the cache entry of the config hash `key`, and write the row of the cache entry.

    Raises `OSError` when a copy fails, and `sqlite3.Error` when the write fails.
    """
    entry_dir = cache_dir(settings) / key
    await asyncio.to_thread(_copy_into_entry, artifacts, entry_dir)
    row = {
        "config_hash": key,
        "firmware_version": firmware_version,
        "variant": variant,
        "artifact_dir": str(entry_dir),
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
    """Return the number of cache entries removed, after each one unused for `cache_ttl_seconds` is removed."""
    oldest = time.time() - settings.cache_ttl_seconds
    removed = 0
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
                shutil.rmtree(artifact_dir, ignore_errors=True)
                removed += 1
        await db.commit()
    if removed:
        logger.info(f"Removed {removed} expired build cache entries")
    return removed
