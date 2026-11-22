#!/usr/bin/env bash
# A shipping project whose setup run kept 3 metrics and 1 saved query local (fixtures/shipping_run.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/../fixtures/shipping_run.py" "$here/../.." local
