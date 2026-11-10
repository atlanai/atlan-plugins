# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a declared BI model says about each of its metrics, in the shape a metric card uses.

PROVENANCE. Written for this repo; not ported. Design: `docs/design/declared-models-contract.md`.

A governed model (a Snowflake semantic view is the first) declares, for every metric, its
formula, the dimensions it can be cut by, the relationships that reach them, the key of every
table and example questions. This module reads that from ONE model's `bi_facts` (its tables,
relationships and measures) and writes it as `measures[].declared`, which travels with the
statement compiled from the metric (A2 -> A3 -> the verified layer) and is what X1 to X3 read.
Nothing here knows a vendor, reads a warehouse, or keys a rule on a column's or table's name:
every rule is about the STRUCTURE the model declares.

ROLES, NOT TABLES. A model names its tables by role (`logical_name`): a flight's `dep` and
`arr` airports are two roles over one physical table. Every path, join and qualifier here is a
role, so two roles are two aliases and two joins, never one.

EVERY KEY COLUMN. A relationship joins on every column pair it declares (`columns`), never on
the first.

PATHS THAT NEVER FAN OUT. A role is reached along declared relationships only: many-to-one
forwards, one-to-one either way, any number of hops. A range or as-of relationship is a
relationship, not a pair of column names: it is never walked, and a role only it reaches is
refused by name.

EXPANDED FIRST. A model's formula names its facts, dimensions and other metrics by
`role.name`. Each is replaced by what it stands for, recursively, before anything is placed
or compared, so `SUM(orders.fare)` with `orders.fare AS orders.FARE_USD` is
`SUM(orders.FARE_USD)`. A metric naming a metric of ANOTHER table is refused: its aggregate
belongs to that table's grain, and one flat statement would compute it at this one's.

The same functions place a SQL measure in a statement (`plugs/readers/bicompile`, A3) and write the
declaration (`declare`, A1), so the statement the compiler writes and the card X3 accepts are
built from one reading of the model.
"""
from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

# A `role.name` reference: not part of a longer dotted name, not a function call.
_REF = re.compile(r'(?<![\w.$"])("?)([A-Za-z_][\w$]*)\1\s*\.\s*("?)([A-Za-z_][\w$]*)\3'
                  r'(?![\w$"(]|\s*\.)')
# A table qualifier in front of a column (`t` in `t.col`), as written.
_QUALIFIER = re.compile(r'(?<![\w.$"])("?)([A-Za-z_][A-Za-z0-9_$]*)\1(?=\s*\.\s*"?[A-Za-z_])')
_SIMPLE = re.compile(r'^\s*"?[A-Za-z_][\w$]*"?\s*\.\s*"?[A-Za-z_][\w$]*"?\s*$')
_CARD = {"M:1": "many_to_one", "1:1": "one_to_one", "MANY_TO_ONE": "many_to_one",
         "ONE_TO_ONE": "one_to_one"}

# Date functions, and which of their arguments is the DATE. Only that argument is marked: the
# amount `DATEADD` adds is a number, and marking it a date is a claim the SQL never made.
_DATE_ARGS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (exp.Year, ("this",)), (exp.Quarter, ("this",)), (exp.Month, ("this",)),
    (exp.Week, ("this",)), (exp.Day, ("this",)), (exp.DayOfWeek, ("this",)),
    (exp.DayOfYear, ("this",)), (exp.DateTrunc, ("this",)), (exp.TimestampTrunc, ("this",)),
    (exp.DateAdd, ("this",)), (exp.DateSub, ("this",)), (exp.TsOrDsAdd, ("this",)),
    (exp.DateDiff, ("this", "expression")), (exp.Extract, ("expression",)))
# The levelled forms: one of these over a single column welds a level into the dimension.
_LEVELLED = (exp.Year, exp.Quarter, exp.Month, exp.Week, exp.Day, exp.DayOfWeek,
             exp.DayOfYear, exp.DateTrunc, exp.TimestampTrunc, exp.Extract)
_DATE_EXPR = (exp.CurrentDate, exp.CurrentTimestamp, exp.DateTrunc, exp.TimestampTrunc,
              exp.DateAdd, exp.DateSub, exp.TsOrDsAdd)
_DATE_LITERAL = re.compile(r"^\d{4}-\d{2}-\d{2}")
_COMPARE = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.Between)


def _atomic(sql: str) -> bool:
    """One column, or one function call spanning the whole text: safe to put in place of a
    name without brackets."""
    text = str(sql or "").strip()
    if _SIMPLE.match(text):
        return True
    m = re.match(r"^[A-Za-z_][\w$]*\s*\(", text)
    if not m or not text.endswith(")"):
        return False
    depth = 0
    for is_code, part in outside_literals(text[m.end() - 1:]):
        if not is_code:
            continue
        for i, ch in enumerate(part):
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if depth == 0 and (i < len(part) - 1):
                return False
    return depth == 0


class NotPlaced(ValueError):
    """The model's declaration cannot be placed in one statement. The message names why."""


# ------------------------------------------------------------------ SQL text, outside literals

