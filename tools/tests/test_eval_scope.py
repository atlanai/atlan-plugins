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
        self.run, self.ev = run, self.tmp / "evals"

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
        self.assertEqual([o["label"] for o in res["ask"]["options"]], ["finance (1 question)", "Generate 5 new questions"])
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
            "description": "Old.", "definition": {"version": 1, "formula": "SUM(a)", "examples": [{"sql": "SELECT\n1"}]}}]))
        ev.mkdir()
        (ev / "fixes.json").write_text(json.dumps([{"metric": "Cost", "misses": ["q1"], "changes": [
            {"path": "formula", "value": "SUM(b)"}, {"path": "description", "value": "New."}]}]))
        out = self.acc("version", "--evals", str(ev), "--run", str(run), "--metric", "Cost", "--head-version", "1")
        meta = out["metadata"]
        self.assertEqual((out["version"], meta["description"], meta["definition"]["formula"],
                          meta["definition"]["examples"][0]["sql"]), (2, "New.", "SUM(b)", "SELECT\n1"))
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
            saved = acc.cmd_saved(__import__("argparse").Namespace(evals=str(ev), agent="job agent", workspace="WS",
                                                                     runs_link="https://r", released=["Cost:2"]))["text"]
            self.assertEqual(saved.splitlines(), ["Saved to WS:", "  job agent, with 7 questions and 2 runs scored from 71% to 100%",
                                                  "  Cost version 2, released", "See the runs in Atlan: https://r"])

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
        real_gold, real_stage = check.gold, check.stage
        check.gold = lambda run, ev, recs: {"ran": [{"id": "m1", "rows": 1, "first": [9]}],
                                               "failed": [{"id": "q2", "why": "did not run"}]}
        check.stage = lambda ev, slug: staged.append(slug)
        try:
            import argparse
            res = check.cmd_load(argparse.Namespace(project=str(self.tmp), file=str(sets / "finance.jsonl")))
        finally:
            check.gold, check.stage = real_gold, real_stage
        ev = Path(res["evals"])
        spec = json.loads((ev / "work" / "spec.json").read_text())
        self.assertEqual([(r["id"], r["review_status"]) for r in spec["records"]], [("m1", "approved")])
        self.assertEqual((res["questions"], res["dropped"][0]["id"], staged, res["generated"]), (1, "q2", ["finance"], False))
        self.assertEqual(json.loads((ev / "work" / "selection.json").read_text())[0]["metric"], "Net Revenue")

    def test_a_set_line_without_sql_is_refused(self) -> None:
        bad = self.tmp / "bad.jsonl"
        bad.write_text('{"question": "Total net revenue?"}\n')
        self.assertEqual(self.check("load", "--project", str(self.tmp), "--file", str(bad))["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
