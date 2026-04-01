# Delivery Hub — Project Guide

## What This Is

A local-first FastAPI + HTMX web app for managing award delivery — writing, judging, and plan tracking. Sister app to the Award Sales Hub (sales-automation). SQLite is local memory, Monday.com is the upstream data source.

## Code Conventions

- **Python 3.11+**, type hints on all public functions
- **Pydantic v2** for all data models (BaseModel, not dataclass)
- **SQLite** via Python's built-in sqlite3 — no ORM
- **httpx** for async HTTP
- **FastAPI** with Jinja2 templates, **HTMX** for interactivity — no SPA, no npm
- Imports: stdlib → third-party → local, separated by blank lines
- Naming: snake_case for files/functions/variables, PascalCase for classes
- Error handling: let exceptions propagate unless there's a specific recovery action
- No docstrings on obvious methods. Docstrings on public API functions and anything non-obvious.
- Tests use pytest. Test files mirror source: `tests/test_store/test_db.py` etc.

## Project Structure

```
src/
├── adapters/          # External API communication + sync workers
├── core/              # Pure logic — reads from cache only, no API calls
├── store/             # SQLite persistence (schema, CRUD)
├── web/               # FastAPI app, Jinja2 templates, static assets
config/                # Configuration files
data/                  # Data files
tests/                 # Mirrors src/ structure
```

## Running

```bash
./setup.sh             # One-time: create venv, install deps
./start.sh             # Start at http://127.0.0.1:8001
```
