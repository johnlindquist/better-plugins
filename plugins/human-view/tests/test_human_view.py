#!/usr/bin/env python3
"""Standalone tests for the human-view plugin.

Runs the hook as a subprocess (as Codex would), then verifies the gating
behavior and the loopback daemon's HTTP loop. No cmux required — the cmux pane
open is best-effort and skipped when CMUX_WORKSPACE_ID is absent.

Run:  python3 tests/test_human_view.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK = PLUGIN_ROOT / "hooks" / "human_view_hook.py"


def run_hook(payload: dict, data_dir: Path) -> str:
    env = os.environ.copy()
    env["HUMAN_VIEW_DATA"] = str(data_dir)
    env["HUMAN_VIEW_IDLE_TIMEOUT"] = "60"
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
    )
    return proc.stdout.strip()


def http_json(port: int, token: str) -> dict:
    url = f"http://127.0.0.1:{port}/state.json?token={token}"
    with urllib.request.urlopen(url, timeout=2) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_control(out: str) -> dict:
    text = out.strip()
    assert text.startswith("{") and text.endswith("}"), repr(out)
    return json.loads(text)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS: " if cond else "FAIL: ") + name)
        if not cond:
            failures += 1

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)

        # A) no trigger -> dormant
        out = run_hook({"hook_event_name": "UserPromptSubmit",
                        "session_id": "a", "prompt": "normal request"}, data)
        check("no-trigger emits continue", '"continue":true' in out)
        check("no-trigger creates no session dir", not (data / "sessions" / "a").exists())

        # UserPromptSubmit may carry suppressOutput; PostToolUse/Stop must NOT
        # ("suppressOutput field is only supported on UserPromptSubmit hooks").
        check("UserPromptSubmit output is valid suppressOutput", '"suppressOutput":true' in out or out == '{"continue":true}')

        # B) trigger -> activate + daemon
        out = run_hook({"hook_event_name": "UserPromptSubmit",
                        "session_id": "b", "prompt": "#human build auth middleware"}, data)
        sdir = data / "sessions" / "b"
        check("trigger emits continue", '"continue":true' in out)
        check("trigger creates session dir", sdir.exists())
        check("trigger writes active flag", (sdir / "active").exists())

        meta = json.loads((sdir / "meta.json").read_text())
        port, token = meta["port"], meta["token"]
        st = http_json(port, token)
        check("daemon serves state over http", st.get("revision", 0) >= 1)
        check("title strips trigger token", "#human" not in st.get("title", ""))
        check("title is the real request", "auth" in st.get("title", "").lower())

        rev1 = st["revision"]
        post_out = run_hook({"hook_event_name": "PostToolUse", "session_id": "b", "tool_name": "apply_patch"}, data)
        stop_out = run_hook({"hook_event_name": "Stop", "session_id": "b",
                             "last_assistant_message": "done"}, data)
        # Regression guard for the "unsupported suppressOutput" hook failure.
        check("PostToolUse output omits suppressOutput", "suppressOutput" not in post_out)
        check("Stop output omits suppressOutput", "suppressOutput" not in stop_out)
        check("PostToolUse still emits continue", '"continue":true' in post_out)
        time.sleep(0.2)
        st2 = http_json(port, token)
        check("active session keeps updating", st2["revision"] > rev1)
        check("Stop sets phase + summary", st2.get("phase") == "Turn complete" and bool(st2.get("summary")))

        # C) per-event control-JSON contract: suppressOutput only on UserPromptSubmit.
        for event_name, expect_suppress in [
            ("UserPromptSubmit", True),
            ("PostToolUse", False),
            ("Stop", False),
            ("SomethingFuture", False),
        ]:
            payload = {"hook_event_name": event_name, "session_id": f"contract-{event_name}"}
            if event_name == "UserPromptSubmit":
                payload["prompt"] = "normal request"
            elif event_name == "Stop":
                payload["last_assistant_message"] = "done"
            else:
                payload["tool_name"] = "Bash"
            control = parse_control(run_hook(payload, data))
            check(f"{event_name} emits continue", control.get("continue") is True)
            check(
                f"{event_name} suppressOutput contract",
                ("suppressOutput" in control) is expect_suppress,
            )

        # D) redaction: secrets in prompt/summary must not reach served state.
        run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "secret",
                  "prompt": "#human run with API_KEY=should-not-leak"}, data)
        secret_meta = json.loads((data / "sessions" / "secret" / "meta.json").read_text())
        secret_state = http_json(secret_meta["port"], secret_meta["token"])
        check("human-view redacts prompt secrets", "should-not-leak" not in json.dumps(secret_state))
        check("human-view keeps redaction marker", "<redacted>" in json.dumps(secret_state))
        run_hook({"hook_event_name": "Stop", "session_id": "secret",
                  "last_assistant_message": "Finished with Bearer abc.def.ghi"}, data)
        time.sleep(0.2)
        secret_state_2 = http_json(secret_meta["port"], secret_meta["token"])
        check("human-view redacts stop summary secrets", "abc.def.ghi" not in json.dumps(secret_state_2))

        # cleanup: stop daemons
        for stop_meta in (meta, secret_meta):
            try:
                os.kill(int(stop_meta["pid"]), 15)
            except Exception:
                pass

    print(f"\n{'OK' if failures == 0 else 'FAILED'}: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
