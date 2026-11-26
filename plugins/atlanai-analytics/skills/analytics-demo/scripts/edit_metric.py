#!/usr/bin/env python3
"""edit_metric.py: change one metric or saved query in a run, by definition field.

    python3 edit_metric.py --run-dir <run> <request.json>

The request is {"name", "changes": [{"path": "<definition field, dotted>", "value"}]}, with each rule in plain words as
"before" and "after" for the card the result hands over ("note", "added" and "removed" as the card takes them). The old
definition is kept beside the run. A passing check updates the engine's copies and a failing check
restores the old rule. Prints one JSON object.
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "analytics-build" / "scripts"))
from project import Project, ProjectError, query_value  # noqa: E402
from context_state import digest, ordinary  # noqa: E402


def _select(run, name):
    names = []
    for kind, file in (('metric', 'metrics.json'), ('sql_query', 'queries.json')):
        path = run.context / file
        bodies = run.read(path) if path.is_file() else []
        for body in bodies:
            if name and (name in (body.get('name'), body.get('display_name')) or
                         kind == 'sql_query' and name == 'sql_query:' + body['name']):
                return kind, path, bodies, body
            names.append(body.get('name', ''))
    raise ProjectError('Select a metric or saved query in this run by name: ' + ', '.join(names) + '.')


def edit_metric(run, request):
    """Change one metric or query by definition field, as a proposal's changes name them. The
    old definition is kept beside the run and the new one must pass its check, or nothing changes."""
    name, changes = request.get('name', ''), [{**c, 'path': _field(c.get('path'))} for c in request.get('changes') or []
                                              if isinstance(c, dict)]
    kind, path, bodies, body = _select(run, name)
    metric = kind == 'metric'
    waiting = run.root / (PENDING if metric else 'edit-pending-query-%s.json' % digest(body['name']))
    if 'result' in request and not changes:
        return _finish_on_warehouse(run, kind, path, bodies, body, waiting, request['result'])
    if not changes:
        raise ProjectError('Name at least one change: {"path": "<definition field, dotted>", "value": ...}.')
    if not metric and waiting.exists():
        raise ProjectError("Send the saved query's pending warehouse result before editing it again.")
    old = body['definition']
    was = _checked(run, kind, body)
    checks = run.context / 'candidate-checks.json'
    prior = (run.read(checks) if not metric and checks.exists() else {}).get('sql_query:' + body['name'])
    old_text = {k: body.get(k) for k in ('description', 'display_name') if metric or k in body}
    version = int((old.get('version') if metric else
                   ((old.get('extensions') or {}).get('atlan_analytics') or {}).get('edit_version')) or 1)
    body['definition'] = copy.deepcopy(old)
    _apply_changes(body, changes, kind, version)
    # Old versions live beside the run, never in it: an agent answering from the run must not
    # find the old rule there.
    kept = _version_file(run, kind, body['name'], version)
    _keep(kept, {**old_text, 'definition': old})
    run.write(path, bodies)
    try:
        checked = (run.check_metric if metric else run.check_query)({'name': body['name']})
    except ValueError as error:
        _restore_rule(body, old, old_text)
        run.write(path, bodies)
        if not metric:
            _restore_query_check(run, body['name'], prior)
        raise ProjectError(f'The new rule failed its test, so the old rule stays: {error}')
    if isinstance(checked, dict) and checked.get('status') == 'run_on_warehouse':
        # A warehouse check only names its statement: the change waits for its rows before it counts or shows a card.
        run.write(waiting, {'name': body['name'], 'was': was, 'kept': str(kept), 'old_text': old_text,
                            'version': version, 'changes': changes, **({} if metric else {'prior_check': prior}),
                            'words': {k: request[k] for k in CARD_WORDS if request.get(k)}})
        again = "this metric's name again" if metric else '"name": ' + json.dumps('sql_query:' + body['name'])
        return {'status': 'run_on_warehouse', 'name': body['name'], 'version': version + 1, 'sql': checked.get('sql'),
                'next': f'Run `sql` with the warehouse\'s read-only tool, then send {again} with "result": '
                        'the rows it returned, or {"error": "<the tool\'s message>"}. The new rule counts, and its card '
                        'shows, only once it has run; a failure puts the old rule back.'}
    _mirror_edit(run, kind, body, changes, kept, version)
    return {**_edit_card(run, kind, body, request, old_text, was), 'status': 'ok', 'name': body['name'], 'version': version + 1,
            'previous': str(kept), 'changed': [c['path'] for c in changes]}


# A rule change on a warehouse, waiting for its statement's rows; and the card's own words a request may carry.
PENDING = 'edit-pending.json'
CARD_WORDS = ('before', 'after', 'note', 'added', 'removed')


def _finish_on_warehouse(run, kind, path, bodies, body, waiting, result):
    """Record the rows the new rule's statement returned on the warehouse: the change then counts and its card shows, or
    the statement failed and the old rule comes back. A change saved before it ran showed its old number on both sides
    of its card (2026-11-26)."""
    pending = run.read(waiting) if waiting.exists() else {}
    if pending.get('name') != body['name']:
        raise ProjectError('No rule change is waiting for its warehouse result: send the change first.')
    kept = _version_file(run, kind, body['name'], int(pending['version']))
    checked = (run.check_metric if kind == 'metric' else run.check_query)({'name': body['name'], 'result': result})
    if not (isinstance(checked, dict) and checked.get('passed')):
        record = run.read(run.context / 'candidate-checks.json').get(kind + ':' + body['name']) or {}
        _restore_rule(body, run.read(kept)['definition'], pending.get('old_text') or {})
        run.write(path, bodies)
        if kind == 'sql_query':
            _restore_query_check(run, body['name'], pending.get('prior_check'))
        waiting.unlink()
        why = '; '.join(str(e) for e in record.get('errors') or []) or 'it did not pass'
        raise ProjectError(f'The new rule failed its test on the warehouse, so the old rule stays: {why}')
    _mirror_edit(run, kind, body, pending['changes'], kept, pending['version'])
    waiting.unlink()
    return {**_edit_card(run, kind, body, pending.get('words') or {}, pending.get('old_text') or {}, pending.get('was')),
            'status': 'ok', 'name': body['name'], 'version': pending['version'] + 1, 'previous': str(kept),
            'changed': [c['path'] for c in pending['changes']]}


def _field(path):
    """A field named from the metric ("definition.formula") or from its definition ("formula"): the definition's own.
    Named from the metric, an edit nested a second definition inside the first (dry run F, 2026-11-25)."""
    path = str(path or '')
    return path[len('definition.'):] if path.startswith('definition.') else path


def _checked(run, kind, body):
    """The checked number for the current definition. Recheck locally when an older check kept no value."""
    path = run.context / 'candidate-checks.json'
    name = body['name']
    for attempt in (0, 1):
        check = (run.read(path) if path.exists() else {}).get(kind + ':' + name) or {}
        if check.get('definition_hash') != digest(body.get('definition') or {}) or kind == 'sql_query' and check.get('errors'):
            check = {}
        rows = (check.get('composition') or {}).get('rows') or []
        if kind == 'sql_query':
            if 'checked_value' in check:
                return check['checked_value']
            value = query_value(rows)
        else:
            value = next((v for v in (rows[0] if rows and isinstance(rows[0], list) else [])
                          if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
        if value is not None or attempt:
            return value
        try:
            (run.check_metric if kind == 'metric' else run.check_query)({'name': name})
        except ValueError:
            return None


def _edit_card(run, kind, body, request, old_text, was):
    """The card that shows the change, with the edit's own result: one number before and after, and each rule in the
    plain words the request gives (else the metric's description, when the change rewrote it)."""
    import side_view
    now = _checked(run, kind, body)
    if was is None or now is None:
        return {}
    label = body.get('display_name') or body['name']
    words = {k: request[k].strip() for k in ('before', 'after') if isinstance(request.get(k), str) and request[k].strip()}
    if not words and old_text.get('description') != body.get('description'):
        words = {'before': old_text.get('description') or '', 'after': body.get('description') or ''}
    extra = {k: request[k] for k in ('note', 'added', 'removed') if request.get(k)}
    data = {'metric': label, 'values': [[label, was, now]], **{k: v for k, v in words.items() if v}, **extra}
    return side_view.handed(side_view.show_card(run.root, 'edit', data))

# Where the engine keeps its own copy of a definition field (its card names a few differently).
CARD_FIELD = {'source.sql': 'sql', 'source.entity': 'entity', 'source.alias': 'alias'}

def _version_file(run, kind, name, version):
    # Query names can include dots and underscores. Keep them distinct in revision history.
    if kind == 'metric':
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    else:
        slug = re.sub(r'[^a-z0-9_.-]', '-', name)
    folder = 'metric-versions' if kind == 'metric' else 'query-versions'
    return ordinary(run.root.parent / folder / run.root.name / f'{slug}-v{version}.json')

def _keep(path, value):
    # Beside the run under .atlan-metrics/, outside the run's guarded store on purpose.
    path = ordinary(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1))

