"""Monday.com GraphQL API adapter — read-only sync of board data."""

import json
import logging

import httpx

from src.adapters.models import EdnaItem
from src.adapters.submission_models import SubmissionItem
from src.adapters.velma_models import VelmaItem

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

    def get_board_group_ids(self, board_id: int, group_titles: list[str]) -> list[str]:
        """Look up Monday group IDs by their display titles."""
        query = f'{{ boards(ids: [{board_id}]) {{ groups {{ id title }} }} }}'
        response = self._execute_query(query)
        groups = response["data"]["boards"][0]["groups"]
        title_lower = {t.lower() for t in group_titles}
        return [g["id"] for g in groups if g["title"].lower() in title_lower]

    def get_edna_items(self, groups: list[str] | None = None) -> list[EdnaItem]:
        """Fetch items from the Briefed Edna Tracker board.

        Args:
            groups: Optional list of group titles to fetch (e.g. ["Active"]).
                    If None, fetches all items.
        """
        board_id = self.boards["edna_tracker"]

        group_ids = None
        if groups:
            group_ids = self.get_board_group_ids(board_id, groups)
            if not group_ids:
                logger.warning("No matching group IDs found for %s", groups)

        items = self._fetch_board_items(board_id, label="edna tracker", group_ids=group_ids)
        return self.parse_edna_items(items)

    def parse_edna_items(self, items: list[dict]) -> list[EdnaItem]:
        """Parse raw edna tracker items into EdnaItem models."""
        col_map = self.columns["edna_tracker"]
        edna_items: list[EdnaItem] = []
        for item in items:
            cols = _parse_column_values(item["column_values"])
            try:
                group = item.get("group")
                board_group = group["title"] if group else None

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
        """Fetch all items from the submission tracker boards (active + archive)."""
        board_ids = self.boards["submission_tracker"]
        if not isinstance(board_ids, list):
            board_ids = [board_ids]
        all_items = []
        for board_id in board_ids:
            all_items.extend(self._fetch_board_items(board_id, label=f"submission tracker {board_id}"))
        return self.parse_submission_items(all_items)

    def parse_submission_items(self, items: list[dict]) -> list[SubmissionItem]:
        """Parse raw submission tracker items into SubmissionItem models."""
        col_map = self.columns["submission_tracker"]
        submission_items: list[SubmissionItem] = []
        for item in items:
            cols = _parse_column_values(item["column_values"])
            try:
                group = item.get("group")
                board_group = group["title"] if group else None

                escalate = _checkbox(cols, col_map["escalate"])
                sl_url, sl_text = _link(cols, col_map["submission_link"])

                delivery_status = _text(cols, col_map["delivery_status"])
                submitted_date = _submitted_date(cols, col_map["delivery_status"], delivery_status)

                # Prefer creation_log column (preserves original date after board duplication)
                # over Monday's created_at (which resets on duplication)
                creation_log_text = _text(cols, col_map.get("creation_log", ""))
                original_created_at = creation_log_text or item.get("created_at")

                sub = SubmissionItem(
                    monday_id=item["id"],
                    name=item["name"],
                    board_group=board_group,
                    created_at=original_created_at,
                    delivery_status=delivery_status,
                    submitted_date=submitted_date,
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
                    submission_link_url=sl_url,
                    submission_link_text=sl_text,
                    result_status=_text(cols, col_map["result_status"]),
                    monday_updated_at=item.get("updated_at"),
                )
                submission_items.append(sub)
            except Exception:
                logger.warning("Skipping submission item %s: parse error", item["id"], exc_info=True)

        return submission_items

    def get_velma_items(self, groups: list[str] | None = None) -> list[VelmaItem]:
        """Fetch items from the Briefed Velma Tracker board."""
        board_id = self.boards["velma_tracker"]

        group_ids = None
        if groups:
            group_ids = self.get_board_group_ids(board_id, groups)
            if not group_ids:
                logger.warning("No matching group IDs found for %s", groups)

        items = self._fetch_board_items(board_id, label="velma tracker", group_ids=group_ids)
        return self.parse_velma_items(items)

    def parse_velma_items(self, items: list[dict]) -> list[VelmaItem]:
        """Parse raw velma tracker items into VelmaItem models."""
        col_map = self.columns["velma_tracker"]
        velma_items: list[VelmaItem] = []
        for item in items:
            cols = _parse_column_values(item["column_values"])
            try:
                group = item.get("group")
                board_group = group["title"] if group else None

                it_url, it_text = _link(cols, col_map["interview_transcript"])
                pi_url, pi_text = _link(cols, col_map["processed_interview"])
                ps1_url, ps1_text = _link(cols, col_map["prev_submission_1"])
                ps2_url, ps2_text = _link(cols, col_map["prev_submission_2"])
                sd1_url, sd1_text = _link(cols, col_map["supporting_doc_1"])
                sd2_url, sd2_text = _link(cols, col_map["supporting_doc_2"])
                sd3_url, sd3_text = _link(cols, col_map["supporting_doc_3"])
                sd4_url, sd4_text = _link(cols, col_map["supporting_doc_4"])
                ms_url, ms_text = _link(cols, col_map["mapped_submission"])
                vd_url, vd_text = _link(cols, col_map["velma_draft"])

                tracker_rel = cols.get(col_map["tracker_relation"], {})
                linked_ids = tracker_rel.get("linked_item_ids", [])
                tracker_submission_id = str(linked_ids[0]) if linked_ids else None

                velma_item = VelmaItem(
                    monday_id=item["id"],
                    name=item["name"],
                    board_group=board_group,
                    velma_status=_text(cols, col_map["velma_status"]),
                    writer=_text(cols, col_map["writer"]),
                    award=_text(cols, col_map["award"]),
                    category=_text(cols, col_map["category"]),
                    interview_transcript_url=it_url,
                    interview_transcript_text=it_text,
                    submission_link=_text(cols, col_map["submission_link"]),
                    prev_submission_1_url=ps1_url,
                    prev_submission_1_text=ps1_text,
                    prev_submission_2_url=ps2_url,
                    prev_submission_2_text=ps2_text,
                    supporting_doc_1_url=sd1_url,
                    supporting_doc_1_text=sd1_text,
                    supporting_doc_2_url=sd2_url,
                    supporting_doc_2_text=sd2_text,
                    supporting_doc_3_url=sd3_url,
                    supporting_doc_3_text=sd3_text,
                    supporting_doc_4_url=sd4_url,
                    supporting_doc_4_text=sd4_text,
                    processed_interview_url=pi_url,
                    processed_interview_text=pi_text,
                    mapped_submission_url=ms_url,
                    mapped_submission_text=ms_text,
                    velma_draft_url=vd_url,
                    velma_draft_text=vd_text,
                    tracker_submission_id=tracker_submission_id,
                    monday_updated_at=item.get("updated_at"),
                )
                velma_items.append(velma_item)
            except Exception:
                logger.warning("Skipping velma item %s: parse error", item["id"], exc_info=True)

        return velma_items

    # -- Internal helpers -----------------------------------------------------

    def _fetch_board_items(self, board_id: int, label: str = "items", group_ids: list[str] | None = None) -> list[dict]:
        """Paginate through a board's items_page and return all raw item dicts."""
        all_items: list[dict] = []
        cursor: str | None = None

        while True:
            query = _build_items_query(board_id, cursor, group_ids=group_ids)
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

    def update_item_columns(self, board_id: int, item_id: str, column_values: dict) -> bool:
        """Update multiple columns on a Monday.com item (status, links, etc)."""
        mutation = """
        mutation($board_id: ID!, $item_id: ID!, $column_values: JSON!) {
            change_multiple_column_values(
                board_id: $board_id
                item_id: $item_id
                column_values: $column_values
            ) { id }
        }
        """
        variables = {
            "board_id": str(board_id),
            "item_id": str(item_id),
            "column_values": json.dumps(column_values),
        }
        body = self._execute_query(mutation, variables=variables)
        return "data" in body and body["data"].get("change_multiple_column_values") is not None

    def update_item_status(self, board_id: int, item_id: str, status_col_id: str, status_label: str) -> bool:
        """Update a status column on a Monday.com item."""
        return self.update_item_columns(board_id, item_id, {status_col_id: {"label": status_label}})

    def _execute_query(self, query: str, variables: dict | None = None) -> dict:
        """Send a GraphQL query to Monday.com and return the parsed response."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

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


def _build_items_query(board_id: int, cursor: str | None, group_ids: list[str] | None = None) -> str:
    """Build the GraphQL query string for fetching a page of board items.

    Args:
        group_ids: Optional list of Monday group IDs to filter by.
    """
    if cursor is None:
        cursor_arg = ""
    else:
        cursor_arg = f', cursor: "{cursor}"'

    # Add group filter via query_params if specified
    query_params = ""
    if group_ids and not cursor:
        rules = ", ".join(f'{{column_id: "group", compare_value: ["{gid}"]}}' for gid in group_ids)
        query_params = f', query_params: {{rules: [{rules}]}}'

    return (
        "{ boards(ids: [" + str(board_id) + "]) {"
        " items_page(limit: " + str(PAGE_LIMIT) + cursor_arg + query_params + ") {"
        " cursor items { id name created_at updated_at group { title }"
        " column_values { id text value"
        " ... on MirrorValue { display_value }"
        " ... on FormulaValue { display_value }"
        " ... on BoardRelationValue { display_value linked_item_ids }"
        " ... on StatusValue { updated_at }"
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


def _submitted_date(cols: dict[str, dict], col_id: str, delivery_status: str | None) -> str | None:
    """Extract updated_at date from the status column when delivery_status is 'Submitted'.

    Monday.com StatusValue exposes an updated_at timestamp that records
    when the status was last changed — giving us the exact submission date.
    """
    if delivery_status != "Submitted" or not col_id:
        return None
    cv = cols.get(col_id)
    if cv is None:
        return None
    updated_at = cv.get("updated_at")
    if not updated_at:
        return None
    try:
        return updated_at[:10]  # YYYY-MM-DD
    except (TypeError, IndexError):
        return None


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

    # Normalize empty strings to None
    if url is not None and url.strip() == "":
        url = None
    if display_text is not None and display_text.strip() == "":
        display_text = None

    # Fallback: text field may contain "display - url" format
    if not url:
        text = cv.get("text")
        if text and text.strip():
            display_text = text.strip()

    return url, display_text
