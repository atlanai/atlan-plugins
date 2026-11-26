# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Databricks metric view reader -> the `bi_facts` contract, declarations included.

PROVENANCE. Written for this repo; not ported. The precedent it follows is the Snowflake
package's `semantic_view.py`: a governed model with no report layer, read into the same
contract every other BI reader writes, so nothing downstream branches on vendor. Design:
`docs/design/declared-models-contract.md`.

FOUR INPUT SHAPES, ONE READER. All carry the same YAML definition:

  * **describe**: the document `DESCRIBE TABLE EXTENDED <view> AS JSON` returns (`view_text`
    is the YAML), or the one Unity Catalog's table API returns (`view_definition`). Its
    `columns` carry each field's and measure's type.
  * **extended**: a description with what the YAML leaves out beside it,
    `{"metric_view": <describe>, "source_columns": {table: [column]}, "primary_keys":
    {table: [column]}}`: the columns of the tables the view reads (a join written without
    table prefixes cannot be read without them) and each table's key.
  * **yaml**: the definition alone (`version`, `source`, `fields` or `dimensions`,
    `measures`), as it is kept in a repository.
  * **ddl**: `CREATE VIEW ... WITH METRICS LANGUAGE YAML AS $$ ... $$`.

The shape is detected from the content and never guessed.

**ONE SOURCE, NAMED `source`.** The view's source table is the role `source`, as the view
itself calls it. Each join is a role under its own name; a nested join is named by its path
(`customer.nation` is the role `customer__nation`). A many-to-one join is one declared
relationship on every key column pair it names; a one-to-many join is carried as one and never
walked, so a measure that needs it is refused by name.

**EVERY COLUMN REFERENCE IS QUALIFIED HERE.** A metric view writes a source column bare
(`SUM(o_totalprice)`); a statement with joins cannot. Each expression is parsed in the
Databricks dialect and every column is bound to its role, so `lib/declared` places it like any
other SQL measure. A bare name that an EARLIER field defines is that field; any other bare name
is a column of the source.

**`MEASURE(x)` IS REPLACED HERE, AND A MEASURE IS NOT A NAME.** A metric view names another
measure only as `MEASURE(x)`; a bare `x` is always a column or a field. So this reader puts
the referenced measure's expression in place of each `MEASURE(x)` and marks every measure
`referable: false`: `lib/declared` then never reads `source.revenue` as the measure `revenue`,
which it would otherwise (`revenue: SUM(revenue)` is a common spelling).

**THE VIEW'S FILTER IS PART OF EVERY MEASURE.** A top-level `filter` applies to every query of
the view. The declaration has no field for a model-wide predicate, so it is written into each
measure: every aggregate gains `FILTER (WHERE <filter>)`. The number is the view's. A measure
whose aggregates cannot all be seen (an unknown function) is not read, and a note says so.

**WHAT IS NOT READ, EACH SAID BY NAME** in `_reader_notes`, never dropped silently: a window
measure and every measure built on one (its frame is applied when the view is queried), an
expression that names a `parameters` entry, a wildcard field, a column that needs quoting, a
join whose source is a query, and a join condition that is not equalities between the two
tables. A view whose own source is a query or another metric view is refused whole.

A metric view declares no key. The extended shape carries its source table's; otherwise the
grain is the card author's to state.

Deterministic: the same file always yields the same facts, in the file's own order.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from metrics_agent import sockets as S
from metrics_agent.lib import declared as _declared
from metrics_agent.plugs.readers.bicompile import FormulaCompiler

VENDOR = "databricks-metric-view"
DIALECT = "databricks"
SUFFIXES = (".json", ".yaml", ".yml", ".sql")
SOURCE_ROLE = "source"
VERSIONS = ("0.1", "1.1")

