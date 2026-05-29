#!/usr/bin/env python3
"""Hermetic tests for human-md. No real cmux, no network.

We inject a fake cmux via HUMAN_MD_CMUX pointing at a shim that records argv
and cwd to a file, mirroring gemini-video's injectable-runner seam.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "human_md_hook.py")

failures = []


def check(name, cond):
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}")
        failures.append(name)


def run_event(event, env):
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(event),
        capture_output=True, text=True, env=env,
    )
    return proc


def base_env(tmp, cmux_log):
    env = dict(os.environ)
    env["PLUGIN_DATA"] = os.path.join(tmp, "data")
    return env


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def wait_cmux(path, expect, timeout=4.0):
    """The hook opens cmux via a DETACHED process, so the shim writes its log
    line asynchronously. Poll until at least `expect` lines exist."""
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        if os.path.exists(path):
            last = [ln for ln in read(path).splitlines() if ln.strip()]
            if len(last) >= expect:
                return last
        time.sleep(0.05)
    return last


def main():
    tmp = tempfile.mkdtemp()
    proj = os.path.join(tmp, "proj")
    os.makedirs(proj)
    cmux_log = os.path.join(tmp, "cmux.log")
    env = base_env(tmp, cmux_log)
    md = os.path.join(proj, "HUMAN.md")

    # Fake cmux: a shell shim that records cwd + args to cmux_log.
    shim = os.path.join(tmp, "cmux")
    with open(shim, "w") as f:
        f.write("#!/bin/sh\n")
        f.write(f'printf "%s\\t%s\\n" "$(pwd)" "$*" >> "{cmux_log}"\n')
    os.chmod(shim, 0o755)
    env["HUMAN_MD_CMUX"] = shim

    # 1) No trigger: invisible, no files, continue
    p = run_event({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": proj, "prompt": "hello world"}, env)
    out = json.loads(p.stdout)
    check("no-trigger emits continue", out.get("continue") is True)
    check("no-trigger creates no session dir",
          not os.path.exists(os.path.join(env["PLUGIN_DATA"], "sessions", "s1")))
    check("no-trigger writes no HUMAN.md", not os.path.exists(md))

    # 2) Trigger: opt-in, writes HUMAN.md, opens cmux once, suppresses output
    p = run_event({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": proj, "prompt": "#md build auth middleware"}, env)
    out = json.loads(p.stdout)
    check("trigger emits continue+suppress",
          out.get("continue") is True and out.get("suppressOutput") is True)
    check("trigger creates active flag",
          os.path.exists(os.path.join(env["PLUGIN_DATA"], "sessions", "s1", "active")))
    check("trigger writes HUMAN.md", os.path.exists(md))
    body = read(md)
    check("HUMAN.md has managed marker", "human-md:managed" in body)
    check("HUMAN.md has status section", "## Status" in body)
    check("HUMAN.md has prompt section", "## Current Prompt" in body)
    check("HUMAN.md strips trigger token", "#md" not in body)
    log = wait_cmux(cmux_log, 1)
    check("cmux markdown open invoked once", len(log) == 1)
    check("cmux argv is markdown open HUMAN.md", log and log[0].split("\t")[1] == "markdown open HUMAN.md")
    check("cmux cwd is project cwd", log and log[0].split("\t")[0] == os.path.realpath(proj))

    # 3) PostToolUse: updates file, no reopen, continue only (no suppressOutput)
    p = run_event({"hook_event_name": "PostToolUse", "session_id": "s1",
                   "cwd": proj, "tool_name": "Bash",
                   "tool_input": {"command": "pytest -q"}}, env)
    out = json.loads(p.stdout)
    check("PostToolUse emits continue", out.get("continue") is True)
    check("PostToolUse omits suppressOutput", "suppressOutput" not in out)
    body = read(md)
    check("PostToolUse updates HUMAN.md", "pytest -q" in body)
    log = wait_cmux(cmux_log, 1)
    check("PostToolUse does not reopen cmux", len(log) == 1)

    # 4) Sticky: a normal prompt (no #md) still updates because session is active
    p = run_event({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": proj, "prompt": "now add tests"}, env)
    out = json.loads(p.stdout)
    body = read(md)
    check("sticky session updates without trigger", "now add tests" in body)
    check("sticky UserPromptSubmit still suppresses output", out.get("suppressOutput") is True)
    log = wait_cmux(cmux_log, 1)
    check("sticky UserPromptSubmit does not reopen cmux", len(log) == 1)

    # 5) Stop: phase done, records assistant message, continue only
    p = run_event({"hook_event_name": "Stop", "session_id": "s1",
                   "cwd": proj, "last_assistant_message": "All tests pass."}, env)
    out = json.loads(p.stdout)
    check("Stop emits continue", out.get("continue") is True)
    check("Stop omits suppressOutput", "suppressOutput" not in out)
    body = read(md)
    check("Stop updates phase and assistant message",
          "done" in body and "All tests pass." in body)
    log = wait_cmux(cmux_log, 1)
    check("Stop does not reopen cmux", len(log) == 1)

    # 6) Redaction across surfaces (same cwd: s2 reuses managed HUMAN.md)
    run_event({"hook_event_name": "UserPromptSubmit", "session_id": "s2",
               "cwd": proj, "prompt": "#md use sk-abcdef012345678901234567890"}, env)
    check("redacts prompt secrets", "sk-abcdef012345678901234567890" not in read(md))
    run_event({"hook_event_name": "PostToolUse", "session_id": "s2",
               "cwd": proj, "tool_name": "Bash",
               "tool_input": {"command": "export TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123"}}, env)
    check("redacts tool secrets", "ghp_abcdefghijklmnopqrstuvwxyz0123" not in read(md))
    run_event({"hook_event_name": "Stop", "session_id": "s2",
               "cwd": proj, "last_assistant_message": "key sk-zzzzzzzzzzzzzzzzzzzzzzzz done"}, env)
    check("redacts stop summary secrets", "sk-zzzzzzzzzzzzzzzzzzzzzzzz" not in read(md))

    # 7) Do not clobber a user-owned HUMAN.md
    proj2 = os.path.join(tmp, "proj2")
    os.makedirs(proj2)
    user_md = os.path.join(proj2, "HUMAN.md")
    with open(user_md, "w") as f:
        f.write("# My own notes\nDo not touch.\n")
    run_event({"hook_event_name": "UserPromptSubmit", "session_id": "s3",
               "cwd": proj2, "prompt": "#md start"}, env)
    check("does not clobber user HUMAN.md", read(user_md) == "# My own notes\nDo not touch.\n")
    fallback = os.path.join(proj2, "HUMAN.session.md")
    check("uses fallback markdown path", os.path.exists(fallback))
    # s1 + s2 (both opened HUMAN.md) + s3 (fallback) = 3 expected open calls.
    log = wait_cmux(cmux_log, 3)
    check("opens fallback markdown path",
          any(line.split("\t")[1] == "markdown open HUMAN.session.md" for line in log))

    # 8) Contract sweep: suppressOutput only on UserPromptSubmit
    for ev, sid in (("UserPromptSubmit", "s4"), ("PostToolUse", "s4"), ("Stop", "s4"),
                    ("SomethingFuture", "s4")):
        if ev == "UserPromptSubmit":
            e = {"hook_event_name": ev, "session_id": sid, "cwd": proj, "prompt": "#md go"}
        else:
            e = {"hook_event_name": ev, "session_id": sid, "cwd": proj,
                 "tool_name": "Bash", "tool_input": {"command": "ls"},
                 "last_assistant_message": "hi"}
        p = run_event(e, env)
        out = json.loads(p.stdout)
        check(f"{ev} emits continue", out.get("continue") is True)
        if ev == "UserPromptSubmit":
            check(f"{ev} suppressOutput contract", out.get("suppressOutput") is True)
        else:
            check(f"{ev} suppressOutput contract", "suppressOutput" not in out)

    print(f"\nOK: {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
