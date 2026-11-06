# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The socket at run time: stages ask here, and this asks the plug the run selected.

`sockets.py` holds the contracts; this module holds the running instances. It reads the plugs
S0 recorded in `run_inputs.json`, builds each plug once per process with its settings, and
hands stages what they need, always through the socket gates:

* `sql_runner(ri, dialect)`: the SQL-check plug's runner (or, with several, a runner that
  sends each statement to the plug holding its tables), wrapped in `ReadOnlyRunner`;
* `history_by_plug(ri)`: query records from the history plug(s), validated one at a time;
* `catalogue(ri, …)`: column metadata from the schema plugs.

Every plug S0 recorded is built with its settings (`build`) and asked through its methods.
No plug is chosen here by its name, and no warehouse, file format or catalogue is known here
(AGENT_GUARDRAILS rule 22).
"""
from __future__ import annotations

import pathlib
from typing import Any, Iterator

from metrics_agent import socket_gates as G
from metrics_agent import sockets as S


def _names(ri: dict, cap: str) -> list[str]:
    return [e["name"] for e in _entries(ri, cap)]


def _entries(ri: dict, cap: str) -> list[dict]:
    value = (ri.get("plugs") or {}).get(cap)
    if isinstance(value, list):
        return list(value)
    return [value] if value else []


def served(ri: dict, cap: str) -> list[dict]:
    """The plugs S0 recorded for `cap`, each with the settings it is built with."""
    return [e for e in _entries(ri, cap) if "settings" in e]


def as_list(value: Any) -> list[str]:
    """A setting that is a list, or a comma-separated string, as a list."""
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


_BUILT: dict[tuple, Any] = {}


def build(entry: dict, *, dialect: str):
    """The plug of a recorded entry, built once per process with the settings S0 recorded
    for it. Every capability of every stage reaches a plug through here."""
    import json  # noqa: PLC0415

    key = (entry["name"], json.dumps(entry.get("settings") or {}, sort_keys=True), dialect)
    if key not in _BUILT:
        _BUILT[key] = plug(entry["name"], entry.get("settings") or {}, dialect=dialect,
                           purpose=f"this run ({entry['name']})")
    return _BUILT[key]


def input_paths(entry: dict, role: str) -> list[pathlib.Path]:
    """The files and folders a recorded plug reads for `role`: every setting its own
    declarations ask for as a `path` (`setup_questions`). What a manifest records as the
    stage's inputs, by path, never by content."""
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    try:
        cls = _plugs.load(entry["name"])
    except _plugs.PlugError:
        return []
    settings = entry.get("settings") or {}
    out = []
    for q in (getattr(cls, "setup_questions", None) or {}).get(role) or ():
        if q.kind != "path":
            continue
        out += [pathlib.Path(p) for p in as_list(settings.get(q.key))]
    return out


def bi_root(files: list, dest: pathlib.Path, settings: dict) -> pathlib.Path | None:
    """The one path A1 reads a BI plug's definitions from: the `bi` folder or file the plug
    read in place when every file is under it, else `dest` when the plug wrote there, else
    the folder the files share (a single file is itself)."""
    import os  # noqa: PLC0415

    paths = [pathlib.Path(f).expanduser().resolve() for f in files or []]
    if not paths:
        return None

    def under(root: pathlib.Path) -> bool:
        return all(p == root or root in p.parents for p in paths)

    for given in as_list(settings.get("bi"))[:1]:
        root = pathlib.Path(given).expanduser().resolve()
        if under(root):
            return root
    dest = pathlib.Path(dest).expanduser().resolve()
    if dest.is_dir() and under(dest):
        return dest
    if len(paths) == 1:
        return paths[0]
    return pathlib.Path(os.path.commonpath([str(p) for p in paths]))


# ── SQL check ────────────────────────────────────────────────────────────────────────────
def plug(name: str, settings: dict | None = None, *, dialect: str, purpose: str):
    """An instance of the installed plug `name`, or a refusal saying what to install."""
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    try:
        cls = _plugs.load(name)
    except _plugs.PlugError as exc:
        raise _plugs.PlugError(f"{purpose} needs the {name!r} plug, and it cannot be used: "
                               f"{exc} (`metrics-agent plugs` lists what is installed)") from None
    return cls(settings or {}, dialect=dialect)


