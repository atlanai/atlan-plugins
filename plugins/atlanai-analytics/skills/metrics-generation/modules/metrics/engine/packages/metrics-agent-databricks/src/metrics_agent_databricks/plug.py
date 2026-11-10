# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Databricks input over its SQL connector; every warehouse read passes the common gate."""
from __future__ import annotations

from metrics_agent import sockets as S
from metrics_agent.lib import access
from metrics_agent.socket_gates import ReadOnlyRunner, plain_table_name, quote_part
from . import auth


def access_blocker(exc):
    state = str(getattr(exc, "sqlstate", "") or getattr(exc, "sql_state", ""))
    # Connector error strings are classified locally and never copied into artifacts.
    text = str(exc).upper()
    if "UC_NOT_ENABLED" in text:
        code, ask = "database_not_visible", "Use Unity Catalog-enabled compute in a workspace assigned to the appropriate metastore; do not change grants automatically"
    elif state == "42501" or any(v in text for v in ("PERMISSION_DENIED", "INSUFFICIENT_PERMISSIONS")):
        code, ask = "no_select_grant", "Ask the warehouse administrator for read access to the selected source"
    elif state.startswith("28") or any(v in text for v in ("UNAUTHENTICATED", "INVALID_ACCESS_TOKEN")):
        code, ask = "warehouse_sign_in_failed", "Repeat the selected Databricks sign-in without sharing credentials"
    elif state in ("42P01", "42704") or "TABLE_OR_VIEW_NOT_FOUND" in text:
        code, ask = "object_not_visible", "Confirm the selected table exists and the role can read it"
    else:
        return None
    return access.blocker(code, source="databricks-direct", stage="sql_check",
                          not_read="the selected Databricks source", ask=ask)


class DatabricksDirect:
    name = "databricks-direct"
    kind = "input"
    custody = "customer"
    default_dialect = "databricks"
    capabilities = ("query_history", "table_schema", "sql_runner")
    requires_env = (auth.HOST, auth.PATH, auth.TOKEN, auth.DSN, auth.OWN_DSN,
                    "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET",
                    "DATABRICKS_HOST", "DATABRICKS_CONFIG_PROFILE", "DATABRICKS_AUTH_TYPE")
    catalog_per_database = True
    credential_routes = (
        S.CredentialRoute("browser OAuth", (auth.HOST, auth.PATH), note="A person at a real terminal; non-secret host/path may also be settings"),
        S.CredentialRoute("access token", (auth.HOST, auth.PATH, auth.TOKEN)),
        S.CredentialRoute("service-principal OAuth M2M", (auth.HOST, auth.PATH, "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET")),
        S.CredentialRoute("SDK unified authentication", (), note="Matching SDK profile or DATABRICKS_HOST; CLI, cloud and workload identities use their configured SDK provider"),
        S.CredentialRoute("existing connection DSN", (auth.DSN,)),
        S.CredentialRoute("Databricks connection DSN", (auth.OWN_DSN,)),
    )
    setup_questions = {
        "history": (S.SetupQuestion("workspace_id", "The selected Databricks workspace ID (from its URL)"),),
        "sql_check": (S.SetupQuestion("catalog", "Default catalog", required=False),
                      S.SetupQuestion("schema", "Default schema", required=False)),
    }

    def __init__(self, settings=None, *, dialect="databricks"):
        self.settings = dict(settings or {})
        self.dialect = dialect or "databricks"

    @classmethod
    def env_missing(cls, use=None):
        # Alternative credential routes and non-secret settings are checked together below.
        return ()

    @classmethod
    def validate_settings(cls, role, settings):
        if role == "history":
            from .history import workspace_id
            try:
                workspace_id(settings)
            except ValueError as exc:
                return str(exc)
        if settings.get("sign_in") not in (None, *auth.SIGN_INS):
            return "Databricks sign_in must be browser, azure-browser, token, oauth-m2m or unified"
        return None

    def check(self, settings):
        try:
            auth.connection_options(settings)
        except auth.AuthConfigurationError as exc:
            return S.CheckResult(False, str(exc), fix="Databricks connection metadata and sign-in")
        except Exception:
            return S.CheckResult(False, "Databricks connection configuration is invalid", fix="Databricks connection metadata and sign-in")
        try:
            with ReadOnlyRunner(type(self)(settings, dialect=self.dialect).sql_runner(), self.dialect) as ro:
                ro.fetch("SELECT 1", 1)
        except Exception as exc:
            return S.CheckResult(False, "Databricks connection refused: " + type(exc).__name__,
                                 fix="The selected sign-in and SQL warehouse CAN USE permission")
        return S.CheckResult(True, "connected read-only")

    def sql_runner(self):
        from metrics_agent.lib.engine import Engine

        def connect():
            from databricks import sql
            return sql.connect(**auth.connection_options(self.settings))

        # The core never needs the credential-bearing connection string.
        return Engine("databricks", "", self.dialect, connector=connect,
                      catalog_per_database=True, access_blocker=access_blocker)

    def query_history(self, tables, window):
        from .history import records
        return records(self.sql_runner(), tables, window, self.settings)

    def table_schema(self, tables, *, max_description_chars):
        by_catalog = {}
        for table in tables:
            if not isinstance(table, str) or len(table.split('.')) != 3 or not plain_table_name(table):
                raise ValueError("Databricks schema reads need CATALOG.SCHEMA.TABLE identifiers")
            catalog, schema, name = table.split('.')
            by_catalog.setdefault(catalog, []).append((schema, name))
        from sqlglot import exp
        def literal(value):
            return exp.Literal.string(value).sql(dialect="databricks")
        columns = {}
        with ReadOnlyRunner(self.sql_runner(), self.dialect) as ro:
            for catalog, pairs in sorted(by_catalog.items()):
                wanted = " OR ".join(f"(table_schema = {literal(schema)} AND table_name = {literal(name)})"
                                     for schema, name in sorted(set(pairs)))
                sql = ("SELECT table_schema, table_name, column_name, data_type, comment\n"
                       f"FROM {quote_part(catalog, self.dialect)}.information_schema.columns\n"
                       f"WHERE {wanted}\nORDER BY table_schema, table_name, ordinal_position")
                for rows in ro.scan(sql, 5000):
                    for schema, table, column, dtype, comment in rows:
                        columns.setdefault(f"{catalog}.{schema}.{table}".upper(), []).append(
                            S.Column(column, dtype, str(comment)[:max_description_chars] if comment else None))
        return S.SchemaResult(columns, f"read {len(columns)} selected table schemas",
                              {"source": "databricks-information-schema"})

    def bi_definitions(self, assets, dest):
        raise S.NotSupported("Use an installed BI reader or a supported local definition capture")

    def resolve_scope(self, refs):
        raise S.NotSupported("List bounded CATALOG.SCHEMA.TABLE names")
