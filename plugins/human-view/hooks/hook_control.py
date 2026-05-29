"""Shared hook-output contract for human-view's Python hook.

Codex only accepts the ``suppressOutput`` field on UserPromptSubmit hooks
("suppressOutput field is only supported on UserPromptSubmit hooks"), so it must
be omitted for PostToolUse / Stop / unknown events. Every hook invocation should
emit exactly one control object on stdout.
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def control_for_event(event: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"continue": True}
    if event == "UserPromptSubmit":
        out["suppressOutput"] = True
    return out


def emit_control(event: str | None, stream: TextIO | None = None) -> None:
    target = stream or sys.stdout
    target.write(json.dumps(control_for_event(event), separators=(",", ":")) + "\n")
    target.flush()
