# Better Plugins

Codex plugins from John Lindquist for making agents more effective.

## Plugins

- `toolsmith`: observes supported Codex prompts and tool calls locally, then turns repeated patterns into practical tool, skill, script, and AGENTS.md improvements.

## Install

Make sure plugin hooks are enabled in `~/.codex/config.toml`:

```toml
[features]
hooks = true
plugins = true
plugin_hooks = true
```

Add the Better Plugins marketplace and install Toolsmith:

```bash
codex plugin marketplace add johnlindquist/better-plugins
codex plugin add toolsmith@better-plugins
```

Restart Codex after installing or updating plugins. Open `/plugins` to confirm the plugin is installed and enabled, then open `/hooks` to review/trust bundled hooks if Codex asks.

## Local Development Install

```bash
git clone https://github.com/johnlindquist/better-plugins.git
cd better-plugins
codex plugin marketplace add "$PWD"
codex plugin add toolsmith@better-plugins
```

After local edits, reinstall the plugin so Codex refreshes its cached copy:

```bash
codex plugin remove toolsmith@better-plugins
codex plugin add toolsmith@better-plugins
```

## Toolsmith Data

Toolsmith writes to `$PLUGIN_DATA` when Codex provides it. In current Codex plugin installs this is typically:

```text
~/.codex/plugins/data/better-plugins-toolsmith/
```

It also writes a locator fallback under:

```text
~/.codex/plugin-data/toolsmith/active-data-root.json
```

The raw daily JSONL lives under `events/`; the compact deduped index lives at `indexes/tool-index.json`.

Toolsmith captures `UserPromptSubmit` events so recommendations can understand intent before judging tool usage. For example, a native macOS Swift task should not produce a generic browser-verification recommendation just because the original prompt mentioned a website.

## Checks

```bash
cd plugins/toolsmith
node --check hooks/capture_pre_tool_use.js
python3 -m py_compile scripts/*.py tests/*.py
python3 -m unittest discover -s tests
python3 scripts/better_tools.py doctor
```
