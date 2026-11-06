# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Snowflake semantic view parser plugin -> the `bi_facts` contract, declarations included.

PROVENANCE. Written for this repo; not ported. The precedent it follows is `metrics_agent/plugs/readers/tabular.py`:
a semantic model with no report layer, read into the same contract every other BI reader
writes, so nothing downstream branches on vendor. Design:
`docs/design/declared-models-contract.md`.

THREE INPUT SHAPES, ONE READER. All describe the same object and differ only in spelling:

  * **native** — Snowflake's semantic model YAML, as `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW`
    returns it and as a hand-written Cortex Analyst model is laid out: top-level `tables`,
    `base_table: {database, schema, table}`, `primary_key: {columns}`, `expr`, and
    `relationships[].relationship_columns`.
  * **catalog** — a reconstruction from catalog metadata: a `semantic_view` header and
    `logical_tables`, `base_table` as one dotted string, `primary_key` as a list, `expression`
    on dimensions and facts, `sql` on metrics.
  * **ddl** — the `CREATE SEMANTIC VIEW` statement itself (`SELECT GET_DDL('SEMANTIC_VIEW', …)`).
    `semantic_view_ddl.py` rewrites it into the native shape first, so it is read by
    exactly the native path.

The shape is detected from the content and never guessed: a file carrying neither `tables`
nor `logical_tables` is refused by name.

**A METRIC'S SQL IS ALREADY SQL.** Every measure is emitted with `formula_language: "sql"`,
verbatim, on its home table by the view's own (logical) name. A3 places it; nothing translates
it. A view-level (derived) metric is placed over the one table every metric it names is
computed over; one that spans tables has no home and is said so.

**WHAT THE VIEW DECLARES IS CARRIED, NOT RE-DERIVED LATER.** Each table's primary key, every
column's and metric's synonyms and comment, `PRIVATE` as `is_hidden`, and each relationship as
ONE row with its name, both roles and every key column pair. Then `lib/declared` writes each
metric's `declared` block (formula expanded, joins, dimensions, grain, FROM clause, and the
view's verified queries that read it, compiled to base-table SQL by
`semantic_view_queries.py`). A metric that cannot be placed has none, and a note says
why.

**NO REPORT LAYER, SO `usage` IS CURATION AND SAYS SO.** As for a `.bim`: one entry per metric
that is not hidden, `container_kind: "perspective"` — the semantic view is literally a curated
perspective over its tables — never an observed-usage kind, and an EMPTY `dashboards` list so
`n_dashboards` stays 0 and curation is never counted as dashboard placement.

**RELATIONSHIPS ARE DECLARED.** One is inferred only when the caller asks (`infer_relationships`,
from policy, off by default) and the view declares none, by `lib/declared.infer_relationships`,
marked `kind: "inferred"`.

**WHAT THE CONTRACT DOES NOT CARRY**, reported in `_reader_notes` rather than dropped silently:
table synonyms, named filters, sample values, verified queries that did not compile, and the
agent instructions (`AI_SQL_GENERATION`, `AI_QUESTION_CATEGORIZATION`).

Deterministic: the same file always yields the same facts, in the file's own order.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

from metrics_agent import sockets as S
from metrics_agent.lib import declared as _declared
from metrics_agent.plugs.readers.bicompile import FormulaCompiler

from metrics_agent_snowflake import semantic_view_ddl
from metrics_agent_snowflake import semantic_view_queries as _queries

VENDOR = "snowflake-semantic-view"
# A semantic view is a file of YAML or of its DDL, whatever it is called.
SUFFIXES = (".sql", ".yaml", ".yml")


class SemanticViewError(S.ReaderRefused):
    """A file this reader cannot read as a semantic view, said in words an operator can act
    on (`sockets.ReaderRefused`)."""


_SIMPLE_COL = re.compile(r'^\s*(?:"?[A-Za-z_][A-Za-z0-9_$]*"?\s*\.\s*)?"?([A-Za-z_][A-Za-z0-9_$]*)"?\s*$')
_CARD = {"many_to_one": "M:1", "one_to_one": "1:1", "one_to_many": "1:M", "many_to_many": "M:M"}


def models(path: pathlib.Path) -> list[pathlib.Path]:
    """The views a BI input holds: the file itself, or every view file in a folder (one model
    each), sorted."""
    path = pathlib.Path(path)
    if not path.is_dir():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)


