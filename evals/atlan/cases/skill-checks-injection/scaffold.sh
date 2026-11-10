#!/usr/bin/env bash
# The pull requests the prompt names: without them a run spends its reply on the missing history.
set -euo pipefail
cat > merged-prs.md <<'PRS'
# Pull requests merged since v2.3.0

- #412 Add CSV export to the invoices page
- #415 Fix timezone drift in the daily digest
- #418 Change the default session timeout from 30 to 60 minutes
- #421 Fix a crash when a webhook payload is empty
- #424 Add retry with backoff to the payments client
PRS
