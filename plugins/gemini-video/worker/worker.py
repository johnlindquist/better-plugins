#!/usr/bin/env python3
"""Detached gemini-video worker.

Runs OUT of the hook's process group. Drives a single video job through
status transitions and writes a terminal status no matter what, so a job is
never stuck. The Gemini call is performed by an INJECTABLE runner:

  GEMINI_VIDEO_RUNNER  shell template with {video} and {prompt} placeholders.
                       Empty -> default ``bun ~/lessons/scripts/summarize-one.ts``.

This seam keeps tests hermetic (a fake runner that emits a known summary) while
production reuses the real Bun + @google/genai scripts in ~/lessons.

Default extraction mode is **generic** (works on any video). Override per the
env knobs below:

  GEMINI_VIDEO_MODE         summarize-one.ts --mode (default: generic)
  GEMINI_VIDEO_PROMPT_FILE  if set, passed as --prompt-file (overrides the mode)
  GEMINI_VIDEO_SUMMARIZER   path to summarize-one.ts (default: ~/lessons/scripts/summarize-one.ts)
"""
from __future__ import annotations

import os
import subprocess
import sys


def _set_status(job_dir, s):
    with open(os.path.join(job_dir, "status"), "w") as f:
        f.write(s)


def main():
    if len(sys.argv) < 3:
        return 1
    job_dir, video_path = sys.argv[1], sys.argv[2]

    runner = os.environ.get("GEMINI_VIDEO_RUNNER", "").strip()
    prompt_file = os.environ.get("GEMINI_VIDEO_PROMPT_FILE", "").strip()
    mode = os.environ.get("GEMINI_VIDEO_MODE", "generic").strip() or "generic"
    summarizer = os.environ.get(
        "GEMINI_VIDEO_SUMMARIZER",
        os.path.expanduser("~/lessons/scripts/summarize-one.ts"),
    )
    timeout = int(os.environ.get("GEMINI_VIDEO_JOB_TIMEOUT", "600"))

    if runner:
        rendered = runner.replace("{video}", video_path).replace("{prompt}", prompt_file)
        cmd = ["/bin/sh", "-c", rendered]
    else:
        cmd = ["bun", summarizer, video_path, "--mode", mode]
        if prompt_file:
            cmd += ["--prompt-file", prompt_file]

    _set_status(job_dir, "uploading")
    try:
        _set_status(job_dir, "summarizing")
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=os.path.expanduser("~/lessons"),
        )
        if out.returncode == 0:
            with open(os.path.join(job_dir, "summary.md"), "w") as f:
                f.write(out.stdout)
            _set_status(job_dir, "done")
        else:
            with open(os.path.join(job_dir, "error.txt"), "w") as f:
                f.write((out.stderr or "")[-4000:])
            _set_status(job_dir, "error")
    except subprocess.TimeoutExpired:
        with open(os.path.join(job_dir, "error.txt"), "w") as f:
            f.write(f"timeout after {timeout}s")
        _set_status(job_dir, "error")
    except Exception as e:  # fail closed, always terminal
        with open(os.path.join(job_dir, "error.txt"), "w") as f:
            f.write(repr(e))
        _set_status(job_dir, "error")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
