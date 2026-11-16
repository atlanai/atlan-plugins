#!/usr/bin/env python3
"""Export a verified paired metrics arm into an ordinary loaded EVAL folder."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BUILD = HERE.parents[1] / "analytics-build/scripts"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BUILD))
import comparison_report
import analytics_eval


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def load(path: Path):
    return json.loads(path.read_text())


def _case_key(case_id: str) -> str:
    return hashlib.sha256(case_id.encode()).hexdigest()[:16]


def _assert_target_matches(comparison: Path, evals: Path, manifest: dict) -> None:
    cases = evals / "eval/cases.jsonl"
    gold = evals / "eval/answers.jsonl"
    if not cases.is_file() or not gold.is_file():
        raise ValueError("Target must be a loaded EVAL folder with cases.jsonl and answers.jsonl")
    for name, path in (("cases", cases), ("gold", gold)):
        if sha(path) != manifest["inputs"][name]:
            raise ValueError(f"Target EVAL {name} digest differs from the frozen comparison input")
    # The report collector checks the trusted file and case coverage. This explicit comparison
    # also fails closed if either source representation ever changes independently.
    trusted = comparison_report.trusted(comparison, manifest)
    rows = analytics_eval.read_jsonl(cases)
    ids = [row.get("case_id") for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(manifest["questions"] and trusted):
        raise ValueError("Target EVAL question ids differ from the frozen comparison")
    by_id = {row["case_id"]: row for row in rows}
    for question in manifest["questions"]:
        row = by_id.get(question["id"])
        if not row or row.get("question") != question["question"]:
            raise ValueError(f"Target EVAL question differs for {question['id']}")
    answer_rows = {row["case_id"]: row for row in analytics_eval.read_jsonl(gold)}
    if set(answer_rows) != set(trusted):
        raise ValueError("Target trusted SQL coverage differs from the comparison")
    for case_id, row in trusted.items():
        if answer_rows[case_id].get("gold_sql") != row.get("gold_sql"):
            raise ValueError(f"Target trusted SQL differs for {case_id}")


def _variant_dir(evals: Path, variant: str) -> Path:
    if variant != "baseline" and not re.fullmatch(r"v[1-9][0-9]*", variant):
        raise ValueError("Variant must be baseline or v1, v2, and so on")
    return evals / "hillclimb" / variant


def _already_exported(target: Path, comparison: Path, report: dict, variant: str) -> dict:
    sidecar_path = target / "comparison-export.json"
    if not sidecar_path.is_file():
        raise ValueError("Target variant already exists; refusing to overwrite it")
    sidecar = load(sidecar_path)
    manifest = report["manifest"]
    if sidecar.get("comparison_key") != manifest["comparison_key"] or sidecar.get("variant") != variant:
        raise ValueError("Existing export sidecar belongs to another comparison or variant")
    if sidecar.get("comparison_source") != str(comparison.resolve()):
        raise ValueError("Existing export comparison source changed")
    rows_path = target / "results.jsonl"
    if not rows_path.is_file():
        raise ValueError("Existing export has no results.jsonl")
    loaded_rows = analytics_eval.read_jsonl(rows_path)
    rows = {row["prompt_id"]: row for row in loaded_rows}
    expected = sidecar.get("exported_rows", {})
    if len(rows) != len(loaded_rows) or set(rows) != set(expected):
        raise ValueError("Existing export rows changed")
    for case_id, digest in expected.items():
        if hashlib.sha256(canonical(rows[case_id])).hexdigest() != digest:
            raise ValueError("Existing export row changed: " + case_id)
    for relpath, digest in sidecar.get("evidence_hashes", {}).items():
        path = target / relpath
        if not path.is_file() or sha(path) != digest:
            raise ValueError("Existing exported evidence changed: " + relpath)
    if sidecar.get("without_metrics_source") != str(comparison.resolve() / "executions/without_metrics"):
        raise ValueError("Existing baseline evidence link changed")
    source_rows = {row["id"]: row for row in report["rows"]}
    for case_id, source_row in source_rows.items():
        source_record = source_row["arms"]["with_metrics"]
        row = rows[case_id]
        if (row.get("rep") != 0 or row.get("prompt") != source_row["question"]
                or row.get("sql") != source_record["answer"]["sql"]
                or row.get("grade") != source_record["score"]["grade"]
                or row.get("class") != source_record["score"].get("class")
                or row.get("model") != manifest["model"] or row.get("isolation") != "verified"):
            raise ValueError("Existing EVAL row differs from the verified metrics arm: " + case_id)
        result_tag = f"{case_id}_rep0"
        results_dir = target / "results"
        gold_path, agent_path = results_dir / f"{result_tag}.gold.json", results_dir / f"{result_tag}.agent.json"
        if not gold_path.is_file() or not agent_path.is_file():
            raise ValueError("Existing EVAL result files are missing: " + case_id)
        if analytics_eval.normalize_result(load(gold_path)) != analytics_eval.normalize_result(source_record["gold_result"]):
            raise ValueError("Existing trusted result differs from the paired arm: " + case_id)
        if analytics_eval.normalize_result(load(agent_path)) != analytics_eval.normalize_result(source_record["agent_result"]):
            raise ValueError("Existing executed result differs from the paired arm: " + case_id)
        evidence = target / "comparison-evidence/with_metrics" / _case_key(case_id)
        source_evidence = comparison / "executions/with_metrics" / _case_key(case_id)
        expected_files = {p.relative_to(source_evidence).as_posix() for p in source_evidence.rglob("*") if p.is_file()}
        expected_files.update({"answer.json", "gold-result.json", "agent-result.json"})
        actual_files = {p.relative_to(evidence).as_posix() for p in evidence.rglob("*") if p.is_file()}
        if actual_files != expected_files:
            raise ValueError("Existing copied comparison evidence changed: " + case_id)
        for relative in expected_files - {"answer.json", "gold-result.json", "agent-result.json"}:
            if sha(evidence / relative) != sha(source_evidence / relative):
                raise ValueError("Existing copied transcript evidence changed: " + case_id)
    return {"status": "ok", "exists": True, "variant": variant,
            "comparison_key": manifest["comparison_key"], "rows": len(rows), "directory": str(target)}


def export(comparison: Path, evals: Path, variant: str) -> dict:
    comparison, evals = comparison.resolve(), evals.resolve()
    report = comparison_report.collect(comparison)
    if not report["complete"]:
        raise ValueError("Comparison is incomplete; export requires a verified pair for every question")
    manifest = report["manifest"]
    _assert_target_matches(comparison, evals, manifest)
    for row in report["rows"]:
        cached_gold = evals / "eval/gold" / f"{row['id']}.json"
        if cached_gold.is_file() and analytics_eval.normalize_result(load(cached_gold)) != \
                analytics_eval.normalize_result(row["arms"]["with_metrics"]["gold_result"]):
            raise ValueError(f"Target trusted result differs for {row['id']}")
    target = _variant_dir(evals, variant)
    if target.exists():
        return _already_exported(target, comparison, report, variant)

    # Score parity is checked before writing anything into the destination.
    for row in report["rows"]:
        scored = row["arms"]["with_metrics"]
        trusted = comparison_report.trusted(comparison, manifest)[row["id"]]
        expected = analytics_eval.grade(scored["gold_result"], scored["agent_result"],
                                        trusted["gold_sql"], scored["answer"]["sql"],
                                        analytics_eval.case_sig(trusted),
                                        trusted.get("representation_equivalence"))
        if expected != scored["score"]:
            raise ValueError(f"Ordinary EVAL grading differs from the paired score for {row['id']}")

    hillclimb = evals / "hillclimb"
    hillclimb.mkdir(parents=True, exist_ok=True)
    if (hillclimb / variant).exists():
        raise ValueError("Target variant already exists; refusing to overwrite it")
    with tempfile.TemporaryDirectory(prefix=".comparison-export-", dir=hillclimb) as temporary:
        stage = Path(temporary)
        staged_hillclimb = stage / "hillclimb"
        staged_hillclimb.mkdir()
        state = hillclimb / "_state.json"
        if state.is_file():
            shutil.copyfile(state, staged_hillclimb / "_state.json")
        staged_project = stage / "project"
        staged_project.mkdir()
        (staged_project / "eval").symlink_to(evals / "eval", target_is_directory=True)
        (staged_project / "hillclimb").symlink_to(staged_hillclimb, target_is_directory=True)
        exported = staged_hillclimb / variant
        evidence_root = exported / "comparison-evidence" / "with_metrics"
        evidence_root.mkdir(parents=True)
        row_hashes = {}
        evidence_hashes = {}
        for row in report["rows"]:
            case_id = row["id"]
            source = comparison / "executions/with_metrics" / _case_key(case_id)
            dest = evidence_root / _case_key(case_id)
            shutil.copytree(source, dest)
            for path in sorted(p for p in dest.rglob("*") if p.is_file()):
                relative = path.relative_to(exported).as_posix()
                evidence_hashes[relative] = sha(path)
            source_record = row["arms"]["with_metrics"]
            answer = dest / "answer.json"
            gold_result = dest / "gold-result.json"
            agent_result = dest / "agent-result.json"
            answer.write_bytes(canonical(source_record["answer"]) + b"\n")
            gold_result.write_bytes(canonical(source_record["gold_result"]) + b"\n")
            exact_result = dict(source_record["agent_result"])
            exact_result["source"] = "transcript" if source_record.get("result_source") != "controller_replay" else "replay"
            agent_result.write_bytes(canonical(exact_result) + b"\n")
            for path in (answer, gold_result, agent_result):
                evidence_hashes[path.relative_to(exported).as_posix()] = sha(path)
            transcript = dest / "events.jsonl"
            if not transcript.is_file() or not analytics_eval.parse_transcript(str(transcript))["assistant_messages"]:
                raise ValueError(f"analytics_eval cannot normalize the retained transcript for {case_id}")
            result = analytics_eval.cmd_record(type("RecordArgs", (), {
                "dir": str(staged_project), "variant": variant, "case": case_id, "rep": 0,
                "answer_file": str(answer), "gold_result": str(gold_result),
                "agent_result": str(agent_result), "transcript": str(transcript),
                "model": manifest["model"], "latency_s": source_record.get("seconds"),
                "cls": None, "reason": None, "verdict": None, "question": None,
            })())
            if result.get("class") != source_record["score"].get("class") or \
                    result.get("pass") != source_record["score"]["grade"].get("pass"):
                raise ValueError(f"analytics_eval record score differs from paired score for {case_id}: "
                                 f"record={result!r}, paired={source_record['score']['grade']!r}")
            # The ordinary recorder output includes the SQL, scored result and parsed transcript;
            # the comparison evidence folder keeps the exact source record and raw native trace.
        results = exported / "results.jsonl"
        written = {row["prompt_id"]: row for row in analytics_eval.read_jsonl(results)}
        if set(written) != {row["id"] for row in report["rows"]}:
            raise ValueError("Ordinary EVAL recorder did not produce complete case coverage")
        row_hashes = {case_id: hashlib.sha256(canonical(row)).hexdigest()
                      for case_id, row in written.items()}
        sidecar = {
            "format": 1, "comparison_key": manifest["comparison_key"],
            "baseline_key": manifest["baseline_key"], "variant": variant,
            "arm": "with_metrics", "model": manifest["model"], "effort": manifest["effort"],
            "source_inputs": manifest["inputs"], "case_ids": [q["id"] for q in manifest["questions"]],
            "exported_rows": row_hashes,
            "evidence_hashes": evidence_hashes,
            "comparison_source": str(comparison),
            "without_metrics_source": str(comparison / "executions/without_metrics"),
            "method": "verified paired comparison; metrics arm imported as one ordinary EVAL variant",
        }
        (exported / "comparison-export.json").write_text(json.dumps(sidecar, indent=2) + "\n")
        # Recheck after staging. Never replace an existing variant, including an empty one.
        if target.exists():
            raise ValueError("Target variant appeared during export; refusing to overwrite it")
        os.rename(exported, target)
    return {"status": "ok", "exists": False, "variant": variant,
            "comparison_key": manifest["comparison_key"], "rows": len(report["rows"]),
            "directory": str(target)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--evals", type=Path, required=True)
    parser.add_argument("--variant", required=True, help="baseline for the initial import, then v1, v2, ...")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(export(args.comparison, args.evals, args.variant), indent=2))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
