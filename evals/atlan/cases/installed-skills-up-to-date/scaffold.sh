#!/usr/bin/env bash
# A project where oncall-handoff 1.0.0 was installed from Atlan through MCP, with the install
# record that install leaves beside the skill folders. Atlan's latest is 1.1.0.
set -euo pipefail
mkdir -p .claude/skills/oncall-handoff/references
cat > .claude/skills/oncall-handoff/SKILL.md <<'EOF'
---
name: oncall-handoff
description: Use when handing over an on-call shift, to list open incidents, escalations and follow-ups for the next engineer.
---

# On-call handoff

1. List every incident opened or still open during the shift, with its severity and owner.
2. For each escalation, follow [references/escalation.md](references/escalation.md).
3. Write the handoff note: open incidents first, then follow-ups, then anything noisy.
EOF
cat > .claude/skills/oncall-handoff/references/escalation.md <<'EOF'
# Escalation

- Sev 1: page the incident commander and post in #incidents.
- Sev 2: post in #incidents and tag the owning team.
- Sev 3: open a ticket for the owning team.
EOF
cat > .claude/skills/oncall-handoff/references/runbook-links.md <<'EOF'
# Runbooks

- Database failover: see the platform wiki, page DB-07.
- Queue backlog: see the platform wiki, page Q-02.
EOF
cat > .claude/skills/.atlan-installed.json <<'EOF'
{
  "oncall-handoff": {
    "artifact_id": "skill_01evaloncall",
    "workspace_id": "workspace_01evalplatform",
    "release_id": "rel_01evaloncall100",
    "version_label": "1.0.0",
    "version_ordinal": 1,
    "installed_at": "2026-08-03T10:00:00Z",
    "files": {
      "SKILL.md": {
        "file_id": "file_01evaloncallmd",
        "file_version_ordinal": 1,
        "sha256": "0e9fe82a0b0c9d126fa9a0fb8ef9ab9c3ac84be94c259691492a09595e181657"
      },
      "references/escalation.md": {
        "file_id": "file_01evaloncallesc",
        "file_version_ordinal": 1,
        "sha256": "0ea08557a425715bbfa3fc877714318361a5debacdbcdd425a207f4b93315acd"
      },
      "references/runbook-links.md": {
        "file_id": "file_01evaloncallrun",
        "file_version_ordinal": 1,
        "sha256": "22930a800be89a611c4154069054a6634abdb2cc126cec6becca8e396cf5d7f4"
      }
    }
  }
}
EOF
