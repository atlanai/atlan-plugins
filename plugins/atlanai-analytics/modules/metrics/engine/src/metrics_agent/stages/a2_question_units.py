#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A2 — question units, and the container envelope that makes C3 able to write a real question.

A visual is a fully specified business question: the report author already decided which
question was worth asking. This stage folds each usage entry into a self-contained unit and,
separately, recovers its NEIGHBOURHOOD.

THE FACTS CONTRACT IS LOSSY ON PURPOSE. A1 distils the extract into what A3 needs to write
correct SQL: columns, aggregates, filters, joins, grain. What it discards is precisely the
human-authored layer — axis titles, tooltip templates, number formats, conditional-formatting
thresholds, dashboard text boxes and section headers. Worthless to A3, and MOST OF THE
BUSINESS CONTEXT for C3.

So A2 preserves the VERBATIM declarative source of the widget and of its dashboard alongside
the structured facts. Two consumers, two diets, one extract. Fragments are written to the run
directory and referenced BY PATH — question_units.json stays readable and diffable.

CONTAINER FILTERS ARE A CORRECTNESS PROBLEM, NOT A PHRASING ONE. A dashboard-level quick
filter, a Tableau filter action or a Power BI report-level filter changes the POPULATION of
every chart it reaches. Recover it here or the SQL A3 writes is wrong in a way no gate
downstream will catch — it will parse, execute and return a plausible number.

Two deterministic outcomes, both RECORDED rather than silent, and never both for one widget:

* a unit with zero measures is not a question. It is NOT emitted; it is listed in `dropped[]`
  with its reason. `dropped[]` holds only what is truly not emitted.
* a unit whose measures have no warehouse lineage IS emitted, with `status: unresolved` and an
  `unresolved_reason` naming the measures no partition resolves — the operator's signal that
  the extract is missing a partition, not that the estate lacks the data. A3 never compiles
  an unresolved unit, and it is not also listed in `dropped[]`.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys


from metrics_agent.lib import artifacts
from metrics_agent.lib import bi_tags as _bi_tags  # noqa: E402
from metrics_agent.lib import bi_models as _models  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "a2_question_units"
OUT = "question_units.json"
FRAGDIR = "fragments"


def _resolve_edges(relationships: list[dict], wh: list[str],
                   logical_to_physical: dict[str, str] | None = None,
                   homes: list[str] | None = None) -> list[dict]:
    """Relationship edges rewritten onto the warehouse tables this unit actually reads.

    A relationship names the model's LOGICAL table. Where the model renamed that table to a
    physical object with a different leaf (`FACT_ORDERS` reading `…DAILY_ORDER_SUMMARY_VW`),
    the bare-leaf match finds nothing, so the model's own logical -> physical map is tried
    first.

    Kept: every edge that touches a table of the unit, and every edge on a SHORTEST path from
    a measure-home table (`homes`) to another table of the unit. A column two hops away
    (sales -> product -> category) is reached through a table the unit itself never names,
    and without the middle edge no path exists. Every shortest path is kept, not the first,
    so a consumer can see when two of them make the path ambiguous. An end the unit does not
    read is rewritten to its warehouse table only on such a path."""
    physical = {t.split(".")[-1].lower(): t for t in wh}
    in_wh = set(wh)
    l2p = logical_to_physical or {}

    def _side(name: str) -> str | None:
        mapped = l2p.get(name.lower())
        if mapped:
            return mapped
        return physical.get(name.split(".")[-1].lower())

    resolved = []
    for r in relationships:
        f = _side(str(r.get("from_table") or "")) or r.get("from_table")
        t = _side(str(r.get("to_table") or "")) or r.get("to_table")
        resolved.append({**r, "from_table": f, "to_table": t})

    adj: dict[str, set[str]] = {}
    for r in resolved:
        adj.setdefault(r["from_table"], set()).add(r["to_table"])
        adj.setdefault(r["to_table"], set()).add(r["from_table"])

    def _dist(start: str) -> dict[str, int]:
        seen, frontier = {start: 0}, [start]
        while frontier:
            nxt = []
            for cur in frontier:
                for n in sorted(adj.get(cur, ())):
                    if n not in seen:
                        seen[n] = seen[cur] + 1
                        nxt.append(n)
            frontier = nxt
        return seen

    on_path: set[int] = set()
    for h in homes or []:
        dh = _dist(h)
        for target in wh:
            if target == h or target not in dh:
                continue
            dt = _dist(target)
            for i, r in enumerate(resolved):
                a, b = r["from_table"], r["to_table"]
                for x, y in ((a, b), (b, a)):
                    if x in dh and y in dt and dh[x] + 1 + dt[y] == dh[target]:
                        on_path.add(i)

    out = []
    for i, (orig, r) in enumerate(zip(relationships, resolved)):
        if i in on_path:
            out.append(r)           # a hop the statement joins through: physical both ends
        elif r["from_table"] in in_wh or r["to_table"] in in_wh:
            # An end the unit does not read keeps the model's name: rewriting it would claim
            # a table the statement never reads.
            out.append({**r, **{k: orig[k] for k in ("from_table", "to_table")
                                if r[k] not in in_wh}})
    return out


