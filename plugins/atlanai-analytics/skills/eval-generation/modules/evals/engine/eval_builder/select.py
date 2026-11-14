"""Which queries deserve to become evals.

The naive answer — "the most frequently run ones" — is wrong. The most frequent
queries in any warehouse are tooling: dbt tests, BI cache refreshes, connector
polling, catalog crawlers. They run thousands of times and evaluate nothing.

A query earns a place in an eval set on five independent dimensions. They are
scored separately and reported separately, because a reviewer needs to see *why*
a query was kept, not just that it scored 0.72.

    admissible   is this a real analytical question at all?   (hard gate)
    deterministic  will it give the same answer twice?        (hard gate)
    discriminating does getting it right require context?     (the whole point)
    representative does anyone actually ask this?
    verifiable     can we compare the answer cheaply?

The first two are gates, not scores: a non-deterministic query is not a
"low-scoring eval", it is not an eval. The last three are ranked.

`discriminating` is the load-bearing one. An eval only has value if a badly
contextualised agent would fail it. `SELECT COUNT(*) FROM ORDERS` is answerable
by any agent with a schema, so it measures nothing. A query carrying a non-obvious
join path and a business filter literal (`WHERE is_test_store = FALSE`) is
exactly where agents fail, so it measures everything.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from eval_builder.elements import ElementKind, VisualElement

# ---------------------------------------------------------------------------
# Gate 1: admissible — is this a human asking a question of the data?
# ---------------------------------------------------------------------------

# Machine-issued traffic splits in two, and the split is the whole point.
#
# A pipeline moves or checks data. There is no business question behind a
# Fivetran sync or a dbt model build — nobody wanted to know anything, so there
# is nothing to recover.
#
# A BI tool is the opposite. Every query it issues exists because someone built
# a dashboard to answer a question, and the SQL carries the metric definition,
# the filters and the joins that the business already agreed on. That is the
# scarcest material there is: a bench run over human-authored SQL scored
# `metric_definition 0 <- thin`, and dashboards are where those definitions
# live. Dropping Looker and Tableau as "tooling" threw away the best source in
# the warehouse while a separate module in this same package existed to read
# exactly that material out of workbook files.
#
# So pipelines are a gate, and BI is a provenance label.
_PIPELINE_TOOL_PATTERNS = (
    r"\bdbt\b", r"fivetran", r"airbyte", r"stitch", r"matillion", r"informatica",
    r"hightouch", r"census", r"meltano", r"segment",
    r"monte ?carlo", r"great ?expectations", r"soda", r"elementary",
)
_BI_TOOL_PATTERNS = (
    r"looker", r"tableau", r"power ?bi", r"sigma", r"mode analytics", r"metabase",
    r"superset", r"thoughtspot", r"\bhex\b", r"quicksight", r"domo", r"omni",
)

# Deliberately NOT matched against raw SQL text: vendor names collide with real
# identifiers and literals. Scanning query bodies for a vendor name rejected
# three human-authored bench queries — one for a database whose name contained
# the vendor string, two for an email literal in a WHERE clause. Tool signatures are read from the
# client application field and from leading comments, where tools actually
# stamp themselves.
_PIPELINE_RE = re.compile("|".join(_PIPELINE_TOOL_PATTERNS), re.I)
_BI_RE = re.compile("|".join(_BI_TOOL_PATTERNS), re.I)
# Retained for callers that only need "was this machine-issued at all".
_TOOL_RE = re.compile("|".join(_PIPELINE_TOOL_PATTERNS + _BI_TOOL_PATTERNS), re.I)
_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*", re.S)


def _tool_signature_in_comments(sql: str, pattern=None) -> str | None:
    """dbt, Looker and friends stamp a JSON or key=value header comment."""
    for comment in _COMMENT_RE.findall(sql[:2000]):
        m = (pattern or _TOOL_RE).search(comment)
        if m:
            return m.group(0)
    return None


def bi_origin(c: Candidate) -> str | None:
    """Which BI tool issued this, if one did.

    Checked in the two places a BI tool identifies itself: the client
    application field, and the header comment Looker and friends stamp.
    """
    if c.client_app:
        m = _BI_RE.search(c.client_app)
        if m:
            return m.group(0)
    if c.user_name:
        m = _BI_RE.search(c.user_name)
        if m:
            return m.group(0)
    return _tool_signature_in_comments(c.sql, _BI_RE)

# Query text that is structurally not a business question.
_NON_ANALYTICAL: tuple = (
    exp.Create, exp.Drop, exp.Alter, exp.Insert, exp.Update, exp.Delete,
    exp.Merge, exp.Grant, exp.Use, exp.Describe, exp.Command,
)

_STAGING_RE = re.compile(r"\b(tmp|temp|stg|staging|scratch|_bak|_backup|test_)\w*", re.I)

# The warehouse's own telemetry. "How busy has the warehouse been this month?"
# is a real question, but it is a question about the warehouse rather than about
# the business the warehouse holds — it would never appear in a semantic layer,
# so an agent answering it well says nothing about talk-to-data.
_METADATA_SCHEMA_RE = re.compile(
    r"^(snowflake\.(account_usage|organization_usage|reader_account_usage)|"
    r"[^.]+\.information_schema)\.", re.I,
)

# Identity is the strongest tooling signal in a real warehouse, and the one that
# was missing. In a busy warehouse the busiest query issuers are typically all
# service accounts — an ingestion service, an event pipeline, a reverse-ETL
# user, a BI service user and so on — well ahead of the first human. Filtering on query text alone let all
# of that through.
_SERVICE_ACCOUNT_RE = re.compile(
    r"^(svc[-_]|.*_svc$|system$|.*_service$)"          # explicit service naming
    # Ingestion vendors only. BI vendors are handled by bi_origin(), which
    # keeps them — their queries carry a question.
    r"|^(fivetran|segment|airbyte|stitch|matillion|hightouch|census|meltano)"
    r"|_(svc|service|bot|daemon|job|etl|sync|backend)(_|$)"
    r"|^.*_analytics_user$",
    re.I,
)


def looks_like_service_account(user: str | None) -> bool:
    return bool(user and _SERVICE_ACCOUNT_RE.search(user.strip()))


# ---------------------------------------------------------------------------
# Gate 2: deterministic — will it return the same value on a re-run?
# ---------------------------------------------------------------------------

_NONDETERMINISTIC = re.compile(
    r"\b(random|uuid_string|current_timestamp|current_date|localtime|"
    r"getdate|sysdate|current_user|current_session)\s*\(",
    re.I,
)
_SAMPLE_RE = re.compile(r"\b(sample|tablesample)\b", re.I)

# Query history records the statement, not the values bound to it. A mined
# query carrying `WHERE group_name = ?` describes a population nobody can
# reconstruct: the literal that made it meaningful is gone. Four of
# twenty-five candidates in a cold run were like this, and the drafter had to
# invent a rule because the tool offered none.
_BIND_PARAM_RE = re.compile(r"(?<![\w:])\?(?![\w?])|:\d+\b|%\(\w+\)s")


@dataclass
class Candidate:
    """A mined query plus the evidence for keeping or dropping it."""

    query_id: str
    sql: str
    run_count: int = 1
    distinct_users: int = 1
    last_seen: str | None = None
    first_seen: str | None = None
    rows_returned: int | None = None
    user_name: str | None = None
    client_app: str | None = None
    # A BI element's page and type, when the source records them (Sigma does).
    page: str | None = None
    element_type: str | None = None
    # Set when `prune` cut the SQL down to its question; `sql` is then the cut.
    original_sql: str | None = None
    # The tile this candidate was built from. BI sources set it; query history
    # has none. When set, `sql` is the tile SQL and `page`/`element_type`
    # mirror `element.page` / `element.raw_kind`.
    element: VisualElement | None = None
    # The SQL dialect the source's connector names (see `dialect`); None: the run's.
    dialect: str | None = None

    # filled in by score()
    bi_origin: str | None = None   # the BI tool that issued it, if one did
    admissible: bool = True
    deterministic: bool = True
    scores: dict[str, float] = field(default_factory=dict)
    # per-failure-mode contribution, so a set can be balanced across modes
    modes: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        """Weighted rank among admissible, deterministic candidates.

        Discrimination carries the most weight: an eval that everyone passes
        tells you nothing, however popular the underlying query is.
        """
        if not (self.admissible and self.deterministic):
            return 0.0
        return (
            0.50 * self.scores.get("discriminating", 0.0)
            + 0.30 * self.scores.get("representative", 0.0)
            + 0.20 * self.scores.get("verifiable", 0.0)
        )


def _dialect(c: Candidate) -> str:
    """A tile's SQL is in its warehouse's dialect (Redshift for some Tableau
    sources); query history is Snowflake."""
    from eval_builder import dialect as D

    return D.of(c)


# sqlglot parses leniently: garbage like "SELEKT ** FRM" comes back as an Alias
# rather than raising. Requiring a real query root is what actually rejects it.
_QUERY_ROOTS: tuple = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.With)


def _parse(sql: str, dialect: str | None = None) -> exp.Expression | None:
    from eval_builder import dialect as D

    dialect = dialect or D.run()
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 - unparseable SQL is inadmissible, not fatal
        return None
    if tree is None:
        return None
    # DDL/DML roots are handled separately by check_admissible, so let them pass
    # through; anything that is neither a query nor a statement is garbage.
    if isinstance(tree, _QUERY_ROOTS + _NON_ANALYTICAL):
        tree._raw_sql = sql  # type: ignore[attr-defined]
        return tree
    return None


def _raw_sql(tree: exp.Expression) -> str:
    """The text this tree was parsed from, when sqlglot preserved it."""
    from eval_builder import dialect as D

    return getattr(tree, "_raw_sql", "") or tree.sql(dialect=D.run())


def _tables(tree: exp.Expression) -> list[str]:
    """Real tables only.

    sqlglot reports a CTE reference as an exp.Table, so `WITH dedup AS (...)
    SELECT * FROM dedup` yields a "table" called dedup. Left uncorrected that
    pollutes the subject-area grouping (a live run produced domains named `d`,
    `s`, `lines` and `dedup`), sends the schema lookup after names that do not
    exist, and makes the leak check compare questions against CTE aliases.
    """
    cte_names = {
        cte.alias_or_name.lower()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }
    out = []
    for t in tree.find_all(exp.Table):
        name = ".".join(p for p in (t.catalog, t.db, t.name) if p)
        if not name or name in out:
            continue
        # An unqualified name matching a CTE in the same query is that CTE.
        if not t.db and not t.catalog and t.name.lower() in cte_names:
            continue
        out.append(name)
    return out


def check_admissible(c: Candidate, tree: exp.Expression | None) -> None:
    if tree is None:
        c.admissible = False
        c.notes.append("dropped: SQL does not parse")
        return
    if isinstance(tree, _NON_ANALYTICAL):
        c.admissible = False
        c.notes.append(f"dropped: {type(tree).__name__.lower()} statement, not a question")
        return
    # BI is established first: a Looker service account trips the service-account
    # rule on its name, and dropping it there would lose the dashboard's
    # question along with it.
    bi = bi_origin(c)
    if bi:
        c.bi_origin = bi
        # A dashboard refresh runs on a schedule. Its execution count measures
        # cadence, not how often anyone wanted the answer, so it must not be
        # read as demand the way a human re-running a query is.
        c.run_count = min(c.run_count, 1)
        c.notes.append(f"kept: issued by {bi} — a dashboard's question, "
                       "run count not treated as demand")
    elif looks_like_service_account(c.user_name):
        c.admissible = False
        c.notes.append(f"dropped: issued by a service account ({c.user_name})")
        return

    if not bi:
        if c.client_app and _PIPELINE_RE.search(c.client_app):
            c.admissible = False
            c.notes.append(f"dropped: issued by a pipeline ({c.client_app})")
            return
        sig = _tool_signature_in_comments(c.sql, _PIPELINE_RE)
        if sig:
            c.admissible = False
            c.notes.append(f"dropped: pipeline signature in header comment ({sig})")
            return
    # The OUTERMOST filter is what decides whether this is a question or a
    # lookup. A query can carry real business rules deep in a CTE and still end
    # with `WHERE salesforce_id = '0015e...'`, which makes the whole thing a
    # report about one pinned entity — no business question reaches it.
    outer_where = tree.args.get("where")
    if outer_where is not None and _surrogate_filters(outer_where):
        c.admissible = False
        c.notes.append(
            "dropped: the outer filter pins an identifier — a per-entity report, "
            "whatever rules the inner CTEs apply"
        )
        return

    surrogate = _surrogate_filters(tree)
    if surrogate:
        others = [
            n for n in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like)
            if n not in surrogate
            and any(isinstance(x, exp.Literal | exp.Boolean) for x in n.iter_expressions())
        ]
        if not others:
            c.admissible = False
            c.notes.append(
                "dropped: filters only on a pinned identifier — a per-entity "
                "report, not a business question, and the golden SQL would not "
                "survive a clone"
            )
            return

    meta = [t for t in c.tables if _METADATA_SCHEMA_RE.search(t)]
    if meta and len(meta) == len(c.tables):
        c.admissible = False
        c.notes.append(f"dropped: warehouse metadata, not business data ({meta[0]})")
        return

    staged = [t for t in c.tables if _STAGING_RE.search(t)]
    if staged and len(staged) == len(c.tables):
        c.admissible = False
        c.notes.append(f"dropped: only touches staging/temp objects ({', '.join(staged)})")
        return
    # A bare SELECT with no projection narrowing is exploration, not a question.
    if isinstance(tree, exp.Select):
        projections = tree.args.get("expressions") or []
        if len(projections) == 1 and isinstance(projections[0], exp.Star) and not tree.args.get("where"):
            c.admissible = False
            c.notes.append("dropped: unfiltered SELECT * — browsing, not asking")


def _bind_params(sql: str) -> int:
    """Unresolved placeholders, ignoring those inside string literals."""
    stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return len(_BIND_PARAM_RE.findall(stripped))


def check_deterministic(c: Candidate) -> None:
    if _NONDETERMINISTIC.search(c.sql):
        fn = _NONDETERMINISTIC.search(c.sql).group(1)  # type: ignore[union-attr]
        # A time function is recoverable: the builder pins an as-of anchor.
        if fn.lower() in {"current_timestamp", "current_date", "getdate", "sysdate", "localtime"}:
            c.notes.append(f"needs as-of anchor: uses {fn}()")
        else:
            c.deterministic = False
            c.notes.append(f"dropped: non-deterministic {fn}()")
            return
    if _SAMPLE_RE.search(c.sql):
        c.deterministic = False
        c.notes.append("dropped: uses SAMPLE")


# The failure modes that actually make agents get SQL wrong. This taxonomy is
# not invented here: it matches the gap types a human labelled across a
# text-to-SQL benchmark (filter_value, date_window, metric_definition), with two
# structural modes added for the things joins and grain break.
FAILURE_MODES = ("filter_value", "date_window", "metric_definition", "join_path", "grain")

_DATE_FN = re.compile(
    r"\b(dateadd|datediff|date_trunc|to_date|to_timestamp|last_day|dayofweek|"
    r"month|year|quarter|week)\s*\(", re.I,
)
# Deliberately loose: `to_dt`, `days`, `created_at`, `snapshot_date` are all
# time columns, and a trailing \b missed every plural and abbreviated form.
_DATE_COL = re.compile(r"(date|dt|_at|_ts|time|day|month|year|quarter|week|duration)", re.I)


# Columns whose values are handles, not facts about the business.
_ID_COL_RE = re.compile(r"(^|_)(id|ids|key|guid|uuid|sfid|pk)$", re.I)
# Opaque literals: Salesforce ids, UUIDs, and long mixed alphanumeric handles.
_SURROGATE_LITERAL_RE = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"   # uuid
    r"|[0-9a-zA-Z]{15}|[0-9a-zA-Z]{18}"                                    # salesforce
    r"|[A-Za-z0-9_-]{20,})$"                                               # long handle
)


def _is_surrogate_filter(node: exp.Expression) -> bool:
    """A filter pinning an opaque identifier is a parameter, not a business rule.

    `WHERE salesforce_id = '001xx000003DGb2AAG'` makes the query a per-entity
    report. There is no business question behind it: either the question has to
    quote the id (which no person asks, and which hands the agent the filter) or
    the agent cannot know which entity was meant. Golden SQL also has to stay
    portable across a clone or a synthetic fixture, and a hardcoded surrogate key
    does not survive that.
    """
    cols = [c.name for c in node.find_all(exp.Column) if c.name]
    if not any(_ID_COL_RE.search(c) for c in cols):
        return False
    for lit in node.find_all(exp.Literal):
        text = str(lit.this)
        if lit.is_string and _SURROGATE_LITERAL_RE.match(text):
            return True
        if not lit.is_string and text.isdigit() and len(text) >= 4:
            return True
    return False


def _surrogate_filters(tree: exp.Expression) -> list[exp.Expression]:
    return [
        n for n in tree.find_all(exp.EQ, exp.In)
        if _is_surrogate_filter(n)
    ]


def _detect_filter_value(tree: exp.Expression) -> tuple[float, str | None]:
    """Business rules encoded as literal comparisons.

    Counted wherever they appear, not just in WHERE: a rule hidden inside a
    CASE or IFF (`AVG(IFF(has_converted, ...))`) is a filter the agent must
    know about just as much as one in a WHERE clause.
    """
    n = 0
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like, exp.Is):
        if _is_surrogate_filter(node):
            continue   # a pinned handle is a parameter, not a rule
        if any(isinstance(x, exp.Literal | exp.Boolean) for x in node.iter_expressions()):
            n += 1
    # Thresholds are business rules too: `days >= 0` excludes bad records just
    # as deliberately as `status = 'active'` selects good ones. Date ranges are
    # excluded here — they are scored as date_window instead, not twice.
    for node in tree.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE):
        cols = [c.name for c in node.find_all(exp.Column) if c.name]
        if any(_DATE_COL.search(c) for c in cols):
            continue
        if any(isinstance(x, exp.Literal) for x in node.iter_expressions()):
            n += 1
    # A bare boolean column used as a condition is itself a business rule.
    for node in tree.find_all(exp.If, exp.Case):
        for cond in node.find_all(exp.Column):
            if _looks_boolean(cond):
                n += 1
                break
    if not n:
        return 0.0, None
    return min(0.30, 0.10 * n), f"{n} business filter(s) — rules not derivable from schema"


def _looks_boolean(col: exp.Column) -> bool:
    return bool(re.match(r"^(is|has|should|can)_", col.name or "", re.I))


def _detect_date_window(tree: exp.Expression) -> tuple[float, str | None]:
    """Explicit time boundaries — the single most common silent wrong answer."""
    hits = []
    for node in tree.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between):
        cols = [c.name for c in node.find_all(exp.Column) if c.name]
        if any(_DATE_COL.search(c) for c in cols):
            hits.append(node)
    # Search the original text, not tree.sql(): re-rendering can drop or rewrite
    # the very constructs being looked for.
    fn = _DATE_FN.search(tree.sql()) or _DATE_FN.search(_raw_sql(tree))
    if not hits and not fn:
        return 0.0, None
    score = min(0.25, 0.12 * len(hits)) + (0.08 if fn else 0.0)
    detail = f"{len(hits)} date-range predicate(s)" if hits else "date arithmetic"
    return min(0.25, score), f"{detail} — window boundary must be exact"


def _detect_metric_definition(tree: exp.Expression) -> tuple[float, str | None]:
    """A metric whose definition lives in the SQL, not in the column name.

    Conditional aggregation and ratios between aggregates are where an agent
    invents its own definition and quietly returns a different number.
    """
    conditional_aggs = 0
    for agg in tree.find_all(exp.AggFunc):
        if list(agg.find_all(exp.Case, exp.If)):
            conditional_aggs += 1

    ratio = False
    for div in tree.find_all(exp.Div):
        if list(div.find_all(exp.AggFunc)):
            ratio = True
            break

    guards = len(list(tree.find_all(exp.Nullif)))

    score, why = 0.0, []
    if conditional_aggs:
        score += min(0.25, 0.12 * conditional_aggs)
        why.append(f"{conditional_aggs} conditional aggregate(s)")
    if ratio:
        score += 0.15
        why.append("ratio between aggregates")
    if guards:
        score += 0.05
        why.append("NULLIF guard")
    if not why:
        return 0.0, None
    return min(0.35, score), f"metric definition in SQL: {', '.join(why)}"


def _detect_join_path(tree: exp.Expression) -> tuple[float, str | None]:
    joins = list(tree.find_all(exp.Join))
    if not joins:
        return 0.0, None
    return min(0.30, 0.12 * len(joins)), f"{len(joins)} join(s) — join path must be inferred"


def _detect_grain(tree: exp.Expression) -> tuple[float, str | None]:
    """Row grain: what one row means after the derivation."""
    score, why = 0.0, []
    if list(tree.find_all(exp.Window)):
        score += 0.15
        why.append("window function")
    # find_all, not args: a CTE's GROUP BY is where the grain is actually set,
    # and the root Select does not carry either in its own args.
    ctes = list(tree.find_all(exp.CTE))
    if ctes:
        score += min(0.12, 0.04 * len(ctes))
        why.append(f"{len(ctes)} CTE(s)")
    if list(tree.find_all(exp.Distinct)):
        score += 0.05
        why.append("DISTINCT")
    groups = list(tree.find_all(exp.Group))
    if groups:
        score += 0.05
        why.append("GROUP BY")
    if not why:
        return 0.0, None
    return min(0.25, score), f"grain: {', '.join(why)}"


_DETECTORS = {
    "filter_value": _detect_filter_value,
    "date_window": _detect_date_window,
    "metric_definition": _detect_metric_definition,
    "join_path": _detect_join_path,
    "grain": _detect_grain,
}


def score_discriminating(c: Candidate, tree: exp.Expression) -> float:
    """How much context does answering this correctly require?

    Scored as coverage across named failure modes rather than one opaque number,
    so the builder can assemble a *balanced* eval set instead of twenty
    variations of the same join.
    """
    total = 0.0
    for mode, detect in _DETECTORS.items():
        s, note = detect(tree)
        if s > 0:
            c.modes[mode] = round(s, 3)
            total += s
            if note:
                c.notes.append(note)

    if total == 0.0:
        c.notes.append("no discriminating structure — any agent with a schema answers this")
    return min(1.0, total)


def score_representative(c: Candidate) -> float:
    """Does real demand exist for this question?

    Log-scaled: the 400th run of a query is not 400x the evidence of the first.
    Distinct users matter more than raw count — one person on a cron looks
    identical to real demand under a count, and isn't.
    """
    import math

    freq = min(1.0, math.log1p(c.run_count) / math.log1p(50))
    users = min(1.0, math.log1p(c.distinct_users) / math.log1p(8))
    return 0.4 * freq + 0.6 * users


def score_verifiable(c: Candidate, tree: exp.Expression) -> float:
    """Can the runner compare the answer cheaply and unambiguously?

    A scalar is ideal. A wide result set is not wrong, but comparing it is
    fragile, so it ranks lower.
    """
    projections = tree.args.get("expressions") or []
    has_agg = bool(list(tree.find_all(exp.AggFunc)))
    has_group = bool(tree.args.get("group"))

    if has_agg and not has_group and len(projections) == 1:
        c.notes.append("scalar result — exact comparison")
        return 1.0
    if has_agg and len(projections) <= 3:
        c.notes.append("small aggregate result")
        return 0.75
    if c.rows_returned is not None:
        if c.rows_returned <= 1:
            return 1.0
        if c.rows_returned <= 50:
            return 0.6
        c.notes.append(f"{c.rows_returned} rows — comparison is fragile")
        return 0.2
    if tree.args.get("limit"):
        return 0.5
    c.notes.append("unbounded result set — hard to compare")
    return 0.25


def _pinned_record(tree: exp.Expression | None) -> str | None:
    """`project_id = 'P08'`: an identifier column held to one literal value."""
    if tree is None:
        return None
    for eq in tree.find_all(exp.EQ):
        col, lit = eq.this, eq.expression
        if isinstance(lit, exp.Column) and isinstance(col, exp.Literal):
            col, lit = lit, col
        if isinstance(col, exp.Column) and isinstance(lit, exp.Literal) \
                and (col.name.lower() == "id" or col.name.lower().endswith("_id")):
            return f"{col.name} = {lit.sql()}"
    return None


def score(c: Candidate) -> Candidate:
    """Run every gate and score on one candidate, recording the reasoning."""
    # Before parsing: a statement with placeholders frequently fails to parse,
    # and "SQL does not parse" is a misleading reason for what is really a
    # missing value. The reason is what a reviewer acts on.
    binds = _bind_params(c.sql)
    if binds:
        c.admissible = False
        c.notes.append(
            f"dropped: {binds} unresolved bind parameter(s) — the values that "
            "defined the population were never recorded, so the question cannot "
            "be reconstructed"
        )
        return c

    tree = _parse(c.sql, _dialect(c))
    if tree is not None:
        c.tables = _tables(tree)

    check_admissible(c, tree)
    if not c.admissible:
        return c
    pinned = _pinned_record(tree) if c.element is None else None
    if pinned:
        c.admissible = False
        c.notes.append(
            f"dropped: pinned to one record ({pinned}) — nobody asks it in those "
            "words, and a question that names the record gives the filter away"
        )
        return c
    check_deterministic(c)
    if not c.deterministic:
        return c

    assert tree is not None
    c.scores = {
        "discriminating": score_discriminating(c, tree),
        "representative": score_representative(c),
        "verifiable": score_verifiable(c, tree),
    }
    return c


def rank(candidates: list[Candidate], limit: int | None = None,
         min_discriminating: float = 0.15) -> list[Candidate]:
    """Score, filter and order. Returns only what deserves an eval.

    `min_discriminating` is the floor that removes questions every agent passes.
    It is a separate knob from the overall rank because it encodes a judgment —
    "this eval cannot distinguish a good agent from a bad one" — that should not
    be washed out by a high popularity score.
    """
    scored = [score(c) for c in candidates]
    kept = [
        c for c in scored
        if c.admissible and c.deterministic
        and c.scores.get("discriminating", 0.0) >= min_discriminating
    ]
    for c in scored:
        if c.admissible and c.deterministic and c not in kept:
            c.notes.append(
                f"dropped: discrimination {c.scores.get('discriminating', 0.0):.2f} "
                f"below floor {min_discriminating:.2f}"
            )
    kept.sort(key=_rank_key)
    return kept[:limit] if limit else kept


def _rank_key(c: Candidate) -> tuple:
    """Total first; ties broken on evidence, then on content — never input order.

    A file source has flat representativeness and Sigma KPI SQL ends in LIMIT,
    so totals collapse onto a handful of values: on one workbook about half
    the clean candidates tied on a single total, and a stable sort filled most
    slots from that tie in file order. Next comes how far past the 1.0 cap
    the failure modes sum, then how many modes are covered, then a hash of the
    SQL — arbitrary, but the same whatever order the input arrived in.

    A tile has an identity of its own, so it ends on the element id (then the
    candidate id, which tells a filter variant from its base) instead: tile SQL
    is sqlglot's rendering, and hashing it would make the order depend on the
    sqlglot version.
    """
    depth = sum(c.modes.values())
    if c.element is not None:
        digest = f"{c.element.element_id}\x00{c.query_id}"
    else:
        digest = hashlib.sha1(" ".join(c.sql.split()).encode()).hexdigest()
    return (-round(c.total, 9), -round(depth, 9), -len(c.modes), digest)


def split_ties(selected: list[Candidate], pool: list[Candidate],
               group=lambda c: None) -> list[tuple[float, str | None, int, int]]:
    """Ties the selection cut through: (total, group, tied, chosen) wherever
    some of a tied group made it in and some did not — the places where the
    tie-break, not the score, decided.

    `group` is `domain_of` when `diversify` chose the set: it takes a quota per
    subject area, so the lowest total selected is a quota, not a cut-off, and
    a tie only matters among candidates competing for the same quota.
    """
    def key(c):
        return round(c.total, 9), group(c)
    tied = Counter(key(c) for c in pool)
    chosen = Counter(key(c) for c in selected)
    return sorted(((t, g, tied[(t, g)], n) for (t, g), n in chosen.items()
                   if n < tied[(t, g)]), key=lambda x: -x[0])


def domain_of(c: Candidate) -> str:
    """database.schema of the first table — a good-enough proxy for subject area."""
    if not c.tables:
        return "?"
    parts = c.tables[0].split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]


def diversify(candidates: list[Candidate], limit: int,
              max_share: float = 0.35) -> list[Candidate]:
    """Spread the set across subject areas instead of taking the top N.

    Straight top-N collapses onto whoever writes the most sophisticated SQL. On
    a live run the twelve best-ranked queries produced nine eval questions that
    were all about support-ticket quality — one analyst's dashboard, not the
    data estate. A set like that measures an agent's grasp of one domain and
    says nothing about the rest.

    Candidates stay rank-ordered within a domain; the cap only limits how much
    of the final set any one domain may occupy.
    """
    if limit <= 0 or not candidates:
        return []

    cap = max(1, int(limit * max_share))
    by_domain: dict[str, list[Candidate]] = {}
    for c in candidates:                      # already rank-ordered
        by_domain.setdefault(domain_of(c), []).append(c)

    picked: list[Candidate] = []
    exhausted: set[str] = set()
    # Round-robin so the strongest query from each domain lands before the
    # second-strongest from any of them.
    while len(picked) < limit and len(exhausted) < len(by_domain):
        for domain, queue in by_domain.items():
            if domain in exhausted or len(picked) >= limit:
                continue
            taken = sum(1 for p in picked if domain_of(p) == domain)
            if taken >= cap or not queue:
                exhausted.add(domain)
                continue
            picked.append(queue.pop(0))

    # If the cap left the set short (few domains available), backfill by rank
    # rather than silently returning less than asked for.
    if len(picked) < limit:
        chosen = {id(p) for p in picked}
        for c in candidates:
            if len(picked) >= limit:
                break
            if id(c) not in chosen:
                picked.append(c)

    return picked


def shape_key(sql: str, dialect: str | None = None) -> str:
    """Identity for "the same query, differently parameterised".

    Literals are normalised away, so a query re-run with a different date or
    account collapses onto its twin. A cold run surfaced one exact duplicate
    pair and two near-duplicate pairs among 25 candidates, and the only thing
    that caught them was a human reading all 25 SQL bodies.
    """
    from eval_builder import dialect as D

    dialect = dialect or D.run()
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
        for lit in tree.find_all(exp.Literal):
            lit.set("this", "?")
        return tree.sql(dialect=dialect).lower()
    except Exception:  # noqa: BLE001
        return " ".join(sql.lower().split())


def dedupe(candidates: list[Candidate]) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Collapse candidates that are the same query in different clothes.

    Returns the survivors and the (dropped id, kept id) pairs, because a silent
    merge is how a set ends up looking more varied than it is.

    Tiles are never merged. Two tiles whose SQL differs only in literals are
    two questions -- "% Shipped" and "% Returned" on one workbook -- and a
    merge kept one and lost the other's title. Each is noted as a family
    member instead, and `select_by_coverage` takes one per family first.
    """
    seen: dict[str, Candidate] = {}
    out: list[Candidate] = []
    merged: list[tuple[str, str]] = []
    for c in candidates:
        k = shape_key(c.sql, _dialect(c))
        keeper = seen.get(k)
        if keeper is None:
            seen[k] = c
            out.append(c)
            continue
        if c.element is not None or keeper.element is not None:
            title = f' ("{keeper.element.title}")' if keeper.element is not None else ""
            c.notes.append(f"family: same shape as {keeper.query_id}{title}, "
                           "differing only in literals")
            out.append(c)
            continue
        # Keep the better-evidenced one; a re-run is evidence of demand.
        keeper.run_count += c.run_count
        keeper.distinct_users = max(keeper.distinct_users, c.distinct_users)
        merged.append((c.query_id, keeper.query_id))
    return out, merged


