#!/usr/bin/env python3
"""gemini-video hook entrypoint.

Fast, crash-safe, contract-correct. On every event it: parses stdin JSON, gates
on the ``#gemini-video`` opt-in trigger (sticky per session), detects existing
.mp4 paths, atomically claims each new (path,size,mtime) job, and spawns a
DETACHED worker that runs the Gemini summarizer. The hook itself only does O(1)
filesystem work and always emits exactly one control-JSON line, never blocking
Codex.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_control import emit_control  # noqa: E402
from redaction import redact_text  # noqa: E402
from detector import (  # noqa: E402
    resolve_all,
    extract_from_tool_input,
)
from ledger import (  # noqa: E402
    data_root,
    session_dir,
    jobs_dir,
    job_dir_for,
    claim_job,
    write_json,
    write_status,
    ledger_add,
    stop_summary_line,
)

TRIGGER = os.environ.get("GEMINI_VIDEO_TRIGGER", "#gemini-video")
CURRENT_EVENT = "UserPromptSubmit"


def _job_key(rp: str) -> str:
    st = os.stat(rp)
    raw = f"{rp}:{st.st_size}:{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def main() -> int:
    global CURRENT_EVENT
    raw = sys.stdin.read() or "{}"
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    event = payload.get("hook_event_name") or "UserPromptSubmit"
    CURRENT_EVENT = event
    sid = payload.get("session_id") or "default"
    cwd = payload.get("cwd") or os.getcwd()
    data = data_root()
    sdir = session_dir(data, sid)
    active = (sdir / "active").exists()

    if event == "UserPromptSubmit":
        prompt = payload.get("prompt") or ""
        if TRIGGER in prompt:
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "active").touch()
            active = True
            prompt = prompt.replace(TRIGGER, "").strip()
        if active:
            for rp in resolve_all(prompt, cwd):
                _maybe_start(data, sdir, sid, rp)
        return 0

    # Non-UserPromptSubmit events do nothing unless the session opted in.
    if not active:
        return 0

    if event == "PostToolUse":
        for rp in extract_from_tool_input(payload.get("tool_input"), cwd):
            _maybe_start(data, sdir, sid, rp)
        return 0

    if event == "Stop":
        # Conservative: write a discoverable, redacted session summary file
        # rather than printing to the control-JSON stdout channel. User-facing
        # Stop text is later polish until the stdout-for-Stop contract is pinned.
        try:
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "last-summary.txt").write_text(
                redact_text(stop_summary_line(data, sdir))
            )
        except Exception:
            pass
        return 0

    return 0


def _maybe_start(data, sdir, sid, rp):
    key = _job_key(rp)
    if not claim_job(jobs_dir(data), key):
        return  # already claimed by an earlier hook firing -> idempotent
    jdir = job_dir_for(data, key)
    st = os.stat(rp)
    write_json(jdir / "meta.json", {
        "path": rp, "size": st.st_size, "mtime": st.st_mtime_ns, "session": sid,
    })
    write_status(jdir, "queued")
    ledger_add(sdir, key, rp)
    _spawn_worker(jdir, rp)


def _spawn_worker(jdir, rp):
    plugin_root = Path(__file__).resolve().parent.parent
    worker = plugin_root / "worker" / "worker.py"
    with open(Path(jdir) / "worker.log", "ab", buffering=0) as log:
        subprocess.Popen(
            [sys.executable, str(worker), str(jdir), rp],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True, close_fds=True, env=os.environ.copy(),
        )
    # return immediately; never wait()


def _run():
    try:
        main()
    except BaseException:
        pass
    finally:
        emit_control(CURRENT_EVENT)
        raise SystemExit(0)


if __name__ == "__main__":
    _run()
