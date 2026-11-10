# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The socket contracts: what a plug must provide, and in which shape.

The core never knows which plug is attached. An input plug supplies query history, table
schemas, BI definition files, a SQL runner and, optionally, scope resolution; an output plug
delivers the finished bundle somewhere; an observer watches a run. Everything here is
standard library only, so a plug package can depend on it without pulling in the pipeline.

The safety checks that wrap every plug (read-only SQL, custody, schema validation) live in
`metrics_agent.socket_gates`; no plug can opt out of them.
"""
from __future__ import annotations

import pathlib
from dataclasses import asdict, dataclass, field
from typing import Any, ContextManager, Iterator, Literal, Protocol, runtime_checkable

Custody = Literal["customer", "third-party"]
"""Whose systems a plug's data passes through: the customer's own, or a third party's store
(a catalogue's, a vendor's). A `third-party` plug is refused in a standalone run. A plug may
name the third party in `custodian` ("Acme Catalog"), for the words a refusal uses."""

CUSTOMER, THIRD_PARTY = "customer", "third-party"
MODES = ("standalone", "connected")
"""Where a run's inputs may come from: `standalone` reads only the customer's own systems;
`connected` reads through at least one third-party plug. Never where output goes."""

PlugKind = Literal["input", "output", "observer"]
Lane = str
"""Which publish target admits an output plug. Declared by the plug (`files` is the core's
own: nothing leaves the run directory); the core names no other."""

INPUT_CAPABILITIES = ("query_history", "table_schema", "bi_definitions", "sql_runner",
                      "resolve_scope")

# The `run.yml` role (an `inputs.<role>` key, a `setup_questions` role) each input capability
# is listed under.
ROLES = {"history": "query_history", "schema": "table_schema", "bi": "bi_definitions",
         "sql_check": "sql_runner", "scope": "resolve_scope"}


class NotSupported(Exception):
    """A plug does not offer this capability. The core decides what that means."""


class NotRouted(Exception):
    """No single SQL-check plug of the run holds every table a statement reads (with several
    SQL-check plugs, each covering different databases). The statement is checked parse-only,
    and the reason is recorded on its verdict."""


class AccessBlocked(RuntimeError):
    """An access, permission or availability limit, met by a plug: a sign-in refused, an item
    this identity may not read, a warehouse suspended or a table it has no SELECT on.

    It carries one or more blockers in the common format (`access_report.schema.json`,
    `$defs/blocker`; build them with `metrics_agent.lib.access.blocker`). The core never stops
    the run on one: it records each in the run's `access_report.json`, goes on with what is
    readable, and marks what the blocker touches as unverified. A plug raises it where it
    used to raise a plain error for an access limit (it is a `RuntimeError`, so a caller that
    knows nothing of blockers still sees a failure); the message must carry no credential."""

    def __init__(self, blockers: list[dict[str, Any]], message: str | None = None) -> None:
        self.blockers = [dict(b) for b in blockers]
        first = self.blockers[0] if self.blockers else {}
        super().__init__(message or first.get("detail") or first.get("not_read")
                         or "access blocked")


# ── records ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class QueryRecord:
    """One statement: either one execution, or a pre-aggregated shape.

    A record with `exec_count` or `user_count` set is pre-aggregated: H0 sums its counts
    instead of counting it as one execution. `query_text` is kept exactly as written and is
    never flattened (guardrail 17). `user_name` is scrubbed by the core before it is stored.
    """
    query_text: str
    query_id: str | None = None
    user_name: str | None = None
    start_time: str | None = None          # ISO 8601
    execution_status: str = "SUCCESS"
    query_type: str = "SELECT"
    exec_count: int | None = None
    user_count: int | None = None
    catalog: str | None = None             # session database
    schema: str | None = None              # session schema
    source: str | None = None              # free-text origin label

    @property
    def pre_aggregated(self) -> bool:
        return self.exec_count is not None or self.user_count is not None

    def to_dict(self) -> dict[str, Any]:
        """The record with unset optional fields left out."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "QueryRecord":
        """Read one history row in any of the shapes the pipeline accepts today.

        Read exactly as H0 always has, so moving a reader behind the socket changes nothing:

        * the statement is `query_text`, else `sql`;
        * a row with an `exec` or `users` key is pre-aggregated (a lakehouse pull, a shape
          table): its counts are taken as given, and the lakehouse's honest 0 stays 0. A
          negative count is no count at all, so `exec` and `users` are both clamped at 0;
        * the query id is the first of `ID_KEYS` present, the time the first of `TIME_KEYS`.
          A lakehouse row's `first_ts`/`last_ts` are not read: H0 never read them.
        """
        text = row.get("query_text") or row.get("sql") or ""
        if not str(text).strip():               # whitespace is not a statement
            raise ValueError("a history row needs `query_text` (one execution) or `sql` (a pre-aggregated shape)")
        pre = "exec" in row or "users" in row
        return cls(
            query_text=text,
            query_id=_first(row, ID_KEYS),
            user_name=row.get("user_name") or None,
            start_time=_first(row, TIME_KEYS),
            execution_status=str(row.get("execution_status") or "SUCCESS"),
            query_type=str(row.get("query_type") or "SELECT"),
            exec_count=max(int(row.get("exec") or 0), 0) if pre else None,
            user_count=max(int(row.get("users") or 0), 0) if pre else None,
            catalog=row.get("catalog"),
            schema=row.get("schema"),
            source=row.get("src") or row.get("source") or None)


