"""Deterministic previews from actual candidates; never requires a workspace."""
import html
import json
from context_state import digest

STATUS_LABELS = {'draft': 'Draft', 'schema_checked': 'Schema checked; execution untested',
                 'executed': 'SQL executed for observed parameter samples',
                 'example_executed': 'Authored SQL example executed; formula equivalence not established',
                 'held': 'Held', 'released': 'Released in Atlan'}


def candidates(run):
    checks_path = run.context / 'candidate-checks.json'
    checks = run.read(checks_path) if checks_path.exists() else {}
    publication = run.manifest().get('publication', {}) if run.path.exists() else {}
    released = {(a['kind'], a['name'], a['definition_hash']) for a in publication.get('artifacts', [])}
    result = []
    for kind, file in (('metric', 'metrics.json'), ('sql_query', 'queries.json')):
        path = run.context / file
        bodies = run.read(path) if path.exists() else []
        if not isinstance(bodies, list):
            raise ValueError('Candidate files must contain artifact arrays.')
        for body in bodies:
            definition = body.get('definition', {})
            key = kind + ':' + body.get('name', '')
            revision = digest(definition)
            check = checks.get(key, {})
            status = 'draft'
            if check.get('definition_hash') == revision:
                status = check['status']
            if (kind, body.get('name'), revision) in released:
                status = 'released'
            evidence = definition.get('extensions', {}).get('atlan_analytics', {})
            result.append({'key': key, 'kind': kind, 'body': body, 'definition_hash': revision,
                           'status': status, 'evidence': evidence, 'check': check if check.get('definition_hash') == revision else {}})
    if len({c['key'] for c in result}) != len(result):
        raise ValueError('Candidate names are duplicated within a kind. Reconcile them before preview.')
    return sorted(result, key=lambda c: c['key'])


def choose(items, limit):
    if limit is None:
        return items
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose between 1 and 100 samples, or use --all.')
    # Alternate model and history metrics, without relabeling detail SQL as metrics.
    model = [c for c in items if c['evidence'].get('declared')]
    history = [c for c in items if c not in model]
    ordered = []
    while model or history:
        for group in (model, history):
            if group:
                ordered.append(group.pop(0))
    return ordered[:limit]


