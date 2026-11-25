"""Usage telemetry in the command hooks: off until the build names an endpoint, held to the schema,
and never able to change or delay a hook's answer."""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "plugins" / "atlanai"
HOOKS = CORE / "command-hooks"
ENDPOINT = "https://telemetry.example/v1/batch"
NOW = 1795600800  # 2026-11-25T10:00:00Z


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


telemetry = load("atlan_plugin_telemetry_test", HOOKS / "telemetry.py")
SCHEMA = json.loads((HOOKS / "telemetry_events.json").read_text(encoding="utf-8"))
BUILT = json.loads((HOOKS / "telemetry_config.json").read_text(encoding="utf-8"))
# A stand-in key, built here so no key-shaped string is committed.
KEY = "test" + "key" + "12345678"
ON = {**BUILT, "endpoint": ENDPOINT, "write_key": KEY}
OFF = {**BUILT, "write_key": ""}
GATEWAY_SESSION = "mcp_session_01JABCDEFGHJKMNPQRSTVWXYZ0"
INSTALL = "0b0e7a52-7d0e-4c1b-9a3e-2f6f1c9d4e10"
COMMON = {"harness": "codex", "os": "macos", "install_id": INSTALL, "app_version": BUILT["version"]}


class Schema(unittest.TestCase):
    def test_it_is_on_exactly_when_the_build_carries_a_write_key(self):
        """As the dashboard's analytics load only with a Segment write key, and go straight to Segment."""
        self.assertEqual(BUILT["endpoint"], "https://api.segment.io/v1/batch")
        self.assertEqual(telemetry.is_on(BUILT), bool(BUILT["write_key"]))
        self.assertTrue(telemetry.is_on(ON))
        self.assertFalse(telemetry.is_on(OFF))
        self.assertFalse(telemetry.is_on({**ON, "endpoint": "http://telemetry.example/v1/batch"}))
        self.assertFalse(telemetry.is_on({**ON, "endpoint": ""}))
        self.assertFalse(telemetry.is_on({**ON, "write_key": "not a key"}))

    def test_the_category_is_the_dashboard_plans_name_for_this_codebase(self):
        self.assertEqual(SCHEMA["category"], "claude_plugin")

    def test_events_are_object_action_with_typed_properties_only(self):
        for name, event in SCHEMA["events"].items():
            self.assertRegex(name, r"^[a-z]+_[a-z_]+$")
            self.assertTrue(set(event["hosts"]) <= set(SCHEMA["enums"]["harness"]), name)
            for key, spec in event["props"].items():
                self.assertIn(spec["type"], ("boolean", "version", "enum", "listed", "mcp_session"), f"{name}.{key}")
                if spec["type"] == "enum":
                    self.assertIn(spec["of"], SCHEMA["enums"], f"{name}.{key}")
                if spec["type"] == "listed":
                    self.assertIn(spec["of"], ("skills", "commands"), f"{name}.{key}")

    def test_no_event_can_carry_free_text(self):
        """Every string a message carries is from a closed list, a version, or an id made here."""
        for name, event in SCHEMA["events"].items():
            for spec in event["props"].values():
                self.assertNotIn(spec["type"], ("string", "text", "integer"), name)

    def test_the_hooks_module_is_built_from_the_same_schema(self):
        text = (CORE / "claude-hooks" / "telemetry-config.ts").read_text(encoding="utf-8")
        for name in SCHEMA["events"]:
            self.assertIn(f'"{name}"', text)
        for values in SCHEMA["enums"].values():
            for value in values:
                self.assertIn(f'"{value}"', text)

    def test_every_event_is_in_the_readme(self):
        readme = (CORE / "README.md").read_text(encoding="utf-8")
        for name in SCHEMA["events"]:
            self.assertIn(f"`{SCHEMA['category']}_{name}`", readme)
        for word in ("DO_NOT_TRACK", "DISABLE_TELEMETRY", "ATLAN_PLUGIN_TELEMETRY", "telemetry-off"):
            self.assertIn(word, readme)

    def test_the_lists_are_this_repositorys_own_skills_and_commands(self):
        skills = {f"{path.parent.parent.parent.name}:{path.parent.name}" for path in (ROOT / "plugins").glob("*/skills/*/SKILL.md")}
        self.assertEqual(set(BUILT["skills"]), skills)
        self.assertIn("atlanai:continue", BUILT["commands"])


