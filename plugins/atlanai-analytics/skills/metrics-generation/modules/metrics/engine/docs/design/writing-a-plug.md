# Writing a plug

> **Status: the plug interface may still change before 1.0.** This guide describes it as it
> stands. If you are connecting a new system, open an issue first, so we can help you shape it
> and tell you before anything you depend on moves.

A plug connects the agent to one system: a warehouse, a BI tool, a destination for results, or
a tracing backend. The core never knows which plug is attached; it talks to every plug through
a **socket**, a written contract plus safety checks that always run. [`plugs.md`](plugs.md) is
the design and [`sockets.md`](sockets.md) the contract every plug follows. This page is how to
write one: the contract, a worked example (a BigQuery input
plug), registering it, how its settings reach it, sharing a capability with other plugs, BI
plugs, custody, `check()`, the environment, set-up questions, and testing it with the
conformance kit.

## The contract in one page

A plug is a class. The core finds it through a Python entry point, builds it with its
settings, and calls it. Three kinds:

| Kind | What it does | Must declare | Must define |
|---|---|---|---|
| `input` | supplies query history, table schemas, BI definition files, a SQL runner and/or scope resolution | `name`, `kind`, `custody`, `requires_env` | `check(settings)` and the capabilities it offers |
| `output` | delivers the finished bundle somewhere | `name`, `kind`, `lane`, `requires_env` | `check`, `resolve`, `plan`, `publish` |
| `observer` | watches a run (tracing) | `name`, `kind`, `requires_env` | `start_run`, `stage_span`, `artifact`, `gate`, `decision`, `finish_run` |

The full signatures are in `metrics_agent/sockets.py`, which is standard library only, so a plug
package can depend on it without pulling in the pipeline.

**Construction.** The core builds an input or output plug as `Plug(settings, dialect=...)`, where
`settings` is the plug's own block of `run.yml` (`settings.<plug name>`) and `dialect` is the
run's SQL dialect, and an observer as `Plug()`. Keep the block as `self.settings`: the
conformance kit checks that the built plug holds what it was given. A plug imports its
vendor's library inside the function that needs it, so installing the plug costs nothing until
a run uses it.

**Input capabilities.** Implement the ones your system has; leave out the rest, or define them
to raise `NotSupported`.

| Capability | Signature | Returns |
|---|---|---|
| `query_history` | `(tables, window)` | an iterator of `QueryRecord`: one execution, or one distinct statement with `exec_count` and `user_count` |
| `table_schema` | `(tables, *, max_description_chars)` | a `SchemaResult(columns, note, manifest_record)`: `columns` maps `DB.SCHEMA.TABLE` to a list of `Column(name, datatype, description)` |
| `bi_definitions` | `(assets, dest)` | a list of `pathlib.Path`s to raw definition files, written under `dest` or read in place; the BI reader named by `bi_vendor` turns them into `bi_facts` |
| `sql_runner` | `()` | a `SqlRunner` (`connect`, `explain`, `fetch`, `scan`, `columns_of`, `columns_of_many`, `close`, a context manager, and `dialect`), or `None` |
| `resolve_scope` | `(refs)` | a `Scope(tables, note)`: a tuple of fully qualified `DB.SCHEMA.TABLE` names |

What the core passes in:

- `tables`: the run's bounded tables. It may be empty: history admission is the core's job, and
  a schema plug asked for no table reads what it can, or returns no column and says why in
  `note`.
- `window`: a `Window(start, end)` of ISO 8601 dates or timestamps; either end may be `None`.
- `assets`: `settings.<plug>.bi_assets`, as a list (empty when unset: your own settings then
  name what to deliver). `dest` is `<run>/inputs/bi/<plug>/`.
- `refs`: `settings.<plug>.assets`, as a list.

A `QueryRecord` holds `query_text` (exactly as written; never reformat it), and optionally
`query_id`, `user_name` (the core scrubs it), `start_time`, `execution_status`, `query_type`,
`exec_count`, `user_count`, `catalog` and `schema` (the session database and schema) and
`source` (a free-text label). Set `catalog` when you know it: with several history plugs, a
statement is the same statement only when its text and session database match.

Declare `capabilities = (...)` when a method exists only to refuse, so the core and the set-up
interview know what you really offer. `run.yml` may name your plug for any capability it
offers, and is refused when it names it for one it does not.

**Rules the socket enforces, which no plug can opt out of:**

- **Read-only.** Every statement passes the core's read-only check before it reaches your
  runner (`socket_gates.ReadOnlyRunner`). Your plug never receives a statement that writes.
  Use a read-only role anyway: the check is the second lock, not the first.
- **Custody.** A standalone run refuses, by name and before any capability is called, a plug
  whose data passes through a third party's store.
