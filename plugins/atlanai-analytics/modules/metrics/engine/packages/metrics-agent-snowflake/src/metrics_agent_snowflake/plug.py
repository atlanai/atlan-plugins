# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The `snowflake-direct` input plug: a Snowflake account, read on the customer's own grant.

Custody `customer`: nothing passes through a third party's store.

* **SQL check**: a read-only connection (the socket refuses any write before it is sent).
* **schema**: column names, types and comments from each database's INFORMATION_SCHEMA.
* **history**: bounded native Account Usage history, or an existing shape table. Native
  executions are aggregated by exact SQL and session database/schema, without exporting users.
* **BI definitions**: semantic views (`semantic_views`, each `DB.SCHEMA.VIEW`), read as their
  DDL by one `SELECT GET_DDL('SEMANTIC_VIEW', '<view>')` each, through the same read-only
  connection, and written as `<view>.sql`. Or a semantic view file already on disk (`bi`),
  read in place. This package's semantic-view reader parses them (`semantic_view.py`,
  registered as the `snowflake-semantic-view` BI reader): this plug fetches text and
  interprets none of it. A fetch of several views is one folder; the reader reads it as one
  model per view.

The connection settings come from `METRICS_AGENT_WAREHOUSE_DSN` as `k=v;k=v` (account, user, role,
warehouse, authenticator, private_key_file, …), passed to `snowflake.connector.connect`. The
role should be read-only; the socket's check is the second lock, not the first. `catalog` in
run.yml is the session's database, so a statement that names `SCHEMA.TABLE` resolves; a
`database=` in the DSN wins over it. `check_sql_session` (run by `metrics-agent setup --check`
for a SQL check) fails, naming both, when the session has no current database.
"""
from __future__ import annotations

import os
import pathlib
import re
from typing import Any, Iterator

from metrics_agent import sockets as S
from metrics_agent.lib.querylog import shape_table_records

# Keep native history separate from the three-column shape-table contract.
_ACCOUNT_USAGE = re.compile(r'^"?SNOWFLAKE"?\."?ACCOUNT_USAGE"?\."?QUERY_HISTORY"?$',
                            re.IGNORECASE)


# SNOWFLAKE'S ACCESS REFUSALS, by its own error numbers, as the run's access codes
# (`lib/access`). Recorded live on the test account: 90073 (22000) "Warehouse '<wh>' cannot be
# resumed because resource monitor '<rm>' has exceeded its quota"; 2003 (42S02) "Object
# '<db.schema.table>' does not exist or not authorized"; 2003 (02000) "Database '<db>' does not
# exist or not authorized". From Snowflake's documentation: 606 (57P03) no active warehouse;
# 3001 (42501) insufficient privileges; 390100/390144/250001 a refused sign-in. Recorded live
# too: 390189 "Role '<role>' specified in the connect string does not exist or not authorized"
# (a role not granted to the user), answered at connect. Snowflake
# answers a table that does not exist and one the role may not see with the same 2003, so
# the blocker asks for both: the grant, or the right name.
_QUOTED = re.compile(r"'([^']{1,255})'")
_DATABASE = re.compile(r"(?:^|\s)Database '", re.IGNORECASE)
_WAREHOUSE_ERRNOS = {90073, 606}
_SIGN_IN_ERRNOS = {390100, 390144, 250001, 390318, 390189}


def connection_options(dsn):
    """Convert declared connector options at the vendor boundary, never in the core."""
    from metrics_agent.lib.engine import parse_kv
    from snowflake.connector.connection import DEFAULT_CONFIGURATION

    options = parse_kv(dsn)
    for name, value in list(options.items()):
        if name not in DEFAULT_CONFIGURATION or not isinstance(value, str):
            continue
        types = DEFAULT_CONFIGURATION[name][1]
        types = types if isinstance(types, tuple) else (types,)
        if bool in types and str not in types:
            if value.lower() not in ('true', 'false'):
                raise ValueError(f"Snowflake option {name} must be true or false")
            options[name] = value.lower() == 'true'
        elif int in types and str not in types:
            if not re.fullmatch(r'[0-9]+', value):
                raise ValueError(f"Snowflake option {name} must be a nonnegative integer")
            options[name] = int(value)
    return options


def access_blocker(exc: BaseException) -> dict[str, Any] | None:
    """The access-report blocker for one of Snowflake's access refusals; None for any other
    error (a wrong column, a syntax error), which stays a failed statement."""
    from metrics_agent.lib import access as _access  # noqa: PLC0415

    errno = getattr(exc, "errno", None)
    msg = str(exc)
    low = msg.lower()
    names = _QUOTED.findall(msg)
    # The error number and the first sentence: what the operator forwards to the admin.
    detail = " ".join(x.strip() for x in msg.strip().splitlines()[:2]) or type(exc).__name__

    def make(code: str, kind: str, name: str | None, not_read: str, ask: str):
        return _access.blocker(code, source=SnowflakeDirect.name, stage="sql_check",
                               item={"kind": kind, "name": name}, not_read=not_read,
                               ask=ask, detail=detail)

    if errno in _WAREHOUSE_ERRNOS or ("resource monitor" in low and "quota" in low):
        wh = names[0] if names and errno != 606 else None
        why = ("its resource monitor has used its credit quota" if "quota" in low
               else "the session has no usable warehouse")
        return make("warehouse_unavailable", "warehouse", wh,
                    f"every statement's check against the warehouse ({why})",
                    "resume the warehouse: raise or reset its resource monitor's quota, or "
                    "grant the verifying role USAGE on a warehouse and name it in "
                    "METRICS_AGENT_WAREHOUSE_DSN (warehouse=)")
    if errno in _SIGN_IN_ERRNOS:
        role = names[0] if errno == 390189 and names else None
        return make("warehouse_sign_in_failed", "role" if role else "account", role,
                    "every statement's check against the warehouse (the sign-in was refused)",
                    ("GRANT ROLE " + role + " to the verifying user, or name a role it holds "
                     "in METRICS_AGENT_WAREHOUSE_DSN (role=)") if role else
                    "a working sign-in for the verifying user in METRICS_AGENT_WAREHOUSE_DSN "
                    "(user, role, key or token)")
    if errno == 3001 or getattr(exc, "sqlstate", None) == "42501":
        return make("no_select_grant", "table", names[0] if names else None,
                    "the check of every statement over this table (no SELECT privilege)",
                    "GRANT SELECT on the table to the verifying role")
    if errno == 2003 and "does not exist or not authorized" in low:
        if _DATABASE.search(msg):
            return make("database_not_visible", "database", names[0] if names else None,
                        "the check of every statement over this database (the verifying "
                        "role cannot see it)",
                        "GRANT USAGE on the database (and its schema) to the verifying role, "
                        "or confirm the database name the BI connection uses")
        return make("object_not_visible", "table", names[0] if names else None,
                    "the check of every statement over this table (it does not exist, or the "
                    "verifying role may not see it)",
                    "GRANT SELECT on the table to the verifying role (and USAGE on its "
                    "schema), or confirm the table name the BI definition uses")
    return None


class SnowflakeDirect:
    name = "snowflake-direct"
    capabilities = ("query_history", "table_schema", "bi_definitions", "sql_runner")
    default_dialect = "snowflake"
    # A semantic view is a BI model (a governed one): this package's reader reads its DDL or
    # YAML.
    bi_vendor = "snowflake-semantic-view"
    # With `semantic_views` and no `bi`, the run fetches their DDL itself (`s_fetch_bi`, the
    # first step of `metrics-agent run`) into the run directory, so S0 never reaches out.
    fetch_settings = ("semantic_views",)
    setup_questions = {
        "history": (S.SetupQuestion("shape_table", "Existing shape table, DB.SCHEMA.TABLE "
                                       "(blank: read native Account Usage history)", required=False),
                    S.SetupQuestion("columns", "Its columns, in order: the statement, its "
                                    "execution count, its user count[, a query id] (blank: "
                                    "sql,exec,users)", required=False, kind="list"),),
        "sql_check": (S.SetupQuestion("catalog", "The session's database: a statement naming "
                                      "SCHEMA.TABLE resolves against it, and Snowflake cannot "
                                      "check one without it (blank only when the DSN sets "
                                      "database=)", required=False),),
        "bi": (S.SetupQuestion("semantic_views", "Semantic views to read, DB.SCHEMA.VIEW "
                               "(comma-separated; blank: a file on disk)", required=False,
                               kind="list"),
               S.SetupQuestion("bi", "Path to a semantic view's DDL or YAML on disk (blank when "
                               "the run reads the views)", required=False, kind="path")),
    }
    kind = "input"
    custody = "customer"
    requires_env: tuple[str, ...] = ("METRICS_AGENT_WAREHOUSE_DSN",)

    # Snowflake's `information_schema` lives in each database (`lib/engine.Engine`).
    catalog_per_database = True

    def __init__(self, settings: dict[str, Any] | None = None, *, dialect: str = "snowflake"):
        self.settings = dict(settings or {})
        self.dialect = dialect or "snowflake"

    def _dsn(self) -> str:
        dsn = os.environ.get("METRICS_AGENT_WAREHOUSE_DSN") or ""
        if not dsn:
            from metrics_agent.lib.engine import EngineError  # noqa: PLC0415

            raise EngineError("METRICS_AGENT_WAREHOUSE_DSN is unset — there is deliberately no default.")
        return dsn

    def check(self, settings: dict[str, Any]) -> S.CheckResult:
        """Connects read-only, runs `SELECT 1`, and reads the columns of the first bounded
        table (`tables`) or of the shape table, when one is named."""
        if not os.environ.get("METRICS_AGENT_WAREHOUSE_DSN"):
            return S.env_refusal(self.name, ("METRICS_AGENT_WAREHOUSE_DSN",))
        probe = (settings.get("tables") or [None])[0] or settings.get("shape_table")
        try:
            from metrics_agent.socket_gates import ReadOnlyRunner  # noqa: PLC0415

            with ReadOnlyRunner(SnowflakeDirect(settings, dialect=self.dialect).sql_runner(),
                                self.dialect) as ro:
                ro.fetch("SELECT 1", 1)
                if probe and not ro.columns_of_many([probe]).get(probe):
                    return S.CheckResult(False, f"connected, but {probe} has no columns this "
                                                "role can see",
                                         fix="the role in METRICS_AGENT_WAREHOUSE_DSN, or the table name")
        except Exception as exc:  # noqa: BLE001 - the check reports, it never raises
            return S.CheckResult(False, f"could not connect: {type(exc).__name__}",
                                 fix="METRICS_AGENT_WAREHOUSE_DSN (account, user, role, warehouse)")
        return S.CheckResult(True, "connected" + (f" and read {probe}" if probe else ""))

    def check_sql_session(self, settings: dict[str, Any]) -> S.CheckResult:
        """As the SQL check: the session has a current database, so a logged statement that
        names `SCHEMA.TABLE` resolves. Without one Snowflake refuses to EXPLAIN it ("this
        session does not have a current database"), and most of a run's checks fail for that
        reason alone. `catalog` (or `database=` in the DSN) sets it."""
        if not os.environ.get("METRICS_AGENT_WAREHOUSE_DSN"):
            return S.env_refusal(self.name, ("METRICS_AGENT_WAREHOUSE_DSN",))
        try:
            from metrics_agent.socket_gates import ReadOnlyRunner  # noqa: PLC0415

            with ReadOnlyRunner(SnowflakeDirect(settings, dialect=self.dialect).sql_runner(),
                                self.dialect) as ro:
                _cols, rows = ro.fetch("SELECT CURRENT_DATABASE()", 1)
        except Exception as exc:  # noqa: BLE001 - the check reports, it never raises
            return S.CheckResult(False, f"could not ask the session for its database: "
                                        f"{type(exc).__name__}",
                                 fix="METRICS_AGENT_WAREHOUSE_DSN (account, user, role, warehouse)")
        current = rows[0][0] if rows and rows[0] else None
        if not current:
            return S.CheckResult(
                False, "connected, but the session has no current database, so a statement "
                       "that names SCHEMA.TABLE cannot be checked (Snowflake refuses to EXPLAIN "
                       "it)",
                fix=f"settings.{self.name}.catalog (the database statements resolve against), "
                    "or database=<DB> in METRICS_AGENT_WAREHOUSE_DSN")
        return S.CheckResult(True, f"SCHEMA.TABLE resolves against the database {current}")

    def sql_runner(self):
        from metrics_agent.lib import engine as _engine  # noqa: PLC0415

        dsn = self._dsn()
        catalog = str(self.settings.get("catalog") or "").strip()

        def connect():
            import snowflake.connector as sf  # noqa: PLC0415 - only when a run uses it

            kwargs = connection_options(dsn)
            # THE DATABASE TO CHECK AGAINST. A logged statement often names `SCHEMA.TABLE`,
            # relying on the session's database, and Snowflake refuses to EXPLAIN it on a
            # session with none. Offered by the setup interview and, until it was passed here,
            # read by nothing.
            if catalog:
                kwargs.setdefault("database", catalog)
            return sf.connect(**kwargs)

        return _engine.Engine("snowflake", dsn, self.dialect, connector=connect,
                              catalog_per_database=self.catalog_per_database,
                              access_blocker=access_blocker)

    @classmethod
    def validate_settings(cls, role: str, settings: dict[str, Any]) -> str | None:
        if role != "history":
            return None
        from .history import source

        try:
            source(settings)
            table = settings.get("shape_table")
            if table and _ACCOUNT_USAGE.match(str(table).strip()):
                return ("Use history_source: account_usage and omit shape_table to read "
                        "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY directly")
        except ValueError as exc:
            return str(exc)
        return None

    def query_history(self, tables: list[str], window: S.Window) -> Iterator[S.QueryRecord]:
        from .history import records, source

        problem = self.validate_settings("history", self.settings)
        if problem:
            raise ValueError(problem)
        if source(self.settings) == "account_usage":
            return records(self.sql_runner(), tables, window)
        return shape_table_records(self.sql_runner(), self.dialect, self.settings["shape_table"],
                                   self.settings.get("columns"))

    def table_schema(self, tables: list[str], *, max_description_chars: int) -> S.SchemaResult:
        """One INFORMATION_SCHEMA read per database. Identifiers are checked before they are
        put in the statement, and the statement still passes the read-only check."""
        from metrics_agent.socket_gates import (  # noqa: PLC0415
            ReadOnlyRunner, plain_table_name, quote_part)

        by_db: dict[str, list[tuple[str, str]]] = {}
        for t in tables:
            parts = t.split(".")
            # The socket's own rule: plain parts only (a single hyphen between characters is
            # allowed, and quoted where the database stands as an identifier); a quote, `;`,
            # whitespace, a comment or a control character is refused, never escaped.
            if len(parts) != 3 or not plain_table_name(t):
                raise ValueError(f"not a DB.SCHEMA.TABLE identifier: {t!r}")
            by_db.setdefault(parts[0].upper(), []).append((parts[1].upper(), parts[2].upper()))
        columns: dict[str, list[S.Column]] = {}
        with ReadOnlyRunner(self.sql_runner(), self.dialect) as ro:
            for db, pairs in sorted(by_db.items()):
                wanted = " OR ".join(f"(table_schema = '{s}' AND table_name = '{n}')"
                                     for s, n in sorted(set(pairs)))
                sql = (f"SELECT table_schema, table_name, column_name, data_type, comment "
                       f"FROM {quote_part(db, self.dialect)}.information_schema.columns "
                       f"WHERE {wanted} "
                       f"ORDER BY table_schema, table_name, ordinal_position")
                for rows in ro.scan(sql, 5000):
                    for sch, tab, col, dtype, comment in rows:
                        columns.setdefault(f"{db}.{sch}.{tab}".upper(), []).append(S.Column(
                            col, dtype, (str(comment)[:max_description_chars]
                                         if comment else None)))
        return S.SchemaResult(columns, f"read {len(columns)} of {len(tables)} table(s) from "
                              "INFORMATION_SCHEMA", {"source": "snowflake-information-schema"})

    def bi_definitions(self, assets: list[str], dest: pathlib.Path) -> list[pathlib.Path]:
        """Each semantic view's DDL, one `<VIEW>.sql` per view under `dest`; or the `bi` file
        (or folder of view files) read in place.

        One read-only statement per view, `SELECT GET_DDL('SEMANTIC_VIEW', '<view>')`. A name
        is checked as a plain `DB.SCHEMA.VIEW` identifier before it is put in the statement,
        and is never escaped: a quote, `;`, whitespace or comment in it refuses. The statement
        then passes the socket's read-only check like every other."""
        from metrics_agent.socket_gates import ReadOnlyRunner, plain_table_name  # noqa: PLC0415

        if self.settings.get("bi"):
            root = pathlib.Path(str(self.settings["bi"])).expanduser()
            if root.is_dir():
                return sorted(p for p in root.rglob("*")
                              if p.is_file() and p.suffix.lower() in (".sql", ".yaml", ".yml"))
            return [root]
        views = [str(v).strip() for v in (assets or []) if str(v).strip()]
        if not views:
            raw = self.settings.get("semantic_views") or []
            views = [v.strip() for v in (raw.split(",") if isinstance(raw, str) else raw)
                     if str(v).strip()]
        if not views:
            raise S.NotSupported("snowflake-direct: set `semantic_views` (DB.SCHEMA.VIEW, ...) "
                                 "or `bi` (a semantic view file) to read BI definitions")
        bad = [v for v in views if len(v.split(".")) != 3 or not plain_table_name(v)]
        if bad:
            raise ValueError(f"not a DB.SCHEMA.VIEW identifier: {bad[0]!r}")
        dest = pathlib.Path(dest)
        out: list[pathlib.Path] = []
        with ReadOnlyRunner(self.sql_runner(), self.dialect) as ro:
            for view in views:
                _cols, rows = ro.fetch(f"SELECT GET_DDL('SEMANTIC_VIEW', '{view}')", 1)
                ddl = rows[0][0] if rows and rows[0] else None
                if not ddl:
                    raise RuntimeError(f"GET_DDL returned no definition for {view}")
                path = dest / f"{view.upper()}.sql"
                path.write_text(str(ddl), encoding="utf-8")
                out.append(path)
        return out

    def resolve_scope(self, refs: list[str]) -> S.Scope:
        raise S.NotSupported("snowflake-direct does not resolve asset names; list the tables")