def load(path: pathlib.Path) -> tuple[dict, bool]:
    """(the native-shape document, whether it came from DDL)."""
    src = pathlib.Path(path)
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise SemanticViewError(f"{src} is not readable: {type(exc).__name__}") from None
    if semantic_view_ddl.is_ddl(text):
        try:
            return semantic_view_ddl.to_native(text), True
        except semantic_view_ddl.SemanticViewDDLError as exc:
            raise SemanticViewError(f"{src}: {exc}") from None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SemanticViewError(f"{src} is not readable YAML: {type(exc).__name__}") from None
    if not isinstance(doc, dict):
        raise SemanticViewError(f"{src} is not a YAML mapping")
    return doc, False


def _shape(doc: dict) -> str:
    if isinstance(doc.get("logical_tables"), list):
        return "catalog"
    if isinstance(doc.get("tables"), list):
        return "native"
    raise SemanticViewError("not a semantic view: expected a top-level `tables` (Snowflake "
                            "semantic model YAML) or `logical_tables` list")


def _base_table(raw: Any, tname: str) -> tuple[str | None, str | None, str]:
    """-> (database, schema, object) from either spelling of `base_table`."""
    if isinstance(raw, dict):
        obj = raw.get("table")
        if not obj:
            raise SemanticViewError(f"table {tname!r}: base_table has no `table`")
        return raw.get("database"), raw.get("schema"), str(obj)
    if isinstance(raw, str) and raw.strip():
        parts = [p.strip().strip('"') for p in raw.split(".")]
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 1:
            return None, None, parts[0]
    raise SemanticViewError(f"table {tname!r}: base_table is not `DB.SCHEMA.OBJECT` or a "
                            f"{{database, schema, table}} mapping")


def _pk(raw: Any) -> list[str]:
    cols = raw.get("columns") if isinstance(raw, dict) else raw
    return [str(c) for c in (cols or [])]


def _expr(item: dict) -> str:
    """The SQL of a dimension, fact or metric under any of the spellings in use."""
    for key in ("expr", "expression", "sql"):
        if item.get(key) not in (None, ""):
            return str(item[key]).strip()
    return ""


def _hidden(item: dict) -> bool:
    return "private" in str(item.get("access_modifier") or "").lower()


def _words(item: dict) -> list[str]:
    return [str(x) for x in item.get("synonyms") or [] if str(x).strip()]


def _column(item: dict, role: str | None) -> dict[str, Any]:
    expr = _expr(item)
    simple = _SIMPLE_COL.match(expr) if expr else None
    col: dict[str, Any] = {
        "name": str(item["name"]),
        "data_type": item.get("data_type"),
        "role": role,
        "is_calculated": not simple,
        # As the view spells it: a formula that names the column reads this.
        "source_column": simple.group(1) if simple else str(item["name"]),
    }
    if expr and not simple:
        col["formula"] = expr
        col["formula_language"] = "sql"
    if item.get("description"):
        col["description"] = str(item["description"])
    if _words(item):
        col["synonyms"] = _words(item)
    if _hidden(item):
        col["is_hidden"] = True
    return col


def _relationships(rels: list[dict], by_role: dict[str, str],
                   physical_of: dict[str, str]) -> list[dict]:
    """Each declared relationship as ONE row: its name, both roles, every key column pair."""
    out: list[dict] = []
    for r in rels:
        left = by_role.get(str(r.get("left_table") or "").lower())
        right = by_role.get(str(r.get("right_table") or "").lower())
        if not (left and right):
            raise SemanticViewError(f"relationship {r.get('name')!r} names a table that is "
                                    f"not in the semantic view")
        pairs = [{"from_column": str(p["left_column"]), "to_column": str(p["right_column"])}
                 for p in r.get("relationship_columns") or []]
        if not pairs:
            raise SemanticViewError(f"relationship {r.get('name')!r} names no columns")
        kind = str(r.get("relationship_kind") or "relationship")
        row: dict[str, Any] = {
            "from_table": physical_of[left], "from_column": pairs[0]["from_column"],
            "to_table": physical_of[right], "to_column": pairs[0]["to_column"],
            "is_active": True,
            "cardinality": _CARD.get(str(r.get("relationship_type") or "").lower(), "unknown"),
            "kind": kind, "name": str(r.get("name") or f"{left}_to_{right}"),
            "from_role": left, "to_role": right, "columns": pairs}
        if kind in ("range", "asof"):
            row["condition"] = str(r.get("condition") or "")
        out.append(row)
    return out