- **A reader converts; the core reads `bi_facts`.** A BI plug delivers raw files and the BI
  reader its `bi_vendor` names turns them into `bi_facts`. See [BI plugs](#bi-plugs).
- **Validated at the boundary.** Every record you return is checked against its schema
  (`query_record.schema.json`, `table_schema.schema.json`, in `src/metrics_agent/data/schemas/`)
  before a stage sees it.
- **Secrets come from the environment.** Never from settings: `run.yml` is a file people share.

## A worked example: BigQuery

This plug reads a BigQuery project on the customer's own grant. It offers three capabilities:

- **SQL check.** BigQuery has no `EXPLAIN`, so `explain` is a *dry run*, which compiles the
  statement, resolves every table and column, and reads nothing.
- **History.** One record per distinct statement from `INFORMATION_SCHEMA.JOBS_BY_PROJECT`, with
  its execution and user counts, aggregated in BigQuery. No user identity leaves the warehouse.
- **Schema.** Column names, types and descriptions from
  `INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`.

The project and region are the plug's own settings (`settings.bigquery-direct.project` and
`.region` in `run.yml`), with `METRICS_AGENT_WAREHOUSE_DSN` as `project=<id>;region=<region>` as the
fallback when no project is set; the credential comes from `GOOGLE_APPLICATION_CREDENTIALS`,
which the Google client reads itself. The file below is complete; it is also what this
repository's test suite runs, against a scripted fake of BigQuery.