def outside_literals(sql: str) -> list[tuple[bool, str]]:
    """-> [(is_code, text)]. Single-quoted literals ('' escaped) are never code."""
    parts, buf, i, n = [], [], 0, len(sql)
    while i < n:
        if sql[i] == "'":
            if buf:
                parts.append((True, "".join(buf)))
                buf = []
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            parts.append((False, sql[i:j + 1]))
            i = j + 1
            continue
        buf.append(sql[i])
        i += 1
    if buf:
        parts.append((True, "".join(buf)))
    return parts


def qualifiers(sql: str) -> list[str]:
    """Table qualifiers a SQL expression reads (`t` in `t.col`), lower-cased, first-seen
    order. Text inside string literals is not read."""
    seen: list[str] = []
    for is_code, text in outside_literals(str(sql or "")):
        if not is_code:
            continue
        for m in _QUALIFIER.finditer(text):
            q = m.group(2).lower()
            if q not in seen:
                seen.append(q)
    return seen


def requalify(sql: str, names: dict[str, str]) -> str:
    """Rebind each known qualifier (lower-cased key) to `names[q]`; literals and unknown
    qualifiers are left as written."""
    def sub(m: re.Match) -> str:
        return names.get(m.group(2).lower(), m.group(0))
    return "".join(_QUALIFIER.sub(sub, text) if is_code else text
                   for is_code, text in outside_literals(str(sql or "")))


def code_norm(sql: str) -> str:
    """SQL compared for meaning, not layout: whitespace removed and identifiers case-folded;
    string literals kept exactly as written."""
    return "".join(re.sub(r"\s+", "", t).upper() if code else t
                   for code, t in outside_literals(str(sql or "")))


def title(name: str) -> str:
    """A model's own name, in words (`order_date` -> `Order Date`). An acronym stays upper."""
    return " ".join(w if w.isupper() and len(w) > 1 else w.capitalize()
                    for w in str(name).replace("_", " ").split())


def datatype(data_type: str | None) -> str | None:
    """The card vocabulary for a type the MODEL declares, or None when it declares none (or
    one this does not know). Never read from a warehouse."""
    t = str(data_type or "").strip().upper()
    if not t:
        return None
    if t.startswith("TIMESTAMP") or t.startswith("DATETIME"):
        return "timestamp"
    if t.startswith("DATE"):
        return "date"
    if t.startswith("BOOL"):
        return "boolean"
    if re.match(r"^(VARCHAR|STRING|TEXT|CHAR|NVARCHAR|NCHAR)\b", t):
        return "string"
    if re.match(r"^(FLOAT|DOUBLE|REAL)\b", t):
        return "decimal"
    m = re.match(r"^(NUMBER|NUMERIC|DECIMAL)\s*(?:\(\s*\d+\s*(?:,\s*(\d+)\s*)?\))?", t)
    if m:
        return "decimal" if m.group(2) and int(m.group(2)) > 0 else "integer"
    if re.match(r"^(INT|INTEGER|BIGINT|SMALLINT|TINYINT|BYTEINT)\b", t):
        return "integer"
    return None


# ------------------------------------------------------------------ the model

@dataclass
class Edge:
    """One directed step that never fans out: from `src` role to `dst` role."""
    src: str
    dst: str
    pairs: list[tuple[str, str]]          # (column on src, column on dst), every key column
    cardinality: str                       # many_to_one | one_to_one
    name: str = ""
    kind: str = "relationship"


@dataclass
class Role:
    name: str                              # as the model writes it
    physical: str
    primary_key: list[str] = field(default_factory=list)
    columns: list[dict] = field(default_factory=list)


@dataclass
class Item:
    """A name a formula can use: a column the model defines by SQL (a fact or dimension), or a
    metric. `sql` is what it stands for, still in the model's own role names."""
    kind: str                              # column | measure
    role: str | None
    name: str
    sql: str
    home: str | None = None                # a measure's home role (lower), or None


