#!/usr/bin/env python3
"""scope.py: the use case and the metric-aware question pool for an eval set.

The analytics run already holds what an eval set needs to be about something: its use case
(`interaction.json`), the metrics built for it (`context/metrics.json`), and every dashboard
tile and history shape the engine verified, each linked to the metrics it computes
(`context/queries.json`, `engine/metric_cards.json`). This script turns that into eval-builder
input, then picks the set: the focus metric first, then breadth. Standard library only.

    python3 scope.py inputs --project <project> [--run <run-dir>] [--evals <evals-run>]
    python3 scope.py pick --evals <evals-run> [--limit 5] [--focus-count 2]
    python3 scope.py gold --run <run-dir> --evals <evals-run> --project <project.py>
    python3 scope.py sets --project <project>
    python3 scope.py load --project <project> --file <set.jsonl>

An existing eval set is a JSONL file, one question per line: {"id", "question", "sql"}, plus
optional "tests" (the metric it checks) and "note" (the rule the SQL encodes). Sets live in
<project>/.atlan-evals/sets/, where an answering agent that reads one is caught as contaminated.

Every command prints one JSON object. Exit 2 means something the person must supply.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

# Words that say nothing about which metric a use case needs.
_STOP = {"a", "an", "and", "the", "of", "to", "in", "on", "for", "by", "or", "is", "it", "its", "with",
         "which", "what", "why", "how", "that", "this", "build", "agent", "answers", "answer", "each",
         "per", "as", "at", "from", "be", "are", "was", "were", "their", "they", "not", "all", "one"}


class Blocked(Exception):
    pass


def load(path: pathlib.Path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def dump(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def find_run(project: pathlib.Path) -> pathlib.Path:
    """The project's built analytics run: one with metrics. Several ask which use case."""
    ready = sorted((r for r in (project / ".atlan-metrics").glob("run_*")
                    if (r / "context" / "metrics.json").is_file()),
                   key=lambda r: (r / "run.json").stat().st_mtime if (r / "run.json").exists() else 0, reverse=True)
    if not ready:
        raise Blocked("No built analytics run in this project. Build the context first.")
    if len(ready) > 1:
        cases = [f"{r.name}: {((load(r / 'interaction.json', {}) or {}).get('intent') or {}).get('purpose') or 'no use case'}"
                 for r in ready]
        raise Blocked("Several built runs; ask which use case to test: " + "; ".join(cases))
    return ready[0]


RUNNER = pathlib.Path(__file__).resolve().parents[2] / "analytics-build" / "scripts" / "project.py"


def saved_to(run: pathlib.Path):
    """The Atlan Trident workspace the run's context was saved to, or None for this machine only."""
    return ((load(run / "run.json", {}) or {}).get("publication") or {}).get("workspace_id")


def evals_dir(project: pathlib.Path, run: pathlib.Path) -> pathlib.Path:
    """A fresh eval folder for this run: a scored set never changes, so a repeat check gets -2, -3."""
    base = project / ".atlan-evals" / run.name
    n, ev = 1, base
    while (ev / "hillclimb").exists():
        n += 1
        ev = base.with_name(f"{run.name}-{n}")
    return ev


