#!/usr/bin/env python3
"""eval_runs: find a built analytics run and say how its SQL is executed.

Shared by `eval-generation` (scope.py) and `accuracy-check` (check.py), so both agree on which
run a project holds, what it was built for and where its SQL runs. Standard library only.

A built run is a folder with `context/metrics.json`. A demo or a local-file build keeps it in
`<project>/.atlan-metrics/run_*`; a warehouse build names it after its use case, in
`<project>/.atlan-metrics/<slug>` or `<project>/.atlan-analytics/<slug>`.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOTS = (".atlan-metrics", ".atlan-analytics")
LOCAL = ("duckdb", "sqlite")


class Blocked(Exception):
    pass


def load(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text()) if path.is_file() else default
    except (ValueError, OSError):
        return default


def is_built(run: pathlib.Path) -> bool:
    return (run / "context" / "metrics.json").is_file()


def purpose(run: pathlib.Path) -> str:
    intent = (load(run / "interaction.json", {}) or {}).get("intent") or {}
    return intent.get("purpose") or intent.get("domain") or "no use case recorded"


def built_runs(project: pathlib.Path) -> list:
    """Every built run in the project, newest first."""
    runs = [r for root in ROOTS for r in (project / root).glob("*") if r.is_dir() and is_built(r)]
    return sorted(runs, key=lambda r: (r / "context" / "metrics.json").stat().st_mtime, reverse=True)


def outside(project: pathlib.Path, run: pathlib.Path) -> pathlib.Path:
    """Refuse a project that is the run or sits inside it: eval sets and trusted answers are written
    under the project, and the agents that answer read the run."""
    proj, r = project.expanduser().resolve(), run.resolve()
    if proj == r or r in proj.parents:
        raise Blocked(f"--project is inside the run at {r}. Use the folder that holds the run's "
                      f".atlan-metrics or .atlan-analytics folder ({r.parent.parent}), so eval files stay "
                      "outside what answering agents read.")
    return run


def find_run(project: pathlib.Path, run=None) -> pathlib.Path:
    """The run to use: the one named, else the project's only built run. Several ask which."""
    if run:
        named = pathlib.Path(run).expanduser()
        if not named.is_absolute():
            named = project / named
        if not is_built(named):
            raise Blocked(f"Nothing is built at {named}: it has no context/metrics.json. Name the folder "
                          "that holds your agent, or ask me to build one first.")
        return outside(project, named)
    ready = built_runs(project)
    if not ready:
        raise Blocked("This folder has no agent built with Atlan Analytics, so there's nothing to test. "
                      "Open the folder that holds your agent, or ask me to build one.")
    if len(ready) > 1:
        cases = [f"{r.relative_to(project)}: {purpose(r)}" for r in ready]
        raise Blocked("This folder has more than one agent: " + "; ".join(cases) + ". Which one should I test?")
    return ready[0]


def use_case(run: pathlib.Path, given: str = "") -> dict:
    """The run's use case: what its interview recorded, else the one the caller gives."""
    intent = dict((load(run / "interaction.json", {}) or {}).get("intent") or {})
    if given and not (intent.get("domain") or intent.get("purpose")):
        intent["purpose"] = given
    return intent


def engine_dir(run: pathlib.Path):
    """The engine folder that holds this run's metric cards: the one run.json names, else
    `engine/`, else the newest `engine*/` with cards. None when the run has none."""
    named = (load(run / "run.json", {}) or {}).get("engine_run")
    tried = []
    if named:
        p = pathlib.Path(named)
        tried.append(p if p.is_absolute() else run / p)
    tried.append(run / "engine")
    tried += sorted((d for d in run.glob("engine*") if d.is_dir()),
                    key=lambda d: d.stat().st_mtime, reverse=True)
    return next((d for d in tried if (d / "metric_cards.json").is_file()), None)


def _yaml_value(path: pathlib.Path, key: str):
    """One top-level scalar from the engine's run.yml, without a YAML library."""
    if not path.is_file():
        return None
    m = re.search(rf"^{re.escape(key)}:\s*['\"]?([A-Za-z0-9_.-]+)", path.read_text(), re.M)
    return m.group(1) if m else None


def tables(run: pathlib.Path) -> list:
    """The source tables this run's metrics read: what a raw-source agent is told to work from."""
    rows = load(run / "context" / "metrics.json", []) or []
    rows = rows.get("metrics") or [] if isinstance(rows, dict) else rows
    found = {((m.get("definition") or {}).get("source") or {}).get("entity") for m in rows if isinstance(m, dict)}
    return sorted(t for t in found if t)


def execution(run: pathlib.Path) -> dict:
    """Where this run's SQL executes, and `tables`, the source tables its metrics read.

    `{"via": "local", "kind": "sqlite" | "duckdb"}`: the run is bound to a local database, and
    `project.py execute` runs a statement on it.
    `{"via": "host", "dialect", "warehouse"}`: a warehouse build. Nothing on this machine can run
    its SQL; the host runs each statement with the warehouse query tool, and the result is read
    from that call. `warehouse` is the tool the build recorded, when it recorded one.
    """
    bound = (load(run / "run.json", {}) or {}).get("execution") or {}
    if bound.get("kind") in LOCAL:
        return {"via": "local", "kind": bound["kind"], "tables": tables(run)}
    warehouse = (load(run / "state.json", {}) or {}).get("warehouse") or None
    dialect = (warehouse or {}).get("dialect")
    if not dialect:
        for q in load(run / "context" / "queries.json", []) or []:
            dialect = (q.get("definition") or {}).get("dialect")
            if dialect:
                break
    if not dialect:
        eng = engine_dir(run)
        dialect = _yaml_value(run / "run.yml", "dialect") or (_yaml_value(eng / "run.yml", "dialect") if eng else None)
    return {"via": "host", "dialect": dialect, "warehouse": warehouse, "tables": tables(run)}
