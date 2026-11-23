#!/usr/bin/env python3
"""accuracy.py: check the fixes the demo diagnoses from an accuracy check's misses.

    python3 accuracy.py fixes --evals <evals-run> --file <fixes.json>
    python3 accuracy.py version --evals <evals-run> --run <run> --metric <name> --head-version <N>
    python3 accuracy.py result --evals <evals-run> [--generated]
    python3 accuracy.py saved --evals <evals-run> --agent <agent> --workspace <name> --org <slug> [--released <metric>:<version> ...]

A fix is applied from <evals-run>/fixes.json, so its shape is checked here rather than trusted.
The set is the source of truth: only a question that asks the opposite is redrafted. Prints one
JSON object; exit 2 means something to correct. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

HARNESS = pathlib.Path(__file__).resolve().parents[2] / "analytics-build" / "scripts" / "analytics_eval.py"
BANNER = (" *   .  '   *    .   '  *   .    '   *  .",
          " '   *   .   {n} OUT OF {n} RIGHT   .   *   '",
          " .   *    '   .    *   '    .   *    '  .")
SOURCE = ("These questions come from what your team already asks. So the score shows your agent can answer those. "
          "It doesn't show how it does on new questions.")

# A phrase that moves with the calendar, and a date or period that does not. A fix that ties the
# first to the second answers this run's question and every later one wrongly.
RELATIVE = re.compile(r"\b(?:last|this|previous|prior|current|past|recent|latest|next)\s+"
                      r"(?:\d+\s+)?(?:day|week|month|quarter|year|fiscal year|fy)s?\b|\b(?:to date|year to date|ytd|mtd|qtd)\b", re.I)
FIXED = re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b|\bQ[1-4]\s*(?:FY)?\s*'?(?:19|20)?\d{2}\b|\bFY\s*'?\d{2,4}\b"
                   r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?(?:19|20)\d{2}\b", re.I)

FIX_KINDS = ("metric", "shared", "redraft", "new_metric")


class Blocked(Exception):
    pass


def load(path: pathlib.Path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def dump(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def cmd_fixes(a) -> dict:
    """Check the diagnosed fixes and write <evals-run>/fixes.json, recommended first: a fix is
    applied from there, so its shape is checked here rather than trusted."""
    ev = pathlib.Path(a.evals)
    fixes = load(pathlib.Path(a.file))
    if not isinstance(fixes, list) or not fixes:
        raise Blocked("Give the fixes as a JSON list, the recommended one first.")
    cases = {json.loads(l)["case_id"] for l in (ev / "eval" / "cases.jsonl").read_text().splitlines() if l.strip()} \
        if (ev / "eval" / "cases.jsonl").exists() else set()
    for n, f in enumerate(fixes, 1):
        kind = f.setdefault("kind", "metric")
        if kind not in FIX_KINDS:
            raise Blocked(f"Fix {n}: kind is one of {', '.join(FIX_KINDS)}.")
        missing = [m for m in f.get("misses") or [] if cases and m not in cases]
        if not f.get("misses") or missing:
            raise Blocked(f"Fix {n} names no miss of this set" + (f": {', '.join(missing)}" if missing else "") + ".")
        if not f.get("why"):
            raise Blocked(f"Fix {n} needs why the miss failed.")
        # Read from the rule in plain words, never from the changes: a parameter's default date is not a pin.
        said = str(f.get("rule_proposed") or "")
        pins = kind in ("metric", "shared") and RELATIVE.search(said) and FIXED.search(said)
        if pins:
            raise Blocked(f"Fix {n} ties a period that moves ({RELATIVE.search(said).group(0)}) to a fixed one "
                          f"({FIXED.search(said).group(0)}): it would answer every later question with that "
                          "date. This miss is the question's wording. Offer rewording the question instead: "
                          'kind "redraft", with a why that says the period is relative.')
        if kind == "redraft" and not re.search(r"opposite|relative", f["why"], re.I):
            raise Blocked(f"Fix {n}: the set is the source of truth; a question is redrafted only when it asks "
                          "the opposite of its SQL, or names a relative period its SQL fixes. Fix the context instead.")
        if kind == "metric":
            if not (f.get("metric") and f.get("rule_now") and f.get("rule_proposed") and f.get("changes")):
                raise Blocked(f"Fix {n} needs metric, rule_now, rule_proposed and changes.")
            _check_changes(n, f["changes"])
        if kind == "shared":
            # One rule in plain words, applied to each metric that answers that kind of question: a miss that
            # repeats on another metric is a habit, and fixing one metric at a time moves it to the next.
            each = f.get("metrics") or []
            if not (f.get("rule_now") and f.get("rule_proposed") and len(each) > 1
                    and all(isinstance(m, dict) and m.get("metric") and m.get("changes") for m in each)):
                raise Blocked(f"Fix {n} is shared: it needs rule_now, rule_proposed and metrics, two or more, each "
                              "with its own changes.")
            for m in each:
                _check_changes(n, m["changes"])
    if (ev / "fixes.json").exists():
        # Each round's fixes, kept: a new miss is compared with what earlier fixes already changed.
        with (ev / "fixes-history.jsonl").open("a") as fh:
            fh.write(json.dumps({"fixes": load(ev / "fixes.json", [])}) + "\n")
    dump(ev / "fixes.json", fixes)
    first = fixes[0]
    named = first.get("metric") or (f"{first['metrics'][0]['metric']} and {len(first['metrics']) - 1} more"
                                    if first.get("kind") == "shared" else first["kind"])
    return {"status": "ok", "fixes": len(fixes), "recommended": named, "file": str(ev / "fixes.json")}


def _check_changes(n: int, changes: list) -> None:
    for c in changes:
        head = str(c.get("path") or "").split(".")[0]
        if not head or head in ("version", "spec_version") or "value" not in c:
            raise Blocked(f"Fix {n}: each change is a definition field, description or display_name, with a value.")


def _changes_for(fixes: list, metric: str) -> list:
    """A metric's changes: its own fix, or its part of a shared one."""
    for f in fixes or []:
        if f.get("metric") == metric and f.get("changes"):
            return f["changes"]
        for m in f.get("metrics") or [] if f.get("kind") == "shared" else []:
            if isinstance(m, dict) and m.get("metric") == metric and m.get("changes"):
                return m["changes"]
    return []