def cmd_inputs(a) -> dict:
    project = pathlib.Path(a.project)
    existing = cmd_sets(a)["sets"]
    if existing and not a.generate:
        # The person may already hold the questions they trust: offer those before generating.
        return {"status": "choose", "sets": existing,
                "next": "Ask once which questions to test with: each set, or Generate 5 new questions. "
                        "Then `load --file <set>`, or `inputs --generate`."}
    run = pathlib.Path(a.run) if a.run else find_run(project)
    ev = pathlib.Path(a.evals) if a.evals else evals_dir(project, run)
    interaction = load(run / "interaction.json", {}) or {}
    intent = interaction.get("intent") or {}
    if not (intent.get("domain") or intent.get("purpose")):
        raise Blocked("The run records no use case. Ask for it once: which agent, answering what.")
    metrics = load(run / "context" / "metrics.json", []) or []
    queries = load(run / "context" / "queries.json", []) or []
    cards = load(run / "engine" / "metric_cards.json", {}) or {}
    if not metrics or not queries:
        raise Blocked("The run has no built metrics or queries yet. Build the context first.")
    refs_by_vq = cards.get("metric_refs_by_vq") or {}
    names = {c.get("metric_ref"): c.get("display_name") or c.get("name") for c in cards.get("metrics") or []}
    focus = (interaction.get("focus") or "").removeprefix("metric:")
    focus_ref = next((r for r, n in names.items() if n == focus), None)

    pool, rows = {}, []
    for q in queries:
        ext = ((q.get("definition") or {}).get("extensions") or {}).get("metrics_agent") or {}
        evid = ext.get("evidence") or {}
        vq = ext.get("vq_id") or evid.get("vq_id")
        sql = (q.get("definition") or {}).get("sql")
        if not vq or not sql:
            continue
        prov = evid.get("provenance") or {}
        hist = prov.get("history") or {}
        lane = "tile" if prov.get("lane") == "bi" else "history"
        pool[vq] = {"source": lane, "metrics": refs_by_vq.get(vq, []),
                    "runs": hist.get("n_exec"), "tile": ((prov.get("bi") or {}).get("visual"))}
        rows.append({"id": vq, "sql": sql, "dialect": (q.get("definition") or {}).get("dialect"),
                     "exec": hist.get("n_exec") or (1 if lane == "history" else None),
                     "users": hist.get("n_distinct_users")})
    src = ev / "src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    (src / "pool.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    schema = run / "context" / "database-schema.json"
    if schema.is_file():
        shutil.copy(schema, src / "database-schema.json")
    tables = sorted({t for m in metrics for t in _tables(m)})
    text = {c.get("metric_ref"): f"{c.get('display_name') or ''} {c.get('description') or ''}"
            for c in cards.get("metrics") or []}
    want = _words(f"{intent.get('domain') or ''} {intent.get('purpose') or ''}")
    scope = {"use_case": intent, "focus": focus_ref,
             "metrics": [{"ref": r, "name": n, "relevance": len(want & _words(text.get(r, "")))}
                         for r, n in names.items()],
             "related": _related(run, focus_ref), "pool": pool}
    dump(ev / "scope.json", scope)
    dialect = next((r["dialect"] for r in rows if r.get("dialect")), None)
    return {"status": "ok", "run": str(run), "evals": str(ev), "context_saved_to": saved_to(run), "use_case": intent, "focus": names.get(focus_ref) or None,
            "pool": len(rows), "tiles": sum(1 for p in pool.values() if p["source"] == "tile"),
            "dialect": dialect, "tables": tables, "src": str(src)}


def _words(text: str) -> set:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2}


def _tables(metric: dict) -> list:
    entity = ((metric.get("definition") or {}).get("source") or {}).get("entity")
    return [entity] if entity else []


def _related(run: pathlib.Path, focus_ref) -> list:
    """Metrics one edge away from the focus, nearest first."""
    edges = (load(run / "engine" / "metric_relationships.json", {}) or {}).get("edges") or []
    out = []
    for e in sorted(edges, key=lambda e: -(e.get("confidence") or 0)):
        for a, b in ((e.get("from_ref"), e.get("to_ref")), (e.get("to_ref"), e.get("from_ref"))):
            if a == focus_ref and b and b not in out:
                out.append(b)
    return out


