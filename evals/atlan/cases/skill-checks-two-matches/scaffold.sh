#!/usr/bin/env bash
set -euo pipefail
# The plan the prompt names, so the run reaches the skill check instead of stopping at a missing file.
cat > plan.txt <<'EOF'
Terraform will perform the following actions:

  # aws_s3_bucket.reports will be created
  + resource "aws_s3_bucket" "reports" {
      + bucket = "example-reports"
    }

  # aws_iam_role_policy.reader will be updated in-place
  ~ resource "aws_iam_role_policy" "reader" {
      ~ policy = jsonencode({ Statement = [{ Action = "s3:*", Resource = "*" }] })
    }

Plan: 1 to add, 1 to change, 0 to destroy.
EOF
