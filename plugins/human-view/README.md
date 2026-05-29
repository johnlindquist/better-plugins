# human-view — a live Codex task view in a cmux browser pane

A Codex plugin that paints the current task as a themed **human view** in a
cmux browser pane beside the terminal, and keeps it updated as the conversation
progresses — **without ever reloading the pane**.

## Activation — the `#human` trigger

The hooks stay completely dormant by default. A session only gets a human view
once you include the token **`#human`** anywhere in a prompt, e.g.:

> `#human build an auth middleware with tests`

From then on that session stays active: every later prompt, tool use, and turn
keeps updating the view — no need to repeat `#human`. Sessions where you never
type `#human` create nothing (no dir, no daemon, no pane). The token is stripped
from the displayed title/prompt. Override it with `HUMAN_VIEW_TRIGGER`.

(`#human` is used rather than `$human` so the host agent never tries to resolve
it as a `$`-prefixed skill reference.)

## Why a daemon (the one non-obvious decision)

A prior experiment proved that a cmux browser pane (WKWebView) pointed at a
`file://` page does **not** auto-refresh when the file changes on disk; only an
explicit `cmux browser <surface> reload` updates it. And WKWebView blocks a
`file://` page from `fetch()`-ing a sibling `file://` JSON (opaque-origin CORS).

So instead of fighting the browser, `human-view` serves a normal same-origin web
app from a tiny **loopback HTTP daemon** on `127.0.0.1`:

- `GET /`            → the human view HTML (token injected)
- `GET /state.json`  → the live state the page polls (token-gated, 403 without)
- `GET /healthz`     → liveness

The page polls `/state.json` every 800 ms and live-updates. The hooks only ever
write a small JSON file. After the pane is opened once, **cmux is never touched
again** — no reloads, no focus stealing, no stale-surface risk.

## Architecture

```
UserPromptSubmit / PostToolUse / Stop
        │  (stdin JSON: session_id, hook_event_name, prompt, last_assistant_message…)
        ▼
hooks/human_view_hook.py        ← fast: mutate state.json, ensure daemon, print {"continue":true}
        │ spawns (detached, once) ──► daemon/human_view_daemon.py  (127.0.0.1:<ephemeral>)
        │ opens (once) ───────────► cmux new-pane --type browser --url http://127.0.0.1:<port>/?token=…
        ▼
state.json  ◄── served as /state.json ──►  assets/index.html  (polls every 800ms)
```

Per-session state lives under `$PLUGIN_DATA/sessions/<session_id>/`
(`state.json` + `meta.json` with `port`, `token`, `pid`, `surface_ref`).
The daemon is single-instance per session (hook checks `meta.pid`/`port` before
spawning) and self-exits after `HUMAN_VIEW_IDLE_TIMEOUT` seconds (default 1800)
of no HTTP traffic.

## Files

| File | Role |
|------|------|
| `.codex-plugin/plugin.json` | Plugin manifest (points at `hooks/hooks.json`) |
| `hooks/hooks.json`          | Wires `UserPromptSubmit`, `PostToolUse`, `Stop` |
| `hooks/human_view_hook.py`  | The thin, fast hook (state + daemon + open pane once) |
| `hooks/hook_control.py`     | Shared per-event control-JSON contract (`suppressOutput` only on `UserPromptSubmit`) |
| `hooks/redaction.py`        | Best-effort secret redaction for prompt + summary before they reach state |
| `daemon/human_view_daemon.py` | Loopback HTTP server for `/` + `/state.json` |
| `assets/index.html`         | The polling human view (unique accent + glyph per task) |

## Design generation (slice 1)

Deterministic and free: the title is derived from the prompt, and the accent
hue + glyph are derived from `sha256(prompt)`. No nested `codex exec`, so there
is no hook recursion to guard against yet. (Roadmap: an async, *opt-in* nested
`codex exec` that returns a **validated JSON design spec only** — never raw
HTML/JS — to make each task feel more bespoke.)