class Model:
    """ONE declared model: its roles, the names its formulas use, and its relationships as
    edges that never fan out. Built from a single `bi_facts` partition (`of`), or from a
    statement's own pieces (`from_graph`, the compiler's regression corpus)."""

    def __init__(self) -> None:
        self.roles: dict[str, Role] = {}
        self.items: dict[tuple[str | None, str], Item] = {}
        self.edges: dict[str, list[Edge]] = {}
        # Relationships that are not walked (range, as-of), by the role they leave from and
        # the role they reach: what a refusal names.
        self.blocked: dict[tuple[str, str], str] = {}

    # -- building --------------------------------------------------------------------------
    @classmethod
    def of(cls, facts: dict) -> "Model":
        m = cls()
        for t in facts.get("tables") or []:
            role = str(t.get("logical_name") or t["name"])
            m.roles[role.lower()] = Role(role, t["name"], list(t.get("primary_key") or []),
                                         list(t.get("columns") or []))
            for c in t.get("columns") or []:
                expr = cls._column_sql(role, c)
                if expr is not None:
                    m.items[(role.lower(), str(c["name"]).lower())] = Item(
                        "column", role.lower(), str(c["name"]), expr)
        for ms in facts.get("measures") or []:
            if ms.get("formula_language") != "sql" or not ms.get("formula"):
                continue
            home = str(ms.get("home_table") or "").lower() or None
            m.items[(home, str(ms["name"]).lower())] = Item(
                "measure", home, str(ms["name"]), str(ms["formula"]), home)
        for r in facts.get("relationships") or []:
            m._relate(r)
        return m

    @classmethod
    def from_graph(cls, logical: dict[str, str], graph: list[dict]) -> "Model":
        """Roles from a logical -> physical map and edges from a unit's join graph, no
        formulas to expand: what a compiler regression case can state."""
        m = cls()
        for role, physical in (logical or {}).items():
            m.roles[str(role).lower()] = Role(str(role), str(physical))
        for r in graph or []:
            m._relate(r)
        return m

    @staticmethod
    def _column_sql(role: str, c: dict) -> str | None:
        """What a column NAME stands for in a formula, when it is not the column itself."""
        if c.get("formula") and c.get("formula_language") == "sql":
            return str(c["formula"])
        # A plain column is its own name, spelled as the warehouse spells it.
        src = str(c.get("source_column") or "")
        if src and src != str(c["name"]):
            return f"{role}.{src}"
        return None

    def role_of(self, table: str) -> str | None:
        """The one role over `table` (a role name or a physical name), or None when none or
        several are."""
        key = str(table or "").lower()
        if key in self.roles:
            return key
        hits = [k for k, r in self.roles.items() if r.physical.lower() == key]
        return hits[0] if len(hits) == 1 else None

    def _relate(self, r: dict) -> None:
        a = (str(r.get("from_role") or "").lower() or self.role_of(r.get("from_table")))
        b = (str(r.get("to_role") or "").lower() or self.role_of(r.get("to_table")))
        if not a or not b:
            return
        kind = str(r.get("kind") or "relationship")
        name = str(r.get("name") or f"{a}_to_{b}")
        if kind in ("range", "asof"):
            self.blocked[(a, b)] = (f"{kind} relationship {name!r} "
                                    f"({r.get('condition') or 'a non-equality condition'})")
            return
        # A blend is a left join AFTER aggregation, and an inactive relationship is used only
        # where a measure activates it: neither is a path a statement joins along.
        if kind == "blend" or r.get("is_active") is False:
            why = "blend" if kind == "blend" else "inactive relationship"
            self.blocked[(a, b)] = f"{why} {name!r}"
            self.blocked.setdefault((b, a), f"{why} {name!r}")
            return
        pairs = [(str(p["from_column"]), str(p["to_column"])) for p in r.get("columns") or []]
        pairs = pairs or [(str(r["from_column"]), str(r["to_column"]))]
        card = str(r.get("cardinality") or "").upper()
        # One relationship given as one row per column pair (an older reader) is still ONE
        # relationship: rows that share both ends and a name are merged, never two joins.
        for e in self.edges.get(a, []):
            if e.dst == b and e.name == name:
                for p in pairs:
                    if p not in e.pairs:
                        e.pairs.append(p)
                for back in self.edges.get(b, []):
                    if back.dst == a and back.name == name:
                        for p in pairs:
                            if p[::-1] not in back.pairs:
                                back.pairs.append(p[::-1])
                return
        # `join_type` is not read: every hop here is for a MEASURE to read a column, and a
        # measure reading a column must not change which rows the statement has (an inner
        # join would drop them for every other measure, and the declared card joins LEFT).
        if card in _CARD:
            self.edges.setdefault(a, []).append(Edge(a, b, list(pairs), _CARD[card], name, kind))
        if _CARD.get(card) == "one_to_one":
            self.edges.setdefault(b, []).append(
                Edge(b, a, [p[::-1] for p in pairs], "one_to_one", name, kind))

    # -- reading formulas ------------------------------------------------------------------
    def expand(self, sql: str, home: str | None, own: tuple | None = None) -> str:
        """`sql` with every fact, dimension and metric it names replaced by what it stands
        for, recursively. A name already being expanded is the column itself, and so is the
        name `own` (role, name) when `sql` is that item's own definition: `price AS price / 100`
        reads the column `price`, never itself. Raises NotPlaced for a metric of another
        table."""
        home = (home or "").lower() or None

        def walk(text: str, stack: tuple) -> str:
            def sub(m: re.Match) -> str:
                key = (m.group(2).lower(), m.group(4).lower())
                item = self.items.get(key)
                if item is None or key in stack:
                    return m.group(0)
                if item.kind == "measure" and home and item.home and item.home != home:
                    raise NotPlaced(f"names metric {m.group(0)}, which is computed over "
                                    f"{item.home}: its aggregate belongs to that table's "
                                    f"grain, not to one statement over {home}")
                inner = walk(item.sql, stack + (key,))
                return inner if _atomic(inner) else f"({inner})"
            return "".join(_REF.sub(sub, t) if code else t
                           for code, t in outside_literals(text))
        own = tuple(str(x).lower() for x in own) if own else None
        return walk(str(sql or ""), (own,) if own else ())

    def measure_home(self, sql: str) -> str | None:
        """The one role a view-level metric is computed over: the home of every metric it
        names, or the one role it reads when it names none. None when that is not one role."""
        homes = {self.items[k].home for k in self._refs(sql)
                 if self.items[k].kind == "measure"}
        homes.discard(None)
        if len(homes) == 1:
            return homes.pop()
        if homes:
            return None
        roles = [q for q in qualifiers(sql) if q in self.roles]
        return roles[0] if len(set(roles)) == 1 else None

    def _refs(self, sql: str, stack: tuple = ()) -> list[tuple]:
        out = []
        for code, text in outside_literals(str(sql or "")):
            if not code:
                continue
            for m in _REF.finditer(text):
                key = (m.group(2).lower(), m.group(4).lower())
                if key in self.items and key not in stack:
                    out.append(key)
                    out += self._refs(self.items[key].sql, stack + (key,))
        return out

    # -- paths -----------------------------------------------------------------------------
    def reach(self, home: str) -> dict[str, list[Edge]]:
        """{role: the hops that reach it from `home`}, breadth first, never fanning out."""
        home = home.lower()
        seen: dict[str, list[Edge]] = {home: []}
        queue = deque([home])
        while queue:
            cur = queue.popleft()
            for e in self.edges.get(cur, []):
                if e.dst not in seen:
                    seen[e.dst] = seen[cur] + [e]
                    queue.append(e.dst)
        return seen

    def path(self, home: str, target: str, reach: dict | None = None) -> list[Edge]:
        reach = reach if reach is not None else self.reach(home)
        target = target.lower()
        if target in reach:
            return reach[target]
        if target not in self.roles:
            raise NotPlaced(f"reads {target!r}, which is not a table of the model")
        why = [w for (a, b), w in sorted(self.blocked.items()) if b == target and a in reach]
        if why:
            raise NotPlaced(f"reads {target}, which only a {why[0]} reaches; it is not "
                            f"compiled")
        raise NotPlaced(f"reads {target}, which no declared many-to-one or one-to-one path "
                        f"from {home} reaches")

    def place(self, home: str, roles: list[str]) -> list[Edge]:
        """Every hop needed to reach `roles` from `home`, each once, in the order reached."""
        reach = self.reach(home)
        hops: list[Edge] = []
        for r in roles:
            if r.lower() == home.lower():
                continue
            for e in self.path(home, r, reach):
                if e not in hops:
                    hops.append(e)
        return hops


