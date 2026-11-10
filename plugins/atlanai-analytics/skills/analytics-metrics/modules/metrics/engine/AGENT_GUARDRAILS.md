# metrics-agent: guardrails

BI (Power BI / Tableau) + SQL query history → **verified queries** (registry `sql_query`) and
**metrics** (registry `metric`). The full stage design lives in
`PIPELINE.md`; this file is the non-negotiable subset.

These are the rules that are easy to break by accident and expensive to discover later. A
change that breaks one is refused in review, whatever else it fixes.

**The design in one line (rule 22): the core is common; plugs convert.** The core reads and
writes only its own common formats; an input plug converts a source into them and an output
plug converts them into a destination's format. Read rule 22 before adding a source, a
destination or a field.

## Hard rules

1. **Schemas before scripts.** Every artifact validates against `src/metrics_agent/data/schemas/*.json`. A stage that
   writes an artifact validates it before exit. No stage invents a shape.

2. **Deterministic stages are scripts; LLM stages are skills with a validator script behind
   them.** No stage is both. A skill's output is never trusted, only checked : every skill has
   a validator that can reject it.

   **A3 is a COMPILER, not an LLM stage.** The reader that read a model compiles its units
   (`compile_unit`, rule 22); the built-in readers share `plugs/readers/bicompile.py` and
   `plugs/readers/data/bi_compiler_rules.yml`. The compiler writes the SQL from the rule table;
   the model REVIEWS it at A3b and proposes rule ROWS, never SQL.
   A row is data : schema-checkable, diffable, reversible; arbitrary code is none of those,
   which is exactly why the rule may never be a code patch. The engine owns the statement's
   SHAPE (source table, joins, filter staging); the table owns the VOCABULARY (what `DIVIDE`
   becomes). `a3_loop.py` drives it: a learned rule recompiles **every** widget, so when the
   loop settles every statement came from one rule table. It halts on a fixpoint (a rule
   proposed again is refused as existing, so the loop cannot cycle), on drift (a rule changed
   an already-compiled statement) and on a coverage regression. A learned row is `provisional`, marked on every object it touched, and
   graduates only after holding across `compiler.graduate_after_estates` distinct estates.
   **The gate is the regression corpus, not a person** : the loop is autonomous.

3. **THE AGENT HOLDS NO MODEL KEY.** An LLM stage emits a prompt bundle (`--emit-prompt`)
   and ingests a reviewed answer (`--from`). The model that writes that answer is the
   operator's own Claude Code session running the skill : never a key this agent carries,
   and never a driver script looping over an API on its behalf.

   The failure this prevents has already happened once: C3 was batched through a shared API
   key to author thousands of questions, which put the metric agent's work on the budget of
   the system that evaluates it. That budget is the EVALUATOR's, the two must never be
   mixed, and the exhaustion showed up as "the agent is blocked" when the agent had spent
   almost none of it.

   Scale is not an exemption. A corpus too large to author in one session is authored in
   slices : `--emit-prompt` bundles are sliceable and `--from` merges : and C3's
   `--allow-structural-fallback` states, out loud and on the manifest, how much was not
   authored. C3's `--deterministic-draft` is its no-model path, flagged on every object; the
   metric gates (X1-X4) have none.

4. **No force flag, ever.** A dirty verify report, an uncited literal, a column that does not
   exist, a rejected family partition: all block. The ONE legitimate softening is missing
   *infrastructure* (no warehouse ⇒ candidates are `unprobed` / `parse_only`), never a failed
   check. Do not add `--force`, `--allow-dirty` or `--skip-gate`.

5. **Policy governs admission and ranking; it never makes a claim true.** Every threshold is in
   `agent_policy.yml`, read via `lib/policy.py`. A magic number in admission or ranking code is
   a defect. Registry transport caps are NOT policy : they are facts about the Atlan Agent
   Registry and live in the Registry plug's `registry.py` (`metrics-agent-atlan`) with their
   source.

6. **Copy, never import.** Code taken from another project is a COPY, with a provenance
   header saying where it came from and what was deliberately left behind. Importing across
   projects drags in import-time side effects and path constants.

7. **The registry source of truth is its own published contract** (its metric-kind validation).
   Field lists, caps and validation rules are read from it : never from a downstream schema that
   mirrored it once. Re-read it before touching `r1`/`r1b`/`r2`/`r3`.

