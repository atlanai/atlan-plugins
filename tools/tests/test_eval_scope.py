"""eval-generation's scope.py: the use case and metric links decide the set, not run counts alone."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / "plugins/atlanai-analytics/skills/eval-generation/scripts/scope.py"


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

    def test_fixes_are_checked_before_they_are_written(self):
        ev = self.tmp / "ev"
        (ev / "eval").mkdir(parents=True)
        (ev / "eval" / "cases.jsonl").write_text(json.dumps({"case_id": "q1"}) + "\n")
        good = [{"misses": ["q1"], "why": "Different filter", "metric": "M", "rule_now": "all rows",
                 "rule_proposed": "closed rows", "changes": [{"path": "source.sql", "value": "x"}]},
                {"misses": ["q1"], "why": "Question asks the opposite", "kind": "redraft"}]
        (self.tmp / "f.json").write_text(json.dumps(good))
        out = self.scope("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))
        self.assertEqual((out["fixes"], out["recommended"]), (2, "M"))
        for bad in ([{**good[0], "misses": ["q9"]}], [{**good[0], "changes": [{"path": "version", "value": 2}]}],
                    [{**good[0], "rule_now": ""}]):
            (self.tmp / "f.json").write_text(json.dumps(bad))
            self.assertEqual(self.scope("fixes", "--evals", str(ev), "--file", str(self.tmp / "f.json"))["status"], "BLOCKED")

    def test_inputs_reads_the_use_case_and_links_every_query_to_its_metrics(self) -> None:
        res = self.scope("inputs", "--project", str(self.tmp), "--evals", str(self.ev))
        self.assertEqual((res["focus"], res["pool"], res["tiles"]), ("Net Revenue", 6, 1))
        self.assertEqual(res["run"], str(self.run))
        rows = [json.loads(l) for l in (self.ev / "src" / "pool.jsonl").read_text().splitlines()]
        self.assertEqual({r["id"]: r["exec"] for r in rows}["h-head"], 90)

    def test_pick_takes_the_focus_metric_first_then_the_metrics_nearest_the_use_case(self) -> None:
        self.scope("inputs", "--project", str(self.tmp), "--evals", str(self.ev))
        work = self.ev / "work"
        (work / "prompts").mkdir(parents=True)
        cands = [{"id": f"m{i}", "query_id": vq, "scores": {"x": 0.5}} for i, vq in
                 enumerate(["h-head", "h-rev2", "tile-rev", "h-refund", "h-rev", "h-mix"])]
        (work / "candidates.json").write_text(json.dumps(cands))
        (work / "answers.json").write_text(json.dumps({c["id"]: {} for c in cands}))
        for c in cands:
            (work / "prompts" / f"{c['id']}.txt").write_text("p")
        res = self.scope("pick", "--evals", str(self.ev), "--limit", "4")
        picked = [(s["metric"], s["from"]) for s in res["selection"]]
        # Two on the focus metric (its tile, then the most-run query), then breadth by relevance:
        # the refund and region metrics speak to "why revenue fell by region"; headcount, run most, does not.
        self.assertEqual(picked, [("Net Revenue", "dashboard tile"), ("Net Revenue", "past query, run 40 times"),
                                  ("Region Mix", "past query, run 8 times"), ("Refund Rate", "past query, run 5 times")])
        self.assertEqual(len(list((work / "prompts").glob("*.txt"))), 4)
        self.assertEqual(len(json.loads((work / "answers.json").read_text())), 4)

    def test_a_run_without_a_use_case_is_refused(self) -> None:
        (self.run / "interaction.json").write_text("{}")
        res = self.scope("inputs", "--project", str(self.tmp), "--evals", str(self.ev))
        self.assertEqual(res["status"], "BLOCKED")

    def test_the_project_run_is_found_and_a_scored_set_gets_a_fresh_folder(self) -> None:
        (self.tmp / ".atlan-metrics" / "run_unbuilt").mkdir()  # location's fresh, empty run
        first = self.scope("inputs", "--project", str(self.tmp))
        self.assertEqual(first["evals"], str(self.tmp / ".atlan-evals" / self.run.name))
        (Path(first["evals"]) / "hillclimb").mkdir()
        self.assertTrue(self.scope("inputs", "--project", str(self.tmp))["evals"].endswith(self.run.name + "-2"))

    def test_an_existing_eval_set_is_listed_and_loaded_as_an_approved_set(self) -> None:
        sets = self.tmp / ".atlan-evals" / "sets"
        sets.mkdir(parents=True)
        (sets / "finance.jsonl").write_text(
            '{"id": "m1", "question": "Total net revenue?", "sql": "SELECT 1", "tests": "Net Revenue", "note": "booked orders only"}\n'
            '{"question": "Refund rate?", "sql": "SELECT 2"}\n')
        chosen = self.scope("inputs", "--project", str(self.tmp))
        self.assertEqual((chosen["status"], len(chosen["sets"])), ("choose", 1))
        generated = self.scope("inputs", "--project", str(self.tmp), "--generate")
        self.assertEqual((generated["status"], generated["context_saved_to"]), ("ok", None))
        listed = self.scope("sets", "--project", str(self.tmp))
        self.assertEqual([(s["name"], s["questions"]) for s in listed["sets"]], [("finance", 2)])
        res = self.scope("load", "--project", str(self.tmp), "--file", str(sets / "finance.jsonl"))
        self.assertEqual((res["status"], res["questions"]), ("ok", 2))
        ev = Path(res["evals"])
        spec = json.loads((ev / "work" / "spec.json").read_text())
        self.assertEqual([(r["id"], r["review_status"], r["golden_sql"]) for r in spec["records"]],
                         [("m1", "approved", "SELECT 1"), ("q2", "approved", "SELECT 2")])
        self.assertEqual(json.loads((ev / "work" / "selection.json").read_text())[0]["metric"], "Net Revenue")

    def test_a_set_line_without_sql_is_refused(self) -> None:
        bad = self.tmp / "bad.jsonl"
        bad.write_text('{"question": "Total net revenue?"}\n')
        self.assertEqual(self.scope("load", "--project", str(self.tmp), "--file", str(bad))["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