def sql_runner(ri: dict, dialect: str) -> G.ReadOnlyRunner:
    """A read-only runner for this run's SQL check: the SQL-check plug(s) S0 recorded, each
    built with its own settings. Raises when the run has none; callers already treat that as
    "no warehouse" and record `skipped_no_substrate`."""
    from metrics_agent.lib.engine import EngineError  # noqa: PLC0415

    checks = served(ri, "sql_check")
    if checks:
        # The SQL-check plug(s) S0 recorded, each built with its own settings. With several,
        # each statement goes to the one whose databases hold every table it reads.
        if len(checks) == 1:
            return G.ReadOnlyRunner(_runner_of(checks[0], dialect), dialect)
        return G.ReadOnlyRunner(RoutingRunner(
            [(e["name"], as_list((e.get("settings") or {}).get("databases")),
              _runner_of(e, dialect)) for e in checks], dialect), dialect)
    raise EngineError("this run lists no SQL-check plug (`inputs.sql_check` in run.yml), so "
                      "no statement can be checked against a warehouse; the witness is "
                      "parse-only.")


def _runner_of(entry: dict, dialect: str) -> S.SqlRunner:
    from metrics_agent.lib.engine import EngineError  # noqa: PLC0415

    runner = build(entry, dialect=dialect).sql_runner()
    if runner is None:
        raise EngineError(f"the {entry['name']} plug offers no SQL runner with its settings")
    return runner


def statement_tables(sql: str, dialect: str) -> list[tuple[str, str]]:
    """[(database, table name as written)] for every table a statement reads; a CTE is not a
    table. `database` is "" when the statement does not name one."""
    import sqlglot  # noqa: PLC0415
    from sqlglot import exp  # noqa: PLC0415

    from metrics_agent.lib import dialect as _dialect  # noqa: PLC0415

    try:
        tree = sqlglot.parse_one(sql, read=_dialect.sqlglot_read(dialect))
    except Exception:  # noqa: BLE001 - an unparseable statement names no table we can route
        return []
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out = []
    for t in tree.find_all(exp.Table):
        if not t.name or (not t.db and t.name.lower() in ctes):
            continue
        parts = [p for p in (t.catalog, t.db, t.name) if p]
        out.append(((t.catalog or "").lower(), ".".join(parts)))
    return list(dict.fromkeys(out))


class RoutingRunner:
    """Several SQL-check plugs as one runner, each statement sent to the ONE plug that holds
    every table it reads: by the databases each plug's settings list (`databases`), else by
    which plug's catalogue answers for the table. A statement no single plug holds raises
    `NotRouted`, and the witness records it parse-only, saying why.

    Wrapped in `ReadOnlyRunner` like any runner, so no statement that writes reaches a plug."""

    def __init__(self, routes: list[tuple[str, list[str], Any]], dialect: str) -> None:
        self.dialect = dialect
        self.routes = [(name, {d.lower() for d in dbs}, runner) for name, dbs, runner in routes]
        self._open: set[str] = set()

    def _use(self, name: str, runner: Any) -> Any:
        if name not in self._open:
            runner.connect()
            self._open.add(name)
        return runner

    def _holders(self, database: str, table: str) -> list[str]:
        named = [n for n, dbs, _ in self.routes if database and database in dbs]
        if named:
            return named
        held = []
        for name, dbs, runner in self.routes:
            if dbs and database:
                continue            # a plug that lists its databases holds only those
            try:
                if self._use(name, runner).columns_of(table):
                    held.append(name)
            except Exception:  # noqa: BLE001 - a catalogue that cannot answer holds nothing
                continue
        return held

    def route(self, tables: list[tuple[str, str]]) -> tuple[str, Any]:
        if not tables:
            name, _dbs, runner = self.routes[0]
            return name, self._use(name, runner)
        owners: set[str] | None = None
        for database, table in tables:
            held = set(self._holders(database, table))
            owners = held if owners is None else owners & held
        if not owners or len(owners) != 1:
            names = ", ".join(t for _d, t in tables)
            why = ("no SQL-check plug of this run holds all of them" if not owners
                   else f"several plugs hold them ({', '.join(sorted(owners))})")
            raise S.NotRouted(f"checked parse-only: it reads {names}, and {why}")
        name = owners.pop()
        runner = next(r for n, _d, r in self.routes if n == name)
        return name, self._use(name, runner)

    def _for(self, sql: str) -> Any:
        return self.route(statement_tables(sql, self.dialect))[1]

    def connect(self) -> None:
        """Each plug connects when a statement first goes to it."""

    def explain(self, sql: str) -> int:
        return self._for(sql).explain(sql)

    def fetch(self, sql: str, limit: int):
        return self._for(sql).fetch(sql, limit)

    def scan(self, sql: str, batch: int):
        return self._for(sql).scan(sql, batch)

    def columns_of(self, table: str) -> list[str]:
        parts = table.split(".")
        try:
            runner = self.route([(parts[0].lower() if len(parts) == 3 else "", table)])[1]
        except S.NotRouted:
            return []
        return runner.columns_of(table)

    def columns_of_many(self, tables: list[str]) -> dict[str, list[str]]:
        by_plug: dict[str, list[str]] = {}
        for t in tables:
            parts = t.split(".")
            try:
                name, _r = self.route([(parts[0].lower() if len(parts) == 3 else "", t)])
            except S.NotRouted:
                continue                    # absent evidence, never a pass
            by_plug.setdefault(name, []).append(t)
        out: dict[str, list[str]] = {}
        for name, ts in by_plug.items():
            runner = next(r for n, _d, r in self.routes if n == name)
            out.update(self._use(name, runner).columns_of_many(ts))
        return out

    def close(self) -> None:
        for name, _dbs, runner in self.routes:
            if name in self._open:
                try:
                    runner.close()
                finally:
                    self._open.discard(name)

    def __enter__(self) -> "RoutingRunner":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ── history ──────────────────────────────────────────────────────────────────────────────
