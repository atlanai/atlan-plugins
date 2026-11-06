# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Power BI parser — a semantic-model extract -> the vendor-neutral bi_facts contract.

PROVENANCE. The TMDL and M-partition readers follow the shape of an earlier project's (copied,
never imported). The M-lineage mining is no longer inline:
it lives in `readers/mquery.py`, shared with every other Power BI reader, because the same
expressions arrive here from a TMDL partition, from a `.pbix` zip and from a `.bim`, and a
target one reader can name and another cannot is a parser bug rather than a format
difference.

Deliberately left behind from that pipeline: its `.pbix` binary path, its extract-inventory
collapsing, and its Atlan presigned-URL fetch — each of those is a piece of work of its own
rather than something this parser should grow.

WHAT MAKES POWER BI DIFFERENT FROM TABLEAU: a measure is DECLARED ONCE on the model in DAX and
is already a closure — expression, and often its own filter context, in one place. A Tableau
field is only a metric once combined with the shelf it sits on. So this parser reads measures
directly, and `aggregation_at_use_site` is false throughout.

THE RICHEST SIGNAL HERE is the M partition expression: it carries the fully-qualified
warehouse target AND the WHERE-clause population rules the report author applied before any
visual saw the data. Missing those means A3 authors SQL over a wider population than the
report ever showed.

Reads:
  definition/model.tmdl  (or definition/tables/*.tmdl)  — tables, columns, measures, relationships
  definition/**/*.json                                   — report pages and visual containers
"""
from __future__ import annotations

import contextvars
import json
import pathlib
import re
import sys
from typing import Any

from metrics_agent.plugs.readers import powerbi_layout as _crawler  # noqa: E402
from metrics_agent.lib import bi_tags as _bi_tags  # noqa: E402
from metrics_agent.plugs.readers.bicompile import FormulaCompiler  # noqa: E402
from metrics_agent.plugs.readers import mquery as _mquery  # noqa: E402

# A PROPERTY of the object above (not part of a multi-line expression): where a measure's
# expression ends.
_BODY_END = re.compile(r"^\s*(formatString|formatStringDefinition|lineageTag|displayFolder|"
                       r"description|isHidden|annotation|changedProperty|dataCategory|"
                       r"detailRowsDefinition|kpi|sourceLineageTag|dataType)\b", re.I)
MEASURE = re.compile(r"^\s*measure\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))\s*=\s*(.*)$", re.I)
# A column, or a CALCULATED column (`column Margin = [Amount] * 0.1`): its DAX follows `=`,
# on the same line or, when `=` ends the line, on the lines below.
COLUMN = re.compile(r"^\s*column\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=]+))\s*(?:=\s*(.*))?$",
                    re.I)
TABLE = re.compile(r"^\s*table\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))\s*$", re.I)
DATATYPE = re.compile(r"^\s*dataType:\s*(\S+)", re.I)
SOURCE_COLUMN = re.compile(r"^\s*sourceColumn:\s*(.+)$", re.I)
REL = re.compile(r"^\s*relationship\s+(\S+)", re.I)
FROM_COL = re.compile(r"^\s*fromColumn:\s*(.+)$", re.I)
TO_COL = re.compile(r"^\s*toColumn:\s*(.+)$", re.I)
CROSS = re.compile(r"^\s*crossFilteringBehavior:\s*(\S+)", re.I)
CARD = re.compile(r"^\s*(toCardinality|fromCardinality):\s*(\S+)", re.I)
ACTIVE = re.compile(r"^\s*isActive:\s*(\S+)", re.I)
# TMDL writes a true boolean property either as `name: true` or as the bare name.
RELY = re.compile(r"^\s*relyOnReferentialIntegrity(?:\s*:\s*(\S+))?\s*$", re.I)
FORMAT_STRING = re.compile(r"^\s*formatString:\s*(.+)$", re.I)
DISPLAY_FOLDER = re.compile(r"^\s*displayFolder:\s*(.+)$", re.I)
DESCRIPTION = re.compile(r"^\s*///\s?(.*)$")
# A named annotation on the object above: the model's own metadata, kept as a tag.
ANNOTATION = re.compile(r"^\s*annotation\s+(?:'((?:[^']|'')+)'|\"([^\"]+)\"|([^\s=]+))\s*=\s*(.*)$",
                        re.I)
# Annotations Power BI writes for itself (how a column summarizes, a format hint, the
# properties it changed): tool bookkeeping, not an author's metadata.
TOOL_ANNOTATIONS = re.compile(r"^(PBI_|__|SummarizationSetBy$|UnderlyingDateTimeDataType$|"
                               r"LinkedQueryName$|TabularEditor_)", re.I)
# A measure's KPI: `kpi`, then its target, status and trend expressions and graphics.
KPI = re.compile(r"^\s*kpi\s*$", re.I)
KPI_EXPR = re.compile(r"^(\s*)(targetExpression|statusExpression|trendExpression)\s*=\s*(.*)$",
                      re.I)
KPI_PROP = re.compile(r"^\s*(statusGraphic|trendGraphic):\s*(.+)$", re.I)
# A perspective (`perspectives/<name>.tmdl`) and what it includes.
PERSPECTIVE = re.compile(r"^\s*perspective\s+(?:'((?:[^']|'')+)'|\"([^\"]+)\"|(\S+))\s*$", re.I)
PERSPECTIVE_PART = re.compile(r"^\s*perspective(Table|Measure|Column)\s+"
                              r"(?:'((?:[^']|'')+)'|\"([^\"]+)\"|(\S+))\s*$", re.I)
HIDDEN = re.compile(r"^\s*isHidden(?:\s*:\s*true)?\s*$", re.I)
# A column reference in DAX: `'Table'[Column]` or `Table[Column]`.
_COLUMN_REF = re.compile(r"(?:'((?:[^']|'')+)'|(?<![\w\]])([A-Za-z_][\w.]*))\s*\[(?:[^\]]|\]\])+\]")
# A measure reference in DAX: `[Name]` not preceded by a table name (a column is `T[Name]`).
_MEASURE_REF = re.compile(r"(?<![\w'\]])\[((?:[^\]]|\]\])+)\]")
HIERARCHY = re.compile(r"^\s*hierarchy\s+(?:'((?:[^']|'')+)'|\"([^\"]+)\"|(\S+))\s*$", re.I)
LEVEL = re.compile(r"^\s*level\s+(?:'((?:[^']|'')+)'|\"([^\"]+)\"|(\S+))\s*$", re.I)
LEVEL_COLUMN = re.compile(r"^\s*column:\s*(.+?)\s*$", re.I)
# A field switch (a "field parameter"): a calculated table of (label, NAMEOF(field), order)
# rows. The field is a measure (`[name]`, `'T'[name]`) or a column (`'T'[name]`).
NAMEOF_ROW = re.compile(
    r"\(\s*\"((?:[^\"]|\"\")*)\"\s*,\s*NAMEOF\s*\(\s*(?:'((?:[^']|'')+)'|([A-Za-z_][\w.]*))?"
    r"\s*\[((?:[^\]]|\]\])+)\]\s*\)\s*,\s*(-?\d+(?:\.\d+)?)", re.I)
# A calculation group's item: `calculationItem <name> = <DAX>` (the DAX on this line or on the
# lines below), and its `ordinal`.
CALC_ITEM = re.compile(
    r"^\s*calculationItem\s+(?:'((?:[^']|'')+)'|\"([^\"]+)\"|([^\s=]+))\s*=\s*(.*)$", re.I)
CALC_ORDINAL = re.compile(r"^\s*ordinal:\s*(-?\d+)\s*$", re.I)
# A calculation group's precedence (`precedence: 20` under `calculationGroup`): the higher is
# applied around the lower. An item's dynamic format string (`formatStringDefinition = <DAX>`).
CALC_PRECEDENCE = re.compile(r"^\s*precedence:\s*(-?\d+)\s*$", re.I)
FORMAT_DEFINITION = re.compile(r"^\s*formatStringDefinition\s*=\s*(.*)$", re.I)
EXPR_NAME = re.compile(r"^expression\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))\s*$", re.I)
# `partition <name> = <kind>`: `m` carries Power Query; `entity` names a table another source
# holds (Direct Lake, or a table of another semantic model in a composite model).
PARTITION = re.compile(r"^\s*partition\s+(?:'[^']*'|\"[^\"]*\"|[^\s=]+)\s*=\s*(\w+)", re.I)
ENTITY_PROP = re.compile(r"^\s*(entityName|schemaName|expressionSource):\s*(.+?)\s*$", re.I)
# A top-level shared expression: `expression <name> = <M on this line, or on the lines below>`.
SHARED_EXPR = re.compile(
    r"^expression\s+(?:'((?:[^']|'')+)'|\"([^\"]+)\"|([^\s=]+))\s*=\s*(.*)$", re.I)

# M / Power Query: the fully-qualified target and the population rules applied before any
# visual saw the data.
# The native query's inner SQL is extracted by `readers/mquery.native_sql`, not by a regex here.
# The regex this replaced assumed the first argument held no comma, so on the ordinary
# `Value.NativeQuery(Snowflake.Databases("acct","WH"), "SELECT …")` it captured `WH` and
# recorded a one-character statement as the table's evidence.


class PowerBIError(ValueError):
    pass


def _unquote(*groups: str | None) -> str:
    for g in groups:
        if g:
            return g.strip().strip("'\"")
    return ""


def _find_model(root: pathlib.Path) -> list[pathlib.Path]:
    files = sorted(root.rglob("*.tmdl"))
    if not files:
        raise PowerBIError(
            f"no .tmdl under {root}. A Power BI extract must contain definition/model.tmdl "
            f"(or definition/tables/*.tmdl); a .pbix binary is not read here — acquisition "
            f"is out of scope (A0).")
    return files


def _parse_m(expr: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """The warehouse target and the native SQL out of a partition expression.

    THE RESOLUTION IS `readers/mquery`, SHARED WITH EVERY OTHER POWER BI READER. What used to be
    here were four inline regexes that knew only the `[Name=..., Kind=...]` navigation form.
    Measured on the reference extract, they named the object on 32 of 1,644 M-backed tables
    where the shared miner names 963 — because the dominant form in the wild is
    `Value.NativeQuery`, and on that form the old code returned early with `object: None`.
    A table with no object is a table `a2` cannot bound or map to a physical name, so those
    1,600-odd tables were invisible to the rest of the pipeline.

    TWO THINGS ARE KEPT FROM THE BASE rather than taken from the earlier project, and both are
    deliberate:

      * `raw_expression` is the native SQL when there is one, not the whole M expression.
        That SQL carries the WHERE-clause population rules the report author applied before
        any visual saw the data, and it is what the compiler and the evidence tests read.
        Where there is no native query the scrubbed M expression stands in, which is the
        earlier project's behaviour and the only evidence available. Either way it is SCRUBBED and
        never cut.
      * `object_kind` says `native-sql`, the base's spelling, not the earlier project's
        `native_query`.
      * `published_datasource` stays on the contract. It is Tableau's, always None here, and
        `bi_facts.schema.json` sets `additionalProperties: false` — so dropping it would make
        this parser's output fail validation against the shared contract.
    """
    # Shared with the .pbix and .bim readers, so one model read from two containers names
    # the same source. `object` is lowercased: `_qualified` and `a2`'s logical-to-physical
    # map both key on it, and TMDL and SQL disagree about case.
    return _mquery.source_lineage(_mquery.substitute_parameters(expr or "", params or {}))


def _partition_m(lines: list[str]) -> str:
    """The M of a TMDL partition: what follows `source =`, on its line and on the lines below
    it that are indented deeper. The partition's other properties (`mode: import`, an
    annotation) are not M."""
    for i, ln in enumerate(lines):
        head = ln.strip()
        if head.lower().startswith("source") and head[6:].lstrip().startswith("="):
            depth = _indent_level(ln)
            first = head[6:].lstrip()[1:].strip()
            body = [first] if first else []
            for nxt in lines[i + 1:]:
                if nxt.strip() and _indent_level(nxt) <= depth:
                    break
                body.append(nxt)
            return "\n".join(body).strip("\n")
    return "\n".join(lines)


def _qualified(l: dict[str, Any]) -> str | None:
    return _mquery.qualified(l)


def name_physically(entry: dict[str, Any]) -> dict[str, Any]:
    """A model table whose source names a warehouse object is KNOWN by that object: `name`
    becomes the qualified physical name and the model's own name moves to `logical_name`,
    which is what `a2` maps logical to physical through. The TMDL reader does this at its
    partition; the `.bim`/`.tmsl` and `.pbix` readers call this, so one model read from any
    container compiles to the same tables — left on the model name, the compiler wrote
    `FROM FCT_SEASON_PERFORMANCE` and every statement failed its witness."""
    q = _qualified(entry.get("source_lineage") or {})
    if q and q != entry.get("name"):
        entry["logical_name"] = entry["name"]
        entry["name"] = q
    return entry


_CARD_LETTER = {"many": "M", "one": "1", "m": "M", "1": "1", "2": "M"}


PARTITION_MODE = re.compile(r"^\s*mode\s*:\s*(\w+)\s*$", re.I)


def storage_mode(value: Any) -> str | None:
    """A partition's mode -> the common `storage_mode`: import, directquery or dual."""
    v = str(value or "").strip().lower()
    return {"import": "import", "directquery": "directquery", "dual": "dual",
            "push": "import", "default": None}.get(v)


def storage_semantics(tables: list[dict], relationships: list[dict]) -> None:
    """What a table's storage mode changes in its relationships: "assume referential
    integrity" is a DirectQuery setting (the engine then joins INNER in the source query);
    an Import model ignores it, so such a relationship keeps its unmatched rows under the
    blank member. Applied once tables and relationships are both read."""
    mode: dict[str, str | None] = {}
    for t in tables:
        for nm in (t.get("logical_name"), t.get("name")):
            if nm:
                mode[str(nm).lower()] = t.get("storage_mode")
    for r in relationships:
        if not r.get("rely_on_referential_integrity") or r.get("cardinality") == "M:M":
            continue
        ends = [mode.get(str(r.get(k) or "").lower()) for k in ("from_table", "to_table")]
        if any(m in (None, "import") for m in ends):
            r["join_type"] = "left"


def relationship_semantics(from_cardinality: Any = None, to_cardinality: Any = None,
                           cross_filter: Any = None, rely_on_ri: Any = None) -> dict[str, Any]:
    """A Power BI relationship's settings -> the common relationship fields.

    Every Power BI container (TMDL, a `.bim`, a `.pbix`, a scan result) states the same four
    settings in its own spelling, so they are read here once:

    * cardinality: each end is `many` or `one`; an OMITTED end is the default (many on the
      from side, one on the to side), never unknown. `M:1` in the common vocabulary.
    * cross filter: `bothDirections` / `Both` is 'both'; anything else is 'single'.
    * join type. A REGULAR relationship (one end is one) keeps a from-end row whose key the
      to end lacks, under the to end's (Blank) member: 'left'. A LIMITED one (many-to-many)
      has no blank member, and a relationship that assumes referential integrity is
      joined inner by the engine: both are 'inner'.
    """
    def end(v: Any, default: str) -> str | None:
        v = default if v is None or v == "" else v
        return _CARD_LETTER.get(str(v).strip().lower())
    frm, to = end(from_cardinality, "many"), end(to_cardinality, "one")
    card = f"{frm}:{to}" if frm and to else "unknown"
    cross = str(cross_filter or "").strip().lower()
    rely = str(rely_on_ri).strip().lower() in ("true", "1") if rely_on_ri is not None else False
    return {"cardinality": card,
            "cross_filter": "both" if cross in ("bothdirections", "both", "2") else "single",
            "join_type": "inner" if card == "M:M" or rely else "left",
            **({"rely_on_referential_integrity": True} if rely else {})}


