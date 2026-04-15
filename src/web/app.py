"""FastAPI web application — Delivery Hub dashboard."""

import json
import logging
import os
import tempfile
from collections import defaultdict

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from config.settings import load_settings
from src.adapters.claude_cli import analyse, chat_sync, reset_chat
from src.adapters.monday_sync import sync_monday, sync_submissions
from src.store.cache import (
    apply_default_guidelines,
    apply_default_velma_guidelines,
    get_ai_insights,
    get_all_edna_items,
    get_all_submission_items,
    get_all_velma_items,
    get_submission_alerts,
    get_sync_meta,
    set_sync_meta,
    update_guideline,
    update_velma_guideline,
    upsert_ai_insights,
)
from src.store.db import get_db

log = logging.getLogger(__name__)

app = FastAPI(title="Award Delivery Hub")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

settings = load_settings()
DB_PATH = settings["database"]["path"]

# Signal flag: set when a velma process completes and the frontend should refresh
_velma_refresh_pending = False

# Group display order
GROUP_ORDER = ["Active", "Done", "2025"]

# Velma writing state machine
VELMA_STATUS_ACTIONS = {
    "Transcript Ready": ["process", "process-interview-write"],
    "Ready for Mapping": ["map", "map-write"],
    "Interview Processed": ["write"],
    "Mapped": ["write"],
    "Mapped and Processed": ["write"],
}

VELMA_ACTION_FLAGS = {
    "process": ["guidelines", "batch", "interview_notes", "supporting_docs", "custom"],
    "map": ["guidelines", "batch", "custom"],
    "write": ["guidelines", "batch", "interview_notes", "pre_interview", "supporting_docs", "custom"],
    "process-interview-write": ["guidelines", "batch", "interview_notes", "pre_interview", "supporting_docs", "custom"],
    "map-write": ["guidelines", "batch", "interview_notes", "pre_interview", "supporting_docs", "custom"],
}

VELMA_ACTIONS = set(VELMA_ACTION_FLAGS.keys())


def _grouped_edna_items() -> tuple[dict[str, list[dict]], str | None]:
    """Load edna items from cache, grouped by board_group."""
    conn = get_db(DB_PATH)
    apply_default_guidelines(conn)
    items = get_all_edna_items(conn)
    last_sync = get_sync_meta(conn, "last_sync")
    conn.close()

    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        group_name = item.get("board_group") or "Ungrouped"
        groups[group_name].append(item)

    # Sort into defined order, then any extras
    ordered: dict[str, list[dict]] = {}
    for name in GROUP_ORDER:
        if name in groups:
            ordered[name] = groups.pop(name)
    for name in sorted(groups.keys()):
        ordered[name] = groups[name]

    return ordered, last_sync


def _grouped_velma_items() -> tuple[dict[str, list[dict]], str | None]:
    """Load velma items from cache, grouped by board_group."""
    conn = get_db(DB_PATH)
    apply_default_velma_guidelines(conn)
    items = get_all_velma_items(conn)
    last_sync = get_sync_meta(conn, "velma_last_sync")
    conn.close()

    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        group_name = item.get("board_group") or "Ungrouped"
        groups[group_name].append(item)

    ordered: dict[str, list[dict]] = {}
    for name in GROUP_ORDER:
        if name in groups:
            ordered[name] = groups.pop(name)
    for name in sorted(groups.keys()):
        ordered[name] = groups[name]

    return ordered, last_sync


