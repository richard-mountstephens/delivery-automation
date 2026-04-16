"""Data model for Velma Tracker board items."""

from pydantic import BaseModel


class VelmaItem(BaseModel):
    monday_id: str
    name: str
    board_group: str | None = None
    velma_status: str | None = None
    writer: str | None = None
    award: str | None = None
    category: str | None = None
    # Input links
    interview_transcript_url: str | None = None
    interview_transcript_text: str | None = None
    # Context links
    submission_link: str | None = None  # mirror column, text only
    prev_submission_1_url: str | None = None
    prev_submission_1_text: str | None = None
    prev_submission_2_url: str | None = None
    prev_submission_2_text: str | None = None
    supporting_doc_1_url: str | None = None
    supporting_doc_1_text: str | None = None
    supporting_doc_2_url: str | None = None
    supporting_doc_2_text: str | None = None
    supporting_doc_3_url: str | None = None
    supporting_doc_3_text: str | None = None
    supporting_doc_4_url: str | None = None
    supporting_doc_4_text: str | None = None
    # Output links
    processed_interview_url: str | None = None
    processed_interview_text: str | None = None
    mapped_submission_url: str | None = None
    mapped_submission_text: str | None = None
    velma_draft_url: str | None = None
    velma_draft_text: str | None = None
    tracker_submission_id: str | None = None
    monday_updated_at: str | None = None