# ------------------------------------------------------------------ placing a SQL measure

def place_in_statement(model: Model, home: str, formulas: list[str], alias: str,
                       first_join: int) -> tuple[list[str], list[str], dict[str, str]]:
    """(expanded formulas bound to the statement's aliases, `LEFT JOIN` clauses, role->alias)
    for SQL measures over `home`. Every role gets its own alias, so two roles over one table
    are two joins. Raises NotPlaced."""
    expanded = [model.expand(f, home) for f in formulas]
    roles = [q for f in expanded for q in qualifiers(f)]
    unknown = sorted({q for q in roles if q not in model.roles})
    if unknown:
        raise NotPlaced(f"reads table qualifier(s) {unknown}, which are not tables of the "
                        f"model")
    hops = model.place(home, roles)
    names = {home.lower(): alias}
    clauses = []
    n = first_join
    for e in hops:
        n += 1
        names[e.dst] = f"j{n}"
        on = " AND ".join(f"j{n}.{dc} = {names[e.src]}.{sc}" for sc, dc in e.pairs)
        clauses.append(f"LEFT JOIN {model.roles[e.dst].physical} j{n} ON {on}")
    return [requalify(f, names) for f in expanded], clauses, names


# ------------------------------------------------------------------ the declaration

def _parse(sql: str, dialect: str):
    try:
        return sqlglot.parse_one(sql, read=dialect or None)
    except Exception:  # noqa: BLE001 - an unparseable expression carries no structural evidence
        return None


def _col_key(col: exp.Column) -> tuple[str, str] | None:
    return (col.table.lower(), col.name.upper()) if col.table else None


def evidence(texts: list[str], dialect: str) -> tuple[set, set]:
    """({(role, COLUMN)} the model's own SQL treats as dates, {...} it uses as conditions).

    A date is the DATE argument of a date function, or a bare column compared with a date
    expression or a date literal. A condition is a bare column used AS a condition (alone, or
    an operand of AND / OR / NOT), compared with nothing: a flag."""
    dates, conditions = set(), set()
    for text in texts:
        tree = _parse(text, dialect)
        if tree is None:
            continue
        for node in tree.walk():
            for kind, args in _DATE_ARGS:
                if isinstance(node, kind):
                    for a in args:
                        arg = node.args.get(a)
                        if isinstance(arg, exp.Column) and (k := _col_key(arg)):
                            dates.add(k)
            if isinstance(node, _COMPARE):
                others = list(node.iter_expressions())
                dateish = any(isinstance(x, _DATE_EXPR) or x.find(*_DATE_EXPR) is not None
                              or (isinstance(x, exp.Literal) and x.is_string
                                  and _DATE_LITERAL.match(x.this or ""))
                              for x in others)
                if dateish:
                    for x in others:
                        if isinstance(x, exp.Column) and (k := _col_key(x)):
                            dates.add(k)
            elif isinstance(node, (exp.If, exp.Where)):
                stack = [node.this]
                while stack:
                    x = stack.pop()
                    if isinstance(x, (exp.And, exp.Or)):
                        stack += [x.this, x.expression]
                    elif isinstance(x, (exp.Not, exp.Paren)):
                        stack.append(x.this)
                    elif isinstance(x, exp.Column) and (k := _col_key(x)):
                        conditions.add(k)
    return dates, conditions