def _parse_tmdl(files: list[pathlib.Path], params: dict[str, str] | None = None
                ) -> tuple[list[dict], list[dict], list[dict]]:
    tables: list[dict] = []
    measures: list[dict] = []
    relationships: list[dict] = []

    for f in files:
        lines = f.read_text(errors="ignore").splitlines()
        cur_table: dict | None = None
        cur_col: dict | None = None
        cur_rel: dict | None = None
        cur_measure: dict | None = None
        cur_hier: dict | None = None
        cur_level: dict | None = None
        doc_lines: list[str] = []
        cur_kpi: dict | None = None          # the KPI of `cur_measure` being read
        kpi_body: list | None = None         # [key, indent, lines] of a multi-line expression
        in_partition = False
        partition: list[str] = []
        partition_kind = ""

        def close_col() -> None:
            nonlocal cur_col
            if cur_table is not None and cur_col is not None:
                cur_table["columns"].append(cur_col)
            cur_col = None

        def close_partition() -> None:
            nonlocal in_partition, partition
            if in_partition and cur_table is not None and partition_kind == "entity":
                # No Power Query: the partition names a table, and a SHARED expression says
                # where it lives. Resolved once the whole model is read (`_resolve_entities`).
                props = {}
                for pl in partition:
                    pm = ENTITY_PROP.match(pl)
                    if pm:
                        key = {"entityname": "entityName", "schemaname": "schemaName",
                               "expressionsource": "expressionSource"}[pm.group(1).lower()]
                        props[key] = _unquote(pm.group(2))
                if props.get("entityName"):
                    cur_table["_entity"] = {"entity": props["entityName"],
                                            "schema": props.get("schemaName") or None,
                                            "expression": props.get("expressionSource")}
            elif in_partition and partition_kind == "calculated":
                # A CALCULATED table (DAX: a date table built by CALENDAR, the hidden auto
                # date/time table) reads no warehouse object, so it has no lineage; read as
                # Power Query, its DAX named a table that does not exist. A table of
                # (label, NAMEOF(field), order) rows is a FIELD SWITCH: its choices are kept.
                if cur_table is not None:
                    choices = field_choices("\n".join(partition))
                    if choices:
                        cur_table["field_choices"] = choices
                    else:
                        # Its rows as a statement (`daxtables`, from `read_power_query`).
                        cur_table["_dax_table"] = _partition_m(partition)
            if in_partition and cur_table is not None:
                mode = next((m.group(1) for m in (PARTITION_MODE.match(pl) for pl in partition)
                             if m), None)
                if mode:
                    cur_table["storage_mode"] = storage_mode(mode)
            if in_partition and partition_kind in ("entity", "calculated"):
                pass
            elif in_partition and cur_table is not None and partition:
                lin = _parse_m("\n".join(partition), params)
                cur_table["source_lineage"] = lin
                # Read again, step by step, once the whole model is known
                # (`mquery.read_power_query`).
                cur_table["_m"] = _partition_m(partition)
                q = _qualified(lin)
                if q:
                    cur_table["name"] = q
            in_partition = False
            partition = []

        body: list[str] | None = None      # a MULTI-LINE measure expression being collected

        def close_body():
            nonlocal body
            owner = cur_measure if cur_measure is not None else cur_col
            if body is not None and owner is not None:
                owner["formula"] = "\n".join(body).strip().strip("`").strip()
            body = None

        for raw in lines:
            line = raw.rstrip()
            if kpi_body is not None:
                # A KPI expression on the lines below its `=`: every line indented deeper.
                ind = len(line) - len(line.lstrip())
                if not line.strip() or line.strip() == "```" or ind > kpi_body[1]:
                    if line.strip() and line.strip() != "```":
                        kpi_body[2].append(line.strip())
                    continue
                cur_kpi[kpi_body[0]] = "\n".join(kpi_body[2]).strip() or None
                kpi_body = None
            if cur_kpi is not None:
                ke = KPI_EXPR.match(line)
                if ke:
                    key = ke.group(2)[0].lower() + ke.group(2)[1:].replace("Expression", "")
                    expr = (ke.group(3) or "").strip()
                    if expr and expr != "```":
                        cur_kpi[key] = expr
                    else:
                        kpi_body = [key, len(ke.group(1)), []]
                    continue
                kp = KPI_PROP.match(line)
                if kp:
                    if kp.group(1).lower() == "statusgraphic":
                        cur_kpi["status_graphic"] = kp.group(2).strip().strip("'\"")
                    continue
                if line.strip() and not KPI.match(line) and (
                        MEASURE.match(line) or COLUMN.match(line) or TABLE.match(line)
                        or len(line) - len(line.lstrip()) <= cur_kpi["_indent"]):
                    cur_kpi = None
            if body is not None:
                # A measure whose `=` ends the line carries its expression on the lines below
                # (`VAR … RETURN …`), up to its first property or the next object. Reading only
                # the `=` line left 13 of 53 measures on a real model with no formula at all,
                # and every widget using them compiled to nothing.
                stripped = line.strip()
                if not stripped or stripped == "```" or not (
                        _BODY_END.match(line) or MEASURE.match(line) or COLUMN.match(line)
                        or TABLE.match(line) or stripped.startswith(("///", "partition "))):
                    if stripped != "```":
                        body.append(stripped)
                    continue
                close_body()
            t = TABLE.match(line)
            if t and not line.lstrip().startswith(("//", "///")):
                cur_measure = None
                cur_hier = cur_level = None
                close_col()
                close_partition()
                if cur_table:
                    tables.append(cur_table)
                cur_table = {"name": _unquote(*t.groups()),
                             # THE TMDL NAME, KEPT. `name` is rewritten to the physical table
                             # once the partition resolves, and a Power BI MEASURE declares
                             # its home by this LOGICAL name — so overwriting without keeping
                             # it left A2 comparing `account_intelligence` against
                             # `analytics.cx.account_activity`, resolving nothing, and
                             # dropping every unit on a real model.
                             "logical_name": _unquote(*t.groups()),
                             "description": "",
                             "is_hidden": False, "columns": [],
                             "source_lineage": {}}
                continue

            if line.lstrip().lower().startswith("partition "):
                close_col()
                cur_hier = cur_level = None
                close_partition()
                in_partition = True
                partition = []
                pk = PARTITION.match(line)
                partition_kind = pk.group(1).lower() if pk else ""
                continue
            if in_partition:
                if line.strip() and not line.startswith(("\t", "  ")):
                    close_partition()
                else:
                    partition.append(line)
                    continue

            hm = HIERARCHY.match(line)
            if hm and cur_table is not None:
                # A hierarchy of the table: its levels, each a column, in drill order.
                close_col()
                cur_measure = None
                cur_hier = {"name": _unquote(*hm.groups()).replace("''", "'"), "levels": []}
                cur_level = None
                cur_table.setdefault("hierarchies", []).append(cur_hier)
                continue
            lm = LEVEL.match(line)
            if lm and cur_hier is not None:
                cur_level = {"name": _unquote(*lm.groups()).replace("''", "'"), "column": None}
                cur_hier["levels"].append(cur_level)
                continue
            if cur_level is not None:
                lc = LEVEL_COLUMN.match(line)
                if lc:
                    cur_level["column"] = lc.group(1).strip().strip("'\"").replace("''", "'")
                    continue

            ci = CALC_ITEM.match(line)
            if ci and cur_table is not None:
                # A calculation group's item: read as the item's DAX, applied to the measure a
                # visual shows (`calculation_groups`).
                close_col()
                cur_hier = cur_level = None
                cur_measure = {"name": _unquote(ci.group(1), ci.group(2), ci.group(3))
                               .replace("''", "'"), "formula": (ci.group(4) or "").strip(),
                               "_calc_item": True, "ordinal": None}
                cur_table.setdefault("_calc_items", []).append(cur_measure)
                if not cur_measure["formula"] or cur_measure["formula"] == "```":
                    cur_measure["formula"] = ""
                    body = []
                continue
            co = CALC_ORDINAL.match(line)
            if co and cur_measure is not None and cur_measure.get("_calc_item"):
                cur_measure["ordinal"] = int(co.group(1))
                continue
            cp = CALC_PRECEDENCE.match(line)
            if cp and cur_table is not None and cur_measure is None:
                cur_table["_calc_precedence"] = int(cp.group(1))
                continue
            fd = FORMAT_DEFINITION.match(line)
            if fd and cur_measure is not None and cur_measure.get("_calc_item"):
                cur_measure["format_string"] = fd.group(1).strip() or None
                continue

            m = MEASURE.match(line)
            if m and cur_table is not None:
                close_col()
                cur_hier = cur_level = None
                cur_measure = {
                    "name": _unquote(m.group(1), m.group(2), m.group(3)),
                    "formula": (m.group(4) or "").strip(),
                    "formula_language": "dax",
                    "home_table": cur_table["name"],
                    "folder": None,
                    "description": " ".join(doc_lines).strip() or "",
                    # A DAX measure is already a closure — unlike Tableau, where the
                    # aggregation is chosen on the shelf.
                    "aggregation_at_use_site": False,
                    # The author's number format. Not in the SQL and not in the DAX: it is the
                    # UNIT, and it is the difference between 0.71 and 71%.
                    "format_string": None,
                }
                measures.append(cur_measure)
                doc_lines = []
                if not cur_measure["formula"] or cur_measure["formula"] == "```":
                    cur_measure["formula"] = ""
                    body = []
                continue

            c = COLUMN.match(line)
            if c and cur_table is not None:
                close_col()
                cur_measure = None
                cur_hier = cur_level = None
                expr = c.group(4)
                cur_col = {"name": _unquote(c.group(1), c.group(2), c.group(3)),
                           "data_type": None,
                           "caption": None, "default_format": None,
                           "description": " ".join(doc_lines).strip(),
                           "is_calculated": expr is not None,
                           "formula": (expr or "").strip() if expr is not None else None,
                           "formula_language": "dax" if expr is not None else None,
                           "source_column": None, "role": None}
                doc_lines = []
                if expr is not None and cur_col["formula"] in ("", "```"):
                    cur_col["formula"] = ""
                    body = []
                continue
            doc = DESCRIPTION.match(line)
            if doc:
                # TMDL `///` doc comments are the author's own description of a measure.
                doc_lines.append(doc.group(1).strip())
                continue

            if KPI.match(line) and cur_measure is not None and not cur_measure.get("_calc_item"):
                # The measure is a KPI's value: its target and status follow (read above).
                cur_kpi = cur_measure.setdefault("kpi", {"target": None, "status": None,
                                                         "status_graphic": None})
                cur_kpi["_indent"] = len(line) - len(line.lstrip())
                continue
            an = ANNOTATION.match(line)
            if an:
                # The model's own metadata on a measure or a column, kept verbatim as a tag;
                # never read by the compiler. Power BI's own bookkeeping is left out.
                aname = _unquote(an.group(1), an.group(2), an.group(3)).replace("''", "'")
                owner = cur_measure if cur_measure is not None else cur_col
                if owner is not None and not owner.get("_calc_item") \
                        and not TOOL_ANNOTATIONS.match(aname):
                    owner.setdefault("tags", {}).setdefault("annotations", []).append(
                        {"name": aname, "value": an.group(4).strip().strip('"')})
                continue
            fs = FORMAT_STRING.match(line)
            if fs:
                # The contract has two homes for a number format: `format_string` on a
                # MEASURE, `default_format` on a COLUMN. Writing the measure's key onto a
                # column failed the schema on the first real extract — the fixture had no
                # formatted column, so nothing caught it.
                value = fs.group(1).strip().strip("'\"")
                if cur_measure is not None:
                    cur_measure["format_string"] = value
                elif cur_col is not None:
                    cur_col["default_format"] = value
                continue
            df = DISPLAY_FOLDER.match(line)
            if df:
                # PBI display folders group measures by business area — the domain vocabulary
                # the author already chose. A column may carry one too (`folder`).
                if cur_measure is not None:
                    cur_measure["folder"] = df.group(1).strip().strip("'\"")
                elif cur_col is not None:
                    cur_col["folder"] = df.group(1).strip().strip("'\"")
                continue

            if cur_col is not None:
                d = DATATYPE.match(line)
                if d:
                    cur_col["data_type"] = d.group(1)
                s = SOURCE_COLUMN.match(line)
                if s:
                    cur_col["source_column"] = s.group(1).strip().strip("'\"").lower()
                    # The exact spelling: Power Query names columns case-sensitively.
                    cur_col["_source_exact"] = s.group(1).strip().strip("'\"")
                if HIDDEN.match(line):
                    # Read only to tell a measure table (`measure_tables`), then dropped.
                    cur_col["_hidden"] = True

            r = REL.match(line)
            if r:
                if cur_rel:
                    relationships.append(cur_rel)
                # The settings are gathered raw and normalised when the relationship closes
                # (`_close_relationship`): an omitted end or flag is its DEFAULT, not unknown.
                cur_rel = {"from_table": "", "from_column": "", "to_table": "",
                           "to_column": "", "cardinality": None, "cross_filter": None,
                           "is_active": True, "kind": "relationship",
                           "name": r.group(1).strip("'\""), "_raw": {}}
                continue
            if cur_rel is not None:
                fc, tc = FROM_COL.match(line), TO_COL.match(line)
                if fc:
                    tbl, _, col = fc.group(1).strip().rpartition(".")
                    cur_rel["from_table"] = tbl.strip("'\"")
                    cur_rel["from_column"] = col.strip("'\"")
                if tc:
                    tbl, _, col = tc.group(1).strip().rpartition(".")
                    cur_rel["to_table"] = tbl.strip("'\"")
                    cur_rel["to_column"] = col.strip("'\"")
                x = CROSS.match(line)
                if x:
                    # Bidirectional relationships change what a join MEANS; A3 must declare
                    # rather than approximate them.
                    cur_rel["_raw"]["cross"] = x.group(1)
                cd = CARD.match(line)
                if cd:
                    # Each END separately: keeping whichever came last reported `one` for
                    # every relationship, a multiplicity of neither end.
                    end = "fromCardinality" if cd.group(1).lower().startswith("from") \
                        else "toCardinality"
                    cur_rel["_raw"][end] = cd.group(2)
                ac = ACTIVE.match(line)
                if ac:
                    # An inactive relationship is used only where a measure activates it; read
                    # as active, the join planner joined along whichever came first.
                    cur_rel["is_active"] = ac.group(1).strip().lower() != "false"
                ri = RELY.match(line)
                if ri:
                    cur_rel["_raw"]["rely"] = ri.group(1) or "true"

        close_body()
        close_col()
        close_partition()
        if cur_table:
            tables.append(cur_table)
        if cur_rel:
            relationships.append(cur_rel)

    relationships = [_close_relationship(r) for r in relationships
                     if r["from_table"] and r["to_table"]]
    for t in tables:
        if t.get("hierarchies"):
            t["hierarchies"] = [{"name": h["name"], "levels": [
                lv for lv in h["levels"] if lv.get("column")]} for h in t["hierarchies"]]
    return tables, measures, relationships


def perspectives(files: list[pathlib.Path]) -> dict[tuple[str, str], list[str]]:
    """Each perspective (`perspectives/<name>.tmdl`) and the measures and columns it includes:
    {(table, name) lower: [perspective, ...]}. A perspective that includes a whole table
    (no measure or column listed under it) includes everything on it: ("<table>", "*")."""
    out: dict[tuple[str, str], list[str]] = {}
    for f in files:
        name = table = None
        listed = False
        for raw in f.read_text(errors="ignore").splitlines():
            pm = PERSPECTIVE.match(raw)
            if pm:
                if table and not listed:
                    out.setdefault((table.lower(), "*"), []).append(name)
                name, table, listed = _unquote(*pm.groups()).replace("''", "'"), None, False
                continue
            pp = PERSPECTIVE_PART.match(raw)
            if pp and name:
                obj = _unquote(pp.group(2), pp.group(3), pp.group(4)).replace("''", "'")
                if pp.group(1).lower() == "table":
                    if table and not listed:
                        out.setdefault((table.lower(), "*"), []).append(name)
                    table, listed = obj, False
                elif table:
                    listed = True
                    out.setdefault((table.lower(), obj.lower()), []).append(name)
        if table and not listed and name:
            out.setdefault((table.lower(), "*"), []).append(name)
    return out


def model_tags(files: list[pathlib.Path], tables: list[dict], measures: list[dict]) -> None:
    """The model's own metadata on its measures and columns, after the whole model is read:
    the perspectives that include each, the other measures each measure names (`uses`, a
    measure chain), and a KPI's target and status declared as measures of their own, named as
    the engine names them (`_<measure> Goal`, `_<measure> Status`: seen live, executeQueries
    answers both), so a visual that shows one compiles it. Tags are context; none changes SQL.
    """
    persp = perspectives(files)

    def _with(owner: dict, table: str, name: str) -> None:
        got = sorted(set(persp.get((table.lower(), name.lower()), [])
                         + persp.get((table.lower(), "*"), [])))
        if got:
            owner.setdefault("tags", {})["perspectives"] = got

    if persp:
        for t in tables:
            for c in t.get("columns") or []:
                _with(c, str(t.get("logical_name") or t["name"]), c["name"])
        for m in measures:
            _with(m, str(m.get("home_table") or ""), m["name"])
    names = {m["name"].lower(): m["name"] for m in measures}
    for m in list(measures):
        kpi = m.get("kpi")
        if not kpi:
            continue
        kpi.pop("_indent", None)
        for part, suffix, key in (("target", "Goal", "target_measure"),
                                  ("status", "Status", "status_measure")):
            if not kpi.get(part):
                continue
            kname = f"_{m['name']} {suffix}"
            kpi[key] = kname
            if kname.lower() not in names:
                measures.append({"name": kname, "formula": kpi[part], "formula_language": "dax",
                                 "home_table": m.get("home_table"), "folder": m.get("folder"),
                                 "description": f"The {part} of the KPI {m['name']!r}",
                                 "aggregation_at_use_site": False, "format_string": None,
                                 "kpi_of": {"measure": m["name"], "part": part}})
                names[kname.lower()] = kname
        for k in ("target", "status", "status_graphic", "target_measure", "status_measure"):
            kpi.setdefault(k, None)
    measure_uses(measures)


def measure_tables(tables: list[dict], measures: list[dict],
                   relationships: list[dict] | None = None) -> None:
    """A MEASURE TABLE holds measures and nothing a person reads: every column hidden (the
    placeholder column a dedicated measure table is built with), or none, and no warehouse
    table behind it (a calculated row, entered data). Where a measure
    lives does not change what it computes (a DAX measure's home table plays no part in its
    evaluation), so each measure on one is homed on the table its formula reads (through the
    measures it names), and keeps the table it is shown on as `shown_on`. The table is marked
    `measure_table`, so its own source (a calculated row, entered data) is never read as data.
    """
    by_name = {str(t.get("logical_name") or t["name"]).lower(): t for t in tables}
    hidden = {id(t): [c.pop("_hidden", False) for c in t["columns"]] for t in tables}
    # A table whose columns are all hidden but that reads a warehouse table is a fact table
    # shown by its measures only (a common modelling style): its rows are data, it stays home.
    mtabs = {key for key, t in by_name.items() if all(hidden[id(t)])
             and not (t.get("source_lineage") or {}).get("object")
             and not (t.get("source_lineage") or {}).get("native_sql")
             and any(str(m.get("home_table") or "").lower() == key for m in measures)}
    for t in tables:
        hidden.pop(id(t), None)
    if not mtabs:
        return
    by_measure = {m["name"].lower(): m for m in measures}

    many = {str(r.get("from_table") or "").lower() for r in relationships or []}

    def tables_read(m: dict, depth: int = 0) -> list[str]:
        """The data tables the formula reads, in order: through the measures it names first
        (their aggregates are what it computes), then its own column references."""
        f = m.get("formula") or ""
        out: list[str] = []
        if depth < 32:
            for ref in _MEASURE_REF.findall(f):
                other = by_measure.get(ref.replace("]]", "]").lower())
                if other is not None and other is not m:
                    out += [x for x in tables_read(other, depth + 1) if x not in out]
        for q, bare in _COLUMN_REF.findall(f):
            name = (q or bare).replace("''", "'").lower()
            if name in by_name and name not in mtabs and name not in out:
                out.append(name)
        return out

    def reads(m: dict) -> str | None:
        """The table the measure is computed over: of the tables it reads, the first on the
        many side of a relationship (a fact), else the first."""
        got = tables_read(m)
        pick = next((x for x in got if x in many), got[0] if got else None)
        return (by_name[pick].get("logical_name") or by_name[pick]["name"]) if pick else None

    for key in mtabs:
        by_name[key]["measure_table"] = True
    for m in measures:
        home = str(m.get("home_table") or "")
        if home.lower() in mtabs:
            kpi_of = by_measure.get(str((m.get("kpi_of") or {}).get("measure") or "").lower())
            data = reads(m) or (reads(kpi_of) if kpi_of else None)
            m["shown_on"] = home
            if data:
                m["home_table"] = data


