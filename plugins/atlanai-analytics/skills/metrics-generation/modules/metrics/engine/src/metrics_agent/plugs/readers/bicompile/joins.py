# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The join plan and the filters: which tables a unit reaches and along which relationships, relative dates, member tests and Top N, and where each filter goes. Part of bicompile; see __init__.py."""
from __future__ import annotations

import pathlib
import re
from typing import Any
import yaml

from .common import (  # noqa: F401
    CompileError,
    Result,
)

# ------------------------------------------------------------------------------ the joins
# A relationship end is `one` or `many`; the vocabulary each reader writes is read here once.
_CARD_ENDS = {
    "m:1": ("many", "one"), "*:1": ("many", "one"), "many_to_one": ("many", "one"),
    "1:1": ("one", "one"), "one_to_one": ("one", "one"),
    "1:m": ("one", "many"), "1:*": ("one", "many"), "one_to_many": ("one", "many"),
    "m:m": ("many", "many"), "*:*": ("many", "many"), "many_to_many": ("many", "many"),
}
# Relationships a statement never joins along: a condition rather than equal columns, and a
# blend, which is a left join AFTER aggregation at the primary's grain, not a SQL join.
_NOT_WALKED = {"range": "a range relationship (a condition, not equal columns)",
               "asof": "an as-of relationship (a condition, not equal columns)",
               "blend": "a data blend (a left join after aggregation at the primary's grain, "
                        "not a SQL join)"}


def _card_ends(card: Any) -> tuple[str | None, str | None]:
    key = re.sub(r"[\s-]+", "_", str(card or "").strip().lower())
    return _CARD_ENDS.get(key, (None, None))


def _physical_edges(graph: list[dict], table_of: dict[str, str]) -> list[dict]:
    """Each relationship with an end named as a model table (`table_of`'s key, lower) named as
    the warehouse table that model table reads; ends already physical are kept."""
    def phys(t: Any) -> Any:
        return table_of.get(str(t or "").lower(), t) if t else t
    return [{**e, "from_table": phys(e.get("from_table")), "to_table": phys(e.get("to_table"))}
            for e in graph]


def _renamed_edges(graph: list[dict], renames: dict[tuple[str, str], str]) -> list[dict]:
    """Each relationship with its columns as the tables read them (`renames`: (table, bare,
    lower; model column, lower) -> the column read), where they differ."""
    def col(table: Any, c: Any) -> Any:
        return renames.get((_bare(table), str(c or "").lower()), c) if c else c
    out = []
    for e in graph:
        f, t = e.get("from_table"), e.get("to_table")
        new = {**e, "from_column": col(f, e.get("from_column")),
               "to_column": col(t, e.get("to_column"))}
        if e.get("columns"):
            new["columns"] = [{**p, "from_column": col(f, p.get("from_column")),
                               "to_column": col(t, p.get("to_column"))}
                              for p in e["columns"]]
        out.append(new)
    return out


def _edge_pairs(e: dict) -> list[tuple[str, str]]:
    pairs = [(str(p["from_column"]), str(p["to_column"])) for p in e.get("columns") or []]
    return pairs or [(str(e["from_column"]), str(e["to_column"]))]


class _Step:
    """One hop of a join path: along relationship `edge` (its index in the unit's graph),
    forwards (from end -> to end) or back, onto `table`."""

    def __init__(self, idx: int, edge: dict, forward: bool, table: str, distinct: bool,
                 keep: bool, keep_far: bool = False) -> None:
        self.idx, self.edge, self.forward, self.table = idx, edge, forward, table
        # keep: the near end's unmatched rows survive; keep_far: the reached end's too (a
        # relationship that keeps what does not match on either side).
        self.distinct, self.keep, self.keep_far = distinct, keep, keep_far
        pairs = _edge_pairs(edge)
        # (column on the table the hop leaves, column on the table it reaches)
        self.pairs = pairs if forward else [(b, a) for a, b in pairs]

    @property
    def key(self) -> tuple[int, bool]:
        return (self.idx, self.forward)

    @property
    def name(self) -> str:
        e = self.edge
        return str(e.get("name") or f"{_bare(e.get('from_table'))}.{e.get('from_column')} -> "
                                     f"{_bare(e.get('to_table'))}.{e.get('to_column')}")