# ---------------------------------------------------------------------------
# Tiles: coverage first
# ---------------------------------------------------------------------------

def decision_count(c: Candidate) -> int:
    """Decisions a tile makes that its question has to recover: the filters
    it applies, the dimensions it breaks down by, and the measures computed
    rather than plainly aggregated."""
    el = c.element
    if el is None:
        return 0
    return (sum(1 for f in el.filters if f.applied)
            + len(el.dimensions)
            + sum(1 for m in el.measures if (m.agg or "").upper() == "CUSTOM"))


def _tile_order(c: Candidate) -> tuple:
    return (-decision_count(c), c.element.element_id if c.element is not None else "",
            c.query_id)


def tile_page(c: Candidate) -> str | None:
    return c.element.page if c.element is not None else c.page


def tile_kind(c: Candidate) -> ElementKind:
    return c.element.kind if c.element is not None else ElementKind.UNKNOWN


def page_order(page: str | None) -> tuple:
    """Pages by name, a tile without one last."""
    return (page is None, page or "")


def _families(candidates: list[Candidate]) -> dict[int, int]:
    """id(candidate) -> family: a tile with its filter variants (same element)
    and with its near-duplicates (same shape, other literals)."""
    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[tuple, int] = {}
    for i, c in enumerate(candidates):
        keys = [("shape", shape_key(c.sql, _dialect(c)))]
        if c.element is not None:
            keys.append(("element", c.element.element_id))
        for k in keys:
            if k in first:
                parent[find(i)] = find(first[k])
            else:
                first[k] = i
    return {id(c): find(i) for i, c in enumerate(candidates)}