8. **Scrub always; truncate never; no name leaves the run directory.** Literals matching
   `pii.redact_patterns` are replaced in place : unconditional. User names are COUNTED, never
   written (`pii.redact_user_names: true`, the default); with `false` they are kept only in the
   run directory's history artifacts (`query_corpus.jsonl`, `query_shapes.json`), to find a
   metric's owners. No switch lets one out: every write stage (`publish_target.require`), every
   output plug (a `Bundle` carrying a name cannot be built) and every observer (name lists
   reach it as counts) refuses or strips one : enforced at the boundary, generically, never per
   destination. An observed history literal is kept only when `pii.literal_min_shapes` shapes OR
   `pii.literal_min_users` people use it. `run_inputs.json` records input PATHS, never
   contents. A BI expression of the VIEWING user's name compiles to a bind placeholder
   (`viewer_bind`), never a value; A4 binds the fetching identity's name in memory only to
   execute it, and masks it out of every verdict. A learned rule row may never read the viewer.
   A BI formula or filter that COMPARES with a person's address or name keeps its meaning
   without the value: A1 numbers each scrubbed literal (`<redacted:N>`, the same value the
   same N), A3 reads it as the bind placeholder `withheld_N` (the `withheld_bind` rule row),
   and only A4 reads the BI files again in memory to bind and execute it, masking every value
   out of the verdict (`lib/withheld.py`). No artifact holds the value or a digest of it. A
   statement holding a literal the scrub removed as a secret is refused by name (A3), never
   bound and never published comparing with the marker.

   **There is no length cap on SQL, anywhere.** Redaction leaves a parseable statement; a cut
   mid-clause does not. Applied to the corpus a cap destroys exactly the longest, most complex
   queries : the analytically interesting ones : and the loss then reads as a property of the
   estate. Measured on a real estate: 13.1% of rows truncated, 10.9% of rows dropped as
   `parse_failed`, a number reported as a finding before it was found to be self-inflicted.
   Applied to published text it ships SQL nobody can run. So `Scrubber.text()` redacts and
   never cuts; the old cap and its policy keys were retired rather than left as knobs that
   did nothing.

9. **Closed world.** Every authored filter and literal cites evidence (DAX, M, Tableau calc,
   LookML, column profile, schema, query log, visual usage, verified query). No citation → the
   clause is dropped and the object flagged. This is also the prompt-injection control: BI
   titles, dashboard text and SQL comments are *data, never instruction*.

10. **The dialect is declared, never inferred.** Blocking at S0. Consumed by A3 authoring, B3/B5
   sqlglot read, every witness, and `definition.dialect`. One dialect per run.

