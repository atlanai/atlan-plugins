"""Native-chat controls across restarts and a complete local first-answer rehearsal."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_analytics_experience as fixtures
from project import Project
import experience
import progress


class ConversationChoices(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalyticsExperience()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.body = self.fixture.candidate()
        state = self.fixture.run.manifest()
        state['entrypoint'] = 'project'
        self.fixture.write('run.json', state)
        self.run = Project(self.fixture.run.root)

    def test_selection_pause_details_keep_local_and_resume_survive_restart(self):
        before = self.fixture.files()
        self.run.journey('select', 'metric-source')
        self.run.journey('pause')
        restarted = Project(self.run.root)
        self.assertEqual(restarted.journey()['continuation']['action'], 'resume')
        self.assertEqual(restarted.journey('details')['interaction']['mode'], 'paused')
        self.assertIn('SELECT SUM', restarted.journey('details')['chat'])
        self.assertNotIn('Resume here', restarted.journey('details')['chat'])
        restarted.journey('keep-local')
        self.assertEqual(Project(self.run.root).journey()['interaction']['mode'], 'local')
        restarted.journey('resume')
        self.assertEqual(restarted.journey()['continuation']['action'], 'build')
        self.assertEqual(restarted.experience(format='json')['focus'], 'metric:shipped-units')
        after = self.fixture.files()
        self.assertEqual(set(after) - set(before), {'interaction.json'})
        self.assertEqual({k: after[k] for k in before}, before)

    def test_missing_focus_survives_scope_change_without_substitution(self):
        self.run.journey('select', 'metric-source')
        self.fixture.write('context/metrics.json', [{**self.body, 'name': 'replacement', 'source_id': 'new'}])
        model = Project(self.run.root).experience(format='json')
        self.assertIsNone(model['sample'])
        self.assertEqual(model['continuation']['action'], 'repair')
        before = self.fixture.files()
        with self.assertRaises(ValueError):
            self.run.journey('select', 'missing')
        self.assertEqual(before, self.fixture.files())

    def test_focus_outside_first_page_is_recoverable(self):
        self.fixture.write('context/metrics.json', [
            {**self.body, 'name': 'metric-' + str(i), 'source_id': 'source-' + str(i)} for i in range(125)])
        self.run.journey('select', 'metric:metric-124')
        model = Project(self.run.root).experience(format='json')
        self.assertEqual(model['sample']['id'], 'metric:metric-124')
        self.assertTrue(model['items_truncated'])
        self.assertEqual(len(model['items']), 100)

    def test_completed_local_stage_never_claims_ready_for_questions(self):
        for stage, next_action in [('inspect', 'choose-domain'), ('scope', 'choose-domain'), ('build', 'check'), ('check', 'preview')]:
            self.fixture.write('progress.json', {'stage': stage, 'status': 'complete'})
            self.assertEqual(self.run.journey()['continuation']['action'], next_action)
            self.assertEqual(self.run.experience(format='json')['counts']['released'], 0)

    def test_resume_does_not_clear_auth_hold_or_publish(self):
        self.run.journey('pause')
        self.fixture.write('auth-recovery.json', {'status': 'permission_denied'})
        self.assertEqual(self.run.journey('resume')['continuation']['action'], 'recover-access')
        self.fixture.write('auth-recovery.json', {'status': 'connected'})
        self.fixture.write('context/candidate-checks.json', {'metric:shipped-units': {
            'definition_hash': fixtures.digest(self.body['definition']), 'status': 'held'}})
        self.run.journey('select', 'metric-source')
        self.assertEqual(self.run.journey('resume')['continuation']['action'], 'repair')
        self.assertNotIn('publication', self.run.current())

    def test_resume_canceled_checkpoint_and_keep_local_preserves_release(self):
        self.fixture.write('progress.json', {'stage': 'check', 'status': 'canceled'})
        self.assertEqual(self.run.journey('resume')['continuation']['action'], 'check')
        self.fixture.receipt(self.body)
        self.run.journey('keep-local')
        model = self.run.experience(format='json')
        self.assertEqual(model['counts']['released'], 1)
        self.assertEqual(model['continuation']['action'], 'ask')
        self.assertNotIn('drafts', model['phase']['label'])
        self.assertEqual(self.run.journey('resume')['continuation']['action'], 'ask')

    def test_keep_local_continues_preparation_and_never_requests_publication(self):
        self.run.journey('domain', answer='Shipping')
        self.run.journey('purpose', answer='Explore totals')
        self.run.journey('select', 'metric-source')
        self.run.journey('keep-local')
        self.fixture.write('auth-recovery.json', {'status': 'permission_denied'})
        for stage, status, expected in [('build', 'running', 'build'), ('check', 'complete', 'preview'),
                                        ('preview', 'complete', 'explore-local')]:
            self.fixture.write('progress.json', {'stage': stage, 'status': status})
            model = self.run.experience(format='json')
            self.assertEqual(model['continuation']['action'], expected)
            self.assertEqual(model['interaction']['mode'], 'local')
            if stage != 'preview':
                self.assertNotEqual(model['phase']['status'], 'local')

    def test_cli_bad_selection_does_not_mark_engine_failed(self):
        before = self.fixture.files()
        result = subprocess.run([sys.executable, '-I', str(fixtures.SCRIPTS / 'project.py'),
                                 '--run-dir', str(self.run.root), 'journey', 'select', '--focus', 'missing'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(before, self.fixture.files())

    def test_corrupt_or_symlinked_choices_cannot_silently_resume(self):
        path = self.run.root / 'interaction.json'
        for bad in ({'version': 1, 'mode': 'active', 'focus': []}, {'version': 99, 'mode': 'active'}):
            self.run.write(path, bad)
            with self.assertRaises(ValueError):
                self.run.journey()
        path.write_text('not json')
        self.assertEqual(self.run.experience(format='json')['continuation']['action'], 'repair')
        path.unlink()
        path.symlink_to(self.run.path)
        with self.assertRaises(ValueError):
            self.run.journey('resume')


@unittest.skipUnless(importlib.util.find_spec('duckdb'), 'Requires the existing approved DuckDB runtime.')
class FirstAnswerRehearsal(unittest.TestCase):
    def test_survey_history_check_preview_restart_release_retrieve_and_fresh_answer(self):
        # Source data and authored gate outputs are synthetic; Registry transport is a double.
        # Survey, preparation, schema checks, SQL checks, receipts and answers execute for real.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import analytics_ux_prototype as prototype
        import duckdb
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            estate = root / 'source'
            estate.mkdir()
            (estate / 'shipments.csv').write_text(
                'shipment_id,region,status,units,shipping_cost,weight_kg\n'
                'S001,North,shipped,12,36,18\nS002,South,shipped,8,28,12\n'
                'S003,North,pending,5,15,7\n')
            metrics, queries = prototype.candidate_bodies()
            metric, query = metrics[0], queries[0]
            (estate / 'querylog.jsonl').write_text(json.dumps({'sql': query['definition']['sql'], 'exec': 3, 'users': 2}) + '\n')
            run = Project(root / 'run')
            survey = run.survey(estate)
            self.assertEqual(run.journey()['continuation']['action'], 'choose-domain')
            self.assertEqual(run.journey('domain', answer='Shipped units')['continuation']['action'], 'choose-purpose')
            run.journey('purpose', answer='How many units shipped by region?')
            self.assertEqual(run.history()['intake']['rows_read'], 1)
            source = next(s for s in survey['sources'] if s['path'] == 'shipments.csv')
            prepared = run.prepare({'database': 'warehouse.duckdb', 'tables': [
                {'source_id': source['source_id'], 'table': 'shipping.shipments'}]})
            run.bind(prepared['database'], ['shipping.shipments'])
            run.write(run.context / 'metrics.json', [metric])
            run.write(run.context / 'queries.json', [query])
            run.check()
            self.assertEqual(run.journey()['continuation']['action'], 'select-focus')
            run.journey('select', focus='metric:' + metric['name'])
            self.assertEqual(run.experience(format='json')['sample']['status'], 'schema_checked')
            run.check_metric({'name': metric['name']})
            run.check_query({'name': query['name']})
            progress.update(run, 'preview', 'running')
            preview = run.experience(format='json')
            self.assertEqual(preview['sample']['status'], 'example_executed')
            self.assertEqual(preview['continuation']['action'], 'destination')
            run.journey('select', preview['focus'])
            run.journey('keep-local')
            run = Project(root / 'run')
            self.assertEqual(run.journey()['interaction']['mode'], 'local')
            self.assertEqual(run.journey('resume')['continuation']['action'], 'destination')
            remote = {'workspace_id': 'workspace_example', 'artifacts': []}
            for kind, body in [('metric', metric), ('sql_query', query)]:
                remote['artifacts'].append({'workspace_id': 'workspace_example', 'id': kind + '_example',
                    'kind': kind, 'name': body['name'], 'display_name': body['display_name'], 'description': body['description'],
                    'artifact_status': 'active', 'is_released': True,
                    'release': {'release_id': 'release_' + kind, 'version_id': 'version_' + kind,
                                'channel': 'latest', 'release_status': 'released', 'is_current': True},
                    'metadata': {'definition': copy.deepcopy(body['definition'])}})
            run.publication(copy.deepcopy(remote))
            run = Project(root / 'run')
            self.assertEqual(run.journey()['continuation']['action'], 'ask')
            self.assertEqual(run.experience(format='json')['focus'], preview['focus'])
            self.assertEqual(run.experience(format='json')['counts']['released'], 2)
            request = {'readbacks': copy.deepcopy(remote), 'artifact_id': 'sql_query_example'}
            answer = run.answer(request)
            self.assertEqual(answer['rows'], [['North', 12], ['South', 8]])
            self.assertEqual(answer['retrieval_check'], 'passed')
            with duckdb.connect(str(prepared['database'])) as con:
                con.execute("INSERT INTO shipping.shipments VALUES ('S004','North','shipped',2,6,3)")
            self.assertEqual(run.answer(request)['rows'], [['North', 14], ['South', 8]])
            request['readbacks']['artifacts'][1]['release']['release_id'] = 'release_changed'
            with self.assertRaises(ValueError):
                run.answer(request)
            self.assertEqual(run.experience(format='json')['focus'], preview['focus'])


if __name__ == '__main__':
    unittest.main()