def display(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    import re
    # Treat source prose as text, including Markdown images and links.
    return re.sub(r'([\\`*_\[\]()!|#])', r'\\\1', html.escape(text, quote=False).replace('\n', ' '))


def sql_block(value):
    text = str(value)
    import re
    fence = '`' * max(3, 1 + max((len(m[0]) for m in re.finditer(r'`+', text)), default=0))
    return fence + 'sql\n' + text + '\n' + fence


def source_label(candidate):
    evidence = candidate['evidence']
    declared = evidence.get('declared') or {}
    parts = []
    if declared:
        parts.append(str(declared.get('kind') or 'Model') + ': '
                     + str(declared.get('name') or declared.get('ref') or 'declared measure'))
    if evidence.get('shapes'):
        count = len(evidence['shapes'])
        parts.append(f'Query history: {count} ' + ('pattern' if count == 1 else 'patterns'))
    return '; '.join(parts) or 'Source needs review'


def chat(run, limit=5):
    """Visible first preview, including both model meaning and history SQL when present."""
    items = candidates(run)
    metrics = [c for c in items if c['kind'] == 'metric']
    queries = [c for c in items if c['kind'] == 'sql_query']
    selected = choose(metrics, limit)
    if not items:
        return 'No metrics or saved queries are ready to preview. Author candidates from the surveyed sources first.\n'
    metrics_label = 'metric' if len(metrics) == 1 else 'metrics'
    query_label = 'saved query' if len(queries) == 1 else 'saved queries'
    lines = [f'Preview: {len(metrics)} {metrics_label} and {len(queries)} {query_label}.', '',
             'Local candidates are provisional until reconciled. Checks do not establish business certification or dashboard parity.', '']
    if selected:
        lines += [f'Showing {len(selected)} of {len(metrics)} metrics.', '',
                  '| Metric | Meaning | Calculation | Population / filters | Source | Check status |',
                  '| --- | --- | --- | --- | --- | --- |']
        for item in selected:
            body, definition = item['body'], item['body']['definition']
            cells = [body.get('display_name') or body['name'],
                     body.get('description') or 'Meaning needs review',
                     definition.get('formula') or definition.get('sql') or 'Not authored',
                     definition.get('mandatory_filters', definition.get('filters')) or 'None declared; inspect source population',
                     source_label(item), STATUS_LABELS[item['status']]]
            lines.append('| ' + ' | '.join(display(c) for c in cells) + ' |')
        lines.append('')
    # Prefer actual history evidence for the accompanying SQL, not a guessed query.
    history = [q for q in queries if q['evidence'].get('shapes')]
    chosen_queries = choose(history or queries, 1 if metrics else limit)
    for item in chosen_queries:
        body, definition = item['body'], item['body']['definition']
        lines += ['Saved SQL: ' + display(definition.get('question') or body.get('display_name') or body['name']), '',
                  'Source: ' + display(source_label(item)) + '. Status: ' + STATUS_LABELS[item['status']] + '.', '',
                  sql_block(definition.get('sql') or 'Not authored'), '']
        if definition.get('parameters'):
            lines += ['Parameters: ' + display(', '.join(p['name'] for p in definition['parameters'])), '']
    if not queries:
        lines += ['No saved SQL queries authored yet; the calculations above are metric expressions.', '']
    lines += ['You can inspect all definitions or a specific metric before continuing.', '']
    return '\n'.join(lines)


def markdown(run, limit=5, kind='metric'):
    items = [c for c in candidates(run) if c['kind'] == kind]
    selected = choose(items, limit)
    label = 'metrics' if kind == 'metric' else 'saved queries'
    if not selected:
        return f'No {label} are ready to preview. Complete source inspection and author candidates, then preview again. No Registry writes were made.\n'
    lines = [f'Showing {len(selected)} of {len(items)} {label}. Local candidates are provisional until destination reconciliation.', '']
    if limit and len(items) < limit:
        lines += [f'Only {len(items)} {label} are available; all are shown.', '']
    for candidate in selected:
        body, evidence = candidate['body'], candidate['evidence']
        definition = body['definition']
        label = STATUS_LABELS[candidate['status']]
        lines += [f"### {display(body.get('display_name') or body['name'])}", '',
                  f"Status: {label}. Schema checks and SQL execution do not establish dashboard parity.", '',
                  display(body.get('description') or definition.get('question') or 'Meaning needs review.'), '',
                  'Calculation / SQL:', '', sql_block(definition.get('sql') or definition.get('formula') or 'Not authored'), '',
                  'Grain: ' + display(definition.get('grain', 'Not specified')) + '. Unit: ' + display(definition.get('unit') or definition.get('output', {}).get('unit') or 'Not specified') + '.', '',
                  'Dimensions: ' + display(definition.get('dimensions', [])), '',
                  'Filters: ' + display(definition.get('mandatory_filters', definition.get('filters', []))), '',
                  'Source evidence: ' + display(evidence or body.get('source_id') or 'Needs review'), '']
        if candidate['check'].get('errors'):
            lines += ['Held: ' + display(candidate['check']['errors']), '']
    return '\n'.join(lines)


def html_snapshot(run, progress):
    cards = []
    for item in candidates(run):
        body = item['body']
        definition = body['definition']
        evidence = item['evidence']
        declared = evidence.get('declared') or {}
        provenance = []
        if declared:
            provenance.append(str(declared.get('name', 'Declared measure')) + ' from ' + str(declared.get('ref', declared.get('kind', 'model'))))
        if evidence.get('shapes'):
            provenance.append('Query history: ' + ', '.join(str(s) for s in evidence['shapes']))
        if evidence.get('mapping'):
            provenance.append(str(evidence['mapping']))
        dimensions = definition.get('dimensions', [])
        dimension_labels = ', '.join(d.get('name', '') if isinstance(d, dict) else str(d) for d in dimensions)
        grain = definition.get('grain') or 'No grouping specified'
        if isinstance(grain, list):
            grain = ', '.join(str(g) for g in grain)
        detail = [('Grain', grain),
                  ('Unit', definition.get('unit') or definition.get('output', {}).get('unit') or 'Not specified'),
                  ('Dimensions', dimension_labels or 'None declared'),
                  ('Filters', definition.get('mandatory_filters', definition.get('filters')) or 'None declared'),
                  ('Source evidence', '; '.join(provenance) or body.get('source_id') or 'Needs review')]
        description = body.get('description') or definition.get('question') or 'Meaning needs review.'
        cards.append('<details><summary><strong>' + html.escape(body.get('display_name') or body['name'])
                     + '</strong><br><small>' + html.escape(STATUS_LABELS[item['status']]) + '</small></summary><p>'
                     + html.escape(description) + '</p><pre>'
                     + html.escape(str(definition.get('sql') or definition.get('formula') or 'Not authored')) + '</pre><dl>'
                     + ''.join('<dt>' + label + '</dt><dd>' + html.escape(value if isinstance(value, str)
                                else json.dumps(value, ensure_ascii=False)) + '</dd>' for label, value in detail)
                     + '</dl></details>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>Analytics setup progress</title><style>body{max-width:960px;margin:40px auto;padding:24px;'
            'font:16px system-ui;background:#f8fafc;color:#17243b}details{background:white;padding:18px;margin:12px 0;'
            'border:1px solid #dbe3ed;border-radius:12px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
            'small{color:#405570}dt{font-weight:600;margin-top:10px}dd{margin:4px 0;overflow-wrap:anywhere}'
            'a{color:#2155b9}</style><h1>Analytics setup</h1><p>'
            + html.escape(progress['summary']) + '</p><p>Snapshot. Regenerate after the next stage, then '
            '<a href="">refresh this view</a>. Continue decisions in chat.</p>' + ''.join(cards) + '</html>')