def _apply_card(card, changes):
    """Carry definition-field changes onto an engine card, which names a few fields differently."""
    if not isinstance(card, dict):
        return
    for change in changes:
        path = change['path'].removeprefix('extensions.metrics_agent.card.')
        field = CARD_FIELD.get(path, path)
        if '.' not in field and field in card:
            card[field] = change['value']


def _restore_query_check(run, name, prior):
    import progress
    path = run.context / 'candidate-checks.json'
    if path.is_file():
        checks = run.read(path)
        key = 'sql_query:' + name
        if prior is None:
            checks.pop(key, None)
        else:
            checks[key] = prior
        run.write(path, checks)
        progress.update_checks(run)


def _restore_rule(body, definition, old_text):
    body['definition'] = definition
    for key in ('description', 'display_name'):
        body.pop(key, None)
    body.update(old_text)


def _apply_query_fields(query, changes):
    """The fields on a verified query that its exported saved query shares."""
    if isinstance(query, dict):
        for change in changes:
            if change['path'] in ('sql', 'question', 'description'):
                query[change['path']] = change['value']


def _apply_changes(body, changes, kind, version):
    """Apply the same definition edits to the checked candidate and its engine export."""
    definition = body.setdefault('definition', {})
    for change in changes:
        keys = change['path'].split('.')
        if not all(keys) or keys[0] in ('version', 'spec_version'):
            raise ProjectError('A change names a definition field, description or display_name, never version or spec_version.')
        node = body if keys in (['description'], ['display_name']) else definition
        for key in keys[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                raise ProjectError('A dotted change must name a field inside a definition object.')
        node[keys[-1]] = change.get('value')
    if kind == 'metric':
        definition['version'] = version + 1
        _apply_card(((definition.get('extensions') or {}).get('metrics_agent') or {}).get('card'), changes)
    else:
        # Saved queries allow local edit revisions in extensions, not at the top of the typed definition.
        extensions = definition.setdefault('extensions', {})
        if not isinstance(extensions, dict) or not isinstance(extensions.setdefault('atlan_analytics', {}), dict):
            raise ProjectError('The saved query extensions must be objects.')
        extensions['atlan_analytics']['edit_version'] = version + 1
        engine = extensions.get('metrics_agent')
        _apply_query_fields(engine.get('evidence') if isinstance(engine, dict) else None, changes)


def _mirror_edit(run, kind, body, changes, kept, version):
    """Carry checked changes to the matching engine export and its metric card or verified query."""
    metric = kind == 'metric'
    before = {}
    export = run.root / 'engine-export' / ('metrics.json' if metric else 'queries.json')
    if export.is_file():
        bodies = run.read(export)
        for item in bodies:
            if item.get('name') == body['name']:
                before['export'] = copy.deepcopy(item)
                _apply_changes(item, changes, kind, version)
        if metric or 'export' in before:
            run.write(export, bodies)
    cards = run.root / 'engine' / 'metric_cards.json'
    if metric and cards.is_file():
        doc = run.read(cards)
        for card in doc.get('metrics') or []:
            if body['name'] in (card.get('name'), card.get('display_name')):
                before['card'] = copy.deepcopy(card)
                _apply_card(card, changes)
        run.write(cards, doc)
    elif not metric:
        _mirror_queries(run, body, changes, before)
    if before:
        _keep(kept.with_name(kept.stem + '-engine.json'), before)


def _mirror_queries(run, body, changes, before):
    from run_facts import RunFacts
    engine = RunFacts(run.root).engine
    if not engine.is_relative_to(run.root):
        return
    ext = (body['definition'].get('extensions') or {}).get('metrics_agent') or {}
    evidence = ext.get('evidence') if isinstance(ext, dict) else None
    query_id = (ext.get('vq_id') if isinstance(ext, dict) else None) or (evidence.get('vq_id') if isinstance(evidence, dict) else None)
    field, match = ('vq_id', query_id) if query_id else ('name', body['name'])
    for file in ('verified_queries.json', 'verified_queries.bi.json', 'verified_queries.history.json'):
        path = engine / file
        if path.is_file():
            layer = run.read(path)
            for query in layer.get('objects') or []:
                if query.get(field) == match:
                    before[file] = copy.deepcopy(query)
                    _apply_query_fields(query, changes)
                    run.write(path, layer)
                    break


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--run-dir", required=True)
    p.add_argument("request")
    a = p.parse_args(argv)
    try:
        run = Project(a.run_dir)
        print(json.dumps(edit_metric(run, json.loads(pathlib.Path(a.request).read_text()))))
        return 0
    except (ProjectError, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "message": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