```python
# bigquery_direct.py
"""`bigquery-direct`: a BigQuery project, read on the customer's own grant."""
from __future__ import annotations

import os
import re
from typing import Any, Iterator

from metrics_agent import sockets as S
from metrics_agent.lib.engine import parse_kv
from metrics_agent.plugs.local_files import shape_table_records
from metrics_agent.socket_gates import ReadOnlyRunner

PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REGION = re.compile(r"^[a-z]+(-[a-z]+[0-9]*)?$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.]+(Z|[+-]\d{2}:\d{2})?)?$")


def _connect(project: str):
    """A DB-API connection. The Google client is imported here, so installing the plug costs
    nothing until a run uses it, and a test replaces this one function with a fake."""
    from google.cloud import bigquery
    from google.cloud.bigquery import dbapi

    return dbapi.connect(bigquery.Client(project=project))


def _dry_run():
    """BigQuery's compile-only mode: every name resolved, nothing read."""
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)


def _split(table: str) -> tuple[str, str, str]:
    """`project.dataset.table`, each part checked before it goes into a statement."""
    parts = table.split(".")
    if len(parts) != 3 or not PROJECT.match(parts[0]) or not all(map(NAME.match, parts[1:])):
        raise ValueError(f"not a project.dataset.table name: {table!r}")
    return parts[0], parts[1], parts[2]


def _by_dataset(tables: list[str]) -> dict[tuple[str, str], dict[str, str]]:
    groups: dict[tuple[str, str], dict[str, str]] = {}
    for t in tables:
        project, dataset, name = _split(t)
        groups.setdefault((project, dataset), {})[name] = t
    return groups


def _jobs(project: str, region: str, columns: str) -> str:
    return (f"SELECT {columns} FROM `{project}`.`region-{region}`"
            ".INFORMATION_SCHEMA.JOBS_BY_PROJECT")


class BigQueryRunner:
    """The SQL runner. The core wraps it in ReadOnlyRunner, so a write never reaches it."""

    kind = "bigquery"

    def __init__(self, project: str, dialect: str = "bigquery") -> None:
        self.project, self.dialect, self._conn = project, dialect, None

    def connect(self) -> None:
        if self._conn is None:
            self._conn = _connect(self.project)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "BigQueryRunner":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, sql: str, **kwargs: Any):
        self.connect()
        cur = self._conn.cursor()
        cur.execute(sql, **kwargs)
        return cur

    def explain(self, sql: str) -> int:
        self._run(sql, job_config=_dry_run())
        return 0                  # a dry run has no plan rows: that it compiled is the answer

    def fetch(self, sql: str, limit: int) -> tuple[list[str], list[tuple]]:
        body = sql.rstrip().rstrip(";").rstrip()      # a terminated statement cannot be wrapped
        cur = self._run(f"SELECT * FROM (\n{body}\n) AS _witness LIMIT {int(limit)}")
        return [d[0] for d in cur.description or ()], [tuple(r) for r in cur.fetchall()]

    def scan(self, sql: str, batch: int) -> Iterator[list[tuple]]:
        cur = self._run(sql)
        while rows := cur.fetchmany(batch):
            yield [tuple(r) for r in rows]

    def columns_of(self, table: str) -> list[str]:
        return self.columns_of_many([table]).get(table, [])

    def columns_of_many(self, tables: list[str]) -> dict[str, list[str]]:
        """One INFORMATION_SCHEMA read per dataset. A table missing from the answer is absent
        evidence, never "no columns"."""
        out: dict[str, list[str]] = {}
        for (project, dataset), wanted in _by_dataset(tables).items():
            names = ", ".join(f"'{t}'" for t in sorted(wanted))
            try:
                rows = self._run(f"SELECT table_name, column_name FROM `{project}.{dataset}`"
                                 f".INFORMATION_SCHEMA.COLUMNS WHERE table_name IN ({names}) "
                                 "ORDER BY table_name, ordinal_position").fetchall()
            except Exception:  # noqa: BLE001 - an unreadable dataset is absent evidence
                continue
            for table, column in rows:
                if table in wanted:
                    out.setdefault(wanted[table], []).append(str(column).lower())
        return out


WHERE = (S.SetupQuestion("project", "The project id (else project= in METRICS_AGENT_WAREHOUSE_DSN)",
                         required=False),
         S.SetupQuestion("region", "The region of its jobs (default us)", required=False))


class BigQueryDirect:
    name = "bigquery-direct"
    kind = "input"
    custody = "customer"
    capabilities = ("query_history", "table_schema", "sql_runner")
    default_dialect = "bigquery"
    requires_env: tuple[str, ...] = ("METRICS_AGENT_WAREHOUSE_DSN", "GOOGLE_APPLICATION_CREDENTIALS")
    setup_questions = {            # asked for each role the plug is chosen for
        "history": WHERE + (S.SetupQuestion(
            "shape_table", "A view of distinct statements with their counts "
            "(project.dataset.table, columns sql, exec, users)", required=False),),
        "schema": WHERE,
        "sql_check": WHERE,
    }

    def __init__(self, settings: dict[str, Any] | None = None, *,
                 dialect: str = "bigquery") -> None:
        self.settings = dict(settings or {})
        self.dialect = dialect or "bigquery"

    @classmethod
    def env_missing(cls, use: str | None = None) -> tuple[str, ...]:
        """What the interview says is unset. Only the key is always needed: the project may
        come from settings instead of METRICS_AGENT_WAREHOUSE_DSN, and `check` says when neither does."""
        return S.missing_env(("GOOGLE_APPLICATION_CREDENTIALS",))

    def _where(self) -> tuple[str, str]:
        """(project, region): this plug's own settings (`settings.bigquery-direct`), which
        the core hands it at every stage, else `METRICS_AGENT_WAREHOUSE_DSN`. Neither is a secret."""
        kv = parse_kv(os.environ.get("METRICS_AGENT_WAREHOUSE_DSN") or "")
        project = str(self.settings.get("project") or kv.get("project") or "")
        region = str(self.settings.get("region") or kv.get("region") or "us")
        if not PROJECT.match(project) or not REGION.match(region):
            raise ValueError("no BigQuery project id and region: set settings.bigquery-direct."
                             "project (and .region), or project= in METRICS_AGENT_WAREHOUSE_DSN")
        return project, region

    # -- check ---------------------------------------------------------------------------
    def check(self, settings: dict[str, Any]) -> S.CheckResult:
        """Connects, runs `SELECT 1`, dry-runs the jobs view (which proves the history
        grant and reads nothing), and reads the columns of the first bounded table when the
        interview names one."""
        needed = ("GOOGLE_APPLICATION_CREDENTIALS",) + (
            () if self.settings.get("project") else ("METRICS_AGENT_WAREHOUSE_DSN",))
        missing = S.missing_env(needed)
        if missing:
            return S.env_refusal(self.name, missing)
        try:
            project, region = self._where()
        except ValueError as exc:
            return S.CheckResult(False, str(exc), fix="settings.bigquery-direct.project, or "
                                                      "project= in METRICS_AGENT_WAREHOUSE_DSN")
        probe = (settings.get("tables") or [None])[0]
        try:
            with ReadOnlyRunner(BigQueryRunner(project, self.dialect), self.dialect) as ro:
                ro.fetch("SELECT 1", 1)
                ro.explain(_jobs(project, region, "job_id"))
                if probe and not ro.columns_of_many([probe]).get(probe):
                    return S.CheckResult(
                        False, f"connected, but {probe} has no columns these credentials "
                               "can see",
                        fix="the service account's dataset access, or the table name")
        except Exception as exc:  # noqa: BLE001 - the check reports, it never raises
            return S.CheckResult(False, f"BigQuery refused: {type(exc).__name__}",
                                 fix="GOOGLE_APPLICATION_CREDENTIALS (a key BigQuery "
                                     "accepts), or settings.bigquery-direct.project")
        return S.CheckResult(True, "connected; the jobs view compiles")

    # -- capabilities --------------------------------------------------------------------
    def sql_runner(self) -> BigQueryRunner:
        return BigQueryRunner(self._where()[0], self.dialect)

    def query_history(self, tables: list[str], window: S.Window) -> Iterator[S.QueryRecord]:
        """A shape table when one is set; otherwise the jobs view, aggregated per statement.
        Admission (bounded tables, statement type) is the core's job, so `tables` is unused."""
        if self.settings.get("shape_table"):
            project, dataset, name = _split(str(self.settings["shape_table"]))
            # A plain name: the shared reader refuses anything else and quotes each part
            # the way the dialect writes an identifier.
            yield from shape_table_records(self.sql_runner(), self.dialect,
                                           f"{project}.{dataset}.{name}")
            return
        project, region = self._where()
        where = ["job_type = 'QUERY'", "state = 'DONE'", "error_result IS NULL"]
        for op, end in ((">=", window.start), ("<=", window.end)):
            if end:
                if not STAMP.match(str(end)):
                    raise ValueError(f"not an ISO date or timestamp: {end!r}")
                where.append(f"creation_time {op} TIMESTAMP('{end}')")
        sql = (_jobs(project, region, "query AS sql, COUNT(*) AS exec, "
                                      "COUNT(DISTINCT user_email) AS users, "
                                      "MAX(job_id) AS query_id")
               + " WHERE " + " AND ".join(where) + " GROUP BY query ORDER BY exec DESC")
        with ReadOnlyRunner(self.sql_runner(), self.dialect) as ro:
            for rows in ro.scan(sql, 5000):
                for text, execs, users, job_id in rows:
                    if text and str(text).strip():
                        yield S.QueryRecord(str(text), query_id=job_id,
                                            exec_count=int(execs), user_count=int(users),
                                            source="bigquery-jobs")

    def table_schema(self, tables: list[str], *,
                     max_description_chars: int) -> S.SchemaResult:
        groups = _by_dataset(tables)          # refuses a name that is not an identifier
        columns: dict[str, list[S.Column]] = {}
        with ReadOnlyRunner(self.sql_runner(), self.dialect) as ro:
            for (project, dataset), wanted in sorted(groups.items()):
                names = ", ".join(f"'{t}'" for t in sorted(wanted))
                sql = (f"SELECT table_name, field_path, data_type, description "
                       f"FROM `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS "
                       f"WHERE table_name IN ({names}) ORDER BY table_name, field_path")
                for rows in ro.scan(sql, 5000):
                    for table, path, dtype, desc in rows:
                        if table in wanted:
                            columns.setdefault(wanted[table], []).append(S.Column(
                                path, dtype,
                                str(desc)[:max_description_chars] if desc else None))
        return S.SchemaResult(columns, f"read {len(columns)} of {len(tables)} table(s) from "
                                       "INFORMATION_SCHEMA",
                              {"source": "bigquery-information-schema"})
