#!/usr/bin/env bash
# The pipeline, end to end: a thin wrapper over scripts/run.py, which owns the stage order and
# the human gates. The run is described by run.yml: its inputs, where results go (`publish`) and
# whether it is traced (`observers`).
#
# It used to be the order itself, with its author's paths, workspace and agent ids, a user
# token exported as the agent's trace key, and `--archive-superseded` always on. None of that
# belongs in a shipped script: the inputs are in run.yml, the credentials come from the
# environment, and a destructive flag is passed only by an operator who means it.
#
#   ./run_pipeline.sh --run output/acme [--config run.yml]
#
# With no run.yml it starts the set-up interview at a terminal, and refuses (listing the
# questions) without one. See `scripts/run.py --help`.
#
# It runs from the caller's directory, as `metrics-agent run` does: a relative --run, a relative
# input path and ./run.yml all mean the same files with either command. Only the interpreter
# and scripts/run.py are found beside this file.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-$HERE/.venv/bin/python}"
# Checked before the exec, so a missing interpreter exits 127 ("command not found") on every
# bash: bash 4+ exits 127 from a failed exec, but the bash 3.2 macOS ships exits 1.
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "run_pipeline.sh: no Python interpreter at $PY (create .venv, or set PY)" >&2
    exit 127
fi
exec "$PY" "$HERE/scripts/run.py" "$@"
