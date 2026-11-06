# Metrics module

Analytics owns the metrics engine as readable source in this repository.

```text
plugins/atlanai-analytics/
  modules/
    workflow.py
    metrics/
      module.json
      driver.py
      worker.py
      export.py
      validation.py
      engine/
        pyproject.toml
        src/metrics_agent/
        packages/
        examples/plugs/
        skills/
        tests/
        docs/
```

## Ownership and extension

Edit the engine, readers, input plugs, gate instructions, schemas, policy and regression corpus
in this tree. All source and test changes are reviewed and released with Analytics.

The core consumes common formats. Python entry-point groups remain the extension boundary
for inputs, BI readers, output plugs and observers. A new source is a plug, not a core special
case. Other Analytics agents get separate modules and share task-scope workflow decisions.

Runtime setup installs a private, constrained environment from the current module source. Its
content digest includes code, schemas, rules and gate instructions. The installed core and
materialized gate instructions are verified against that source before execution. Runtime
identity changes when maintained source changes; completed runs keep their matching version
until explicitly migrated. Dependencies stay pinned by constraints.txt.

## Customer flow

The plugin owns the interview, customer progress and selected Registry workspace. It reuses
known source, dialect and connection information. Read the selected asset and generated files
as task steps, without asking conversational permission for each read. Stage a browser
download once into the project's source folder; use that copy for inventory and processing.
Host filesystem controls still apply and may show their own access prompt.

Packaged-runtime initialization is an ordinary task step. The plugin does not add permission
bypass flags, change sandbox settings or create new MCP connections automatically.

Resolve the destination once. A request to save metrics in a chosen Atlan workspace authorizes
in-scope creates and their source links. Show the checked plan as progress and continue, rather
than asking for another publish confirmation. Explicit draft/preview requests defer writes.
Manual review, unknown targets, changed source scope and unauthorized revisions remain decision
points. The shared authorization helper evaluates this scope and makes no Registry calls.

## Execution and publication

The dispatcher runs the module's original runner and authored validators. At a judgment gate,
it returns instruction, prompt and answer paths to the current assistant. The assistant authors
an answer and resumes through the same validator. It never asks the customer to answer a gate.
No engine stage calls a model API or requests a model key.

Engine runs use publish: [files] and no observers. Export checks completion hashes and the
output privacy boundary, preserves the metric execution fields, and projects metrics.json,
queries.json and links.json. Actual producer attribution is recorded during generation and
carried into Registry provenance. Failed independent parity holds affected metrics and links.

The existing Registry publisher owns create/revise/reuse, ETags, idempotency, batching and
release readbacks. Its transport is unchanged. A task-scoped authorization record avoids
duplicate conversational prompts without granting host permissions or overriding release policy.

## Development checks

Run make check and make validate at the repository root. The standard suite covers source
identity, workflow authorization, projection fidelity and variant packaging. Runtime acceptance
adds synthetic source staging, every authored gate, resume, producer provenance, publication
planning and independent parity. It makes no model or Registry calls.

Use a private module runtime for runtime acceptance:

```sh
make test-metrics-runtime METRICS_RUNTIME=/absolute/private/runtime
```

The ported engine test suite and its generated fixtures are omitted from this repository.
Plugin-level regression tests and runtime acceptance remain in tools/tests. Maintain those
checks with behavior changes; the source plugs and their common socket contracts remain intact.

Python 3.11 and 3.12 on macOS/Linux are supported by the current runtime. Live model behavior
evals and external warehouse checks are separate from the offline suite. Never use eval gold
as metric-generation context.

## Structured publication holds

The adapter reads the completed C4 report, whose compiler_review warnings identify SQL the
reviewer called wrong. C4 has already matched findings to the current compile, so a stale
finding on replaced SQL does not become a publication hold. The completion receipt binds the
report alongside the verified layer and metric artifacts. Missing warehouse access, a
parse-only label and review prose do not by themselves hold an item.

Export writes publication-holds.json against those completed output hashes. Each held query
has its query ID, source ID, SQL digest, status, reason and review detail. Metrics derived
from a held query and dependent metrics are held too. Their sources and links are excluded
from the publishable files. Independent parity failures continue to create holds.

MINE check, task authorization and the resumable publisher enforce the same manifest. A held
item or link reintroduced after export is refused. The publisher checks again before returning
a call, and a changed manifest invalidates the reviewed plan. Registry transport is unchanged.

Repair rejected SQL through the engine's gates, advance and export into a fresh context. Never
remove a hold by changing descriptions, editing the manifest or deleting evidence. A completed
older run with a C4 report needs an advance to bind that report before a new export. Existing
unstructured engine contexts must be exported again; legacy hand-authored contexts keep their
existing path. Held-only runs stop with their reasons and make no publication calls.


Required member selections are checked against the completed source units and SQL independently
of review severity. An uncaptured selection or an unproved SQL restriction creates a
required_filter_missing hold. A reader-declared per-member variant may remain eligible when
its SQL groups by every required source key. Ordinary optional blank filters do not create
member requirements. Source-unit evidence is bound into the completion receipt alongside C4.
After updating the engine instructions, start a new run with the new runtime; resume older
runs with their original plugin/runtime before exporting their evidence.


## Stable source identity and current eligibility

Source staging accepts a stable source-system key through --source-id. It names the captured
asset from that key while preserving its format, custody sidecar and immutable content hashes.
The download filename can change and the same report keeps its reader identities. Different
source keys remain distinct, and changed bytes require a new capture directory. Local files
without a source-system identity retain their existing filename behavior; do not infer identity
by stripping browser suffixes. Report titles remain display context, not identity keys.

Format 2 publication manifests record eligibility check versions. All publication entry points
refuse older or incomplete check sets even when their manifest digest is internally consistent.
Rerun with the current plugin/runtime from the selected source, preserving the user's answers.
The source-stage contracts and Registry transport remain unchanged.

X3 rejects nested aggregates in one query scope, aggregate/window expressions in row filters
and windows inside aggregates. Separate subqueries and valid grouped window calculations are
accepted. Export repeats aggregation validation so older invalid card layers cannot obtain
current eligibility evidence. Focused synthetic tests cover rejection, repair, stable reader
identities across renamed downloads and outdated publication contexts. Warehouse execution
and independent parity remain separate checks.

## Interactive connector sign-in

Run preparation and advancement with a real host terminal when Power BI or Snowflake needs
a device code, browser sign-in or terminal MFA. The wrapper relays prompts immediately and
keeps stdout reserved for its JSON response. Interactive operations time out after 15 minutes.
Unattended Power BI runs still require environment credentials. Tableau's native environment
credential routes do not use interactive terminal sign-in. Never paste credentials into chat.

## Native Snowflake history

The Snowflake input defaults to native Account Usage when no shape table is selected. It
aggregates successful SELECTs over a bounded window, preserving exact SQL and session
database/schema, with execution counts, distinct-user counts and a representative query ID.
The core keeps session schemas distinct when folding common history records. User names are
not exported. The package policy defaults to 90 days and 5,000 SQL/context groups; overflow
blocks before any partial result is admitted. The existing shape-table route remains available.
Absolute run window dates make the interval reproducible; the UTC end bound is exclusive.
