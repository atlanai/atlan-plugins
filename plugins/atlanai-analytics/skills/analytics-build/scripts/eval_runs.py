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
    near = [(min(abs(n - target), abs(n / 100 - target) if abs(target) <= 1 else abs(n - target)), v)
            for v in (rows[0] if len(rows) == 1 else []) for n in [_number(v)] if n is not None] if target is not None else []
    # Only a cell near the known value stands for the answer, a share written as a percent included: the nearest, however
    # far, showed a ticket count beside a known 81.5% (2026-11-26).
    near = [x for x in near if x[0] <= 0.5 * max(abs(target), 1e-9)]
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
    """The folder whose questions the side page lists: the newest check, unless a set was written after it and not
    tested yet. Written after a check, a new set showed as the old check's questions (2026-11-26)."""
    checked, written = check_folder(run), written_folder(run)
    if not (checked and written):
        return checked or written
    try:
        newer = (written / "work" / "spec.json").stat().st_mtime > (checked / "work" / "spec.json").stat().st_mtime
    except OSError:
        return checked
    return written if newer else checked


def set_key(file, rows):
    """Two files may hold the same questions, and a file may be rewritten between checks."""
    from context_state import digest
    return digest([str(pathlib.Path(file).resolve()), rows])


def question_counts(name, origins):
    if not origins:
        return {}
    origins = [str(origin or "") for origin in origins]
    return {"questions": len(origins), "tiles": origins.count("dashboard tile"),
            "queries": sum(o.startswith("past query") for o in origins),
            **({"set": name} if name and all(o.startswith("eval set") for o in origins) else {})}


def questions_chip(ev):
    """The questions actually emitted or loaded for this check, including the identity recorded with their chip."""
    if ev.parent.name == "gen":
        emitted = (load(ev / "scope.json", {}) or {}).get("emitted")
        if emitted:
            return emitted
    spec = load(ev / "work" / "spec.json", {}) or {}
    picked = {s.get("id"): s.get("from") or "" for s in load(ev / "work" / "selection.json", []) or [] if isinstance(s, dict)}
    counts = question_counts(spec.get("name"), [picked.get(r["id"], "") for r in spec.get("records") or []])
    return {**counts, "set_key": spec.get("set_key") or str(ev.resolve())} if counts else {}


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
    if ev.parent.name != ".atlan-evals" or not (ev / "work" / "spec.json").is_file():
        return None
    matches = [r for r in built_runs(ev.parent.parent) if re.fullmatch(re.escape(r.name) + r"(-\d+)?", ev.name)]
    return matches[0] if len(matches) == 1 else None


def _pass(vd: pathlib.Path, case: str, marks: dict, reasons: dict, known: dict, said_by: dict = None, names: dict = None):
    """What one pass did with one question: its answer as soon as the answer lands, its mark once it is scored, why a
    miss missed in plain words (the why file the report reads), and the metrics and saved queries the answer used."""
    said = vd / "answers" / f"{case}_rep0.json"
    if not said.is_file():
        return None
    answer = load(said)
    if not isinstance(answer, dict):
        answer = {"answer": said.read_text(errors="replace")[:2000]}
    result = load(vd / "agent_results" / f"{case}_rep0.json", {}) or {}
    reason = str(reasons.get(case) or "")
    right = (marks[case] == 1) if case in marks else None
    whys = load(vd / "why.json", {}) or {}
    why = whys.get(case) if isinstance(whys, dict) and isinstance(whys.get(case), dict) and right is False else None
    ids = (said_by or {}).get(case) or []
    used = [(names or {}).get(i) for i in ids if not str(i).startswith("vq:")]
    queries = len({i for i in ids if str(i).startswith("vq:")})
    rows = result.get("rows") if isinstance(result, dict) and isinstance(result.get("rows"), list) else None
    return {"value": shown_beside(result, known) if rows else None,
            "answer": str(answer.get("answer") or "").strip(), "sql": str(answer.get("sql") or "").strip(),
            "right": right, "why": reason if reason != "match" else "",
            "cause": {"shown": WHY_SHOWN.get(str(why.get("why")), str(why.get("why") or "")),
                      "detail": str(why.get("detail") or "")} if why else None,
            # Metrics by name; saved queries counted, since a saved query's name is its whole question.
            "used": [n for k, n in enumerate(used) if n and n not in used[:k]]
            + ([("a saved query" if queries == 1 else f"{queries} saved queries")] if queries else []),
            "rows": _first_rows(result) if rows is not None else None}


