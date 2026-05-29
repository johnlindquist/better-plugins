"""Best-effort secret redaction for human-view state.

human-view writes the current prompt and the assistant's stop summary into the
per-session state.json that the loopback daemon serves to a visible browser
pane. Redact obvious secrets before they reach that surface. This reduces
accidental leakage; it does not prove safety.
"""
from __future__ import annotations

import re

ENV_SECRET_RE = re.compile(
    r"\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|AUTH|COOKIE)[A-Z0-9_]*)=([^\s'\"`]+)",
    re.I,
)
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.I)
BASIC_AUTH_URL_RE = re.compile(r"\b(https?://)([^/\s:@]+):([^/\s@]+)@", re.I)
SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(api[_-]?key|token|secret|password|passwd|authorization|cookie)\s*[:=]\s*([^\s,'\"}]+)",
    re.I,
)


def redact_text(value: str, max_chars: int | None = None) -> str:
    out = value or ""
    out = ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", out)
    out = BEARER_RE.sub("Bearer <redacted>", out)
    out = BASIC_AUTH_URL_RE.sub(lambda m: f"{m.group(1)}<redacted>@", out)
    out = SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=<redacted>", out)
    if max_chars is not None and len(out) > max_chars:
        out = out[:max_chars] + "…<truncated>"
    return out
