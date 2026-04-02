"""SQLite cache CRUD operations."""

import sqlite3
import time

from src.adapters.models import EdnaItem
from src.adapters.submission_models import SubmissionItem


def bulk_upsert_edna_items(conn: sqlite3.Connection, items: list[EdnaItem]) -> None:
    """Insert or replace Edna items into the cache, preserving user-set guideline."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Preserve existing guideline values set by the user
    existing = {
        row["monday_id"]: row["guideline"]
        for row in conn.execute("SELECT monday_id, guideline FROM cache_edna_items").fetchall()
    }
    conn.executemany(
        """INSERT OR REPLACE INTO cache_edna_items
           (monday_id, name, board_group, award, category, edna_status,
            triage_score, writer, reviewer, edna_review_link, edna_review_link_text,
            monday_updated_at, synced_at, guideline)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                item.monday_id, item.name, item.board_group, item.award,
                item.category, item.edna_status, item.triage_score,
                item.writer, item.reviewer, item.edna_review_link,
                item.edna_review_link_text, item.monday_updated_at, now,
                existing.get(item.monday_id),  # preserve existing guideline
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


def update_guideline(conn: sqlite3.Connection, monday_id: str, guideline: str) -> None:
    """Update the guideline code for a single item."""
    conn.execute(
        "UPDATE cache_edna_items SET guideline = ? WHERE monday_id = ?",
        (guideline, monday_id),
    )
    conn.commit()


# -- Submission items --------------------------------------------------------


def bulk_upsert_submission_items(conn: sqlite3.Connection, items: list[SubmissionItem]) -> None:
    """Insert or replace submission items into the cache."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.executemany(
        """INSERT OR REPLACE INTO cache_submission_items
           (monday_id, name, board_group, delivery_status, sales_status,
            writer, reviewer, close_date, target_finish_date, extension_date,
            category, company, award, escalate,
            date_alert, writer_alert, metrics_alert, asset_alert,
            contingency_days, spare_days_est,
            days_since, metrics_status, asset_status, asset_days_since,
            writer_due, reviewer_due,
            monday_updated_at, synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                item.monday_id, item.name, item.board_group,
                item.delivery_status, item.sales_status,
                item.writer, item.reviewer,
                item.close_date, item.target_finish_date, item.extension_date,
                item.category, item.company, item.award,
                1 if item.escalate else 0,
                item.date_alert, item.writer_alert, item.metrics_alert, item.asset_alert,
                item.contingency_days, item.spare_days_est,
                item.days_since, item.metrics_status, item.asset_status, item.asset_days_since,
                item.writer_due, item.reviewer_due,
                item.monday_updated_at, now,
            )
            for item in items
        ],
    )
    conn.commit()


def get_all_submission_items(conn: sqlite3.Connection) -> list[dict]:
    """Return all cached submission items as dicts."""
    rows = conn.execute("SELECT * FROM cache_submission_items").fetchall()
    return [dict(row) for row in rows]


def get_submission_alerts(conn: sqlite3.Connection) -> list[dict]:
    """Return submission items that have at least one non-empty alert."""
    rows = conn.execute(
        """SELECT * FROM cache_submission_items
           WHERE (date_alert IS NOT NULL AND date_alert != '')
              OR (writer_alert IS NOT NULL AND writer_alert != '')
              OR (metrics_alert IS NOT NULL AND metrics_alert != '')
              OR (asset_alert IS NOT NULL AND asset_alert != '')
              OR escalate = 1"""
    ).fetchall()
    return [dict(row) for row in rows]


# -- AI insights -------------------------------------------------------------


def upsert_ai_insights(conn: sqlite3.Connection, insights: list[dict]) -> None:
    """Store AI-generated insights for submission items."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.executemany(
        """INSERT OR REPLACE INTO cache_ai_insights
           (monday_id, recommendation, reasoning_chain, confidence, severity,
            session_id, analysed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                i["monday_id"], i.get("recommendation"), i.get("reasoning_chain"),
                i.get("confidence"), i.get("severity"),
                i.get("session_id"), now,
            )
            for i in insights
        ],
    )
    conn.commit()


def get_ai_insights(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return AI insights keyed by monday_id."""
    rows = conn.execute("SELECT * FROM cache_ai_insights").fetchall()
    return {row["monday_id"]: dict(row) for row in rows}


def get_ai_session_id(conn: sqlite3.Connection) -> str | None:
    """Return the most recent AI analysis session ID."""
    return get_sync_meta(conn, "ai_session_id")


MORTGAGE_PREFIXES = ("MFAA", "MPA", "FBAA", "BBA", "BIA", "ABA-Broking")


def apply_default_guidelines(conn: sqlite3.Connection) -> None:
    """Set default guideline for items that don't have one yet."""
    rows = conn.execute(
        "SELECT monday_id, award FROM cache_edna_items WHERE guideline IS NULL"
    ).fetchall()
    if not rows:
        return
    updates: list[tuple[str, str]] = []
    for row in rows:
        award = row["award"] or ""
        guideline = "m" if any(award.startswith(p) for p in MORTGAGE_PREFIXES) else "s"
        updates.append((guideline, row["monday_id"]))
    conn.executemany(
        "UPDATE cache_edna_items SET guideline = ? WHERE monday_id = ?", updates
    )
    conn.commit()
