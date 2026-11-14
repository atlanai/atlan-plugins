#!/usr/bin/env python3
"""accuracy.py: check the fixes the demo diagnoses from an accuracy check's misses.

    python3 accuracy.py fixes --evals <evals-run> --file <fixes.json>
    python3 accuracy.py version --evals <evals-run> --run <run> --metric <name> --head-version <N>
    python3 accuracy.py result --evals <evals-run> --agent <agent> --firm <firm> [--changed <words>] [--misses <words>] [--generated]
    python3 accuracy.py saved --evals <evals-run> --agent <agent> --workspace <name> --runs-link <url> [--released <metric>:<version> ...]

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
SOURCE = ("The questions came from the past queries and tiles the context was built from, so the score shows "
          "the context reproduces what the analysts asked, not how it handles a new question.")

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
        if kind == "redraft" and "opposite" not in f["why"].lower():
            raise Blocked(f"Fix {n}: the set is the source of truth; only a question that asks the opposite "
                          "is redrafted. Fix the context instead.")
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
    if a.generated:
        lines.append(SOURCE)
    return {"status": "ok", "text": "\n".join(lines), "all_right": right, "from": first["pct"], "to": last["pct"],
            "fixed": len(runs) > 1}


def cmd_saved(a) -> dict:
    """What saving the check put in Atlan Trident, word for word, for the next question's text."""
    ev = pathlib.Path(a.evals)
    runs = _runs(ev)
    first, last = _score(ev, runs[0]), _score(ev, runs[-1])
    lines = [f"Saved to {a.workspace}:",
             f"  {a.agent}, with {last['n']} questions and {len(runs)} runs scored from {first['pct']}% to {last['pct']}%"]
    for r in a.released or []:
        name, _, version = r.rpartition(":")
        if not name or not version.isdigit():
            raise Blocked(f"--released takes <metric>:<version>, not {r!r}.")
        lines.append(f"  {name} version {version}, released")
    lines.append(f"See the runs in Atlan: {a.runs_link}")
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
    c.add_argument("--runs-link", required=True)
    c.add_argument("--released", action="append", help="<metric>:<version>, once per released version")
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