def _tuple_named(f: dict, source_names, wh_table) -> dict:
    """A `tuple` filter with each of its columns named as the warehouse reads it."""
    v = f.get("value")
    if f.get("op") != "tuple" or not isinstance(v, dict):
        return f
    cols = [_with_table(dict(c, column=source_names([c["column"]])[0]), wh_table(c.get("table")))
            for c in v.get("columns") or []]
    return {**f, "value": {**v, "columns": cols}}


def _with_table(f: dict, table: str | None) -> dict:
    """A filter entry with `table` rewritten to the warehouse table, or dropped when the
    model's table resolves to none."""
    f = {k: v for k, v in f.items() if k != "table"}
    return {**f, "table": table} if table else f


def _to_source_columns(names: list[str], wh: list[str],
                       source_of: dict[str, dict[str, str]]) -> list[str]:
    """Model column names -> the warehouse columns they read, where the model renamed them.

    A visual names a column by its MODEL name (`Cal-Month`); Power Query may have renamed it
    from the warehouse's (`CAL_MONTH_KEY`), and the compiler can only emit the latter. Only
    an unambiguous rename is applied: when the unit's tables disagree about what a name reads,
    the model name is kept and the compiler refuses it visibly rather than guessing."""
    out = []
    for n in names:
        hits = {source_of.get(t, {}).get(str(n).lower()) for t in wh} - {None}
        out.append(hits.pop() if len(hits) == 1 else n)
    return out


def _unit_source_columns(names: list[str], native: dict | None, wh: list[str],
                         source_of: dict[str, dict[str, str]]) -> list[str]:
    """The columns a unit groups and filters by, as the statement A3 builds can name them.

    A native-query unit reads its OWN query, so a renamed column is that query's output column,
    from the reader's map for that logical table. The physical name cannot say which query:
    several logical tables read one physical object, and their renames disagree (`Unique Users`
    is `total_unique_users` in one query and `unique_users` in another). Any other unit takes
    the warehouse rename its tables agree on (`_to_source_columns`)."""
    if native:
        own = {str(k).lower(): v for k, v in (native.get("columns") or {}).items()}
        return [own.get(str(n).lower(), n) for n in names]
    return _to_source_columns(names, wh, source_of)


def _catalog_index(ri: dict) -> dict[tuple[str, str], dict]:
    """(role, name) -> the catalogue identity a BI plug resolved its assets to (`bi_assets`).

    Joined BY NAME AND ROLE, and only where exactly one resolved asset of that role carries
    the name: an ambiguous name gets no id rather than a wrong one. The role is the neutral
    one a reader gives an asset (`workbook`, `report`, …); the plug that resolved the assets
    put it on each row, so this stage knows no catalogue's type names.
    """
    resolved = [a for s in ((ri.get("bi") or {}).get("sources") or [])
                for a in (s.get("assets") or [])]
    hits: dict[tuple[str, str], list[dict]] = {}
    for a in resolved:
        role = a.get("role")
        nm = str(a.get("name") or "").strip().lower()
        if not nm or not role:
            continue
        hits.setdefault((role, nm), []).append(a)
    return {k: v[0] for k, v in hits.items() if len(v) == 1}


def _with_catalog(assets: list[dict], index: dict) -> list[dict]:
    out = []
    for a in assets:
        hit = index.get((a["role"], str(a.get("name") or "").strip().lower()))
        out.append({**a, "catalog_id": (hit or {}).get("id") or a.get("catalog_id"),
                    "catalog_qualified_name": ((hit or {}).get("qualified_name")
                                               or a.get("catalog_qualified_name"))})
    return out