def _judging_stats(groups: dict[str, list[dict]]) -> dict:
    """Compute summary table data from reviewed Edna items."""
    reviewed = [
        item for items in groups.values() for item in items
        if item.get("edna_status") == "Edna Reviewed"
        and item.get("triage_score") is not None
    ]
    if not reviewed:
        return {"total_reviewed": 0, "by_award": [], "by_writer": []}

    # By award: avg score, count, top writer
    scores_by_award: dict[str, list[float]] = defaultdict(list)
    award_writers: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in reviewed:
        award = item.get("award") or "Unknown"
        writer = item.get("writer") or "Unknown"
        scores_by_award[award].append(item["triage_score"])
        award_writers[award][writer] += 1

    by_award = []
    for award, scores in scores_by_award.items():
        top_writer, top_count = max(award_writers[award].items(), key=lambda x: x[1])
        by_award.append({
            "award": award,
            "avg_score": round(sum(scores) / len(scores), 1),
            "count": len(scores),
            "top_writer": top_writer,
            "top_writer_count": top_count,
        })
    by_award.sort(key=lambda x: x["count"], reverse=True)

    # By writer: avg score, count, top award
    scores_by_writer: dict[str, list[float]] = defaultdict(list)
    writer_awards: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in reviewed:
        writer = item.get("writer") or "Unknown"
        award = item.get("award") or "Unknown"
        scores_by_writer[writer].append(item["triage_score"])
        writer_awards[writer][award] += 1

    by_writer = []
    for writer, scores in scores_by_writer.items():
        top_award, top_count = max(writer_awards[writer].items(), key=lambda x: x[1])
        by_writer.append({
            "writer": writer,
            "avg_score": round(sum(scores) / len(scores), 1),
            "count": len(scores),
            "top_award": top_award,
            "top_award_count": top_count,
        })
    by_writer.sort(key=lambda x: x["count"], reverse=True)

    return {"total_reviewed": len(reviewed), "by_award": by_award, "by_writer": by_writer}


FINISHED_STATUSES = {"Submitted", "Next Best Offer", "DNP But Billed"}
EXCLUDED_STATUSES = {"Prospect", "Did Not Proceed"}
ACTIVE_GROUPS = {"Active", "Future"}

# Australian national public holidays for business day calculations
from datetime import date as _date, timedelta as _td

AU_PUBLIC_HOLIDAYS = {
    _date(2025, 1, 1), _date(2025, 1, 27),
    _date(2025, 4, 18), _date(2025, 4, 19), _date(2025, 4, 21),
    _date(2025, 4, 25), _date(2025, 6, 9),
    _date(2025, 12, 25), _date(2025, 12, 26),
    _date(2026, 1, 1), _date(2026, 1, 26),
    _date(2026, 4, 3), _date(2026, 4, 4), _date(2026, 4, 6),
    _date(2026, 4, 25), _date(2026, 4, 27), _date(2026, 6, 8),
    _date(2026, 12, 25), _date(2026, 12, 26), _date(2026, 12, 28),
    _date(2027, 1, 1), _date(2027, 1, 26),
    _date(2027, 3, 26), _date(2027, 3, 27), _date(2027, 3, 29),
    _date(2027, 4, 26), _date(2027, 6, 14),
    _date(2027, 12, 25), _date(2027, 12, 27),
}


def business_days_until(date_str: str | None) -> int | None:
    """Count business days from today to date_str (excl weekends + AU holidays)."""
    if not date_str:
        return None
    try:
        target = _date.fromisoformat(date_str)
    except ValueError:
        return None
    today = _date.today()
    if target <= today:
        return 0
    count = 0
    d = today
    while d < target:
        d += _td(days=1)
        if d.weekday() < 5 and d not in AU_PUBLIC_HOLIDAYS:
            count += 1
    return count

# Monday delivery status lifecycle order
STATUS_ORDER = [
    "Awaiting To Open",
    "Delivery Setup",
    "Confirm Hours",
    "Writer Briefed",
    "With Writer",
    "With Velma",
    "With Reviewer",
    "Ready & Hold",
    "REVIEWED-NEW Link",
    "With Client",
    "2nd Writer Review",
    "2nd Client Review",
    "With Reviewer - FINAL",
    "With Client for Final Approval",
    "With C to Upload",
    "Upload Pending",
    "Approved to Submit",
    "With Client - ESCALATE",
    "Pending Aggregator Approval",
    "Submitted",
    "Next Best Offer",
    "DNP But Billed",
    "Submission Issue",
]

def _status_sort_key(status: str) -> int:
    """Return sort index for a delivery status, matching Monday lifecycle order."""
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return len(STATUS_ORDER)


