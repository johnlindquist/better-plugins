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

The raw store is a capped daily JSONL spool under `events/`. It includes prompt-intent records from `UserPromptSubmit` plus tool-call records from `PreToolUse`. Do not load raw JSONL into the conversation by default. The hook updates `indexes/tool-index.json` automatically, and `better_tools.py index` can regenerate a compact deduped index. Use the index and summaries because they collapse duplicate calls by input hash and normalized command pattern while preserving enough prompt context to avoid intent-blind recommendations.

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

3. If the user asks for researched tool recommendations, browse current official docs or primary sources for the candidate tools before recommending them.

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

6. Guide the user through one improvement at a time. For each proposal, include:

- observed evidence from the corpus
- why the current tool behavior is wasteful or risky
- the smallest better tool or instruction
- the exact file(s) to change
- a falsifiable verification command or runtime proof

## Output Shape

Prefer this structure:

```markdown
## Observed Pattern
<short evidence summary>

## Highest-Leverage Improvement
<one change, not a grab bag>

## Blindspots
<missing tools or verification paths inferred from project type and logs>

## AGENTS.md Suggestions
<patch-ready bullets, or say none>

## Next Verification
<command or runtime proof>
```

Do not dump raw tool arguments unless the user asks. The hook redacts common secret-like keys, but tool arguments may still include sensitive project details.
