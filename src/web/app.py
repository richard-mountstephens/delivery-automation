"""FastAPI web application — Delivery Hub dashboard."""

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

app = FastAPI(title="Award Delivery Hub")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def award_plans(request: Request):
    return templates.TemplateResponse(request, "award_plans.html")


@app.get("/writing", response_class=HTMLResponse)
async def award_writing(request: Request):
    return templates.TemplateResponse(request, "award_writing.html")


@app.get("/judging", response_class=HTMLResponse)
async def award_judging(request: Request):
    return templates.TemplateResponse(request, "award_judging.html")


if __name__ == "__main__":
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8001, reload=True)