def _levelled(sql: str, dialect: str) -> bool:
    tree = _parse(sql, dialect)
    return (isinstance(tree, _LEVELLED)
            and len({c.name.upper() for c in tree.find_all(exp.Column)}) == 1)


def _bare_column(sql: str) -> tuple[str, str] | None:
    """(role, COLUMN) when `sql` is one qualified column and nothing else."""
    m = re.match(r'^\s*"?([A-Za-z_][\w$]*)"?\s*\.\s*"?([A-Za-z_][\w$]*)"?\s*$', str(sql or ""))
    return (m.group(1).lower(), m.group(2).upper()) if m else None


class Declarer:
    """Writes `measures[].declared` for one model. `examples` are the model's own example
    questions, compiled by its reader: [{name, question, sql, metrics: [(role, name)],
    dimensions: [(role, name)] grouped by, refs: [(role, name)] every dimension read}] (`sql`
    None when it did not compile)."""

    def __init__(self, facts: dict, *, dialect: str, examples: list[dict] | None = None):
        self.model = Model.of(facts)
        self.dialect = dialect
        self.examples = list(examples or [])
        texts = [c.get("formula") or "" for t in facts.get("tables") or []
                 for c in t.get("columns") or []]
        texts += [m.get("formula") or "" for m in facts.get("measures") or []]
        texts += [x["sql"] for x in self.examples if x.get("sql")]
        self.dates, self.conditions = evidence([self.model.expand(t, None) for t in texts
                                                if t], dialect)

    def _names(self, home: str, roles: list[str]) -> dict[str, str]:
        """role -> how a card writes it: the physical table, or the role's own name for a
        table that plays more than one role here (then it is a derived table in `source`)."""
        counts = Counter(self.model.roles[r].physical.upper() for r in roles)
        out = {}
        for r in roles:
            phys = self.model.roles[r].physical
            out[r] = (phys if r == home or counts[phys.upper()] == 1
                      else self.model.roles[r].name)
        return out

    def _kind(self, sql: str, c: dict) -> tuple[str, str]:
        typed = datatype(c.get("data_type"))
        bare = _bare_column(sql)
        if typed in ("date", "timestamp"):
            return ("time" if bare else "categorical"), typed
        if typed:
            # A type the model declares wins: a number is never a date, whatever SQL it is in.
            return ("boolean" if typed == "boolean" else "categorical"), typed
        if bare and bare in self.dates:
            return "time", "date"
        if bare and bare in self.conditions:
            return "boolean", "boolean"
        return "categorical", "unknown"

    def declare(self, measure: dict) -> dict:
        """The declaration of one metric. Raises NotPlaced, naming why."""
        model = self.model
        home = str(measure.get("home_table") or "").lower()
        if home not in model.roles:
            raise NotPlaced("is not computed over one table of the model")
        formula = model.expand(measure["formula"], home, (home, str(measure["name"])))
        reach = model.reach(home)
        need = qualifiers(formula)
        unknown = sorted({q for q in need if q not in model.roles})
        if unknown:
            raise NotPlaced(f"reads table qualifier(s) {unknown}, which are not tables of "
                            f"the model")
        for q in need:
            model.path(home, q, reach)
        # Dimensions: every one the model declares on a role this metric reaches. A levelled
        # time expression (a date part or truncation over one column) is not one: the bare
        # date is, and the level is the consumer's.
        dims, taken, used = [], set(), set(need)
        for role in reach:                            # home first, then breadth first
            r = model.roles[role]
            for c in r.columns:
                if c.get("role") != "dimension" or c.get("is_hidden"):
                    continue
                raw = model.expand(c.get("formula") or
                                   f"{r.name}.{c.get('source_column') or c['name']}", home,
                                   (r.name, c["name"]))
                if _levelled(raw, self.dialect):
                    continue
                if any(q not in reach for q in qualifiers(raw)):
                    continue
                name = title(c["name"])
                if name.lower() in taken:
                    name = f"{name} ({title(r.name)})"
                if name.lower() in taken:
                    continue
                taken.add(name.lower())
                used.update(qualifiers(raw))
                kind, dtype = self._kind(raw, c)
                dims.append({"_raw": raw, "_ref": (role, str(c["name"]).lower()),
                             "name": name, "kind": kind, "datatype": dtype,
                             "required_join": None if role == home else r.name.lower(),
                             **({"description": str(c["description"])}
                                if c.get("description") else {}),
                             **({"synonyms": list(c["synonyms"])} if c.get("synonyms") else {})})
        hops = model.place(home, [q for q in reach if q in used])
        names = self._names(home, [home] + [e.dst for e in hops])
        joins, source = [], names[home]
        for e in hops:
            to = model.roles[e.dst]
            on = " AND ".join(f"{names[e.src]}.{sc} = {names[e.dst]}.{dc}"
                              for sc, dc in e.pairs)
            joins.append({"name": to.name.lower(), "to": to.physical, "sql": on,
                          "cardinality": e.cardinality,
                          "origin": f"a relationship the model declares ({e.name}); it never "
                                    f"fans out"})
            target = (to.physical if names[e.dst] == to.physical
                      else f"(SELECT * FROM {to.physical}) {names[e.dst]}")
            source += f"\nLEFT JOIN {target}\n  ON {on}"
        by_ref = {}
        for d in dims:
            raw = d.pop("_raw")
            d["sql"] = requalify(raw, names)
            by_ref[d.pop("_ref")] = d
        home_role = model.roles[home]
        mine = (home, str(measure["name"]).lower())
        examples, times = [], Counter()
        for x in self.examples:
            if not x.get("sql") or mine not in {(str(a or "").lower(), str(b).lower())
                                                for a, b in x.get("metrics") or []}:
                continue
            group = [by_ref[k]["name"] for k in x.get("dimensions") or [] if k in by_ref]
            examples.append({"source": f"declared:{x['name']}", "question": x.get("question"),
                             "sql": x["sql"], "group_by": list(dict.fromkeys(group))})
            for k in x.get("refs") or []:
                if k in by_ref and by_ref[k]["kind"] == "time":
                    times[by_ref[k]["sql"]] += 1
        top = times.most_common(2)
        return {
            "entity": home_role.physical,
            "formula": requalify(formula, names),
            "grain": [f"{home_role.physical}.{k}" for k in home_role.primary_key],
            "source": source,
            "joins": joins,
            "dimensions": dims,
            "examples": examples,
            "timeframe_column": (top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1])
                                 else None),
        }