_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_DDL = re.compile(r"(?is)\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                  r"(?P<name>[`\w.$-]+).*?\bWITH\s+METRICS\b.*?\$\$(?P<yaml>.*)\$\$")
_QUERY = re.compile(r"(?is)^\s*\(?\s*(SELECT|WITH)\b")
_CARD = {"many_to_one": "M:1", "one_to_many": "1:M"}


class MetricViewError(S.ReaderRefused):
    """A file this reader cannot read as a metric view, said in words an operator can act on
    (`sockets.ReaderRefused`)."""


class _Skip(Exception):
    """One field, measure or join this reader does not read; the message is the note."""


# ── loading ──────────────────────────────────────────────────────────────────────────────

def _yaml(text: str, where: str) -> dict:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MetricViewError(f"{where} is not readable YAML: {type(exc).__name__}") from None
    if not isinstance(doc, dict):
        raise MetricViewError(f"{where} is not a YAML mapping")
    return doc


def load(path: pathlib.Path) -> tuple[dict, dict, str]:
    """(the view's YAML definition, what came with it, the input shape). What came with it:
    `name`, `columns` (the view's own, typed), `source_columns` and `primary_keys` (by table),
    each present only when the input carries it."""
    src = pathlib.Path(path)
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise MetricViewError(f"{src} is not readable: {type(exc).__name__}") from None
    if src.suffix.lower() == ".json":
        try:
            doc = json.loads(text)
        except ValueError:
            raise MetricViewError(f"{src} is not readable JSON") from None
        if not isinstance(doc, dict):
            raise MetricViewError(f"{src} is not a JSON object")
        shape = "extended" if isinstance(doc.get("metric_view"), dict) else "describe"
        desc = doc["metric_view"] if shape == "extended" else doc
        text = desc.get("view_text") or desc.get("view_definition")
        if not isinstance(text, str):
            raise MetricViewError(f"{src}: not a metric view description: expected the JSON of "
                                  "`DESCRIBE TABLE EXTENDED <view> AS JSON`, with `view_text`")
        parts = [desc.get("catalog_name"), desc.get("schema_name"),
                 desc.get("table_name") or desc.get("name")]
        name = ".".join(str(p) for p in parts if p) or src.stem
        extra = {"name": name, "columns": desc.get("columns") or [],
                 "source_columns": doc.get("source_columns") or {},
                 "primary_keys": doc.get("primary_keys") or {}}
        return _yaml(text, f"{src}: the view's YAML"), extra, shape
    ddl = _DDL.search(text)
    if ddl:
        return (_yaml(ddl.group("yaml"), f"{src}: the view's YAML"),
                {"name": ddl.group("name").replace("`", "")}, "ddl")
    return _yaml(text, str(src)), {"name": src.stem}, "yaml"


def is_metric_view(doc: Any) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("source"), str)
            and any(isinstance(doc.get(k), list) for k in ("measures", "fields", "dimensions")))


def models(path: pathlib.Path) -> list[pathlib.Path]:
    """The views a BI input holds: the file itself, or every view file in a folder (one model
    each), sorted."""
    path = pathlib.Path(path)
    if not path.is_dir():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)


