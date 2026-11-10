# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Build the metric's declared `dimensions[]` and `joins[]` from the verified queries.

THE RULE THIS FILE EXISTS TO ENFORCE: **never drop a dimension.** Every dimension the SQL
evidences is declared. A sub-field we cannot resolve is ADMITTED IN PLACE (`unknown`), never
turned into a reason to omit the dimension — the same principle `the card gate._derive_output`
already applies to the metric's own output type, where `custom` beats a guessed `decimal`.
The registry does not force the alternative either: its metric validation's list of required
fields does not include `dimensions[].datatype`, and its datatype vocabulary check targets
`definition.output.datatype` only. `unknown` is accepted, unwarned.

A DIMENSION IS NOT ONLY A `GROUP BY`. Five roles evidence one, and the weakest of them is the
grouped role, because it is the only one everybody remembers:

  grouped           GROUP BY d.department
  filtered          WHERE d.region = 'EMEA' — but ONLY when the value VARIES across the
                    family. A predicate pinned to one value is definitional: it is part of
                    what the metric IS, and slicing by it is meaningless inside the metric's
                    own population. A varying one is a knob, and a knob is a dimension. This
                    is also the only role that arrives with its VALUES already known.
  projected         SELECT d.department beside the aggregate, with no GROUP BY
  window_partition  PARTITION BY d.region
  bi_declared       the workbook says `role: dimension` outright — stronger than inference

WHERE THE JOIN GOES. A dimension reached through a join is useless without the join, so every
join in the parent statement is declared and `required_join` points at it. This mirrors the
rule the card gate enforces on `source.sql`: a query carrying one redundant join beats a
query computing a different number, so a join is dropped only when it demonstrably cannot
change the population. The inconsistency being fixed here is that the join survived in the
STATEMENT and was never DECLARED beside it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import sqlshape as S

UNKNOWN = "unknown"

# Observed operators -> the registry's `filter_form`. Free text at the gateway; kept to a
# small closed set here so a consumer can switch on it.
FILTER_FORM = {
    "eq": "equality", "=": "equality", "neq": "equality", "<>": "equality",
    "in": "membership", "like": "pattern", "ilike": "pattern",
    "gt": "range", ">": "range", "gte": "range", ">=": "range",
    "lt": "range", "<": "range", "lte": "range", "<=": "range",
    "between": "range", "is": "nullity",
}


def _slug(text: str) -> str:
    return re.sub(r"\W+", "_", str(text or "").strip().lower()).strip("_") or "dimension"


def _bare(table: str) -> str:
    return str(table or "").split(".")[-1].lower()


def entity_rename(tree, entity: str, metric_alias: str) -> dict[str, str]:
    """{parent alias for the ENTITY -> the metric's own alias}.

    The canonical statement says `FROM <entity> m`, so a join or dimension still bound to the
    parent's `f` references an alias that statement never declares. Only the entity's aliases
    are rewritten: a joined table keeps its own, or the join reads the fact table on both
    sides and computes something else entirely.
    """
    bare = _bare(entity)
    return {a: metric_alias for a, t in S.alias_map(tree).items()
            if _bare(t) == bare and a != metric_alias}


def cardinality_of(bi_rel: list[dict]):
    """Look a join's cardinality up in the BI model's declared relationships.

    A SQL JOIN says nothing about multiplicity — `INNER` is not `many_to_one`. Power BI states
    it outright on the TMDL relationship and `powerbi.py:260` already parses it; this is the
    only place that fact was being thrown away. Matched on the BARE table names because the
    BI model names logical tables and the SQL names physical ones.

    Lives here, beside `declare_joins`, which is its only consumer. It was D3's before the
    card gate replaced that stage.
    """
    index: dict[frozenset, str] = {}
    for r in bi_rel or []:
        a, b = (str(r.get("from_table") or "").split(".")[-1].lower(),
                str(r.get("to_table") or "").split(".")[-1].lower())
        card = str(r.get("cardinality") or "").strip().lower()
        if a and b and card:
            index.setdefault(frozenset({a, b}), card)

    def lookup(parent: dict, join: dict) -> str:
        right = str(join.get("table") or "").split(".")[-1].lower()
        for tbl in parent.get("tables", []) or []:
            hit = index.get(frozenset({right, str(tbl).split(".")[-1].lower()}))
            if hit:
                return hit
        return ""

    return lookup