def attach(facts: dict, *, dialect: str, examples: list[dict] | None = None) -> list[str]:
    """Write `declared` on every metric of `facts` the model lets this module place, in
    place. -> notes naming each metric that could not be placed, and why."""
    d = Declarer(facts, dialect=dialect, examples=examples)
    notes = []
    for m in facts.get("measures") or []:
        if m.get("formula_language") != "sql":
            continue
        try:
            m["declared"] = d.declare(m)
        except NotPlaced as exc:
            notes.append(f"metric {m['name']!r} {exc}; it carries no declaration")
    return notes


# ------------------------------------------------------------------ inferring a relationship

def infer_relationships(facts: dict) -> list[dict]:
    """Joins a model's metrics need and it does not declare, inferred from primary keys.

    Only when asked (policy), and only on structure: for a metric over H that reads R, H -> R
    is inferred when every column of R's key is a column of H, that key is the key of no other
    table (so it names R and nothing else), and none of those columns is part of H's own key
    (a table's identity is not a reference to another table). So two tables that each key on
    an `ID` never join on it. Each is marked `kind: "inferred"` and many-to-one."""
    model = Model.of(facts)
    keys = Counter(frozenset(k.upper() for k in r.primary_key)
                   for r in model.roles.values() if r.primary_key)
    cols = {role: {str(c.get("source_column") or c["name"]).upper() for c in r.columns}
            for role, r in model.roles.items()}
    out, seen = [], set()
    for ms in facts.get("measures") or []:
        home = str(ms.get("home_table") or "").lower()
        if home not in model.roles or not model.roles[home].primary_key:
            continue
        own = {k.upper() for k in model.roles[home].primary_key}
        for other in qualifiers(model.expand(ms.get("formula") or "", home)):
            r = model.roles.get(other)
            if other == home or r is None or not r.primary_key or (home, other) in seen:
                continue
            key = [k.upper() for k in r.primary_key]
            if (not set(key) <= cols[home] or set(key) & own
                    or keys[frozenset(key)] != 1):
                continue
            seen.add((home, other))
            pairs = [{"from_column": k, "to_column": k} for k in r.primary_key]
            out.append({"from_table": model.roles[home].physical, "from_column": pairs[0]["from_column"],
                        "to_table": r.physical, "to_column": pairs[0]["to_column"],
                        "is_active": True, "cardinality": "M:1", "kind": "inferred",
                        "name": f"{model.roles[home].name}_to_{r.name}",
                        "from_role": model.roles[home].name, "to_role": r.name,
                        "columns": pairs})
    return out


# ------------------------------------------------------------------ the card side (X1 to X3)
#
# A statement A3 compiled from a declared metric carries the declaration on its provenance
# (`provenance.bi.declared`). The metric gates read it from there and never ask which vendor a
# model came from: X1 and X2 show it beside the statement, and X3 holds the card built from it
# to it, then completes the card from it.

EXAMPLE_PREFIX = "declared:"


def of_statement(stmt: dict | None) -> dict | None:
    """The declaration a verified statement carries, or None."""
    return (((stmt or {}).get("provenance") or {}).get("bi") or {}).get("declared") or None


def key_of(decl: dict) -> str:
    """A declared metric's identity: its model and its name. Two models that both declare
    `revenue` declare two metrics."""
    return f"{decl.get('model')}::{decl.get('metric')}".lower()


def identity(decl: dict) -> tuple[str, str]:
    """What the declared metric computes: its entity and its formula. Two declarations that
    agree on both are one metric declared twice."""
    return str(decl.get("entity") or "").upper(), code_norm(decl.get("formula"))


def brief(decl: dict) -> dict:
    """What X1 and X2 show beside a statement compiled from a declared metric."""
    return {k: decl.get(k) for k in ("model", "metric", "entity", "formula", "description",
                                     "synonyms")}


def _norm(sql: str | None) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip().upper()


def _words(text: str | None) -> str:
    return " ".join(str(text or "").lower().split())


