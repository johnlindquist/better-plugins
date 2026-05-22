#!/usr/bin/env python3
import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


PLUGIN_NAME = "toolsmith"
HOME_FALLBACK = Path.home() / ".codex" / "plugin-data" / PLUGIN_NAME
URL_RE = re.compile(r"https?://\S+", re.I)
NATIVE_MACOS_SIGNALS = {
    "macos": 2,
    "swift": 2,
    "swiftui": 2,
    "appkit": 3,
    "accessibility": 3,
    "axuielement": 4,
    "axui": 3,
    "nswindow": 3,
    "nspanel": 3,
    "xcodebuild": 3,
    "package.swift": 2,
    ".xcodeproj": 3,
    "text cursor": 2,
    "selected text range": 3,
}
WEB_APP_SIGNALS = {
    "web app": 4,
    "frontend": 3,
    "front-end": 3,
    "browser": 3,
    "dom": 3,
    "css": 2,
    "html": 2,
    "react": 2,
    "next.js": 2,
    "vite": 2,
    "playwright": 4,
    "puppeteer": 4,
    "cypress": 4,
    "localhost": 2,
    ".tsx": 2,
    ".jsx": 2,
    "npm run dev": 3,
}
RESEARCH_PHASE_GUIDANCE = [
    "State the observed gap in one sentence before researching external candidates.",
    "Check installed/current local tools first, including project scripts, plugin cache, dependencies, and harmless `<tool> --help` or `--version` probes.",
    "Use primary sources for external candidates: official docs, official GitHub repositories, package registries, release notes, or standards docs.",
    "Apply recency expectations: AI/agent tools 3-6 months, browser/front-end tools 6-12 months, CLI/dev tools and MCP servers 12 months, stable macOS/POSIX/standards primary docs may be older.",
    "Label each candidate `recommend`, `defer`, or `reject` and include install/use plus falsifiable verification before suggesting adoption.",
]


