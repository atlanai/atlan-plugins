#!/usr/bin/env python3
"""Generate synthetic first-use presentation fixtures, not an engine or Registry run.

python3 tools/analytics_ux_prototype.py --out /absolute/empty/output
Uses only the standard library and existing presentation helpers. Participant inputs and
observer answers are separated. Every check state is seeded evidence for a UI replay.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
from context_state import ContextState, digest, ordinary  # noqa: E402
import experience  # noqa: E402
import metric_preview  # noqa: E402
from project import Project  # noqa: E402

STAMP = '2026-11-08T00:00:00+00:00'
NOTICE = ('Synthetic fixture replay; no live Registry publication. '
          'Candidate and check states are seeded, not engine-generated or SQL-executed.')
COLUMNS = ('shipment_id', 'region', 'status', 'units', 'shipping_cost', 'weight_kg')
DATASETS = {
    'data-x': [
        ('S001', 'North', 'shipped', 12, '36.00', '18.00'),
        ('S002', 'South', 'shipped', 8, '28.00', '12.00'),
        ('S003', 'North', 'pending', 5, '15.00', '7.50'),
        ('S004', 'East', 'shipped', 10, '40.00', '20.00'),
        ('S005', 'West', 'canceled', 6, '24.00', '9.00'),
        ('S006', 'South', 'shipped', 20, '50.00', '25.00'),
    ],
    'data-y': [
        ('F101', 'West', 'shipped', 15, '45.00', '22.50'),
        ('F102', 'East', 'pending', 4, '16.00', '6.00'),
        ('F103', 'North', 'shipped', 9, '36.00', '18.00'),
        ('F104', 'West', 'canceled', 7, '21.00', '10.50'),
        ('F105', 'South', 'shipped', 14, '42.00', '21.00'),
        ('F106', 'North', 'shipped', 22, '66.00', '33.00'),
    ],
}
FILTER = "shipping.shipments.status = 'shipped'"
METRICS = (
    ('shipped-units', 'Shipped units', 'Units on shipped shipments.',
     'SUM(shipping.shipments.units)', 'sum', 'integer', 'units'),
    ('shipping-cost', 'Shipping cost', 'Total shipping cost for shipped shipments.',
     'SUM(shipping.shipments.shipping_cost)', 'sum', 'decimal', 'currency'),
    ('shipped-shipments', 'Shipped shipments', 'Number of shipments with shipped status.',
     'COUNT(*)', 'count', 'integer', 'shipments'),
    ('shipped-weight', 'Shipped weight', 'Total weight of shipped shipments.',
     'SUM(shipping.shipments.weight_kg)', 'sum', 'decimal', 'kg'),
    ('shipping-cost-per-unit', 'Shipping cost per unit', 'Shipping cost divided by shipped units.',
     'SUM(shipping.shipments.shipping_cost) / NULLIF(SUM(shipping.shipments.units), 0)',
     'ratio', 'decimal', 'currency per unit'),
)


def statements():
    return [
        ('shipped-units-by-region', 'How many units shipped by region?',
         'SELECT region, SUM(units) AS shipped_units FROM shipping.shipments '
         "WHERE status = 'shipped' GROUP BY region ORDER BY region"),
        ('total-shipping-cost', 'What is the total shipping cost?',
         "SELECT SUM(shipping_cost) AS shipping_cost FROM shipping.shipments WHERE status = 'shipped'"),
        ('shipped-shipment-count', 'How many shipments shipped?',
         "SELECT COUNT(*) AS shipped_shipments FROM shipping.shipments WHERE status = 'shipped'"),
        ('shipped-weight-by-region', 'What weight shipped by region?',
         'SELECT region, SUM(weight_kg) AS shipped_weight FROM shipping.shipments '
         "WHERE status = 'shipped' GROUP BY region ORDER BY region"),
        ('shipping-cost-per-unit', 'What is shipping cost per shipped unit?',
         'SELECT SUM(shipping_cost) / NULLIF(SUM(units), 0) AS cost_per_unit '
         "FROM shipping.shipments WHERE status = 'shipped'"),
    ]


def participant_inputs(output):
    expected = {}
    for label, rows in DATASETS.items():
        folder = output.root / 'participant-inputs' / label
        csv_text = io.StringIO(newline='')
        writer = csv.writer(csv_text, lineterminator='\n')
        writer.writerow(COLUMNS)
        writer.writerows(rows)
        output.write_text(folder / 'shipments.csv', csv_text.getvalue())
        columns = [{'name': name, 'dataType': 'int64' if name == 'units' else
                    'decimal' if name in ('shipping_cost', 'weight_kg') else 'string'} for name in COLUMNS]
        model = {'name': 'Shipping', 'compatibilityLevel': 1600, 'model': {'tables': [{
            'name': 'Shipments', 'columns': columns, 'measures': [{
                'name': 'Shipped units', 'expression':
                'CALCULATE(SUM(Shipments[units]), Shipments[status] = "shipped")',
                'formatString': '0'}]}]}}
        output.write(folder / 'shipping.bim', model)
        history = [{'sql': sql, 'exec': 6 + index, 'users': 2} for index, (_, _, sql) in enumerate(statements())]
        output.write_text(folder / 'query_history.jsonl', ''.join(json.dumps(row) + '\n' for row in history))
        output.write_text(folder / 'README.md',
                          '# Shipping context\n\nSynthetic shipping records, a Power BI model export and query history.\n'
                          'Use shipments.csv as shipping.shipments when selecting a local data binding.\n'
                          'The model contains a declared measure; history contains additional questions.\n'
                          'This model-only fixture has no report pages. Do not invent a page selector.\n')
        shipped = [dict(zip(COLUMNS, row)) for row in rows if row[2] == 'shipped']
        units = sum(row['units'] for row in shipped)
        cost = sum((Decimal(row['shipping_cost']) for row in shipped), Decimal(0))
        weight = sum((Decimal(row['weight_kg']) for row in shipped), Decimal(0))
        regions = sorted({row['region'] for row in shipped})
        expected[label] = {
            'method': 'Independent Python integer counts and Decimal sums over fixture rows; no SQL execution.',
            'required_population': "status = 'shipped'", 'excluded_statuses': ['pending', 'canceled'],
            'source_rows': len(rows), 'shipped-shipments': len(shipped), 'shipped-units': units,
            'shipping-cost': str(cost), 'shipped-weight': str(weight),
            'shipping-cost-per-unit': str(cost / units) if units else None,
            'shipped-units-by-region': {region: sum(r['units'] for r in shipped if r['region'] == region) for region in regions},
            'shipped-weight-by-region': {region: str(sum((Decimal(r['weight_kg']) for r in shipped
                                                        if r['region'] == region), Decimal(0))) for region in regions},
        }
    output.write(output.root / 'observer-only' / 'expected-values.json', expected)
    output.write_text(output.root / 'observer-only' / 'README.md',
                      '# Observer answers\n\nDo not expose this directory to the participant or generation assistant.\n'
                      'These values were calculated from rows using Python counts and Decimal.\n'
                      'They are not engine outputs or evidence of warehouse validation.\n'
                      'Both inputs are model-only. A report-page task needs a separate report fixture;\n'
                      'use measure/source scope for these folders. Participant testing has not run.\n')


def candidate_bodies():
    metrics, queries = [], []
    for index, (name, display, meaning, formula, aggregation, datatype, unit) in enumerate(METRICS):
        sql = f'SELECT {formula} AS value FROM shipping.shipments WHERE {FILTER}'
        evidence = {'shapes': [f'shape_{index + 1}']}
        if index == 0:
            evidence['declared'] = {'name': display, 'ref': 'shipping.bim', 'kind': 'Power BI model'}
        definition = {
            'spec_version': '1.0-draft.4', 'version': 1, 'dialect': 'duckdb', 'formula': formula,
            'aggregation': {'type': aggregation, **({'zero_denominator': 'null'} if aggregation == 'ratio' else {})},
            'source': {'entity': 'shipping.shipments', 'table': 'shipping.shipments', 'alias': 'shipments'},
            'output': {'alias': name.replace('-', '_'), 'datatype': datatype, 'unit': unit},
            'population': 'Shipments with shipped status.', 'exclusions': ['Pending and canceled shipments.'],
            'mandatory_filters': [FILTER], 'filters': [], 'grain': 'One row per shipment; key uniqueness is not declared.', 'joins': [],
            'dimensions': [{'name': 'Region', 'kind': 'categorical', 'sql': 'shipping.shipments.region', 'datatype': 'string'}],
            'examples': [{'sql': sql, 'source': f'shape:shape_{index + 1}'}],
            'extensions': {'atlan_analytics': evidence},
        }
        metrics.append({'name': name, 'display_name': display, 'description': meaning,
                        'source_id': 'synthetic/' + name, 'definition': definition})
    for index, (name, question, sql) in enumerate(statements()):
        queries.append({'name': name, 'display_name': question, 'description': question,
                        'source_id': 'synthetic/query/' + name, 'definition': {
                            'spec_version': '1.0-draft.1', 'sql': sql, 'question': question,
                            'dialect': 'duckdb', 'review_status': 'unreviewed',
                            'usage_note': 'Shipments with shipped status.',
                            'analysis': {'tables': ['shipping.shipments'], 'filters': [
                                {'column': 'shipping.shipments.status', 'operator': '=', 'value': 'shipped'}]},
                            'extensions': {'atlan_analytics': {'shapes': [f'shape_{index + 1}']}}}})
    return metrics, queries


def seed_run(output, name):
    run = Project(output.root / 'replays' / name)
    run.write(run.path, {'entrypoint': 'project', 'sources': [
        {'path': 'shipping.bim', 'kind': 'powerbi', 'status': 'available'},
        {'path': 'query_history.jsonl', 'kind': 'query_history', 'status': 'available'},
        {'path': 'shipments.csv', 'kind': 'csv', 'status': 'available'}]})
    run.write(run.root / 'FIXTURE.json', {'synthetic': True, 'notice': NOTICE, 'seeded_at': STAMP})
    return run


def seed_early(run):
    state = run.read(run.path)
    state['engine_run'] = 'engine'
    run.write(run.path, state)
    run.write(run.root / 'progress.json', {'stage': 'build', 'status': 'running'})
    run.write(run.root / 'engine/module.json', {'status': 'paused'})
    sql = "SELECT SUM(units) AS shipped_units FROM shipping.shipments WHERE status = 'shipped'"
    layer = {'contract_version': '1.0', 'dialect': 'duckdb', 'scope': 'synthetic-shipping',
             'profile': 'history-only', 'objects': [{'vq_id': 'vq_1', 'sql': sql,
                                                  'question': 'How many units shipped?'}]}
    layer['layer_sha'] = hashlib.sha256(json.dumps(layer, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]
    run.write(run.root / 'engine/verified_queries.json', layer)
    run.write(run.root / 'engine/metric_proposals.json', {
        'contract_version': '1.0', 'layer_sha': layer['layer_sha'], 'proposals': [{
            'proposal_id': 'proposal_1', 'vq_id': 'vq_1', 'name': 'Shipped units',
            'expression': 'SUM(units)', 'entity': 'shipping.shipments',
            'definition': 'Units on shipped shipments.', 'mandatory_filters': ["status = 'shipped'"]}]})
    run.write(run.root / 'engine/manifest.json', {'stages': [
        {'stage': 'c5_emit', 'verdict': 'ok', 'outputs': ['verified_queries.json']},
        {'stage': 'x1_propose', 'verdict': 'ok', 'outputs': ['metric_proposals.json']},
        {'stage': 'x2_emit', 'verdict': 'ok', 'notes': 'awaiting_decision', 'outputs': []}]})


def seed_checked(run, state_name):
    metrics, queries = candidate_bodies()
    run.write(run.context / 'metrics.json', metrics)
    run.write(run.context / 'queries.json', queries)
    run.write(run.context / 'links.json', [])
    checks = {kind + ':' + body['name']: {'definition_hash': digest(body['definition']),
                                         'status': 'schema_checked'}
              for kind, rows in [('metric', metrics), ('sql_query', queries)] for body in rows}
    run.write(run.context / 'candidate-checks.json', checks)
    run.write(run.root / 'progress.json', {'stage': 'preview',
                                          'status': 'waiting-for-user' if state_name == 'paused' else 'running'})
    if state_name == 'access-denied':
        run.write(run.root / 'auth-recovery.json', {'status': 'permission_denied'})


def render_replay(run):
    model = experience.snapshot(run)
    model['snapshot_at'] = STAMP
    # The fixture banner is applied here, outside the product renderer and word counts.
    banner = '<aside role="note" style="padding:16px;background:#fff3cf;color:#382d0b;font:16px system-ui"><strong>' + NOTICE + '</strong></aside>'
    page = experience.render_html(model).replace('<main>', banner + '<main>', 1)
    run.write(run.root / 'snapshot.json', model)
    run.write_text(run.root / 'snapshot.html', page)
    run.write_text(run.root / 'chat.md', NOTICE + '\n\n' + experience.render_chat(model) + '\n')
    run.write_text(run.root / 'detail.md', NOTICE + '\n\n' + experience.render_chat(model, detail=True) + '\n')
    return model


def word_counts(text):
    # A reproducible text count, not reading time or human comprehension.
    sql = '\n'.join(re.findall(r'`{3,}sql\n(.*?)\n`{3,}', text, flags=re.S))
    return {'whitespace_tokens': len(text.split()), 'sql_whitespace_tokens': len(sql.split())}


def build(out):
    destination = ordinary(out)
    if destination.exists() and (not destination.is_dir() or next(destination.iterdir(), None) is not None):
        raise ValueError('Choose a new or empty output directory; existing files were not changed.')
    output = ContextState(destination)
    participant_inputs(output)
    models, checked_run = {}, None
    for name in ('early-proposal', 'checked', 'paused', 'access-denied'):
        run = seed_run(output, name)
        seed_early(run) if name == 'early-proposal' else seed_checked(run, name)
        models[name] = render_replay(run)
        if name == 'checked':
            checked_run = run
    baseline = metric_preview.chat(checked_run, limit=5)
    proposed = experience.render_chat(models['checked'])
    detail = experience.render_chat(models['checked'], detail=True)
    for filename, content in [('baseline.md', baseline), ('proposed.md', proposed), ('proposed-detail.md', detail)]:
        output.write_text(output.root / 'comparison' / filename, content + '\n')
    measurement = {
        'fixture': 'replays/checked', 'synthetic': True, 'same_candidate_checkpoint': True,
        'method': 'Whitespace-separated tokens. SQL fenced-block tokens counted separately; no human reading-time claim.',
        'baseline': {'renderer': 'metric_preview.chat(run, limit=5)', 'metric_samples': 5, **word_counts(baseline)},
        'proposed': {'renderer': 'experience.render_chat(snapshot)', 'metric_samples': 1, **word_counts(proposed)},
        'proposed_detail': {'renderer': 'experience.render_chat(snapshot, detail=True)', **word_counts(detail)},
        'interpretation': 'Different default detail levels from the same five-metric/five-query fixture. '
                          'Word reduction alone is not evidence of improved usability or equal information density.',
    }
    output.write(output.root / 'comparison' / 'measurement.json', measurement)
    output.write_text(output.root / 'README.md', '# Analytics UX fixture replay\n\n' + NOTICE + '\n\n'
                      'Open index.html for the four checkpoints. Each snapshot is static and labels saved evidence.\n'
                      'No engine, SQL executor, model API or Registry call ran. There is no published-state fixture.\n'
                      'participant-inputs/data-x and data-y are the only folders to give a study participant or assistant.\n'
                      'Keep observer-only, replays and comparison out of their input scope. Participant testing has not run.\n'
                      'The two input folders have model measures, query history and CSVs, but no report pages.\n'
                      'comparison contains old/new rendering of the same checked fixture and token-count methodology.\n')
    links = ''.join(f'<li><a href="replays/{name}/snapshot.html">{name}</a></li>' for name in models)
    output.write_text(output.root / 'index.html', '<!doctype html><html lang="en"><meta charset="utf-8">'
                      '<meta name="viewport" content="width=device-width"><title>Analytics UX fixture replay</title>'
                      '<h1>Analytics UX fixture replay</h1><p><strong>' + NOTICE + '</strong></p><ul>' + links
                      + '</ul><p>All snapshots are static. Participant study: not run.</p></html>')
    output.write(output.root / 'manifest.json', {
        'synthetic': True, 'notice': NOTICE, 'fixture_timestamp': STAMP,
        'engine_calls': 0, 'sql_executions': 0, 'model_calls': 0, 'registry_calls': 0,
        'checkpoints': list(models), 'participant_inputs': ['participant-inputs/data-x', 'participant-inputs/data-y'],
        'observer_answers': 'observer-only/expected-values.json',
        'helpers': {name: hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest()
                    for name in ('experience.py', 'metric_preview.py', 'project.py')},
    })
    return {'status': 'generated', 'synthetic': True, 'out': str(destination),
            'index': str(destination / 'index.html'), 'checkpoints': list(models),
            'comparison': measurement, 'participant_study': 'not_run'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, help='New or empty ordinary output directory; never overwritten.')
    args = parser.parse_args(argv)
    try:
        result = build(args.out)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
