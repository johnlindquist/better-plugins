"""Secret redaction for text rendered into HUMAN.md.

HUMAN.md is written to disk in the user's project, so it must be redacted with
at least the same care as the human-view HTTP payload. This is a superset of
human-view's patterns.
"""
import re

_PATTERNS = [
    # OpenAI / generic sk- keys
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    # Anthropic
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    # AWS access key id
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # GitHub tokens
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    # Google API key
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    # Slack token
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    # Bearer tokens
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    # JWT
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    # Private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

_KV = re.compile(
    r"(?i)\b([a-z0-9_\-]*(?:secret|token|password|passwd|api[_\-]?key|access[_\-]?key)[a-z0-9_\-]*)\s*[:=]\s*([^\s,;]+)"
)

_ENV_KEYS = re.compile(r"(?i)(secret|token|password|passwd|api[_\-]?key|access[_\-]?key)")

REDACTED = "[REDACTED]"


def redact_text(text):
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    out = _KV.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
    return out


def redact_value(value):
    """Recursively redact strings inside dict/list/scalar structures."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _ENV_KEYS.search(k) and isinstance(v, str):
                out[k] = REDACTED
            else:
                out[k] = redact_value(v)
        return out
    return value
