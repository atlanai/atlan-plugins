#!/usr/bin/env python3
"""Prepare immutable inputs for a paired, local accuracy comparison.

This prepares work, not a score. Execution and transcript auditing remain required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def prepare(root: Path, cases: Path, gold: Path, schema: Path, history: Path, metrics: Path,
            data: Path, model: str, effort: str, harness: str, host: str = "codex", execution_schema: str = "site") -> dict:
    # Identity includes the whole set, including trusted SQL, but answering packets
    # contain only one question. Never put this manifest in an answering directory.
    inputs = {name: path.read_bytes() for name, path in
              (("cases", cases), ("gold", gold), ("schema", schema), ("history", history),
               ("metrics", metrics), ("data", data))}
    rows = [json.loads(line) for line in inputs["cases"].splitlines() if line.strip()]
    questions = []
    for row in rows:
        ident = row.get("case_id", row.get("id"))
        if not isinstance(ident, str) or not ident or not isinstance(row.get("question"), str):
            raise ValueError("Every case needs an id and question.")
        questions.append({"id": ident, "question": row["question"]})
    if not questions or len({q["id"] for q in questions}) != len(questions):
        raise ValueError("The set must be nonempty with unique ids.")
    if not all((model.strip(), effort.strip(), harness.strip())):
        raise ValueError("Record model, effort (or unsupported) and harness version.")
    if host not in ("codex", "claude") or not execution_schema.strip():
        raise ValueError("Record a supported host and execution schema.")
    hashes = {name: digest(value) for name, value in inputs.items()}
    scorer = Path(__file__).resolve().parents[2] / "analytics-build/scripts/analytics_eval.py"
    runtime = Path(__file__).resolve().parent
    runtime_digest = digest(b"".join((runtime / name).read_bytes() for name in ("comparison_run.py", "comparison_tools.py")))
    baseline = {"format": 3, "host": host, "execution_schema": execution_schema, "runtime_sha256": runtime_digest, "scorer_sha256": digest(scorer.read_bytes()), "inputs": {k: v for k, v in hashes.items() if k != "metrics"},
                "model": model, "effort": effort, "harness": harness,
                "protocol": "same-schema-history-plus-metrics-v1"}
    baseline_key = digest(canonical(baseline))
    identity = {**baseline, "inputs": hashes, "baseline_key": baseline_key}
    key = digest(canonical(identity))
    destination = root / key
    manifest = {**identity, "comparison_key": key, "questions": questions,
                "status": "prepared", "arms": ["without_metrics", "with_metrics"]}
    if destination.exists():
        if (destination / "trusted.jsonl").read_bytes() != inputs["gold"]:
            raise ValueError("Existing trusted answers changed; do not reuse them.")
        if (destination / "manifest.json").read_bytes() != canonical(manifest):
            raise ValueError("Existing comparison manifest changed; do not reuse it.")
        for arm in manifest["arms"]:
            expected = {"schema.json", "history.jsonl"} | ({"metrics.json"} if arm == "with_metrics" else set())
            if {p.name for p in (destination / arm).iterdir()} != expected:
                raise ValueError("Unexpected file in answering packet; do not reuse it.")
            for name, filename in (("schema", "schema.json"), ("history", "history.jsonl"),
                                   ("metrics", "metrics.json")):
                if name == "metrics" and arm == "without_metrics":
                    continue
                if (destination / arm / filename).read_bytes() != inputs[name]:
                    raise ValueError("Existing comparison input changed; do not reuse it.")
        return {"status": "prepared", "reused_inputs": True, "directory": str(destination),
                "baseline_key": baseline_key, "comparison_key": key}
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=root) as temporary:
        stage = Path(temporary) / "comparison"
        stage.mkdir()
        for arm in manifest["arms"]:
            packet = stage / arm
            packet.mkdir()
            (packet / "schema.json").write_bytes(inputs["schema"])
            (packet / "history.jsonl").write_bytes(inputs["history"])
            if arm == "with_metrics":
                (packet / "metrics.json").write_bytes(inputs["metrics"])
        (stage / "trusted.jsonl").write_bytes(inputs["gold"])
        (stage / "manifest.json").write_bytes(canonical(manifest))
        stage.rename(destination)
    return {"status": "prepared", "reused_inputs": False, "directory": str(destination),
            "baseline_key": baseline_key, "comparison_key": key}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "cases", "gold", "schema", "history", "metrics", "data"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("model", "effort", "harness"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--host", choices=["codex", "claude"], default="codex")
    parser.add_argument("--execution-schema", default="site")
    try:
        print(json.dumps(prepare(**vars(parser.parse_args())), indent=2))
    except (ValueError, OSError) as error:
        parser.exit(2, json.dumps({"status": "BLOCKED", "reason": str(error)}) + "\n")


if __name__ == "__main__":
    main()
