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
    completed_at TIMESTAMP
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


async def init_db(settings: Settings) -> None:
    """Create tables if they don't exist."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def get_db(settings: Settings) -> aiosqlite.Connection:
    """Get a database connection."""
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    return db


async def record_build(
    db: aiosqlite.Connection,
    build_id: str,
    variant: str,
    architecture: str,
    firmware_format: str,
    status: str = "queued",
) -> None:
    """Insert a new build record."""
    await db.execute(
        "INSERT INTO builds (id, variant, architecture, firmware_format, status) VALUES (?, ?, ?, ?, ?)",
        (build_id, variant, architecture, firmware_format, status),
    )
    await db.commit()


async def update_build_status(
    db: aiosqlite.Connection,
    build_id: str,
    status: str,
    firmware_path: str | None = None,
    build_log: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update build status and optional fields."""
    fields = ["status = ?"]
    values: list = [status]

    if firmware_path is not None:
        fields.append("firmware_path = ?")
        values.append(firmware_path)
    if build_log is not None:
        fields.append("build_log = ?")
        values.append(build_log)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if status in ("complete", "failed"):
        fields.append("completed_at = CURRENT_TIMESTAMP")

    values.append(build_id)
    await db.execute(f"UPDATE builds SET {', '.join(fields)} WHERE id = ?", values)
    await db.commit()


async def get_build(db: aiosqlite.Connection, build_id: str) -> dict | None:
    """Get a build record by ID."""
    cursor = await db.execute("SELECT * FROM builds WHERE id = ?", (build_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def get_recent_builds(db: aiosqlite.Connection, limit: int = 20) -> list[dict]:
    """Get recent builds ordered by creation time."""
    cursor = await db.execute(
        "SELECT * FROM builds ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]