def described(model_text: str | None, author: str | None) -> str | None:
    """The model's comment, then whatever an author's description adds to it."""
    if not model_text:
        return author
    if not author or _words(author) in _words(model_text):
        return model_text
    if _words(model_text) in _words(author):
        return author                                 # already the model's, and more
    return f"{model_text.rstrip()} {author.strip()}"


def with_synonyms(description: str | None, name: str, synonyms: list[str]) -> str | None:
    """`description`, ending with the dimension's other names ("Also called: a, b."). A
    published dimension has no field for them, and they are the words that tell a reader which
    dimension a question means."""
    other = [s for s in dict.fromkeys(synonyms or [])
             if _words(s) != _words(name) and "_" not in s]
    if not other or "also called:" in (description or "").lower():
        return description
    tail = f"Also called: {', '.join(other)}."
    return f"{description.rstrip()} {tail}" if description else tail


def _columns(sql: str, dialect: str) -> set[tuple[str, str]]:
    tree = _parse(sql, dialect)
    if tree is None:
        return set()
    out = set()
    for c in tree.find_all(exp.Column):
        parts = [p for p in (c.catalog, c.db, c.table) if p]
        if parts:
            out.add((".".join(parts).upper(), c.name.upper()))
    return out


def _in_source(target: str, source: str) -> bool:
    """Whether a FROM clause already makes a join: a derived table by its name, a table by
    its whole, fully qualified name (any case)."""
    m = re.match(r"^\(.*\)\s+([A-Za-z_]\w*)$", target, re.S)
    if m:
        return re.search(rf"\)\s*(?:AS\s+)?{re.escape(m.group(1))}\b", source, re.I) is not None
    return re.search(rf"(?<![\w.]){re.escape(target)}(?![\w])", source, re.I) is not None


def _targets(source: str) -> dict[str, str]:
    """{ON condition (normalised): what the declared FROM clause joins} from its LEFT JOINs."""
    out = {}
    for m in re.finditer(r"LEFT JOIN (.+?)\n  ON (.+?)(?=\nLEFT JOIN |\Z)", source or "", re.S):
        out[_norm(m.group(2))] = m.group(1).strip()
    return out


def held_to(card: dict, decl: dict) -> list[str]:
    """What a card built from a declared metric must be, as the author wrote it: the declared
    formula (layout and identifier case aside) over the declared entity, and only the model's
    own examples, carried rather than retyped."""
    out = []
    name = f"{decl.get('model')}'s {decl.get('metric')!r}"
    if code_norm(card.get("formula")) != code_norm(decl["formula"]):
        out.append(f"formula is not the one {name} declares. Carry it verbatim: "
                   f"{decl['formula']}")
    entity = str((card.get("source") or {}).get("entity") or "")
    if entity.upper() != str(decl["entity"]).upper():
        out.append(f"entity is {entity!r}, but {name} is computed over {decl['entity']}.")
    mine = {e["source"] for e in decl.get("examples") or []}
    for e in card.get("examples") or []:
        src = str(e.get("source") or "")
        if not src.startswith(EXAMPLE_PREFIX):
            continue
        if src not in mine:
            out.append(f"example {src} is not one of the examples {name} declares "
                       f"({', '.join(sorted(mine)) or 'none'}).")
        for field_ in ("sql", "question"):
            if e.get(field_) is not None:
                out.append(f"example {src} supplies its own `{field_}`. Do not write it — the "
                           f"model's own {field_} is carried.")
    return out