def _manager_data() -> dict:
    """Load submission data for the Manager dashboard."""
    conn = get_db(DB_PATH)
    all_items = get_all_submission_items(conn)
    raw_alerts = get_submission_alerts(conn)
    insights = get_ai_insights(conn)
    last_sync = get_sync_meta(conn, "submissions_last_sync")
    ai_session_id = get_sync_meta(conn, "ai_session_id")
    conn.close()

    # Filter to active items (active group, not finished)
    active = [
        i for i in all_items
        if i.get("board_group") in ACTIVE_GROUPS
        and i.get("delivery_status") not in FINISHED_STATUSES
    ]

    # Submitted items for historical charts (any group, status == Submitted)
    submitted = [
        i for i in all_items
        if i.get("delivery_status") == "Submitted"
        and i.get("sales_status") not in EXCLUDED_STATUSES
    ]

    # Filter alerts to active group only
    alerts = [
        a for a in raw_alerts
        if a.get("board_group") in ACTIVE_GROUPS
        and a.get("delivery_status") not in FINISHED_STATUSES
    ]

    # Status counts
    status_counts: dict[str, int] = defaultdict(int)
    for item in active:
        status = item.get("delivery_status") or "Unknown"
        status_counts[status] += 1

    # Writer workload
    writer_counts: dict[str, dict] = defaultdict(lambda: {"active": 0, "alerts": 0})
    for item in active:
        writer = item.get("writer") or "Unassigned"
        writer_counts[writer]["active"] += 1
    for alert in alerts:
        writer = alert.get("writer") or "Unassigned"
        if alert.get("delivery_status") not in FINISHED_STATUSES:
            writer_counts[writer]["alerts"] += 1

    # Collect distinct alert values for filters
    alert_values: set[str] = set()
    for a in alerts:
        for col in ("date_alert", "writer_alert", "metrics_alert", "asset_alert"):
            v = a.get(col)
            if v:
                alert_values.add(v)

    # Enrich alerts with AI insights
    for alert in alerts:
        mid = alert["monday_id"]
        if mid in insights:
            alert["ai"] = insights[mid]

    # Sort status counts by lifecycle order
    sorted_status_counts = dict(sorted(status_counts.items(), key=lambda x: _status_sort_key(x[0])))

    # Chart data: stacked bar — status x award
    award_set: set[str] = set()
    chart_data: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in active:
        status = item.get("delivery_status") or "Unknown"
        award = item.get("award") or "Unknown"
        chart_data[status][award] += 1
        award_set.add(award)

    # Sort statuses by lifecycle, awards by frequency
    chart_statuses = sorted(chart_data.keys(), key=_status_sort_key)
    award_totals = defaultdict(int)
    for status_counts_inner in chart_data.values():
        for award, count in status_counts_inner.items():
            award_totals[award] += count
    chart_awards = sorted(award_set, key=lambda a: -award_totals[a])

    chart_datasets = []
    # Distinct colors for awards
    award_colors = [
        "#ffcb00", "#007eb5", "#00c875", "#bb3354", "#579bfc",
        "#ff6d3b", "#7e3b8a", "#fdab3d", "#9cd326", "#66ccff",
        "#784bd1", "#4eccc6", "#ff5ac4", "#a9bee8", "#037f4c",
        "#df2f4a", "#cab641", "#bda8f9", "#ff007f", "#175a63",
    ]
    for i, award in enumerate(chart_awards):
        color = award_colors[i % len(award_colors)]
        data_points = [chart_data[s].get(award, 0) for s in chart_statuses]
        chart_datasets.append({
            "label": award,
            "data": data_points,
            "backgroundColor": color,
        })

    return {
        "alert_values": sorted(alert_values),
        "alerts": alerts,
        "active_count": len(active),
        "status_counts": sorted_status_counts,
        "chart_statuses": chart_statuses,
        "chart_datasets": chart_datasets,
        "writer_counts": dict(sorted(writer_counts.items())),
        "all_items": active,
        "chart_items_json": json.dumps([
            {
                "name": i.get("name") or "",
                "delivery_status": i.get("delivery_status") or "Unknown",
                "award": i.get("award") or "Unknown",
                "writer": i.get("writer") or "Unassigned",
                "company": i.get("company") or "Unknown",
                "category": i.get("category") or "",
                "close_date": i.get("close_date"),
                "target_finish_date": i.get("target_finish_date"),
                "days_since": i.get("days_since"),
                "date_alert": i.get("date_alert"),
                "metrics_status": i.get("metrics_status"),
                "asset_status": i.get("asset_status"),
                "created_at": i.get("created_at"),
            }
            for i in active
        ]),
        "submitted_items_json": json.dumps([
            {
                "name": i.get("name") or "",
                "award": i.get("award") or "Unknown",
                "company": i.get("company") or "Unknown",
                "category": i.get("category") or "",
                "close_date": i.get("close_date"),
                "submitted_date": i.get("submitted_date"),
            }
            for i in submitted
        ]),
        "all_created_items_json": json.dumps([
            {
                "name": i.get("name") or "",
                "award": i.get("award") or "Unknown",
                "company": i.get("company") or "Unknown",
                "category": i.get("category") or "",
                "created_at": i.get("created_at"),
            }
            for i in all_items
            if i.get("sales_status") not in EXCLUDED_STATUSES
        ]),
        "last_sync": last_sync,
        "ai_session_id": ai_session_id,
    }