def cmd_pick(a) -> dict:
    ev = pathlib.Path(a.evals)
    work = ev / "work"
    scope = load(ev / "scope.json")
    cands = load(work / "candidates.json")
    if not scope or cands is None:
        raise Blocked("Run `inputs`, then eval-builder `build`, before `pick`.")
    pool = scope["pool"]
    names = {m["ref"]: m["name"] for m in scope["metrics"]}

    def metric_of(c):
        refs = (pool.get(c.get("query_id")) or {}).get("metrics") or []
        return refs[0] if refs else None

    def strength(c):  # a tile first, then how often analysts ran it, then the engine's own score
        p = pool.get(c.get("query_id")) or {}
        return (p.get("source") != "tile", -(p.get("runs") or 0), -sum((c.get("scores") or {}).values()))

    by_metric: dict = {}
    for c in cands:
        m = metric_of(c)
        if m:
            by_metric.setdefault(m, []).append(c)
    for lst in by_metric.values():
        lst.sort(key=strength)
    focus = scope.get("focus")
    relevance = {m["ref"]: m.get("relevance") or 0 for m in scope["metrics"]}
    related = [m for m in scope.get("related") or [] if m in by_metric and m != focus]
    order = ([focus] if focus in by_metric else []) + related + \
        sorted((m for m in by_metric if m != focus and m not in related),
               key=lambda m: (-relevance.get(m, 0), strength(by_metric[m][0])))
    chosen, taken = [], {m: 0 for m in by_metric}
    if focus in by_metric:
        for c in by_metric[focus][:a.focus_count]:
            chosen.append((focus, c))
            taken[focus] += 1
    breadth = [m for m in order if m != focus] + ([focus] if focus in by_metric else [])
    while len(chosen) < a.limit:  # breadth: one per metric per pass, the focus metric last
        added = False
        for m in breadth:
            if len(chosen) < a.limit and taken[m] < len(by_metric[m]):
                chosen.append((m, by_metric[m][taken[m]]))
                taken[m] += 1
                added = True
        if not added:
            break

    keep = {c["id"] for _, c in chosen}
    dump(work / "candidates.json", [c for c in cands if c["id"] in keep])
    answers = load(work / "answers.json", {}) or {}
    dump(work / "answers.json", {k: v for k, v in answers.items() if k in keep})
    for p in (work / "prompts").glob("*.txt") if (work / "prompts").exists() else []:
        if p.stem not in keep:
            p.unlink()
    selection = [{"id": c["id"], "metric": names.get(m, m), "focus": m == focus,
                  "from": "dashboard tile" if (pool.get(c["query_id"]) or {}).get("source") == "tile" else
                  f"past query, run {(pool.get(c['query_id']) or {}).get('runs') or 1} times"}
                 for m, c in chosen]
    dump(work / "selection.json", selection)
    return {"status": "ok", "picked": len(selection), "of": len(cands),
            "metrics_covered": len({s["metric"] for s in selection}), "selection": selection}


def cmd_sets(a) -> dict:
    """The eval sets this project already holds, so the person can pick one instead of generating."""
    found = []
    for f in sorted((pathlib.Path(a.project) / ".atlan-evals" / "sets").glob("*.jsonl")):
        rows = [l for l in f.read_text().splitlines() if l.strip()]
        found.append({"file": str(f), "name": f.stem, "questions": len(rows)})
    return {"status": "ok", "sets": found}


