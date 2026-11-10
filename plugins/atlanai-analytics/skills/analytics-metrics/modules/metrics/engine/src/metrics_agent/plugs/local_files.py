# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The Local files plug: inputs that are files on this machine.

Everything a run can read without a network:

* **history**: a query log, JSONL or JSONL.gz, one row per execution or one row per distinct
  statement (`QueryRecord.from_row` reads both); a `.json` list of SQL statements or rows; or
  a shape table inside a DuckDB file.
* **schema**: a JSON file `{"DB.SCHEMA.TABLE": [{"name", "datatype", "description"}, …]}`.
* **BI definitions**: a folder or file of BI definitions that an installed BI reader reads
  (`metrics_agent.readers`: the core ships Tableau and Power BI). This plug only finds the
  files, by the suffixes the installed readers declare; the reader `bi_vendor` names reads them.
* **SQL check**: a read-only DuckDB database.

It is also where an assistant skill lands what it fetched: the skill writes these files and the
core runs with this plug. A `bi` folder holding a `capture_manifest.json` is a capture
(`lib/bi_capture`, written by the `fetch-bi-in-browser` or `fetch-bi-over-mcp` skill): only the
definition files its manifest lists are read, each checked against the hash it recorded, and
its oracle files never are.

Custody comes from the file, not from the plug. A history or schema file is the customer's
unless a sidecar `<file>.manifest.json` says the data passed through a third party
(`"custody": "third-party"`) (the lakehouse pull writes
one), in which case the socket refuses it in a standalone run.

Settings (all optional, all paths):
    history       a log file, or a list of them
    shape_table   DB.SCHEMA.TABLE inside `database`, read instead of a log file
    columns       the shape table's columns: sql,exec,users[,query_id]
    schema        the schema JSON file
    bi            the BI folder or file
    bi_vendor     the installed BI reader that reads `bi`
    database      the DuckDB file (default: $METRICS_AGENT_WAREHOUSE_DSN)
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any, Iterator

from metrics_agent import readers as _readers
from metrics_agent import sockets as S


def _vendors() -> tuple[str, ...]:
    """The vendors `bi_vendor` may name: every installed BI reader, never a fixed list."""
    return _readers.names()


class _SetupQuestions:
    """`setup_questions`, built when read: the `bi_vendor` choices are the readers installed
    at that moment, so a package that adds one is offered without this plug changing."""

    def __get__(self, obj: Any, cls: Any) -> dict[str, tuple[S.SetupQuestion, ...]]:
        return {
            "history": (S.SetupQuestion("history", "Path to the query log (.jsonl, .jsonl.gz, "
                                                   "or a .json list of SQL statements)",
                                        kind="path"),),
            "bi": (S.SetupQuestion("bi", "Path to the BI definitions (a folder or a file)",
                                   kind="path"),
                   S.SetupQuestion("bi_vendor", "Which BI tool wrote those files?",
                                   kind="choice", choices=_vendors())),
            "sql_check": (S.SetupQuestion("database", "Path to the DuckDB database the SQL is "
                                          "checked against", kind="path"),),
            "schema": (S.SetupQuestion("schema", "Path to a JSON file mapping DB.SCHEMA.TABLE "
                                       "to its columns (name, datatype, description)",
                                       kind="path"),),
        }


def _paths(value: Any) -> list[pathlib.Path]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    return [pathlib.Path(str(v)).expanduser().resolve() for v in items]


# The common query-log format and the custody sidecar are the core's (`lib/querylog`): this
# plug, like every other one that hands over a log file, reads them through it.
from metrics_agent.lib import bi_capture as _bi_capture  # noqa: E402
from metrics_agent.lib.querylog import (  # noqa: E402,F401
    file_custody, read_log, shape_table_records, stamped_custody)


# The settings every DuckDB connection this plug opens is held to (see DuckDBRunner).
DUCKDB_LOCKDOWN = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "lock_configuration": True,
}