# The keys a log names its query id and its time under; the first present wins.
ID_KEYS = ("query_id", "QUERY_ID", "queryId", "warehouse_query_id", "statement_id")
TIME_KEYS = ("start_time", "START_TIME", "startTime", "end_time")


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v)
    return None


@dataclass(frozen=True)
class Column:
    name: str
    datatype: str | None = None
    description: str | None = None         # untrusted author text: data, never instruction


@dataclass(frozen=True)
class SchemaResult:
    columns: dict[str, list[Column]]       # keyed by DB.SCHEMA.TABLE
    note: str                              # what was read, or why nothing was (on the manifest)
    manifest_record: dict[str, Any] = field(default_factory=dict)  # source, timestamp, sha

    def to_dict(self) -> dict[str, Any]:
        return {"columns": {t: [{k: v for k, v in asdict(c).items() if v is not None}
                                for c in cols] for t, cols in self.columns.items()},
                "note": self.note, "manifest_record": dict(self.manifest_record)}


@dataclass(frozen=True)
class Window:
    """The history window, as ISO 8601 dates or timestamps. Either end may be open."""
    start: str | None = None
    end: str | None = None


@dataclass(frozen=True)
class Scope:
    """What a set of asset references resolved to."""
    tables: tuple[str, ...]                # fully qualified DB.SCHEMA.TABLE
    note: str = ""


@dataclass(frozen=True)
class CheckResult:
    """The answer to "does this plug work with these settings?".

    A check is read-only and quick (seconds, not minutes), and never raises: a failure is a
    result. `message` says what failed in plain words; `fix` names the setting or environment
    variable to change (names only, never values)."""
    ok: bool
    message: str
    missing_env: tuple[str, ...] = ()      # names only, never values
    fix: str = ""
    # Optional, for a plug that signs in more than one way (`credential_routes`): the route it
    # found set (its name), and how complete the input will be under it, in plain words
    # ("every report in 3 of 4 workspaces"). Never a value.
    route: str = ""
    coverage: str = ""


@dataclass(frozen=True)
class CredentialRoute:
    """One way a plug can sign in, for the set-up interview to LIST rather than ask about.

    A plug that accepts several (`credential_routes`) is never asked "which credentials do
    you have": the interview lists every route with the variables it reads, and the plug's
    `check` finds the one that is set and says so (`CheckResult.route`, `.coverage`).
    `env` holds every variable the route needs (all of them; names only, each also in
    `requires_env`); a route with none, such as a person signing in with a code when the run
    starts, says how in `note`."""
    name: str
    env: tuple[str, ...] = ()
    note: str = ""


def route_set(route: CredentialRoute) -> bool:
    """Whether every variable `route` reads is set (a route with none is never "set": it is
    what happens when nothing else is)."""
    return bool(route.env) and not missing_env(route.env)


def missing_env(names: tuple[str, ...]) -> tuple[str, ...]:
    """The variables among `names` that are unset or blank. Names only."""
    import os  # noqa: PLC0415

    return tuple(n for n in names if not (os.environ.get(n) or "").strip())


