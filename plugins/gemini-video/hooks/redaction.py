"""Best-effort secret redaction for gemini-video.

This masks obvious secret-like substrings before any prompt/summary text is
written to user-visible surfaces. It is intentionally conservative: it is a
leakage reducer, not a security boundary. (Copied verbatim from human-view.)
"""
from __future__ import annotations

import re

ENV_SECRET_RE = re.compile(r"\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)[A-Z0-9_]*)\s*[=:]\s*(\S+)")
BEARER_RE = re.compile(r"\b(Bearer|Basic)\s+([A-Za-z0-9._\-+/=]+)", re.IGNORECASE)
BASIC_AUTH_URL_RE = re.compile(r"\b(https?://)([^/\s:@]+):([^/\s@]+)@")
SECRET_ASSIGNMENT_RE = re.compile(r"\b(api[_-]?key|access[_-]?token|secret)\b\s*[=:]\s*(\S+)", re.IGNORECASE)


def redact_text(value, max_chars=None):
    if value is None:
        return ""
    text = str(value)
    text = ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    text = BEARER_RE.sub(lambda m: f"{m.group(1)} <redacted>", text)
    text = BASIC_AUTH_URL_RE.sub(lambda m: f"{m.group(1)}<redacted>@", text)
    text = SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars] + "…<truncated>"
    return text


if __name__ == "__main__":
    pass
