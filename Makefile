# Every target is standard library Python except `validate`, which needs the claude CLI.
PYTHON ?= python3
# The analytics helpers' offline selftests run in `check`.
ANALYTICS_SCRIPTS = plugins/atlanai-analytics/skills/analytics-build/scripts

.PHONY: check build lint test test-metrics-runtime site codex-directory validate eval eval-analytics

check:
	$(PYTHON) tools/build.py --check
	$(PYTHON) tools/lint.py
	$(PYTHON) -m unittest discover -s tools/tests -q
	$(PYTHON) $(ANALYTICS_SCRIPTS)/analytics_eval.py selftest
	$(PYTHON) $(ANALYTICS_SCRIPTS)/context_contracts.py selftest

build:
	$(PYTHON) tools/build.py

lint:
	$(PYTHON) tools/lint.py

test:
	$(PYTHON) -m unittest discover -s tools/tests -q

# Explicit integration check: set up the isolated module once, then test without network.
# The ordinary check target runs its source/projection tests without installing dependencies.
test-metrics-runtime:
	test -n "$(METRICS_RUNTIME)"
	ANALYTICS_METRICS_RUNTIME="$(METRICS_RUNTIME)" $(PYTHON) -m unittest tools.tests.test_metrics_module tools.tests.test_snowflake_history tools.tests.test_databricks_module tools.tests.test_connector_connectivity -q

# The skills as an Agent Skills discovery index, for a website to serve at /.well-known/agent-skills/.
site:
	$(PYTHON) tools/skill_index.py dist/site

# The core as a hookless package for OpenAI's plugin directory: dist/codex-directory/atlanai/ and
# the zip to upload beside it. The directory refuses plugins with lifecycle hooks.
codex-directory:
	rm -rf dist/codex-directory
	$(PYTHON) tools/build.py --codex-directory dist/codex-directory

# The marketplace, then each plugin's manifest and components: validating a manifest does not
# check skills or commands. The directory profile adds the checks a directory submission must pass.
# A mod's folder, and the core's for its claude-hooks/, is validated whole, which reads its hooks
# module, and its tests run.
validate:
	claude plugin validate .claude-plugin/marketplace.json --strict
	for plugin in plugins/*/; do \
		claude plugin validate $${plugin}.claude-plugin/plugin.json --strict --profile directory || exit 1; \
		for tree in skills commands agents; do \
			if [ -d $${plugin}$$tree ]; then claude plugin validate $${plugin}$$tree --strict || exit 1; fi; \
		done; \
		if [ -f $${plugin}hooks/hooks.json ] || [ -f $${plugin}claude-hooks/hooks.json ]; then \
			claude plugin validate $$plugin --strict || exit 1; \
			claude plugin test $$plugin || exit 1; \
		fi; \
	done

# The core plugin's behaviour evals against a local gateway emulator: claude CLI, costs money,
# not in CI. Pass options through, for example EVAL_ARGS="--runs 1 --case 'use-skill-*'".
EVAL_ARGS ?= --runs 2 -j 4
eval:
	$(PYTHON) evals/atlan/run.py -- $(EVAL_ARGS)

# The same for the atlanai-analytics plugin: routing, isolation, publishing and propose behaviour.
eval-analytics:
	$(PYTHON) evals/atlan-analytics/run.py -- $(EVAL_ARGS)
