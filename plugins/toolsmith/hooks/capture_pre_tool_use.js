#!/usr/bin/env node
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");

const PLUGIN_NAME = "toolsmith";
const PLUGIN_VERSION = "0.1.0";
const HOOK_NAME = "capture_pre_tool_use";
const EVENT_SCHEMA_VERSION = 2;
const MAX_STDIN_BYTES = intEnv("BETTER_TOOLS_MAX_STDIN_BYTES", 4 * 1024 * 1024);
const MAX_RECORD_BYTES = intEnv("BETTER_TOOLS_MAX_RECORD_BYTES", 256 * 1024);
const MAX_INDEX_PATTERNS = intEnv("BETTER_TOOLS_MAX_INDEX_PATTERNS", 2000);
const MAX_INDEX_INPUT_HASHES = intEnv("BETTER_TOOLS_MAX_INDEX_INPUT_HASHES", 10000);
const MAX_PROMPT_EXCERPT_CHARS = intEnv("TOOLSMITH_MAX_PROMPT_EXCERPT_CHARS", 800);
const MAX_TOOL_PROMPT_EXCERPT_CHARS = intEnv("TOOLSMITH_MAX_TOOL_PROMPT_EXCERPT_CHARS", 280);
const PROMPT_STATE_TTL_MS = intEnv("TOOLSMITH_PROMPT_STATE_TTL_MS", 6 * 60 * 60 * 1000);
const CAPTURE_PROMPT_TEXT = process.env.TOOLSMITH_CAPTURE_PROMPT_TEXT !== "0";
const RETENTION_DAYS = intEnv("BETTER_TOOLS_RETENTION_DAYS", 90);
const MAX_TOTAL_BYTES = intEnv("BETTER_TOOLS_MAX_BYTES", 250 * 1000 * 1000);
const SECRET_KEY_RE = /(api[_-]?key|token|secret|password|passwd|authorization|bearer|client[_-]?secret|cookie|credential)/i;
const ENV_SECRET_RE = /\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|AUTH|COOKIE)[A-Z0-9_]*)=([^\s'"]+)/gi;
const BEARER_RE = /\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi;
const BASIC_AUTH_URL_RE = /\b(https?:\/\/)([^\/\s:@]+):([^\/\s@]+)@/gi;

function intEnv(name, fallback) {
  const value = Number.parseInt(process.env[name] || "", 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function nowIso() {
  return new Date().toISOString();
}

function dayStamp() {
  return nowIso().slice(0, 10);
}

function mkdirp(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function hashSecretPath(dataRoot) {
  return path.join(dataRoot.root, "state", "hash-secret");
}

function getHashSecret(dataRoot) {
  const file = hashSecretPath(dataRoot);
  try {
    return fs.readFileSync(file);
  } catch (_) {
    mkdirp(path.dirname(file));
    const secret = crypto.randomBytes(32);
    try {
      fs.writeFileSync(file, secret, { mode: 0o600 });
    } catch (_) {
      fs.writeFileSync(file, secret);
    }
    return secret;
  }
}

function localHash(dataRoot, value) {
  return `hmac-sha256:${crypto.createHmac("sha256", getHashSecret(dataRoot)).update(String(value)).digest("hex")}`;
}

function stableJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
}

function stableHomeFallback() {
  const home = os.homedir();
  return home ? path.join(home, ".codex", "plugin-data", PLUGIN_NAME) : null;
}

function resolveDataRoot() {
  if (process.env.PLUGIN_DATA) return { root: process.env.PLUGIN_DATA, source: "PLUGIN_DATA" };
  if (process.env.CLAUDE_PLUGIN_DATA) return { root: process.env.CLAUDE_PLUGIN_DATA, source: "CLAUDE_PLUGIN_DATA" };
  const home = stableHomeFallback();
  if (home) return { root: home, source: "HOME_FALLBACK" };
  return { root: path.join(os.tmpdir(), PLUGIN_NAME), source: "TMP_FALLBACK" };
}

function writeLocator(dataRoot) {
  if (process.env.TOOLSMITH_WRITE_LOCATOR === "0") return;
  const fallback = stableHomeFallback();
  if (!fallback) return;
  try {
    mkdirp(fallback);
    fs.writeFileSync(
      path.join(fallback, "active-data-root.json"),
      `${JSON.stringify({
        schema_version: 1,
        plugin: PLUGIN_NAME,
        data_root: dataRoot.root,
        data_root_source: dataRoot.source,
        updated_at: nowIso(),
      }, null, 2)}\n`,
      "utf8"
    );
  } catch (_) {}
}

function appendJsonl(filePath, value) {
  mkdirp(path.dirname(filePath));
  fs.appendFileSync(filePath, `${JSON.stringify(value)}\n`, "utf8");
}

function readStdinLimited() {
  return new Promise((resolve) => {
    const chunks = [];
    let bytes = 0;
    let truncated = false;
    process.stdin.on("data", (chunk) => {
      bytes += chunk.length;
      if (bytes <= MAX_STDIN_BYTES) {
        chunks.push(chunk);
      } else {
        truncated = true;
      }
    });
    process.stdin.on("end", () => {
      resolve({ raw: Buffer.concat(chunks).toString("utf8"), bytes, truncated });
    });
    process.stdin.on("error", () => {
      resolve({ raw: "", bytes, truncated: true });
    });
  });
}

function redactString(value, counters) {
  let out = value;
  out = out.replace(ENV_SECRET_RE, (_, key) => {
    counters.redacted_count += 1;
    return `${key}=<redacted>`;
  });
  out = out.replace(BEARER_RE, () => {
    counters.redacted_count += 1;
    return "Bearer <redacted>";
  });
  out = out.replace(BASIC_AUTH_URL_RE, (_, scheme) => {
    counters.redacted_count += 1;
    return `${scheme}<redacted>@`;
  });
  if (out.length > 16 * 1024) {
    counters.truncated_count += 1;
    out = `${out.slice(0, 16 * 1024)}...<truncated>`;
  }
  return out;
}

function redact(value, counters, depth = 0) {
  if (depth > 8) {
    counters.truncated_count += 1;
    return "<max-depth>";
  }
  if (typeof value === "string") return redactString(value, counters);
  if (value === null || typeof value === "number" || typeof value === "boolean") return value;
  if (Array.isArray(value)) {
    const items = value.slice(0, 100).map((item) => redact(item, counters, depth + 1));
    if (value.length > 100) {
      counters.truncated_count += 1;
      items.push(`...<${value.length - 100} more items>`);
    }
    return items;
  }
  if (typeof value === "object") {
    const out = {};
    const entries = Object.entries(value);
    for (const [key, item] of entries.slice(0, 100)) {
      if (SECRET_KEY_RE.test(key)) {
        counters.redacted_count += 1;
        out[key] = "<redacted>";
      } else {
        out[key] = redact(item, counters, depth + 1);
      }
    }
    if (entries.length > 100) {
      counters.truncated_count += 1;
      out.__truncated_keys__ = entries.length - 100;
    }
    return out;
  }
  return String(value);
}

function classifyTool(toolName) {
  if (toolName === "Bash") return "bash";
  if (toolName === "apply_patch" || toolName === "Edit" || toolName === "Write") return "file_edit";
  if (toolName && toolName.startsWith("mcp__")) return "mcp";
  return "unknown";
}

function findGitRoot(cwd) {
  if (!cwd || typeof cwd !== "string") return null;
  let current = path.resolve(cwd);
  for (let i = 0; i < 24; i += 1) {
    try {
      if (fs.existsSync(path.join(current, ".git"))) return current;
    } catch (_) {
      return null;
    }
    const parent = path.dirname(current);
    if (parent === current) return null;
    current = parent;
  }
  return null;
}

function commandFromInput(input) {
  if (!input || typeof input !== "object") return null;
  return typeof input.command === "string" ? input.command : typeof input.cmd === "string" ? input.cmd : null;
}

function normalizeCommand(command) {
  return command
    .replace(/"[^"]*"/g, '"<str>"')
    .replace(/'[^']*'/g, "'<str>'")
    .replace(/\/Users\/[^ ]+/g, "/<path>")
    .replace(/\b[0-9a-f]{7,64}\b/g, "<hash>")
    .replace(/\b\d+\b/g, "<num>")
    .slice(0, 220);
}

function trimRecord(record) {
  if (Buffer.byteLength(JSON.stringify(record), "utf8") <= MAX_RECORD_BYTES) return record;
  record.tool.input_truncated = true;
  record.tool.input = {
    __truncated_record__: true,
    input_hash: record.tool.input_hash,
    input_bytes: record.tool.input_bytes,
  };
  if (record.tool.command && record.tool.command.length > 2048) {
    record.tool.command = `${record.tool.command.slice(0, 2048)}...<truncated>`;
  }
  return record;
}

function promptStatePath(dataRoot) {
  return path.join(dataRoot.root, "state", "recent-prompts.json");
}

function promptKey(sessionId, turnId) {
  return `${sessionId || ""}\u001f${turnId || ""}`;
}

function readPromptState(dataRoot) {
  try {
    return JSON.parse(fs.readFileSync(promptStatePath(dataRoot), "utf8"));
  } catch (_) {
    return { schema_version: 1, prompts: {} };
  }
}

function parseTimeMs(value) {
  const ms = Date.parse(String(value || ""));
  return Number.isFinite(ms) ? ms : null;
}

function promptAgeMs(prompt) {
  const ms = parseTimeMs(prompt && prompt.observed_at);
  return ms ? Date.now() - ms : Number.POSITIVE_INFINITY;
}

function prunePromptState(prompts) {
  const entries = Object.entries(prompts || {})
    .filter(([, prompt]) => promptAgeMs(prompt) <= PROMPT_STATE_TTL_MS)
    .sort((a, b) => String(b[1].observed_at || "").localeCompare(String(a[1].observed_at || "")))
    .slice(0, 200);
  return Object.fromEntries(entries);
}

function writePromptState(dataRoot, state) {
  try {
    const statePath = promptStatePath(dataRoot);
    mkdirp(path.dirname(statePath));
    fs.writeFileSync(
      statePath,
      `${JSON.stringify({ schema_version: EVENT_SCHEMA_VERSION, prompts: prunePromptState(state.prompts || {}) }, null, 2)}\n`,
      "utf8"
    );
  } catch (_) {}
}

function promptFromInput(input) {
  for (const key of ["prompt", "user_prompt", "message"]) {
    if (typeof input[key] === "string") return input[key];
  }
  return "";
}

function buildPromptRecord(input, rawMeta, dataRoot) {
  const counters = { redacted_count: 0, truncated_count: 0 };
  const observedAt = nowIso();
  const rawPrompt = promptFromInput(input);
  const redactedFull = redactString(rawPrompt, counters);
  const promptTruncated = redactedFull.length > MAX_PROMPT_EXCERPT_CHARS;
  const excerpt = CAPTURE_PROMPT_TEXT ? redactedFull.slice(0, MAX_PROMPT_EXCERPT_CHARS) : "";
  const promptHash = rawPrompt ? localHash(dataRoot, rawPrompt) : null;
  const promptId = rawPrompt ? localHash(dataRoot, ["prompt", input.session_id || "", input.turn_id || "", promptHash].join("\u001f")) : null;
  const taskId = localHash(dataRoot, ["task", input.session_id || "", input.turn_id || "", promptHash || observedAt].join("\u001f"));
  const record = {
    schema_version: EVENT_SCHEMA_VERSION,
    kind: "user_prompt",
    observed_at: observedAt,
    plugin: { name: PLUGIN_NAME, version: PLUGIN_VERSION, hook: HOOK_NAME },
    hook: {
      event_name: String(input.hook_event_name || "UserPromptSubmit"),
      permission_mode: input.permission_mode || null,
      model: input.model || null,
    },
    ids: {
      event_id: localHash(dataRoot, [observedAt, input.session_id || "", input.turn_id || "", "user_prompt", promptHash || ""].join("\u001f")),
      session_id: input.session_id || null,
      turn_id: input.turn_id || null,
      tool_use_id: null,
      prompt_id: promptId,
      task_id: taskId,
    },
    task: {
      task_id: taskId,
      prompt_id: promptId,
    },
    prompt: {
      text: excerpt,
      excerpt,
      prompt_hash: promptHash,
      prompt_bytes: Buffer.byteLength(rawPrompt, "utf8"),
      prompt_truncated: promptTruncated,
      capture_mode: CAPTURE_PROMPT_TEXT ? "excerpt" : "hash_only",
      capture_status: rawPrompt ? "captured" : "missing",
      payload_keys: rawPrompt ? undefined : Object.keys(input).slice(0, 50),
    },
    redaction: {
      enabled: true,
      rules_version: "2026-05-21",
      redacted_count: counters.redacted_count,
      truncated_count: counters.truncated_count + (promptTruncated ? 1 : 0),
    },
    capture: {
      data_root_source: dataRoot.source,
      raw_stdin_bytes: rawMeta.bytes,
      parse_status: "ok",
    },
  };
  if (rawPrompt) {
    const state = readPromptState(dataRoot);
    state.prompts = state.prompts || {};
    state.prompts[promptKey(input.session_id || null, input.turn_id || null)] = {
      observed_at: observedAt,
      session_id: input.session_id || null,
      turn_id: input.turn_id || null,
      task_id: taskId,
      prompt_id: promptId,
      prompt_hash: promptHash,
      text: excerpt,
    };
    writePromptState(dataRoot, state);
  }
  return record;
}

function recentPromptFor(input, dataRoot) {
  const state = readPromptState(dataRoot);
  const prompts = prunePromptState(state.prompts || {});
  const sessionId = input.session_id || null;
  const turnId = input.turn_id || null;
  if (sessionId && turnId) {
    const exact = prompts[promptKey(sessionId, turnId)];
    if (exact) {
      return { ...exact, link_method: "same_turn", link_confidence: 1.0, link_age_ms: promptAgeMs(exact) };
    }
  }
  if (!sessionId) return null;
  const recent = Object.values(prompts)
    .filter((prompt) => prompt && prompt.session_id === sessionId)
    .filter((prompt) => promptAgeMs(prompt) <= PROMPT_STATE_TTL_MS)
    .sort((a, b) => String(b.observed_at || "").localeCompare(String(a.observed_at || "")))[0] || null;
  return recent ? { ...recent, link_method: "same_session_recent", link_confidence: 0.55, link_age_ms: promptAgeMs(recent) } : null;
}

function buildToolRecord(input, rawMeta, dataRoot) {
  const counters = { redacted_count: 0, truncated_count: 0 };
  const toolInputRaw = input.tool_input || {};
  const toolInput = redact(toolInputRaw, counters);
  const toolName = String(input.tool_name || "unknown");
  const cwd = typeof input.cwd === "string" ? input.cwd : null;
  const gitRoot = findGitRoot(cwd);
  const projectSource = gitRoot || cwd || "unknown-project";
  const command = commandFromInput(toolInput);
  const rawInputJson = stableJson(toolInputRaw);
  const prompt = recentPromptFor(input, dataRoot);
  return trimRecord({
    schema_version: EVENT_SCHEMA_VERSION,
    kind: "tool_call",
    observed_at: nowIso(),
    plugin: { name: PLUGIN_NAME, version: PLUGIN_VERSION, hook: HOOK_NAME },
    hook: {
      event_name: String(input.hook_event_name || "PreToolUse"),
      permission_mode: input.permission_mode || null,
      model: input.model || null,
    },
    ids: {
      event_id: localHash(dataRoot, [input.session_id || "", input.turn_id || "", input.tool_use_id || "", toolName, rawInputJson].join("\u001f")),
      session_id: input.session_id || null,
      turn_id: input.turn_id || null,
      tool_use_id: input.tool_use_id || null,
      task_id: prompt ? prompt.task_id || null : null,
    },
    project: {
      cwd,
      cwd_hash: cwd ? localHash(dataRoot, cwd) : null,
      git_root: gitRoot,
      git_root_hash: gitRoot ? localHash(dataRoot, gitRoot) : null,
      project_key: localHash(dataRoot, projectSource),
      project_name: path.basename(gitRoot || cwd || "unknown"),
    },
    task: prompt ? {
      task_id: prompt.task_id || null,
      prompt_id: prompt.prompt_id || null,
      prompt_hash: prompt.prompt_hash || null,
      prompt_excerpt: (prompt.text || "").slice(0, MAX_TOOL_PROMPT_EXCERPT_CHARS),
      prompt_observed_at: prompt.observed_at || null,
      link_method: prompt.link_method || null,
      link_confidence: prompt.link_confidence || null,
      link_age_ms: prompt.link_age_ms || null,
    } : null,
    tool: {
      name: toolName,
      family: classifyTool(toolName),
      input: toolInput,
      input_hash: localHash(dataRoot, rawInputJson),
      input_bytes: Buffer.byteLength(rawInputJson, "utf8"),
      input_truncated: rawMeta.truncated,
      command,
      command_hash: command ? localHash(dataRoot, command) : null,
    },
    redaction: {
      enabled: true,
      rules_version: "2026-05-21",
      redacted_count: counters.redacted_count,
      truncated_count: counters.truncated_count,
    },
    capture: {
      data_root_source: dataRoot.source,
      raw_stdin_bytes: rawMeta.bytes,
      parse_status: "ok",
    },
  });
}

function buildRecord(input, rawMeta, dataRoot) {
  const eventName = String(input.hook_event_name || "PreToolUse");
  if (eventName === "UserPromptSubmit") return buildPromptRecord(input, rawMeta, dataRoot);
  return buildToolRecord(input, rawMeta, dataRoot);
}

function buildError(error, rawMeta, dataRoot) {
  const counters = { redacted_count: 0, truncated_count: 0 };
  return {
    schema_version: EVENT_SCHEMA_VERSION,
    kind: "hook_error",
    observed_at: nowIso(),
    plugin: { name: PLUGIN_NAME, version: PLUGIN_VERSION, hook: HOOK_NAME },
    error: {
      type: rawMeta.truncated ? "stdin_too_large_or_parse_error" : "stdin_parse_error",
      message: String(error && error.message ? error.message : error),
      raw_hash: localHash(dataRoot, rawMeta.raw || ""),
      raw_preview: redactString((rawMeta.raw || "").slice(0, 2000), counters),
    },
    capture: {
      data_root_source: dataRoot.source,
      raw_stdin_bytes: rawMeta.bytes,
      parse_status: "error",
    },
  };
}

function incrementMap(map, key, limit) {
  if (!key) return;
  if (Object.prototype.hasOwnProperty.call(map, key)) {
    map[key] += 1;
    return;
  }
  if (Object.keys(map).length < limit) {
    map[key] = 1;
  }
}

function updateLiveIndex(dataRoot, record) {
  if (!record) return;
  try {
    const indexPath = path.join(dataRoot.root, "indexes", "live-index.json");
    mkdirp(path.dirname(indexPath));
    let index = {};
    try {
      index = JSON.parse(fs.readFileSync(indexPath, "utf8"));
    } catch (_) {}
    index.schema_version = EVENT_SCHEMA_VERSION;
    index.index_kind = "live_incremental";
    index.generated_at = nowIso();
    index.warning = "Append-only health index. Use better_tools.py index or summary for authoritative retention-window recommendations.";
    index.records = Number(index.records || 0) + 1;
    index.record_kinds = index.record_kinds || {};
    incrementMap(index.record_kinds, record.kind || "unknown", 50);
    index.tool_records = Number(index.tool_records || 0) + (record.kind === "tool_call" ? 1 : 0);
    index.prompt_records = Number(index.prompt_records || 0) + (record.kind === "user_prompt" ? 1 : 0);
    if (record.kind === "user_prompt") {
      index.recent_prompts = index.recent_prompts || [];
      if (record.prompt && record.prompt.prompt_hash) {
        index.recent_prompts.unshift({
          observed_at: record.observed_at,
          task_id: record.ids && record.ids.task_id,
          prompt_hash: record.prompt.prompt_hash,
          excerpt: record.prompt.excerpt || "",
          capture_mode: record.prompt.capture_mode || "unknown",
        });
        index.recent_prompts = index.recent_prompts.slice(0, 20);
      }
      fs.writeFileSync(indexPath, `${JSON.stringify(index, null, 2)}\n`, "utf8");
      return;
    }
    if (record.kind !== "tool_call") {
      fs.writeFileSync(indexPath, `${JSON.stringify(index, null, 2)}\n`, "utf8");
      return;
    }
    index.top_tools = index.top_tools || {};
    index.top_projects = index.top_projects || {};
    index.input_hash_counts = index.input_hash_counts || {};
    index.command_patterns = index.command_patterns || {};
    index.examples = index.examples || {};

    const toolName = record.tool && record.tool.name ? record.tool.name : "unknown";
    const projectName = record.project && record.project.project_name ? record.project.project_name : "unknown";
    incrementMap(index.top_tools, toolName, 200);
    incrementMap(index.top_projects, projectName, 500);
    incrementMap(index.input_hash_counts, record.tool.input_hash, MAX_INDEX_INPUT_HASHES);

    const command = record.tool && record.tool.command ? record.tool.command.split("\n")[0].slice(0, 220) : "";
    if (command) {
      const pattern = normalizeCommand(command);
      incrementMap(index.command_patterns, pattern, MAX_INDEX_PATTERNS);
      if (!index.examples[pattern]) index.examples[pattern] = command;
    }
    index.unique_tool_inputs = Object.keys(index.input_hash_counts).length;
    index.duplicate_tool_input_calls = Object.values(index.input_hash_counts)
      .reduce((sum, count) => sum + Math.max(0, Number(count) - 1), 0);
    fs.writeFileSync(indexPath, `${JSON.stringify(index, null, 2)}\n`, "utf8");
  } catch (_) {}
}

function maybePrune(dataRoot) {
  try {
    const stateDir = path.join(dataRoot.root, "state");
    const stateFile = path.join(stateDir, "retention.json");
    mkdirp(stateDir);
    const today = dayStamp();
    let state = {};
    try {
      state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
    } catch (_) {}
    if (state.last_pruned_day === today) return;
    const cutoff = Date.now() - RETENTION_DAYS * 24 * 60 * 60 * 1000;
    const pruneJsonlDir = (dir) => {
      if (!fs.existsSync(dir)) return [];
      const kept = [];
      const items = fs.readdirSync(dir)
        .filter((name) => name.endsWith(".jsonl"))
        .map((name) => path.join(dir, name))
        .map((file) => ({ file, stat: fs.statSync(file) }))
        .sort((a, b) => a.stat.mtimeMs - b.stat.mtimeMs);
      for (const item of items) {
        if (item.stat.mtimeMs >= cutoff) kept.push(item);
        else {
          try {
            fs.unlinkSync(item.file);
          } catch (_) {}
        }
      }
      return kept;
    };
    let files = pruneJsonlDir(path.join(dataRoot.root, "events"));
    pruneJsonlDir(path.join(dataRoot.root, "errors"));
    try {
      const promptState = readPromptState(dataRoot);
      promptState.prompts = prunePromptState(promptState.prompts || {});
      writePromptState(dataRoot, promptState);
    } catch (_) {}
    let total = files.reduce((sum, item) => sum + item.stat.size, 0);
    for (const item of files) {
      if (total <= MAX_TOTAL_BYTES) break;
      try {
        fs.unlinkSync(item.file);
        total -= item.stat.size;
      } catch (_) {}
    }
    fs.writeFileSync(stateFile, `${JSON.stringify({ last_pruned_day: today }, null, 2)}\n`, "utf8");
  } catch (_) {}
}

async function main() {
  const dataRoot = resolveDataRoot();
  try {
    mkdirp(dataRoot.root);
    writeLocator(dataRoot);
    const rawMeta = await readStdinLimited();
    const day = dayStamp();
    try {
      if (rawMeta.truncated) throw new Error(`stdin exceeded ${MAX_STDIN_BYTES} bytes`);
      const record = buildRecord(JSON.parse(rawMeta.raw || "{}"), rawMeta, dataRoot);
      appendJsonl(path.join(dataRoot.root, "events", `${day}.jsonl`), record);
      updateLiveIndex(dataRoot, record);
    } catch (error) {
      appendJsonl(path.join(dataRoot.root, "errors", `${day}.jsonl`), buildError(error, rawMeta, dataRoot));
    }
    maybePrune(dataRoot);
  } catch (_) {}
}

process.on("uncaughtException", () => process.exit(0));
process.on("unhandledRejection", () => process.exit(0));
main().finally(() => process.exit(0));
