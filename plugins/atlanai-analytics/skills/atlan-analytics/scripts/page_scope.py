"""Conservative report scope: stable measures plus separate page/visual contexts."""
import json
import re
from context_state import digest


def objects(value):
    if isinstance(value, str) and value[:1] in ('{', '['):
        try:
            value = json.loads(value)
        except ValueError:
            return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def pages(inventory):
    result = []
    for i, page in enumerate(inventory.get('pages', [])):
        visibility = str(page.get('visibility', '')).lower()
        page_type = str(page.get('type') or page.get('pageType') or page.get('pageBinding', {}).get('type', '')).lower()
        hidden = visibility in ('1', 'true', 'hidden', 'hiddeninviewmode')
        support = hidden or page_type in ('tooltip', 'drillthrough')
        result.append({**page, 'id': page.get('name') or 'page_' + str(i + 1),
                       'label': page.get('displayName') or page.get('name') or 'Page ' + str(i + 1),
                       'supporting': support, 'hidden': hidden})
    if len({p['id'] for p in result}) != len(result):
        raise ValueError('Page IDs are ambiguous. Review the report inventory.')
    return result


def select(inventory, requested=None):
    available = pages(inventory)
    measures = {(m['table'], m['name']): m for m in inventory.get('measures', [])}
    if not available:
        if requested not in (None, [], ['all']):
            raise ValueError('This export has model scope only; it has no report page layout.')
        missing = inventory.get('model_metadata_available') is False
        return {'mode': 'model', 'pages': [], 'measure_refs': [list(k) for k in sorted(measures)],
                'contexts': [], 'held': [{'reason': 'Model definitions are unavailable.'}] if missing else [],
                'coverage': 'partial' if missing else 'model_only'}
    requested = requested or ['all']
    selected = [p for p in available if not p['supporting']] if requested == ['all'] else []
    if requested != ['all']:
        for name in requested:
            matches = [p for p in available if name in (p['id'], p['label'])]
            if len(matches) != 1:
                raise ValueError('A requested page is missing or ambiguous. Select its exact page ID.')
            if matches[0] not in selected:
                selected.append(matches[0])
    found, contexts, held = set(), [], []
    if inventory.get('model_metadata_available') is False:
        held.append({'reason': 'Model definitions are unavailable; retain page selection and retrieve the linked model.'})
    columns = {(t['name'], c['name']) for t in inventory.get('tables', []) for c in t.get('columns', [])}
    names = {}
    for key in measures:
        names.setdefault(key[1], []).append(key)
    def resolve(table, name):
        if table and (table, name) in measures:
            return (table, name)
        candidates = names.get(name, []) if not table else []
        return candidates[0] if len(candidates) == 1 else None
    for page in selected:
        for index, visual in enumerate(page.get('visuals', [])):
            refs, missing = set(), set()
            aliases = {o['Name']: o['Entity'] for o in objects(visual)
                       if isinstance(o.get('Name'), str) and isinstance(o.get('Entity'), str)}
            for obj in objects(visual):
                measure = obj.get('Measure')
                if isinstance(measure, dict):
                    source = measure.get('Expression', {}).get('SourceRef', {})
                    table = source.get('Entity') or aliases.get(source.get('Source'))
                    if source.get('Source') and not table:
                        table = '<unresolved source alias>'
                    name = measure.get('Property')
                    key = resolve(table, name)
                    if key:
                        refs.add(key)
                    else:
                        missing.add(str(name or 'unresolved measure'))
                ref = obj.get('queryRef')
                if isinstance(ref, str):
                    matches = [k for k in measures if ref in ('.'.join(k), k[1])]
                    if len(matches) == 1:
                        refs.add(matches[0])
                    elif ref not in {'.'.join(k) for k in columns}:
                        missing.add('Unresolved projection: ' + ref)
            visual_id = visual.get('id') or 'visual_' + str(index + 1)
            if not refs and visual.get('type') not in ('textbox', 'shape', 'image', 'actionButton'):
                missing.add('No resolvable measure reference; review columns, filters and implicit aggregations.')
            if missing:
                held.append({'page_id': page['id'], 'visual_id': visual_id, 'reason': sorted(missing)})
            found.update(refs)
            contexts.append({'page_id': page['id'], 'visual_id': visual_id,
                             'measure_refs': [list(k) for k in sorted(refs)],
                             'report_filters': inventory.get('report_filters', []),
                             'page_filters': page.get('filters', []), 'visual_filters': visual.get('filters', []),
                             'projections': visual.get('projections', {})})
    # Measures hidden from report pages can still be dependencies. Columns are
    # not promoted to measures, and an unresolved reference never widens scope.
    pending = list(found)
    while pending:
        key = pending.pop()
        expression = measures[key].get('expression', '')
        if isinstance(expression, list):
            expression = '\n'.join(expression)
        expression = re.sub(r'"(?:[^"]|"")*"', '""', expression)
        for match in re.finditer(r"(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z_0-9]*))\s*)?\[([^\]]+)\]", expression):
            table = (match[1] or match[2] or '').replace("''", "'")
            name = match[3]
            dependency = resolve(table, name)
            if dependency and dependency not in found:
                found.add(dependency); pending.append(dependency)
            elif not dependency and (table or key[0], name) not in columns:
                held.append({'measure_ref': list(key), 'reason': 'Unresolved dependency: ' + name})
    result = {'mode': 'pages', 'pages': [p['id'] for p in selected],
              'measure_refs': [list(k) for k in sorted(found)], 'contexts': contexts, 'held': held,
              'coverage': 'partial' if held else 'resolved',
              'supporting_pages': [p['id'] for p in available if p['supporting']]}
    result['scope_hash'] = digest(result)
    return result
