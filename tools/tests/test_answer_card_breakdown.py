"""A breakdown's answer card led with its first row. The Ticketfuddle demo run of 2026-11-26 asked "How much net fee
revenue did Ticketfuddle earn by fee type?", got seven rows back in the query's own order, and the card read
"$1,677 · delivery": the delivery fee alone, not the $302,118 the seven rows add up to."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from analytics_run import SCRIPTS, make_run, stop_pages

sys.path.insert(0, str(SCRIPTS))
import run_facts  # noqa: E402
import visuals  # noqa: E402

QUESTION = 'How much net fee revenue did Ticketfuddle earn by fee type?'
# The rows the run's query returned, as its answer.json recorded them.
RESULT = {'columns': [{'name': 'fee_type'}, {'name': 'net_fee_revenue'}],
          'rows': [['delivery', 1677.0], ['demand', 51641.05], ['order_processing', 24655.9], ['refunded', -12175.08],
                   ['resale_buyer', 22963.8], ['resale_seller', 11481.9], ['service', 201873.12]],
          'truncated': False}
SQL = 'SELECT t.fee_type, SUM(t.amount) AS net_fee_revenue FROM ledger t GROUP BY t.fee_type'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


class BreakdownAnswerCard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root, local=True)
        write(self.run / 'context' / 'metrics.json', [
            {'name': 'Net Fee Revenue', 'definition': {'formula': 'SUM(ledger.amount)', 'aggregation': {'type': 'sum'},
                                                       'output': {'unit': 'USD'}}},
            {'name': 'Fee Take Rate', 'definition': {'formula': 'SUM(f) / SUM(v)', 'aggregation': {'type': 'ratio'},
                                                     'output': {'unit': 'fraction'}}}])
        self.facts = run_facts.RunFacts(self.run)

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def answer(self, result=RESULT, metric='Net Fee Revenue', **sql):
        return visuals.answer_from({'question': QUESTION, 'metrics': [metric]}, result, self.facts, **sql)

    def test_a_breakdown_of_a_metric_that_adds_up_leads_with_the_total(self):
        data = self.answer()
        self.assertEqual(data['headline'], 'Net Fee Revenue: $302,118')
        self.assertNotIn('highlight', data, 'no single row is the answer')
        self.assertEqual(len(data['rows']), 7)
        text = visuals.render('answer', {**data, 'page': ''}, plain=True)
        self.assertIn('$302,118', text.splitlines()[0])
        self.assertNotIn('▶', text)

    def test_a_breakdown_of_a_rate_never_adds_its_rows(self):
        result = {'columns': ['fee_type', 'fee_take_rate'], 'rows': [['primary', 0.27], ['resale', 0.12]]}
        self.assertNotIn('0.39', self.answer(result, 'Fee Take Rate')['headline'])

    def test_a_top_n_or_cut_short_result_never_claims_a_total(self):
        self.assertEqual(self.answer(sql=SQL + ' ORDER BY 2 DESC LIMIT 3')['headline'], 'delivery: $1,677')
        self.assertEqual(self.answer({**RESULT, 'truncated': True})['headline'], 'delivery: $1,677')

    def test_a_null_value_in_the_first_row_keeps_the_card(self):
        # A job with no rework logged returns NULL beside its name; the card was dropped because the first row's value
        # column was read as having no number.
        result = {'columns': ['job', 'net_fee_revenue'], 'rows': [['P01', None], ['P02', 30084.0], ['P03', 3716.0]]}
        data = self.answer(result)
        self.assertEqual(data['headline'], 'Net Fee Revenue: $33,800')
        self.assertEqual(data['rows'], [['P02', 30084.0], ['P03', 3716.0]])


if __name__ == '__main__':
    unittest.main()