@dataclass(frozen=True)
class SetupQuestion:
    """One non-secret setting a plug needs, as the set-up interview asks it.

    A plug declares them per role in `setup_questions` ({"history": (q, …), "bi": …,
    "sql_check": …, "schema": …, "publish": …}); the answer is stored under
    `settings.<plug>.<key>` in run.yml. `kind` is `text`, `path` (must exist), `list`
    (comma-separated) or `choice` (one of `choices`)."""
    key: str
    text: str
    required: bool = True
    kind: str = "text"
    choices: tuple[str, ...] = ()


# The optional declarations a plug class may make for the set-up interview, all generic:
#   capabilities         (input) the capabilities it offers; default: the methods it defines
#   setup_questions      {role: (SetupQuestion, …)}: the settings it needs per role
#   default_dialect      (a SQL-check plug) the dialect its runner speaks, offered and confirmed
#   env_missing(use)     classmethod: the variables still unset for one use ("query_history",
#                        "bi_definitions", "sql_runner", "table_schema", "publish", "observe"),
#                        names only, "A or B" for alternatives; default: unset `requires_env`
#   offered_with         (observer) output plugs it makes sense with; offered only beside one
#   fetch_settings       (BI input) settings that ask it to fetch its definitions itself; with
#                        one set and no `bi`, `metrics-agent run` fetches before S0 (s_fetch_bi)
#   validate_settings(role, settings)
#                        classmethod: what is wrong with these settings for `role`, or None;
#                        replaces the `required` rule for a plug whose settings have
#                        alternatives (a log file OR a shape table)
#   dialects             (a SQL-check plug) every dialect its runner speaks
#   credential_routes    (CredentialRoute, …): every way the plug can sign in. The interview
#                        lists them, never asks which one the person has; `check` reports the
#                        route it found and the coverage in `CheckResult.route`/`.coverage`
#   check_sql_session(settings)
#                        (a SQL-check plug) whether a session opened with these settings can
#                        resolve a statement naming `SCHEMA.TABLE` (it has a current
#                        database, catalog or project), as a `CheckResult` with the fix named.
#                        Read-only and quick like `check`, and never raises. `setup --check`
#                        calls it for each plug `inputs.sql_check` lists, after its `check`
#                        passed; the plug decides how to ask its own session
#   custodian            (a third-party plug) who holds the data, for a refusal's words
#   issued_markers       substrings marking the statements the plug's own system issues
#                        (a preview, a crawl): H0 never admits one as analysis
#   fetched_models(root) (a BI plug that fetches) the model folders a fetch wrote under
#                        `root`, one per model; with several, the run names one
#                        (`settings.<plug>.model`). Undeclared, the fetch is one model.
def settings_problem(cls: Any, role: str, settings: dict[str, Any]) -> str | None:
    """What is wrong with `settings` for `cls` in `role`, judged by the plug's OWN
    declarations, or None. Every key it declares a question for is checked by its kind; a
    required question must be answered unless the plug judges its settings itself
    (`validate_settings`). A key no question names is the plug's own business."""
    name = getattr(cls, "name", "?")
    questions = tuple((getattr(cls, "setup_questions", None) or {}).get(role) or ())
    for q in questions:
        value = settings.get(q.key)
        where = f"settings.{name}.{q.key}"
        if value in (None, "", []):
            continue
        if q.kind == "path":
            paths = value if isinstance(value, list) else [value]
            for v in paths:
                if not isinstance(v, str) or not pathlib.Path(v).expanduser().exists():
                    return f"{where}: {v} does not exist"
        elif q.kind == "list":
            if not (isinstance(value, str) or (isinstance(value, list)
                                               and all(isinstance(v, str) for v in value))):
                return f"{where} must be a list of names (or a comma-separated string)"
        elif q.kind == "choice":
            if value not in q.choices:
                return f"{where} is {value!r}; it must be one of {', '.join(q.choices)}"
    hook = getattr(cls, "validate_settings", None)
    if callable(hook):
        return hook(role, settings)
    for q in questions:
        if q.required and settings.get(q.key) in (None, "", []):
            return f"plug {name!r} needs settings.{name}.{q.key}"
    return None


def env_missing_for(cls: Any, use: str | None = None) -> tuple[str, ...]:
    """What `cls` still needs from the environment for `use`. Names only."""
    hook = getattr(cls, "env_missing", None)
    if callable(hook):
        return tuple(hook(use))
    return missing_env(tuple(getattr(cls, "requires_env", ()) or ()))


