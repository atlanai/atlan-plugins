#!/usr/bin/env bash
# A shipping project whose run built 3 metrics and 1 query, chose no destination, and only linked preview.html (fixtures/shipping_run.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/../fixtures/shipping_run.py" "$here/../.." preview
