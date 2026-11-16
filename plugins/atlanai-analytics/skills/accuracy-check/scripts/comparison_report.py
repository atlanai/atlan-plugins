#!/usr/bin/env python3
"""Render a paired comparison with every question and its retained execution evidence.

Prepared or invalid runs have no accuracy headline. Complete runs may be persisted as
baseline evidence; hashes, model, effort and case coverage must match before reuse.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import sys

BUILD = Path(__file__).resolve().parents[2] / 'analytics-build/scripts'
sys.path.insert(0, str(BUILD))
import analytics_eval as scorer


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def h(value):
    return html.escape(str(value), quote=True)


def case_key(case_id):
    return hashlib.sha256(case_id.encode()).hexdigest()[:16]


def baseline_identity(manifest):
    """Recompute the cache key from its declared, metrics-independent inputs."""
    identity = {key: manifest[key] for key in ('format', 'scorer_sha256', 'inputs', 'model', 'effort', 'harness', 'protocol')}
    if manifest.get('format', 1) >= 2:
        identity['runtime_sha256'] = manifest['runtime_sha256']
    if manifest.get('format', 1) >= 3:
        identity.update(host=manifest['host'], execution_schema=manifest['execution_schema'])
    identity['inputs'] = {key: value for key, value in identity['inputs'].items() if key != 'metrics'}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_packets(directory, manifest):
    """Check the actual answering packets against the frozen comparison inputs."""
    if manifest.get('arms') != ['without_metrics', 'with_metrics']:
        raise ValueError('Comparison arms must be ordered without_metrics, then with_metrics')
    for arm in manifest['arms']:
        packet = directory / arm
        expected = {'schema': 'schema.json', 'history': 'history.jsonl'}
        if arm == 'with_metrics':
            expected['metrics'] = 'metrics.json'
        if not packet.is_dir() or {p.name for p in packet.iterdir()} != set(expected.values()):
            raise ValueError('Answering context packet is missing or contains unexpected files')
        for name, filename in expected.items():
            if sha(packet / filename) != manifest['inputs'][name]:
                raise ValueError('Answering context packet differs from frozen ' + name)


def codex_startup_warning(item):
    """Recognize the known non-executable unstable-feature startup notice only."""
    message = item.get('message') if isinstance(item, dict) else None
    return item.get('type') == 'error' and isinstance(message, str) and message.startswith(
        'Under-development features enabled: '
    )


def verified_record(folder, manifest, case_id, arm, gold):
    path = folder / 'record.json'
    if not path.is_file():
        return {'status': 'pending', 'error': 'This answering run has not completed.'}
    record = load(path)
    if record.get('case_id') != case_id or record.get('arm') != arm:
        raise ValueError('Execution record belongs to another question or arm')
    if record.get('status') != 'scored':
        return record
    if record.get('model') != manifest['model'] or record.get('effort') != manifest['effort']:
        raise ValueError('Execution model or effort differs from the comparison')
    evidence = record.get('evidence_hashes', {})
    required = {'events.jsonl', 'tools.jsonl', 'prompt.txt', 'launch.json', 'gold-execution.json'}
    if record.get('result_source') == 'controller_replay':
        required.add('final-sql-replay.json')
    elif record.get('result_source') not in ('agent_tool', None):
        raise ValueError('Unknown result source')
    if not required <= evidence.keys():
        raise ValueError('Missing raw execution evidence')
    for name, digest in evidence.items():
        if Path(name).name != name or not (folder / name).is_file() or sha(folder / name) != digest:
            raise ValueError('Execution evidence changed: ' + name)
    question = next(q['question'] for q in manifest['questions'] if q['id'] == case_id)
    if (folder / 'prompt.txt').read_text() != question:
        raise ValueError('Execution prompt differs from the question')
    if record.get('isolation') != 'capability_allowlist_and_trace_audit' or not record.get('session_id'):
        raise ValueError('Execution lacks a trace audit and native session id')
    # Cross-check the serialized score against the raw tool transcript and frozen gold.
    events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines() if line.strip()]
    tool_rows = [json.loads(line) for line in (folder / 'tools.jsonl').read_text().splitlines() if line.strip()]
    calls, final = [], None
    launch = load(folder / 'launch.json')
    if manifest.get('format', 1) >= 3 and launch.get('host') != manifest['host']:
        raise ValueError('Execution host differs from the comparison')
    if manifest.get('format', 1) >= 3 and launch.get('execution_schema') != manifest['execution_schema']:
        raise ValueError('Execution schema differs from the comparison')
    command = launch.get('command', [])
    if not isinstance(command, list) or manifest['model'] not in command:
        raise ValueError('Launch command does not identify the pinned model')
    if launch.get('host') == 'claude':
        effort_args = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == '--effort']
        if (manifest['effort'] == 'unsupported' and effort_args) or (manifest['effort'] != 'unsupported' and effort_args != [manifest['effort']]):
            raise ValueError('Launch effort differs from the comparison')
    elif launch.get('host') == 'codex':
        effort_args = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == '-c' and command[i + 1].startswith('model_reasoning_effort=')]
        if effort_args != ['model_reasoning_effort=' + json.dumps(manifest['effort'])]:
            raise ValueError('Launch effort differs from the comparison')
    if launch.get('host') == 'claude':
        init = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
        allowed = {'mcp__data__read_context', 'mcp__data__execute_sql'}
        if len(init) != 1 or set(init[0].get('tools', [])) != allowed or init[0].get('model') != manifest['model']:
            raise ValueError('Claude capabilities or model were not verified')
        for event in events:
            if event.get('type') == 'assistant':
                for block in event.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_use':
                        if block['name'] not in allowed:
                            raise ValueError('Unexpected answering tool')
                        calls.append((block['name'].removeprefix('mcp__data__'), block.get('input', {})))
            if event.get('type') == 'result' and not event.get('is_error'):
                final = event.get('result')
                if event.get('session_id') != record['session_id']:
                    raise ValueError('Native session id differs')
    elif launch.get('host') == 'codex':
        if 'native-transcript.jsonl' not in evidence:
            raise ValueError('Codex native transcript is missing')
        native = [json.loads(line) for line in (folder / 'native-transcript.jsonl').read_text().splitlines() if line.strip()]
        models = [e['payload'].get('model') for e in native if e.get('type') == 'turn_context']
        efforts = [e['payload'].get('effort') for e in native if e.get('type') == 'turn_context']
        if not efforts or set(efforts) != {manifest['effort']}:
            raise ValueError('Codex actual effort differs')
        if not models or set(models) != {manifest['model']}:
            raise ValueError('Codex actual model differs')
        if not any(e.get('type') == 'turn.completed' for e in events):
            raise ValueError('Codex turn did not complete')
        for event in events:
            if event.get('type') == 'item.completed':
                item = event['item']; kind = item.get('type')
                if kind == 'mcp_tool_call':
                    if item.get('server') != 'data' or item.get('tool') not in ('read_context', 'execute_sql'):
                        raise ValueError('Unexpected answering tool')
                    calls.append((item['tool'], item.get('arguments', {})))
                elif kind == 'agent_message':
                    final = item.get('text')
                elif kind == 'error' and codex_startup_warning(item):
                    continue
                elif kind not in ('reasoning', 'todo_list'):
                    raise ValueError('Unsupported executable event')
    else:
        raise ValueError('No supported raw transcript adapter for this host')
    encoded = lambda values: sorted(json.dumps(v, sort_keys=True) for v in values)
    if encoded(calls) != encoded([(r['tool'], r['arguments']) for r in tool_rows]):
        raise ValueError('Native transcript differs from tool audit')
    if not final:
        raise ValueError('Missing final model answer')
    receipt = load(folder / 'gold-execution.json')
    if (receipt.get('sql') != gold.get('gold_sql') or receipt.get('result') != record.get('gold_result')
            or receipt.get('database_sha256') != manifest['inputs']['data']
            or receipt.get('trusted_sha256') != manifest['inputs']['gold']
            or receipt.get('executor_sha256') != launch.get('server_sha256')):
        raise ValueError('Gold execution receipt differs from frozen data, trusted SQL, or saved result')
    decoder = json.JSONDecoder(); answers = []
    for index, char in enumerate(final):
        if char == '{':
            try:
                value, _ = decoder.raw_decode(final[index:])
                if isinstance(value, dict) and isinstance(value.get('sql'), str):
                    answers.append(value)
            except ValueError:
                pass
    if record['answer'] not in answers:
        raise ValueError('Saved answer differs from the native answer')
    matches = [r['result'] for r in tool_rows if r['tool'] == 'execute_sql' and r['arguments'].get('sql') == record['answer']['sql']]
    if record.get('result_source') == 'controller_replay':
        if matches:
            raise ValueError('Controller replay is recorded although the agent executed the final SQL verbatim')
        replay = load(folder / 'final-sql-replay.json')
        if (replay.get('sql') != record['answer']['sql'] or replay.get('result') != record['agent_result']
                or replay.get('database_sha256') != manifest['inputs']['data']
                or replay.get('executor_sha256') != launch.get('server_sha256')):
            raise ValueError('Final SQL replay receipt differs from the saved answer, result, or frozen inputs')
    elif not matches or matches[-1] != record['agent_result']:
        raise ValueError('Saved result differs from exact executed SQL')
    if arm == 'without_metrics' and any(r['tool'] == 'read_context' and r['arguments'].get('kind') == 'metrics' and 'text' in r['result'] for r in tool_rows):
        raise ValueError('Baseline read metrics')
    score = scorer.grade(record['gold_result'], record['agent_result'], gold['gold_sql'], record['answer']['sql'], scorer.case_sig(gold))
    if score != record['score']:
        raise ValueError('Saved grade differs from deterministic rescoring')
    return record


def trusted(directory, manifest):
    path = directory / 'trusted.jsonl'
    if sha(path) != manifest['inputs']['gold']:
        raise ValueError('Trusted SQL changed')
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    result = {r['case_id']: r for r in rows}
    if len(result) != len(rows) or set(result) != {q['id'] for q in manifest['questions']}:
        raise ValueError('Trusted SQL coverage differs from the comparison questions')
    return result


def collect(comparison):
    manifest = load(comparison / 'manifest.json')
    if manifest.get('baseline_key') != baseline_identity(manifest):
        raise ValueError('Comparison baseline identity does not match its inputs')
    verify_packets(comparison, manifest)
    gold = trusted(comparison, manifest)
    rows = []
    for question in manifest['questions']:
        row = {**question, 'arms': {}}
        for arm in manifest['arms']:
            folder = comparison / 'executions' / arm / case_key(question['id'])
            row['arms'][arm] = verified_record(folder, manifest, question['id'], arm, gold[question['id']])
        pair = list(row['arms'].values())
        if all(r['status'] == 'scored' for r in pair):
            if pair[0]['gold_result'] != pair[1]['gold_result']:
                raise ValueError('Trusted result differs between arms')
            a, b = [r['score']['grade']['pass'] for r in pair]
            row['outcome'] = 'win' if b > a else 'regression' if a > b else 'both_right' if a else 'both_wrong'
        else:
            row['outcome'] = 'pending'
        rows.append(row)
    complete = bool(rows) and all(r['outcome'] != 'pending' for r in rows)
    totals = {name: sum(r['outcome'] == name for r in rows)
              for name in ('win', 'regression', 'both_right', 'both_wrong', 'pending')}
    scores = {arm: sum(r['arms'][arm].get('score', {}).get('grade', {}).get('pass', 0) for r in rows)
              for arm in manifest['arms']} if complete else None
    return {'manifest': manifest, 'complete': complete, 'total': len(rows), 'scores': scores, 'totals': totals, 'rows': rows}


def persist_baseline(comparison, destination):
    report = collect(comparison)
    manifest = report['manifest']
    # Baseline completion is independent of the metrics arm, but never of case coverage.
    if not report['rows'] or any(r['arms']['without_metrics']['status'] != 'scored' for r in report['rows']):
        raise ValueError('Baseline is incomplete; no reusable baseline was saved')
    target = destination / manifest['baseline_key']
    if target.exists():
        cached = load(target / 'manifest.json')
        if cached.get('baseline_key') != baseline_identity(cached) or cached['baseline_key'] != manifest['baseline_key']:
            raise ValueError('Baseline cache identity differs')
        if cached.get('questions') != manifest['questions']:
            raise ValueError('Baseline cache question coverage differs')
        cache_gold = trusted(target, cached)
        if (target / 'without_metrics').is_dir() is False:
            raise ValueError('Baseline cache executions are missing')
        for row in report['rows']:
            verified_record(target / 'without_metrics' / case_key(row['id']), cached, row['id'], 'without_metrics', cache_gold[row['id']])
        return target
    destination.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='.baseline-', dir=destination) as temporary:
        stage = Path(temporary) / 'entry'
        stage.mkdir()
        shutil.copytree(comparison / 'executions/without_metrics', stage / 'without_metrics')
        shutil.copyfile(comparison / 'trusted.jsonl', stage / 'trusted.jsonl')
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        stage.rename(target)
    return target


def reuse_baseline(comparison, cache):
    manifest = load(comparison / 'manifest.json')
    if manifest.get('baseline_key') != baseline_identity(manifest):
        raise ValueError('Comparison baseline identity does not match its inputs')
    verify_packets(comparison, manifest)
    source = cache / manifest['baseline_key']
    cached = load(source / 'manifest.json')
    if cached.get('baseline_key') != baseline_identity(cached) or cached['baseline_key'] != manifest['baseline_key'] or cached['questions'] != manifest['questions']:
        raise ValueError('Baseline cache does not match')
    if any(cached['inputs'].get(key) != manifest['inputs'].get(key) for key in ('cases', 'gold', 'schema', 'history', 'data')):
        raise ValueError('Baseline cache frozen inputs do not match')
    cache_gold = trusted(source, cached)
    for question in manifest['questions']:
        record = verified_record(source / 'without_metrics' / case_key(question['id']), cached, question['id'], 'without_metrics', cache_gold[question['id']])
        if record['status'] != 'scored':
            raise ValueError('Baseline cache is incomplete')
    target = comparison / 'executions/without_metrics'
    if target.exists():
        raise ValueError('This comparison already has baseline attempts; preserve them')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / 'without_metrics', target)
    (comparison / 'baseline-reuse.json').write_text(json.dumps({'baseline_key': manifest['baseline_key'], 'source': str(source)}))
    return target


def table(result):
    if not result:
        return '<p class="muted">No executed result.</p>'
    if result.get('error'):
        return '<pre>' + h(result['error']) + '</pre>'
    columns, rows = result.get('columns', []), result.get('rows', [])
    head = ''.join('<th>' + h(c) + '</th>' for c in columns)
    body = ''.join('<tr>' + ''.join('<td>' + h(v) + '</td>' for v in r) + '</tr>' for r in rows)
    return '<div class="table"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>'


STYLE = '''
:root{--bg:#f4f4f6;--bg-elevated:#fff;--fg:#1b1f29;--fg-body:#34394b;--muted:#525c73;--muted-soft:#6a7692;--border:#eff1f5;--border-strong:#e0e4eb;--accent:#3c71df;--accent-hover:#225bd2;--accent-soft:#f0f7ff;--good:#00b28a;--good-soft:#f0fffc;--bad:#cc4747;--bad-soft:#fff0f0;--code-bg:#f6f7f9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg-body);font:15px/1.6 Inter,Calibri,system-ui,sans-serif}main{max-width:1150px;margin:0 auto;padding:40px 24px 90px}h1,h2,h3{color:var(--fg);line-height:1.3}h1{font-size:34px;margin:8px 0 14px}h2{margin-top:32px}.meta{text-transform:uppercase;font-size:11px;letter-spacing:.08em;color:var(--muted-soft)}.muted{color:var(--muted)}.cards,.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card,details{background:white;border:1px solid var(--border-strong);border-radius:6px;padding:20px;margin:14px 0}.number{display:block;font-size:32px;color:var(--fg);font-weight:650}.badge{font-size:12px;border-radius:4px;padding:3px 8px;background:var(--accent-soft);color:var(--accent);display:inline-block}.win{background:var(--good-soft);color:var(--good)}.regression{background:var(--bad-soft);color:var(--bad)}summary{cursor:pointer;font-weight:600;color:var(--fg)}summary .badge{margin-right:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--code-bg);color:var(--fg);font:12px/1.6 'JetBrains Mono',monospace;padding:14px;border-radius:4px;max-height:360px;overflow:auto}.table{max-height:340px;overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}td,th{text-align:left;border-bottom:1px solid var(--border);padding:8px;vertical-align:top}th{position:sticky;top:0;background:white}a{color:var(--accent)}button,select{font:inherit;border:1px solid var(--border-strong);padding:8px 12px;border-radius:4px;background:white;color:var(--fg);cursor:pointer}button:hover{border-color:var(--accent-hover)}textarea{width:100%;min-height:74px;padding:10px;border:1px solid var(--border-strong);border-radius:4px;font:inherit}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);padding:12px 0;z-index:2}.note{padding:15px;border-left:3px solid var(--accent);background:var(--accent-soft)}.flow{display:flex;align-items:center;flex-direction:column;gap:8px}.node{background:white;border:1px solid var(--border-strong);border-radius:6px;padding:12px 20px;width:340px;max-width:100%;text-align:center}.choice{border:2px solid var(--accent);background:var(--accent-soft);border-radius:30px}.branches{display:flex;gap:24px}.branches .node{width:260px}.arrow{color:var(--muted-soft)}.hidden{display:none}.evidence{font-size:12px;overflow-wrap:anywhere}.rule{border-left:3px solid var(--good);padding-left:12px}@media(max-width:700px){.cards,.pair{grid-template-columns:1fr}.branches{flex-direction:column;gap:8px}main{padding:22px 14px}h1{font-size:28px}}@media print{.toolbar,textarea,button{display:none}details{break-inside:avoid}}
'''


STYLE += '\nmain{max-width:1480px;padding:24px 24px 40px}h1{font-size:26px;margin:4px 0 16px}.environment{font-size:13px;line-height:1.65;padding-left:20px;margin:0 0 12px}.takeaway{font-size:13px;margin:12px 0;color:var(--fg)}.toolbar{position:static;padding:8px 0}.overview{overflow:auto;background:white;border:1px solid var(--border-strong);border-radius:6px}.overview table{font-size:12px;min-width:1060px}.overview td{vertical-align:middle;padding:12px 10px}.overview th{white-space:nowrap;padding:12px 10px}.qtext{white-space:nowrap;max-width:390px;overflow:hidden;text-overflow:ellipsis}.overview tr:hover{background:var(--accent-soft)}.actions{white-space:nowrap}.actions button{padding:3px 5px;border:0;background:transparent;color:var(--accent);font-size:12px}.reason{padding-left:14px;margin:0;max-width:290px;line-height:1.4}.pass{background:var(--good-soft);color:#00795e}.fail{background:var(--bad-soft);color:var(--bad)}dialog{width:min(1100px,94vw);max-height:88vh;overflow:auto;border:1px solid var(--border-strong);border-radius:6px;padding:24px;color:var(--fg-body)}dialog::backdrop{background:rgba(27,31,41,.45)}dialog header{display:flex;gap:16px;justify-content:space-between;align-items:start}dialog h2{font-size:20px;margin:0 0 16px}dialog nav{display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--border-strong);padding-bottom:12px;margin-bottom:16px}dialog nav [aria-pressed=true]{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}[hidden]{display:none!important}button:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}dialog pre{max-height:50vh}.close{flex-shrink:0}@media(max-width:700px){main{padding:18px 12px}.environment{font-size:12px}dialog{padding:16px}.pair{grid-template-columns:1fr}.qtext{max-width:310px}}\n'

def render(comparison, sources=None, diagnoses=None):
    report = collect(comparison)
    manifest = report['manifest']; n = report['total']
    source_map = {r['id']: r for r in sources or []}
    diagnoses = diagnoses or {}
    gold_rows = trusted(comparison, manifest)
    headline = (f"{report['scores']['without_metrics']}/{n} without metrics · "
                f"{report['scores']['with_metrics']}/{n} with metrics") if report['complete'] else 'Comparison in progress'
    rows, dialogs = [], []
    for index, row in enumerate(report['rows'], 1):
        ident = row['id']; diagnosis = diagnoses.get(ident, {}); source = source_map.get(ident, {})
        panels = {name: [] for name in ('sql', 'results', 'evidence')}
        verdicts, failed = [], []
        for arm, record in row['arms'].items():
            label = 'Without metrics' if arm == 'without_metrics' else 'With metrics'
            scored = record['status'] == 'scored'
            passed = scored and record['score']['grade']['pass']
            verdict = ('Correct' if passed else 'Incorrect') if scored else 'Unmeasured'
            verdicts.append(f'<td><span class="badge {"pass" if passed else "fail" if scored else ""}">{verdict}</span></td>')
            if scored and not passed:
                failed.append(label)
            panels['sql'].append(f'<section><h3>{label}</h3><pre>{h(record.get("answer", {}).get("sql", "Not executed"))}</pre></section>')
            panels['results'].append(f'<section><h3>{label} · {verdict}</h3>{table(record.get("agent_result"))}</section>')
            folder = 'executions/' + arm + '/' + case_key(ident)
            links = ''.join(f'<li><a href="{folder}/{file}">{title}</a></li>' for file, title in
                            [('events.jsonl','Transcript'),('tools.jsonl','Tool results'),('record.json','Score record'),('launch.json','Runtime configuration')]) if scored else ''
            result_source = 'Controller replay' if record.get('result_source') == 'controller_replay' else 'Agent tool execution' if scored else 'Not executed'
            panels['evidence'].append(f'<section><h3>{label}</h3><p>Result source: {result_source}</p><ul>{links}</ul><p>{h(record.get("error", ""))}</p></section>')
        note = diagnosis.get('explanation', 'Cause not yet diagnosed; inspect the executed results and metric rule.')
        brief = diagnosis.get('brief')
        if not failed:
            brief = 'No failures.' if row['outcome'] == 'both_right' else 'Awaiting valid execution.'
        elif not brief:
            brief = '; '.join(failed) + ': cause not yet diagnosed.'
        reason = '<ul class="reason"><li>' + h(brief) + '</li></ul>' if failed else '<span class="muted">' + h(brief) + '</span>'
        actions = ''.join(f'<button data-dialog="q-{index}" data-panel="{name}">{title}</button>' for name,title in [('sql','SQL'),('results','Results'),('why','Why'),('evidence','Evidence'),('feedback','Feedback')])
        rows.append(f'<tr class="question" data-outcome="{row["outcome"]}"><td class="qtext" title="{h(row["question"])}">{index}. {h(row["question"])}</td>{"".join(verdicts)}<td>{reason}</td><td class="actions">{actions}</td></tr>')
        golden = next((r.get('gold_result') for r in row['arms'].values() if r.get('gold_result')), None)
        tabs = ''.join(f'<button data-panel="{name}" aria-pressed="false">{title}</button>' for name,title in [('sql','SQL'),('results','Results'),('why','Why'),('evidence','Evidence'),('feedback','Feedback')])
        content = ''.join(f'<div data-content="{name}" hidden><div class="pair">{"".join(parts)}</div>' +
                          (f'<h3>Trusted SQL</h3><pre>{h(gold_rows[ident]["gold_sql"])}</pre>' if name == 'sql' else
                           f'<h3>Trusted result</h3>{table(golden)}' if name == 'results' else '') + '</div>' for name,parts in panels.items())
        dialogs.append(f'''<dialog id="q-{index}" aria-labelledby="title-{index}"><header><h2 id="title-{index}">{index}. {h(row['question'])}</h2><button class="close" aria-label="Close details">Close</button></header><nav aria-label="Question details">{tabs}</nav>{content}
<div data-content="why" hidden><p>{h(note)}</p><p class="rule">{h(diagnosis.get('metric_rule', ''))}</p><p class="muted">Source: {h(source.get('from', 'Frozen trusted-answer set'))}</p></div>
<div data-content="feedback" hidden><label for="feedback-{index}">Your feedback</label><textarea id="feedback-{index}" data-case="{h(ident)}" placeholder="Question wording, expected rule, or interpretation to revisit"></textarea><p class="muted">Saved in this browser. Use Export feedback to share.</p></div></dialog>''')
    reused = (comparison / 'baseline-reuse.json').is_file()
    hosts = sorted({load(p).get('host', 'unknown') for p in (comparison / 'executions').glob('*/*/launch.json')})
    environment = ', '.join(hosts) or manifest.get('host', 'Not yet executed')
    conclusion = diagnoses.get('_summary', 'Inspect each result before attributing a difference to metrics.')
    metadata = f'''<ul class="environment">
<li><strong>Environment:</strong> local SQLite demo; {h(environment)} CLI; fresh isolated agent per question and arm. {n} questions.</li>
<li><strong>Model:</strong> {h(manifest['model'])}; reasoning effort: {h(manifest['effort'])}. Same model and instructions in both runs.</li>
<li><strong>Context:</strong> both runs get schema + raw query history; only the with-metrics run gets metric definitions. Read-only SQL tools.</li>
<li><strong>Scorer:</strong> deterministic <code>analytics_eval</code>; executed results compared with trusted SQL, using each case’s numeric precision. Percent and fraction values are not equivalent.</li>
<li><strong>Baseline:</strong> {'reused verified evidence' if reused else 'fresh run, retained for reuse' if report['complete'] else 'not yet complete'}; questions, data, model, effort and runtime are pinned.</li>
<li><strong>Scope:</strong> source history overlaps the eval questions. This is a small reproduction test, not held-out accuracy.</li></ul>'''
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Metrics accuracy comparison</title><style>{STYLE}</style></head><body><main>
<p class="meta">Atlan Analytics · comparison review</p><h1>{h(headline)}</h1>{metadata}<p class="takeaway">{h(conclusion)}</p>
<div class="toolbar"><label for="filter">Show</label><select id="filter"><option value="all">All questions</option><option value="win">Extra exact matches</option><option value="regression">Regressions</option><option value="both_wrong">Both incorrect</option><option value="pending">Unmeasured</option></select><button data-dialog="workflow-modal">Workflow</button><button id="export">Export feedback</button></div>
<div class="overview"><table aria-label="Question comparison"><thead><tr><th>Question</th><th>Without metrics</th><th>With metrics</th><th>Why incorrect</th><th>Details</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
{''.join(dialogs)}<dialog id="workflow-modal" aria-labelledby="workflow-title"><header><h2 id="workflow-title">Evaluation workflow</h2><button class="close" aria-label="Close workflow">Close</button></header><p class="muted">Process nodes run automatically. Choice nodes are decisions for the person. This diagram describes the target demo flow.</p><div class="flow">
<div class="node"><span class="meta">Process</span><br>Generate or load source-backed evals</div><span class="arrow">↓</span>
<div class="node choice"><span class="meta">Choice</span><br>Run these questions?</div><span class="arrow">↓ Run</span>
<div class="node"><span class="meta">Process</span><br>Freeze questions, data, model and effort</div><span class="arrow">↓</span>
<div class="node choice"><span class="meta">Choice · when a baseline exists</span><br>Use saved baseline or another model/effort?</div><span class="arrow">↓</span>
<div class="branches"><div class="node"><span class="meta">Process</span><br>Without metrics<br><small>Reuse matching verified run, otherwise run fresh agents and persist</small></div><div class="node"><span class="meta">Process</span><br>With metrics<br><small>Same questions, model, effort and tools; add metrics only</small></div></div><span class="arrow">↓</span>
<div class="node"><span class="meta">Artifact</span><br>Per-question comparison<br><small>Wins, ties, regressions, SQL and evidence</small></div><span class="arrow">↓</span>
<div class="node choice"><span class="meta">Choice</span><br>Keep the report or improve a rule?</div><span class="arrow">↓ Improve</span>
<div class="node"><span class="meta">Process</span><br>Version the metric and rerun<br><small>Same questions keep their baseline. Changed questions require both arms again.</small></div></div></dialog>
<p class="evidence">Comparison {h(manifest['comparison_key'][:12])} · <a href="report.json">Structured report</a> · <a href="manifest.json">Frozen inputs &amp; scorer fingerprint</a>{' · <a href="replication-summary.json">Targeted repeats</a>' if (comparison / 'replication-summary.json').is_file() else ''}</p>
</main><script>
const prefix='atlan-comparison-'+{json.dumps(manifest['comparison_key'])};
const switchPanel=(dialog,name)=>{{dialog.querySelectorAll('[data-content]').forEach(p=>p.hidden=p.dataset.content!==name);dialog.querySelectorAll('nav [data-panel]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.panel===name));}};
document.querySelectorAll('[data-dialog]').forEach(b=>b.onclick=()=>{{const d=document.getElementById(b.dataset.dialog);switchPanel(d,b.dataset.panel||'sql');d.showModal();}});
document.querySelectorAll('dialog').forEach(d=>{{d.querySelector('.close').onclick=()=>d.close();d.querySelectorAll('nav [data-panel]').forEach(b=>b.onclick=()=>switchPanel(d,b.dataset.panel));d.addEventListener('click',e=>{{if(e.target===d){{const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}}});}});
document.querySelector('#filter').onchange=e=>document.querySelectorAll('.question').forEach(q=>q.hidden=e.target.value!=='all'&&q.dataset.outcome!==e.target.value);
document.querySelectorAll('textarea').forEach(t=>{{try{{t.value=localStorage.getItem(prefix+t.dataset.case)||''}}catch(e){{}}t.oninput=()=>{{try{{localStorage.setItem(prefix+t.dataset.case,t.value)}}catch(e){{}}}}}});
document.querySelector('#export').onclick=()=>{{const feedback=Object.fromEntries([...document.querySelectorAll('textarea')].map(t=>[t.dataset.case,t.value]));const url=URL.createObjectURL(new Blob([JSON.stringify({{comparison:prefix,feedback}},null,2)],{{type:'application/json'}}));const a=document.createElement('a');a.href=url;a.download='comparison-feedback.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}};
</script></body></html>'''
    (comparison / 'report.json').write_text(json.dumps(report, indent=2))
    (comparison / 'report.html').write_text(page)
    return comparison / 'report.html'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--comparison', type=Path, required=True)
    p.add_argument('--sources', type=Path)
    p.add_argument('--diagnoses', type=Path)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--persist-baseline', type=Path)
    group.add_argument('--reuse-baseline', type=Path)
    a = p.parse_args()
    try:
        if a.persist_baseline:
            print(persist_baseline(a.comparison, a.persist_baseline))
        elif a.reuse_baseline:
            print(reuse_baseline(a.comparison, a.reuse_baseline))
        else:
            sources = [json.loads(line) for line in a.sources.read_text().splitlines() if line.strip()] if a.sources else None
            print(render(a.comparison, sources, load(a.diagnoses) if a.diagnoses else None))
    except (ValueError, OSError, KeyError) as error:
        p.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
