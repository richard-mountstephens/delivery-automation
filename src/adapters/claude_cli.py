"""Claude API adapter — uses Anthropic SDK for delivery analysis."""

import json
import logging
import os
import time

from anthropic import Anthropic, RateLimitError

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8192
MAX_RETRIES = 3
RETRY_DELAY = 60

DATA_TOOLS = [
    {
        "name": "get_all_active_submissions",
        "description": (
            "Fetch the full active submissions dataset from the delivery board. "
            "Use this when the user's question goes beyond the alert data already provided — "
            "for example: questions about all submissions (not just those with alerts), "
            "target finish dates, delivery status breakdowns, writer workload across all items, "
            "award/category counts, or any question where the alert data alone is insufficient. "
            "Do NOT call this for questions that can be answered from the alert data already in your context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason why alert data is insufficient for this question",
                },
            },
            "required": ["reason"],
        },
    },
]


def _get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Get your key from https://console.anthropic.com/"
        )
    return Anthropic(api_key=api_key)


def analyse(alerts: list[dict], rules_context: str) -> dict:
    """Run AI analysis on alert items using the Anthropic API.

    Returns dict with 'summary', 'insights' (list), and 'patterns'.
    """
    alerts_json = json.dumps(alerts, indent=2, default=str)

    prompt = f"""You are the RBD Delivery Manager AI. Analyse these active delivery alerts and brief the manager.

ALERTS DATA ({len(alerts)} items with active alerts from Monday.com):
{alerts_json}

Produce a JSON response with this structure:
{{
  "summary": "The briefing text — see OUTPUT FORMAT below",
  "patterns": ["short pattern description"],
  "insights": [
    {{
      "monday_id": "item id",
      "recommendation": "specific action (1 sentence)",
      "reasoning_chain": "data signals behind this",
      "confidence": "High/Medium/Low",
      "severity": "URGENT/WARNING/INFO"
    }}
  ]
}}

OUTPUT FORMAT for the summary field — follow this template exactly:

**Priority Actions**
1. **[Action verb] [item/person]** — [specific instruction]. [Key date/reason].
2. **[Action verb] [item/person]** — [specific instruction]. [Key date/reason].
(max 5 priorities. Bold the action + target. One line each.)

**Date Alerts**
- [Item name] ([writer]) — [status], closes [date]. [What's wrong and what to do].
- [Grouped: "X items have [issue]: [names]"]
(skip section if none)

**Writer & Reviewer**
- [Item name] ([writer]) — [alert type]. [What to do].
- [Grouped: "X items need writer due dates: [names]"]
(skip section if none)

**Assets & Metrics**
- [Item name] ([writer]) — [alert type], closes [date]. [What to do].
- [Grouped: "X items have [issue]: [names]"]
(skip section if none)

**Capacity**
- [Writer name] has [N] active items, [M] with alerts. [Risk assessment].
(skip section if no capacity concerns)

RULES:
- Use **bold** for section headings and action targets within numbered items.
- Use bullet points (- ) for items within each section.
- Group similar issues rather than listing individually (e.g. "6 items need writer due dates set: AFG CRM, AFG Broker, ...").
- Always cite specific names, dates, and counts.
- Skip sections with no alerts.
- Keep total briefing under 300 words.
- IMPORTANT: Each item includes pre-calculated business_days_to_close, business_days_to_target, and business_days_to_extension. These account for weekends and Australian public holidays. Always use these numbers when describing urgency — say "X business days" not "X days". For example, Apr 7 from Apr 2 is 1 business day (Easter weekend Apr 3-6), not 5 days."""

    try:
        client = _get_client()
    except RuntimeError as e:
        logger.error(str(e))
        return {"error": str(e), "insights": [], "summary": ""}

    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=rules_context,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except RateLimitError:
            if attempt == MAX_RETRIES - 1:
                logger.error("Rate limited after %d retries", MAX_RETRIES)
                return {"error": "Rate limited — try again shortly", "insights": [], "summary": ""}
            wait_time = RETRY_DELAY * (attempt + 1)
            logger.warning("Rate limited. Waiting %ds before retry %d/%d", wait_time, attempt + 2, MAX_RETRIES)
            time.sleep(wait_time)
        except Exception as e:
            logger.error("Anthropic API error: %s", e)
            return {"error": str(e), "insights": [], "summary": ""}

    result_text = ""
    for block in response.content:
        if block.type == "text":
            result_text = block.text
            break

    analysis = _extract_json(result_text)
    if analysis:
        return analysis

    return {"summary": result_text, "insights": []}