def measure_uses(measures: list[dict]) -> None:
    """`uses`: the other measures each DAX measure names (`[Name]`, never `T[Name]`)."""
    names = {m["name"].lower(): m["name"] for m in measures}
    for m in measures:
        if m.get("formula_language") not in (None, "dax"):
            continue
        used = []
        for ref in _MEASURE_REF.findall(m.get("formula") or ""):
            n = names.get(ref.replace("]]", "]").lower())
            if n and n != m["name"] and n not in used:
                used.append(n)
        if used:
            m["uses"] = used


def calculation_groups(tables: list[dict]) -> None:
    """Each table holding calculation items (`_calc_items`, TMDL) or a .bim
    `calculationGroup`: its `calculation_group` - the column that names the items (the one
    reading `Name`) and each item's DAX, in ordinal order."""
    for t in tables:
        items = t.pop("_calc_items", None)
        precedence = t.pop("_calc_precedence", None)
        if not items:
            continue
        col = next((c["name"] for c in t.get("columns") or []
                    if str(c.get("source_column") or "").strip("[]").lower() == "name"), None)
        if col is None:
            continue
        ordered = sorted(enumerate(items), key=lambda kv: (
            kv[1].get("ordinal") if kv[1].get("ordinal") is not None else kv[0], kv[0]))
        t["calculation_group"] = {"column": col, "items": [
            {"name": it["name"], "expression": it.get("formula") or ""}
            | ({"format_string": it["format_string"]} if it.get("format_string") else {})
            for _i, it in ordered if it.get("formula")]}
        if precedence is not None:
            t["calculation_group"]["precedence"] = int(precedence)


def field_choices(dax: str) -> list[dict]:
    """The choices of a field switch's calculated table (`{("Amount", NAMEOF([Sales]), 0),
    ...}`): label, the field's table (None for a bare measure name) and name, and order. The
    kind (measure or column) is settled against the model (`_classify_choices`)."""
    out = []
    for m in NAMEOF_ROW.finditer(dax or ""):
        table = (m.group(2) or "").replace("''", "'") or m.group(3) or None
        out.append({"label": m.group(1).replace('""', '"'), "table": table,
                    "name": m.group(4).replace("]]", "]"), "kind": "measure",
                    "order": float(m.group(5)) if "." in m.group(5) else int(m.group(5))})
    return out


def classify_choices(tables: list[dict]) -> None:
    """Each field switch's choice is a COLUMN when its table has a column of that name, else a
    measure (a measure's name is unique in a model)."""
    cols = {}
    for t in tables:
        for key in {str(t.get("logical_name") or ""), str(t.get("name") or "")} - {""}:
            cols[key.lower()] = {c["name"].lower() for c in t.get("columns") or []}
    for t in tables:
        for ch in t.get("field_choices") or []:
            if ch.get("table") and ch["name"].lower() in cols.get(str(ch["table"]).lower(), ()):
                ch["kind"] = "column"


def _close_relationship(rel: dict) -> dict:
    raw = rel.pop("_raw", {}) or {}
    rel.update(relationship_semantics(raw.get("fromCardinality"), raw.get("toCardinality"),
                                      raw.get("cross"), raw.get("rely")))
    return rel


def _indent_level(line: str) -> int:
    """TMDL nesting depth: a tab, or four spaces, is one level."""
    lead = line[:len(line) - len(line.lstrip(" \t"))]
    return lead.count("\t") + lead.count(" ") // 4


def shared_expressions(files: list[pathlib.Path]) -> dict[str, str]:
    """Every top-level `expression <name> = …` of a TMDL model -> its M text.

    The M follows `=` on the same line or, when `=` ends the line, on the lines below at two
    levels of indentation; one level is the expression's own properties (`lineageTag`,
    `annotation`), which are not M. Keyed by the name exactly as declared."""
    out: dict[str, str] = {}
    for f in files:
        name: str | None = None
        body: list[str] = []
        for raw in f.read_text(errors="ignore").splitlines() + [""]:
            m = SHARED_EXPR.match(raw)
            top = raw.strip() and _indent_level(raw) == 0
            if name is not None and (m or top or (raw.strip() and _indent_level(raw) == 1)):
                out[name] = "\n".join(body).strip()
                name = None
            if m:
                name = (m.group(1) or "").replace("''", "'") or m.group(2) or m.group(3)
                body = [m.group(4)] if m.group(4).strip() else []
                continue
            if name is not None and raw.strip() and _indent_level(raw) >= 2:
                body.append(raw.strip())
        if name is not None:
            out[name] = "\n".join(body).strip()
    return out


# ── composite models: a table read by DirectQuery from ANOTHER semantic model ────────────
#
# A composite model's table can be another semantic model's table (`partition … = entity`
# whose shared expression is `AnalysisServices.Database(<workspace>, <model>)`), and that model
# can itself be composite: a CHAIN. The table means what the model at the end of the chain
# says it means, so it is resolved to that model's own definition: its physical table, its
# columns' source columns, and the measures and relationships declared there. Any depth; a
# model met twice on one chain is a cycle, refused by name, and so is a link whose model was
# not supplied, since reading the table as its own source would describe a table that does not
# exist in any warehouse.
_ITEM_SUFFIXES = (".semanticmodel", ".dataset")


def _model_label(path: pathlib.Path) -> str:
    """The name a model is known by in a chain: `.platform`'s display name, else the folder's
    own name without a PBIP item suffix, else (a `.bim`) the model's name in the file."""
    if path.is_dir():
        name = _crawler.display_name(path) or path.name
        low = name.lower()
        for suffix in _ITEM_SUFFIXES:
            if low.endswith(suffix):
                return name[:-len(suffix)]
        return name
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        return str((doc.get("model") or doc).get("name") or path.stem)
    except (OSError, json.JSONDecodeError, AttributeError):
        return path.stem


def _candidate_models(here: pathlib.Path) -> list[pathlib.Path]:
    """Where the upstream models of the model at `here` may be: every model of the extract it
    sits in (`semanticModels/<ws>/<model>/`), else the folders and `.bim` files beside it."""
    folder = here if here.is_dir() else here.parent
    if folder.parent.parent.name == "semanticModels":
        root = folder.parent.parent.parent
        try:
            return [m for m in _crawler.find_models(root) if m.resolve() != folder.resolve()]
        except _crawler.ExtractError:
            return []
    out: list[pathlib.Path] = []
    try:
        siblings = sorted(folder.parent.iterdir()) if here.is_dir() else sorted(folder.iterdir())
    except OSError:
        return []
    for p in _crawler.safe_paths(folder.parent if here.is_dir() else folder, siblings):
        if p.resolve() == here.resolve():
            continue
        if p.is_dir() and any(p.rglob("*.tmdl")):
            out.append(p)
        elif p.is_file() and p.suffix.lower() in (".bim", ".tmsl"):
            out.append(p)
    return out


def _workspace_of(path: pathlib.Path) -> str | None:
    """The workspace name a fetch recorded for a model (`envelope.json` `workspaceName`)."""
    try:
        env = _crawler.read_envelope(path) if path.is_dir() else None
    except _crawler.UnreadableEnvelope:
        return None
    return str(env.get("workspaceName")) if env and env.get("workspaceName") else None


def find_upstream(here: pathlib.Path, ref: dict,
                  candidates: list[pathlib.Path] | None = None) -> list[pathlib.Path]:
    """The supplied models a chain link `ref` (`mquery.remote_model`) may name: matched by the
    model's name, then narrowed by workspace where a fetch recorded one."""
    pool = _candidate_models(here) if candidates is None else candidates
    want = (ref.get("model") or "").casefold()
    hits = [p for p in pool if _model_label(p).casefold() == want]
    if len(hits) > 1 and ref.get("workspace"):
        named = [p for p in hits
                 if (_workspace_of(p) or "").casefold() == ref["workspace"].casefold()]
        hits = named or hits
    return hits


def _parse_model(path: pathlib.Path, chain: tuple) -> dict[str, Any]:
    if path.is_dir():
        return parse(path, _chain=chain)
    from metrics_agent.plugs.readers import tabular  # noqa: PLC0415 - it imports this module
    return tabular.parse(path, _chain=chain)


def _refused(msg: str) -> Exception:
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415
    return ReaderRefused(msg)


def _link_name(ref: dict) -> str:
    return f"semantic model {ref['model']!r}" + (
        f" in workspace {ref['workspace']!r}" if ref.get("workspace") else "")


def resolve_entities(tables: list[dict], measures: list[dict], relationships: list[dict],
                     expressions: dict[str, str], here: pathlib.Path,
                     chain: tuple = ()) -> list[str]:
    """Resolve every table an entity partition names (`table["_entity"]`), in place.

    Direct Lake (a physical table): its lineage is the lakehouse or warehouse table.
    A composite model's table: the upstream model is found among the supplied models, read
    (recursively, so a chain of any depth resolves), and the table takes that model's
    definition. Returns the sentences `_reader_notes` carries, one per resolved table."""
    notes: list[str] = []
    here_key = str(here.resolve())
    chain = chain or (here_key,)
    upstream: dict[str, dict] = {}
    for t in tables:
        ent = t.pop("_entity", None)
        if not ent:
            continue
        expr = expressions.get(ent.get("expression") or "") if ent.get("expression") else None
        ref = _mquery.remote_model(expr or "")
        if ref is None:
            t["source_lineage"] = _mquery.entity_lineage(expr, ent["entity"], ent["schema"])
            name_physically(t)
            notes.append(f"table {t.get('logical_name') or t['name']!r} is read in place "
                         f"(Direct Lake) from {t['name']}.")
            continue
        key = f"{ref['workspace'] or ''}\x00{ref['model']}".casefold()
        if key not in upstream:
            hits = find_upstream(here, ref)
            if not hits:
                raise _refused(
                    f"table {t.get('logical_name') or t['name']!r} of {here.name} is a table of "
                    f"{_link_name(ref)}, read by DirectQuery, and that model was not supplied.\n"
                    "  A composite model is read through the models it chains to. Fetch it "
                    "too (the powerbi-fabric plug's `with_model`, or the extractor's "
                    "--with-model, follows the chain), or put its folder beside this one.")
            if len(hits) > 1:
                raise _refused(
                    f"{_link_name(ref)}, which {here.name} reads by DirectQuery, matches "
                    f"{len(hits)} supplied models: " + ", ".join(str(h) for h in hits)
                    + ". Supply only the one the chain names.")
            target = hits[0]
            if str(target.resolve()) in chain:
                raise _refused(
                    f"the composite-model chain loops: {_link_name(ref)} is already on it ("
                    + " -> ".join(pathlib.Path(c).name for c in chain)
                    + f" -> {target.name}).")
            upstream[key] = {"facts": _parse_model(target, chain + (str(target.resolve()),)),
                             "path": target, "ref": ref}
        up = upstream[key]
        notes += _adopt_remote(t, ent["entity"], up, measures, relationships, here)
    _adopt_relationships(tables, relationships, upstream)
    return notes


def _adopt_remote(t: dict, entity: str, up: dict, measures: list[dict],
                  relationships: list[dict], here: pathlib.Path) -> list[str]:
    """One local table takes the definition of the upstream model's table `entity`."""
    facts = up["facts"]
    by_name = {(rt.get("logical_name") or rt["name"]).casefold(): rt for rt in facts["tables"]}
    rt = by_name.get(entity.casefold())
    if rt is None:
        raise _refused(
            f"table {t.get('logical_name') or t['name']!r} of {here.name} reads table "
            f"{entity!r} of {_link_name(up['ref'])}, and that model has no such table.")
    local = t.get("logical_name") or t["name"]
    t["logical_name"] = local
    # upstream table -> this model's name for it, for the relationships adopted after
    up.setdefault("local_of", {})[(rt.get("logical_name") or rt["name"]).casefold()] = local
    t["source_lineage"] = json.loads(json.dumps(rt.get("source_lineage") or {}))
    t["source_lineage"].pop("native_sql", None)
    t["source_lineage"].pop("native_columns", None)
    if rt["name"] != (rt.get("logical_name") or rt["name"]):
        t["name"] = rt["name"]            # the physical table at the end of the chain
    if not t.get("description") and rt.get("description"):
        t["description"] = rt["description"]
    remote_cols = {c["name"].casefold(): c for c in rt.get("columns") or []}
    if not t.get("columns"):
        t["columns"] = json.loads(json.dumps(rt.get("columns") or []))
    for c in t["columns"]:
        if c.get("is_calculated"):
            continue
        rc = remote_cols.get(str(c.get("source_column") or c["name"]).casefold())
        if rc is None:
            continue
        c["source_column"] = rc.get("source_column")
        if rc.get("is_calculated"):
            c["is_calculated"] = True
            c["formula"], c["formula_language"] = rc.get("formula"), rc.get("formula_language")
        if not c.get("data_type") and rc.get("data_type"):
            c["data_type"] = rc["data_type"]
    # The upstream model's measures on this table, unless this model declares its own.
    have = {m["name"].casefold() for m in measures}
    homes = {entity.casefold(), str(rt["name"]).casefold()}
    for m in facts.get("measures") or []:
        if str(m.get("home_table") or "").casefold() in homes and m["name"].casefold() not in have:
            measures.append({**m, "home_table": local})
            have.add(m["name"].casefold())
    return [f"table {local!r} is table {entity!r} of {_link_name(up['ref'])} (read from "
            f"{up['path'].name}), sourced from {t['name']}."]


def _adopt_relationships(tables: list[dict], relationships: list[dict],
                         upstream: dict[str, dict]) -> None:
    """The upstream models' relationships between tables this model also reads, under this
    model's table names, unless this model already declares one on the same columns."""
    for up in upstream.values():
        local_of = up.get("local_of") or {}
        seen = {(r["from_table"].casefold(), r["from_column"].casefold(),
                 r["to_table"].casefold(), r["to_column"].casefold()) for r in relationships}
        for r in up["facts"].get("relationships") or []:
            f, to = local_of.get(r["from_table"].casefold()), local_of.get(r["to_table"].casefold())
            if not (f and to):
                continue
            k = (f.casefold(), r["from_column"].casefold(), to.casefold(),
                 r["to_column"].casefold())
            if k not in seen:
                seen.add(k)
                relationships.append({**r, "from_table": f, "to_table": to})


def readable_models(root: pathlib.Path) -> list[pathlib.Path]:
    """The models of an extract a run could read on its own: every model except one that
    another model of the same extract reads by DirectQuery (an upstream link of a chain,
    which is read THROUGH the model above it). A loop leaves every model in."""
    models = _crawler.find_models(root)
    upstream: set[pathlib.Path] = set()
    for m in models:
        exprs = shared_expressions(sorted(m.rglob("*.tmdl")))
        for expr in exprs.values():
            ref = _mquery.remote_model(expr)
            if ref is None:
                continue
            hits = find_upstream(m, ref, [x for x in models if x != m])
            if len(hits) == 1:
                upstream.add(hits[0])
    top = [m for m in models if m not in upstream]
    return top or models


_NO_RESTRICTION = object()
_MISSING = object()
# Power BI's comparison kinds, in the semantic query's own numbering.
_COMPARISON = {0: "eq", 1: "gt", 2: "gte", 3: "lt", 4: "lte"}
# The semantic query's time units (`TimeUnit` in the PBIR semanticQuery schema).
_TIME_UNITS = {0: "day", 1: "week", 2: "month", 3: "year", 4: "decade", 5: "second",
               6: "minute", 7: "hour"}
# What an EMPTY cell is in a filter's comparison: DAX compares a BLANK as 0, as the empty
# text and as the zero date (30 Dec 1899), so `qty < 5` keeps the rows with no quantity. Said
# on every filter this reader writes; the compiler applies it, never the tool's name.
_NULL_AS = {"number": 0, "text": "", "date": "1899-12-30"}
# The visual types that SELECT rather than show: a slicer's saved selection filters the other
# visuals on its page, and it is no question of its own.
_SLICER_TYPES = {"slicer", "advancedslicervisual", "listslicer", "textslicer"}
# Power BI's relative date filters count a week from Sunday, whatever the model's culture
# ("The relative week options assume weeks start on Sunday": Microsoft Learn, "Create a
# relative date slicer or filter in Power BI", considerations).
_WEEK_START = "sunday"


class _ModelView:
    """What a report's fields resolve against beyond their own text: the model's hierarchy
    levels (a level is a column of its table) and its field switches (a table of choices,
    and the choices a page's slicer saved), set while one report is read."""

    def __init__(self, tables: list[dict]) -> None:
        self.levels: dict[tuple[str, str, str], str] = {}
        self.choices: dict[str, list[dict]] = {}
        self.picks: dict[str, list[str]] = {}
        for t in tables or []:
            names = {str(t.get("logical_name") or ""), str(t.get("name") or "")} - {""}
            for h in t.get("hierarchies") or []:
                for lv in h.get("levels") or []:
                    for n in names:
                        self.levels[(n.lower(), str(h["name"]).lower(),
                                     str(lv["name"]).lower())] = str(lv["column"])
            if t.get("field_choices"):
                for n in names:
                    self.choices[n.lower()] = sorted(t["field_choices"],
                                                     key=lambda c: c.get("order", 0))

    def chosen(self, table: str) -> list[dict]:
        """The choices a visual on this page shows: the page's saved selection, else the
        first by order (what the switch shows with nothing selected)."""
        items = self.choices.get(str(table).lower()) or []
        want = self.picks.get(str(table).lower())
        if want:
            got = [c for c in items if c["label"] in want]
            if got:
                return got
        return items[:1]


