"""Business literal evidence never disables identifying-field or ambiguous-name controls."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import analytics_eval as ev
import context_contracts as mine

path = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine/src/metrics_agent/lib/literal_privacy.py'
spec = importlib.util.spec_from_file_location('engine_privacy_contract', path)
engine = importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)

BUSINESS = [
    "SELECT SUM(amount) FROM orders WHERE supplier = 'Harbor Builders'",
    "SELECT SUM(amount) FROM orders WHERE vendor_name = 'Example Ltd'",
    "SELECT SUM(amount) FROM orders WHERE town = 'Cedar Vale'",
    'SELECT SUM(amount) FROM orders o WHERE o."city_name" IN (\'Cedar Vale\', \'Harbor Town\')',
    "SELECT SUM(amount) FROM orders WHERE 'Cedar Vale' = o.city",
    "SELECT SUM(amount) FROM orders WHERE city NOT IN ('Cedar Vale', 'Harbor Town')",
    "SELECT SUM(amount) FROM orders WHERE stage = 'Pending Review'",
]
PRIVATE = [
    "SELECT SUM(amount) FROM orders WHERE owner = 'Synthetic Person'",
    "SELECT SUM(amount) FROM orders WHERE full_name = 'New York'",
    "SELECT SUM(amount) FROM orders WHERE ref = 'Synthetic Person'",
    "SELECT SUM(amount) FROM orders WHERE supplier = 'Synthetic Person'",
    "SELECT SUM(amount) FROM orders WHERE company_name = 'Harbor Builders'",
    "SELECT SUM(amount) FROM orders WHERE user_id = 42",
    "SELECT SUM(amount) FROM orders WHERE order_number IN (17, 18)",
    "SELECT SUM(amount) FROM orders WHERE ref = 'ord_ab12cd'",
    "SELECT SUM(amount) FROM orders WHERE email = 'person@example.test'",
    "SELECT SUM(amount) FROM orders WHERE city = 'Cedar Vale' AND ref = 'Synthetic Person'",
    "SELECT SUM(amount) FROM orders WHERE ref = /* outer /* inner */ city = --*/\n 'Synthetic Person'",
    "SELECT SUM(amount) FROM orders WHERE ref = /* city = */ 'Synthetic Person'",
    "SELECT SUM(amount) FROM orders WHERE ref = $tag$ city = 'Synthetic Person' $tag$",
    "SELECT SUM(amount) FROM orders WHERE ref IN (LOWER('Synthetic Person'))",
    "SELECT SUM(amount) FROM orders WHERE ref = 'city = ' || 'Synthetic Person'",
    "SELECT SUM(amount) FROM orders WHERE ref = 'value' -- city = 'Synthetic Person'",
]


def body(sql):
    return {'name': 'order-amount', 'display_name': 'Order amount', 'definition': {
        'spec_version': mine.QUERY_SPEC, 'question': 'What is the total amount?',
        'dialect': 'duckdb', 'sql': sql}}


class LiteralPrivacy(unittest.TestCase):
    def test_bounded_business_context_passes_both_history_routes_and_query_checks(self):
        for sql in BUSINESS:
            with self.subTest(sql=sql):
                self.assertIsNone(ev.pii_reason(sql))
                self.assertIsNone(engine.pii_reason(sql))
                self.assertIsNone(mine._pii(sql))
                self.assertEqual(mine.check_query(body(sql))[0], [])

    def test_person_customer_identifier_and_ambiguous_values_still_block(self):
        for sql in PRIVATE:
            with self.subTest(sql=sql):
                self.assertIsNotNone(ev.pii_reason(sql))
                self.assertEqual(ev.pii_reason(sql), engine.pii_reason(sql))
                self.assertIsNotNone(mine._pii(sql))
                self.assertTrue(mine.check_query(body(sql))[0])

    def test_business_context_is_not_a_prose_allowlist_or_an_early_exit(self):
        self.assertIsNotNone(engine.full_name_hit("supplier = 'Harbor Builders'"))
        self.assertIsNone(engine.full_name_hit("supplier = 'Harbor Builders'", sql=True))
        self.assertIsNotNone(engine.full_name_hit("city = 'Cedar Vale' AND ref = 'Synthetic Person'", sql=True))
        self.assertIsNotNone(engine.pii_reason("full_name = 'Closed Won'"))
        self.assertIsNone(engine.pii_reason("stage = 'Closed Won'"))

    def test_unsupported_expression_cannot_claim_business_evidence(self):
        for sql in ("SELECT 1 WHERE LOWER(city) = 'Cedar Vale'",
                    "SELECT 1 WHERE city = CONCAT('Cedar Vale', '')",
                    "SELECT 1 WHERE city IN (SELECT 'Cedar Vale')"):
            self.assertIsNotNone(engine.pii_reason(sql))

    def test_large_in_lists_keep_every_literal_checked(self):
        values = ', '.join(["'Cedar Vale'"] * 3000)
        self.assertIsNone(engine.pii_reason('SELECT 1 FROM places WHERE city IN (' + values + ')'))
        self.assertIsNotNone(engine.pii_reason('SELECT 1 FROM places WHERE city IN (' + values + ") AND ref = 'Synthetic Person'"))

    def test_filter_preserves_safe_rows_and_counts_but_never_reproduces_private_values(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'history.jsonl', Path(folder) / 'clean.jsonl'
            source.write_text('\n'.join(json.dumps({'sql': q, 'exec': 12, 'users': 2}) for q in BUSINESS + PRIVATE))
            result = ev.filter_querylog(str(source), str(target))
            rows = [json.loads(line) for line in target.read_text().splitlines()]
            self.assertEqual((result['kept'], result['dropped']), (len(BUSINESS), len(PRIVATE)))
            self.assertEqual([r['sql'] for r in rows], BUSINESS)
            self.assertTrue(all(r['exec'] == 12 and r['users'] == 2 for r in rows))
            self.assertNotIn('Synthetic Person', json.dumps(result) + target.read_text())
            self.assertNotIn('Synthetic Person', json.dumps(mine.check_query(body(PRIVATE[0]))))


if __name__ == '__main__':
    unittest.main()
