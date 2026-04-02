# Manager Dashboard — Design Document

## Purpose

The Manager tab provides intelligent oversight of the RBD awards delivery pipeline. It surfaces issues that require attention, provides AI-powered recommendations, and enables conversational exploration — all in a single screen.

The dashboard does not replace Monday.com as the operational system. Monday remains the source of truth for status, assignments, and dates. The dashboard reads Monday's data and alert columns, enriches them with AI reasoning, and presents a management-focused view.

## Design Principles

### 1. Leverage existing platforms

Monday.com formula columns are the rules engine. The board already computes Date Alerts, Writer Alerts, Metrics Alerts, and Asset Alerts using formulas that encode business logic (e.g., "Target Date Issue" when the target finish date exceeds the close date). The dashboard reads these computed values — it does not duplicate the logic.

A Google Sheet defines management rules as natural-language guidance for Claude to interpret. This keeps rules editable by the manager without code changes, and allows nuanced, contextual guidance that a rigid rules engine cannot express.

### 2. Dynamic blocks over chat-first

Research (Artium.AI user testing) found that chat-first interfaces "dominate the entire experience so much that people wouldn't look elsewhere on the screen." The dashboard uses **dynamic blocks** — AI-enhanced visual cards that proactively surface insights — as the primary interface. Chat is available as a sidebar for deeper exploration, but is never the primary interaction mode.

### 3. Progressive disclosure (three levels)

Based on IEEE VIS 2024 research on dashboard design:

- **Level 1 — Summary cards**: Alert count, KPIs, AI-generated priorities. The manager sees at a glance whether anything needs attention.
- **Level 2 — Expandable panels**: Pipeline status breakdown, writer workload, pattern analysis. Provides context without overwhelming.
- **Level 3 — Full analytical canvas**: Complete submission table plus conversational chat. Used for deep exploration and management meetings.

### 4. Provisional content

AI-generated recommendations are displayed with visual distinction (reduced opacity, AI badge, "provisional" label) to signal they are machine-generated and require human judgement. A "Noted" action brings the insight to full visual weight, acknowledging the manager has reviewed it.

### 5. Data provenance

Every AI recommendation includes a **reasoning chain** — the specific data signals that led to the conclusion. Not "at risk" but "flagged because target date is 3 days past close date AND writer has 4 other active items due this week." This enables the manager to assess recommendation quality and builds trust.

### 6. CO-STAR prompting

Analysis prompts follow the CO-STAR framework:

- **Context**: Lifecycle table + action rules from Google Sheet + all current submission data
- **Objective**: Identify issues, assess urgency, recommend specific actions
- **Style**: Concise, evidence-based — cite the specific data signals
- **Tone**: Professional, direct — a trusted colleague briefing a manager
- **Audience**: Delivery manager who knows the clients and writers but needs AI to spot patterns across 60+ items
- **Response format**: Structured JSON with per-item recommendations, reasoning chains, confidence levels, and overall summary

## Architecture

### Data sources

| Source | What it provides | How it's accessed |
|--------|-----------------|-------------------|
| Monday.com Board 1901925100 | Submission data, delivery status, writer assignments, formula-computed alerts | GraphQL API, paginated fetch, cached in SQLite |
| Monday.com Board 1853068454 | Award metadata (close date, extension date) | Via board relations from submission tracker |
| Google Sheet (rules) | Delivery status lifecycle (sequenced statuses with "ball is with" and lifecycle stage) + action rules (natural language guidance per alert type) | Google Sheets API, cached locally with TTL |
| Claude CLI | Intelligent analysis and conversational follow-up | `claude -p` subprocess using Max subscription |

### Data flow

```
Monday Board 1901925100
  ├── Formula columns (Date/Writer/Metrics/Asset Alerts)
  ├── Delivery Status, Writer, Close Date, Target Date, etc.
  │
  ▼ [Refresh] — GraphQL fetch, paginated, warm-upsert
SQLite cache (delivery_hub.db, cache_submission_items table)
  │
  ├──▶ Dashboard renders data (Jinja2 + HTMX)
  │
  ▼ [Analyse] — constructs prompt with data + rules
Google Sheet rules (lifecycle table + action rules)
  │
  ▼ Combined into system prompt
claude -p (subprocess, Max subscription)
  │
  ▼ Structured JSON analysis
SQLite (cache_ai_insights table)
  │
  ▼ Dashboard re-renders with AI insights inline
  │
  ▼ Chat: claude -p --resume SESSION_ID (streamed via SSE)
```

### LLM integration

All LLM calls go through the `claude` CLI as a subprocess. This uses the Claude Max subscription — no API keys or additional charges.

- **Analysis**: `claude -p "prompt" --output-format json --max-turns 1` — one-shot structured analysis
- **Chat**: `claude -p "message" --resume SESSION_ID --output-format stream-json` — conversational follow-up with full context
- **System prompt**: `--append-system-prompt-file` loads the Google Sheet rules as context

