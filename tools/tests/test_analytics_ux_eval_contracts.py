"""Check deterministic UX graders and the evidence their reply-only judges receive.

Claude Code documents that last_message judges do not see the case prompt:
https://code.claude.com/docs/en/plugin-evals#what-a-grader-can-look-at
These tests validate grader contracts without invoking a paid judge. They do not
claim to predict LLM verdicts. Like test_tools.EvalCasesTest, they inspect the small
authored YAML subset directly rather than adding a YAML dependency.
"""

from pathlib import Path
import re
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
    "ux-project-understanding", "ux-native-local-details", "ux-intent-domain", "ux-intent-purpose", "ux-intent-known",
    "ux-finish-local-review",
}


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
        for reference in ("SUM(s.units)", "s.status = 'shipped'", "region", "shipping-01",
                          "start_date", "2026-10-01", "bound separately"):
            self.assertIn(reference, body)

    def test_final_judge_has_question_references_and_time_distinctions(self) -> None:
        body = graders("ux-one-metric-final")["supported-question-handoff"]
        for question in QUESTIONS:
            self.assertIn(question, body)
        self.assertIn("published earlier", body)
        self.assertIn("ran earlier", body)
        self.assertIn("in this turn", body)
        self.assertIn("future explanation", body)
        self.assertIn("before running SQL", body)

    def test_checkpoint_cases_prohibit_calls_on_every_available_runner(self) -> None:
        expected = {"Bash"} | {
            "mcp__plugin_atlanai-analytics_atlan__mcp__" + runner
            for runner in ("call_read_tool", "call_write_tool", "call_tool")
        }
        for name in sorted(CASE_NAMES):
            forbidden = set()
            for body in graders(name).values():
                if "    type: tool_used\n" not in body or "    max: 0\n" not in body:
                    continue
                self.assertIn("    min: 0\n", body, name)
                # No input_match loophole: any call on these checkpoint tools fails.
                self.assertNotIn("input_match:", body, name)
                forbidden.add(re.search(r"(?m)^    tool: (.+)$", body).group(1))
            self.assertEqual(forbidden, expected | ({"Write"} if name == "ux-native-local-details" else set()), name)

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


if __name__ == "__main__":
    unittest.main()