```

Things to notice:

- **Every identifier is checked before it goes into a statement** (`_split`, `PROJECT`,
  `NAME`, `STAMP`). Table names come from SQL the estate wrote, so they are data. The read-only
  check still runs on every statement, because the plug's own statements go through
  `ReadOnlyRunner` too.
- **Settings first, the environment as a fallback.** The project and region are ordinary
  settings, so `run.yml` and the interview carry them; only the key is a variable.
  `env_missing` therefore reports only the key, and `check` asks for `METRICS_AGENT_WAREHOUSE_DSN` only
  when no project is set.
- **`bi_definitions` and `resolve_scope` are simply absent.** The core reads `capabilities` and
  copes with the rest missing.
- **`JOBS_BY_PROJECT` keeps about 180 days** and needs the `bigquery.jobs.listAll` permission;
  the check's dry run proves the permission without reading a row.
- **The aggregation keeps people out of it.** `COUNT(DISTINCT user_email)` is all that leaves
  BigQuery; no user name reaches the agent.

## Registering it

A plug package registers each plug class under the entry-point group `metrics_agent.plugs`,
with the plug's `name` as the entry-point name. The two must match, or the plug is refused when
it loads.

```toml
[project]
name = "metrics-agent-bigquery"
dependencies = ["metrics-agent", "google-cloud-bigquery"]   # pin versions you have tested

[project.entry-points."metrics_agent.plugs"]
bigquery-direct = "bigquery_direct:BigQueryDirect"
```

After `pip install -e .`, `metrics-agent plugs` lists it with its kind, custody, capabilities
and the variables it reads (`--json` for a machine). A plug that fails to load is listed with
its error on its own line, and the command exits 1; the other plugs are listed as usual. Two
packages registering one name is refused, naming both.

A package can also add **stages** (a fetch step, for example) under the group
`metrics_agent.stages`. The value is a module whose `main()` runs the stage through
`metrics_agent.lib.stage.main`, and which calls `main()` when run as `python -m <module>` (the
pipeline runs each stage as its own process); `metrics-agent stage <name>` then runs it like a
core stage.

```toml
[project.entry-points."metrics_agent.stages"]
s_fetch_querylog_bigquery = "bigquery_fetch_stage"
```

## How a run uses it: settings, from `run.yml` to your plug

Name the plug in `run.yml` for any capability it offers, and give it a `settings.<plug>`
block. Its name means nothing to the core: a plug from any package is served the same way.

```yaml
dialect: bigquery
inputs:
  history: [bigquery-direct]
  schema: [bigquery-direct]
  sql_check: bigquery-direct
settings:
  bigquery-direct:
    project: exampleproject
    region: eu
    # shape_table: exampleproject.ops.query_shapes   # a view, instead of the jobs view
