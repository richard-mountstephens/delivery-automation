"""Data models for Submission Tracker board items."""

from pydantic import BaseModel


class SubmissionItem(BaseModel):
    monday_id: str
    name: str
    board_group: str | None = None
    delivery_status: str | None = None
    sales_status: str | None = None
    writer: str | None = None
    reviewer: str | None = None
    close_date: str | None = None
    target_finish_date: str | None = None
    extension_date: str | None = None
    category: str | None = None
    company: str | None = None
    award: str | None = None
    escalate: bool = False
    # Formula-computed alert columns
    date_alert: str | None = None
    writer_alert: str | None = None
    metrics_alert: str | None = None
    asset_alert: str | None = None
    contingency_days: str | None = None
    spare_days_est: str | None = None
    days_since: str | None = None
    metrics_status: str | None = None
    asset_status: str | None = None
    asset_days_since: str | None = None
    writer_due: str | None = None
    reviewer_due: str | None = None
    submission_link_url: str | None = None
    submission_link_text: str | None = None
    result_status: str | None = None
    submitted_date: str | None = None
    created_at: str | None = None
    monday_updated_at: str | None = None
