"""What a large semantic model needed at publish time: the DAX zero date is not a fixed reporting
date, the exported card does not carry its SQL twice, and `X ($)` and `X (%)` are two metrics."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'plugins/atlanai-analytics'
SCRIPTS = PLUGIN / 'skills/analytics-build/scripts'
ENGINE = PLUGIN / 'skills/metrics-generation/modules/metrics/engine'
sys.path.insert(0, str(PLUGIN / 'skills/metrics-generation'))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / 'src'))
from modules.metrics import driver
import context_contracts
from metrics_agent.lib import slugs

spec = importlib.util.spec_from_file_location('publish_time_export', PLUGIN / 'skills/metrics-generation/modules/metrics/export.py')
exporter = importlib.util.module_from_spec(spec)
with mock.patch.dict(sys.modules, {'driver': driver}):
    spec.loader.exec_module(exporter)

BIG = 'SELECT ' + ' + '.join(f'SUM(t.c{i})' for i in range(4000)) + ' AS v FROM db.s.fact t'


def card(**over):
    base = {'name': 'Members Gained', 'metric_ref': 'members-gained', 'source_id': 'metric:s:abc',
            'description': 'Members who moved up a tier.', 'dialect': 'snowflake',
            'entity': 'db.s.moves', 'alias': 'm', 'sql': 'SELECT 1 AS v FROM db.s.moves m',
            'formula': 'SUM(m.v)', 'aggregation': {'type': 'sum'}, 'output': {'datatype': 'number'},
            'derived_from': ['q-1'], 'gotchas': ['A note for the model owner.'],
            'examples': [{'source': 'witness', 'sql': 'SELECT 1', 'question': 'How many?',
                          'witness_rows': 1}]}
    base.update(over)
    return base


def fixed_date_errors(sql):
    """The fixed-date finding the metric checker makes on one SQL field."""
    return ['source.sql holds a fixed date'] if context_contracts._DATE.search(sql) else []


class ZeroDateTests(unittest.TestCase):
    def test_dax_zero_date_is_not_a_fixed_reporting_date(self):
        sql = ("SELECT 1 FROM db.s.t t WHERE t.d >= x OR (t.d IS NULL AND "
               "CAST('1899-12-30' AS DATE) >= x)")
        self.assertEqual(fixed_date_errors(sql), [])

    def test_a_real_period_literal_is_still_refused(self):
        self.assertTrue(fixed_date_errors("SELECT 1 FROM db.s.t t WHERE t.d >= '2026-07-01'"))
        self.assertTrue(fixed_date_errors(
            "SELECT 1 FROM db.s.t t WHERE t.d >= '1899-12-31' AND t.e = CAST('1899-12-30' AS DATE)"))


class CardEvidenceTests(unittest.TestCase):
    def body(self, c):
        return exporter.metric_body(c, {'layer_sha': 'sha', 'producer': 'test'}, 'commit')

    def test_card_copy_drops_only_what_the_definition_holds(self):
        d = self.body(card())['definition']
        kept = d['extensions']['metrics_agent']['card']
        self.assertEqual(d['source']['sql'], card()['sql'])
        self.assertNotIn('sql', kept)
        self.assertEqual(kept['sql_at'], 'definition.source.sql')
        self.assertEqual(kept['examples'], [{'witness_rows': 1}])
        self.assertEqual(d['examples'][0]['sql'], 'SELECT 1')
        for field in ('gotchas', 'derived_from', 'name', 'formula'):
            self.assertEqual(kept[field], card()[field])

    def test_large_statement_is_stored_once(self):
        c = card(sql=BIG, examples=[{'source': 'witness', 'sql': BIG}])
        size = len(json.dumps(self.body(c), separators=(',', ':')).encode())
        self.assertLess(size, 2.2 * len(BIG))

    def test_card_without_sql_keeps_its_entity(self):
        kept = self.body(card(sql=None))['definition']['extensions']['metrics_agent']['card']
        self.assertEqual(kept['entity'], 'db.s.moves')
        self.assertNotIn('sql_at', kept)


class MetricRefTests(unittest.TestCase):
    def test_currency_and_percent_are_two_metrics(self):
        self.assertEqual(slugs.metric_ref('Var ($) LY PLR Sales'), 'var-usd-ly-plr-sales')
        self.assertEqual(slugs.metric_ref('Var (%) LY PLR Sales'), 'var-pct-ly-plr-sales')

    def test_plain_names_keep_their_handle(self):
        for name in ('PLR Sales', 'Total Members', 'Conversion Rate GA'):
            self.assertEqual(slugs.metric_ref(name), slugs.kebab(name))

    def test_case_still_folds(self):
        self.assertEqual(slugs.metric_ref('AOV ($)'), slugs.metric_ref('aov ($)'))


if __name__ == '__main__':
    unittest.main()
