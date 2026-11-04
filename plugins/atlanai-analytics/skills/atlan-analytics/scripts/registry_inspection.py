"""Read-only Registry inspection through the installed CLI; no credential reads or writes."""
import json
import math
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

MAX_BYTES = 2_000_000


def parse_response(raw):
    # Schema responses need a different budget from small user-authored candidates.
    if len(raw) > MAX_BYTES:
        raise ValueError('registry_response_limits')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('registry_invalid_response')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('registry_invalid_response')))
        remaining = 50000
        def visit(item, depth=0):
            nonlocal remaining
            remaining -= 1
            if remaining < 0 or depth > 32:
                raise ValueError('registry_response_limits')
            if isinstance(item, dict):
                if len(item) > 1000 or any(len(k) > 256 for k in item):
                    raise ValueError('registry_response_limits')
                for child in item.values(): visit(child, depth + 1)
            elif isinstance(item, list):
                if len(item) > 1000: raise ValueError('registry_response_limits')
                for child in item: visit(child, depth + 1)
            elif isinstance(item, str):
                if len(item) > 32768: raise ValueError('registry_response_limits')
                if any(ord(c) < 32 and c not in '\n\r\t' for c in item):
                    raise ValueError('registry_invalid_response')
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError('registry_invalid_response')
        visit(value)
        return value
    except (UnicodeError, RecursionError, TypeError, json.JSONDecodeError):
        raise ValueError('registry_invalid_response') from None


def inspect_contract(client, selected):
    # Fetch only the needed kind. Unrelated kinds and catalog pagination cannot block it.
    kind = client.api(selected, 'get', '/kinds/metric')
    if not isinstance(kind, dict) or kind.get('kind') != 'metric':
        raise ValueError('registry_kind_contract_mismatch')
    statuses = [kind[k] for k in ('status', 'kind_status') if k in kind]
    if not statuses or any(s != 'active' for s in statuses):
        raise ValueError('registry_metric_unsupported')
    manifest = kind.get('manifest')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('properties'), dict) or 'definition' not in manifest['properties']:
        raise ValueError('registry_kind_contract_mismatch')
    workspace = client.api(selected, 'get', '/workspaces/' + selected['workspace_id'], fields=(('include', 'permissions'),))
    if not isinstance(workspace, dict) or workspace.get('id') != selected['workspace_id']:
        raise ValueError('registry_workspace_contract_mismatch')
    permissions = workspace.get('permissions', {})
    if not isinstance(permissions, dict):
        raise ValueError('registry_workspace_contract_mismatch')
    return {'target': selected, 'metric_supported': True, 'releasable': manifest.get('x-gateway-releasable') is True,
            'write_permission': 'observed_allowed' if permissions.get('can_create_content') is True and workspace.get('workspace_status') == 'active' else 'unverified_or_denied',
            'workspace_status': workspace.get('workspace_status'), 'profile_admission': 'unverified', '_manifest': manifest}


class ReadCLI:
    def __init__(self):
        self.executable = shutil.which('atlanai')

    def run(self, arguments):
        if not self.executable: raise ValueError('registry_not_installed')
        try:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                result = subprocess.run([self.executable, *arguments], stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=err, timeout=35, check=False)
                if result.returncode:
                    err.seek(0)
                    diagnostic = err.read(8193)
                    statuses = set(re.findall(rb'^atlanai: [^\r\n]*\(HTTP ([1-5][0-9]{2})(?:, [A-Za-z][A-Za-z0-9_-]{0,127})?\)[ \t]*\r?$', diagnostic, re.M)) if len(diagnostic) <= 8192 else set()
                    raise ValueError('registry_auth_required' if statuses == {b'401'} else 'registry_permission_denied' if statuses == {b'403'} else 'registry_call_failed')
                out.seek(0)
                return parse_response(out.read(MAX_BYTES + 1))
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('registry_call_failed') from None

    def api(self, selected, method, path, fields=()):
        if not isinstance(selected, dict) or set(selected) != {'gateway_origin','workspace_id'}:
            raise ValueError('invalid_arguments')
        workspace = selected.get('workspace_id')
        origin = selected.get('gateway_origin')
        if not isinstance(workspace, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}', workspace):
            raise ValueError('invalid_arguments')
        parsed = urlsplit(origin) if isinstance(origin, str) else None
        if not parsed or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise ValueError('invalid_arguments')
        if method != 'get' or path not in ('/kinds/metric', '/workspaces/' + workspace) or fields not in ((), (('include','permissions'),)):
            raise ValueError('invalid_arguments')
        auth = self.run(['auth', 'status', '--json', 'state,gateway'])
        if not isinstance(auth, dict) or auth.get('state') != 'authenticated':
            raise ValueError('registry_auth_required')
        if auth.get('gateway', '').rstrip('/') != origin.rstrip('/'):
            raise ValueError('registry_target_changed')
        args = ['api', 'get', 'registry:' + path]
        for key, value in fields: args += ['-f', key + '=' + value]
        return self.run(args)
