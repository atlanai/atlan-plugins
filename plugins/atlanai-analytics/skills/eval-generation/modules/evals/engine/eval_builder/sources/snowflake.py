"""Mine schema and query history from Snowflake.

Two history sources, chosen by what the caller's role can actually reach — the
same fallback the doctor's `warehouse.query_history` check reports on:

  ACCOUNT_USAGE.QUERY_HISTORY      org-wide, 365 days, needs a grant
  INFORMATION_SCHEMA.QUERY_HISTORY caller's own, 7 days, always available

Verified against a live warehouse via browser SSO. Column names and shapes
below are confirmed, not provisional.
"""

from __future__ import annotations

from dataclasses import dataclass

from eval_builder.select import Candidate

# Only successful SELECTs by real users are candidates. The filtering that needs
# to parse SQL lives in select.py; this stage removes what SQL alone cannot see.
#
# Service accounts are excluded HERE, not just downstream. Ordering by recency
# and taking the top N spends the entire sample budget on whatever runs most
# often, and on a busy account that is service traffic by a wide margin: a
# recent pull can come back almost entirely service-issued, leaving next to no
# usable candidates. Excluding at the source is what makes the budget buy human queries.
#
# Matched by SHAPE, never by a list of account names. An earlier version named
# the service accounts of the warehouse it was written against, which did two
# bad things at once: it shipped one company's internal identifiers to everyone
# who installed the skill, and it silently did nothing on any other warehouse,
# whose service accounts are of course named differently. A filter that looks
# like it works and does not is worse than no filter.
#
# Anything site-specific belongs in --exclude-users, which is the caller's to
# supply and nobody else's to inherit.
_SERVICE_ACCOUNT_SQL_FILTER = """
  and not (
       user_name ilike 'svc[-_]%%'
    or user_name ilike '%%_svc'
    or user_name ilike '%%_service'
    or user_name ilike '%%_bot'
    or user_name ilike '%%_daemon'
    or user_name ilike '%%_analytics_user'
    or user_name ilike '%%backend%%'
    or user_name ilike '%%_etl'
    or user_name ilike '%%_sync'
    or user_name ilike '%%_job'
    or user_name = 'SYSTEM'
    -- Ingestion vendors, which issue under their own product name on every
    -- account that runs them. BI vendors are deliberately NOT here: a Looker
    -- or Tableau account issues the dashboard's SQL, which carries a business
    -- question and the metric definition behind it. Those are kept and
    -- labelled downstream, not excluded at the source.
    or user_name ilike 'fivetran%%'
    or user_name ilike 'segment%%'
    or user_name ilike 'airbyte%%'
    or user_name ilike 'stitch%%'
    or user_name ilike 'matillion%%'
    or user_name ilike 'hightouch%%'
    or user_name ilike 'census%%'
    %(extra)s
  )
"""

_ACCOUNT_USAGE_SQL = """
select
    query_id,
    query_text,
    user_name,
    warehouse_name,
    rows_produced,
    start_time,
    execution_status
from snowflake.account_usage.query_history
where start_time >= dateadd('day', -%(days)d, current_timestamp())
  and execution_status = 'SUCCESS'
  and query_type = 'SELECT'
  and query_text is not null
  and len(query_text) between 60 and 20000
""" + _SERVICE_ACCOUNT_SQL_FILTER + """
order by start_time desc
limit %(limit)d
"""

# INFORMATION_SCHEMA exposes a table function, not a view, and carries neither
# rows_produced nor a query_type column under the same names.
_INFORMATION_SCHEMA_SQL = """
select
    query_id,
    query_text,
    user_name,
    warehouse_name,
    rows_produced,
    start_time,
    execution_status
from table(information_schema.query_history(
    end_time_range_start => dateadd('day', -%(days)d, current_timestamp()),
    result_limit => %(limit)d
))
where execution_status = 'SUCCESS'
  and query_text ilike 'select%%'
order by start_time desc
"""


@dataclass
class MinedHistory:
    candidates: list[Candidate]
    source: str          # "account_usage" | "information_schema"
    days: int
    note: str = ""


def _extra_exclusions(users: list[str] | None) -> str:
    """Site-specific service accounts, as a SQL clause.

    Quoting is done here rather than by a bind parameter because the clause is
    interpolated into a `%`-formatted template; single quotes are doubled so a
    name can never close the literal and continue the statement.
    """
    names = [u.strip() for u in (users or []) if u and u.strip()]
    if not names:
        return ""
    quoted = ", ".join("'" + n.replace("'", "''") + "'" for n in names)
    return f"or upper(user_name) in ({quoted.upper()})"


