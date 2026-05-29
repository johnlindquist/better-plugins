"""State layout, atomic job claiming, and display ledger for gemini-video.

Concurrency primitive: an atomic ``O_CREAT|O_EXCL`` claim file per job dir is the
single source of truth for "who runs the worker". ``ledger.json`` is best-effort
display state only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def data_root() -> Path:
    for env in ("GEMINI_VIDEO_DATA", "PLUGIN_DATA", "CODEX_PLUGIN_DATA"):
        v = os.environ.get(env)
        if v:
            return Path(v)
    codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    return Path(codex_home) / "gemini-video"


def session_dir(data: Path, sid: str) -> Path:
    return Path(data) / "sessions" / sid


def jobs_dir(data: Path) -> Path:
    return Path(data) / "jobs"


def job_dir_for(data: Path, key: str) -> Path:
    return jobs_dir(data) / key


def claim_job(jobs: Path, key: str) -> bool:
    """Atomically claim a job. Returns True only for the single claimer."""
    d = Path(jobs) / key
    d.mkdir(parents=True, exist_ok=True)
    claim = d / "claim"
    try:
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def write_status(jdir: Path, status: str) -> None:
    (Path(jdir) / "status").write_text(status)


def ledger_add(sdir: Path, key: str, path: str) -> None:
    sdir = Path(sdir)
    sdir.mkdir(parents=True, exist_ok=True)
    ledger = sdir / "ledger.json"
    data = read_json(ledger, {}) or {}
    data[key] = {"path": path, "status": "queued"}
    write_json(ledger, data)


def stop_summary_line(data: Path, sdir: Path) -> str:
    ledger = read_json(Path(sdir) / "ledger.json", {}) or {}
    if not ledger:
        return "gemini-video: no videos this session"
    counts = {"done": 0, "error": 0, "running": 0}
    for key in ledger:
        st = job_dir_for(Path(data), key) / "status"
        s = st.read_text().strip() if st.exists() else "queued"
        if s == "done":
            counts["done"] += 1
        elif s == "error":
            counts["error"] += 1
        else:
            counts["running"] += 1
    return (f"gemini-video: {counts['done']} done, "
            f"{counts['running']} running, {counts['error']} error")
