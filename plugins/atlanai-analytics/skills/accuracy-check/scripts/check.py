#!/usr/bin/env python3
"""check.py: run any eval set against the context built in a project.

An eval set is a JSONL file in <project>/.atlan-evals/sets/, one question per line: {"id",
"question", "sql"}, plus optional "tests" (the metric it checks), "from" (where the question came
from) and "note" (the rule the SQL encodes). A generated set and a brought one look the same.

    python3 check.py start --project <project> [--run <run-dir>]
    python3 check.py load --project <project> --file <set.jsonl> [--run <run-dir>]
    python3 check.py gold --evals <eval-dir> [--transcript <file> ...]
    python3 check.py show --evals <eval-dir>
    python3 check.py edit --evals <eval-dir> --question <number> --text "<new wording>"
    python3 check.py drop --evals <eval-dir> --questions <numbers or ids>
    python3 check.py link --evals <eval folder> --org <organization slug>

`start` finds the built run, where its context lives, how its SQL is executed and the sets the
project holds. `load` puts a set in a fresh eval folder in .atlan-evals/ and gets each question's
trusted answer. On a run bound to a local database it runs each trusted SQL there and sets the
questions up for scoring. On a warehouse run nothing on this machine can run SQL, so `load` writes
each trusted SQL to a file and returns `pending_gold`: the host runs each one with the warehouse
query tool, and `gold` reads the results (from the transcript of the agent that ran them, or from
result files) and sets the questions up for scoring. `link` gives the Atlan pages of a saved check:
the agent's runs and the last run. Every command prints one JSON object; exit 2 means something
the person must supply. Standard library only.
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
sys.path.insert(0, str(BUILD))

from eval_runs import Blocked, execution, find_run, load, use_case  # noqa: E402


def dump(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str))


def _values(node):
    """Every leaf value of a save's id map, flat or grouped by kind."""
    if isinstance(node, dict):
        for v in node.values():
            yield from _values(v)
    elif isinstance(node, list):
        for v in node:
            yield from _values(v)
    else:
        yield node


def saved_to(run: pathlib.Path):
    """The Atlan Trident workspace the run's context was saved to, or None for this machine only:
    the run's publication receipt, else what a save recorded beside the context."""
    ws = ((load(run / "run.json", {}) or {}).get("publication") or {}).get("workspace_id")
    # The save's approved plan is written by code, whatever shape the save's own records take.
    auth = load(run / "authorization.json", {}) or {}
    if not ws and auth.get("approved_plans") and str(auth.get("workspace_id") or "").startswith("workspace_"):
        return auth["workspace_id"]
    for f in [] if ws else sorted((run / "context").glob("published*.json")):
        rec = load(f)
        for r in rec if isinstance(rec, list) else [rec]:
            if isinstance(r, dict) and str(r.get("workspace_id") or "").startswith("workspace_"):
                return r["workspace_id"]
    # A save's id map names no workspace; the approved plan beside it does.
    ids = load(run / "context" / "published.json", {}) if not ws else {}
    if any(str(v).startswith(("metric_", "sql_query_")) for v in _values(ids)):
        ws = (load(run / "authorization.json", {}) or {}).get("workspace_id")
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


def checks(project: pathlib.Path, run: pathlib.Path) -> list:
    """The checks already scored on this run, oldest first: read only when the person asks to
    compare with one. A new request to check accuracy never starts from these."""
    found = []
    for ev in sorted((project / ".atlan-evals").glob(run.name + "*"), key=lambda p: p.stat().st_mtime):
        runs = sorted(d.name for d in (ev / "hillclimb").glob("*") if (d / "results.jsonl").is_file())
        if runs:
            spec = load(ev / "work" / "spec.json", {}) or {}
            found.append({"evals": str(ev), "set": spec.get("name"), "questions": len(spec.get("records") or []),
                          "runs": runs,
                          "scored_at": datetime.datetime.fromtimestamp(
                              max((ev / "hillclimb" / r / "results.jsonl").stat().st_mtime for r in runs),
                              datetime.timezone.utc).isoformat(timespec="minutes")})
    return found