def observed_by_column(parents: Iterable[dict]) -> dict[str, list[str]]:
    """Values the shape collapse already captured, keyed by BARE column — so a dimension that
    arrives through the GROUPED role still carries what the estate was seen filtering on."""
    out: dict[str, list[str]] = {}
    for o in parents:
        for col, entries in ((o.get("observed_values") or {}).get("by_column") or {}).items():
            bucket = out.setdefault(str(col).split(".")[-1].lower(), [])
            for e in entries:
                v = e.get("value") if isinstance(e, dict) else e
                if v is not None and str(v) not in bucket:
                    bucket.append(str(v))
    return out


def declare_joins(parents: Iterable[dict], dialect: str, cardinality_of=None, *,
                  entity: str = "", metric_alias: str = "m") -> tuple[list[dict], dict[str, str]]:
    """Every join across the family, declared once. -> (joins, {alias: join_name}).

    `cardinality` is the one field a SQL statement cannot answer. Power BI states it outright
    on the TMDL relationship and the parser already captures it (`powerbi.py:260`); when no
    such declaration exists it is admitted as `unknown` rather than inferred from a join type,
    which says nothing about multiplicity.
    """
    joins: dict[str, dict] = {}
    alias_to_name: dict[str, str] = {}
    for parent in parents:
        try:
            tree = S.parse(parent["sql"], dialect)
        except S.ParseError:
            continue
        rename = entity_rename(tree, entity, metric_alias) if entity else {}
        for j in S.join_clauses(tree, dialect, rename):
            name = _slug(_bare(j["table"]))
            existing = joins.get(name)
            if existing and existing["to"] != j["table"]:
                # Two different tables with the same bare name. Disambiguate rather than let
                # one silently win — `name` must be unique within the definition (r2 rule 5).
                name = _slug(j["table"])
            card = UNKNOWN
            if cardinality_of is not None:
                card = cardinality_of(parent, j) or UNKNOWN
            if name not in joins:
                joins[name] = {
                    "name": name,
                    "to": j["table"],
                    "sql": j["sql"],
                    "cardinality": card,
                    "origin": f"verified_query:{parent.get('vq_id', '')}",
                }
            elif joins[name]["cardinality"] == UNKNOWN and card != UNKNOWN:
                joins[name]["cardinality"] = card
            if j["alias"]:
                alias_to_name[j["alias"]] = name
            alias_to_name.setdefault(_bare(j["table"]), name)
    return sorted(joins.values(), key=lambda x: x["name"]), alias_to_name


def _filtered_observations(cand: dict, dialect: str) -> list[dict]:
    """The filtered role, which only the FAMILY can see.

    `parameter_filters` is D1's name for a predicate repeated across verified queries whose
    VALUE varies — exactly a knob. `definitional_filters` is the pinned kind and is
    deliberately absent here: it is population, not a slicer.
    """
    out = []
    for f in cand.get("parameter_filters", []) or []:
        col = str(f.get("column") or "")
        if not col:
            continue
        qualifier, bare = ("", col.lower())
        if "." in col:
            qualifier, bare = col.rsplit(".", 1)[0].lower(), col.rsplit(".", 1)[1].lower()
        out.append({
            "name": bare, "sql": col, "raw_sql": col,
            "bare_column": bare, "qualifier": qualifier,
            "roles": ["filtered"], "is_expression": False,
            "ops": [str(f.get("op") or "").lower()],
            "values": sorted({str(v) for v in (f.get("observed_values") or [])}),
        })
    return out


