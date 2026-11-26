"""Check deterministic UX graders and the evidence their reply-only judges receive.

Claude Code documents that last_message judges do not see the case prompt:
https://code.claude.com/docs/en/plugin-evals#what-a-grader-can-look-at
These tests validate grader contracts without invoking a paid judge. They do not
claim to predict LLM verdicts. Like test_tools.EvalCasesTest, they inspect the small
authored YAML subset directly rather than adding a YAML dependency.
"""

from pathlib import Path
from unittest import mock
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals" / "atlan-analytics" / "cases"
SQL = (
    "SELECT region, SUM(units) AS shipped_units FROM shipping.shipments "
    "WHERE status = 'shipped' AND shipped_at >= $start_date GROUP BY region LIMIT 100"
)
QUESTIONS = (
    "How many units shipped by region?",
    "What was the shipping cost by region?",
)
CASE_NAMES = {
    "ux-opening-orientation", "ux-one-metric-final", "ux-details-on-request",
    "ux-no-workspace-preserves-work", "ux-resume-held-auth", "ux-explicit-new-workspace",
    "ux-project-understanding", "ux-native-local-details", "ux-intent-domain", "ux-intent-known",
    "ux-finish-local-review", "ux-checklist-opening", "ux-checklist-resume",
}
# Real tasks: the case's scaffold saves a shipping project (evals/atlan-analytics/fixtures/shipping_run.py)
# and the model finds and reads it with the skill's helpers, so these may use the shell.
SAVED_RUN_CASES = {
    "ux-details-on-request": "local", "ux-native-local-details": "local", "ux-explicit-new-workspace": "local",
    "ux-one-metric-final": "published", "setup-preview-before-workspace": "preview",
}
FIXTURE = ROOT / "evals" / "atlan-analytics" / "fixtures" / "shipping_run.py"
PLUGIN = ROOT / "plugins" / "atlanai-analytics"


def graders(case_name: str) -> dict[str, str]:
    text = (CASES / case_name / "case.yaml").read_text(encoding="utf-8")
    return dict(re.findall(r"(?m)^  - name: (\S+)\n((?:    .*\n)+)", text))