def mine_query_history(conn, days: int = 90, limit: int = 2000,
                       exclude_users: list[str] | None = None) -> MinedHistory:
    """Pull candidate queries, preferring org-wide history when reachable."""
    params = {"days": days, "limit": limit,
              "extra": _extra_exclusions(exclude_users)}
    try:
        rows = _fetch(conn, _ACCOUNT_USAGE_SQL % params)
        source, note = "account_usage", ""
    except Exception as e:  # noqa: BLE001 - a denied grant is expected, not exceptional
        detail = str(e).splitlines()[0]
        # A missing grant is expected and handled. Anything else — a typo in the
        # SQL after an edit, a suspended warehouse, a network blip — would be
        # silently reported as a permissions problem while quietly narrowing the
        # window from 90 org-wide days to 7 of your own.
        looks_like_permissions = any(
            k in detail.lower() for k in
            ("privilege", "not authorized", "does not exist", "access", "denied")
        )
        if not looks_like_permissions:
            raise RuntimeError(
                f"ACCOUNT_USAGE query failed for a reason that is not a missing "
                f"grant: {detail[:160]}. Not falling back — the narrower source "
                "would hide this."
            ) from e
        rows = _fetch(conn, _INFORMATION_SCHEMA_SQL % {
            "days": min(days, 7), "limit": limit, "extra": params["extra"]})
        source = "information_schema"
        days = min(days, 7)
        note = (f"ACCOUNT_USAGE unavailable ({detail[:100]}); "
                f"fell back to your own queries over {days} days")

    return MinedHistory(_to_candidates(rows), source, days, note)


def _fetch(conn, sql: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _shape_key(sql: str) -> str:
    """Identity for "the same question asked again".

    Collapsing on exact text does not work: in a live run every single
    candidate came back with run_count=1, because
    analysts re-run a question with a different date literal or account id
    rather than byte-identical SQL. That made the `representative` signal
    uniformly useless.

    Normalising literals away groups those re-runs together, so run count and
    distinct users measure recurring demand rather than copy-paste exactness.
    """
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read="snowflake")
        for lit in tree.find_all(exp.Literal):
            lit.set("this", "?")
        return tree.sql(dialect="snowflake").lower()
    except Exception:  # noqa: BLE001 - fall back to the cruder identity
        return " ".join(sql.lower().split())


def _to_candidates(rows: list[dict]) -> list[Candidate]:
    """Collapse re-runs of the same question into one candidate.

    Run count and distinct users are the evidence that anyone actually asks
    this, so they have to be aggregated before scoring, not after.
    """
    by_text: dict[str, Candidate] = {}
    users: dict[str, set[str]] = {}

    for r in rows:
        text = (r.get("query_text") or "").strip()
        if not text:
            continue
        key = _shape_key(text)
        user = r.get("user_name") or ""
        started = r.get("start_time")
        started = started.isoformat() if hasattr(started, "isoformat") else str(started or "")

        if key in by_text:
            c = by_text[key]
            c.run_count += 1
            if started and (not c.last_seen or started > c.last_seen):
                c.last_seen = started
            if started and (not c.first_seen or started < c.first_seen):
                c.first_seen = started
        else:
            by_text[key] = Candidate(
                query_id=str(r.get("query_id") or key[:32]),
                sql=text,
                dialect="snowflake",          # this miner reads Snowflake's own history
                run_count=1,
                distinct_users=1,
                first_seen=started or None,
                last_seen=started or None,
                rows_returned=r.get("rows_produced"),
                user_name=user or None,
                # NOT warehouse_name: that never carries a tool name, and
                # mapping it here silently disabled the tooling gate.
                client_app=None,
            )
            users[key] = set()
        if user:
            users[key].add(user)

    for key, c in by_text.items():
        c.distinct_users = max(1, len(users.get(key, ())))
    return list(by_text.values())


_SCHEMA_SQL = """
select table_catalog, table_schema, table_name, column_name, data_type, comment
from %(database)s.information_schema.columns
where table_schema not in ('INFORMATION_SCHEMA')
order by table_schema, table_name, ordinal_position
"""


def mine_schema(conn, database: str) -> dict[str, list[dict]]:
    """Column-level schema, keyed by fully-qualified table name."""
    rows = _fetch(conn, _SCHEMA_SQL % {"database": database})
    out: dict[str, list[dict]] = {}
    for r in rows:
        fq = f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}"
        out.setdefault(fq, []).append({
            "column": r["column_name"],
            "type": r["data_type"],
            "comment": r.get("comment"),
        })
    return out