CARD_KEPT = ("metric_ref", "measure_id", "source_id", "gotchas", "tier", "tier_basis")


def cmd_version(a) -> dict:
    """The next version of a saved metric with its fix applied: the `metadata` for
    registry__append_artifact_version, built from this run's definition, never retyped. The demo makes
    the same edit on this machine right after, so this run stays at the version Atlan holds."""
    ev, run = pathlib.Path(a.evals), pathlib.Path(a.run)
    changes = _changes_for(load(ev / "fixes.json", []), a.metric)
    if not changes:
        raise Blocked(f"No fix with changes for {a.metric} in {ev / 'fixes.json'}.")
    body = next((b for b in load(run / "context" / "metrics.json", []) or []
                 if a.metric in (b.get("name"), b.get("display_name"))), None)
    if not body:
        raise Blocked(f"{a.metric} is not in this run's context.")
    d = json.loads(json.dumps(body["definition"]))
    if int(d.get("version") or 1) != a.head_version:
        raise Blocked(f"Atlan holds version {a.head_version} of {a.metric}, but the saved definition here is version "
                      f"{d.get('version') or 1}: someone changed it since. Propose the fix for its owner instead.")
    meta = {"display_name": body.get("display_name") or a.metric, "description": body.get("description")}
    for c in changes:
        keys = c["path"].split(".")
        if keys[0] in ("description", "display_name") and len(keys) == 1:
            meta[keys[0]] = c["value"]
            continue
        node = d
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = c["value"]
    d["version"] = a.head_version + 1
    # The engine card restates the whole first proposal (its SQL, joins and BI formulas), which is
    # stale once a rule changes and doubles what has to be sent. Atlan keeps its notes and identity.
    engine = (d.get("extensions") or {}).get("metrics_agent")
    if isinstance(engine, dict) and isinstance(engine.get("card"), dict):
        engine["card"] = {k: v for k, v in engine["card"].items() if k in CARD_KEPT}
    meta["definition"] = d
    return {"status": "ok", "metric": a.metric, "version": d["version"], "metadata": meta}


def _runs(ev: pathlib.Path) -> list:
    """The scored runs of the check, first to last: baseline, then each test again."""
    found = [d.name for d in (ev / "hillclimb").glob("*") if (d / "results.jsonl").is_file()]
    later = sorted((v for v in found if re.fullmatch(r"v\d+", v)), key=lambda v: int(v[1:]))
    return (["baseline"] if "baseline" in found else []) + later


def _score(ev: pathlib.Path, variant: str) -> dict:
    out = subprocess.run([sys.executable, str(HARNESS), "summary", "--dir", str(ev), "--variant", variant],
                         capture_output=True, text=True)
    try:
        s = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise Blocked(f"No score for {variant}: {(out.stdout or out.stderr)[-200:]}")
    v = s.get("validation") or {}
    return {"line": s.get("score_line") or "", "n": int(v.get("n") or 0),
            "pct": int(100 * float(v.get("pass_rate") or 0) + 0.5)}  # half up, as the result lines round


def _questions(n) -> str:
    return f"{n} question" if n == 1 else f"{n} questions"


def _compare(ev: pathlib.Path, after: str) -> dict:
    """The before-and-after comparison: column names only against the context, on the same questions, with the
    result's numbers (`result`) and a widget (`widget`)."""
    out = subprocess.run([sys.executable, str(HARNESS), "before-after", "--dir", str(ev),
                          "--before", "raw", "--after", after], capture_output=True, text=True)
    try:
        got = json.loads(out.stdout)
    except json.JSONDecodeError:
        got = {}
    if not got.get("report"):
        raise Blocked("This check has no raw-source pass to compare with. Run the before-and-after pass, "
                      "then ask for the result again.")
    return got


