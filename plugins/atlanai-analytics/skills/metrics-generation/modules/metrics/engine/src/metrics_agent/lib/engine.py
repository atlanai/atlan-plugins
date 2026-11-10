# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A read-only cursor over one warehouse: the code every SQL-check plug shares.

The core opens no warehouse itself (AGENT_GUARDRAILS rule 22). Every warehouse is opened by its
plug, which passes a `connector` (a factory for a DB-API-style connection) and says whether its
`information_schema` is per database; everything else here, the read-only check, the bounded
wrapping and the catalogue reads, is the same code for all of them.

Every warehouse read goes through here. Two locks, both non-negotiable:
  1. the configured role is read-only (an operational requirement, stated in .env.template);
  2. `sqlshape.has_dml` refuses anything that is not a pure read BEFORE transport.

Rule 12: credentials come from env with no defaults, and a token value is never printed.
"""
from __future__ import annotations

from typing import Any

from metrics_agent import socket_gates as _gates
from metrics_agent import sockets as _sockets

from . import sqlshape

class EngineError(RuntimeError):
    """Raised on a missing backend, a missing credential, or a refused statement."""


class ReadOnlyViolation(EngineError, _gates.ReadOnlyViolation):
    """Raised when a statement is not a pure read. Never weakened. Also a socket
    `ReadOnlyViolation`, so a caller handles both locks the same way."""


def _takes_dialect(factory) -> bool:
    """Whether a connector factory NAMES a `dialect` parameter. A bare `**kwargs` does not
    count: a factory that forwards its keywords to a vendor driver would hand it one the
    driver does not know."""
    import inspect

    try:
        params = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "dialect" and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
               for p in params)


class Engine:
    """A read-only cursor over one warehouse."""

    def __init__(self, kind: str, dsn: str, dialect: str, *, connector=None,
                 catalog_per_database: bool = False, access_blocker=None) -> None:
        self.kind = kind
        self.dialect = dialect
        self._dsn = dsn
        self._conn: Any = None
        # Whether `information_schema` lives in each database (asked as `<db>.information_
        # schema`) or spans every attached one (filtered on `table_catalog`). A fact about
        # the warehouse, so its plug declares it.
        self.catalog_per_database = catalog_per_database
        # A plug that brings its own connection (Atlan's query path) passes a factory for a
        # DB-API-like object; everything else here (read-only check, wrapping, catalogue reads)
        # applies to it unchanged. A factory that takes a `dialect` keyword is given the run's
        # declared dialect, so a connection that inspects SQL itself reads it in the same
        # dialect this engine's gate does; a factory that takes no arguments is called bare.
        self._connector = connector
        # AN ACCESS LIMIT IS NOT A FAILED STATEMENT. A plug that can tell its warehouse's
        # refusals apart passes `access_blocker(exc) -> blocker | None` (a suspended or
        # over-quota warehouse, a table with no SELECT grant, a database it cannot see), and
        # the refusal is raised as `sockets.AccessBlocked`: the witness records the statement
        # as unchecked for that named reason instead of as a failure of the SQL. Any other
        # error is raised as it was.
        self._access_blocker = access_blocker

    def _execute(self, cur: Any, sql: str) -> None:
        try:
            cur.execute(sql)
        except Exception as exc:
            self._raise_blocked(exc)
            raise

    def _raise_blocked(self, exc: BaseException) -> None:
        if self._access_blocker is None:
            return
        try:
            found = self._access_blocker(exc)
        except Exception:  # noqa: BLE001 - a classifier that fails classifies nothing
            found = None
        if found:
            raise _sockets.AccessBlocked([found]) from None

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        if self._conn is not None:
            return
        if self._connector is not None:
            try:
                self._conn = (self._connector(dialect=self.dialect)
                              if _takes_dialect(self._connector) else self._connector())
            except Exception as exc:
                self._raise_blocked(exc)
                raise
        else:
            raise EngineError(f"warehouse {self.kind!r} has no connector: a warehouse is opened "
                              "by its plug, which passes one; the core opens none")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "Engine":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the only read path -------------------------------------------------
    def fetch(self, sql: str, limit: int) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Execute a bounded read. Wrapping is done with a real subquery, never by string
        surgery, and the statement is never single-line-flattened (rule 11)."""
        if sqlshape.has_dml(sql, self.dialect):
            raise ReadOnlyViolation(
                "statement is not a pure read — DML/DDL is refused before transport. "
                "This gate is never weakened."
            )
        self.connect()
        # A TERMINATED STATEMENT CANNOT BE WRAPPED. `SELECT * FROM ( ... ; ) AS t` is a
        # syntax error, and a warehouse that allows multiple statements refuses the call as
        # "multiple SQL statements in a single API call". Query logs are full of terminated
        # statements — 113 of this estate's verified queries end in one — so without this the
        # failure looks like a defect in the analyst's SQL rather than in our wrapper.
        wrapped = f"SELECT * FROM (\n{sql.rstrip().rstrip(';').rstrip()}\n) AS _vqa_witness LIMIT {int(limit)}"
        cur = self._conn.cursor() if hasattr(self._conn, "cursor") else self._conn
        self._execute(cur, wrapped)
        rows = list(cur.fetchall())
        desc = getattr(cur, "description", None)
        cols = [d[0] for d in desc] if desc else []
        return cols, rows

    def explain(self, sql: str) -> int:
        """Compile the statement without running it. -> number of plan rows.

        NOT routed through `fetch`, which wraps its argument in a bounded subquery. That
        wrapping is right for `execute` and fatal here: `SELECT * FROM (EXPLAIN ...)` is not
        valid SQL on any engine, so `witness_mode: explain` could never have succeeded
        against a real warehouse — it failed with a syntax error that named EXPLAIN rather
        than the wrapper, which reads like the statement's fault instead of ours.

        The read-only gate still applies: EXPLAIN of a DML statement is refused like any
        other, because the point is to compile a read, not to describe a write.
        """
        if sqlshape.has_dml(sql, self.dialect):
            raise ReadOnlyViolation(
                "statement is not a pure read — DML/DDL is refused before transport. "
                "This gate is never weakened."
            )
        self.connect()
        cur = self._conn.cursor() if hasattr(self._conn, "cursor") else self._conn
        self._execute(cur, f"EXPLAIN {sql}")
        return len(list(cur.fetchall()))

    def scan(self, sql: str, batch: int):
        """Every row of a read, in batches. The caller (the socket's `ReadOnlyRunner`) has
        already checked that it is a read; it is checked here again, like every path in."""
        if sqlshape.has_dml(sql, self.dialect):
            raise ReadOnlyViolation(
                "statement is not a pure read — DML/DDL is refused before transport. "
                "This gate is never weakened."
            )
        self.connect()
        cur = self._conn.cursor() if hasattr(self._conn, "cursor") else self._conn
        self._execute(cur, sql)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                return
            yield list(rows)

    def columns_of_many(self, tables: list[str]) -> dict[str, list[str]]:
        """Column lists for MANY tables, one round trip per database instead of per table.

        `columns_of` asks `information_schema` once per table. That is fine for a handful and
        quadratic-feeling for a real estate: this run's gates needed 215 tables and spent tens
        of minutes sleeping on 215 serial round trips, each returning a few rows. The catalogue
        is one table — ask it once per database with an `IN` list and group the answer.

        Returns only what the warehouse actually answered. A table missing from the result is
        ABSENT EVIDENCE, exactly as a failed single read is, and the caller must keep treating
        it that way — the speed-up must not quietly become "no columns, so nothing is wrong".
        """
        self.connect()
        per_database = self.catalog_per_database
        groups: dict[str, list[str]] = {}
        # A NAME THAT IS NOT PLAIN IS NEVER PUT INTO THE QUERY, here as at the socket: it stays
        # absent evidence. A plain part is a string literal below (quotes doubled), and the
        # database, the one part that stands as an identifier, is quoted for the dialect when
        # it carries a hyphen (`quote_part`).
        for t in tables:
            if not _gates.plain_table_name(t):
                continue
            parts = t.split(".")
            db = parts[-3] if len(parts) >= 3 else ""
            groups.setdefault(db, []).append(t)
        out: dict[str, list[str]] = {}
        cur = self._conn.cursor() if hasattr(self._conn, "cursor") else self._conn
        for db, group in groups.items():
            keys = {}
            for t in group:
                parts = t.split(".")
                keys[(parts[-2].lower() if len(parts) >= 2 else "", parts[-1].lower())] = t
            pairs = ", ".join(
                "(" + "'" + sch.replace("'", "''") + "', '" + nm.replace("'", "''") + "')"
                for sch, nm in keys
            )
            catalog = f"{_gates.quote_part(db, self.dialect)}." if db and per_database else ""
            where = f"(lower(table_schema), lower(table_name)) IN ({pairs})"
            if db and not per_database:
                where += f" AND lower(table_catalog) = lower('{db.replace(chr(39), chr(39) * 2)}')"
            sql = (f"SELECT lower(table_schema), lower(table_name), column_name "
                   f"FROM {catalog}information_schema.columns WHERE {where} "
                   f"ORDER BY table_schema, table_name, ordinal_position")
            try:
                cur.execute(sql)
                rows = cur.fetchall()
            except Exception:
                continue          # absent evidence for this database, not a pass
            for sch, nm, col in rows:
                t = keys.get((str(sch).lower(), str(nm).lower()))
                if t:
                    out.setdefault(t, []).append(str(col).lower())
        return out

    def columns_of(self, table: str) -> list[str]:
        """Real column names for a fully-qualified table, for C4's column-grounding gate.

        On a warehouse whose `information_schema` is PER DATABASE (its plug says so), an
        unqualified read of it fails outright on a session with no current database — and on a
        session that HAS one, it silently answers about the wrong database. The three-part name
        we were given already says which database to ask; use it.

        WHICH IS NOT TRUE EVERYWHERE, and qualifying regardless breaks the engines where it is
        not. An engine with ONE `information_schema` spanning every attached database treats
        the first part of a three-part name as a catalog only if something of that name is
        attached — otherwise the read fails with "catalog does not exist" and the gate records
        absence of evidence for a table it could have read perfectly well. So the qualifier
        goes on only where the plug declares a per-database catalog, and `table_catalog`
        carries the same restriction everywhere else.
        """
        if not _gates.plain_table_name(table):
            raise ReadOnlyViolation(f"table name {table!r} is not a plain DB.SCHEMA.TABLE "
                                    "identifier; its columns are not looked up")
        self.connect()
        parts = [x.replace("'", "''") for x in table.split(".")]
        name = parts[-1]
        where = f"lower(table_name) = lower('{name}')"
        if len(parts) >= 2:
            where += f" AND lower(table_schema) = lower('{parts[-2]}')"
        per_database = self.catalog_per_database
        if len(parts) >= 3 and not per_database:
            where += f" AND lower(table_catalog) = lower('{parts[-3]}')"
        catalog = (f"{_gates.quote_part(parts[-3], self.dialect)}."
                   if len(parts) >= 3 and per_database else "")
        sql = (
            f"SELECT column_name FROM {catalog}information_schema.columns "
            f"WHERE {where} ORDER BY ordinal_position"
        )
        cur = self._conn.cursor() if hasattr(self._conn, "cursor") else self._conn
        self._execute(cur, sql)
        return [str(r[0]).lower() for r in cur.fetchall()]


# Connector arguments that are NOT strings. A DSN is text, so anything the driver wants as
# bytes has to be converted here or the connection fails at handshake with an error that
# names neither the key nor the cause.
BYTES_ARGS = ("private_key_file_pwd", "private_key")


def parse_kv(dsn: str) -> dict[str, object]:
    """`k=v;k=v` connection settings, as a plug passes them to its vendor's `connect`."""
    out: dict[str, object] = {}
    for part in dsn.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            out[k] = v.encode() if k in BYTES_ARGS and v else v
    return out