class _JoinPlan:
    """The joins a statement needs, and where each column it groups or filters by is read.

    `at(column, table)` is the alias a column is read under: the statement's own alias, or the
    alias of the table a declared relationship path reaches."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.clauses: list[str] = []
        self.unreachable: set[str] = set()
        self.reasons: list[str] = []
        self.full = False          # a hop keeps the reached table's unmatched rows too
        self._at: dict[tuple[str, str], str] = {}
        # Each joined alias -> the key columns it is joined on, so an expression over a
        # joined table's row can tell an unmatched row (all NULL) from a matched one.
        self.keys: dict[str, list[str]] = {}
        # (column, warehouse table) a MEASURE reads through a joined table -> its alias, and
        # why one could not be reached (a measure that needs it is refused, not the unit).
        self.measure_at: dict[tuple[str, str], str] = {}
        self.unplaced: dict[tuple[str, str], str] = {}
        # Each joined alias -> the warehouse table it reads (bare).
        self.table_at: dict[str, str] = {}
        # Columns on a table the relationships reach only against their filter direction or
        # along an inactive relationship: (column -> (table, hint)). Such a table does not
        # filter the statement's rows; the visual lists its members beside every row.
        self.detached: dict[str, tuple[str, str | None]] = {}
        # A path through an end that is not one row (many-to-many, or one-to-many along a
        # relationship that filters both ways): its root alias -> how it is read
        # (`_expand_branches`), and the row predicates on its tables, which belong inside it.
        self.branches: dict[str, dict] = {}
        self.branch_preds: dict[str, list[str]] = {}
        self.graph: list[dict] = []

    def at(self, column: str, table: str | None = None) -> str:
        col = str(column).lower()
        return (self._at.get((col, _bare(table))) or self._at.get((col, ""))
                or self.alias)


def _blank_rows_having(values: list[str]) -> str | None:
    """`(a IS NOT NULL OR b IS NOT NULL ...)` over the statement's value projections (each
    `expr AS alias`); None when there is none, or one is a window (a HAVING cannot test it)."""
    exprs = [x.rsplit(" AS ", 1)[0] for x in values]
    if not exprs or any(" OVER (" in e for e in exprs):
        return None
    return "(" + "\n       OR ".join(f"{e} IS NOT NULL" for e in exprs) + ")"


def _hide_blank_rows_after(sql: str, select: list[str], n_groups: int) -> str:
    """The statement's rows without those whose every value is empty, where a value is a
    window (computed across the visual's rows), which a HAVING cannot test. Power BI leaves a
    row out of a visual when every measure on it is BLANK, whatever the measure reads
    (SUMMARIZECOLUMNS keeps a row only where at least one of its expressions is not blank:
    learn.microsoft.com/dax/summarizecolumns-function-dax). The window is computed first, over
    every grouped row: a total over a wider context reads the model's rows, not the rows the
    visual goes on to show (seen against Power BI: a city with no red sales has no share of
    the red total and no row)."""
    names = [_out_name(x) for x in select]
    cols = ",\n       ".join(f"b_.{n}" for n in names)
    shown = "\n    OR ".join(f"b_.{n} IS NOT NULL" for n in names[n_groups:])
    return f"SELECT {cols}\nFROM   (\n{sql}\n) b_\nWHERE  ({shown})"


_JOIN_CLAUSE = re.compile(r"^\s*(?:LEFT\s+|FULL\s+(?:OUTER\s+)?|INNER\s+)?JOIN\s+(.+?)\s+(\w+)"
                          r"\s+ON\s+(.*)$", re.S | re.I)


def _with_every_member(leaf: str, select: list[str], n_groups: int, gexprs: list[str],
                       src: str, alias: str, joins: list[str], preds: list[str], rules,
                       dialect: str, graph: list[dict] | None = None,
                       members: list[str] | None = None,
                       folded: set[int] | frozenset = frozenset()) -> str:
    """"Show items with no data": every member of the grouping columns, with the leaf
    statement's values where it has a row (NULL elsewhere).

    The members of the columns read on ONE table are the combinations that table holds
    (under the filters on it, and on the tables it reaches along its own many-to-one hops,
    which filter it); columns of different tables are crossed. A filter on a table only
    REACHED FROM a grouped table (the fact, when a dimension is grouped) does not narrow that
    dimension's members. The leaf's own rows are kept too (a FULL JOIN), so a member only the
    rows have (the blank member of unmatched keys) stays."""
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"it joins the members to the rows, NULL matching NULL, which has "
                           f"no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    # The join tree: each joined alias -> (its table, its ON clause, the alias it hangs off).
    tree: dict[str, tuple[str, str, str]] = {}
    for j in joins:
        m = _JOIN_CLAUSE.match(j)
        if not m:
            raise CompileError("a join of the statement this cannot read")
        parents = [a for a in re.findall(r"\b(\w+)\.", m.group(3)) if a != m.group(2)]
        if len(set(parents)) != 1:
            raise CompileError("a join of the statement that hangs off no single table")
        tree[m.group(2)] = (m.group(1), j, parents[0])

    def below(a: str) -> list[str]:
        out, todo = [], [a]
        while todo:
            cur = todo.pop(0)
            kids = [k for k, (_t, _j, par) in tree.items() if par == cur]
            out += kids
            todo += kids
        return out

    def aliases_in(text: str) -> set[str]:
        return {a for a in re.findall(r"\b(\w+)\.", re.sub(r"'(?:[^']|'')*'", "", text))
                if a == alias or a in tree}

    owner: list[str] = []
    for e in gexprs:
        on = aliases_in(e)
        if len(on) != 1:
            raise CompileError(f"a grouping column read on {len(on)} tables at once")
        owner.append(next(iter(on)))
    outs = []
    for x in select[:n_groups]:
        name = x.rsplit(" AS ", 1)[1] if " AS " in x else x.rsplit(".", 1)[-1]
        outs.append(name.strip())
    parts = []
    for k, a in enumerate(dict.fromkeys(owner)):
        reach = [a] + below(a)
        # Only a filter on the table itself narrows its members (seen live: SUMMARIZECOLUMNS
        # crosses a dimension's every member with the others under a filter on a table its
        # rows reach, e.g. every product beside the categories a department filter keeps);
        # the fact, which reaches every dimension, is narrowed by all of them.
        mine = [p for p in preds if aliases_in(p) and (
            aliases_in(p) <= {a} or (a == alias and aliases_in(p) <= set(reach)))]
        # A member filter (the members a Top N keeps) is a predicate on its key's table,
        # whatever its ranking subquery reads.
        for p in members or []:
            key = re.match(r"\(?\s*(\w+)\.\w+\s+IN\s*\(", p)
            if key and (key.group(1) == a or (a == alias and key.group(1) in reach)):
                mine.append(p)
        # A text column the model compares case- and trailing-space-insensitively is one
        # member per folded value (seen live: 'north' and 'North' are one region), shown by
        # the spelling the rows show.
        mine_idx = [i for i, o in enumerate(owner) if o == a]
        fold = [i for i in mine_idx if i in folded]
        cols = [f"MIN({gexprs[i]}) AS g{i}" if i in folded else f"{gexprs[i]} AS g{i}"
                for i in mine_idx]
        frm = (f"{src} {alias}" if a == alias else f"{tree[a][0]} {a}")
        body = (("SELECT " if fold else "SELECT DISTINCT ")
                + ",\n                   ".join(cols)
                + f"\n            FROM   {frm}"
                + "".join(f"\n            {tree[b][1]}" for b in below(a)))
        if mine:
            body += "\n            WHERE  " + "\n              AND  ".join(mine)
        if fold:
            body += "\n            GROUP BY " + ", ".join(
                _Parser._text_key(gexprs[i]) if i in folded else gexprs[i] for i in mine_idx)
        if not mine and a != alias:
            # The table's BLANK member: a row reaching it with no match (an unmatched key) is
            # a member of its own, with no filter on the table to leave it out.
            path, cur = [], a
            while cur in tree:
                path.insert(0, tree[cur][1])
                cur = tree[cur][2]
            key = re.search(rf"\b{re.escape(a)}\.(\w+)", tree[a][1].split(" ON ", 1)[-1])
            nulls = ", ".join(f"NULL AS g{i}" for i, o in enumerate(owner) if o == a)
            if cur == alias and key:
                body = (f"{body}\n            UNION\n            SELECT {nulls}"
                        f"\n            FROM   {src} {alias}"
                        + "".join(f"\n            {j}" for j in path)
                        + f"\n            WHERE  {a}.{key.group(1)} IS NULL")
            # Another relationship into the table (an inactive one too) whose keys it lacks
            # makes the same blank member (seen live: the ship date's empty keys give the
            # date table a blank year).
            other = _blank_member_test(tree[a][0], [e for e in graph or []
                                                    if e.get("is_active") is False])
            if other:
                body = (f"{body}\n            UNION\n            SELECT {nulls}"
                        f"\n            FROM   (SELECT 1 AS one) z\n            WHERE  {other}")
        parts.append(f"(\n            {body}\n        ) m{k}")
    idx = {a: k for k, a in enumerate(dict.fromkeys(owner))}
    on = " AND ".join(tmpl.format(_Parser._text_key(f"m{idx[o]}.g{i}"),
                                  _Parser._text_key(f"v.{outs[i]}")) if i in folded
                      else tmpl.format(f"m{idx[o]}.g{i}", f"v.{outs[i]}")
                      for i, o in enumerate(owner))
    keys = [f"COALESCE(m{idx[o]}.g{i}, v.{outs[i]}) AS {outs[i]}" for i, o in enumerate(owner)]
    vals = [f"v.{x.rsplit(' AS ', 1)[1].strip()}" for x in select[n_groups:]]
    inner = "\n".join("    " + ln for ln in leaf.splitlines())
    members = "\n    CROSS JOIN ".join(parts)
    return ("SELECT " + ",\n       ".join(keys + vals)
            + f"\nFROM   {members}\nFULL JOIN (\n{inner}\n) v\n  ON  {on}")


_REF = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\.([A-Za-z_]\w*)")


def _branch_split(preds: list[str], plan: _JoinPlan) -> list[str]:
    """The row predicates that stay on the statement; the ones over a branch's tables only go
    inside it (`plan.branch_preds`): outside, a member that passes would repeat a row."""
    if not plan.branches:
        return preds
    owner = {al: root for root, br in plan.branches.items() for al in br["aliases"]}
    out = []
    for p in preds:
        text = re.sub(r"'(?:[^']|'')*'", "", p)
        hit = {owner.get(m.group(1)) for m in _REF.finditer(text)
               if m.group(1) in owner or m.group(1) == plan.alias
               or m.group(1) in plan.table_at}
        roots = {h for h in hit if h is not None}
        if not roots:
            out.append(p)
            continue
        if len(roots) > 1 or None in hit:
            raise CompileError("a filter over a many-to-many (or both-ways) path and another "
                               "table at once, which one distinct set of members cannot hold")
        plan.branch_preds.setdefault(next(iter(roots)), []).append(p)
    return out


def _blank_in(f: dict) -> bool | None:
    """Whether a filter keeps a blank member: an `in` listing the blank, a `not_in` not
    listing it; a test of the empty value (`is_null`), a text test (a blank holds no text:
    `contains` fails it, `not_contains` keeps it) and a comparison read with the filter's
    stand-in for an empty cell (`null_as`: 0, "" or the zero date). None for a filter whose
    test of a blank is not read here (a Top N, a relative date)."""
    op, v = str(f.get("op") or "").lower(), f.get("value")
    vals = v if isinstance(v, list) else [v]
    if op in ("in", "=") or (op == "eq" and isinstance(v, list)):
        return any(x is None for x in vals)
    if op in ("not_in", "ne", "<>"):
        return not any(x is None for x in vals)
    if op in ("is_null", "not_null"):
        return op == "is_null"
    if op in _TEXT_OPS:
        return op.startswith("not_")
    if op in ("eq", "filter", "gt", "lt", "neq", "lte", "gte", "range") and v is not None:
        return _empty_passes("eq" if op == "filter" else op, v, f.get("null_as"))
    if op in ("any_of", "all_of") and isinstance(v, list) and v:
        parts = [_blank_in({**part, "null_as": f.get("null_as")}) for part in v
                 if isinstance(part, dict)]
        if None in parts or len(parts) != len(v):
            return None
        return any(parts) if op == "any_of" else all(parts)
    return None


def _blank_member_test(table: str, graph: list[dict], _seen: frozenset = frozenset()
                       ) -> str | None:
    """When `table` has a BLANK member: the condition that some relationship into its one end
    holds a key it lacks (or no key), or that the table on its many side has a blank member
    of its own (whose key is blank: seen live, a category lists a blank when a sale's product
    is unknown, every product's category known). None when no relationship ends on it as
    one."""
    tests = []
    for e in graph or []:
        fs, ts = _card_ends(e.get("cardinality"))
        if str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        for many_t, one_t, many_end, one_end, flip in (
                (e.get("from_table"), e.get("to_table"), fs, ts, False),
                (e.get("to_table"), e.get("from_table"), ts, fs, True)):
            if _bare(one_t) != _bare(table) or one_end != "one" or many_end != "many":
                continue
            pairs = _edge_pairs(e)
            pairs = [(b, a) for a, b in pairs] if flip else pairs
            on = " AND ".join(f"y_.{oc} = x_.{mc}" for mc, oc in pairs)
            tests.append(f"EXISTS (SELECT 1 FROM {many_t} x_ "
                         f"WHERE NOT EXISTS (SELECT 1 FROM {one_t} y_ WHERE {on}))")
            if _bare(many_t) not in _seen:
                farther = _blank_member_test(many_t, graph, _seen | {_bare(table)})
                if farther:
                    tests.append(farther)
    return " OR ".join(dict.fromkeys(tests)) or None


def _expand_branches(sql: str, plan: _JoinPlan, rules, dialect: str) -> str:
    """Each branch placeholder -> `INNER JOIN (SELECT DISTINCT member, columns ...) a ON
    member = key`, NULL matching NULL (a blank member is a member). The statement's references
    to the branch's tables are read off the distinct set."""
    if not plan.branches:
        return sql
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"a many-to-many (or both-ways) path joins on its members, NULL "
                           f"matching NULL, which has no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    for a, br in plan.branches.items():
        mine = set(br["aliases"])
        holder = f"__BRANCH_{a}__"
        head, _, tail = sql.partition(holder)
        if not _:
            continue
        # Each column the statement reads on the branch's tables is projected under its own
        # name, or `<alias>_<column>` where two of its tables share that name.
        found = {(m.group(1), m.group(2)) for m in _REF.finditer(head + "\n" + tail)
                 if m.group(1) in mine}
        count: dict[str, int] = {}
        for _al, col in found:
            count[col.lower()] = count.get(col.lower(), 0) + 1
        refs = {(al, col): (col if count[col.lower()] == 1 and not col.lower().startswith(
            f"{a}_m") else f"{al}_{col}") for al, col in found}

        def sub(m: re.Match, a: str = a, mine: set = mine, refs: dict = refs) -> str:
            al, col = m.group(1), m.group(2)
            if al not in mine:
                return m.group(0)
            return f"{a}.{refs[(al, col)]}"
        head, tail = _REF.sub(sub, head), _REF.sub(sub, tail)
        step = br["step"]
        e = step.edge
        near_table = e["from_table"] if step.forward else e["to_table"]
        pairs = step.pairs                    # (column on the table left, column reached)
        if br["near_one"]:
            # The member is the row of the one end each row reaches, or its blank member.
            member = [f"{a}_m.{sc}" for sc, _dc in pairs]
            via = (f"\n    LEFT JOIN {near_table} {a}_m ON "
                   + " AND ".join(f"{a}_m.{sc} = {a}.{dc}" for sc, dc in pairs))
        else:
            member = [f"{a}.{dc}" for _sc, dc in pairs]
            via = ""
        base = str(step.table)
        blank = _blank_member_test(base, plan.graph)
        if blank:
            base = (f"(\n      SELECT * FROM {step.table}\n      UNION ALL\n      SELECT b0_.*"
                    f"\n      FROM   (SELECT 1 AS one_) o_\n      LEFT JOIN {step.table} b0_ "
                    f"ON 1 = 0\n      WHERE  {blank}\n    )")
        cols = [f"{m} AS {a}_m{i}" for i, m in enumerate(member)]
        cols += [f"{al}.{col} AS {name}" for (al, col), name in refs.items()
                 if name not in {f"{a}_m{i}" for i in range(len(member))}]
        body = ("SELECT DISTINCT " + ",\n           ".join(dict.fromkeys(cols))
                + f"\n    FROM   {base} {a}" + via
                + "".join(f"\n    {c}" for c in br["clauses"]))
        preds = plan.branch_preds.get(a) or []
        if preds:
            body += "\n    WHERE  " + "\n      AND  ".join(preds)
        on = " AND ".join(tmpl.format(f"{a}.{a}_m{i}", f"{br['prev']}.{sc}")
                          for i, (sc, _dc) in enumerate(pairs))
        sql = head + f"INNER JOIN (\n    {body}\n  ) {a} ON {on}" + tail
    return sql


def _branch_many_hop(step: "_Step", a: str, prev: str, on: str, root: str,
                     prev_step: "_Step") -> str:
    """A hop inside a branch onto an end that is NOT one row (one-to-many filtering both ways,
    or many-to-many), as an INNER JOIN. A row with no row of the reached table reaches no
    member of the tables beyond it; kept by a LEFT JOIN its NULLs read as their BLANK member
    (seen against Power BI: a store with no sales counted its country for the blank customer).

    From a ONE end, the reached table's rows whose key that end lacks belong to its blank
    member, the row whose key is NULL here (the branch's own blank row, or a row a LEFT JOIN
    left unmatched): they are joined to it. Where the end left was itself reached as many
    rows, its blank member is no row of the join, so those rows would be lost: refused."""
    fs, ts = _card_ends(step.edge.get("cardinality"))
    near = fs if step.forward else ts
    if near != "one":
        return f"INNER JOIN {step.table} {a} ON {on}"
    if prev != root and prev_step.distinct:
        raise CompileError("a both-ways path that leaves a table reached as many rows for "
                           "its own many side: the rows its blank member holds are not "
                           "placed")
    near_table = step.edge["from_table"] if step.forward else step.edge["to_table"]
    lost = " AND ".join(f"{prev}.{sc} IS NULL" for sc, _dc in step.pairs)
    match = " AND ".join(f"y_.{sc} = {a}.{dc}" for sc, dc in step.pairs)
    return (f"INNER JOIN {step.table} {a} ON ({on} OR ({lost} AND NOT EXISTS "
            f"(SELECT 1 FROM {near_table} y_ WHERE {match})))")


def _tuple_columns(f: dict) -> list[dict]:
    """The columns a `tuple` filter tests together ({column, table} each), else none."""
    v = f.get("value")
    if str(f.get("op") or "").lower() != "tuple" or not isinstance(v, dict):
        return []
    return [c for c in v.get("columns") or [] if isinstance(c, dict) and c.get("column")]


def _filter_reads_column(f: dict) -> bool:
    """A filter entry the compiler turns into a predicate on its column."""
    if (f.get("stage") or "").lower() in ("measure", "table-calc"):
        return False
    if str(f.get("op") or "").lower() == "topn":
        return False
    return not (f.get("value") is None and f.get("value_kind") == "absent")


def _plan_joins(unit: dict, source: str, alias: str, columns: dict[str, set[str]],
                extra: list[tuple[str, str]] | tuple = (), graph: list[dict] | None = None,
                fields: list[tuple[str, str]] | tuple = (), whole_model: bool = False
                ) -> _JoinPlan:
    """The joins that reach every column the unit groups or filters by.

    THE SEMANTICS ARE THE RELATIONSHIP'S, read off the common relationship fields, never the
    tool's name:

    * Only an ACTIVE relationship is walked (`is_active`), and never a blend, a range or an
      as-of one. A table only those reach is refused by name.
    * A hop never multiplies the statement's rows. From the many end to the one end (or across
      a one-to-one) it is a plain join. Onto an end that is not one (many-to-many, or a
      multiplicity the model does not state) the reached table is read as its DISTINCT rows
      over the columns the statement needs, so each row of the statement's table is counted
      once per member, as a relationship that aggregates each table at its own grain does.
      From the one end to the many end it is walked only when the relationship filters both
      ways (`cross_filter: both`), and then read the same distinct way; otherwise refused.
    * Unmatched rows follow `join_type`: a `left` relationship keeps a many-end row whose key
      the one end lacks (a NULL member, the tool's blank one), a `full` one keeps an unmatched
      row of either end, an `inner` one neither. A row of the statement's table is kept
      (LEFT JOIN) exactly when its end is one of those the relationship keeps: for `left`, the
      many end, however the model stored the relationship round. Under `full` the reached
      table's unmatched rows are kept too (FULL OUTER JOIN: a member with no fact rows shows,
      with NULL measures). A filter still applies after the join: one on the statement's
      table removes such a member, as Tableau's own answer does (seen live).
    * Any number of hops (a snowflaked dimension two tables away). When two different paths
      of the same length reach a table, which one the model means is not knowable here: the
      column is refused by name, never joined along the first.
    * A column is read on the table the unit says it is on (`group_by_tables`, a filter's
      `table`); otherwise on the statement's own table when that holds it, else on the one
      unit table that does. Two candidate tables and no word on which is refused by name.
    * `extra` are columns a MEASURE reads on another table (a CALCULATE filter on a
      dimension's column, RELATED): reached only along hops that never repeat a row of the
      statement's table, and always kept by a LEFT JOIN, since the measure reading them must
      not change which rows the statement has. One that cannot be reached is recorded in
      `unplaced`; the measure is refused, not the unit.
    """
    plan = _JoinPlan(alias)
    gb = list(unit.get("group_by") or [])
    gbt = list(unit.get("group_by_tables") or [])
    # (column, table, is a grouping, is a measure's)
    requests: list[tuple[str, str | None, bool, bool]] = []
    for i, g in enumerate(gb):
        requests.append((str(g).lower(), gbt[i] if i < len(gbt) else None, True, False))
    for f in unit.get("filters") or []:
        if f.get("column") and _filter_reads_column(f):
            requests.append((str(f["column"]).lower(), f.get("table"), False, False))
        for c in _tuple_columns(f):
            requests.append((str(c["column"]).lower(), c.get("table"), False, False))
    for col, table in extra:
        requests.append((str(col).lower(), table, False, True))
    for col, table in fields:
        requests.append((str(col).lower(), table, False, False))
    for mj in unit.get("member_joins") or []:
        # A table whose members the statement always joins (a TREATAS of its values): read
        # like a filter's column, so the join keeps only the rows that match a member.
        requests.append((str(mj["column"]).lower(), mj["table"], False, False))
    src = _bare(source)
    plan.table_at[alias] = src
    if not requests:
        return plan

    tables = unit.get("warehouse_tables") or []
    graph = unit.get("join_graph") or [] if graph is None else graph

    def _locate(col: str, hint: str | None, grouping: bool) -> tuple[str | None, str | None]:
        if hint:
            return (None if _bare(hint) == src else str(hint)), None
        if col in _columns_of(source, columns):
            return None, None
        cands = list(dict.fromkeys(t for t in tables if _bare(t) != src
                                   and col in _columns_of(t, columns)))
        if len(cands) == 1:
            return cands[0], None
        if not cands:
            # A filter whose column no table is known to hold stays on the statement's table
            # (the compiler cannot place it elsewhere, and EXPLAIN says so); a grouping column
            # with no home is refused, as it always was.
            return None, ("" if grouping else None)
        return None, (f"column {col!r} is on {sorted(cands)} and the BI file does not say "
                      f"which")

    blocked: dict[str, list[str]] = {}
    # Tables blocked only by a relationship's filter direction or by its being inactive: the
    # relationship is there, it filters nothing towards the statement's rows.
    soft: set[str] = set()
    hard: set[str] = set()

    def _steps_from(cur: str):
        for i, e in enumerate(graph):
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            if cur not in (a, b) or a == b:
                continue
            other_end = b if cur == a else a
            other_table = str(e["to_table"] if cur == a else e["from_table"])
            label = str(e.get("name") or f"{a} -> {b}")
            kind = str(e.get("kind") or "relationship")
            if kind in _NOT_WALKED:
                blocked.setdefault(other_end, []).append(f"{_NOT_WALKED[kind]} ({label})")
                hard.add(other_end)
                continue
            if e.get("is_active") is False:
                blocked.setdefault(other_end, []).append(
                    f"an inactive relationship ({label}), which the model uses only where a "
                    f"measure activates it")
                soft.add(other_end)
                continue
            fs, ts = _card_ends(e.get("cardinality"))
            jt = str(e.get("join_type") or "left").lower()
            both = str(e.get("cross_filter") or "").lower() in ("both", "bothdirections")
            forward = cur == a
            near, far = (fs, ts) if forward else (ts, fs)
            # Is a row of the end the statement stands on kept when it matches nothing? A
            # `left` relationship keeps its MANY end's (its from end's, when that says
            # nothing): the blank member is always on the other side, whichever way round
            # the model stored the relationship.
            primary_is_from = not (fs == "one" and ts == "many")
            keep = jt == "full" or (jt == "left" and forward == primary_is_from)
            if fs == "one" and ts == "one" and jt == "left":
                # One-to-one: each end has a blank member for the other's unmatched rows, so
                # a row of the end the statement stands on is kept whichever way it is walked.
                keep = True
            far_kept = jt == "full"
            if far == "one":
                yield other_end, _Step(i, e, forward, other_table, False, keep, far_kept)
            elif far is None or (far == "many" and near == "many"):
                if forward or both:
                    yield other_end, _Step(i, e, forward, other_table, True, keep, far_kept)
                else:
                    blocked.setdefault(other_end, []).append(
                        f"relationship {label}, which filters only towards {cur}")
                    soft.add(other_end)
            elif both:
                yield other_end, _Step(i, e, forward, other_table, True, keep, far_kept)
            else:
                blocked.setdefault(other_end, []).append(
                    f"relationship {label} from its one end to its many end, which repeats "
                    f"rows and does not filter that way")
                soft.add(other_end)

    # Breadth first, keeping up to two shortest paths per table: a second is an ambiguity.
    dist: dict[str, int] = {src: 0}
    paths: dict[str, list[tuple[_Step, ...]]] = {src: [()]}
    frontier = [src]
    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            for other, step in _steps_from(cur):
                if other == src:
                    continue
                if other not in dist:
                    dist[other] = dist[cur] + 1
                    paths[other] = []
                    nxt.append(other)
                if dist[other] != dist[cur] + 1:
                    continue
                for p in paths[cur]:
                    if len(paths[other]) < 2 and all(s.key != step.key for s in p):
                        paths[other].append(p + (step,))
        frontier = nxt

    # Every table some relationship (other than a blend or a condition) connects to the
    # statement's table, whichever way it filters and whether it is active.
    relaxed, todo = {src}, [src]
    while todo:
        cur = todo.pop()
        for e in graph:
            if str(e.get("kind") or "relationship") in _NOT_WALKED:
                continue
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            for x, y in ((a, b), (b, a)):
                if x == cur and y not in relaxed:
                    relaxed.add(y)
                    todo.append(y)

    alias_of_path: dict[tuple, str] = {}
    hops: list[tuple[str, _Step, str]] = []               # (alias, step, alias it leaves)
    needs: dict[str, list[str]] = {}                       # alias -> columns read on it

    def _need(a: str, col: str) -> None:
        if col.lower() not in [c.lower() for c in needs.setdefault(a, [])]:
            needs[a].append(col)

    structural: set[tuple] = set()
    for col, hint, grouping, measure in requests:
        if measure:
            target = str(hint)
            tb = _bare(target)
            found = paths.get(tb) or []
            if tb == src:
                continue
            why = None
            if len(found) != 1:
                why = (f"two relationship paths of the same length reach {target}"
                       if len(found) > 1 else
                       "; ".join(dict.fromkeys(blocked.get(tb) or []))
                       or "no declared relationship reaches it")
            elif any(st.distinct for st in found[0]):
                why = (f"a relationship on the way to {target} does not end on one row, so "
                       f"joining it would repeat the measure's rows")
            if why:
                plan.unplaced.setdefault((col, tb), why)
                continue
            prev = alias
            for n in range(1, len(found[0]) + 1):
                key = tuple(s.key for s in found[0][:n])
                step = found[0][n - 1]
                if key not in alias_of_path:
                    a = f"j{len(alias_of_path) + 1}"
                    alias_of_path[key] = a
                    hops.append((a, step, prev))
                prev = alias_of_path[key]
            _need(prev, col)
            plan.measure_at[(col, tb)] = prev
            continue
        target, why = _locate(col, hint, grouping)
        if why is not None:
            plan.unreachable.add(col)
            if why and why not in plan.reasons:
                plan.reasons.append(why)
            continue
        if target is None:
            # On the statement's own table. Recorded, so a same-named column another request
            # reads on a joined table does not take this one's place.
            plan._at[(col, _bare(hint))] = alias
            plan._at.setdefault((col, ""), alias)
            continue
        tb = _bare(target)
        found = paths.get(tb) or []
        if len(found) != 1:
            if len(found) > 1:
                why = (f"two relationship paths of the same length reach {target} from "
                       f"{source} ({' / '.join(' -> '.join(s.name for s in p) for p in found)})"
                       f"; which one the model means is not knowable here")
            elif blocked.get(tb):
                why = (f"{target} is reached only through "
                       + "; ".join(dict.fromkeys(blocked[tb])))
            else:
                why = ""
            # A table no relationship of the model links to the statement's table at all
            # (`whole_model`: the graph is every relationship the model has) filters nothing
            # either: seen live, a target table beside the fact lists its members beside
            # every row, and a filter on the fact leaves the target's measure as it is.
            unlinked = whole_model and tb not in relaxed
            if (not found and tb not in hard
                    and (unlinked or (tb in relaxed and (tb in soft or not blocked.get(tb))))):
                # The relationships exist and filter nothing towards these rows (against
                # their direction, or inactive): the column is listed beside them, not
                # joined (`_detached_statement`).
                plan.detached[col] = (str(target), hint)
                continue
            # A filter too: read off the statement's table under the same name, it would
            # narrow a different column, or none.
            plan.unreachable.add(col)
            if why and why not in plan.reasons:
                plan.reasons.append(why)
            continue
        prev = alias
        for n in range(1, len(found[0]) + 1):
            key = tuple(s.key for s in found[0][:n])
            step = found[0][n - 1]
            structural.add(key)
            if key not in alias_of_path:
                a = f"j{len(alias_of_path) + 1}"
                alias_of_path[key] = a
                hops.append((a, step, prev))
            prev = alias_of_path[key]
        orig = next((g for g in gb if str(g).lower() == col), col)
        _need(prev, orig if grouping else col)
        plan._at[(col, _bare(hint))] = prev
        plan._at.setdefault((col, ""), prev)

    for a, step, prev in hops:
        for sc, dc in step.pairs:
            _need(a, dc)
            # The key the hop leaves from: a table read as its distinct rows must carry it
            # for the next table to join on.
            if prev != alias:
                _need(prev, sc)
    key_of = {a: k for k, a in alias_of_path.items()}
    # A BRANCH: a hop onto an end that is not one row, along a relationship whose
    # multiplicity the model states (many-to-many, or one-to-many filtering both ways). The
    # tables from it on are read as ONE distinct set of (member of the table it leaves, the
    # columns needed beyond), filtered inside, and joined on the member: each row of the
    # statement's table is counted once per member however many paths lead to it.
    children: dict[str, list[str]] = {}
    for a, _step, prev in hops:
        children.setdefault(prev, []).append(a)
    in_branch: dict[str, str] = {}
    for a, step, prev in hops:
        fs, ts = _card_ends(step.edge.get("cardinality"))
        if (step.distinct and fs and ts and prev not in in_branch
                and key_of[a] in structural):
            todo = [a]
            while todo:
                cur = todo.pop()
                in_branch[cur] = a
                todo += children.get(cur, [])
            near = fs if step.forward else ts
            plan.branches[a] = {"step": step, "prev": prev, "near_one": near == "one",
                                "aliases": [], "clauses": []}
    for a, step, prev in hops:
        plan.keys[a] = [dc for _sc, dc in step.pairs]
        plan.table_at[a] = _bare(step.table)
        on = " AND ".join(f"{a}.{dc} = {prev}.{sc}" for sc, dc in step.pairs)
        kw = {(True, True): "FULL OUTER JOIN", (True, False): "LEFT JOIN",
              (False, True): "RIGHT JOIN", (False, False): "INNER JOIN"}[
            (step.keep, step.keep_far)]
        if key_of[a] not in structural:
            kw = "LEFT JOIN"                # read by a measure only: changes no row
        root = in_branch.get(a)
        if root is not None:
            plan.branches[root]["aliases"].append(a)
            if root == a:
                plan.clauses.append(f"__BRANCH_{a}__")
            else:
                # Inside the branch every hop is a plain join: the distinct set it is read
                # as already counts each member once. A hop to a ONE end keeps a row with no
                # match (LEFT JOIN: its NULL is that table's blank member).
                clause = f"LEFT JOIN {step.table} {a} ON {on}"
                if step.distinct:
                    clause = _branch_many_hop(step, a, prev, on, root,
                                              next(s for x, s, _p in hops if x == prev))
                plan.branches[root]["clauses"].append(clause)
            continue
        plan.full = plan.full or step.keep_far
        if step.distinct:
            body = ", ".join(needs.get(a, []))
            plan.clauses.append(f"{kw} (\n    SELECT DISTINCT {body}\n    FROM   {step.table}"
                                f"\n  ) {a} ON {on}")
        else:
            plan.clauses.append(f"{kw} {step.table} {a} ON {on}")
    if plan.branches:
        plan.graph = graph
    return plan


def _expanded_tokens(formula: str, measures: dict[str, str] | None,
                     seen: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """The tokens of a DAX formula with every measure it references expanded in place: what
    the formula reads, all the way down. Only for LOOKING (which tables, which
    relationships); compiling inlines measures itself."""
    try:
        toks = tokenize(formula or "")
    except CompileError:
        return []
    out: list[tuple[str, str]] = []
    for k, v in toks:
        key = _column_ref(v).lower() if k == "col" else None
        if key and measures and key in measures and key not in seen:
            out += _expanded_tokens(measures[key], measures, seen + (key,))
        else:
            out.append((k, v))
    return out


def _edge_key(e: dict) -> tuple:
    return (_bare(e.get("from_table")), str(e.get("from_column") or "").lower(),
            _bare(e.get("to_table")), str(e.get("to_column") or "").lower())


def _measure_graph(unit: dict, source: str, measures: dict[str, str] | None,
                   table_of: dict[str, str] | None, model_graph: list[dict] | None
                   ) -> tuple[list[dict], list[tuple[str, str]], set, bool]:
    """The relationships a statement may join along, the columns its DAX measures read on
    other tables, the relationship changes its measures make, and whether its measures need
    statements of their own.

    The graph is the unit's own plus the model's, on warehouse tables. A measure that is a
    CALCULATE with USERELATIONSHIP(a, b) joins along that relationship instead of the active
    one between the same two tables; CROSSFILTER(a, b, Both | None | OneWay) makes that
    relationship filter both ways, not at all, or one way. When every measure makes the same
    changes they apply to the whole statement. When they differ, each set of measures is a
    statement of its own (`_joined_statements`), except a USERELATIONSHIP whose far table the
    visual reads nothing of: it changes no row, and the measures share one statement."""
    tmap = {k.lower(): v for k, v in (table_of or {}).items()}

    def phys(name: Any) -> Any:
        return tmap.get(str(name or "").lower(), name)

    graph: list[dict] = []
    seen: set[tuple] = set()
    for e in list(unit.get("join_graph") or []) + list(model_graph or []):
        e = {**e, "from_table": phys(e.get("from_table")), "to_table": phys(e.get("to_table"))}
        if _edge_key(e) in seen:
            continue
        seen.add(_edge_key(e))
        graph.append(e)
    src = _bare(source)
    extra: list[tuple[str, str]] = []
    specs = [_relationship_spec(m, measures, tmap, src, extra)
             for m in unit.get("measures") or []]
    named = {x for x in specs if x}
    if not named:
        return graph, extra, set(), False
    used = {k for sp in named for k in _spec_keys(sp)}
    if len(named) == 1 and all(x == next(iter(named)) for x in specs):
        return _apply_spec(graph, next(iter(named))), extra, used, False
    spec = next(iter(named))
    if len(named) == 1 and all(k[0] == "use" for k in spec):
        ends = set()
        for k in spec:
            hit = _spec_edge(graph, k[1])
            if hit is not None:
                ends |= {_bare(graph[hit].get("from_table")), _bare(graph[hit].get("to_table"))}
        read = {_bare(t) for t in (unit.get("warehouse_tables") or [])[1:]}
        read |= {_bare(t) for t in unit.get("group_by_tables") or [] if t}
        read |= {_bare(f.get("table")) for f in unit.get("filters") or [] if f.get("table")}
        if not ((ends - {src}) & read):
            return graph, extra, used, False
    return graph, extra, used, True


def _measure_homes(unit: dict, source: str, measures: dict[str, str] | None,
                   table_of: dict[str, str] | None) -> dict[str, str]:
    """Each DAX measure's own table: the statement's table when its formula reads it (or no
    table at all), else the one other warehouse table it reads, when there is exactly one.
    A measure reading several other tables keeps the statement's table (and is refused by
    name there, as before)."""
    tmap = {k.lower(): v for k, v in (table_of or {}).items()}
    src = _bare(source)
    out: dict[str, str] = {}
    if not tmap:
        return out
    for m in unit.get("measures") or []:
        if m.get("formula_language") != "dax" or not m.get("formula"):
            continue
        # A context test of another table (SELECTEDVALUE(Article[Brand])) reads none of its
        # rows: the measure's own table is the one it aggregates. A measure that aggregates
        # no table at all (ISFILTERED(Customer[City]) alone) is a statement over the table
        # its test names: on any other, a grouping column that does not filter that table
        # is listed beside the statement, out of the context the test reads.
        expanded = _expanded_tokens(m["formula"], measures)
        named: dict[str, str] = {}
        for toks in (_row_tokens(expanded), expanded):
            for k, v in toks:
                t = None
                if k in ("qualified", "quoted_qualified"):
                    t = _table_ref(v)
                elif k == "str" and v.startswith("'"):
                    t = v[1:-1].replace("''", "'")
                elif k == "func" and v.lower() in tmap:
                    t = v
                phys = tmap.get(str(t or "").lower())
                if phys:
                    named[_bare(phys)] = phys
            if named:
                break
        if not named or src in named:
            continue
        if len(named) == 1:
            out[m["name"]] = next(iter(named.values()))
    return out


def _spec_keys(spec: frozenset) -> set:
    """The relationship keys (frozenset of (column, table) ends) a spec names."""
    return {k[1] for k in spec}


def _relationship_spec(m: dict, measures: dict[str, str] | None, tmap: dict, src: str,
                       extra: list) -> frozenset | None:
    """The relationship changes one DAX measure makes: {("use", ends)} for USERELATIONSHIP,
    {("cross", ends, direction)} for CROSSFILTER. None for a measure that is not DAX; empty
    for one that changes nothing. Also appends to `extra` the columns it reads on other
    tables."""
    if m.get("formula_language") != "dax" or not m.get("formula"):
        return None
    toks = _expanded_tokens(m["formula"], measures)
    out = set()
    for i, (k, v) in enumerate(toks):
        if k in ("qualified", "quoted_qualified"):
            t = _table_ref(v)
            physical = tmap.get(str(t).lower()) if t else None
            if physical and _bare(physical) != src:
                req = (_column_ref(v).lower(), physical)
                if req not in extra:
                    extra.append(req)
        up = v.upper() if k == "func" else ""
        if up in ("USERELATIONSHIP", "CROSSFILTER") and i + 5 < len(toks) \
                and toks[i + 1][0] == "lparen" and toks[i + 3][0] == "comma":
            ends = []
            for tk in (toks[i + 2], toks[i + 4]):
                t = _table_ref(tk[1]) if tk[0] != "col" else None
                ends.append((_column_ref(tk[1]).lower(), _bare(tmap.get(str(t).lower(), t))))
            if up == "USERELATIONSHIP":
                out.add(("use", frozenset(ends)))
            elif i + 6 < len(toks) and toks[i + 5][0] == "comma":
                out.add(("cross", frozenset(ends), str(toks[i + 6][1]).strip("'\"").lower()))
    return frozenset(out)


def _spec_edge(graph: list[dict], ends: frozenset) -> int | None:
    return next((i for i, e in enumerate(graph)
                 if frozenset({(str(e.get("from_column") or "").lower(), _bare(e.get("from_table"))),
                               (str(e.get("to_column") or "").lower(), _bare(e.get("to_table")))})
                 == ends), None)


def _apply_spec(graph: list[dict], spec: frozenset) -> list[dict]:
    """The graph a measure's relationship changes leave: USERELATIONSHIP activates its
    relationship and deactivates the others between the same two tables; CROSSFILTER Both
    filters both ways, None filters nothing (the relationship is not walked), OneWay one way."""
    out = [dict(e) for e in graph]
    for k in sorted(spec, key=lambda x: x[0] != "use"):
        hit = _spec_edge(out, k[1])
        if hit is None:
            continue
        if k[0] == "use":
            ends = {_bare(out[hit].get("from_table")), _bare(out[hit].get("to_table"))}
            for i, e in enumerate(out):
                if i == hit:
                    e["is_active"] = True
                elif {_bare(e.get("from_table")), _bare(e.get("to_table"))} == ends:
                    e["is_active"] = False
        else:
            how = k[2]
            if how == "none":
                out[hit]["is_active"] = False
            elif how == "both":
                out[hit]["cross_filter"] = "both"
            elif how.startswith("oneway"):
                out[hit]["cross_filter"] = "single"
    return out


_DATE_TYPES = re.compile(r"(?i)^(date|datetime|timestamp)")


def _date_keys(graph: list[dict], source: str, types: dict[tuple[str, str], str]
               ) -> dict[tuple[str, str], dict]:
    """(date column, date table) -> how the statement's table reaches it: every active
    relationship from the measure's table to a table whose ONE end is that column. A column
    the model types as other than a date is not one."""
    src = _bare(source)
    out: dict[tuple[str, str], dict] = {}
    for e in graph:
        if e.get("is_active") is False or str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        fs, ts = _card_ends(e.get("cardinality"))
        for near, far, ncol, fcol, far_end in (
                (e.get("from_table"), e.get("to_table"), e.get("from_column"),
                 e.get("to_column"), ts),
                (e.get("to_table"), e.get("from_table"), e.get("to_column"),
                 e.get("from_column"), fs)):
            if _bare(near) != src or far_end != "one" or _bare(far) == src:
                continue
            dtype = types.get((_bare(far), str(fcol).lower())) \
                or types.get((str(far).lower(), str(fcol).lower()))
            if dtype and not _DATE_TYPES.match(dtype):
                continue
            out[(str(fcol).lower(), _bare(far))] = {
                "table": far, "column": str(fcol), "fact_column": str(ncol)}
    return out


def _keyed_date(ctx: "_UnitContext", col: tuple[str, str],
                table_of: dict[str, str] | None = None) -> dict | None:
    """A date column of a date table the statement's table reaches on ANOTHER key (an integer
    `Date Sk` beside the table's `Calendar Date`), registered as a date column of its own
    (`ctx.date_keys`): a date set is then computed over the date column's values and read
    through the key (`key_column`), each date to its key row. None where the column is not
    a date, or no single relationship reaches its table."""
    column, table = col
    # The model names its tables one way, the warehouse another: a type is looked up by both.
    names = {table} | {k.lower() for k, v in (table_of or {}).items() if _bare(v) == table}
    dtype = next((ty for (t, c), ty in ctx.types.items()
                  if (t in names or _bare(t) in names) and c == column), None)
    if not dtype or not _DATE_TYPES.match(dtype):
        return None
    key = _lookup_key(ctx, table)
    if key is None:
        return None
    via = ctx.lookup_keys[key]
    info = {"table": via["table"], "column": ctx.renames.get((_bare(table), column), column),
            "fact_column": via["fact_column"], "key_column": via["column"]}
    ctx.date_keys[col] = info
    return info


def _lookup_key(ctx: "_UnitContext", table: str) -> tuple[str, str] | None:
    """The key a lookup date set (`_Parser._lookup_dates`) reaches `table` (bare) on: the one
    active relationship from the statement's table whose ONE end is on `table`, any data type.
    Registered in `ctx.lookup_keys`, so the date-set statement joins on it. None when there is
    no such relationship, or more than one (which key the tool filters through is then the
    model's choice, not one this reads)."""
    src = ctx.source
    hits = {}
    for e in ctx.graph:
        if e.get("is_active") is False or str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        fs, ts = _card_ends(e.get("cardinality"))
        for near, far, ncol, fcol, far_end in (
                (e.get("from_table"), e.get("to_table"), e.get("from_column"),
                 e.get("to_column"), ts),
                (e.get("to_table"), e.get("from_table"), e.get("to_column"),
                 e.get("from_column"), fs)):
            if _bare(near) == src and far_end == "one" and _bare(far) == table:
                hits[(str(fcol).lower(), _bare(far))] = {
                    "table": far, "column": str(fcol), "fact_column": str(ncol)}
    if len(hits) != 1:
        return None
    key, info = next(iter(hits.items()))
    ctx.lookup_keys[key] = info
    return key


def _measure_elsewhere(m: dict, unit: dict, source: str,
                       columns: dict[str, set[str]]) -> str | None:
    """Why a shelf-aggregated column measure cannot be computed over `source`, or None.

    Only a measure with no formula (a column plus an aggregation), only when the schema
    evidence says the column is not on the statement's table and is on another of the unit's
    tables. A blend link names the post-aggregation join that table needs."""
    if m.get("formula") or not columns or not _columns_of(source, columns):
        return None
    col = str(m.get("name") or "").lower()
    if col in _columns_of(source, columns):
        return None
    homes = [t for t in unit.get("warehouse_tables") or []
             if _bare(t) != _bare(source) and col in _columns_of(t, columns)]
    if not homes:
        return None
    blend = any(str(e.get("kind")) == "blend" and {_bare(e.get("from_table")),
                                                   _bare(e.get("to_table"))}
                == {_bare(source), _bare(homes[0])} for e in unit.get("join_graph") or [])
    if blend:
        return (f"aggregates a column of {homes[0]}, which a data blend links to {source}: "
                f"a blend aggregates the secondary at the link grain and left-joins it after "
                f"aggregation, which is not compiled")
    return (f"aggregates a column of {homes[0]}, not of {source}: a statement over {source} "
            f"does not aggregate another table at that table's grain")


def _columns_of(table: str, columns: dict[str, set[str]]) -> set[str]:
    """What the parser resolved as reachable on a table. Absent evidence means the compiler
    cannot PLACE the column, which is a refusal rather than an assumption."""
    hit = columns.get(table)
    if hit is None:
        hit = next((v for k, v in columns.items() if _bare(k) == _bare(table)), None)
    return set(hit or ())


def _bare(table: Any) -> str:
    return str(table or "").split(".")[-1].lower()


def _strip_alias(pred: str, alias: str, to: str = "") -> str:
    """`pred` with every `alias.` qualifier removed (or replaced by `to`), OUTSIDE quotes
    only. A context filter runs inside a subquery that has no alias; a plain text replace
    also rewrote literals (`'gift.card'` lost its `t.`)."""
    out, i, n, q = [], 0, len(pred), None
    head = alias + "."
    while i < n:
        ch = pred[i]
        if q:
            out.append(ch)
            if ch == q:
                if i + 1 < n and pred[i + 1] == q:      # a doubled quote is an escape
                    out.append(pred[i + 1])
                    i += 1
                else:
                    q = None
            i += 1
            continue
        if ch in ("'", '"'):
            q = ch
            out.append(ch)
            i += 1
            continue
        if pred.startswith(head, i) and (i == 0 or not (pred[i - 1].isalnum()
                                                        or pred[i - 1] in "_.")):
            i += len(head)
            if to:
                out.append(to + ".")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# A filter that keeps the MEMBERS of a field passing a test over the population (a Top N by
# an expression, a condition): not a row predicate, compiled by the statement around it.
MEMBER_OPS = ("top_n", "condition")


_RELATIVE_PERIODS = ("day", "month", "quarter", "year")


def _relative_date(target: str, v: dict, rules, dialect: str | None, res: Result
                   ) -> tuple[str | None, str | None]:
    """A relative date filter as a range of whole periods: from the start of the period
    `first` periods from today (or from the anchor) to the end of the period `last` periods
    from it. A week starts on the value's `week_start`. "To date" (`include_future` false)
    also ends the range at today, or at the moment the query runs for a time column (the
    current time, read as the session's wall clock). The date arithmetic per dialect is rule
    data (`op.relative_date`). -> (predicate, None) or (None, why it is refused)."""
    period = str(v.get("period") or "").lower()
    if v.get("unknown"):
        return None, (f"a relative date filter with setting(s) {v['unknown']} the compiler "
                      f"does not read, which may move its window")
    rule = rules.operator("relative_date", dialect or "")
    emit = (rule or {}).get("emit") or {}
    d = str(dialect or "").lower()
    shift = (emit.get("shift") or {}).get(d)
    if rule is None or not shift:
        return None, f"no relative date rule for the {dialect or 'run'} dialect"
    start = str(v.get("week_start") or "").lower()
    iso = (emit.get("weekday_iso") or {}).get(d)
    if period == "week" and not (start in _ISO_DAY and iso):
        return None, ("a relative date filter by week whose first day of the week the file "
                      "does not state" if start not in _ISO_DAY else
                      f"a relative date filter by week, which has no weekday form for the "
                      f"{dialect or 'run'} dialect")
    if period not in _RELATIVE_PERIODS and period != "week":
        return None, (f"a relative date filter by {period or 'an unnamed period'}: only "
                      f"{', '.join(_RELATIVE_PERIODS + ('week',))} are compiled")
    # The time zone the BI tool reads the current moment in (the viewer file's): "today" and
    # "now" are both read there, so the window's periods and a "to date" end agree with what
    # the tool shows across the warehouse session's midnight.
    tz, zoned = v.get("time_zone"), None
    if tz:
        if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+)*", str(tz)) or len(str(tz)) > 64:
            return None, f"a relative date filter in a time zone {tz!r} that is not one"
        zoned = (emit.get("now_in_zone") or {}).get(d)
        if not zoned:
            return None, (f"a relative date filter in the time zone {tz}, which has no form "
                          f"for the {dialect or 'run'} dialect")
        names = (emit.get("zone_names") or {}).get(d)
        if names is not None:
            if str(tz) not in names:
                return None, (f"a relative date filter in the time zone {tz}, which the "
                              f"{dialect} dialect names by its own names, and the rule lists "
                              f"no name for it")
            tz = names[str(tz)]
        zoned = zoned.replace("{tz}", str(tz))
        res.rules_used.append("engine.relative_date_time_zone")
    if v.get("anchor"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v["anchor"])):
            return None, f"a relative date anchor {v['anchor']!r} that is not a date"
        anchor = f"CAST('{v['anchor']}' AS DATE)"
    else:
        # Today as the dialect spells it (`today`), else the standard CURRENT_DATE.
        anchor = (f"CAST({zoned} AS DATE)" if zoned
                  else (emit.get("today") or {}).get(d) or "CURRENT_DATE")
    # The start of a period (`trunc`, {p} the period, {d} the date), else DATE_TRUNC.
    cut = (emit.get("trunc") or {}).get(d) or "DATE_TRUNC('{p}', {d})"

    def trunc(expr: str) -> str:
        if period != "week":
            return cut.format(p=period, d=expr)
        # The week's first day: the day moved back to the weekday the data source starts on.
        return shift.format(p="day", n="-" + _mod(f"{iso.format(expr)} - {_ISO_DAY[start]} + 7",
                                                  "7", dialect),
                            d=expr)

    def at(n: int) -> str:
        return trunc(shift.format(p=period, n=int(n), d=anchor) if n else anchor)
    res.rules_used.append(rule["id"])
    pred = f"{target} >= {at(int(v['first']))} AND {target} < {at(int(v['last']) + 1)}"
    if not v.get("include_future", True):
        # "To date": nothing after today (the anchor), or after now on a time column.
        # The current moment in the time zone the BI tool reads it in (the viewer's), else
        # the session's wall clock.
        now = zoned or (emit.get("now") or {}).get(d)
        if not now:
            return None, (f"a relative date filter to date, which has no current-time form "
                          f"for the {dialect or 'run'} dialect")
        end = (f"{shift.format(p='day', n=1, d=anchor)}" if v.get("anchor") else now)
        pred += (f" AND {target} < {end}" if v.get("anchor") else f" AND {target} <= {end}")
        res.rules_used.append("engine.relative_date_to_date")
    pred = f"({pred})"
    if v.get("include_null"):
        pred = f"({pred} OR {target} IS NULL)"
    return pred, None


# Tests on a column's text (the common format's `contains` family), each an operator row.
_TEXT_OPS = {"contains": "contains", "not_contains": "contains", "starts_with": "starts_with",
             "not_starts_with": "starts_with"}
# The comparison of each op, for deciding whether an empty cell's stand-in (`null_as`) passes.
_CMP = {"eq": lambda a, b: a == b, "gt": lambda a, b: a > b, "lt": lambda a, b: a < b,
        "neq": lambda a, b: a != b}
_DATE_LIT = re.compile(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?")
_NUM_LIT = re.compile(r"-?\d+(\.\d+)?")


def _foldable(v) -> bool:
    """A filter value compared as text the model folds (case, trailing spaces): a string that
    is not a number or a date literal. A key column's numbers (`ORDER_ID IN (1, 2)`) and a
    date are compared as they are; folding them would compare a number as text."""
    return isinstance(v, str) and not _NUM_LIT.fullmatch(v) and not _DATE_LIT.fullmatch(v)


def _stand_in(literal: Any, null_as: dict):
    """(empty cell's stand-in, literal) as two comparable values, by the literal's type."""
    if isinstance(literal, bool):
        return None, None
    if isinstance(literal, (int, float)) or _NUM_LIT.fullmatch(str(literal)):
        n = null_as.get("number")
        return (None, None) if n is None else (float(n), float(literal))
    if _DATE_LIT.fullmatch(str(literal)):
        d = null_as.get("date")
        return (None, None) if d is None else (str(d), str(literal)[:10])
    t = null_as.get("text")
    return (None, None) if t is None else (str(t).casefold(), str(literal).casefold())


def _empty_passes(op: str, value: Any, null_as: dict | None) -> bool:
    """Whether an EMPTY cell passes the comparison, with the empty value read as the
    filter's stand-in (`null_as`, the tool's comparison rule). False without one."""
    if not null_as or value is None:
        return False
    if op in ("range", "lte") and isinstance(value, dict):
        ok = True
        for bound, cmp in (("min", lambda a, b: a >= b), ("max", lambda a, b: a <= b)):
            if value.get(bound) is None:
                continue
            a, b = _stand_in(value[bound], null_as)
            ok = ok and a is not None and cmp(a, b)
        return ok
    if op in _CMP and not isinstance(value, (list, dict)):
        a, b = _stand_in(value, null_as)
        return a is not None and _CMP[op](a, b)
    return False


def _test_pred(target: str, op: str, value: Any, f: dict, rules, dialect: str | None,
               res: "Result", text_key: bool = False, force: bool = False
               ) -> tuple[str | None, str | None]:
    """The predicates of the common format's tests that the generic path does not write:
    a membership with the EMPTY member (a JSON null in the list), the text tests, `any_of` /
    `all_of`, and a comparison an empty cell passes under the filter's `null_as`.
    -> (predicate, None); (None, why) when it cannot be written; (None, None) when the
    generic path writes it (unless `force`, which writes every comparison here)."""
    null_as = f.get("null_as")
    if op in ("any_of", "all_of"):
        if not isinstance(value, list) or not value:
            return None, f"{op} with no tests"
        parts = []
        for part in value:
            if not isinstance(part, dict) or part.get("value") is None:
                return None, f"a part of {op} with no value"
            pred, why = _test_pred(target, str(part.get("op") or ""), part["value"], f,
                                   rules, dialect, res, text_key, force=True)
            if pred is None:
                return None, why or f"a part of {op} ({part.get('op')}) has no rule"
            parts.append(pred)
        res.rules_used.append(f"engine.{op}")
        return "(" + (" OR " if op == "any_of" else " AND ").join(parts) + ")", None
    if op in _TEXT_OPS:
        rule = rules.operator(_TEXT_OPS[op], dialect or "")
        if rule is None:
            return None, f"no operator rule for {_TEXT_OPS[op]}"
        res.rules_used.append(rule["id"])
        lit = str(value).upper() if text_key else str(value)
        col = _Parser._text_key(target) if text_key else target
        pred = _apply(_emit_for(rule, dialect), [col, _literal(lit, dialect)], op)
        # An empty cell holds no text: it fails the test, and passes its negation.
        return ((f"NOT COALESCE(({pred}), FALSE)" if op.startswith("not_") else f"({pred})"),
                None)
    if op in ("is_null", "not_null"):
        if not force:
            return None, None
        return f"{target} IS {'NOT ' if op == 'not_null' else ''}NULL", None
    if op in ("in", "not_in") or (op in ("eq", "filter") and isinstance(value, list)):
        vals = value if isinstance(value, list) else [value]
        if None not in vals and not force:
            return None, None
        rest = [v for v in vals if v is not None]
        keyed = text_key and rest and all(_foldable(v) for v in rest)
        col = _Parser._text_key(target) if keyed else target
        lits = _literal_list([v.upper() for v in rest] if keyed else rest, dialect)
        if op == "not_in":
            res.rules_used.append("engine.empty_member")
            if not rest:
                return f"{target} IS NOT NULL", None
            if None in vals:
                return f"({target} IS NOT NULL AND {col} NOT IN ({lits}))", None
            return f"({target} IS NULL OR {col} NOT IN ({lits}))", None
        if not rest:
            return f"{target} IS NULL", None
        member = f"{col} IN ({lits})" if len(rest) > 1 else f"{col} = {lits}"
        if None in vals:
            res.rules_used.append("engine.empty_member")
            return f"({target} IS NULL OR {member})", None
        return member, None
    if op in ("eq", "filter", "gt", "lt", "neq", "lte", "range"):
        op = "eq" if op == "filter" else op
        passes = _empty_passes(op, value, null_as)
        if not passes and not force:
            return None, None
        if isinstance(value, dict):
            preds = []
            for bound, row in (("min", "range"), ("max", "lte")):
                if value.get(bound) is None:
                    continue
                rule = rules.operator(row)
                if rule is None:
                    return None, f"no operator rule for {row}"
                res.rules_used.append(rule["id"])
                preds.append(_apply(_emit_for(rule, dialect),
                                    [target, _literal(value[bound], dialect)], row))
            if not preds:
                return None, "a range with no bound"
            pred = " AND ".join(preds)
        else:
            rule = rules.operator(op, dialect or "")
            if rule is None:
                return None, f"no operator rule for {op}"
            res.rules_used.append(rule["id"])
            if text_key and op in ("eq", "neq") and isinstance(value, str) \
                    and not _NUM_LIT.fullmatch(value) and not _DATE_LIT.fullmatch(value):
                pred = _apply(_emit_for(rule, dialect), [_Parser._text_key(target),
                                                       _literal(value.upper(), dialect)], op)
            else:
                pred = _apply(_emit_for(rule, dialect), [target, _literal(value, dialect)], op)
        if passes:
            # An empty cell compares as its stand-in and passes: the rows with no value stay.
            res.rules_used.append("engine.empty_compares_as")
            return f"({pred} OR {target} IS NULL)", None
        return (f"({pred})" if " AND " in pred else pred), None
    return None, None


def _tuple_pred(f: dict, rules, dialect: str | None, res: "Result", text_key: bool,
                target_of) -> tuple[str | None, str | None]:
    """A `tuple` filter -> an OR of one AND per selected combination ((c1 = a AND c2 = b)
    OR ...), a NULL member as IS NULL; `not` (the value's `negated`) keeps the rows matching
    none. -> (predicate, None) or (None, why)."""
    v = f["value"]
    cols = _tuple_columns(f)
    rows = [r for r in v.get("rows") or [] if isinstance(r, list)]
    if not cols or not rows or any(len(r) != len(cols) for r in rows):
        return None, (f"a filter over {len(cols)} column(s) together whose selected "
                      f"combinations do not each name every column")
    rule = rules.operator("eq", dialect or "")
    if rule is None:
        return None, "no operator rule for eq"
    res.rules_used.append(rule["id"])
    targets = [target_of(c["column"], c.get("table")) for c in cols]
    alts = []
    for r in rows:
        parts = []
        for tgt, val in zip(targets, r):
            if val is None:
                parts.append(f"{tgt} IS NULL")
            elif text_key and isinstance(val, str) and not _NUM_LIT.fullmatch(val) \
                    and not _DATE_LIT.fullmatch(val):
                parts.append(_apply(_emit_for(rule, dialect), [_Parser._text_key(tgt),
                                                             _literal(val.upper(), dialect)],
                                    "eq"))
            else:
                parts.append(_apply(_emit_for(rule, dialect), [tgt, _literal(val, dialect)],
                                    "eq"))
        alts.append("(" + " AND ".join(parts) + ")")
    res.rules_used.append("engine.tuple_filter")
    pred = "(" + "\n       OR ".join(alts) + ")"
    if v.get("negated"):
        pred = f"NOT COALESCE({pred}, FALSE)"
    return pred, None


def _relative_range(target: str, v: dict, rules, dialect: str | None, res: "Result"
                    ) -> tuple[str | None, str | None]:
    """A range whose bounds are computed from the moment the query runs (in UTC): each bound
    a list of steps from now, a move (`add`) or a truncation to the start of a unit (`span`).
    A lower bound is `>=`; an upper bound ending in a span covers that span to its end
    (`<` the start of the next one), else `<=`. Per-dialect SQL is rule data
    (`op.relative_range`). -> (predicate, None) or (None, why)."""
    rule = rules.operator("relative_range", dialect or "")
    emit = (rule or {}).get("emit") or {}
    d = str(dialect or "").lower()
    now, shift, trunc = ((emit.get(k) or {}).get(d) for k in ("now", "shift", "trunc"))
    if rule is None or not (now and shift and trunc):
        return None, f"no relative range rule for the {dialect or 'run'} dialect"
    units = set(emit.get("units") or ())
    # A WEEK starts on the day the filter states (`week_start`, the reader's word for the
    # tool's rule): its start is the day truncation moved back to that weekday, the ISO day
    # number per dialect being rule data (`weekday_iso`).
    start = str(v.get("week_start") or "").lower()
    iso = (emit.get("weekday_iso") or {}).get(d)

    def week_start(expr: str) -> str:
        day = trunc.format(p="day", d=expr)
        back = "-" + _mod(f"{iso.format(day)} - {_ISO_DAY[start]} + 7", "7", dialect)
        return shift.format(p="day", n=back, d=day)
    preds = []
    for side in ("lower", "upper"):
        b = v.get(side)
        if b is None:
            continue
        expr, last = now, None
        for st in (b or {}).get("steps") or []:
            unit = str(st[-1]).lower()
            if unit == "week" and st[0] == "span" and not (start in _ISO_DAY and iso):
                return None, ("a relative date bound cut to a week, whose first day the "
                              "filter does not state" if start not in _ISO_DAY else
                              f"a relative date bound cut to a week, which has no weekday "
                              f"form for the {dialect or 'run'} dialect")
            if unit not in units and unit != "week":
                return None, (f"a relative date or time bound moved or cut by {unit}: only "
                              f"{', '.join(sorted(units | {'week'}))} are compiled")
            if st[0] == "add":
                expr = shift.format(p=unit, n=int(st[1]), d=expr)
            elif unit == "week":
                expr = week_start(expr)
            else:
                expr = trunc.format(p=unit, d=expr)
            last = st
        if side == "lower":
            preds.append(f"{target} >= {expr}")
        elif last is not None and last[0] == "span":
            preds.append(f"{target} < {shift.format(p=str(last[-1]).lower(), n=1, d=expr)}")
        else:
            preds.append(f"{target} <= {expr}")
    if not preds:
        return None, "a relative range with no bound"
    res.rules_used.append(rule["id"])
    return "(" + " AND ".join(preds) + ")", None


_AGG_SQL = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN|STDDEV\w*|VAR\w*)\s*\(", re.I)
_READER_DIALECTS = (yaml.safe_load((pathlib.Path(__file__).parent.parent / "data"
                                     / "reader_dialects.yml").read_text()) or {})
# Dialects with no `NULLS LAST` in an ORDER BY (`data/reader_dialects.yml`).
_NO_NULLS_LAST = frozenset(_READER_DIALECTS.get("no_nulls_last") or ())
# The remainder of a whole-number division, where a dialect does not spell it MOD(a, b).
_MODULO = {str(k).lower(): str(v) for k, v in (_READER_DIALECTS.get("modulo") or {}).items()}


def _sort_term(expr: str, direction: str, nulls: str, dialect: str | None) -> str:
    """One ORDER BY term with its empty values FIRST or LAST. A dialect with no NULLS FIRST /
    LAST (`no_nulls_last` in data/reader_dialects.yml) sorts on a key that is 0 for the end
    the empty values go to, then on the value; a constant (no column to order by) is
    `(SELECT NULL)`, the only constant such a dialect's window takes."""
    if str(dialect or "").lower() not in _NO_NULLS_LAST:
        return f"{expr} {direction} NULLS {nulls}"
    if re.fullmatch(r"\d+", expr.strip()):
        return "(SELECT NULL)"
    first = nulls.upper() == "FIRST"
    return (f"CASE WHEN {expr} IS NULL THEN {0 if first else 1} ELSE {1 if first else 0} END, "
            f"{expr} {direction}")


# The first n rows of an ordered statement, where a dialect does not spell it LIMIT n.
_LIMIT = {str(k).lower(): str(v) for k, v in (_READER_DIALECTS.get("limit") or {}).items()}


def _limit(n: int, dialect: str | None) -> str:
    """`LIMIT n`, or the dialect's own form (`limit` in data/reader_dialects.yml) after an
    ORDER BY."""
    return _LIMIT.get(str(dialect or "").lower(), "LIMIT {n}").format(n=int(n))


def _mod(x: str, m: str, dialect: str | None) -> str:
    """`x` modulo `m` as the dialect spells it (`modulo` in data/reader_dialects.yml), else
    MOD(x, m). The sign is the dividend's in every dialect listed, as MOD's is."""
    return _MODULO.get(str(dialect or "").lower(), "MOD({0}, {1})").format(x, m)


def _member_filters(members: list[dict], unit: dict, rules, alias: str, dialect: str,
                    res: Result, *, src: str, plan, measures, calc, calc_language,
                    dim_preds: list[str], joins: list[str] | tuple = (),
                    compile_by=None, limit_ok: bool = True) -> tuple[list[str], bool]:
    """Filters that keep the MEMBERS of a field passing a test, as row predicates:
    (predicates, whether a Top N among them replaces the statement's LIMIT).

    A Top N by an expression and a condition on an aggregate are computed over the
    population the data source and context filters leave, independently of the other
    dimension filters, and then intersected with them: the field's value is in the members
    a subquery over that population keeps (a NULL member included, which `IN` alone would
    drop). Ranking inside the filtered rows instead answers the context-filter question. A
    Top N on the one field the sheet groups by, with nothing else filtering, is the same
    rows as the statement's own ORDER BY … LIMIT and is left to it. A condition with no
    aggregate is a test of each row's own value.

    A Top N whose `population` is `filtered` ranks the members within EVERY other filter of
    the view (the statement's own rows, its joins and its row predicates), by the ranking
    measure as the statement computes it (`compile_by`); `empty_members: excluded` leaves out
    a member whose measure is empty, and `ties` keeps every member tied with the Nth (a rank,
    not a row count)."""
    if not members:
        return [], False
    if not calc_language:
        raise CompileError(
            f"filter(s) on {sorted({str(f['column']) for f in members})} keep the members an "
            f"expression selects, and the unit names no language to compile it in")
    gb = [str(g).lower() for g in unit.get("group_by") or []]
    preds: list[str] = []
    top_done = False

    def compile_over(expr: str, on: str) -> str:
        p = _Parser(_tokens(expr, calc_language), rules, calc_language, on, dialect, None,
                    measures, None, ())
        sql = p.parse()
        res.rules_used += p.used
        res.provisional_rules += p.provisional
        res.unknown += p.unknown
        return sql

    for f in members:
        col, op, v = str(f["column"]), str(f["op"]).lower(), f["value"]
        what = "Top N" if op == "top_n" else "condition"
        key_t = calc(col, f.get("table")) or f"{plan.at(col, f.get('table'))}.{col}"
        if op == "top_n" and str(v.get("population") or "") == "filtered":
            preds.append(_ranked_members(f, key_t, v, alias, dialect, res, src=src,
                                         joins=joins, dim_preds=dim_preds,
                                         compile_by=compile_by))
            top_done = top_done or bool(unit.get("top_n"))
            continue
        if re.search(r"\bj\d+\.", key_t):
            # A column of a joined table: its members ranked (or tested) over the statement's
            # own rows through the same joins, before the view's dimension filters (a Top N
            # or a condition is computed over every row that reaches the members).
            if op == "top_n":
                # Its members are the joined table's: a fact row that reaches no row of it
                # (the blank member the view shows for it) is not ranked (checked live: the
                # bottom department by quantity is a department, never that blank).
                held = _joined_row_test(key_t, joins)
                preds.append(_ranked_members(f, key_t, v, alias, dialect, res, src=src,
                                             joins=joins, dim_preds=[held] if held else [],
                                             compile_by=compile_by))
                top_done = top_done or bool(unit.get("top_n"))
                continue
            preds.append(_tested_members(f, key_t, v, alias, res, src=src, joins=joins,
                                         compile_by=compile_by))
            continue
        key_m = _strip_alias(key_t, alias, "m")
        try:
            if op == "top_n":
                by = compile_over(str(v["by"]), "m")
                n = int(v["n"])
                if (unit.get("top_n") == n and gb == [col.lower()] and not dim_preds
                        and len(members) == 1 and limit_ok):
                    continue        # the statement's own ORDER BY … LIMIT is these rows
                order = _sort_term(by, "ASC" if v.get("direction") == "asc" else "DESC",
                                   "LAST", dialect)
                sub = (f"      SELECT {key_m} AS member_key\n      FROM   {src} m\n"
                       f"      GROUP BY {key_m}\n      ORDER BY {order}\n"
                       f"      {_limit(n, dialect)}")
                res.rules_used.append("engine.top_n_members")
                top_done = top_done or bool(unit.get("top_n"))
            else:
                cond = compile_over(str(v["expression"]), "m")
                negate = bool(v.get("exclude"))
                if not _AGG_SQL.search(cond):
                    row = _strip_alias(cond, "m", alias)
                    preds.append(f"NOT COALESCE(({row}), FALSE)" if negate else f"({row})")
                    res.rules_used.append("engine.condition_rows")
                    continue
                test = f"NOT COALESCE(({cond}), FALSE)" if negate else f"({cond})"
                sub = (f"      SELECT {key_m} AS member_key\n      FROM   {src} m\n"
                       f"      GROUP BY {key_m}\n      HAVING {test}")
                res.rules_used.append("engine.condition_members")
        except CompileError as exc:
            raise CompileError(f"the {what} filter on {col!r} does not compile ({exc}); "
                               f"dropping it would return other members") from exc
        preds.append(f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q\n  )\n"
                     f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
                     f"    ) q WHERE q.member_key IS NULL\n  )))")
    return preds, top_done


def _joined_row_test(key_t: str, joins: list[str]) -> str | None:
    """A test that the row reached a row of the joined table `key_t` reads (`j<n>.col`): a
    column its join matches on is not NULL. None where the join names none."""
    m = re.match(r"^(j\d+)\.", key_t)
    if not m:
        return None
    for j in joins:
        if re.search(rf"\)\s*{m.group(1)}\s+ON\b|\s{m.group(1)}\s+ON\b", j):
            on = j.split(" ON ", 1)[-1]
            col = re.search(rf"\b{m.group(1)}\.(\w+)\b", on)
            if col:
                return f"{m.group(1)}.{col.group(1)} IS NOT NULL"
    return None


def _tested_members(f: dict, key_t: str, v: dict, alias: str, res: Result, *, src: str,
                    joins, compile_by) -> str:
    """The members of a joined table's column a condition filter keeps: the statement's own
    rows through its joins, grouped by the column, those whose aggregate passes (Exclude:
    those whose test is not true)."""
    col = str(f["column"])
    if compile_by is None:
        raise CompileError(f"the condition on {col!r} names no way to compile its expression")
    try:
        cond = compile_by(str(v["expression"]))
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        raise CompileError(f"the condition filter on {col!r} does not compile ({exc}); "
                           f"dropping it would return other members") from exc
    test = f"NOT COALESCE(({cond}), FALSE)" if v.get("exclude") else f"({cond})"
    if not _AGG_SQL.search(cond):
        res.rules_used.append("engine.condition_rows")
        return test
    sub = f"      SELECT {key_t} AS member_key\n      FROM   {src} {alias}"
    for j in joins:
        sub += "\n      " + j.replace("\n", "\n      ")
    sub += f"\n      GROUP BY {key_t}\n      HAVING {test}"
    res.rules_used.append("engine.condition_members")
    return (f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q\n  )\n"
            f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
            f"    ) q WHERE q.member_key IS NULL\n  )))")


