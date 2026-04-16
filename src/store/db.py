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

CREATE TABLE IF NOT EXISTS cache_velma_items (
    monday_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    board_group TEXT,
    velma_status TEXT,
    writer TEXT,
    award TEXT,
    category TEXT,
    interview_transcript_url TEXT,
    interview_transcript_text TEXT,
    processed_interview_url TEXT,
    processed_interview_text TEXT,
    submission_link TEXT,
    prev_submission_1_url TEXT,
    prev_submission_1_text TEXT,
    prev_submission_2_url TEXT,
    prev_submission_2_text TEXT,
    supporting_doc_1_url TEXT,
    supporting_doc_1_text TEXT,
    supporting_doc_2_url TEXT,
    supporting_doc_2_text TEXT,
    supporting_doc_3_url TEXT,
    supporting_doc_3_text TEXT,
    supporting_doc_4_url TEXT,
    supporting_doc_4_text TEXT,
    mapped_submission_url TEXT,
    mapped_submission_text TEXT,
    velma_draft_url TEXT,
    velma_draft_text TEXT,
    tracker_submission_id TEXT,
    guideline TEXT,
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
        ("cache_velma_items", "writer", "TEXT"),
        ("cache_velma_items", "award", "TEXT"),
        ("cache_velma_items", "category", "TEXT"),
        ("cache_velma_items", "tracker_submission_id", "TEXT"),
        ("cache_submission_items", "submission_link_url", "TEXT"),
        ("cache_submission_items", "submission_link_text", "TEXT"),
        ("cache_submission_items", "result_status", "TEXT"),
        ("cache_submission_items", "submitted_date", "TEXT"),
        ("cache_submission_items", "created_at", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    return conn