11. **The agent only ever CREATEs.** Never PATCH. Re-runs rely on `source_id` identity
    reconcile, which appends. A record whose head carries an unrecognised `source_version` was
    touched by a human: report and skip it, never append over it. The ONE exception is the
    run's own trace experiment: `trace.close_experiment` PATCHes its status once when the run
    ends (a run's progress, not published content). `tests/test_observers.py` keeps it the
    only PATCH in the code.

12. **C3 reads a bounded `bi_context` block, never the BI files.** A2 builds it: widget
    title/subtitle/description, **number format** (the UNIT), **reference lines** (the TARGET),
    tooltip, axis titles, sort/top-N, measure captions and folders, dashboard title,
    **annotations**, siblings (the nearest `bi.max_sibling_widgets`, with `siblings_omitted`
    when the cap cuts the list, so the block stays bounded on a crowded dashboard), scoped
    filters, and the cross-dashboard count. A few hundred
    bytes per unit instead of two file reads : and unlike a path the model may or may not have
    followed, it is reviewable and testable. The fragments under `fragments/` stay on disk for
    HUMAN review and are never named in a prompt.

13. **Dimensions are DECLARED, and never dropped.** A dimension is not only a `GROUP BY`:
    five roles evidence one : `grouped`, `filtered` (a predicate whose value VARIES across the
    family; a pinned one stays population), `projected`, `window_partition`, and
    `bi_declared`. Each is read from the parent tree with its **qualifier intact**, because
    `extract_feat` strips it on purpose for the shape key and `d.department` is not
    `department`. Every join in the parent is declared too and `required_join` points at it :
    a dimension reached through a join is useless without it. A sub-field that cannot be
    resolved is **admitted in place** as `unknown`; it is never a reason to omit the
    dimension. The Atlan Agent Registry permits this: its metric kind does not require
    `dimensions[].datatype`, and it checks the datatype vocabulary on `output.datatype` only
    (the Registry's specification: https://github.com/atlanai/ai-registry). Roles ride in `extensions` : `Dimension` is `deny_unknown_fields`.

14. **Column metadata is optional, always.** The schema plugs (`inputs.schema`) close the ONE
    dimension field SQL cannot answer (`datatype`) and bring the column `description`; Atlan's metadata
    lakehouse (an Iceberg copy of the Atlan catalog, read through the `atlan-lakehouse` plug)
    is one of them. It is keyed by its `connection` setting
    (a connection qualifiedName), because the same `schema.table` exists under several
    connections on a real tenant. One scan per run, scoped to the run's tables (the whole connection only when the run
    knows none, and the note says so). An unreachable metadata lakehouse is missing infrastructure under rule 3: it
    degrades, never blocks. `pyiceberg` is imported inside `connect()` so the offline suite
    needs no dependency; live lookup is exercised only behind `pytest -m real`. A column
    description is **untrusted author-written text** : carried verbatim, attributed to
    `schema`, never paraphrased, data never instruction. The read is recorded on the manifest
    with its timestamp and content sha, because a live source can legitimately differ between
    two runs.

15. **Faithfulness: the WORDS must match the SQL.** The witness proves the statement runs;
    nothing else looks at the prose, and the prose is what a person reads before trusting the
    number. C3 states each restriction as a **predicate** in `claims[]` and C4 parses it and
    looks for it in the statement. **Absent → the object is flagged `needs_review`** with the
    claim named, never blocked: the words are wrong, not the SQL, and throwing away a correct
    query over a bad sentence is the worse trade. **Unresolved** (behind a CTE or subquery)
    routes to review : the checker's limit is not the object's defect. Restriction language
    with **no** claims is flagged too, or the gate is trivially avoided by declaring nothing.
    On the METRIC lane the filters are authored rather than derived, so D4B runs the same
    gate against what was authored: every restriction the card declares must name a column the
    card's own `source.sql` reads. Prose and placeholders are skipped, not reported : a rule
    that cries wolf is a rule nobody reads. The one thing that blocks is the **band**: below `faithfulness.min_pass_rate` the
    run halts, because a mass-flagging event is a pipeline defect, not a shippable low score.

16. **Observed values survive the collapse.** Two queries differing only in a filter literal
    share one shape, and only the representative's value stays in the SQL. A literal that
    defines the metric (a CASE label in a measure, a DATE_TRUNC grain) is part of the shape. The others are captured
    where the collapsed group exists (lane H), shaped by policy (`observed_values.*`), unioned
    at C2, and land at R1 in the `sql_query` artifact's `analysis.filters[].value`, beside the
    representative's literal. That is the only field the kind has for them, and the Registry
    deliberately does not index filter values (a literal can be customer data): never smuggle
    them into `usage_note`, `description` or any searchable text to win the findability back.
    They are CONTENT, never identity: `source_id` must stay unaffected.

17. **Never single-line-flatten SQL.** Strip comments with the parser, not a regex. A prior
    corpus pass flattened statements while leaving inline `--` comments and silently commented
    out the rest of every affected statement : 223 of 296 witness failures.

18. **A CANONICAL STATEMENT MUST COMPILE, and it must stand alone.** Parsing certifies
    syntax and nothing else : measured on a real estate, every canonical metric statement
    parsed and **38% of them failed to compile**: columns that do not exist, and CTEs
    left behind when the statement was lifted out of its parent queries. Three defences, and
    none of them is optional:

    * **The card gate refuses a `source.sql` that is not a runnable statement over the
      declared entity.** No placeholders, a real `SELECT … FROM`, and the query must read
      `source.entity`. Deterministic, needs no warehouse, and it is the one structural defect
      nothing else in the pipeline can see. X3 also refuses a reference still wearing a query
      alias outside `examples`, because `m.amount` names nothing once the SQL beside it is
      gone.
    * **`explain` is the DEFAULT witness mode.** `parse` is the fallback for an estate with no
      warehouse, and S0 says plainly that nothing was compiled when it is used.
    * **D4B WITHHOLDS a card that contradicts itself**, by name, with the count reported and
      the reason recorded : `--withhold` is an operator's decision, not a force flag, and a
      name that matches nothing drops nothing. A governed definition
      that cannot run is worse than an absent one, because a consumer trusts it. This is NOT
      the softening of rule 3: that covers missing infrastructure, and a `fail` verdict is a
      present answer. Where no warehouse exists the verdict is `skipped_no_substrate` and
      nothing is withheld for it.

19. **Secrets.** Registry token, warehouse credentials and Atlan keys from env with NO defaults.
    Never print a token value. Warehouse role is read-only; the SQL gate blocks DML/DDL.
    `run.yml` never holds a credential: a setting that names or carries one is refused when the
    file is loaded. An Atlan credential goes only to a host under the policy's allowlists
    (`atlan.allowed_domain_suffixes`, `allowed_registry_suffixes`, `allowed_polaris_suffixes`,
    shipped with the Atlan package's own policy defaults).

20. **Custody binds the source.** A `standalone` run never reads data a third party holds: a
    plug with custody `third-party` (the Atlan lakehouse, say), or a file whose sidecar says it
    passed through a third party. S0 refuses it by name, and no flag relaxes it. A `connected`
    run with no third-party plug is refused too.

    **An access limit is not a refusal.** A sign-in refused, an item an identity may not read,
    a seat that cannot download, a suspended warehouse or a table with no SELECT grant is a
    named blocker in `access_report.json` (`sockets.AccessBlocked`, `lib/access`): the run
    goes on with what is readable and marks what it touches unverified. Custody and the
    read-only check stay refusals: never record one as a blocker to get past it.

21. **The operator decides where results go, every time.** The publish target (`run.yml`'s
    `publish`, the lanes its output plugs declare) has no default and is never inferred, for example
    from which credentials are set. This limits who chooses, not what can be chosen: any output
    plug, including a new one a contributor writes, is a valid destination once the operator
    lists it. A write stage writes only to a destination the operator chose, and is a dry run
    until given its write flag.

22. **The core is common; plugs convert.** This is the design, and every change follows it.
    The core reads and writes only its own common formats and never knows which source or
    destination is attached. A **plug** is anything that converts: an *input plug* turns a
    source (files, a warehouse, a catalogue, a BI tool) into a common format the core reads :
    query records, table schemas, `bi_facts`; an *output plug* turns the core's common outputs :
    verified queries, metric cards, relationships : into a destination's format and delivers it.
    What follows from it:

    - **No core stage or library names a vendor, a source format or a destination, or branches
      on where data came from.** When a stage needs to know something about the data, the
      common format carries it as a field the plug fills. `tests/test_core_boundary.py` fails
      the build otherwise.
    - **A new source or destination is a new plug, never a core edit.** If supporting it would
      mean editing the core, the common format is missing a field: extend the format, for every
      plug, rather than teach the core a special case.
    - **A plug's own things are the plug's:** its payloads, stages
      (`metrics_agent.stages` entry points), run artifacts and schemas (`metrics_agent.artifacts`),
      the steps and options it adds to `metrics-agent run` (`run_steps*`, `run_options`), what it
      does before, after or when a run fails (`run_start`, `run_complete`, `run_failed`), the
      target S0 records for it (`s0_settings`) and its policy defaults (`POLICY`).
    - **The core reaches a plug only through the registry**: by capability, by lane or by an
      attribute the plug declares, never by importing it and never by its name. A run is
      described by `run.yml` alone: S0 takes no input flag, so no flag can mean a plug.
    - **A BI tool's language is its reader's.** The reader declares the formula languages it
      writes (`languages`) and compiles them into SQL itself (`prepare`, `compile_unit`), with
      its own rule table and regression corpus. The core keeps only what every compiler shares:
      the rule-table format and its gate (`lib/rules.py`) and the result (`sockets.Compiled`).
      The common schemas name no language; their `x-vocabulary` is filled from the readers.
    - **Every plug follows the socket spec**, `docs/design/sockets.md`: what each socket
      takes, what it must return, and the common formats. A plug that needs more than the spec
      gives is a change to the spec, for every plug.
    - Built-in plugs (local files, the files output, the BI file readers and their compiler)
      ship with the core distribution, under `metrics_agent/plugs/`, and follow the same rule.

    What stays in the core on purpose: the safety contract every plug is held to (read-only
    SQL, custody, the publish target and its lanes, schema validation, the closed evidence
    vocabulary), in neutral words: custody `customer` or `third-party`, mode `standalone` or
    `connected`, lanes declared by the output plugs. There is no other exception.
    `tests/test_core_boundary.py` checks code strings, schema keys and enums, core policy keys,
    S0's flags and these words.

23. **The BI tool's own metadata is context, never semantics; a grade names its evidence.**
    A measure's description, display folder, annotations, perspectives, field comments, content
    tags and an asset's endorsement (certified, promoted) are read into `bi_facts` and carried to
    the gate bundles and the metric card. No compiler reads them and none changes a statement: a
    description saying "excludes returns" adds no filter. Who endorsed an asset is never
    written. The conversion grade (`quality`: exact, equivalent_with_gotcha, approximate,
    unverified, refused) says which checks ran; `exact` is the compiler's complete translation
    plus the warehouse accepting it, never a claim that the value was compared with the BI tool.

## Settled decisions (do not re-litigate)

| | |
|---|---|
| Dataset granularity | **One** dataset per run scope. Domain is a deterministic `categories` value : BI workbook/report name or warehouse schema, verbatim. No clustering, no confirmation, no LLM. |
| `label` vocabulary | Exactly `verified` · `parse_only` · `needs_review`. **Never `approved`.** |
| Examples per metric | **Chosen, not ranked.** The card's author names which of the metric's own source statements illustrate it; X3 refuses any other, and carries each example's SQL and question from the verified layer rather than letting the author retype them. |
| Dimensions | Declared from the verified queries in all five roles, never dropped. Unresolved sub-fields admitted as `unknown`. |
| Joins | Every join in the parent is declared. Never pruned : the rule D1b already applies to the slice SQL. `cardinality` from the BI model where it declares one, `unknown` otherwise. |
| A3 | A deterministic compiler. The LLM reviews and proposes rule rows; it never writes SQL. |
| Compiler rules | Data in a rule table (the built-in readers': `plugs/readers/data/bi_compiler_rules.yml`), never code. Learned rows are provisional and gated by the regression corpus. |
| Metadata lakehouse | Optional always. Supplies dimension `datatype` and `description`. Live lookup only behind `pytest -m real`. |
| Scope | Both lanes, both BI vendors. Nothing deferred. |
| Table bounding | `querylog.scope.match: any` : a query survives if it touches **at least one** bounded table, and its outside tables are recorded on the object. `all` (every table inside the bound) stays available as a policy value. |
| Gate 2 | Blocks a column missing from a table it COULD read; **flags** one missing because the table could not be read at all. Unverified is not the same as wrong. |
| Ungrounded metrics | A card that contradicts itself is **withheld** at D4B, by name and with the reason; the published `metric_refs_by_vq` names only what survived. |
| Record versioning | Create-only with a stable `source_id`; never PATCH. |
| Glossary sync | The glossary is the view PEOPLE read, derived one way from the layer. A term knows where it came from: its identity keys and, per field, the hash of what the agent wrote live ON THE TERM, never only in a run directory. Paired by Registry id → `source_id` → `metric_ref` (key rewritten) → adopted once by name (neither key), inside the target glossary only. Definition-bearing attributes are always written; name, description and README only where the agent wrote the live value last : a person's is kept and the withheld change reported. Categories, owners, certificate and placement are never sent after create; a new term lands in the policy's inbox category. A retired metric's term is deprecated once, never deleted. |
| BI context | C3 gets an inlined `bi_context` block, not file paths. Fragments are review-only. |
| Faithfulness | Claims are stated as predicates and checked against the statement. Absent flags, band blocks. |
| Observed values | Carried for BOTH lanes. Floor defaults to 1 (carry everything); the per-column cap is a policy value and a cap that bites is reported, never silent. |
| Plugs | The core is common; plugs convert (rule 22). Any combination of plugs. The core never routes by a plug's name: each plug is validated against its own declarations, recorded with its settings, and served through its methods (`plug_runtime`). Several plugs per capability follow the plug design, `docs/design/plugs.md` in the public tree (one history corpus, schema and scope unions, mixed BI vendors, SQL check routed by database); one dialect per run; custody is per plug and a standalone run refuses any third-party one. |

## Layout

```
src/metrics_agent/ the installable package (`pip install -e .`, command `metrics-agent`)
  data/            the agent's own data files, shipped inside the package (`_paths.DATA`):
                   schemas/ (the contract surface) · agent_policy.yml
  lib/             policy · manifest · pii · redact · slugs · dialect · sqlshape · witness
                   engine · whgate · card_sql · evidence · dimensions · faithfulness
                   rules · measures · consistency · cardfields · …
  plugs/           the built-in plugs: local-files (input), files (output), and
                   readers/ (Tableau and Power BI → bi_facts.schema.json, and bicompile, the
                   compiler they share; readers/data/ holds its bi_compiler_rules.yml and
                   compiler_corpus/, the regression corpus that GATES every learned rule,
                   synthetic only); reached through the registries only, plugs/__init__.py
                   and readers.py (rule 22)
  sockets.py · plug_runtime.py · socket_gates.py · observe.py · run_config.py · setup.py
                   the plug interface, building plugs, the read-only and custody gates,
                   observers, run.yml, the set-up interview
  stages/s0_*      run inputs
  stages/a*_       lane A (BI)          a1 parse · a2 units · a3 COMPILE · a3b review
                                        · a3_loop (driver) · a4 witness
  stages/h*_       lane H (history)     h0 corpus · h1 shapes · h2 measures · h5 verified
  stages/c*_       lane C (converge)    c1 normalise · c2 merge · c3 questions · c4 gates
                                        (incl. faithfulness) · c5 emit
  stages/m*_       merge + describe     m1 merge · m2 describe (a GATE)
  stages/d*_ x*_   lane D (metrics)     d1 candidates · x1 propose · x2 merge · x3 emit
                                        (the card) · d4b consistency · x4 relate.
                                        X1-X4 are GATES.
  stages/run.py    the one command      `metrics-agent run`; it runs the steps each output
                                        plug declares and names none of them
packages/          plug packages that depend on the core, never the reverse:
  metrics-agent-atlan/  the `atlan-lakehouse` plug (lakehouse history, metadata-lakehouse columns, Atlan asset
                   scopes, Atlan's query path) and its policy defaults (`atlan.*`,
                   `glossary.*`, `get_context.*`), the `registry-rest` output plug (R1/R1b
                   envelopes and R2 gateway rules from the core's bundle, R3/R3b/R4 push),
                   the `atlan-glossary` output plug (G1 terms from the bundle, G2-G4), the
                   `atlan-trace` observer (tracing, T0-T2), and the Atlan fetch stages
  metrics-agent-snowflake/  the `snowflake-direct` plug (SQL check, schema, shape-table
                   history, semantic views as BI definitions) and the
                   `snowflake-semantic-view` BI reader (a declared model's
                   declarations too, through lib/declared.py)
  metrics-agent-powerbi/    the `powerbi-fabric` plug (Power BI from Microsoft Fabric)
  metrics-agent-tableau/    the `tableau-cloud` plug (Tableau workbooks and their published
                   data sources, from Tableau Cloud or Tableau Server)
examples/plugs/    community example plugs (databricks-direct, postgres-direct, jsonl-trace)
scripts/           one-line shims, so `python scripts/<stage>.py` keeps working; and the
                   standalone Power BI extractor (scripts/pbi-definition-extract/)
skills/    the LLM stages (describe-verified-sql · author-questions · propose-metrics ·
                   merge-metrics · author-metric-card · relate-metrics · review-compiler)
                   + the orchestrator (run-pipeline) and setup-run
tests/fixtures/    SYNTHETIC ONLY : no customer extract, no customer log, ever
output/            git-ignored
```

Nothing is published to a package index. Every package here installs from a clone by path:
`pip install -c constraints.txt -r requirements-all.txt` is everything (and what CI installs),
`pip install -e packages/<name>` one plug package. So no extra, doc, skill or message names one
of this repository's packages for pip to fetch by name: on an index that name could belong to
anyone (tests/test_packaging.py scans every `pip install` a user can read).

## CLI convention

Every stage takes `--run <dir>` and `--policy <path>`, reads its named inputs from the run
directory, writes **exactly one** artifact, and appends **exactly one** manifest entry. A stage
that writes two artifacts is two stages.

## Estate profiles

`bi-only` · `history-only` · `both` : all first-class, none degraded. An absent lane is not
run: `metrics-agent run` plans only the lanes the profile S0 settled has, and `m1_merge` builds
the layer from whichever lane is present. Where a lane is absent its
signal is replaced by a NAMED substitute recorded on the object (`provenance.ranking_basis`),
never by a default.
