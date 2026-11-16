"""Opt-in ratio representation equivalence stays column- and row-scoped."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/analytics-build/scripts/analytics_eval.py'
spec = importlib.util.spec_from_file_location('analytics_eval_representation', SCRIPT)
analytics_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analytics_eval)


class RepresentationEquivalenceTest(unittest.TestCase):
    def test_opt_in_percent_fraction_with_nulls_and_associated_rows(self):
        gold = {'columns': ['region', 'rate_pct', 'n'],
                'rows': [['west', 12.34, 10], ['east', None, 2]]}
        agent = {'columns': ['region', 'rate_pct', 'n'],
                 'rows': [['east', None, 2], ['west', .1234, 10]]}
        contract = {'percent_fraction': {'rate_pct': {'decimal_places': 4}}}
        self.assertEqual(analytics_eval.grade(gold, agent, 'select x', 'select x')['class'], 'wrong_result')
        scored = analytics_eval.grade(gold, agent, 'select x', 'select x', representation_equivalence=contract)
        self.assertEqual(scored['class'], 'pass')
        swapped = {'columns': ['region', 'rate_pct', 'n'],
                   'rows': [['east', None, 10], ['west', .1234, 2]]}
        self.assertEqual(analytics_eval.grade(gold, swapped, 'select x', 'select x',
                                              representation_equivalence=contract)['class'], 'wrong_result')

    def test_wrong_denominator_and_uncontracted_scale_remain_wrong(self):
        gold = {'columns': ['rate_pct', 'revenue'], 'rows': [[12.34, 10000]]}
        bad_ratio = {'columns': ['rate_pct', 'revenue'], 'rows': [[.1234, 1000000]]}
        contract = {'percent_fraction': {'rate_pct': {'decimal_places': 4}}}
        self.assertEqual(analytics_eval.grade(gold, bad_ratio, 'select x', 'select x',
                                              representation_equivalence=contract)['class'], 'wrong_result')
        self.assertEqual(analytics_eval.grade(gold, bad_ratio, 'select x', 'select x')['class'], 'wrong_result')

    def test_contract_validation_and_declared_precision(self):
        gold = {'columns': ['rate_pct'], 'rows': [[12.34]]}
        agent = {'columns': ['rate_pct'], 'rows': [[.12345]]}
        loose = {'percent_fraction': {'rate_pct': {'decimal_places': 2}}}
        tight = {'percent_fraction': {'rate_pct': {'decimal_places': 4}}}
        self.assertEqual(analytics_eval.grade(gold, agent, 'select x', 'select x',
                                              representation_equivalence=tight)['class'], 'wrong_result')
        self.assertEqual(analytics_eval.grade(gold, agent, 'select x', 'select x',
                                              representation_equivalence=loose)['class'], 'pass')
        precise_gold = {'columns': ['rate_pct'], 'rows': [[12.34567]]}
        precise_agent = {'columns': ['ratio'], 'rows': [[.12345]]}
        self.assertEqual(analytics_eval.grade(precise_gold, precise_agent, 'select x', 'select x',
                                              representation_equivalence={'percent_fraction': {
                                                  'rate_pct': {'decimal_places': 6}}})['class'], 'wrong_result')
        with self.assertRaisesRegex(ValueError, 'decimal_places'):
            analytics_eval.grade(gold, agent, 'select x', 'select x',
                                 representation_equivalence={'percent_fraction': {'rate_pct': {'decimal_places': 20}}})

    def test_same_scale_uses_declared_precision_before_default_sig(self):
        for gold, actual, places in [(12.341, 12.344, 4), (.123456, .123455, 6)]:
            with self.subTest(gold=gold):
                result = analytics_eval.grade(
                    {'columns': ['ratio'], 'rows': [[gold]]},
                    {'columns': ['ratio_alias'], 'rows': [[actual]]},
                    representation_equivalence={'percent_fraction': {
                        'ratio': {'decimal_places': places}}})
                self.assertEqual(result['class'], 'wrong_result')

    def test_both_directions_aliases_and_multiple_contract_columns(self):
        contract = {'percent_fraction': {
            'billable_utilisation': {'decimal_places': 4},
            'waste_pct': {'decimal_places': 4},
        }}
        gold = {'columns': ['billable_utilisation', 'waste_pct', 'team'],
                'rows': [[.7654, 0, 'a'], [None, .1234, 'b']]}
        agent = {'columns': ['billable_pct', 'wasted_spending_share_pct', 'team'],
                 'rows': [[76.54, 0, 'a'], [None, 12.34, 'b']]}
        self.assertEqual(analytics_eval.grade(gold, agent, 'select x', 'select x',
                                              representation_equivalence=contract)['class'], 'pass')
        # Reverse direction, with percentage values in trusted results.
        reverse_gold = {'columns': ['rate'], 'rows': [[76.54], [0], [None]]}
        reverse_agent = {'columns': ['ratio'], 'rows': [[.7654], [0], [None]]}
        self.assertEqual(analytics_eval.grade(reverse_gold, reverse_agent, 'select x', 'select x',
                                              representation_equivalence={'percent_fraction': {
                                                  'rate': {'decimal_places': 4}}})['class'], 'pass')
        mixed_gold = {'columns': ['already_fraction', 'percent'], 'rows': [[.7654, 12.34]]}
        mixed_agent = {'columns': ['fraction_alias', 'fraction_alias_2'], 'rows': [[.7654, .1234]]}
        mixed_contract = {'percent_fraction': {
            'already_fraction': {'decimal_places': 4}, 'percent': {'decimal_places': 4}}}
        self.assertEqual(analytics_eval.grade(mixed_gold, mixed_agent, 'select x', 'select x',
                                              representation_equivalence=mixed_contract)['class'], 'pass')

    def test_non_numeric_cells_and_zero_are_safe(self):
        gold = {'columns': ['rate'], 'rows': [[0], [None], ['not reported']]}
        agent = {'columns': ['rate_alias'], 'rows': [[None], [0], ['not reported']]}
        self.assertEqual(analytics_eval.grade(gold, agent, 'select x', 'select x',
                                              representation_equivalence={'percent_fraction': {
                                                  'rate': {'decimal_places': 4}}})['class'], 'pass')
        nan_gold = {'columns': ['rate'], 'rows': [[float('nan')]]}
        nan_agent = {'columns': ['alias'], 'rows': [[float('nan')]]}
        self.assertEqual(analytics_eval.grade(nan_gold, nan_agent, 'select x', 'select x',
                                              representation_equivalence={'percent_fraction': {
                                                  'rate': {'decimal_places': 4}}})['class'], 'wrong_result')


if __name__ == '__main__':
    unittest.main()