def _interleave(queues: list[list]) -> list:
    """One from each queue in turn: a1, b1, c1, a2, b2, a3, ..."""
    out: list = []
    for i in range(max((len(q) for q in queues), default=0)):
        out.extend(q[i] for q in queues if i < len(q))
    return out


def _round_robin(candidates: list[Candidate]) -> list[Candidate]:
    """Pages in turn, and within a page its kinds in turn."""
    groups: dict = {}
    for c in candidates:
        groups.setdefault(tile_page(c), {}).setdefault(tile_kind(c), []).append(c)
    kinds = list(ElementKind)
    return _interleave([
        _interleave([sorted(groups[page][k], key=_tile_order)
                     for k in sorted(groups[page], key=kinds.index)])
        for page in sorted(groups, key=page_order)])


def select_by_coverage(candidates: list[Candidate], limit: int) -> list[Candidate]:
    """Tiles chosen to cover the workbook rather than ranked by their SQL.

    Ranking tiles by SQL buries the headline: the score model rewards what
    Sigma compiles (pass-through CTEs, preview joins), and pruning sent a
    headline KPI from the top third of the ranking to near the bottom. A dashboard's questions sit on
    its pages and in its kinds of tile, so slots go round the pages, and within
    a page round its kinds. Inside one page and kind the tile making the most
    decisions goes first, then the element id -- never SQL length.

    A family (a tile and its filter variants, or tiles whose SQL differs only
    in literals) contributes one member until every family has had its turn;
    the others only fill a quota that would otherwise go unfilled.
    """
    if limit <= 0 or not candidates:
        return []
    family = _families(candidates)
    lead: dict[int, Candidate] = {}
    for c in sorted(candidates, key=_tile_order):
        lead.setdefault(family[id(c)], c)
    leads = [c for c in candidates if lead[family[id(c)]] is c]
    rest = [c for c in candidates if lead[family[id(c)]] is not c]
    return (_round_robin(leads) + _round_robin(rest))[:limit]


def explain(candidates: list[Candidate]) -> str:
    """Render the ranking so a human can agree or disagree with it."""
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i:>3}. [{c.total:.2f}] {c.query_id}  "
            f"disc={c.scores.get('discriminating', 0):.2f} "
            f"rep={c.scores.get('representative', 0):.2f} "
            f"ver={c.scores.get('verifiable', 0):.2f}"
        )
        lines.append(f"     tables: {', '.join(c.tables) or '—'}")
        for n in c.notes:
            lines.append(f"     · {n}")
    return "\n".join(lines)
