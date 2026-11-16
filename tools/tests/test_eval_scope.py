"""eval-generation's scope.py decides and writes the set; accuracy-check's check.py runs any set the same way."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / "plugins/atlanai-analytics/skills/eval-generation/scripts/scope.py"
CHECK = ROOT / "plugins/atlanai-analytics/skills/accuracy-check/scripts/check.py"
ACC = ROOT / "plugins/atlanai-analytics/skills/analytics-demo/scripts/accuracy.py"
sys.path.insert(0, str(ACC.parent))
sys.path.insert(0, str(CHECK.parent))
import check  # noqa: E402


def query(vq: str, lane: str, runs: int | None = None) -> dict:
    prov = {"lane": lane, "bi": {"visual": vq} if lane == "bi" else None,
            "history": {"n_exec": runs, "n_distinct_users": 2} if lane == "history" else None}
    return {"name": vq, "definition": {"sql": f"SELECT 1 AS {vq.replace('-', '_')}", "dialect": "sqlite",
                                       "extensions": {"metrics_agent": {"vq_id": vq, "evidence": {"provenance": prov}}}}}


class ScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        run = self.tmp / ".atlan-metrics" / "run_0000000000000001"
        (run / "context").mkdir(parents=True)
        (run / "engine").mkdir()
        (run / "interaction.json").write_text(json.dumps({
            "focus": "metric:Net Revenue",
            "intent": {"domain": "Revenue", "purpose": "Build a revenue agent that answers why revenue fell by region"}}))
        cards = [("net-revenue", "Net Revenue", "Revenue after refunds"),
                 ("refund-rate", "Refund Rate", "Share of revenue refunded, which explains revenue falls"),
                 ("headcount", "Headcount", "People employed"),
                 ("region-mix", "Region Mix", "Revenue share by region")]
        (run / "context" / "metrics.json").write_text(json.dumps(
            [{"name": n, "definition": {"source": {"entity": "s.orders"}}} for _, n, _ in cards]))
        (run / "context" / "queries.json").write_text(json.dumps([
            query("tile-rev", "bi"), query("h-rev", "history", 40), query("h-rev2", "history", 3),
            query("h-refund", "history", 5), query("h-head", "history", 90), query("h-mix", "history", 8)]))
        (run / "engine" / "metric_cards.json").write_text(json.dumps({
            "metrics": [{"metric_ref": r, "display_name": n, "description": d} for r, n, d in cards],
            "metric_refs_by_vq": {"tile-rev": ["net-revenue"], "h-rev": ["net-revenue"], "h-rev2": ["net-revenue"],
                                  "h-refund": ["refund-rate"], "h-head": ["headcount"], "h-mix": ["region-mix"]}}))
        (run / "run.json").write_text(json.dumps({"execution": {"kind": "sqlite", "path": "x.sqlite"}}))
        self.run, self.ev = run, self.tmp / "evals"

    def warehouse_run(self, root: str = ".atlan-analytics", name: str = "support-tickets") -> Path:
        """A warehouse build: named after its use case, no local binding, no interview record."""
        run = self.tmp / root / name
        (run / "context").mkdir(parents=True)
        (run / "engine-v2").mkdir()
        (run / "run.json").write_text(json.dumps({"engine_run": "engine-v2"}))
        (run / "state.json").write_text(json.dumps({"warehouse": {"kind": "snowflake", "dialect": "snowflake",
                                                                 "tool": "mcp__wh__sql_exec_tool"}}))
        (run / "context" / "metrics.json").write_text(json.dumps([{"name": "Tickets", "definition": {"source": {"entity": "L.Z.TICKET"}}}]))
        q = query("h-tix", "history", 7)
        q["definition"]["dialect"] = "snowflake"
        (run / "context" / "queries.json").write_text(json.dumps([q]))
        (run / "engine-v2" / "metric_cards.json").write_text(json.dumps({
            "metrics": [{"metric_ref": "tickets", "display_name": "Tickets", "description": "Support tickets created"}],
            "metric_refs_by_vq": {"h-tix": ["tickets"]}}))
        return run

    @staticmethod
    def transcript(path: Path, calls: list) -> Path:
        """A subagent transcript: each (sql, result text, is_error) as a tool call and its result."""
        lines = []
        for i, (sql, text, bad) in enumerate(calls):
            lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "mcp__wh__sql_exec_tool", "input": {"sql": sql}}]}})
            lines.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": bad,
                 "content": [{"type": "text", "text": text}]}]}})
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        return path

    def scope(self, *args) -> dict:
        out = subprocess.run([sys.executable, str(SCOPE), *args], capture_output=True, text=True)
        return json.loads(out.stdout)

    def acc(self, *args) -> dict:
        out = subprocess.run([sys.executable, str(ACC), *args], capture_output=True, text=True)
        return json.loads(out.stdout)

    def check(self, *args) -> dict:
        out = subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True)
        return json.loads(out.stdout)

    def test_fixes_are_checked_before_they_are_written(self):
        ev = self.tmp / "ev"
        (ev / "eval").mkdir(parents=True)
        (ev / "eval" / "cases.jsonl").write_text(json.dumps({"case_id": "q1"}) + "\n")
        good = [{"misses": ["q1"], "why": "Different filter", "metric": "M", "rule_now": "all rows",
                 "rule_proposed": "closed rows", "changes": [{"path": "source.sql", "value": "x"}]},
                {"misses": ["q1"], "why": "Question asks the opposite", "kind": "redraft"}]
        (self.tmp / "f.json").write_text(json.dumps(good))
        out = self.acc("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))
        self.assertEqual((out["fixes"], out["recommended"]), (2, "M"))
        for bad in ([{**good[0], "misses": ["q9"]}], [{"misses": ["q1"], "why": "Different rule", "kind": "redraft"}], [{**good[0], "changes": [{"path": "version", "value": 2}]}],
                    [{**good[0], "rule_now": ""}]):
            (self.tmp / "f.json").write_text(json.dumps(bad))
            self.assertEqual(self.acc("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))["status"], "BLOCKED")

    def test_inputs_reads_the_use_case_and_links_every_query_to_its_metrics(self) -> None:
        res = self.scope("inputs", "--project", str(self.tmp))
        self.ev = Path(res["gen"])
        self.assertEqual((res["focus"], res["pool"], res["tiles"]), ("Net Revenue", 6, 1))
        self.assertEqual(res["run"], str(self.run))
        rows = [json.loads(l) for l in (self.ev / "src" / "pool.jsonl").read_text().splitlines()]
        self.assertEqual({r["id"]: r["exec"] for r in rows}["h-head"], 90)

    def test_pick_takes_the_focus_metric_first_then_the_metrics_nearest_the_use_case(self) -> None:
        self.ev = Path(self.scope("inputs", "--project", str(self.tmp))["gen"])
        work = self.ev / "work"
        (work / "prompts").mkdir(parents=True)
        cands = [{"id": f"m{i}", "query_id": vq, "scores": {"x": 0.5}} for i, vq in
                 enumerate(["h-head", "h-rev2", "tile-rev", "h-refund", "h-rev", "h-mix"])]
        (work / "candidates.json").write_text(json.dumps(cands))
        (work / "answers.json").write_text(json.dumps({c["id"]: {} for c in cands}))
        for c in cands:
            (work / "prompts" / f"{c['id']}.txt").write_text("p")
        res = self.scope("pick", "--gen", str(self.ev), "--limit", "4")
        picked = [(s["metric"], s["from"]) for s in res["selection"]]
        # Two on the focus metric (its tile, then the most-run query), then breadth by relevance:
        # the refund and region metrics speak to "why revenue fell by region"; headcount, run most, does not.
        self.assertEqual(picked, [("Net Revenue", "dashboard tile"), ("Net Revenue", "past query, run 40 times"),
                                  ("Region Mix", "past query, run 8 times"), ("Refund Rate", "past query, run 5 times")])
        self.assertEqual(len(list((work / "prompts").glob("*.txt"))), 4)
        self.assertEqual(len(json.loads((work / "answers.json").read_text())), 4)

    def test_pick_fills_a_short_set_with_past_queries_no_metric_computes(self) -> None:
        self.ev = Path(self.scope("inputs", "--project", str(self.tmp))["gen"])
        work = self.ev / "work"
        work.mkdir(parents=True, exist_ok=True)
        cands = [{"id": "m0", "query_id": "h-rev", "scores": {}}, {"id": "m1", "query_id": "h-adhoc", "scores": {}}]
        (work / "candidates.json").write_text(json.dumps(cands))
        res = self.scope("pick", "--gen", str(self.ev))
        self.assertEqual([(s["id"], s["metric"]) for s in res["selection"]], [("m0", "Net Revenue"), ("m1", "")])
        self.assertEqual((res["picked"], res["metrics_covered"], res["no_metric"]), (2, 1, 1))

    def test_a_generated_set_asks_for_twenty_questions(self) -> None:
        sys.path.insert(0, str(ROOT / "plugins/atlanai-analytics/skills/eval-generation/scripts"))
        import scope
        seen = {}
        original, scope.cmd_pick = scope.cmd_pick, lambda a: seen.update(limit=a.limit, focus=a.focus_count) or {"status": "ok"}
        try:
            scope.main(["pick", "--gen", str(self.ev)])
        finally:
            scope.cmd_pick = original
        self.assertEqual(seen, {"limit": 20, "focus": 2})

    def test_a_run_without_a_use_case_is_refused(self) -> None:
        (self.run / "interaction.json").write_text("{}")
        res = self.scope("inputs", "--project", str(self.tmp))
        self.assertEqual(res["status"], "BLOCKED")

    def test_emit_writes_the_set_in_the_shape_a_brought_set_has(self) -> None:
        gen = Path(self.scope("inputs", "--project", str(self.tmp))["gen"])
        (gen / "work").mkdir(parents=True, exist_ok=True)
        (gen / "work" / "spec.json").write_text(json.dumps({"records": [
            {"id": "m1", "question": "Total net revenue?", "golden_sql": "SELECT 1", "review_status": "pending"},
            {"id": "m2", "question": "Refund rate?", "golden_sql": "SELECT 2", "review_status": "rejected"}]}))
        (gen / "work" / "selection.json").write_text(json.dumps([{"id": "m1", "metric": "Net Revenue", "from": "dashboard tile"}]))
        res = self.scope("emit", "--gen", str(gen), "--name", "Revenue check")
        rows = [json.loads(l) for l in Path(res["set"]).read_text().splitlines()]
        self.assertEqual(Path(res["set"]), self.tmp / ".atlan-evals" / "sets" / "revenue-check.jsonl")
        self.assertEqual(rows, [{"id": "m1", "question": "Total net revenue?", "sql": "SELECT 1",
                                 "tests": "Net Revenue", "from": "dashboard tile", "generated": True}])

    def test_ready_turns_any_set_file_into_an_approved_spec_to_save(self) -> None:
        sets = self.tmp / ".atlan-evals" / "sets"
        sets.mkdir(parents=True)
        (sets / "finance.jsonl").write_text('{"id": "f1", "question": "Q?", "sql": "SELECT 1", "from": "dashboard tile"}\n')
        res = self.scope("ready", "--set", str(sets / "finance.jsonl"))
        spec = json.loads((Path(res["dir"]) / "work" / "spec.json").read_text())
        self.assertEqual((res["slug"], [(r["id"], r["review_status"]) for r in spec["records"]]), ("finance", [("f1", "approved")]))

    def test_start_finds_the_run_and_the_sets_and_a_scored_set_gets_a_fresh_folder(self) -> None:
        (self.tmp / ".atlan-metrics" / "run_unbuilt").mkdir()  # location's fresh, empty run
        sets = self.tmp / ".atlan-evals" / "sets"
        sets.mkdir(parents=True)
        (sets / "finance.jsonl").write_text('{"question": "Q?", "sql": "SELECT 1"}\n')
        res = self.check("start", "--project", str(self.tmp))
        self.assertEqual((res["run"], res["context_saved_to"], [(s["name"], s["questions"]) for s in res["sets"]]),
                         (str(self.run), None, [("finance", 1)]))
        self.assertEqual([o["label"] for o in res["ask"]["options"]], ["finance (1 question)", "Generate 20 new questions"])
        (self.run / "context" / "published.json").write_text(json.dumps({"metric:yard:1": "metric_01x"}))
        (self.run / "authorization.json").write_text(json.dumps({"workspace_id": "workspace_01saved"}))
        self.assertEqual(self.check("start", "--project", str(self.tmp))["context_saved_to"], "workspace_01saved")
        (self.run / "context" / "published.json").write_text(json.dumps({"metrics": {"Cost": "metric_01x"}}))
        self.assertEqual(self.check("start", "--project", str(self.tmp))["context_saved_to"], "workspace_01saved")
        (self.run / "context" / "published.json").unlink()
        (self.run / "authorization.json").write_text(json.dumps({"workspace_id": "workspace_01plan",
                                                                  "approved_plans": [{"plan_id": "p1"}]}))
        self.assertEqual(self.check("start", "--project", str(self.tmp))["context_saved_to"], "workspace_01plan")
        (self.run / "authorization.json").unlink()
        (self.run / "context" / "published-manual.json").write_text(json.dumps({"workspace_id": "workspace_01abc"}))
        self.assertEqual(self.check("start", "--project", str(self.tmp))["context_saved_to"], "workspace_01abc")
        first = check.evals_dir(self.tmp, self.run)
        (first / "hillclimb").mkdir(parents=True)
        self.assertTrue(str(check.evals_dir(self.tmp, self.run)).endswith(self.run.name + "-2"))

    def test_version_applies_the_fix_to_the_saved_definition_and_refuses_a_moved_head(self) -> None:
        ev, run = self.tmp / "ev", self.tmp / "run"
        (run / "context").mkdir(parents=True)
        (run / "context" / "metrics.json").write_text(json.dumps([{"name": "Cost", "display_name": "Cost",
            "description": "Old.", "definition": {"version": 1, "formula": "SUM(a)", "examples": [{"sql": "SELECT\n1"}],
            "extensions": {"metrics_agent": {"commit": "c1", "card": {"source_id": "m:1", "gotchas": ["Note."],
                                                                         "formula": "SUM(a)", "bi_definitions": [{"formula": "X = \"y\""}]}}}}}]))
        ev.mkdir()
        (ev / "fixes.json").write_text(json.dumps([{"metric": "Cost", "misses": ["q1"], "changes": [
            {"path": "formula", "value": "SUM(b)"}, {"path": "description", "value": "New."}]}]))
        out = self.acc("version", "--evals", str(ev), "--run", str(run), "--metric", "Cost", "--head-version", "1")
        meta = out["metadata"]
        self.assertEqual((out["version"], meta["description"], meta["definition"]["formula"],
                          meta["definition"]["examples"][0]["sql"]), (2, "New.", "SUM(b)", "SELECT\n1"))
        self.assertEqual(meta["definition"]["extensions"]["metrics_agent"],
                         {"commit": "c1", "card": {"source_id": "m:1", "gotchas": ["Note."]}})
        moved = self.acc("version", "--evals", str(ev), "--run", str(run), "--metric", "Cost", "--head-version", "3")
        self.assertEqual(moved["status"], "BLOCKED")

    def test_result_and_saved_texts_come_from_the_scores(self) -> None:
        from unittest import mock
        acc = __import__("accuracy")
        ev = self.tmp / "ev"
        for v in ("baseline", "v1"):
            (ev / "hillclimb" / v).mkdir(parents=True)
            (ev / "hillclimb" / v / "results.jsonl").write_text("{}\n")
        scores = {"baseline": {"line": "71% accuracy: 5 of 7", "n": 7, "pct": 71},
                  "v1": {"line": "100% accuracy: 7 of 7", "n": 7, "pct": 100}}
        with mock.patch.object(acc, "_score", lambda e, v: scores[v]):
            ns = lambda **kw: __import__("argparse").Namespace(**{**dict(evals=str(ev), agent="job agent", firm="Acme",
                                                                         changed=None, misses=None, generated=False), **kw})
            text = acc.cmd_result(ns(changed="the rule changed"))["text"]
            self.assertIn("7 OUT OF 7 RIGHT", text)
            self.assertIn("Your job agent has reached 100% accuracy, up from 71%, now that the rule changed.", text)
            self.assertTrue(text.endswith("100% accuracy: 7 of 7"))
            with self.assertRaises(acc.Blocked):
                acc.cmd_result(ns())
            (ev / "state.json").write_text(json.dumps({"ids": {"agent_id": "agent_01a"}}))
            saved = acc.cmd_saved(__import__("argparse").Namespace(evals=str(ev), agent="job agent", workspace="WS",
                                                                     org="acme", origin="https://app.atlan.com",
                                                                     released=["Cost:2"], withdrawn=None))["text"]
            self.assertEqual(saved.splitlines(), ["Saved to WS:", "  job agent, with 7 questions and 2 runs scored from 71% to 100%",
                                                  "  Cost version 2, released",
                                                  "See the runs in Atlan: https://app.atlan.com/acme/registry/evals/experiments/agent/agent_01a?ws=all"])
            withdrawn = acc.cmd_saved(__import__("argparse").Namespace(evals=str(ev), agent="job agent", workspace="WS",
                                                                         org="acme", origin="https://app.atlan.com",
                                                                         released=None, withdrawn=["Cost:2"]))["text"]
            self.assertIn("  Cost version 2, withdrawn", withdrawn.splitlines())

    def test_link_names_the_agents_runs_and_the_last_run(self) -> None:
        ev = self.tmp / ".atlan-evals" / "saved"
        ev.mkdir(parents=True)
        (ev / "state.json").write_text(json.dumps({"ids": {"agent_id": "agent_01a", "experiments": {
            "baseline": "experiment_01b", "v1": "experiment_01c"}}}))
        res = self.check("link", "--evals", str(ev), "--org", "acme")
        base = "https://app.atlan.com/acme/registry/evals/experiments/agent/agent_01a"
        self.assertEqual((res["runs"], res["last_run"]), (base + "?ws=all", base + "/experiment_01c?ws=all"))

    def test_a_set_loads_as_approved_and_drops_a_question_whose_sql_does_not_run(self) -> None:
        sets = self.tmp / ".atlan-evals" / "sets"
        sets.mkdir(parents=True)
        (sets / "finance.jsonl").write_text(
            '{"id": "m1", "question": "Total net revenue?", "sql": "SELECT 1", "tests": "Net Revenue", "note": "booked orders only"}\n'
            '{"question": "Refund rate?", "sql": "SELECT nope"}\n')
        staged = []
        real_gold, real_stage = check.gold_local, check.stage
        check.gold_local = lambda run, ev, recs: {"ran": [{"id": "m1", "rows": 1, "first": [9]}],
                                                     "failed": [{"id": "q2", "why": "did not run"}]}
        check.stage = lambda ev, slug: staged.append(slug)
        try:
            import argparse
            res = check.cmd_load(argparse.Namespace(project=str(self.tmp), file=str(sets / "finance.jsonl"), run=None))
        finally:
            check.gold_local, check.stage = real_gold, real_stage
        ev = Path(res["evals"])
        spec = json.loads((ev / "work" / "spec.json").read_text())
        self.assertEqual([(r["id"], r["review_status"]) for r in spec["records"]], [("m1", "approved")])
        self.assertEqual((res["questions"], res["dropped"][0]["id"], staged, res["generated"]), (1, "q2", ["finance"], False))
        self.assertEqual(json.loads((ev / "work" / "selection.json").read_text())[0]["metric"], "Net Revenue")

    def test_a_warehouse_run_is_found_by_name_and_says_its_sql_runs_in_the_host(self) -> None:
        wh = self.warehouse_run()
        several = self.check("start", "--project", str(self.tmp))
        self.assertEqual(several["status"], "BLOCKED")
        self.assertIn(".atlan-analytics/support-tickets", several["reason"])
        res = self.check("start", "--project", str(self.tmp), "--run", str(wh))
        self.assertEqual((res["run"], res["execution"]["via"], res["execution"]["dialect"],
                          res["execution"]["warehouse"]["tool"]), (str(wh), "host", "snowflake", "mcp__wh__sql_exec_tool"))
        self.assertEqual(self.check("start", "--project", str(self.tmp), "--run", str(self.run))["execution"],
                         {"via": "local", "kind": "sqlite"})
        self.assertEqual(self.check("start", "--project", str(self.tmp), "--run", str(self.tmp / "nope"))["status"], "BLOCKED")

    def test_inputs_takes_a_named_warehouse_run_its_engine_folder_and_a_given_use_case(self) -> None:
        wh = self.warehouse_run(".atlan-metrics", "sales-pipeline")
        self.assertEqual(self.scope("inputs", "--project", str(self.tmp), "--run", str(wh))["status"], "BLOCKED")
        res = self.scope("inputs", "--project", str(self.tmp), "--run", str(wh), "--use-case", "Support agent: ticket volume")
        self.assertEqual((res["status"], res["pool"], res["dialect"], res["use_case"]["purpose"]),
                         ("ok", 1, "snowflake", "Support agent: ticket volume"))
        scope = json.loads((Path(res["gen"]) / "scope.json").read_text())
        self.assertEqual(scope["pool"]["h-tix"]["metrics"], ["tickets"])

    def test_a_warehouse_set_waits_for_results_the_host_ran_and_reads_them_from_the_transcript(self) -> None:
        wh = self.warehouse_run()
        sets = self.tmp / ".atlan-evals" / "sets"
        sets.mkdir(parents=True)
        (sets / "support.jsonl").write_text(
            '{"id": "a", "question": "How many tickets?", "sql": "select count(*) as n\\nfrom t;", "tests": "Tickets"}\n'
            '{"id": "b", "question": "How many open?", "sql": "select count(*) from t where open"}\n'
            '{"id": "c", "question": "By week?", "sql": "select wk, count(*) from t group by 1"}\n'
            '{"id": "d", "question": "Reopens?", "sql": "select count(*) from reopens"}\n')
        res = self.check("load", "--project", str(self.tmp), "--run", str(wh), "--file", str(sets / "support.jsonl"))
        self.assertEqual((res["status"], res["execution"]["via"], [p["id"] for p in res["pending"]]),
                         ("pending_gold", "host", ["a", "b", "c", "d"]))
        ev = Path(res["evals"])
        self.assertEqual(Path(res["pending"][0]["sql_file"]).read_text(), "select count(*) as n\nfrom t;\n")
        self.assertFalse((ev / "work" / "spec.json").exists())
        snow = lambda cols, rows: json.dumps({"query_id": "q", "result_set": {  # noqa: E731
            **({"data": rows} if rows else {}),
            "resultSetMetaData": {"numRows": len(rows), "partitionInfo": [{"rowCount": len(rows)}],
                                  "rowType": [{"name": c} for c in cols]}}})
        # a: Snowflake's own shape, whitespace differs. b: no rows. c: changed on the way. d: failed.
        t = self.transcript(self.tmp / "runner.jsonl", [
            ("select count(*) as n from t", snow(["N"], [["42"]]), False),
            ("select count(*) from t where open", snow(["C"], []), False),
            ("select wk, count(*) from t group by 1 limit 10", snow(["WK", "C"], [["w1", "3"]]), False),
            ("select count(*) from reopens", "SQL compilation error", True)])
        stuck = self.check("gold", "--evals", str(ev), "--transcript", str(t))
        self.assertEqual(stuck["status"], "BLOCKED")
        self.assertIn("c:", stuck["reason"])
        # c's raw result saved to its result file, as a host saves a large tool result; d's error too.
        Path(res["pending"][2]["result_file"]).parent.mkdir(parents=True)
        Path(res["pending"][2]["result_file"]).write_text(json.dumps([{"type": "text", "text": snow(["WK", "C"], [["w1", "3"], ["w2", "5"]])}]))
        Path(res["pending"][3]["result_file"]).write_text(json.dumps({"error": "SQL compilation error"}))
        real_stage = check.stage
        check.stage = lambda ev, slug: None
        try:
            import argparse
            done = check.cmd_gold(argparse.Namespace(evals=str(ev), transcript=[str(t)], allow_missing=False))
        finally:
            check.stage = real_stage
        self.assertEqual((done["status"], done["questions"], sorted((d["id"], d["why"]) for d in done["dropped"])),
                         ("ok", 2, [("b", "no rows"), ("d", "SQL compilation error")]))
        gold = json.loads((ev / "eval" / "gold" / "a.json").read_text())
        self.assertEqual((gold["columns"], gold["rows"], gold["source"]), (["N"], [["42"]], "transcript"))
        self.assertEqual(json.loads((ev / "eval" / "gold" / "c.json").read_text())["rows"], [["w1", "3"], ["w2", "5"]])
        self.assertEqual([r["id"] for r in json.loads((ev / "work" / "spec.json").read_text())["records"]], ["a", "c"])
        self.assertEqual(self.check("gold", "--evals", str(ev))["status"], "BLOCKED")  # already set up

    def test_snowflake_results_grade_and_a_context_folder_is_not_eval_data(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        import analytics_eval as harness
        snow = {"result_set": {"data": [["a", "10"]], "resultSetMetaData": {
            "numRows": 1, "partitionInfo": [{"rowCount": 1}], "rowType": [{"name": "K"}, {"name": "V"}]}}}
        self.assertEqual(harness.grade(snow, {"columns": ["k", "v"], "rows": [["a", 10]]}, "select 1")["class"], "pass")
        paged = {"result_set": {"data": [["a", "10"]], "resultSetMetaData": {
            "numRows": 900, "partitionInfo": [{}, {}], "rowType": [{"name": "K"}, {"name": "V"}]}}}
        self.assertEqual(harness.grade(paged, snow, "select 1")["class"], "incomplete")
        # An agent runs its SQL across lines and states it on one: the same statement either way.
        self.assertEqual(harness._sql_key("with a as (\n  select 1,\n  2\n)\nselect * from a;"),
                         harness._sql_key("with a as (select 1, 2) select * from a"))
        self.assertNotEqual(harness._sql_key("select a b from t"), harness._sql_key("select ab from t"))
        self.assertFalse(harness._EVAL_PATH.search("/p/.atlan-analytics/support-tickets/context/metrics.json"))
        self.assertTrue(harness._EVAL_PATH.search("/p/.atlan-analytics/shop/eval/answers.jsonl"))
        self.assertTrue(harness._EVAL_PATH.search("/p/.atlan-evals/support-tickets/eval/gold/a.json"))

    def test_a_set_line_without_sql_is_refused(self) -> None:
        bad = self.tmp / "bad.jsonl"
        bad.write_text('{"question": "Total net revenue?"}\n')
        self.assertEqual(self.check("load", "--project", str(self.tmp), "--file", str(bad))["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