def cmd_result(a) -> dict:
    """The numbers the result is written from, never a finished card: the assistant writes the result's words by its
    outcome (ahead, tie, behind, small set), around the number lines given here. The banner, when every question is
    right, is the one piece put as it is."""
    ev = pathlib.Path(a.evals)
    runs = _runs(ev)
    if not runs:
        raise Blocked("This eval folder has no scored run yet.")
    first, last = _score(ev, runs[0]), _score(ev, runs[-1])
    # Every check runs the raw-source pass, so every result is against column names only, on the latest run.
    compared = _compare(ev, runs[-1])
    res = compared.get("result") or {}
    if not res:
        raise Blocked("The comparison has no result numbers. Run the before-and-after again, then ask for the result.")
    right = bool(res.get("all_right"))
    out = {"status": "ok", "banner": "\n".join(b.format(n=res["questions"]) for b in BANNER) if right else "",
           **{k: res[k] for k in ("accuracy", "baseline", "lift", "outcome", "questions", "small_set", "misses",
                                  "turned_around", "lost", "left_out", "lines")},
           "all_right": right, "fixed": len(runs) > 1, "from": first["pct"], "to": last["pct"],
           "source": SOURCE if a.generated else ""}
    if len(runs) > 1:
        out["lines"] = {**out["lines"], "fixed": f"Up from {first['pct']}% before the fix."}
    if a.widget:
        out["widget"] = compared.get("widget") or ""
    return out


def cmd_saved(a) -> dict:
    """What saving the check put in Atlan, word for word, for the next question's text."""
    ev = pathlib.Path(a.evals)
    runs = _runs(ev)
    first, last = _score(ev, runs[0]), _score(ev, runs[-1])
    scored = (f"1 round scored {last['pct']}%" if len(runs) == 1
              else f"{len(runs)} rounds scored from {first['pct']}% to {last['pct']}%")
    lines = [f"Saved to {a.workspace}:", f"  {a.agent or 'This check'}, with {_questions(last['n'])} and {scored}"]
    if (ev / "hillclimb" / "raw" / "results.jsonl").is_file():
        lines.append(f"  column names only: {_score(ev, 'raw')['pct']}%")
    for state, given in (("released", a.released), ("withdrawn", a.withdrawn)):
        for r in given or []:
            name, _, version = r.rpartition(":")
            if not name or not version.isdigit():
                raise Blocked(f"--{state} takes <metric>:<version>, not {r!r}.")
            lines.append(f"  {name} version {version}, {state}")
    ids = (load(ev / "state.json", {}) or {}).get("ids") or {}
    agent, exps = ids.get("agent_id"), ids.get("experiments") or {}
    if not exps or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", a.org):
        raise Blocked("Save the check first, and pass the organization's URL slug (--org).")
    base = f"{a.origin}/{a.org}/registry/evals/experiments"
    # The last run's page compares it with the run before it; a saved agent has a page of its own.
    page = f"{base}/agent/{agent}?ws=all" if str(agent).startswith("agent_") else f"{base}/{list(exps.values())[-1]}?ws=all"
    lines.append(f"See the scores in Atlan: {page}")
    return {"status": "ok", "text": "\n".join(lines)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    s = p.add_subparsers(dest="cmd", required=True)
    c = s.add_parser("fixes", help="check the diagnosed fixes and write fixes.json, recommended first")
    c.add_argument("--evals", required=True)
    c.add_argument("--file", required=True, help="a JSON list of fixes")
    c.set_defaults(func=cmd_fixes)
    c = s.add_parser("version", help="the next version of a saved metric with its fix applied")
    c.add_argument("--evals", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--metric", required=True)
    c.add_argument("--head-version", type=int, required=True, help="the version_ordinal Atlan holds now")
    c.set_defaults(func=cmd_version)
    c = s.add_parser("result", help="the numbers the result is written from, and the banner when every question is right")
    c.add_argument("--evals", required=True)
    c.add_argument("--agent", help="unused; accepted so an older call still runs")
    c.add_argument("--firm", help="unused; accepted so an older call still runs")
    c.add_argument("--generated", action="store_true", help="CHECK load returned generated: true")
    c.add_argument("--widget", action="store_true", help="also return the comparison's widget, for a side page")
    c.set_defaults(func=cmd_result)
    c = s.add_parser("saved", help="what saving the check put in Atlan, word for word")
    c.add_argument("--evals", required=True)
    c.add_argument("--agent", required=True)
    c.add_argument("--workspace", required=True)
    c.add_argument("--org", required=True, help="the organization's URL slug, as the proof links use")
    c.add_argument("--origin", default="https://app.atlan.com")
    c.add_argument("--released", action="append", help="<metric>:<version>, once per released version")
    c.add_argument("--withdrawn", action="append", help="<metric>:<version>, once per withdrawn release")
    c.set_defaults(func=cmd_saved)
    a = p.parse_args(argv)
    try:
        print(json.dumps(a.func(a), indent=1))
        return 0
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