The initial analysis creates a session ID. Subsequent chat messages resume that session, so Claude retains the full analysis context throughout the conversation.

**Why not the Agent SDK?** The Claude Agent SDK (`claude_agent_sdk`) requires separate API billing. The `claude` CLI is the only programmatic interface that uses the Max subscription.

### Google Sheet rules

The rules sheet has two sections:

**Delivery Status Lifecycle** — an ordered list of all delivery statuses with:
- **Ball is with**: Who is responsible at this stage (Co-ordinator, Writer, IT Support, Reviewer, n/a)
- **Lifecycle Stage**: Early, Middle, Late, Finished, Error

This tells Claude where an item is in the delivery process and who should be followed up with.

**Action Rules** — contextual guidance per alert type:
- **Column**: Which alert column (Date Alerts, Writer Alerts, etc.)
- **Issue**: The alert value (Target Date Issue, Target Date Blank, Escalated, etc.)
- **Action Rules**: Natural language guidance designed for Claude to interpret

The action rules are deliberately nuanced — not rigid if/then logic but contextual guidance:
- "Target Date Issue urgency depends on how close the target date is to the Close Date (or Extension Date) as it defines the contingency runway."
- "The required action depends on the delivery status, as it defines who 'has the ball' and requires followup."
- "This is not so much of an issue early in the delivery lifecycle... it is an urgent issue if we don't have one for late in the cycle."

Claude combines the lifecycle stage, who has the ball, the action rules, and the actual item data to produce intelligent, contextual recommendations. This is why it must be an LLM, not a deterministic rules engine.

## UX Design

### Layout

```
┌──────────────────────────────────────────┬────────────────────┐
│  Manager             [Refresh] [Analyse] │  AI Assistant      │
│  Last sync: 2 Apr 2026 07:45             │                    │
├──────────────────────────────────────────┤  Summary and       │
│                                          │  conversation      │
│  ▼ ALERTS (3)                            │  appear here       │
│  Alert cards with inline AI insights     │  after [Analyse]   │
│                                          │                    │
│  ▼ PIPELINE SUMMARY                     │                    │
│  Status counts, writer workload          │  ┌──────────────┐  │
│                                          │  │ Ask me...    │  │
│  ► FULL PIPELINE (68) [collapsed]       │  └──────────────┘  │
└──────────────────────────────────────────┴────────────────────┘
```

### Alert cards

Each alert card shows:
- Alert type badge (Date/Writer/Metrics/Asset) with severity colour
- Submission name, client, award
- Key context: close date, delivery status, assigned writer, ball is with
- Monday.com item link
- After analysis: AI recommendation (provisional), reasoning chain, confidence level

### Pipeline summary

- Horizontal bar or table: count of items at each delivery status
- Writer workload: items per writer, with alert count
- Upcoming deadlines: items closing in 7/14/30 days

### Chat panel

- Right sidebar, always visible
- Receives the AI analysis summary after [Analyse]
- Text input for follow-up questions
- Responses stream in real-time via SSE
- Session persists across messages (full analysis context retained)

## Technical patterns

### Follows existing delivery-automation conventions
- FastAPI routes with Jinja2 templates
- HTMX for partial page updates (hx-post, hx-target, hx-swap)
- SQLite for local caching with warm-upsert pattern
- Pydantic models for data validation
- settings.yaml for board IDs and column mappings
- Collapsible sections matching the judging page pattern

### New patterns introduced
- SSE (Server-Sent Events) for streaming Claude responses to the chat panel
- Google Sheets API integration for reading rules
- Claude CLI subprocess management (session creation, resumption, output parsing)

## Research references

- Artium.AI: Dynamic blocks vs chat-first interfaces — user testing showed chat dominates and suppresses dashboard engagement
- IEEE VIS 2024: Three-tier progressive disclosure for analytical dashboards
- Artium.AI: Provisional content pattern — AI content at 70% opacity until reviewed
- Nielsen Norman Group: 72% of users say AI language impacts trust — provenance and reasoning chains are essential
- CO-STAR prompting framework: Context, Objective, Style, Tone, Audience, Response format
- Monday.com MCP: First-party hosted server at mcp.monday.com, OAuth, no local setup
- Claude Max vs API: CLI uses subscription, Agent SDK requires separate billing

## Future evolution

This design establishes a reusable pattern for management agents:
1. **Sales Manager** in the sales-automation app (same architecture: Monday data + Google Sheet rules + Claude CLI)
2. **Scheduled analysis** via Claude Code triggers (daily morning analysis, email digest)
3. **Historical trending** (track alerts over time in SQLite, show improvement patterns)
4. **Action integration** (e.g., "Draft email to writer" directly from a recommendation)