def env_refusal(plug: str, missing: tuple[str, ...]) -> CheckResult:
    """The check result for a plug whose credentials are not all set."""
    return CheckResult(False, f"{plug} needs {', '.join(missing)}", missing,
                       "set " + ", ".join(missing) + " in the environment")


# ── input socket ─────────────────────────────────────────────────────────────────────────
@runtime_checkable
class SqlRunner(Protocol):
    """A read-only connection to the warehouse. The core wraps every runner so a statement
    that writes never reaches it (`socket_gates.ReadOnlyRunner`)."""
    dialect: str

    def connect(self) -> None: ...
    def explain(self, sql: str) -> int: ...
    def fetch(self, sql: str, limit: int) -> tuple[list[str], list[tuple]]: ...
    def scan(self, sql: str, batch: int) -> Iterator[list[tuple]]: ...
    def columns_of(self, table: str) -> list[str]: ...
    def columns_of_many(self, tables: list[str]) -> dict[str, list[str]]: ...
    def close(self) -> None: ...
    def __enter__(self) -> "SqlRunner": ...
    def __exit__(self, *exc: Any) -> None: ...


@runtime_checkable
class InputPlug(Protocol):
    """A source of inputs. A plug implements the capabilities it has and raises
    `NotSupported` for the rest."""
    name: str                              # registry key, e.g. "local-files"
    kind: PlugKind                         # "input"
    custody: Custody
    requires_env: tuple[str, ...]          # environment variable NAMES it reads
    # Optional, for a plug offering `bi_definitions`: the BI vendor its files are (the name of
    # an installed reader; None when the operator's `bi_vendor` setting says).
    # bi_vendor: str | None
    # Optional, for a plug offering `bi_definitions`: the catalogue identities of the BI
    # assets it delivered, so a published object can name its source by id:
    #   bi_assets(assets) -> [{role, name, id, qualified_name}]
    # `role` is the neutral one a reader gives an asset (`workbook`, `report`, …).
    # Optional, for a plug offering `bi_definitions` that fetches as an identity:
    #   viewer_names() -> {"user_name": str, "full_name": str} | None
    # the names the BI tool's own user functions return for that identity, so verification
    # can bind a statement filtered to the viewing user. Returned in memory, to be bound and
    # dropped: never written, logged or kept (guardrail 8). A plug offering it writes
    # `user_name_bindable: true` in the viewer file it writes beside the definitions.

    def check(self, settings: dict[str, Any]) -> CheckResult: ...
    def query_history(self, tables: list[str], window: Window) -> Iterator[QueryRecord]: ...
    def table_schema(self, tables: list[str], *,
                     max_description_chars: int) -> SchemaResult: ...
    def bi_definitions(self, assets: list[str], dest: pathlib.Path) -> list[pathlib.Path]: ...
    def sql_runner(self) -> SqlRunner | None: ...
    def resolve_scope(self, refs: list[str]) -> Scope: ...


# ── BI readers ───────────────────────────────────────────────────────────────────────────
class ReaderRefused(ValueError):
    """A reader cannot read this path, and says why in words an operator can act on (a
    template instead of a model, a whole tenant instead of one model, a suffix it does not
    know). The stage that asked prefixes its own name; it is never a traceback."""