def _three_part(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text or _QUERY.match(text):
        return None
    parts = [p.strip().strip("`") for p in text.split(".")]
    return ".".join(parts) if len(parts) == 3 and all(parts) else None


# ── names and expressions ────────────────────────────────────────────────────────────────

def _ident(name: str) -> str:
    """One join name as part of a role name: a plain identifier, as `lib/declared` reads one."""
    out = re.sub(r"[^A-Za-z0-9_$]", "_", str(name)).strip("_") or "join"
    return out if not out[0].isdigit() else f"j_{out}"


def _parse(sql: Any, what: str) -> exp.Expression:
    text = str(sql if sql is not None else "").strip()
    if not text:
        raise _Skip(f"{what} has no expression")
    try:
        return sqlglot.parse_one(text, read=DIALECT)
    except Exception:  # noqa: BLE001 - an expression the parser cannot read is not read
        raise _Skip(f"{what} is not SQL the parser reads") from None


def _sql(tree: exp.Expression) -> str:
    return tree.sql(dialect=DIALECT)


def _is_measure_call(node: exp.Expression) -> bool:
    return isinstance(node, exp.Anonymous) and str(node.name).upper() == "MEASURE"


class _View:
    """One metric view being read: its roles, and the fields and measures read so far."""

    def __init__(self, doc: dict, extra: dict) -> None:
        self.doc = doc
        self.extra = extra
        self.notes: list[str] = []
        # role (lower) -> {"name", "physical", "path": the join path as the view writes it}
        self.roles: dict[str, dict] = {}
        self.by_path: dict[tuple[str, ...], str] = {}
        self.relationships: list[dict] = []
        self.parameters = {str(p.get("name") or "").lower()
                           for p in doc.get("parameters") or [] if isinstance(p, dict)}
        # field name (lower) -> {"name", "role", "tree", "reads", "plain"}; in definition order.
        self.fields: dict[str, dict] = {}
        # measure name (lower) -> the measure's expression, columns bound and every
        # `MEASURE()` replaced, before the view's filter is written in.
        self.measures: dict[str, exp.Expression] = {}
        self.measure_names: dict[str, str] = {}
        self.unread: set[str] = set()                 # measures named but not read
        self.unread_fields: set[str] = set()
        # what reads what: {"measure 'a'": {"field 'b'", "measure 'c'"}}. Something that is
        # dropped takes everything built on it with it.
        self.deps: dict[str, set[str]] = {}
        # (role, COLUMN) every expression reads as a COLUMN OF THE TABLE, by what read it.
        self.physical: dict[tuple[str, str], list[str]] = {}

    # -- roles ---------------------------------------------------------------------------
    def columns_of(self, role: str) -> set[str] | None:
        """The table's own columns (lower), when the input carries them."""
        cols = {str(k).lower(): v for k, v in (self.extra.get("source_columns") or {}).items()}
        got = cols.get(self.roles[role]["physical"].lower())
        return {str(c.get("name") if isinstance(c, dict) else c).lower() for c in got} \
            if got else None

    def add_source(self) -> None:
        raw = self.doc.get("source")
        physical = _three_part(raw)
        if not physical:
            what = ("a query" if _QUERY.match(str(raw or "")) else
                    "not a CATALOG.SCHEMA.TABLE name")
            raise MetricViewError(f"the metric view's source is {what}; this reader reads a "
                                  "view over one table, with joins to others")
        self.roles[SOURCE_ROLE] = {"name": SOURCE_ROLE, "physical": physical, "path": ()}
        self.by_path[(SOURCE_ROLE,)] = SOURCE_ROLE

    def add_joins(self, joins: Any, parent: str, path: tuple[str, ...]) -> None:
        for j in joins if isinstance(joins, list) else []:
            if not isinstance(j, dict) or not j.get("name"):
                self.notes.append("a join without a name is not read")
                continue
            name = str(j["name"])
            here = path + (name.lower(),)
            role = "__".join(_ident(p) for p in here)
            label = ".".join(here)
            physical = _three_part(j.get("source"))
            if not physical:
                self.notes.append(f"join {label!r} reads a query, not a table; it is not "
                                  f"read, and nothing that needs it is placed")
                continue
            if role.lower() in self.roles:
                self.notes.append(f"join {label!r} has the name of another join; it is not read")
                continue
            self.roles[role.lower()] = {"name": role, "physical": physical, "path": here}
            self.by_path[here] = role.lower()
            try:
                pairs = self._pairs(j, parent, role.lower(), name)
            except _Skip as exc:
                self.notes.append(f"join {label!r} {exc}; nothing that needs it is placed")
                pairs = []
            if pairs:
                card = str(j.get("cardinality") or "many_to_one").lower()
                self.relationships.append({
                    "from_table": self.roles[parent]["physical"], "from_column": pairs[0][0],
                    "to_table": physical, "to_column": pairs[0][1], "is_active": True,
                    "cardinality": _CARD.get(card, "unknown"), "kind": "relationship",
                    "name": f"{self.roles[parent]['name']}_to_{role}",
                    "from_role": self.roles[parent]["name"], "to_role": role,
                    "columns": [{"from_column": a, "to_column": b} for a, b in pairs]})
                if card != "many_to_one":
                    self.notes.append(f"join {label!r} is {card}: it is not walked, and a "
                                      f"measure that reads it is not placed")
            self.add_joins(j.get("joins"), role.lower(), here)

    def _pairs(self, join: dict, parent: str, role: str, name: str) -> list[tuple[str, str]]:
        """Every (parent column, joined column) pair of one join."""
        using = join.get("using")
        if using:
            return [(str(c), str(c)) for c in (using if isinstance(using, list) else [using])]
        # YAML 1.1 reads a bare `on:` key as the boolean true.
        cond = join.get("on", join.get(True))
        if cond in (None, ""):
            raise _Skip("has neither `on` nor `using`")
        tree = _parse(cond, "its condition")
        terms, stack = [], [tree]
        while stack:
            node = stack.pop()
            if isinstance(node, exp.Paren):
                stack.append(node.this)
            elif isinstance(node, exp.And):
                stack += [node.expression, node.this]
            else:
                terms.append(node)
        parent_names = {SOURCE_ROLE} if parent == SOURCE_ROLE else \
            {self.roles[parent]["path"][-1], ".".join(self.roles[parent]["path"])}
        joined_names = {name.lower(), ".".join(self.roles[role]["path"])}
        out = []
        for term in terms:
            if not (isinstance(term, exp.EQ) and isinstance(term.this, exp.Column)
                    and isinstance(term.expression, exp.Column)):
                raise _Skip("is not written as equalities between the two tables")
            sides = [self._side(c, parent, role, parent_names, joined_names)
                     for c in (term.this, term.expression)]
            known = [s for s, _ in sides if s]
            if not known:
                raise _Skip("names its columns without a table prefix, and the tables' "
                            "columns did not come with the view: prefix each side, or name "
                            "them in `source_columns`")
            if len(known) == 1:                       # the other column is the other table's
                sides = [(s or ("joined" if known[0] == "parent" else "parent"), c)
                         for s, c in sides]
            if {s for s, _ in sides} != {"parent", "joined"}:
                raise _Skip("compares two columns of one table")
            by = dict(sides)
            out.append((by["parent"], by["joined"]))
        return out

    def _side(self, col: exp.Column, parent: str, role: str, parent_names: set,
              joined_names: set) -> tuple[str | None, str]:
        parts = [p.name for p in col.parts]
        name = parts[-1]
        if not _PLAIN.fullmatch(name):
            raise _Skip(f"names the column `{name}`, which needs quoting")
        prefix = ".".join(parts[:-1]).lower()
        if prefix:
            if prefix in joined_names:
                return "joined", name
            if prefix in parent_names:
                return "parent", name
            raise _Skip(f"names `{prefix}`, which is neither of the two tables")
        mine, theirs = self.columns_of(role), self.columns_of(parent)
        if mine is None or theirs is None:
            return None, name
        # Databricks reads a bare name in `on` as the joined table's when it has one.
        if name.lower() in mine:
            return "joined", name
        return ("parent", name) if name.lower() in theirs else (None, name)

    # -- expressions ---------------------------------------------------------------------
    def bind(self, raw: Any, what: str, *, own: str | None = None,
             measures: bool = False) -> tuple[exp.Expression, set[str]]:
        """(`raw` with every column bound to its role and every `MEASURE()` replaced, the
        roles it reads). `own` is the field being defined: its own name inside its own
        expression is the column. Raises _Skip, naming why."""
        tree = _parse(raw, what)
        if isinstance(tree, exp.Star) or (isinstance(tree, exp.Column)
                                          and isinstance(tree.this, exp.Star)):
            raise _Skip(f"{what} is a wildcard; its columns are not listed in the view")
        if tree.find(exp.Window):
            raise _Skip(f"{what} uses a window function")
        reads: set[str] = set()
        deps = self.deps.setdefault(what, set())

        def measure(node: exp.Expression) -> exp.Expression:
            args = node.expressions
            ref = args[0] if len(args) == 1 and isinstance(args[0], exp.Column) else None
            key = ref.name.lower() if ref is not None and len(ref.parts) == 1 else None
            if not measures:
                raise _Skip(f"{what} reads a measure; only a measure can")
            if key is None or key not in self.measures:
                named = _sql(args[0]) if args else ""
                why = ("which is not read" if key in self.unread else
                       "which is not an earlier measure of this view")
                raise _Skip(f"{what} reads MEASURE({named}), {why}")
            inner = self.measures[key].copy()
            reads.update(_declared.qualifiers(_sql(inner)))
            deps.add(f"measure {self.measure_names[key]!r}")
            return exp.Paren(this=inner)

        def column(node: exp.Column) -> exp.Expression:
            parts = [p.name for p in node.parts]
            lower = [p.lower() for p in parts]
            role, rest = None, parts
            for k in range(len(parts) - 1, 0, -1):    # the longest join path that prefixes it
                if tuple(lower[:k]) in self.by_path:
                    role, rest = self.by_path[tuple(lower[:k])], parts[k:]
                    break
            if role is None and len(parts) == 1:
                name = lower[0]
                if name in self.parameters:
                    raise _Skip(f"{what} reads the parameter `{parts[0]}`")
                if name in self.unread_fields and name != own:
                    raise _Skip(f"{what} reads the field `{parts[0]}`, which is not read")
                field = self.fields.get(name)
                if field is not None and name != own:
                    reads.update(field["reads"])
                    deps.add(f"field {field['name']!r}")
                    if field["plain"]:
                        return exp.column(field["name"], table=field["role_name"])
                    # A field whose name needs quoting cannot be named in a formula
                    # `lib/declared` reads: its expression stands in its place.
                    return exp.Paren(this=field["tree"].copy())
            if role is None and len(parts) > 1:
                raise _Skip(f"{what} names `{'.'.join(parts)}`: `{parts[0]}` is neither the "
                            f"source nor a join this reader read")
            role = role or SOURCE_ROLE
            if len(rest) > 1:
                raise _Skip(f"{what} reads a nested value, `{'.'.join(rest)}`")
            if not _PLAIN.fullmatch(rest[0]):
                raise _Skip(f"{what} names the column `{rest[0]}`, which needs quoting")
            reads.add(role)
            self.physical.setdefault((role, rest[0].upper()), []).append(what)
            bound = exp.column(rest[0], table=self.roles[role]["name"])
            bound.meta["physical"] = True
            return bound

        def visit(node: exp.Expression) -> exp.Expression:
            if _is_measure_call(node):
                return measure(node)
            if isinstance(node, exp.Column):
                return column(node)
            return node

        return tree.transform(visit, copy=True), reads


def _filtered(tree: exp.Expression, cond: exp.Expression, what: str) -> exp.Expression:
    """`tree` with the view's filter written into every aggregate. Raises _Skip when an
    aggregate could be hidden in a function the parser does not know."""
    unknown = sorted({str(n.name).upper() for n in tree.find_all(exp.Anonymous)})
    if unknown:
        raise _Skip(f"{what} calls {', '.join(unknown)}, which may aggregate: the view's "
                    f"filter cannot be written into it")

    def visit(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Filter):
            where = node.expression
            where.set("this", exp.and_(exp.paren(where.this.copy()), exp.paren(cond.copy())))
            return node
        if isinstance(node, exp.AggFunc) and not isinstance(node.parent, exp.Filter) \
                and node.find_ancestor(exp.AggFunc) is None:
            return exp.Filter(this=node.copy(), expression=exp.Where(this=cond.copy()))
        return node

    return tree.transform(visit, copy=True)


# ── types ────────────────────────────────────────────────────────────────────────────────

def _types(columns: list) -> dict[str, str]:
    """{field or measure name (lower): its type, as the view's description declares it}."""
    out = {}
    for c in columns or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        t = c.get("type") or c.get("type_text")       # DESCRIBE's, or the table API's
        name = str((t.get("name") if isinstance(t, dict) else t) or "").upper()
        if isinstance(t, dict) and name == "DECIMAL" and t.get("precision") is not None:
            name = f"DECIMAL({t['precision']},{t.get('scale') or 0})"
        if name:
            out[str(c["name"]).lower()] = "BIGINT" if name == "LONG" else name
    return out


def _words(item: dict) -> list[str]:
    out = [str(x) for x in item.get("synonyms") or [] if str(x).strip()]
    shown = str(item.get("display_name") or "").strip()
    if shown and shown.lower() not in {str(item.get("name") or "").lower(),
                                       *(w.lower() for w in out)}:
        out.insert(0, shown)
    return out


def _name(item: dict, kind: str) -> str:
    """An item's name, or the name Databricks derives when it has none: a column's own."""
    if item.get("name") not in (None, ""):
        return str(item["name"])
    try:
        tree = sqlglot.parse_one(str(item.get("expr") or ""), read=DIALECT)
    except Exception:  # noqa: BLE001
        tree = None
    if isinstance(tree, exp.Column):
        return tree.name
    raise _Skip(f"a {kind} without a name ({str(item.get('expr') or '')[:60]!r}) is not read")


# ── the parse ────────────────────────────────────────────────────────────────────────────

def parse(path: pathlib.Path, report_index: dict | None = None, *,
          dialect: str | None = None) -> dict[str, Any]:
    """`report_index` is accepted and ignored: a metric view has no report layer. A metric
    view's SQL is Databricks SQL, whatever the run's `dialect`."""
    src = pathlib.Path(path)
    doc, extra, shape = load(src)
    if not is_metric_view(doc):
        raise MetricViewError(f"{src}: not a metric view: expected `source` and `measures` "
                              "(with `fields` or `dimensions`)")
    name = str(extra.get("name") or src.stem)
    version = str(doc.get("version") or "").strip()
    view = _View(doc, extra)
    if version not in VERSIONS:
        view.notes.append(f"specification version {version or '(none)'!r} is not one this "
                          f"reader was written for ({', '.join(VERSIONS)}); read as 1.1")
    try:
        view.add_source()
    except MetricViewError as exc:
        raise MetricViewError(f"{src}: {exc}") from None
    view.add_joins(doc.get("joins"), SOURCE_ROLE, ())
    types = _types(extra.get("columns") or [])
    columns: dict[str, list[dict]] = {r: [] for r in view.roles}
    not_carried = {"formats": 0}

    # FIELDS, in the view's order: a field may read only the ones before it.
    for item in doc.get("fields") or doc.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        try:
            fname = _name(item, "field")
            what = f"field {fname!r}"
            tree, reads = view.bind(item.get("expr"), what, own=fname.lower())
        except _Skip as exc:
            view.notes.append(f"{exc}; it is not read")
            if item.get("name"):
                view.unread_fields.add(str(item["name"]).lower())
            continue
        others = [r for r in sorted(reads) if r != SOURCE_ROLE]
        role = others[0] if others else SOURCE_ROLE
        if len(others) > 1:
            view.notes.append(f"field {fname!r} reads {len(others)} joined tables; it is "
                              f"listed under {view.roles[role]['name']}")
        role_name = view.roles[role]["name"]
        # One column of one table, as written: a plain column under the field's name.
        simple = isinstance(tree, exp.Column) and bool(tree.meta.get("physical"))
        col: dict[str, Any] = {"name": fname, "data_type": types.get(fname.lower()),
                               "role": "dimension", "is_calculated": not simple,
                               "source_column": tree.name if simple else fname}
        if not simple:
            col["formula"] = _sql(tree)
            col["formula_language"] = "sql"
        if item.get("comment"):
            col["description"] = str(item["comment"])
        if _words(item):
            col["synonyms"] = _words(item)
        not_carried["formats"] += bool(item.get("format"))
        columns[role].append(col)
        view.fields[fname.lower()] = {"name": fname, "role": role, "role_name": role_name,
                                      "tree": tree, "reads": reads | {role},
                                      "plain": bool(_PLAIN.fullmatch(fname)),
                                      "computed": not simple or tree.name.lower() != fname.lower()}

    # THE VIEW'S FILTER, bound like any expression: it may name a field.
    cond = None
    if doc.get("filter") not in (None, ""):
        try:
            cond, _reads = view.bind(doc["filter"], "the view's filter")
        except _Skip as exc:
            raise MetricViewError(f"{src}: {exc}; every measure of the view depends on it, "
                                  "so the view is not read") from None

    measures: list[dict] = []
    for item in doc.get("measures") or []:
        if not isinstance(item, dict):
            continue
        try:
            mname = _name(item, "measure")
        except _Skip as exc:
            view.notes.append(f"{exc}")
            continue
        what = f"measure {mname!r}"
        try:
            if item.get("window"):
                raise _Skip(f"{what} is a window measure: its frame is applied when the view "
                            f"is queried")
            tree, _reads = view.bind(item.get("expr"), what, measures=True)
            final = _filtered(tree, cond, what) if cond is not None else tree
        except _Skip as exc:
            view.notes.append(f"{exc}; it is not read")
            view.unread.add(mname.lower())
            continue
        view.measures[mname.lower()] = tree
        view.measure_names[mname.lower()] = mname
        m: dict[str, Any] = {"name": mname, "home_table": SOURCE_ROLE, "formula": _sql(final),
                             "formula_language": "sql", "aggregation_at_use_site": False,
                             # A metric view names a measure only as MEASURE(x), replaced
                             # above: `source.<name>` in a formula is always a column.
                             "referable": False}
        if item.get("comment"):
            m["description"] = str(item["comment"])
        if _words(item):
            m["synonyms"] = _words(item)
        not_carried["formats"] += bool(item.get("format"))
        measures.append(m)

    if not measures:
        why = f" ({len(view.unread)} not read: see the reader's notes)" if view.unread else ""
        raise MetricViewError(f"{src}: the metric view declares no measure this reader "
                              f"reads{why}" + "".join(f"\n  {n}" for n in view.notes))

    # A COMPUTED FIELD WITH A COLUMN'S NAME. `lib/declared` reads `role.name` as the field
    # wherever it appears. Where an expression reads the COLUMN of that name (written before
    # the field is defined, or under its table's name), the two cannot be told apart, so what
    # reads it is dropped by name rather than computed from the wrong one.
    clash: dict[str, str] = {}
    for f in view.fields.values():
        if not f["computed"]:
            continue
        own = f"field {f['name']!r}"
        for what in view.physical.get((f["role"], f["name"].upper()), []):
            if what != own:
                clash[what] = f["name"]
    for what, fname in sorted(clash.items()):
        view.notes.append(f"{what} reads the column `{fname}`, and a field of that name "
                          f"redefines it; it is not read")
    dropped = set(clash)
    grew = True
    while grew:                                       # and everything built on what was dropped
        grew = False
        for what, needs in sorted(view.deps.items()):
            hit = sorted(needs & dropped)
            if hit and what not in dropped:
                dropped.add(what)
                grew = True
                view.notes.append(f"{what} reads {hit[0]}, which is not read; it is not read")
    if "the view's filter" in dropped:
        raise MetricViewError(f"{src}: the view's filter cannot be read, and every measure of "
                              "the view depends on it" + "".join(f"\n  {n}" for n in view.notes))
    measures = [m for m in measures if f"measure {m['name']!r}" not in dropped]
    for role in columns:
        columns[role] = [c for c in columns[role] if f"field {c['name']!r}" not in dropped]
    if not measures:
        raise MetricViewError(f"{src}: the metric view declares no measure this reader reads"
                              + "".join(f"\n  {n}" for n in view.notes))

    keys = {str(k).lower(): [str(c) for c in v or []]
            for k, v in (extra.get("primary_keys") or {}).items()}
    tables: list[dict] = []
    for role, r in view.roles.items():
        cols = columns[role]
        pk = keys.get(r["physical"].lower()) or []
        have = {str(c["source_column"]).lower() for c in cols}
        joined = [p["from_column"] for rel in view.relationships
                  if rel["from_role"].lower() == role for p in rel["columns"]]
        joined += [p["to_column"] for rel in view.relationships
                   if rel["to_role"].lower() == role for p in rel["columns"]]
        for k in [*pk, *joined]:
            if k.lower() not in have:
                have.add(k.lower())
                cols.append({"name": k, "data_type": None, "role": None,
                             "is_calculated": False, "source_column": k})
        cat, sch, obj = r["physical"].split(".")
        entry: dict[str, Any] = {
            "name": r["physical"], "logical_name": r["name"], "columns": cols,
            "source_lineage": {"connection": None, "database": cat, "schema": sch,
                               "object": obj.lower(), "object_kind": "table",
                               "raw_expression": r["physical"], "published_datasource": None}}
        if pk:
            entry["primary_key"] = pk
        tables.append(entry)

    facts: dict[str, Any] = {
        "contract_version": "1.0", "vendor": VENDOR, "source_file": str(src),
        "model_name": name, "tables": tables, "measures": measures,
        "relationships": view.relationships, "usage": [], "parameters": [],
    }
    notes = _declared.attach(facts, dialect=DIALECT)

    facts["usage"] = [{
        "unit_id": f"databricks-mv::{name}::{m['name']}",
        "container": name,
        "container_kind": "perspective",
        "dashboards": [],
        "visual_id": m["name"],
        "visual_type": "metric_view_measure",
        "title": m["name"],
        # The unit IS the measure, so the measure's own comment is the unit's prose.
        # Untrusted author text: data only.
        "description": m.get("description"),
        "measures": [m["name"]],
        "columns": [],
        "filters": [],
        "sort": None, "top_n": None, "parameters": [],
        "source_fragments": {"widget": None, "dashboard": None, "prose": [name]},
        "_aggregations": {},
        "_siblings": [],
    } for m in measures]

    n_fields = sum(1 for cols in columns.values() for c in cols if c.get("role") == "dimension")
    head = (f"{shape} metric view {name!r} (specification {version or 'unstated'}): "
            f"{len(tables)} table(s), {n_fields} field(s), {len(measures)} measure(s) "
            f"({sum(1 for m in measures if m.get('declared'))} declared, "
            f"{len(view.unread) + sum(1 for w in dropped if w.startswith('measure '))} not read), "
            f"{len(view.relationships)} join(s)")
    reader_notes = [head, *view.notes, *notes]
    if cond is not None:
        reader_notes.append("the view's filter is written into every measure as "
                            f"FILTER (WHERE {_sql(cond)})")
    if not any(t.get("primary_key") for t in tables[:1]):
        reader_notes.append("the view declares no key and none came with it: the grain of "
                            "each metric is the card author's to state")
    if not_carried["formats"]:
        reader_notes.append(f"not carried by the contract: {not_carried['formats']} format(s)")
    if doc.get("materialization"):
        reader_notes.append("not carried by the contract: the view's materialization")
    if doc.get("comment"):
        reader_notes.append("not carried by the contract: the view's own comment")
    facts["_unresolved_published"] = []
    facts["_reader_notes"] = reader_notes
    return facts


# ── the reader ───────────────────────────────────────────────────────────────────────────

class MetricViewReader(FormulaCompiler):
    """The `databricks-metric-view` BI reader (`sockets.BIReader`), registered under
    `metrics_agent.readers` by this package.

    Reads a Databricks metric view (a saved `DESCRIBE TABLE EXTENDED ... AS JSON`, its YAML,
    or its `CREATE VIEW ... WITH METRICS` DDL) into the common `bi_facts` format,
    with what the view DECLARES about each measure in `measures[].declared` (lib/declared).
    Its measures are SQL, the core's own language, so it declares no other `languages`; they
    compile through the shared engine, which places a SQL measure in its model by role. A
    folder of views is several models, one per view (`models`)."""

    name = VENDOR
    suffixes = SUFFIXES
    languages: tuple[str, ...] = ()

    def detect(self, path: pathlib.Path) -> bool:
        path = pathlib.Path(path)
        if path.is_dir():
            return any(self.detect(p) for p in models(path))
        if path.suffix.lower() not in SUFFIXES:
            return False
        try:
            doc, _extra, _shape = load(path)
        except MetricViewError:
            return False
        return is_metric_view(doc)

    def models(self, path: pathlib.Path) -> list[pathlib.Path]:
        """One path per view: every view file in a folder; a single file is itself."""
        path = pathlib.Path(path)
        found = models(path)
        if path.is_dir() and not found:
            raise MetricViewError(f"no metric view file (.json, .yaml, .yml, .sql) under {path}")
        return found

    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]:
        """ONE view."""
        return parse(pathlib.Path(path), dialect=opts.get("dialect") or None)
