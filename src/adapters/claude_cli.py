"""Claude CLI subprocess wrapper — uses Max subscription via `claude -p`."""

import json
import logging
import subprocess
import tempfile
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

CLAUDE_CMD = "claude"


def analyse(alerts: list[dict], rules_context: str) -> dict:
    """Run AI analysis on alert items using Claude CLI.

    Returns dict with 'summary', 'insights' (list), and 'session_id'.
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

    # Write rules to temp file for --append-system-prompt-file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(rules_context)
        rules_file = f.name

    try:
        result = subprocess.run(
            [
                CLAUDE_CMD, "-p", prompt,
                "--append-system-prompt-file", rules_file,
                "--output-format", "json",
                "--max-turns", "1",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        logger.error("claude CLI not found — is Claude Code installed?")
        return {"error": "Claude CLI not found", "insights": [], "summary": ""}
    except subprocess.TimeoutExpired:
        logger.error("Claude analysis timed out after 120s")
        return {"error": "Analysis timed out", "insights": [], "summary": ""}

    if result.returncode != 0:
        logger.error("Claude CLI error: %s", result.stderr)
        return {"error": result.stderr.strip(), "insights": [], "summary": ""}

    # Parse the JSON output from claude CLI
    try:
        output = json.loads(result.stdout)
        # claude -p --output-format json wraps in {result, session_id, ...}
        session_id = output.get("session_id")
        result_text = output.get("result", "")

        # The result text contains our JSON — extract it
        analysis = _extract_json(result_text)
        if analysis:
            analysis["session_id"] = session_id
            return analysis

        # If no structured JSON, return the raw text as summary
        return {
            "summary": result_text,
            "insights": [],
            "session_id": session_id,
        }

    except json.JSONDecodeError:
        logger.warning("Could not parse Claude output as JSON")
        return {
            "summary": result.stdout.strip(),
            "insights": [],
            "session_id": None,
        }


def chat_sync(message: str, session_id: str | None = None) -> dict:
    """Send a chat message to Claude, optionally resuming a session.

    Returns dict with 'text' and 'session_id'.
    """
    cmd = [CLAUDE_CMD, "-p", message, "--output-format", "json"]
    if session_id:
        cmd.extend(["--resume", session_id])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return {"text": "Error: Claude CLI not found", "session_id": session_id}
    except subprocess.TimeoutExpired:
        return {"text": "Error: Response timed out", "session_id": session_id}

    if result.returncode != 0:
        return {"text": f"Error: {result.stderr.strip()}", "session_id": session_id}

    try:
        output = json.loads(result.stdout)
        return {
            "text": output.get("result", ""),
            "session_id": output.get("session_id", session_id),
        }
    except json.JSONDecodeError:
        return {"text": result.stdout.strip(), "session_id": session_id}


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from text that may contain markdown fences or prose."""
    # Try the whole thing first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find JSON within markdown fences
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        try:
            return json.loads(text[start:end])
        except (json.JSONDecodeError, ValueError):
            pass

    # Try to find a JSON object in the text
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
