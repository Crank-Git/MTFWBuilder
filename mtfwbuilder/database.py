"""SQLite database initialization and access (raw SQL, no ORM)."""

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
    firmware_version TEXT
);

CREATE TABLE IF NOT EXISTS config_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_builds_status ON builds(status);
CREATE INDEX IF NOT EXISTS idx_builds_created ON builds(created_at);
"""

# `CREATE TABLE IF NOT EXISTS` does not change a table that exists, so an older database gets these columns here.
ADDED_BUILD_COLUMNS = {"config_hash": "TEXT", "firmware_version": "TEXT"}

# A build in one of these build statuses has not ended.
UNENDED_STATUSES = ("queued", "compiling", "linking", "packaging")

SAVE_BUILD = """
INSERT INTO builds (
    id, variant, architecture, firmware_format, status, firmware_path, error_message,
    config_hash, firmware_version, completed_at
) VALUES (
    :id, :variant, :architecture, :firmware_format, :status, :firmware_path, :error_message,
    :config_hash, :firmware_version,
    CASE WHEN :status IN ('complete', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP END
)
ON CONFLICT(id) DO UPDATE SET
    status = excluded.status,
    firmware_path = excluded.firmware_path,
    error_message = excluded.error_message,
    firmware_version = excluded.firmware_version,
    completed_at = excluded.completed_at
"""


async def init_db(settings: Settings) -> None:
    """Create the tables, and add the columns that an older database does not have."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA)
        cursor = await db.execute("PRAGMA table_info(builds)")
        columns = {row[1] for row in await cursor.fetchall()}
        for name, sql_type in ADDED_BUILD_COLUMNS.items():
            if name not in columns:
                await db.execute(f"ALTER TABLE builds ADD COLUMN {name} {sql_type}")
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
