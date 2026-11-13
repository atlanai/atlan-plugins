"""The helpers behind the setup survey and the fixes the entry flows rely on: a folder's
sources listed without a run, a Snowflake connector result read as saved, and business labels
told apart from people's names."""
import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import context_contracts as contracts  # noqa: E402
from analytics_eval import full_name_hit  # noqa: E402
from source_inventory import summarize  # noqa: E402


class DiscoverTest(unittest.TestCase):
    def test_groups_sources_in_build_order_and_reads_no_contents(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'reports').mkdir()
            (root / 'reports' / 'sales.pbix').write_bytes(b'not a real report')
            (root / 'query_history.jsonl').write_text('{"sql": "select 1"}\n')
            (root / 'orders.csv').write_text('id\n1\n')
            (root / '.env').write_text('TOKEN=x')
            (root / 'secrets').mkdir()
            (root / 'secrets' / 'model.bim').write_text('{}')
            before = sorted(p.name for p in root.rglob('*'))
            out = summarize(root)
            self.assertEqual(list(out['sources']), ['bi', 'history', 'data'])
            self.assertEqual(out['counts'], {'bi': 1, 'history': 1, 'data': 1})
            self.assertEqual(out['sources']['bi'][0]['path'], 'reports/sales.pbix')
            self.assertFalse(out['created'] or out['remote_writes'])
            self.assertEqual(before, sorted(p.name for p in root.rglob('*')))

    def test_caps_each_group_and_says_how_many_it_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            for n in range(15):
                (Path(d) / f'q{n}.sql').write_text('select 1')
            out = summarize(d, per_role=12)
            self.assertEqual((len(out['sources']['history']), out['omitted']['history']), (12, 3))

    def test_the_demo_folders_are_never_part_of_a_setup(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for folder in ('atlan-analytics-demo/yard/powerbi', 'analytics-agent-accelerator/box-office', 'reports'):
                (root / folder).mkdir(parents=True)
            (root / 'atlan-analytics-demo/yard/powerbi/model.bim').write_text('{}')
            (root / 'analytics-agent-accelerator/box-office/pulse.twb').write_text('<workbook/>')
            (root / 'reports/sales.bim').write_text('{}')
            out = summarize(root)
            self.assertEqual([s['path'] for s in out['sources']['bi']], ['reports/sales.bim'])
            self.assertEqual(out['demo_files_skipped'], 2)

    def test_cli_needs_no_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = str(Path(tmp).resolve())  # the helper refuses a path that runs through a symlink
            (Path(d) / 'model.bim').write_text('{}')
            run = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), 'discover', '--workspace', d],
                                 capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(run.stdout)['counts']['bi'], 1)
            self.assertEqual([p.name for p in Path(d).iterdir()], ['model.bim'])


class SnowflakeResultTest(unittest.TestCase):
    def test_connector_result_becomes_history_without_copying(self):
        payload = {'query_id': 'q', 'result_set': {
            'data': [['select a from t', '7', '3'], ['select b from t', '2', '1']],
            'resultSetMetaData': {'rowType': [{'name': 'SQL'}, {'name': 'EXEC'}, {'name': 'USERS'}]}}}
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / 'saved.json', Path(d) / 'querylog.jsonl'
            src.write_text(json.dumps([{'type': 'text', 'text': json.dumps(payload)}]))
            res = contracts.cmd_querylog(argparse.Namespace(src=[str(src)], dst=str(dst), append=False, limit=5000))
            self.assertEqual(res['statements'], 2)
            self.assertEqual(json.loads(dst.read_text().splitlines()[0]), {'sql': 'select a from t', 'exec': 7, 'users': 3})