def cmd_load(a) -> dict:
    """An existing eval set in place of generation: the same files generate leaves, ready to run."""
    project = pathlib.Path(a.project)
    run = pathlib.Path(a.run) if a.run else find_run(project)
    ev = pathlib.Path(a.evals) if a.evals else evals_dir(project, run)
    src = pathlib.Path(a.file)
    rows = []
    for n, line in enumerate(src.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            raise Blocked(f"{src.name} line {n} is not JSON.")
        if not (r.get("question") and r.get("sql")):
            raise Blocked(f"{src.name} line {n} needs a question and its sql.")
        rows.append(r)
    if not rows:
        raise Blocked(f"{src.name} holds no questions.")
    records = [{"id": str(r.get("id") or f"q{i}"), "question": r["question"], "question_spec": r.get("note") or "",
                "golden_sql": r["sql"], "review_status": "approved", "provenance": {"source": f"eval set {src.stem}"},
                "modes": []} for i, r in enumerate(rows, 1)]
    work = ev / "work"
    dump(work / "spec.json", {"name": src.stem, "source_description": f"eval set {src.name}", "records": records})
    dump(work / "selection.json", [{"id": rec["id"], "metric": r.get("tests") or "", "focus": False,
                                    "from": f"eval set {src.stem}"} for rec, r in zip(records, rows)])
    interaction = load(run / "interaction.json", {}) or {}
    dump(ev / "scope.json", {"use_case": interaction.get("intent") or {}, "set": str(src), "pool": {}})
    # A set the person brought is already approved, so its trusted answers run now and it is ready to test.
    gold = cmd_gold(argparse.Namespace(run=str(run), evals=str(ev), runner=getattr(a, "runner", None)))
    return {"status": "ok", "run": str(run), "evals": str(ev), "context_saved_to": saved_to(run),
            "set": src.stem, "questions": len(records), "gold_ran": gold["ran"], "gold_failed": gold["failed"]}


def cmd_gold(a) -> dict:
    """Run each reviewed record's trusted SQL on the run's bound data, through PROJECT execute."""
    ev = pathlib.Path(a.evals)
    spec = load(ev / "work" / "spec.json")
    if not spec:
        raise Blocked("No reviewed set: run eval-builder `review` first.")
    gold, req = ev / "eval" / "gold", ev / "eval" / "requests"
    gold.mkdir(parents=True, exist_ok=True)
    req.mkdir(parents=True, exist_ok=True)
    out, failed = [], []
    for r in spec.get("records") or []:
        rq = req / f"{r['id']}.json"
        rq.write_text(json.dumps({"sql": r["golden_sql"], "limit": 1000}))
        proc = subprocess.run([sys.executable, str(a.runner or RUNNER), "--run-dir", a.run, "execute", str(rq)],
                              capture_output=True, text=True, timeout=120)
        try:
            res = json.loads(proc.stdout)
        except json.JSONDecodeError:
            res = {"status": "error", "message": (proc.stderr or proc.stdout)[:300]}
        if res.get("status") != "ok" or not res.get("rows") or res.get("truncated"):
            why = "no rows" if res.get("status") == "ok" and not res.get("rows") else \
                "truncated" if res.get("truncated") else res.get("message") or "did not run"
            failed.append({"id": r["id"], "why": why})
            continue
        res["ran_at"] = res.get("ran_at") or _now()
        dump(gold / f"{r['id']}.json", res)
        cols = [c.get("name") if isinstance(c, dict) else c for c in res.get("columns") or []]
        out.append({"id": r["id"], "rows": len(res["rows"]), "columns": cols, "first": res["rows"][0][:4]})
    return {"status": "ok", "ran": len(out), "failed": failed, "preview": out}


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    s = p.add_subparsers(dest="cmd", required=True)
    c = s.add_parser("inputs", help="eval-builder input and scope.json from the analytics run")
    c.add_argument("--project", required=True, help="the folder holding .atlan-metrics/")
    c.add_argument("--run", help="a run to use instead of the project's one built run")
    c.add_argument("--evals", help="an eval folder to use instead of a fresh one")
    c.add_argument("--generate", action="store_true", help="generate even though eval sets exist")
    c.set_defaults(func=cmd_inputs)
    c = s.add_parser("pick", help="the set: the focus metric first, then breadth")
    c.add_argument("--evals", required=True)
    c.add_argument("--limit", type=int, default=5)
    c.add_argument("--focus-count", type=int, default=2)
    c.set_defaults(func=cmd_pick)
    c = s.add_parser("sets", help="the eval sets this project already holds")
    c.add_argument("--project", required=True)
    c.set_defaults(func=cmd_sets)
    c = s.add_parser("load", help="use an existing eval set instead of generating one")
    c.add_argument("--project", required=True)
    c.add_argument("--file", required=True)
    c.add_argument("--run")
    c.add_argument("--evals")
    c.set_defaults(func=cmd_load)
    c = s.add_parser("gold", help="run each reviewed record's trusted SQL on the run's data")
    c.add_argument("--run", required=True)
    c.add_argument("--evals", required=True)
    c.add_argument("--runner", help="another analytics-build scripts/project.py (tests only)")
    c.set_defaults(func=cmd_gold)
    a = p.parse_args(argv)
    try:
        print(json.dumps(a.func(a), indent=1))
        return 0
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
