"""FastAPI web application — Delivery Hub dashboard."""

import logging
from collections import defaultdict

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from config.settings import load_settings
from src.adapters.monday_sync import sync_monday
from src.store.cache import (
    apply_default_guidelines,
    get_all_edna_items,
    get_sync_meta,
    update_guideline,
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


@app.get("/", response_class=HTMLResponse)
async def award_plans(request: Request):
    return templates.TemplateResponse(request, "award_plans.html")


@app.get("/writing", response_class=HTMLResponse)
async def award_writing(request: Request):
    return templates.TemplateResponse(request, "award_writing.html")


@app.get("/judging", response_class=HTMLResponse)
async def award_judging(request: Request):
    grouped, last_sync = _grouped_edna_items()
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
    })


@app.post("/sync/monday", response_class=HTMLResponse)
async def trigger_sync_monday(request: Request):
    result = sync_monday(DB_PATH, settings)
    grouped, last_sync = _grouped_edna_items()
    return templates.TemplateResponse(request, "award_judging.html", {
        "groups": grouped,
        "last_sync": last_sync,
        "sync_result": result,
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

    if not name:
        return JSONResponse({"error": "Name is required"}, status_code=400)

    # Escape single quotes in the name for shell safety
    safe_name = name.replace("'", "'\\''")
    cmd = f"edna judge -g {guideline} '{safe_name}'"

    osascript = f'''tell application "Terminal"
  activate
  do script "{cmd}"
end tell'''
    subprocess.Popen(["osascript", "-e", osascript])
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8001, reload=True)
