"""Prepare local bulk-create payloads from an authorized, checked publication plan.

Standard library only. No Registry calls, credentials, revision writes or link writes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analytics-metrics"))
from modules.workflow import Authorization
from modules.metrics.publication import publication_identity
from modules.metrics.holds import enforce_context
import context_contracts


MAX_ITEMS = 25
MAX_BYTES = 240 * 1024  # Leave room inside the publish helper's 256 KiB call limit.


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def prepare(context, authorization, workspace, sources, record_receipts=False):
    root = Path(context).resolve()
    documents = {name: json.loads((root / (name + '.json')).read_text(encoding='utf-8'))
                 for name in ('plan', 'metrics', 'queries', 'links')}
    holds = enforce_context(root, documents['metrics'], documents['queries'], documents['links'])
    if holds is not None:
        documents['publication_holds'] = holds
    plan_id, revisions = publication_identity(workspace_id=workspace, sources=sources, documents=documents)
    decision = Authorization.from_record(authorization).publication(
        workspace_id=workspace, sources=sources, plan_id=plan_id, revisions=revisions)
    if decision['decision'] != 'continue':
        raise ValueError('Publication is not authorized for this exact plan and workspace.')
    batches = []
    deferred = {'revise': 0, 'reuse': 0, 'held': 0}
    for lane, kind, check in (('metrics', 'metric', context_contracts.check_metric),
                              ('queries', 'sql_query', context_contracts.check_query)):
        bodies = documents[lane]
        by_source = {body['source_id']: body for body in bodies}
        if len(by_source) != len(bodies):
            raise ValueError('Resolve duplicate source identities before preparing batches.')
        selected, seen = [], set()
        for item in documents['plan'][lane]:
            source = item.get('source_id')
            if source not in by_source or source in seen:
                raise ValueError('The checked plan does not match the publication bodies.')
            seen.add(source)
            if not item['ok']:
                deferred['held'] += 1
                continue
            if item['action'] != 'create':
                deferred[item['action']] += 1
                continue
            body = by_source[source]
            if body.get('workspace_id', workspace) != workspace or item.get('match_id'):
                raise ValueError('A create must target the selected workspace without an existing match.')
            if len(encode([body])) > MAX_BYTES:
                raise ValueError('One definition exceeds the local batch limit; nothing was truncated.')
            errors, _ = check(body)
            if errors:
                raise ValueError('A create body failed validation; run the publication checker again.')
            selected.append({**body, 'workspace_id': workspace})
        if seen != set(by_source):
            raise ValueError('The checked plan omits publication bodies.')
        chunk = []
        for item in selected:
            if chunk and (len(chunk) == MAX_ITEMS or len(encode(chunk + [item])) > MAX_BYTES):
                batches.append((kind, chunk))
                chunk = []
            if len(encode([item])) > MAX_BYTES:
                raise ValueError('One definition exceeds the local batch limit; nothing was truncated.')
            chunk.append(item)
        if chunk:
            batches.append((kind, chunk))
    # Validate everything before writing the first request file.
    destination = root / 'publish-batches'
    destination.mkdir(exist_ok=True)
    if destination.is_symlink():
        raise ValueError('The batch destination must be an ordinary directory.')
    calls = []
    receipt_path = root.parent / 'receipts' / plan_id
    # Keep repeated read-call arguments short when the run is under the current directory.
    cwd = Path.cwd().resolve()
    receipt_dir = str(receipt_path.relative_to(cwd) if receipt_path.is_relative_to(cwd) else receipt_path)
    for kind, items in batches:
        data = encode(items)
        sha = hashlib.sha256(data).hexdigest()
        path = destination / (kind + '-' + sha + '.json')
        try:
            with path.open('xb') as stream:
                stream.write(data)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise ValueError('An existing batch file changed; inspect it locally.') from None
        calls.append({'name': 'registry__create_artifacts', 'count': len(items),
                      'source_ids': [item['source_id'] for item in items],
                      'arguments': {'kind': kind, 'items': [], 'workspace_id': workspace,
                                    'items_from': str(path), 'items_sha256': sha}})
        if record_receipts:
            calls[-1]['arguments']['receipt_dir'] = receipt_dir
    return {'plan_id': plan_id, 'calls': calls, 'deferred': deferred,
            **({'receipt_dir': receipt_dir} if record_receipts else {}),
            'links': sum(item['ok'] for item in documents['plan']['links'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--authorization', type=Path, required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--source', action='append', required=True)
    parser.add_argument('--record-receipts', action='store_true')
    args = parser.parse_args()
    try:
        authorization = json.loads(args.authorization.read_text(encoding='utf-8'))
        result = prepare(args.context, authorization, args.workspace, args.source, args.record_receipts)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError, context_contracts.Blocked):
        print(json.dumps({'status': 'REFUSED', 'reason': 'Recheck the publication plan, authorization, holds and local batch files.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
