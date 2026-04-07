"""FastAPI web application — Delivery Hub dashboard."""

import json
import logging
from collections import defaultdict

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from config.settings import load_settings
from src.adapters.claude_cli import analyse, chat_sync
from src.adapters.monday_sync import sync_monday, sync_submissions
from src.store.cache import (
    apply_default_guidelines,
    get_ai_insights,
    get_all_edna_items,
    get_all_submission_items,
    get_submission_alerts,
    get_sync_meta,
    set_sync_meta,
    update_guideline,
    upsert_ai_insights,
)
from src.store.db import get_db

log = logging.getLogger(__name__)

app = FastAPI(title="Award Delivery Hub")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

settings = load_settings()
DB_PATH = settings["database"]["path"]

# Group display order
GROUP_ORDER = ["Active", "Done", "2025"]


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
            }
            for i in active
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
    return templates.TemplateResponse(request, "award_writing.html")


@app.get("/judging", response_class=HTMLResponse)
async def award_judging(request: Request):
    grouped, last_sync = _grouped_edna_items()
    chart_data = _judging_stats(grouped)
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "chart_data": chart_data,
    })


@app.post("/sync/monday", response_class=HTMLResponse)
async def trigger_sync_monday(request: Request):
    """Quick sync: only Active group from Edna tracker."""
    from src.adapters.monday_sync import sync_monday_quick
    result = sync_monday_quick(DB_PATH, settings)
    grouped, last_sync = _grouped_edna_items()
    chart_data = _judging_stats(grouped)
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "sync_result": result,
        "chart_data": chart_data,
    })


@app.post("/sync/monday-full", response_class=HTMLResponse)
async def trigger_sync_monday_full(request: Request):
    """Full sync: all groups from Edna tracker + Submission tracker."""
    result = sync_monday(DB_PATH, settings)
    grouped, last_sync = _grouped_edna_items()
    chart_data = _judging_stats(grouped)
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "sync_result": result,
        "chart_data": chart_data,
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

    # Calculate business days (excluding weekends + AU public holidays)
    from datetime import date as _date, timedelta as _td

    # Australian national public holidays for 2025-2027
    AU_PUBLIC_HOLIDAYS = {
        # 2025
        _date(2025, 1, 1), _date(2025, 1, 27),  # New Year, Australia Day
        _date(2025, 4, 18), _date(2025, 4, 19), _date(2025, 4, 21),  # Good Friday, Saturday, Easter Monday
        _date(2025, 4, 25),  # ANZAC Day
        _date(2025, 6, 9),   # Queen's Birthday (VIC/most states)
        _date(2025, 12, 25), _date(2025, 12, 26),  # Christmas, Boxing Day
        # 2026
        _date(2026, 1, 1), _date(2026, 1, 26),  # New Year, Australia Day
        _date(2026, 4, 3), _date(2026, 4, 4), _date(2026, 4, 6),  # Good Friday, Saturday, Easter Monday
        _date(2026, 4, 25),  # ANZAC Day (Saturday — observed Monday 27th)
        _date(2026, 4, 27),  # ANZAC Day observed
        _date(2026, 6, 8),   # Queen's Birthday
        _date(2026, 12, 25), _date(2026, 12, 26),  # Christmas, Boxing Day
        _date(2026, 12, 28),  # Boxing Day observed (Sat→Mon)
        # 2027
        _date(2027, 1, 1), _date(2027, 1, 26),
        _date(2027, 3, 26), _date(2027, 3, 27), _date(2027, 3, 29),  # Easter
        _date(2027, 4, 26),  # ANZAC Day observed (Sun→Mon)
        _date(2027, 6, 14),
        _date(2027, 12, 25), _date(2027, 12, 27),  # Christmas, Boxing Day observed
    }

    def _business_days_until(date_str: str | None) -> int | None:
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

    slim_alerts = [
        {
            "monday_id": a["monday_id"],
            "name": a["name"],
            "delivery_status": a.get("delivery_status"),
            "writer": a.get("writer"),
            "close_date": a.get("close_date"),
            "business_days_to_close": _business_days_until(a.get("close_date")),
            "target_finish_date": a.get("target_finish_date"),
            "business_days_to_target": _business_days_until(a.get("target_finish_date")),
            "extension_date": a.get("extension_date"),
            "business_days_to_extension": _business_days_until(a.get("extension_date")),
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

    # Store insights
    insights = result.get("insights", [])
    if insights:
        upsert_ai_insights(conn, insights)

    # Store session ID for chat continuity
    session_id = result.get("session_id")
    if session_id:
        set_sync_meta(conn, "ai_session_id", session_id)

    conn.close()

    return JSONResponse({
        "ok": True,
        "summary": result.get("summary", ""),
        "patterns": result.get("patterns", []),
        "insight_count": len(insights),
    })


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Chat with Claude, resuming the analysis session."""
    from fastapi.responses import StreamingResponse

    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "No message provided"}, status_code=400)

    conn = get_db(DB_PATH)
    session_id = get_sync_meta(conn, "ai_session_id")
    conn.close()

    # For now, use synchronous chat and return as SSE
    result = chat_sync(message, session_id)

    # Update session ID if it changed
    new_session_id = result.get("session_id")
    if new_session_id and new_session_id != session_id:
        conn = get_db(DB_PATH)
        set_sync_meta(conn, "ai_session_id", new_session_id)
        conn.close()

    async def event_stream():
        import json as json_mod
        text = result.get("text", "")
        # Send the full response as a single SSE event
        yield f"data: {json_mod.dumps({'text': text})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8001, reload=True)
