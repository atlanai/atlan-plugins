"""Adapt the core bundle to Analytics context files, with no Registry I/O.

Projection follows the pinned card contract. It preserves original identities and keeps
non-execution evidence in extensions, rather than recomputing or reauthoring metrics.
"""
import copy
import json
from pathlib import Path
import sys

from driver import ordinary, write

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.metrics import holds as publication_holds
from modules.metrics.required_filters import required_filter_holds

JOIN = ('name', 'to', 'sql', 'cardinality', 'origin')
DIMENSION = ('name', 'kind', 'sql', 'datatype', 'values', 'values_truncated', 'value_map',
             'required_join', 'filter_form', 'description')
EXAMPLE = ('source', 'sql', 'question', 'group_by', 'filters', 'parameters', 'result')


def pick(value, fields):
    return {key: copy.deepcopy(value[key]) for key in fields if key in value}


def metric_body(card, layer, commit):
    source = {'entity': card['entity'], 'alias': card['alias'] or 'm',
              **({'sql': card['sql']} if card.get('sql') else {'table': card['entity']}),
              'parameters': [pick(p, ('name', 'datatype', 'default')) for p in card.get('parameters') or []]}
    if card.get('timeframe_column'):
        source['timeframe_column'] = card['timeframe_column']
    dimensions = []
    for dim in card.get('dimensions') or []:
        item = pick(dim, DIMENSION)
        if not item.get('required_join') and dim.get('required_joins'):
            item['required_join'] = dim['required_joins'][0]
        dimensions.append(item)
    definition = {'spec_version': '1.0-draft.4', 'version': 1, 'dialect': card['dialect'],
                  'formula': card['formula'], 'aggregation': copy.deepcopy(card['aggregation']),
                  'output': copy.deepcopy(card['output']), 'source': source,
                  'population': card.get('population') or '', 'grain': card.get('grain') or '',
                  'exclusions': card.get('exclusions') or [], 'filters': card.get('filters') or [],
                  'mandatory_filters': card.get('mandatory_filters') or [],
                  'depends_on': card.get('depends_on') or [],
                  'joins': [pick(j, JOIN) for j in card.get('joins') or []],
                  'dimensions': dimensions,
                  'examples': [pick(e, EXAMPLE) for e in card.get('examples') or []],
                  'provenance': {**copy.deepcopy(card.get('provenance') or {}),
                                 'proposed_by': layer.get('producer') or 'metrics-agent/model-unrecorded'},
                  'extensions': {'metrics_agent': {'commit': commit,
                     'derived_from': card.get('derived_from') or [],
                     # Includes conversion quality, parameter descriptions, dimensional roles,
                     # trust tier and original evidence without claiming fresh verification.
                     'card': copy.deepcopy(card)}}}
    return {'name': card['name'], 'display_name': card.get('display_name') or card['name'],
            'description': card['description'],
            'search_text': ' '.join(card.get('synonyms') or []),
            'source_type': 'external', 'source_id': card['source_id'],
            'source_version': layer['layer_sha'], 'definition': definition}


_PRIVATE = ("holds a person's name", 'holds an email address', 'holds an identifier literal')


def privacy_holds(saved, check):
    """Split saved queries into those to keep and those every error of which is a privacy
    finding. The detail names the field and the kind of finding, never the value found."""
    kept, private = [], []
    for body in saved:
        errors, _ = check(body)
        if errors and all(any(mark in error for mark in _PRIVATE) for error in errors):
            private.append((body, '; '.join(sorted({error.split(' (')[0].split(':')[0] for error in errors}))))
        else:
            kept.append(body)
    return kept, private