@runtime_checkable
class BIReader(Protocol):
    """Turns one BI tool's definition files into the common `bi_facts` format.

    A BI input plug DELIVERS files; a reader READS them. Readers are registered under the
    entry-point group `metrics_agent.readers`, with the reader's `name` as the entry-point
    name, and the core reaches one only through `metrics_agent.readers` — never by import, so
    a new BI tool is a package that registers a reader and nothing in the core changes. The
    readers the core ships (`tableau`, `powerbi`) are registered exactly the same way.

    `name` is the vendor a run names (`bi_vendor` in run.yml) and the
    `vendor` the reader writes into `bi_facts`. `parse` returns ONE model in that format,
    with every vendor-specific spelling already resolved: a table whose source is a SQL
    statement carries the statement (`source_lineage.native_sql`) and the name each column has
    in its output (`source_lineage.native_columns`), and optionally every warehouse object its
    source reads (`source_lineage.reads`); a widget whose calculated fields are
    compiled from their formula says in which language (`expression_language`). No stage after
    A1 reads the vendor's own syntax.

    `languages` names every formula language the reader writes (`formula_language`,
    `expression_language`); a formula in any other language is refused at parse, and each one
    is also an evidence source a claim may cite. `sql` is the core's own and needs no
    declaring.

    COMPILING IS THE READER'S. A formula is written in the tool's language, so turning it into
    SQL is the reader's job, never the core's. A3 asks the reader that wrote a model for:

      prepare(facts) -> model      once per model: whatever its compile needs from the whole
                                   model (the formulas a measure references, defaults...)
      compile_unit(unit, rules, dialect, *, model=None, columns=None) -> Compiled
                                   one question unit -> one statement, or a named reason why
                                   not. `model` is what `prepare` returned; a regression case
                                   passes none, only the `columns` it pins (and `logical`,
                                   the model's roles, when the case names them).
      compiler_rules: Path         its vocabulary table (rule rows, `lib/rules.py` format)
      compiler_corpus: Path        its regression cases: the gate a learned row must pass

    A reader with no `compile_unit` compiles nothing: A3 refuses its units by name.

    Optional, each called only when present:
      form(path) -> str            which of the reader's forms reads `path` (A1 counts it);
                                   raises `ReaderRefused` for a path it cannot read
      refuse(path) -> str | None   why `path` is not ONE model this reader could read, asked
                                   of every installed reader by S0 before a run is recorded
      one_model(path) -> path | None
                                   when `path` is a container of models that holds exactly
                                   ONE a run could read, that model; S0 reads it and says so
      explain_empty_usage(path) -> (counts, lines)
                                   when a model parses with no widgets: what the files say
                                   about why (counted on A1's manifest, and said out loud)
      units_without_usage(facts) -> [unit]
                                   when a model parses with no widgets: the units it offers
                                   instead (one per measure, on no dashboard); A1 still says
                                   zero usage, and what was offered
      models(path) -> [path]       the models `path` holds, one path each (a folder of model
                                   files is several models, merged like two BI plugs);
                                   without it, the path is one model

    `parse` is handed the same options for every reader, the run's `dialect` and `policy`,
    and `model`: the operator's `bi_model` setting on the BI input (None when unset), naming
    the ONE model to read when a file holds several. A reader whose file can hold several
    refuses an ambiguous one by name, listing what it holds, rather than choose; a reader
    whose file holds one ignores it. A reader takes what it needs. It may list what it read but the contract cannot carry in
    `_reader_notes` (sentences), which A1 says out loud and does not keep.
    """
    name: str
    suffixes: tuple[str, ...]              # lower-case, with the dot: the files it reads
    languages: tuple[str, ...]             # the formula languages it writes

    def detect(self, path: pathlib.Path) -> bool: ...
    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]: ...


TRANSLATED, PARTIAL, REFUSED = "translated", "partial", "refused"


@dataclass
class Compiled:
    """What a reader's `compile_unit` returns for one question unit.

    `coverage` is `translated` (the whole unit), `partial` (some measure has no SQL form; it is
    named in `untranslatable`, never approximated) or `refused` (no statement; `reason` says
    why, and `unknown` names what a new rule row would have to cover, as `language:FUNCTION`).
    `rules_used` and `provisional_rules` are rule ids from the table it was given; `gotchas`
    are sentences every object compiled from the unit carries (a value fixed at a default).
    `totals` holds one entry per total grain the unit names (`question_units` `totals`):
    {kind, group_by, sql, reason}, each its own statement beside `sql`."""
    sql: str = ""
    coverage: str = REFUSED
    reason: str = ""
    untranslatable: list[dict] = field(default_factory=list)   # {construct, in_measure}
    rules_used: list[str] = field(default_factory=list)
    provisional_rules: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)   # what a new rule would have to cover
    gotchas: list[str] = field(default_factory=list)
    totals: list[dict] = field(default_factory=list)   # {kind, group_by, sql, reason}