# A miss's cause as the report shows it: the why file names it in the report's own words.
WHY_SHOWN = {"Different rule": "Different definition", "No definition": "No metric fit", "Query failed": "Its SQL failed"}
# The rows a question shows of each side, known and answered.
FIRST_ROWS = 5


def _first_rows(result: dict) -> dict:
    cols = [c.get("name") if isinstance(c, dict) else c for c in result.get("columns") or []]
    return {"columns": cols, "rows": [list(r) for r in (result.get("rows") or [])[:FIRST_ROWS] if isinstance(r, list)]}


def _results(vd: pathlib.Path) -> list:
    import analytics_eval
    return analytics_eval.read_results(vd / "results.jsonl") if (vd / "results.jsonl").is_file() else []


def _scored(vd: pathlib.Path):
    """Each question's mark in a pass, as the accuracy card reads them, and the reason each was graded so."""
    import analytics_eval
    rows = _results(vd)
    return analytics_eval.case_means(rows), {r["prompt_id"]: r.get("reason") for r in rows if r.get("rep") in (0, "0")}


def _said_by(vd: pathlib.Path) -> dict:
    """The metric and saved query ids each answer used, as its row records them."""
    return {r["prompt_id"]: [str(i) for i in r.get("used_context_ids") or []] for r in _results(vd)}


def _names(run) -> dict:
    """Each metric's and saved query's name by the id an answer records."""
    if run is None:
        return {}
    out = {}
    for file in ("metrics.json", "queries.json"):
        for b in load(pathlib.Path(run) / "context" / file, []) or []:
            if isinstance(b, dict) and b.get("source_id"):
                out[str(b["source_id"])] = str(b.get("display_name") or b.get("name") or "")
    return out


def _digest(sql: str) -> str:
    """A known answer's SQL, the same however it is spaced or cased: a check that rewords its questions keeps it."""
    import hashlib
    return hashlib.sha256(" ".join(str(sql or "").lower().split()).encode()).hexdigest()[:16]


def _gold_sql(ev: pathlib.Path) -> dict:
    out = {}
    try:
        lines = (ev / "eval" / "answers.jsonl").read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("gold_sql"):
            out[str(r.get("case_id"))] = str(r["gold_sql"])
    return out


def _context_passes(ev: pathlib.Path) -> list:
    """The passes with the context, newest first: a test again writes v1, v2 beside the first."""
    hill = ev / "hillclimb"
    passes = sorted((d for d in hill.glob("*") if d.is_dir() and d.name != "raw"), key=lambda d: d.stat().st_mtime,
                    reverse=True)
    return passes or [hill / "baseline"]


def _context_pass(ev: pathlib.Path) -> pathlib.Path:
    return _context_passes(ev)[0]


def _covers(vd: pathlib.Path, case: str) -> bool:
    """Whether a pass tests this question: one that tested a few again names them (`EVAL todo --questions`), and leaves
    the rest to the passes before it."""
    only = load(vd / "questions.json")
    return not isinstance(only, list) or case in only


def _day(stamp) -> str:
    import datetime
    try:
        t = datetime.datetime.fromisoformat(str(stamp))
    except ValueError:
        return ""
    return "%d %s" % (t.day, t.strftime("%b"))


