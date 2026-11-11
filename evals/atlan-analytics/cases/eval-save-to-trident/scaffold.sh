#!/usr/bin/env bash
# A scored, approved eval set for the Indigo context, ready to save.
set -euo pipefail
mkdir -p .atlan-evals/indigo/work .atlan-evals/indigo/eval/gold
cat > .atlan-evals/indigo/work/spec.json <<'JSON'
{"name": "indigo", "source_description": "dashboard tiles and analyst queries", "records": [
 {"id": "q1", "question": "How long do our aircraft sit between flights?", "question_spec": "Average turnaround minutes, scheduled departures, October 2026",
  "golden_sql": "SELECT AVG(turnaround_minutes) FROM ops.legs WHERE month = '2026-10'", "review_status": "approved",
  "provenance": {"source": "query_history"}, "modes": ["grain"]},
 {"id": "q2", "question": "How far off schedule are our block times?", "question_spec": "Average block time variance, October 2026",
  "golden_sql": "SELECT AVG(block_variance) FROM ops.legs WHERE month = '2026-10'", "review_status": "approved",
  "provenance": {"source": "bi:powerbi"}, "modes": []}]}
JSON
