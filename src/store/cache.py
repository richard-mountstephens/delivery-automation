"""SQLite cache CRUD operations."""

import sqlite3
import time

from src.adapters.models import EdnaItem


def bulk_upsert_edna_items(conn: sqlite3.Connection, items: list[EdnaItem]) -> None:
    """Insert or replace Edna items into the cache."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.executemany(
        """INSERT OR REPLACE INTO cache_edna_items
           (monday_id, name, board_group, award, category, edna_status,
            triage_score, writer, reviewer, edna_review_link, edna_review_link_text,
            monday_updated_at, synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                item.monday_id, item.name, item.board_group, item.award,
                item.category, item.edna_status, item.triage_score,
                item.writer, item.reviewer, item.edna_review_link,
                item.edna_review_link_text, item.monday_updated_at, now,
            )
            for item in items
        ],
    )
    conn.commit()


def get_all_edna_items(conn: sqlite3.Connection) -> list[dict]:
    """Return all cached Edna items as dicts."""
    rows = conn.execute("SELECT * FROM cache_edna_items").fetchall()
    return [dict(row) for row in rows]


def get_cached_updated_at(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Return {monday_id: monday_updated_at} for warm-upsert comparison."""
    rows = conn.execute(
        f"SELECT monday_id, monday_updated_at FROM {table}"  # noqa: S608
    ).fetchall()
    return {row["monday_id"]: row["monday_updated_at"] for row in rows}


def get_sync_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a value from cache_meta."""
    row = conn.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_sync_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a value in cache_meta."""
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()
