#!/usr/bin/env python3
"""human-md: maintain HUMAN.md and open it once in a cmux markdown pane.

Reads one Codex hook event on stdin, updates a per-session ledger, writes
HUMAN.md (redacted) into the session cwd, and on first opt-in opens it with
`cmux markdown open HUMAN.md`. cmux's own watcher refreshes the pane on
subsequent writes, so there is no daemon and no polling.

Opt-in: the user includes `#md` in a prompt. Opt-in is sticky per session via
a flag file, mirroring human-view.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hook_control
import ledger
import redaction
import render_md

TRIGGER = "#md"
MANAGED_MARKER = render_md.MANAGED_MARKER
FALLBACK_NAME = "HUMAN.session.md"


def _read_event():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


def _strip_trigger(text):
    if not text:
        return text, False
    had = TRIGGER in text
    # Remove the token but keep the rest of the prompt readable.
    cleaned = text.replace(TRIGGER, "").strip()
    return cleaned, had


def _target_md_path(cwd, session_id):
    """Pick a HUMAN.md path we are allowed to own without clobbering the user."""
    primary = os.path.join(cwd, "HUMAN.md")
    # Remember a decision once so the same file is reused for the session.
    remembered = ledger.read_flag(session_id, "mdpath")
    if remembered:
        return remembered
    chosen = primary
    if os.path.exists(primary):
        try:
            with open(primary, "r", encoding="utf-8") as f:
                head = f.read(200)
        except OSError:
            head = ""
        if MANAGED_MARKER not in head:
            # User already owns HUMAN.md; use a side file instead.
            chosen = os.path.join(cwd, FALLBACK_NAME)
    ledger.set_flag(session_id, "mdpath", chosen)
    return chosen


def _open_cmux(md_path, cwd):
    """Open the markdown pane once. Never pollute our stdout."""
    cmux_cmd = os.environ.get("HUMAN_MD_CMUX", "cmux")
    rel = os.path.basename(md_path)
    argv = [cmux_cmd, "markdown", "open", rel]
    try:
        subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except (OSError, ValueError):
        return False


def _summarize_tool(tool_input):
    if isinstance(tool_input, dict):
        for key in ("command", "path", "file_path", "pattern", "query", "url"):
            if key in tool_input and isinstance(tool_input[key], str):
                return tool_input[key][:120]
        try:
            return json.dumps(tool_input)[:120]
        except (TypeError, ValueError):
            return ""
    if isinstance(tool_input, str):
        return tool_input[:120]
    return ""


def _write_md(session_id, state, cwd):
    state["updated"] = ledger.now_str()
    md_path = _target_md_path(cwd, session_id)
    content = render_md.render(state)
    tmp = md_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, md_path)
    return md_path


def handle_user_prompt_submit(event):
    session_id = event.get("session_id")
    cwd = event.get("cwd") or os.getcwd()
    raw_prompt = event.get("prompt") or ""
    cleaned, had_trigger = _strip_trigger(raw_prompt)

    active = ledger.has_flag(session_id, "active")
    if not (had_trigger or active):
        # Not our session; stay invisible.
        hook_control.emit(continue_=True, suppress_output=False)
        return

    if had_trigger and not active:
        ledger.set_flag(session_id, "active", "1")
        active = True

    state = ledger.load(session_id)
    state["session_id"] = session_id
    state["phase"] = "working"
    state["prompt"] = redaction.redact_text(cleaned)
    state.setdefault("events", []).append(
        {"kind": "prompt", "what": redaction.redact_text(cleaned)[:120],
         "turn_id": event.get("turn_id"), "ts": ledger.now_str()}
    )
    ledger.save(session_id, state)

    md_path = _write_md(session_id, state, cwd)

    if not ledger.has_flag(session_id, "opened"):
        if _open_cmux(md_path, cwd):
            ledger.set_flag(session_id, "opened", "1")

    # suppressOutput is valid ONLY here (UserPromptSubmit).
    hook_control.emit(continue_=True, suppress_output=True)


def handle_refresh(event, phase):
    session_id = event.get("session_id")
    cwd = event.get("cwd") or os.getcwd()

    if not ledger.has_flag(session_id, "active"):
        hook_control.emit_continue()
        return

    state = ledger.load(session_id)
    state["session_id"] = session_id
    state["phase"] = phase

    if event.get("hook_event_name") == "PostToolUse":
        tool = event.get("tool_name") or event.get("tool") or "?"
        summary = _summarize_tool(event.get("tool_input"))
        state.setdefault("events", []).append(
            {"kind": "tool", "tool": redaction.redact_text(str(tool)),
             "summary": redaction.redact_text(summary), "ts": ledger.now_str()}
        )

    msg = event.get("last_assistant_message")
    if msg:
        state["last_assistant_message"] = redaction.redact_text(msg)
        state.setdefault("events", []).append(
            {"kind": "assistant", "what": "assistant message", "ts": ledger.now_str()}
        )

    ledger.save(session_id, state)
    _write_md(session_id, state, cwd)

    # NEVER emit suppressOutput on non-UserPromptSubmit events.
    hook_control.emit_continue()


def main():
    event = _read_event()
    name = event.get("hook_event_name") or ""
    if name == "UserPromptSubmit":
        handle_user_prompt_submit(event)
    elif name == "PostToolUse":
        handle_refresh(event, phase="working")
    elif name == "Stop":
        handle_refresh(event, phase="done")
    else:
        # Unknown/future events: honor the contract, do nothing visible.
        hook_control.emit_continue()


if __name__ == "__main__":
    main()
