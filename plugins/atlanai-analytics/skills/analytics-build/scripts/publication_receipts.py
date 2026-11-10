"""Validate captured MCP receipts and record a resumable publication without model copying.

No network or Registry writes. New runs still discover live definitions and reconcile them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

import context_contracts
from project import Project
from retrieval import releases

MAX_BYTES = 256 * 1024
MAX_FILES = 128


def read(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Receipt files must be bounded ordinary files inside this run.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Receipt is not an ordinary file.')
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Receipt exceeds the size limit.')
    return data


def documents(directory):
    paths = sorted(directory.glob('*.json'))
    if len(paths) > MAX_FILES:
        raise ValueError('Too many receipt files; review the selected receipt set.')
    for path in paths:
        data = read(path)
        if path.name != hashlib.sha256(data).hexdigest() + '.json':
            raise ValueError('A receipt is incomplete or changed; read it back again, without repeating writes.')
        value = json.loads(data)
        if (set(value) != {'format', 'request', 'response'} or value['format'] != 1
                or not isinstance(value['request'], dict) or not isinstance(value['response'], dict)):
            raise ValueError('Unsupported receipt format.')
        yield value


def assemble(run_dir, receipt_dir, workspace, finalize=False):
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
    for entry in documents(directory):
        request, response = entry['request'], entry['response']
        name, args = request.get('name'), request.get('arguments', {})
        if name == 'registry__create_artifacts':
            slots = response.get('items')
            items = args.get('items', [])
            if not isinstance(slots, list) or len(slots) != len(items):
                raise ValueError('An incomplete bulk response requires live reconciliation.')
            for slot in slots:
                index = slot.get('index')
                if type(index) is not int or not 0 <= index < len(items):
                    raise ValueError('Invalid bulk receipt index.')
                if slot.get('ok') is True:
                    actual = slot['artifact']
                    if actual.get('kind') != args['kind'] or any(actual.get(k) != v for k, v in items[index].items()):
                        raise ValueError('A create receipt differs from its request.')
                    key = (actual['kind'], actual['source_id'])
                    if key in expected and actual.get('workspace_id') == workspace:
                        if key in creates and creates[key] != actual['id']:
                            raise ValueError('Conflicting create identities; reconcile live state.')
                        creates[key] = actual['id']
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
    for key, ident in creates.items():
        if key in artifacts and artifacts[key]['id'] != ident:
            raise ValueError('The release readback does not belong to the recorded create.')
    missing = [body['name'] for key, (body, _) in expected.items() if key not in artifacts]
    by_name = {(a['kind'], a['name']): a['id'] for a in artifacts.values()}
    missing_links = []
    for link in plan['links']:
        if not link['ok']:
            continue
        source = by_name.get((link['from']['kind'], link['from']['name']))
        target = by_name.get((link['to']['kind'], link['to']['name']))
        def proves(snapshot):
            return any(e.get('rel_type') == link['rel_type'] and e.get('to', {}).get('id') == target
                       and all(e.get('attributes', {}).get(k) == v for k, v in link.get('attributes', {}).items())
                       for e in snapshot['outgoing'])
        if not source or not target or not any(proves(s) for s in edge_reads.get(source, [])):
            missing_links.append({'from': link['from'], 'to': link['to'], 'rel_type': link['rel_type']})
    cwd = Path.cwd().resolve()
    receipt_label = str(directory.relative_to(cwd) if directory.is_relative_to(cwd) else directory)
    result = {'status': 'ready' if not missing and not missing_links and artifacts else 'pending',
              'captured_creates': len(creates), 'released_readbacks': len(artifacts),
              'missing_readbacks': missing, 'missing_links': missing_links,
              'readback_calls': [{'name': 'registry__get_artifact', 'arguments': {
                  'artifact_id': ident, 'channel': 'latest', 'receipt_dir': receipt_label}}
                  for key, (_, match) in expected.items() if key not in artifacts
                  for ident in [creates.get(key) or match] if ident],
              'link_readback_calls': [{'name': 'registry__get_related_artifacts', 'arguments': {
                  'artifact_id': source, 'receipt_dir': receipt_label}}
                  for source in sorted({by_name.get((link['from']['kind'], link['from']['name']))
                                        for link in missing_links} - {None})]}
    if finalize:
        if result['status'] != 'ready':
            raise ValueError('Publication receipts are incomplete. Complete readbacks or reconcile; never replay writes.')
        payload = {'workspace_id': workspace, 'artifacts': list(artifacts.values())}
        project.write(run / 'captured-readbacks.json', payload)
        receipt = project.publication(payload)
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