def cmd_start(a) -> dict:
    project = pathlib.Path(a.project)
    run = find_run(project, a.run)
    found = sets(project)
    # Every check runs the raw-source pass beside the context pass, whoever starts it: a person
    # asking here directly, the build or the demo. There is no way to leave it out.
    before_after = True
    out = {"status": "ok", "run": str(run), "context_saved_to": saved_to(run),
           "use_case": use_case(run), "execution": execution(run), "sets": found,
           "before_after": before_after,
           "checks": checks(project, run),
           "new_check": "Every request to check accuracy starts a new eval folder. Use `checks` only "
                        "when the person asks to compare with an earlier run."}
    if not found:
        out["next"] = "generate"
        return out
    # With a set in the folder the person chooses, even when there is one: a caller that skips
    # this question tests questions nobody picked.
    q = lambda n: f"{n} question{'' if n == 1 else 's'}"  # noqa: E731
    names = ", ".join(f"{s['name']}, {q(s['questions'])}" for s in found)
    out["ask"] = {
        "question": "Which questions should I test with?",
        "text": (f"This folder has {len(found)} eval set{'s' if len(found) != 1 else ''}: {names}, with their "
                 "trusted answers.\nNext I ask each question to a fresh agent that sees only your context, "
                 + ("and to one that sees only the raw tables, and compare both answers with the trusted one. "
                    if before_after else "and compare its answer with the trusted one. ")
                 + "Which questions should I test with?"),
        "options": [{"label": f"{s['name']} ({q(s['questions'])})",
                     "description": "The eval set you already have."} for s in found]
                   + [{"label": f"Generate {a.questions} new questions",
                       "description": "From your dashboard and the queries your analysts run most."}]}
    return out


MAX_ROWS = 100  # a trusted result longer than this is a list of records, not an answer


def _keep(res: dict):
    """Why a trusted result cannot anchor a question, or None when it can."""
    if res.get("error"):
        return str(res["error"])[:300]
    if res.get("truncated"):
        return "truncated"
    if not res.get("rows"):
        return "no rows"
    if len(res["rows"]) > MAX_ROWS:
        return f"{len(res['rows'])} rows: too long a list for one question to reproduce"
    return None


