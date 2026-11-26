#!/usr/bin/env python3
"""edit_metric.py: change one metric in a demo run, by definition field, as a fix names it.

    python3 edit_metric.py --run-dir <run> <request.json>

The request is {"name", "changes": [{"path": "<definition field, dotted>", "value"}]}, with each rule in plain words as
"before" and "after" for the card the result hands over ("note", "added" and "removed" as the card takes them). The old
definition is kept beside the run, never in it, the version goes up, the metric is checked again
and the engine's own copies follow; a failing check keeps the old rule. Prints one JSON object.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "analytics-build" / "scripts"))
from project import Project, ProjectError  # noqa: E402
import layout  # noqa: E402

def edit_metric(run, request):
    """Change one metric in this run by definition field, as a proposal's changes name them. The
    old definition is kept beside the run and the new one must pass its check, or nothing changes."""
    name, changes = request.get('name', ''), [{**c, 'path': _field(c.get('path'))} for c in request.get('changes') or []
                                              if isinstance(c, dict)]
    path = run.context / 'metrics.json'
    bodies = run.read(path) if path.exists() else []
    body = next((b for b in bodies if name in (b.get('name'), b.get('display_name'))), None)
    if not body:
        raise ProjectError('Select a metric in this run by name: ' + ', '.join(b.get('name', '') for b in bodies) + '.')
    if not changes:
        raise ProjectError('Name at least one change: {"path": "<definition field, dotted>", "value": ...}.')
    old = json.loads(json.dumps(body['definition']))
    new = json.loads(json.dumps(old))
    was = _checked(run, body['name'])
    old_text = {k: body.get(k) for k in ('description', 'display_name')}
    for change in changes:
        keys = str(change.get('path') or '').split('.')
        if keys[0] in old_text and len(keys) == 1:
            # The metric's own words: agents read them beside the rule, so they change with it.
            body[keys[0]] = change.get('value')
            continue
        if not keys[0] or keys[0] in ('version', 'spec_version'):
            raise ProjectError('A change names a definition field, description or display_name, never version or spec_version.')
        node = new
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = change.get('value')
    _apply_card(((new.get('extensions') or {}).get('metrics_agent') or {}).get('card'), changes)
    version = int(old.get('version') or 1)
    new['version'] = version + 1
    # Old versions live beside the run, never in it: an agent answering from the run must not
    # find the old rule there.
    root = run.context.parent
    kept = root.parent / 'metric-versions' / root.name / f"{re.sub(r'[^a-z0-9]+', '-', body['name'].lower()).strip('-')}-v{version}.json"
    _keep(kept, {**old_text, 'definition': old})
    body['definition'] = new
    run.write(path, bodies)
    try:
        run.check_metric({'name': body['name']})
    except ValueError as error:
        body['definition'] = old
        body.update(old_text)
        run.write(path, bodies)
        raise ProjectError(f'The new rule failed its test, so the old rule stays: {error}')
    _mirror_metric_edit(run, body['name'], changes, kept, version)
    return {**_edit_card(run, body, request, old_text, was), 'status': 'ok', 'name': body['name'], 'version': version + 1,
            'previous': str(kept), 'changed': [c['path'] for c in changes]}


def _field(path):
    """A field named from the metric ("definition.formula") or from its definition ("formula"): the definition's own.
    Named from the metric, an edit nested a second definition inside the first (dry run F, 2026-11-25)."""
    path = str(path or '')
    return path[len('definition.'):] if path.startswith('definition.') else path


def _checked(run, name):
    """The metric's checked value: the first number its check worked out, checking it now when it never was."""
    path = run.context / 'candidate-checks.json'
    for attempt in (0, 1):
        check = (run.read(path) if path.exists() else {}).get('metric:' + name) or {}
        rows = (check.get('composition') or {}).get('rows') or []
        value = next((v for v in (rows[0] if rows and isinstance(rows[0], list) else [])
                      if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
        if value is not None or attempt:
            return value
        try:
            run.check_metric({'name': name})
        except ValueError:
            return None


def _edit_card(run, body, request, old_text, was):
    """The card that shows the change, with the edit's own result: one number before and after, and each rule in the
    plain words the request gives (else the metric's description, when the change rewrote it)."""
    import side_view
    now = _checked(run, body['name'])
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

def _keep(path, value):
    # Beside the run under .atlan-analytics/, outside the run's guarded store on purpose.
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

def _mirror_metric_edit(run, name, changes, kept, version):
    """The engine keeps two more copies of each metric beside the context: its export and its
    card. An agent that searches the run can read either, so an edit reaches them too, with
    their old text kept beside the old definition."""
    root = run.context.parent
    export, cards = root / 'engine-export' / 'metrics.json', root / 'engine' / 'metric_cards.json'
    before = {}
    if export.is_file():
        bodies = run.read(export)
        for b in bodies:
            if b.get('name') == name:
                before['export'] = json.loads(json.dumps(b))
                for change in changes:
                    keys = change['path'].split('.')
                    node = b if keys[0] in ('description', 'display_name') and len(keys) == 1 else b.setdefault('definition', {})
                    for key in keys[:-1]:
                        node = node.setdefault(key, {})
                    node[keys[-1]] = change['value']
                b.setdefault('definition', {})['version'] = version + 1
                _apply_card((((b.get('definition') or {}).get('extensions') or {}).get('metrics_agent') or {}).get('card'), changes)
        run.write(export, bodies)
    if cards.is_file():
        doc = run.read(cards)
        for card in doc.get('metrics') or []:
            if name in (card.get('name'), card.get('display_name')):
                before['card'] = json.loads(json.dumps(card))
                _apply_card(card, changes)
        run.write(cards, doc)
    if before:
        _keep(kept.with_name(kept.stem + '-engine.json'), before)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--run-dir", required=True)
    p.add_argument("request")
    a = p.parse_args(argv)
    try:
        run = Project(layout.follow(a.run_dir))
        print(json.dumps(edit_metric(run, json.loads(pathlib.Path(a.request).read_text()))))
        return 0
    except (ProjectError, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "message": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