def project(cards, queries, relationships, scope, commit, producer=None):
    from metrics_agent.lib import slugs
    metrics = [metric_body(card, {'layer_sha': cards.get('layer_sha') or queries['layer_sha'], 'producer': producer}, commit)
               for card in cards['metrics']]
    saved, by_vq = [], {}
    for query in queries['objects']:
        ident = query['vq_id']
        # Analytics' existing saved-query checker requires a slug; the spoken question and
        # original name remain in display/evidence, and the reconciliation identity is intact.
        name = slugs.kebab(query['name'], max_len=128)
        by_vq[ident] = name
        saved.append({'name': name, 'display_name': query['question'],
                      'description': query.get('description') or query['question'],
                      'source_type': 'external',
                      'source_id': slugs.sql_query_source_id(scope, ident.split('-', 1)[-1]),
                      'source_version': queries['layer_sha'],
                      'definition': {'spec_version': '1.0-draft.1', 'question': query['question'],
                          'sql': query['sql'], 'dialect': queries['dialect'],
                          'review_status': 'verified' if query.get('label') == 'verified' else 'unreviewed',
                          'extensions': {'metrics_agent': {'vq_id': ident, 'commit': commit,
                                                          'evidence': copy.deepcopy(query)}}}})
    refs = {card['metric_ref']: card['name'] for card in cards['metrics']}
    names = {card['name'] for card in cards['metrics']}
    links = []
    for card in cards['metrics']:
        for ident in card['derived_from']:
            if ident not in by_vq:
                raise ValueError('A metric source is missing from the verified layer.')
            links.append({'from': {'kind': 'sql_query', 'name': by_vq[ident]},
                          'to': {'kind': 'metric', 'name': card['name']}, 'rel_type': 'source_of',
                          'attributes': {'origin': 'metrics-agent'}})
    for edge in relationships.get('edges') or []:
        # X4's edges carry names and optional refs. Resolve against this run only.
        source = edge['from'] if edge['from'] in names else refs.get(edge['from_ref'] if edge.get('from_ref') else edge['from'])
        target = edge['to'] if edge['to'] in names else refs.get(edge['to_ref'] if edge.get('to_ref') else edge['to'])
        if not source or not target:
            raise ValueError('A relationship endpoint is missing from this metric bundle.')
        links.append({'from': {'kind': 'metric', 'name': source},
                      'to': {'kind': 'metric', 'name': target}, 'rel_type': edge['rel_type'],
                      'attributes': {**copy.deepcopy(edge.get('attributes') or {}),
                                     **pick(edge, ('relation', 'role', 'label', 'confidence', 'exact'))}})
    for kind, bodies in (('metric', metrics), ('query', saved)):
        for field in ('name', 'source_id'):
            values = [body[field] for body in bodies]
            if len(values) != len(set(values)):
                raise ValueError(f'Duplicate {kind} {field}; resolve it in the authored layer.')
    return metrics, saved, links