def body(ctx: stage.Ctx) -> None:
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    facts = ctx.read("bi_facts.json", "bi_facts.schema.json")
    catalog_ix = _catalog_index(ri)
    side = {}
    spath = ctx.run / "a1_sidecar.json"
    if spath.is_file():
        side = artifacts.read(ctx.run, "a1_sidecar.json", "a1_sidecar.schema.json")["per_unit"]
        ctx.entry.inputs.append(str(spath))

    fragdir = ctx.run / FRAGDIR
    fragdir.mkdir(parents=True, exist_ok=True)

    # Each BI model on its own (a run may read several, one per BI plug): its names are
    # looked up only within it, and its units, containers and domain already carry its
    # plug (lib/bi_models). One model is one partition, read exactly as before.
    frag_names: dict[str, str] = {}
    containers_written: set[str] = set()

    def _units_of(facts: dict) -> tuple[list[dict], list[dict]]:
        model_assets = _with_catalog(facts.get("assets") or [], catalog_ix)
        # table name -> its resolved warehouse target and columns
        lineage: dict[str, dict] = {t["name"]: t.get("source_lineage") or {} for t in facts["tables"]}
        measures = {m["name"].lower(): m for m in facts["measures"]}
        cols_of: dict[str, list[str]] = {
            t["name"]: [c["source_column"] or c["name"] for c in t["columns"]]
            for t in facts["tables"]}
        # table -> {model column name: the warehouse column it reads}, renamed columns only.
        source_of: dict[str, dict[str, str]] = {
            t["name"]: {str(c["name"]).lower(): c["source_column"] for c in t["columns"]
                        if c.get("source_column") and not c.get("is_calculated")
                        and str(c["name"]).lower() != str(c["source_column"]).lower()}
            for t in facts["tables"]}
        # A measure's declared home table, resolved to the warehouse name the table now carries
        # (TMDL names the logical table; the partition renames it to the physical target).
        logical_to_physical = {}
        for t in facts["tables"]:
            obj = (t.get("source_lineage") or {}).get("object")
            if obj:
                logical_to_physical[obj.split(".")[-1].lower()] = t["name"]
            logical_to_physical[t["name"].split(".")[-1].lower()] = t["name"]
            # THE ACTUAL LOGICAL NAME. Both keys above are leaves of the PHYSICAL name — by the
            # time this runs the partition has already rewritten `name`, so neither can ever be
            # the TMDL name a Power BI measure declares as its home. On a real model that left
            # every `home_table` unresolved, so all 67 units had no warehouse lineage.
            # The synthetic fixture missed it because its measures share names with their
            # columns, so the column branch resolved them regardless.
            if t.get("logical_name"):
                logical_to_physical.setdefault(t["logical_name"].lower(), t["name"])
        # NATIVE-QUERY TABLES, by LOGICAL name. Several logical tables can read the same
        # physical object through different native queries, so the physical name cannot tell
        # them apart; the logical name (a measure's declared home) can. For each, the query A3
        # compiles against and the model-column -> query-output-column map. Both are the
        # READER's: it extracted the statement from whatever wrapped it and named each
        # column's output spelling (`source_lineage.native_sql`, `.native_columns`), so this
        # stage reads no vendor syntax.
        native_by_logical: dict[str, dict] = {}
        for t in facts["tables"]:
            lin = t.get("source_lineage") or {}
            sql = lin.get("native_sql")
            if not sql or not t.get("logical_name"):
                continue
            native_by_logical[t["logical_name"].lower()] = {
                "logical": t["logical_name"], "sql": sql,
                "columns": {p["name"]: p["column"] for p in lin.get("native_columns") or []}}
        logical_home_of = {m["name"].lower(): str(m.get("home_table") or "").lower()
                           for m in facts["measures"]}
        # Tables whose source the READER refused, by every name they go by: the reason is the
        # reader's, by name (a workbook, a file, a step with no faithful SQL), and a unit that
        # reads such a table says it instead of a generic "no lineage".
        refused_table: dict[str, str] = {}
        for t in facts["tables"]:
            why = (t.get("source_lineage") or {}).get("refusal")
            if why:
                for nm in {t["name"], t.get("logical_name") or ""} - {""}:
                    refused_table[str(nm).lower()] = why

        # A measure's declared home by its logical name, or by the warehouse table itself (a
        # measure the reader builds over a field, such as a quick table calculation, which is
        # no column of any table).
        by_physical = {t["name"].lower(): t["name"] for t in facts["tables"]}
        home_table_of = {m["name"].lower():
                         logical_to_physical.get(str(m.get("home_table", "")).lower())
                         or by_physical.get(str(m.get("home_table") or "").lower())
                         for m in facts["measures"]}

        # Columns the MODEL ITSELF declares as dimensions. A Tableau field with role="dimension"
        # is a dimension whether or not it ever reaches rows, columns or a GROUP BY — the author
        # saying so outranks inferring it from a statement, and it is the only place a
        # filter-shelf-only field is visible at all.
        _dim_decl: dict[str, dict] = {}
        for t in facts["tables"]:
            for c in t["columns"]:
                if c.get("role") != "dimension":
                    continue
                for key in {c["name"].lower(), (c.get("source_column") or "").lower()} - {""}:
                    _dim_decl.setdefault(key, {
                        "name": (c.get("source_column") or c["name"]).lower(),
                        "column": (c.get("source_column") or c["name"]).lower(),
                        "caption": c.get("caption"),
                        "table": t["name"],
                    })

        # The author's own label per column, so a measure carries the words a person searches
        # for alongside the identifier the SQL uses.
        _caption_of = {c["name"].lower(): c.get("caption")
                       for t in facts["tables"] for c in t["columns"]}
        _caption_of.update({(c.get("source_column") or "").lower(): c.get("caption")
                            for t in facts["tables"] for c in t["columns"]
                            if c.get("source_column")})

        # A table whose statement IS its source (`statement_is_source`) names no physical
        # object, so it is a warehouse target exactly when that statement was recovered.
        logical_of = {t["name"]: (t.get("logical_name") or "").lower() for t in facts["tables"]
                      if (t.get("source_lineage") or {}).get("statement_is_source")}
        all_tables = [t["name"] for t in facts["tables"]
                      if (t.get("source_lineage") or {}).get("object")
                      or t["name"].count(".") >= 2
                      or ((t.get("source_lineage") or {}).get("statement_is_source")
                          and (t.get("logical_name") or "").lower() in native_by_logical)]

        # Siblings per container, for the envelope.
        by_container: dict[str, list[dict]] = {}
        for u in facts["usage"]:
            by_container.setdefault(u["container"], []).append(u)
        # BOUNDED: every widget lists the others on its dashboard, so uncapped the context is
        # quadratic in widgets per dashboard. The cap is policy; when it bites the nearest
        # siblings are kept and the count cut is recorded.
        max_sibs = ctx.policy.get("bi.max_sibling_widgets")
        if isinstance(max_sibs, bool) or not isinstance(max_sibs, int) or max_sibs < 0:
            raise SystemExit(f"a2: REFUSED — policy bi.max_sibling_widgets is {max_sibs!r}; it "
                             "must be a whole number of widgets, 0 or more.")

        units: list[dict] = []
        dropped: list[dict] = []
        domain = facts.get("model_name") or "bi"

        max_var = ctx.policy.get("bi.max_variants_per_widget")
        if isinstance(max_var, bool) or not isinstance(max_var, int) or max_var < 0:
            raise SystemExit(f"a2: REFUSED — policy bi.max_variants_per_widget is {max_var!r}; "
                             "it must be a whole number of units, 0 or more.")
        n_variants: dict[str, int] = {}
        fixed_tables = {str(n).lower() for t in facts["tables"] if t.get("fixed_rows")
                        for n in (t.get("name"), t.get("logical_name")) if n}
        for u in facts["usage"]:
            uid = u["unit_id"]
            aggs = (side.get(uid) or {}).get("aggregations", {})
            if u.get("variant_of"):
                # A widget answered another way it can show: bounded per widget (policy); a
                # variant over the cap is named, never silently lost.
                n_variants[u["variant_of"]] = n_variants.get(u["variant_of"], 0) + 1
                if n_variants[u["variant_of"]] > max_var:
                    dropped.append({"unit_id": uid, "reason": "variant_cap",
                                    "detail": f"more than bi.max_variants_per_widget "
                                              f"({max_var}) variants of {u['variant_of']}"})
                    continue

            if not u["measures"] and not u.get("visual_matrix") and not (
                    u.get("distinct_rows") and u.get("columns")):
                dropped.append({"unit_id": uid, "reason": "no_measures",
                                "detail": "text box, image or navigation — not a question"})
                continue

            # Fragments are kept for HUMAN REVIEW only — they are how you check what the
            # parser actually saw. C3 no longer reads them: it gets the bounded `bi_context`
            # block below, which is cheaper, identical across vendors, reviewable and testable.
            wfrag = fragdir / f"{_fragment_name(uid, frag_names)}.widget.json"
            widget = {"unit_id": uid, "title": u["title"], "visual_type": u["visual_type"],
                      "measures": u["measures"], "columns": u["columns"],
                      "filters": u["filters"], "top_n": u["top_n"], "sort": u["sort"],
                      "parameters": u["parameters"],
                      "note": "Verbatim widget-level source. PROSE: it may ground a naming claim "
                              "at C3 and can never authorise a filter."}
            artifacts.validate(widget, "widget_fragment.schema.json", what=f"{FRAGDIR}/{wfrag.name}")
            wfrag.write_text(json.dumps(widget, indent=2) + "\n")
            dfrag = fragdir / f"{_fragment_name(u['container'], frag_names)}.dashboard.json"
            # Written once per container per RUN: a file left by an earlier run is replaced, not
            # trusted.
            if u["container"] not in containers_written:
                containers_written.add(u["container"])
                sibs = by_container.get(u["container"], [])
                kept, cut = first_widgets(sibs, u["container"], max_sibs)
                dash = {"container": u["container"], "kind": u["container_kind"],
                        "siblings": [{"title": s["title"], "measures": s["measures"]}
                                     for s in kept],
                        **({"siblings_omitted": cut} if cut else {}),
                        "prose": sorted({p for s in sibs
                                         for p in (s.get("source_fragments") or {}).get("prose",
                                                                                        [])}),
                        "note": "Verbatim dashboard/page-level source, including author-written "
                                "text."}
                artifacts.validate(dash, "dashboard_fragment.schema.json",
                                   what=f"{FRAGDIR}/{dfrag.name}")
                dfrag.write_text(json.dumps(dash, indent=2) + "\n")

            # Resolve warehouse targets. The MEASURE columns decide the primary table: a unit
            # aggregating units_received belongs to the fact table, and picking alphabetically
            # put it on a dimension table — wrong in a way that only shows up at execution.
            # A Power BI measure is a NAMED object declared on a table, not a raw column, so
            # matching only on column names resolves nothing and drops every unit. Tableau pills
            # name columns directly. Both are handled: declared home_table first, columns second.
            declared_homes = {home_table_of.get(m.lower()) for m in u["measures"]}
            declared_homes.discard(None)

            measure_home: list[str] = []
            other: list[str] = []
            names_of: dict[str, set[str]] = {}
            for t in all_tables:
                # Warehouse names AND the model names a renamed column is shown by.
                names = {c.lower() for c in cols_of.get(t, [])} | set(source_of.get(t, {}))
                names_of[t] = names
                if t in declared_homes or any(m.lower() in names for m in u["measures"]):
                    measure_home.append(t)
                elif any(c.lower() in names for c in u["columns"]):
                    other.append(t)
            # The table the BI file says a column or a filter is on, as a warehouse table.
            def _wh_table(name: str | None) -> str | None:
                if not name:
                    return None
                # Only a table the statement can read: a model table with no warehouse
                # object (one the model computes) is no join target.
                physical = logical_to_physical.get(str(name).lower())
                if physical in names_of:
                    return physical
                return name if name in names_of else None
            col_tables = [_wh_table(x) for x in (u.get("column_tables") or [])]
            # A FILTER on a column of another table reads that table too: a page filter on a
            # dimension's column is joined to the dimension. Only where the measure's own
            # table does not hold the column, or the file says it is on another table.
            for f in u["filters"]:
                # Each column a tuple filter tests together reads its own table.
                for c in ((f.get("value") or {}).get("columns") or []
                          if f.get("op") == "tuple" and isinstance(f.get("value"), dict)
                          else []):
                    t = _wh_table(c.get("table"))
                    if t and t not in measure_home and t not in other:
                        other.append(t)
                col = str(f.get("column") or "").lower()
                hinted = _wh_table(f.get("table"))
                if hinted:
                    cands = [hinted]
                elif not col or any(col in names_of.get(h, set()) for h in measure_home):
                    continue
                else:
                    cands = [t for t in all_tables if col in names_of.get(t, set())]
                for t in cands:
                    if t not in measure_home and t not in other:
                        other.append(t)
            for t in col_tables:
                if t and t not in measure_home and t not in other:
                    other.append(t)
            if not measure_home and u.get("distinct_rows"):
                # A table of columns only: its rows are the combinations that exist in the
                # table every column's table is reached from along many-to-one edges.
                named = [t for t in col_tables if t] if all(col_tables) and col_tables \
                    else list(other)
                home = _distinct_home(list(dict.fromkeys(named)), facts["relationships"],
                                      logical_to_physical, all_tables)
                if home:
                    # One table's rows also list its BLANK member when a relationship into
                    # it has a key it lacks: the compiler adds it from the model's
                    # relationships (any table on a many side, active or not, or one whose
                    # own blank member reaches it).
                    measure_home = [home]
                    other = [t for t in other if t != home]
            wh = measure_home + other
            status = "resolved" if measure_home else "unresolved"
            # No table claims this unit's measures. The unit is still EMITTED, `unresolved`, with
            # its measures named so the operator can see WHICH partition the extract is missing —
            # never silently dropped, never also listed in `dropped[]` (which holds only what is
            # not emitted), and never quietly attached to whichever table sorted first.
            unresolved_reason = (None if measure_home else
                                 f"no warehouse lineage: no partition resolves measures "
                                 f"{u['measures']}; the extract is missing this datasource "
                                 f"(often a PUBLISHED one, whose connection lives in a "
                                 f"separate .tds)")
            # A table the unit reads whose source the reader refused: its measures' home, or
            # a table a column or filter is on. Named, never compiled against something else.
            hinted = [str(m.get("home_table") or "") for m in facts["measures"]
                      if m["name"].lower() in {x.lower() for x in u["measures"]}]
            hinted += [str(x or "") for x in (u.get("column_tables") or [])]
            hinted += [str(f.get("table") or "") for f in u["filters"]]
            why = next((refused_table[h.lower()] for h in hinted if h.lower() in refused_table),
                       None)
            if why:
                status, unresolved_reason = "unresolved", why

            sibs, sibs_cut = nearest_siblings(u, by_container.get(u["container"], []), max_sibs)
            siblings = [{"title": s["title"], "measures": s["measures"]} for s in sibs]
            omitted = {"siblings_omitted": sibs_cut} if sibs_cut else {}

            # One native-query home for every measure -> A3 reads that query's result set.
            native = _native_for(u["measures"], logical_home_of, native_by_logical, wh,
                                 logical_of)

            def _source_names(names: list[str]) -> list[str]:
                return _unit_source_columns(names, native, wh, source_of)

            scoped = [f for f in u["filters"] if f["scope"] in
                      ("page", "report", "context", "dashboard-action", "quick-filter",
                       "datasource", "slicer")]

            units.append({
                "unit_id": uid,
                "title": u["title"],
                "measures": [{"name": m,
                              "formula": (measures.get(m.lower()) or {}).get("formula"),
                              "formula_language": (measures.get(m.lower()) or {}).get(
                                  "formula_language"),
                              "aggregation": aggs.get(m),
                              # A measure computed over the view's aggregated rows: the
                              # columns it runs along and restarts on, named as group_by is.
                              **({"table_calc": _table_calc_of(
                                  (u.get("table_calcs") or {})[m], _source_names)}
                                 if m in (u.get("table_calcs") or {}) else {}),
                              # A SQL measure is placed over its home ROLE (A3), which the
                              # physical table alone cannot name when two roles share it.
                              **({"home_table": (measures.get(m.lower()) or {}).get(
                                  "home_table")}
                                 if (measures.get(m.lower()) or {}).get("formula_language")
                                 == "sql" else {}),
                              # A measure the view computes for its own calculations and
                              # does not show.
                              **({"hidden": True} if m in ((u.get("visual_matrix") or {})
                                                           .get("hidden") or []) else {})}
                             for m in u["measures"]] + _visual_calc_measures(u),
                **_declared_of(u["measures"], measures, domain),
                "group_by": _source_names(u["columns"]),
                **({"group_by_tables": col_tables}
                   if any(col_tables) and len(col_tables) == len(u["columns"]) else {}),
                "filters": [_tuple_named(
                    _with_table(dict(f, column=_source_names([f["column"]])[0]),
                                _wh_table(f.get("table")))
                    if f.get("column") else f, _source_names, _wh_table)
                    for f in u["filters"]],
                # The values the view takes when it is opened, and which widget a variant
                # unit answers another way.
                **({"parameters": [_with_table(dict(x, column=_source_names([x["column"]])[0]),
                                               _wh_table(x.get("table")))
                                   for x in u["parameters"] if x.get("column")]}
                   if any(x.get("column") for x in u.get("parameters") or []) else {}),
                **({"variant_of": u["variant_of"], "variant": u.get("variant") or "variant"}
                   if u.get("variant_of") else {}),
                "sort": u["sort"],
                "top_n": u["top_n"],
                # Whether the view hides the rows whose every measure is empty (or lists
                # members with no rows): the compiler's to apply, from the reader's word.
                **({"blank_rows": u["blank_rows"]} if u.get("blank_rows") else {}),
                # The total rows the view shows beside its leaf rows, named as group_by is.
                **({"totals": [{"kind": t["kind"],
                                "group_by": _source_names(list(t.get("group_by") or []))}
                               for t in u["totals"]]} if u.get("totals") else {}),
                **({"distinct_rows": True} if u.get("distinct_rows") else {}),
                # The view's own calculations and the matrix they run over, its columns named
                # as group_by is.
                **({"visual_matrix": _visual_matrix_of(u["visual_matrix"], _source_names)}
                   if u.get("visual_matrix") else {}),
                # Grouped by a column of a table of fixed rows (a parameter): one statement
                # per value, as many as a widget's variants may be (policy).
                **({"max_values_grouped": max_var}
                   if any(str(t or "").lower() in fixed_tables
                          for t in u.get("column_tables") or []) else {}),
                # Order is MEANINGFUL here — measure-home table first — so it is deduped
                # without sorting. Sorting would undo the resolution two lines above and put a
                # dimension table back in front of the fact table.
                "warehouse_tables": list(dict.fromkeys(wh)),
                # A relationship names the model's LOGICAL tables (`FCT_SEASON_PERFORMANCE`);
                # `wh` holds the PHYSICAL ones (`retail.merch.fct_season_performance`). Matching
                # them with `in` compared two different naming systems and quietly produced an
                # empty graph — which downstream reads as "no relationship exists", so every
                # cross-table grouping became unreachable. Matched on the bare name, and REWRITTEN
                # to the physical names so no consumer has to repeat this translation.
                "join_graph": _resolve_edges(facts["relationships"], wh,
                                             logical_to_physical, measure_home),
                "container": {
                    "path": [facts.get("model_name") or "", u["container"], str(u["visual_id"])],
                    "dashboards": u["dashboards"],
                    "siblings": siblings,
                    **omitted,
                    "n_dashboards": len(u["dashboards"]),
                    "assets": model_assets + _with_catalog(u.get("assets") or [], catalog_ix),
                    "scoped_filters": scoped,
                    "source_fragments": {
                        "widget": str(wfrag.relative_to(ctx.run)),
                        "dashboard": str(dfrag.relative_to(ctx.run)),
                        "prose": (u.get("source_fragments") or {}).get("prose", []),
                    },
                },
                # The bounded metadata block C3 reads INSTEAD of the files. Every field here
                # settles something the SQL cannot: the number format is the UNIT, a reference
                # line is the TARGET, the annotations and siblings are the audience and the
                # decision. A1's facts contract discards all of it by design, because A3 does not
                # need it — and it is most of what C3 does need.
                "bi_context": {
                    "workbook": facts.get("model_name") or None,
                    "dashboard": (u["dashboards"][0] if u["dashboards"] else None),
                    "also_on_dashboards": [d["name"] for d in u["dashboards"][1:]],
                    "n_dashboards": len(u["dashboards"]),
                    "widget": {
                        "title": u.get("title") or None,
                        "subtitle": u.get("subtitle"),
                        "visual_type": u.get("visual_type"),
                        "description": u.get("description"),
                        "number_format": u.get("number_format"),
                        "tooltip": u.get("tooltip"),
                        "sort": u.get("sort"),
                        "top_n": u.get("top_n"),
                        "axis_titles": u.get("axis_titles") or {},
                        "reference_lines": u.get("reference_lines") or [],
                        "conditional_formatting": u.get("conditional_formatting") or [],
                    },
                    # Each measure as the model defines it: its style, its formula, and the
                    # model's own metadata (description, folder, annotations, perspectives,
                    # endorsement, KPI) as context. Never read by a compiler.
                    "measures": [
                        _bi_tags.measure_context(
                            m, measures.get(m.lower()), caption=_caption_of.get(m.lower()),
                            aggregation=aggs.get(m), assets=model_assets,
                            max_chars=ctx.policy.get("bi.max_context_chars"),
                            items=_bi_tags.calculation_items(u, facts["tables"]))
                        for m in u["measures"]],
                    # The NEAREST `bi.max_sibling_widgets` of them; `siblings_omitted` says how
                    # many more the dashboard holds, so the author knows the list is cut.
                    "siblings": [dict(s) for s in siblings],
                    **omitted,
                    # Declared by the model, not inferred from the SQL. Carried so D3 can
                    # declare a dimension the statement alone would never reveal.
                    # `columns` holds the rows/cols shelves and the encodings ONLY. A field that
                    # reaches nothing but the FILTER shelf is absent from it — and that is exactly
                    # the dimension no statement will ever reveal either, so the filter columns
                    # are read here too or the case this block exists for is the one it misses.
                    "dimensions": [
                        _dim_decl[k] for k in dict.fromkeys(
                            c.lower() for c in (
                                list(u["columns"])
                                + [f.get("column") or "" for f in (u.get("filters") or [])])
                            if c and c.lower() in _dim_decl)],
                    "annotations": (u.get("source_fragments") or {}).get("prose", []),
                    "scoped_filters": [
                        {"scope": f["scope"], "column": f["column"], "op": f.get("op"),
                         "value": f.get("value")} for f in scoped],
                },
                # The language the widget's calculated fields are written in, when the reader
                # says its columns, filters and sort may name one (compiled from its formula).
                **({"expression_language": u["expression_language"]}
                   if u.get("expression_language") else {}),
                "native_source": native,
                # Deterministic: the workbook/report name, verbatim. No clustering, no LLM.
                "domain": domain,
                "status": status,
                "unresolved_reason": unresolved_reason,
            })
        return units, dropped

    units, dropped = [], []
    for _model, part in _models.partitions(facts):
        got, cut = _units_of(part)
        units += got
        dropped += cut

    ctx.write(OUT, {"contract_version": "1.0", "vendor": facts["vendor"],
                    **({"vendors": facts["vendors"]} if facts.get("vendors") else {}),
                    "units": units, "dropped": dropped}, "question_units.schema.json")
    n_never = sum(1 for u in units if u["container"]["n_dashboards"] == 0)
    import json as _json
    ctx_bytes = sum(len(_json.dumps(u["bi_context"])) for u in units)
    ctx.count(units=len(units), dropped=len(dropped), fragments=len(list(fragdir.iterdir())),
              never_surfaced=n_never, bi_context_bytes=ctx_bytes)
    n_unresolved = sum(1 for u in units if u["status"] == "unresolved")
    ctx.say(f"a2: {len(units)} question units ({n_unresolved} unresolved: no warehouse "
            f"lineage), {len(dropped)} not emitted, each with a named reason. "
            f"{n_never} appear on no dashboard (authored but never surfaced — a gotcha, "
            f"not an omission).")
    ctx.say(f"a2: bi_context is {ctx_bytes} bytes across {len(units)} unit(s) "
            f"(~{ctx_bytes // max(len(units), 1)} each) — what C3 reads instead of the files. "
            f"The fragments in {FRAGDIR}/ are kept for human review only.")