def locate_data_dir(override: Optional[str] = None) -> Path:
    if override:
        return Path(override).expanduser()
    for env_name in ("PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
        if os.environ.get(env_name):
            return Path(os.environ[env_name]).expanduser()
    locator = HOME_FALLBACK / "active-data-root.json"
    if locator.exists():
        try:
            data = json.loads(locator.read_text())
            if data.get("data_root"):
                located = Path(data["data_root"]).expanduser()
                if located.exists():
                    return located
        except Exception:
            pass
    return HOME_FALLBACK


def parse_time(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def iter_jsonl(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(value, dict):
                        yield value
        except FileNotFoundError:
            continue


def event_files(root: Path) -> list[Path]:
    return sorted((root / "events").glob("*.jsonl"))


def error_files(root: Path) -> list[Path]:
    return sorted((root / "errors").glob("*.jsonl"))


def recent_events(root: Path, days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events = []
    for event in iter_jsonl(event_files(root)):
        observed = parse_time(str(event.get("observed_at") or ""))
        if not observed or observed >= cutoff:
            events.append(event)
    return events


def command_text(event: dict[str, Any]) -> str:
    tool = event.get("tool") if isinstance(event.get("tool"), dict) else {}
    command = tool.get("command")
    if isinstance(command, str):
        return command
    input_value = tool.get("input")
    if isinstance(input_value, dict):
        for key in ("command", "cmd"):
            if isinstance(input_value.get(key), str):
                return input_value[key]
    return ""


def prompt_text(event: dict[str, Any]) -> str:
    prompt = event.get("prompt") if isinstance(event.get("prompt"), dict) else {}
    for key in ("excerpt", "text"):
        if isinstance(prompt.get(key), str) and prompt[key]:
            return prompt[key]
    task = event.get("task") if isinstance(event.get("task"), dict) else {}
    if isinstance(task.get("prompt_excerpt"), str) and task["prompt_excerpt"]:
        return task["prompt_excerpt"]
    # Backward compatibility with early Toolsmith prompt-link records.
    intent = event.get("intent") if isinstance(event.get("intent"), dict) else {}
    if isinstance(intent.get("prompt_excerpt"), str):
        return intent["prompt_excerpt"]
    return ""


def normalize_command(command: str) -> str:
    value = re.sub(r'"[^"]*"', '"<str>"', command)
    value = re.sub(r"'[^']*'", "'<str>'", value)
    value = re.sub(r"/Users/[^ ]+", "/<path>", value)
    value = re.sub(r"\b[0-9a-f]{7,64}\b", "<hash>", value)
    value = re.sub(r"\b\d+\b", "<num>", value)
    return value[:220]


def classify_intent_text(text: str) -> dict[str, Any]:
    lowered = text.lower()
    scores: Counter[str] = Counter()
    signals: dict[str, list[str]] = {"native_macos": [], "web_app": [], "external_reference": []}
    if URL_RE.search(text):
        signals["external_reference"].append("url_present")
    for term, weight in NATIVE_MACOS_SIGNALS.items():
        if term in lowered:
            scores["native_macos"] += weight
            signals["native_macos"].append(term)
    for term, weight in WEB_APP_SIGNALS.items():
        if term in lowered:
            scores["web_app"] += weight
            signals["web_app"].append(term)
    domains: list[str] = []
    if scores["native_macos"] >= 3:
        domains.append("native_macos")
    if scores["web_app"] >= 4:
        domains.append("web_app")
    if signals["external_reference"]:
        domains.append("external_reference")
    primary_domain = "unknown"
    if scores:
        primary_domain = scores.most_common(1)[0][0]
    elif signals["external_reference"]:
        primary_domain = "external_reference"
    return {
        "primary_domain": primary_domain,
        "domains": domains,
        "scores": dict(scores),
        "signals": {key: value for key, value in signals.items() if value},
        "rules_version": "intent-lexicon-2026-05-22",
    }


def task_key(event: dict[str, Any]) -> str:
    task = event.get("task") if isinstance(event.get("task"), dict) else {}
    ids = event.get("ids") if isinstance(event.get("ids"), dict) else {}
    if isinstance(task.get("task_id"), str) and task["task_id"]:
        return task["task_id"]
    if isinstance(ids.get("task_id"), str) and ids["task_id"]:
        return ids["task_id"]
    session_id = ids.get("session_id")
    turn_id = ids.get("turn_id")
    if session_id and turn_id:
        return f"session-turn:{session_id}\u001f{turn_id}"
    return f"event:{ids.get('event_id') or event.get('observed_at') or id(event)}"


def build_task_summaries(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        key = task_key(event)
        task = tasks.setdefault(
            key,
            {
                "task_key": key,
                "prompt_excerpt": "",
                "tool_names": Counter(),
                "commands": [],
                "tool_records": 0,
                "prompt_records": 0,
                "texts": [],
            },
        )
        kind = str(event.get("kind") or "unknown")
        text = prompt_text(event)
        if kind == "user_prompt":
            task["prompt_records"] += 1
            if text and not task["prompt_excerpt"]:
                task["prompt_excerpt"] = text
            if text:
                task["texts"].append(text)
        if kind == "tool_call":
            task["tool_records"] += 1
            tool = event.get("tool") if isinstance(event.get("tool"), dict) else {}
            name = str(tool.get("name") or "unknown")
            task["tool_names"][name] += 1
            command = command_text(event)
            if command:
                task["commands"].append(command)
                task["texts"].append(command)
            if text:
                task["texts"].append(text)
                if not task["prompt_excerpt"]:
                    task["prompt_excerpt"] = text
    output = []
    for task in tasks.values():
        intent = classify_intent_text("\n".join(task["texts"]))
        output.append({
            "task_key": task["task_key"],
            "prompt_excerpt": task["prompt_excerpt"],
            "tool_names": task["tool_names"],
            "commands": task["commands"],
            "tool_records": task["tool_records"],
            "prompt_records": task["prompt_records"],
            "intent": intent,
        })
    return output


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = build_task_summaries(events)
    records = Counter()
    tools = Counter()
    families = Counter()
    projects = Counter()
    commands = Counter()
    normalized = Counter()
    input_hashes = Counter()
    prompts = Counter()
    examples: dict[str, str] = {}
    for event in events:
        kind = str(event.get("kind") or "unknown")
        records[kind] += 1
        prompt = prompt_text(event)
        if prompt:
            prompts[prompt] += 1
        if kind != "tool_call":
            continue
        tool = event.get("tool") if isinstance(event.get("tool"), dict) else {}
        project = event.get("project") if isinstance(event.get("project"), dict) else {}
        tools[str(tool.get("name") or "unknown")] += 1
        families[str(tool.get("family") or "unknown")] += 1
        projects[str(project.get("project_name") or "unknown")] += 1
        input_hash = str(tool.get("input_hash") or "")
        if input_hash:
            input_hashes[input_hash] += 1
        command = command_text(event)
        if command:
            first_line = command.splitlines()[0][:220]
            pattern = normalize_command(first_line)
            commands[first_line] += 1
            normalized[pattern] += 1
            examples.setdefault(pattern, first_line)
    return {
        "records": len(events),
        "record_kinds": records,
        "tools": tools,
        "families": families,
        "projects": projects,
        "commands": commands,
        "patterns": normalized,
        "input_hashes": input_hashes,
        "prompts": prompts,
        "examples": examples,
        "tasks": tasks,
        "intent_domains": Counter(
            task["intent"]["primary_domain"]
            for task in tasks
            if task["intent"]["primary_domain"] != "unknown"
        ),
    }


def command_has(commands: Iterable[str], name: str) -> bool:
    pattern = re.compile(rf"(^|[\s|;&]){re.escape(name)}($|[\s|;&])")
    return any(pattern.search(command) for command in commands)


def tool_blob(task: dict[str, Any]) -> str:
    return "\n".join(name.lower() for name in task.get("tool_names", {}))


def command_blob(task: dict[str, Any]) -> str:
    return "\n".join(str(command).lower() for command in task.get("commands", []))


def is_web_app_task(task: dict[str, Any]) -> bool:
    return task["intent"]["primary_domain"] == "web_app" or task["intent"]["scores"].get("web_app", 0) >= 4


def is_native_macos_task(task: dict[str, Any]) -> bool:
    return task["intent"]["primary_domain"] == "native_macos" or task["intent"]["scores"].get("native_macos", 0) >= 3


def has_browser_runtime_proof(task: dict[str, Any]) -> bool:
    blob = f"{tool_blob(task)}\n{command_blob(task)}"
    return any(term in blob for term in ("browser", "playwright", "puppeteer", "selenium", "cypress", "mcp__playwright"))


def has_native_build(task: dict[str, Any]) -> bool:
    blob = command_blob(task)
    return any(term in blob for term in ("swift build", "swift test", "xcodebuild"))


def has_native_runtime_proof(task: dict[str, Any]) -> bool:
    blob = command_blob(task)
    return any(term in blob for term in ("swift run", "open -a", "osascript", "screencapture", "log stream", "xcrun simctl", "axuielement"))


def recommendations(summary: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    command_values = list(summary["commands"].keys())
    if command_has(command_values, "grep") and not command_has(command_values, "rg"):
        recs.append("AGENTS.md: prefer `rg` and `rg --files` over `grep`/`find` for repo search.")
    if command_has(command_values, "jq") or any("python3 - <<" in command for command in command_values):
        recs.append("script: repeated structured-data shell analysis is a good candidate for a small repo-local helper.")
    if any("git status" in command for command in summary["commands"]):
        recs.append("script: consider a repo-status helper that prints branch, dirty files, and untracked files consistently.")
    tasks = summary.get("tasks", [])
    web_tasks = [task for task in tasks if is_web_app_task(task)]
    native_tasks = [task for task in tasks if is_native_macos_task(task)]
    if web_tasks and not any(has_browser_runtime_proof(task) for task in web_tasks):
        recs.append("blindspot: web-app/front-end tasks lack browser/runtime verification; add a Playwright/browser smoke path before making UI claims.")
    if native_tasks and any(has_native_build(task) for task in native_tasks) and not any(has_native_runtime_proof(task) for task in native_tasks):
        recs.append("blindspot: native macOS/AppKit/AX work needs target-app runtime proof, not generic browser verification; pair `swift build`/`xcodebuild` with an Accessibility-focused smoke against the app under test.")
    if summary["records"] < 10:
        recs.append("data quality: collect more events before making durable tooling decisions.")
    if not recs:
        recs.append("workflow: no obvious blindspot; convert the highest-frequency repeated pattern into the smallest script, skill, or AGENTS.md note.")
    return recs


def needs_research_phase(recs: list[str]) -> bool:
    return any(
        rec.startswith(("blindspot:", "script:", "skill:", "MCP/tool:"))
        for rec in recs
    )


def print_doctor(root: Path) -> None:
    events = list(iter_jsonl(event_files(root)))
    errors = list(iter_jsonl(error_files(root)))
    summary = summarize(events)
    newest = max((event.get("observed_at", "") for event in events), default="none")
    print("Toolsmith doctor")
    print(f"Data root: {root}")
    print(f"Events: {len(event_files(root))} files, {len(events)} records")
    print(f"Errors: {len(error_files(root))} files, {len(errors)} records")
    print(f"Newest event: {newest}")
    kind_summary = ", ".join(f"{name}={count}" for name, count in summary["record_kinds"].most_common(10))
    print(f"Record kinds: {kind_summary or 'none'}")
    top_tools = ", ".join(f"{name}={count}" for name, count in summary["tools"].most_common(10))
    print(f"Top tools: {top_tools or 'none'}")
    duplicate_total = sum(count - 1 for count in summary["input_hashes"].values() if count > 1)
    print(f"Duplicate tool-input calls: {duplicate_total}")
    config = Path.home() / ".codex" / "config.toml"
    if config.exists():
        text = config.read_text(errors="ignore")
        print(f"plugin_hooks enabled: {'plugin_hooks = true' in text}")


def render_summary(root: Path, days: int) -> str:
    events = recent_events(root, days)
    summary = summarize(events)
    recs = recommendations(summary)
    lines = [
        "# Toolsmith Corpus Summary",
        "",
        f"Data root: `{root}`",
        f"Window: last {days} days",
        f"Records: {summary['records']}",
        f"Tool records: {summary['record_kinds'].get('tool_call', 0)}",
        f"Prompt records: {summary['record_kinds'].get('user_prompt', 0)}",
        f"Unique tool inputs: {len(summary['input_hashes'])}",
        "",
        "## Recent User Intent",
    ]
    for prompt, _count in summary["prompts"].most_common(5):
        clean = " ".join(prompt.split())
        if len(clean) > 220:
            clean = clean[:217] + "..."
        lines.append(f"- {clean}")
    if not summary["prompts"]:
        lines.append("- No prompt intent captured yet.")
    lines.extend([
        "",
        "## Intent Domains",
    ])
    for domain, count in summary["intent_domains"].most_common(10):
        lines.append(f"- `{domain}`: {count} task(s)")
    if not summary["intent_domains"]:
        lines.append("- No task intent domains inferred yet.")
    lines.extend([
        "",
        "## Task Slices",
    ])
    for task in summary["tasks"][:10]:
        excerpt = " ".join((task.get("prompt_excerpt") or "").split())
        if len(excerpt) > 160:
            excerpt = excerpt[:157] + "..."
        lines.append(f"- `{task['intent']['primary_domain']}` tools={task['tool_records']} prompt=`{excerpt or 'none'}`")
    lines.extend([
        "",
        "## Top Tools",
    ])
    for name, count in summary["tools"].most_common(15):
        lines.append(f"- `{name}`: {count}")
    lines.append("")
    lines.append("## Top Projects")
    for name, count in summary["projects"].most_common(10):
        lines.append(f"- `{name}`: {count}")
    lines.append("")
    lines.append("## Repeated Patterns")
    for pattern, count in summary["patterns"].most_common(15):
        example = summary["examples"].get(pattern, pattern)
        lines.append(f"- {count}x `{pattern}`")
        if example != pattern:
            lines.append(f"  Example: `{example}`")
    lines.append("")
    lines.append("## Recommendations")
    for rec in recs:
        lines.append(f"- {rec}")
    if needs_research_phase(recs):
        lines.extend([
            "",
            "## Research Phase Guidance",
        ])
        for item in RESEARCH_PHASE_GUIDANCE:
            lines.append(f"- {item}")
        lines.extend([
            "",
            "Candidate output fields: decision, candidate, source type, recency evidence, local fit, install/use path, verification.",
        ])
    return "\n".join(lines) + "\n"


def compact_index(root: Path, days: int) -> dict[str, Any]:
    events = recent_events(root, days)
    summary = summarize(events)
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "source_event_files": [str(path) for path in event_files(root)],
        "records": summary["records"],
        "tool_records": summary["record_kinds"].get("tool_call", 0),
        "prompt_records": summary["record_kinds"].get("user_prompt", 0),
        "unique_tool_inputs": len(summary["input_hashes"]),
        "duplicate_tool_input_calls": sum(count - 1 for count in summary["input_hashes"].values() if count > 1),
        "top_tools": summary["tools"].most_common(50),
        "top_projects": summary["projects"].most_common(50),
        "intent_domains": summary["intent_domains"].most_common(20),
        "recent_prompts": [
            {"prompt_excerpt": prompt[:220], "count": count}
            for prompt, count in summary["prompts"].most_common(20)
        ],
        "task_summaries": [
            {
                "primary_domain": task["intent"]["primary_domain"],
                "domains": task["intent"]["domains"],
                "scores": task["intent"]["scores"],
                "signals": task["intent"]["signals"],
                "prompt_excerpt": task["prompt_excerpt"][:220],
                "tool_records": task["tool_records"],
                "top_tools": task["tool_names"].most_common(10),
                "top_command_patterns": [
                    normalize_command(command.splitlines()[0])
                    for command in task["commands"][:10]
                ],
            }
            for task in summary["tasks"][:50]
        ],
        "top_command_patterns": [
            {
                "pattern": pattern,
                "count": count,
                "example": summary["examples"].get(pattern, pattern),
            }
            for pattern, count in summary["patterns"].most_common(100)
        ],
        "recommendations": recommendations(summary),
    }


def write_index(root: Path, days: int) -> Path:
    index = compact_index(root, days)
    output = root / "indexes" / "tool-index.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def render_agents_md(root: Path, days: int) -> str:
    summary = summarize(recent_events(root, days))
    lines = ["# Suggested AGENTS.md Updates", ""]
    for rec in recommendations(summary):
        if rec.startswith("AGENTS.md:"):
            lines.append(f"- {rec.removeprefix('AGENTS.md:').strip()}")
    if len(lines) == 2:
        lines.append("- No strong AGENTS.md suggestion yet; gather more tool events or inspect project-specific repeated patterns.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Toolsmith JSONL corpus.")
    parser.add_argument("command", choices=["locate", "doctor", "index", "summary", "patterns", "blindspots", "agents-md", "report", "export"])
    parser.add_argument("--data-dir")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out")
    args = parser.parse_args()
    root = locate_data_dir(args.data_dir)
    if args.command == "locate":
        print(root)
    elif args.command == "doctor":
        print_doctor(root)
    elif args.command == "index":
        print(write_index(root, args.days))
    elif args.command in ("summary", "patterns", "blindspots"):
        print(render_summary(root, args.days), end="")
    elif args.command == "agents-md":
        print(render_agents_md(root, args.days), end="")
    elif args.command == "report":
        output = Path(args.out).expanduser() if args.out else root / "reports" / f"tool-corpus-summary-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_summary(root, args.days), encoding="utf-8")
        print(output)
    elif args.command == "export":
        for event in recent_events(root, args.days):
            print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
