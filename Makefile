# Every target is standard library Python except `validate`, which needs the claude CLI.
PYTHON ?= python3
# Sibling checkouts of the repositories upstream.json pins, for `sync` and `sync-adopt`.
AGENT_GATEWAY ?= ../gateway
APP_SKILL ?= ../skill service
APP_AGENT ?= ../agent service
APP_METRIC ?= ../metric service
SOURCES = --from registry-files=$(AGENT_GATEWAY) --from skill=$(APP_SKILL) --from agent=$(APP_AGENT) --from analytics=$(APP_METRIC)

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

# The manifests, then the components: validating a manifest does not check skills or commands.
# The directory profile adds the checks a plugin directory submission must pass.
validate:
	claude plugin validate .claude-plugin/plugin.json --strict --profile directory
	claude plugin validate .claude-plugin/marketplace.json --strict
	claude plugin validate skills --strict
	claude plugin validate commands --strict
	for addon in plugins/*/; do \
		claude plugin validate $${addon}.claude-plugin/plugin.json --strict --profile directory || exit 1; \
		for tree in skills commands agents; do \
			if [ -d $${addon}$$tree ]; then claude plugin validate $${addon}$$tree --strict || exit 1; fi; \
		done; \
	done
