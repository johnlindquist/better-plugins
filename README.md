# dev.build Plugins

Official Codex plugins from dev.build.

## Plugins

- `toolsmith`: observes supported Codex tool calls locally and turns repeated patterns into practical tool, skill, script, and AGENTS.md improvements.

## Install

Make sure plugin hooks are enabled in `~/.codex/config.toml`:

```toml
[features]
hooks = true
plugins = true
plugin_hooks = true
```

Add the dev.build marketplace and install Toolsmith:

```bash
codex plugin marketplace add johnlindquist/dev-build-plugins
codex plugin add toolsmith@dev-build
```

Restart Codex after installing or updating plugins. Open `/plugins` to confirm the plugin is installed and enabled, then open `/hooks` to review/trust bundled hooks if Codex asks.

## Local Development Install

```bash
git clone https://github.com/johnlindquist/dev-build-plugins.git
cd dev-build-plugins
codex plugin marketplace add "$PWD"
codex plugin add toolsmith@dev-build
```

After local edits, reinstall the plugin so Codex refreshes its cached copy:

```bash
codex plugin remove toolsmith@dev-build
codex plugin add toolsmith@dev-build
```

## Toolsmith Data

Toolsmith writes to `$PLUGIN_DATA` when Codex provides it. In current Codex plugin installs this is typically:

```text
~/.codex/plugins/data/dev-build-toolsmith/
```

It also writes a locator fallback under:

```text
~/.codex/plugin-data/toolsmith/active-data-root.json
```

The raw daily JSONL lives under `events/`; the compact deduped index lives at `indexes/tool-index.json`.

## Checks

```bash
cd plugins/toolsmith
node --check hooks/capture_pre_tool_use.js
python3 -m py_compile scripts/*.py tests/*.py
python3 -m unittest discover -s tests
python3 scripts/better_tools.py doctor
```