@app.get("/manager", response_class=HTMLResponse)
async def manager_dashboard(request: Request):
    data = _manager_data()
    return templates.TemplateResponse(request, "manager.html", data)


@app.post("/sync/submissions", response_class=HTMLResponse)
async def trigger_sync_submissions(request: Request):
    result = sync_submissions(DB_PATH, settings)
    data = _manager_data()
    data["sync_result"] = result
    return templates.TemplateResponse(request, "manager.html", data)


@app.get("/", response_class=HTMLResponse)
async def award_plans(request: Request):
    return templates.TemplateResponse(request, "award_plans.html")


@app.get("/writing", response_class=HTMLResponse)
async def award_writing(request: Request):
    grouped, last_sync = _grouped_velma_items()
    velma_board_id = settings["monday"]["boards"]["velma_tracker"]
    return templates.TemplateResponse(request, "award_writing.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "velma_board_id": velma_board_id,
        "status_actions": VELMA_STATUS_ACTIONS,
        "action_flags": VELMA_ACTION_FLAGS,
    })


@app.post("/sync/velma", response_class=HTMLResponse)
async def trigger_sync_velma(request: Request):
    """Quick sync: only Active group from Velma tracker."""
    from src.adapters.monday_sync import sync_velma_quick
    result = sync_velma_quick(DB_PATH, settings)
    grouped, last_sync = _grouped_velma_items()
    velma_board_id = settings["monday"]["boards"]["velma_tracker"]
    return templates.TemplateResponse(request, "award_writing.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "sync_result": result,
        "velma_board_id": velma_board_id,
        "status_actions": VELMA_STATUS_ACTIONS,
        "action_flags": VELMA_ACTION_FLAGS,
    })


@app.post("/sync/velma-full", response_class=HTMLResponse)
async def trigger_sync_velma_full(request: Request):
    """Full sync: all groups from Velma tracker."""
    from src.adapters.monday_sync import sync_velma
    result = sync_velma(DB_PATH, settings)
    grouped, last_sync = _grouped_velma_items()
    velma_board_id = settings["monday"]["boards"]["velma_tracker"]
    return templates.TemplateResponse(request, "award_writing.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "sync_result": result,
        "velma_board_id": velma_board_id,
        "status_actions": VELMA_STATUS_ACTIONS,
        "action_flags": VELMA_ACTION_FLAGS,
    })


@app.post("/api/writing/velma-done")
async def signal_velma_done():
    """Called by terminal when a velma process finishes. Sets a flag for the frontend to pick up."""
    global _velma_refresh_pending
    _velma_refresh_pending = True
    return JSONResponse({"ok": True})


@app.get("/api/writing/velma-done")
async def check_velma_done():
    """Polled by the frontend to check if a velma process has completed."""
    global _velma_refresh_pending
    if _velma_refresh_pending:
        _velma_refresh_pending = False
        return JSONResponse({"refresh": True})
    return JSONResponse({"refresh": False})