```

What happens to that block:

1. **Checked when `run.yml` is read** (by `metrics-agent run`, `metrics-agent setup --check`
   and S0). A key that names or carries a credential is refused at any depth. Then, for each
   role the plug is listed under, every key you declare a `SetupQuestion` for is checked by
   its `kind` (a `path` exists, a `choice` is one of its `choices`, a `list` is a list or a
   comma-separated string), and each `required` question must be answered. If you declare
   `validate_settings(role, settings)`, it replaces the `required` rule: return what is wrong,
   in words that name the setting, or `None`. Keys you declare no question for are passed
   through unchecked.
2. **Recorded by S0** on the plug's entry in `run_inputs.json` (`plugs.<capability>[].settings`),
   with every setting your questions declare as a `path` made absolute, so a stage run from
   another directory reads the same files.
3. **Handed to the plug** at every stage that needs it: the core builds `Plug(settings,
   dialect=…)` with the recorded block, once per process, and calls the capability. H0 asks
   `query_history`; X3, the column catalogue, asks `table_schema`; the SQL check of H5, A4 and
   C4 asks `sql_runner`; S0 asks `bi_definitions` (or `s_fetch_bi` before it), `bi_assets`
   when you declare it, and `resolve_scope`.

A few keys in a plug's block belong to the core, not to the plug; don't declare questions for
them: `witness_mode` (on the first SQL-check plug: `explain`, the default, `execute`, `parse`
or `none`), `engine` (on a history plug: the engine the history came from, checked against the
dialect) and `databases` (see below). `assets` and `bi_assets` are what the core passes to
`resolve_scope` and `bi_definitions`.

**There is no command-line route.** A run is described by `run.yml` alone: S0 takes no input
flag, and every plug, the shipped ones included, is built with its settings and asked through
its methods. Your settings are the whole interface.

## Several plugs per capability

Every input capability takes a list in `run.yml` (`inputs.sql_check` takes one plug or a
list), and every plug in it is served through its methods, in the order listed. Write your plug
so it works alongside others:

| Capability | With several plugs | What it means for your plug |
|---|---|---|
| history | ONE corpus. A statement (normalised text and session database) an earlier plug supplied is not counted again; each row records its plug. | Set `catalog` on a record when you know the session database. |
| schema | The union; for a column two plugs describe, the first listed wins and the disagreement is noted. | Return only what you know; an unknown type is `None`, not a guess. |
| BI | Each plug's definitions are parsed on their own, every unit id prefixed with the plug's name, then merged. With models from different vendors, the run's vendor is recorded as `mixed`, with `vendors` listing each. | Declare `bi_vendor`; deliver only your own files. |
| scope | The union of the tables each plug resolves. | Return fully qualified names only. |
| SQL check | Each statement goes to the plug whose `settings.<plug>.databases` lists the database of every table it reads; without that setting, to the one plug whose catalogue holds those tables. A statement no single plug holds is checked parse-only, saying why. | `columns_of` must answer only for tables you really hold; an empty list means "not mine". |
| dialect | One per run. With several SQL-check plugs, one whose `default_dialect` and `dialects` do not include the run's dialect is refused when `run.yml` is read. | Declare `dialects` if your runner speaks more than one. |

Custody is per plug: one input plug with custody `third-party` means the run must be
`connected`, and a standalone run refuses it by name however many other plugs it lists. More in [Several plugs
per capability](plugs.md#several-plugs-per-capability).

## BI plugs

A BI plug delivers raw definition files; a **BI reader** turns them into `bi_facts`, the common
format the pipeline reads. The readers for Tableau and Power BI ship with the core. A new BI
tool is a reader you register under `metrics_agent.readers` (the contract is
`sockets.BIReader`: `name`, `suffixes`, `detect(path)`, `parse(path, **opts) -> bi_facts`, and
optionally `form`, `refuse`, `explain_empty_usage`, `units_without_usage`). `parse` resolves
every vendor spelling itself: a table whose source is a SQL statement carries it
(`source_lineage.native_sql`) and each column's output name (`source_lineage.native_columns`),
and a widget whose calculated fields are compiled from their formula names the language
(`expression_language`).

**The reader compiles its own formulas.** It declares the formula languages it writes
(`languages = ("sheets-calc",)`; `parse` refuses any other) and turns them into SQL itself:
`prepare(facts)` once per model, `compile_unit(unit, rules, dialect, *, model=None,
columns=None) -> sockets.Compiled` once per widget, against the rule table it ships
(`compiler_rules`), gated by the regression cases it ships (`compiler_corpus`). A construct with
no SQL form is named in `untranslatable`, never approximated. A reader with no `compile_unit`
is still read, and its widgets are refused at A3 by name. The full contract, with the rule-row
and corpus-case formats, is [the socket spec](sockets.md#the-bi-reader-socket). Nothing in the
core changes. Beyond `bi_definitions`:

- **`bi_vendor`**: the name of the BI reader that reads your files (`"tableau"`, `"powerbi"`, or
  any installed reader). Declare it on
  the class, or, when one plug can deliver either, set it to `None` and ask for it as a
  `bi_vendor` setting (`SetupQuestion(..., kind="choice", choices=metrics_agent.readers.names())`).
  A model that declares its metrics' dimensions, joins and keys is carried as described in
  [declared models](declared-models-contract.md).
- **A path that holds several models** (a folder of model files): the reader's optional
  `models(path)` hook returns one path per model, and each is read as its own model, merged
  like two BI plugs. Without it, the path is one model.
  S0 refuses a BI plug that names neither.
- **A file that holds several models of which a run reads one** (a scan result's datasets, a
  list of scorecards): the operator names it in the BI input's `bi_model` setting, which S0
  records and A1 hands the reader as `parse(path, model=...)`. The reader matches it against
  what the file holds and refuses an unset, unmatched or ambiguous one by name.
- **Where the files go.** Write them under `dest` (`<run>/inputs/bi/<plug>/`), or return paths
  inside a folder your settings name as `bi` and read them in place. S0 records the one folder
  they share, and A1 reads everything under it.
- **`fetch_settings`**: a tuple of setting names, for a plug that fetches definitions from the
  vendor's service (`("targets", "workspace")`, say). When one of them is set and `bi` is not,
  `metrics-agent run` asks the plug for its files in a step of its own, `s_fetch_bi`, before
  S0, so S0 never reaches the network for BI; the step records what it fetched in
  `<run>/bi_fetch.json`, and a resumed run reads it rather than fetching again. By hand, run
  `metrics-agent stage s_fetch_bi --run <dir> --config run.yml` before S0. A `bi` path always
  wins: an extract on disk is never fetched over. The fetch step reads ONE model. When a fetch
  can bring several, declare `fetched_models(root)`: the model folders it wrote under `root`.
  The run reads the only one, or the one `settings.<plug>.model` names. Without it, the folder
  the fetch wrote is the model.
- **`bi_assets(assets)`**: optional, for a plug that knows the catalogue identities of the
  assets it delivered. Return `[{"role", "name", "id", "qualified_name"}]`, `role` the neutral
  one a reader gives an asset (`workbook`, `report`, …). S0 records them on the BI source, and
  A2 joins them onto each unit's assets by role and name (`catalog_id`,
  `catalog_qualified_name`), so a published object names its source by id.

## Custody

`custody` says whose systems the data passes through:

- `customer`: the customer's own systems, on their own grant (a warehouse, their BI tenant,
  files they exported).
- `third-party`: data held in, or read through, a third party's store (a catalogue's, a
  vendor's). Name the holder in `custodian` (`"Atlan"`, say): the interview and the refusal use
  it. A run declared `standalone` refuses such a plug by name, before any capability is called,
  and no setting relaxes that.

Custody belongs to the data, not to the code. A plug that reads a file can be stricter per
instance: the core reads `custody` from the plug it built with the run's settings, so set
`self.custody = "third-party"` in `__init__` when those settings point at a third party's data.
The `local-files` plug does this for a file whose sidecar `<file>.manifest.json` records any
custody other than `customer`. One `third-party` plug, among however many a run lists, means
the run must be `connected`.

## `check()`

The set-up interview calls `check(settings)` while a person waits, and `metrics-agent setup
--check run.yml` calls it again later. It must be:

- **read-only and quick**: seconds, not minutes (the kit allows 10). Prove access, read nothing
  of the customer's data: `SELECT 1`, a dry run, a namespace listing, one catalogue read.
- **never raising**: a failure is a `CheckResult(False, …)`, not an exception.
- **specific**: `message` says what failed in plain words; `fix` names the setting
  (`settings.<plug>.<key>`) or the variable to change. The interview goes back to the question
  that set a named setting.
- **names only**: never a value, not even part of one. Say `type(exc).__name__`, not the
  exception text, when the text could echo a connection string.

`check` receives the plug's own `settings.<plug>` block (the plug was built with the same
block), and, for a SQL-check plug, the run's `tables` as well, so it can prove that one bounded
table is visible. `sockets.missing_env(names)` says which variables are unset, and
`sockets.env_refusal(name, missing)` gives every plug the same wording for them.

## The environment: `requires_env` and `env_missing`

`requires_env` is every environment variable the plug reads, as a tuple of names. It is what
`metrics-agent plugs` shows and what the interview tells a person to set, so a variable the
plug reads but does not declare is one nobody is told about.

When which variables are needed depends on the use, add a classmethod
`env_missing(use) -> tuple[str, ...]`, where `use` is `query_history`, `table_schema`,
`bi_definitions`, `sql_runner`, `resolve_scope`, `publish` or `observe` (or `None`). Return names only; write
alternatives as `"A or B"` or `"A+B or C"`. Every name it can return must be in
`requires_env`. Without the hook, the core reports the unset names in `requires_env`, and the
interview does not offer the plug for that use until they are set. A variable needed only in
some cases is still declared, and left out of `env_missing`: the worked example declares
`METRICS_AGENT_WAREHOUSE_DSN`, which it reads only when no project is set, and its `check` decides
whether this run needs it.

## Set-up questions and the other declarations

All optional; the interview names no plug and asks about each one from these:

| Declaration | Meaning |
|---|---|
| `setup_questions` | `{role: (SetupQuestion(key, text, required, kind, choices), …)}`. Input roles: `history`, `bi`, `sql_check`, `schema`, each only for a capability you offer; an output uses `publish`. (A scope plug has no role here: it reads its references from `settings.<plug>.assets`.) A key asked under several roles is asked for each role chosen. `kind` is `text`, `path` (must exist), `list` (comma-separated) or `choice` (one of `choices`). Answers go to `settings.<plug>.<key>`. Never ask for a secret. |
| `default_dialect` | (a SQL-check plug) the dialect offered, one the SQL parser knows (`metrics_agent.lib.dialect.SUPPORTED`, sqlglot's dialects; the core lists none of its own). |
| `capabilities` | (input) what it offers; default: the methods it defines. |
| `offered_with` | (observer) the outputs it belongs beside; offered only when one is chosen. |
| `wanted()` | (observer) classmethod: whether to trace a run that did not list it, from its own environment. False when none of it is set. |
| `validate_settings(role, settings)` | (input) classmethod: what is wrong with these settings for `role`, or `None`. Declare it when your settings have alternatives; it replaces the `required` rule of your questions. |
| `dialects` | (a SQL-check plug) every dialect your runner speaks, when more than `default_dialect`. With several SQL-check plugs in a run, each must speak the run's dialect. |
| `credential_routes` | (input) every way the plug signs in, as `CredentialRoute(name, env, note)`: `env` the variables that route needs (all declared in `requires_env`), `note` how a route with none works (a person signing in with a code). The interview lists the routes with which variables are set and never asks which credentials the person has; with nothing set it names the least to set. `check` reports the route it found and how complete the input will be (`CheckResult.route`, `.coverage`). A plug that declares routes is offered whatever is set. |
| `check_sql_session(settings)` | (a SQL-check plug) whether a session opened with these settings resolves a statement naming `SCHEMA.TABLE`: it has a current database, catalog or project. Return a `CheckResult` like `check` (read-only, quick, never raising, the fix named, e.g. `settings.<plug>.catalog`). `setup --check` calls it for each plug `inputs.sql_check` lists once `check` passes, and the conformance kit tests it. Declare it when your warehouse can open a session with no default namespace. |
| `bi_vendor` | (a BI plug) the name of an installed BI reader (`tableau`, `powerbi`, `snowflake-semantic-view` with the Snowflake package…), or `None` when a `bi_vendor` setting says. See [BI plugs](#bi-plugs). |
| `fetch_settings` | (a BI plug) the settings that ask it to fetch its definitions itself, before S0. See [BI plugs](#bi-plugs). |
| `fetched_models(root)` | (a BI plug that fetches) the model folders a fetch wrote. See [BI plugs](#bi-plugs). |
| `custodian` | (a `third-party` plug) who holds the data. See [Custody](#custody). |
| `issued_markers` | any plug: substrings marking the statements its own system issues (a preview, a crawl), such as `("/* ATLAN(",)`. H0 drops a statement that carries one as a tool preview; the core names no tool. |

With several SQL-check plugs, the interview also asks each one for its `databases` (stored as
`settings.<plug>.databases`); you declare nothing for it.

## Output plugs and observers

An **output plug** receives the finished, validated bundle in the core's own formats
(`bundle.verified_queries`, `bundle.metrics`, `bundle.relationships`) and converts it into what
its destination takes, in its own code and stages: the core builds no destination's payload.
`plan(bundle)` is a dry run that sends nothing and returns a `PublishPlan` in its own `lane`,
whose `creates` lists one entry per RECORD it would create (each saved query, metric, edge,
term, link or upload : never one per call or stage), each with its `kind` and its name or id;
`publish(bundle)` does it and returns a ledger in the `push_ledger.schema.json` format;
`resolve(settings)` settles what the run records (a workspace id, say). The agent only ever
creates records: an output plug never edits or deletes.

What a destination needs from `metrics-agent run` it declares on the plug, so the core names
none of it:

| Declaration | What it does |
|---|---|
| `run_steps_before(options)`, `run_steps(options)`, `run_steps_last(options)` | the stages the run executes for this destination, as `(label, stage, args)`: before the pipeline, after it in the order run.yml lists destinations, and after every destination |
| `run_options` | `(flag, argparse kwargs)` pairs added to `metrics-agent run` when the plug is installed |
| `s0_settings`, `settle_target(settings, *, required)` | `(setting, variable, help)` for the target S0 records under `run_inputs.outputs.<plug>`, each read from `settings.<plug>.<setting>` in `run.yml`, else the variable; the plug checks and resolves it |
| `run_start(options)`, `run_failed(options, env, why)`, `run_complete(options, env)` | (output or observer) the run's lifecycle: before the first stage (return what to add to every stage's environment, such as an identity a trace reports as), when it stops on a failure (close what you opened), and when it completes (read back what you wrote). `options` holds the command's options (yours from `run_options` among them), `trace` and your `settings` |
| `[project.entry-points."metrics_agent.stages"]` | your stages, run as `metrics-agent stage <name>` |
| `[project.entry-points."metrics_agent.artifacts"]` | a module with `SCHEMAS`, `RUN_ARTIFACTS`, `UNSCHEMAD`: the files your stages leave in the run directory and the schemas that hold them; and optionally `POLICY`, a YAML file of your own policy sections, merged under the run's policy (a section the run's policy file names wins) | The core's `files` plug
(`metrics_agent/plugs/files.py`) is the smallest one.

An **observer** watches a run. Each stage is its own process, so `start_run` and `finish_run`
run once per stage. An observer must never stop a run on purpose, and must let a stage's own
failure through `stage_span` unchanged; when it fails, the core says so once and the run goes on
untraced, unless the operator made tracing required (`METRICS_AGENT_TRACE_REQUIRED=1`), in which case the
core stops the run and asks them. [`examples/plugs/jsonl-trace`](../../examples/plugs/jsonl-trace)
is a complete one in under a hundred lines, with a README to follow; the two SQL-check examples
beside it have one too.

## Testing it with the conformance kit

`metrics_agent.testing.conformance` is the socket contract as a pytest suite
(from a clone of this repository, `pip install -e ".[testing]"`). Subclass the class for your plug's kind, say how it
works and how it breaks, and every test is inherited. The kit runs offline: each call happens
in a sandbox that refuses network connections and child processes, records any statement that
is not a pure read, wraps your SQL runner in the core's `ReadOnlyRunner`, runs from an empty
working directory with an empty home directory, and fails a write outside `dest` (BI files) or the run folder (outputs and
observers). So you test against a fake of the remote system, as below.

```python
# test_bigquery_direct.py
import re

