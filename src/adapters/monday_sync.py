"""Monday.com sync worker — pulls tracker items into the local SQLite cache."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from config.settings import get_monday_token, load_settings
from src.adapters.monday import MondayAdapter
from src.store.cache import (
    bulk_upsert_edna_items,
    bulk_upsert_submission_items,
    bulk_upsert_velma_items,
    get_cached_updated_at,
    set_sync_meta,
)
from src.store.db import get_db


class SyncResult(BaseModel):
    edna_synced: int = 0
    edna_unchanged: int = 0
    submissions_synced: int = 0
    submissions_unchanged: int = 0
    velma_synced: int = 0
    velma_unchanged: int = 0
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


def _fetch_board(adapter: MondayAdapter, board_id: int, label: str,
                 group_ids: list[str] | None = None) -> tuple[str, list[dict], float]:
    """Fetch a single board's items, returning (label, items, elapsed_seconds)."""
    t0 = time.monotonic()
    items = adapter._fetch_board_items(board_id, label=label, group_ids=group_ids)
    return label, items, time.monotonic() - t0


def _parallel_fetch(adapter: MondayAdapter, tasks: list[tuple]) -> tuple[dict[str, list[dict]], dict[str, float], list[str]]:
    """Fetch multiple boards in parallel.

    Args:
        tasks: List of (key, board_id, label, group_ids) tuples.

    Returns:
        (raw_items_by_key, timing_by_key, errors)
    """
    raw: dict[str, list[dict]] = {}
    fetch_times: dict[str, float] = {}
    errors: list[str] = []

    print(f"Fetching {len(tasks)} board(s) in parallel...")
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {}
        for task in tasks:
            key, board_id, label = task[0], task[1], task[2]
            group_ids = task[3] if len(task) > 3 else None
            futures[pool.submit(_fetch_board, adapter, board_id, label, group_ids)] = key

        for future in as_completed(futures):
            key = futures[future]
            try:
                _, items, elapsed = future.result()
                raw[key] = items
                fetch_times[key] = elapsed
            except Exception as exc:
                msg = f"Fetch {key} failed: {exc}"
                print(f"  ERROR: {msg}")
                errors.append(msg)
                raw[key] = []
                fetch_times[key] = 0.0

    timing_parts = ", ".join(f"{k}: {t:.1f}s" for k, t in sorted(fetch_times.items()))
    total = max(fetch_times.values()) if fetch_times else 0
    print(f"All boards fetched in {total:.1f}s ({timing_parts})")
    return raw, fetch_times, errors


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
        board_ids = adapter.boards["submission_tracker"]
        if not isinstance(board_ids, list):
            board_ids = [board_ids]
        raw_items = []
        for board_id in board_ids:
            raw_items.extend(adapter._fetch_board_items(board_id, label=f"submission tracker {board_id}"))
        sub_items = adapter.parse_submission_items(raw_items)
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


