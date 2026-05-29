# gemini-video — auto-summarize .mp4 paths with Gemini

A Codex plugin that watches for **paths ending in `.mp4`** showing up in your
prompts (and in tool calls), and — once a session is opted in — runs your
existing Gemini video-processing scripts on each new video in a **detached
worker**, so the hook never blocks Codex.

## Activation — the `#gemini-video` trigger

The hooks stay dormant by default. A session only starts processing videos once
you include the token **`#gemini-video`** anywhere in a prompt, e.g.:

> `#gemini-video summarize ~/Downloads/demo.mp4`

From then on that session stays active (sticky): later prompts and tool calls
that mention an existing `.mp4` are picked up automatically. The token is
stripped before the prompt is stored. Override it with `GEMINI_VIDEO_TRIGGER`.

(`#gemini-video` mirrors the `#human` / `#toolsmith` convention so the host
agent never resolves it as a `$`-prefixed skill reference.)

## How it works

```
UserPromptSubmit / PostToolUse / Stop
        │  (stdin JSON: session_id, cwd, hook_event_name, prompt, tool_input…)
        ▼
hooks/gemini_video_hook.py   ← fast: detect .mp4 → atomic claim → spawn detached worker → emit control JSON
        │ spawns (detached, once per video) ──► worker/worker.py
        │                                          └─ runs: bun ~/lessons/scripts/summarize-one.ts <video> <prompt>
        ▼
$PLUGIN_DATA/jobs/<job_key>/{status,summary.md,error.txt,worker.log}
```

- **Detection** (`hooks/detector.py`): a permissive regex finds `.mp4` tokens
  (absolute, relative, `~`, `$VAR`, quoted-with-spaces) in the prompt and in any
  `tool_input`; each is resolved against the hook's `cwd` and kept only if it is
  an **existing** regular file. `PostToolUse` (not `PreToolUse`) is used so the
  file already exists.
- **Idempotency** (`hooks/ledger.py`): each job is keyed by
  `sha256(realpath:size:mtime)` and guarded by an atomic `O_CREAT|O_EXCL` claim
  file, so the same video is processed exactly once even though it reappears
  across many hook firings.
- **Detached execution** (`worker/worker.py`): the hook returns in milliseconds;
  the worker does the slow upload + summarize and always writes a terminal
  `status` (`done`/`error`), bounded by `GEMINI_VIDEO_JOB_TIMEOUT`.

## The Gemini call is injectable (and that's the test seam)

The worker runs `GEMINI_VIDEO_RUNNER` — a shell template with `{video}` and
`{prompt}` placeholders. When empty, it defaults to:

```
bun ~/lessons/scripts/summarize-one.ts <video> <prompt-file>
```

which reuses your existing `@google/genai` setup, `GEMINI_API_KEY`, and the
36k-char `gemini-extract-lesson-prompt.md`. Tests set `GEMINI_VIDEO_RUNNER` to a
fake `echo` so they are fully hermetic (no network, no key).

> Prerequisite for real runs: `~/lessons/scripts/summarize-one.ts` (a thin
> wrapper around `summarizeVideo`) and `bun` + `~/lessons/node_modules`.

## Files

| File | Role |
|------|------|
| `.codex-plugin/plugin.json` | Plugin manifest (points at `hooks/hooks.json`) |
| `hooks/hooks.json`          | Wires `UserPromptSubmit`, `PostToolUse`, `Stop` (stable after install) |
| `hooks/gemini_video_hook.py`| Thin hook: detect → claim → spawn → emit control |
| `hooks/hook_control.py`     | Per-event control-JSON contract (`suppressOutput` only on `UserPromptSubmit`) |
| `hooks/redaction.py`        | Best-effort secret redaction for user-visible text |
| `hooks/detector.py`         | mp4 extraction + `resolve_existing` |
| `hooks/ledger.py`           | data layout, atomic `claim_job`, status + display ledger |
| `worker/worker.py`          | Detached worker; runs the injectable Gemini runner |
| `tests/test_gemini_video.py`| Hermetic test (stubs Gemini via `GEMINI_VIDEO_RUNNER`) |

## Verify

Hermetic (no network, proves the first slice):

```bash
cd plugins/gemini-video
python3 tests/test_gemini_video.py     # expect: OK: 0 failure(s)
```

Real smoke (one real Gemini call — opt-in, needs a key):

```bash
export GEMINI_API_KEY=...      # do not echo
SID=smoke
echo '{"hook_event_name":"UserPromptSubmit","session_id":"'"$SID"'","prompt":"#gemini-video summarize /abs/path/real.mp4","cwd":"'"$HOME"'"}' \
  | GEMINI_VIDEO_DATA=/tmp/gv python3 hooks/gemini_video_hook.py
sleep 5; cat /tmp/gv/jobs/*/status; head /tmp/gv/jobs/*/summary.md 2>/dev/null
```

## Config knobs (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `GEMINI_VIDEO_TRIGGER` | `#gemini-video` | Token a prompt must contain to activate a session |
| `GEMINI_VIDEO_DATA` | `$PLUGIN_DATA` or `$CODEX_HOME/gemini-video` | State root |
| `GEMINI_VIDEO_RUNNER` | _(empty → default bun cmd)_ | Shell template (`{video}`,`{prompt}`); the test seam |
| `GEMINI_VIDEO_PROMPT_FILE` | `~/lessons/scripts/gemini-extract-lesson-prompt.md` | Prompt passed to Gemini |
| `GEMINI_VIDEO_JOB_TIMEOUT` | `600` | Overall worker timeout (seconds), fails closed |
| `GEMINI_API_KEY` | _(required for real runs)_ | Inherited by the worker; never logged |

## Security notes

- The hook does only O(1) filesystem work and **never** calls bun/network
  inline; all slow work is detached, so a hook can never exceed its timeout.
- `GEMINI_API_KEY` is inherited by the worker from the environment and is never
  written to logs. User-visible text passes through `redaction.py`.
- Only **existing** `.mp4` files resolved against `cwd` are ever acted on.
- A top-level `try/except BaseException` guarantees the hook always emits valid
  control JSON and exits `0`, so a bug can never block Codex.
- `hooks.json` is frozen after install (Codex trusts its hash); all behavior
  lives in helper scripts, which can change without re-trust.
