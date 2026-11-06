"""Publication eligibility from structured engine checks, without Registry I/O.

The common engine owns review matching, including stale SQL findings. The adapter consumes
its effective C4 verdicts; prose, labels and missing warehouse access cannot create a hold.
"""
import hashlib
import json
from pathlib import Path
import re


FILE = 'publication-holds.json'
_HASH = re.compile(r'[a-f0-9]{64}')


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def sql_identity(sql):
    return hashlib.sha256(sql.encode()).hexdigest()


def review_holds(queries, report):
    by_id = {q['vq_id']: q for q in queries['objects']}
    if report is None:
        if any((q.get('provenance') or {}).get('lane') in ('bi', 'merged') for q in by_id.values()):
            raise ValueError('BI publication requires the completed compiler-review gate report.')
        return {}
    if report.get('stage') != 'c4_gates' or report.get('clean') is not True:
        raise ValueError('Publication requires a clean C4 gate report.')
    held = {}
    for check in report['checks']:
        if check['gate'] != 'compiler_review' or check['status'] == 'pass':
            continue
        query_id = check['object']
        if query_id not in by_id:
            raise ValueError('A compiler-review hold names a query absent from the completed layer.')
        held[query_id] = {'reason': 'compiler_review_wrong', 'detail': check.get('detail', '')}
    return held


def validate(document):
    """Standard-library validation of the publication-holds schema for host preflights."""
    if not isinstance(document, dict) or set(document) != {'format', 'outputs', 'metrics', 'queries'} or type(document['format']) is not int or document['format'] != 1:
        raise ValueError('Invalid publication hold manifest.')
    outputs = document['outputs']
    if not isinstance(outputs, dict) or not outputs or any(not isinstance(k, str) or not isinstance(v, str)
                                                          or not _HASH.fullmatch(v) for k, v in outputs.items()):
        raise ValueError('Publication holds must name the completed output hashes.')
    allowed = {'source_id', 'name', 'status', 'reason', 'query_id', 'sql_sha256', 'detail', 'query_ids'}
    for kind in ('metrics', 'queries'):
        rows, seen = document[kind], set()
        if not isinstance(rows, list):
            raise ValueError('Publication holds must be lists.')
        for row in rows:
            if not isinstance(row, dict) or set(row) - allowed or row.get('status') != 'held':
                raise ValueError('Invalid publication hold record.')
            required = ('source_id', 'name', 'reason') + (('query_id', 'sql_sha256') if kind == 'queries' else ())
            if any(not isinstance(row.get(k), str) or not row[k] for k in required):
                raise ValueError('Publication hold identity or reason is missing.')
            if row['source_id'] in seen:
                raise ValueError('Duplicate publication hold identity.')
            seen.add(row['source_id'])
            if 'sql_sha256' in row and (not isinstance(row['sql_sha256'], str) or not _HASH.fullmatch(row['sql_sha256'])):
                raise ValueError('Publication hold SQL identity is invalid.')
            if 'query_id' in row and (not isinstance(row['query_id'], str) or not row['query_id']):
                raise ValueError('Publication hold query identity is invalid.')
            if 'detail' in row and not isinstance(row['detail'], str):
                raise ValueError('Publication hold detail must be text.')
            if 'query_ids' in row and (not isinstance(row['query_ids'], list)
                                      or any(not isinstance(q, str) or not q for q in row['query_ids'])):
                raise ValueError('Publication hold source queries are invalid.')
    return document


def _read(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Publication evidence must use ordinary private files.')
    return json.loads(path.read_text(encoding='utf-8'))


def enforce_context(context, metrics=None, queries=None, links=None):
    """Reject held artifacts/links reintroduced after export; bind holds to source evidence."""
    root = Path(context).absolute()
    marker, path = root / 'metrics-source.json', root / FILE
    if not marker.exists() and not path.exists():
        return None  # Legacy hand-authored contexts have no engine review evidence.
    source = _read(marker) if marker.exists() else {}
    if not isinstance(source, dict):
        raise ValueError('Invalid metrics source evidence.')
    if source.get('module') != 'metrics' and not path.exists():
        return None
    if source.get('module') != 'metrics' or not path.is_file() or not source.get('publication_holds_sha256'):
        raise ValueError('Re-export the completed metrics run with structured publication holds.')
    document = validate(_read(path))
    if identity(document) != source['publication_holds_sha256'] or document['outputs'] != source.get('outputs'):
        raise ValueError('Publication hold evidence changed; re-export into a new context.')
    metrics = _read(root / 'metrics.json') if metrics is None else metrics
    queries = _read(root / 'queries.json') if queries is None else queries
    links = _read(root / 'links.json') if links is None else links
    if any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
           for rows in (metrics, queries, links)):
        raise ValueError('Publication context files must contain lists of objects.')
    metric_targets = {v for row in document['metrics'] for v in (row['name'], row['source_id'])}
    for kind, bodies in (('metrics', metrics), ('queries', queries)):
        held = document[kind]
        ids, names = {r['source_id'] for r in held}, {r['name'] for r in held}
        qids = {r['query_id'] for r in held if 'query_id' in r}
        sqls = {r['sql_sha256'] for r in held if 'sql_sha256' in r}
        for body in bodies:
            definition = body.get('definition') or {}
            if not isinstance(definition, dict) or not isinstance(definition.get('extensions', {}), dict):
                raise ValueError('Publication definitions and extensions must be objects.')
            evidence = (definition.get('extensions') or {}).get('metrics_agent') or {}
            if not isinstance(evidence, dict) or not isinstance(evidence.get('card', {}), dict):
                raise ValueError('Publication source evidence must be an object.')
            if kind == 'queries' and not isinstance(definition.get('sql', ''), str):
                raise ValueError('Publication SQL must be text.')
            original_id = (evidence.get('card') or {}).get('source_id')
            if kind == 'metrics' and any(isinstance(target, str) and target in metric_targets
                                         for target in definition.get('depends_on') or []):
                raise ValueError('A publication metric depends on a held artifact.')
            if (body.get('source_id') in ids or original_id in ids or body.get('name') in names
                    or evidence.get('vq_id') in qids
                    or (kind == 'queries' and sql_identity(definition.get('sql') or '') in sqls)):
                raise ValueError('A held artifact was reintroduced into the publication context.')
    held_ends = {('metric', r['name']) for r in document['metrics']} | {('sql_query', r['name']) for r in document['queries']}
    if any(not isinstance(link.get(end, {}), dict) for link in links for end in ('from', 'to')):
        raise ValueError('Publication link endpoints must be objects.')
    if any((link.get(end, {}).get('kind'), link.get(end, {}).get('name')) in held_ends
           for link in links for end in ('from', 'to')):
        raise ValueError('A publication link points to a held artifact.')
    return document