def sync_monday_quick(db_path: str | Path, settings: dict | None = None) -> SyncResult:
    """Fast sync: only fetch Active group from Edna tracker. Skip submissions."""
    start = time.monotonic()
    errors: list[str] = []

    if settings is None:
        settings = load_settings()

    token = get_monday_token()
    adapter = MondayAdapter(settings=settings, api_token=token)
    conn = get_db(db_path)

    edna_synced = 0
    edna_unchanged = 0

    print("Quick sync: Edna tracker (Active only)...")
    try:
        items = adapter.get_edna_items(groups=["Active"])
        edna_synced, edna_unchanged = _warm_upsert(
            conn, items, "cache_edna_items", bulk_upsert_edna_items, "edna items",
        )
    except Exception as exc:
        msg = f"Edna tracker quick sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    try:
        set_sync_meta(conn, "last_sync", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception as exc:
        errors.append(f"Failed to update cache_meta: {exc}")

    conn.close()
    duration = time.monotonic() - start
    print(f"Quick sync done: {edna_synced} edna in {duration:.1f}s")

    return SyncResult(
        edna_synced=edna_synced,
        edna_unchanged=edna_unchanged,
        submissions_synced=0,
        submissions_unchanged=0,
        duration_seconds=round(duration, 2),
        errors=errors,
    )


def sync_monday(db_path: str | Path, settings: dict | None = None) -> SyncResult:
    """Full sync: fetch Edna + Submission trackers in parallel."""
    start = time.monotonic()
    errors: list[str] = []

    if settings is None:
        settings = load_settings()

    token = get_monday_token()
    adapter = MondayAdapter(settings=settings, api_token=token)
    conn = get_db(db_path)

    boards = adapter.boards

    # Fetch edna + submission boards in parallel
    sub_board_ids = boards["submission_tracker"]
    if not isinstance(sub_board_ids, list):
        sub_board_ids = [sub_board_ids]
    fetch_tasks = [("edna", boards["edna_tracker"], "edna tracker")]
    for i, bid in enumerate(sub_board_ids):
        fetch_tasks.append((f"submissions_{i}", bid, f"submission tracker {bid}"))
    raw, _, fetch_errors = _parallel_fetch(adapter, fetch_tasks)
    # Merge submission results from all boards
    raw["submissions"] = []
    for i in range(len(sub_board_ids)):
        raw["submissions"].extend(raw.pop(f"submissions_{i}", []))
    errors.extend(fetch_errors)

    # Process edna
    edna_synced = 0
    edna_unchanged = 0
    print("Processing edna items...")
    try:
        items = adapter.parse_edna_items(raw.get("edna", []))
        edna_synced, edna_unchanged = _warm_upsert(
            conn, items, "cache_edna_items", bulk_upsert_edna_items, "edna items",
        )
    except Exception as exc:
        msg = f"Edna tracker sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    # Process submissions
    submissions_synced = 0
    submissions_unchanged = 0
    print("Processing submission items...")
    try:
        sub_items = adapter.parse_submission_items(raw.get("submissions", []))
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


def sync_velma_quick(db_path: str | Path, settings: dict | None = None) -> SyncResult:
    """Fast sync: only fetch Active group from Velma Tracker board."""
    start = time.monotonic()
    errors: list[str] = []

    if settings is None:
        settings = load_settings()

    token = get_monday_token()
    adapter = MondayAdapter(settings=settings, api_token=token)
    conn = get_db(db_path)

    velma_synced = 0
    velma_unchanged = 0

    print("Quick sync: Velma tracker (Active only)...")
    try:
        items = adapter.get_velma_items(groups=["Active"])
        velma_synced, velma_unchanged = _warm_upsert(
            conn, items, "cache_velma_items", bulk_upsert_velma_items, "velma items",
        )
    except Exception as exc:
        msg = f"Velma tracker quick sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    try:
        set_sync_meta(conn, "velma_last_sync", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception as exc:
        errors.append(f"Failed to update cache_meta: {exc}")

    conn.close()
    duration = time.monotonic() - start
    print(f"Quick sync done: {velma_synced} velma in {duration:.1f}s")

    return SyncResult(
        velma_synced=velma_synced,
        velma_unchanged=velma_unchanged,
        duration_seconds=round(duration, 2),
        errors=errors,
    )


def sync_velma(db_path: str | Path, settings: dict | None = None) -> SyncResult:
    """Full sync: fetch Velma + Submission trackers in parallel."""
    start = time.monotonic()
    errors: list[str] = []

    if settings is None:
        settings = load_settings()

    token = get_monday_token()
    adapter = MondayAdapter(settings=settings, api_token=token)
    conn = get_db(db_path)

    boards = adapter.boards

    # Fetch velma + submission boards in parallel
    sub_board_ids = boards["submission_tracker"]
    if not isinstance(sub_board_ids, list):
        sub_board_ids = [sub_board_ids]
    fetch_tasks = [("velma", boards["velma_tracker"], "velma tracker")]
    for i, bid in enumerate(sub_board_ids):
        fetch_tasks.append((f"submissions_{i}", bid, f"submission tracker {bid}"))
    raw, _, fetch_errors = _parallel_fetch(adapter, fetch_tasks)
    # Merge submission results from all boards
    raw["submissions"] = []
    for i in range(len(sub_board_ids)):
        raw["submissions"].extend(raw.pop(f"submissions_{i}", []))
    errors.extend(fetch_errors)

    # Process submissions first (velma links reference them)
    submissions_synced = 0
    submissions_unchanged = 0
    print("Processing submission items...")
    try:
        sub_items = adapter.parse_submission_items(raw.get("submissions", []))
        submissions_synced, submissions_unchanged = _warm_upsert(
            conn, sub_items, "cache_submission_items",
            bulk_upsert_submission_items, "submission items",
        )
    except Exception as exc:
        msg = f"Submission tracker sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    # Process velma (force update all, matching original behavior)
    velma_synced = 0
    velma_unchanged = 0
    print("Processing velma items (full — force update all)...")
    try:
        items = adapter.parse_velma_items(raw.get("velma", []))
        if items:
            bulk_upsert_velma_items(conn, items)
        velma_synced = len(items)
        velma_unchanged = 0
    except Exception as exc:
        msg = f"Velma tracker sync failed: {exc}"
        print(f"  ERROR: {msg}")
        errors.append(msg)

    try:
        set_sync_meta(conn, "velma_last_sync", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception as exc:
        errors.append(f"Failed to update cache_meta: {exc}")

    conn.close()
    duration = time.monotonic() - start
    print(f"Done: {velma_synced} velma, {submissions_synced} submissions in {duration:.1f}s")

    return SyncResult(
        velma_synced=velma_synced,
        velma_unchanged=velma_unchanged,
        submissions_synced=submissions_synced,
        submissions_unchanged=submissions_unchanged,
        duration_seconds=round(duration, 2),
        errors=errors,
    )
