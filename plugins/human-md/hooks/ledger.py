"""Per-session state ledger for human-md.

Each hook invocation sees only its own event. To render a coherent HUMAN.md we
persist a small JSON ledger per session under $PLUGIN_DATA and append events to
it. The renderer reads the whole ledger.

Layout under data root:
  <data>/sessions/<session_id>/ledger.json   # accumulated state
  <data>/sessions/<session_id>/opened        # sentinel: cmux pane opened once
  <data>/sessions/<session_id>/active        # sentinel: #md opt-in is sticky
  <data>/sessions/<session_id>/mdpath        # absolute path to the HUMAN.md we own
"""
import json
import os
import time


def _data_root():
    # PLUGIN_DATA is provided by Codex; fall back to a temp-ish path for tests.
    root = os.environ.get("PLUGIN_DATA") or os.environ.get("CODEX_PLUGIN_DATA")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".codex", "human-md-data")
    return root


def session_dir(session_id):
    """Path to the session dir. Does NOT create it (read paths must stay
    side-effect free so a non-opted-in session leaves no trace)."""
    sid = session_id or "unknown-session"
    return os.path.join(_data_root(), "sessions", sid)


def _ensure_session_dir(session_id):
    d = session_dir(session_id)
    os.makedirs(d, exist_ok=True)
    return d


def _ledger_path(session_id):
    return os.path.join(session_dir(session_id), "ledger.json")


def load(session_id):
    p = _ledger_path(session_id)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"session_id": session_id, "events": [], "phase": "idle", "prompt": None,
                "last_assistant_message": None}


def save(session_id, state):
    _ensure_session_dir(session_id)
    p = _ledger_path(session_id)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def flag_path(session_id, name):
    return os.path.join(session_dir(session_id), name)


def has_flag(session_id, name):
    return os.path.exists(flag_path(session_id, name))


def set_flag(session_id, name, value="1"):
    _ensure_session_dir(session_id)
    with open(flag_path(session_id, name), "w", encoding="utf-8") as f:
        f.write(str(value))


def read_flag(session_id, name):
    try:
        with open(flag_path(session_id, name), "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
