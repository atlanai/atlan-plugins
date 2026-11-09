"""Capture bounded native Registry results in immutable local receipt files.

No network or credentials. Both hook implementations use this writer so file containment,
no-follow opens, atomic publication and response checks have one implementation.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

MAX_BYTES = 256 * 1024
MAX_DEPTH = 8
OPERATIONS = {'registry__create_artifacts', 'registry__get_artifact', 'registry__get_related_artifacts'}
DENIED_DIRS = {'.git', '.codex', '.agents', '.ssh', '.aws', '.gnupg', '.kube', '.docker', '.azure', 'gcloud'}
KEY_FILES = re.compile(r'^(?:\.env(?:\..*)?|\.npmrc|\.netrc|\.pypirc|\.git-credentials)$|\.(?:pem|key|p12|pfx|tfvars|tfstate)$', re.I)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def directory(label, cwd, roots, create=False):
    """Return an open directory descriptor, traversing every component without following links."""
    if (not isinstance(label, str) or not label or label.startswith(('~', '//', '\\\\'))
            or '..' in Path(label).parts):
        raise ValueError('Choose a receipt directory inside the working tree.')
    path = os.path.abspath(os.path.join(cwd, label))
    trees = [os.path.realpath(r) for r in roots if isinstance(r, str) and os.path.isdir(r)]
    root = next((r for r in trees if os.path.commonpath([r, path]) == r), None)
    if root is None:
        raise ValueError('Receipt directory is outside the working tree.')
    parts = Path(os.path.relpath(path, root)).parts
    if len(parts) + 1 > MAX_DEPTH or any(p.lower() in DENIED_DIRS or KEY_FILES.search(p) for p in parts):
        raise ValueError('Receipt path is not allowed.')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return path, fd
    except BaseException:
        os.close(fd)
        raise


def read_at(fd, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('Invalid receipt file.')
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Receipt exceeds the size limit.')
    return data


def immutable(label, document, cwd, roots, filename=None):
    data = encoded(document)
    if len(data) > MAX_BYTES:
        raise ValueError('Receipt exceeds the size limit.')
    sha = hashlib.sha256(data).hexdigest()
    name = filename or sha + '.json'
    path, fd = directory(label, cwd, roots, create=True)
    temp = '.pending-' + os.urandom(16).hex()
    try:
        try:
            old = read_at(fd, name)
        except FileNotFoundError:
            old = None
        if old is not None:
            if old != data:
                raise ValueError('An immutable receipt conflicts with an existing file.')
        else:
            out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(out, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                except FileExistsError:
                    if read_at(fd, name) != data:
                        raise ValueError('Concurrent receipt conflict.') from None
                os.fsync(fd)
            finally:
                os.unlink(temp, dir_fd=fd)
        return {'path': os.path.join(path, name), 'sha256': sha}
    finally:
        os.close(fd)


def request_view(request):
    name, args = request.get('name'), request.get('arguments')
    if name not in OPERATIONS or not isinstance(args, dict) or 'receipt_dir' in args:
        raise ValueError('Unsupported receipt operation.')
    if name == 'registry__create_artifacts':
        if args.get('kind') not in ('metric', 'sql_query') or not isinstance(args.get('items'), list) or not 1 <= len(args['items']) <= 100:
            raise ValueError('Invalid bulk request.')
        items = [{k: item.get(k) for k in ('name', 'source_id', 'workspace_id')} for item in args['items']]
        if any(not all(isinstance(v, str) and v for v in item.values()) for item in items):
            raise ValueError('Bulk request identity is incomplete.')
        args = {'kind': args['kind'], 'items': items}
    else:
        artifact = args.get('artifact_id')
        if not isinstance(artifact, str) or not artifact.startswith(('metric_', 'sql_query_')):
            raise ValueError('Receipt reads must name a metric or saved query.')
        if name == 'registry__get_artifact' and not (args.get('channel') == 'latest' or args.get('release_id')):
            raise ValueError('A head read is not a publication receipt.')
    return {'name': name, 'arguments': args}


def checked_response(request, text, is_error=False):
    response = json.loads(text)
    if not isinstance(response, dict):
        raise ValueError('Receipt needs a complete native response.')
    if 'mcp_session_id' in response and isinstance(response.get('result'), dict):
        response = response['result']
    name, args = request['name'], request['arguments']
    if name == 'registry__create_artifacts':
        slots = response.get('items')
        if not isinstance(slots, list) or len(slots) != len(args['items']):
            raise ValueError('Incomplete bulk outcome; reconcile the uncertain write.')
        indices = set()
        for slot in slots:
            index = slot.get('index')
            if type(index) is not int or not 0 <= index < len(slots) or index in indices or type(slot.get('ok')) is not bool:
                raise ValueError('Invalid bulk outcome index.')
            indices.add(index)
            if slot['ok']:
                artifact = slot.get('artifact', {})
                if artifact.get('kind') != args['kind'] or any(artifact.get(k) != v for k, v in args['items'][index].items()):
                    raise ValueError('Bulk outcome identity differs from its request.')
                if not artifact.get('id'):
                    raise ValueError('Successful outcome has no artifact identity.')
    elif is_error or response.get('error') or response.get('isError'):
        raise ValueError('A failed read is not a receipt.')
    elif name == 'registry__get_artifact':
        release = response.get('release', {})
        if (response.get('id') != args['artifact_id'] or response.get('kind') not in ('metric', 'sql_query')
                or response.get('is_released') is not True or release.get('release_status') != 'released'
                or release.get('is_current') is not True or not release.get('release_id')
                or not release.get('version_id') or not isinstance(response.get('metadata', {}).get('definition'), dict)):
            raise ValueError('Incomplete or mismatched released-artifact response.')
        if args.get('channel') and release.get('channel') != args['channel'] or args.get('release_id') and release.get('release_id') != args['release_id']:
            raise ValueError('Release selector differs from its request.')
    elif not isinstance(response.get('outgoing'), list) or not isinstance(response.get('incoming'), list):
        raise ValueError('Incomplete relationship readback.')
    # Reuse the existing credential scanner, including JSON-escaped credential values.
    spec = importlib.util.spec_from_file_location('receipt_checks', Path(__file__).resolve().parents[1] / 'skills/atlan-author-skills/scripts/check_bundle.py')
    checker = importlib.util.module_from_spec(spec)
    written, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(checker)
    finally:
        sys.dont_write_bytecode = written
    if not checker.check_files([('receipt', [('response.txt', encoded(response))], [], True)])['ok']:
        raise ValueError('Receipt failed the existing content checks.')
    return response


def capture(payload):
    request = request_view(payload['request'])
    response = checked_response(request, payload['response_text'], payload.get('is_error', False))
    saved = immutable(payload['receipt_dir'], {'format': 1, 'request': request, 'response': response},
                      payload['cwd'], payload['roots'])
    return {'recorded': True, **saved, 'operation': request['name']}


def pending_key(event):
    identity = event.get('tool_use_id')
    if not isinstance(identity, str) or not identity or len(identity) > 256:
        raise ValueError('A tool-use identity is required for receipt recording.')
    return hashlib.sha256(encoded([event.get('session_id', ''), identity])).hexdigest() + '.json'


def prepare_pending(event, request, receipt_dir, roots):
    cwd = event['cwd']
    view = request_view(request)
    _, fd = directory(receipt_dir, cwd, roots, create=True)
    os.close(fd)
    # The post event may carry rewritten arguments, so retain the local destination by tool id.
    immutable('.atlan-receipt-pending', {'format': 1, 'request': view, 'receipt_dir': receipt_dir},
              cwd, roots, pending_key(event))


def finish_pending(event, roots, response_text, is_error):
    try:
        _, fd = directory('.atlan-receipt-pending', event['cwd'], roots)
        try:
            data = json.loads(read_at(fd, pending_key(event)))
        finally:
            os.close(fd)
    except FileNotFoundError:
        return None
    return capture({**data, 'cwd': event['cwd'], 'roots': roots,
                    'response_text': response_text, 'is_error': is_error})


def main():
    try:
        payload = json.loads(sys.stdin.read(MAX_BYTES * 3))
        if sys.argv[1:] == ['check']:
            request_view(payload['request'])
            _, fd = directory(payload['receipt_dir'], payload['cwd'], payload['roots'], create=True)
            os.close(fd)
            result = {'ready': True}
        else:
            result = capture(payload)
        print(json.dumps({**result, 'python_executable': sys.executable}))
        return 0
    except Exception:
        print(json.dumps({'recorded': False, 'reason': 'Receipt recording is pending. Keep the tool result and reconcile; never repeat a write to repair a local receipt.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
