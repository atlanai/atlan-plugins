#!/usr/bin/env bash
# A shipping project whose run released 7 metrics and 4 saved queries and held 1 metric (fixtures/shipping_run.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/../fixtures/shipping_run.py" "$here/../.." published
