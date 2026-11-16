#!/usr/bin/env bash
# An eval set for the Indigo context, ready to save.
set -euo pipefail
mkdir -p .atlan-evals/sets
cat > .atlan-evals/sets/indigo.jsonl <<'JSONL'
{"id": "q1", "question": "How long do our aircraft sit between flights?", "sql": "SELECT AVG(turnaround_minutes) FROM ops.legs WHERE month = '2026-10'", "tests": "turnaround_time", "from": "past query, run 40 times"}
{"id": "q2", "question": "How far off schedule are our block times?", "sql": "SELECT AVG(block_variance) FROM ops.legs WHERE month = '2026-10'", "tests": "block_variance", "from": "dashboard tile"}
JSONL
