---
name: toolsmith
description: Analyze the local Toolsmith corpus of Codex tool calls, identify tool optimization opportunities and blindspots, propose new tools or skills, and suggest focused AGENTS.md/software-engineering improvements based on observed agent behavior.
---

# Toolsmith

Use this skill when the user wants to improve tools over time from observed Codex tool usage, asks for a tool blindspot analysis, wants suggestions for new tools/MCP servers/skills/scripts, or asks how AGENTS.md should change based on recent agent behavior.

## Data Source

The companion `PreToolUse` hook stores a rolling corpus under `$PLUGIN_DATA` when Codex provides it, falling back to:

```text
~/.codex/plugin-data/toolsmith/
```

The raw store is a capped daily JSONL spool under `events/`. It includes prompt-intent records from `UserPromptSubmit` plus tool-call records from `PreToolUse`. Do not load raw JSONL into the conversation by default.

The hook may also write `indexes/live-index.json`, which is append-only health metadata. Do not treat it as the authoritative recommendation index. Run `better_tools.py index` or `better_tools.py summary` for retention-window recommendations. Use the index and summaries because they collapse duplicate calls by input hash and normalized command pattern while preserving enough prompt context to avoid intent-blind recommendations.

## Workflow

1. Run the analyzer from the plugin root:

```bash
python3 "$PLUGIN_ROOT/scripts/better_tools.py" doctor
python3 "$PLUGIN_ROOT/scripts/better_tools.py" index --days 30
python3 "$PLUGIN_ROOT/scripts/better_tools.py" summary --days 30
```

If `$PLUGIN_ROOT` is not available in the current shell, use the installed plugin path or the development checkout:

```bash
python3 /Users/johnlindquist/dev/better-plugins/plugins/toolsmith/scripts/better_tools.py summary --days 30
```

2. Use the report as evidence, not as the final answer. Inspect the current project before making durable recommendations:

```bash
pwd
ls
find .. -maxdepth 2 -name AGENTS.md -o -name package.json -o -name pyproject.toml -o -name Cargo.toml
```

3. Run a Research Phase before recommending tools, plugins, skills, scripts, MCP servers, or external projects that are not already installed.

Research Phase gate:

- First state the observed gap in one sentence, grounded in the corpus and current project. If you cannot state the gap clearly, label the item `defer` and do not research a tool list.
- Separate `installed/current local tools` from `external candidates`. Check the local environment, plugin cache, project scripts, repo dependencies, and existing commands before proposing anything new.
- For unfamiliar installed CLI tools, run harmless local discovery first, such as `<tool> --help`, `<tool> help`, or `<tool> --version`, and summarize only relevant capabilities.
- When web/search is needed for external candidates, use current primary sources: official docs, vendor docs, official GitHub repositories, package registry pages, release notes, or standards docs. Do not rely on blog posts, SEO lists, or stale tutorials as the main source.
- Apply the quality bar: the candidate must fit the observed gap, have an explicit install/use path, show maintenance or stability evidence, expose a verification/smoke path, and have acceptable permissions/auth/security risk for the user's workflow.
- Apply the recency bar by domain:
  - AI/LLM/agent tooling: source or release evidence from the last 3-6 months unless the tool is the official stable API.
  - Browser/front-end/test tooling: docs or release evidence from the last 6-12 months.
  - CLI/dev tooling and MCP servers: maintenance, release, or compatibility evidence from the last 12 months.
  - macOS/system APIs, POSIX/Unix tools, and standards: older primary docs are acceptable when the API/tool is stable; third-party wrappers or examples still need recent maintenance evidence.
- Assign a decision label to each candidate:
  - `recommend`: strong local fit, current primary source, clear install/use path, and concrete verification.
  - `defer`: plausible fit but weak signal, unclear local need, insufficient source recency, or missing verification path.
  - `reject`: does not fit the observed gap, is stale/unmaintained for the domain, has unacceptable permissions/security risk, or duplicates an installed capability.
- Do not auto-install external candidates unless the user explicitly asks. Provide the install command and a falsifiable verification command or runtime proof instead.

4. For unfamiliar CLI tools already present in the corpus or current project, inspect their local capabilities before recommending replacements or wrappers. Prefer harmless help/version probes:

```bash
<tool> --help
<tool> help
<tool> --version
```

Capture only the relevant options and subcommands. Do not run mutating commands just to learn a tool.

5. Classify each recommendation:

- `AGENTS.md`: a concise instruction that would have improved observed agent behavior.
- `script`: a repo-local command that compresses a repeated shell workflow.
- `skill`: an agent workflow for recurring judgment-heavy work.
- `MCP/tool`: a capability gap where structured APIs would beat shell scraping or ad hoc browser work.
- `defer`: a weak signal that needs more logged events.

6. Before declaring a blindspot, inspect task intent. Treat `external_reference` as context, not an implementation target. A URL in the prompt is not enough to recommend browser/web verification.

Only recommend browser/runtime-web verification when the task is classified as web-app, front-end, DOM, browser, or localhost UI work.

For native macOS Swift/AppKit/Accessibility work, recommend native proof instead: build/test plus target-app runtime evidence such as Accessibility permission state, focused AX element, selected text range, bounds/range output, app launch behavior, logs, or native screenshot/smoke evidence.

7. Guide the user through one improvement at a time. For each proposal, include:

- observed evidence from the corpus
- why the current tool behavior is wasteful or risky
- the smallest better tool or instruction
- the exact file(s) to change
- a falsifiable verification command or runtime proof
- if external candidates were researched, the decision label, source type, recency evidence, local-fit rationale, install/use path, and verification path

## Output Shape

Prefer this structure:

```markdown
## Observed Pattern
<short evidence summary>

## Highest-Leverage Improvement
<one change, not a grab bag>

## Blindspots
<missing tools or verification paths inferred from project type and logs>

## Research Phase
Observed gap: <one sentence grounded in corpus/project evidence>
Installed/current local tools checked: <tools/scripts/plugins/deps already available, or "none found">
External research needed: <yes/no and why>

## Candidate Tools
| Decision | Candidate | Source Type | Recency Evidence | Local Fit | Install/Use Path | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| recommend/defer/reject | <name> | <official docs/GitHub/package registry/local --help> | <date/version/stability note> | <why it fits or does not> | <command or none> | <smoke/runtime proof> |

## AGENTS.md Suggestions
<patch-ready bullets, or say none>

## Next Verification
<command or runtime proof>
```

Do not dump raw tool arguments unless the user asks. The hook redacts common secret-like keys, but tool arguments may still include sensitive project details.

When using Toolsmith summaries, prefer `Intent Domains` and `Task Slices` over global absence of a tool family. Do not infer a blindspot from missing browser tools across the whole corpus if the relevant task slice is native desktop, CLI, backend, or research-only.