def _ranked_members(f: dict, key_t: str, v: dict, alias: str, dialect: str, res: Result, *,
                    src: str, joins, dim_preds: list[str], compile_by) -> str:
    """The members a Top N keeps when it ranks within every other filter of the view: the
    statement's own rows grouped by the field, ranked by the measure, those ranked N or
    better kept (with ties when the filter says so; a member with an empty measure left out
    when it says so). A NULL member is kept by its own test: `IN` never matches a NULL."""
    col = str(f["column"])
    if compile_by is None:
        raise CompileError(f"the Top N on {col!r} names no way to compile its measure")
    try:
        by = compile_by(str(v["by"]))
        n = int(v["n"])
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        raise CompileError(f"the Top N filter on {col!r} does not compile ({exc}); dropping "
                           f"it would return other members") from exc
    if " OVER (" in by:
        raise CompileError(f"the Top N on {col!r} ranks by a window, which a ranking over "
                           f"the members does not compute")
    order = _sort_term(by, "ASC" if str(v.get("direction")).lower() == "asc" else "DESC",
                       "LAST", dialect)
    rank = "RANK()" if v.get("ties") else "ROW_NUMBER()"
    rows = [p for p in dim_preds if not _AGG_SQL.search(p)]
    sub = (f"      SELECT {key_t} AS member_key,\n"
           f"             {rank} OVER (ORDER BY {order}) AS member_rank\n"
           f"      FROM   {src} {alias}")
    for j in joins:
        sub += "\n      " + j.replace("\n", "\n      ")
    if rows:
        sub += "\n      WHERE  " + "\n        AND  ".join(rows)
    sub += f"\n      GROUP BY {key_t}"
    if str(v.get("empty_members") or "") == "excluded":
        sub += f"\n      HAVING {by} IS NOT NULL"
    res.rules_used.append("engine.top_n_ranked_members")
    return (f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q "
            f"WHERE q.member_rank <= {n}\n  )\n"
            f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
            f"    ) q WHERE q.member_key IS NULL AND q.member_rank <= {n}\n  )))")


