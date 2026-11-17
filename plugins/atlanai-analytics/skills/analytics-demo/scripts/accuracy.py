#!/usr/bin/env python3
"""accuracy.py: check the fixes the demo diagnoses from an accuracy check's misses.

    python3 accuracy.py fixes --evals <evals-run> --file <fixes.json>
    python3 accuracy.py version --evals <evals-run> --run <run> --metric <name> --head-version <N>
    python3 accuracy.py result --evals <evals-run> --agent <agent> --firm <firm> [--changed <words>] [--misses <words>] [--generated]
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
SOURCE = ("These questions were written from your own dashboards and past queries. So this score says the "
          "agent can answer what your team already asks. It does not say how it will do on a question nobody "
          "has asked before.")

# A phrase that moves with the calendar, and a date or period that does not. A fix that ties the
# first to the second answers this run's question and every later one wrongly.
RELATIVE = re.compile(r"\b(?:last|this|previous|prior|current|past|recent|latest|next)\s+"
                      r"(?:\d+\s+)?(?:day|week|month|quarter|year|fiscal year|fy)s?\b|\b(?:to date|year to date|ytd|mtd|qtd)\b", re.I)
FIXED = re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b|\bQ[1-4]\s*(?:FY)?\s*'?(?:19|20)?\d{2}\b|\bFY\s*'?\d{2,4}\b"
                   r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?(?:19|20)\d{2}\b", re.I)

FIX_KINDS = ("metric", "redraft", "new_metric")


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
        pins = kind == "metric" and RELATIVE.search(said) and FIXED.search(said)
        if pins:
            raise Blocked(f"Fix {n} ties a period that moves ({RELATIVE.search(said).group(0)}) to a fixed one "
                          f"({FIXED.search(said).group(0)}): it would answer every later question with that "
                          "date. This miss is the question's wording. Offer rewording the question instead: "
                          'kind "redraft", with a why that says the period is relative.')
        if kind == "redraft" and not re.search(r"opposite|relative", f["why"], re.I):
            raise Blocked(f"Fix {n}: the set is the source of truth; a question is redrafted only when it asks "
                          "the opposite of its SQL, or names a relative period its SQL fixes. Fix the context instead.")
        if kind == "metric":
            changes = f.get("changes") or []
            if not (f.get("metric") and f.get("rule_now") and f.get("rule_proposed") and changes):
                raise Blocked(f"Fix {n} needs metric, rule_now, rule_proposed and changes.")
            for c in changes:
                head = str(c.get("path") or "").split(".")[0]
                if not head or head in ("version", "spec_version") or "value" not in c:
                    raise Blocked(f"Fix {n}: each change is a definition field, description or display_name, with a value.")
    dump(ev / "fixes.json", fixes)
    return {"status": "ok", "fixes": len(fixes), "recommended": fixes[0].get("metric") or fixes[0]["kind"],
            "file": str(ev / "fixes.json")}


CARD_KEPT = ("metric_ref", "measure_id", "source_id", "gotchas", "tier", "tier_basis")


def cmd_version(a) -> dict:
    """The next version of a saved metric with its fix applied: the `metadata` for
    registry__append_artifact_version, built from this run's definition, never retyped. The demo makes
    the same edit on this machine right after, so this run stays at the version Atlan holds."""
    ev, run = pathlib.Path(a.evals), pathlib.Path(a.run)
    fix = next((f for f in load(ev / "fixes.json", []) or [] if f.get("metric") == a.metric and f.get("changes")), None)
    if not fix:
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
    for c in fix["changes"]:
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
            "pct": round(100 * float(v.get("pass_rate") or 0))}


def _compare(ev: pathlib.Path, after: str) -> str:
    """The before-and-after report: the raw tables against the context, on the same questions."""
    out = subprocess.run([sys.executable, str(HARNESS), "before-after", "--dir", str(ev),
                          "--before", "raw", "--after", after], capture_output=True, text=True)
    try:
        report = json.loads(out.stdout).get("report")
    except json.JSONDecodeError:
        report = None
    if not report:
        raise Blocked("This check has no raw-source pass to compare with. Run the before-and-after pass, "
                      "then ask for the result again.")
    return report


def cmd_result(a) -> dict:
    """The demo's result block, word for word: banner when every question is right, result line, score line."""
    ev = pathlib.Path(a.evals)
    runs = _runs(ev)
    if not runs:
        raise Blocked("This eval folder has no scored run yet.")
    first, last = _score(ev, runs[0]), _score(ev, runs[-1])
    right = last["pct"] == 100
    lines = [b.format(n=last["n"]) for b in BANNER] + [""] if right else []
    if right and len(runs) > 1:
        if not a.changed:
            raise Blocked("After a fix, say what changed (--changed), in plain words.")
        lines.append(f"Your {a.agent} has reached 100% accuracy, up from {first['pct']}%, now that {a.changed}.")
    elif right:
        lines.append(f"Your {a.agent} is at 100% accuracy on the {last['n']} questions {a.firm}'s analysts ask most.")
    else:
        if not a.misses:
            raise Blocked("With misses left, say what they share (--misses), in plain words.")
        lines.append(f"Your {a.agent} misses {a.misses}.")
    lines.append(last["line"])
    # Every check runs the raw-source pass, so every result carries the comparison.
    lines += ["", _compare(ev, runs[-1])]
    if a.generated:
        lines.append(SOURCE)
    return {"status": "ok", "text": "\n".join(lines), "all_right": right, "from": first["pct"], "to": last["pct"],
            "compared": True,
            "fixed": len(runs) > 1}


def cmd_saved(a) -> dict:
    """What saving the check put in Atlan Trident, word for word, for the next question's text."""
    ev = pathlib.Path(a.evals)
    runs = _runs(ev)
    first, last = _score(ev, runs[0]), _score(ev, runs[-1])
    scored = (f"1 run scored {last['pct']}%" if len(runs) == 1
              else f"{len(runs)} runs scored from {first['pct']}% to {last['pct']}%")
    lines = [f"Saved to {a.workspace}:", f"  {a.agent or 'This check'}, with {last['n']} questions and {scored}"]
    if (ev / "hillclimb" / "raw" / "results.jsonl").is_file():
        lines.append(f"  Raw tables, no context: {_score(ev, 'raw')['pct']}%")
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
    lines.append(f"See the runs in Atlan: {page}")
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
    c = s.add_parser("result", help="the result block, word for word")
    c.add_argument("--evals", required=True)
    c.add_argument("--agent", required=True)
    c.add_argument("--firm", required=True)
    c.add_argument("--changed", help="after a fix: what changed, plain words")
    c.add_argument("--misses", help="with misses left: what they share, plain words")
    c.add_argument("--generated", action="store_true", help="CHECK load returned generated: true")
    c.set_defaults(func=cmd_result)
    c = s.add_parser("saved", help="what saving the check put in Atlan Trident, word for word")
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