class Compose(unittest.TestCase):
    def compose(self, name, props, common=None):
        return telemetry.compose(name, props, common or COMMON, SCHEMA, ON, "2026-11-25T10:00:00Z", "m-1")

    def test_writes_a_segment_track_message_in_the_dashboards_grammar(self):
        message = self.compose("publish_refused", {"operation": "skill_create", "reason": "credential-file"}, {**COMMON, "session": "abcdef0123456789"})
        self.assertEqual(message["type"], "track")
        self.assertEqual(message["event"], "claude_plugin_publish_refused")
        self.assertEqual(message["anonymousId"], INSTALL)
        self.assertEqual(message["context"]["ip"], "0.0.0.0")
        self.assertEqual(message["properties"], {
            "category": "claude_plugin", "object": "publish", "action": "refused", "app_version": BUILT["version"],
            "plugin": BUILT["plugin"], "harness": "codex", "os": "macos", "install_id": INSTALL,
            "session": "abcdef0123456789", "operation": "skill_create", "reason": "credential-file"})

    def test_drops_anything_outside_the_schema(self):
        self.assertIsNone(self.compose("prompt_submitted", {"text": "hello"}))
        self.assertIsNone(self.compose("publish_refused", {"operation": "skill_create", "reason": "/Users/someone/secret.pem"}))
        self.assertIsNone(self.compose("publish_refused", {"operation": "skill_create"}))
        self.assertIsNone(self.compose("skill_invoked", {"skill": "atlanai:atlan-continue"}))  # Claude Code's alone
        self.assertIsNone(self.compose("install_active", {"first_seen": True}, {**COMMON, "install_id": "someone@example.com"}))
        self.assertIsNone(self.compose("install_active", {"first_seen": "yes"}))
        self.assertIsNone(self.compose("publish_prepared", {"operation": "bulk_create", "mcp_session_id": "my notes about the merger"}))
        joined = self.compose("publish_prepared", {"operation": "bulk_create", "mcp_session_id": GATEWAY_SESSION})
        self.assertEqual(joined["properties"]["mcp_session_id"], GATEWAY_SESSION)
        kept = self.compose("publish_prepared", {"operation": "bulk_create", "path": "/work/payload.json"})
        self.assertNotIn("path", kept["properties"])
        self.assertNotIn("session", self.compose("install_active", {"first_seen": True}, {**COMMON, "session": "session-1"})["properties"])

    def test_folds_refusals_errors_and_operations(self):
        self.assertEqual(telemetry.refusal_of("demo/.env:1:1: credential-file: looks like a credential file\n1 files", "skill_create"), "credential-file")
        self.assertEqual(telemetry.refusal_of("SKILL.md:1:1: frontmatter-description: too long", "skill_create"), "frontmatter")
        self.assertEqual(telemetry.refusal_of("SKILL.md:4:2: zero-width: U+200B is invisible", "skill_create"), "hidden-text")
        self.assertEqual(telemetry.refusal_of("too-many-files: 200 files", "skill_create"), "too-many-files")
        self.assertEqual(telemetry.refusal_of("The local publication batch was refused.", "bulk_create"), "local-batch")
        self.assertEqual(telemetry.refusal_of("The receipt destination could not be prepared.", "bulk_create"), "receipt")
        self.assertEqual(telemetry.refusal_of(None, "skill_create"), "other")
        for text, reason in (("401 authentication required", "auth"), ('{"status":403,"code":"forbidden"}', "permission_denied"),
                             ('{"code":"not_found"}', "not_found"), ("JSON-RPC -32003 rate_limited", "rate_limited"),
                             ("HTTP 503 Service Unavailable", "server_error"), ("upstream timed out", "timeout"),
                             ('{"code":"invalid_arguments"}', "client_error"), ("boom", "unknown")):
            self.assertEqual(telemetry.failure_reason(text), reason)
            self.assertIn(reason, SCHEMA["enums"]["failure"])
        self.assertEqual(telemetry.operation_of("extension__skill__create"), "skill_create")
        self.assertEqual(telemetry.operation_of("registry__create_artifacts"), "bulk_create")
        self.assertEqual(telemetry.operation_of("registry__publish_output"), "output")
        self.assertIsNone(telemetry.operation_of("registry__get_artifact"))
        for folded in telemetry.RULE_CLASS.values():
            self.assertIn(folded, SCHEMA["enums"]["refusal"])


