#!/usr/bin/env python3
"""human-view Codex hook.

Wired to UserPromptSubmit / PostToolUse / Stop. It is deliberately fast:
read stdin, mutate a small per-session state.json, make sure the loopback
daemon is running, open the cmux browser pane exactly once, then print the
required `{"continue": true}` and exit. The browser page polls the daemon, so
no cmux interaction is needed after the pane is opened.

Slice 1 derives a unique-but-deterministic design (hue + glyph + title) from a
hash of the first prompt. No nested `codex exec` is used, so there is no hook
recursion to guard against yet.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# hook_control.py and redaction.py are siblings in this hooks/ directory. When a
# script is run directly its directory is normally sys.path[0], but insert it
# explicitly so the imports work no matter how Codex launches the hook.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_control import emit_control  # noqa: E402
from redaction import redact_text  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DAEMON = PLUGIN_ROOT / "daemon" / "human_view_daemon.py"
SHAPES = ["spark", "orbit", "stack", "wave", "hex", "bolt"]
MAX_EVENTS = 40
# A session only gets a human view once a UserPromptSubmit prompt contains this
# trigger token. After that, the session stays active (later prompts/tools/stops
# keep updating the view) until the session ends. We use "#human" (not "$human")
# so the host agent doesn't try to resolve it as a "$"-prefixed skill reference.
TRIGGER = os.environ.get("HUMAN_VIEW_TRIGGER", "#human")


CURRENT_EVENT = "UserPromptSubmit"
EMITTED_CONTROL = False


def safe_emit(event: str | None) -> None:
    """Emit the hook's control JSON exactly once, never raising.

    Uses the shared hook_control contract: `suppressOutput` is only valid on
    UserPromptSubmit hooks ("suppressOutput field is only supported on
    UserPromptSubmit hooks"), so it is omitted for every other event. Guarded so
    a crash-path emit can never double-print or block Codex.
    """
    global EMITTED_CONTROL
    if EMITTED_CONTROL:
        return
    try:
        emit_control(event)
    except Exception:
        pass
    EMITTED_CONTROL = True


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def debug(msg: str) -> None:
    try:
        log = Path(os.environ.get("HUMAN_VIEW_LOG", Path.home() / ".codex/logs/human-view.jsonl"))
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now_iso(), "msg": msg}) + "\n")
    except Exception:
        pass


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def state_dir_for(session_id: str) -> Path:
    base = os.environ.get("PLUGIN_DATA") or os.environ.get("HUMAN_VIEW_DATA")
    if base:
        root = Path(base).expanduser()
    else:
        root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "human-view"
    return root / "sessions" / (session_id or "default")


def strip_trigger(text: str) -> str:
    """Remove the trigger token so it never shows up in the title/prompt."""
    return re.sub(r"\s*" + re.escape(TRIGGER) + r"\s*", " ", text or "").strip()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def derive_title(prompt: str) -> str:
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    if not text:
        return "Codex task"
    words = text.split(" ")
    title = " ".join(words[:8])
    if len(title) > 64:
        title = title[:61].rstrip() + "…"
    return title[0].upper() + title[1:]


def derive_design(prompt: str) -> dict[str, Any]:
    h = hashlib.sha256((prompt or "task").encode("utf-8")).digest()
    hue = h[0] / 255 * 360
    shape = SHAPES[h[1] % len(SHAPES)]
    return {"hue": round(hue, 1), "shape": shape}


def append_event(state: dict[str, Any], kind: str, what: str) -> None:
    events = state.setdefault("events", [])
    events.append({"kind": kind, "what": what, "ts": now_iso()})
    if len(events) > MAX_EVENTS:
        del events[:-MAX_EVENTS]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def ensure_daemon(state_dir: Path) -> dict[str, Any]:
    meta_path = state_dir / "meta.json"
    meta = load_json(meta_path, {})
    pid = meta.get("pid")
    if pid and pid_alive(int(pid)) and meta.get("port"):
        return meta

    # Spawn detached daemon; it (re)writes meta.json with port/token/pid.
    subprocess.Popen(
        [sys.executable, str(DAEMON), "--state-dir", str(state_dir),
         "--idle-timeout", os.environ.get("HUMAN_VIEW_IDLE_TIMEOUT", "1800")],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, start_new_session=True,
    )
    # Wait briefly for the daemon to publish its port.
    for _ in range(50):  # up to ~5s
        meta = load_json(meta_path, {})
        if meta.get("port") and meta.get("pid") and pid_alive(int(meta["pid"])):
            return meta
        time.sleep(0.1)
    debug("daemon did not publish port in time")
    return meta


def open_pane(meta: dict[str, Any], state_dir: Path) -> None:
    """Open the cmux browser pane exactly once per session."""
    meta_path = state_dir / "meta.json"
    if meta.get("surface_opened"):
        return
    ws = os.environ.get("CMUX_WORKSPACE_ID", "")
    port, token = meta.get("port"), meta.get("token")
    if not (port and token):
        return
    url = f"http://127.0.0.1:{port}/?token={token}"
    args = ["cmux", "new-pane"]
    if ws:
        args += ["--workspace", ws]
    args += ["--type", "browser", "--direction", "right", "--url", url, "--focus", "false", "--json"]
    try:
        res = subprocess.run(args, text=True, capture_output=True, timeout=10)
        surface = ""
        try:
            out = json.loads(res.stdout or "{}")
            surface = out.get("surface_ref") or out.get("surface") or ""
            if not surface and isinstance(out.get("surfaces"), list) and out["surfaces"]:
                surface = out["surfaces"][0]
        except Exception:
            m = re.search(r"surface:\d+", res.stdout or "")
            surface = m.group(0) if m else ""
        meta = load_json(meta_path, meta)
        meta["surface_opened"] = True
        meta["surface_ref"] = surface
        meta["url"] = url
        write_json(meta_path, meta)
        debug(f"opened pane surface={surface!r} rc={res.returncode} out={res.stdout[:200]!r} err={res.stderr[:200]!r}")
    except Exception as e:
        debug(f"open_pane failed: {e!r}")


def update_state(state: dict[str, Any], event: str, payload: dict[str, Any]) -> dict[str, Any]:
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at"] = now_iso()

    if event == "UserPromptSubmit":
        prompt = redact_text(strip_trigger(payload.get("prompt", "") or ""), 1200)
        if not state.get("title"):
            state["title"] = derive_title(prompt)
            state["design"] = derive_design(prompt)
        state["phase"] = "Working"
        state["prompt"] = prompt
        state["status_detail"] = "New task received — getting to work"
        state.pop("summary", None)
        append_event(state, "prompt", (prompt[:90] or "(empty)"))
    elif event == "PostToolUse":
        # Field names not yet confirmed; defensively probe several.
        tool = (payload.get("tool_name") or payload.get("tool")
                or (payload.get("tool_use") or {}).get("name") or "tool")
        state["phase"] = "Working"
        state["status_detail"] = f"Used {tool}"
        append_event(state, "tool", str(tool)[:90])
    elif event == "Stop":
        state["phase"] = "Turn complete"
        msg = redact_text(payload.get("last_assistant_message", "") or "", 1500)
        if msg:
            state["summary"] = msg
        state["status_detail"] = "Ready for the next turn"
        append_event(state, "done", "turn complete")
    else:
        state["status_detail"] = f"Event: {event}"
        append_event(state, "event", event)
    return state


def main() -> int:
    global CURRENT_EVENT
    payload = read_payload()
    event = payload.get("hook_event_name", "UserPromptSubmit")
    CURRENT_EVENT = event
    session_id = payload.get("session_id", "") or "default"

    try:
        sd = state_dir_for(session_id)
        active_flag = sd / "active"
        triggered = event == "UserPromptSubmit" and TRIGGER in (payload.get("prompt", "") or "")

        # Gate: stay completely dormant until a prompt opts in with the trigger
        # token. Once a session is active, keep updating on every later event.
        if not active_flag.exists() and not triggered:
            safe_emit(event)
            return 0

        sd.mkdir(parents=True, exist_ok=True)
        if triggered and not active_flag.exists():
            active_flag.write_text("1", encoding="utf-8")
        debug(f"event={event} session={session_id} triggered={triggered} keys={sorted(payload.keys())}")

        state_path = sd / "state.json"
        state = load_json(state_path, {"revision": 0})
        state = update_state(state, event, payload)
        write_json(state_path, state)

        meta = ensure_daemon(sd)
        if event == "UserPromptSubmit":
            open_pane(meta, sd)
    except Exception as e:
        debug(f"hook error: {e!r}")

    safe_emit(event)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:  # noqa: BLE001 - never let a hook block Codex
        if isinstance(exc, SystemExit):
            raise
        try:
            debug(f"fatal hook error: {exc!r}")
        except Exception:
            pass
        safe_emit(CURRENT_EVENT)
        raise SystemExit(0)