def _bi_observations(parent: dict, metric_alias: str = "m") -> list[dict]:
    """Fields the workbook DECLARES as dimensions. A Tableau field on the filter shelf is a
    dimension even when it never reaches rows or columns, and the author saying so is better
    evidence than us inferring it from a statement."""
    bi = (parent.get("provenance") or {}).get("bi") or {}
    ctx = bi.get("bi_context") or {}
    out = []
    for f in ctx.get("dimensions", []) or []:
        name = str(f.get("name") or f.get("caption") or "").strip()
        if not name:
            continue
        col = str(f.get("column") or name).lower()
        out.append({
            # No statement ever referenced it, so the only sensible binding is the metric's
            # own alias. A bare column name would be unusable text.
            "name": name.lower(), "sql": str(f.get("sql") or f"{metric_alias}.{col}"),
            "raw_sql": col,
            "bare_column": col,
            # The table the MODEL declared it on. Without it this observation canonicalises
            # against the entity and splits off a duplicate of its own SQL-derived twin.
            "table": f.get("table") or "",
            "qualifier": "", "roles": ["bi_declared"], "is_expression": False,
            "caption": f.get("caption"),
        })
    return out


def _canon(obs: dict, alias_to_bare: dict[str, str], dialect: str, entity: str) -> str:
    """A spelling that is the same for the same column however the author aliased it.

    Two analysts writing `FROM fct_sales f` and `FROM fct_sales fs` describe ONE dimension.
    Merging on the raw text would declare `department` and `department_2` pointing at the same
    column — reintroducing, inside the metric, exactly the duplication the semantic shape key
    exists to collapse. So every qualifier is resolved to its table's bare name first.
    """
    raw = obs.get("raw_sql") or obs["sql"]
    if not obs.get("qualifier"):
        # No qualifier to resolve: a BI-declared field, or a filter recorded bare. Bind it to
        # the table it was declared on so it can still meet its SQL-derived twin.
        table = _bare(obs.get("table") or entity)
        return f"{table}.{raw}".lower() if "." not in raw else raw.lower()
    return re.sub(r"\s+", " ", S.realias(raw, alias_to_bare, dialect).strip().lower())


def _merge_all(groups: list[list[dict]]) -> list[dict]:
    flat: list[dict] = []
    for g in groups:
        flat.extend(g)
    merged = _merge_on(flat, "canon")
    # merge_observations keys on the qualified SQL and keeps roles; the extras (values, ops,
    # captions) are unioned here because they are this module's concern, not sqlshape's.
    extra: dict[str, dict] = {}
    for o in flat:
        key = o["canon"]
        slot = extra.setdefault(key, {"ops": set(), "values": set(), "caption": None})
        slot["ops"].update(o.get("ops", []))
        slot["values"].update(o.get("values", []))
        slot["caption"] = slot["caption"] or o.get("caption")
    for d in merged:
        slot = extra.get(d["canon"], {"ops": set(), "values": set(), "caption": None})
        d["ops"] = sorted(slot["ops"])
        d["values"] = sorted(slot["values"])
        d["caption"] = slot["caption"]
    return merged


def _merge_on(obs: list[dict], key: str) -> list[dict]:
    """Same key => ONE dimension carrying every role that evidenced it."""
    by_key: dict[str, dict] = {}
    for o in obs:
        k = o[key]
        cur = by_key.get(k)
        if cur is None:
            by_key[k] = {**o, "roles": list(dict.fromkeys(o["roles"]))}
            continue
        cur["roles"] = list(dict.fromkeys(cur["roles"] + o["roles"]))
        if o.get("_named") and not cur.get("_named"):
            cur["name"], cur["_named"] = o["name"], True
        # A qualified spelling beats an unqualified one: it is the executable text.
        if o.get("qualifier") and not cur.get("qualifier"):
            cur["sql"], cur["raw_sql"] = o["sql"], o.get("raw_sql") or o["sql"]
            cur["qualifier"] = o["qualifier"]
    for d in by_key.values():
        d.pop("_named", None)
    return sorted(by_key.values(), key=lambda d: (d["name"], d["sql"]))