@app.post("/api/writing/guideline")
async def save_velma_guideline(request: Request):
    """Persist a guideline change for a single Velma item."""
    data = await request.json()
    monday_id = data.get("monday_id")
    guideline = data.get("guideline")
    if not monday_id or guideline not in ("m", "s", "a"):
        return JSONResponse({"error": "Invalid parameters"}, status_code=400)
    conn = get_db(DB_PATH)
    update_velma_guideline(conn, monday_id, guideline)
    conn.close()
    return JSONResponse({"ok": True})


VELMA_VALID_STATUSES = {
    "Transcript Ready", "Ready for Mapping", "Interview Processed",
    "Mapped", "Mapped and Processed", "Done", "ERROR",
}


@app.post("/api/writing/status")
async def save_velma_status(request: Request):
    """Update the Velma Status on Monday.com and in the local cache."""
    from config.settings import get_monday_token
    from src.adapters.monday import MondayAdapter

    data = await request.json()
    monday_id = data.get("monday_id")
    new_status = data.get("status")

    if not monday_id or new_status not in VELMA_VALID_STATUSES:
        return JSONResponse({"error": "Invalid parameters"}, status_code=400)

    board_id = settings["monday"]["boards"]["velma_tracker"]
    status_col_id = settings["monday"]["columns"]["velma_tracker"]["velma_status"]

    try:
        token = get_monday_token()
        adapter = MondayAdapter(settings=settings, api_token=token)
        success = adapter.update_item_status(board_id, monday_id, status_col_id, new_status)
        if not success:
            return JSONResponse({"error": "Monday.com update failed"}, status_code=500)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    # Update local cache
    conn = get_db(DB_PATH)
    conn.execute(
        "UPDATE cache_velma_items SET velma_status = ? WHERE monday_id = ?",
        (new_status, monday_id),
    )
    conn.commit()
    conn.close()

    return JSONResponse({"ok": True})


PREV_SUBMISSION_SLOTS = {
    1: "link_mm0h124p",  # prev_submission_1
    2: "link_mm0hx5kt",  # prev_submission_2
}


@app.get("/api/writing/prev-submissions/{monday_id}")
async def get_prev_submissions(monday_id: str):
    """Get previous submissions for the same company as a Velma item."""
    conn = get_db(DB_PATH)

    # Look up Velma item's linked submission tracker ID
    row = conn.execute(
        "SELECT tracker_submission_id FROM cache_velma_items WHERE monday_id = ?",
        (monday_id,),
    ).fetchone()
    if not row or not row["tracker_submission_id"]:
        conn.close()
        return JSONResponse({"submissions": [], "company": None})

    # Look up company from submission tracker
    sub_row = conn.execute(
        "SELECT company FROM cache_submission_items WHERE monday_id = ?",
        (row["tracker_submission_id"],),
    ).fetchone()
    if not sub_row or not sub_row["company"]:
        conn.close()
        return JSONResponse({"submissions": [], "company": None})

    company = sub_row["company"]

    # Get all submissions for this company
    rows = conn.execute(
        """SELECT monday_id, name, award, category, delivery_status, result_status,
                  submission_link_url, submission_link_text
           FROM cache_submission_items
           WHERE company = ?
           ORDER BY close_date DESC, monday_updated_at DESC""",
        (company,),
    ).fetchall()
    conn.close()

    submissions = [
        {
            "monday_id": r["monday_id"],
            "name": r["name"],
            "award": r["award"],
            "category": r["category"],
            "delivery_status": r["delivery_status"],
            "result_status": r["result_status"],
            "link": r["submission_link_url"],
            "link_text": r["submission_link_text"] or r["name"],
        }
        for r in rows
    ]

    return JSONResponse({"submissions": submissions, "company": company})


