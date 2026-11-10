#!/usr/bin/env bash
# A repository with two skills in it and no workflow yet: an empty one has nothing to sync.
set -euo pipefail
mkdir -p skills/deploy-preview skills/standup-notes .git
cat > .git/config <<'CONFIG'
[remote "origin"]
	url = https://github.com/acme/platform-skills.git
	fetch = +refs/heads/*:refs/remotes/origin/*
[branch "main"]
	remote = origin
	merge = refs/heads/main
CONFIG
printf 'ref: refs/heads/main\n' > .git/HEAD
cat > skills/deploy-preview/SKILL.md <<'SKILL'
---
name: deploy-preview
description: Deploy a branch to a preview environment. Use when asked for a preview link for a pull request.
---
Build the branch, deploy it to the preview cluster and reply with the link.
SKILL
cat > skills/standup-notes/SKILL.md <<'SKILL'
---
name: standup-notes
description: Summarize a standup into blockers, owners and next steps. Use when asked for standup notes.
---
List each blocker with its owner, then the next steps.
SKILL
