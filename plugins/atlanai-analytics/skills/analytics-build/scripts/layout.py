"""Where Atlan Analytics keeps its files in a person's project: one folder, `.atlan-analytics/`.

    .atlan-analytics/<run>/        a setup run (its context, engine and checks)
    .atlan-analytics/runtime/      the metrics engine's pinned runtime
    .atlan-analytics/evals/        eval sets (sets/), drafts (gen/) and scored checks
    .atlan-analytics/demo/<demo>/  a demo's working copy of its data
    .atlan-analytics/readiness/    the readiness check's record and report

One folder means one line in a project's .gitignore. Earlier versions wrote `.atlan-metrics/`,
`.atlan-evals/` and `atlan-analytics-demo/`; `adopt` moves those in once, on first use, and
rewrites the absolute paths a run records, so an earlier run resumes where it stopped.
Standard library only.
"""
from __future__ import annotations

from pathlib import Path

ROOT = '.atlan-analytics'
RUNTIME = 'runtime'
EVALS = 'evals'
DEMO = 'demo'
READINESS = 'readiness'
# A run is a direct child of ROOT, so these names are never a run's.
RESERVED = (RUNTIME, EVALS, DEMO, READINESS)
# Each earlier folder, and where its contents go under ROOT ('' is ROOT itself).
EARLIER = (('.atlan-metrics', ''), ('.atlan-evals', EVALS), ('atlan-analytics-demo', DEMO))
TEXT = ('.json', '.jsonl', '.yml', '.yaml', '.md', '.txt', '.sql', '.csv', '.toml')
MAX_REWRITE = 20_000_000


def root(project) -> Path:
    return Path(project) / ROOT


def current(project, path) -> Path:
    """Where `path` is now: a path inside an earlier folder of `project`, after `adopt`, is the same
    item under ROOT. A path an earlier chat named keeps working once the folders have moved."""
    given = Path(path).expanduser()
    project, path = Path(project).expanduser().resolve(), given.resolve()
    for old, sub in EARLIER:
        try:
            rest = path.relative_to(project / old)
        except ValueError:
            continue
        moved = (root(project) / sub if sub else root(project)) / rest
        if not path.exists() and moved.exists():
            return moved
    return given


def follow(path) -> Path:
    """A path inside an earlier folder, wherever it is now: its project's folders are moved in
    first. Any other path is returned as given."""
    path = Path(path).expanduser()
    for old, _ in EARLIER:
        if old in path.parts:
            at = len(path.parts) - 1 - path.parts[::-1].index(old)
            project = Path(*path.parts[:at])
            adopt(project)
            return current(project, path)
    return path


def evals(project) -> Path:
    """The project's eval folder, after bringing in what an earlier version kept elsewhere."""
    adopt(project)
    return root(project) / EVALS


def adopt(project) -> dict:
    """Move the folders an earlier version wrote in `project` into ROOT, once.

    An entry whose name is already taken in ROOT stays where it is and is named in `kept`, and its
    earlier folder stays too; nothing is overwritten. Moved runs record absolute paths (their own
    folder, the demo folder they surveyed), so each text file under ROOT is rewritten from the old
    path to the new one. The runtime's own environment is moved as is: it runs from its new place."""
    project = Path(project).expanduser().resolve()
    moved, kept, pairs = [], [], []
    for old, sub in EARLIER:
        source = project / old
        if not source.is_dir() or source.is_symlink():
            continue
        target = root(project) / sub if sub else root(project)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        for entry in sorted(source.iterdir()):
            destination = target / entry.name
            # The earlier runtime becomes runtime/; an earlier run named like another reserved
            # folder would take its place, so it stays.
            if destination.exists() or (not sub and entry.name in RESERVED and entry.name != RUNTIME):
                kept.append(str(entry))
                continue
            entry.rename(destination)
            moved.append(f'{old}/{entry.name}')
        pairs.append((f'{source}/', f'{target}/'))
        try:
            source.rmdir()
        except OSError:
            pass  # something stayed behind (a kept entry); the folder stays with it
    if moved:
        _rewrite(root(project), pairs)
    return {'moved': moved, 'kept': kept,
            **({'message': f'Moved {len(moved)} earlier item(s) into {ROOT}/; add {ROOT}/ to .gitignore.'}
               if moved else {})}


def _rewrite(base: Path, pairs) -> None:
    for path in base.rglob('*'):
        if (not path.is_file() or path.is_symlink() or path.suffix not in TEXT
                or 'venv' in path.relative_to(base).parts or path.stat().st_size > MAX_REWRITE):
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        new = text
        for old, current in pairs:
            new = new.replace(old, current)
        if new != text:
            path.write_text(new, encoding='utf-8')
