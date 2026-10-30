# Every target is standard library Python except `validate`, which needs the claude CLI.
PYTHON ?= python3
# Sibling checkouts of the repositories upstream.json pins, for `sync` and `sync-adopt`.
AGENT_GATEWAY ?= ../gateway
APP_SKILL ?= ../skill service
APP_AGENT ?= ../agent service
SOURCES = --from registry-files=$(AGENT_GATEWAY) --from skill=$(APP_SKILL) --from agent=$(APP_AGENT)

.PHONY: check build sync sync-adopt lint test site validate

check:
	$(PYTHON) scripts/build.py --check
	$(PYTHON) scripts/sync_upstream.py --check
	$(PYTHON) scripts/lint.py
	$(PYTHON) -m unittest discover -s tests -q

build:
	$(PYTHON) scripts/build.py

# Re-copy the vendored recipes at their pinned revisions.
sync:
	$(PYTHON) scripts/sync_upstream.py $(SOURCES)

# Move every pin to its checkout's HEAD, then copy.
sync-adopt:
	$(PYTHON) scripts/sync_upstream.py $(SOURCES) --adopt-head

lint:
	$(PYTHON) scripts/lint.py

test:
	$(PYTHON) -m unittest discover -s tests -q

# The skills as an Agent Skills discovery index, for a website to serve at /.well-known/agent-skills/.
site:
	$(PYTHON) scripts/skill_index.py dist/site

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