def export_context(run, context, receipt, parity=None):
    from metrics_agent import sockets
    from metrics_agent.lib import artifacts
    # Reuse the core output boundary, including its refusal to expose user-name lists.
    sockets.Bundle(run, run / 'verified_queries.json', run / 'metric_cards.json',
                   run / 'metric_relationships.json')
    cards = artifacts.read(run, 'metric_cards.json')
    queries = artifacts.read(run, 'verified_queries.json')
    relationships = artifacts.read(run, 'metric_relationships.json')
    if cards.get('partial'):
        raise ValueError('Complete the metric cards before publication.')
    if cards.get('spec_version') != '1.0-draft.1':
        raise ValueError('Unsupported core card contract; update the adapter explicitly.')
    from metrics_agent.lib import card_sql
    for card in cards['metrics']:
        problems = card_sql.aggregation_problems(card_sql.canonical_statement(card), card.get('dialect'))
        if problems:
            raise ValueError('A metric card failed aggregation checks; repair it through the authored card gate: '
                             + '; '.join(problems))
    ri = artifacts.read(run, 'run_inputs.json')
    metrics, saved, links = project(cards, queries, relationships, ri['scope'], receipt['commit'], receipt.get('producer'))
    report = None
    if (run / 'verify_report.json').is_file():
        if 'verify_report.json' not in receipt['outputs']:
            raise ValueError('Advance the completed run to bind its compiler-review evidence before export.')
        report = artifacts.read(run, 'verify_report.json')
    review = publication_holds.review_holds(queries, report)
    units = None
    if (run / 'question_units.json').is_file():
        if 'question_units.json' not in receipt['outputs']:
            raise ValueError('Advance the completed run to bind its required-filter evidence before export.')
        units = artifacts.read(run, 'question_units.json')
    for query_id, decision in required_filter_holds(queries, units).items():
        review.setdefault(query_id, decision)


    from validation import validation
    checked = validation(cards, parity, receipt['outputs'])
    held, held_vq = [], set(review)
    for body, card in zip(metrics, cards['metrics']):
        bad_sources = sorted(set(card['derived_from']) & set(review))
        if bad_sources:
            held.append({'source_id': body['source_id'], 'name': body['name'], 'status': 'held',
                         'reason': 'source_query_held', 'query_ids': bad_sources})
            held_vq.update(card['derived_from'])
    for body, card in zip(metrics, cards['metrics']):
        status = checked.get(body['source_id'])
        if status is None and (card.get('bi_definitions') or card.get('quality')):
            status = {'parity': 'unmeasured', 'reasons': ['independent_reference_not_compared']}
        if status:
            body['definition']['extensions']['atlan_analytics'] = {'dashboard_validation': status}
        if status and status['parity'] == 'failed':
            if not any(item['source_id'] == body['source_id'] for item in held):
                held.append({'source_id': body['source_id'], 'name': body['name'],
                             'status': 'held', 'reason': 'dashboard_parity_failed'})
            held_vq.update(card['derived_from'])
    names = {body['name']: body for body in metrics}
    cards_by_name = {card['name']: card for card in cards['metrics']}
    held_names = {item['name'] for item in held}
    dependencies = {(link['from']['name'], link['to']['name']) for link in links
                    if link['rel_type'] == 'depends_on' and link['from']['kind'] == 'metric'
                    and link['to']['kind'] == 'metric'}
    local_names = {key: card['name'] for card in cards['metrics']
                   for key in (card['name'], card.get('metric_ref'), card.get('source_id')) if key}
    for body in metrics:
        for target in body['definition'].get('depends_on') or []:
            if isinstance(target, str) and target in local_names:
                dependencies.add((body['name'], local_names[target]))
    changed = True
    while changed:
        changed = False
        for name, target in sorted(dependencies):
            if target in held_names and name not in held_names:
                held_names.add(name)
                held.append({'source_id': names[name]['source_id'], 'name': name,
                             'status': 'held', 'reason': 'required_metric_held'})
                held_vq.update(cards_by_name[name]['derived_from'])
                changed = True
    held_ids = {item['source_id'] for item in held}
    metrics = [body for body in metrics if body['source_id'] not in held_ids]
    held_queries = []
    for body in saved:
        query_id = body['definition']['extensions']['metrics_agent']['vq_id']
        if query_id in held_vq:
            held_queries.append({'name': body['name'], 'source_id': body['source_id'],
                                 'query_id': query_id, 'status': 'held',
                                 'sql_sha256': publication_holds.sql_identity(body['definition']['sql']),
                                 **review.get(query_id, {'reason': 'source_of_held_metric'})})
    saved = [body for body in saved if body['definition']['extensions']['metrics_agent']['vq_id'] not in held_vq]
    endpoints = {('metric', body['name']) for body in metrics} | {('sql_query', body['name']) for body in saved}
    links = [link for link in links if all((link[end]['kind'], link[end]['name']) in endpoints for end in ('from', 'to'))]
    # Use the existing publisher's checks; never relax them to accommodate the new engine.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'analytics-build/scripts'))
    import context_contracts
    # A saved query that only fails the privacy check is held and left out, as any other held
    # query is: it never ships, and it no longer stops the metrics and queries that passed.
    saved, private = privacy_holds(saved, context_contracts.check_query)
    for body, detail in private:
        held_queries.append({'name': body['name'], 'source_id': body['source_id'],
                             'query_id': body['definition']['extensions']['metrics_agent']['vq_id'],
                             'status': 'held', 'reason': 'privacy_review', 'detail': detail,
                             'sql_sha256': publication_holds.sql_identity(body['definition']['sql'])})
    endpoints = {('metric', body['name']) for body in metrics} | {('sql_query', body['name']) for body in saved}
    links = [link for link in links if all((link[end]['kind'], link[end]['name']) in endpoints for end in ('from', 'to'))]
    failures = []
    for kind, bodies, check in (('metric', metrics, context_contracts.check_metric),
                                ('sql_query', saved, context_contracts.check_query)):
        for body in bodies:
            errors, _ = check(body)
            if errors:
                failures.append({'kind': kind, 'name': body['name'], 'errors': errors})
    if failures:
        write(run / 'export-refusals.json', failures)
        raise ValueError('Context export refused; repair the named fields in export-refusals.json through the original gates.')
    hold_document = publication_holds.validate({'format': 2, 'checks': publication_holds.CHECKS, 'outputs': receipt['outputs'],
                                               'metrics': held, 'queries': held_queries})
    context = ordinary(context)
    context.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = context / 'metrics-source.json'
    source = {'module': 'metrics', 'commit': receipt['commit'], 'run': str(run),
              'outputs': receipt['outputs'], 'metrics': len(metrics), 'queries': len(saved),
              'links': len(links), 'held': held, 'held_queries': held_queries,
              'validation': checked, 'publication_holds_sha256': publication_holds.identity(hold_document),
              'remote_writes': False}
    if any(context.iterdir()):
        if not marker.is_file() or json.loads(marker.read_text()) != source:
            raise ValueError('Context contains another run or publication state; use a new context directory.')
        if not all((context / name).is_file() for name in ('metrics.json', 'queries.json', 'links.json')):
            raise ValueError('Exported context is incomplete; use a new context directory.')
        publication_holds.enforce_context(context)
        # The publisher can reconcile names and versions here. Re-export must never undo it.
        return {**source, 'status': 'exported', 'context': str(context), 'reused': True}
    for name, body in (('metrics.json', metrics), ('queries.json', saved), ('links.json', links),
                       ('metrics-source.json', source), (publication_holds.FILE, hold_document)):
        write(context / name, body)
    return {'status': 'exported', 'context': str(context), 'metrics': len(metrics),
            'queries': len(saved), 'links': len(links), 'held': held, 'held_queries': held_queries, 'remote_writes': False,
            'next': 'Read scoped Registry inventory, run the existing check and publication flow.'}