def _plan_filters(unit: dict, rules, alias: str, res: Result,
                  colmap: dict[str, str] | None = None, resolve=None,
                  dialect: str | None = None, place=None, text_key: bool = False,
                  special: dict | None = None):
    """The unit's filters as predicates: (context, dimension, deferred). With `text_key` (a
    model that compares text without case and without a column's trailing spaces), a filter
    on text values compares the column's key with each value's.

    With `special`, a caller that places them itself receives the `datasource`-stage
    predicates (applied to every row before any context filter) in `special["datasource"]`
    and the member filters (`MEMBER_OPS`) in `special["members"]`; without it the former are
    context predicates and the latter are deferred (refused by name)."""
    ctx, dim, deferred = [], [], []
    for f in unit.get("filters", []):
        column = f.get("column")
        op = str(f.get("op") or "").lower()
        if op == "top_rank" and unit.get("top_n"):
            # The rank filter of a Top N, already recovered into `top_n`: emitting it as a
            # predicate yields `WHERE rank = NULL`, which runs and returns nothing.
            continue
        if op in MEMBER_OPS:
            if f.get("value") is None:
                deferred.append({**f, "_why": "no-value"})
            elif special is not None and colmap is None:
                special["members"].append(f)
            elif (special is not None and op == "top_n" and isinstance(f.get("value"), dict)
                  and str(f["value"].get("population") or "") == "filtered"
                  and str(column).lower() in colmap):
                # Over a native query: a Top N ranked within the view's own filters ranks by
                # the measure as the statement computes it (`compile_by`, which reads the
                # query's columns), so only the member column needs the query's spelling.
                # Other member filters compile their expression without that map: deferred.
                special["members"].append({**f, "column": _ident(colmap[str(column).lower()]),
                                           "table": None})
            else:
                deferred.append({**f, "_why": "member-filter"})
            continue
        if colmap is not None and f.get("value") is not None and (
                (f.get("stage") or "").lower() not in ("measure", "table-calc")):
            if str(column).lower() not in colmap:
                # A calc over the query's columns (Tableau custom SQL) resolves below; only a
                # name that is neither is deferred.
                if not (resolve and resolve(column, f.get("table"))):
                    deferred.append({**f, "_why": "not-a-column"})
                    continue
            else:
                column = _ident(colmap[str(column).lower()])
        stage = (f.get("stage") or "").lower()
        if (stage in ("measure", "table-calc") and f.get("value") is None
                and f.get("value_kind") == "absent"):
            # A filter card on a value with nothing set (seen in a service-saved report: an
            # `Advanced` filter on the visual's measure, no condition): it narrows nothing.
            continue
        if stage == "measure" and f.get("measure") and special is not None and colmap is None:
            # A test of a MEASURE's value on each row of the view: placed by the caller, as a
            # HAVING over the compiled measure.
            if f.get("value") is None:
                deferred.append({**f, "_why": "no-value"})
            else:
                special.setdefault("measure_tests", []).append(f)
            continue
        if stage in ("measure", "table-calc"):
            # A measure-stage filter is applied after aggregation. Skipping it would ship a
            # statement missing a filter the widget applies (it used to be skipped whenever
            # the sheet had a Top N, which silently dropped a date range), so it is named.
            deferred.append({**f, "_why": "measure-stage"})
            continue
        # Where this filter's predicates go: a data source filter's own list when the caller
        # places it, else the context tier for it and for a context filter; a filter of what
        # the view SHOWS (`display`) to its own list, applied after every calculation.
        if stage == "datasource" and special is not None:
            into = special["datasource"]
        elif stage == "display" and special is not None:
            into = special.setdefault("display", [])
        elif stage in ("context", "datasource"):
            into = ctx
        else:
            into = dim
        if op == "relative_date" and isinstance(f.get("value"), dict):
            on = place(column, f.get("table")) if place and colmap is None else alias
            target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
            pred, why = _relative_date(target, f["value"], rules, dialect, res)
            if why:
                res.untranslatable.append({"construct": "relative date filter", "why": why})
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if op in ("not_null", "is_null"):
            on = place(column, f.get("table")) if place and colmap is None else alias
            target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
            into.append(f"{target} IS {'NOT ' if op == 'not_null' else ''}NULL")
            res.rules_used.append("engine.null_filter")
            continue
        if op == "tuple" and isinstance(f.get("value"), dict):
            # Several columns tested together (a hierarchy slicer's selected paths): a row
            # passes when its columns equal one selected combination, an empty member read
            # as NULL. One column's members alone would keep combinations never selected.
            pred, why = _tuple_pred(f, rules, dialect, res, text_key,
                                    lambda c, t: ((resolve(c, t) if resolve else None)
                                                  or f"{(place(c, t) if place and colmap is None else alias)}.{c}"))
            if pred is None:
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if f.get("value") is None and f.get("value_kind") == "absent":
            continue        # a filter that narrows nothing (nothing selected): no predicate
        if f.get("value") is None or (isinstance(f.get("value"), dict)
                                      and op not in ("relative_range",)
                                      and f["value"].get("min") is None
                                      and f["value"].get("max") is None):
            # The widget applies this filter, but the file recorded no value for it. Dropping
            # it would compute a different number that still runs; it is named instead.
            deferred.append({**f, "_why": "no-value"})
            continue
        # The alias the column is read under: a joined table's for a column of another
        # table (`_plan_joins`), else the statement's own.
        on = place(column, f.get("table")) if place and colmap is None else alias
        target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
        if op == "relative_range" and isinstance(f["value"], dict):
            pred, why = _relative_range(target, f["value"], rules, dialect, res)
            if why:
                res.untranslatable.append({"construct": "relative date filter", "why": why})
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if f.get("null_as") is not None or op in _TEXT_OPS or op in ("any_of", "all_of") \
                or (isinstance(f["value"], list) and None in f["value"]):
            pred, why = _test_pred(target, op, f["value"], f, rules, dialect, res, text_key)
            if pred is not None:
                into.append(pred)
                continue
            if why:
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
        if isinstance(f["value"], dict):
            # A RANGE carries both bounds, either of which may be open. Each bound is its own
            # operator row: `range` (>=) for the lower, `lte` for the upper.
            preds, missing = [], []
            for bound, op in (("min", "range"), ("max", "lte")):
                if f["value"].get(bound) is None:
                    continue
                rule = rules.operator(op)
                if rule is None:
                    missing.append(op)
                    continue
                res.rules_used.append(rule["id"])
                if rule.get("tier") == "provisional":
                    res.provisional_rules.append(rule["id"])
                preds.append(_apply(_emit_for(rule, dialect),
                                    [target, _literal(f["value"][bound], dialect)], op))
            if missing:
                res.unknown += [f"operator:{op}" for op in missing]
                deferred.append({**f, "op": ",".join(missing)})
                continue
            if f["value"].get("or_null"):
                # The range, and the rows with no value ("include null values").
                preds = [f"({' AND '.join(preds)} OR {target} IS NULL)"]
                res.rules_used.append("engine.range_or_null")
            into.extend(preds)
            continue
        vals = f["value"] if isinstance(f["value"], (list, tuple, set)) else [f["value"]]
        if resolve and not re.search(rf"\b({re.escape(alias)}|j\d+)\.", target):
            # A filter on a calc that reads NO column (a parameter-driven sheet switch) is a
            # display control, not a data predicate: emitting `'Trend' IN ('Bar')` returns
            # nothing. Declared, never emitted.
            res.untranslatable.append({"construct": "parameter-only filter",
                                       "why": f"filter on {column!r} controls which view is "
                                              f"shown, not which rows"})
            continue
        if text_key and vals and all(_foldable(v) and v != "%null%" for v in vals) \
                and str(f.get("op") or "eq").lower() in ("in", "eq", "filter", "not_in"):
            # Text as the model holds it (seen live: 'North' and 'north' are one value): the
            # column's key against each value's, upper-cased here so the value stays a
            # literal a reader of the statement can still see.
            keyed = _literal_list([v.upper() for v in vals], dialect)
            res.rules_used.append("engine.text_key_filter")
            key = _Parser._text_key(target)
            if str(f.get("op") or "").lower() == "not_in":
                pred = f"({target} IS NULL OR {key} NOT IN ({keyed}))"
            elif len(vals) == 1:
                pred = f"{key} = {keyed}"
            else:
                pred = f"{key} IN ({keyed})"
            into.append(pred)
            continue
        if str(f.get("op") or "").lower() == "not_in":
            # A Tableau EXCLUDE filter. It keeps NULL rows unless `%null%` is itself excluded;
            # a bare SQL NOT IN would silently drop them too.
            excl_null = "%null%" in [str(v) for v in vals]
            rest = [v for v in vals if str(v) != "%null%"]
            if rest:
                pred = (f"({target} IS NOT NULL AND {target} NOT IN ({_literal_list(rest, dialect)}))"
                        if excl_null else
                        f"({target} IS NULL OR {target} NOT IN ({_literal_list(rest, dialect)}))")
            else:
                pred = f"{target} IS NOT NULL"
            res.rules_used.append("engine.tableau_exclude")
            into.append(pred)
            continue
        if "%null%" in [str(v) for v in vals]:
            # Tableau's member `%null%` IS NULL; `IN ('%null%')` matches a literal string.
            rest = [v for v in vals if str(v) != "%null%"]
            pred = (f"({target} IS NULL OR {target} IN ({_literal_list(rest, dialect)}))"
                    if rest else f"{target} IS NULL")
            into.append(pred)
            continue
        rule = rules.operator(str(f.get("op") or "eq").lower(), dialect or "")
        if rule is None:
            res.unknown.append(f"operator:{f.get('op')}")
            deferred.append(f)
            continue
        emit = rule.get("emit") or {}
        multi = isinstance(f.get("value"), (list, tuple, set))
        if multi and not emit.get("list_arg"):
            # A Tableau filter on several members carries a LIST. Only a membership operator
            # can express it; anything else would have to flatten it, and a flattened list is
            # a literal nobody wrote.
            res.unknown.append(f"operator:{f.get('op')}:multi-value")
            deferred.append({**f, "_why": "multi-value"})
            continue
        res.rules_used.append(rule["id"])
        if rule.get("tier") == "provisional":
            res.provisional_rules.append(rule["id"])
        value = (_literal_list(f["value"], dialect) if multi
                 else _literal(f.get("value"), dialect))
        pred = _apply(_emit_for(rule, dialect), [target, value], str(f.get("op")))
        into.append(pred)
    return ctx, dim, deferred


# The sibling modules' names, imported after this module's own definitions: the modules
# call each other, and a name is looked up only when a function runs.
from .parser import (  # noqa: E402
    _Parser,
    _column_ref,
    _table_ref,
    tokenize,
)
from .expressions import (  # noqa: E402
    _ISO_DAY,
    _apply,
    _emit_for,
    _tokens,
)
from .statements import (  # noqa: E402
    _ident,
    _literal,
    _literal_list,
    _out_name,
    _row_tokens,
)