@app.post("/api/writing/prev-submission")
async def update_prev_submission(request: Request):
    """Set or clear a prev_submission link on a Velma item (writes to Monday)."""
    from config.settings import get_monday_token
    from src.adapters.monday import MondayAdapter

    data = await request.json()
    monday_id = data.get("monday_id")
    slot = data.get("slot")
    url = data.get("url")  # None to clear
    text = data.get("text", "")

    if not monday_id or slot not in (1, 2):
        return JSONResponse({"error": "Invalid parameters"}, status_code=400)

    col_id = PREV_SUBMISSION_SLOTS[slot]
    board_id = settings["monday"]["boards"]["velma_tracker"]

    # Build link value
    if url:
        link_val = {"url": url, "text": text}
    else:
        link_val = {"url": "", "text": ""}

    try:
        token = get_monday_token()
        adapter = MondayAdapter(settings=settings, api_token=token)
        success = adapter.update_item_columns(board_id, monday_id, {col_id: link_val})
        if not success:
            return JSONResponse({"error": "Monday.com update failed"}, status_code=500)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    # Update local cache
    conn = get_db(DB_PATH)
    url_col = f"prev_submission_{slot}_url"
    text_col = f"prev_submission_{slot}_text"
    conn.execute(
        f"UPDATE cache_velma_items SET {url_col} = ?, {text_col} = ? WHERE monday_id = ?",  # noqa: S608
        (url if url else None, text if url else None, monday_id),
    )
    conn.commit()
    conn.close()

    return JSONResponse({"ok": True})


@app.post("/api/writing/launch")
async def launch_writing(request: Request):
    """Open Terminal.app with a velma command."""
    import subprocess

    data = await request.json()
    name = data.get("name", "")
    action = data.get("action", "")
    guideline = data.get("guideline")
    batch = data.get("batch", False)
    interview_notes = data.get("interview_notes", False)
    supporting_docs = data.get("supporting_docs", False)
    pre_interview = data.get("pre_interview", False)
    custom = data.get("custom", "")
    award = data.get("award", "")

    if not name or action not in VELMA_ACTIONS:
        return JSONResponse({"error": "Invalid parameters"}, status_code=400)

    safe_name = name.replace("'", "'\\''")
    cmd = f"velma {action} '{safe_name}'"

    if guideline:
        cmd += f" -g {guideline}"
    if batch:
        cmd += " --batch"
    if interview_notes:
        cmd += " --interview-notes"
    if supporting_docs:
        cmd += " --supporting-docs"
    if pre_interview:
        cmd += " --pre-interview"
    if custom:
        safe_custom = custom.replace("'", "'\\''")
        cmd += f" --custom '{safe_custom}'"
    if award:
        safe_award = award.replace("'", "'\\''")
        cmd += f" --award '{safe_award}'"

    # Write a temp script that runs velma, triggers refresh, and closes the terminal window
    script_content = f"""#!/bin/bash
{cmd}
curl -s -X POST http://127.0.0.1:8001/api/writing/velma-done > /dev/null 2>&1
sleep 5
osascript -e 'tell application "Terminal" to close front window' &
exit
"""
    script_file = tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False, prefix='velma_')
    script_file.write(script_content)
    script_file.close()
    os.chmod(script_file.name, 0o755)

    osascript = f'''tell application "Terminal"
  activate
  do script "{script_file.name}"
end tell'''
    subprocess.Popen(["osascript", "-e", osascript])
    return JSONResponse({"ok": True, "command": cmd})


@app.get("/judging", response_class=HTMLResponse)
async def award_judging(request: Request):
    grouped, last_sync = _grouped_edna_items()
    chart_data = _judging_stats(grouped)
    edna_board_id = settings["monday"]["boards"]["edna_tracker"]
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "chart_data": chart_data,
        "edna_board_id": edna_board_id,
    })


@app.post("/sync/monday", response_class=HTMLResponse)
async def trigger_sync_monday(request: Request):
    """Quick sync: only Active group from Edna tracker."""
    from src.adapters.monday_sync import sync_monday_quick
    result = sync_monday_quick(DB_PATH, settings)
    grouped, last_sync = _grouped_edna_items()
    chart_data = _judging_stats(grouped)
    edna_board_id = settings["monday"]["boards"]["edna_tracker"]
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "sync_result": result,
        "chart_data": chart_data,
        "edna_board_id": edna_board_id,
    })


@app.post("/sync/monday-full", response_class=HTMLResponse)
async def trigger_sync_monday_full(request: Request):
    """Full sync: all groups from Edna tracker + Submission tracker."""
    result = sync_monday(DB_PATH, settings)
    grouped, last_sync = _grouped_edna_items()
    chart_data = _judging_stats(grouped)
    edna_board_id = settings["monday"]["boards"]["edna_tracker"]
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "sync_result": result,
        "chart_data": chart_data,
        "edna_board_id": edna_board_id,
    })


