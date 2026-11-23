"""A person who picks some pages of a report gets a build of those pages only.

A live dashboard run (2026-11-23) asked "Which pages matter to you?", the person picked one
page of eight (11 of 50 tiles), and the build still wrote questions, metrics and saved queries
for all 50 tiles over about 35 minutes. The report is fetched before the pick, and run.yml
cannot change once a run has started, so the pick is its own record (`page_scope.json`,
written by `ENGINE pages`). A2 leaves the other pages' units out, keeping the pages the chosen
tiles drill through to or show as tooltips; the runner re-runs A2 when the pick changes.
"""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
METRICS = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation'
MODULE = METRICS / 'modules/metrics'
ENGINE = MODULE / 'engine'
sys.path.insert(0, str(METRICS))
sys.path.insert(0, str(MODULE))
sys.path.insert(0, str(ENGINE / 'src'))
from modules.metrics import driver  # noqa: E402

DEPENDENCIES = all(importlib.util.find_spec(n) for n in ('sqlglot', 'yaml', 'jsonschema'))
if DEPENDENCIES:
    from metrics_agent.lib import artifacts, page_scope, policy  # noqa: E402
    from metrics_agent.plugs.readers import powerbi  # noqa: E402
    from metrics_agent.stages import a2_question_units as a2  # noqa: E402
    from metrics_agent.stages import run as runner  # noqa: E402


def unit(page, visual, *, measures=('Total Sales',), columns=(), params=(), links=()):
    return {'unit_id': f'powerbi::Sales::{page}::{visual}', 'container': page, 'container_kind': 'page',
            'dashboards': [{'name': page, 'title': page, 'position': 0}], 'visual_id': visual,
            'visual_type': 'card', 'title': visual, 'measures': list(measures), 'columns': list(columns),
            'column_tables': ['sales' for _ in columns], 'filters': [], 'sort': None, 'top_n': None,
            'parameters': [dict(p) for p in params], 'subtitle': None, 'description': None,
            'number_format': None, 'axis_titles': {}, 'tooltip': None, 'reference_lines': [],
            'conditional_formatting': [], 'source_fragments': {'widget': None, 'dashboard': None, 'prose': [page]},
            **({'links': list(links)} if links else {})}


REGION = {'name': 'p1', 'kind': 'member', 'column': 'region', 'table': 'sales', 'keeps_filters': True}
PRODUCT = {'name': 'p2', 'kind': 'member', 'column': 'product', 'table': 'sales', 'keeps_filters': True}
# Eight tiles on five pages: Overview names a tooltip page, and shows the region that Region
# Detail's drillthrough is bound to, which alone does not bring that page in (the person's
# pick decides, 2026-11-23); Product Drill is a drillthrough nothing on Overview opens.
USAGE = [unit('Overview', 'v1', columns=('Region',)), unit('Overview', 'v2', links=('Sales Tip',)),
         unit('Region Detail', 'd1', columns=('Region',), params=(REGION,)),
         unit('Sales Tip', 't1'), unit('Call Scoring', 'c1'), unit('Call Scoring', 'c2'),
         unit('Product Drill', 'p1', params=(PRODUCT,)), unit('Product Drill', 'p2', params=(PRODUCT,))]