def history_by_plug(ri: dict) -> Iterator[tuple[str, S.QueryRecord]]:
    """(plug, record) for every record of every history plug S0 recorded, in the order listed: each plug built with its settings and asked
    `query_history(tables, window)`, each record validated at the boundary. H0 folds them into
    ONE corpus."""
    entries = served(ri, "history")
    if not entries:
        raise S.NotSupported("the run records no history plug")
    tables = [str(t) for t in ((ri.get("bounded_tables") or {}).get("tables") or [])]
    ql = ri.get("querylog") or {}
    window = S.Window(ql.get("window_start"), ql.get("window_end"))
    for e in entries:
        records = build(e, dialect=ri.get("dialect") or "").query_history(tables, window)
        for rec in G.validated(records, "query_record.schema.json", plug=e["name"]):
            yield e["name"], rec


# ── schema ───────────────────────────────────────────────────────────────────────────────
def catalogue(ri: dict | None, *, max_description_chars: int, tables=None):
    """(columns, note): column metadata from the schema plug, in the `Columns` shape X3 reads.

    `tables` are the tables the run's layer reads; the lookup is scoped to them (with none,
    the plug reads every column it can see under the connection). `ri` is None when policy
    turned the lookup off. Never blocks: a missing plug, like an unreachable store, is an
    empty catalogue that says why.
    """
    from metrics_agent.lib.catalogue import Columns  # noqa: PLC0415

    if ri is None:
        return Columns.empty(), "the column-metadata lookup is off by policy"
    if served(ri, "schema"):
        return _plug_catalogue(ri, served(ri, "schema"), max_description_chars, tables)
    return Columns.empty(), ("the run lists no schema plug, so no column metadata: every "
                             "dimension datatype comes from the SQL or is admitted unknown")


def _plug_catalogue(ri: dict, entries: list[dict], max_description_chars: int, tables):
    """(Columns, note): the union of what each schema plug says, in the order listed. For a
    column two plugs both describe, the first listed wins and a disagreement about its type is
    recorded in the note. Never raises: a plug that cannot answer is a note."""
    from metrics_agent.lib.catalogue import Columns  # noqa: PLC0415

    want = sorted(set(tables or ()) | set(_bounded(ri)))
    by_key: dict[str, dict] = {}
    said_by: dict[str, str] = {}
    notes, conflicts = [], []
    for e in entries:
        name = e["name"]
        try:
            p = build(e, dialect=ri.get("dialect") or "")
            if "table_schema" not in S.capabilities(p):
                notes.append(f"{name}: columns come from its SQL check")
                continue
            res = p.table_schema(want, max_description_chars=max_description_chars)
            list(G.validated([res.to_dict()], "table_schema.schema.json", plug=name))
        except S.NotSupported as exc:
            notes.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - optional, always
            notes.append(f"{name} unavailable ({type(exc).__name__}: {exc})")
            continue
        notes.append(f"{name}: {res.note}")
        for table, cols in sorted(res.columns.items()):
            for c in cols:
                key = f"{table.lower()}.{c.name.lower()}"
                if key in by_key:
                    first = by_key[key].get("datatype")
                    if first and c.datatype and str(first).lower() != str(c.datatype).lower():
                        conflicts.append(f"{key} ({said_by[key]}: {first}; {name}: "
                                         f"{c.datatype})")
                    continue
                by_key[key] = {"datatype": c.datatype, "description": c.description}
                said_by[key] = name
    note = "; ".join(notes)
    if conflicts:
        note += (f"; {len(conflicts)} column(s) described differently by two schema plugs, the "
                 "first listed kept: " + ", ".join(conflicts[:10]))
    return (Columns(by_key, connection_qn="", source="+".join(e["name"] for e in entries)),
            note)


def _bounded(ri: dict) -> list[str]:
    """The run's bounded tables as S0 recorded them (empty when A2 derives them later)."""
    return [str(t) for t in ((ri.get("bounded_tables") or {}).get("tables") or []) if t]
