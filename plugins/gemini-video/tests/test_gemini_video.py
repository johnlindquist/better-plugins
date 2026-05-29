#!/usr/bin/env python3
"""Standalone, hermetic tests for the gemini-video plugin.

Runs the hook as a subprocess (as Codex would) and STUBS the Gemini call via
GEMINI_VIDEO_RUNNER, so there is no network, no real upload, and no API key
needed. Verifies: dormant without trigger, activation with #gemini-video, mp4
detection, atomic-claim idempotency, the detached worker producing a summary,
and the per-event suppressOutput contract.

Run:  python3 tests/test_gemini_video.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "gemini_video_hook.py"


def run_hook(payload, env):
    p = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        text=True, capture_output=True, env=env,
    )
    return p.stdout.strip()


def base_env(data, runner):
    e = os.environ.copy()
    e["GEMINI_VIDEO_DATA"] = str(data)
    e["GEMINI_VIDEO_RUNNER"] = runner  # fake: emit a known summary, no Gemini
    return e


def main():
    fails = 0

    def check(n, c):
        nonlocal fails
        print(("PASS: " if c else "FAIL: ") + n)
        if not c:
            fails += 1

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        vid = Path(tmp) / "clip.mp4"
        vid.write_bytes(b"\x00\x00\x00\x18ftyp")  # fake mp4 bytes
        runner = 'echo "FAKE SUMMARY for {video}"'

        # A) no trigger -> dormant, nothing created
        out = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "a",
                        "prompt": f"look at {vid}"}, base_env(data, runner))
        check("no-trigger emits continue", '"continue":true' in out)
        check("no-trigger creates nothing", not (data / "sessions" / "a").exists())

        # B) trigger + mp4 -> activate, claim, detached worker -> summary
        out = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "b",
                        "prompt": f"#gemini-video summarize {vid}"}, base_env(data, runner))
        check("trigger emits continue+suppress",
              '"continue":true' in out and '"suppressOutput":true' in out)
        check("session active flag", (data / "sessions" / "b" / "active").exists())

        jobs = data / "jobs"
        ok = False
        for _ in range(50):
            done = list(jobs.glob("*/status")) if jobs.exists() else []
            if done and all(p.read_text().strip() == "done" for p in done):
                ok = True
                break
            time.sleep(0.1)
        check("worker produced a job", jobs.exists() and any(jobs.iterdir()))
        check("worker reached done", ok)
        sms = list(jobs.glob("*/summary.md"))
        check("summary written", bool(sms) and "FAKE SUMMARY" in sms[0].read_text())

        # C) re-fire same path -> no second job (idempotent atomic claim)
        n_before = len(list(jobs.iterdir()))
        run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "b",
                  "prompt": f"#gemini-video summarize {vid}"}, base_env(data, runner))
        time.sleep(0.3)
        check("idempotent: no new job", len(list(jobs.iterdir())) == n_before)

        # C2) PostToolUse detects an mp4 in tool_input (session already active)
        vid2 = Path(tmp) / "second.mp4"
        vid2.write_bytes(b"\x00\x00\x00\x18ftyp")
        run_hook({"hook_event_name": "PostToolUse", "session_id": "b",
                  "tool_name": "shell", "tool_input": {"command": f"cp x {vid2}"}},
                 base_env(data, runner))
        found2 = False
        for _ in range(50):
            if any("second.mp4" in (read_meta(p)) for p in jobs.glob("*/meta.json")):
                found2 = True
                break
            time.sleep(0.1)
        check("PostToolUse detects mp4 in tool_input", found2)

        # D) per-event contract: PostToolUse/Stop must NOT carry suppressOutput
        for ev in ("PostToolUse", "Stop"):
            payload = {"hook_event_name": ev, "session_id": "b"}
            if ev == "PostToolUse":
                payload["tool_name"] = "shell"
                payload["tool_input"] = {"command": "ls"}
            if ev == "Stop":
                payload["last_assistant_message"] = "done"
            o = run_hook(payload, base_env(data, runner))
            check(f"{ev} continue", '"continue":true' in o)
            check(f"{ev} no suppressOutput", "suppressOutput" not in o)

        # E) future/unknown event still emits a valid control line, no suppress
        o = run_hook({"hook_event_name": "SomethingFuture", "session_id": "b"},
                     base_env(data, runner))
        check("unknown event continue", '"continue":true' in o)
        check("unknown event no suppressOutput", "suppressOutput" not in o)

    print(f"\n{'OK' if fails == 0 else 'FAILED'}: {fails} failure(s)")
    return 1 if fails else 0


def read_meta(p):
    try:
        return p.read_text()
    except Exception:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
