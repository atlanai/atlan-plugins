"""Read-only workbench layout and exact-identity focus. No runtime or network actions."""
import html


def focus_sample(model, focus=None):
    if focus is not None:
        candidates = list(model['items'])
        if model.get('sample') and model['sample'] not in candidates:
            candidates.append(model['sample'])
        matches = [i for i in candidates if focus in (i.get('id'), i.get('source_id'))]
        model['sample'] = matches[0] if len(matches) == 1 else None
        if model['sample'] is None:
            model['warnings'].append('The selected metric is unavailable or ambiguous in this snapshot. The focus was not changed.')
            model['next_step'] = 'Locate the selected metric in the saved definitions before continuing.'
            model['next_prompt'] = 'Find the selected metric in this saved run before continuing.'
    sample = model.get('sample')
    model['focus'] = sample['id'] if sample else focus
    if (sample and sample['kind'] == 'metric' and sample['status'] == 'released'
            and model['phase']['code'] == 'ready' and model['phase']['status'] in ('complete', 'partial_ready') and not model['warnings']):
        model['next_prompt'] = (model['next_prompt'] + ' Using the published definition for ' + sample['name']
                                + ', suggest a supported question. Retrieve its published context before answering '
                                + 'and execute through the selected data path. Exclude held definitions.')
    return model


def enhance(page, model):
    """Reuse the escaped renderer and its CSP-hashed, read-only controls."""
    esc = lambda value: html.escape(str(value), quote=True)
    style = """
*{box-sizing:border-box}.workbench{display:block}.workbench aside{margin:16px 0}
nav{margin:16px 0;font-size:.875rem}nav span+span:before{content:" / ";white-space:pre}
nav [aria-current=step]{font-weight:600}.eyebrow{font-size:.875rem}
.revision{font:12px ui-monospace,monospace;overflow-wrap:anywhere}
.collection{margin:24px 0}.notice{border-inline-start:2px solid;padding-inline-start:16px}
"""
    page = page.replace('</style>', style + '</style>', 1)
    stage = model['phase']['code']
    milestone = 'Discover' if stage in ('inspect', 'scope') else 'Use' if stage in ('publish', 'ready') else 'Understand'
    nav = '<nav aria-label="Current journey stage">' + ''.join(
        '<span' + (' aria-current="step"' if name == milestone else '') + '>' + name + '</span>'
        for name in ('Discover', 'Understand', 'Use')) + '</nav>'
    page = page.replace('<main>', '<main><div class="eyebrow">Analytics</div>', 1)
    page = page.replace('</small>', '</small>' + nav, 1)
    page = page.replace('<p>' + esc(model['next_step']) + '</p>', '', 1)
    sample = model.get('sample')
    if sample:
        location = 'Saved in Atlan' if sample['status'] == 'released' else 'Local, not published'
        sql = sample.get('sql') or sample.get('calculation') or ''
        focus = ('<article id="focus-metric"><div class="eyebrow">' + location + '</div><h2>' + esc(sample['name'])
                 + '</h2><p>' + esc(sample['meaning']) + '</p><p class="status">' + esc(sample['status_label'])
                 + '</p><p><strong>Includes:</strong> ' + esc(sample.get('population', 'Review source scope'))
                 + '</p><details><summary>Inspect calculation, SQL and source</summary><pre>' + esc(sample.get('calculation', ''))
                 + '</pre><h3>' + esc(sample.get('sql_label', 'SQL')) + '</h3><pre>' + esc(sql) + '</pre><p>'
                 + esc('; '.join(sample['provenance'])) + '</p>'
                 + ''.join('<p>' + esc(n) + '</p>' for n in sample.get('detail_notes', []))
                 + '<p>Saved evidence, not fresh execution or proof of dashboard parity.</p><p class="revision">Identity: '
                 + esc(model.get('focus', sample['id'])) + '<br>Definition revision: '
                 + esc(sample.get('definition_hash', 'not recorded')) + '</p></details></article>')
    else:
        focus = '<article><h2>No selected definition yet</h2><p>' + esc(model['next_step']) + '</p></article>'
    holds = model.get('holds', [])
    held = ('<div class="notice"><strong>Still needs attention</strong><p>' + str(model['counts']['held'])
            + ' items need repair before publication.</p>' + ''.join('<p>' + esc(h['name']) + ': '
            + esc(h['reason'].replace('_', ' ')) + '</p>' for h in holds[:100]) + '</div>') if model['counts']['held'] else ''
    workbench = ('<div class="workbench"><section aria-label="Selected metric">' + focus + held
                 + '</section><aside><h2>What happens next</h2><p>' + esc(model['next_step'])
                 + '</p><p class="muted">Scope changes, pauses and publication happen in chat. This snapshot only lets you inspect saved evidence.</p></aside></div>')
    marker = '<label for="filter">'
    page = page.replace(marker, workbench + '<details class="collection"><summary>Browse saved definitions</summary>' + marker, 1)
    page = page.replace('<section class="summary"><h2>Continue in chat</h2>', '</details><section class="summary"><h2>Continue in chat</h2>', 1)
    return page
