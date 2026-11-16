"""Result-limit wrappers must not discard analytical eval sources."""

import importlib.util
import sys
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[2] / (
    "plugins/atlanai-analytics/skills/eval-generation/modules/evals/engine")
HAS_SQLGLOT = importlib.util.find_spec("sqlglot") is not None


@unittest.skipUnless(HAS_SQLGLOT, "requires the evaluation runtime's sqlglot")
class QueryWrapperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ENGINE))
        from eval_builder import select
        cls.select = select

    def candidate(self, sql):
        return self.select.Candidate(query_id="source-query", sql=sql)

    def test_wrapped_business_aggregate_survives_unchanged(self):
        inner = ("SELECT COUNT(*) AS n FROM alerts WHERE status IN ('open', 'pending') "
                 "AND priority = 'urgent'")
        for sql in (inner, f"SELECT * FROM ({inner}) LIMIT 20",
                    f"SELECT * FROM (SELECT * FROM ({inner}) LIMIT 20) LIMIT 1000"):
            with self.subTest(sql=sql):
                candidate = self.candidate(sql)
                self.assertEqual(self.select.rank([candidate]), [candidate])
                self.assertEqual(candidate.sql, sql)

    def test_actual_browsing_stays_rejected_inside_wrappers(self):
        for sql in ("SELECT * FROM alerts LIMIT 20",
                    "SELECT * FROM (SELECT * FROM alerts) LIMIT 20",
                    "SELECT * FROM (SELECT * FROM (SELECT * FROM alerts)) LIMIT 20"):
            with self.subTest(sql=sql):
                self.assertEqual(self.select.rank([self.candidate(sql)]), [])

    def test_wrappers_do_not_bypass_other_gates(self):
        for inner in ("SELECT COUNT(*) FROM alerts WHERE account_id = 'A01'",
                      "SELECT RANDOM() FROM alerts WHERE status = 'open'",
                      "SELECT COUNT(*) FROM alerts WHERE status = ?"):
            with self.subTest(sql=inner):
                self.assertEqual(self.select.rank([self.candidate(
                    f"SELECT * FROM ({inner}) LIMIT 20")]), [])


if __name__ == "__main__":
    unittest.main()