## Install / wire it up

### Quick path — user-level hooks (fastest for a spike)

Add to `~/.codex/config.toml` (Codex will ask you to trust the hook the first
time; the hash is re-checked on every edit):

```toml
[hooks]
UserPromptSubmit = [
  { hooks = [ { type = "command", command = "python3 \"/Users/johnlindquist/codex-workshop/sdk-investigation/human-view/hooks/human_view_hook.py\"", timeout = 8, statusMessage = "Updating human view" } ] }
]
PostToolUse = [
  { matcher = "*", hooks = [ { type = "command", command = "python3 \"/Users/johnlindquist/codex-workshop/sdk-investigation/human-view/hooks/human_view_hook.py\"", timeout = 5 } ] }
]
Stop = [
  { hooks = [ { type = "command", command = "python3 \"/Users/johnlindquist/codex-workshop/sdk-investigation/human-view/hooks/human_view_hook.py\"", timeout = 5 } ] }
]
```

When wired as a user-level hook (not a plugin), `PLUGIN_ROOT`/`PLUGIN_DATA`
aren't set; the script falls back to deriving its root from `__file__` and
stores state under `$CODEX_HOME/human-view/`.

### Plugin path

Publish `human-view/` through a local marketplace and `codex plugin add` it
(mirrors the `cmux-autotab-naming` plugin). Then `PLUGIN_ROOT`/`PLUGIN_DATA`
are provided by Codex automatically.

## Verify (matches the automated test in this repo)

```bash
export HUMAN_VIEW_DATA=/tmp/human-view-test
SID=demo
DIR="$HUMAN_VIEW_DATA/sessions/$SID"

# 1) simulate a prompt WITH the trigger -> opens the pane + starts the daemon
echo '{"hook_event_name":"UserPromptSubmit","session_id":"'"$SID"'","prompt":"#human Add a CLI flag parser and tests"}' \
  | python3 hooks/human_view_hook.py

SURFACE=$(python3 -c 'import json;print(json.load(open("'"$DIR"'/meta.json"))["surface_ref"])')
cmux browser "$SURFACE" wait --load-state complete --timeout-ms 15000
cmux browser "$SURFACE" get text body          # shows the task title + "Working"

# 2) simulate a second prompt -> body changes WITHOUT any reload
echo '{"hook_event_name":"UserPromptSubmit","session_id":"'"$SID"'","prompt":"Now add an error-path test"}' \
  | python3 hooks/human_view_hook.py
sleep 2
cmux browser "$SURFACE" get text body          # changed content, no reload called
```

Last automated run: **10/10 checks passed**, including
"cmux pane content changed WITHOUT manual reload".

## Config knobs (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `HUMAN_VIEW_TRIGGER` | `#human` | Token a prompt must contain to activate a session |
| `HUMAN_VIEW_DATA` | `$PLUGIN_DATA` or `$CODEX_HOME/human-view` | State root |
| `HUMAN_VIEW_IDLE_TIMEOUT` | `1800` | Daemon idle self-exit (seconds) |
| `HUMAN_VIEW_LOG` | `~/.codex/logs/human-view.jsonl` | Debug log |

## Security notes

- Daemon binds **only** `127.0.0.1`, is **read-only** over HTTP, and gates
  `/state.json` with an unguessable per-session token.
- The hook never focuses or reloads anything; it only opens its own pane once
  with `--focus false`, honoring the cmux non-disruptive rules.
- The prompt and assistant summary are passed through `redaction.py` before
  being written to state, so obvious secrets (`API_KEY=…`, `Bearer …`,
  `https://user:pass@…`, `token: …`) are masked as `<redacted>` on the visible
  pane. This reduces accidental leakage; it does not prove safety. The page also
  renders everything with `textContent`, so task text can never inject markup.
- A top-level `try/except BaseException` guarantees the hook always emits valid
  control JSON and exits `0`, so a future bug can never block Codex.
