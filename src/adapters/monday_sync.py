"""Monday.com sync worker — pulls Edna tracker items into the local SQLite cache."""

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from config.settings import get_monday_token, load_settings
from src.adapters.monday import MondayAdapter
from src.store.cache import (
    bulk_upsert_edna_items,
    bulk_upsert_submission_items,
    get_cached_updated_at,
    set_sync_meta,
)
from src.store.db import get_db


class SyncResult(BaseModel):
    edna_synced: int = 0
    edna_unchanged: int = 0
    submissions_synced: int = 0
    submissions_unchanged: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = []


def _warm_upsert(
    conn: sqlite3.Connection,
    items: list[Any],
    table: str,
    upsert_fn: Callable,
    label: str,
) -> tuple[int, int]:
    """Compare monday_updated_at with cached values and only upsert changed items."""
    cached = get_cached_updated_at(conn, table)
    changed = [
        item for item in items
        if item.monday_updated_at != cached.get(item.monday_id)
    ]
    unchanged = len(items) - len(changed)
    if changed:
        upsert_fn(conn, changed)
    print(f"  {len(changed)} updated, {unchanged} unchanged")
    return len(changed), unchanged


def sync_submissions(db_path: str | Path, settings: dict | None = None) -> SyncResult:
    """Sync only the Submission Tracker board into the local cache."""
    start = time.monotonic()
    errors: list[str] = []

    if settings is None:
        settings = load_settings()

    token = get_monday_token()
    adapter = MondayAdapter(settings=settings, api_token=token)
    conn = get_db(db_path)

    submissions_synced = 0
    submissions_unchanged = 0

    print("Syncing Submission tracker...")
    try:
        sub_items = adapter.get_submission_items()
        submissions_synced, submissions_unchanged = _warm_upsert(
            conn, sub_items, "cache_submission_items",
            bulk_upsert_submission_items, "submission items",
        )
    except Exception as exc:
        msg = f"Submission tracker sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    try:
        set_sync_meta(conn, "submissions_last_sync", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception as exc:
        errors.append(f"Failed to update cache_meta: {exc}")

    conn.close()
    duration = time.monotonic() - start
    print(f"Done: {submissions_synced} submissions in {duration:.1f}s")

    return SyncResult(
        submissions_synced=submissions_synced,
        submissions_unchanged=submissions_unchanged,
        duration_seconds=round(duration, 2),
        errors=errors,
    )


def sync_monday(db_path: str | Path, settings: dict | None = None) -> SyncResult:
    """Run a sync from Monday.com into the local SQLite cache."""
    start = time.monotonic()
    errors: list[str] = []

    if settings is None:
        settings = load_settings()

    token = get_monday_token()
    adapter = MondayAdapter(settings=settings, api_token=token)
    conn = get_db(db_path)

    edna_synced = 0
    edna_unchanged = 0
    submissions_synced = 0
    submissions_unchanged = 0

    print("Syncing Edna tracker...")
    try:
        items = adapter.get_edna_items()
        edna_synced, edna_unchanged = _warm_upsert(
            conn, items, "cache_edna_items", bulk_upsert_edna_items, "edna items",
        )
    except Exception as exc:
        msg = f"Edna tracker sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    print("Syncing Submission tracker...")
    try:
        sub_items = adapter.get_submission_items()
        submissions_synced, submissions_unchanged = _warm_upsert(
            conn, sub_items, "cache_submission_items",
            bulk_upsert_submission_items, "submission items",
        )
    except Exception as exc:
        msg = f"Submission tracker sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    try:
        set_sync_meta(conn, "last_sync", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception as exc:
        msg = f"Failed to update cache_meta: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    conn.close()

    duration = time.monotonic() - start
    print(f"Done: {edna_synced} edna, {submissions_synced} submissions in {duration:.1f}s")

    return SyncResult(
        edna_synced=edna_synced,
        edna_unchanged=edna_unchanged,
        submissions_synced=submissions_synced,
        submissions_unchanged=submissions_unchanged,
        duration_seconds=round(duration, 2),
        errors=errors,
    )
