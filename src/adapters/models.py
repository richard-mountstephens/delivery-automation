"""Shared data models for Monday.com board data."""

from pydantic import BaseModel


class EdnaItem(BaseModel):
    monday_id: str
    name: str
    board_group: str | None = None
    award: str | None = None
    category: str | None = None
    edna_status: str | None = None
    triage_score: float | None = None
    writer: str | None = None
    reviewer: str | None = None
    edna_review_link: str | None = None
    edna_review_link_text: str | None = None
    monday_updated_at: str | None = None
