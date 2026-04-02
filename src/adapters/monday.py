"""Monday.com GraphQL API adapter — read-only sync of board data."""

import json
import logging

import httpx

from src.adapters.models import EdnaItem
from src.adapters.submission_models import SubmissionItem

logger = logging.getLogger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"
PAGE_LIMIT = 500


class MondayAdapter:
    def __init__(self, settings: dict, api_token: str):
        self.settings = settings
        self.boards = settings["monday"]["boards"]
        self.columns = settings["monday"]["columns"]
        self.client = httpx.Client(
            headers={
                "Authorization": api_token,
                "Content-Type": "application/json",
            },
            timeout=90.0,
        )

    def get_edna_items(self) -> list[EdnaItem]:
        """Fetch all items from the Briefed Edna Tracker board."""
        board_id = self.boards["edna_tracker"]
        col_map = self.columns["edna_tracker"]

        items = self._fetch_board_items(board_id, label="edna tracker")

        edna_items: list[EdnaItem] = []
        for item in items:
            cols = _parse_column_values(item["column_values"])
            try:
                group = item.get("group")
                board_group = group["title"] if group else None

                # Parse link column — has both URL and display text
                link_url, link_text = _link(cols, col_map["edna_review_link"])

                edna_item = EdnaItem(
                    monday_id=item["id"],
                    name=item["name"],
                    board_group=board_group,
                    award=_text(cols, col_map["award"]),
                    category=_text(cols, col_map["category"]),
                    edna_status=_text(cols, col_map["edna_status"]),
                    triage_score=_number(cols, col_map["triage_score"]),
                    writer=_text(cols, col_map["writer"]),
                    reviewer=_text(cols, col_map["reviewer"]),
                    edna_review_link=link_url,
                    edna_review_link_text=link_text,
                    monday_updated_at=item.get("updated_at"),
                )
                edna_items.append(edna_item)
            except Exception:
                logger.warning("Skipping edna item %s: parse error", item["id"], exc_info=True)

        return edna_items

    def get_submission_items(self) -> list[SubmissionItem]:
        """Fetch all items from the New Award Submission Tracker board."""
        board_id = self.boards["submission_tracker"]
        col_map = self.columns["submission_tracker"]

        items = self._fetch_board_items(board_id, label="submission tracker")

        submission_items: list[SubmissionItem] = []
        for item in items:
            cols = _parse_column_values(item["column_values"])
            try:
                group = item.get("group")
                board_group = group["title"] if group else None

                # Checkbox: parse from JSON value
                escalate = _checkbox(cols, col_map["escalate"])

                sub = SubmissionItem(
                    monday_id=item["id"],
                    name=item["name"],
                    board_group=board_group,
                    delivery_status=_text(cols, col_map["delivery_status"]),
                    sales_status=_text(cols, col_map["sales_status"]),
                    writer=_text(cols, col_map["writer"]),
                    reviewer=_text(cols, col_map["reviewer"]),
                    close_date=_text(cols, col_map["close_date"]),
                    target_finish_date=_text(cols, col_map["target_finish_date"]),
                    extension_date=_text(cols, col_map["extension_date"]),
                    category=_text(cols, col_map["category"]),
                    company=_text(cols, col_map["company"]),
                    award=_text(cols, col_map["award"]),
                    escalate=escalate,
                    date_alert=_text(cols, col_map["date_alert"]),
                    writer_alert=_text(cols, col_map["writer_alert"]),
                    metrics_alert=_text(cols, col_map["metrics_alert"]),
                    asset_alert=_text(cols, col_map["asset_alert"]),
                    contingency_days=_text(cols, col_map["contingency_days"]),
                    spare_days_est=_text(cols, col_map["spare_days_est"]),
                    days_since=_text(cols, col_map["days_since"]),
                    metrics_status=_text(cols, col_map["metrics_status"]),
                    asset_status=_text(cols, col_map["asset_status"]),
                    asset_days_since=_text(cols, col_map["asset_days_since"]),
                    writer_due=_text(cols, col_map["writer_due"]),
                    reviewer_due=_text(cols, col_map["reviewer_due"]),
                    monday_updated_at=item.get("updated_at"),
                )
                submission_items.append(sub)
            except Exception:
                logger.warning("Skipping submission item %s: parse error", item["id"], exc_info=True)

        return submission_items

    # -- Internal helpers -----------------------------------------------------

    def _fetch_board_items(self, board_id: int, label: str = "items") -> list[dict]:
        """Paginate through a board's items_page and return all raw item dicts."""
        all_items: list[dict] = []
        cursor: str | None = None

        while True:
            query = _build_items_query(board_id, cursor)
            response = self._execute_query(query)

            items_page = response["data"]["boards"][0]["items_page"]
            items = items_page["items"]
            all_items.extend(items)
            cursor = items_page["cursor"]

            print(f"Fetching {label}... {len(all_items)}")

            if cursor is None:
                break

        print(f"Fetched {len(all_items)} {label} total.")
        return all_items

    def _execute_query(self, query: str) -> dict:
        """Send a GraphQL query to Monday.com and return the parsed response."""
        payload = {"query": query}

        try:
            resp = self.client.post(MONDAY_API_URL, json=payload)
        except httpx.TransportError as exc:
            logger.error("Monday API transport error: %s", exc)
            raise

        if resp.status_code == 401:
            raise PermissionError("Monday API authentication failed — check your MONDAY_API_TOKEN.")
        if resp.status_code == 403:
            raise PermissionError("Monday API authorization denied — token lacks required permissions.")

        resp.raise_for_status()

        body = resp.json()
        if "errors" in body:
            error_messages = [e.get("message", str(e)) for e in body["errors"]]
            raise RuntimeError(f"Monday GraphQL errors: {'; '.join(error_messages)}")

        return body