class LabelOrNameTest(unittest.TestCase):
    LABELS = ('UPPER(RTRIM("Engagement Status")) = \'Actively Worked\'',
              "coverage_tier in ('Well Covered', 'Light Coverage')",
              "CASE WHEN s >= 4 THEN 'Well Covered' ELSE 'No Coverage' END AS coverage_tier",
              "CASE WHEN on_track THEN 'On Pace' END", "call_class <> 'No Transcript'", "x in ('Very Low')",
              "where stage = 'Closed Won'",
              "LINE_OF_BUSINESS NOT IN ('Variable Spend')",
              "NOT (t.LINE_OF_BUSINESS IN ('Fixed Cost', 'Variable Spend'))",
              "\"Line of Business\" <> 'Variable Spend'", "t.LOB = 'Variable Spend'",
              "SALES_FINANCE_CATEGORY NOT IN ('Variable Spend')", "ACTIVITY_CLASS IN ('New Business')",
              "c.CATEGORY_NAME NOT IN ('Gift Cards')", "REGION_DESC = 'North East'",
              # A Tableau calculated field, compiled: labels written from a category test.
              "WHERE ((CASE WHEN t.CHANNEL = 'ONLINE' THEN 'Variable Spend' ELSE 'Fixed Spend' END)) "
              "IN ('Variable Spend')",
              "CASE WHEN UPPER(t.LOB) IN ('VS', 'VAR') THEN 'Variable Spend' END AS lob_group")
    NAMES = ("where rep = 'Jane Smith'", "status = 'Open' and owner_name = 'Jane Smith'",
             "CASE WHEN id = 4 THEN 'Jane Smith' END AS owner_name", "CASE WHEN id = 4 THEN 'Jane Smith' END",
             "tier in ('Gold Plus') and manager like 'Mary Jones%'",
             "line_of_business = 'Fleet' and business_owner = 'Jane Smith'", "region_manager = 'Jane Smith'",
             "CASE WHEN t.OWNER_ID = 4 THEN 'Jane Smith' ELSE 'Other Rep' END IN ('Jane Smith')",
             "CASE WHEN t.CHANNEL = 'ONLINE' THEN 'Jane Smith' END AS owner_name",
             "CASE WHEN t.CHANNEL = 'ONLINE' AND t.REP_ID = 7 THEN 'Jane Smith' END",
             "CASE WHEN t.CHANNEL = 'ONLINE' THEN 'Fixed Spend' END = 'X' and rep = 'Jane Smith'")

    def test_reviewed_values_pass_and_nothing_else(self):
        from analytics_eval import set_reviewed_values
        sql = "t.SPEND_BUCKET_OWNER = 'Variable Spend' and rep = 'Jane Smith'"
        try:
            set_reviewed_values(['Variable Spend'])
            self.assertEqual(full_name_hit(sql), "'Jane Smith'")
            self.assertIsNone(full_name_hit("t.SPEND_OWNER = 'Variable Spend'"))
        finally:
            set_reviewed_values([])
        self.assertEqual(full_name_hit("t.SPEND_OWNER = 'Variable Spend'"), "'Variable Spend'")

    def test_business_labels_pass(self):
        for sql in self.LABELS:
            with self.subTest(sql=sql):
                self.assertIsNone(full_name_hit(sql))

    def test_names_beside_people_columns_are_still_refused(self):
        for sql in self.NAMES:
            with self.subTest(sql=sql):
                self.assertIsNotNone(full_name_hit(sql))


class PrivacyHoldTest(unittest.TestCase):
    def test_a_query_with_a_name_is_held_without_repeating_it_and_the_rest_ship(self):
        import importlib.util
        module = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics'
        sys.path[:0] = [str(module.parents[1]), str(module)]  # export imports its sibling driver by name
        spec = importlib.util.spec_from_file_location('entry_hold_export', module / 'export.py')
        export = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(export)
        privacy_holds = export.privacy_holds

        def check(body):
            return ((["definition.sql holds a person's name ('Jane Smith')"] if body['name'] == 'named' else
                     ['definition.sql: unknown column'] if body['name'] == 'broken' else []), None)
        kept, private = privacy_holds([{'name': 'ok'}, {'name': 'named'}, {'name': 'broken'}], check)
        self.assertEqual([b['name'] for b in kept], ['ok', 'broken'])  # a non-privacy error still refuses the export
        self.assertEqual([(b['name'], d) for b, d in private], [('named', "definition.sql holds a person's name")])
        self.assertNotIn('Jane', private[0][1])


class ReportSqlKeepsItsNamesTest(unittest.TestCase):
    def errors(self, lane, sql):
        body = {'name': 'q', 'display_name': 'Q', 'definition': {'spec_version': contracts.QUERY_SPEC, 'question': 'How many calls?',
                                                                 'sql': sql, 'dialect': 'snowflake',
                                            'extensions': {'metrics_agent': {'evidence': {'provenance': {'lane': lane}}}}}}
        return [e for e in contracts.check_query(body)[0] if ' holds ' in e]

    def test_a_name_in_report_sql_ships_and_the_same_name_from_history_does_not(self):
        sql = "select count(*) from calls where rep_name not in ('Jane Smith')"
        self.assertEqual(self.errors('bi', sql), [])
        self.assertEqual(len(self.errors('history', sql)), 1)
        self.assertIn("a person's name", self.errors('history', sql)[0])

    def test_an_email_in_report_sql_is_still_refused(self):
        self.assertIn('an email address', self.errors('bi', "select 1 from t where owner = 'jane@example.com'")[0])


if __name__ == '__main__':
    unittest.main()
