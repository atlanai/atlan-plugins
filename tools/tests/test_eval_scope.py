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
        run = self.tmp / ".atlan-analytics" / "run_0000000000000001"
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

    def test_a_miss_that_repeats_gets_one_fix_for_every_metric_it_touches(self):
        # A desktop run fixed waste rate's total row, and billable time missed the same way in the retest.
        ev = self.tmp / "ev"
        (ev / "eval").mkdir(parents=True)
        (ev / "eval" / "cases.jsonl").write_text(json.dumps({"case_id": "q1"}) + "\n" + json.dumps({"case_id": "q2"}) + "\n")
        first = [{"misses": ["q1"], "why": "Added a total row", "metric": "Waste rate", "rule_now": "no rule on totals",
                  "rule_proposed": "no total row", "changes": [{"path": "description", "value": "Waste. No total row."}]}]
        (self.tmp / "f.json").write_text(json.dumps(first))
        self.assertEqual(self.acc("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))["status"], "ok")
        shared = [{"kind": "shared", "misses": ["q2"], "why": "Added a total row, as waste rate did before its fix",
                   "rule_now": "each metric says nothing about totals",
                   "rule_proposed": "a breakdown returns one row per group, with no total row unless the question asks for one",
                   "metrics": [{"metric": "Billable time", "changes": [{"path": "description", "value": "Billable. No total row."}]},
                               {"metric": "Rework cost", "changes": [{"path": "description", "value": "Rework. No total row."}]}]}]
        (self.tmp / "f.json").write_text(json.dumps(shared))
        out = self.acc("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))
        self.assertEqual((out["status"], out["recommended"]), ("ok", "Billable time and 1 more"))
        history = [json.loads(l) for l in (ev / "fixes-history.jsonl").read_text().splitlines()]
        self.assertEqual(history[-1]["fixes"], [dict(f, kind="metric") for f in first], "the earlier round's fixes are kept to compare a new miss with")
        for bad in ([{**shared[0], "metrics": shared[0]["metrics"][:1]}], [{**shared[0], "rule_proposed": ""}],
                    [{**shared[0], "metrics": [{"metric": "Billable time", "changes": []}, shared[0]["metrics"][1]]}]):
            (self.tmp / "f.json").write_text(json.dumps(bad))
            self.assertEqual(self.acc("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))["status"], "BLOCKED")

    def test_version_reads_a_shared_fix_for_its_metric(self) -> None:
        ev, run = self.tmp / "ev", self.tmp / "run"
        (run / "context").mkdir(parents=True)
        (run / "context" / "metrics.json").write_text(json.dumps([{"name": "Rework cost", "description": "Old.",
                                                                    "definition": {"version": 1}}]))
        ev.mkdir()
        (ev / "fixes.json").write_text(json.dumps([{"kind": "shared", "misses": ["q2"], "metrics": [
            {"metric": "Billable time", "changes": [{"path": "description", "value": "Billable. No total row."}]},
            {"metric": "Rework cost", "changes": [{"path": "description", "value": "Rework. No total row."}]}]}]))
        out = self.acc("version", "--evals", str(ev), "--run", str(run), "--metric", "Rework cost", "--head-version", "1")
        self.assertEqual((out["version"], out["metadata"]["description"]), (2, "Rework. No total row."))

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
        res = self.scope("pick", "--gen", str(self.ev), "--limit", "4", "--spare", "0")
        picked = [(s["metric"], s["from"]) for s in res["selection"]]
        # Two on the focus metric (its tile, then the most-run query), then breadth by relevance:
        # the refund and region metrics speak to "why revenue fell by region"; headcount, run most, does not.
        self.assertEqual(picked, [("Net Revenue", "dashboard tile"), ("Net Revenue", "past query, used 40 times"),
                                  ("Region Mix", "past query, used 8 times"), ("Refund Rate", "past query, used 5 times")])
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

    def test_pick_skips_a_query_no_question_can_reproduce_and_drafts_spares(self) -> None:
        self.ev = Path(self.scope("inputs", "--project", str(self.tmp))["gen"])
        work = self.ev / "work"
        work.mkdir(parents=True, exist_ok=True)
        cands = [{"id": "m0", "query_id": "h-rev", "sql": "select sum(amount) from s.orders -- total"},
                 {"id": "m1", "query_id": "h-rev2", "sql": "select id, name, stage from s.orders where region = 'count(x)'"},
                 {"id": "m2", "query_id": "h-head", "sql": "select count(*) from s.orders where false limit 0"},
                 {"id": "m3", "query_id": "h-refund", "sql": "select region, count(*) from s.orders group by 1"},
                 {"id": "m4", "query_id": "h-mix", "sql": "select count(distinct id) from s.orders"},
                 {"id": "m5", "query_id": "h-mix", "sql": "select count(*) as failures, count(*) <> 0 as should_warn from (select 1)"},
                 {"id": "m6", "query_id": "h-mix", "sql": "select 'orders' as tbl, max(_LOADED_AT) as latest from s.orders"}]
        (work / "candidates.json").write_text(json.dumps(cands))
        res = self.scope("pick", "--gen", str(self.ev), "--limit", "2", "--spare", "1")
        self.assertEqual({s["id"]: s["why"] for s in res["skipped"]},
                         {"m1": "lists records, with no total or count", "m2": "a test query that returns nothing",
                          "m5": "a data test, not a business question",
                          "m6": "a freshness check on when tables last loaded"})
        self.assertEqual((res["picked"], res["limit"]), (3, 2))  # two asked for, one spare
        picked = [s["id"] for s in res["selection"]]
        # The draft found the first question unanswerable: the spare takes its place, in pick order.
        (work / "spec.json").write_text(json.dumps({"records": [
            {"id": i, "question": f"q {i}", "golden_sql": "select 1",
             "review_status": "rejected" if i == picked[0] else "approved"} for i in picked]}))
        out = self.scope("emit", "--gen", str(self.ev), "--name", "spares")
        rows = [json.loads(l)["id"] for l in Path(out["set"]).read_text().splitlines()]
        self.assertEqual((rows, out["spares_unused"]), (picked[1:], 0))
        # A pick that outgrows the draft slots an earlier pick left is refused, not drafted short.
        (work / "candidates.json").write_text(json.dumps(cands))
        (work / "answers.json").write_text(json.dumps({"m0": {}}))
        self.assertEqual(self.scope("pick", "--gen", str(self.ev), "--limit", "2", "--spare", "0")["status"], "BLOCKED")

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
        self.assertEqual(Path(res["set"]), self.tmp / ".atlan-analytics" / "evals" / "sets" / "revenue-check.jsonl")
        self.assertEqual(rows, [{"id": "m1", "question": "Total net revenue?", "sql": "SELECT 1",
                                 "tests": "Net Revenue", "from": "dashboard tile", "generated": True}])
        # The set's own ids, so the chip counts the set and not a rejected draft or an unused spare.
        self.assertEqual(json.loads((gen / "work" / "emitted.json").read_text()), ["m1"])
        self.assertIn("1 test question", res["show_first"])
        # Written questions are an output: the chip comes with the result, and the side page lists them.
        self.assertIn("test question", res["show_first"])
        self.assertIn("show_first", res["visual"])

    def test_ready_turns_any_set_file_into_an_approved_spec_to_save(self) -> None:
        sets = self.tmp / ".atlan-analytics" / "evals" / "sets"
        sets.mkdir(parents=True)
        (sets / "finance.jsonl").write_text('{"id": "f1", "question": "Q?", "sql": "SELECT 1", "from": "dashboard tile"}\n')
        res = self.scope("ready", "--set", str(sets / "finance.jsonl"))
        spec = json.loads((Path(res["dir"]) / "work" / "spec.json").read_text())
        self.assertEqual((res["slug"], [(r["id"], r["review_status"]) for r in spec["records"]]), ("finance", [("f1", "approved")]))

    def test_start_finds_the_run_and_the_sets_and_a_scored_set_gets_a_fresh_folder(self) -> None:
        (self.tmp / ".atlan-analytics" / "run_unbuilt").mkdir()  # location's fresh, empty run
        sets = self.tmp / ".atlan-analytics" / "evals" / "sets"
        sets.mkdir(parents=True)
        (sets / "finance.jsonl").write_text('{"question": "Q?", "sql": "SELECT 1"}\n')
        res = self.check("start", "--project", str(self.tmp))
        self.assertEqual((res["run"], res["context_saved_to"], [(s["name"], s["questions"]) for s in res["sets"]]),
                         (str(self.run), None, [("finance", 1)]))
        self.assertEqual([o["label"] for o in res["ask"]["options"]], ["finance (1 question)", "Write 20 new questions"])
        # Every check runs the raw-source pass too, whoever starts it and whatever its size.
        self.assertTrue(res["before_after"])
        self.assertIn("column names only", res["ask"]["text"])
        self.assertEqual(res["ask"]["options"][-1]["label"], "Write 20 new questions")  # a direct check: 20
        demo = self.check("start", "--project", str(self.tmp), "--run", str(self.run), "--questions", "8")
        self.assertEqual(demo["ask"]["options"][-1]["label"], "Write 8 new questions")
        self.assertTrue(demo["before_after"])
        self.assertIn("column names only", demo["ask"]["text"])
        self.assertNotIn("--context-only", CHECK.read_text())  # no caller can leave the comparison out
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
        compared = []
        result = {"accuracy": 100, "baseline": 29, "lift": 71, "outcome": "ahead", "questions": 7, "small_set": False,
                  "misses": [], "turned_around": [1, 2, 3, 4, 5], "lost": [], "left_out": 0, "all_right": True,
                  "lines": {"accuracy": "Accuracy: 100%.",
                            "before_after": "With column names only: 29%. With your agent: 100%.", "small_set": ""}}
        with mock.patch.object(acc, "_score", lambda e, v: scores[v]), \
                mock.patch.object(acc, "_compare", lambda e, after: compared.append(after) or
                                  {"report": "card", "widget": "<div>card</div>", "result": result}):
            ns = lambda **kw: __import__("argparse").Namespace(**{**dict(evals=str(ev), agent="job agent", firm="Acme",
                                                                         generated=False, widget=False), **kw})
            out = acc.cmd_result(ns())
            # The numbers the result is written from, never a finished card; the banner is the one piece put as it is.
            self.assertIn("7 OUT OF 7 RIGHT", out["banner"])
            self.assertNotIn("text", out)
            self.assertEqual((out["accuracy"], out["baseline"], out["outcome"], out["from"]), (100, 29, "ahead", 71))
            self.assertEqual(out["lines"]["fixed"], "Up from 71% before the fix.")
            self.assertEqual(out["lines"]["before_after"], "With column names only: 29%. With your agent: 100%.")
            # The comparison is always against the latest run, and a widget only when asked for.
            self.assertEqual(compared, ["v1"])
            self.assertNotIn("widget", out)
            self.assertEqual(acc.cmd_result(ns(widget=True))["widget"], "<div>card</div>")
            self.assertEqual(acc.cmd_result(ns(generated=True))["source"], acc.SOURCE)
            with mock.patch.object(acc, "_compare", lambda e, after: {"report": "card", "result": {
                    **result, "all_right": False, "accuracy": 86, "misses": [{"number": 3, "question": "Q?"}]}}):
                self.assertEqual(acc.cmd_result(ns())["banner"], "")  # no banner with a miss left
            (ev / "state.json").write_text(json.dumps({"ids": {"agent_id": "agent_01a", "experiments": {
                "baseline": "experiment_01b", "v1": "experiment_01c"}}}))
            saved = acc.cmd_saved(__import__("argparse").Namespace(evals=str(ev), agent="job agent", workspace="WS",
                                                                     org="acme", origin="https://app.atlan.com",
                                                                     released=["Cost:2"], withdrawn=None))["text"]
            self.assertEqual(saved.splitlines(), ["Saved to WS:", "  job agent, with 7 questions and 2 rounds scored from 71% to 100%",
                                                  "  Cost version 2, released",
                                                  "See the scores in Atlan Trident: https://app.atlan.com/acme/registry/evals/experiments/agent/agent_01a?ws=all"])
            withdrawn = acc.cmd_saved(__import__("argparse").Namespace(evals=str(ev), agent="job agent", workspace="WS",
                                                                         org="acme", origin="https://app.atlan.com",
                                                                         released=None, withdrawn=["Cost:2"]))["text"]
            self.assertIn("  Cost version 2, withdrawn", withdrawn.splitlines())
            # Saved without an agent: the link is the last run's page, compared with the run before it.
            (ev / "state.json").write_text(json.dumps({"ids": {"experiments": {
                "baseline": "experiment_01b", "v1": "experiment_01c"}}}))
            bare = acc.cmd_saved(__import__("argparse").Namespace(evals=str(ev), agent="job agent", workspace="WS",
                                                                    org="acme", origin="https://app.atlan.com",
                                                                    released=None, withdrawn=None))["text"]
            self.assertEqual(bare.splitlines()[-1], "See the scores in Atlan Trident: "
                             "https://app.atlan.com/acme/registry/evals/experiments/experiment_01c?ws=all")
            (ev / "state.json").write_text(json.dumps({"ids": {}}))
            with self.assertRaises(acc.Blocked):
                acc.cmd_saved(__import__("argparse").Namespace(evals=str(ev), agent="job agent", workspace="WS",
                                                               org="acme", origin="https://app.atlan.com",
                                                               released=None, withdrawn=None))

    def test_link_names_the_agents_runs_and_the_last_run(self) -> None:
        ev = self.tmp / ".atlan-analytics" / "evals" / "saved"
        ev.mkdir(parents=True)
        (ev / "state.json").write_text(json.dumps({"ids": {"agent_id": "agent_01a", "experiments": {
            "baseline": "experiment_01b", "v1": "experiment_01c"}}}))
        res = self.check("link", "--evals", str(ev), "--org", "acme")
        base = "https://app.atlan.com/acme/registry/evals/experiments/agent/agent_01a"
        self.assertEqual((res["runs"], res["last_run"]), (base + "?ws=all", base + "/experiment_01c?ws=all"))
        # Without an agent, the runs list under All runs and the last run has its own page.
        (ev / "state.json").write_text(json.dumps({"ids": {"experiments": {
            "raw": "experiment_01a", "baseline": "experiment_01b"}}}))
        res = self.check("link", "--evals", str(ev), "--org", "acme")
        base = "https://app.atlan.com/acme/registry/evals/experiments"
        self.assertEqual((res["runs"], res["last_run"]),
                         (base + "?ws=all&mode=runs", base + "/experiment_01b?ws=all"))

    def test_a_set_loads_as_approved_and_drops_a_question_whose_sql_does_not_run(self) -> None:
        sets = self.tmp / ".atlan-analytics" / "evals" / "sets"
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
                         {"via": "local", "kind": "sqlite", "tables": ["s.orders"]})
        # The raw-source pass is told which tables to work from: the ones the run's metrics read.
        self.assertEqual(res["execution"]["tables"], ["L.Z.TICKET"])
        self.assertEqual(self.check("start", "--project", str(self.tmp), "--run", str(self.tmp / "nope"))["status"], "BLOCKED")
        # Eval files never go inside the run that answering agents read.
        inside = self.check("start", "--project", str(wh), "--run", str(wh))
        self.assertEqual(inside["status"], "BLOCKED")
        self.assertIn(str(self.tmp.resolve()), inside["reason"])

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
        sets = self.tmp / ".atlan-analytics" / "evals" / "sets"
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
                         ("ok", 2, [("b", "returned nothing"), ("d", "SQL compilation error")]))
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
        # An answering agent that names no SQL, or a stand-in for it, is graded on the last read it ran.
        ok = json.dumps({"columns": ["n"], "rows": [[7]]})
        t = self.transcript(self.tmp / "agent.jsonl", [("select 1 as n", ok, False), ("select count(*) n from t", ok, False),
                                                       ("select broken", "error", True)])
        got, _ = harness.transcript_result(t, "<the SQL above, verbatim>", last_call=True)
        self.assertEqual((got["source"], got["sql"], got["rows"]), ("transcript_last_call", "select count(*) n from t", [[7]]))
        self.assertEqual(harness.transcript_result(t, "", last_call=True)[0]["sql"], "select count(*) n from t")
        self.assertIsNone(harness.transcript_result(t, "<the SQL above, verbatim>")[0])  # never for trusted SQL
        self.assertEqual(harness.transcript_result(t, "select 1 as n", last_call=True)[0]["source"], "transcript")
        self.assertFalse(harness._EVAL_PATH.search("/p/.atlan-analytics/support-tickets/context/metrics.json"))
        self.assertTrue(harness._EVAL_PATH.search("/p/.atlan-analytics/shop/eval/answers.jsonl"))
        self.assertTrue(harness._EVAL_PATH.search("/p/.atlan-evals/support-tickets/eval/gold/a.json"))

    def test_a_trusted_result_that_is_a_long_list_cannot_anchor_a_question(self) -> None:
        self.assertIsNone(check._keep({"rows": [[1]] * check.MAX_ROWS}))
        self.assertIn("a list too long", check._keep({"rows": [[1]] * (check.MAX_ROWS + 1)}))

    def scored_check(self) -> Path:
        """An eval folder with three questions, a baseline run and a later run."""
        import analytics_eval as harness
        ev = self.tmp / ".atlan-analytics" / "evals" / self.run.name
        records = [{"id": i, "question": f"Question {i}?", "question_spec": "", "golden_sql": f"SELECT {n}",
                    "review_status": "approved", "provenance": {"source": "eval set finance"}, "modes": []}
                   for n, i in enumerate(("a", "b", "c"), 1)]
        check.dump(ev / "work" / "spec.json", {"name": "finance", "records": records})
        check.dump(ev / "work" / "selection.json", [{"id": r["id"], "metric": "Net Revenue", "expected": ["June", 10]}
                                                    for r in records])
        for r in records:
            check.dump(ev / "eval" / "gold" / f"{r['id']}.json", {"columns": ["n"], "rows": [[1]]})
        check.stage(ev, "finance")
        for variant, passed in (("baseline", {"a": 1, "b": 0, "c": 1}), ("v1", {"a": 1, "b": 1, "c": 0})):
            vd = harness.Proj(str(ev)).vdir(variant)
            (vd / "results.jsonl").write_text("".join(json.dumps({
                "prompt_id": i, "rep": 0, "prompt": f"Question {i}?", "tags": [], "grade": {"pass": p, "exec_ok": 1},
                "class": "pass" if p else "wrong_result", "reason": "single value differs", "ts": "2026-11-16T10:00:00+00:00",
                "isolation": "verified"}) + "\n" for i, p in passed.items()))
            check.dump(vd / "results" / "c_rep0.agent.json", {"columns": ["m", "n"], "rows": [["June", 12]]})
        return ev

    def harness(self, *args) -> dict:
        out = subprocess.run([sys.executable, str(check.HARNESS), *args], capture_output=True, text=True)
        return json.loads(out.stdout)

    def test_every_run_gets_the_same_report_naming_the_run_it_is_compared_with(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        ev = self.scored_check()
        (ev / "why.json").write_text(json.dumps({"c": {"why": "Different filter", "detail": "It counted another month."}}))
        rep = self.harness("report", "--dir", str(ev), "--why", "why.json")
        text = rep["report"]
        self.assertIn("test 2 of 2", text)
        self.assertIn("Tests on these questions", text)
        self.assertIn("Compared with the first test (16 Nov 10:00 UTC), kept on this machine.", text)
        self.assertIn("Since the first test, questions now right: 2. Now wrong: 3.", text)
        self.assertIn("Different filter (1)\n  3. Question c?\n     Tests Net Revenue. Expected June, 10. Got June, 12.\n     Why: It counted another month.", text)
        self.assertEqual((rep["misses"], rep["unexplained"]), ([3], []))
        first = self.harness("report", "--dir", str(ev), "--variant", "baseline")
        self.assertIn("test 1 of 2", first["report"])
        self.assertNotIn("Compared with", first["report"])
        self.assertIn("Not yet explained (1)\n  2. Question b?", first["report"])  # the same number in every run
        self.assertEqual(first["unexplained"], ["b"])

    def test_start_lists_earlier_checks_only_for_a_comparison(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        ev = self.scored_check()
        res = self.check("start", "--project", str(self.tmp))
        self.assertEqual([(c["evals"], c["set"], c["runs"]) for c in res["checks"]], [(str(ev), "finance", ["baseline", "v1"])])
        self.assertIn("starts a new eval folder", res["new_check"])
        self.assertTrue(str(check.evals_dir(self.tmp, self.run)).endswith("-2"))  # a new check never reuses it

    def test_the_person_can_drop_questions_by_number_before_anything_is_scored(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        ev = self.scored_check()
        self.assertEqual(self.check("drop", "--evals", str(ev), "--questions", "2")["status"], "BLOCKED")  # scored
        import shutil
        shutil.rmtree(ev / "hillclimb")
        self.assertEqual(self.check("drop", "--evals", str(ev), "--questions", "9")["status"], "BLOCKED")
        self.assertEqual(self.check("drop", "--evals", str(ev), "--questions", "1,2,3")["status"], "BLOCKED")
        res = self.check("drop", "--evals", str(ev), "--questions", "2")
        self.assertEqual((res["questions"], res["dropped"]), (2, ["b"]))
        self.assertEqual([json.loads(l)["case_id"] for l in (ev / "eval" / "cases.jsonl").read_text().splitlines()], ["a", "c"])
        self.assertFalse((ev / "eval" / "gold" / "b.json").exists())
        self.assertEqual([s["id"] for s in json.loads((ev / "work" / "selection.json").read_text())], ["a", "c"])

    def test_a_fix_never_ties_a_moving_period_to_a_date(self) -> None:
        ev = self.tmp / "ev"
        (ev / "eval").mkdir(parents=True)
        (ev / "eval" / "cases.jsonl").write_text(json.dumps({"case_id": "q1"}) + "\n")
        fix = {"misses": ["q1"], "why": "Different filter", "metric": "CSAT", "rule_now": "no period named",
               "changes": [{"path": "description", "value": "x"}]}
        for rule, status in (("last quarter means Q2 2026", "BLOCKED"), ("this month is read as 2026-07", "BLOCKED"),
                             ("also called the support satisfaction score", "ok"),
                             ("counts tickets created in the last 30 days", "ok")):
            (self.tmp / "f.json").write_text(json.dumps([{**fix, "rule_proposed": rule}]))
            out = self.acc("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))
            self.assertEqual(out["status"], status, rule)
        (self.tmp / "f.json").write_text(json.dumps([{"misses": ["q1"], "kind": "redraft",
                                                       "why": "The question names a relative period its SQL fixes"}]))
        self.assertEqual(self.acc("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))["status"], "ok")

    def test_the_generated_note_says_what_the_score_does_and_does_not_show(self) -> None:
        sys.path.insert(0, str(ACC.parent))
        import accuracy
        self.assertIn("It doesn't show how it does on new questions.", accuracy.SOURCE)
        self.assertNotIn("reproduces", accuracy.SOURCE)

    def test_show_prints_the_set_as_one_readable_table(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        ev = self.scored_check()
        check.dump(ev / "eval" / "gold" / "a.json", {"columns": ["n"], "rows": [["1234567"]]})
        check.dump(ev / "eval" / "gold" / "b.json", {"columns": ["ms", "cnt", "x", "y"],
                                                     "rows": [["2026-06-01 00:00:00", "1684", "4.90", "z"], ["2026-07-01", "9", "1", "z"]]})
        check.dump(ev / "eval" / "gold" / "c.json", {"columns": ["k"], "rows": []})
        table = self.check("show", "--evals", str(ev))["table"].splitlines()
        self.assertEqual(table[:2], ["| # | Question | Metric it tests | Known answer |", "| ---: | --- | --- | --- |"])
        self.assertEqual(table[2], "| 1 | Question a? | Net Revenue | 1,234,567 |")
        self.assertEqual(table[3], "| 2 | Question b? | Net Revenue | 2 rows, starting 2026-06-01, 1,684, 4.9, … |")
        self.assertEqual(table[4], "| 3 | Question c? | Net Revenue | nothing |")
        self.assertNotIn("cnt", "\n".join(table))  # a query's aliases never reach the person

    def test_the_person_can_reword_a_question_before_anything_is_scored(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        ev = self.scored_check()
        sets = ev.parent / "sets"
        sets.mkdir(parents=True, exist_ok=True)
        (sets / "finance.jsonl").write_text("".join(json.dumps({"id": i, "question": f"Question {i}?", "sql": "SELECT 1"}) + "\n"
                                                    for i in "abc"))
        self.assertEqual(self.check("edit", "--evals", str(ev), "--question", "2", "--text", "New?")["status"], "BLOCKED")
        import shutil
        shutil.rmtree(ev / "hillclimb")
        self.assertEqual(self.check("edit", "--evals", str(ev), "--question", "9", "--text", "What was revenue in June?")["status"], "BLOCKED")
        self.assertEqual(self.check("edit", "--evals", str(ev), "--question", "2", "--text", "no question mark")["status"], "BLOCKED")
        res = self.check("edit", "--evals", str(ev), "--question", "2", "--text", "  What was net revenue in June 2026?  ")
        self.assertEqual((res["question"], res["id"], res["to"]), (2, "b", "What was net revenue in June 2026?"))
        cases = [json.loads(l) for l in (ev / "eval" / "cases.jsonl").read_text().splitlines()]
        self.assertEqual(cases[1]["question"], "What was net revenue in June 2026?")
        self.assertEqual(json.loads((sets / "finance.jsonl").read_text().splitlines()[1])["question"],
                         "What was net revenue in June 2026?")
        self.assertIn("What was net revenue in June 2026?", self.check("show", "--evals", str(ev))["table"])

    def test_the_run_question_reads_right_for_one_question(self) -> None:
        one, many = check.run_ask(1), check.run_ask(3)
        self.assertEqual(one["question"], "Test your agent on this question?")
        self.assertEqual(one["options"][0]["description"], "I test it with your agent's metrics and with column names only.")
        self.assertEqual(many["question"], "Test your agent on these 3 questions?")
        self.assertEqual(many["options"][0]["description"], "I test all 3, with your agent's metrics and with column names only.")
        self.assertNotIn("1 questions", json.dumps(one))

    def test_the_report_gives_the_misses_and_the_comparison_on_their_own(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        ev = self.scored_check()
        rep = self.harness("report", "--dir", str(ev))
        self.assertTrue(rep["misses_text"].startswith("Wrong answers (1), by cause"))
        self.assertIn("  3. Question c?", rep["misses_text"])
        self.assertTrue(rep["compared_text"].startswith("Compared with the first test"))
        self.assertIn(rep["misses_text"], rep["report"])  # the full report keeps every section

    def test_the_result_numbers_come_with_the_comparison(self) -> None:
        sys.path.insert(0, str(check.BUILD))
        import analytics_eval as harness
        ev = self.scored_check()
        raw = harness.Proj(str(ev)).vdir("raw")
        (raw / "results.jsonl").write_text("".join(json.dumps({
            "prompt_id": i, "rep": 0, "prompt": f"Question {i}?", "tags": [], "grade": {"pass": p, "exec_ok": 1},
            "class": "pass" if p else "wrong_result", "ts": "2026-11-16T10:00:00+00:00", "isolation": "verified"}) + "\n"
            for i, p in (("a", 1), ("b", 1), ("c", 1))))
        res = self.harness("before-after", "--dir", str(ev), "--before", "raw", "--after", "v1")["result"]
        # Behind: the honest outcome, never a lift that isn't there.
        self.assertEqual((res["accuracy"], res["baseline"], res["lift"], res["outcome"]), (67, 100, -33, "behind"))
        self.assertEqual(res["lines"]["before_after"], "With column names only: 100%. With your agent: 67%.")
        self.assertEqual(res["lines"]["small_set"], "Based on 3 questions. Add more for a sharper score.")
        self.assertEqual((res["misses"], res["lost"]), ([{"number": 3, "question": "Question c?"}], [3]))

    def test_next_offers_what_the_run_could_not_read_biggest_gap_first(self) -> None:
        ev = self.scored_check()
        run = self.warehouse_run()
        (run / "interaction.json").write_text(json.dumps({
            "report": "Region Targets: all pages", "readiness": {"result": "1 of 1 tiles, past queries not readable"}}))
        (run / "engine-v2" / "run.yml").write_text(json.dumps({"inputs": {"history": [], "schema": [], "bi": ["powerbi"]}}))
        out = self.check("next", "--evals", str(ev), "--run", str(run))
        self.assertEqual(out["keys"], ["past_queries", "another_dashboard", "tables", "done"])
        self.assertIn("SNOWFLAKE.GOVERNANCE_VIEWER", out["options"][0]["description"])
        self.assertIn("never change permissions", out["options"][0]["description"])
        self.assertEqual(out["lead"], check.LEAD)
        self.assertEqual(out["question"], "What should I add next?")
        self.assertLessEqual(len(out["options"]), 4)
        # Pages left out, and past queries read: the other pages, then the person's own questions.
        (run / "interaction.json").write_text(json.dumps({"report": "Region Targets: Overview"}))
        (run / "context" / "querylog.jsonl").write_text('{"sql": "select 1"}\n')
        out = self.check("next", "--evals", str(ev), "--run", str(run))
        self.assertEqual(out["keys"], ["other_pages", "own_questions", "done"])
        self.assertIn("Region Targets", out["options"][0]["description"])
        # Five questions or more can be saved for the team; a demo offers no source it could not read.
        gaps = {**check.run_gaps(run), "demo": True}
        keys = [o["key"] for o in check.next_options(gaps, 8)]
        self.assertEqual(keys, ["own_questions", "save", "done"])
        save = check.next_options(gaps, 8)[1]
        self.assertEqual(save["description"], "Save this score to Atlan Trident, so your team can see it.")
        self.assertNotIn("fingerprint", json.dumps(check.next_options(gaps, 8)))
        self.assertNotIn("save", [o["key"] for o in check.next_options(gaps, 4)])

    def test_a_set_line_without_sql_is_refused(self) -> None:
        bad = self.tmp / "bad.jsonl"
        bad.write_text('{"question": "Total net revenue?"}\n')
        self.assertEqual(self.check("load", "--project", str(self.tmp), "--file", str(bad))["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
