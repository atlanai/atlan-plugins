"""Validate captured MCP receipts and record a resumable publication without model copying.

No network or Atlan Trident writes. New runs still discover live definitions and reconcile them.
"""
import argparse
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat

import context_contracts
import progress
from project import Project
from retrieval import releases

MAX_BYTES = 256 * 1024
MAX_FILES_PER_SEGMENT = 128
READ_BATCH_SIZE = 8
# The runner shares about 80 KB across the batch. Leave room for its envelope and
# estimate from the checked bodies, which the native response quotes as JSON text.
READ_BATCH_BYTES = 64 * 1024
READ_REPLY_OVERHEAD = 2048


def read(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Receipt files must be bounded ordinary files inside this run.')
    # Windows lacks both flags; the symlink and regular-file checks still hold there.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Receipt is not an ordinary file.')
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Receipt exceeds the size limit.')
    return data


def documents(directory, *, native=False):
    segments = sorted(islice(directory.glob('part-*'), 257))
    if len(segments) > 256:
        raise ValueError('Too many receipt segments.')
    for segment in segments:
        if (not re.fullmatch(r'part-[0-9a-f]{2}', segment.name)
                or segment.is_symlink() or not segment.is_dir()):
            raise ValueError('Invalid receipt segment.')
    # Native receipts always live in segments. Hosts can keep their scratch input in the
    # receipt directory without making it part of the evidence. Legacy receipts can be flat.
    for segment in (segments if native else (directory, *segments)):
        paths = sorted(islice(segment.glob('*.json'), MAX_FILES_PER_SEGMENT + 1))
        if len(paths) > MAX_FILES_PER_SEGMENT:
            raise ValueError('Too many files in a receipt segment.')
        for path in paths:
            data = read(path)
            digest = hashlib.sha256(data).hexdigest()
            if path.name != digest + '.json' or segment != directory and segment.name != 'part-' + digest[:2]:
                raise ValueError('A receipt is incomplete or changed; read it back again, without repeating writes.')
            value = json.loads(data)
            if (set(value) != {'format', 'request', 'response'} or value['format'] != 1
                    or not isinstance(value['request'], dict) or not isinstance(value['response'], dict)):
                raise ValueError('Unsupported receipt format.')
            yield value


def readback_batches(calls, receipt_label=None, estimates=None):
    estimates = estimates or {}
    batches, pending, size = [], [], 0
    for call in calls:
        args = {k: v for k, v in call['arguments'].items() if k != 'receipt_dir'}
        estimated = estimates.get(args.get('artifact_id'), READ_REPLY_OVERHEAD)
        if pending and (len(pending) == READ_BATCH_SIZE or size + estimated > READ_BATCH_BYTES):
            batches.append({'calls': pending, **({'receipt_dir': receipt_label} if receipt_label else {})})
            pending, size = [], 0
        pending.append({'name': call['name'], 'arguments': args})
        size += estimated
    if pending:
        batches.append({'calls': pending, **({'receipt_dir': receipt_label} if receipt_label else {})})
    return batches


def slot_matches(kind, item, artifact):
    """Whether a bulk create's ok slot is the item its request sent: the slot is the artifact's
    handle (`id`, `name`, version, status) unless the call sent `full`, so it is matched on its
    name and its id's kind prefix, and any identity field a whole row carries must match too.
    The receipt hook (`command-hooks/receipt_store.py`) checks the same before saving."""
    if not isinstance(artifact, dict) or not isinstance(artifact.get('id'), str):
        return False
    expected = {'kind': kind, **item}
    return (artifact['id'].startswith(kind + '_') and artifact.get('name') == item.get('name')
            and all(artifact[k] == v for k, v in expected.items() if k in artifact))


def assemble(run_dir, receipt_dir, workspace, finalize=False, *, native=False, skip_lookup=()):
    run = Path(run_dir).absolute()
    directory = Path(receipt_dir).absolute()
    if not directory.is_relative_to(run) or any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('Select receipts inside the existing run, without symbolic links.')
    project = Project(run)
    context = project.context
    plan = json.loads((context / 'plan.json').read_text())
    bodies = {lane: json.loads((context / (lane + '.json')).read_text()) for lane in ('metrics', 'queries', 'links')}
    context_contracts.check_publication_holds(context, bodies['metrics'], bodies['queries'], bodies['links'])
    expected = {}
    for lane, kind in (('metrics', 'metric'), ('queries', 'sql_query')):
        by_source = {b['source_id']: b for b in bodies[lane]}
        for item in plan[lane]:
            if item['ok']:
                body = by_source[item['source_id']]
                expected[(kind, body['source_id'])] = (body, item.get('match_id'))
    artifacts, creates, edge_reads = {}, {}, {}
    failures, recovered, lookup_receipts = {}, {}, []
    for entry in documents(directory, native=native):
        request, response = entry['request'], entry['response']
        name, args = request.get('name'), request.get('arguments', {})
        if name == 'registry__create_artifacts':
            slots = response.get('items')
            items = args.get('items', [])
            if not isinstance(slots, list) or (not native and len(slots) != len(items)):
                raise ValueError('An incomplete bulk response requires live reconciliation.')
            indices = set()
            for slot in slots:
                index = slot.get('index')
                if type(index) is not int or not 0 <= index < len(items) or index in indices:
                    raise ValueError('Invalid bulk receipt index.')
                indices.add(index)
                sent = items[index]
                if native:
                    key = (args['kind'], sent.get('source_id'))
                    if key not in expected or sent.get('workspace_id') != workspace or (
                            sent.get('name') != expected[key][0].get('name')):
                        raise ValueError('A create receipt is outside the checked plan.')
                    if slot.get('ok') is False:
                        code = slot.get('code')
                        if code in ('conflict', 'invalid_arguments', 'forbidden', 'not_found', 'unknown_kind'):
                            failures[key] = code
                if slot.get('ok') is True:
                    actual = slot.get('artifact')
                    if not slot_matches(args['kind'], sent, actual):
                        raise ValueError('A create receipt differs from its request.')
                    # A slot is the artifact's handle, so its kind, source and workspace are the
                    # request's: slot_matches held any the slot carries to them.
                    key = (args['kind'], sent.get('source_id'))
                    if key in expected and sent.get('workspace_id') == workspace:
                        if key in creates and creates[key] != actual['id']:
                            raise ValueError('Conflicting create identities; reconcile live state.')
                        creates[key] = actual['id']
        elif native and name == 'registry__list_artifacts':
            lookup_receipts.append((args, response['items']))
        elif name == 'registry__get_artifact':
            if response.get('id') != args.get('artifact_id') or not (args.get('channel') == 'latest' or args.get('release_id')):
                raise ValueError('A head or mismatched read cannot establish publication.')
            normalized = releases({'workspace_id': workspace, 'artifacts': [response]})[0]
            key = (normalized['kind'], response.get('source_id'))
            if key not in expected:
                raise ValueError('A receipt is outside the checked publication plan.')
            body, match = expected[key]
            if (match and normalized['id'] != match or response['metadata']['definition'] != body['definition']
                    or any(response.get(k) != body.get(k) for k in ('name', 'display_name', 'description', 'source_id'))):
                raise ValueError('A released readback differs from the checked definition or identity.')
            if args.get('channel') and response['release'].get('channel') != args['channel'] or args.get('release_id') and response['release'].get('release_id') != args['release_id']:
                raise ValueError('A release receipt differs from its requested selector.')
            if key in artifacts:
                old = artifacts[key]
                if old['id'] != response['id'] or old['release']['release_id'] != response['release']['release_id']:
                    raise ValueError('Conflicting released revisions; reconcile before resuming.')
            artifacts[key] = response
        elif name == 'registry__get_related_artifacts':
            if not isinstance(response.get('outgoing'), list) or not isinstance(response.get('incoming'), list):
                raise ValueError('Incomplete relationship receipt.')
            edge_reads.setdefault(args['artifact_id'], []).append(response)
        else:
            raise ValueError('Unsupported receipt operation.')
    expected_names = {(key[0], entry['name']): key for key, (entry, _) in expected.items()}
    attempted_lookups = set()
    for args, rows in lookup_receipts:
        key = expected_names.get((args.get('kind'), args.get('name')))
        if key is None or args.get('workspace_id') != workspace or args.get('limit') != 2:
            raise ValueError('An inventory receipt is outside the checked plan.')
        attempted_lookups.add(key)
        matching = [row['id'] for row in rows if row.get('name') == args['name']
                    and row.get('workspace_id') == workspace
                    and row.get('source_id', key[1]) == key[1]
                    and row.get('id', '').startswith(key[0] + '_')]
        if len(matching) == 1:
            if key in recovered and recovered[key] != matching[0]:
                raise ValueError('Several artifact identities match a recovered item.')
            recovered[key] = matching[0]
    if set(failures) & (set(creates) | set(artifacts)):
        raise ValueError('Conflicting create outcomes require reconciliation.')
    for key, ident in creates.items():
        if key in artifacts and artifacts[key]['id'] != ident:
            raise ValueError('The release readback does not belong to the recorded create.')
    missing = [body['name'] for key, (body, _) in expected.items() if key not in artifacts and key not in failures]
    by_name = {(a['kind'], a['name']): a['id'] for a in artifacts.values()}
    missing_links, held_links, link_writes = [], [], []
    for link in plan['links']:
        if not link['ok']:
            continue
        source_name = (link['from']['kind'], link['from']['name'])
        target_name = (link['to']['kind'], link['to']['name'])
        source = by_name.get(source_name)
        target = by_name.get(target_name)
        edge_key = {'from': link['from'], 'to': link['to'], 'rel_type': link['rel_type']}
        if expected_names.get(source_name) in failures or expected_names.get(target_name) in failures:
            held_links.append({**edge_key, 'reason': 'An endpoint was held after a create refusal.'})
            continue
        def proves(snapshot):
            return any(e.get('rel_type') == link['rel_type'] and e.get('to', {}).get('id') == target
                       and all(e.get('attributes', {}).get(k) == v for k, v in link.get('attributes', {}).items())
                       for e in snapshot['outgoing'])
        snapshots = edge_reads.get(source, [])
        if not source or not target or not any(proves(s) for s in snapshots):
            if native and source and target and snapshots and link['rel_type'] in ('source_of', 'uses_metric'):
                if any(e.get('rel_type') == link['rel_type'] and e.get('to', {}).get('id') == target
                       for snapshot in snapshots for e in snapshot['outgoing']):
                    held_links.append({**edge_key, 'reason': 'An existing relationship has different attributes.'})
                    continue
                link_writes.append({'artifact_id': source, 'subject_id': target, 'rel_type': link['rel_type'],
                                    'attributes': link.get('attributes', {})})
            missing_links.append(edge_key)
    cwd = Path.cwd().resolve()
    receipt_label = str(directory.relative_to(cwd) if directory.is_relative_to(cwd) else directory)
    local = {} if native else {'receipt_dir': receipt_label}
    result = {'status': 'ready' if not missing and not missing_links and artifacts else 'pending',
              'captured_creates': len(creates), 'released_readbacks': len(artifacts),
              'missing_readbacks': missing, 'missing_links': missing_links,
              'runner': 'mcp__call_read_tool',
              'runner_note': 'Use single verified reads for legacy receipts.',
              'readback_calls': [{'name': 'registry__get_artifact', 'arguments': {
                  'artifact_id': ident, 'channel': 'latest', **local}}
                  for key, (_, match) in expected.items() if key not in artifacts and key not in failures
                  for ident in [creates.get(key) or match or recovered.get(key)] if ident],
              'link_readback_calls': [{'name': 'registry__get_related_artifacts', 'arguments': {
                  'artifact_id': source, **local}}
                  for source in sorted({by_name.get((link['from']['kind'], link['from']['name']))
                                        for link in missing_links} - {None})]}
    estimates = {ident: len(json.dumps(json.dumps(body))) + READ_REPLY_OVERHEAD
                 for key, (body, match) in expected.items() if key not in artifacts
                 for ident in [creates.get(key) or match or recovered.get(key)] if ident}
    if native:
        result.pop('runner_note')
        result['batch_runner'] = 'mcp__call_read_tools'
        result['readback_batches'] = readback_batches(result['readback_calls'], estimates=estimates)
        result['link_readback_batches'] = readback_batches(result['link_readback_calls'])
        result['held'] = [{'kind': key[0], 'name': expected[key][0]['name'], 'code': code}
                          for key, code in failures.items()]
        result['held_links'] = held_links
        # Existing edges can be read alongside definitions. The reducer still requires both
        # endpoint releases to match the plan before it will offer any relationship write.
        known = {key: (creates.get(key) or match or recovered.get(key))
                 for key, (_, match) in expected.items() if key not in failures}
        sources = {known.get(expected_names.get((link['from']['kind'], link['from']['name'])))
                   for link in plan['links'] if link['ok']
                   and expected_names.get((link['to']['kind'], link['to']['name'])) not in failures}
        early_edges = [{'name': 'registry__get_related_artifacts', 'arguments': {'artifact_id': ident}}
                       for ident in sorted(sources - {None}) if ident not in edge_reads]
        result['verification_batches'] = readback_batches([*result['readback_calls'], *early_edges], estimates=estimates)
        result['lookup_calls'] = [{'name': 'registry__list_artifacts', 'arguments': {
            'kind': key[0], 'name': entry['name'], 'workspace_id': workspace, 'limit': 2}}
            for key, (entry, match) in expected.items() if not match and key not in artifacts
            and key not in creates and key not in failures and key not in attempted_lookups and key not in skip_lookup]
        result['lookup_batches'] = readback_batches(result['lookup_calls'])
        result['link_calls'] = [{'name': 'registry__relate_artifacts', 'arguments': {'links': link_writes[i:i + 100]}}
                                for i in range(0, len(link_writes), 100)]
        if result['status'] == 'ready' and (failures or held_links):
            result['status'] = 'partial'
    if finalize:
        if result['status'] not in ('ready', 'partial'):
            raise ValueError('Publication receipts are incomplete. Complete readbacks or reconcile; never replay writes.')
        payload = {'workspace_id': workspace, 'artifacts': list(artifacts.values())}
        project.write(run / 'captured-readbacks.json', payload)
        receipt = project.publication(payload)
        if result['status'] == 'partial':
            progress.update(project, 'publish', 'complete-with-held-items')
        # Keep only compact identity facts here; definitions remain in the immutable native files.
        project.write(context / 'published.json', [
            {'id': a['id'], 'kind': a['kind'], 'name': a['name'], 'source_id': a['source_id'],
             'release_id': a['release']['release_id'], 'version_id': a['release']['version_id']}
            for a in artifacts.values()])
        result.update(recorded=True, artifacts=len(receipt['artifacts']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--receipts', type=Path, required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    try:
        result = assemble(args.run_dir, args.receipts, args.workspace, args.finalize)
        print(json.dumps(result))
        return 0 if result['status'] == 'ready' else 2
    except (ValueError, OSError, KeyError, TypeError, context_contracts.Blocked):
        print(json.dumps({'status': 'pending', 'recorded': False,
                          'reason': 'Receipts are incomplete, conflicting or outside the checked plan. Reconcile live state and capture fresh readbacks; do not repeat writes.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