class AnalyticsUxEvalContractsTest(unittest.TestCase):
    def test_all_reply_judges_declare_their_context_boundary(self) -> None:
        found = {path.parent.name for path in CASES.glob("ux-*/case.yaml")}
        self.assertEqual(found, CASE_NAMES)
        for name in sorted(CASE_NAMES):
            for grader, body in graders(name).items():
                if "    type: llm\n" in body:
                    self.assertIn("    focus: last_message\n", body, f"{name}: {grader}")
                    # These phrases hid expected data in a prompt the judge cannot see.
                    self.assertNotRegex(body, r"complete supplied SQL|two supplied supported questions")

    def test_detail_judge_has_the_exact_query_and_parameter_reference(self) -> None:
        body = graders("ux-details-on-request")["exact-sql-and-evidence"]
        self.assertIn(SQL, body)
        for reference in ("SUM(s.units)", "s.status = 'shipped'", "region", "start_date", "2026-10-01",
                          "stay a parameter"):
            self.assertIn(reference, body)

    def test_final_judge_has_question_references_and_time_distinctions(self) -> None:
        body = graders("ux-one-metric-final")["supported-question-handoff"]
        for question in QUESTIONS:
            self.assertIn(question, body)
        self.assertIn("in this turn", body)
        self.assertIn("before running SQL", body)
        self.assertIn("On-time rate", body, "the held metric is not offered as answerable")

    def forbidden(self, name: str) -> set[str]:
        tools = set()
        for body in graders(name).values():
            if "    type: tool_used\n" not in body or "    max: 0\n" not in body:
                continue
            self.assertIn("    min: 0\n", body, name)
            if "input_match:" not in body:
                tools.add(re.search(r"(?m)^    tool: (.+)$", body).group(1))
        return tools

    def test_checkpoint_cases_prohibit_calls_on_every_available_runner(self) -> None:
        expected = {"Bash"} | {
            "mcp__plugin_atlanai-analytics_atlan__mcp__" + runner
            for runner in ("call_read_tool", "call_write_tool", "call_tool")
        }
        for name in sorted(CASE_NAMES - set(SAVED_RUN_CASES)):
            # No input_match loophole: any call on these checkpoint tools fails.
            for body in graders(name).values():
                if "    type: tool_used\n" in body and "    max: 0\n" in body:
                    self.assertNotIn("input_match:", body, name)
            self.assertEqual(self.forbidden(name), expected, name)

    def test_saved_run_cases_read_the_project_their_scaffold_saves_and_write_nothing_to_atlan(self) -> None:
        writes = {"mcp__plugin_atlanai-analytics_atlan__mcp__" + runner for runner in ("call_write_tool", "call_tool")}
        for name, variant in sorted(SAVED_RUN_CASES.items()):
            text = (CASES / name / "case.yaml").read_text(encoding="utf-8")
            self.assertIn("  scaffold_script: scaffold.sh\n", text, name)
            self.assertRegex(text, r"(?m)^  allowed_tools: \[.*\bBash\b.*\]$", name)
            scaffold = (CASES / name / "scaffold.sh").read_text(encoding="utf-8")
            self.assertIn('/../fixtures/shipping_run.py" "$here/../.." ' + variant, scaffold, name)
            self.assertTrue(writes <= self.forbidden(name), name)

    def test_sql_regex_preserves_semantics_but_allows_formatting(self) -> None:
        body = graders("ux-details-on-request")["saved-sql-preserved"]
        self.assertIn("    type: regex\n", body)
        self.assertIn("    target: last_message\n", body)
        # YAML single-quoted scalars escape a quote by doubling it. The pattern
        # uses only the syntax shared by Python and JavaScript regular expressions.
        scalar = re.search(r"(?m)^    pattern: '(.*)'$", body).group(1)
        pattern = scalar.replace("''", "'")
        formatted = (
            "SELECT region, SUM(units) AS shipped_units\nFROM shipping.shipments\n"
            "WHERE status = 'shipped'\n  AND shipped_at >= $start_date\n"
            "GROUP BY region\nLIMIT 100"
        )
        for query in (SQL, formatted, SQL.replace("SUM(units)", "SUM ( units )")):
            with self.subTest(query=query):
                self.assertRegex(f"```sql\n{query}\n```", pattern)
        changed_queries = (
            SQL.replace("status = 'shipped' AND ", ""),
            SQL.replace(" AND shipped_at >= $start_date", ""),
            SQL.replace("$start_date", "'2026-10-01'"),
            SQL.replace("'shipped'", "'returned'"),
            SQL.replace(">=", ">"),
            SQL.replace("SUM(units)", "COUNT(units)"),
            SQL.replace("LIMIT 100", "LIMIT 1000"),
            SQL + " UNION SELECT region, 0 FROM other_table",
        )
        for query in changed_queries:
            with self.subTest(query=query):
                self.assertNotRegex(f"```sql\n{query}\n```", pattern)
        self.assertNotRegex(SQL, pattern, "The saved query must be shown in a code block")