def _declared_of(names: list[str], measures: dict, model: str) -> dict:
    """{"declared": ...} for a unit of ONE metric its model declares (bi_facts
    `measures[].declared`), with the model and the metric that identify it; else {}. It travels
    with the statement A3 compiles from the unit, to X1-X3."""
    if len(names) != 1:
        return {}
    m = measures.get(str(names[0]).lower()) or {}
    if not m.get("declared"):
        return {}
    return {"declared": {"model": model, "metric": m["name"],
                         "description": m.get("description") or None,
                         "synonyms": list(m.get("synonyms") or []), **m["declared"]}}


def _position(u: dict, container: str):
    """The widget's position on `container` as the parser recorded it, or None."""
    for d in u.get("dashboards") or []:
        pos = d.get("position")
        if d.get("name") == container and isinstance(pos, int) and not isinstance(pos, bool):
            return pos
    return None


def _ranks(widgets: list[dict], container: str) -> list[int]:
    """Each widget's rank in dashboard order: by position when every widget has one,
    otherwise the order the parser gave them. Deterministic either way."""
    pos = [_position(w, container) for w in widgets]
    if all(p is not None for p in pos):
        order = sorted(range(len(widgets)), key=lambda i: (pos[i], i))
    else:
        order = list(range(len(widgets)))
    rank = [0] * len(widgets)
    for r, i in enumerate(order):
        rank[i] = r
    return rank