def build(fam_members: list[dict], parents_by_vq: dict[str, dict], *, entity: str,
          dialect: str, columns=None, alias_to_join: dict[str, str] | None = None,
          observed_by_column: dict[str, list[str]] | None = None,
          max_values: int = 50, metric_alias: str = "m") -> tuple[list[dict], dict[str, Any]]:
    """-> (dimensions, report). One dimension per distinct expression, never dropped."""
    alias_to_join = alias_to_join or {}
    observed_by_column = observed_by_column or {}
    groups: list[list[dict]] = []
    alias_maps: dict[str, dict[str, str]] = {}

    for cand in fam_members:
        parent = parents_by_vq.get(cand["vq_id"])
        if parent is None:
            continue
        try:
            tree = S.parse(parent["sql"], dialect)
        except S.ParseError:
            continue
        alias_maps.update(S.alias_map(tree))
        metric_aliases = {c["alias"] for c in fam_members if c["vq_id"] == cand["vq_id"]}
        rename = entity_rename(tree, entity, metric_alias)
        alias_to_bare = {a: _bare(t) for a, t in S.alias_map(tree).items()}
        batch = (S.dimension_observations(tree, dialect, metric_aliases, rename)
                 + _filtered_observations(cand, dialect)
                 + _bi_observations(parent, metric_alias))
        for o in batch:
            o["canon"] = _canon(o, alias_to_bare, dialect, entity)
        groups.append(batch)

    merged = _merge_all(groups)

    report = {"total": 0, "datatype_resolved": 0, "datatype_unknown": 0,
              "described": 0, "conflicts": [], "by_role": {}}
    used_names: dict[str, int] = {}
    out: list[dict] = []

    for d in merged:
        table = alias_maps.get(d["qualifier"], "") if d["qualifier"] else entity
        table = table or entity
        meta = columns.get(table, d["bare_column"]) if columns else None
        datatype = S.normalise_datatype(meta.get("datatype")) if meta else ""
        description = (meta or {}).get("description")

        # An expression has the type its FUNCTION forces, not the type of the column it
        # reads: DATE_TRUNC over a timestamp is still a date even when the catalogue says the
        # underlying column is a string.
        temporal = S.is_temporal(_safe_parse(d["sql"], dialect), datatype)
        if d["is_expression"] and temporal:
            datatype = datatype if datatype in ("date", "timestamp") else "timestamp"

        name = _unique(_slug(d["name"]), used_names)
        required_join = None
        if d["qualifier"] and d["qualifier"] in alias_to_join:
            if _bare(table) != _bare(entity):
                required_join = alias_to_join[d["qualifier"]]

        values = list(d.get("values") or [])
        if not values:
            values = list(observed_by_column.get(d["bare_column"], []))
        truncated = len(values) > max_values
        if truncated:
            values = values[:max_values]

        forms = sorted({FILTER_FORM.get(op, op) for op in d.get("ops", []) if op})
        entry = {
            "name": name,
            "kind": "time" if temporal else "categorical",
            "sql": d["sql"],
            "datatype": datatype or UNKNOWN,
            "filter_form": forms[0] if len(forms) == 1 else ("mixed" if forms else None),
            "values": values,
            "values_truncated": True if truncated else None,
            "value_map": None,
            "required_join": required_join,
            "description": description,
            # Not part of the registry's Dimension (it is `deny_unknown_fields`); carried out
            # to `extensions` by r1 so the evidence for each dimension stays visible.
            "_roles": d["roles"],
            # The parent's own spelling. D1b records a slice's GROUP BY before the entity —
            # and therefore the rename — is known, so this is what that match runs against.
            "_raw_sql": d.get("raw_sql") or d["sql"],
            "_source_table": table,
            "_bare_column": d["bare_column"],
            "_caption": d.get("caption"),
        }
        report["total"] += 1
        if entry["datatype"] == UNKNOWN:
            report["datatype_unknown"] += 1
        else:
            report["datatype_resolved"] += 1
        if description:
            report["described"] += 1
        for r in d["roles"]:
            report["by_role"][r] = report["by_role"].get(r, 0) + 1
        out.append(entry)

    return sorted(out, key=lambda x: x["name"]), report


