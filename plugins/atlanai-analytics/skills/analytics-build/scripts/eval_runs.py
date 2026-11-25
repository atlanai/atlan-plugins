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
import os
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


def cell(value) -> str:
    """One value as a person reads it: a number with separators, a date without a time. A share keeps three
    significant figures, so 0.815 and 0.817 never both read 0.82."""
    if value is None:
        return "none"
    text = str(value).strip()
    try:
        number = float(text)
        if re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
            if number.is_integer():
                return f"{int(number):,}"
            return f"{number:.3g}" if abs(number) < 1 else f"{number:,.2f}".rstrip("0").rstrip(".")
    except ValueError:
        pass
    text = re.sub(r"^(\d{4}-\d{2}-\d{2})[ T]00:00(:00(\.0+)?)?([+-]00:?00|Z)?$", r"\1", text)
    return text.replace("|", "/").replace("\n", " ")


def expected(result: dict) -> str:
    """A result as a person reads it: one value on its own, otherwise its size and the first row's leading values.
    Column names stay out: a query's aliases (ms, cnt) mean nothing to the person."""
    rows = result.get("rows") or []
    if not rows:
        return "nothing"
    first = [cell(v) for v in rows[0] if cell(v) not in ("", "none")]
    if len(rows) == 1 and len(rows[0]) == 1:
        return first[0] if first else "none"
    shown = ", ".join(first[:3]) + (", …" if len(first) > 3 else "")
    return f"1 row: {shown}" if len(rows) == 1 else f"{len(rows)} rows, starting {shown}"


def _number(value):
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def shown_beside(result: dict, known: dict) -> str:
    """An answer's result as it reads beside its known answer. Against one known value, the cell of a one-row result
    nearest to it, as the comparison finds the value among extra columns; otherwise the result as `expected` reads it."""
    rows, gold = result.get("rows") or [], known.get("rows") or []
    target = _number(gold[0][0]) if len(gold) == 1 and len(gold[0]) == 1 else None
    near = [(abs(n - target), v) for v in (rows[0] if len(rows) == 1 else []) for n in [_number(v)] if n is not None] \
        if target is not None else []
    return cell(min(near, key=lambda x: x[0])[1]) if near else expected(result)


def check_folder(run: pathlib.Path):
    """The newest accuracy check of this run with its questions loaded: `<project>/.atlan-evals/<run name>`, or
    `-2`, `-3` for a later check. None before any. Another run's check never counts, whatever its name starts with."""
    run = pathlib.Path(run)
    project = run.parent.parent if run.parent.name in ROOTS else run.parent
    pattern = re.compile(re.escape(run.name) + r"(-\d+)?")
    root = project / ".atlan-evals"
    # Read every second by the side page: a folder removed while it is read means no check, never an error.
    try:
        found = [root / n for n in os.listdir(root) if pattern.fullmatch(n) and (root / n / "work" / "spec.json").is_file()]
        return max(found, key=lambda ev: (ev / "work" / "spec.json").stat().st_mtime, default=None)
    except OSError:
        return None


def written_folder(run: pathlib.Path):
    """Where `eval-generation` drafts this run's test questions, once it has a set: `<project>/.atlan-evals/gen/<run
    name>`. None before any."""
    run = pathlib.Path(run)
    project = run.parent.parent if run.parent.name in ROOTS else run.parent
    gen = project / ".atlan-evals" / "gen" / run.name
    return gen if (gen / "work" / "spec.json").is_file() else None


def questions_folder(run: pathlib.Path):
    """The folder whose questions the side page lists: the newest check, else a set written but not tested yet."""
    return check_folder(run) or written_folder(run)


def others(run: pathlib.Path) -> list:
    """The other agents built in the same folder, newest first: each by its name, its counts and the day it was built."""
    import datetime
    run = pathlib.Path(run)
    project = run.parent.parent if run.parent.name in ROOTS else run.parent
    out = []
    for r in built_runs(project):
        if r.resolve() == run.resolve():
            continue
        metrics = load(r / "context" / "metrics.json", []) or []
        queries = load(r / "context" / "queries.json", []) or []
        inter = load(r / "interaction.json", {}) or {}
        intent = inter.get("intent") if isinstance(inter.get("intent"), dict) else {}
        named = str(inter.get("report") or "").split(":")[0].strip() or str(intent.get("purpose") or intent.get("domain") or "")
        name = re.split(r":|\.(?=\s|$)", named, maxsplit=1)[0].strip() or r.name
        day = datetime.datetime.fromtimestamp((r / "context" / "metrics.json").stat().st_mtime)
        out.append({"name": name[:60], "metrics": len(metrics) if isinstance(metrics, list) else 0,
                    "queries": len(queries) if isinstance(queries, list) else 0, "built": "%d %s" % (day.day, day.strftime("%b"))})
    return out


