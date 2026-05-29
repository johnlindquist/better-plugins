"""Render the per-session ledger into HUMAN.md content.

The first line is a managed marker so we can detect files we own and avoid
clobbering a user's own HUMAN.md.
"""
from redaction import redact_text

MANAGED_MARKER = "<!-- human-md:managed v1 -->"


def _heading(state):
    prompt = state.get("prompt") or "(waiting for prompt)"
    first_line = prompt.strip().splitlines()[0] if prompt.strip() else "(waiting for prompt)"
    return first_line[:80]


def render(state):
    lines = []
    lines.append(MANAGED_MARKER)
    lines.append("")
    lines.append(f"# {redact_text(_heading(state))}")
    lines.append("")

    # Status
    lines.append("## Status")
    lines.append("")
    lines.append(f"- Phase: **{state.get('phase', 'idle')}**")
    lines.append(f"- Session: `{state.get('session_id', 'unknown')}`")
    lines.append(f"- Updated: {state.get('updated', '')}")
    lines.append("")

    # Current prompt
    lines.append("## Current Prompt")
    lines.append("")
    prompt = state.get("prompt")
    if prompt:
        lines.append("> " + redact_text(prompt).replace("\n", "\n> "))
    else:
        lines.append("_None yet._")
    lines.append("")

    # Recent tool calls
    lines.append("## Recent Tool Calls")
    lines.append("")
    tools = [e for e in state.get("events", []) if e.get("kind") == "tool"]
    if tools:
        for e in tools[-10:]:
            name = redact_text(str(e.get("tool", "?")))
            summary = redact_text(str(e.get("summary", "")))
            ts = e.get("ts", "")
            if summary:
                lines.append(f"- `{name}` — {summary} ({ts})")
            else:
                lines.append(f"- `{name}` ({ts})")
    else:
        lines.append("_No tool calls yet._")
    lines.append("")

    # Last assistant message
    lines.append("## Last Assistant Message")
    lines.append("")
    msg = state.get("last_assistant_message")
    if msg:
        lines.append(redact_text(msg))
    else:
        lines.append("_None yet._")
    lines.append("")

    # Event log
    lines.append("## Event Log")
    lines.append("")
    for e in state.get("events", [])[-20:]:
        kind = e.get("kind", "?")
        ts = e.get("ts", "")
        what = redact_text(str(e.get("what", e.get("tool", ""))))
        lines.append(f"- [{ts}] {kind}: {what}")
    lines.append("")

    return "\n".join(lines)