_VIEW: contextvars.ContextVar[_ModelView | None] = contextvars.ContextVar("pbi_view",
                                                                          default=None)


def _choice_field(c: dict) -> dict:
    """A field switch's choice as the semantic-query field it puts on the visual."""
    kind = "Column" if c["kind"] == "column" else "Measure"
    ref = {"Expression": {"SourceRef": {"Entity": c.get("table") or ""}}, "Property": c["name"]}
    return {kind: ref}


def _lit(node: dict):
    """A semantic-query literal -> its value: `'RETAIL'` -> RETAIL, `100L` -> 100,
    `datetime'2025-01-01T00:00:00'` -> 2025-01-01 (a date: midnight), `null` -> None.
    _MISSING when the node is not a literal."""
    raw = ((node or {}).get("Literal") or {}).get("Value")
    if not isinstance(raw, str):
        return _MISSING
    if raw == "null":
        return None
    m = re.fullmatch(r"(datetime|date|time)?'(.*)'", raw, re.S)
    if m:
        text = m.group(2).replace("''", "'")
        if m.group(1) in ("datetime", "date"):
            d = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}:\d{2})(?:\.0+)?)?", text)
            if d:
                return d.group(1) if d.group(2) in (None, "00:00:00") \
                    else f"{d.group(1)} {d.group(2)}"
        return text
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)[LDM]?", raw)
    return m.group(1) if m else raw


def _literal_value(node: dict) -> str | None:
    """A literal as text, None for `null` or for no literal (the goals reader's form)."""
    v = _lit(node)
    return None if v is _MISSING else v


def _clock(node: dict) -> dict | None:
    """A bound computed from the moment the query runs (`Now`, moved by `DateAdd` and
    truncated by `DateSpan`) -> {"steps": [["add", n, unit] | ["span", unit], ...]}, applied
    in order to the current time. None when the node is no such expression."""
    node = node or {}
    if isinstance(node.get("Now"), dict):
        return {"steps": []}
    for key in ("DateAdd", "DateSpan"):
        d = node.get(key)
        if not isinstance(d, dict):
            continue
        inner = _clock(d.get("Expression") or {})
        unit = _TIME_UNITS.get(d.get("TimeUnit"))
        if inner is None or unit is None:
            return None
        if key == "DateSpan":
            return {"steps": inner["steps"] + [["span", unit]]}
        amt = d.get("Amount")
        if not isinstance(amt, (int, float)) or isinstance(amt, bool) or amt != int(amt):
            return None
        return {"steps": inner["steps"] + [["add", int(amt), unit]]}
    return None


def _flat(kind: str, parts: list[tuple[str, Any]]) -> list[dict]:
    out: list[dict] = []
    for op, value in parts:
        if op == kind:
            out.extend(value)
        else:
            out.append({"op": op, "value": value})
    return out


def _week_rule(op: str, value: Any) -> tuple[str, Any]:
    """A relative range moved or cut by a week says the day a week starts on."""
    if op == "relative_range" and isinstance(value, dict) and any(
            str(st[-1]) == "week" for b in (value.get("lower"), value.get("upper")) if b
            for st in b.get("steps") or []):
        return op, {**value, "week_start": _WEEK_START}
    return op, value


def _tuple(inn: dict, sources: dict[str, str]) -> tuple[str, Any]:
    """An `In` over several columns at once (a hierarchy slicer's selected paths): `tuple`,
    the columns and the selected combinations, each column a model column or a date level
    (`_derived` carries the levels to declare)."""
    cols, derived = [], []
    for e in inn.get("Expressions") or []:
        f = _field(e if isinstance(e, dict) else {}, sources)
        if f is None or f["kind"] not in ("column", "date_level"):
            return "tuple", None
        cols.append({"column": str(f["name"]).lower(),
                     **({"table": str(f["table"])} if f.get("table") else {})})
        if f["kind"] == "date_level":
            derived.append(dict(f))
    rows = []
    for r in inn.get("Values") or []:
        if not isinstance(r, list) or len(r) != len(cols):
            return "tuple", None
        vals = [_lit(x) for x in r]
        if any(v is _MISSING for v in vals):
            return "tuple", None
        rows.append(vals)
    if not rows:
        return "tuple", None
    return "tuple", {"columns": cols, "rows": rows, **({"_derived": derived} if derived else {})}


def _columns_tested(c: dict, sources: dict[str, str]) -> set[tuple[str, str]]:
    """(table, column) of every field the leaves of an And / Or tree test."""
    if not isinstance(c, dict):
        return set()
    for key in ("And", "Or"):
        if key in c:
            return (_columns_tested((c[key] or {}).get("Left") or {}, sources)
                    | _columns_tested((c[key] or {}).get("Right") or {}, sources))
    if "Not" in c:
        return _columns_tested((c["Not"] or {}).get("Expression") or {}, sources)
    fd = _field(_condition_field(c) or {}, sources)
    return {(str(fd.get("table") or "").lower(), str(fd["name"]).lower())} if fd else set()


def _paths_tuple(c: dict, sources: dict[str, str]) -> tuple[str, Any]:
    """An Or of Ands of equalities over the same columns -> `tuple` {columns, rows}."""
    def leaves(node, key):
        if isinstance(node, dict) and key in node:
            return (leaves((node[key] or {}).get("Left") or {}, key)
                    + leaves((node[key] or {}).get("Right") or {}, key))
        return [node]
    cols: list[dict] | None = None
    rows = []
    for path in leaves(c, "Or"):
        row = {}
        for eq in leaves(path, "And"):
            cmp = (eq or {}).get("Comparison") or {}
            fd = _field(cmp.get("Left") or {}, sources)
            val = _lit(cmp.get("Right") or {})
            if cmp.get("ComparisonKind") != 0 or fd is None or fd["kind"] != "column" \
                    or val is _MISSING:
                return "tuple", None
            row[(str(fd.get("table") or ""), str(fd["name"]).lower())] = val
        keys = sorted(row)
        if cols is None:
            cols = [{"column": k[1], **({"table": k[0]} if k[0] else {})} for k in keys]
        elif [(x.get("table") or "", x["column"]) for x in cols] != keys:
            return "tuple", None
        rows.append([row[k] for k in keys])
    return "tuple", {"columns": cols, "rows": rows} if cols else None


def _condition(c: dict, sources: dict[str, str] | None = None) -> tuple[str, Any]:
    return _week_rule(*_condition_of(c, sources or {}))


def _condition_of(c: dict, sources: dict[str, str]) -> tuple[str, Any]:
    """One semantic-query condition -> (op, value) in the common filter format. A condition
    this cannot read keeps its op with no value, which the compiler refuses by name rather
    than dropping.

    * `In`: `in` with the members; a `null` member is the BLANK member (JSON null in the
      list), a list of only it is `is_null`. Over several columns at once (a hierarchy slicer's
      tuples) it is `tuple` {columns, rows}: one column's members alone would drop the others.
    * `Not`: `not_in`, `not_null`, `not_contains`, `not_starts_with`.
    * `Comparison`: eq/gt/lt, gte/lte as a one-sided `range`; `= null` is `is_null`.
    * `Contains`, `StartsWith`; `And`/`Or` of two conditions: a `range`, or `all_of` / `any_of`
      with the parts as {op, value}.
    * `Between` with literal bounds: a `range`. A bound computed from now (`Now`, `DateAdd`,
      `DateSpan`: a relative date or time filter): `relative_range` {lower, upper}, each
      {"steps": [...]} (`_clock`).
    """
    c = c or {}
    if "In" in c:
        inn = c["In"] or {}
        rows = [r for r in (inn.get("Values") or []) if isinstance(r, list) and r]
        if len(inn.get("Expressions") or []) > 1 or any(len(r) > 1 for r in rows):
            return _tuple(inn, sources)
        vals = [_lit(r[0]) for r in rows]
        if not vals or any(v is _MISSING for v in vals):
            return "in", None
        if vals == [None]:
            return "is_null", True
        return "in", vals if len(vals) > 1 else vals[0]
    if "Not" in c:
        op, value = _condition((c["Not"] or {}).get("Expression") or {}, sources)
        if op == "tuple":
            return op, ({**value, "negated": True} if value else None)
        if value is None:
            return "not", None
        if op == "in":
            return "not_in", value if isinstance(value, list) else [value]
        if op == "is_null":
            return "not_null", True
        if op in ("contains", "starts_with"):
            return f"not_{op}", value
        return "not", None
    if "Comparison" in c:
        cmp = c["Comparison"] or {}
        kind = _COMPARISON.get(cmp.get("ComparisonKind"))
        clock = _clock(cmp.get("Right") or {})
        if clock is not None:
            if kind == "gte":
                return "relative_range", {"lower": clock, "upper": None}
            if kind == "lte":
                return "relative_range", {"lower": None, "upper": clock}
            return "relative_range", None
        value = _lit(cmp.get("Right") or {})
        if kind is None or value is _MISSING:
            return "comparison", None
        if value is None:
            return ("is_null", True) if kind == "eq" else ("comparison", None)
        if kind == "gte":
            return "range", {"min": value, "max": None}
        if kind == "lte":
            return "range", {"min": None, "max": value}
        return kind, value
    for key, op in (("Contains", "contains"), ("StartsWith", "starts_with")):
        if key in c:
            value = _lit((c[key] or {}).get("Right") or {})
            if value is _MISSING or value is None:
                return op, None
            return op, str(value)
    if "Between" in c:
        b = c["Between"] or {}
        lo, hi = b.get("LowerBound") or {}, b.get("UpperBound") or {}
        clo, chi = _clock(lo), _clock(hi)
        if clo is not None and chi is not None:
            return "relative_range", {"lower": clo, "upper": chi}
        vlo, vhi = _lit(lo), _lit(hi)
        if vlo in (_MISSING, None) or vhi in (_MISSING, None):
            return "range", None
        return "range", {"min": vlo, "max": vhi}
    for key, kind in (("And", "all_of"), ("Or", "any_of")):
        if key in c:
            if len(_columns_tested(c, sources)) > 1:
                # Tests of SEVERAL columns joined by And / Or (a hierarchy slicer may save its
                # paths as an Or of Ands of equalities): the paths as a `tuple` when that is
                # the shape, else refused by name; read as one column's tests it would be
                # silently wrong.
                return _paths_tuple(c, sources)
            left = _condition((c[key] or {}).get("Left") or {}, sources)
            right = _condition((c[key] or {}).get("Right") or {}, sources)
            if left[1] is None or right[1] is None:
                return kind, None
            if kind == "all_of" and left[0] == right[0] == "range" \
                    and None in (left[1]["min"], right[1]["min"]) \
                    and None in (left[1]["max"], right[1]["max"]):
                return "range", {"min": left[1]["min"] if left[1]["min"] is not None
                                 else right[1]["min"],
                                 "max": left[1]["max"] if left[1]["max"] is not None
                                 else right[1]["max"]}
            return kind, _flat(kind, [left, right])
    return "where", None


def _semantic_filter(filt: Any):
    """A filter's `filter` block -> (op, value), or _NO_RESTRICTION when it holds no
    condition at all (a filter card with nothing selected restricts nothing)."""
    where = (filt or {}).get("Where") if isinstance(filt, dict) else None
    if not where:
        return _NO_RESTRICTION
    if len(where) != 1:
        return "where", None
    sources = {str(x.get("Name")): str(x.get("Entity")) for x in filt.get("From") or []
               if isinstance(x, dict) and x.get("Name") and x.get("Entity")}
    return _condition((where[0] or {}).get("Condition") or {}, sources)


def _condition_field(cond: dict) -> dict | None:
    """The field a condition tests (its first column, measure or aggregation)."""
    cond = cond or {}
    for key in ("Column", "Measure", "Aggregation", "HierarchyLevel"):
        if key in cond:
            return {key: cond[key]}
    for key, sub in (("In", "Expressions"),):
        if key in cond:
            exprs = (cond[key] or {}).get(sub) or []
            return _condition_field(exprs[0]) if exprs else None
    for key in ("Comparison", "Contains", "StartsWith"):
        if key in cond:
            return _condition_field((cond[key] or {}).get("Left") or {})
    if "Between" in cond:
        return _condition_field((cond["Between"] or {}).get("Expression") or {})
    if "Not" in cond:
        return _condition_field((cond["Not"] or {}).get("Expression") or {})
    for key in ("And", "Or"):
        if key in cond:
            return _condition_field((cond[key] or {}).get("Left") or {})
    return None


def _filter_entry(f: dict, field: dict, scope: str, sources: dict[str, str] | None = None,
                  how: str | None = None) -> dict:
    """One filter (a filter card, or a slicer's saved selection) -> the common filter entry.

    The FIELD decides what is filtered: a column (of the table the file names), a level of a
    date hierarchy (its level column), or a MEASURE or a column aggregated there (an implicit
    measure): a filter on the visual's aggregated value, stage `measure`, `measure` naming it.
    A field of no known kind is kept, named, with no value: the filter is refused, never
    read as a column it is not. A Top N keeps the members of its field ranked by a measure:
    op `top_n`."""
    fd = _field(field or {}, sources)
    derived = [dict(fd)] if fd and fd["kind"] in ("implicit", "date_level") else []
    base = {"scope": scope, "null_as": dict(_NULL_AS)}
    ev = (f"powerbi {scope}-level " + ("Top N filter" if str(f.get("type") or "").lower()
                                         == "topn" else "filter")
          + (f" on {fd['table']}" if fd and fd.get("table") else "")
          + (f" ({how})" if how else ""))
    if fd is None:
        return {**base, "stage": "dimension", "column": str(f.get("name") or "filter"),
                "op": "field", "value": None,
                "evidence": ev + ": a field of a kind this reader does not know"}
    if fd["kind"] in ("measure", "implicit"):
        base.update({"stage": "measure", "column": fd["name"], "measure": fd["name"]})
    else:
        base.update({"stage": "dimension", "column": str(fd["name"]).lower(),
                     **({"table": str(fd["table"])} if fd.get("table") else {})})
    if derived:
        base["_derived"] = derived
    if str(f.get("type") or "").lower() == "topn":
        n, sort, by = _topn_of([f])
        if by is not None and by["kind"] in ("implicit",):
            base.setdefault("_derived", []).append(dict(by))
        value = None
        if n is not None and sort:
            expr, _, direction = sort.rpartition(" ")
            # Power BI's Top N: the members of the field ranked by the measure within every
            # other filter of the visual, a member whose measure is BLANK never ranked, and
            # every member tied with the Nth kept (TOPN keeps ties).
            value = {"n": n, "by": expr, "direction": direction.lower(), "ties": True,
                     "population": "filtered", "empty_members": "excluded"}
        if base["stage"] == "measure":
            value = None
        return {**base, "stage": "dimension" if value else base["stage"], "op": "top_n",
                "value": value, "evidence": ev}
    cond = _semantic_filter(f.get("filter"))
    if cond is _NO_RESTRICTION:
        # Nothing selected (or a drillthrough, whose value arrives when a person clicks):
        # context a reader sees, which narrows nothing. `value_kind: absent` says so, so the
        # compiler neither emits nor refuses it.
        return {**base, "op": "filter", "value": f.get("value"),
                **({"value_kind": "absent"} if f.get("value") is None else {}),
                "evidence": ev}
    op, value = cond
    if op == "tuple" and isinstance(value, dict) and value.get("_derived"):
        value = dict(value)
        base.setdefault("_derived", []).extend(value.pop("_derived"))
    return {**base, "op": op, "value": value, "evidence": ev}


def _pbir_filters(node: dict, scope: str) -> list[dict]:
    """PBIR `filterConfig.filters[]` -> the common filter entries. `howCreated` is carried
    into the evidence string because a Drillthrough filter and one the author placed on the
    page are different claims about the population."""
    return [_filter_entry(f, f.get("field") or {}, scope, how=f.get("howCreated"))
            for f in ((node.get("filterConfig") or {}).get("filters") or [])
            if isinstance(f, dict)]


def _slicer_selection(visual: dict) -> list[dict]:
    """A slicer's SAVED selection -> filter entries (scope `slicer`) for the other visuals
    on its page. The selection is the filter in `objects.general[].properties.filter`; the
    field is the one its condition tests, else the slicer's own field. Nothing selected
    restricts nothing."""
    out: list[dict] = []
    for g in ((visual.get("objects") or {}).get("general") or []):
        flt = ((((g or {}).get("properties") or {}).get("filter") or {}).get("filter"))
        if not isinstance(flt, dict) or not flt.get("Where"):
            continue
        sources = {str(x.get("Name")): str(x.get("Entity")) for x in flt.get("From") or []
                   if isinstance(x, dict) and x.get("Name") and x.get("Entity")}
        cond = ((flt["Where"][0] or {}).get("Condition") or {})
        field = _condition_field(cond)
        if field is None:
            proj = _projection_fields((visual.get("query") or {}).get("queryState") or {})
            field = (proj[0][1].get("field") or {}) if proj else {}
        out.append(_filter_entry({"filter": flt, "type": "Categorical"}, field, "slicer",
                                 sources, how="slicer selection"))
    return out


def _is_slicer(visual: dict) -> bool:
    return str(visual.get("visualType") or "").lower() in _SLICER_TYPES


def _obj_flag(entries: Any, prop: str, selector: str | None = None) -> bool | None:
    """A boolean formatting property (`{"expr": {"Literal": {"Value": "false"}}}`) of a
    visual's objects entry: the entry with no selector, or the one whose selector names a
    field (`selector.metadata`, the field's queryRef). None when not set."""
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        sel = (e.get("selector") or {}).get("metadata") if isinstance(e.get("selector"),
                                                                       dict) else None
        if (selector is None and e.get("selector")) or (selector is not None
                                                        and sel != selector):
            continue
        node = ((e.get("properties") or {}).get(prop) or {})
        lit = ((node.get("expr") or {}).get("Literal") or {}).get("Value")
        if isinstance(lit, str) and lit.lower() in ("true", "false"):
            return lit.lower() == "true"
    return None