import pytest

import bigquery_direct
from bigquery_direct import BigQueryDirect
from metrics_agent import sockets as S
from metrics_agent.testing.conformance import InputPlugConformance

# What the fake BigQuery answers: (a pattern in the statement, column names, rows).
SCRIPT = [
    (r"JOBS_BY_PROJECT", ["sql", "exec", "users", "query_id"],
     [("SELECT order_id, amount FROM exampleproject.sales.orders", 12, 3, "job_42")]),
    (r"COLUMN_FIELD_PATHS", ["table_name", "field_path", "data_type", "description"],
     [("orders", "order_id", "INT64", "The order's identifier, unique across every region"),
      ("orders", "amount", "NUMERIC", None)]),
    (r"INFORMATION_SCHEMA\.COLUMNS", ["table_name", "column_name"],
     [("orders", "order_id"), ("orders", "amount")]),
    (r"query_shapes", ["sql", "exec", "users"],
     [("SELECT amount FROM exampleproject.sales.orders", 5, 2)]),
    (r"SELECT 1", ["one"], [(1,)]),
]


class FakeCursor:
    def __init__(self, project):
        self.project, self.description, self._rows = project, None, []

    def execute(self, sql, job_config=None):
        if self.project == "deniedproject":
            raise PermissionError("403 Access Denied")
        if job_config is not None:                # a dry run: compiled, nothing read
            self.description, self._rows = [], []
            return
        for pattern, columns, rows in SCRIPT:
            if re.search(pattern, sql):
                self.description = [(c,) for c in columns]
                self._rows = list(rows)
                return
        raise AssertionError(f"the fake has no answer for: {sql}")

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, size):
        rows, self._rows = self._rows[:size], self._rows[size:]
        return rows


