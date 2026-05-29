#!/usr/bin/env node
"use strict";

// Independent, per-plugin loopback dashboard for toolsmith.
//
// Spawned detached by the capture hook (never inline) once a session is enabled
// with the #toolsmith trigger. Serves AGGREGATE-ONLY state on 127.0.0.1 behind
// a token: counts and top-N names, never raw prompts, commands, paths, or tool
// input. The browser page polls /state.json. Idle self-exit keeps it cheap.

const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const crypto = require("crypto");
const childProcess = require("child_process");

const PLUGIN_NAME = "toolsmith";
const DEFAULT_IDLE_TIMEOUT_MS = 30 * 60 * 1000;

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : null;
}

function nowIso() {
  return new Date().toISOString();
}

function mkdirp(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_) {
    return fallback;
  }
}

function writeJsonAtomic(filePath, value) {
  mkdirp(path.dirname(filePath));
  const tmp = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tmp, filePath);
  try {
    fs.chmodSync(filePath, 0o600);
  } catch (_) {}
}

function homeFallback() {
  const home = os.homedir();
  return home
    ? path.join(home, ".codex", "plugin-data", PLUGIN_NAME)
    : path.join(os.tmpdir(), PLUGIN_NAME);
}

function toPairs(map, limit) {
  return Object.entries(map || {})
    .map(([name, count]) => ({ name, count: Number(count) || 0 }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, limit);
}

const dataRoot = path.resolve(
  argValue("--data-root") ||
    process.env.PLUGIN_DATA ||
    process.env.CLAUDE_PLUGIN_DATA ||
    homeFallback()
);
const dashboardDir = path.join(dataRoot, "dashboard");
const metaPath = path.join(dashboardDir, "meta.json");
const liveIndexPath = path.join(dataRoot, "indexes", "live-index.json");
const htmlPath = path.join(__dirname, "..", "assets", "dashboard.html");
const idleTimeoutMs =
  Number.parseInt(process.env.TOOLSMITH_DASHBOARD_IDLE_TIMEOUT_MS || "", 10) ||
  DEFAULT_IDLE_TIMEOUT_MS;

mkdirp(dashboardDir);
let meta = readJson(metaPath, {});
const token = meta.token || crypto.randomBytes(16).toString("hex");
let lastActivity = Date.now();

function publicState() {
  const index = readJson(liveIndexPath, {});
  const redaction = index.redaction || {};
  return {
    schema_version: 1,
    plugin: PLUGIN_NAME,
    generated_at: nowIso(),
    source_generated_at: index.generated_at || null,
    warning:
      "Live aggregate dashboard. Raw prompt text and raw tool input are intentionally not served.",
    totals: {
      records: Number(index.records || 0),
      prompt_records: Number(index.prompt_records || 0),
      tool_records: Number(index.tool_records || 0),
      unique_tool_inputs: Number(index.unique_tool_inputs || 0),
      duplicate_tool_input_calls: Number(index.duplicate_tool_input_calls || 0),
    },
    redaction: {
      redacted_count: Number(redaction.redacted_count || 0),
      truncated_count: Number(redaction.truncated_count || 0),
    },
    top_tools: toPairs(index.top_tools, 12),
    top_projects: toPairs(index.top_projects, 8),
  };
}

function send(res, status, body, contentType) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(String(body), "utf8");
  res.writeHead(status, {
    "Content-Type": contentType,
    "Content-Length": bytes.length,
    "Cache-Control": "no-store, max-age=0",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy":
      "default-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
  });
  res.end(bytes);
}

function maybeOpenCmux(port) {
  if (process.env.TOOLSMITH_DASHBOARD_OPEN_BROWSER === "0") return;
  meta = readJson(metaPath, meta);
  if (meta.surface_opened) return;
  const workspace = process.env.CMUX_WORKSPACE_ID || "";
  const url = `http://127.0.0.1:${port}/?token=${encodeURIComponent(token)}`;
  const args = ["new-pane"];
  if (workspace) args.push("--workspace", workspace);
  args.push("--type", "browser", "--direction", "right", "--url", url, "--focus", "false", "--json");
  let stdout = "";
  let stderr = "";
  try {
    const child = childProcess.spawn("cmux", args, {
      stdio: ["ignore", "pipe", "pipe"],
      detached: false,
    });
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", () => {});
    child.on("close", (code) => {
      const next = readJson(metaPath, meta);
      next.open_attempted_at = nowIso();
      next.open_exit_code = code;
      next.open_stderr_preview = stderr.slice(0, 300);
      if (code === 0) {
        next.surface_opened = true;
        try {
          const parsed = JSON.parse(stdout || "{}");
          next.surface_ref = parsed.surface_ref || parsed.surface || "";
          if (!next.surface_ref && Array.isArray(parsed.surfaces) && parsed.surfaces.length) {
            next.surface_ref = parsed.surfaces[0];
          }
        } catch (_) {}
      }
      writeJsonAtomic(metaPath, next);
    });
  } catch (_) {}
}

const server = http.createServer((req, res) => {
  lastActivity = Date.now();
  const parsed = new URL(req.url || "/", "http://127.0.0.1");
  const pathname = parsed.pathname;
  if (pathname === "/healthz") {
    send(res, 200, "ok", "text/plain; charset=utf-8");
    return;
  }
  if (pathname === "/" || pathname === "/index.html") {
    let html = "";
    try {
      html = fs.readFileSync(htmlPath, "utf8").split("__TOKEN__").join(token);
    } catch (_) {
      html = "<!doctype html><title>Toolsmith Live</title><p>dashboard.html missing</p>";
    }
    send(res, 200, html, "text/html; charset=utf-8");
    return;
  }
  if (pathname === "/state.json") {
    if (parsed.searchParams.get("token") !== token) {
      send(res, 403, JSON.stringify({ error: "forbidden" }), "application/json");
      return;
    }
    send(res, 200, JSON.stringify(publicState()), "application/json");
    return;
  }
  send(res, 404, JSON.stringify({ error: "not found" }), "application/json");
});

server.listen(0, "127.0.0.1", () => {
  const port = server.address().port;
  const url = `http://127.0.0.1:${port}/?token=${encodeURIComponent(token)}`;
  meta = {
    ...readJson(metaPath, {}),
    schema_version: 1,
    plugin: PLUGIN_NAME,
    pid: process.pid,
    port,
    token,
    url,
    updated_at: nowIso(),
  };
  writeJsonAtomic(metaPath, meta);
  maybeOpenCmux(port);
});

setInterval(() => {
  if (Date.now() - lastActivity > idleTimeoutMs) process.exit(0);
}, 15_000).unref();