@app.post("/api/judging/guideline")
async def save_guideline(request: Request):
    """Persist a guideline change for a single item."""
    data = await request.json()
    monday_id = data.get("monday_id")
    guideline = data.get("guideline")
    if not monday_id or guideline not in ("m", "s"):
        return JSONResponse({"error": "Invalid parameters"}, status_code=400)
    conn = get_db(DB_PATH)
    update_guideline(conn, monday_id, guideline)
    conn.close()
    return JSONResponse({"ok": True})


@app.post("/api/judging/launch")
async def launch_judging(request: Request):
    """Open Terminal.app with edna judge command."""
    import subprocess

    data = await request.json()
    name = data.get("name", "")
    guideline = data.get("guideline", "s")
    award = data.get("award", "")

    if not name:
        return JSONResponse({"error": "Name is required"}, status_code=400)

    # Escape single quotes in the name for shell safety
    safe_name = name.replace("'", "'\\''")
    award_flag = f" --award '{award.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" if award else ""
    cmd = f"edna judge -g {guideline}{award_flag} '{safe_name}'"

    osascript = f'''tell application "Terminal"
  activate
  do script "{cmd}"
end tell'''
    subprocess.Popen(["osascript", "-e", osascript])
    return JSONResponse({"ok": True})


# -- Manager AI endpoints ----------------------------------------------------

# Google Doc ID for alert rules — read at analysis time
RULES_DOC_ID = "1JQkD037KVK5R9DPvTsLEgrP3Oa4nIWej-los97Lkw2Q"

# Cache for rules doc content
_rules_cache: dict = {"content": None, "fetched_at": 0}
RULES_CACHE_TTL = 3600  # 1 hour


def _get_rules_context() -> str:
    """Read the alert rules from the Google Doc, with caching."""
    import time as _time
    now = _time.time()
    if _rules_cache["content"] and (now - _rules_cache["fetched_at"]) < RULES_CACHE_TTL:
        return _rules_cache["content"]

    try:
        from gdrive.auth import get_credentials
        from googleapiclient.discovery import build as gbuild

        creds = get_credentials(
            ["https://www.googleapis.com/auth/documents.readonly"],
            "token_drive_docs.json",
            auth_message="",
        )
        docs = gbuild("docs", "v1", credentials=creds)
        doc = docs.documents().get(documentId=RULES_DOC_ID).execute()

        text = ""
        for element in doc.get("body", {}).get("content", []):
            if "paragraph" in element:
                for elem in element["paragraph"].get("elements", []):
                    if "textRun" in elem:
                        text += elem["textRun"]["content"]

        _rules_cache["content"] = text
        _rules_cache["fetched_at"] = now
        log.info("Loaded alert rules from Google Doc (%d chars)", len(text))
        return text

    except Exception as exc:
        log.warning("Could not read rules doc: %s — using fallback", exc)
        return _FALLBACK_RULES


_FALLBACK_RULES = """You are the RBD Delivery Manager AI assistant.
Analyse the alert data provided. Each alert value has been computed by Monday.com formula columns.
Consider writer capacity across all items, deadline clustering, and lifecycle stage context.
Always cite specific data in your reasoning."""


@app.post("/api/clear-rules-cache")
async def clear_rules_cache():
    """Force re-read of the Google Doc rules on next analysis."""
    _rules_cache["content"] = None
    _rules_cache["fetched_at"] = 0
    return JSONResponse({"ok": True})


