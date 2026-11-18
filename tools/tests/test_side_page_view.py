"""The side page as the person sees it: the output the run made, never the process that makes it.

The page's own script renders a snapshot through `window.SidePage.view`, run here in Node with the browser stubbed out.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from analytics_run import SCRIPTS

PAGE = Path(SCRIPTS) / 'side_page.html'
DRIVER = r"""
const fs = require('node:fs'), vm = require('node:vm');
const [html, payload] = [fs.readFileSync(process.argv[1], 'utf8'), JSON.parse(fs.readFileSync(0, 'utf8'))];
const script = html.split(/<script[^>]*>/).pop().split('</script>')[0];
const el = () => ({innerHTML: '', textContent: '', dataset: {}, style: {}, removeAttribute() {}, setAttribute() {},
  querySelector: () => null, querySelectorAll: () => [], contains: () => false, focus() {}, scrollIntoView() {},
  getBoundingClientRect: () => ({top: 0}), classList: {add() {}, remove() {}}});
const noop = () => 0;
const window = {addEventListener: noop, matchMedia: () => ({matches: false, addEventListener: noop}), scrollTo: noop,
  scrollY: 0};
const sandbox = {window, document: {getElementById: el, addEventListener: noop, querySelector: () => null,
  documentElement: el(), activeElement: null}, location: {hash: '', pathname: '/', search: ''},
  history: {replaceState: noop}, localStorage: {getItem: () => null, setItem: noop}, setInterval: noop,
  setTimeout: noop, clearInterval: noop, clearTimeout: noop, fetch: () => new Promise(() => {}), console,
  URLSearchParams, Date, Math, JSON, Set, Map, Object, Array, String, Number, Promise};