class FakeConnection:
    def __init__(self, project):
        self.project = project

    def cursor(self):
        return FakeCursor(self.project)

    def close(self):
        pass


class TestBigQueryDirect(InputPlugConformance):
    plug = BigQueryDirect
    settings = {"project": "exampleproject", "region": "us"}      # settings.bigquery-direct
    env = {"GOOGLE_APPLICATION_CREDENTIALS": "/keys/reader.json"}
    tables = ["exampleproject.sales.orders"]
    window = S.Window("2026-01-01", "2026-03-31")
    broken = {
        "no project anywhere": {"settings": {"project": None}, "fix": "METRICS_AGENT_WAREHOUSE_DSN"},
        "no project in the DSN": {"settings": {"project": None},
                                  "env": {"METRICS_AGENT_WAREHOUSE_DSN": "region=us"},
                                  "fix": "settings.bigquery-direct.project"},
        "not a project id": {"settings": {"project": "Not A Project"},
                             "fix": "settings.bigquery-direct.project"},
        "no credentials": {"unset": ("GOOGLE_APPLICATION_CREDENTIALS",),
                           "fix": "GOOGLE_APPLICATION_CREDENTIALS"},
        "a project that refuses": {"settings": {"project": "deniedproject"},
                                   "fix": "GOOGLE_APPLICATION_CREDENTIALS"},
        "a table it cannot see": {"settings": {"tables": ["exampleproject.sales.missing"]},
                                  "fix": "table name"},
    }

    @pytest.fixture(autouse=True)
    def fake_bigquery(self, monkeypatch):
        monkeypatch.setattr(bigquery_direct, "_connect", FakeConnection)
        monkeypatch.setattr(bigquery_direct, "_dry_run", lambda: "dry run")