# Session-keyed chat histories (in-memory, single-server)
_chat_sessions: dict[str, list[dict]] = {}
_MAX_SESSIONS = 20
_MAX_MESSAGES_PER_SESSION = 50


def _call_api(client, system_prompt, messages, tools=None):
    """Call the Claude API with retry logic. Returns the response."""
    kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS, system=system_prompt, messages=messages)
    if tools:
        kwargs["tools"] = tools

    for attempt in range(MAX_RETRIES):
        try:
            return client.messages.create(**kwargs)
        except RateLimitError:
            if attempt == MAX_RETRIES - 1:
                raise
            wait_time = RETRY_DELAY * (attempt + 1)
            logger.warning("Rate limited. Waiting %ds before retry %d/%d", wait_time, attempt + 2, MAX_RETRIES)
            time.sleep(wait_time)


def chat_sync(
    message: str,
    alert_context: str = "",
    rules_context: str = "",
    fetch_full_data: callable = None,
    session_id: str = "default",
) -> dict:
    """Send a chat message with delivery alert data context.

    Alert data is always included. If Claude needs broader data, it calls the
    get_all_active_submissions tool and fetch_full_data() provides it.

    Returns dict with 'text'.
    """
    # Evict oldest session if at capacity
    if session_id not in _chat_sessions and len(_chat_sessions) >= _MAX_SESSIONS:
        oldest = next(iter(_chat_sessions))
        del _chat_sessions[oldest]
    history = _chat_sessions.setdefault(session_id, [])
    while len(history) > _MAX_MESSAGES_PER_SESSION:
        history.pop(0)

    system_prompt = f"""You are the RBD Delivery Manager AI Assistant. You help the delivery manager understand their active submission alerts, writer workloads, deadlines, and delivery pipeline.

MANAGEMENT RULES AND GUIDANCE:
{rules_context}

CURRENT ALERT DATA (submissions with active alerts only):
{alert_context}

The alert data above covers submissions that have date, writer, metrics, or asset alerts. If the user asks about ALL active submissions (e.g. target finish dates, delivery status breakdowns, total counts, writer workload beyond alert items), use the get_all_active_submissions tool to fetch the full dataset.

Keep responses concise and actionable. Use markdown formatting (bold, lists, tables) for clarity. Always cite specific names, dates, and counts from the data. Use "business days" when discussing deadlines (the data includes pre-calculated business days that exclude weekends and Australian public holidays)."""

    try:
        client = _get_client()
    except RuntimeError as e:
        return {"text": str(e)}

    history.append({"role": "user", "content": message})

    try:
        response = _call_api(client, system_prompt, history, tools=DATA_TOOLS if fetch_full_data else None)
    except RateLimitError:
        history.pop()
        return {"text": "Rate limited — try again shortly."}
    except Exception as e:
        history.pop()
        return {"text": f"Error: {e}"}

    # Check if Claude wants the full submissions data
    tool_use_block = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "get_all_active_submissions":
            tool_use_block = block
            break

    if tool_use_block and fetch_full_data:
        logger.info("Chat requested full submissions data: %s", tool_use_block.input.get("reason", ""))

        # Add Claude's tool_use response to history
        history.append({"role": "assistant", "content": response.content})

        # Fetch full data and send as tool_result
        full_data = fetch_full_data()
        history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_block.id,
                "content": full_data,
            }],
        })

        # Get Claude's final answer with the full data
        try:
            response = _call_api(client, system_prompt, history)
        except Exception as e:
            return {"text": f"Error: {e}"}

    # Extract text from final response
    result_text = ""
    for block in response.content:
        if block.type == "text":
            result_text += block.text

    history.append({"role": "assistant", "content": result_text})
    return {"text": result_text}


def reset_chat(session_id: str = "default") -> None:
    """Clear chat history for a session."""
    _chat_sessions.pop(session_id, None)


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from text that may contain markdown fences or prose."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        try:
            return json.loads(text[start:end])
        except (json.JSONDecodeError, ValueError):
            pass

    for i, ch in enumerate(text):
        if ch == "{":
            depth = 0
            for j in range(i, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[i : j + 1])
                        except json.JSONDecodeError:
                            break
            break

    return None
