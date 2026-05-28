#!/usr/bin/env python3
"""human-view daemon.

A tiny loopback HTTP server that backs a single Codex session's "human view".
It serves:

  GET /                -> the human view HTML (token injected)
  GET /state.json      -> the current state JSON (token-gated)
  GET /healthz         -> "ok" (no token)

Why a daemon instead of file://?
  A verified experiment proved a cmux browser pane (WKWebView) pointed at a
  file:// page does NOT auto-refresh when the file changes on disk, and
  WKWebView blocks file:// -> sibling file:// fetch() (opaque origin / CORS).
  Serving over http://127.0.0.1 lets the page poll /state.json and live-update
  with zero cmux interaction after the pane is first opened.

Lifecycle:
  - Binds 127.0.0.1:<ephemeral>. Writes {port, pid, token} to meta.json.
  - Idle self-timeout: exits if no HTTP request for --idle-timeout seconds, so
    abandoned sessions don't leak processes.
  - Single instance per session is enforced by the hook (it checks meta.json's
    pid/port before spawning).
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", required=True, help="Per-session state directory")
    ap.add_argument("--idle-timeout", type=float, default=1800.0,
                    help="Exit after this many seconds with no requests")
    args = ap.parse_args()

    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    meta_path = state_dir / "meta.json"
    state_path = state_dir / "state.json"
    index_path = Path(__file__).resolve().parent.parent / "assets" / "index.html"
    index_html = index_path.read_text(encoding="utf-8")

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    token = meta.get("token") or os.urandom(16).hex()

    last_activity = [time.time()]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # silence default logging
            pass

        def _query(self):
            return parse_qs(urlparse(self.path).query)

        def _send(self, code, body: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def do_GET(self):
            last_activity[0] = time.time()
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = index_html.replace("__TOKEN__", token).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path == "/healthz":
                self._send(200, b"ok", "text/plain; charset=utf-8")
                return
            if path == "/state.json":
                if self._query().get("token", [""])[0] != token:
                    self._send(403, b'{"error":"forbidden"}', "application/json")
                    return
                try:
                    body = state_path.read_bytes()
                except FileNotFoundError:
                    body = b'{"revision":0,"phase":"Starting"}'
                self._send(200, body, "application/json")
                return
            self._send(404, b'{"error":"not found"}', "application/json")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]

    meta.update({"port": port, "token": token, "pid": os.getpid()})
    tmp = meta_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta), encoding="utf-8")
    tmp.replace(meta_path)

    def watchdog():
        while True:
            time.sleep(15)
            if time.time() - last_activity[0] > args.idle_timeout:
                os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
