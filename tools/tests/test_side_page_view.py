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
            for process in ("Describe each query", "Define each metric", 'rules written', 'Step '):
                self.assertFalse(process in words, 'the build checklist stays in the chat: %s' % process)

    def test_every_metric_shows_one_of_four_plain_states(self):
        # The person, 2026-11-18: draft, merged, written and checked were the build's words. Combining is something
        # that happens to found metrics, not a place they sit, so a merged metric is still Found until it is defined.
        words = self.text(self.render())
        for name, state in (('Gross margin', 'Tested'), ('Rework cost', 'Needs a look'), ('Labour cost', 'Defined'),
                            ('Material waste', 'Found'), ('Crew utilisation', 'Found')):
            self.assertRegex(words, re.escape(name) + r'.{0,80}?\b' + state + r'\b')
        for old in ('Final', 'Kept back', 'Writing rule', 'Draft', 'Merged', 'Written', 'Checked', 'Failed'):
            self.assertNotRegex(words, r'\b%s\b' % old)

    def test_found_and_defined_rows_show_what_each_state_has(self):
        # The person, 2026-11-18: "if you found something, why can't it be defined?" A found row says where it was
        # found; a defined one says how it is worked out; the line over the list says the three in order.
        snap = dict(SNAPSHOT, metrics=[metric('Rework cost', 'merged', 'merged', sources=[{'id': 'h-1', 'kind': 'query'},
                                                                                      {'id': 'bi-1', 'kind': 'tile'}]),
                                       metric('Late jobs', 'final', description='Jobs handed over late. Counted once.')],
                    drafts=[], build=[{'id': 'merge', 'state': 'done'}])
        words = self.text(self.render(snap))
        self.assertIn('Rework cost Found in 1 dashboard tile and 1 past query Found', words)
        self.assertIn('Late jobs Jobs handed over late Defined', words)
        # A long definition still gives its row a line: its gist, before the colon, clipped to one line (h8: half the
        # rows had none, their first sentences being over 160 characters).
        long = 'What rework has cost in crew labour: hours logged as rework times each crew member’s hourly rate, ' * 3
        snap = dict(snap, metrics=[metric('Rework cost', 'final', description=long)])
        self.assertIn('Rework cost What rework has cost in crew labour Defined', self.text(self.render(snap)))
        self.assertIn('Each metric is found in your sources, then defined: exactly how it’s worked out, then tested on '
                      'Fixit’s data.', words)

    def test_a_draft_not_yet_merged_is_a_draft_metric_and_a_merged_one_is_inside_its_metric(self):
        html = self.render()
        words = self.text(html)
        self.assertRegex(words, r'Job count.{0,60}?\bFound\b')
        self.assertFalse('>Drafts<' in html.replace(' ', ''), 'no separate drafts section')
        self.assertNotRegex(words, r'\bMargin\b.{0,30}\bCombined\b', 'a merged draft lives in its metric, not the list')

    def test_metrics_come_first_then_queries(self):
        # The person's pick, 2026-11-18: one switch between the lists, metrics first and shown first.
        html = self.render()
        self.assertLess(html.index('data-v="metrics"'), html.index('data-v="queries"'))
        self.assertIn('Gross margin', self.text(html))

    def test_a_metric_profile_reads_like_the_registry_s(self):
        # Metric Studio's own profile, in one scroll (the person's pick of two drawn from Studio rendered on its mock,
        # 2026-11-18): title and meta line, three facts, then its panel cards in Studio's order and words.
        words = self.text(self.render(metric='Gross margin', tog=['p:calc', 'p:split', 'p:from']))
        order = ['Gross margin', 'A total, in pounds (£)', 'State', 'Tested', 'Tested value', '£118,681', 'Made from',
                 '1 dashboard tile and 1 past query', 'What it measures', 'Includes', 'Jobs not cancelled', 'Granularity',
                 'One job', 'Excludes', 'Cancelled jobs', 'How it&#39;s calculated', 'Computes', 'gross_margin', 'Adds up',
                 'Reads from', 'site.jobs', 'Show the definition as written', 'How it can be split', 'Month',
                 'Where it came from', 'How many jobs did we run?']
        at = [words.find(x) for x in order]
        self.assertTrue(all(i >= 0 for i in at), [x for x, i in zip(order, at) if i < 0])
        self.assertEqual(at, sorted(at), 'in the order Studio shows them')

    def test_a_profile_is_studio_s_frame_in_one_scroll(self):
        # Metric Studio's profile frame: a title, a meta line, three facts in a
        # soft well, then panel cards that open and close in place; no tabs and no rail in a narrow pane.
        snap = dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0], related=[['Builds on', 'Labour cost']],
                                            example_sql='SELECT SUM(margin) FROM site.jobs')] + SNAPSHOT['metrics'][1:])
        html = self.render(snap, metric='Gross margin')
        self.assertIn('<h1 class="ptitle">Gross margin</h1>', html)
        self.assertEqual(html.count('class="pcell"'), 3)
        # The person, 2026-11-18: only "What it measures" opens by default; the rest open when wanted.
        self.assertIn('data-tog="p:measures" data-k="t:p:measures" aria-expanded="true"', html)
        for closed in ('calc', 'split', 'from', 'related', 'details'):
            self.assertIn('data-tog="p:%s" data-k="t:p:%s" aria-expanded="false"' % (closed, closed), html)
        self.assertNotIn('Builds on', self.text(html))
        self.assertIn('Builds on Labour cost', self.text(self.render(snap, metric='Gross margin', tog=['p:related'])))
        closed = self.text(self.render(snap, metric='Gross margin', tog=['p:measures']))
        self.assertNotIn('Jobs not cancelled', closed, 'a card closes in place')
        # The formula card shows its outline, or the SQL behind it, as Studio's does.
        self.assertIn('data-tog="sqlv:Gross margin"', self.render(snap, metric='Gross margin', tog=['p:calc']))
        self.assertNotIn('SELECT SUM(margin)', self.text(html))
        sql = self.text(self.render(snap, metric='Gross margin', tog=['p:calc', 'sqlv:Gross margin']))
        self.assertIn('SELECT SUM(margin) FROM site.jobs', sql)
        self.assertNotIn('Computes', sql)

    def test_a_profile_lets_the_person_inspect_what_it_names(self):
        # The person, 2026-11-18: "Made from: 2 dashboard tiles and 3 past queries" could not be inspected. It is a link
        # that opens "Where it came from" and goes there; Computes shows its formula on a click; the metric's own name
        # and where it is kept sit in Details, with a copy button, as Studio's details card.
        html = self.render(metric='Gross margin')
        self.assertIn('data-act="reveal" data-v="p:from" data-k="reveal:from">1 dashboard tile and 1 past query</button>', html)
        calc = self.render(metric='Gross margin', tog=['p:calc'])
        self.assertIn('data-tog="fx:Gross margin"', calc)
        self.assertNotIn('SUM(margin)', self.text(calc))
        self.assertIn('SUM(margin)', self.text(self.render(metric='Gross margin', tog=['p:calc', 'fx:Gross margin'])))
        details = self.render(metric='Gross margin', tog=['p:details'])
        self.assertIn('Name gross_margin', self.text(details))
        self.assertIn('data-act="copy" data-v="gross_margin"', details)
        self.assertIn('Kept On this machine', self.text(details))

    def test_a_profile_s_text_keeps_to_three_sizes(self):
        # The person, 2026-11-18: the description was too big and the sizes disoriented. Body text is 14 px, labels and
        # details 12 px, the title 20 px.
        page = PAGE.read_text(encoding='utf-8')
        self.assertIn('.pdsc { font-size: 14px; line-height: 22px;', page)
        self.assertIn('.ptitle { font-size: 20px; line-height: 28px;', page)
        sizes = set(re.findall(r'font-size: (\d+)px', page.split('/* Metric Studio\'s list row and profile', 1)[1]))
        self.assertLessEqual(sizes, {'12', '14', '20', '11', '10'}, sizes)

    def test_a_query_marked_for_a_read_says_so_and_why(self):
        # The engine marks a query worth a person's read, with notes on what it does: its row says so, and its profile
        # opens on the notes, so the chat's count and the page agree.
        q = dict(SNAPSHOT['queries'][0], review='read', notes=['Returns blank rather than 0 when no job is late.'])
        snap = dict(SNAPSHOT, queries=[q])
        self.assertIn('Past query, run 12 times · worth a read', self.text(self.render(snap, list='queries')))
        html = self.render(snap, query='h-1')
        self.assertIn('data-tog="p:notes" data-k="t:p:notes" aria-expanded="true"', html)
        self.assertIn('Worth knowing Marked worth a read before you rely on it. Returns blank rather than 0 when no job is '
                      'late.', self.text(html))
        plain = self.render(query='h-1')
        self.assertNotIn('Worth knowing', self.text(plain), 'a query with nothing to know has no such card')

    def test_the_way_back_names_where_it_goes(self):
        # Back from a query opened from a metric returns to that metric, so the breadcrumb names it, not the list.
        self.assertIn('Saved queries / How many jobs did we run?', self.text(self.render(query='h-1')))
        self.assertIn('Gross margin / How many jobs did we run?', self.text(self.render(query='h-1', **{'from': 'Gross margin'})))

    def test_a_profile_moves_as_studio_s_does(self):
        # Metric Studio's motion: a profile's content slides in 16 px and fades over 240 ms; a card opens over 200 ms;
        # none of it under reduced motion.
        page = PAGE.read_text(encoding='utf-8')
        self.assertRegex(page, r'@keyframes pslide \{ from \{ opacity: 0; transform: translateX\(16px\); \} \}')
        self.assertIn('.prof.enter { animation: pslide 240ms cubic-bezier(0.2, 0, 0, 1); }', page)
        # A card opens and closes over 200 ms, its height and its chevron, as Studio's panel; a link that goes somewhere
        # on the page scrolls there and marks it (the smaller of 700 ms and 280 ms plus 0.2 ms a pixel).
        self.assertIn("const PANEL = {duration: 200, easing: 'cubic-bezier(0.4, 0, 0.2, 1)'};", page)
        self.assertIn('Math.min(700, 280 + 0.2 * Math.abs(', page)
        self.assertIn('.landed { animation: land 1.8s ease-out; }', page)
        self.assertRegex(page, r'prefers-reduced-motion: reduce\)[^}]*\.prof\.enter, \.landed \{ animation: none; \}')

    def test_a_metric_profile_says_what_a_metric_is(self):
        # People do not know what a metric is: the profile says it in one plain line.
        self.assertIn('A metric is one agreed number', self.text(self.render(metric='Gross margin')))

    def test_a_query_profile_reads_like_the_registry_s_saved_query(self):
        words = self.text(self.render(query='h-1', tog=['p:qmetrics', 'p:usage', 'p:cols']))
        order = ['How many jobs did we run?', 'Past query', 'State', 'Tested', 'Comes from', 'A past query',
                 'Linked metrics', '1 metric made from it', 'Counts jobs that were not cancelled.', 'A query is', 'The SQL',
                 'SELECT COUNT(*)', 'Metrics made from it', 'This query', 'Made from this query', 'Usage',
                 'Run 12 times by 2 analysts', 'Columns, tables and filters', 'Tables site.jobs']
        at = [words.find(x) for x in order]
        self.assertTrue(all(i >= 0 for i in at), [x for x, i in zip(order, at) if i < 0])
        self.assertEqual(at, sorted(at), 'in the order Studio shows a saved query')

    def test_a_grain_written_as_column_names_reads_as_words(self):
        # Live, Granularity showed "project_id, entry_type": a first-time reader cannot read column names.
        snapshot = dict(SNAPSHOT, metrics=[metric('Gross margin', 'final', grain='project_id, entry_type'),
                                           metric('Rework cost', 'final', grain='One job')])
        shown = lambda name: self.text(self.render(snapshot, metric=name))
        self.assertIn('Granularity One row per project and entry type', shown('Gross margin'))
        self.assertIn('Granularity One job', shown('Rework cost'))

    def test_a_checked_value_sits_beside_its_dashboard_tile_s_figure(self):
        # A first-time reader never saw the dashboard's own figure beside the agent's, nor what "Checked" means.
        tiled = lambda tile: dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0], tile=tile)])
        words = self.text(self.render(tiled({'name': 'Gross margin', 'value': '£118,681', 'same': True}), metric='Gross margin'))
        self.assertIn('Tested value £118,681 Same as the dashboard’s formula for its Gross margin tile', words)
        # A fresh reader of h8: "Checked only means it returned a number. Any number would pass that." It also means the
        # written rule matches its SQL (the engine refuses a rule that contradicts it), and a demo's data is the firm's.
        self.assertIn('Tested on Fixit’s data: it returned a number, and its definition matches its SQL', words)
        words = self.text(self.render(tiled({'name': 'Gross margin', 'value': '£117,000', 'same': False}), metric='Gross margin'))
        self.assertIn('Tested value £118,681 The dashboard’s formula for its Gross margin tile gives £117,000', words)

    def test_a_profile_shows_the_rule_in_plain_words_and_folds_the_rule_as_written(self):
        # A first-time reader of a live run stopped at "Sum", "GBP", "Granularity", a formula, table names and the engine's
        # ledger wording, all in plain view. Analysts can still open the rule as the engine wrote it.
        long = ("A job ledger with up to three entries per job not cancelled: the job's contract value as a positive "
                "'contract' entry and its labour cost as a negative 'labour' entry.")
        snapshot = dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0], population=long, grain='project_id, entry_type')])
        words = self.text(self.render(snapshot, metric='Gross margin'))
        self.assertIn('Metric · A total, in pounds (£)', words)
        self.assertIn('Adds up every row it counts.', self.text(self.render(snapshot, metric='Gross margin', tog=['p:calc'])))
        # Studio's frame (the person's pick) names the granularity in words and the tables a metric reads, as Studio does.
        for engine in ('Sum ·', 'GBP', 'SUM(margin)', '&#39;contract&#39; entry', 'Counted once per'):
            self.assertNotIn(engine, words)
        self.assertIn('Excludes Cancelled jobs', words, 'what it leaves out reads as words, so it stays in view')
        opened = self.text(self.render(snapshot, metric='Gross margin', tog=['p:calc', 'rule:Gross margin']))
        for engine in ('Hide the definition as written', 'Formula SUM(margin)', '&#39;contract&#39; entry'):
            self.assertIn(engine, opened)

    def test_a_short_plain_population_stays_in_view(self):
        words = self.text(self.render(metric='Gross margin'))
        self.assertIn('Includes Jobs not cancelled', words)


    def test_the_list_names_each_kind_of_number_in_words(self):
        # Live, rows carried "Sum", "Count" and "Rate": the engine's aggregation names.
        snapshot = dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0], type='Sum'), metric('Late jobs', 'final', type='Count'),
                                           metric('Waste rate', 'final', type='Rate')])
        words = self.text(self.render(snapshot))
        # Each row's second line (how it is worked out) sits between its name and its kind.
        for name, said in (('Gross margin', 'Total'), ('Late jobs', 'Count'), ('Waste rate', 'Share')):
            self.assertRegex(words, re.escape(name) + r' (?:[^.]*? )?' + said + r'\b')
        self.assertNotIn(' Sum ', words + ' ')
        self.assertNotIn(' Rate ', words + ' ')

    def test_tabs_filter_by_state_and_keep_their_place_as_counts_change(self):
        # The person, 2026-11-18: tabs to filter through the states. A tab that appears and vanishes as states fill
        # would move the others, so all four stay, each with its count; an empty one cannot be picked.
        snapshot = dict(SNAPSHOT, metrics=[dict(SNAPSHOT['metrics'][0])], drafts=[])
        html = self.render(snapshot)
        for tab in ('All 1', 'Found 0', 'Defined 0', 'Tested 1', 'Needs a look 0'):
            label, n = tab.rsplit(' ', 1)
            self.assertIn('>%s <span class="n num">%s</span></button>' % (label, n), html)
        self.assertRegex(html, r'data-v="draft"[^>]*disabled')
        self.assertNotRegex(html, r'data-v="checked"[^>]*disabled')

    def test_lists_read_like_metric_studio_s(self):
        # The person, 2026-11-18: the page should look like Metric Studio's lists, on the latest design system. Studio
        # draws rows on the page with a hairline between them and no icon, the name then a muted line; state pills with
        # their count apart; the status badge with its tone's glyph (a tick for success, a triangle for a warning); a
        # saved query's question on two lines.
        snap = dict(SNAPSHOT, queries=SNAPSHOT['queries'] + [
            {'id': 'bi-1', 'name': 'Margin tile', 'question': 'What is the margin?', 'kind': 'tile', 'status': 'drafted',
             'visual': 'Gross margin', 'page': 'Site'}])
        html = self.render(snap) + self.render(snap, list='queries')
        self.assertIn('>Found <span class="n num">3</span></button>', html)
        self.assertNotIn('class="card lst"', html, 'a list sits on the page, as Studio\'s does')
        self.assertIn('data-s="checked"><span class="mm">', html, 'a metric row leads with its name, no icon')
        # The person, 2026-11-18: with the kind under the name and the value at the far right the row read disoriented;
        # the kind, the value and the state sit together on the right, the state as the design system's badge.
        self.assertRegex(html, r'<span class="badge ok"><svg[^>]*>.*?</svg>Tested</span>')
        self.assertRegex(html, r'<span class="badge warn"><svg[^>]*>.*?</svg>Needs a look</span>')
        self.assertIn('<span class="badge">Found</span>', html, 'a state that is not a status draws no glyph')
        self.assertRegex(self.text(html), r'Gross margin Gross margin in pounds Total £118,681 Tested')
        self.assertIn('<span class="nm cl2">How many jobs did we run?</span>', html)
        words = self.text(html)
        self.assertIn('How many jobs did we run? Past query, run 12 times', words)
        self.assertIn('What is the margin? Dashboard tile “Gross margin” on Site', words)

    def test_one_switch_shows_one_list_at_a_time(self):
        # The person's pick of two layouts, 2026-11-18: metrics and queries behind one switch, as Studio keeps them on
        # separate pages, so a long query list never pushes the metrics off the page.
        html = self.render()
        self.assertIn('data-act="list" data-v="metrics" data-k="list:metrics" aria-pressed="true">Metrics <span class="n num">6</span>', html)
        self.assertIn('data-act="list" data-v="queries" data-k="list:queries" aria-pressed="false">Queries <span class="n num">1</span>', html)
        words = self.text(html)
        self.assertIn('Crew utilisation', words)
        self.assertNotIn('How many jobs did we run?', words)
        words = self.text(self.render(list='queries'))
        self.assertIn('How many jobs did we run?', words)
        self.assertNotIn('Crew utilisation', words)

    def test_rows_the_current_step_works_on_say_so_before_they_change(self):
        # The person, 2026-11-18: rows changed under them with nothing to say a change was coming. While a step runs,
        # the rows it is working on carry a quiet pending line.
        merged = dict(SNAPSHOT, metrics=[metric('Rework cost', 'merged', 'merged'), metric('Late jobs', 'final')], drafts=[],
                      build=[{'id': 'merge', 'state': 'done'}, {'id': 'rules', 'state': 'now'}])
        words = self.text(self.render(merged))
        self.assertRegex(words, r'Rework cost.{0,40}being defined')
        self.assertNotRegex(words, r'Late jobs.{0,40}being defined', 'already defined')
        drafting = dict(SNAPSHOT, metrics=[metric('Crew utilisation', 'draft')], drafts=[],
                        build=[{'id': 'merge', 'state': 'now'}])
        self.assertRegex(self.text(self.render(drafting)), r'Crew utilisation.{0,40}being combined')
        # Found covers combined metrics too: one already combined is not pending while the step goes on (h8 at 13 min).
        combining = dict(drafting, metrics=[metric('Crew utilisation', 'draft'), metric('Rework cost', 'merged', 'merged')])
        self.assertNotRegex(self.text(self.render(combining)), r'Rework cost.{0,40}being combined')
        done = dict(merged, build=[{'id': 'merge', 'state': 'done'}, {'id': 'rules', 'state': 'done'}])
        self.assertNotIn('being defined', self.text(self.render(done)), 'nothing pending once the step is over')

    def test_queries_keep_one_order_and_show_their_state_like_metrics(self):
        # The person, 2026-11-18: queries jitter as metrics did, with long questions making every jump worse. Before
        # the checks they come from the engine's list, after from the checked context, in another order: the page
        # sorts them the same way every time (dashboard tiles first, then past queries most run first).
        q = lambda i, kind, runs, status, question: {'id': i, 'name': question, 'question': question, 'kind': kind,
                                                     'runs': runs, 'status': status}
        scrambled = [q('h-2', 'query', 3, 'drafted', 'How many snags?'), q('bi-1', 'tile', 0, 'checked', 'What is the margin?'),
                     q('h-1', 'query', 12, 'look', 'How many jobs did we run?')]
        html = self.render(dict(SNAPSHOT, queries=scrambled), list='queries')
        words = self.text(html)
        order = [words.index(x) for x in ('What is the margin?', 'How many jobs did we run?', 'How many snags?')]
        self.assertEqual(order, sorted(order))
        self.assertIn('data-f="q:h-1"', html)
        self.assertRegex(words, r'What is the margin\?.{0,50}Tested')
        self.assertRegex(words, r'How many jobs did we run\?.{0,50}Needs a look')
        self.assertRegex(words, r'How many snags\?.{0,50}Found')
        for tab in ('All 3', 'Found 1', 'Tested 1', 'Needs a look 1'):
            label, n = tab.rsplit(' ', 1)
            self.assertIn('>%s <span class="n num">%s</span></button>' % (label, n), html)
        checking = self.text(self.render(dict(SNAPSHOT, queries=scrambled, build=[{'id': 'check', 'state': 'now'}]), list='queries'))
        self.assertRegex(checking, r'How many snags\?.{0,30}being tested')