def _safe_parse(sql: str, dialect: str):
    try:
        return S.parse(sql, dialect)
    except S.ParseError:
        return None


def _unique(name: str, used: dict[str, int]) -> str:
    """Names must be unique within the definition (r2 rule 5). Deterministic, never random."""
    if name not in used:
        used[name] = 1
        return name
    used[name] += 1
    return f"{name}_{used[name]}"


def reconcile_datatype(from_catalogue: str | None,
                       from_warehouse: str | None) -> tuple[str, str]:
    """-> (datatype, note). The schema plugs' catalogue first, information_schema
    corroborates.

    When the two DISAGREE neither is published: a silent tiebreak in a governed layer is worse
    than an admitted gap, and the conflict is recorded so somebody can go and fix the estate.
    """
    a, b = S.normalise_datatype(from_catalogue), S.normalise_datatype(from_warehouse)
    if a and b and a != b:
        return UNKNOWN, f"conflict: catalogue={a!r} information_schema={b!r}"
    return (a or b or UNKNOWN), ""


def names_for(group_by_sql: Iterable[str], dims: list[dict],
              dialect: str) -> tuple[list[str], list[str]]:
    """(resolved dimension names, unresolved expressions) for one example's GROUP BY.

    Lives here rather than in either caller because D4's reference-integrity GATE and R1's
    EMIT must answer identically — a gate that computes the mapping differently from the
    emitter would pass a payload the gateway then refuses.
    """
    index: dict[str, str] = {}
    for d in dims:
        for spelling in (d.get("_raw_sql"), d.get("sql")):
            if spelling:
                index.setdefault(re.sub(r"\s+", " ", str(spelling).strip().lower()), d["name"])
                index.setdefault(_alias_free(spelling, dialect), d["name"])
    names, unresolved = [], []
    for g in group_by_sql or []:
        key = re.sub(r"\s+", " ", str(g).strip().lower())
        hit = index.get(key) or index.get(_alias_free(g, dialect))
        if hit and hit not in names:
            names.append(hit)
        elif not hit:
            unresolved.append(g)
    return names, unresolved


def _alias_free(expr: str, dialect: str) -> str:
    """The same expression with every column's TABLE QUALIFIER dropped.

    A dimension is the same dimension whichever alias the statement gave its table, and one
    statement routinely uses several: an outer query says `Q.ASSIGNED_AGENT` while its own
    inner subquery says `Q2.ASSIGNED_AGENT`, and `entity_rename` rebinds a third spelling
    (`m.`) onto the entity side. Indexing only the literal spellings made D4's
    reference-integrity gate refuse a GROUP BY over a dimension that WAS declared — measured
    on this estate, 17 metrics blocked on 4 statements, none of them missing a dimension.

    This is the same normal form `faithfulness.normalise` applies to predicates, and for the
    same reason: identity belongs to the column, not to the letter in front of it. Both D4's
    gate and R1's emitter call through here, so they cannot disagree about it.
    """
    text = str(expr or "").strip()
    if not text:
        return ""
    try:
        tree = S.parse(text, dialect)
    except Exception:
        return re.sub(r"\s+", " ", text.lower())
    node = tree.copy()
    for col in node.find_all(S.exp.Column):
        col.set("table", None)
    return re.sub(r"\s+", " ", node.sql().strip().lower())