class SavedRunFixtureTest(unittest.TestCase):
    """The shipping project the saved-run cases ask about, read back by the helper the model runs."""

    def journey(self, variant: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(FIXTURE), str(PLUGIN), variant], cwd=tmp, check=True)
            out = subprocess.run([sys.executable, str(PLUGIN / "skills" / "analytics-build" / "scripts" / "project.py"),
                                  "--run-dir", str(Path(tmp) / ".atlan-analytics" / "shipping-run"), "journey", "status"],
                                 cwd=tmp, check=True, capture_output=True, text=True).stdout
        return json.loads(out)

    def test_each_variant_reads_back_as_its_cases_say(self) -> None:
        local = self.journey("local")
        self.assertIn("3 metrics and 1 saved query. None saved to Atlan Trident yet.", local["chat"])
        self.assertIn("Example tested.", local["chat"])
        self.assertEqual(local["interaction"]["focus"], "metric:shipped-units")
        preview = self.journey("preview")
        self.assertIn("Structure checked, not yet tested.", preview["chat"])
        self.assertEqual(preview["continuation"]["action"], "destination")
        published = self.journey("published")
        self.assertIn("8 metrics and 4 saved queries. 11 saved to Atlan Trident.", published["chat"])
        self.assertIn("1 item is held back from Atlan Trident: On-time rate (The denominator is undefined", published["chat"])
        # A release keeps the check it passed, so the hand-off's sample can say how it was checked.
        self.assertIn("Saved to Atlan Trident, example tested.", published["chat"])

    def test_the_ready_question_is_composed_where_the_run_folder_cannot_be_written(self) -> None:
        # The eval sandbox's shell, like a read-only checkout, refuses writes: the note that spares a repeat is lost, the
        # question is not.
        sys.path.insert(0, str(PLUGIN / "skills" / "analytics-build" / "scripts"))
        import project
        import side_view
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(FIXTURE), str(PLUGIN), "published"], cwd=tmp, check=True)
            run = str(Path(tmp) / ".atlan-analytics" / "shipping-run")
            out = io.StringIO()
            with mock.patch.object(side_view, "note", side_effect=OSError(30, "Read-only file system")), \
                    mock.patch("sys.argv", ["project.py", "--run-dir", run, "question", "ready"]), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(project.main(), 0)
        asked = json.loads(out.getvalue())
        self.assertIn("7 metrics and 4 saved queries saved to Atlan Trident, in Shipping; 1 metric stored on this machine",
                      " ".join(asked["question"].split()))
        labels = [option["label"] for option in asked["options"]]
        self.assertEqual(labels[:2], ["How many units shipped by region?", "What was the shipping cost by region?"])



class DemoRunFixtureTest(unittest.TestCase):
    """The Fixit demo demo-ready-next-actions picks up, read back by the helpers the model runs."""

    def test_the_demo_reads_back_ready_for_its_first_question(self) -> None:
        case = CASES / "demo-ready-next-actions"
        self.assertIn('/../fixtures/demo_run.py" "$here/../.."', (case / "scaffold.sh").read_text(encoding="utf-8"))
        judge = " ".join(graders("demo-ready-next-actions")["expected-handoff"].split())
        helper = [sys.executable, str(PLUGIN / "skills" / "analytics-build" / "scripts" / "project.py"), "--run-dir"]
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(FIXTURE.parent / "demo_run.py"), str(PLUGIN)], cwd=tmp, check=True)
            run = str(Path(tmp) / ".atlan-analytics" / "fixit-demo")
            status = json.loads(subprocess.run(helper + [run, "journey", "status"], cwd=tmp, check=True,
                                               capture_output=True, text=True).stdout)
            ready = json.loads(subprocess.run(
                helper + [run, "question", "ready", "--data",
                          str(PLUGIN / "skills" / "analytics-demo" / "references" / "yard.json")],
                cwd=tmp, check=True, capture_output=True, text=True).stdout)
        steps = status["checklist"]["steps"]
        self.assertEqual(len(steps), 8)
        self.assertEqual([s["id"] for s in steps if s["status"] != "completed"], ["ask"])
        self.assertNotIn("visual", status)
        self.assertIn("Stored on this machine", ready["question"])
        self.assertIn("on this machine", ready["question"])
        # The judge holds the questions the helper offers, since it never sees the prompt or the run.
        for option in ready["options"][:2]:
            self.assertIn(option["label"], judge)



if __name__ == "__main__":
    unittest.main()
