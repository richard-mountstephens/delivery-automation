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

CREATE TABLE IF NOT EXISTS cache_submission_items (
    monday_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    board_group TEXT,
    delivery_status TEXT,
    sales_status TEXT,
    writer TEXT,
    reviewer TEXT,
    close_date TEXT,
    target_finish_date TEXT,
    extension_date TEXT,
    category TEXT,
    company TEXT,
    award TEXT,
    escalate INTEGER DEFAULT 0,
    date_alert TEXT,
    writer_alert TEXT,
    metrics_alert TEXT,
    asset_alert TEXT,
    contingency_days TEXT,
    spare_days_est TEXT,
    days_since TEXT,
    metrics_status TEXT,
    asset_status TEXT,
    asset_days_since TEXT,
    writer_due TEXT,
    reviewer_due TEXT,
    monday_updated_at TEXT,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_ai_insights (
    monday_id TEXT PRIMARY KEY,
    recommendation TEXT,
    reasoning_chain TEXT,
    confidence TEXT,
    severity TEXT,
    session_id TEXT,
    analysed_at TEXT NOT NULL
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
    for table, col, col_type in [
        ("cache_edna_items", "guideline", "TEXT"),
        ("cache_submission_items", "days_since", "TEXT"),
        ("cache_submission_items", "metrics_status", "TEXT"),
        ("cache_submission_items", "asset_status", "TEXT"),
        ("cache_submission_items", "asset_days_since", "TEXT"),
        ("cache_submission_items", "writer_due", "TEXT"),
        ("cache_submission_items", "reviewer_due", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    return conn
