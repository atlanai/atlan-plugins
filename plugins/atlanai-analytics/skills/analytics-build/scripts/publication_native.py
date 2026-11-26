"""Read prepared native MCP requests and capture tool results. No network or credentials."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from publication_batches import MAX_BYTES, checked_documents, encode, publication_identity, write_once
from publication_receipts import MAX_BYTES as RECEIPT_MAX_BYTES, assemble, read, slot_matches
from retrieval import releases

READS = {'registry__get_artifact', 'registry__get_related_artifacts', 'registry__list_artifacts'}


def manifest_data(path, run, workspace):
    if path.parent != run / 'context' / 'publish-batches':
        raise ValueError('Use the prepared manifest inside this run.')
    data = json.loads(read(path))
    if (data['workspace_id'] != workspace or path.name != f"plan-{data['plan_id']}.json"
            or Path(data['receipt_dir']) != run / 'receipts' / data['plan_id']):
        raise ValueError('The manifest does not match this run and workspace.')
    current, _ = publication_identity(workspace_id=workspace, sources=data['sources'],
                                      documents=checked_documents(run / 'context'))
    if current != data['plan_id']:
        raise ValueError('The checked plan changed after approval.')
    return data


def batch_request(manifest_path, manifest, index):
    if type(index) is not int or not 0 <= index < len(manifest['calls']):
        raise ValueError('Choose a batch index from the prepared manifest.')
    batch = manifest['calls'][index]
    kind = batch['arguments']['kind']
    expected_name = f"{kind}-{batch['sha256']}.json"
    if kind not in ('metric', 'sql_query') or batch['items_file'] != expected_name:
        raise ValueError('The prepared batch file has an unexpected name.')
    path = manifest_path.parent / expected_name
    data = read(path)
    if hashlib.sha256(data).hexdigest() != batch['sha256']:
        raise ValueError('The prepared batch changed.')
    items = json.loads(data)
    lane = 'metrics' if kind == 'metric' else 'queries'
    context = manifest_path.parent.parent
    plan = json.loads(read(context / 'plan.json'))
    approved = {row['source_id'] for row in plan[lane] if row['ok'] and row['action'] == 'create'}
    wanted = {row['source_id']: {**row, 'workspace_id': manifest['workspace_id']}
              for row in json.loads((context / f'{lane}.json').read_text()) if row['source_id'] in approved}
    if (len(items) != batch['count'] or [i['source_id'] for i in items] != batch['source_ids']
            or any(i != wanted.get(i['source_id']) for i in items)):
        raise ValueError('The prepared call differs from the approved create items.')
    return {'name': 'registry__create_artifacts', 'arguments': {'kind': kind, 'items': items}}


def reserve(directory, index):
    # Reserve before exposing a write descriptor. A lost command result is uncertain too.
    try:
        (directory / f'dispatch-{index}.pending').touch(mode=0o600, exist_ok=False)
    except FileExistsError:
        raise ValueError('This batch may have run. Reconcile its items and never replay it.') from None


def claim(manifest_path, manifest, directory, index):
    request = batch_request(manifest_path, manifest, index)
    reserve(directory, index)
    return request


def claim_group(manifest_path, manifest, directory, indices):
    if len(set(indices)) != len(indices):
        raise ValueError('Select each batch only once.')
    requests = [batch_request(manifest_path, manifest, index) for index in indices]
    if sum(len(encode(request)) for request in requests) > MAX_BYTES:
        raise ValueError('Claim fewer batches at a time.')
    if any((directory / f'dispatch-{index}.pending').exists() for index in indices):
        raise ValueError('A selected batch may have run. Reconcile its items and never replay it.')
    for index in indices:
        reserve(directory, index)
    return {'requests': [{'index': index, **request} for index, request in zip(indices, requests)]}


def body(value):
    """Unwrap a native result without accepting a truncated or errored frame."""
    if not isinstance(value, dict) or value.get('isError') or value.get('error'):
        raise ValueError('The tool did not return a successful native result.')
    meta = value.get('_meta') or {}
    if 'com.atlan/truncated' in meta or value.get('resultType') not in (None, 'complete'):
        raise ValueError('The tool result is incomplete.')
    if 'content' in value:
        blocks = value['content']
        if len(blocks) != 1 or blocks[0].get('type') != 'text':
            raise ValueError('Expected one native JSON result.')
        return body(json.loads(blocks[0]['text']))
    return value


def record(directory, request, response):
    data = encode({'format': 1, 'request': request, 'response': response})
    if len(data) > RECEIPT_MAX_BYTES:
        raise ValueError('The receipt is too large.')
    sha = hashlib.sha256(data).hexdigest()
    part = directory / ('part-' + sha[:2])
    part.mkdir(mode=0o700, exist_ok=True)
    if part.is_symlink():
        raise ValueError('The receipt segment must be an ordinary directory.')
    write_once(part / (sha + '.json'), data)


def capture_one(directory, request, response, workspace):
    name, args = request['name'], request['arguments']
    if name == 'registry__create_artifacts':
        kind, items = args['kind'], args['items']
        slots = response.get('items')
        if not isinstance(slots, list):
            raise ValueError('The create outcome has no result slots.')
        indices = set()
        for slot in slots:
            index = slot.get('index')
            if type(index) is not int or not 0 <= index < len(items) or index in indices:
                raise ValueError('Invalid or duplicate result index.')
            indices.add(index)
            if slot.get('ok') is True and not slot_matches(kind, items[index], slot.get('artifact')):
                raise ValueError('A successful slot differs from its request.')
        # Only identities belong in the create receipt. Readback proves the definition.
        request = {'name': name, 'arguments': {
            'kind': kind, 'items': [{k: row[k] for k in ('workspace_id', 'source_id', 'name')}
                                    for row in items]}}
        response = {'items': slots}
    elif name == 'registry__get_artifact':
        if (set(args) - {'artifact_id', 'channel', 'release_id'} or response.get('id') != args.get('artifact_id')
                or response.get('workspace_id') != workspace
                or not (args.get('channel') == 'latest' or args.get('release_id'))):
            raise ValueError('The readback must prove a release in this workspace.')
        releases({'workspace_id': workspace, 'artifacts': [response]})
    elif name == 'registry__get_related_artifacts':
        if set(args) != {'artifact_id'} or not all(isinstance(response.get(k), list) for k in ('incoming', 'outgoing')):
            raise ValueError('The relationship readback is incomplete.')
    elif name == 'registry__list_artifacts':
        if (set(args) != {'workspace_id', 'kind', 'name', 'limit'} or args.get('workspace_id') != workspace
                or args.get('kind') not in ('metric', 'sql_query') or args.get('limit') != 2
                or not isinstance(response.get('items'), list)):
            raise ValueError('The lookup must target one approved name in this workspace.')
        response = {'items': [{k: row[k] for k in ('id', 'name', 'kind', 'workspace_id', 'source_id') if k in row}
                              for row in response['items']]}
    else:
        raise ValueError('Unsupported receipt operation.')
    record(directory, request, response)


def capture(directory, request, reply, workspace):
    response = body(reply)
    if request['name'] != 'mcp__call_read_tools':
        capture_one(directory, request, response, workspace)
        return {'captured': 1, 'pending': 0}
    calls, slots = request['arguments']['calls'], response.get('results')
    if (not isinstance(slots, list) or not 1 <= len(calls) <= 8 or len(slots) != len(calls)
            or any(call['name'] not in READS or call['name'] != slot.get('name')
                   for call, slot in zip(calls, slots))):
        raise ValueError('The read batch response does not match its requests.')
    captured = 0
    for call, slot in zip(calls, slots):
        try:
            if slot.get('isError') is not False or slot.get('resultType') != 'complete':
                raise ValueError('The read slot is incomplete.')
            capture_one(directory, call, body(slot), workspace)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        captured += 1
    return {'captured': captured, 'pending': len(calls) - captured}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--workspace', required=True)
    action = parser.add_subparsers(dest='action', required=True)
    take = action.add_parser('claim')
    take.add_argument('indices', type=int, nargs='+')
    save = action.add_parser('capture')
    save.add_argument('--batch', type=int)
    save.add_argument('--input', type=Path, help='read native result JSON from this file instead of stdin')
    advance = action.add_parser('advance')
    advance.add_argument('--input', type=Path)
    status = action.add_parser('status')
    status.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    try:
        run, path = args.run_dir.absolute(), args.manifest.absolute()
        manifest = manifest_data(path, run, args.workspace)
        directory = Path(manifest['receipt_dir'])
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ValueError('Use a receipt directory inside the run without symlinks.')
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        def record_one(data, batch=None):
            if batch is None:
                request, reply = data['request'], data['reply']
                if request['name'] not in READS | {'mcp__call_read_tools'}:
                    raise ValueError('Use the prepared batch index for a create result.')
            else:
                request, reply = batch_request(path, manifest, batch), data
                if not (directory / f'dispatch-{batch}.pending').is_file():
                    raise ValueError('The batch was not reserved.')
            return capture(directory, request, reply, args.workspace)

        def current(finalize=False):
            unsent = [i for i in range(len(manifest['calls'])) if not (directory / f'dispatch-{i}.pending').exists()]
            skip = {(manifest['calls'][i]['arguments']['kind'], source) for i in unsent
                    for source in manifest['calls'][i]['source_ids']}
            return {**assemble(run, directory, args.workspace, finalize, native=True, skip_lookup=skip),
                    'unsent_batches': unsent}

        if args.action == 'claim':
            result = (claim(path, manifest, directory, args.indices[0]) if len(args.indices) == 1
                      else claim_group(path, manifest, directory, args.indices))
        elif args.action in ('capture', 'advance'):
            data = json.loads(read(args.input) if args.input else sys.stdin.read(3 * MAX_BYTES))
            if args.action == 'capture':
                result = record_one(data, args.batch)
            else:
                entries = data['results']
                if not isinstance(entries, list) or not 1 <= len(entries) <= 25:
                    raise ValueError('Record one to 25 results at a time.')
                recorded, refused = [], []
                for index, entry in enumerate(entries):
                    try:
                        recorded.append(record_one(entry['reply'] if 'batch' in entry else entry, entry.get('batch')))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        refused.append(index)
                result = {**current(), 'captured': sum(x['captured'] for x in recorded),
                          'pending_slots': sum(x['pending'] for x in recorded), 'refused_inputs': refused}
        else:
            result = current(args.finalize)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({'status': 'pending', 'reason': str(error) if isinstance(error, ValueError)
                          and not isinstance(error, json.JSONDecodeError) else 'The local input or tool result is incomplete.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