def _totals(visual_type: str, objects: dict, fields: list[tuple[str, dict, dict]]
            ) -> list[dict]:
    """The total rows a table or a matrix shows, one per grain, by the grouping columns each
    keeps (leaf order). A table (`tableEx`) shows one grand total unless `total.totals` is
    off. A matrix (`pivotTable`) shows row subtotals (each row level but the innermost, and
    the grand total) unless `subTotals.rowSubtotals` is off, per level where an entry's
    selector names the level's field; columns the same with `columnSubtotals`; every row grain
    crossed with every column grain. Other visuals show none."""
    vt = str(visual_type or "").lower()
    objects = objects or {}
    groups = [(r, p, f) for r, p, f in fields if f["kind"] in ("column", "date_level")]
    if not groups:
        return []
    if vt == "tableex":
        if _obj_flag(objects.get("total"), "totals") is False:
            return []
        return [{"kind": "grand_total", "group_by": []}]
    if vt != "pivottable":
        return []
    sub = objects.get("subTotals")
    rows = [(p, f) for r, p, f in groups if str(r).lower() == "rows"]
    cols = [(p, f) for r, p, f in groups if str(r).lower() == "columns"]

    def grains(axis: list, prop: str) -> list[list[str]]:
        names = [f["name"] for _p, f in axis]
        if not names or _obj_flag(sub, prop) is False:
            return [names]
        out = [[]]
        for k, (p, _f) in enumerate(axis[:-1]):
            if _obj_flag(sub, prop, str(p.get("queryRef") or "")) is not False:
                out.append(names[:k + 1])
        return out + [names]
    leaf = ([f["name"] for _p, f in rows], [f["name"] for _p, f in cols])
    out: list[dict] = []
    seen: set[tuple] = set()
    for rg in grains(rows, "rowSubtotals"):
        for cg in grains(cols, "columnSubtotals"):
            if (rg, cg) == leaf:
                continue
            keep = list(dict.fromkeys(rg + cg))
            if tuple(keep) in seen:
                continue
            seen.add(tuple(keep))
            out.append({"kind": "subtotal" if keep else "grand_total", "group_by": keep})
    return out


def _blank_rows(query_state: dict) -> str:
    """`hidden`: the visual leaves out the rows whose every value is BLANK. `shown`: "Show
    items with no data" (`showAll`) lists the members with no rows too."""
    return "shown" if any(isinstance(b, dict) and b.get("showAll")
                          for b in (query_state or {}).values()) else "hidden"


# Power BI's aggregate functions for a COLUMN aggregated at the use site (an implicit
# measure), in the semantic query's own numbering (`QueryAggregateFunction` in the PBIR
# semanticQuery schema, microsoft/json-schemas): the DAX that computes it and the label the
# report shows. Count (Distinct) and Count (Not Blanks) skip blanks, and Standard deviation and
# Variance are the population forms: the worked example in Microsoft's "Work with aggregates"
# page gives 3 distinct values and a variance of 416.67 for six values and one blank.
_IMPLICIT = {0: ("SUM", "Sum"), 1: ("AVERAGE", "Average"),
             2: ("DISTINCTCOUNTNOBLANK", "Count (Distinct)"), 3: ("MIN", "Min"),
             4: ("MAX", "Max"), 5: ("COUNTA", "Count"), 6: ("MEDIAN", "Median"),
             7: ("STDEV.P", "Standard deviation"), 8: ("VAR.P", "Variance")}

# The levels of an AUTO date/time hierarchy (the hidden date table Power BI builds for each
# date column when the model leaves auto date/time on) -> the DAX of each level, over the
# source column. The hidden table's columns are `Year = YEAR([Date])`, `Quarter = "Qtr " &
# [QuarterNo]`, `Month = FORMAT([Date], "MMMM")` and `Day = DAY([Date])`, joined on the date
# part; a date with no row in it is the blank member at every level. Month names are the
# model culture's; the English names are written out, so a model in another culture differs.
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_DATE_LEVELS = {
    "year": "YEAR({c})",
    "quarter": 'IF(ISBLANK({c}), BLANK(), "Qtr " & QUARTER({c}))',
    "month": "SWITCH(MONTH({c}), " + ", ".join(
        f'{i}, "{n}"' for i, n in enumerate(_MONTHS, 1)) + ")",
    "day": "DAY({c})",
}


def _dax_column(table: str | None, column: str) -> str:
    col = "[" + column.replace("]", "]]") + "]"
    return ("'" + table.replace("'", "''") + "'" + col) if table else col


def _source_entity(container: Any, sources: dict[str, str]) -> str | None:
    """The model table a field is on: PBIR names it (`SourceRef.Entity`); the legacy layout's
    query names an alias (`SourceRef.Source`) that its `From` maps to the table."""
    ref = ((container or {}).get("Expression") or {}).get("SourceRef") or {}
    ent = ref.get("Entity") or sources.get(str(ref.get("Source")))
    return str(ent) if ent else None


def _field(field: dict, sources: dict[str, str] | None = None) -> dict | None:
    """One semantic-query field -> what it is: a model measure, a column, a column aggregated
    at the use site (an implicit measure), or a level of a date hierarchy. None when it is none
    of these."""
    sources = sources or {}
    if not isinstance(field, dict):
        return None
    vc = field.get("NativeVisualCalculation")
    if isinstance(vc, dict) and vc.get("Expression") and vc.get("Name"):
        # A VISUAL CALCULATION: the visual's own formula over its matrix (its rows, its
        # totals, its other fields by their `nativeQueryRef`), saved on the projection
        # (semanticQuery schema: QueryNativeVisualCalc {Language, Expression, Name}).
        return {"kind": "visual_calc", "name": str(vc["Name"]),
                "expression": str(vc["Expression"]),
                "language": str(vc.get("Language") or "dax").lower()}
    for kind in ("Measure", "Column"):
        node = field.get(kind)
        if isinstance(node, dict) and node.get("Property"):
            return {"kind": kind.lower(), "name": str(node["Property"]),
                    "table": _source_entity(node, sources)}
    agg = field.get("Aggregation")
    if isinstance(agg, dict):
        inner = agg.get("Expression") or {}
        if isinstance(inner.get("Measure"), dict):
            return _field(inner, sources)
        col = inner.get("Column")
        if isinstance(col, dict) and col.get("Property"):
            table = _source_entity(col, sources)
            fn = _IMPLICIT.get(agg.get("Function"))
            if fn is None:
                # A code outside the schema's enum: kept as a measure that compiles to nothing
                # (no formula), so the unit says which field it could not read.
                return {"kind": "implicit", "name": f"{col['Property']} (aggregation "
                        f"{agg.get('Function')!r})", "table": table, "formula": None,
                        "column": str(col["Property"])}
            dax_fn, label = fn
            return {"kind": "implicit", "name": f"{label} of {col['Property']}",
                    "table": table, "column": str(col["Property"]), "label": label,
                    "formula": f"{dax_fn}({_dax_column(table, str(col['Property']))})"}
    if isinstance(field.get("Arithmetic"), dict) or isinstance(field.get("ScopedEval"), dict):
        # A value computed at the use site: "Show value as" a percent of a total is the value
        # divided by itself evaluated over a wider scope (`ScopedEval`: the scope lists the
        # columns that stay). Declared on the model as the DAX it stands for, with each scope
        # still open (`scopes`): which of the visual's grouping columns a scope removes is
        # known only once every field of the visual is read (`_bind_scopes`).
        inner = [f for f in (_field(x, sources) for x in _subfields(field)) if f]
        base = inner[0]["name"] if inner else "a value"
        scopes: list[tuple[str, list[tuple[str | None, str]]]] = []
        try:
            dax = _value_dax(field, sources, scopes)
        except ValueError:
            dax = None
        return {"kind": "implicit", "name": f"{base} (shown as a calculation)",
                "table": inner[0].get("table") if inner else None, "formula": dax,
                "column": base, "scopes": scopes, "show_as": True}
    lvl = field.get("HierarchyLevel")
    if isinstance(lvl, dict) and lvl.get("Level"):
        hier = ((lvl.get("Expression") or {}).get("Hierarchy") or {})
        src = hier.get("Expression") or {}
        pvs = src.get("PropertyVariationSource")
        level = str(lvl["Level"])
        if isinstance(pvs, dict) and pvs.get("Property"):
            # A level of the date column's own (auto date/time) hierarchy: a grouping derived
            # from the column, declared on its table as the calculated column it is.
            table = _source_entity(pvs, sources)
            tmpl = _DATE_LEVELS.get(level.lower())
            column = str(pvs["Property"])
            return {"kind": "date_level", "name": f"{column} {level}", "table": table,
                    "column": column, "level": level,
                    "formula": (tmpl.format(c=_dax_column(table, column)) if tmpl else None)}
        # A level of a hierarchy the model declares on a table: the column the model's
        # hierarchy names for the level (a level may be renamed), else the column of the
        # level's name.
        table = _source_entity(hier, sources)
        view = _VIEW.get()
        column = (view.levels.get((str(table or "").lower(), str(hier.get("Hierarchy") or "")
                                   .lower(), level.lower())) if view else None)
        return {"kind": "column", "name": column or level, "table": table}
    return None


# The semantic query's ArithmeticOperatorKind (semanticQuery schema, microsoft/json-schemas):
# 0 Add, 1 Subtract, 2 Multiply, 3 Divide. Divide is DAX's DIVIDE (blank on a zero divisor).
_ARITH = {0: "({0} + {1})", 1: "({0} - {1})", 2: "({0} * {1})", 3: "DIVIDE({0}, {1})"}
_SCOPE_MARK = "\x00scope{0}\x00"


def _value_dax(node: dict, sources: dict[str, str], scopes: list) -> str:
    """A value expression of a visual's query as DAX: a measure, a column aggregated at the
    use site, Arithmetic over two values, or a ScopedEval (registered in `scopes` as (its
    expression, the columns its scope keeps) and left as a mark). ValueError for anything
    else."""
    if isinstance(node.get("Arithmetic"), dict):
        a = node["Arithmetic"]
        tmpl = _ARITH.get(a.get("Operator"))
        if tmpl is None:
            raise ValueError(f"arithmetic operator {a.get('Operator')!r}")
        return tmpl.format(_value_dax(a.get("Left") or {}, sources, scopes),
                           _value_dax(a.get("Right") or {}, sources, scopes))
    if isinstance(node.get("ScopedEval"), dict):
        se = node["ScopedEval"]
        inner = _value_dax(se.get("Expression") or {}, sources, scopes)
        keep = []
        for x in se.get("Scope") or []:
            f = _field(x if isinstance(x, dict) else {}, sources)
            if f is None or f["kind"] not in ("column", "date_level"):
                raise ValueError("a scope that is not a column")
            keep.append((f.get("table"), f["name"]))
        scopes.append((inner, keep))
        return _SCOPE_MARK.format(len(scopes) - 1)
    f = _field(node, sources)
    if f is None or f["kind"] not in ("measure", "implicit") or not (
            f["kind"] == "measure" or f.get("formula")):
        raise ValueError("a value that is not a measure")
    if f["kind"] == "measure":
        return _dax_column(None, f["name"])
    return str(f["formula"])


def _bind_scopes(fields: list[tuple[str, dict, dict]]) -> None:
    """Each show-as field's DAX, its scopes closed over the visual's own grouping columns: a
    scope keeps the columns it lists and removes the visual's others, as ALLSELECTED does (the
    visual's filters stay: the total of the visual's own rows). The field is named as the
    report labels it (`nativeQueryRef`, e.g. "%GT Sum of AMOUNT")."""
    groups = [(f.get("table"), f["name"]) for _r, _p, f in fields
              if f["kind"] in ("column", "date_level")]
    for _r, p, f in fields:
        if not f.get("show_as"):
            continue
        if p.get("nativeQueryRef"):
            f["name"] = str(p["nativeQueryRef"])
        dax = f.get("formula")
        if not dax:
            continue
        for _round in range(len(f.get("scopes") or []) + 1):
            if "\x00" not in dax:
                break
            for i, (inner, keep) in enumerate(f["scopes"]):
                kept = {n.lower() for _t, n in keep}
                gone = [(t, n) for t, n in groups if n.lower() not in kept]
                body = (f"CALCULATE({inner}, "
                        + ", ".join(f"ALLSELECTED({_dax_column(t, n)})" for t, n in gone)
                        + ")") if gone else inner
                dax = dax.replace(_SCOPE_MARK.format(i), body)
        f["formula"] = dax if "\x00" not in dax else None
        f.pop("scopes", None)
        f.pop("show_as", None)


def _subfields(node: Any) -> list[dict]:
    """The measure, column or aggregation fields inside an expression, outermost first."""
    out: list[dict] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("Measure", "Column", "Aggregation") and isinstance(v, dict):
                out.append({k: v})
            else:
                out += _subfields(v)
    elif isinstance(node, list):
        for x in node:
            out += _subfields(x)
    return out


def _projection_fields(blocks: dict, sources: dict[str, str] | None = None,
                       resolve=None) -> list[tuple[str, dict, dict]]:
    """(role, projection, field) for every field a visual QUERIES. DRILL: a role holding
    several fields with `active` set shows only the active ones (the drill level the visual
    was saved at), so the others are not grouped by. `resolve(projection)` gives the field
    of a projection that does not carry its own (the legacy layout)."""
    out: list[tuple[str, dict, dict]] = []
    view = _VIEW.get()
    for role, blk in (blocks or {}).items():
        projs = (blk.get("projections") if isinstance(blk, dict) else blk) or []
        projs = [p for p in projs if isinstance(p, dict)]
        if view is not None and view.choices:
            # A FIELD SWITCH on the role: its label column stands for the field(s) chosen,
            # each projected in its place.
            wide = []
            for p in projs:
                col = (p.get("field") or {}).get("Column") or {}
                ent = _source_entity(col, sources or {}) if col else None
                if ent and ent.lower() in view.choices:
                    wide += [{"field": _choice_field(c), "queryRef": f"{ent}.{c['label']}",
                              "nativeQueryRef": c["name"], "_choice": (ent, c["label"])}
                             for c in view.chosen(ent)]
                else:
                    wide.append(p)
            projs = wide
        if any("active" in p for p in projs):
            projs = [p for p in projs if p.get("active") is not False]
        for p in projs:
            f = _field(p.get("field") or {}, sources)
            if f is None and resolve is not None:
                f = resolve(p)
            if f is None:
                # The model's OWN name first; the visual's label (`nativeQueryRef`, which an
                # author can rename per visual) is the fallback, never the answer.
                name = p.get("nativeQueryRef") or (p.get("queryRef") or "").split(".")[-1]
                if not name:
                    continue
                f = {"kind": "measure" if role.lower() in ("values", "y", "measure")
                     else "column", "name": str(name),
                     "table": (p.get("queryRef") or "").rpartition(".")[0] or None}
            out.append((role, p, f))
    _bind_scopes(out)
    return out


def _split_fields(fields: list[tuple[str, dict, dict]]) -> tuple[list[str], list[str]]:
    meas: list[str] = []
    cols: list[str] = []
    for _role, _p, f in fields:
        if f["kind"] == "visual_calc":
            continue
        (meas if f["kind"] in ("measure", "implicit") else cols).append(f["name"])
    return meas, cols


# The roles whose fields lay out each axis of a visual's matrix, where its visual
# calculations run: the ROWS axis (a matrix's Rows, a chart's category axis), the COLUMNS axis
# (a matrix's Columns, a chart's legend) and the ROWPAGES axis (small multiples). A table's
# columns are ONE level of the rows axis; any other role's field is one level each.
_AXIS_ROLES = {"rows": "rows", "category": "rows", "axis": "rows", "group": "rows",
               "details": "rows", "values": "rows", "columns": "columns", "series": "columns",
               "legend": "columns", "breakdown": "columns", "smallmultiple": "pages"}
_ONE_LEVEL = {"tableex", "multirowcard"}


def _visual_matrix(visual_type: str, fields: list[tuple[str, dict, dict]]) -> dict | None:
    """The matrix a visual's own calculations run over (`visual_matrix` in bi_facts), or None
    when the visual has none: its axes' levels, each calculation as saved, what each name an
    expression references is (`nativeQueryRef`, unique per visual), and the fields the visual
    hides (`hidden`: in the matrix for the calculations, not shown)."""
    calcs = [(p, f) for _r, p, f in fields if f["kind"] == "visual_calc"]
    if not calcs:
        return None
    vt = str(visual_type or "").lower()
    axes: dict[str, list[list[str]]] = {"rows": [], "columns": [], "pages": []}
    for role, _p, f in fields:
        if f["kind"] not in ("column", "date_level"):
            continue
        axis = _AXIS_ROLES.get(str(role).lower(), "rows")
        if vt in _ONE_LEVEL and axis == "rows":
            if not axes["rows"]:
                axes["rows"].append([])
            if f["name"] not in axes["rows"][0]:
                axes["rows"][0].append(f["name"])
        elif not any(f["name"] in lvl for lvl in axes[axis]):
            axes[axis].append([f["name"]])
    refs: dict[str, dict] = {}
    hidden: list[str] = []
    for _r, p, f in fields:
        kind = {"column": "column", "date_level": "column", "measure": "measure",
                "implicit": "measure", "visual_calc": "calc"}.get(f["kind"])
        if kind is None:
            continue
        for ref in (p.get("nativeQueryRef"), f["name"]):
            if ref and str(ref) not in refs:
                refs[str(ref)] = {kind: f["name"]}
        if p.get("hidden") and kind == "measure" and f["name"] not in hidden:
            hidden.append(f["name"])
    return {"language": calcs[0][1]["language"],
            "axes": {k: v for k, v in axes.items() if v},
            "calcs": [{"name": f["name"], "expression": f["expression"],
                       **({"hidden": True} if p.get("hidden") else {})} for p, f in calcs],
            "refs": refs, **({"hidden": hidden} if hidden else {})}


