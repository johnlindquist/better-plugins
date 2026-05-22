import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "hooks" / "capture_pre_tool_use.js"
ANALYZE = ROOT / "scripts" / "better_tools.py"


class BetterToolsTests(unittest.TestCase):
    def test_capture_writes_daily_jsonl_with_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PLUGIN_DATA"] = tmp
            payload = {
                "session_id": "s1",
                "turn_id": "t1",
                "tool_use_id": "u1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": str(ROOT),
                "model": "gpt-test",
                "tool_input": {
                    "cmd": "grep -R TODO .",
                    "api_key": "should-not-leak",
                },
            }
            result = subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            event_files = list((Path(tmp) / "events").glob("*.jsonl"))
            self.assertEqual(len(event_files), 1)
            self.assertTrue((Path(tmp) / "indexes" / "tool-index.json").exists())
            content = event_files[0].read_text()
            self.assertIn("grep -R TODO", content)
            self.assertNotIn("should-not-leak", content)
            self.assertIn("<redacted>", content)

    def test_analyzer_reports_blindspot_from_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PLUGIN_DATA"] = tmp
            payload = {
                "session_id": "s2",
                "turn_id": "t2",
                "tool_use_id": "u2",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": str(ROOT),
                "tool_input": {"cmd": "grep -R TODO ."},
            }
            subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            result = subprocess.run(
                [sys.executable, str(ANALYZE), "summary", "--data-dir", tmp, "--days", "1"],
                text=True,
                capture_output=True,
                check=False,
                cwd=str(ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Toolsmith Corpus Summary", result.stdout)
            self.assertIn("prefer `rg`", result.stdout)

    def test_index_collapses_duplicate_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PLUGIN_DATA"] = tmp
            payload = {
                "session_id": "s3",
                "turn_id": "t3",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": str(ROOT),
                "tool_input": {"cmd": "grep -R TODO ."},
            }
            for index in range(25):
                payload["tool_use_id"] = f"u{index}"
                subprocess.run(
                    ["node", str(CAPTURE)],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    env=env,
                    check=True,
                )
            result = subprocess.run(
                [sys.executable, str(ANALYZE), "index", "--data-dir", tmp, "--days", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            index_path = Path(result.stdout.strip())
            data = json.loads(index_path.read_text())
            self.assertEqual(data["records"], 25)
            self.assertEqual(data["unique_tool_inputs"], 1)
            self.assertEqual(data["duplicate_tool_input_calls"], 24)
            self.assertEqual(data["top_command_patterns"][0]["count"], 25)

    def test_prompt_intent_is_attached_to_following_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PLUGIN_DATA"] = tmp
            prompt_payload = {
                "session_id": "s4",
                "turn_id": "t4",
                "hook_event_name": "UserPromptSubmit",
                "model": "gpt-test",
                "prompt": "Build a native macOS Swift AppKit AXUIElement proof of concept with token=secret",
            }
            tool_payload = {
                "session_id": "s4",
                "turn_id": "t4",
                "tool_use_id": "u4",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": str(ROOT),
                "tool_input": {"cmd": "swift build"},
            }
            for payload in (prompt_payload, tool_payload):
                subprocess.run(
                    ["node", str(CAPTURE)],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    env=env,
                    check=True,
                )

            events = []
            for path in (Path(tmp) / "events").glob("*.jsonl"):
                events.extend(json.loads(line) for line in path.read_text().splitlines())
            prompt_events = [event for event in events if event["kind"] == "user_prompt"]
            tool_events = [event for event in events if event["kind"] == "tool_call"]
            self.assertEqual(len(prompt_events), 1)
            self.assertEqual(len(tool_events), 1)
            self.assertIn("native macOS Swift", prompt_events[0]["prompt"]["text"])
            self.assertNotIn("secret", json.dumps(events))
            self.assertEqual(
                tool_events[0]["intent"]["prompt_hash"],
                prompt_events[0]["prompt"]["prompt_hash"],
            )
            self.assertIn("AXUIElement", tool_events[0]["intent"]["prompt_excerpt"])

    def test_native_intent_does_not_emit_web_blindspot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PLUGIN_DATA"] = tmp
            payloads = [
                {
                    "session_id": "s5",
                    "turn_id": "t5",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "Prototype a native macOS Swift AppKit Accessibility AX caret overlay",
                },
                {
                    "session_id": "s5",
                    "turn_id": "t5",
                    "tool_use_id": "u5",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "cwd": str(ROOT),
                    "tool_input": {"cmd": "swift build"},
                },
            ]
            for payload in payloads:
                subprocess.run(
                    ["node", str(CAPTURE)],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    env=env,
                    check=True,
                )
            result = subprocess.run(
                [sys.executable, str(ANALYZE), "summary", "--data-dir", tmp, "--days", "1"],
                text=True,
                capture_output=True,
                check=False,
                cwd=str(ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("native UI work needs runtime proof", result.stdout)
            self.assertNotIn("no browser/web verification tools", result.stdout)


if __name__ == "__main__":
    unittest.main()