def parse(path: pathlib.Path, report_index: dict | None = None, *,
          infer_relationships: bool = False, dialect: str | None = None) -> dict[str, Any]:
    """`report_index` is accepted and ignored: a semantic view has no report layer. `dialect`
    is the run's (declared at S0, never assumed here): the view's own SQL is read in it to
    learn which columns it treats as dates or flags."""
    src = pathlib.Path(path)
    doc, ddl = load(src)
    shape = _shape(doc)
    head = doc.get("semantic_view") if shape == "catalog" else doc
    head = head if isinstance(head, dict) else {}
    view = str(head.get("name") or src.stem)
    raw_tables = doc["logical_tables"] if shape == "catalog" else doc["tables"]

    by_role = {str(t["name"]).lower(): str(t["name"]) for t in raw_tables if t.get("name")}
    tables: list[dict] = []
    measures: list[dict] = []
    physical_of: dict[str, str] = {}
    notes: dict[str, int] = {"table synonyms": 0, "named filters": 0, "sample values": 0}

    for t in raw_tables:
        tname = str(t.get("name") or "")
        if not tname:
            raise SemanticViewError("a table has no name")
        db, sch, obj = _base_table(t.get("base_table"), tname)
        pk = _pk(t.get("primary_key"))
        cols: list[dict] = []
        for key, role in (("dimensions", "dimension"), ("time_dimensions", "dimension"),
                          ("facts", "measure")):
            for item in t.get(key) or []:
                cols.append(_column(item, role))
                notes["sample values"] += bool(item.get("sample_values"))
        have = {str(c["source_column"]).lower() for c in cols}
        for k in pk:
            if k.lower() not in have:
                cols.append({"name": k, "data_type": None, "role": None,
                             "is_calculated": False, "source_column": k})
        notes["named filters"] += len(t.get("filters") or [])
        notes["table synonyms"] += len(t.get("synonyms") or [])
        # The contract's convention (see lib/bi_scope.py): by the time a reader returns, a
        # table's `name` is its PHYSICAL target and `logical_name` keeps the model's own name,
        # which is what a measure's `home_table` and a formula's qualifiers refer to.
        physical = ".".join(p for p in (db, sch, obj) if p)
        physical_of[tname] = physical
        entry: dict[str, Any] = {
            "name": physical, "logical_name": tname, "columns": cols,
            "source_lineage": {"connection": None, "database": db, "schema": sch,
                               "object": obj.lower(), "object_kind": "table",
                               "raw_expression": physical,
                               "published_datasource": None},
        }
        if t.get("description"):
            entry["description"] = str(t["description"])
        if pk:
            entry["primary_key"] = pk
        tables.append(entry)
        for msr in t.get("metrics") or []:
            measures.append(_measure(msr, tname))

    for msr in doc.get("metrics") or []:            # view-level (derived) metrics
        measures.append(_measure(msr, None))

    if not measures:
        raise SemanticViewError(f"{src}: the semantic view declares no metrics")

    facts: dict[str, Any] = {
        "contract_version": "1.0", "vendor": VENDOR, "source_file": str(src),
        "model_name": view, "tables": tables, "measures": measures,
        "relationships": _relationships(doc.get("relationships") or [], by_role, physical_of),
        "usage": [], "parameters": [],
    }
    if not facts["relationships"] and infer_relationships:
        facts["relationships"] = _declared.infer_relationships(facts)

    reader_notes: list[str] = []
    # A VIEW-LEVEL METRIC is computed over the one table every metric it names is computed
    # over. One that spans tables has no single home, and is said so rather than placed.
    model = _declared.Model.of(facts)
    for m in measures:
        if m["home_table"] is None:
            home = model.measure_home(m["formula"])
            if home is None:
                reader_notes.append(f"view-level metric {m['name']!r} is computed over more "
                                    f"than one table; it is not placed")
                continue
            m["home_table"] = model.roles[home].name

    examples, failed = _queries.compile_all(_declared.Model.of(facts), doc.get("verified_queries")
                                            or [])
    reader_notes += [f"verified query {name!r} not compiled: {why}" for name, why in failed]
    reader_notes += _declared.attach(facts, dialect=dialect or "", examples=examples)

    facts["usage"] = [{
        "unit_id": f"snowflake-sv::{view}::{m['name']}",
        "container": view,
        "container_kind": "perspective",
        "dashboards": [],
        "visual_id": m["name"],
        "visual_type": "semantic_view_metric",
        "title": m["name"],
        # The unit IS the metric, so the metric's own description is the unit's prose — the
        # field A2 carries to C3 as the widget description. Untrusted author text: data only.
        "description": m.get("description"),
        "measures": [m["name"]],
        "columns": [],
        "filters": [],
        "sort": None, "top_n": None, "parameters": [],
        "source_fragments": {"widget": None, "dashboard": None, "prose": [view]},
        "_aggregations": {},
        "_siblings": [],
    } for m in measures if not m.get("is_hidden")]

    n_declared = sum(1 for m in measures if m.get("declared"))
    n_hidden = sum(1 for m in measures if m.get("is_hidden"))
    reader_notes.insert(0, f"{'ddl' if ddl else shape} semantic view {view!r}: {len(tables)} "
                           f"tables, {len(measures)} metrics ({n_hidden} hidden, {n_declared} "
                           f"declared), {len(facts['relationships'])} relationships "
                           f"({sum(r['kind'] == 'inferred' for r in facts['relationships'])} "
                           f"inferred), {len(examples) - len(failed)}/{len(examples)} verified "
                           f"queries compiled")
    for what, n in notes.items():
        if n:
            reader_notes.append(f"not carried by the contract: {n} {what}")
    for key, text_ in (doc.get("instructions") or {}).items():
        if key != "comment" and text_:
            reader_notes.append(f"not carried by the contract: {key} ({len(text_.split())} "
                                f"words of agent instructions)")
    facts["_unresolved_published"] = []
    facts["_reader_notes"] = reader_notes
    return facts


