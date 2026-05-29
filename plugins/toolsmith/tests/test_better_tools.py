import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "hooks" / "capture_pre_tool_use.js"
ANALYZE = ROOT / "scripts" / "better_tools.py"
SKILL = ROOT / "skills" / "toolsmith" / "SKILL.md"
README = ROOT.parents[1] / "README.md"


def hook_env(data_root: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PLUGIN_DATA"] = data_root
    env["TOOLSMITH_WRITE_LOCATOR"] = "0"
    # Existing capture tests predate the opt-in privacy gate, so force the legacy
    # ambient-capture behavior and keep the dashboard daemon out of unit runs.
    env["TOOLSMITH_CAPTURE_MODE"] = "always"
    env["TOOLSMITH_DASHBOARD"] = "0"
    return env


class BetterToolsTests(unittest.TestCase):
    def parse_control(self, result: "subprocess.CompletedProcess[str]") -> dict:
        """Assert the hook emitted exactly one valid control JSON object."""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        text = result.stdout.strip()
        self.assertTrue(text.startswith("{") and text.endswith("}"), repr(result.stdout))
        self.assertEqual(text.count("{"), 1, repr(result.stdout))
        return json.loads(text)

    def test_capture_writes_daily_jsonl_with_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
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
            control = self.parse_control(result)
            self.assertEqual(control.get("continue"), True)
            self.assertNotIn("suppressOutput", control)
            event_files = list((Path(tmp) / "events").glob("*.jsonl"))
            self.assertEqual(len(event_files), 1)
            self.assertTrue((Path(tmp) / "indexes" / "live-index.json").exists())
            content = event_files[0].read_text()
            self.assertIn("grep -R TODO", content)
            self.assertNotIn("should-not-leak", content)
            self.assertIn("<redacted>", content)

    def test_analyzer_reports_blindspot_from_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
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
            self.assertNotIn("Research Phase Guidance", result.stdout)

    def test_index_collapses_duplicate_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
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
            env = hook_env(tmp)
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
                tool_events[0]["task"]["prompt_hash"],
                prompt_events[0]["prompt"]["prompt_hash"],
            )
            self.assertEqual(tool_events[0]["task"]["link_method"], "same_turn")
            self.assertIn("AXUIElement", tool_events[0]["task"]["prompt_excerpt"])

    def test_native_intent_does_not_emit_web_blindspot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
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
            self.assertIn("native macOS/AppKit/AX work needs target-app runtime proof", result.stdout)
            self.assertNotIn("no browser/web verification tools", result.stdout)

    def test_native_macos_prompt_with_context_url_does_not_emit_web_blindspot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
            payloads = [
                {
                    "session_id": "s-native",
                    "turn_id": "t-native",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": (
                        "https://cotypist.app/ is context. How did this person figure out text cursor "
                        "position in a native macOS Accessibility API Swift AppKit app?"
                    ),
                },
                {
                    "session_id": "s-native",
                    "turn_id": "t-native",
                    "tool_use_id": "u-native",
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
            self.assertIn("native_macos", result.stdout)
            self.assertNotIn("no browser/web verification", result.stdout)
            self.assertNotIn("web-app/front-end tasks lack browser", result.stdout)

    def test_web_app_prompt_without_browser_tool_emits_web_blindspot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
            payloads = [
                {
                    "session_id": "s-web",
                    "turn_id": "t-web",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "Fix the React web app page at localhost and verify browser rendering.",
                },
                {
                    "session_id": "s-web",
                    "turn_id": "t-web",
                    "tool_use_id": "u-web",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "cwd": str(ROOT),
                    "tool_input": {"cmd": "npm test"},
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
            self.assertIn("web-app/front-end tasks lack browser", result.stdout)
            self.assertIn("Research Phase Guidance", result.stdout)
            self.assertIn("recommend`, `defer`, or `reject", result.stdout)

    def test_mixed_native_and_web_tasks_are_recommended_per_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
            cases = [
                ("s-native", "t-native", "Native macOS Accessibility API Swift AppKit cursor bounds work.", "swift build"),
                ("s-web", "t-web", "React web app DOM rendering bug at localhost.", "npm test"),
            ]
            for session_id, turn_id, prompt, command in cases:
                for payload in (
                    {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": prompt,
                    },
                    {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "tool_use_id": f"u-{turn_id}",
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "cwd": str(ROOT),
                        "tool_input": {"cmd": command},
                    },
                ):
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
            self.assertIn("native_macos", result.stdout)
            self.assertIn("web-app/front-end tasks lack browser", result.stdout)

    def test_prompt_capture_redacts_secret_like_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
            subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps({
                    "session_id": "s-secret",
                    "turn_id": "t-secret",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "Run this with API_KEY=should-not-leak for a Swift test.",
                }),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            data = "\n".join(file.read_text() for file in (Path(tmp) / "events").glob("*.jsonl"))
            state = (Path(tmp) / "state" / "recent-prompts.json").read_text()
            self.assertNotIn("should-not-leak", data)
            self.assertNotIn("should-not-leak", state)
            self.assertIn("<redacted>", data)

    def test_tests_can_disable_home_locator_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            env = hook_env(tmp)
            env["HOME"] = home
            subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps({
                    "session_id": "s-locator",
                    "turn_id": "t-locator",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "locator smoke",
                }),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            self.assertFalse((Path(home) / ".codex" / "plugin-data" / "toolsmith" / "active-data-root.json").exists())

    def test_skill_contains_external_research_contract(self) -> None:
        text = SKILL.read_text()
        required = [
            "Research Phase gate",
            "observed gap in one sentence",
            "installed/current local tools",
            "current primary sources",
            "official docs",
            "quality bar",
            "recency bar by domain",
            "AI/LLM/agent tooling",
            "recommend",
            "defer",
            "reject",
            "Do not auto-install external candidates",
            "Candidate Tools",
            "Source Type",
            "Recency Evidence",
            "Install/Use Path",
            "Verification",
        ]
        for needle in required:
            self.assertIn(needle, text)

    def test_readme_documents_research_phase_for_users(self) -> None:
        text = README.read_text()
        required = [
            "Research Phase",
            "observed gap in one sentence",
            "installed/current local tools",
            "current primary sources",
            "official docs",
            "recommend`, `defer`, or `reject",
            "verification command or runtime proof",
        ]
        for needle in required:
            self.assertIn(needle, text)

    def test_hook_control_json_contract_by_event(self) -> None:
        cases = [
            ("UserPromptSubmit", True),
            ("PreToolUse", False),
            ("PostToolUse", False),
            ("Stop", False),
            ("SomethingFuture", False),
        ]
        for event_name, expect_suppress in cases:
            with self.subTest(event_name=event_name), tempfile.TemporaryDirectory() as tmp:
                env = hook_env(tmp)
                payload = {
                    "session_id": f"s-{event_name}",
                    "turn_id": f"t-{event_name}",
                    "hook_event_name": event_name,
                    "cwd": str(ROOT),
                }
                if event_name == "UserPromptSubmit":
                    payload["prompt"] = "contract test API_KEY=should-not-leak"
                else:
                    payload.update({
                        "tool_use_id": f"u-{event_name}",
                        "tool_name": "Bash",
                        "tool_input": {"cmd": "echo ok", "api_key": "should-not-leak"},
                    })
                result = subprocess.run(
                    ["node", str(CAPTURE)],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
                control = self.parse_control(result)
                self.assertEqual(control.get("continue"), True)
                if expect_suppress:
                    self.assertEqual(control.get("suppressOutput"), True)
                else:
                    self.assertNotIn("suppressOutput", control)
                self.assertNotIn("should-not-leak", result.stdout)

    def test_opt_in_dormant_until_toolsmith_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
            env["TOOLSMITH_CAPTURE_MODE"] = "opt-in"
            dormant = subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps({
                    "session_id": "s-opt",
                    "turn_id": "t-opt-0",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "cwd": str(ROOT),
                    "tool_input": {"cmd": "echo dormant"},
                }),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.parse_control(dormant)
            self.assertFalse((Path(tmp) / "events").exists())

            enabled = subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps({
                    "session_id": "s-opt",
                    "turn_id": "t-opt-1",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "#toolsmith begin capture API_KEY=should-not-leak",
                }),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            control = self.parse_control(enabled)
            self.assertEqual(control.get("suppressOutput"), True)

            subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps({
                    "session_id": "s-opt",
                    "turn_id": "t-opt-2",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "cwd": str(ROOT),
                    "tool_input": {"cmd": "echo after enable"},
                }),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            content = "\n".join(file.read_text() for file in (Path(tmp) / "events").glob("*.jsonl"))
            self.assertIn("begin capture", content)
            self.assertNotIn("#toolsmith", content)
            self.assertNotIn("should-not-leak", content)
            self.assertIn("after enable", content)
            self.assertNotIn("dormant", content)

    def test_dashboard_daemon_serves_aggregate_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = hook_env(tmp)
            env["TOOLSMITH_CAPTURE_MODE"] = "opt-in"
            env["TOOLSMITH_DASHBOARD"] = "1"
            env["TOOLSMITH_DASHBOARD_OPEN_BROWSER"] = "0"
            enable = subprocess.run(
                ["node", str(CAPTURE)],
                input=json.dumps({
                    "session_id": "s-dash",
                    "turn_id": "t-dash-1",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "#toolsmith open the live dashboard",
                }),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.parse_control(enable)
            meta_path = Path(tmp) / "dashboard" / "meta.json"
            meta = None
            for _ in range(50):
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text())
                    if meta.get("port") and meta.get("token"):
                        break
                time.sleep(0.1)
            self.assertIsNotNone(meta, "dashboard daemon did not publish meta.json")
            self.assertTrue(meta.get("port") and meta.get("token"))
            try:
                subprocess.run(
                    ["node", str(CAPTURE)],
                    input=json.dumps({
                        "session_id": "s-dash",
                        "turn_id": "t-dash-2",
                        "hook_event_name": "PreToolUse",
                        "tool_use_id": "u-dash",
                        "tool_name": "Bash",
                        "cwd": str(ROOT),
                        "tool_input": {"cmd": "echo dashboard", "api_key": "should-not-leak"},
                    }),
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
                state_url = f"http://127.0.0.1:{meta['port']}/state.json?token={meta['token']}"
                state: dict = {}
                for _ in range(30):
                    with urllib.request.urlopen(state_url, timeout=2) as response:
                        state = json.loads(response.read().decode("utf-8"))
                    if state.get("totals", {}).get("tool_records", 0) >= 1:
                        break
                    time.sleep(0.1)
                dumped = json.dumps(state)
                self.assertGreaterEqual(state["totals"]["records"], 2)
                self.assertGreaterEqual(state["totals"]["tool_records"], 1)
                self.assertTrue(any(item["name"] == "Bash" for item in state["top_tools"]))
                self.assertNotIn("should-not-leak", dumped)
                self.assertNotIn("open the live dashboard", dumped)
            finally:
                try:
                    os.kill(int(meta["pid"]), 15)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