sandbox.window.document = sandbox.document;
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
const ui = Object.assign({}, payload.ui || {}); ui.tog = new Set(ui.tog || []);
process.stdout.write(sandbox.window.SidePage.view(payload.snapshot, ui));
"""


def metric(name, stage, status=None, **kw):
    return dict({'name': name, 'stage': stage, 'status': status or ('draft' if stage == 'draft' else 'final'),
                 'description': name + ' in pounds.', 'sources': []}, **kw)


SNAPSHOT = {
    'flow': 'demo', 'firm': 'Fixit', 'title': 'Job profit agent', 'updated': '2026-11-18T10:00:00Z',
    'now': {'id': 'metrics', 'title': "Writing each metric's rule", 'busy': True, 'bar': 0.4,
            'count': {'n': 2, 'of': 5, 'unit': 'rules written'}},
    'steps': [{'id': 'metrics', 'status': 'now', 'name': 'Metrics'}],
    'build': [{'id': 'describe', 'name': 'Describe each query', 'state': 'done', 'result': '30 queries'},
              {'id': 'rules', 'name': "Write each metric's rule", 'state': 'now'}],
    'metrics': [metric('Gross margin', 'final', 'checked', value='£118,681', population='Jobs not cancelled', type='Sum',
                       leaves_out=['Cancelled jobs'], unit='GBP', grain='One job', formula='SUM(margin)',
                       dimensions=['Job', 'Month'], table='site.jobs', **{'from': ['Margin', 'Job margin']},
                       reasoning='Both drafts sum contract value less costs.',
                       sources=[{'id': 'h-1', 'kind': 'query', 'question': 'How many jobs did we run?', 'runs': 12},
                                {'kind': 'tile', 'visual': 'Gross margin', 'page': 'Site'}]),
                metric('Rework cost', 'final', 'look', why='Came back empty'),
                metric('Labour cost', 'final'),
                metric('Material waste', 'merged', 'merged'),
                metric('Crew utilisation', 'draft')],
    'drafts': [{'id': 'h-1#0', 'name': 'Job count', 'kind': 'query', 'question': 'How many jobs?'},
               {'id': 'h-2#0', 'name': 'Margin', 'kind': 'query', 'into': 'Gross margin'}],
    'queries': [{'id': 'h-1', 'name': 'How many jobs?', 'question': 'How many jobs did we run?', 'status': 'checked',
                 'kind': 'query', 'description': 'Counts jobs that were not cancelled.', 'sql': 'SELECT COUNT(*) FROM site.jobs',
                 'tables': ['site.jobs'], 'runs': 12, 'analysts': 2, 'metrics': ['Gross margin']}],
}


@unittest.skipUnless(shutil.which('node'), 'Node renders the page script')
class SidePageView(unittest.TestCase):
    def render(self, snapshot=SNAPSHOT, **ui):
        result = subprocess.run([shutil.which('node'), '-e', DRIVER, str(PAGE)], capture_output=True, text=True,
                                input=json.dumps({'snapshot': snapshot, 'ui': ui}), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def text(self, html):
        return ' '.join(re.sub(r'<[^>]+>', ' ', html).split())

    def test_the_page_shows_what_was_made_never_the_process(self):
        # Team review, 2026-11-18: progress stays in the chat; the page beside it holds only the output.
        for wide in (False, True):
            html = self.render(wide=wide)
            self.assertFalse('role="progressbar"' in html, 'no progress bar')
            words = self.text(html)
            for process in ("Describe each query", "Write each metric&#39;s rule", 'rules written', 'Step '):
                self.assertFalse(process in words, 'the build checklist stays in the chat: %s' % process)

    def test_every_metric_shows_one_of_five_plain_states(self):
        words = self.text(self.render())
        for name, state in (('Gross margin', 'Checked'), ('Rework cost', 'Failed'), ('Labour cost', 'Written'),
                            ('Material waste', 'Merged'), ('Crew utilisation', 'Draft')):
            self.assertRegex(words, re.escape(name) + r'.{0,60}?\b' + state + r'\b')
        for old in ('Final', 'Kept back', 'Writing rule'):
            self.assertNotIn(old, words)

    def test_a_draft_not_yet_merged_is_a_draft_metric_and_a_merged_one_is_inside_its_metric(self):
        html = self.render()
        words = self.text(html)
        self.assertRegex(words, r'Job count.{0,60}?\bDraft\b')
        self.assertFalse('>Drafts<' in html.replace(' ', ''), 'no separate drafts section')
        self.assertNotRegex(words, r'\bMargin\b.{0,30}\bMerged\b', 'a merged draft lives in its metric, not the list')

    def test_metrics_come_first_then_queries(self):
        words = self.text(self.render())
        self.assertLess(words.index('Gross margin'), words.index('How many jobs did we run?'))

    def test_a_metric_profile_reads_like_the_registry_s(self):
        # Metric Studio's own section names, so the plugin and the registry show one experience.
        words = self.text(self.render(metric='Gross margin'))
        order = ['Gross margin', 'Checked', 'Checked value', '£118,681', 'Made from', '1 dashboard tile and 1 past query',
                 'Kept', 'What it measures', 'Includes', 'Jobs not cancelled', 'Excludes', 'Cancelled jobs',
                 'How it&#39;s calculated', 'Adds up', 'Show the rule as written', 'How it can be split', 'Month',
                 'Where it came from', 'How many jobs did we run?']
        at = [words.find(x) for x in order]
        self.assertTrue(all(i >= 0 for i in at), [x for x, i in zip(order, at) if i < 0])
        self.assertEqual(at, sorted(at), 'in the order Studio shows them')

    def test_a_metric_profile_says_what_a_metric_is(self):
        # People do not know what a metric is: the profile says it in one plain line.
        self.assertIn('A metric is one agreed number', self.text(self.render(metric='Gross margin')))

    def test_a_query_profile_reads_like_the_registry_s_saved_query(self):
        words = self.text(self.render(query='h-1'))
        order = ['How many jobs did we run?', 'State', 'Checked', 'Comes from', 'A past query', 'Metrics made from it',
                 'Gross margin', 'Counts jobs that were not cancelled.', 'A query is', 'The query', 'SELECT COUNT(*)',
                 'Tables site.jobs', 'Usage', 'Run 12 times by 2 analysts']
        at = [words.find(x) for x in order]
        self.assertTrue(all(i >= 0 for i in at), [x for x, i in zip(order, at) if i < 0])
        self.assertEqual(at, sorted(at), 'in the order Studio shows a saved query')

    def test_a_grain_written_as_column_names_reads_as_words(self):
        # Live, Granularity showed "project_id, entry_type": a first-time reader cannot read column names.
        snapshot = dict(SNAPSHOT, metrics=[metric('Gross margin', 'final', grain='project_id, entry_type'),
                                           metric('Rework cost', 'final', grain='One job')])
        opened = lambda name: self.text(self.render(snapshot, metric=name, tog=['rule:' + name]))
        self.assertIn('Counted once per One row per project and entry type', opened('Gross margin'))
        self.assertIn('Counted once per One job', opened('Rework cost'))

    def test_a_checked_value_sits_beside_its_dashboard_tile_s_figure(self):
        # A first-time reader never saw the dashboard's own figure beside the agent's, nor what "Checked" means.
        tiled = lambda tile: dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0], tile=tile)])
        words = self.text(self.render(tiled({'name': 'Gross margin', 'value': '£118,681', 'same': True}), metric='Gross margin'))
        self.assertIn('Checked value £118,681 Same as the dashboard’s formula for its Gross margin tile', words)
        # A fresh reader of h8: "Checked only means it returned a number. Any number would pass that." It also means the
        # written rule matches its SQL (the engine refuses a rule that contradicts it), and a demo's data is the firm's.
        self.assertIn('Its written rule matches its SQL, and it ran on Fixit’s data and returned a number', words)
        words = self.text(self.render(tiled({'name': 'Gross margin', 'value': '£117,000', 'same': False}), metric='Gross margin'))
        self.assertIn('Checked value £118,681 The dashboard’s formula for its Gross margin tile gives £117,000', words)

    def test_a_profile_shows_the_rule_in_plain_words_and_folds_the_rule_as_written(self):
        # A first-time reader of a live run stopped at "Sum", "GBP", "Granularity", a formula, table names and the engine's
        # ledger wording, all in plain view. Analysts can still open the rule as the engine wrote it.
        long = ("A job ledger with up to three entries per job not cancelled: the job's contract value as a positive "
                "'contract' entry and its labour cost as a negative 'labour' entry.")
        snapshot = dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0], population=long, grain='project_id, entry_type')])
        words = self.text(self.render(snapshot, metric='Gross margin'))
        self.assertIn('Metric · A total, in pounds (£)', words)
        self.assertIn('Adds up every row it counts.', words)
        for engine in ('Sum ·', 'GBP', 'Granularity', 'SUM(margin)', 'site.jobs', '&#39;contract&#39; entry', 'Counted once per'):
            self.assertNotIn(engine, words)
        self.assertIn('Excludes Cancelled jobs', words, 'what it leaves out reads as words, so it stays in view')
        opened = self.text(self.render(snapshot, metric='Gross margin', tog=['rule:Gross margin']))
        for engine in ('Hide the rule as written', 'Formula SUM(margin)', 'Reads site.jobs', '&#39;contract&#39; entry',
                       'Counted once per One row per project and entry type'):
            self.assertIn(engine, opened)

    def test_a_short_plain_population_stays_in_view(self):
        words = self.text(self.render(metric='Gross margin'))
        self.assertIn('Includes Jobs not cancelled', words)


    def test_the_list_names_each_kind_of_number_in_words(self):
        # Live, rows carried "Sum", "Count" and "Rate": the engine's aggregation names.
        snapshot = dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0], type='Sum'), metric('Late jobs', 'final', type='Count'),
                                           metric('Waste rate', 'final', type='Rate')])
        words = self.text(self.render(snapshot))
        for said in ('Gross margin Total', 'Late jobs Count', 'Waste rate Share'):
            self.assertIn(said, words)
        self.assertNotIn(' Sum ', words + ' ')
        self.assertNotIn(' Rate ', words + ' ')
