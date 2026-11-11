#!/usr/bin/env python3
"""check.py: run any eval set against the context built in a project.

An eval set is a JSONL file in <project>/.atlan-evals/sets/, one question per line: {"id",
"question", "sql"}, plus optional "tests" (the metric it checks), "from" (where the question came
from) and "note" (the rule the SQL encodes). A generated set and a brought one look the same.

    python3 check.py start --project <project>
    python3 check.py load --project <project> --file <set.jsonl>

`start` finds the built run, where its context lives and the sets the project holds. `load` puts a
set in a fresh eval folder beside the run, runs each trusted SQL on the run's data and sets the
questions up for scoring. Every command prints one JSON object; exit 2 means something the person
must supply. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys

BUILD = pathlib.Path(__file__).resolve().parents[2] / "analytics-build" / "scripts"
RUNNER, HARNESS = BUILD / "project.py", BUILD / "analytics_eval.py"


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


def saved_to(run: pathlib.Path):
    """The Atlan Trident workspace the run's context was saved to, or None for this machine only:
    the run's publication receipt, else what a save recorded beside the context."""
    ws = ((load(run / "run.json", {}) or {}).get("publication") or {}).get("workspace_id")
    for f in [] if ws else sorted((run / "context").glob("published*.json")):
        rec = load(f)
        for r in rec if isinstance(rec, list) else [rec]:
            if isinstance(r, dict) and str(r.get("workspace_id") or "").startswith("workspace_"):
                return r["workspace_id"]
    return ws


def evals_dir(project: pathlib.Path, run: pathlib.Path) -> pathlib.Path:
    """A fresh eval folder for this run: a scored set never changes, so a repeat check gets -2, -3."""
    base = project / ".atlan-evals" / run.name
    n, ev = 1, base
    while (ev / "hillclimb").exists() or (ev / "work").exists():
        n += 1
        ev = base.with_name(f"{run.name}-{n}")
    return ev


def sets(project: pathlib.Path) -> list:
    found = []
    for f in sorted((project / ".atlan-evals" / "sets").glob("*.jsonl")):
        rows = [l for l in f.read_text().splitlines() if l.strip()]
        found.append({"file": str(f), "name": f.stem, "questions": len(rows)})
    return found


def cmd_start(a) -> dict:
    project = pathlib.Path(a.project)
    run = find_run(project)
    interaction = load(run / "interaction.json", {}) or {}
    return {"status": "ok", "run": str(run), "context_saved_to": saved_to(run),
            "use_case": interaction.get("intent") or {}, "sets": sets(project)}


def gold(run: pathlib.Path, ev: pathlib.Path, records: list) -> dict:
    """Run each trusted SQL on the run's bound data. One that fails or returns nothing leaves the set."""
    gdir, req = ev / "eval" / "gold", ev / "eval" / "requests"
    req.mkdir(parents=True, exist_ok=True)
    out, failed = [], []
    for r in records:
        rq = req / f"{r['id']}.json"
        rq.write_text(json.dumps({"sql": r["golden_sql"], "limit": 1000}))
        proc = subprocess.run([sys.executable, str(RUNNER), "--run-dir", str(run), "execute", str(rq)],
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
        res["ran_at"] = res.get("ran_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        dump(gdir / f"{r['id']}.json", res)
        out.append({"id": r["id"], "rows": len(res["rows"]), "first": res["rows"][0][:4]})
    return {"ran": out, "failed": failed}


def stage(ev: pathlib.Path, slug: str) -> None:
    """Set the questions up for scoring (EVAL init and import-spec). Nothing is stored anywhere new."""
    for step in (["init", "--slug", slug], ["import-spec", "--spec", str(ev / "work" / "spec.json")]):
        proc = subprocess.run([sys.executable, str(HARNESS), step[0], "--dir", str(ev), *step[1:]],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode:
            raise Blocked(f"Setting the questions up for scoring failed at {step[0]}: {(proc.stdout or proc.stderr)[-300:]}")


def cmd_load(a) -> dict:
    project = pathlib.Path(a.project)
    run = find_run(project)
    ev = evals_dir(project, run)
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
    ran = gold(run, ev, records)
    kept = {g["id"] for g in ran["ran"]}
    records = [r for r in records if r["id"] in kept]
    if not records:
        raise Blocked(f"No trusted SQL in {src.name} ran on this run's data: " +
                      "; ".join(f"{f['id']}: {f['why']}" for f in ran["failed"]))
    by_id = {str(r.get("id") or f"q{i}"): r for i, r in enumerate(rows, 1)}
    dump(ev / "work" / "spec.json", {"name": src.stem, "source_description": f"eval set {src.name}", "records": records})
    dump(ev / "work" / "selection.json", [{"id": rec["id"], "metric": by_id[rec["id"]].get("tests") or "",
                                           "from": by_id[rec["id"]].get("from") or f"eval set {src.stem}",
                                           "expected": next(g["first"] for g in ran["ran"] if g["id"] == rec["id"])}
                                          for rec in records])
    stage(ev, re.sub(r"[^a-z0-9]+", "-", src.stem.lower()).strip("-") or "eval-set")
    # eval-generation writes where each question came from; a set the person brought does not say.
    generated = all(r.get("from") for r in rows)
    return {"status": "ok", "run": str(run), "evals": str(ev), "context_saved_to": saved_to(run), "set": src.stem,
            "generated": generated,
            "questions": len(records), "dropped": ran["failed"], "selection": str(ev / "work" / "selection.json")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    s = p.add_subparsers(dest="cmd", required=True)
    c = s.add_parser("start", help="the built run, where its context lives and the project's eval sets")
    c.add_argument("--project", required=True)
    c.set_defaults(func=cmd_start)
    c = s.add_parser("load", help="a set in a fresh eval folder, trusted answers run, ready to score")
    c.add_argument("--project", required=True)
    c.add_argument("--file", required=True)
    c.set_defaults(func=cmd_load)
    a = p.parse_args(argv)
    try:
        print(json.dumps(a.func(a), indent=1))
        return 0
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