# ── output socket ────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Bundle:
    """The finished, validated output of a run, in the core's own formats. Built by the core,
    never by a plug.

    THE CORE'S FORMATS, NOT A DESTINATION'S. An output plug converts these into what its
    destination takes (Registry envelopes, glossary terms, files...) in its own stages; the core
    never builds a payload for a particular destination."""
    run_dir: pathlib.Path
    verified_queries: pathlib.Path         # data/schemas/verified_queries.schema.json
    metrics: pathlib.Path                  # data/schemas/metric_spec.schema.json
    relationships: pathlib.Path | None = None   # data/schemas/metric_relationships.schema.json

    def __post_init__(self) -> None:
        """THE OUTPUT BOUNDARY: a bundle that holds a user name cannot be built, so no output
        plug — shipped or a contributor's — is ever handed one. Names may live only in the
        run directory's history artifacts (`pii.redact_user_names: false`)."""
        from metrics_agent.lib import pii as _pii  # noqa: PLC0415
        _pii.refuse_user_names(self.run_dir, [p for p in (
            self.verified_queries, self.metrics, self.relationships)
            if p is not None], "publish bundle")


@dataclass(frozen=True)
class PublishPlan:
    """What `publish` would do. Producing it sends nothing."""
    lane: Lane
    # One entry per RECORD it would create (never per stage or call): each carries `kind`
    # (what the record is: "sql_query", "metric", "glossary_term", "file", …) and the
    # record's `name` or `id` (or what identifies it, like a link's two ends).
    creates: tuple[dict[str, Any], ...]
    note: str = ""


Ledger = dict
"""What a publish actually changed: a document in the `push_ledger.schema.json` format."""


@runtime_checkable
class OutputPlug(Protocol):
    """A destination. The agent only ever creates records; a plug must not patch or delete."""
    name: str                              # e.g. "registry-rest"
    kind: PlugKind                         # "output"
    lane: Lane
    requires_env: tuple[str, ...]

    def check(self, settings: dict[str, Any]) -> CheckResult: ...
    def resolve(self, settings: dict[str, Any]) -> dict[str, Any]: ...
    def plan(self, bundle: Bundle) -> PublishPlan: ...
    def publish(self, bundle: Bundle) -> Ledger: ...


# ── observers ────────────────────────────────────────────────────────────────────────────
@runtime_checkable
class Observer(Protocol):
    """Watches a run: tracing to any backend. Optional: the default observer does nothing.

    The stage harness calls these on the active observer, from every stage process. Each
    stage is its own process, so `start_run` and `finish_run` run once per stage, not once
    per run: the run span is opened by the first stage and closed by the last. An observer
    must never stop a run; the harness catches and reports what one raises.

    Required: the methods below. Optional hooks a backend may add, called only when present:
    `human_args(args, stage_name)` (what the stage was asked to do), `update(span, **meta)`
    and `remember_said(stage_name, said)` (what the stage said out loud),
    `ensure_experiment(run_dir, policy)` and `close_pending()` (a backend that groups runs).
    """
    name: str
    kind: PlugKind                         # "observer"
    requires_env: tuple[str, ...]

    def start_run(self, run_dir: pathlib.Path, run_id: str, run_inputs: dict,
                  policy: Any) -> None: ...
    def stage_span(self, stage_name: str, script: str, entry: Any,
                   run_id: str) -> ContextManager[Any]: ...
    def artifact(self, name: str, path: pathlib.Path, data: Any,
                 stage_name: str = "") -> None: ...
    def gate(self, kind: str, question: str, answer: Any, **extra: Any) -> None: ...
    def decision(self, kind: str, subject: str, verdict: str, **extra: Any) -> None: ...
    def finish_run(self, verdict: str = "completed", **extra: Any) -> None: ...


OBSERVER_REQUIRED = ("start_run", "stage_span", "artifact", "gate", "decision", "finish_run")
OBSERVER_OPTIONAL = ("human_args", "update", "remember_said", "ensure_experiment",
                     "close_pending")


def capabilities(plug: Any) -> tuple[str, ...]:
    """The input capabilities a plug offers: its declared `capabilities` when it has them
    (a plug may define a method only to refuse it), else the methods it defines. It may still
    raise `NotSupported` at call time for one it cannot serve with its settings."""
    declared = getattr(plug, "capabilities", None)
    if isinstance(declared, (tuple, list)):
        return tuple(c for c in INPUT_CAPABILITIES if c in declared)
    return tuple(c for c in INPUT_CAPABILITIES if callable(getattr(plug, c, None)))
