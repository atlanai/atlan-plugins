#!/usr/bin/env bash
# The plan the prompt names: without it a run stops to ask for the file and never checks Atlan.
set -euo pipefail
cat > plan.txt <<'PLAN'
Terraform will perform the following actions:

  # aws_security_group_rule.ingress_ssh will be created
  + resource "aws_security_group_rule" "ingress_ssh" {
      + cidr_blocks = ["0.0.0.0/0"]
      + from_port   = 22
      + to_port     = 22
      + protocol    = "tcp"
      + type        = "ingress"
    }

  # aws_db_instance.orders must be replaced
-/+ resource "aws_db_instance" "orders" {
      ~ engine_version = "14.9" -> "15.4" # forces replacement
        identifier     = "orders-prod"
    }

Plan: 2 to add, 0 to change, 1 to destroy.
PLAN