def gold_local(run: pathlib.Path, ev: pathlib.Path, records: list) -> dict:
    """Run each trusted SQL on the run's bound database. One that fails or returns nothing leaves the set."""
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
        if res.get("status") != "ok":
            res = {"error": res.get("message") or res.get("reason") or "did not run"}
        why = _keep(res)
        if why:
            failed.append({"id": r["id"], "why": why})
            continue
        res["ran_at"] = res.get("ran_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        dump(gdir / f"{r['id']}.json", res)
        out.append({"id": r["id"], "rows": len(res["rows"]), "first": res["rows"][0][:4]})
    return {"ran": out, "failed": failed}


def gold_host(ev: pathlib.Path, records: list, transcripts: list) -> dict:
    """Read each trusted SQL's result as the host ran it on the warehouse: a result file saved at
    eval/gold-in/<id>.json (whatever shape the warehouse tool or CLI returned), else the result of
    the tool call that ran exactly that SQL in one of the transcripts. No model copies a value."""
    import analytics_eval as harness
    gdir, inbox = ev / "eval" / "gold", ev / "eval" / "gold-in"
    out, failed, missing = [], [], []
    for r in records:
        res, why, source = None, "no result file and no transcript given", None
        given = inbox / f"{r['id']}.json"
        if given.is_file():
            res, source = harness.normalize_result(given.read_text()), "file"
        else:
            for t in transcripts:
                got, reason = harness.transcript_result(t, r["golden_sql"])
                if got is not None:
                    res, source = got, "transcript"
                    break
                why = reason
        if res is None and re.search(r"truncated|capped the rows", why):
            # It ran, and returned more rows than one response holds: too many to compare whole.
            failed.append({"id": r["id"], "why": "too many rows to compare: the result came back in pages"})
            continue
        if res is None:  # the SQL was not run as written: not the question's fault, so it is not dropped
            missing.append({"id": r["id"], "why": why})
            continue
        why = _keep(res)
        if why:
            failed.append({"id": r["id"], "why": why})
            continue
        gold = {"columns": res.get("columns") or [], "rows": res["rows"], "source": source,
                "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
        dump(gdir / f"{r['id']}.json", gold)
        out.append({"id": r["id"], "rows": len(gold["rows"]), "first": gold["rows"][0][:4]})
    return {"ran": out, "failed": failed, "missing": missing}


def stage(ev: pathlib.Path, slug: str) -> None:
    """Set the questions up for scoring (EVAL init and import-spec). Nothing is stored anywhere new."""
    for step in (["init", "--slug", slug], ["import-spec", "--spec", str(ev / "work" / "spec.json")]):
        proc = subprocess.run([sys.executable, str(HARNESS), step[0], "--dir", str(ev), *step[1:]],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode:
            raise Blocked(f"Setting the questions up for scoring failed at {step[0]}: {(proc.stdout or proc.stderr)[-300:]}")


def read_set(src: pathlib.Path) -> list:
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
    return rows


def finish(ev: pathlib.Path, plan: dict, ran: dict) -> dict:
    """Keep the questions whose trusted SQL ran, write the spec and selection, and stage them."""
    name, rows, records = plan["set"], plan["rows"], plan["records"]
    kept = {g["id"] for g in ran["ran"]}
    records = [r for r in records if r["id"] in kept]
    if not records:
        raise Blocked(f"No trusted SQL in {name} ran on this run's data: " +
                      "; ".join(f"{f['id']}: {f['why']}" for f in ran["failed"]))
    by_id = {str(r.get("id") or f"q{i}"): r for i, r in enumerate(rows, 1)}
    dump(ev / "work" / "spec.json", {"name": name, "source_description": f"eval set {name}.jsonl", "records": records})
    dump(ev / "work" / "selection.json", [{"id": rec["id"], "metric": by_id[rec["id"]].get("tests") or "",
                                           "from": by_id[rec["id"]].get("from") or f"eval set {name}",
                                           "expected": next(g["first"] for g in ran["ran"] if g["id"] == rec["id"])}
                                          for rec in records])
    stage(ev, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "eval-set")
    run = pathlib.Path(plan["run"])
    return {"status": "ok", "run": str(run), "evals": str(ev), "context_saved_to": saved_to(run), "set": name,
            # eval-generation marks each question it wrote; a set the person brought carries no mark.
            "generated": all(r.get("generated") is True for r in rows), "execution": plan["execution"],
            "questions": len(records), "dropped": ran["failed"], "selection": str(ev / "work" / "selection.json")}


def cmd_load(a) -> dict:
    project = pathlib.Path(a.project)
    run = find_run(project, a.run)
    src = pathlib.Path(a.file)
    rows = read_set(src)
    ev = evals_dir(project, run)
    records = [{"id": str(r.get("id") or f"q{i}"), "question": r["question"], "question_spec": r.get("note") or "",
                "golden_sql": r["sql"], "review_status": "approved", "provenance": {"source": f"eval set {src.stem}"},
                "modes": []} for i, r in enumerate(rows, 1)]
    how = execution(run)
    plan = {"set": src.stem, "run": str(run), "execution": how, "rows": rows, "records": records}
    if how["via"] == "local":
        return finish(ev, plan, gold_local(run, ev, records))
    # A warehouse run: the host runs each trusted SQL. Keep the plan so `gold` can finish it.
    dump(ev / "work" / "load.json", plan)
    pending = []
    for r in records:
        sql = ev / "eval" / "requests" / f"{r['id']}.sql"
        sql.parent.mkdir(parents=True, exist_ok=True)
        sql.write_text(r["golden_sql"].strip() + "\n")
        pending.append({"id": r["id"], "sql_file": str(sql), "result_file": str(ev / "eval" / "gold-in" / f"{r['id']}.json")})
    return {"status": "pending_gold", "run": str(run), "evals": str(ev), "set": src.stem, "execution": how,
            "questions": len(records), "pending": pending,
            "next": "Run each sql_file exactly as written with the warehouse query tool, then `check.py gold "
                    "--evals <evals>` with the transcript of the agent that ran them, or with each raw result "
                    "saved to its result_file."}


def cmd_gold(a) -> dict:
    ev = pathlib.Path(a.evals)
    plan = load(ev / "work" / "load.json")
    if not plan:
        raise Blocked(f"No pending set in {ev}: run `check.py load` first.")
    if (ev / "work" / "spec.json").is_file():
        raise Blocked(f"The set in {ev} is already set up for scoring.")
    for t in a.transcript or []:
        if not pathlib.Path(t).is_file():
            raise Blocked(f"No transcript at {t}.")
    ran = gold_host(ev, plan["records"], a.transcript or [])
    if ran["missing"] and not a.allow_missing:
        raise Blocked("No result yet for " + "; ".join(f"{m['id']}: {m['why']}" for m in ran["missing"]) +
                      ". Run each one's sql_file exactly as written and pass that transcript, or save its raw "
                      "result to eval/gold-in/<id>.json, then run `gold` again.")
    ran["failed"] += [{"id": m["id"], "why": "not run: " + m["why"]} for m in ran["missing"]]
    return finish(ev, plan, ran)


def _cell(value) -> str:
    """One value as a person reads it: a number with separators, a date without a time."""
    if value is None:
        return "none"
    text = str(value).strip()
    try:
        number = float(text)
        if re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
            return f"{int(number):,}" if number.is_integer() else f"{number:,.2f}".rstrip("0").rstrip(".")
    except ValueError:
        pass
    text = re.sub(r"^(\d{4}-\d{2}-\d{2})[ T]00:00(:00(\.0+)?)?([+-]00:?00|Z)?$", r"\1", text)
    return text.replace("|", "/").replace("\n", " ")


def _expected(gold: dict) -> str:
    """A trusted result as a person reads it: one value on its own, otherwise its size and the
    first row's leading values. Column names stay out: a query's aliases (ms, cnt) mean nothing
    to the person."""
    rows = gold.get("rows") or []
    if not rows:
        return "no rows"
    first = [_cell(v) for v in rows[0] if _cell(v) not in ("", "none")]
    if len(rows) == 1 and len(rows[0]) == 1:
        return first[0] if first else "none"
    shown = ", ".join(first[:3]) + (", …" if len(first) > 3 else "")
    return f"1 row: {shown}" if len(rows) == 1 else f"{len(rows)} rows, starting {shown}"


def cmd_show(a) -> dict:
    """The loaded set as one markdown table, for the person to read before it runs: the number
    each question keeps in every report, the question, the metric it tests and its trusted answer."""
    ev = pathlib.Path(a.evals)
    spec = load(ev / "work" / "spec.json")
    if not spec:
        raise Blocked(f"No loaded set in {ev}: run `check.py load` (and `gold` on a warehouse run) first.")
    picked = {s.get("id"): s for s in load(ev / "work" / "selection.json", []) or []}
    clean = lambda t: " ".join(str(t or "").split()).replace("|", "/")  # noqa: E731
    lines = ["| # | Question | Tests | Expected |", "| ---: | --- | --- | --- |"]
    for n, r in enumerate(spec["records"], 1):
        gold = load(ev / "eval" / "gold" / f"{r['id']}.json", {}) or {}
        lines.append(f"| {n} | {clean(r['question'])} | {clean((picked.get(r['id']) or {}).get('metric')) or 'none named'} "
                     f"| {clean(_expected(gold))} |")
    return {"status": "ok", "questions": len(spec["records"]), "table": "\n".join(lines)}


def cmd_edit(a) -> dict:
    """Reword one question of a loaded set before anything is scored. The trusted SQL and its
    answer stay as they are, so a new wording must still ask for exactly what that SQL returns.
    The set file is changed too, so the next check asks the same words."""
    ev = pathlib.Path(a.evals)
    spec = load(ev / "work" / "spec.json")
    if not spec:
        raise Blocked(f"No loaded set in {ev}: run `check.py load` (and `gold` on a warehouse run) first.")
    if any((d / "results.jsonl").is_file() for d in (ev / "hillclimb").glob("*")):
        raise Blocked("This set already has a scored run. A scored set never changes: edit the set file, "
                      "then run a new check.")
    records = spec["records"]
    rec = next((r for n, r in enumerate(records, 1) if a.question in (str(n), r["id"])), None)
    if rec is None:
        raise Blocked(f"No question {a.question} in this set. Name it by its number, 1 to {len(records)}.")
    text = " ".join((a.text or "").split())
    if len(text) < 8 or not text.endswith("?"):
        raise Blocked("Give the new wording as one full question, ending with a question mark.")
    before = rec["question"]
    rec["question"] = text
    dump(ev / "work" / "spec.json", spec)
    dump(ev / "work" / "edited-by-person.json",
         (load(ev / "work" / "edited-by-person.json", []) or []) + [{"id": rec["id"], "from": before, "to": text}])
    set_file = ev.parent / "sets" / f"{spec.get('name')}.jsonl"
    if set_file.is_file():
        rows = [json.loads(l) for l in set_file.read_text().splitlines() if l.strip()]
        for n, r in enumerate(rows, 1):
            if str(r.get("id") or f"q{n}") == rec["id"]:
                r["question"] = text
        set_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    stage(ev, re.sub(r"[^a-z0-9]+", "-", str(spec.get("name") or "eval-set").lower()).strip("-") or "eval-set")
    number = next(n for n, r in enumerate(records, 1) if r is rec)
    return {"status": "ok", "evals": str(ev), "question": number, "id": rec["id"], "from": before, "to": text,
            "set_file": str(set_file) if set_file.is_file() else None}


def cmd_drop(a) -> dict:
    """Take questions the person does not want out of a loaded set, before anything is scored.
    The set file keeps them: a set is the person's own, and the next check can use them again."""
    ev = pathlib.Path(a.evals)
    spec = load(ev / "work" / "spec.json")
    if not spec:
        raise Blocked(f"No loaded set in {ev}: run `check.py load` (and `gold` on a warehouse run) first.")
    if any((d / "results.jsonl").is_file() for d in (ev / "hillclimb").glob("*")):
        raise Blocked("This set already has a scored run. A scored set never changes: load the set again "
                      "for a new check, then drop.")
    records = spec["records"]
    # A question is named by its number in the list the person was shown, or by its id.
    want = {s.strip() for s in a.questions.split(",") if s.strip()}
    gone = [r for n, r in enumerate(records, 1) if str(n) in want or r["id"] in want]
    named = {str(n) for n, r in enumerate(records, 1) if r in gone} | {r["id"] for r in gone}
    if want - named:
        raise Blocked(f"No such question in this set: {', '.join(sorted(want - named))}. "
                      f"Name each by its number, 1 to {len(records)}.")
    kept = [r for r in records if r not in gone]
    if not kept:
        raise Blocked("That drops every question. Keep at least one, or choose Not now.")
    ids = {r["id"] for r in gone}
    dump(ev / "work" / "spec.json", {**spec, "records": kept})
    dump(ev / "work" / "selection.json",
         [s for s in load(ev / "work" / "selection.json", []) or [] if s.get("id") not in ids])
    dump(ev / "work" / "dropped-by-person.json", sorted(ids))
    for i in ids:
        (ev / "eval" / "gold" / f"{i}.json").unlink(missing_ok=True)
    stage(ev, re.sub(r"[^a-z0-9]+", "-", str(spec.get("name") or "eval-set").lower()).strip("-") or "eval-set")
    return {"status": "ok", "evals": str(ev), "questions": len(kept), "dropped": sorted(ids)}


def cmd_link(a) -> dict:
    """The Atlan pages of a check saved to Trident: the agent's runs, and the last run against its baseline."""
    from urllib.parse import quote
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+", a.origin):
        raise Blocked("Use the HTTPS app origin, without a path, e.g. https://app.atlan.com.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", a.org):
        raise Blocked("Use the organization's URL slug, the one the context's proof links use.")
    ids = (load(pathlib.Path(a.evals) / "state.json", {}) or {}).get("ids") or {}
    agent, runs = ids.get("agent_id"), ids.get("experiments") or {}
    if not str(agent).startswith("agent_") or not runs:
        raise Blocked("This check is not saved to Atlan Trident yet: no agent or run is recorded.")
    base = f"{a.origin}/{quote(a.org, safe='')}/registry/evals/experiments/agent/{agent}"
    last = list(runs.values())[-1]
    return {"status": "ok", "runs": f"{base}?ws=all", "last_run": f"{base}/{last}?ws=all"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    s = p.add_subparsers(dest="cmd", required=True)
    c = s.add_parser("start", help="the built run, where its context lives and the project's eval sets")
    c.add_argument("--project", required=True)
    c.add_argument("--run", help="the run folder to test, when the project holds several")
    c.add_argument("--questions", type=int, default=20,
                   help="how many questions a new set holds, as the generate option names it (the demo uses 8)")
    c.set_defaults(func=cmd_start)
    c = s.add_parser("load", help="a set in a fresh eval folder, trusted answers run or pending")
    c.add_argument("--project", required=True)
    c.add_argument("--file", required=True)
    c.add_argument("--run", help="the run folder to test, when the project holds several")
    c.set_defaults(func=cmd_load)
    c = s.add_parser("gold", help="a warehouse run: read the trusted results the host ran, ready to score")
    c.add_argument("--evals", required=True)
    c.add_argument("--transcript", action="append", help="transcript of the agent that ran the trusted SQL")
    c.add_argument("--allow-missing", action="store_true",
                   help="drop a question whose trusted SQL has no result, instead of stopping")
    c.set_defaults(func=cmd_gold)
    c = s.add_parser("show", help="the loaded set as one markdown table, to read before it runs")
    c.add_argument("--evals", required=True)
    c.set_defaults(func=cmd_show)
    c = s.add_parser("edit", help="reword one question of a loaded set before it is scored")
    c.add_argument("--evals", required=True)
    c.add_argument("--question", required=True, help="its number in the shown table, or its id")
    c.add_argument("--text", required=True, help="the new wording, one full question")
    c.set_defaults(func=cmd_edit)
    c = s.add_parser("drop", help="take questions out of a loaded set before it is scored")
    c.add_argument("--evals", required=True)
    c.add_argument("--questions", required=True, help="numbers in the shown list, or ids, comma separated")
    c.set_defaults(func=cmd_drop)
    c = s.add_parser("link", help="the Atlan pages of a check saved to Trident")
    c.add_argument("--evals", required=True)
    c.add_argument("--org", required=True)
    c.add_argument("--origin", default="https://app.atlan.com")
    c.set_defaults(func=cmd_link)
    a = p.parse_args(argv)
    try:
        print(json.dumps(a.func(a), indent=1, default=str))
        return 0
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