def complete(card: dict, decl: dict, *, dialect: str) -> tuple[dict, Counter]:
    """`card` (as an author wrote it) completed from the declaration, as a new card. The
    author's additions stay; what the model declares is added, and where both say something
    about one dimension the model's SQL and comment lead. -> (card, counts)."""
    import copy  # noqa: PLC0415
    a = copy.deepcopy(card)
    counts: Counter = Counter()
    src = a.setdefault("source", {})

    # JOINS: every one the model declares, each once, under a name no author join holds.
    joins = a.get("joins") or []
    a["joins"] = joins
    names = {str(j.get("name")) for j in joins}
    by_on = {_norm(j.get("sql")): j.get("name") for j in joins}
    renamed: dict[str, str] = {}
    for j in decl.get("joins") or []:
        if _norm(j["sql"]) in by_on:
            renamed[j["name"]] = by_on[_norm(j["sql"])]
            continue
        nm = j["name"]
        while nm in names:
            nm += "_"
        renamed[j["name"]] = nm
        names.add(nm)
        joins.append({**j, "name": nm})
        counts["joins"] += 1

    # DIMENSIONS: matched to the author's by SQL, or by the one column both read.
    dims = a.get("dimensions") or []
    a["dimensions"] = dims
    by_sql = {_norm(d.get("sql")): d for d in dims if isinstance(d, dict)}
    by_col: dict[tuple, dict] = {}
    for d in dims:
        cols = _columns(d.get("sql") or "", dialect) if isinstance(d, dict) else set()
        if len(cols) == 1:
            by_col.setdefault(next(iter(cols)), d)
    taken = {str(d.get("name") or "").lower() for d in dims if isinstance(d, dict)}
    on_card: dict[str, str] = {}                      # declared dimension name -> its card name
    for v in decl.get("dimensions") or []:
        cols = _columns(v["sql"], dialect)
        col = next(iter(cols)) if len(cols) == 1 else None
        rj = renamed.get(v["required_join"], v["required_join"]) if v["required_join"] else None
        mine = by_sql.get(_norm(v["sql"])) or (by_col.get(col) if col else None)
        if mine is not None:
            if _norm(mine.get("sql")) != _norm(v["sql"]):
                mine["sql"] = v["sql"]                # the model's SQL is the governed one
                if mine.get("kind") == "time" and v["kind"] != "time":
                    mine["kind"] = v["kind"]
                counts["sql_governed"] += 1
            if rj:
                mine["required_join"] = rj
            if v.get("synonyms"):
                mine["synonyms"] = sorted(set(mine.get("synonyms") or []) | set(v["synonyms"]))
            said = described(v.get("description"), mine.get("description"))
            if said:
                mine["description"] = said
            on_card[v["name"]] = mine["name"]
            continue
        name = v["name"]
        if name.lower() in taken:
            name = f"{name} ({title(rj or str(decl['entity']).rsplit('.', 1)[-1])})"
        if name.lower() in taken:
            continue
        taken.add(name.lower())
        on_card[v["name"]] = name
        d = {k: v[k] for k in ("kind", "sql", "datatype") if k in v}
        d = {"name": name, **d, "required_join": rj, "_roles": ["bi_declared"]}
        if v.get("description"):
            d["description"] = v["description"]
        if v.get("synonyms"):
            d["synonyms"] = list(v["synonyms"])
        dims.append(d)
        by_sql[_norm(v["sql"])] = d
        counts["dimensions"] += 1
    for d in dims:
        if isinstance(d, dict):
            said = with_synonyms(d.get("description"), d.get("name") or "",
                                 d.get("synonyms") or [])
            if said != d.get("description"):
                d["description"] = said

    # THE FROM CLAUSE makes every join: `joins[]` only describes one, and a dimension on a
    # joined table is a cut nobody can run until its join is in the FROM clause. A join the
    # model declares never fans out and a LEFT JOIN drops no row, so the number is unchanged.
    targets = _targets(decl.get("source") or "")
    base = str(src.get("sql") or "").strip() or str(src.get("entity") or decl["entity"])
    source = base
    for j in joins:
        if j.get("cardinality") not in ("many_to_one", "one_to_one"):
            continue
        target = targets.get(_norm(j.get("sql"))) or str(j.get("to") or "")
        if target and not _in_source(target, source):
            source += f"\nLEFT JOIN {target}\n  ON {j['sql']}"
            counts["source_joins"] += 1
    if source != base:
        src["sql"] = source

    # EXAMPLES: the model's own, each carried whole (its SQL and question are the model's). An
    # example groups by the model's dimension names; on the card a dimension may carry the
    # author's name instead, so each is named as the card names it, and one the card does not
    # carry is left out rather than referenced.
    def _cut(group):
        return list(dict.fromkeys(on_card[g] for g in group or [] if g in on_card))
    have = {str(e.get("source")) for e in a.get("examples") or []}
    by_name = {e["source"]: {**e, "group_by": _cut(e.get("group_by"))}
               for e in decl.get("examples") or []}
    examples = a.get("examples") or []
    for e in examples:
        if str(e.get("source") or "") in by_name:
            mine = by_name[e["source"]]
            e["sql"], e["question"] = mine["sql"], mine.get("question")
            e.setdefault("group_by", [x for x in mine.get("group_by") or []])
    for e in by_name.values():
        if e["source"] not in have:
            examples.append(dict(e))
            counts["examples"] += 1
    a["examples"] = examples

    if not str(a.get("grain") or "").strip() and decl.get("grain"):
        a["grain"] = ", ".join(decl["grain"])
        counts["grain"] += 1
    if not src.get("timeframe_column") and decl.get("timeframe_column"):
        src["timeframe_column"] = decl["timeframe_column"]
        counts["timeframe"] += 1
    if decl.get("synonyms"):
        a["synonyms"] = sorted(set(a.get("synonyms") or []) | set(decl["synonyms"]))
        counts["synonyms"] += len(decl["synonyms"])
    # The model's comment on the metric is where it writes the rules a consumer must not
    # break (what fans out, what never to sum): a gotcha, verbatim, whoever wrote the card.
    comment = str(decl.get("description") or "").strip()
    notes = a.get("notes") or []
    if comment and comment not in notes:
        a["notes"] = [*notes, comment]
        counts["comments"] += 1
    return a, counts


def unreachable(card: dict) -> list[str]:
    """Dimensions whose join the card's FROM clause does not make: a cut nobody can run."""
    joins = {j.get("name"): j for j in card.get("joins") or []}
    source = str((card.get("source") or {}).get("sql") or "")
    out = []
    for d in card.get("dimensions") or []:
        j = joins.get(d.get("required_join")) if isinstance(d, dict) else None
        if not j:
            continue
        hit = (re.search(rf"\)\s*(?:AS\s+)?{re.escape(str(j['name']))}\b", source, re.I)
               or _in_source(str(j.get("to") or ""), source))
        if not hit:
            out.append(f"dimension {d['name']!r} needs join {j['name']!r} ({j.get('to')}), "
                       f"which the FROM clause does not make")
    return out
