"""SQLite database initialization and access (raw SQL, no ORM)."""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import aiosqlite

from mtfwbuilder.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS builds (
    id TEXT PRIMARY KEY,
    variant TEXT NOT NULL,
    architecture TEXT NOT NULL,
    firmware_format TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    firmware_path TEXT,
    build_log TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    config_hash TEXT,
    firmware_version TEXT,
    fleet_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    config_json TEXT NOT NULL,
    must_reenter_json TEXT NOT NULL,
    stores_secrets INTEGER NOT NULL DEFAULT 0,
    secrets_ciphertext TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS firmware_sources (
    version TEXT PRIMARY KEY,
    channel TEXT,
    path TEXT,
    installed_at TIMESTAMP,
    size_bytes INTEGER,
    status TEXT
);

CREATE TABLE IF NOT EXISTS variants (
    version TEXT NOT NULL,
    env TEXT NOT NULL,
    display_name TEXT NOT NULL,
    manufacturer TEXT NOT NULL,
    architecture TEXT NOT NULL,
    support_level TEXT NOT NULL,
    hw_model INTEGER,
    hw_model_slug TEXT,
    image TEXT,
    requires_dfu INTEGER NOT NULL,
    partition_scheme TEXT,
    has_mui INTEGER NOT NULL,
    has_ink_hud INTEGER NOT NULL,
    actively_supported INTEGER NOT NULL,
    PRIMARY KEY (version, env)
);

CREATE INDEX IF NOT EXISTS idx_builds_status ON builds(status);
CREATE INDEX IF NOT EXISTS idx_builds_created ON builds(created_at);
"""

# The times are Unix seconds, so the TTL check of the build cache compares numbers.
CACHE_ENTRIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    config_hash TEXT PRIMARY KEY,
    firmware_version TEXT NOT NULL,
    variant TEXT NOT NULL,
    artifact_dir TEXT NOT NULL,
    artifact_names TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL
);
"""

# Migration steps for feature fleet-builds, one table each. SQLite enforces no foreign key without a PRAGMA, so a
# fleet delete removes the rows of the other tables itself.
FLEET_SCHEMAS = (
    """
CREATE TABLE IF NOT EXISTS fleets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    profile_id INTEGER NOT NULL,
    firmware_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""",
    """
CREATE TABLE IF NOT EXISTS fleet_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fleet_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    variant TEXT,
    long_name TEXT,
    short_name TEXT,
    role TEXT,
    fixed_lat REAL,
    fixed_lon REAL,
    fixed_alt INTEGER,
    admin_key_ciphertext TEXT,
    position INTEGER
)
""",
    """
CREATE TABLE IF NOT EXISTS fleet_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fleet_id INTEGER NOT NULL,
    firmware_version TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""",
    """
CREATE TABLE IF NOT EXISTS fleet_run_builds (
    fleet_run_id INTEGER NOT NULL,
    fleet_device_id INTEGER NOT NULL,
    build_id TEXT,
    PRIMARY KEY (fleet_run_id, fleet_device_id)
)
""",
)

# Migration for feature fleet-builds: `profiles` replaces `config_profiles`, which no code wrote, so it holds no rows.
DROP_CONFIG_PROFILES = "DROP TABLE IF EXISTS config_profiles"

# `CREATE TABLE IF NOT EXISTS` does not change a table that exists, so an older database gets these columns here.
ADDED_COLUMNS = {
    "builds": {"config_hash": "TEXT", "firmware_version": "TEXT", "fleet_run_id": "INTEGER"},
    "profiles": {"secrets_ciphertext": "TEXT"},
    "cache_entries": {"artifact_names": "TEXT"},
    "fleet_devices": {"position": "INTEGER"},
}

# A build in one of these build statuses has not ended.
UNENDED_STATUSES = ("queued", "compiling", "linking", "packaging")

SAVE_BUILD = """
INSERT INTO builds (
    id, variant, architecture, firmware_format, status, firmware_path, error_message,
    config_hash, firmware_version, fleet_run_id, completed_at
) VALUES (
    :id, :variant, :architecture, :firmware_format, :status, :firmware_path, :error_message,
    :config_hash, :firmware_version, :fleet_run_id,
    CASE WHEN :status IN ('complete', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP END
)
ON CONFLICT(id) DO UPDATE SET
    status = excluded.status,
    firmware_path = excluded.firmware_path,
    error_message = excluded.error_message,
    firmware_version = excluded.firmware_version,
    completed_at = excluded.completed_at
"""

SAVE_FIRMWARE_SOURCE = """
INSERT OR REPLACE INTO firmware_sources (version, channel, path, installed_at, size_bytes, status)
VALUES (:version, :channel, :path, :installed_at, :size_bytes, :status)
"""

SAVE_VARIANT = """
INSERT INTO variants (
    version, env, display_name, manufacturer, architecture, support_level, hw_model, hw_model_slug, image,
    requires_dfu, partition_scheme, has_mui, has_ink_hud, actively_supported
) VALUES (
    :version, :env, :display_name, :manufacturer, :architecture, :support_level, :hw_model, :hw_model_slug, :image,
    :requires_dfu, :partition_scheme, :has_mui, :has_ink_hud, :actively_supported
)
"""

FIND_FIRMWARE_SOURCE = (
    "SELECT * FROM firmware_sources WHERE status = 'ready' AND (:version IS NULL OR version = :version)"
)


def _default_order(record: dict) -> tuple:
    # A tag is `vX.Y.Z.<hash>`, and a text order puts v2.10.0 before v2.9.0, so the numbers decide.
    numbers = tuple(int(part) for part in record["version"].removeprefix("v").split(".")[:3])
    return (record["channel"] == "stable", numbers)


async def init_db(settings: Settings) -> None:
    """Create the tables, drop the unused `config_profiles` table, and add the columns that an old database lacks."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA)
        await db.executescript(CACHE_ENTRIES_SCHEMA)
        for schema in FLEET_SCHEMAS:
            await db.execute(schema)
        await db.execute(DROP_CONFIG_PROFILES)
        for table, added in ADDED_COLUMNS.items():
            cursor = await db.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in await cursor.fetchall()}
            for name, sql_type in added.items():
                if name not in columns:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        await db.commit()


async def get_db(settings: Settings) -> aiosqlite.Connection:
    """Get a database connection."""
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    return db


async def save_build(db: aiosqlite.Connection, record: dict) -> None:
    """Insert the build record, or update the build status fields of the record with the same build id.

    `record` holds one value for each named parameter of `SAVE_BUILD`. It never holds the build log or the config.
    """
    await db.execute(SAVE_BUILD, record)
    await db.commit()


async def fail_unended_builds(db: aiosqlite.Connection, error_message: str) -> int:
    """Return the number of build records that change from an unended build status to `failed`."""
    placeholders = ", ".join("?" for _ in UNENDED_STATUSES)
    cursor = await db.execute(
        "UPDATE builds SET status = 'failed', error_message = ?, completed_at = CURRENT_TIMESTAMP "
        f"WHERE status IN ({placeholders})",
        (error_message, *UNENDED_STATUSES),
    )
    await db.commit()
    return cursor.rowcount


async def get_build(db: aiosqlite.Connection, build_id: str) -> dict | None:
    """Get a build record by ID."""
    cursor = await db.execute("SELECT * FROM builds WHERE id = ?", (build_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def get_recent_builds(db: aiosqlite.Connection, limit: int = 20) -> list[dict]:
    """Get recent builds ordered by creation time."""
    cursor = await db.execute("SELECT * FROM builds ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


def record_firmware_source(database_path: Path, record: dict, registry: Sequence[dict] = ()) -> None:
    """Insert the firmware source record and its registry, or replace those of the same firmware version.

    `record` holds one value for each named parameter of `SAVE_FIRMWARE_SOURCE`. Each `registry` entry holds one
    value for each named parameter of `SAVE_VARIANT` except `version`. The firmware updater runs in a worker
    thread, so this write uses the `sqlite3` module and no event loop.
    """
    # One transaction writes both, so a build never sees a ready firmware source without its registry.
    with closing(sqlite3.connect(database_path)) as db, db:
        db.execute("DELETE FROM variants WHERE version = ?", (record["version"],))
        db.executemany(SAVE_VARIANT, [{**entry, "version": record["version"]} for entry in registry])
        db.execute(SAVE_FIRMWARE_SOURCE, record)


async def find_firmware_source(database_path: Path, version: str | None = None) -> dict | None:
    """Return the ready firmware source record of `version`, or None when no record matches.

    When `version` is None, the result is the default firmware source: the stable firmware source with the highest
    version number, else the firmware source of any release channel with the highest version number.
    """
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(FIND_FIRMWARE_SOURCE, {"version": version})
        records = [dict(row) for row in await cursor.fetchall()]
    return max(records, key=_default_order, default=None)


async def delete_firmware_source(database_path: Path, version: str) -> None:
    """Delete the firmware source record of `version` and its registry."""
    async with aiosqlite.connect(database_path) as db:
        await db.execute("DELETE FROM variants WHERE version = ?", (version,))
        await db.execute("DELETE FROM firmware_sources WHERE version = ?", (version,))
        await db.commit()


async def list_variants(database_path: Path, version: str) -> list[dict]:
    """Return the registry entries of the firmware version, in env order, without the `version` field.

    The fields `requires_dfu`, `has_mui`, `has_ink_hud` and `actively_supported` are booleans.
    """
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM variants WHERE version = ? ORDER BY env", (version,))
        rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        del row["version"]
        for name in ("requires_dfu", "has_mui", "has_ink_hud", "actively_supported"):
            row[name] = bool(row[name])
    return rows


async def list_firmware_sources(database_path: Path) -> list[dict]:
    """Return every firmware source record, the newest installed first."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM firmware_sources ORDER BY installed_at DESC")
        return [dict(row) for row in await cursor.fetchall()]