def _measure(msr: dict, home: str | None) -> dict[str, Any]:
    name = str(msr.get("name") or "")
    formula = _expr(msr)
    if not (name and formula):
        raise SemanticViewError(f"metric {name or '(unnamed)'!r} has no SQL expression")
    m: dict[str, Any] = {"name": name, "home_table": home, "formula": formula,
                         "formula_language": "sql", "aggregation_at_use_site": False}
    if msr.get("description"):
        m["description"] = str(msr["description"])
    if _words(msr):
        m["synonyms"] = _words(msr)
    if _hidden(msr):
        m["is_hidden"] = True
    return m


# ── the reader ───────────────────────────────────────────────────────────────────────────
# This package's own policy default (`artifacts.POLICY`): inferring a join the view does not
# declare is an ADMISSION choice, so it is policy, off unless the operator's policy says so.
INFER_KEY = "semantic_view.infer_relationships_from_primary_keys"


def _infer(policy) -> bool:
    if policy is None:
        return False
    try:
        return bool(policy.get(INFER_KEY))
    except Exception:  # noqa: BLE001 - a policy without the key infers nothing
        return False


class SemanticViewReader(FormulaCompiler):
    """The `snowflake-semantic-view` BI reader (`sockets.BIReader`), registered under
    `metrics_agent.readers` by this package.

    Reads a Snowflake semantic view (native YAML, a catalog reconstruction, or its `CREATE
    SEMANTIC VIEW` DDL) into the common `bi_facts` format, with what the view DECLARES about
    each metric in `measures[].declared` (lib/declared). Its measures are SQL, the core's own
    language, so it declares no other `languages`; they compile through the shared engine,
    which places a SQL measure in its model by role rather than translating it. A folder of
    views is several models, one per view (`models`)."""

    name = VENDOR
    suffixes = SUFFIXES
    languages: tuple[str, ...] = ()

    def detect(self, path: pathlib.Path) -> bool:
        path = pathlib.Path(path)
        if path.is_dir():
            return bool(models(path))
        return path.suffix.lower() in SUFFIXES

    def models(self, path: pathlib.Path) -> list[pathlib.Path]:
        """One path per view: every view file in a folder; a single file is itself."""
        path = pathlib.Path(path)
        found = models(path)
        if path.is_dir() and not found:
            raise SemanticViewError(f"no semantic view file (.sql, .yaml, .yml) under {path}")
        return found

    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]:
        """ONE view. `dialect` is the run's (the view's own SQL is read in it); `policy` says
        whether a join the view does not declare may be inferred from shared primary keys."""
        return parse(pathlib.Path(path), dialect=opts.get("dialect") or None,
                     infer_relationships=_infer(opts.get("policy")))
