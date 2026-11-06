# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Query-history shape features and admission.

PROVENANCE — PORTED FROM an earlier benchmark tool's retrieval-corpus builder. Everything the
origin does between reading a shape row and writing a corpus line lives here so it can be
tested without a warehouse: the admission filters, the sqlglot feature block, the bounded-table
resolution and the recipe family key.

What it deliberately left behind: the origin's warehouse connection and file I/O (a
stage's job), its hard-coded thresholds (now policy), and its notion of a sealed
question. TWO THINGS DELIBERATELY CHANGED, and both are rule-driven rather than taste:

  * **The statement is NOT flattened.** The origin writes `re.sub(r"\\s+", " ", sql)`, which is
    where this corpus lost its newlines. On a one-line statement an inline `--` comment runs to
    end of input and comments out the rest of the query: 153 of 505 downstream cards would not
    parse and 223 of 296 witness failures came from it, and the origin then needed a side file
    of recovered SQL to undo its own damage. Rule 17 forbids it outright. The flattened form is
    still computed — the dedup and family keys are defined on it, so selection is unchanged —
    but what is STORED and later parsed is the statement as the analyst wrote it.

  * **Its constants are policy.** The automation heuristic, the length cap and the near-dup
    threshold are admission, so rule 5 puts them in `agent_policy.yml`. They are also facts
    about an estate: `users < 3 and exec > 500` reads as automation on a warehouse with a large
    analytics team and as the whole team's work on a small one.