@app.post("/api/analyse")
async def run_analysis():
    """Run AI analysis on current alerts using Claude CLI."""
    conn = get_db(DB_PATH)
    all_alerts = get_submission_alerts(conn)

    # Filter to active (non-finished) items only for analysis
    alerts = [
        a for a in all_alerts
        if a.get("delivery_status") not in FINISHED_STATUSES
        and a.get("sales_status") not in EXCLUDED_STATUSES
    ]

    if not alerts:
        conn.close()
        return JSONResponse({"ok": True, "summary": "No active alerts to analyse.", "insights": []})

    slim_alerts = [
        {
            "monday_id": a["monday_id"],
            "name": a["name"],
            "delivery_status": a.get("delivery_status"),
            "writer": a.get("writer"),
            "close_date": a.get("close_date"),
            "business_days_to_close": business_days_until(a.get("close_date")),
            "target_finish_date": a.get("target_finish_date"),
            "business_days_to_target": business_days_until(a.get("target_finish_date")),
            "extension_date": a.get("extension_date"),
            "business_days_to_extension": business_days_until(a.get("extension_date")),
            "contingency_days": a.get("contingency_days"),
            "date_alert": a.get("date_alert"),
            "writer_alert": a.get("writer_alert"),
            "metrics_alert": a.get("metrics_alert"),
            "asset_alert": a.get("asset_alert"),
        }
        for a in alerts
    ]

    rules_context = _get_rules_context()
    result = analyse(slim_alerts, rules_context)

    if result.get("error"):
        conn.close()
        return JSONResponse({"error": result["error"]})

    # Reset chat history so new analysis starts fresh
    reset_chat()

    # Store insights
    insights = result.get("insights", [])
    if insights:
        upsert_ai_insights(conn, insights)

    conn.close()

    return JSONResponse({
        "ok": True,
        "summary": result.get("summary", ""),
        "patterns": result.get("patterns", []),
        "insight_count": len(insights),
    })



def _build_alert_context() -> str:
    """Build a JSON summary of submissions with active alerts."""
    conn = get_db(DB_PATH)
    alerts = get_submission_alerts(conn)
    conn.close()

    active = [
        a for a in alerts
        if a.get("delivery_status") not in FINISHED_STATUSES
        and a.get("sales_status") not in EXCLUDED_STATUSES
    ]
    return json.dumps([_slim_submission(a) for a in active], indent=2, default=str)


def _build_full_submissions_context() -> str:
    """Build a JSON summary of ALL active submissions (called on demand by AI)."""
    conn = get_db(DB_PATH)
    all_items = get_all_submission_items(conn)
    conn.close()

    active = [
        i for i in all_items
        if i.get("delivery_status") not in FINISHED_STATUSES
        and i.get("sales_status") not in EXCLUDED_STATUSES
    ]
    return json.dumps([_slim_submission(a) for a in active], indent=2, default=str)


def _slim_submission(a: dict) -> dict:
    """Extract the fields relevant for AI chat from a submission row."""
    return {
        "monday_id": str(a.get("monday_id", "")),
        "name": a.get("name"),
        "board_group": a.get("board_group"),
        "delivery_status": a.get("delivery_status"),
        "sales_status": a.get("sales_status"),
        "writer": a.get("writer"),
        "reviewer": a.get("reviewer"),
        "award": a.get("award"),
        "category": a.get("category"),
        "company": a.get("company"),
        "close_date": a.get("close_date"),
        "business_days_to_close": business_days_until(a.get("close_date")),
        "target_finish_date": a.get("target_finish_date"),
        "business_days_to_target": business_days_until(a.get("target_finish_date")),
        "extension_date": a.get("extension_date"),
        "business_days_to_extension": business_days_until(a.get("extension_date")),
        "contingency_days": a.get("contingency_days"),
        "date_alert": a.get("date_alert"),
        "writer_alert": a.get("writer_alert"),
        "metrics_alert": a.get("metrics_alert"),
        "asset_alert": a.get("asset_alert"),
    }


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Chat with Claude about the delivery pipeline."""
    from fastapi.responses import StreamingResponse

    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "No message provided"}, status_code=400)

    import asyncio
    alert_context = _build_alert_context()
    rules_context = _get_rules_context()

    result = await asyncio.to_thread(
        chat_sync,
        message=message,
        alert_context=alert_context,
        rules_context=rules_context,
        fetch_full_data=_build_full_submissions_context,
    )

    async def event_stream():
        text = result.get("text", "")
        yield f"data: {json.dumps({'text': text})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/chat/reset")
async def chat_reset_endpoint():
    """Clear chat history."""
    reset_chat()
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8001, reload=True)
