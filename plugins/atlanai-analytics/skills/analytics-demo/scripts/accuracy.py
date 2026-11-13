#!/usr/bin/env python3
"""accuracy.py: check the fixes the demo diagnoses from an accuracy check's misses.

    python3 accuracy.py fixes --evals <evals-run> --file <fixes.json>
    python3 accuracy.py version --evals <evals-run> --run <run> --metric <name> --head-version <N>

A fix is applied from <evals-run>/fixes.json, so its shape is checked here rather than trusted.
The set is the source of truth: only a question that asks the opposite is redrafted. Prints one
JSON object; exit 2 means something to correct. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

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
    a = p.parse_args(argv)
    try:
        print(json.dumps(a.func(a), indent=1))
        return 0
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