class DuckDBRunner:
    """A read-only DuckDB connection. The socket wraps it so a write never reaches it."""

    kind = "duckdb"

    def __init__(self, database: str, dialect: str = "duckdb") -> None:
        self.database = database
        self.dialect = dialect
        from metrics_agent.lib import engine as _engine

        def connect():
            import duckdb  # noqa: PLC0415

            # READ-ONLY IS NOT ENOUGH: a pure SELECT can still reach out. With DuckDB's
            # defaults `read_csv('http://…')` or `read_parquet('/etc/…')` passes the read-only
            # gate and opens a socket or a file the run never named, and an extension is
            # fetched over the network on first use. The plug opens no network connection, so
            # the database is the only thing it may read; the lock stops a statement turning
            # any of this back on.
            return duckdb.connect(database, read_only=True, config=DUCKDB_LOCKDOWN)

        # One `information_schema` spans every attached database: not per database.
        self._engine = _engine.Engine("duckdb", database, dialect, connector=connect)

    def connect(self) -> None:
        self._engine.connect()

    def explain(self, sql: str) -> int:
        return self._engine.explain(sql)

    def fetch(self, sql: str, limit: int):
        return self._engine.fetch(sql, limit)

    def scan(self, sql: str, batch: int) -> Iterator[list[tuple]]:
        return self._engine.scan(sql, batch)

    def columns_of(self, table: str) -> list[str]:
        return self._engine.columns_of(table)

    def columns_of_many(self, tables: list[str]) -> dict[str, list[str]]:
        return self._engine.columns_of_many(tables)

    def close(self) -> None:
        self._engine.close()

    def __enter__(self) -> "DuckDBRunner":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class LocalFiles:
    name = "local-files"
    capabilities = ("query_history", "table_schema", "bi_definitions", "sql_runner")
    # BI files any installed reader reads: which one is the operator's `bi_vendor` setting.
    bi_vendor = None
    default_dialect = "duckdb"
    setup_questions = _SetupQuestions()
    kind = "input"
    custody = "customer"
    requires_env: tuple[str, ...] = ()       # METRICS_AGENT_WAREHOUSE_DSN is read only as a fallback

    @classmethod
    def validate_settings(cls, role: str, settings: dict[str, Any]) -> str | None:
        """History is a log file OR a shape table (with its database); the rest as asked."""
        def need(key: str) -> str:
            return f"plug 'local-files' needs settings.local-files.{key}"
        if role == "history" and not (settings.get("history") or settings.get("shape_table")):
            return need("history") + " (or settings.local-files.shape_table)"
        if role == "history" and settings.get("shape_table") and not settings.get("history") \
                and not (settings.get("database") or os.environ.get("METRICS_AGENT_WAREHOUSE_DSN")):
            # The record would say the history came out of a warehouse this plug cannot reach.
            return (need("database") + " to read its shape_table through: the history comes "
                    "out of that database")
        if role == "bi":
            if not settings.get("bi"):
                return need("bi")
            if settings.get("bi_vendor") not in _vendors():
                return need("bi_vendor") + f" ({' or '.join(_vendors())})"
        if role == "schema" and not settings.get("schema"):
            return need("schema")
        return None

    def __init__(self, settings: dict[str, Any] | None = None, *, dialect: str = "duckdb") -> None:
        self.settings = dict(settings or {})
        self.dialect = dialect
        # Instance custody: the most restrictive of the files it will read.
        files = _paths(self.settings.get("history")) + _paths(self.settings.get("schema"))
        if any(file_custody(f) == S.THIRD_PARTY for f in files if f.exists()):
            self.custody = S.THIRD_PARTY
        # A browser capture records who held it on its way to the folder.
        for root in _paths(self.settings.get("bi")):
            if (_bi_capture.find(root) is not None
                    and stamped_custody(root / _bi_capture.MANIFEST) == S.THIRD_PARTY):
                self.custody = S.THIRD_PARTY

    # -- checks --------------------------------------------------------------------------
    def check(self, settings: dict[str, Any]) -> S.CheckResult:
        """Every named file exists, and each one reads: the first record of each log parses,
        the schema file is the expected JSON, the BI setting holds definition files, and the
        database opens read-only and answers `SELECT 1`."""
        def fail(key: str, msg: str) -> S.CheckResult:
            return S.CheckResult(False, msg, fix=f"settings.local-files.{key}")

        for key in ("history", "schema", "bi", "database"):
            for p in _paths(settings.get(key)):
                if not p.exists():
                    return fail(key, f"{key}: {p} does not exist")
        for p in _paths(settings.get("history")):
            try:
                next(read_log(p))
            except StopIteration:
                return fail("history", f"history: no line of {p.name} is a query record "
                                       "(one JSON object per line, with the statement text)")
            except (OSError, EOFError, UnicodeDecodeError) as exc:
                return fail("history", f"history: {p.name} cannot be read "
                                       f"({type(exc).__name__})")
        for p in _paths(settings.get("schema"))[:1]:
            try:
                doc = json.loads(p.read_text())
            except (OSError, ValueError) as exc:
                return fail("schema", f"schema: {p.name} is not JSON ({type(exc).__name__})")
            if not isinstance(doc, dict):
                return fail("schema", f"schema: {p.name} must map DB.SCHEMA.TABLE to columns")
            try:
                LocalFiles({"schema": str(p)}).table_schema([], max_description_chars=1)
            except ValueError as exc:
                return fail("schema", f"schema: {exc}")
        if settings.get("bi"):
            for p in _paths(settings.get("bi")):
                if _bi_capture.find(p) is None:
                    continue
                try:
                    doc = _bi_capture.load(p)
                except _bi_capture.CaptureError as exc:
                    return fail("bi", f"bi: {exc}")
                if settings.get("bi_vendor") and doc["tool"] != settings["bi_vendor"]:
                    return fail("bi_vendor", f"bi_vendor is {settings['bi_vendor']!r} and the "
                                f"capture at {p.name} was made for {doc['tool']!r}")
            if not LocalFiles(settings).bi_definitions([], pathlib.Path(".")):
                return fail("bi", "bi: no BI definition file any installed reader reads "
                                  "was found there")
            if settings.get("bi_vendor") not in _vendors():
                return fail("bi_vendor", f"bi_vendor must be {' or '.join(_vendors())}")
        if settings.get("database"):
            try:
                from metrics_agent.socket_gates import ReadOnlyRunner  # noqa: PLC0415

                with ReadOnlyRunner(DuckDBRunner(str(_paths(settings["database"])[0])),
                                    "duckdb") as ro:
                    ro.fetch("SELECT 1", 1)
            except Exception as exc:  # noqa: BLE001 - the check reports, it never raises
                return fail("database", f"database: could not open it read-only "
                                        f"({type(exc).__name__})")
        return S.CheckResult(True, "every named file exists and reads")

    # -- history -------------------------------------------------------------------------
    def query_history(self, tables: list[str], window: S.Window) -> Iterator[S.QueryRecord]:
        """Every record in the named logs, in file order. Admission (bounded tables, window,
        status) is the core's job, so nothing is filtered here."""
        logs = _paths(self.settings.get("history"))
        if logs:
            for path in logs:
                yield from read_log(path)
            return
        if self.settings.get("shape_table"):
            yield from self._shape_table()
            return
        raise S.NotSupported("local-files: no `history` file or `shape_table` is set")

    def _shape_table(self) -> Iterator[S.QueryRecord]:
        runner = self.sql_runner()
        if runner is None:
            raise S.NotSupported("local-files: a shape table needs a `database`")
        yield from shape_table_records(runner, self.dialect, self.settings["shape_table"],
                                       self.settings.get("columns"))

    # -- schema --------------------------------------------------------------------------
    def table_schema(self, tables: list[str], *,
                     max_description_chars: int) -> S.SchemaResult:
        paths = _paths(self.settings.get("schema"))
        if not paths:
            raise S.NotSupported("local-files: no `schema` file is set")
        path = paths[0]
        raw = path.read_bytes()
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            raise ValueError(f"local-files: {path.name} must map DB.SCHEMA.TABLE to a list of "
                             "columns")
        wanted = {t.upper() for t in tables}
        columns: dict[str, list[S.Column]] = {}
        for table, cols in doc.items():
            if wanted and table.upper() not in wanted:
                continue
            if not isinstance(cols, list):
                raise ValueError(f"local-files: {path.name}: {table} must hold a list of "
                                 "columns")
            for i, c in enumerate(cols):
                # A column with no name is not evidence of anything; it is refused by
                # position, never by a bare KeyError.
                if not (isinstance(c, dict) and isinstance(c.get("name"), str)
                        and c["name"].strip()):
                    raise ValueError(f"local-files: {path.name}: column {i} of {table} has "
                                     "no `name`")
            columns[table.upper()] = [
                S.Column(c["name"], c.get("datatype"),
                         (c.get("description") or None) and
                         str(c["description"])[:max_description_chars])
                for c in cols]
        return S.SchemaResult(
            columns=columns,
            note=f"read {len(columns)} of {len(wanted) or len(doc)} table(s) from {path.name}",
            manifest_record={"source": str(path), "sha256": hashlib.sha256(raw).hexdigest()})

    # -- BI ------------------------------------------------------------------------------
    def bi_definitions(self, assets: list[str], dest: pathlib.Path) -> list[pathlib.Path]:
        """The definition files under `bi`, in a stable order. Read in place: nothing is
        copied, so `dest` is unused."""
        roots = _paths(self.settings.get("bi"))
        if not roots:
            raise S.NotSupported("local-files: no `bi` folder or file is set")
        out: list[pathlib.Path] = []
        # The files the CHOSEN reader reads, when the vendor is set: a folder of one tool's
        # files never picks up another tool's (stray SQL beside a workbook). Unset, every
        # installed reader's.
        vendor = self.settings.get("bi_vendor")
        try:
            suffixes = (tuple(s.lower() for s in _readers.load(vendor).suffixes or ())
                        if vendor else _readers.suffixes())
        except _readers.ReaderError:
            suffixes = _readers.suffixes()
        for root in roots:
            if _bi_capture.find(root) is not None:
                # A capture: the files its manifest lists as definitions, hash-checked.
                out += _bi_capture.definition_files(root)
                continue
            if root.is_file():
                out.append(root)
            elif root.is_dir():
                out += sorted(p for p in root.rglob("*")
                              if p.is_file() and p.suffix.lower() in suffixes)
        return out

    # -- SQL check -----------------------------------------------------------------------
    def sql_runner(self) -> DuckDBRunner | None:
        database = self.settings.get("database") or os.environ.get("METRICS_AGENT_WAREHOUSE_DSN")
        return DuckDBRunner(str(database), self.dialect) if database else None

    def resolve_scope(self, refs: list[str]) -> S.Scope:
        raise S.NotSupported("local-files does not resolve asset names; list the tables")
