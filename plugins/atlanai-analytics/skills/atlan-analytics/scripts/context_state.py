"""Private, resumable context storage, independent of dashboard/agent jobs."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid


def ordinary(path, root=None):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Use an ordinary local path without symlinks.')
    path = path.resolve()
    if root and not path.is_relative_to(Path(root).expanduser().resolve()):
        raise ValueError('The path is outside the selected run.')
    return path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def resolve_run(workspace, explicit=None, cache=None, home_permitted=False):
    """Permission is a host decision; never probe home by creating files there."""
    if explicit:
        return ordinary(explicit)
    root = ordinary(cache) if cache else (Path.home() / '.atlan/analytics/runs'
                                         if home_permitted else ordinary(workspace) / '.atlan-metrics')
    return ordinary(root / ('run_' + uuid.uuid4().hex[:16]))


class ContextState:
    def __init__(self, directory):
        self.root = ordinary(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'run.json'
        self.context = self.root / 'context'
        self.store = self  # Compatibility with existing project commands.

    def read(self, path):
        path = ordinary(path)
        if not path.is_file() or path.stat().st_size > 4_000_000:
            raise ValueError('Select a bounded JSON artifact.')
        return json.loads(path.read_text())

    def write(self, path, value):
        self.write_text(path, json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False))

    def write_text(self, path, value):
        path = ordinary(path, self.root)
        data = value.encode()
        if len(data) > 4_000_000:
            raise ValueError('Result exceeds the local artifact limit.')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
        try:
            with os.fdopen(fd, 'wb') as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def manifest(self):
        return self.read(self.path)
