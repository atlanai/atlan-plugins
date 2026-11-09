#!/usr/bin/env bash
set -euo pipefail
mkdir -p notes
cat > notes/deploy-runbook.md <<'EOF'
# Deploy runbook

1. Tag the release and push the tag.
2. Export the token: GITHUB_TOKEN=ghp_XXXXXXXXXXXX
3. Run the deploy workflow and watch the canary for ten minutes.
EOF