class Observe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "config"
        self.env = {"XDG_CONFIG_HOME": str(self.home)}
        self.sent = []

    def observe(self, entry, event, result, config=ON, env=None, root=CORE, host="codex", now=NOW):
        body = telemetry.observe(entry, host, event, result, env=self.env if env is None else env, spawn=self.sent.append,
                                 now=now, root=root, config=config)
        return json.loads(body)["batch"] if body else None

    def test_with_no_write_key_it_sends_nothing_and_writes_nothing(self):
        self.assertIsNone(self.observe("session-start", {"session_id": "s"}, {}, config=OFF))
        self.assertEqual(self.sent, [])
        self.assertFalse(self.home.exists())

    def test_session_start_reports_the_install_active_once_a_day(self):
        first = self.observe("session-start", {"session_id": "session-1", "cwd": "/Users/someone/work"}, {})
        self.assertEqual([m["event"] for m in first], ["claude_plugin_install_active"])
        self.assertIs(first[0]["properties"]["first_seen"], True)
        self.assertRegex(first[0]["anonymousId"], telemetry.UUID)
        self.assertRegex(first[0]["properties"]["session"], r"^[0-9a-f]{16}$")
        self.assertEqual(first[0]["timestamp"], "2026-11-25T10:00:00Z")
        self.assertIsNone(self.observe("session-start", {"session_id": "session-2"}, {}))
        tomorrow = self.observe("session-start", {"session_id": "session-3"}, {}, now=NOW + 86400)
        self.assertIs(tomorrow[0]["properties"]["first_seen"], False)
        self.assertEqual(tomorrow[0]["anonymousId"], first[0]["anonymousId"])
        wire = "\n".join(self.sent)
        for leak in ("session-1", "/Users/someone", str(self.home)):
            self.assertNotIn(leak, wire)
        self.assertEqual(sorted(path.name for path in (self.home / "atlan-plugin").iterdir()), ["active-codex", "install-id"])
        self.assertEqual(os.stat(self.home / "atlan-plugin" / "install-id").st_mode & 0o077, 0)

    def test_session_start_reports_an_update_notice_of_this_build(self):
        notice = f"Atlan local publish hooks are installed.\nThe atlanai plugin is at {BUILT['version']} and 9.9.9 is released. Say it once."
        batch = self.observe("session-start", {"session_id": "s"}, {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": notice}})
        self.assertEqual([m["event"] for m in batch], ["claude_plugin_install_active", "claude_plugin_update_prompted"])
        self.assertEqual({k: batch[1]["properties"][k] for k in ("from_version", "to_version", "forced")},
                         {"from_version": BUILT["version"], "to_version": "9.9.9", "forced": False})
        cursor = self.observe("session-start", {"conversation_id": "c"}, {"additional_context": notice}, host="cursor")
        self.assertEqual([m["event"] for m in cursor], ["claude_plugin_install_active", "claude_plugin_update_prompted"])

    def test_pre_reports_what_the_helper_did_never_what_the_call_held(self):
        call = {"session_id": "s", "cwd": "/Users/someone/work", "tool_name": "mcp__atlan__mcp__call_write_tool",
                "tool_input": {"name": "extension__skill__create", "arguments": {"skills": [{"files_from": "private-skill-name"}]}}}
        refusal = "private-skill-name/.env:1:1: credential-file: looks like a credential file; leave it out\nNothing was sent."
        codex = self.observe("pre", call, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": refusal}})
        self.assertEqual(codex[0]["event"], "claude_plugin_publish_refused")
        self.assertEqual((codex[0]["properties"]["operation"], codex[0]["properties"]["reason"]), ("skill_create", "credential-file"))
        cursor = self.observe("pre", call, {"permission": "deny", "agent_message": refusal}, host="cursor")
        self.assertEqual(cursor[0]["properties"]["reason"], "credential-file")
        copilot = self.observe("pre", {"sessionId": "s", "toolArgs": json.dumps(call["tool_input"])}, {"permissionDecision": "deny", "permissionDecisionReason": refusal}, host="copilot")
        self.assertEqual(copilot[0]["properties"]["harness"], "copilot")
        prepared = self.observe("pre", call, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"files": {"SKILL.md": "secret text"}}}})
        self.assertEqual(prepared[0]["event"], "claude_plugin_publish_prepared")
        self.assertNotIn("mcp_session_id", prepared[0]["properties"])
        for session, kept in ((GATEWAY_SESSION, True), ("a note the model wrote", False), (42, False)):
            joined = self.observe("pre", {**call, "tool_input": {**call["tool_input"], "session_id": session}}, {"permission": "allow", "updated_input": {}}, host="cursor")
            self.assertEqual(joined[0]["properties"].get("mcp_session_id"), GATEWAY_SESSION if kept else None)
        self.assertIsNone(self.observe("pre", call, None))
        self.assertIsNone(self.observe("pre", {**call, "tool_input": {"name": "registry__get_artifact"}}, {"permission": "allow", "updated_input": {}}))
        self.assertIsNone(self.observe("post", call, {"additional_context": "x"}))
        wire = "\n".join(self.sent)
        for leak in ("private-skill-name", "secret text", ".env", "/Users/someone", "a note the model wrote"):
            self.assertNotIn(leak, wire)

    def test_every_switch_turns_it_off_and_leaves_no_state(self):
        for variables in ({"DO_NOT_TRACK": "1"}, {"DISABLE_TELEMETRY": "true"}, {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
                          {"ATLAN_PLUGIN_TELEMETRY": "off"}, {"ATLAN_PLUGIN_TELEMETRY": "0"}, {"CI": "true"}):
            self.assertIsNone(self.observe("session-start", {"session_id": "s"}, {}, env={**self.env, **variables}), variables)
        self.assertFalse(self.home.exists())
        (self.home / "atlan-plugin").mkdir(parents=True)
        (self.home / "atlan-plugin" / "telemetry-off").write_text("")
        self.assertIsNone(self.observe("session-start", {"session_id": "s"}, {}))
        self.assertEqual([path.name for path in (self.home / "atlan-plugin").iterdir()], ["telemetry-off"])
        self.assertEqual(self.sent, [])

    def test_a_copy_another_environment_serves_stays_silent(self):
        for change in ({"name": "atlanai-copy"}, {"url": "https://api.dev.example/mcp"}):
            copy = Path(self.tmp.name) / f"copy-{len(change)}-{next(iter(change))}"
            copy.mkdir()
            (copy / "plugin.json").write_text(json.dumps({"name": change.get("name", BUILT["plugin"])}))
            (copy / "mcp.json").write_text(json.dumps({"mcpServers": {"atlan": {"url": change.get("url", BUILT["mcp_url"])}}}))
            self.assertIsNone(self.observe("session-start", {"session_id": "s"}, {}, root=copy), change)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.home.exists())

    def test_an_unknown_harness_an_http_endpoint_and_a_fault_all_say_nothing(self):
        self.assertIsNone(self.observe("session-start", {}, {}, host="claude"))
        self.assertIsNone(self.observe("session-start", {}, {}, config={**ON, "endpoint": "http://telemetry.example"}))

        def broken(_body):
            raise OSError("no child")
        self.assertIsNone(telemetry.observe("session-start", "codex", {}, {}, env=self.env, spawn=broken, now=NOW, root=CORE, config=ON))
        self.assertIsNone(telemetry.observe("session-start", "codex", None, object(), env=None, spawn=broken, config={"endpoint": ENDPOINT, "write_key": KEY}))

    def test_state_is_never_written_through_a_link(self):
        target = Path(self.tmp.name) / "elsewhere"
        target.mkdir()
        self.home.mkdir()
        (self.home / "atlan-plugin").symlink_to(target, target_is_directory=True)
        self.assertIsNone(self.observe("session-start", {"session_id": "s"}, {}))
        self.assertEqual(list(target.iterdir()), [])


