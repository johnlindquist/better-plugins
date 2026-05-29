"""Shared per-event hook control-JSON contract for gemini-video.

Codex only accepts ``suppressOutput`` on ``UserPromptSubmit`` hooks; sending it
on any other event makes Codex reject the hook ("unsupported suppressOutput").
This module centralizes that rule so every hook emits exactly one valid control
line for the event it is handling. (Copied verbatim from human-view.)
"""
from __future__ import annotations

import json
import sys


def control_for_event(event_name: str) -> dict:
    out = {"continue": True}
    if str(event_name or "") == "UserPromptSubmit":
        out["suppressOutput"] = True
    return out


def emit_control(event_name: str, stream=None) -> None:
    target = stream if stream is not None else sys.stdout
    target.write(json.dumps(control_for_event(event_name), separators=(",", ":")) + "\n")
    try:
        target.flush()
    except Exception:
        pass


if __name__ == "__main__":
    pass
