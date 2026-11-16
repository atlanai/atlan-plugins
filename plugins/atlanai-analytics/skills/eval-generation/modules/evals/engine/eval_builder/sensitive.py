"""Catch customer-identifying values before an eval set is shared.

A mined query is written by someone who already has access to the data. An eval
set is pushed to a registry other people read, so the question and its golden SQL
travel further than the query ever did. Anything identifying a real customer must
not make that trip.

This is not hypothetical. A selected candidate can carry customer identifiers in
its literals — a filter on a customer's tenant domain, or customer company names
listed in an IN clause. Such a query can be a good one and still not be shareable
as-is.

Detection is deliberately imperfect and deliberately loud. Company names cannot
be recognised reliably by pattern, so this module's job is to refuse to let a
record through *silently*, not to promise it caught everything. A finding means
a human decides; it never auto-approves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# The organisation's own domain. Internal addresses are staff, not customers —
# a query filtering on an analyst's own email is not a customer disclosure.
INTERNAL_DOMAIN = "atlan.com"

# A tenant subdomain: <customer>.atlan.com. These name a customer directly.
_TENANT_RE = re.compile(rf"^(?P<sub>[a-z0-9][a-z0-9-]*)\.{re.escape(INTERNAL_DOMAIN)}$", re.I)
# Internal subdomains that are not customers.
_INTERNAL_SUBDOMAINS = frozenset({
    "www", "docs", "app", "api", "developer", "platform", "releases",
    "help", "support", "status",
})

_EMAIL_RE = re.compile(r"^[^@\s]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}$", re.I)
# "Example Technology", "Acme Holdings" — capitalised multi-word proper nouns.
_PROPER_NOUN_RE = re.compile(r"^(?:[A-Z][\w&.'-]*)(?:\s+[A-Z][\w&.'-]*)+$")

# Proper-noun shape alone is not enough: 'Closed Lost', '1 - Introduction' and
# 'Qualified' are all status values that look exactly like company names. What
# separates them is the column being compared — an entity column holds entity
# names, a status column holds statuses. Without that context the check fires on
# ordinary enum values and a reviewer learns to ignore it, which is worse than
# not checking.
# The entity word must BE the column, or be qualified by name/title/label.
# Matching it as a bare suffix flagged `cost_per_lead` as a customer identity:
# "lead" ends the column, but the column is a metric, not a person.
_ENTITY_WORDS = (r"company|account|customer|client|org|organisation|organization|"
                 r"tenant|vendor|partner|employer|institution|firm|prospect|lead")
_ENTITY_COLUMN_RE = re.compile(
    rf"^({_ENTITY_WORDS})s?$"
    rf"|(^|_)({_ENTITY_WORDS})_(name|names|title|label|caption)$",
    re.I,
)


@dataclass(frozen=True)
class Finding:
    literal: str
    kind: str
    why: str

    def render(self) -> str:
        return f"{self.kind}: {self.literal!r} — {self.why}"


def classify(value: str, column: str | None = None) -> Finding | None:
    """Does this string literal identify someone outside the organisation?

    `column` is the column the literal is compared against, when known. It gates
    the proper-noun check only; domains and emails identify a customer whatever
    column they sit in.
    """
    v = value.strip()
    if not v or len(v) > 200:
        return None
    # A separator such as ' - ' in `legacy_id || ' - ' || customer_name` sits
    # in the same comparison as a real name, but identifies nobody.
    if not re.search(r"[A-Za-z0-9]", v):
        return None

    m = _TENANT_RE.match(v)
    if m and m.group("sub").lower() not in _INTERNAL_SUBDOMAINS:
        return Finding(v, "tenant-domain", "names a customer tenant")

    m = _EMAIL_RE.match(v)
    if m:
        domain = m.group(1).lower()
        if domain != INTERNAL_DOMAIN and not domain.endswith("." + INTERNAL_DOMAIN):
            return Finding(v, "external-email", "an address outside the organisation")
        return None

    if _DOMAIN_RE.match(v) and not v.lower().endswith(INTERNAL_DOMAIN):
        return Finding(v, "external-domain", "a domain outside the organisation")

    if column and _ENTITY_COLUMN_RE.search(column) and len(v.split()) <= 6:
        return Finding(v, "entity-name",
                       f"a value of {column}, which holds organisation names")

    return None


# Columns whose values are customer content or personal data. A golden SQL that
# projects them returns that content every time the eval runs, to everyone who
# can run it — which is a wider audience than the analyst who wrote the query.
# Columns that name a customer's tenant rather than the customer itself.
_TENANT_COLUMN_RE = re.compile(
    r"(instance_url|tenant_url|tenant_name|tenant_domain|workspace_url|"
    r"customer_domain|site_url|host_name)$", re.I,
)

_PII_COLUMN_RE = re.compile(
    r"^(email|e_mail|email_address|phone|phone_number|mobile|address|"
    r"full_name|first_name|last_name|user_name|username|"
    r"body|comment|comment_body|message|message_body|description_text|"
    r"transcript|content|notes|note_text|subject|ip_address|ssn)$",
    re.I,
)


def _label(name: str) -> str:
    """`Account Name` -> `Account_Name`, so a display label is matched like the
    column it names. Sigma returns `"NAME" AS "Account Name"`, and neither half
    matched: NAME alone is not an entity column, and the label had a space
    where the rule expects an underscore."""
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def _displayed(tree: exp.Expression) -> list[exp.Select] | None:
    """The SELECTs whose projections are the result's columns: the outermost
    one, or each branch of an outermost UNION. None when a star hides them."""
    while isinstance(tree, exp.Subquery):
        tree = tree.this
    if isinstance(tree, exp.SetOperation):
        left, right = _displayed(tree.this), _displayed(tree.expression)
        return None if left is None or right is None else left + right
    if isinstance(tree, exp.Select) and not any(p.is_star for p in tree.expressions):
        return [tree]
    return None


def scan_projection(sql: str, *, outer_only: bool = False,
                    dialect: str | None = None) -> list[Finding]:
    """Columns the query RETURNS that carry personal data or customer identity.

    Distinct from literal scanning: the query text may be perfectly clean while
    its result set is a page of customer ticket bodies, email addresses, or —
    the case this missed at first — a straight list of customer names and tenant
    URLs, from a query containing no customer literal at all.

    `outer_only` is for a tile, whose SQL computes exactly what it displays, so
    only the outermost SELECT is returned. Sigma's CTE chain carries ID and NAME
    on the way to a KPI that returns one sum; scanning every SELECT dropped
    almost every Sigma record at review. A star in the outer SELECT hides what
    it returns, and then every SELECT is scanned as before.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect or _run_dialect())
    except Exception:  # noqa: BLE001
        return []

    selects = (_displayed(tree) if outer_only else None) or list(tree.find_all(exp.Select))
    findings: dict[str, Finding] = {}
    for select in selects:
        for proj in select.args.get("expressions") or []:
            # An aggregate over a sensitive column returns a count, not the
            # values, so only bare column projections are a disclosure.
            if proj.find(exp.AggFunc) is not None:
                continue
            name = proj.alias_or_name or ""
            col = proj.find(exp.Column)
            source = col.name if col is not None else ""
            for candidate in (name, source):
                if not candidate:
                    continue
                key = _label(candidate)
                if _PII_COLUMN_RE.match(key):
                    findings[candidate.lower()] = Finding(
                        candidate, "pii-projection",
                        "the result set would carry personal data or customer content",
                    )
                elif _ENTITY_COLUMN_RE.search(key) or _TENANT_COLUMN_RE.search(key):
                    findings[candidate.lower()] = Finding(
                        candidate, "identity-projection",
                        "the result set would be a list of customer names or tenants",
                    )
    return sorted(findings.values(), key=lambda f: f.literal)