class Send(unittest.TestCase):
    BODY = json.dumps({"batch": [{"type": "track", "event": "claude_plugin_install_active"}]})

    def test_posts_json_to_the_builds_endpoint_only(self):
        seen = []
        self.assertTrue(telemetry.send(self.BODY, config=ON, post=lambda request: seen.append(request) or True))
        self.assertEqual(seen[0].full_url, ENDPOINT)
        self.assertEqual(seen[0].get_method(), "POST")
        self.assertEqual(seen[0].data, self.BODY.encode())
        self.assertEqual(seen[0].get_header("Content-type"), "application/json")

    def test_refuses_no_key_http_an_oversized_or_a_malformed_body(self):
        never = lambda request: self.fail("posted")  # noqa: E731
        self.assertFalse(telemetry.send(self.BODY, config=OFF, post=never))
        self.assertFalse(telemetry.send(self.BODY, config={**ON, "endpoint": "http://telemetry.example"}, post=never))
        self.assertFalse(telemetry.send(json.dumps({"batch": "x" * 40000}), config=ON, post=never))
        self.assertFalse(telemetry.send(json.dumps({"events": []}), config=ON, post=never))

    def test_the_child_exits_clean_on_anything(self):
        for stdin in ("", "not json", self.BODY):
            ran = subprocess.run([sys.executable, "-B", str(HOOKS / "telemetry.py"), "send"], input=stdin, capture_output=True, text=True, timeout=20)
            self.assertEqual((ran.returncode, ran.stdout, ran.stderr), (0, "", ""))