def _column_tables(fields: list[tuple[str, dict, dict]]) -> dict[str, str | None]:
    """Each projected COLUMN -> the model table it is on, or None when the visual names it
    from two tables. A column name alone cannot say which table: a key or a status is often on
    the fact and on a dimension both."""
    seen: dict[str, set[str]] = {}
    for _role, _p, f in fields:
        if f["kind"] in ("column", "date_level"):
            seen.setdefault(f["name"], set()).add(f.get("table") or "")
    return {c: (next(iter(v)) or None) if len(v) == 1 else None for c, v in seen.items()}


def _column_pairs(fields: list[tuple[str, dict, dict]], cols: list[str]
                  ) -> tuple[list[str], list[str | None]]:
    """The visual's grouping columns and the model table of each, sorted by name. A name the
    visual takes from TWO tables (YEAR of 'Order Date' and of 'Ship Date') is two columns,
    one per table, never one with no table."""
    seen: dict[str, set[str]] = {}
    for _role, _p, f in fields:
        if f["kind"] in ("column", "date_level"):
            seen.setdefault(f["name"], set()).add(f.get("table") or "")
    names: list[str] = []
    tables: list[str | None] = []
    for c in sorted(set(cols)):
        ts = sorted(seen.get(c) or {""})
        for t in (ts if len(ts) > 1 and "" not in ts else [ts[0] if len(ts) == 1 else ""]):
            names.append(c)
            tables.append(t or None)
    return names, tables


def _derived(fields: list[tuple[str, dict, dict]]) -> list[dict]:
    """What a visual defines beyond the model: its implicit measures and its date levels."""
    return [dict(f) for _r, _p, f in fields if f["kind"] in ("implicit", "date_level")]


def _pbir_projections(vis: dict) -> tuple[list[str], list[str]]:
    """`query.queryState.<role>.projections[]` -> (measures, columns).

    PBIR states the KIND on the field itself — `{"Measure": …}` or `{"Column": …}` — which is
    better evidence than a guess from the role name. An `Aggregation` field wraps a COLUMN with
    a function chosen at the use site (`Sum(FACT.amount)`): an IMPLICIT MEASURE, the number the
    visual shows, never a grouping column. It is named as the report labels it (`Sum of
    amount`) and declared on the model as the DAX it stands for (`finish_usage`), so the
    statement aggregates the column rather than grouping by it. A `HierarchyLevel` field is a
    level of a hierarchy: of a date column's own (auto date/time) hierarchy, a grouping derived
    from the column (`order_date Year`); of a declared hierarchy, the level's column.
    """
    return _split_fields(_projection_fields((vis.get("query") or {}).get("queryState") or {}))


def _pbir_column_tables(vis: dict) -> dict[str, str | None]:
    return _column_tables(_projection_fields((vis.get("query") or {}).get("queryState") or {}))


def finish_usage(usage: list[dict], tables: list[dict], measures: list[dict]) -> list[str]:
    """Declare on the model what the visuals define beyond it, and say which units name a
    calculated column. Shared by every Power BI container that reads a report.

    * An IMPLICIT MEASURE becomes a model measure with the DAX it stands for; one whose name a
      different model measure already has is named with its table (`Sum of FACT.amount`).
    * A DATE LEVEL becomes a calculated column on the date column's table.
    * A unit that groups or filters by a calculated column (the model's, or a date level) says
      its expression language, so the compiler expands the column's DAX instead of reading a
      warehouse column of that name.

    Returns notes for the reader's `_reader_notes`.
    """
    notes: list[str] = []
    by_name = {m["name"].lower(): m for m in measures}
    by_table = {}
    for t in tables:
        for key in {str(t.get("logical_name") or ""), str(t.get("name") or "")} - {""}:
            by_table.setdefault(key.lower(), t)
    for u in usage:
        rename: dict[str, str] = {}
        for d in u.pop("_derived", None) or []:
            if d["kind"] == "implicit":
                name = d["name"]
                have = by_name.get(name.lower())
                if have is not None and have.get("formula") != d.get("formula"):
                    # Another formula has the name: the table-qualified name, then a numbered
                    # one, until a free name or the same formula (two visuals that show one
                    # value "as a percent" over different columns are two measures).
                    alt = f"{d.get('label') or d['name']} of {d.get('table')}.{d['column']}"
                    n = 1
                    while True:
                        name = alt if n == 1 else f"{d['name']} ({n})"
                        have = by_name.get(name.lower())
                        if have is None or have.get("formula") == d.get("formula"):
                            break
                        n += 1
                if have is None:
                    m = {"name": name, "formula": d.get("formula") or "",
                         "formula_language": "dax", "home_table": d.get("table") or "",
                         "folder": None,
                         "description": (f"{d.get('label') or 'Aggregation'} of the column "
                                         f"{d['column']}, chosen on the visual"),
                         "aggregation_at_use_site": True, "origin": "visual"}
                    measures.append(m)
                    by_name[name.lower()] = m
                    if not d.get("formula"):
                        notes.append(f"a visual aggregates {d['column']!r} with a function "
                                     f"this reader does not know; it is not compiled")
                rename[d["name"]] = name
            elif d["kind"] == "date_level":
                t = by_table.get(str(d.get("table") or "").lower())
                if t is None or not d.get("formula"):
                    notes.append(f"date level {d['level']!r} of {d.get('table')}.{d['column']}"
                                 f" is not declared: "
                                 + ("its table is not in the model" if t is None else
                                    "only Year, Quarter, Month and Day are known"))
                    continue
                if not any(c["name"].lower() == d["name"].lower() for c in t["columns"]):
                    t["columns"].append({
                        "name": d["name"], "data_type": None, "caption": None,
                        "default_format": None,
                        "description": f"{d['level']} of {d['column']} (date hierarchy level)",
                        "is_calculated": True, "formula": d["formula"],
                        "formula_language": "dax", "source_column": None, "role": None})
        if rename:
            u["measures"] = sorted({rename.get(m, m) for m in u["measures"]})
            vm = u.get("visual_matrix")
            if vm:
                # The visual's calculations name its implicit measures as it labels them; the
                # names they are declared under follow.
                vm["refs"] = {k: ({"measure": rename.get(v["measure"], v["measure"])}
                                  if "measure" in v else v) for k, v in vm["refs"].items()}
                if vm.get("hidden"):
                    vm["hidden"] = [rename.get(m, m) for m in vm["hidden"]]
    calc = {c["name"].lower() for t in tables for c in t["columns"]
            if c.get("is_calculated") and c.get("formula") and c.get("formula_language") == "dax"}
    plain = {str(c.get("source_column") or c["name"]).lower() for t in tables
             for c in t["columns"] if not c.get("is_calculated")}
    for u in usage:
        named = {str(c).lower() for c in u.get("columns") or []} | {
            str(f.get("column") or "").lower() for f in u.get("filters") or []}
        if named & (calc - plain):
            u["expression_language"] = "dax"
    return notes


def _pbir_top_n(doc: dict) -> int | None:
    """A PBIR TopN filter states the limit in its own filter block, not on the visual."""
    return _topn_of((doc.get("filterConfig") or {}).get("filters") or [])[0]


def _topn_of(filters: list) -> tuple[int | None, str | None, dict | None]:
    """(limit, sort, the field ranked by) of a TopN filter, in either report format: the filter's subquery holds
    `Top` and the `OrderBy` it ranks by, a measure (or an aggregated column), `[name] DESC`.
    Without the sort the compiler ranked by the visual's first measure, which need not be the
    one the author chose (seen live: "Top 10 Customers" by Total Amount Sold)."""
    for f in filters or []:
        if not isinstance(f, dict) or str(f.get("type") or "").lower() != "topn":
            continue
        flt = f.get("filter") or {}
        # The subquery is inline in the `In` clause, or named there (`SourceRef`) and held in
        # the filter's `From` (seen live).
        named = {s.get("Name"): (s.get("Expression") or {}) for s in flt.get("From") or []
                 if isinstance(s, dict)}
        for clause in flt.get("Where") or []:
            top = ((clause.get("Condition") or {}).get("In") or {}).get("Table") or {}
            ref = (top.get("SourceRef") or {}).get("Source")
            if ref in named:
                top = named[ref]
            q = ((top.get("Subquery") or {}).get("Query") or {})
            n = q.get("Top")
            if not isinstance(n, int):
                continue
            sort = by = None
            for ob in q.get("OrderBy") or []:
                e = ob.get("Expression") or {}
                # By a measure, or by a column aggregated there (the implicit measure the
                # visual shows, named as `_field` names it).
                f = _field(e, {str(x.get("Name")): str(x.get("Entity"))
                               for x in q.get("From") or [] if isinstance(x, dict)})
                if f is not None and f["kind"] in ("measure", "implicit") \
                        and f.get("formula", "") is not None:
                    sort = f"[{f['name']}] {'ASC' if ob.get('Direction') == 1 else 'DESC'}"
                    by = f
                break
            return n, sort, by
    return None, None, None


def _legacy_filters(node: dict) -> list:
    raw = node.get("filters")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    return raw or []


def _parse_pbir_report(rdir: pathlib.Path, tables: list[dict]) -> list[dict]:
    """The PBIR on-disk format -> the SAME usage entries the legacy reader produces.

    WHY THIS EXISTS. A crawler extract carries both report formats side by side, and the base
    read only the legacy one. On a large test tenant extract that meant `usage: []` for
    every report — 341 `visual.json` files on disk and not one of them opened. Because
    `a2_question_units` iterates `usage`, empty usage means no question units, no compiled
    SQL and no metrics from that model: a silent, total loss that reads as a fact about the
    estate.

    The layout: `definition/pages/pages.json` gives the page order,
    `definition/pages/<page>/page.json` the page's own name and filters, and
    `definition/pages/<page>/visuals/<guid>/visual.json` one file per visual.

    The base's visual helpers are reused unchanged by presenting PBIR's
    `visualContainerObjects` under the key the legacy format calls `vcObjects` — the two hold
    the same author-written text under the same property shape, and duplicating the readers
    would be two things to keep in step for no gain.
    """
    pages_dir = rdir / "definition" / "pages"
    if not pages_dir.is_dir():
        return []

    report_name = _crawler.display_name(rdir) or rdir.name

    # REPORT-LEVEL filters, which the legacy reader folds into every unit and this one did
    # not read at all. They are among the container filters that must be folded into a
    # widget's single statement. None of the six reference reports carries one,
    # so nothing was lost today — but the PBIR path is now the only one that fires on a
    # crawler extract, so the gap was silent and unbounded on the next tenant.
    report_filters: list[dict] = []
    rj = rdir / "definition" / "report.json"
    if rj.is_file():
        try:
            report_filters = _pbir_filters(
                json.loads(rj.read_text(errors="ignore")) or {}, "report")
        except (OSError, json.JSONDecodeError):
            report_filters = []

    order: list[str] = []
    pages_json = pages_dir / "pages.json"
    if pages_json.is_file():
        try:
            order = list((json.loads(pages_json.read_text(errors="ignore"))
                          or {}).get("pageOrder") or [])
        except (OSError, json.JSONDecodeError):
            order = []

    page_dirs = sorted((d for d in pages_dir.iterdir() if d.is_dir()),
                       key=lambda d: (order.index(d.name) if d.name in order else len(order),
                                      d.name))

    pages: list[tuple[pathlib.Path, dict, list[tuple[pathlib.Path, dict]]]] = []
    for page_dir in page_dirs:
        meta: dict = {}
        pj = page_dir / "page.json"
        if pj.is_file():
            try:
                meta = json.loads(pj.read_text(errors="ignore")) or {}
            except (OSError, json.JSONDecodeError):
                meta = {}
        loaded: list[tuple[pathlib.Path, dict]] = []
        for vj in sorted((page_dir / "visuals").glob("*/visual.json")):
            try:
                loaded.append((vj, json.loads(vj.read_text(errors="ignore")) or {}))
            except (OSError, json.JSONDecodeError):
                continue
        pages.append((page_dir, meta, loaded))

    view = _ModelView(tables)
    token = _VIEW.set(view)
    try:
        # SYNCED SLICERS: slicers in one sync group (`syncGroup.groupName`, filter changes
        # synced) share one selection; a copy saved with none (hidden on its page, often)
        # takes the group's.
        synced: dict[str, list[dict]] = {}
        for _pd, _meta, loaded in pages:
            for _vj, doc in loaded:
                vis = doc.get("visual") or {}
                grp = vis.get("syncGroup") if isinstance(vis.get("syncGroup"), dict) else None
                if _is_slicer(vis) and grp and grp.get("groupName") \
                        and grp.get("filterChanges", True) is not False:
                    sel = _slicer_selection(vis)
                    if sel and str(grp["groupName"]) not in synced:
                        synced[str(grp["groupName"])] = sel
        usage: list[dict] = []
        for page_dir, meta, loaded in pages:
            usage += _pbir_page(page_dir, meta, loaded, report_name, report_filters, synced,
                                view)
    finally:
        _VIEW.reset(token)
    return usage


def _slicer_field_table(vis: dict) -> str | None:
    """The table of the field a slicer selects on."""
    proj = ((((vis.get("query") or {}).get("queryState") or {}).get("Values") or {})
            .get("projections") or [])
    col = ((proj[0] if proj else {}).get("field") or {}).get("Column") or {}
    return _source_entity(col, {}) if col else None


def _drillthrough(meta: dict) -> tuple[list[dict], set[str]]:
    """A drillthrough page's parameters (common `parameters`, kind `member`) and the names of
    the filters they bind. A page opened from another takes the member a person picked."""
    binding = meta.get("pageBinding") if isinstance(meta.get("pageBinding"), dict) else {}
    if str(binding.get("type") or "") != "Drillthrough":
        return [], set()
    by_name = {str(f.get("name")): f for f in ((meta.get("filterConfig") or {})
                                              .get("filters") or []) if isinstance(f, dict)}
    out, bound = [], set()
    for prm in binding.get("parameters") or []:
        if not isinstance(prm, dict) or prm.get("asAggregation"):
            continue
        flt = by_name.get(str(prm.get("boundFilter") or ""))
        fd = _field((prm.get("fieldExpr") or (flt or {}).get("field") or {}), {})
        if fd is None or fd["kind"] != "column":
            continue
        bound.add(str(prm.get("boundFilter") or ""))
        out.append({"name": str(prm.get("name") or fd["name"]), "kind": "member",
                    "column": str(fd["name"]).lower(), "table": fd.get("table"),
                    "keeps_filters": str(binding.get("acceptsFilterContext") or "Default")
                    != "None"})
    return out, bound


def _pbir_page(page_dir: pathlib.Path, meta: dict, loaded: list, report_name: str,
               report_filters: list[dict], synced: dict[str, list[dict]],
               view: _ModelView) -> list[dict]:
    """One PBIR page -> its usage entries (and their variants)."""
    usage: list[dict] = []
    pname = meta.get("displayName") or meta.get("name") or page_dir.name
    params, bound = _drillthrough(meta)
    page_filters, open_filters = [], []
    for f in ((meta.get("filterConfig") or {}).get("filters") or []):
        if not isinstance(f, dict):
            continue
        entry = _filter_entry(f, f.get("field") or {}, "page", how=f.get("howCreated"))
        page_filters.append(entry)
        if str(f.get("name") or "") not in bound:
            open_filters.append(entry)

    # SLICERS: each one's saved selection filters every other visual on the page, except
    # a visual whose interaction with it is set to none (`visualInteractions` NoFilter). A
    # slicer is no question of its own. A slicer on a FIELD SWITCH chooses the field the
    # page's visuals show; it filters no rows.
    view.picks = {}
    slicers = []
    for vj, doc in loaded:
        vis = doc.get("visual") or {}
        if not _is_slicer(vis):
            continue
        sel = _slicer_selection(vis)
        grp = vis.get("syncGroup") if isinstance(vis.get("syncGroup"), dict) else {}
        if not sel and grp.get("groupName") and grp.get("filterChanges", True) is not False:
            sel = [dict(f, evidence=f["evidence"] + " (synced from another page)")
                   for f in synced.get(str(grp["groupName"])) or []]
        ent = _slicer_field_table(vis)
        if ent and ent.lower() in view.choices:
            labels = []
            for f in sel:
                v = f.get("value")
                labels += [str(x) for x in (v if isinstance(v, list) else [v]) if x is not None]
            if labels:
                view.picks[ent.lower()] = labels
            continue
        slicers.append((doc.get("name") or vj.parent.name, sel))
    no_filter = {(str(x.get("source")), str(x.get("target")))
                 for x in meta.get("visualInteractions") or []
                 if isinstance(x, dict) and x.get("type") == "NoFilter"}
    loaded = [(vj, doc) for vj, doc in loaded if not _is_slicer(doc.get("visual") or {})]

    siblings: list[str] = []
    for vj, doc in loaded:
        vis = doc.get("visual") or {}
        single = {**vis, "vcObjects": vis.get("visualContainerObjects") or {}}
        sm, sc = _pbir_projections(vis)
        siblings.append(_vc_text(single, "title") or (sm[0] if sm else None)
                        or (sc[0] if sc else None)
                        or str(vis.get("visualType") or vj.parent.name))

    for vi, (vj, doc) in enumerate(loaded):
        vis = doc.get("visual") or {}
        qstate = (vis.get("query") or {}).get("queryState") or {}
        switch = _switch_tables(qstate, view)
        saved = dict(view.picks)
        alternatives = [None] + [{t: [c["label"]]} for t in switch
                                 for c in view.choices[t]
                                 if [c["label"]] != [x["label"] for x in view.chosen(t)]]
        for alt in alternatives:
            if alt is not None:
                view.picks = {**saved, **alt}
            try:
                entries = _pbir_visual(vi, vj, doc, pname, meta, report_name, siblings,
                                       slicers, no_filter, page_filters, open_filters,
                                       report_filters, params)
            finally:
                view.picks = saved
            if alt is not None:
                (t, (label,)), = alt.items()
                for e in entries:
                    e["unit_id"] += f"::{t}={label}"
                    e["variant_of"] = e.get("variant_of") or e["unit_id"].rsplit("::", 1)[0]
                    e["variant"] = (e.get("variant", "") + "; " if e.get("variant") else "") \
                        + f"the field switch {t!r} set to {label!r}"
            elif switch:
                for e in entries:
                    note = "; ".join(f"the field switch {t!r} at its saved choice "
                                     f"{[c['label'] for c in view.chosen(t)]}" for t in switch)
                    e["description"] = ((e.get("description") or "") + " " + note).strip()
            usage += entries
    return usage


