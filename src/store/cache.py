"""SQLite cache CRUD operations."""

import sqlite3
import time

from src.adapters.models import EdnaItem
from src.adapters.submission_models import SubmissionItem
from src.adapters.velma_models import VelmaItem


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


def delete_edna_items_not_in(
    conn: sqlite3.Connection,
    keep_ids: set[str],
    board_groups: list[str] | None = None,
) -> int:
    """Delete cached edna items whose monday_id is not in keep_ids.

    If board_groups is given, only rows in those groups are considered — used by
    quick sync (scope = just "Active") so items in other groups aren't wiped.
    """
    if board_groups:
        placeholders = ",".join("?" * len(board_groups))
        rows = conn.execute(
            f"SELECT monday_id FROM cache_edna_items WHERE board_group IN ({placeholders})",  # noqa: S608
            tuple(board_groups),
        ).fetchall()
    else:
        rows = conn.execute("SELECT monday_id FROM cache_edna_items").fetchall()
    to_delete = [r["monday_id"] for r in rows if r["monday_id"] not in keep_ids]
    if to_delete:
        conn.executemany(
            "DELETE FROM cache_edna_items WHERE monday_id = ?",
            [(mid,) for mid in to_delete],
        )
        conn.commit()
    return len(to_delete)


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


# -- Velma items -------------------------------------------------------------


def bulk_upsert_velma_items(conn: sqlite3.Connection, items: list[VelmaItem]) -> None:
    """Insert or replace Velma items into the cache, preserving user-set guideline."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = {
        row["monday_id"]: row["guideline"]
        for row in conn.execute("SELECT monday_id, guideline FROM cache_velma_items").fetchall()
    }
    conn.executemany(
        """INSERT OR REPLACE INTO cache_velma_items
           (monday_id, name, board_group, velma_status,
            writer, award, category,
            interview_transcript_url, interview_transcript_text,
            processed_interview_url, processed_interview_text,
            submission_link,
            prev_submission_1_url, prev_submission_1_text,
            prev_submission_2_url, prev_submission_2_text,
            supporting_doc_1_url, supporting_doc_1_text,
            supporting_doc_2_url, supporting_doc_2_text,
            supporting_doc_3_url, supporting_doc_3_text,
            supporting_doc_4_url, supporting_doc_4_text,
            mapped_submission_url, mapped_submission_text,
            velma_draft_url, velma_draft_text,
            tracker_submission_id,
            guideline, monday_updated_at, synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                item.monday_id, item.name, item.board_group, item.velma_status,
                item.writer, item.award, item.category,
                item.interview_transcript_url, item.interview_transcript_text,
                item.processed_interview_url, item.processed_interview_text,
                item.submission_link,
                item.prev_submission_1_url, item.prev_submission_1_text,
                item.prev_submission_2_url, item.prev_submission_2_text,
                item.supporting_doc_1_url, item.supporting_doc_1_text,
                item.supporting_doc_2_url, item.supporting_doc_2_text,
                item.supporting_doc_3_url, item.supporting_doc_3_text,
                item.supporting_doc_4_url, item.supporting_doc_4_text,
                item.mapped_submission_url, item.mapped_submission_text,
                item.velma_draft_url, item.velma_draft_text,
                item.tracker_submission_id,
                existing.get(item.monday_id),
                item.monday_updated_at, now,
            )
            for item in items
        ],
    )
    conn.commit()


def get_all_velma_items(conn: sqlite3.Connection) -> list[dict]:
    """Return all cached Velma items as dicts."""
    rows = conn.execute("SELECT * FROM cache_velma_items").fetchall()
    return [dict(row) for row in rows]


def update_velma_guideline(conn: sqlite3.Connection, monday_id: str, guideline: str) -> None:
    """Update the guideline code for a single Velma item."""
    conn.execute(
        "UPDATE cache_velma_items SET guideline = ? WHERE monday_id = ?",
        (guideline, monday_id),
    )
    conn.commit()


def apply_default_velma_guidelines(conn: sqlite3.Connection) -> None:
    """Set default guideline for Velma items that don't have one yet."""
    rows = conn.execute(
        "SELECT monday_id, name, award FROM cache_velma_items WHERE guideline IS NULL"
    ).fetchall()
    if not rows:
        return
    updates: list[tuple[str, str]] = []
    for row in rows:
        award = row["award"] or row["name"] or ""
        guideline = "m" if any(award.startswith(p) for p in MORTGAGE_PREFIXES) else "s"
        updates.append((guideline, row["monday_id"]))
    conn.executemany(
        "UPDATE cache_velma_items SET guideline = ? WHERE monday_id = ?", updates
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
            submission_link_url, submission_link_text, result_status,
            submitted_date, created_at, monday_updated_at, synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                item.submission_link_url, item.submission_link_text, item.result_status,
                item.submitted_date, item.created_at, item.monday_updated_at, now,
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
