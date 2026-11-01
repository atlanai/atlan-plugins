# Every target is standard library Python except `validate`, which needs the claude CLI.
PYTHON ?= python3
# Sibling checkouts of the repositories upstream.json pins, for `sync` and `sync-adopt`.
AGENT_GATEWAY ?= ../agent-gateway
APP_SKILL ?= ../app-skill
APP_AGENT ?= ../app-agent
APP_KNOWLEDGE ?= ../app-knowledge
APP_REPO ?= ../app-repo
SOURCES = --from registry-files=$(AGENT_GATEWAY) --from skill=$(APP_SKILL) --from agent=$(APP_AGENT) \
	--from knowledge=$(APP_KNOWLEDGE) --from repo=$(APP_REPO)

# The analytics helpers' offline selftests run in `check`.
ANALYTICS_SCRIPTS = plugins/atlanai-analytics/skills/atlan-analytics/scripts

.PHONY: check build sync sync-adopt lint test site validate eval eval-analytics

check:
	$(PYTHON) tools/build.py --check
	$(PYTHON) tools/sync_upstream.py --check
	$(PYTHON) tools/lint.py
	$(PYTHON) -m unittest discover -s tools/tests -q
	$(PYTHON) $(ANALYTICS_SCRIPTS)/analytics_eval.py selftest
	$(PYTHON) $(ANALYTICS_SCRIPTS)/metrics_mine.py selftest

build:
	$(PYTHON) tools/build.py

# Re-copy the vendored recipes at their pinned revisions.
sync:
	$(PYTHON) tools/sync_upstream.py $(SOURCES)

# Move every pin to its checkout's HEAD, then copy.
sync-adopt:
	$(PYTHON) tools/sync_upstream.py $(SOURCES) --adopt-head

lint:
	$(PYTHON) tools/lint.py

test:
	$(PYTHON) -m unittest discover -s tools/tests -q

# The skills as an Agent Skills discovery index, for a website to serve at /.well-known/agent-skills/.
site:
	$(PYTHON) tools/skill_index.py dist/site

# The marketplace, then each plugin's manifest and components: validating a manifest does not
# check skills or commands. The directory profile adds the checks a directory submission must pass.
validate:
	claude plugin validate .claude-plugin/marketplace.json --strict
	for plugin in plugins/*/; do \
		claude plugin validate $${plugin}.claude-plugin/plugin.json --strict --profile directory || exit 1; \
		for tree in skills commands agents; do \
			if [ -d $${plugin}$$tree ]; then claude plugin validate $${plugin}$$tree --strict || exit 1; fi; \
		done; \
	done

# The core plugin's behaviour evals against a local gateway emulator: claude CLI, costs money,
# not in CI. Pass options through, for example EVAL_ARGS="--runs 1 --case 'use-skill-*'".
EVAL_ARGS ?= --runs 2 -j 4
eval:
	$(PYTHON) evals/atlan/run.py -- $(EVAL_ARGS)

# The same for the atlanai-analytics plugin: routing, isolation, publishing and propose behaviour.
eval-analytics:
	$(PYTHON) evals/atlan-analytics/run.py -- $(EVAL_ARGS)
