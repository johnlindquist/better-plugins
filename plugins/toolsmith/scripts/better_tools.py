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


def normalize_command(command: str) -> str:
    value = re.sub(r'"[^"]*"', '"<str>"', command)
    value = re.sub(r"'[^']*'", "'<str>'", value)
    value = re.sub(r"/Users/[^ ]+", "/<path>", value)
    value = re.sub(r"\b[0-9a-f]{7,64}\b", "<hash>", value)
    value = re.sub(r"\b\d+\b", "<num>", value)
    return value[:220]


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    tools = Counter()
    families = Counter()
    projects = Counter()
    commands = Counter()
    normalized = Counter()
    input_hashes = Counter()
    examples: dict[str, str] = {}
    for event in events:
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
        "tools": tools,
        "families": families,
        "projects": projects,
        "commands": commands,
        "patterns": normalized,
        "input_hashes": input_hashes,
        "examples": examples,
    }


def recommendations(summary: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    commands = "\n".join(summary["commands"].keys())
    tools = summary["tools"]
    if "grep" in commands and "rg" not in commands:
        recs.append("AGENTS.md: prefer `rg` and `rg --files` over `grep`/`find` for repo search.")
    if "jq " in commands or "python3 - <<" in commands:
        recs.append("script: repeated structured-data shell analysis is a good candidate for a small repo-local helper.")
    if any("git status" in command for command in summary["commands"]):
        recs.append("script: consider a repo-status helper that prints branch, dirty files, and untracked files consistently.")
    if not any("browser" in tool.lower() or "web" in tool.lower() for tool in tools):
        recs.append("blindspot: no browser/web verification tools appear in the captured PreToolUse corpus.")
    if summary["records"] < 10:
        recs.append("data quality: collect more events before making durable tooling decisions.")
    if not recs:
        recs.append("workflow: no obvious blindspot; convert the highest-frequency repeated pattern into the smallest script, skill, or AGENTS.md note.")
    return recs


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
    lines = [
        "# Toolsmith Corpus Summary",
        "",
        f"Data root: `{root}`",
        f"Window: last {days} days",
        f"Records: {summary['records']}",
        f"Unique tool inputs: {len(summary['input_hashes'])}",
        "",
        "## Top Tools",
    ]
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
    for rec in recommendations(summary):
        lines.append(f"- {rec}")
    return "\n".join(lines) + "\n"


def compact_index(root: Path, days: int) -> dict[str, Any]:
    events = recent_events(root, days)
    summary = summarize(events)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "source_event_files": [str(path) for path in event_files(root)],
        "records": summary["records"],
        "unique_tool_inputs": len(summary["input_hashes"]),
        "duplicate_tool_input_calls": sum(count - 1 for count in summary["input_hashes"].values() if count > 1),
        "top_tools": summary["tools"].most_common(50),
        "top_projects": summary["projects"].most_common(50),
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
