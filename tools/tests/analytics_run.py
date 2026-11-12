"""A made-up run folder with the files a finished setup run leaves, for the chat and side-view tests.

`make_run` writes only what the chosen sources produce: a dashboard-only run has no query history or
history stages, a history-only run has no dashboard reading, a run kept local has no publication. The run's
task-list state (progress.json, interaction.json) says it finished, unless `stage` names where it stands;
`at` moves it on. `warehouse` makes it read a warehouse in place, as a real own-data run does: no local tables,
history pulled into the engine's own input, the connection recorded in the run's state. `chosen` is what the
person picked to build from when it differs from what exists; `failing` checks fail.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
DEMO = ROOT / 'plugins/atlanai-analytics/skills/analytics-demo/references'


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding='utf-8')


def make_run(root, dashboard=True, history=True, saved=True, local=False, demo=False, answered=True, engine='engine', history_format='jsonl',
             stage=None, warehouse=None, chosen=None, failing=0):
    root = Path(root)
    run, source = root / 'run', root / 'source'
    sources = [] if warehouse else [{'kind': 'data_file', 'path': 'data/jobs.csv', 'status': 'available'},
                                    {'kind': 'data_file', 'path': 'data/work_logs.csv', 'status': 'available'}]
    rows = [{'sql': 'SELECT SUM(hours) FROM site.work_logs', 'exec': 30},
            {'sql': 'SELECT * FROM site.jobs WHERE id = 3', 'exec': 3},
            {'sql': 'SELECT COUNT(*) FROM site.jobs', 'exec': 7}]
    if history and not warehouse:
        name = 'history/querylog.csv' if history_format == 'csv' else 'history/querylog.jsonl'
        sources.append({'kind': 'query_history', 'path': name, 'status': 'available'})
        if history_format == 'csv':
            _write(source / name, 'QUERY_TEXT,EXECUTION_COUNT\n' + ''.join('"%s",%d\n' % (r['sql'], r['exec']) for r in rows))
        elif history_format == 'upper':
            _write(source / name, '\n'.join(json.dumps({'QUERY_TEXT': r['sql'], 'EXECUTION_COUNT': r['exec']}) for r in rows) + '\n')
        else:
            _write(source / name, '\n'.join(json.dumps(r) for r in rows) + '\n')
    if history:
        # The engine's own history input: copied from a local file, or pulled from a warehouse.
        _write(run / 'context' / 'querylog.jsonl', '\n'.join(json.dumps(r) for r in rows) + '\n')
    if dashboard:
        sources.append({'kind': 'powerbi', 'path': 'bi/report.pbit', 'status': 'available'})
        _write(run / 'context' / 'src_1.json', {'counts': {'measures': 2, 'pages': 1}})
    _write(run / 'run.json', {'source_root': str(source), **({'engine_run': str(run / engine)} if engine == 'engine' else {}), 'sources': sources,
                              **({} if warehouse else {'execution': {'tables': ['site.work_logs', 'site.jobs']}})})
    if warehouse:
        _write(run / 'state.json', {'warehouse': {'kind': warehouse, 'dialect': warehouse}})
        _write(run / 'context' / 'table-schema.json', {'SITE.WORK_LOGS': [{}, {}], 'SITE.JOBS': [{}]})
    _write(run / 'survey-cache.json', {'sources': sources})
    _write(run / 'interaction.json', {'version': 1, 'flow': 'demo' if demo else 'build', 'mode': 'local' if local else 'active',
                                      'intent': {'domain': 'Job margin', 'purpose': 'See which jobs lose margin and why'},
                                      'asked': bool(answered and (saved or local))})
    at(run, *(stage or (('ready' if saved and not local else 'preview' if local else 'check'), 'complete')))
    picked = chosen or {'bi': dashboard, 'history': history}
    _write(run / 'engine-config.json', {'inputs': {'bi': ['local-files'] if picked['bi'] else [], 'history': ['local-files'] if picked['history'] else []},
                                        **({'tables': ['SITE.WORK_LOGS', 'SITE.JOBS']} if warehouse else {})})
    if not warehouse:
        _write(run / 'context' / 'data-preparation.json', {'tables': [{'table': 'site.work_logs', 'rows': 120}, {'table': 'site.jobs', 'rows': 9}]})

    stages = [{'stage': 's0_inputs', 'counts': {}}]
    if history:
        stages += [{'stage': 'h0_corpus', 'counts': {'scanned': 3, 'kept': 2}},
                   {'stage': 'h1_shapes', 'counts': {'analytical': 2, 'canonical_shapes': 2}}]
    if dashboard:
        stages += [{'stage': 'a3b_emit', 'counts': {'prompted': 2}}, {'stage': 'a3b_ingest', 'counts': {'findings': 0}},
                   {'stage': 'a4_witness_bi', 'counts': {'witnessed': 2, 'verdict_pass': 2}}]
    questions = (2 if history else 0) + (2 if dashboard else 0)
    stages += [{'stage': 'm2_ingest', 'counts': {'described': questions}},
               {'stage': 'x1_ingest', 'counts': {'proposals': questions - 1, 'no_metric': 1}},
               {'stage': 'x2_ingest', 'counts': {'metrics_out': 2}},
               {'stage': 'x3_ingest', 'counts': {}},
               {'stage': 'x4_ingest', 'counts': {'edges': 1}}]
    engine = run / engine
    _write(engine / 'manifest.json', {'stages': stages})
    if dashboard:
        _write(engine / 'verified_queries.bi.json', {'objects': [{}, {}]})
    if history:
        _write(engine / 'verified_queries.history.json', {'objects': [{}, {}]})
    _write(engine / 'merged_metrics.json', {'n_metrics_out': 2, 'metrics': [
        {'name': 'Gross Margin', 'sources': [{'proposal_id': 'bi-1'}, {'proposal_id': 'h-1#0'}]}, {'name': 'Rework Cost', 'sources': [{'proposal_id': 'h-2#0'}]}]})

    metric = lambda name, unit, origin: {'name': name, 'display_name': name, 'description': name + ' in pounds.',
                                        'definition': {'output': {'unit': unit}, 'formula': 'SUM(x)',
                                                       'provenance': {'derived_from': [origin]}}}
    _write(run / 'context' / 'metrics.json', [metric('Gross Margin', 'GBP', 'bi-gross-margin'), metric('Rework Cost', 'GBP', 'h-rework')])
    _write(run / 'context' / 'queries.json', [{'name': 'q-margin', 'definition': {'review_status': 'verified'}},
                                              {'name': 'q-jobs', 'definition': {'review_status': 'unreviewed'}}])
    check = lambda v: {'status': 'example_executed', 'errors': [], 'composition': {'status': 'executed', 'rows': [[v]]}}
    checks = {'metric:Gross Margin': check(118681.0), 'metric:Rework Cost': check(33800.0),
              'sql_query:q-margin': {'status': 'executed'}, 'sql_query:q-jobs': {'status': 'executed'}}
    for key in list(checks)[::-1][:failing]:
        checks[key] = {'status': 'failed', 'errors': ['The table no longer holds that period.']}
    _write(run / 'context' / 'candidate-checks.json', checks)
    if saved and not local:
        _write(run / 'context' / 'plan.json', {'counts': {'metrics': {'create': 2}, 'queries': {'create': 2}, 'links': 3}, 'links': [{}] * 3})
        meta = json.loads((run / 'run.json').read_text())
        # The receipt `PROJECT publication` writes: each item read back by its exact definition, in a workspace.
        import sys
        sys.path.insert(0, str(SCRIPTS))
        from context_state import digest
        bodies = [('metric', m) for m in json.loads((run / 'context' / 'metrics.json').read_text())] + \
                 [('sql_query', q) for q in json.loads((run / 'context' / 'queries.json').read_text())]
        meta['publication'] = {'workspace_id': 'w1', 'artifacts': [
            {'id': 'a%d' % i, 'kind': kind, 'name': body['name'], 'release_id': 'r%d' % i, 'version_id': 'v%d' % i,
             'channel': 'latest', 'definition_hash': digest(body['definition']), 'version': 1, 'version_label': '0.1.0'}
            for i, (kind, body) in enumerate(bodies)]}
        _write(run / 'run.json', meta)
        _write(run / 'saved.json', {'workspace': 'Test workspace'})
    if not answered:
        return run
    _write(run / 'retrieved-result.json', {'artifacts': [{}, {}]})
    _write(run / 'answer.json', {'question': 'Which job lost the most margin?', 'headline': 'Turbine base: -£6,360', 'unit': 'GBP',
                                 'rows': [['Turbine base', -6360], ['Cat flap', -646.5], ['Barn', 16137]], 'highlight': 'Turbine base',
                                 'rule': 'Contract value less labour and materials', 'proof': [{'name': 'Gross Margin'}],
                                 'breakdown': {'steps': [['Contract', 150000], ['Labour', -156360]]}})
    _write(run / 'layout.json', {'tiles': ['Gross Margin', 'Rework Cost'], 'more': ['On-time rate']})
    _write(run / 'edit.json', {'metric': 'Late Jobs', 'before': 'Late after a week', 'after': 'Late after a day',
                               'values': [['Late jobs', 2, 3]], 'added': ['Bus shelter']})
    return run


def at(run, stage, status='complete'):
    """Where the run stands, as its progress file says."""
    _write(Path(run) / 'progress.json', {'stage': stage, 'status': status})


def facts():
    return json.loads((DEMO / 'yard.json').read_text(encoding='utf-8'))
