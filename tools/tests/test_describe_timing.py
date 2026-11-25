"""Describing statements at the end: what the metric gates read instead, and the run order."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    from metrics_agent.lib import describe_timing as T
    from metrics_agent.lib import filter_carry as F
    from metrics_agent.stages import run

OFF = {'METRICS_AGENT_DESCRIBE_AT': 'start', 'METRICS_AGENT_BI_DRAFT': ''}
END = {'METRICS_AGENT_DESCRIBE_AT': '', 'METRICS_AGENT_BI_DRAFT': 'model'}
END_CODE = {'METRICS_AGENT_DESCRIBE_AT': '', 'METRICS_AGENT_BI_DRAFT': ''}


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class ColumnsTest(unittest.TestCase):
    def by_name(self, sql):
        return {c['column']: c for c in T.columns(sql, 'snowflake')}

    def test_scalar_subquery_column_names_its_expression(self):
        cols = self.by_name(
            "SELECT (SELECT COUNT(*) FROM db.s.book WHERE 1=1) AS c5, "
            "(SELECT COUNT(*) FROM db.s.book WHERE 1=1 AND 1=0) AS p5")
        self.assertEqual(cols['c5']['computes'], 'COUNT(*)')
        self.assertEqual(cols['c5']['from'], ['db.s.book'])
        self.assertNotIn('always_empty', cols['c5'])
        self.assertTrue(cols['p5']['always_empty'])

    def test_a_false_comparison_is_an_ordinary_filter(self):
        cols = self.by_name(
            "SELECT (SELECT COUNT(*) FROM db.s.account a WHERE a.is_deleted = FALSE) AS live, "
            "(SELECT COUNT(*) FROM db.s.account a WHERE FALSE) AS never")
        self.assertNotIn('always_empty', cols['live'])
        self.assertTrue(cols['never']['always_empty'])

    def test_expression_over_a_cte_column_resolves_its_input(self):
        cols = self.by_name(
            "WITH engagement AS (SELECT account_id, COUNT_IF(type = 'form') AS forms "
            "FROM db.s.events GROUP BY 1) "
            "SELECT a.id, COALESCE(eg.forms, 0) AS forms FROM db.s.account a "
            "LEFT JOIN engagement eg ON eg.account_id = a.id")
        inputs = cols['forms']['inputs']
        self.assertEqual(inputs[0]['column'], 'eg.forms')
        self.assertIn('COUNT_IF', inputs[0]['computes'])

    def test_unparseable_sql_gives_no_columns(self):
        self.assertEqual(T.columns('SELECT FROM WHERE (', 'snowflake'), [])

    def test_facts_keep_only_the_columns_the_sql_hides(self):
        o = {'sql': "SELECT a.region, (SELECT COUNT(*) FROM db.s.book) AS c5 FROM db.s.account a"}
        with mock.patch.dict(os.environ, END):
            cols = T.facts(o, 'snowflake', light=True)['columns']
        self.assertEqual([c['column'] for c in cols], ['c5'])


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class FilterCarryTest(unittest.TestCase):
    MERGED = {'mandatory_filters': [{'filter': "CAMPAIGN_NAME = 'Activate_2026'"},
                                    {'filter': 'account is AI-ready'}],
              'optional_filters': [{'filter': "S2_ENTRY_DT BETWEEN '2026-07-01' AND '2026-09-19'"}]}

    def test_a_card_that_carries_every_filter_passes(self):
        card = {'mandatory_filters': ["db.s.m.CAMPAIGN_NAME = 'Activate_2026'"],
                'filters': ['db.s.m.S2_ENTRY_DT BETWEEN {{start}} AND {{end}}']}
        self.assertEqual(F.lost(card, self.MERGED, 'snowflake'), [])

    def test_a_dropped_window_is_named(self):
        card = {'mandatory_filters': ["db.s.m.CAMPAIGN_NAME = 'Activate_2026'",
                                      'db.s.m.S2_ENTRY_DT IS NOT NULL']}
        out = F.lost(card, self.MERGED, 'snowflake')
        self.assertEqual(len(out), 1)
        self.assertIn('s2_entry_dt range', out[0])

    def test_a_fixed_value_made_optional_is_named(self):
        card = {'filters': ['db.s.m.CAMPAIGN_NAME = {{campaign}}',
                            'db.s.m.S2_ENTRY_DT BETWEEN {{start}} AND {{end}}']}
        out = F.lost(card, self.MERGED, 'snowflake')
        self.assertEqual(len(out), 1)
        self.assertIn('only offers it as an optional filter', out[0])

    def test_a_filter_in_the_from_clause_or_formula_counts(self):
        merged = {'mandatory_filters': [{'filter': 'c.IS_DELETED = FALSE'},
                                        {'filter': "r.AI_TIER = 'Leader'"},
                                        {'filter': 'CONTACT_ID IS NOT NULL'}]}
        card = {'formula': "COUNT(CASE WHEN db.s.r.AI_TIER = 'Leader' THEN db.s.r.CONTACT_ID END)",
                'source': {'sql': '(SELECT * FROM db.s.c WHERE db.s.c.IS_DELETED = FALSE) AS c'}}
        self.assertEqual(F.lost(card, merged, 'snowflake'), [])

    def test_a_reason_in_notes_lets_a_filter_go(self):
        card = {'mandatory_filters': ["db.s.m.CAMPAIGN_NAME = 'Activate_2026'"],
                'notes': 'S2_ENTRY_DT window left out: the dashboard shows all time.'}
        self.assertEqual(F.lost(card, self.MERGED, 'snowflake'), [])


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class ProseTest(unittest.TestCase):
    BI = {'name': 'Accounts', 'question': 'How many accounts?', 'description': 'All accounts.',
          'provenance': {'lane': 'bi'}}
    PLACEHOLDER = {'name': 'vq-measure', 'question': 'measure', 'description': 'n/a',
                   'name_provisional': True, 'provenance': {'lane': 'history'}}

    def test_placeholder_prose_is_hidden(self):
        with mock.patch.dict(os.environ, END):
            self.assertEqual(T.prose(self.PLACEHOLDER)['question'], None)

    def test_dashboard_draft_is_kept_unless_drafts_are_made_by_code(self):
        with mock.patch.dict(os.environ, END):
            self.assertEqual(T.prose(self.BI)['question'], 'How many accounts?')
        with mock.patch.dict(os.environ, END_CODE):
            self.assertIsNone(T.prose(self.BI)['question'])

    def test_option_c_is_the_default_and_start_restores_the_earlier_order(self):
        with mock.patch.dict(os.environ, END_CODE):
            self.assertTrue(T.describe_at_end())
            self.assertTrue(T.bi_draft_by_code())
        with mock.patch.dict(os.environ, OFF):
            self.assertFalse(T.describe_at_end())
            self.assertFalse(T.bi_draft_by_code())


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class RunOrderTest(unittest.TestCase):
    def labels(self, env):
        args = SimpleNamespace(answers_dir='/tmp/answers', run='/tmp/run', skip_a3b_review=False,
                               c3_deterministic=False)
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(run, 'publish_steps', return_value=([], [], [])):
            return [s.label for s in run.plan('both', args)]

    def test_start_describes_before_the_metric_gates(self):
        steps = self.labels(OFF)
        self.assertLess(steps.index('m2_ingest'), steps.index('x1_emit'))
        self.assertIn('c3_emit', steps)
        self.assertNotIn('x3_refresh', steps)

    def test_describe_at_end_asks_early_ingests_last_and_refreshes_cards(self):
        steps = self.labels(END)
        self.assertLess(steps.index('m2_emit'), steps.index('x1_emit'))
        self.assertLess(steps.index('x4_ingest'), steps.index('m2_ingest'))
        self.assertLess(steps.index('m2_ingest'), steps.index('m1_merge'))
        self.assertLess(steps.index('m1_merge'), steps.index('x3_refresh'))
        self.assertEqual(steps[-3:], ['x3_refresh', 'd4b_refresh', 'x4_refresh'])

    def test_drafts_by_code_skip_the_dashboard_draft_gate(self):
        steps = self.labels(END_CODE)
        self.assertNotIn('c3_emit', steps)
        self.assertIn('c3_questions', steps)


if __name__ == '__main__':
    unittest.main()