def first_widgets(widgets: list[dict], container: str, cap: int) -> tuple[list[dict], int]:
    """At most `cap` widgets: the first in dashboard order, listed in the parser's order.
    -> (kept, cut)."""
    if len(widgets) <= cap:
        return list(widgets), 0
    rank = _ranks(widgets, container)
    return [w for i, w in enumerate(widgets) if rank[i] < cap], len(widgets) - cap


def nearest_siblings(unit: dict, widgets: list[dict], cap: int) -> tuple[list[dict], int]:
    """The other widgets on `unit`'s dashboard, at most `cap` of them: the NEAREST to it in
    dashboard order (a tie goes to the earlier one), listed in the parser's order.
    -> (kept, cut)."""
    others = [i for i, w in enumerate(widgets) if w["unit_id"] != unit["unit_id"]]
    if len(others) <= cap:
        return [widgets[i] for i in others], 0
    rank = _ranks(widgets, unit["container"])
    me = next((rank[i] for i, w in enumerate(widgets) if w["unit_id"] == unit["unit_id"]), 0)
    keep = set(sorted(others, key=lambda i: (abs(rank[i] - me), rank[i]))[:cap])
    return [widgets[i] for i in others if i in keep], len(others) - cap


def _distinct_home(col_tables: list[str], relationships: list[dict],
                   logical_to_physical: dict[str, str], all_tables) -> str | None:
    """The table a view of columns only lists the existing combinations of: one of its
    columns' tables (else any table) from which every one of them is reached along active
    many-to-one relationships. None when no table reaches them all."""
    if not col_tables:
        return None
    if len(col_tables) == 1:
        return col_tables[0]
    by_bare = {str(t).split(".")[-1].lower(): t for t in all_tables}

    def phys(name: str) -> str | None:
        return (logical_to_physical.get(str(name).lower())
                or by_bare.get(str(name).split(".")[-1].lower()))
    up: dict[str, set[str]] = {}
    for r in relationships or []:
        if r.get("is_active") is False or r.get("kind") not in (None, "relationship",
                                                                "physical"):
            continue
        a, b = phys(r.get("from_table") or ""), phys(r.get("to_table") or "")
        card = str(r.get("cardinality") or "").upper()
        if not a or not b:
            continue
        if card in ("M:1", "1:1"):
            up.setdefault(a, set()).add(b)
        if card in ("1:M", "1:1"):
            up.setdefault(b, set()).add(a)

    def reach(start: str) -> set[str]:
        seen, todo = {start}, [start]
        while todo:
            for nxt in up.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen
    for cand in list(col_tables) + [t for t in all_tables if t not in col_tables]:
        if set(col_tables) <= reach(cand):
            return cand
    return None