def _earlier(ev: pathlib.Path, run) -> dict:
    """How each known answer did in this run's earlier checks, newest first, by its SQL's digest: a check numbers and
    words its questions afresh, and 11 then 8 of 11 on the same questions read as a regression (2026-11-26)."""
    if run is None:
        return {}
    run = pathlib.Path(run)
    project = run.parent.parent if run.parent.name in ROOTS else run.parent
    pattern = re.compile(re.escape(run.name) + r"(-\d+)?")
    root = project / ".atlan-evals"
    try:
        mine = (ev / "work" / "spec.json").stat().st_mtime
        others = sorted((root / n for n in os.listdir(root) if pattern.fullmatch(n) and (root / n).resolve() != ev.resolve()
                         and (root / n / "work" / "spec.json").is_file()
                         and (root / n / "work" / "spec.json").stat().st_mtime < mine),
                        key=lambda d: -(d / "work" / "spec.json").stat().st_mtime)
    except OSError:
        return {}
    out: dict = {}
    for other in others:
        spec = load(other / "work" / "spec.json", {}) or {}
        asked = {str(r.get("id")): str(r.get("question") or "") for r in spec.get("records") or [] if isinstance(r, dict)}
        marks, _ = _scored(_context_pass(other))
        when = _day((load(_context_pass(other) / "summary.json", {}) or {}).get("scored_at"))
        for case, sql in _gold_sql(other).items():
            if case in marks:
                out.setdefault(_digest(sql), []).append({"when": when, "right": marks[case] == 1, "question": asked.get(case, "")})
    return out


def _changed_after(run, scored_at) -> bool:
    """Whether the run's metrics changed after the check scored them: its marks are then for the old rules."""
    import datetime
    try:
        at = datetime.datetime.fromisoformat(str(scored_at)).timestamp()
        return run is not None and (pathlib.Path(run) / "context" / "metrics.json").stat().st_mtime > at + 60
    except (OSError, ValueError, TypeError):
        return False


def questions(ev: pathlib.Path):
    """Every question of a check, numbered as every report numbers it, with the metric it tests, its known answer,
    and what each pass has answered and scored so far: `agent` with the context, `names` with column names only;
    its score, how each question did in earlier checks, and whether the metrics changed after it. The side page reads
    it live; `check.py show` writes its table from it. None before a set is loaded."""
    ev = pathlib.Path(ev)
    spec = load(ev / "work" / "spec.json")
    if not isinstance(spec, dict):
        return None
    picked = {s.get("id"): s for s in load(ev / "work" / "selection.json", []) or [] if isinstance(s, dict)}
    passes, names = _context_passes(ev), ev / "hillclimb" / "raw"
    scored, said = {vd: _scored(vd) for vd in passes}, {vd: _said_by(vd) for vd in passes}
    # Each question's newest pass that tests it: one tested again alone showed beside every other question waiting.
    tested_in = {r["id"]: next((vd for vd in passes if _covers(vd, r["id"])), passes[0])
                 for r in spec.get("records") or []}
    nm, nr = _scored(names)
    run = run_of(ev) if ev.parent.name != "gen" else next(
        (r for r in built_runs(ev.parent.parent.parent) if r.name == ev.name), None)
    called, gold_sql, earlier = _names(run), _gold_sql(ev), _earlier(ev, run)
    nsaid = _said_by(names)
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
                     "known_rows": _first_rows(known) if gold.is_file() and isinstance(known, dict) else None,
                     "agent": _pass(tested_in[case], case, *scored[tested_in[case]], known, said[tested_in[case]], called),
                     "names": _pass(names, case, nm, nr, known, nsaid, called),
                     "earlier": earlier.get(_digest(gold_sql[case]), []) if case in gold_sql else []})
    answered = sum(1 for r in rows if r["agent"])
    # Scored once every question has its row in each pass that ran, a row that counts for nothing included.
    done = bool(rows) and all(r["id"] in scored[tested_in[r["id"]]][1] for r in rows) and (
        not names.is_dir() or all(r["id"] in nr for r in rows))
    ar = {k: v for vd in passes for k, v in scored[vd][1].items()}
    scored_at = next((s for s in ((load(vd / "summary.json", {}) or {}).get("scored_at") for vd in passes) if s), None)

    def tally(key):
        return [sum(1 for r in rows if (r[key] or {}).get("right") is True),
                sum(1 for r in rows if (r[key] or {}).get("right") is not None)]
    return {"total": len(rows), "answered": answered, "rows": rows, "name": str(spec.get("name") or ""),
            "state": "scored" if done else "running" if answered or any(r["names"] for r in rows) else "ready",
            "score": {"agent": tally("agent"), "names": tally("names"), "scored_at": scored_at} if ar else None,
            "changed_after": _changed_after(run, scored_at)}