# -- Module-level helpers (pure functions) ------------------------------------


def _build_items_query(board_id: int, cursor: str | None) -> str:
    """Build the GraphQL query string for fetching a page of board items."""
    if cursor is None:
        cursor_arg = ""
    else:
        cursor_arg = f', cursor: "{cursor}"'

    return (
        "{ boards(ids: [" + str(board_id) + "]) {"
        " items_page(limit: " + str(PAGE_LIMIT) + cursor_arg + ") {"
        " cursor items { id name created_at updated_at group { title }"
        " column_values { id text value"
        " ... on MirrorValue { display_value }"
        " ... on FormulaValue { display_value }"
        " ... on BoardRelationValue { display_value linked_item_ids }"
        " } } } } }"
    )


def _parse_column_values(column_values: list[dict]) -> dict[str, dict]:
    """Index column_values by column ID for fast lookup."""
    return {cv["id"]: cv for cv in column_values}


def _text(cols: dict[str, dict], col_id: str) -> str | None:
    """Extract the display text from a column value, returning None if blank.

    Falls back to display_value for mirror columns where text is not populated.
    """
    cv = cols.get(col_id)
    if cv is None:
        return None
    text = cv.get("text")
    if text is None or text.strip() == "":
        # Mirror columns use display_value instead of text
        text = cv.get("display_value")
    if text is None or (isinstance(text, str) and text.strip() in ("", "null")):
        return None
    return text.strip() if isinstance(text, str) else str(text)


def _number(cols: dict[str, dict], col_id: str) -> float | None:
    """Extract a numeric value from a column."""
    text = _text(cols, col_id)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _checkbox(cols: dict[str, dict], col_id: str) -> bool:
    """Extract a boolean from a checkbox column."""
    cv = cols.get(col_id)
    if cv is None:
        return False
    raw = cv.get("value")
    if raw:
        try:
            parsed = json.loads(raw)
            return parsed.get("checked") == "true"
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback: text field shows "v" when checked
    text = cv.get("text", "")
    return text.strip().lower() in ("v", "true", "yes")


def _link(cols: dict[str, dict], col_id: str) -> tuple[str | None, str | None]:
    """Extract URL and display text from a link column."""
    cv = cols.get(col_id)
    if cv is None:
        return None, None

    url = None
    display_text = None

    raw = cv.get("value")
    if raw:
        try:
            parsed = json.loads(raw)
            url = parsed.get("url")
            display_text = parsed.get("text")
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: text field may contain "display - url" format
    if not url:
        text = cv.get("text")
        if text and text.strip():
            display_text = text.strip()

    return url, display_text