def _native_for(measures: list[str], logical_home_of: dict, native_by_logical: dict,
                tables: list[str] = (), logical_of: dict | None = None):
    homes = {logical_home_of.get(m.lower()) for m in measures} - {None, ""}
    if not homes and tables and logical_of:
        # No measure declares a home (a pill aggregating a plain field), but the unit's
        # tables do: one statement-sourced table is still one source.
        homes = {logical_of.get(t) for t in tables} - {None, ""}
    if len(homes) != 1:
        return None
    return native_by_logical.get(next(iter(homes)))


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:120]


def _visual_calc_measures(u: dict) -> list[dict]:
    """The view's own calculations it shows, as measures of the unit: each formula is the
    calculation's expression, read over the view's matrix (`visual_matrix`)."""
    vm = u.get("visual_matrix") or {}
    return [{"name": c["name"], "formula": c["expression"],
             "formula_language": vm.get("language"), "aggregation": None,
             "visual_calc": True} for c in vm.get("calcs") or [] if not c.get("hidden")]


def _visual_matrix_of(vm: dict, source_names) -> dict:
    """A1's `visual_matrix` with its columns named as the unit's group_by names them."""
    axes = {k: [source_names(list(lvl)) for lvl in levels]
            for k, levels in (vm.get("axes") or {}).items()}
    refs = {k: ({"column": source_names([v["column"]])[0]} if "column" in v else dict(v))
            for k, v in (vm.get("refs") or {}).items()}
    return {**vm, "axes": axes, "refs": refs}