def _switch_tables(qstate: dict, view: _ModelView) -> list[str]:
    """The field switches a visual's roles project (lower-case table names)."""
    out = []
    for blk in (qstate or {}).values():
        for p in (blk.get("projections") if isinstance(blk, dict) else []) or []:
            col = ((p or {}).get("field") or {}).get("Column") or {}
            ent = _source_entity(col, {}) if col else None
            if ent and ent.lower() in view.choices and ent.lower() not in out:
                out.append(ent.lower())
    return out


def _kpi_trend(visual_type: str, fields: list[tuple[str, dict, dict]]) -> str | None:
    """The trend axis of a KPI visual (its number is the value at the axis' latest point)."""
    if str(visual_type or "").lower() != "kpi":
        return None
    trend = [f["name"] for r, _p, f in fields if str(r).lower() in ("trendline", "trend")
             and f["kind"] in ("column", "date_level")]
    return trend[0] if len(trend) == 1 else None


def _pbir_visual(vi: int, vj: pathlib.Path, doc: dict, pname: str, meta: dict,
                 report_name: str, siblings: list[str], slicers: list, no_filter: set,
                 page_filters: list[dict], open_filters: list[dict],
                 report_filters: list[dict], params: list[dict]) -> list[dict]:
    """One visual -> its usage entry, and its variants: on a drillthrough page, the value for
    each member it can be opened with (grouped by the member's column); on a KPI, the latest
    point of its trend (the number the tile shows)."""
    usage: list[dict] = []
    vis = doc.get("visual") or {}
    single = {**vis, "vcObjects": vis.get("visualContainerObjects") or {}}
    qstate = (vis.get("query") or {}).get("queryState") or {}
    fields = _projection_fields(qstate)
    meas, cols = _split_fields(fields)
    vmat = _visual_matrix(vis.get("visualType"), fields)
    me = str(doc.get("name") or vj.parent.name)
    slicer_filters = [f for src, fl in slicers if (str(src), me) not in no_filter
                      for f in fl]
    filters = (_pbir_filters(doc, "visual") + slicer_filters + page_filters
               + report_filters)
    # A visual that queries nothing — a textbox, a shape — is decoration, not a
    # business question. Keeping it would put a unit through the compiler with no
    # measure to compile.
    if not (meas or cols or vmat):
        return []
    # TITLE FALLBACK, and why it is not the visual type. On a code-generated
    # report the author routinely hides the title (`title: [{show: false}]` —
    # 239 of 341 visuals on the reference extract), so there is no authored text.
    # Falling back to `visualType` put the string "barChart" in the field a person
    # reads, and C3 receives that as the widget's name. A MEASURE NAME is real text
    # someone recognises; the visual type is already its own field and does not need
    # to masquerade as a title.
    authored = _vc_text(single, "title")
    vname = (authored or (meas[0] if meas else None) or (cols[0] if cols else None)
             or str(vis.get("visualType")) or f"visual-{vi}")
    usage.append({
        "unit_id": f"powerbi::{report_name}::{pname}::{vj.parent.name}",
        "container": pname,
        "container_kind": "page",
        "dashboards": [{"name": pname, "title": pname, "position": vi}],
        "visual_id": vj.parent.name,
        "visual_type": str(vis.get("visualType") or "visual"),
        "title": str(vname),
        "measures": sorted(set(meas)),
        "columns": _column_pairs(fields, cols)[0],
        "column_tables": _column_pairs(fields, cols)[1],
        # `doc`, NOT `vis`: filterConfig is a SIBLING of "visual" at the document
        # root. Measured on the reference extract — 341 visual.json files, 14 with a
        # root-level filterConfig, ZERO with one under "visual". Reading the wrong
        # node dropped every visual-level filter silently, which is the worst class
        # of defect here: A3 then compiles the widget over a wider population, the
        # witness passes, faithfulness has nothing to contradict, and the published
        # metric describes a population the widget never showed.
        "filters": [{k: v for k, v in f.items() if k != "_derived"} for f in filters],
        "blank_rows": _blank_rows(qstate),
        # Written only where they say something, so a visual without them reads
        # as before.
        **({"totals": tot} if (tot := _totals(vis.get("visualType"),
                                              vis.get("objects") or {}, fields)
                               if meas or vmat else []) else {}),
        **({"distinct_rows": True} if not meas and not vmat else {}),
        **({"visual_matrix": vmat} if vmat else {}),
        "sort": _topn_of((doc.get("filterConfig") or {}).get("filters") or [])[1],
        "top_n": _pbir_top_n(doc) or _top_n(single),
        "parameters": [],
        "subtitle": _vc_text(single, "subTitle"),
        "description": meta.get("description"),
        "number_format": _format_of(single, {}),
        "axis_titles": _axis_titles(single),
        "tooltip": _vc_text(single, "tooltip"),
        "reference_lines": _reference_lines(single),
        "conditional_formatting": _conditional_formatting(single),
        "source_fragments": {
            "widget": None, "dashboard": None,
            "prose": [str(x) for x in (pname, report_name) if x],
        },
        "_aggregations": {},
        "_siblings": siblings,
        "_derived": _derived(fields) + [d for f in filters
                                        for d in f.get("_derived") or []],
    })
    entry = usage[-1]
    if params:
        entry["parameters"] = [dict(x) for x in params]
        names = {x["column"] for x in params}
        # Opened with a member: the value for EVERY member at once, grouped by its column
        # (the filter the opening binds taken off).
        var = json.loads(json.dumps(entry))
        extra = [x for x in params if x["column"] not in {c.lower() for c in var["columns"]}]
        var["columns"] = sorted(set(var["columns"]) | {x["column"] for x in extra})
        tables_of = dict(zip(entry["columns"], entry["column_tables"]))
        tables_of.update({x["column"]: x.get("table") for x in extra})
        var["column_tables"] = [tables_of.get(c) for c in var["columns"]]
        var["filters"] = [{k: v for k, v in f.items() if k != "_derived"} for f in filters
                          if not any(f is g for g in page_filters)
                          or any(f is g for g in open_filters)]
        var["unit_id"] = entry["unit_id"] + "::for each " + ", ".join(sorted(names))
        var["variant_of"] = entry["unit_id"]
        var["variant"] = ("the value it shows when opened with each member of "
                          + ", ".join(sorted(names)))
        var.pop("totals", None)
        if var.get("visual_matrix") and extra:
            # Each member opens its own visual: its calculations run within the member, as
            # over an outer level of pages (a page per member).
            vm = var["visual_matrix"]
            vm["axes"]["pages"] = [[x["column"]] for x in extra] + list(
                vm["axes"].get("pages") or [])
        var["_derived"] = entry["_derived"]
        usage.append(var)
    trend = _kpi_trend(entry["visual_type"], fields)
    if trend and meas:
        var = json.loads(json.dumps({k: v for k, v in entry.items() if k != "_derived"}))
        var["_derived"] = entry["_derived"]
        var["unit_id"] = entry["unit_id"] + "::latest"
        var["variant_of"] = entry["unit_id"]
        var["variant"] = f"the number the tile shows: the value at the latest {trend}"
        var["top_n"], var["sort"] = 1, f"[{trend}] DESC"
        var.pop("totals", None)
        usage.append(var)
    return usage


# The legacy layout's visual interaction codes (a page config's `relationships[].type`):
# 1 filter, 2 highlight, 3 none. From a third-party tool that writes the layout
# (cyphonica/powerbi-pbix-mcp `set_visual_interactions`: "type: 1=filter, 2=highlight,
# 3=none"); a code outside these is kept as an interaction this reader does not know.
_LEGACY_INTERACTION = {1: "filter", 2: "highlight", 3: "none"}


def _parse_report(root: pathlib.Path, tables: list[dict]) -> list[dict]:
    """Report pages and visual containers -> usage entries, the payload of this lane."""
    token = _VIEW.set(_ModelView(tables))
    try:
        return _parse_legacy_report(root)
    finally:
        _VIEW.reset(token)


def _parse_legacy_report(root: pathlib.Path) -> list[dict]:
    usage: list[dict] = []
    for jf in sorted(root.rglob("*.json")):
        try:
            doc = json.loads(jf.read_text(errors="ignore"))
        except Exception:
            continue
        pages = doc.get("sections") or doc.get("pages")
        if not isinstance(pages, list):
            continue
        report_name = doc.get("name") or jf.parent.name
        synced: dict[str, list[dict]] = {}
        for page in pages:
            for v in page.get("visualContainers") or page.get("visuals") or []:
                sv = _legacy_config(v).get("singleVisual") or {}
                grp = sv.get("syncGroup") if isinstance(sv.get("syncGroup"), dict) else {}
                if _is_slicer(sv) and grp.get("groupName") \
                        and grp.get("filterChanges", True) is not False:
                    sel = _slicer_selection(sv)
                    if sel:
                        synced.setdefault(str(grp["groupName"]), sel)
        for pi, page in enumerate(pages):
            pname = page.get("displayName") or page.get("name") or f"page-{pi}"
            visuals = page.get("visualContainers") or page.get("visuals") or []
            page_filters = _filters_of(page, "page")
            report_filters = _filters_of(doc, "report")
            siblings = [(v.get("title") or v.get("name") or f"visual-{i}")
                        for i, v in enumerate(visuals)]
            configs = [_legacy_config(v) for v in visuals]
            # Slicers, as on a PBIR page. The layout keeps a page's visual interactions in the
            # section config's `relationships` (`_LEGACY_INTERACTION`): `none` takes the
            # slicer's selection off the target, a filter or highlight keeps it (a slicer
            # filters), and a code this reader does not know leaves the selection with no
            # value, refused by name rather than filtered or not by a guess. A slicer in a
            # sync group with no selection of its own takes the group's.
            pcfg = _json_of(page.get("config"))
            touched = {(str(r.get("source")), str(r.get("target"))):
                       _LEGACY_INTERACTION.get(r.get("type"))
                       for r in (pcfg.get("relationships") or []) if isinstance(r, dict)}
            slicers = []
            for cfg in configs:
                sv = cfg.get("singleVisual") or {}
                if not _is_slicer(sv):
                    continue
                sel = _slicer_selection(sv)
                grp = sv.get("syncGroup") if isinstance(sv.get("syncGroup"), dict) else {}
                if not sel and grp.get("groupName"):
                    sel = synced.get(str(grp["groupName"])) or []
                slicers.append((str(cfg.get("name") or ""), sel))
            for vi, v in enumerate(visuals):
                cfg = configs[vi]
                if _is_slicer(cfg.get("singleVisual") or {}):
                    continue
                single = cfg.get("singleVisual") or v
                proj = single.get("projections") or {}
                fields = _projection_fields(proj, resolve=_legacy_resolver(single))
                meas, cols = _split_fields(fields)
                vmat = _visual_matrix(single.get("visualType") or v.get("type"), fields)
                vname = (_vc_text(single, "title") or single.get("title")
                         or v.get("title") or v.get("name") or cfg.get("name")
                         or f"visual-{vi}")
                subtitle = _vc_text(single, "subTitle")
                me = str(cfg.get("name") or "")
                slicer_filters = []
                for src, fl in slicers:
                    kind = touched.get((src, me), "filter") if (src, me) in touched else "filter"
                    if kind == "none":
                        continue
                    for f in fl:
                        if kind is None:
                            f = {**f, "op": "slicer_interaction", "value": None,
                                 "evidence": f["evidence"] + ": the page sets an interaction "
                                             "of a kind this reader does not know between "
                                             "this slicer and the visual"}
                        slicer_filters.append(f)
                filters = (_filters_of(v, "visual") + slicer_filters + page_filters
                           + report_filters)
                usage.append({
                    "unit_id": f"powerbi::{report_name}::{pname}::{vname}",
                    "container": pname,
                    "container_kind": "page",
                    "dashboards": [{"name": pname, "title": pname, "position": vi}],
                    "visual_id": str(vname),
                    "visual_type": str(single.get("visualType") or v.get("type") or "visual"),
                    "title": str(single.get("title") or vname),
                    "measures": sorted(set(meas)),
                    "columns": _column_pairs(fields, cols)[0],
                    "column_tables": _column_pairs(fields, cols)[1],
                    "filters": [{k: x for k, x in f.items() if k != "_derived"}
                                for f in filters],
                    # The layout's "Show items with no data": the roles it is on.
                    "blank_rows": "shown" if single.get("showAllRoles") else "hidden",
                    **({"totals": tot} if (tot := _totals(
                        single.get("visualType") or v.get("type"),
                        single.get("objects") or {}, fields) if meas or vmat else [])
                       else {}),
                    **({"distinct_rows": True} if not meas and cols and not vmat else {}),
                    **({"visual_matrix": vmat} if vmat else {}),
                    "sort": _topn_of(_legacy_filters(v))[1],
                    "top_n": _topn_of(_legacy_filters(v))[0] or _top_n(single),
                    "parameters": [],
                    "subtitle": subtitle,
                    "description": page.get("description") or doc.get("description"),
                    "number_format": _format_of(single, proj),
                    "axis_titles": _axis_titles(single),
                    "tooltip": _vc_text(single, "tooltip"),
                    "reference_lines": _reference_lines(single),
                    "conditional_formatting": _conditional_formatting(single),
                    "source_fragments": {
                        "widget": None, "dashboard": None,
                        "prose": [str(x) for x in (page.get("displayName"), report_name) if x],
                    },
                    "_aggregations": {},
                    "_siblings": siblings,
                    "_derived": _derived(fields) + [d for f in filters
                                                    for d in f.get("_derived") or []],
                })
    return usage