class InTheHook(unittest.TestCase):
    """publish_hook.py's main: the answer goes out first and unchanged, then telemetry looks at it."""

    def setUp(self):
        self.hook = load("atlan_publish_hook_telemetry_test", HOOKS / "publish_hook.py")

    def run_main(self, entry, event, observe):
        out = io.StringIO()
        with patch.object(self.hook, "load_telemetry", lambda: type("T", (), {"observe": staticmethod(observe)})), \
                patch.object(self.hook, "update_notice", lambda host: None), \
                patch.object(sys, "stdin", io.StringIO(json.dumps(event))), patch.object(sys, "stdout", out):
            code = self.hook.main([entry, "--host", "codex"])
        return code, out.getvalue()

    def test_the_answer_is_written_before_telemetry_runs_and_is_what_telemetry_sees(self):
        seen = []

        def observe(entry, host, event, result):
            seen.append((entry, host, event, result, sys.stdout.getvalue()))
        code, out = self.run_main("session-start", {"session_id": "s"}, observe)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(seen[0][:3], ("session-start", "codex", {"session_id": "s"}))
        self.assertEqual(seen[0][3], json.loads(out))
        self.assertEqual(seen[0][4], out)

    def test_a_telemetry_fault_changes_nothing(self):
        def observe(*_):
            raise RuntimeError("telemetry broke")
        code, out = self.run_main("session-start", {"session_id": "s"}, observe)
        self.assertEqual(code, 0)
        self.assertIn("SessionStart", out)

    def test_the_real_hook_starts_no_process_under_an_opt_out(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("spawned")), \
                patch.dict(os.environ, {"DO_NOT_TRACK": "1"}), \
                patch.object(self.hook, "update_notice", lambda host: None), \
                patch.object(sys, "stdin", io.StringIO("{}")), patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(self.hook.main(["session-start", "--host", "codex"]), 0)

    def test_the_hook_names_only_its_own_endpoint(self):
        """One fixed host in the sender's source: the build's endpoint is the only place a batch goes."""
        source = (HOOKS / "telemetry.py").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"https?://[^\s\"')]+", source), [])


if __name__ == "__main__":
    unittest.main()
