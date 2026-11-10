# Design: plugs and sockets

> **Status: implemented, pre-1.0.** Everything on this page is what the code does today:
> `run.yml` names the plugs a run uses, and is the only way a run is described. The interface
> may still change before 1.0.

**On this page:** [the idea](#the-idea) · [input socket](#the-input-socket) ·
[output socket](#the-output-socket) · [observer socket](#the-observer-socket) ·
[settings](#settings-reach-every-plug) ·
[several plugs per capability](#several-plugs-per-capability) ·
[`run.yml` and the interview](#choosing-plugs-runyml-and-the-set-up-interview) ·
[the Local files plug](#the-local-files-plug) · [packaging](#packaging)

## The idea

The agent splits into a fixed **core** and replaceable **plugs**, joined by **sockets** (input, output, and an optional observer socket for tracing).

**The core is common; plugs convert.** The core reads and writes only its own common formats
and never knows which source or destination is attached. A plug is anything that converts: an
input plug turns a source into a common format the core reads (query records, table schemas,
`bi_facts`), an output plug turns the core's outputs (verified queries, metric cards,
relationships) into its destination's format and delivers it. Supporting a new warehouse, BI
tool or destination means writing a plug, never changing the core; if it seems to need a core
change, the common format is missing a field, and the format is what changes.

A socket is a written contract: what a plug must provide, in which format, and which safety
checks always run. **[The socket spec](sockets.md) is that contract, and every plug follows
it.** [Writing a plug](writing-a-plug.md) is the how-to, with a worked example and the conformance kit that tests a plug against its
socket.

```text
 INPUT PLUGS (one or more per run)
 ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌──────────────────┐
 │ Local files │ │  Snowflake  │ │    Atlan    │ │  Power BI   │ │ your plug here   │
 └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └────────┬─────────┘
        └───────────────┴───────────────┼───────────────┴─────────────────┘
                                        ▼
        ╔═══════════════════════════════════════════════════════════════════╗
        ║ INPUT SOCKET   checks: read-only SQL · custody · schema validation ║
        ╚════════════════════════════════╤══════════════════════════════════╝
                                         ▼
        ┌───────────────────────────────────────────────────────────────────┐
        │ CORE (common formats): compile → gates → merge → metrics → outputs │
        └────────────────────────────────┬──────────────────────────────────┘
                                         ▼
        ╔═══════════════════════════════════════════════════════════════════╗
        ║ OUTPUT SOCKET  publish target · dry run · ledger                  ║
        ╚════════════════════════════════╤══════════════════════════════════╝
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
             ┌────────────┐      ┌───────────────┐     ┌────────────┐
             │ Files only │      │ Registry      │     │ Glossary   │
             └────────────┘      └───────────────┘     └────────────┘
 OUTPUT PLUGS (one or more: the operator's choice)
```

## The input socket

An input plug offers any of these capabilities. It implements the ones it has and raises
`NotSupported` for the rest; the core copes with the rest missing. The exact signatures are in
`metrics_agent/sockets.py`.

| Capability | The core asks | The plug returns |
|---|---|---|
| Query history | Which SQL ran against these tables in this window? | query records: text, id, user, start time, status, type, optional counts, session database and schema |
| Table schema | What columns do these tables have? | per table: column name, data type, description |
| BI definitions | Give me the definition files for these dashboards. | raw files (Tableau `.twb`/`.tds`, Power BI TMDL, `.pbix`, `.bim`); a BI reader turns them into `bi_facts` |
| SQL check (optional) | Does this SQL compile, and what does it return? | `explain` and bounded `fetch` on a read-only connection |
| Scope (optional) | Which tables do these asset names mean? | a list of fully qualified tables |

```python
class InputPlug(Protocol):
    name: str
    kind: "input"
    custody: Literal["customer", "third-party"]   # whose system the data passes through
    requires_env: tuple[str, ...]           # environment variable NAMES it reads

    def check(self, settings) -> CheckResult: ...          # quick, read-only connection test
    def query_history(self, tables, window) -> Iterator[QueryRecord]: ...
    def table_schema(self, tables, *, max_description_chars) -> SchemaResult: ...
    def bi_definitions(self, assets, dest) -> list[Path]: ...
    def sql_runner(self) -> SqlRunner | None: ...
    def resolve_scope(self, refs) -> Scope: ...
    # optional: bi_assets(assets) -> [{role, name, id, qualified_name}]
```

The SQL runner is the plug's own: the core opens no warehouse. A runner usually wraps the
core's `lib/engine.Engine` with a `connector` the plug passes, and says whether its warehouse
keeps `information_schema` per database (`catalog_per_database`).

**Rules the socket enforces for every plug, which no plug can opt out of:**

- **Read-only.** Every statement passes the core's read-only check before it reaches a plug's
  SQL runner. A plug never receives a statement that writes.
- **Custody.** Each plug declares whose systems the data passes through. A run can be declared
  standalone, and then any plug that reads data through a third party's store is refused before
  anything is read.
- **A reader converts; the core reads `bi_facts`.** A BI plug delivers a tool's files and a BI
  reader (`sockets.BIReader`, entry points `metrics_agent.readers`) turns them into `bi_facts`,
  every vendor spelling resolved, and compiles the tool's formula language into SQL itself
  (`prepare`, `compile_unit`, with its own rule table and regression corpus). The pipeline
  never reads a vendor's syntax and knows no formula language, and a new BI tool is a package
  that registers a reader. The Tableau and Power BI readers, and the compiler they share, ship
  with the core under `metrics_agent/plugs/readers/`.
- **Validated at the boundary.** Every record is checked against a JSON schema before a stage
  sees it.

## The output socket

Every output plug receives the same finished, validated bundle in the core's own formats
(verified queries, metric cards, relationships) and converts it into its destination's format
in its own stages (the Registry plug builds its envelopes, the glossary plug its terms) before
delivering it. It declares the steps it adds to `metrics-agent run` (`run_steps_before`,
`run_steps`, `run_steps_last`), the options it adds to that command (`run_options`), the target
S0 records for it (`s0_settings`, settled by `settle_target`), what it needs before the first
stage, on a failure and at the end (`run_start`, `run_failed`, `run_complete`), and the run
artifacts, schemas and policy defaults its stages use (the `metrics_agent.artifacts` entry
points).

```python
class OutputPlug(Protocol):
    name: str
    kind: "output"
    lane: str                                     # the publish target that admits it
    requires_env: tuple[str, ...]

    def check(self, settings) -> CheckResult: ...
    def resolve(self, settings) -> dict: ...      # the settings it will publish with
    def plan(self, bundle) -> PublishPlan: ...   # dry run: sends nothing
    def publish(self, bundle) -> Ledger: ...     # does it; returns what actually changed
```

The operator always chooses the destination (`publish` in `run.yml`); it is never defaulted.
The run's publish target is the list of the lanes its output plugs declare; `files` is the
core's own lane and means nothing leaves the run directory. A dry run is always
allowed. Every publish leaves a ledger. The agent only ever creates records, never edits them.

## The observer socket

An observer watches a run without taking part in it: tracing, to any backend. The core names
no tracing system; the Registry trace (`atlan-trace`, in `metrics-agent-atlan`) is one observer
plug, and `examples/plugs/jsonl-trace` is another that writes every event to a local file.

```python
class Observer(Protocol):
    name: str
    kind: "observer"
    requires_env: tuple[str, ...]

    def start_run(self, run_dir, run_id, run_inputs, policy) -> None: ...
    def stage_span(self, stage_name, script, entry, run_id): ...   # a context manager
    def artifact(self, name, path, data, stage_name="") -> None: ...
    def gate(self, kind, question, answer, **extra) -> None: ...   # a person answered
    def decision(self, kind, subject, verdict, **extra) -> None: ...  # a rule decided
    def finish_run(self, verdict="completed", **extra) -> None: ...
```

Each stage is its own process, so `start_run` and `finish_run` run once per stage. A backend
may also define these hooks; the core calls each one only when it is there: `human_args`
(what the stage was asked to do), `update` and `remember_said` (what the stage said out loud),
`ensure_experiment` and `close_pending` (for a backend that groups runs into experiments).
A plug missing a required method is refused when it loads, naming what is missing.

* **Selection.** An observer is used when `run.yml` lists it under `observers`, or when its
  own `wanted()` says its environment is configured.
* **Several at once.** Every selected observer receives every event, so a run can trace to
  the Registry and to another system together.
* **Never in the way.** Tracing is optional. When an observer raises, the run says so once,
  naming the plug, and the run and the other observers go on. A listed observer that is not
  installed, or does not load, is said the same way.
* **Not the run's record.** The run's identity and the answers people gave at its gates are
  written by the core whatever the observers, so turning tracing off loses no decision.

## Settings reach every plug

Every input and output plug is built the same way, at every stage that uses it:
`Plug(settings, dialect=…)`, where `settings` is the plug's own `settings.<plug>` block of
`run.yml`. Nothing in the core names a plug to decide what it receives.

1. **Validated against the plug's own declaration.** When `run.yml` is loaded, a setting that
   names or carries a credential is refused, at any depth, as before. Then, for each role the
   plug is listed in, every key it declares a `SetupQuestion` for is checked by its `kind`
   (a `path` exists, a `choice` is one of its choices, a `list` is a list or a comma-separated
   string), and a `required` question must be answered. A plug whose settings have
   alternatives (a log file *or* a shape table) declares `validate_settings(role, settings)`,
   which returns what is wrong or nothing, and replaces the `required` rule for that plug.
   There is no table of settings in the core.
2. **Recorded by S0.** S0 writes each plug's validated block on its entry in `run_inputs.json`
   (`plugs.<capability>[].settings`). It never holds a credential, because the file it came
   from cannot.
3. **Handed to the plug at run time.** `plug_runtime` builds each recorded plug once per process
   with its recorded settings and calls the capability the stage needs.

**One route.** A run is described by `run.yml` and nothing else: S0 takes no input flag, so no
flag can stand for a plug. Every plug is served through its methods:

| Capability | The stage | asks the plug |
|---|---|---|
| history | H0 | `query_history(tables, window)`, through `plug_runtime`; each record validated |
| schema | X3 (the column catalogue) | `table_schema(tables, max_description_chars=…)` |
| BI | S0, or `s_fetch_bi` before it | `bi_definitions(assets, dest)` into `<run>/inputs/bi/<plug>/`, or read in place; A1 parses the folder the files share. When the plug declares `bi_assets(assets)`, S0 records the catalogue identities it returns (role, name, id, qualified name) on the BI source, and A2 joins them onto each unit's assets (`catalog_id`, `catalog_qualified_name`) |
| scope | S0 | `resolve_scope(refs)`, `refs` from `settings.<plug>.assets` |
| SQL check | H5, A4, C4 | `sql_runner()`, wrapped in the read-only runner |

The BI fetch (`fetch_settings`) is one use of this: a BI plug whose `fetch_settings` are set,
and that names no `bi` on disk, is asked for its files by `s_fetch_bi` before S0, so S0 itself
never reaches the network for BI. Any other BI plug is asked by S0, which records where the
files are. `assets` is `settings.<plug>.bi_assets` when it is set, else empty: the plug's own
settings name what to fetch.

## Several plugs per capability

Every input capability takes a list in `run.yml`, and `inputs.sql_check` takes one plug or a
list. With several plugs for one capability, every one of them is served through its methods,
in the order listed:

| Capability | With several plugs |
|---|---|
| history | ONE corpus. Read in the order listed; a statement (normalised text and session database) already supplied by an earlier plug is not counted again, so two sources over the same warehouse do not double its executions; each corpus row records the plug it came from (`plug`). |
| schema | The union. For a column two plugs both describe, the first listed wins and the disagreement is recorded in the catalogue's note. |
| BI | Several BI plugs, so several vendors in one run. A1 parses each plug's definitions on its own and merges the facts; every unit id, container and domain is prefixed with the plug's name, and each model's measures, tables and parameters are read only with that model, so two models naming the same measure do not collide. A2 and A3 run on the merged facts. |
| scope | The union of the tables each plug resolves. |
| SQL check | Allowed when each plug covers different databases. A statement goes to the plug whose `settings.<plug>.databases` lists the database of every table it reads; with no such setting, to the one plug whose catalogue holds those tables. A statement whose tables span plugs, or that no plug claims, is checked parse-only and labelled `parse_only`, with the reason on its witness verdict. With one SQL-check plug nothing changes. |
| dialect | One per run: it is the run's SQL dialect. With several SQL-check plugs, one whose `default_dialect` (or `dialects`) is not the run's dialect is refused when `run.yml` is loaded, naming the plug and both dialects. |
| outputs | any combination |
| observers | any combination; each hears every event |

**Custody, per plug.** A run must be `connected` if ANY input plug it uses has custody
`third-party` (a plug's instance may be stricter than its class: a file whose sidecar says it
passed through a third party). A standalone run refuses such a plug by name, before any
capability is called, however many other plugs it lists; a `connected` run with no such plug is
refused too.

## Choosing plugs: `run.yml` and the set-up interview

```yaml
version: 1
scope: acme
dialect: snowflake
mode: standalone
inputs:
  history: [snowflake-direct, local-files]
  schema: [snowflake-direct]
  bi: [powerbi-fabric]
  sql_check: snowflake-direct
publish: [files]
settings:
  local-files:
    history: exports/queries.jsonl.gz
```

Secrets never go in `run.yml`; each plug reads them from the environment variables it declares.

**BI fetched as part of the run.** A BI plug that can fetch its definitions from the vendor
(`powerbi-fabric`, from your own Fabric tenant) needs no extract on disk. Name what to fetch and
no `bi` path:

```yaml
inputs:
  bi: [powerbi-fabric]
settings:
  powerbi-fabric:
    targets: [https://app.powerbi.com/groups/<workspace>/reports/<report>]   # or workspace: <id>
    with_model: true    # and the models a composite model chains to
    # model: <id>   only when the fetch brings several semantic models a run could read
```

`tableau-cloud` (from your own Tableau Cloud site or Tableau Server) does the same for
Tableau: `workbooks: [<workbook URL, LUID or project/name>]`, each fetched with every published
data source it uses beside it, one folder per workbook (`model: <workbook name>` when there
are several).

`metrics-agent run` then fetches first (`s_fetch_bi`), into `<run>/inputs/bi/<plug>/`, records
the one semantic model it read in `<run>/bi_fetch.json` (per plug, when several BI plugs
fetch), and settles S0 on it. A resumed run
reads what it fetched; `--fresh` fetches again. A `bi` path always wins: an extract on disk is
never fetched over. Run by hand, the fetch is `metrics-agent stage s_fetch_bi --run <dir>
--config run.yml`, before S0.

`metrics-agent setup` builds `run.yml`: it **detects** installed plugs and which credentials are
set, **asks** what it cannot detect (can data pass through a third party, named by the plugs'
`custodian`, where the query history and
BI definitions are, which plug checks SQL, where results go, whether to trace, then scope,
dialect, tables and window), **tests** each chosen plug with its read-only `check()`, and
**writes** `run.yml` after you confirm. Where several plugs are allowed (history, BI, schema,
SQL check, outputs, observers) the question is multi-select, and each chosen plug's settings
are asked in turn. `metrics-agent setup --check run.yml` re-tests an existing file, every
listed plug. `metrics-agent run` uses `--config <path>` or `./run.yml`, or starts the
interview when there is none. An unattended run without `run.yml` exits 2 and lists the
questions instead of guessing.

Every run records the plugs it used in `run_inputs.json`, each input plug with its settings,
and the `run.yml` that described it (path and hash).

A run with several plugs per capability (`bigquery-jobs` stands for any installed plug; none of
that name ships):

```yaml
inputs:
  history: [local-files, bigquery-jobs]      # one corpus, in this order
  bi: [local-files, powerbi-fabric]          # a Tableau workbook and a Power BI model
  sql_check: [bigquery-jobs, local-files]    # each checks the databases it holds
settings:
  local-files: {history: exports/queries.jsonl, bi: tableau/, bi_vendor: tableau,
                database: local.duckdb, databases: [local]}
  bigquery-jobs: {project: example-project, location: EU, databases: [example-project]}
  powerbi-fabric: {bi: extract/sales-model}
```

**What a plug declares for the interview.** Nothing in the interview names a plug; it asks
about each one from the plug class's own declarations, all optional:

| Declaration | Meaning |
|---|---|
| `capabilities` | (input) what it offers; default: the methods it defines. Declare it when a method exists only to refuse. |
| `setup_questions` | `{role: (SetupQuestion(key, text, required, kind, choices), …)}`, roles `history`, `bi`, `sql_check`, `schema`, `publish`. Answers go to `settings.<plug>.<key>`. |
| `default_dialect` | (a SQL-check plug) the dialect offered, which the person confirms. |
| `env_missing(use)` | classmethod: the variables still unset for one use (names only; "A or B" for alternatives). Default: the unset ones in `requires_env`. |
| `offered_with` | (observer) the outputs it belongs beside; offered only when one is chosen. |
| `fetch_settings` | (BI input) the settings that ask the plug to fetch its definitions itself, e.g. `("targets", "workspace")`. With one of them set and no `bi`, `metrics-agent run` calls `bi_definitions` into the run directory before S0. |
| `validate_settings(role, settings)` | classmethod: what is wrong with these settings for `role`, or `None`. Replaces the `required` rule of `setup_questions` for a plug whose settings have alternatives. |
| `fetched_models(root)` | (BI input that fetches) the model folders a fetch wrote under `root`, one per model; with several, the run names one in `settings.<plug>.model`. Without it the fetch is one model, the folder itself. |
| `custodian` | (a `third-party` plug) who holds the data, named in the interview's question and in a refusal. |
| `issued_markers` | substrings that mark the statements the plug's own system issues (a preview, a crawl); H0 never admits one as analysis. |
| `dialects` | (a SQL-check plug) every dialect its runner speaks, when more than `default_dialect`. |
| `credential_routes` | (input) every way the plug signs in, as `CredentialRoute(name, env, note)`: `env` the variables that route needs (all declared in `requires_env`), `note` how a route with none works (a person signing in with a code). The interview lists the routes with which variables are set and never asks which credentials the person has; with nothing set it names the least to set. `check` reports the route it found and how complete the input will be (`CheckResult.route`, `.coverage`). A plug that declares routes is offered whatever is set. |
| `check_sql_session(settings)` | (a SQL-check plug) whether its session resolves a statement naming `SCHEMA.TABLE` (a current database, catalog or project), as a `CheckResult` with the fix named. Run by `setup --check` for each SQL-check plug after `check` passes; `snowflake-direct` asks `SELECT CURRENT_DATABASE()` and names `settings.snowflake-direct.catalog` or `database=` in the DSN. |

`run.yml` may name any installed plug for any capability it offers (`capabilities`); a plug
named for one it does not offer is refused when the file is loaded.

**Writing a plug's check.** `check(settings)` is read-only, takes seconds, never raises, and
returns `CheckResult(ok, message, missing_env, fix)`: `message` says what failed in plain
words and `fix` names the setting (`settings.<plug>.<key>`) or variable to change. The
interview goes back to the question that set the named setting.

## The Local files plug

The core ships one input plug, `local-files`, for everything that is already a file on your
machine. It is also what an assistant skill writes to: the skill fetches, writes these files,
and the core runs on them.

| Setting | File | Format |
|---|---|---|
| `history` | query log, `.jsonl` or `.jsonl.gz`, or a `.json` list of SQL statements (one path, or a list such as `[a.jsonl.gz, b.jsonl]`: several files are read as ONE corpus, in the order listed, each recorded on the manifest and each checked for custody) | one JSON object per line. Either one row per execution (`query_text`, `query_id`, `user_name`, `start_time`, `execution_status`, `query_type`, optional `catalog`, `schema`) or one row per distinct statement (`sql`, `exec`, `users`, optional `src`, `catalog`, `schema`) |
| `shape_table` + `database` | a table inside a DuckDB file | columns named by `columns`, default `sql,exec,users` (a fourth, the query id, is optional) |
| `schema` | `.json` | `{"DB.SCHEMA.TABLE": [{"name": …, "datatype": …, "description": …}, …]}` |
| `bi` + `bi_vendor` | a folder or a file | Tableau `.twb`, `.twbx`, `.tds`, `.tdsx`; Power BI TMDL folders, `.pbix`, `.bim`, `.tmsl`, and with their own readers a saved scan result (`powerbi-scan`), a paginated report `.rdl` (`powerbi-paginated`) and a saved scorecard (`powerbi-goals`; [Power BI service inputs](../guides/power-bi-service-inputs.md)); a Snowflake semantic view's DDL or YAML (`.sql`, `.yaml`, `.yml`; the reader ships with the Snowflake package; [declared models](declared-models-contract.md)). A folder is filtered by the chosen reader's own suffixes. `bi_model` (optional, any BI input) names the one model to read when the file holds several. A Power BI extract root holding one semantic model a run could read is read as that model. |
| `database` | a DuckDB file | used read-only for the SQL check |

**Power BI tables with no Power Query.** A Direct Lake table (`partition … = entity`) is
recorded as the lakehouse or warehouse table it reads: `database` the lakehouse or warehouse
named by its shared expression (`Sql.Database(<endpoint>, <name>)`, or the item of a OneLake
path), `schema` its `schemaName` (`dbo` on a SQL endpoint when none is given), `object` its
`entityName`. A composite model's table read by DirectQuery from another semantic model is
resolved to that model's own definition (its warehouse table, columns, measures and
relationships), up a chain of any depth, when the upstream model is supplied: in the same
extract, or as a folder or `.bim` beside this one. A link that is missing, ambiguous or loops
is refused by name.

Custody comes from the file: a history or schema file whose sidecar
`<file>.manifest.json` records any custody other than `"customer"` (`"third-party"`, written by
a fetch from a third party's store) is treated as a third party's data, and a standalone run
refuses it. The `database` setting falls back to `METRICS_AGENT_WAREHOUSE_DSN` when unset; the plug opens
the DuckDB file itself, read-only and with external access locked.

## Packaging

The core is installable on its own and runs with no account. Each family of plugs is a separate
package that depends on the core, never the other way round, and registers its plugs through the
Python entry-point group `metrics_agent.plugs`. A test fails the build if the core ever imports
vendor code.

Every plug in this repository passes the conformance kit (`metrics_agent.testing.conformance`);
a plug from another package can run the same kit in its own tests. See
[Writing a plug](writing-a-plug.md#testing-it-with-the-conformance-kit).
