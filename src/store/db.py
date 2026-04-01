"""SQLite database setup and schema."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_edna_items (
    monday_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    board_group TEXT,
    award TEXT,
    category TEXT,
    edna_status TEXT,
    triage_score REAL,
    writer TEXT,
    reviewer TEXT,
    edna_review_link TEXT,
    edna_review_link_text TEXT,
    monday_updated_at TEXT,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def get_db(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection and ensure schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # Migrations — idempotent column additions
    try:
        conn.execute("ALTER TABLE cache_edna_items ADD COLUMN guideline TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    return conn