def record(*pages, every=False):
    return {'contract_version': '1.0', 'pages': list(pages), 'all': every}


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies')
class Matching(unittest.TestCase):
    def test_picked_pages_keep_their_tiles_and_the_pages_those_tiles_name(self):
        kept, dropped, scope = page_scope.apply(USAGE, record('Overview'))
        self.assertEqual({u['container'] for u in kept}, {'Overview', 'Sales Tip'})
        self.assertEqual(scope['chosen'], ['Overview'])
        self.assertEqual({(s['page'], s['why']) for s in scope['supporting']}, {('Sales Tip', 'linked')})
        self.assertEqual({d['unit_id'].split('::')[2] for d in dropped}, {'Region Detail', 'Call Scoring', 'Product Drill'})
        self.assertTrue(all(d['reason'] == 'not_on_chosen_pages' and 'was not picked' in d['detail'] for d in dropped))
        self.assertEqual(sorted((x['page'], x['units']) for x in scope['left_out']),
                         [('Call Scoring', 2), ('Product Drill', 2), ('Region Detail', 1)])
        artifacts.validate({'contract_version': '1.0', 'vendor': 'powerbi', 'units': [], 'dropped': dropped,
                            'page_scope': scope}, 'question_units.schema.json')

    def test_every_page_changes_nothing(self):
        self.assertEqual(page_scope.apply(USAGE, None), (USAGE, [], None))
        kept, dropped, scope = page_scope.apply(USAGE, record(every=True))
        self.assertEqual((kept, dropped, scope['applied']), (USAGE, [], False))
        self.assertEqual(page_scope.digest(record(every=True)), page_scope.digest(None))

    def test_a_name_with_other_case_or_spacing_still_matches(self):
        for name in ('call scoring', '  CALL  SCORING ', 'Call_Scoring', "'Call Scoring'"):
            with self.subTest(name=name):
                kept, _dropped, scope = page_scope.apply(USAGE, record(name))
                self.assertEqual({u['container'] for u in kept}, {'Call Scoring'})
                self.assertEqual((scope['chosen'], scope['unmatched']), (['Call Scoring'], []))
        self.assertEqual(page_scope.digest(record('call scoring')), page_scope.digest(record('CALL_SCORING')))

    def test_a_pick_naming_no_page_filters_nothing_and_says_so(self):
        kept, dropped, scope = page_scope.apply(USAGE, record('Coverage'))
        self.assertEqual((kept, dropped, scope['applied']), (USAGE, [], False))
        self.assertIn("No page of this report is called 'Coverage'", scope['note'])
        self.assertIn('Overview', scope['note'])
        kept, _dropped, scope = page_scope.apply(USAGE, record('Call Scoring', 'Coverage'))
        self.assertEqual(len(kept), 2)
        self.assertEqual(scope['unmatched'], ['Coverage'])
        self.assertIn("No page is called 'Coverage'", scope['note'])

    def test_the_reader_names_tooltip_and_drillthrough_pages_but_not_navigation(self):
        names = {'tip_page': 'Sales Tip', 'drill': 'Region Detail', 'other': 'Call Scoring'}
        lit = lambda v: {'expr': {'Literal': {'Value': f"'{v}'"}}}  # noqa: E731
        visual = {'visual': {'visualContainerObjects': {
            'visualTooltip': [{'properties': {'type': lit('ReportPage'), 'section': lit('tip_page')}}],
            'visualLink': [{'properties': {'drillthroughSection': lit('drill'), 'navigationSection': lit('other')}}]}}}
        self.assertEqual(sorted(powerbi._page_links(visual, names)), ['Region Detail', 'Sales Tip'])
        self.assertEqual(powerbi._page_links({'section': lit('nowhere')}, names), [])


class FakeCtx:
    """What A2's body reads and writes, with each artifact held to its schema."""

    def __init__(self, run, facts):
        self.run, self.facts, self.out, self.counts, self.said = Path(run), facts, None, {}, []
        self.policy = policy.load(None)
        self.entry = argparse.Namespace(inputs=[])

    def read(self, name, schema=None):
        return {'run_inputs.json': {}, 'bi_facts.json': self.facts}[name]

    def write(self, name, data, schema):
        artifacts.validate(data, schema, what=name)
        self.out = data

    def count(self, **kw):
        self.counts.update(kw)

    def say(self, msg):
        self.said.append(msg)

    def note_input_path(self, path):
        self.entry.inputs.append(str(path))