def run_of(ev: pathlib.Path):
    """The built run a check belongs to, or None."""
    ev = pathlib.Path(ev)
    return next((r for r in built_runs(ev.parent.parent) if check_folder(r) == ev), None)


def _pass(vd: pathlib.Path, case: str, marks: dict, reasons: dict, known: dict):
    """What one pass did with one question: its answer as soon as the answer lands, its mark once it is scored."""
    said = vd / "answers" / f"{case}_rep0.json"
    if not said.is_file():
        return None
    answer = load(said)
    if not isinstance(answer, dict):
        answer = {"answer": said.read_text(errors="replace")[:2000]}
    result = load(vd / "agent_results" / f"{case}_rep0.json", {}) or {}
    reason = str(reasons.get(case) or "")
    return {"value": shown_beside(result, known) if isinstance(result, dict) and result.get("rows") else None,
            "answer": str(answer.get("answer") or "").strip(), "sql": str(answer.get("sql") or "").strip(),
            "right": (marks[case] == 1) if case in marks else None,
            "why": reason if reason != "match" else ""}


def _scored(vd: pathlib.Path):
    """Each question's mark in a pass, as the accuracy card reads them, and the reason each was graded so."""
    import analytics_eval
    rows = analytics_eval.read_results(vd / "results.jsonl") if (vd / "results.jsonl").is_file() else []
    return analytics_eval.case_means(rows), {r["prompt_id"]: r.get("reason") for r in rows if r.get("rep") in (0, "0")}


def questions(ev: pathlib.Path):
    """Every question of a check, numbered as every report numbers it, with the metric it tests, its known answer,
    and what each pass has answered and scored so far: `agent` with the context, `names` with column names only.
    The side page reads it live; `check.py show` writes its table from it. None before a set is loaded."""
    ev = pathlib.Path(ev)
    spec = load(ev / "work" / "spec.json")
    if not isinstance(spec, dict):
        return None
    picked = {s.get("id"): s for s in load(ev / "work" / "selection.json", []) or [] if isinstance(s, dict)}
    hill = ev / "hillclimb"
    # The newest pass with the context: a test again writes v1, v2 beside the first.
    passes = sorted((d for d in hill.glob("*") if d.is_dir() and d.name != "raw"), key=lambda d: d.stat().st_mtime)
    agent, names = (passes[-1] if passes else hill / "baseline"), hill / "raw"
    (am, ar), (nm, nr) = _scored(agent), _scored(names)
    rows = []
    for n, r in enumerate(spec.get("records") or [], 1):
        case, where = r["id"], str((picked.get(r["id"]) or {}).get("from") or "")
        gold = ev / "eval" / "gold" / f"{case}.json"
        known = load(gold, {}) or {}
        rows.append({"n": n, "id": case, "question": r.get("question") or "",
                     "metric": str((picked.get(case) or {}).get("metric") or ""),
                     "from": "tile" if where == "dashboard tile" else "query" if where.startswith("past query")
                     else "set" if where.startswith("eval set") else "",
                     # Written but not yet tested: its known answer is worked out when a check loads it.
                     "known": expected(known) if gold.is_file() else "",
                     "agent": _pass(agent, case, am, ar, known), "names": _pass(names, case, nm, nr, known)})
    answered = sum(1 for r in rows if r["agent"])
    # Scored once every question has its row in each pass that ran, a row that counts for nothing included.
    done = bool(rows) and all(r["id"] in ar for r in rows) and (not names.is_dir() or all(r["id"] in nr for r in rows))
    return {"total": len(rows), "answered": answered, "rows": rows, "name": str(spec.get("name") or ""),
            "state": "scored" if done else "running" if answered or any(r["names"] for r in rows) else "ready"}
