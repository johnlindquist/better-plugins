"""Centralized stdout control-JSON contract for human-md hooks.

The Codex hook stdout channel accepts exactly ONE JSON object per invocation.
`suppressOutput` is ONLY valid on UserPromptSubmit; emitting it on any other
event triggers an 'unsupported suppressOutput' error. Keep all stdout writes
routed through these helpers so the contract is honored in one place.
"""
import json
import sys


def emit(continue_=True, suppress_output=None):
    """Write exactly one control-JSON line to stdout.

    suppress_output must be None for every event except UserPromptSubmit.
    """
    payload = {"continue": bool(continue_)}
    if suppress_output is not None:
        payload["suppressOutput"] = bool(suppress_output)
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def emit_continue():
    """Safe default for non-UserPromptSubmit events."""
    emit(continue_=True, suppress_output=None)