def _json_of(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _legacy_config(v: dict) -> dict:
    """A legacy visual container's `config` (a JSON string)."""
    return _json_of(v.get("config"))


# A legacy `queryRef` that aggregates a column: `Sum(FACT_SALES.amount)`. The function is the
# query's own name for it; the semantic query's `Select` says the same with its code.
_QUERYREF_AGG = re.compile(r"^\s*(Sum|Avg|Average|Min|Max|Count|CountNonNull|Median|"
                           r"StandardDeviation|Variance)\((.+)\)\s*$", re.I)
_QUERYREF_CODE = {"sum": 0, "avg": 1, "average": 1, "count": 2, "min": 3, "max": 4,
                  "countnonnull": 5, "median": 6, "standarddeviation": 7, "variance": 8}


def _legacy_resolver(single: dict):
    """The field behind a legacy projection. The layout's `prototypeQuery` is the same
    semantic query PBIR writes inline: its `Select` item NAMED by the projection's `queryRef`
    states the field's kind (a measure, a column, an aggregation of a column), and its `From`
    maps each alias to its table. A visual saved without one falls back to the `queryRef`
    itself, `Table.Column` or `Fn(Table.Column)`."""
    q = single.get("prototypeQuery") or {}
    sources = {str(x.get("Name")): str(x.get("Entity")) for x in q.get("From") or []
               if isinstance(x, dict) and x.get("Name") and x.get("Entity")}
    by_ref = {str(x.get("Name")): x for x in q.get("Select") or []
              if isinstance(x, dict) and x.get("Name")}

    def resolve(p: dict) -> dict | None:
        ref = str(p.get("queryRef") or "")
        sel = by_ref.get(ref)
        if sel is not None:
            got = _field(sel, sources)
            if got is not None:
                return got
        m = _QUERYREF_AGG.match(ref)
        if m:
            table, _, col = m.group(2).rpartition(".")
            return _field({"Aggregation": {
                "Expression": {"Column": {"Expression": {"SourceRef": {"Entity": table}},
                                          "Property": col}},
                "Function": _QUERYREF_CODE[m.group(1).lower()]}})
        return None
    return resolve


def _vc_text(single: dict, key: str) -> str | None:
    """Pull author-written text out of a visual's vcObjects block.

    Power BI nests it as vcObjects[key][0].properties.text.expr.Literal.Value, quoted. The
    visual's own `title` field is often absent while this carries the label a person reads.
    """
    vc = single.get("vcObjects") or {}
    entries = vc.get(key) or []
    for e in entries:
        props = (e or {}).get("properties") or {}
        for prop in ("text", "titleText"):
            node = props.get(prop) or {}
            lit = (((node.get("expr") or {}).get("Literal") or {}).get("Value"))
            if isinstance(lit, str) and lit.strip():
                return lit.strip().strip("'\"")
    return None


def _format_of(single: dict, proj: dict) -> str | None:
    objs = single.get("objects") or {}
    for group in objs.values():
        for e in group or []:
            props = (e or {}).get("properties") or {}
            for key in ("formatString", "format"):
                node = props.get(key)
                if isinstance(node, str):
                    return node
                lit = (((node or {}).get("expr") or {}).get("Literal") or {}).get("Value")
                if isinstance(lit, str):
                    return lit.strip().strip("'\"")
    return single.get("formatString")


def _axis_titles(single: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    objs = single.get("objects") or {}
    for axis in ("categoryAxis", "valueAxis"):
        for e in objs.get(axis) or []:
            t = (((e or {}).get("properties") or {}).get("titleText") or {})
            lit = ((t.get("expr") or {}).get("Literal") or {}).get("Value")
            if isinstance(lit, str) and lit.strip():
                out[axis] = lit.strip().strip("'\"")
    return out


def _reference_lines(single: dict) -> list[dict]:
    out: list[dict] = []
    for e in (single.get("objects") or {}).get("y1AxisReferenceLine") or []:
        props = (e or {}).get("properties") or {}
        val = ((props.get("value") or {}).get("expr") or {}).get("Literal", {}).get("Value")
        lab = ((props.get("displayName") or {}).get("expr") or {}).get("Literal", {}).get("Value")
        if val or lab:
            out.append({"value": (str(val).strip("'\"") if val else None),
                        "label": (str(lab).strip("'\"") if lab else None),
                        "kind": "reference-line"})
    return out


def _conditional_formatting(single: dict) -> list[dict]:
    out: list[dict] = []
    for e in (single.get("objects") or {}).get("dataPoint") or []:
        props = (e or {}).get("properties") or {}
        if "fill" in props or "fillRule" in props:
            out.append({"on": None, "palette": "dataPoint", "center": None})
    return out


def _filters_of(node: dict, scope: str) -> list[dict]:
    """The legacy layout's filters (a JSON string or a list; the field at `expression`) ->
    the same entries PBIR's give."""
    return [_filter_entry(f, f.get("expression") or {}, scope)
            for f in _legacy_filters(node) if isinstance(f, dict)]


def _top_n(single: dict) -> int | None:
    for k in ("topN", "TopN", "itemLimit"):
        v = single.get(k)
        if isinstance(v, int):
            return v
    return None


def _parse_expressions(root: pathlib.Path) -> list[dict]:
    """`definition/expressions.tmdl` -> the model's PARAMETERS.

    A shared M expression is only a parameter when it declares `IsParameterQuery=true`; the rest
    are shared queries and are not the model's tunables.

    Why they matter downstream: a parameter is a knob the report author deliberately exposed
    — a date window, an environment — and a parameterised M partition names its object as
    `@param` rather than a literal table. Without the declaration that token is an unexplained
    string in the lineage.

    SCRUBBED, like any M expression: a parameter routinely holds a server or database name.
    """
    path = root / "definition" / "expressions.tmdl"
    if not path.is_file():
        return []
    params: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        s = line.strip()
        if not s.startswith("expression ") or "=" not in s:
            continue
        decl, _, rest = s.partition("=")
        if "isparameterquery=true" not in rest.replace(" ", "").lower():
            continue
        name = _unquote(*EXPR_NAME.match(decl.strip()).groups()) if EXPR_NAME.match(
            decl.strip()) else decl[len("expression"):].strip()
        literal = rest.split("meta", 1)[0].strip()
        params.append({"name": name,
                       "value": _mquery.scrub_m(literal) or None,
                       "kind": "parameter"})
    return params


_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _extension_measures(root: pathlib.Path) -> list[tuple[str, dict]]:
    """A report's own measures (a thin report's local measures over a shared model), as each
    on-disk format keeps them: PBIR `definition/reportExtensions.json` (`entities[].measures`)
    and the legacy layout's `modelExtensions` in the report config. [(entity, measure), ...]."""
    found: list[tuple[str, dict]] = []
    for jf in sorted(root.rglob("reportExtensions.json")):
        try:
            doc = json.loads(jf.read_text(errors="ignore")) or {}
        except (OSError, json.JSONDecodeError):
            continue
        for ent in doc.get("entities") or []:
            for m in (ent.get("measures") if isinstance(ent, dict) else None) or []:
                if isinstance(m, dict) and m.get("name"):
                    found.append((str(ent.get("name") or ""), m))
    for jf in sorted(root.rglob("*.json")):
        if jf.name == "reportExtensions.json":
            continue
        try:
            doc = json.loads(jf.read_text(errors="ignore"))
        except Exception:  # noqa: BLE001 - not a report layout
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("sections"), list):
            continue
        for ext in _json_of(doc.get("config")).get("modelExtensions") or []:
            for ent in (ext.get("entities") if isinstance(ext, dict) else None) or []:
                for m in (ent.get("measures") if isinstance(ent, dict) else None) or []:
                    if isinstance(m, dict) and m.get("name"):
                        found.append((str(ent.get("extends") or ent.get("name") or ""), m))
    return found


def report_measures(root: pathlib.Path, report: str, measures: list[dict]) -> dict[str, str]:
    """Declare a report's own measures on the model (`origin: report`), so its visuals compile
    them, and return {name the report uses: name declared}. A name another definition already
    has (the model's, or another report's) is declared under the report's name in brackets: two
    thin reports may each define a `Margin`, differently."""
    by_name = {m["name"].lower(): m for m in measures}
    rename: dict[str, str] = {}
    for entity, rm in _extension_measures(root):
        name = str(rm["name"])
        m = {"name": name, "formula": str(rm.get("expression") or "").strip(),
             "formula_language": "dax", "home_table": entity or None,
             "folder": rm.get("displayFolder") or None,
             "description": str(rm.get("description") or ""),
             "aggregation_at_use_site": False,
             "format_string": rm.get("formatString") or None,
             "origin": "report", "report": report}
        if rm.get("hidden") is True:
            m["is_hidden"] = True
        have = by_name.get(name.lower())
        if have is not None and (have.get("formula") or "").strip() != m["formula"]:
            m["name"] = f"{name} ({report})"
            rename[name] = m["name"]
            have = by_name.get(m["name"].lower())
        if have is None:
            measures.append(m)
            by_name[m["name"].lower()] = m
    return rename


_ENDORSED = {"certified": "certified", "promoted": "promoted"}


def scan_endorsements(item_dir: pathlib.Path) -> dict[str, dict]:
    """What the admin scanner says about each dataset and report of an extract: {item id:
    {endorsement, description}}. Read from the saved scan results (`<extract>/scans/*.json`,
    documented `WorkspaceInfoResponse`: `endorsementDetails.endorsement` is `Promoted` or
    `Certified`). Who certified it (`certifiedBy`) is a person and is never read. {} when the
    extract holds no scan."""
    out: dict[str, dict] = {}
    for up in list(item_dir.parents)[:4]:
        scans = up / "scans"
        if not scans.is_dir():
            continue
        for sf in sorted(scans.glob("*.json")):
            try:
                doc = json.loads(sf.read_text(errors="ignore")) or {}
            except (OSError, json.JSONDecodeError):
                continue
            for ws in doc.get("workspaces") or []:
                for item in (ws.get("datasets") or []) + (ws.get("reports") or []):
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    level = str(((item.get("endorsementDetails") or {})
                                 .get("endorsement") or "")).lower()
                    out[str(item["id"]).lower()] = {
                        "endorsement": _ENDORSED.get(level),
                        "description": item.get("description") or None}
        break
    return out


def _endorse(asset: dict, scans: dict[str, dict]) -> dict:
    meta = scans.get(str(asset.get("vendor_id") or "").lower()) if scans else None
    if meta:
        if meta.get("endorsement"):
            asset["endorsement"] = meta["endorsement"]
        if meta.get("description"):
            asset["description"] = meta["description"]
    return asset


def _pbi_asset(role: str, name: str, item_dir: pathlib.Path) -> dict[str, Any]:
    """A report's or model's identity: its Fabric id where the directory is named by one
    (a crawler extract), and its workspace id from the directory above. A standalone export
    names its directories by hand, and then there is no id to claim."""
    ws = item_dir.parent.name
    return {"role": role, "name": name or None,
            "vendor_id": item_dir.name if _GUID.match(item_dir.name) else None,
            "path": None,
            "site": ws if _GUID.match(ws) else None,
            "catalog_id": None, "catalog_qualified_name": None}


def parse(path: pathlib.Path, report_index: dict | None = None,
          _chain: tuple = ()) -> dict[str, Any]:
    """One semantic model -> the vendor-neutral `bi_facts` contract.

    IN A CRAWLER EXTRACT THE REPORTS ARE NOT UNDER THE MODEL. `semanticModels/<ws>/<model>/`
    holds the TMDL and the visuals that use it live under `reports/<ws>/<report>/`, bound by
    `semanticmodelid=<guid>` in the report's `definition.pbir`. Parsing only the model
    directory therefore finds no visuals and yields `usage: []` — and because
    `a2_question_units` iterates `usage`, that means no question units, no compiled SQL and
    no metrics from this model. It reads as a finding about the estate when it is a property
    of where we looked, so the reports are resolved here and an empty result is said OUT LOUD
    by the caller rather than passed on quietly.

    A standalone export (a single model directory with its report beside it) still works: the
    sibling tree simply is not there, and the local scan is what answers.

    An extract ROOT holding ONE model a run could read (see `readable_models`) is read as that
    model; with several it is refused, since choosing would decide the run's scope.

    A table an entity partition names is resolved here (`resolve_entities`): Direct Lake to
    its lakehouse or warehouse table, a composite model's table to the definition of the
    model it chains to. `_chain` is the models already on the chain, for the cycle check.
    """
    root = path if path.is_dir() else path.parent
    only = _crawler.refuse_extract_root(root, readable_models)
    if only is not None:
        root = path = only

    files = _find_model(root)
    # The model's text parameters, which a partition's navigation may name instead of a
    # literal database, schema or table.
    shared = shared_expressions(files)
    tables, measures, relationships = _parse_tmdl(files, _mquery.text_parameters(shared))
    model_tags(files, tables, measures)
    measure_tables(tables, measures, relationships)
    calculation_groups(tables)
    storage_semantics(tables, relationships)
    classify_choices(tables)
    notes = _mquery.read_power_query(tables, shared)
    notes += resolve_entities(tables, measures, relationships, shared, root, _chain)

    def _read(d: pathlib.Path) -> list[dict]:
        """Either on-disk report format. An extract carries both, side by side. The report's
        own measures are declared on the model first, so its visuals compile them."""
        rename = report_measures(d, _crawler.display_name(d) or d.name, measures)
        got = _parse_report(d, tables) + _parse_pbir_report(d, tables)
        if rename:
            for u in got:
                u["measures"] = [rename.get(m, m) for m in u.get("measures") or []]
        return got

    report_dirs = _crawler.reports_for(root, report_index)
    scans = scan_endorsements(root)
    usage = _read(root)
    seen = {u.get("unit_id") for u in usage}
    for rd in report_dirs:
        # In a crawler extract an item's DIRECTORY NAME is its Fabric id; it is the identity
        # a catalog keys the report by, so every visual read from it carries it.
        report = _endorse(_pbi_asset("report", _crawler.display_name(rd) or rd.name, rd), scans)
        for u in _read(rd):
            if u.get("unit_id") not in seen:
                seen.add(u.get("unit_id"))
                u["assets"] = [report]
                usage.append(u)

    notes += finish_usage(usage, tables, measures)
    measure_uses(measures)
    model_asset = _endorse(_pbi_asset("semantic_model", _crawler.display_name(root) or root.name,
                                      root), scans)
    # The tool's own metadata a fetch wrote beside the model (`bi_tags.json`), when it did.
    _bi_tags.apply_sidecar([model_asset], _bi_tags.load_sidecar(root))

    out = {
        "contract_version": "1.0",
        "vendor": "powerbi",
        "source_file": str(path),
        # The model's own name where `.platform` carries one. Without it a model is known by
        # its GUID, and the GUID is what would reach every card a person reads.
        "model_name": _crawler.display_name(root) or root.name,
        "tables": _mquery.attach_native_statements(tables),
        "measures": measures,
        "relationships": relationships,
        "usage": usage,
        "assets": [model_asset],
        "parameters": _parse_expressions(root),
        "_unresolved_published": [],
    }
    if notes:
        out["_reader_notes"] = notes
    return out


# --------------------------------------------------------------------------- the reader
# WITHIN Power BI, the CONTAINER decides the form. All three write the same `bi_facts` format
# and share `readers/mquery`, so a warehouse target one can name and another cannot would be a
# bug rather than a format difference.
#
#   a directory      a crawler/Fabric extract: definition/model.tmdl  -> readers.powerbi
#   .pbix            the binary, VertiPaq metadata only               -> readers.pbix
#   .bim / .tmsl     Analysis Services Tabular, JSON                  -> readers.tabular
_FORM_BY_SUFFIX = {".pbix": "pbix", ".bim": "tabular", ".tmsl": "tabular"}


class PowerBIReader(FormulaCompiler):
    """The `powerbi` BI reader (`sockets.BIReader`), registered under `metrics_agent.readers`.

    Three forms, one format (see `_FORM_BY_SUFFIX`)."""

    name = "powerbi"
    # The formula languages it writes; its formulas compile through `FormulaCompiler`.
    languages = ("dax", "m-query")
    # `.tmdl` and `.json` are the files a model directory and its reports are made of.
    suffixes = (".pbix", ".bim", ".tmsl", ".tmdl", ".json")

    def detect(self, path: pathlib.Path) -> bool:
        if path.is_dir():
            return any(path.rglob("*.tmdl"))
        return path.suffix.lower() in _FORM_BY_SUFFIX

    def form(self, path: pathlib.Path) -> str:
        """Which form reads `path`, or a refusal naming what is supported."""
        if path.is_dir():
            return "powerbi"
        found = _FORM_BY_SUFFIX.get(path.suffix.lower())
        if found is not None:
            return found
        from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

        if path.suffix.lower() == ".pbit":
            raise ReaderRefused(
                f"{path.name} is a Power BI TEMPLATE (.pbit), which is not supported.\n"
                "  Open it in Power BI Desktop and save it as a .pbix, or export its semantic "
                "model as TMDL or a .bim, and pass that.")
        raise ReaderRefused(
            f"the powerbi reader does not know how to read {path.name}.\n"
            "  Supported: a semantic-model DIRECTORY (definition/model.tmdl), a .pbix, or a "
            ".bim/.tmsl.\n"
            "  A crawler extract ROOT is not one of them — inventory it and pass one model:\n"
            "    python3 scripts/s_extract_inventory.py --extract <root> --out inventory.json")

    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]:
        # The run's options every reader is handed (`dialect`, `policy`) mean nothing to a
        # TMDL or VertiPaq model; only a caller's report index is this reader's.
        # The run's dialect is: a table whose Power Query changes its rows is read as a SQL
        # statement, which runs on the run's warehouse (`mquery.read_power_query`).
        dialect = opts.get("dialect")
        opts = {k: v for k, v in opts.items() if k == "report_index"}
        kind = self.form(path)
        with _mquery.run_dialect(dialect):
            if kind == "pbix":
                from metrics_agent.plugs.readers import pbix  # noqa: PLC0415 - imports this module
                return pbix.parse(path, **opts)
            if kind == "tabular":
                from metrics_agent.plugs.readers import tabular  # noqa: PLC0415
                return tabular.parse(path, **opts)
            return parse(path, **opts)

    def refuse(self, path: pathlib.Path) -> str | None:
        """A crawler extract ROOT is a whole tenant, and a run builds ONE model: refused when
        it holds several models a run could read (one is `one_model`'s)."""
        try:
            _crawler.refuse_extract_root(path, readable_models)
        except _crawler.ExtractError as exc:
            return str(exc)
        return None

    @staticmethod
    def models(path: pathlib.Path) -> list[pathlib.Path]:
        """The models an extract ROOT holds that a run could read, one path each (an upstream
        model of a chain is read through the model above it); [] for one model or none, read
        as it is."""
        root = pathlib.Path(path)
        if not (root / "semanticModels").is_dir():
            return []
        found = readable_models(root)
        return found if len(found) > 1 else []

    def one_model(self, path: pathlib.Path) -> pathlib.Path | None:
        """The model an extract ROOT holds when it holds exactly one a run could read (an
        upstream model of a chain is read through the model above it, so it is no choice)."""
        try:
            return _crawler.refuse_extract_root(path, readable_models)
        except _crawler.ExtractError:
            return None

    @staticmethod
    def units_without_usage(facts: dict[str, Any]) -> list[dict[str, Any]]:
        """A model with no report of its own (a shared model, a composite one, a fetch of the
        model alone): its measures ARE its metric definitions. One curation unit each, as a
        scan's model is read, so they are compiled; `dashboards` stays empty, so none counts
        as placed on a page. Seen live on a composite model, which produced nothing."""
        from metrics_agent.plugs.readers import powerbi_scan as _scan  # noqa: PLC0415
        units, _hidden = _scan._measure_units(facts["model_name"], facts)
        for u in units:
            u.pop("_aggregations", None)
            u.pop("_siblings", None)
        return units

    def explain_empty_usage(self, path: pathlib.Path) -> tuple[dict[str, Any], list[str]]:
        """Zero usage is a property of the FETCH, and must be said that way.

        A model directory's visuals live in the report trees bound to it, and the envelopes
        are what can tell "the fetch never listed them" from "these reports have none". So
        they are consulted here and the answer names which case this is."""
        root = path if path.is_dir() else path.parent
        reports = _crawler.reports_for(root)
        integrity = _crawler.fetch_integrity(root, reports)
        counts = {"reports_found": len(reports), "envelopes": integrity["with_envelope"],
                  "missing_parts": len(integrity["missing_parts"])}
        if not reports:
            return counts, [
                "    No report directory is bound to this model. In a crawler extract a "
                "report names its model in definition.pbir (semanticmodelid=...); either "
                "none was fetched, or the binding is absent."]
        lines = [f"    {len(reports)} report directory/directories ARE bound to it — so the "
                 "reports were found and their visuals were not."]
        if integrity["missing_parts"]:
            lines.append(f"    {len(integrity['missing_parts'])} part(s) the envelope LISTS "
                         "are not on disk. The fetch did not finish:")
            lines += [f"        {m}" for m in integrity["missing_parts"][:8]]
        elif integrity["with_envelope"] == 0:
            lines.append("    No envelope.json on any of them, so nothing enumerated the "
                         "per-visual files and nothing can say whether any are missing.")
            lines.append("    Run: python3 scripts/s_build_envelopes.py --extract <root> "
                         "--verify — it lists what is actually present and checks it against "
                         "the .sha256 sidecars.")
        elif integrity["reconstructed"]:
            lines.append("    The envelope present was RECONSTRUCTED from what is on disk "
                         "(generatedBy), so it cannot notice a part that failed to download: "
                         "a missing file and a file that never existed look identical to it.")
        else:
            lines.append("    A crawler envelope is present and every part it lists is on "
                         "disk, so the fetch is complete and these reports genuinely carry "
                         "no queryable visuals.")
        return counts, lines
