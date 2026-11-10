#!/usr/bin/env python3
"""Per-run cost of each eval case: gateway round trips, turns, tokens and wall time.

    python3 evals/atlan/metrics.py <results dir> [--csv out.csv]

Reads every `result.json` under the results folder and the trace each run points at, so the
eval must run with `--keep-temp` (traces are deleted otherwise; run.py passes it). A run is one
Claude Code session; `gateway` counts calls to the `atlan` server, `gateway_errors` the ones the
gateway refused (a refused slot of a batch read counts as one), `tool_search` the deferred-tool
loads that precede them, and `wall_s` spans the first to the last trace event.

`budgets.json` beside this file holds each case's gateway calls, turns and cost from a run that
was accepted. A case that goes past one by more than the margin below is printed as over budget;
`--strict` makes that exit 1. `--write-budgets` replaces the file with this run's numbers.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

ATLAN = re.compile(r"^mcp__(?:plugin_[A-Za-z0-9_-]+_)?atlan__")
BUDGETS = Path(__file__).resolve().parent / "budgets.json"
# How far past its budget a case may run before it is reported: calls and turns vary by a step or
# two between runs of one prompt, and cost with them.
MARGIN = {"gateway": 2, "turns": 3, "cost_usd": 0.10}
FIELDS = ["case", "arm", "run", "score", "turns", "gateway", "gateway_errors", "inner_tools", "tool_search",
          "skills", "wall_s", "api_s", "input_tokens", "output_tokens", "cost_usd"]


def stamp(text: str | None) -> float | None:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() if text else None


def refused_slots(content) -> int:
    """How many slots of a batch read's answer the gateway refused."""
    text = "".join(block.get("text", "") for block in content if isinstance(block, dict)) if isinstance(content, list) else content
    try:
        results = json.loads(text).get("results") if isinstance(text, str) else None
    except (json.JSONDecodeError, AttributeError):
        return 0
    return sum(1 for slot in results if isinstance(slot, dict) and slot.get("isError") is True) if isinstance(results, list) else 0


def trace_metrics(path: Path) -> dict:
    turns, gateway, errors, searches, skills, inner, times = set(), 0, 0, 0, [], [], []
    pending: dict[str, bool] = {}
    result: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if (t := stamp(event.get("timestamp"))) is not None:
            times.append(t)
        if event.get("type") == "result":
            result = event
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                turns.add(message.get("id"))
                name = block.get("name", "")
                if ATLAN.match(name):
                    gateway += 1
                    pending[block["id"]] = True
                    called = (block.get("input") or {}).get("name")
                    inner.append(called or name.split("__")[-1])
                elif name == "ToolSearch":
                    searches += 1
                elif name == "Skill":
                    skills.append((block.get("input") or {}).get("skill", ""))
            elif block.get("type") == "tool_result" and pending.pop(block.get("tool_use_id"), False):
                errors += 1 if block.get("is_error") else refused_slots(block.get("content"))
    usage = result.get("usage") or {}
    return {
        "turns": len(turns) + 1,
        "gateway": gateway,
        "gateway_errors": errors,
        "inner_tools": " ".join(inner),
        "tool_search": searches,
        "skills": " ".join(skills),
        "wall_s": round(max(times) - min(times), 1) if times else None,
        "api_s": round(result.get("duration_api_ms", 0) / 1000, 1),
        "input_tokens": sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens")),
        "output_tokens": usage.get("output_tokens", 0),
    }


def rows(results: Path):
    for path in sorted(results.rglob("result.json")):
        for case in json.loads(path.read_text(encoding="utf-8")).get("cases", []):
            for arm, runs in case.get("arms", {}).items():
                for number, run in enumerate(runs):
                    trace = Path(run.get("tracePath") or "")
                    row = {"case": case["name"], "arm": arm, "run": number, "score": run.get("score"),
                           "cost_usd": round(run.get("costUsd") or 0, 4)}
                    if trace.is_file():
                        row |= trace_metrics(trace)
                    yield row


def budgets_from(table: list[dict]) -> dict:
    """Each case's budget: its costliest run with the plugin, measure by measure."""
    out: dict[str, dict] = {}
    for row in table:
        if row["arm"] != "with" or row.get("gateway") is None:
            continue
        held = out.setdefault(row["case"], dict.fromkeys(MARGIN, 0))
        for key in MARGIN:
            held[key] = max(held[key], row[key])
    return out


def over_budget(table: list[dict], budgets: dict) -> list[str]:
    """One line for each measure of a case that ran past its budget by more than the margin."""
    lines = []
    for case, spent in sorted(budgets_from(table).items()):
        for key, margin in MARGIN.items():
            budget = budgets.get(case, {}).get(key)
            if budget is not None and spent[key] > budget + margin:
                lines.append(f"{case}: {key} {spent[key]:g}, budget {budget:g}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--strict", action="store_true", help="exit 1 when a case is over budget")
    parser.add_argument("--write-budgets", action="store_true", help="replace budgets.json with this run's numbers")
    args = parser.parse_args()
    table = list(rows(args.results))
    if not table:
        print("no runs found", file=sys.stderr)
        return 2
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(table)
    with_plugin = [r for r in table if r["arm"] == "with" and r.get("wall_s") is not None]
    for key in ("gateway", "turns", "tool_search", "wall_s", "cost_usd"):
        values = [r[key] for r in with_plugin]
        if values:
            print(f"{key:<12} p50 {statistics.median(values):>7.2f}  max {max(values):>7.2f}  "
                  f"total {sum(values):>8.2f}")
    untraced = sum(1 for r in table if r["arm"] == "with" and r.get("wall_s") is None)
    if untraced:
        print(f"{untraced} run(s) left no trace and are not counted")
    if args.write_budgets:
        BUDGETS.write_text(json.dumps(budgets_from(table), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    over = over_budget(table, json.loads(BUDGETS.read_text(encoding="utf-8"))) if BUDGETS.is_file() else []
    for line in over:
        print(f"over budget  {line}")
    return 1 if over and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