def scan_sql(sql: str, *, dialect: str | None = None) -> list[Finding]:
    """Every string literal in a query that may identify a customer."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect or _run_dialect())
    except Exception:  # noqa: BLE001 - fall back to a crude scan rather than none
        return _scan_text(sql)

    findings: dict[str, Finding] = {}
    # Walk comparisons first so each literal carries the column it is tested
    # against; then sweep the remainder for context-free identifiers.
    seen_nodes = set()
    for cmp_node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like):
        cols = [c.name for c in cmp_node.find_all(exp.Column) if c.name]
        column = cols[0] if cols else None
        for lit in cmp_node.find_all(exp.Literal):
            if not lit.is_string:
                continue
            seen_nodes.add(id(lit))
            f = classify(str(lit.this), column)
            if f:
                findings[f.literal] = f
    for lit in tree.find_all(exp.Literal):
        if not lit.is_string or id(lit) in seen_nodes:
            continue
        f = classify(str(lit.this))
        if f:
            findings[f.literal] = f
    return sorted(findings.values(), key=lambda f: f.literal)


_NAME_LABEL_RE = re.compile(r"^(name|names|title|label|display_?name|full_?name)$", re.I)
_ENTITY_TABLE_RE = re.compile(rf"(^|_)({_ENTITY_WORDS})s?(_|$)", re.I)


def scan_element(element) -> list[Finding]:
    """A tile's dimensions whose label hides what they are.

    A Sigma pivot labels a support tool's organisation name `Name`, and the SQL
    projects `"NAME"`: neither half names an entity, so `scan_projection`
    passes a list of customers. The tile knows the physical column behind the
    label, and a bare name label over an entity table is that list.
    """
    dims = element.get("dimensions") if isinstance(element, dict) else getattr(element, "dimensions", None)
    findings: dict[str, Finding] = {}
    for d in dims or []:
        label = (d.get("label") if isinstance(d, dict) else d.label) or ""
        cols = (d.get("physical_columns") if isinstance(d, dict) else d.physical_columns) or []
        if not _NAME_LABEL_RE.match(_label(label)):
            continue
        for col in cols:
            parts = col.split(".")
            table = parts[-2] if len(parts) >= 2 else ""
            if _ENTITY_TABLE_RE.search(table):
                findings[label.lower()] = Finding(
                    label, "identity-projection",
                    f"the result set would list names from {'.'.join(parts[:-1])}")
                break
    return sorted(findings.values(), key=lambda f: f.literal)


def _scan_text(sql: str) -> list[Finding]:
    findings: dict[str, Finding] = {}
    for raw in re.findall(r"'([^']{1,200})'", sql):
        f = classify(raw)
        if f:
            findings[f.literal] = f
    return sorted(findings.values(), key=lambda f: f.literal)


def scan_record(record) -> list[Finding]:
    """Scan everything about a record that would be published.

    A record built from a tile is judged on what the tile returns, as at build:
    its outermost SELECT. Its literals are scanned wherever they sit.
    """
    element = getattr(record, "element", None) or {}
    from eval_builder import dialect as D

    dialect = element.get("dialect") or D.run()
    findings: dict[str, Finding] = {}
    for f in scan_sql(record.golden_sql, dialect=dialect):
        findings[f.literal] = f
    for f in scan_projection(record.golden_sql, outer_only=bool(element), dialect=dialect):
        findings[f.literal] = f
    for f in scan_element(element):
        findings[f.literal] = f
    # The question and spec are prose, so only explicit identifiers are checked:
    # proper-noun matching on free text fires on ordinary capitalised phrases.
    for text in (record.question, record.question_spec):
        # Multi-label: a lazy pattern captures "example-tenant.atlan" and stops,
        # which classifies as an external domain rather than a customer tenant.
        for token in re.findall(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
            r"|\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b",
            text or "",
        ):
            f = classify(token)
            if f:
                findings[f.literal] = f
    return sorted(findings.values(), key=lambda f: f.literal)


def _run_dialect() -> str:
    from eval_builder import dialect as D

    return D.run()