FACTS = {'contract_version': '1.0', 'vendor': 'powerbi', 'source_file': 'model', 'model_name': 'Sales',
         'tables': [{'name': 'db.sch.sales', 'logical_name': 'sales', 'source_lineage': {'object': 'db.sch.sales'},
                     'columns': [{'name': n, 'source_column': n} for n in ('region', 'product', 'amount')]}],
         'measures': [{'name': 'Total Sales', 'formula': 'SUM(sales[amount])', 'formula_language': 'dax',
                       'home_table': 'sales'}],
         'relationships': [], 'usage': USAGE, 'assets': [], 'parameters': []}


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies')
class QuestionUnits(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name)

    def build(self):
        ctx = FakeCtx(self.run, json.loads(json.dumps(FACTS)))
        a2.body(ctx)
        return ctx

    def test_only_the_chosen_pages_and_their_supporting_pages_reach_generation(self):
        self.build()   # the report read whole first, as the fetch does before the pick
        self.assertTrue(any('Call_Scoring' in p.name for p in (self.run / 'fragments').iterdir()))
        artifacts.write(self.run, 'page_scope.json', record('overview'), 'page_scope.schema.json')
        ctx = self.build()
        self.assertEqual({u['unit_id'].split('::')[2] for u in ctx.out['units']},
                         {'Overview', 'Sales Tip'})
        self.assertEqual(ctx.out['page_scope']['chosen'], ['Overview'])
        self.assertEqual(sum(d['reason'] == 'not_on_chosen_pages' for d in ctx.out['dropped']), 5)
        self.assertEqual(ctx.counts['page_scope'], page_scope.digest(record('Overview')))
        self.assertEqual(ctx.counts['out_of_scope'], 5)
        self.assertTrue(any('left out' in s for s in ctx.said))
        # Only the kept units' fragments stay: a page picked away leaves none behind.
        self.assertFalse(any('Call_Scoring' in p.name for p in (self.run / 'fragments').iterdir()))

    def test_every_page_builds_every_tile_as_before(self):
        ctx = self.build()
        self.assertEqual(len(ctx.out['units']), len(USAGE))
        self.assertNotIn('page_scope', ctx.out)
        self.assertEqual((ctx.counts['page_scope'], 'out_of_scope' in ctx.counts), ('none', False))
        artifacts.write(self.run, 'page_scope.json', record(every=True), 'page_scope.schema.json')
        ctx = self.build()
        self.assertEqual(len(ctx.out['units']), len(USAGE))
        self.assertFalse(ctx.out['page_scope']['applied'])

    def test_a_pick_naming_no_page_builds_every_tile_and_records_why(self):
        artifacts.write(self.run, 'page_scope.json', record('Coverage'), 'page_scope.schema.json')
        ctx = self.build()
        self.assertEqual(len(ctx.out['units']), len(USAGE))
        self.assertIn("No page of this report is called 'Coverage'", ctx.out['page_scope']['note'])
        self.assertTrue(any("called 'Coverage'" in s for s in ctx.said))


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies')
class Rerun(unittest.TestCase):
    """The report is read before the pick: a new pick re-runs A2 and every step after it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name)
        stages = [{'stage': s, 'verdict': 'ok', 'started_at': '2026-11-23T21:09:45+00:00', 'notes': None,
                   'counts': {'page_scope': 'none'} if s == 'a2_question_units' else {}}
                  for s in ('a1_parse_bi', 'a2_question_units', 'a3_translate')]
        (self.run / 'manifest.json').write_text(json.dumps({'stages': stages}))
        self.steps = [runner.Step('a1_parse_bi', 'a1_parse_bi.py'), runner.Step('a2_question_units', 'a2_question_units.py'),
                      runner.Step('a3_translate', 'a3_translate.py')]

    def ran(self):
        seen = []

        def fake(cmd, env=None):
            seen.append(cmd[cmd.index('--as') + 1])
            return argparse.Namespace(returncode=0)
        args = argparse.Namespace(run=str(self.run), policy=None, answers_dir=None)
        runner.execute(self.steps, args, {}, runner=fake)
        return seen

    def test_nothing_reruns_without_a_pick_or_for_every_page(self):
        self.assertEqual(self.ran(), [])
        artifacts.write(self.run, 'page_scope.json', record(every=True), 'page_scope.schema.json')
        self.assertEqual(self.ran(), [])

    def test_a_pick_reruns_a2_and_what_follows(self):
        artifacts.write(self.run, 'page_scope.json', record('Overview'), 'page_scope.schema.json')
        self.assertEqual(self.ran(), ['a2_question_units', 'a3_translate'])


class Command(unittest.TestCase):
    """`ENGINE pages`: the record, and one advance when the build already read other pages."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root, self.run = base / 'runtime', base / 'run_1' / 'engine'

    def manifest(self, digest):
        self.run.mkdir(parents=True, exist_ok=True)
        (self.run / 'module.json').write_text('{}')
        (self.run / 'manifest.json').write_text(json.dumps({'stages': [
            {'stage': 'a2_question_units', 'verdict': 'ok', 'counts': {'page_scope': digest}}]}))

    def test_the_record_is_written_and_read_back_by_the_engine(self):
        doc = driver.record_pages(self.run, ['Coverage', 'Coverage', ' Sessions '])
        saved = json.loads((self.run / 'page_scope.json').read_text())
        self.assertEqual((saved['pages'], saved['all']), (['Coverage', 'Sessions'], False))
        self.assertEqual(doc['pages'], saved['pages'])
        if DEPENDENCIES:
            self.assertEqual(page_scope.read(self.run)['pages'], ['Coverage', 'Sessions'])
        self.assertTrue(json.loads(json.dumps(driver.record_pages(self.run, every=True)))['all'])
        for pages, every in (([], False), (['Coverage'], True)):
            with self.subTest(pages=pages, every=every), self.assertRaises(ValueError):
                driver.record_pages(self.run, pages, every)

    def test_before_the_build_reads_the_report_nothing_is_advanced(self):
        with mock.patch.object(driver, 'invoke') as invoke:
            result, code = driver.scope_pages(self.root, self.run, ['Coverage'], model='m')
        invoke.assert_not_called()
        self.assertEqual((result['status'], code), ('recorded', 0))

    def test_a_new_pick_after_the_fetch_advances_the_build_once(self):
        self.manifest('none')
        with mock.patch.object(driver, 'invoke', return_value=({'status': 'paused', 'gate': {'id': 'a3b'}}, 3)) as invoke:
            result, code = driver.scope_pages(self.root, self.run, ['Coverage'], model='m')
        invoke.assert_called_once_with(self.root, 'advance', ['--run', str(self.run), '--model', 'm'])
        self.assertEqual((result['status'], result['gate']['id'], result['pages'], code), ('paused', 'a3b', ['Coverage'], 3))

    def test_the_same_pick_again_or_every_page_on_an_unscoped_build_advances_nothing(self):
        self.manifest(driver._page_scope().digest({'pages': ['coverage'], 'all': False}))
        with mock.patch.object(driver, 'invoke') as invoke:
            self.assertEqual(driver.scope_pages(self.root, self.run, ['COVERAGE'], model='m')[0]['status'], 'recorded')
            self.manifest('none')
            self.assertEqual(driver.scope_pages(self.root, self.run, every=True, model='m')[0]['status'], 'recorded')
        invoke.assert_not_called()

    def test_the_command_line(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = driver.main(['--runtime', str(self.root), 'pages', '--run', str(self.run), '--page', 'Coverage'])
        self.assertEqual((code, json.loads(out.getvalue())['pages']), (0, ['Coverage']))
        self.assertTrue(os.path.isfile(self.run / 'page_scope.json'))


if __name__ == '__main__':
    unittest.main()