def _table_calc_of(tc: dict, source_names) -> dict:
    """A usage `table_calcs` entry for a unit measure: its columns named as the unit's
    group_by names them (the same mapping), its refusal as is."""
    out = {k: source_names(list(tc.get(k) or [])) for k in ("addressing", "partitioning")}
    if tc.get("sort"):
        # The view's order of an addressed column: keyed, and nested within, by the same names.
        out["sort"] = {}
        for col, rec in tc["sort"].items():
            rec = dict(rec)
            if "within" in rec:
                rec["within"] = source_names(list(rec["within"]))
            out["sort"][source_names([col])[0]] = rec
    if tc.get("pad"):
        out["pad"] = {k: source_names(list(tc["pad"].get(k) or [])) for k in ("rows", "cols")}
    if tc.get("nested"):
        # Each nested calc's own compute-using, its columns named the same way.
        out["nested"] = {calc: {k: source_names(list(rec.get(k) or []))
                                for k in ("addressing", "partitioning")}
                         for calc, rec in tc["nested"].items()}
    if tc.get("refusal"):
        out["refusal"] = tc["refusal"]
    return out


def _fragment_name(name: str, used: dict[str, str]) -> str:
    """A file name for `name`, unique within the run. Two names that sanitise or truncate
    to the same text (`a/b` and `a_b`) would overwrite one fragment with the other, so the
    second takes a short digest of its full name."""
    safe = _safe(name)
    if used.setdefault(safe, name) != name:
        safe = f"{safe[:110]}-{hashlib.sha256(name.encode()).hexdigest()[:8]}"
        used.setdefault(safe, name)
    return safe


def main() -> None:
    stage.main(STAGE, "a2_question_units.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
