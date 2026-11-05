"""Dashboard integration acceptance uses synthetic data and a fake Registry transport."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/atlanai-analytics/skills/atlan-analytics/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).parent / "dashboard_cases"))
import dashboard
from dashboard_publish import Publisher
from dashboard_fixture import URL, capture
from test_dax_sql import mapping
from trident.core import Service
from test_eval_flow import EvalFlow
from test_dax_sql import *  # noqa: F403 - migrated compiler regression cases
from test_file_inspector import FileInventory
from test_snowflake_cli import *  # noqa: F403 - existing query binding regressions


class DashboardPublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.service = Service(self.root / "jobs")
        self.view = self.service.dispatch("dashboard_begin", {"url": URL})
        self.job = self.view["job_id"]
        self.mutate("dashboard_capture", capture=capture())
        self.mutate("dashboard_translate", dialect="sqlite", mappings=[mapping()])
        self.context = self.root / "context"
        dashboard.prepare_context(self.service, self.job, self.context)
        self.pub = Publisher(self.context)
        self.snapshot = {"workspace_id": "workspace_demo", "complete": True,
                         "metric_schema": {"kind": "metric", "definition": {"spec_version": "1.0-draft.4"}},
                         "artifacts": []}

    def mutate(self, name, **args):
        self.view = self.service.dispatch(name, {"job_id": self.job, "expected_revision": self.view["revision"], **args})

    def test_snapshot_warning_survives_registry_plan_and_does_not_prevent_save(self):
        self.mutate('dashboard_plan_evals')
        task=self.service.dispatch('dashboard_next_check',{'job_id':self.job})['task']
        self.mutate('dashboard_record_check',
                    **{k:task[k] for k in ('case_id','plan_generation','capture_hash','definition_hash')},
                    reference={'unavailable':'source_value_unavailable'},
                    actual={'unavailable':'snapshot_mismatch'})
        dashboard.prepare_context(self.service,self.job,self.context)
        plan=self.pub.plan(self.snapshot)
        operation=self.pub.next(plan['plan_id'])
        self.assertEqual(operation['name'],'registry__create_artifact')
        body=json.loads((self.context/'metrics.json').read_text())[0]
        self.assertEqual(body['definition']['extensions']['atlan_analytics']['dashboard_validation']['parity'],'unmeasured')
        self.assertEqual(self.view['agent_readiness']['state'],'ready_with_warnings')

    def artifact(self):
        body = json.loads((self.context / "metrics.json").read_text())[0]
        return {**body, "id": "metric_demo", "kind": "metric", "workspace_id": "workspace_demo", "etag": "head-v1"}

    def test_native_composition_is_one_scalar_not_repeated_per_source_row(self):
        body = self.artifact()["definition"]
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE warehouse_accounts(id,active,amount,source)")
        db.executemany("INSERT INTO warehouse_accounts VALUES(?,?,?,?)", [(1, 1, 10, 'Direct'), (2, 0, 30, 'Direct')])
        sql = "SELECT " + body["formula"] + " FROM " + body["source"]["sql"]
        sql = re.sub(r"\{\{(\w+)\}\}", r":\1", sql)
        params = {p["name"]: p["default"] for p in body["source"]["parameters"]}
        self.assertEqual(db.execute(sql, params).fetchall(), [(1,)])
        import metrics_mine
        errors, _ = metrics_mine.check_metric({k: v for k, v in self.artifact().items() if k not in ('id','kind','workspace_id','etag')})
        self.assertEqual(errors, [])

    def test_create_readback_release_and_resume_never_replay_uncertain_write(self):
        plan = self.pub.plan(self.snapshot)
        first = self.pub.next(plan["plan_id"])
        self.assertEqual(first["name"], "registry__create_artifact")
        self.assertTrue(first["arguments"]["metadata"]["auto_release"] is False)
        self.assertEqual(self.pub.next(plan["plan_id"])["status"], "BLOCKED")
        self.pub.ack({"operation_id": first["operation_id"], "read_succeeded": True, "artifact": self.artifact()}, recover=True)
        for phase in ("head", "release"):
            call = self.pub.next(plan["plan_id"])
            self.assertEqual(call["name"], "registry__get_artifact")
            self.pub.ack({"operation_id": call["operation_id"], "read_succeeded": True,
                          "artifact": self.artifact(), **({"release_id": "release_demo"} if phase == "release" else {})})
        done = self.pub.next(plan["plan_id"])
        self.assertEqual((done["released"], done["drafts"]), (1, 0))
        self.snapshot["artifacts"] = [self.artifact()]
        second = self.pub.plan(self.snapshot)
        self.assertEqual(second["actions"][0]["action"], "reuse")

    def test_revision_uses_etag_and_increases_semantic_version(self):
        previous = self.artifact()
        previous["definition"]["version"] = 7
        previous["definition"]["formula"] = "0"
        self.snapshot["artifacts"] = [previous]
        plan = self.pub.plan(self.snapshot)
        call = self.pub.next(plan["plan_id"])
        self.assertEqual(call["name"], "registry__append_artifact_version")
        self.assertEqual(call["arguments"]["if_match"], "head-v1")
        self.assertEqual(call["arguments"]["metadata"]["definition"]["version"], 8)
        self.assertNotIn("workspace_id", call["arguments"]["metadata"])

    def test_wrong_workspace_duplicate_identity_and_missing_inventory_refuse(self):
        for changes in ({"complete": False}, {"artifacts": [{**self.artifact(), "workspace_id": "workspace_other"}]},
                        {"artifacts": [self.artifact(), {**self.artifact(), "id": "metric_duplicate"}]}):
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ValueError):
                    self.pub.plan({**self.snapshot, **changes})

    def test_wrong_receipt_and_changed_payload_cannot_finish_a_plan(self):
        plan = self.pub.plan(self.snapshot)
        call = self.pub.next(plan["plan_id"])
        with self.assertRaises(ValueError):
            self.pub.ack({"operation_id": call["operation_id"], "read_succeeded": True,
                          "artifact": {**self.artifact(), "workspace_id": "workspace_other"}}, recover=True)
        with self.assertRaises(ValueError):
            self.pub.next("another-plan")


class HostTranscripts(unittest.TestCase):
    def parse(self, events):
        import analytics_eval
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "transcript.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            return analytics_eval.parse_transcript(path)

    def test_codex_answer_and_read_tools_are_retained(self):
        result = self.parse([
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "call_demo",
             "tool": "mcp__call_read_tool", "arguments": {"name": "registry__get_artifact", "arguments": {"artifact_id": "metric_demo", "channel": "latest"}}, "result": {"value": 1}}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "An answer"}},
        ])
        self.assertGreater(result["assistant_messages"], 0)
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["contaminated"], [])

    def test_codex_shell_and_gold_reads_are_contaminated(self):
        for item in [
            {"type": "command_execution", "command": "cat eval/answers.jsonl"},
            {"type": "mcp_tool_call", "tool": "registry__get_artifact", "arguments": {"artifact_id": "dataset_record_synthetic12345"}},
        ]:
            self.assertTrue(self.parse([{"type": "item.completed", "item": item}])["contaminated"])

    def test_codex_rollout_functions_and_no_tool_flags(self):
        import analytics_case
        result = self.parse([{"type": "response_item", "payload": {"type": "function_call", "call_id": "c",
                              "name": "exec_command", "arguments": '{"cmd":"cat answers.jsonl"}'}}])
        self.assertTrue(result["contaminated"])
        from unittest.mock import patch
        with patch("analytics_case.shutil.which", return_value="/host/codex"):
            args = analytics_case.command("codex", Path("/tmp/example"), Path("/tmp/example/answer.json"))
        self.assertIn("--ignore-user-config", args)
        self.assertIn("read-only", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_question_runner_refuses_draft_and_gold_fields(self):
        import analytics_case
        for data in ([{"id": "metric_demo", "definition": {}}],
                     [{"id": "metric_demo", "release_id": "release_demo", "definition": {}, "gold_sql": "SELECT 1"}]):
            with self.assertRaises(ValueError):
                analytics_case.run_case("codex", data, "Question", Path("/not-created"))


class LivePublicationContracts(unittest.TestCase):
    def planned(self, sessions_only):
        import analytics_eval as ev
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            pr = ev.Proj(folder)
            ev.dump_json(pr.p("state.json"), {"slug": "synthetic", "ids": {
                "workspace_id": "workspace_example", "agent_id": "agent_example"}})
            (pr.vdir("codex") / "results.jsonl").write_text(json.dumps({
                "prompt_id": "one", "rep": 0, "prompt": "Question", "answer": "Answer", "model": None}) + "\n")
            return ev.cmd_publish(SimpleNamespace(dir=folder, variant="codex", sessions_only=sessions_only,
                workspace=None, agent_id=None, resend=False, baseline=None))

    def test_scorer_uses_current_definition_contract(self):
        body = self.planned(False)["calls"][0]["arguments"]["metadata"]
        self.assertEqual(body["scorer_kind"], "code")
        self.assertEqual(body["output_type"], "score")
        self.assertFalse(set(body) & {"spec", "scope", "outputs"})

    def test_unknown_host_model_is_omitted_from_nonnullable_session_field(self):
        call = self.planned(True)["calls"][0]
        self.assertEqual(call["tool"], "registry__create_artifacts")
        body = call["arguments"]["items"][0]
        self.assertNotIn("model_id", body)
        self.assertEqual(body["subject_kind"], "agent")

class ReleasedContextEvidence(unittest.TestCase):
    def test_host_citations_are_pinned_to_supplied_releases_and_deduplicated(self):
        from analytics_case import released_references
        context = [{"id": "metric_example", "release_id": "rel_reviewed"}]
        answer = {"used_context_ids": ["metric_example", "metric_example"]}
        self.assertEqual(released_references(answer, context),
                         [{"artifact_id": "metric_example", "release_id": "rel_reviewed"}])
        with self.assertRaises(KeyError):
            released_references({"used_context_ids": ["metric_other"]}, context)
