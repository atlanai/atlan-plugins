"""Cache receipts for host-authorized downloads; no fetching or credential storage."""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs
import hashlib
import re
from context_state import ordinary, digest


def source_identity(url):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('Supply a bounded HTTPS source link.')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Use a regular HTTPS source link without embedded credentials.')
    # Signed URLs are never stored. Query differences still change the cache key.
    safe = urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
    page = parse_qs(parts.query).get('pageName', [None])[0]
    identity = url
    if parts.hostname == 'app.powerbi.com' and re.fullmatch(r'/groups/[A-Za-z0-9_-]+/reports/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?/?', parts.path):
        after = parts.path.split('/reports/', 1)[1].strip('/').split('/')
        page = page or (after[1] if len(after) > 1 else None)
        if set(parse_qs(parts.query)) <= {'pageName', 'experience', 'filter', 'bookmarkGuid'}:
            # An export is report-wide; changing page selection should not download it again.
            identity = urlunsplit((parts.scheme, parts.netloc, parts.path.split('/reports/', 1)[0] + '/reports/' + after[0], '', ''))
    return {'source_hash': digest(identity), 'display_url': safe, 'page_ref': page}


def file_hash(path):
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def cached(run, request):
    source = source_identity(request['url'])
    if any(not isinstance(request.get(k), str) or not request[k] or len(request[k]) > 4096
           for k in ('revision', 'access_scope')):
        raise ValueError('Record the source revision and access scope before reusing an export.')
    key = digest({'source': source['source_hash'], 'revision': request['revision'],
                  'access_scope': request['access_scope']})
    receipt = run.root / 'acquisitions' / (key + '.json')
    if receipt.exists():
        recorded = run.read(receipt)
        path = ordinary(run.root / recorded['path'], run.root)
        if path.is_file() and path.stat().st_size <= 1024 ** 3 and file_hash(path) == recorded['sha256']:
            return {'status': 'reused', 'path': str(path), 'page_ref': source['page_ref']}
    if 'file' not in request:
        return {'status': 'needs_download', 'directory': str(run.root / 'acquisitions'),
                'page_ref': source['page_ref'],
                'next': 'Announce and acquire once through the existing authorized host connection, then record the file.'}
    path = ordinary(request['file'], run.root / 'acquisitions')
    if not path.is_file() or path.stat().st_size > 1024 ** 3:
        raise ValueError('Record a bounded downloaded file inside this run acquisitions directory.')
    run.write(receipt, {'key': key, 'path': str(path.relative_to(run.root)), 'sha256': file_hash(path)})
    return {'status': 'recorded', 'path': str(path), 'page_ref': source['page_ref']}