```

What the kit checks, for every kind:

- the class contract (the same check the core runs when it loads a plug), and the kind;
- `requires_env` holds names, and every name `env_missing(use)` or `check()` reports is
  declared there; with every declared variable set, nothing is reported missing;
- `check()` with nothing configured, with the working settings (must pass) and with each
  broken case (must fail, and its `fix` must name what you said): quick, never raising,
  offline, read-only, writing nothing; and no environment value in what it says, whichever
  way it ends;
- **input**: custody; `capabilities` and `setup_questions` agree with the methods; the
  default dialect is one the core knows; each declared capability answers in the socket's
  shape (records valid against their schema, at least one of them, descriptions no longer
  than asked, BI files under `dest` or a named setting, a runner that fetches, explains, scans
  and reads columns through `ReadOnlyRunner`, scopes of `DB.SCHEMA.TABLE` names); each
  undeclared one is absent or raises `NotSupported`;
- **input settings**: the working `settings` are plain JSON; for every role whose questions
  they answer (at least one key asked, every required key given) they pass your own
  declarations (question kinds, `required`, or your `validate_settings`), exactly as when
  `run.yml` is read; and the plug built with them as a run records them (paths made absolute)
  holds every key in `self.settings`. A settings mistake belongs in `broken` too, so `check`
  names the setting;
- **output**: the lane; `resolve` returns plain JSON; `plan` sends nothing and stays in its
  lane; `publish` returns a ledger that meets `push_ledger.schema.json`;
- **observer**: a whole stage of events in the harness's order, every optional hook it
  defines, and a failing stage whose exception comes through; `wanted()` is False with
  nothing configured.

The hooks, all optional except `plug`:

| Hook | Meaning |
|---|---|
| `settings`, `env` | settings and environment under which the check passes and every capability answers; override the fixtures `plug_settings(tmp_path)` and `plug_environment(tmp_path)` when they need files |
| `broken` | `{label: {"settings": {…}, "env": {…}, "unset": (…), "fix": "…"}}`; settings are merged over the working ones, and `None` removes a key |
| `tables`, `window`, `assets`, `scope_refs` | what the capabilities are asked for |
| `dialect`, `probe_sql` | the dialect the plug is built with, and the read its runner is asked to run |
| `needs_live` | `{name: reason}` for what can only be shown against the real system (`check`, a capability, `resolve`, `plan`, `publish`, `events`): skipped, saying why |
| `bundle(tmp_path)` | (outputs) a fixture; default: a minimal valid bundle |

Passing the kit is necessary, not sufficient: run your plug once against the real system
before you publish it, and say in its README what you ran.

## Known limitations

- **Table names in catalogue lookups.** The read-only runner puts only plain identifiers into a
  catalogue query: per part, `[A-Za-z0-9_$]` with single hyphens between characters, so a
  BigQuery project id such as `example-project` is looked up. A quote, a backtick, `;`,
  whitespace, `--`, `/*` or a control character is never put into the query; such a table stays
  absent evidence. When your plug builds its own catalogue query, use
  `socket_gates.plain_table_name` to admit a name and `socket_gates.quote_part(part, dialect)`
  wherever a part stands as an identifier: it quotes a hyphenated part for the dialect
  (backticks for BigQuery and Databricks, double quotes elsewhere) and refuses anything else.
