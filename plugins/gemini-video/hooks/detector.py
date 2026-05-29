"""Detect .mp4 paths in hook payloads and resolve them to existing files.

The detector is deliberately permissive about how a path is written (absolute,
relative, ``~``, ``$VAR``, quoted-with-spaces) but strict about what it acts on:
only existing regular ``.mp4`` files, resolved against the hook's ``cwd``.
"""
from __future__ import annotations

import json
import os
import re

# Match quoted or bare tokens that end in .mp4 (case-insensitive).
# Handles: absolute, relative, ~, $VAR, quoted-with-spaces.
_MP4_RE = re.compile(r'(?:"([^"]+\.mp4)"|\'([^\']+\.mp4)\'|([^\s"\']+\.mp4))', re.IGNORECASE)


def extract_mp4_candidates(text):
    """Return raw .mp4 path tokens found anywhere in ``text``."""
    if not text:
        return []
    out = []
    for m in _MP4_RE.finditer(str(text)):
        cand = m.group(1) or m.group(2) or m.group(3)
        if cand:
            out.append(cand)
    return out


def resolve_existing(cand, cwd):
    """Resolve a candidate token to a real, existing .mp4 file or ``None``."""
    p = os.path.expanduser(os.path.expandvars(cand))
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    try:
        rp = os.path.realpath(p)
        if os.path.isfile(rp) and rp.lower().endswith(".mp4"):
            return rp
    except OSError:
        return None
    return None


def _candidate_variants(cand):
    """A candidate plus fallbacks. A quoted match inside a JSON/command blob can
    capture a whole command string (e.g. ``cp x /a/b.mp4``); the trailing
    whitespace token recovers the real path. Real quoted-with-spaces filenames
    (``My Demo.mp4``) resolve from the full form first, so they still win."""
    variants = [cand]
    s = cand.strip()
    if s != cand:
        variants.append(s)
    if " " in s or "\t" in s:
        variants.append(s.split()[-1])
    return variants


def resolve_all(text, cwd):
    """All existing .mp4 files referenced anywhere in ``text``, deduped, order-stable."""
    seen = {}
    for cand in extract_mp4_candidates(text):
        for v in _candidate_variants(cand):
            rp = resolve_existing(v, cwd)
            if rp:
                seen[rp] = True
                break
    return list(seen)


def extract_from_tool_input(tool_input, cwd):
    """Extract existing .mp4 paths from a tool_input (str or dict), deduped."""
    if tool_input is None:
        return []
    blob = tool_input if isinstance(tool_input, str) else json.dumps(tool_input)
    return resolve_all(blob, cwd)
