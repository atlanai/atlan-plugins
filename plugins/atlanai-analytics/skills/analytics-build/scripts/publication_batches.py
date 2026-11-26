"""Prepare local bulk-create payloads from an authorized, checked publication plan.

Standard library only. No Atlan Trident calls, credentials, revision writes or link writes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "metrics-generation"))
from modules.workflow import Authorization
from modules.metrics.publication import publication_identity
from modules.metrics.holds import enforce_context
import context_contracts
from project import Project


MAX_ITEMS = 25
MAX_BYTES = 240 * 1024  # Leave room for the native MCP envelope.
NATIVE_BYTES = 32 * 1024  # Keep each request small enough for both hosts' native tool calls.


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def write_once(path, data):
    """Publish a complete private file atomically, or confirm its existing bytes."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        name = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary.close()
            os.link(name, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise ValueError('An existing publication file changed; inspect it locally.') from None
        finally:
            name.unlink(missing_ok=True)


def checked_documents(root):
    documents = {name: json.loads((root / (name + '.json')).read_text(encoding='utf-8'))
                 for name in ('plan', 'metrics', 'queries', 'links')}
    holds = enforce_context(root, documents['metrics'], documents['queries'], documents['links'])
    if holds is not None:
        documents['publication_holds'] = holds
    return documents


def prepare(context, authorization, workspace, sources, record_receipts=False, max_bytes=MAX_BYTES, *, native=False):
    if native and record_receipts:
        raise ValueError('Native batches record tool results explicitly.')
    root = Path(context).resolve()
    if native and root != Project(root.parent).context.resolve():
        raise ValueError('Select the context inside the existing publication run.')
    documents = checked_documents(root)
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
            if chunk and (len(chunk) == MAX_ITEMS or len(encode(chunk + [item])) > max_bytes):
                batches.append((kind, chunk))
                chunk = []
            if len(encode([item])) > MAX_BYTES:
                raise ValueError('One definition exceeds the local batch limit; nothing was truncated.')
            chunk.append(item)  # an item larger than max_bytes travels alone
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
        write_once(path, data)
        call = {'name': 'registry__create_artifacts', 'count': len(items),
                'source_ids': [item['source_id'] for item in items],
                'arguments': {'kind': kind}}
        if native:
            call.update(items_file=path.name, sha256=sha)
        else:
            call['arguments'].update(items=[], workspace_id=workspace, items_from=str(path), items_sha256=sha)
            if record_receipts:
                call['arguments']['receipt_dir'] = receipt_dir
        calls.append(call)
    # RUN THIS FIRST, ON THE CONNECTION THAT WILL WRITE. A host can hold two Atlan connections
    # signed in to different organizations; a refused or empty answer here means this
    # connection cannot reach the reviewed workspace, and nothing should be sent through it.
    # The counts it returns are also the "before" side of the post-write count check.
    preflight = {'name': 'registry__get_artifact_counts',
                 'arguments': {'workspace_id': workspace, 'group_by': 'kind'},
                 'expect_created': {'metric': sum(c['count'] for c in calls if c['arguments']['kind'] == 'metric'),
                                    'sql_query': sum(c['count'] for c in calls if c['arguments']['kind'] == 'sql_query')}}
    result = {'plan_id': plan_id, 'preflight': preflight, 'calls': calls, 'deferred': deferred,
              **({'receipt_dir': receipt_dir} if record_receipts or native else {}),
              'links': sum(item['ok'] for item in documents['plan']['links'])}
    if native:
        manifest = destination / f'plan-{plan_id}.json'
        write_once(manifest, encode({'workspace_id': workspace, 'sources': sources, **result,
                                     'receipt_dir': str(receipt_path)}))
        result = {**result, 'manifest': str(manifest)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--authorization', type=Path, required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--source', action='append', required=True)
    parser.add_argument('--record-receipts', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--max-bytes', type=int, help='cap each batch file in UTF-8 bytes')
    args = parser.parse_args()
    try:
        authorization = json.loads(args.authorization.read_text(encoding='utf-8'))
        result = prepare(args.context, authorization, args.workspace, args.source, args.record_receipts,
                         max_bytes=max(1, min(args.max_bytes or (MAX_BYTES if args.record_receipts else NATIVE_BYTES),
                                              MAX_BYTES)), native=not args.record_receipts)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError, context_contracts.Blocked):
        print(json.dumps({'status': 'REFUSED', 'reason': 'Recheck the publication plan, authorization, holds and local batch files.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
