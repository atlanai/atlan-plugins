#!/usr/bin/env bash
# The Fixit demo built from its dashboard alone and kept on this machine, its celebration shown and
# nothing asked yet (fixtures/demo_run.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/../fixtures/demo_run.py" "$here/../.."