The decontamination drop-set is NOT here. Excluding an evaluation's own answers is a property
of a benchmark, not of a query corpus, so it arrives as an optional exclusion list the
caller supplies and the agent carries no notion of a sealed question.
"""
from __future__ import annotations

import hashlib
import re

import sqlglot
from sqlglot import exp

from metrics_agent.lib import sqlshape

_WS = re.compile(r"\s+")
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
# A table alias carries no vocabulary; a SELECT alias usually carries the estate's own name
# for the thing being computed, which is why the two are separated here.
_TABLE_ALIAS_JUNK = {"TAB", "TMP", "SRC", "SUB", "CTE"}
_WORD_STOP = {"the", "and", "for", "from", "where", "select", "join", "left", "with", "count",
              "sum", "case", "when", "then", "else", "end", "not", "null", "table", "this",
              "that"}
# The harvest signature a benchmark leaves on its own probe queries.
LEAK_RE = re.compile(r"'[a-z0-9]+(?:-[a-z0-9]+)+'\s+AS\s+id", re.I)


def normalise(s: str) -> str:
    return _WS.sub(" ", str(s or "").strip()).lower()


def flatten(s: str) -> str:
    """The one-line form. Used for KEYS ONLY — never stored, never parsed. See the module note."""
    return _WS.sub(" ", str(s or "")).strip()


def bounded_index(bounded: list[str]) -> tuple[dict, dict, set]:
    """Lookups for resolving a statement's tables onto the estate's bounded surface.

    `by_schema_table` resolves a two-part reference, `by_table` a bare one but ONLY when the
    name is unique in the estate — a bare `ACCOUNT` that exists in four schemas names none of
    them, and guessing is how a query gets attributed to a table it never read.
    """
    bt = set()
    for x in bounded or []:
        x = str(x).strip().strip('"').upper()
        if x.count(".") == 2:
            bt.add(x)
    by_schema_table = {(n.split(".")[1], n.split(".")[2]): n for n in bt}
    by_table: dict[str, list] = {}
    for n in bt:
        by_table.setdefault(n.split(".")[2], []).append(n)
    return by_schema_table, by_table, {n.split(".")[2] for n in bt}


def tables_in(tree, by_schema_table: dict, by_table: dict, *,
              default_db: str | None = None, default_schema: str | None = None) -> set:
    """Bounded tables a statement actually reads. CTE names are excluded: a CTE is a name the
    statement invented, and counting it as a table attributes the query to a relation the
    warehouse has never heard of.

    A NAME IS NOT AN IDENTITY. `BENCH_ACME_PROD.MARTS.FCT_X` (a benchmark
    harness's copy) and `ACME_DEV.MARTS.FCT_X` share schema and table with the prod
    object and are different relations. Matching on (schema, table) alone pooled their
    queries with prod — measured on a real tenant, the most frequent "estate" literal was a
    benchmark fixture. So the database is checked whenever it is known: from the reference
    itself (`DB.SCHEMA.T`), else from the statement's session context when the source records
    one (`default_db` / `default_schema`, which a lakehouse pull carries). A reference whose
    database is known and differs from the bounded object's is not that object.
    """
    ctes = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
    ddb = (default_db or "").strip('"').upper()
    dsch = (default_schema or "").strip('"').upper()
    out = set()
    for tn in tree.find_all(exp.Table):
        name = (tn.name or "").upper()
        if not name or name in ctes:
            continue
        sch = (tn.db or "").upper()
        db = (tn.catalog or "").upper() or ddb
        hit = None
        if (sch, name) in by_schema_table:
            hit = by_schema_table[(sch, name)]
        elif not sch and dsch and (dsch, name) in by_schema_table:
            hit = by_schema_table[(dsch, name)]
        elif not sch and len(by_table.get(name, [])) == 1:
            hit = by_table[name][0]
        if hit and (not db or hit.split(".")[0] == db):
            out.add(hit)
    return out


def feat(tree, raw_sql: str = "") -> dict:
    """Structured features for field-aware retrieval — ported verbatim from the origin.

    `raw_sql` is the UNFLATTENED statement on purpose: `comment_toks` mines the vocabulary
    analysts wrote in their comments, and on a flattened statement there is at most one comment
    left to mine.
    """
    def cols(node):
        return [c.name.upper() for c in node.find_all(exp.Column) if c.name]

    filter_cols, groups, aggs, joins, enum_vals = [], [], [], [], []
    win_cols, win_funcs, aliases, num_lits = [], [], [], []
    for w in (list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Having))
              + list(tree.find_all(exp.Qualify))):
        filter_cols += cols(w)
        for lit in w.find_all(exp.Literal):
            if lit.is_string and lit.this:
                enum_vals.append(str(lit.this)[:40])
            elif not lit.is_string and lit.this:
                num_lits.append(str(lit.this)[:20])
    for g in tree.find_all(exp.Group):
        groups += cols(g)
    for a in tree.find_all(exp.AggFunc):
        aggs += [a.key] + cols(a)
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        if on is not None:
            joins += cols(on)
    for wd in tree.find_all(exp.Window):
        for p in (wd.args.get("partition_by") or []):
            win_cols += cols(p)
        order = wd.args.get("order")
        if order is not None:
            win_cols += cols(order)
        fn = wd.this
        if fn is not None:
            win_funcs.append(fn.key.upper())
    for al in tree.find_all(exp.Alias):
        nm = (al.alias or "").upper()
        if len(nm) >= 4 and nm not in _TABLE_ALIAS_JUNK and re.search(r"[A-Z]", nm):
            aliases.append(nm)
    ctoks = []
    for m in _COMMENT_RE.findall(raw_sql or ""):
        ctoks += _WORD_RE.findall(m.lower())

    def dedup(xs, n):
        return list(dict.fromkeys(xs))[:n]

    return {"filter_cols": dedup(filter_cols, 30), "groups": dedup(groups, 15),
            "aggs": dedup(aggs, 15), "joins": dedup(joins, 15),
            "enum_vals": dedup(enum_vals, 30), "win_cols": dedup(win_cols, 15),
            "win_funcs": dedup(win_funcs, 6), "aliases": dedup(aliases, 20),
            "num_lits": dedup(num_lits, 15),
            "comment_toks": dedup([t for t in ctoks if t not in _WORD_STOP], 40)}


def family_key(tabs, f: dict) -> str:
    """Recipe family: the table set plus what the statement filters, aggregates, groups and
    partitions on. Literal-only variants collapse to one family, which is what stops a top-k
    filling with the same query asked of twelve quarters."""
    parts = (tuple(sorted(tabs)),
             tuple(sorted(set(f.get("filter_cols", [])))),
             tuple(sorted(set(f.get("aggs", [])))),
             tuple(sorted(set(f.get("groups", [])))),
             tuple(sorted(set(f.get("win_cols", [])))))
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


def tokens(s: str) -> set:
    return {w for w in _WORD_RE.findall(str(s or "").lower())}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def admit(sql, n_users, n_exec, *, bare, by_schema_table, by_table, dialect,
          max_sql_chars, auto_max_users, auto_min_exec, exclude_norm=None,
          exclude_tokens_by_table=None, neardup_min=0.85, default_db=None,
          default_schema=None, scope_match="any", issued_markers=()):
    """`(row, reason)` — the row to keep, or None and why it was dropped.

    Order matters and is the origin's: the cheap string prunes run before the parse, because
    parsing every row of a warehouse-scale shape table to then discard most of it is the
    difference between minutes and hours.
    """
    if not sql or len(sql) > max_sql_chars:
        return None, "length"
    su = sql.upper()
    if not any(b in su for b in bare):
        return None, "no_bounded_table_mentioned"
    # A STATEMENT A TOOL ISSUED ITSELF (a preview, a crawl) is not analysis. Which markers
    # those are is each plug's declaration (`issued_markers`); the core names no tool.
    if any(str(m).upper() in su for m in issued_markers):
        return None, "tool_preview"
    # AUTOMATION, NOT ANALYSIS. Few people running something very often is a scheduled job;
    # it is the one identity-shaped filter that earns its place, and it looks at behaviour
    # rather than at who someone is.
    if (n_users or 0) < auto_max_users and (n_exec or 0) > auto_min_exec:
        return None, "automation"
    if LEAK_RE.search(sql):
        return None, "harvest_signature"
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return None, "parse_failed"
    if tree is None:
        return None, "parse_failed"
    # A READ, OR NOT EVIDENCE. A shape table has no status or type column, and a log row
    # with no `query_type` reads as a SELECT, so neither source guarantees the statement is
    # a query. A write that READS a bounded table (`INSERT … SELECT`, a CTAS, a MERGE) is a
    # pipeline step, not analysis, and must never reach a layer published as verified SQL.
    # The same allowlist the warehouse gate uses, so the two can never disagree.
    if sqlshape.has_dml(sql, dialect):
        return None, "not_a_read"
    tabs = tables_in(tree, by_schema_table, by_table, default_db=default_db,
                     default_schema=default_schema)
    if not tabs:
        return None, "no_bounded_table_read"
    # How many DISTINCT relations the statement reads in all, bounded or not (CTEs excluded).
    # `tables` names only the bounded ones, so without this a consumer cannot tell a statement
    # that stays inside the estate from one that joins half the warehouse to one bounded table.
    ctes = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
    n_all = len({(t.catalog or "", t.db or "", t.name or "").__repr__().upper()
                 for t in tree.find_all(exp.Table)
                 if t.name and t.name.upper() not in ctes})
    if scope_match == "all" and n_all > len(tabs):
        return None, "reads_outside_bound"
    if exclude_norm and normalise(sql) in exclude_norm:
        return None, "excluded_verbatim"
    if exclude_tokens_by_table:
        ts = tokens(flatten(sql))
        for tb in tabs:
            for other in exclude_tokens_by_table.get(tb, []):
                if jaccard(ts, other) >= neardup_min:
                    return None, "excluded_near_duplicate"
    f = feat(tree, raw_sql=sql)
    # THE STATEMENT IS STORED AS WRITTEN. Only the keys use the flattened form.
    row = {"sql": sql, "tables": sorted(tabs), "exec": int(n_exec or 0),
           "users": int(n_users or 0), "feat": f, "n_tables_read": n_all}
    row["family"] = family_key(tabs, f)
    return row, None
