"""The pipeline as commands.

    init      bind this run to a registry workspace and the agent it grades for
    doctor    what can I build from? an inventory, not a gate
    build     query history or BI tiles -> candidates -> prompts for the questions
    review    check the drafts, then read the pairs and approve     <- the human step
    push      approved records -> registry
    evals     register a skill's own eval suite as a registry dataset
    status    where this run is up to

Only `review` needs a person. Between `build` and `review` sits the one step a
model does — recovering the question a query was written to answer — and
everything either side of it is deterministic.

Checking is folded into `review` rather than given its own command: you review
what survived, so running the checks separately only adds a name to remember.

`init` reads from the registry - workspace, agents, and the caller's permission
matrix - but writes nothing. `push` is the only command that writes. Everything
between them is local.

There is deliberately no upfront re-derivation step. Whether a question is
ambiguous only matters once an agent has failed it, and at that point the runner
can ask the question with its decisions pinned and find out. Paying for that on
every record up front buys information most records never need.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

DEFAULT_WORK = Path("work")


def _today() -> str:
    """Absolute, never relative: a name carrying "latest" tells a later reader
    nothing about which run it was."""
    return date.today().isoformat()


def _read_json(path: Path):
    if not path.exists():
        raise SystemExit(f"missing {path} — run the previous step first")
    return json.loads(path.read_text())


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


def _run_config(work: Path) -> dict:
    p = work / "run.json"
    if not p.exists():
        raise SystemExit(
            "this run is not bound to a workspace — run `eval-builder init "
            "--workspace <name>` first. Binding late means nothing before the "
            "push can warn about a name collision or the wrong destination."
        )
    return json.loads(p.read_text())


# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    from eval_builder.registry import (
        RegistryError, agent_exists, personal_workspace, probe_write_access,
        resolve_workspace, workspace_detail,
    )

    work = Path(args.work)
    if args.local:
        # A host that publishes through its own sign-in (the Analytics plugin's MCP publisher)
        # binds here without the CLI. `push` then refuses: the host publishes, not this tool.
        _write_json(work / "run.json", {"workspace": args.workspace or None, "workspace_id": None,
                                   "agent": args.agent or None, "org_wide": None, "can_delete": None,
                                   "local": True})
        print("  bound locally: nothing reads or writes the registry from this run")
        print(f"\n  run bound in {work / 'run.json'}")
        return 0
    if args.workspace:
        ws_id = resolve_workspace(args.workspace)
    else:
        found = personal_workspace()
        if not found:
            raise SystemExit(
                "no personal workspace found, so there is no safe default — "
                "pass --workspace explicitly")
        ws_id, args.workspace = found
        print(f"  no workspace given; using your own: {args.workspace}")

    refusals: list[str] = []

    # Publishing org-wide is the one mistake with no undo: creating in a shared
    # workspace does not imply the right to delete from it.
    try:
        detail = workspace_detail(ws_id)
    except RegistryError as e:
        detail = {}
        print(f"  note: could not read workspace detail ({str(e)[:70]})")
    if detail.get("org_wide"):
        refusals.append(
            f"{args.workspace} is visible org-wide. Anything pushed there is "
            "seen by the whole organisation, and creating in a workspace does "
            "not grant the right to delete from it."
        )

    if args.agent:
        try:
            found, why = agent_exists(args.agent, ws_id)
            if not found:
                refusals.append(f"agent {args.agent!r}: {why}")
        except RegistryError as e:
            print(f"  note: could not verify the agent ({str(e)[:70]})")

    # Read-only: the gateway reports the caller's capability matrix, so there is
    # nothing to create and nothing to clean up. Still ordered after the other
    # refusals, because there is no reason to make a call about a workspace we
    # have already declined to bind to.
    probe = None
    if refusals and not args.force:
        print("  refusing to bind:")
        for r in refusals:
            print(f"    · {r}")
        print("\n  pass --force to bind anyway, or pick a different workspace.")
        if any("agent" in r for r in refusals):
            print("  If the agent does not exist yet, do not go looking for one "
                  "— drop --agent")
            print("  entirely and build the set; it can be linked after the "
                  "agent is created.")
        return 1

    if not args.skip_access_probe:
        try:
            probe = probe_write_access(ws_id)
        except Exception as e:  # noqa: BLE001
            print(f"  note: access probe failed to run ({str(e)[:70]})")
        else:
            if not probe.can_create:
                refusals.append(f"cannot create artifacts in {args.workspace}: "
                                f"{probe.detail[:110]}")
            elif not probe.can_delete:
                print(f"  WARNING: {probe.render()}")

    if refusals and not args.force:
        print("  refusing to bind:")
        for r in refusals:
            print(f"    · {r}")
        print("\n  pass --force to bind anyway, or pick a different workspace.")
        if any("agent" in r for r in refusals):
            print("  If the agent does not exist yet, do not go looking for one "
                  "— drop --agent")
            print("  entirely and build the set; it can be linked after the "
                  "agent is created.")
        return 1
    for r in refusals:
        print(f"  FORCED past: {r[:100]}")

    cfg = {"workspace": args.workspace, "workspace_id": ws_id, "agent": args.agent,
           "org_wide": bool(detail.get("org_wide")),
           "can_delete": None if probe is None else probe.can_delete}
    _write_json(work / "run.json", cfg)
    print(f"  workspace   {args.workspace} ({ws_id})"
          f"{'  [ORG-WIDE]' if detail.get('org_wide') else ''}")
    print(f"  agent       {args.agent or '— none, the set stands alone'}")
    if not args.agent:
        print("              a set built before its agent exists is a normal "
              "starting point:")
        print("              the questions are about the business, not about "
              "one implementation.")
        print("              link it later with `push --agent <name>` once "
              "there is one.")
    print(f"\n  run bound in {work}/run.json")
    print("  next: eval-builder doctor")
    return 0


def cmd_doctor(args) -> int:
    from eval_builder import doctor as _doctor
    from eval_builder.doctor import render, run

    # A session started with --mcp-config has a different tool set from the
    # global config `claude mcp list` reads, and the doctor must report the one
    # the agent can actually reach.
    _doctor.MCP_CONFIG = args.mcp_config or ""

    work = Path(args.work)
    p = work / "run.json"
    if p.exists():
        cfg = json.loads(p.read_text())
        print(f"  bound to {cfg['workspace']}"
              f"{' · agent ' + cfg['agent'] if cfg.get('agent') else ''}\n")

    results = run()
    if args.from_bi:
        from eval_builder.doctor import check_tableau_inputs

        results.append(check_tableau_inputs(Path(args.from_bi).expanduser()))
    if getattr(args, "from_powerbi", ""):
        from eval_builder.doctor import check_powerbi_inputs

        results.append(check_powerbi_inputs(Path(args.from_powerbi).expanduser()))
    print(render(results))

    # The principle, applied to the use-case: presence of a warehouse says
    # nothing about whether THESE tables are readable. A grant on the schema
    # does not imply a grant on the table.
    if args.tables:
        from eval_builder.doctor import _sf_connect, probe_tables

        wanted = [t.strip() for t in args.tables.split(",") if t.strip()]
        print(f"\n  access to the {len(wanted)} table(s) you named:")
        try:
            conn = _sf_connect()
        except Exception as e:  # noqa: BLE001
            print(f"    cannot probe — no warehouse ({str(e).splitlines()[0][:60]})")
            return 1
        try:
            probed = probe_tables(conn, wanted)
        finally:
            conn.close()
        for t, ok, why in probed:
            print(f"    [{'ok  ' if ok else 'FAIL'}] {t}" + (f"  {why}" if why else ""))
        unreadable = [t for t, ok, _ in probed if not ok]
        if unreadable:
            print(f"\n  {len(unreadable)} unreadable. Scoping a build to these would")
            print("  select nothing, or select queries you cannot re-run.")
            return 1

    return 1 if any(r.status.value == "fail" for r in results) else 0


def _scope_to_tables(candidates: list, spec: str) -> tuple[list, int]:
    """Keep candidates touching the named tables. No search, no guessing.

    `--about` asks the tool to find the ground from a regex, which is a search
    problem it has no business solving. Run against a real warehouse, a pattern
    like `ORDER|CUSTOMER` matched every staging table with CUSTOMER in the
    name and still missed the two tables the domain is actually built on —
    because those back a dashboard and analysts rarely query them by hand.

    When the caller knows the ground, naming it is both simpler and correct.
    Matched on the suffix, so `MARTS.FCT_ORDERS` and the fully-qualified form
    both work and a bare table name matches in any schema.
    """
    wanted = [t.strip().upper() for t in spec.split(",") if t.strip()]
    kept, dropped = [], 0
    # Which named table pulled each candidate in. A dimension every query joins
    # drags its whole neighbourhood along: scoping on DIM_ACCOUNTS brought the
    # marketing-campaign domain into a funnel-focused set. Reporting the split
    # makes that visible in one run instead of three.
    by_table: dict[str, int] = {w: 0 for w in wanted}
    for c in candidates:
        tables = [t.upper() for t in c.tables]
        hits = [w for w in wanted
                if any(t == w or t.endswith("." + w) for t in tables)]
        if hits:
            kept.append(c)
            for w in hits:
                by_table[w] += 1
        else:
            dropped += 1
    return kept, dropped, by_table


def _scope(candidates: list, pattern: str) -> tuple[list, int]:
    """Keep only candidates touching tables that match the use-case pattern.

    An eval set is built FOR something. Mining the whole warehouse and ranking
    globally produces a set spread across release management, alerting and
    support tickets — excellent coverage of an estate, useless as a bench for
    one agent. The pattern is matched against fully-qualified table names, so
    `SALES_PROD.MARTS` scopes to a schema and `FCT_ORDERS` to a family.
    """
    if not pattern:
        return candidates, 0
    rx = re.compile(pattern, re.I)
    kept = [c for c in candidates if any(rx.search(t) for t in c.tables)]
    return kept, len(candidates) - len(kept)


def _resolve_dialects(raw: list, work: Path) -> str:
    """Fill each file row's dialect from a source that names it: the row's own
    field, then `build --dialect`, then the connector Atlan catalogues its
    tables under. The SQL is read only for the table names, never to decide."""
    from eval_builder import dialect as D
    from eval_builder import schema_check as sc

    declared = sum(1 for c in raw if c.dialect)
    flag = 0
    if D._run:                                  # noqa: SLF001 - `build --dialect`
        for c in raw:
            if not c.dialect:
                c.dialect, flag = D._run, flag + 1   # noqa: SLF001
    todo = [c for c in raw if not c.dialect]
    from_atlan = 0
    if todo and sc.route(work):
        tables = {}
        for c in todo:
            for d in ("snowflake", "databricks", "bigquery", "redshift", "tsql"):
                refs = sc.table_refs(c.sql, d)
                if refs:
                    tables[id(c)] = refs
                    break
        catalog, _, _ = sc.load(work, [r for refs in tables.values() for r in refs])
        for c in todo:
            found = {D.from_qualified_name(m.qualified_name)
                     for r in tables.get(id(c), []) for m in ((catalog or {}).get(sc._key(r)) or [])} - {None}
            if len(found) == 1:
                c.dialect = found.pop()
                from_atlan += 1
    assumed = sum(1 for c in raw if not c.dialect)
    by = {}
    for c in raw:
        by[c.dialect or D.run()] = by.get(c.dialect or D.run(), 0) + 1
    line = (", ".join(f"{d} {n}" for d, n in sorted(by.items()))
            + f" (row field {declared}, --dialect {flag}, Atlan connector {from_atlan}")
    if assumed:
        line += f"; {assumed} named by no source, assumed {D.run()}: pass --dialect or a 'dialect' per row"
    return line + ")"


def _dialect_of(c) -> str:
    from eval_builder import dialect as D

    return D.of(c)


def cmd_build(args) -> int:
    from eval_builder.reverse import build_prompt
    from eval_builder.select import (dedupe, diversify, domain_of, rank, select_by_coverage,
                                     split_ties, tile_kind, tile_page)
    from eval_builder.sensitive import scan_element, scan_projection, scan_sql

    work = Path(args.work)
    _run_config(work)
    from eval_builder import dialect as D

    D.use(args.dialect or None)                 # rows and tiles that name their own still win
    if args.sigma_export and not args.from_sigma:
        raise SystemExit("--sigma-export replays a saved export for --from-sigma "
                         "<workbook>; pass both")

    schema: dict = {}
    # A BI source yields tiles: each candidate carries the element it was built
    # from, its SQL is already cut to what the tile displays, and the set is
    # chosen to cover the workbook rather than by its SQL's score.
    tiles = bool(args.from_sigma or args.from_bi or args.from_powerbi)
    if args.from_sigma:
        from eval_builder.doctor import check_atlan_credentials
        from eval_builder.sources.sigma import mine_sigma

        export = Path(args.sigma_export).expanduser() if args.sigma_export else None
        cred = check_atlan_credentials()
        if export is None and cred.status.value != "ok":
            raise SystemExit(f"  [FAIL] {cred.name}  {cred.detail}\n         → {cred.fix}")
        mined = mine_sigma(args.from_sigma, work, export_dir=export,
                           base_url=os.environ.get("ATLAN_BASE_URL"),
                           api_key=os.environ.get("ATLAN_API_KEY"))
        if mined.note:
            print(f"  note: {mined.note}")
        # rank() scores; scoring here too would run every detector twice and
        # append every note twice, doubling the apparent evidence for a
        # candidate in the file a reviewer reads.
        raw = list(mined.candidates)
        schema = mined.schema
        source_label = f"{mined.source}, {args.from_sigma}" + (f", export {export}" if export else "")
        conn = None
    elif args.from_bi:
        from eval_builder.sources.tableau import mine_tableau

        root = Path(args.from_bi).expanduser()
        mined = mine_tableau(root, dialect=args.dialect or None)
        if mined.note:
            print(f"  note: {mined.note}")
        raw = list(mined.candidates)    # rank() scores; see above
        schema = mined.schema
        source_label = f"{mined.source}, {root}"
        conn = None
    elif args.from_powerbi:
        from eval_builder.sources.powerbi import mine_powerbi

        root = Path(args.from_powerbi).expanduser()
        mined = mine_powerbi(root, dialect=args.dialect or None)
        if mined.note:
            print(f"  note: {mined.note}")
        raw = list(mined.candidates)    # rank() scores; see above
        schema = mined.schema
        source_label = f"{mined.source}, {root}"
        conn = None
    elif args.from_files:
        from eval_builder.sources.files import mine_files, read_schema

        from eval_builder.sources.filters import filter_variants, infer_page_filters

        root = Path(args.from_files).expanduser()
        mined = mine_files(root)
        print(f"  note: {mined.note}")
        raw = mined.candidates          # rank() scores; see above
        print(f"  dialect    {_resolve_dialects(raw, work)}")
        schema = read_schema(root)
        source_label = f"files:{root}"
        conn = None
        # BI exports used to come in here with page and element_type. The tile
        # loop is --from-sigma / --from-bi now; these rows are mined as queries.
        bi_rows = sum(1 for c in raw if c.element_type)
        if bi_rows:
            print(f"  hint: {bi_rows} row(s) carry element_type. BI exports now go through "
                  "--from-sigma / --from-bi / --from-powerbi; "
                  + ("this row is" if bi_rows == 1 else "these rows are")
                  + " treated as query history")

        # A BI element's SQL carries its whole dataset, so it is cut to what
        # its question reads before anything scores or scans it.
        from eval_builder.prune import prune_candidates

        pruned = prune_candidates(raw)
        if pruned:
            print(f"  pruned     {pruned} of {len(raw)} to what their question reads — "
                  "scored, scanned and drafted from that; original kept as original_sql")

        # A file source records no filters, so they are inferred: a page filter
        # must apply to every element that uses its column, which makes the
        # intersection across the page a usable signal. It is still inference —
        # on one workbook it ran 3/4 with full recall — so the count is reported
        # rather than folded in silently.
        if schema and len(raw) > 1:
            paged = any(c.page for c in raw)
            ready, unusable = infer_page_filters(
                [c.sql for c in raw], schema,
                pages=[c.page for c in raw] if paged else None)
            variants = [v for c in list(raw)
                        for v in filter_variants(
                            c, [f for f in ready if f.page is None or f.page == c.page],
                            schema)]
            if ready or unusable:
                bits = [f"{len(ready) + len(unusable)} inferred filter candidate(s)"
                        + (f" across {len({c.page for c in raw})} page(s)" if paged else "")]
                if unusable:
                    bits.append(f"{len(unusable)} without sample values "
                                f"({', '.join(unusable[:3])}) — not injected")
                if variants:
                    bits.append(f"{len(variants)} filtered variant(s)")
                print(f"  filters: {'; '.join(bits)}")
            raw = raw + variants
    else:
        from eval_builder.doctor import _sf_connect
        from eval_builder.sources.snowflake import mine_query_history

        conn = _sf_connect()
        history = mine_query_history(conn, days=args.days, limit=args.pool,
                                     exclude_users=args.exclude_users)
        if history.note:
            print(f"  note: {history.note}")
        raw = history.candidates
        source_label = f"{history.source}, {history.days}d"

    variants_line = ""
    if tiles:
        # Every refusal is kept, with its reason: a tile the adapter would not
        # anchor is a finding about the workbook, not something to lose.
        _write_json(work / "refused.json", [el.to_dict() for el in mined.refused])
        variants, variants_line = _tile_variants(raw)
        raw += variants

    # Vocabulary is optional and source-supplied: `vocabulary.json` beside the
    # queries, shaped {"titles": [...], "measures": [...], "filters": [...]}.
    vocabulary = None
    if getattr(args, "from_files", None):
        vpath = Path(args.from_files) / "vocabulary.json"
        if vpath.exists():
            try:
                vocabulary = json.loads(vpath.read_text())
            except Exception as e:  # noqa: BLE001 - bad vocabulary is not fatal
                print(f"  vocabulary.json unreadable, ignoring: {str(e)[:60]}")

    if tiles:
        # A tile is admitted on rank's gates alone: it parses, it is
        # deterministic, it is not pinned to one record. The discrimination
        # floor scores SQL structure -- CTE and join counts -- which for a tile
        # is the BI tool's compilation, not the question; coverage chooses.
        ranked = rank(raw, min_discriminating=0.0)
        kept_ids = {id(c) for c in ranked}
        gated = [c for c in raw if id(c) not in kept_ids]
    else:
        ranked = rank(raw)
    ranked, merged = dedupe(ranked)
    by_table: dict[str, int] = {}
    if args.tables:
        ranked, out_of_scope, by_table = _scope_to_tables(ranked, args.tables)
    else:
        ranked, out_of_scope = _scope(ranked, args.about)

    withheld = []
    clean = []
    for c in ranked:
        # A tile returns its outermost SELECT and nothing else; what its CTEs
        # carry on the way is not disclosed by running it. Literals count
        # wherever they sit.
        el = c.element
        dialect = _dialect_of(c)
        findings = (scan_sql(c.sql, dialect=dialect)
                    + scan_projection(c.sql, outer_only=el is not None, dialect=dialect)
                    + (scan_element(el) if el is not None else []))
        if findings and not args.allow_sensitive:
            withheld.append((c, findings))
        else:
            if findings:
                c.notes.append(
                    "CUSTOMER DATA, kept under --allow-sensitive: "
                    + "; ".join(f.render()[:70] for f in findings[:2]))
            clean.append((c, findings))
    clean = [c for c, _ in clean]

    # A BI or file source has no warehouse to run its SQL against, here or at
    # review. Check it against Atlan's schema now, so a candidate whose SQL
    # names a column the table does not have is gone before anyone drafts a
    # question for it.
    schema_report = None
    if conn is None:
        clean, schema_report = _schema_filter(work, clean)

    # Within a use-case, spreading across subject areas is counter-productive —
    # the whole point is to stay inside one. Diversify only when unscoped.
    # Tiles are a different question: cover the workbook's pages and kinds.
    scoped = bool(args.about or args.tables)
    if tiles:
        selected = select_by_coverage(clean, args.limit)
    else:
        selected = clean[:args.limit] if scoped else diversify(clean, limit=args.limit)

    if conn is not None:
        from eval_builder.sources.snowflake import mine_schema

        dbs = {t.split(".")[0] for c in selected for t in c.tables if len(t.split(".")) == 3}
        for db in dbs:
            try:
                schema.update(mine_schema(conn, db))
            except Exception as e:  # noqa: BLE001
                print(f"  schema unavailable for {db}: {str(e).splitlines()[0][:70]}")
        conn.close()

    wanted = {t for c in selected for t in c.tables}
    _write_json(work / "schema.json", {k: v for k, v in schema.items() if k in wanted})

    records, stub = [], {}
    prompts = work / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(selected, 1):
        rid = f"{args.prefix}-{i:02d}"
        records.append({
            "id": rid, "query_id": c.query_id, "sql": c.sql, "tables": c.tables,
            "modes": list(c.modes), "user": c.user_name, "run_count": c.run_count,
            "distinct_users": c.distinct_users, "first_seen": c.first_seen,
            "last_seen": c.last_seen, "scores": c.scores, "notes": c.notes,
            "original_sql": c.original_sql, "page": c.page, "element_type": c.element_type,
            "dialect": _dialect_of(c),
            "element": c.element.to_dict() if c.element is not None else None,
        })
        (prompts / f"{rid}.txt").write_text(build_prompt(c, schema, vocabulary))
        # Scaffolded so the shape is filled in rather than reconstructed.
        stub[rid] = {"question_natural": "", "question_spec": "",
                     "decisions": [], "as_of": None, "answerable": True}
    _write_json(work / "candidates.json", records)
    if not (work / "answers.json").exists():
        _write_json(work / "answers.json", stub)

    print(f"  mined      {len(raw)} ({source_label})")
    if tiles:
        for line in _tile_summary(mined, work):
            print(line)
        if variants_line:
            print(variants_line)
        if gated:
            print(_gated_line(gated))
        family = sum(1 for c in ranked if any(n.startswith("family:") for n in c.notes))
        if family:
            print(f"  families   {family} tile(s) share a shape with another, differing only "
                  "in literals — kept, one per family selected before any second")
    if merged:
        print(f"  merged     {len(merged)} duplicate(s): "
              f"{', '.join(a + '→' + b for a, b in merged[:4])}")
    if args.tables:
        print(f"  scoped     to {len(args.tables.split(','))} named table(s) — "
              f"{out_of_scope} out of scope")
        total = max(len(ranked), 1)
        for name, n in sorted(by_table.items(), key=lambda kv: -kv[1]):
            share = n / total
            flag = ("  <- in most of them; a dimension this common drags its "
                    "whole neighbourhood in. Scope on facts."
                    if share > 0.6 and n else
                    ("  <- nothing" if not n else ""))
            print(f"               {n:>4} via {name}{flag}")
    if args.about or args.tables:
        if args.about:
            print(f"  scoped     to /{args.about}/ — {out_of_scope} out of scope")
        if not ranked:
            target = f"/{args.about}/" if args.about else args.tables
            print("  NOTHING MATCHED. The mined pool contains no queries against")
            print(f"  tables matching {target}. Either nobody has queried them")
            print("  in this window, your role cannot see those queries, or the")
            print("  pattern is wrong. The set is empty rather than filled with")
            print("  unrelated queries.")
            return 1
    if tiles:
        print(f"  selected   {len(ranked)} -> {len(selected)} across "
              f"{len({tile_page(c) for c in selected})} page(s) and "
              f"{len({tile_kind(c) for c in selected})} kind(s), by coverage")
        for line in _coverage_table(clean, selected):
            print(line)
    else:
        print(f"  selected   {len(ranked)} -> {len(selected)} across "
              f"{len({domain_of(c) for c in selected})} subject areas")
        splits = split_ties(selected, clean, group=(lambda c: None) if scoped else domain_of)
        for total, area, tied, chosen in splits:
            print(f"  tied       {tied} candidates at the cut-off total {total:.3f}"
                  f"{f' in {area}' if area else ''}; {chosen} taken by tie-break "
                  "(mode depth, then mode count, then SQL hash — not file order)")
        if selected and not splits:
            print("  tied       no tie at the cut-off — the scores alone chose the set")
    if args.allow_sensitive:
        kept = sum(1 for c in selected if any("CUSTOMER DATA" in n for n in c.notes))
        print(f"  sensitive  {kept} kept under --allow-sensitive — this set "
              "carries customer-identifying values")
    else:
        print(f"  withheld   {len(withheld)} carrying customer data"
              + (" (--allow-sensitive keeps them)" if withheld else ""))
        for _, f in withheld[:3]:
            print(f"    {f[0].render()[:96]}")
    if schema_report:
        print(schema_report)
    print(f"\n  prompts -> {prompts}/*.txt")
    print(f"  answers  -> {work}/answers.json  (scaffolded, one entry per prompt)")
    print(f"  answer each prompt, then: eval-builder review --name <handle>")
    return 0


def _tile_variants(raw: list) -> tuple[list, str]:
    """Twins of tiles whose dashboard filter has Atlan top values, and the
    line reporting them. The values come from the catalog or not at all."""
    import functools

    from eval_builder import schema_check as sc
    from eval_builder.sources.filters import fetch_top_values, tile_variants, variant_filters

    eligible = sum(len(variant_filters(c.element)) for c in raw if c.element is not None)
    if not eligible:
        return [], ""
    if not sc.rest_configured():
        return [], (f"  variants   none: {eligible} dashboard filter(s) could vary, but "
                    "Atlan's top values need ATLAN_BASE_URL/ATLAN_API_KEY")
    failed: list[str] = []

    @functools.lru_cache(maxsize=None)
    def top(column: str) -> tuple[str, ...]:
        try:
            return tuple(fetch_top_values(column))
        except Exception as e:  # noqa: BLE001 - a failed lookup costs a variant, not the build
            failed.append(f"{column} ({type(e).__name__})")
            return ()

    out = [v for c in raw if c.element is not None for v in tile_variants(c, top)]
    line = (f"  variants   {len(out)} from {eligible} dashboard filter(s): each set to up to "
            "2 of Atlan's most frequent values, the saved one skipped")
    if failed:
        line += f"\n             top-value lookup failed for {', '.join(failed[:3])}"
    return out, line


def _tile_summary(mined, work: Path) -> list[str]:
    """What the adapter made of the workbook: tiles by kind, refusals by reason."""
    import collections

    from eval_builder.elements import ElementKind

    order = list(ElementKind)
    kinds = collections.Counter(c.element.kind for c in mined.candidates
                                if c.element is not None)
    lines = [f"  tiles      {sum(kinds.values())}: " + (" · ".join(
        f"{k.value} {kinds[k]}" for k in sorted(kinds, key=order.index)) or "none")]
    reasons = collections.Counter(el.refusal or "no reason given" for el in mined.refused)
    lines.append(f"  refused    {len(mined.refused)} -> {work / 'refused.json'}")
    for reason, n in reasons.most_common(8):
        lines.append(f"    {n:>4}  {reason[:96]}")
    if len(reasons) > 8:
        lines.append(f"          {len(reasons) - 8} more reason(s) in refused.json")
    return lines


def _gated_line(gated: list) -> str:
    """Tiles rank's gates dropped, by reason: a tile is never lost silently."""
    import collections

    why = collections.Counter(
        next((n.removeprefix("dropped: ").split(" — ")[0][:60]
              for n in c.notes if n.startswith("dropped: ")), "dropped") for c in gated)
    return (f"  gated      {len(gated)} tile(s) dropped by the selector's gates: "
            + "; ".join(f"{n} {reason}" for reason, n in why.most_common(3)))


def _coverage_table(pool: list, selected: list) -> list[str]:
    """Page by kind: how many tiles were there to take, and how many were taken."""
    import collections

    from eval_builder.elements import ElementKind
    from eval_builder.select import page_order, tile_kind, tile_page

    order = list(ElementKind)
    available = collections.Counter((tile_page(c), tile_kind(c)) for c in pool)
    taken = collections.Counter((tile_page(c), tile_kind(c)) for c in selected)
    lines = ["  coverage   page × kind, available/selected"]
    for page in sorted({p for p, _ in available}, key=page_order):
        kinds = sorted({k for p, k in available if p == page}, key=order.index)
        cells = " · ".join(f"{k.value} {available[(page, k)]}/{taken[(page, k)]}" for k in kinds)
        lines.append(f"    {(page or '(no page)')[:32]:<32}  {cells}")
    return lines


def _schema_filter(work: Path, candidates: list) -> tuple[list, str]:
    """Drop candidates whose SQL uses a column Atlan's schema does not have.

    Only `missing` drops. A candidate that cannot be checked is kept — `review`
    marks it UNVERIFIED — and the summary says how many there are.
    """
    from eval_builder import schema_check as sc

    refs = [r for c in candidates for r in sc.table_refs(c.sql, _dialect_of(c))]
    if not refs:
        return candidates, ""
    catalog, route, errors = sc.load(work, refs)
    if route != "REST":
        # Rewritten every build, so the agent's file can follow the candidates.
        request = sc.write_request(work, refs)
    if catalog is None:
        return candidates, (
            "  atlan      golden SQL NOT checked — no warehouse, and no Atlan route\n"
            "             ATLAN_BASE_URL/ATLAN_API_KEY unset. With an Atlan MCP: save the\n"
            f"             tables in {request} to\n"
            f"             {work / sc.SCHEMA_FILE} as it describes, then re-run build.")

    kept, dropped, unverified = [], [], 0
    for c in candidates:
        out = sc.check(c.sql, catalog, _dialect_of(c))
        if out.status == sc.MISSING:
            dropped.append((c.query_id, out.detail))
            continue
        unverified += out.status == sc.UNVERIFIED
        kept.append(c)
    lines = [f"  atlan      {len(candidates)} checked against Atlan ({route}): "
             f"{len(kept) - unverified} ok · {len(dropped)} dropped · {unverified} unverified"]
    lines += [f"    dropped {qid}: {why[:96]}" for qid, why in dropped[:5]]
    lines += [f"    lookup failed — {e}" for e in errors[:3]]
    return kept, "\n".join(lines)


def _assemble(work: Path, name: str, source: str) -> tuple[int, list[tuple[str, str]]]:
    """Drafted questions -> checked spec. Local only; nothing leaves the machine."""
    from eval_builder.build import _to_record
    from eval_builder.doctor import connect
    from eval_builder.elements import VisualElement
    from eval_builder.reverse import parse_response
    from eval_builder.select import Candidate, score
    from eval_builder.sensitive import scan_record
    from eval_builder.spec import EvalSpec

    cfg = _run_config(work)
    cands = {c["id"]: c for c in _read_json(work / "candidates.json")}
    answers = _read_json(work / "answers.json")

    spec = EvalSpec(name=name, source_description=source or "")
    dropped: list[tuple[str, str]] = []
    pending: list = []

    # A candidate with no entry at all is not "checked and dropped", it is
    # unfinished work — and silently omitting it loses track of it.
    undrafted = [rid for rid in cands if rid not in answers]

    for rid, ans in answers.items():
        raw = cands.get(rid)
        if raw is None:
            dropped.append((rid, "no such candidate"))
            continue
        # answerable=False is a deliberate verdict, not an unfinished entry;
        # reporting it as "not answered yet" hides a decision the drafter made.
        if isinstance(ans, dict) and ans.get("answerable") is False:
            why = (ans.get("decisions") or ["not a business question"])[0]
            dropped.append((rid, f"marked not a business question — {why}"))
            continue
        if isinstance(ans, dict) and not (ans.get("question_natural") or "").strip():
            dropped.append((rid, "not drafted yet"))
            continue

        element = raw.get("element")
        cand = score(Candidate(
            query_id=rid, sql=raw["sql"], user_name=raw.get("user"),
            run_count=raw.get("run_count", 1), distinct_users=raw.get("distinct_users", 1),
            first_seen=raw.get("first_seen"), last_seen=raw.get("last_seen"),
            page=raw.get("page"), element_type=raw.get("element_type"),
            original_sql=raw.get("original_sql"),
            element=VisualElement.from_dict(element) if element else None,
            dialect=raw.get("dialect"),
        ))
        # Build's notes are a superset of those score() just wrote again --
        # they also carry the prune, family and variant notes -- so they
        # replace them rather than doubling every finding in the provenance.
        if raw.get("notes"):
            cand.notes = list(raw["notes"])
        rev = parse_response(json.dumps(ans) if isinstance(ans, dict) else ans, cand)
        if not rev.answerable:
            dropped.append((rid, "not a business question"))
            continue
        if rev.leaks:
            dropped.append((rid, "; ".join(rev.leaks)))
            continue

        rec = _to_record(cand, rev)
        rec.modes = list(cand.modes)
        rec.question_spec = rev.question_spec
        rec.decisions = rev.decisions
        pending.append(rec)

    # The answer key should run: a record whose golden SQL errors cannot grade
    # anything, and finding that out at review time wastes the review. But a
    # files build may have no warehouse at all, and refusing to assemble then
    # would make the file source useless — so this degrades rather than blocks.
    # Each record runs on the warehouse its dialect names; one without a
    # reachable warehouse falls back to the Atlan check below.
    conns: dict[str, object] = {}
    for d in sorted({rec.dialect for rec in pending}):
        try:
            conns[d] = connect(d)
        except Exception as e:  # noqa: BLE001
            conns[d] = None
            print(f"  note: golden SQL not executed on {d} — no warehouse ({str(e).splitlines()[0][:60]}).")
    conn = None

    # Without a warehouse, Atlan's schema is the check: a column the table does
    # not have drops the record; anything it cannot tell is kept UNVERIFIED.
    from eval_builder import schema_check as sc

    catalog, route = None, ""
    unexecuted = [rec for rec in pending if conns.get(rec.dialect) is None]
    if unexecuted:
        refs = [r for rec in unexecuted for r in sc.table_refs(rec.golden_sql, rec.dialect)]
        catalog, route, errors = sc.load(work, refs)
        if catalog is None:
            print("        not checked against Atlan either — ATLAN_BASE_URL/ATLAN_API_KEY")
            print(f"        unset and no {sc.SCHEMA_FILE}. Records are kept UNVERIFIED.")
        else:
            print(f"        checking columns against Atlan's schema instead ({route}).")
        for err in errors[:3]:
            print(f"        lookup failed — {err}")

    try:
        for rec in pending:
            conn = conns.get(rec.dialect)
            if conn is None and catalog is not None:
                out = sc.check(rec.golden_sql, catalog, rec.dialect)
                if out.status == sc.MISSING:
                    dropped.append((rec.id, out.detail))
                    continue
                rec.review_note = (
                    f"golden SQL not executed; columns checked against Atlan ({route}): "
                    f"{out.detail}" if out.status == sc.OK
                    else f"UNVERIFIED — golden SQL not executed, Atlan check inconclusive: "
                         f"{out.detail}")
            elif conn is not None:
                try:
                    with conn.cursor() as cur:
                        cur.execute(rec.golden_sql)
                        rows = cur.fetchmany(200)
                    rec.review_note = f"golden SQL returns {len(rows)} row(s)"
                except Exception as e:  # noqa: BLE001
                    dropped.append((rec.id, f"golden SQL does not execute: "
                                            f"{str(e).splitlines()[0][:80]}"))
                    continue
            else:
                rec.review_note = ("UNVERIFIED — golden SQL not executed (no warehouse) "
                                   "and not checked against Atlan (no route)")
            # Last gate before a human spends attention on it.
            findings = scan_record(rec)
            if findings:
                dropped.append((rec.id, findings[0].render()))
                continue
            spec.records.append(rec)
    finally:
        for c_ in conns.values():
            if c_ is not None:
                c_.close()

    spec.dedupe()
    # Approvals already given survive a re-check, so editing one answer does not
    # silently un-approve the rest of the set.
    prior = {}
    sp = work / "spec.json"
    if sp.exists():
        prior = {r["id"]: r.get("review_status", "pending")
                 for r in json.loads(sp.read_text()).get("records", [])}
    for r in spec.records:
        r.review_status = prior.get(r.id, "pending")
    sp.write_text(spec.to_json())
    return len(spec.records), dropped, undrafted


def cmd_review(args) -> int:
    """Render the pairs a person has to agree with, and the set's coverage."""
    import collections

    from eval_builder.select import FAILURE_MODES
    from eval_builder.spec import EvalSpec

    work = Path(args.work)

    # Checking is a precondition of reviewing, not a step of its own: you review
    # what survived. Re-runs are cheap except for executing the golden SQL, so
    # this is skipped when only recording a verdict or handing over the next
    # record — those read the spec that assembly already produced.
    if not (args.approve or args.reject or args.next):
        kept, dropped, undrafted = _assemble(work, args.name, args.source)
        print(f"  checked {kept + len(dropped)} · kept {kept} · dropped {len(dropped)}")
        for rid, why in dropped:
            print(f"    dropped {rid}: {why[:92]}")
        if undrafted:
            print(f"    {len(undrafted)} not drafted yet: {', '.join(undrafted[:8])}")
            print("    (write them in work/answers.json, or leave them — they are"
                  " simply not part of the set)")
        unverified = [r for r in EvalSpec.from_json((work / "spec.json").read_text()).records
                      if r.review_note.startswith("UNVERIFIED")]
        if unverified:
            print(f"\n  ⚠  {len(unverified)} of {kept} kept record(s) UNVERIFIED — their golden SQL")
            print("     was neither executed nor confirmed against Atlan's schema:")
            for r in unverified[:5]:
                why = r.review_note.removeprefix("UNVERIFIED — ").split("inconclusive: ", 1)[-1]
                print(f"     {r.id}: {why[:90]}")
        print()

    spec = EvalSpec.from_json((work / "spec.json").read_text())

    if args.approve or args.reject:
        verdict = "approved" if args.approve else "rejected"
        raw = args.approve or args.reject
        ids = set(raw.split(",")) if raw != "all" else None
        n = 0
        for r in spec.records:
            if ids is None or r.id in ids:
                r.review_status = verdict
                n += 1
        (work / "spec.json").write_text(spec.to_json())
        print(f"  {verdict} {n} record(s)")
        remaining = sum(1 for r in spec.records if r.review_status == "pending")
        if remaining:
            print(f"  {remaining} still pending — `review --next` for the next one")
        else:
            print("  next: eval-builder push")
        return 0

    if args.next:
        return _review_next(work, spec)

    for r in spec.records:
        mark = {"approved": "[approved]", "rejected": "[rejected]"}.get(
            r.review_status, "[  ?  ]")
        print(f"\n{mark} {r.id}    modes: {', '.join(r.modes) or '—'}")
        for i, line in enumerate(_tile_header(r.element)):
            print(f"  {'T' if i == 0 else ' '}  {line[:150]}")
        print(f"  Q  {r.question}")
        if r.question_spec:
            print(f"  =  {r.question_spec[:300]}")
        for d in r.decisions[:4]:
            print(f"  ·  {d}")
        print(f"  SQL {' '.join(r.golden_sql.split())[:150]}")
        if r.review_note:
            mark = "⚠" if r.review_note.startswith("UNVERIFIED") else "✓"
            print(f"  {mark}  {r.review_note}")

    modes = collections.Counter(m for r in spec.records for m in r.modes)
    domains = collections.Counter(
        ".".join(r.tables[0].split(".")[:2]) if r.tables else "?" for r in spec.records)
    print(f"\n  {len(spec.records)} records · "
          f"{sum(1 for r in spec.records if r.review_status == 'approved')} approved")
    print("\n  failure-mode coverage")
    for m in FAILURE_MODES:
        n = modes.get(m, 0)
        flag = "   <- thin" if n <= 1 else ""
        print(f"    {m:<20} {n}{flag}")
    print(f"\n  subject areas: {len(domains)}")
    for d, n in domains.most_common(6):
        print(f"    {d:<34} {n}")
    print("\n  read the pairs above. Does each question sound like something a")
    print("  person would ask, and does the SQL answer it?")
    print("  then: eval-builder review --approve all   (or --approve id1,id2)")
    return 0


def cmd_push(args) -> int:
    from eval_builder.registry import push
    from eval_builder.spec import EvalSpec

    work = Path(args.work)
    cfg = _run_config(work)
    if cfg.get("local"):
        raise SystemExit("this run was bound with `init --local`: its host publishes the set, "
                         "not `push`. In the Analytics plugin that is `analytics_eval.py publish`.")
    spec = EvalSpec.from_json((work / "spec.json").read_text())

    unreviewed = [r.id for r in spec.records if r.review_status != "approved"]
    if unreviewed and not args.force:
        print(f"  {len(unreviewed)} record(s) not approved; they will be skipped.")
        print("  run `eval-builder review` to read them first.")

    result = push(spec, workspace=cfg["workspace_id"],
                  allow_sensitive=args.allow_sensitive)
    print(result.render())

    # The binding becomes a fact in the registry rather than a note on this
    # machine. The runner fetches evals and agent together; it can only do that
    # if the edge exists.
    agent = args.agent or cfg.get("agent")
    if agent and result.dataset_id:
        from eval_builder.registry import link_dataset_to_agent

        ok, detail = link_dataset_to_agent(
            result.dataset_id, agent, cfg["workspace_id"])
        if ok:
            print(f"  linked             evaluates -> {agent} ({detail})")
        else:
            print(f"  NOT linked         {detail}")
            print("                     the set is published but attached to nothing")
    elif agent:
        print(f"  bound agent        {agent} (not linked — no dataset id)")
    return 0


def cmd_evals(args) -> int:
    """Register a skill's own eval suite as a registry dataset.

    A suite that lives only as a file in a bundle is inert: nothing runs it,
    nothing tracks it across versions, and the eval platform that exists two
    directories away never sees it. Registering it makes the skill's behavioural
    expectations first-class artifacts, gradeable by the same machinery as any
    other dataset.

    The unit-checkable assertions stay in the test suite, where they fail fast.
    This carries the behavioural ones — the claims only a run can settle.
    """
    from eval_builder.registry import push
    from eval_builder.spec import EvalRecord, EvalSpec, Provenance

    work = Path(args.work)
    cfg = _run_config(work)
    suite = _read_json(Path(args.suite))

    spec = EvalSpec(
        name=args.name or f"{suite['skill']}-behaviour",
        source_description=suite.get("description", "")[:900],
    )

    for group in ("should_trigger", "should_not_trigger"):
        for case in suite.get(group, []):
            # Unit assertions are enforced by tests; carrying them here would
            # duplicate an expectation in a place that cannot check it.
            behavioural = [a for a in case.get("assertions", [])
                           if not a.startswith("unit:")]
            if not behavioural:
                continue
            spec.records.append(EvalRecord(
                id=case["id"],
                question=case["prompt"],
                # The "answer" to a behavioural eval is the behaviour itself.
                golden_sql="",
                modes=[group],
                question_spec=case["expected_behavior"],
                decisions=behavioural,
                review_status="approved" if args.approve else "pending",
                provenance=Provenance(source="skill_suite", query_id=case["id"]),
            ))

    if not spec.records:
        print("  nothing to register — every assertion is unit-enforced")
        return 0

    print(f"  {len(spec.records)} scenario(s) from {args.suite}")
    for r in spec.records:
        print(f"    {r.id:<34} {len(r.decisions)} behavioural assertion(s)")

    if args.dry_run:
        print("\n  --dry-run: nothing sent")
        return 0

    result = push(spec, workspace=cfg["workspace_id"], input_key="prompt")
    print(result.render())

    if args.record_run:
        return _record_suite_run(args, cfg, spec, result.dataset_id)
    return 0


def _dataset_record_ids(dataset_id: str) -> dict[str, str]:
    """{input prompt: registry record id} for joining results back to rows."""
    from eval_builder.registry import api

    out, offset = {}, 0
    while True:
        page = api("GET", f"/eval/v1/datasets/{dataset_id}/records"
                          f"?limit=100&offset={offset}")
        items = page.get("items", [])
        for item in items:
            prompt = ((item.get("input") or {}).get("prompt")
                      or item.get("display_name") or "")
            if prompt:
                out[prompt] = item["id"]
        if len(items) < 100:
            return out
        offset += 100


def _record_suite_run(args, cfg, spec, dataset_id: str) -> int:
    """Run the suite for real and catalog the run as an experiment.

    The numbers have to come from an execution, not from a remembered result.
    A suite that passed last week and is asserted to pass today is exactly the
    kind of claim an eval platform exists to stop people making.
    """
    import subprocess
    import sys
    import tempfile

    runner = Path(args.suite).with_name("run_trigger_evals.py")
    if not runner.exists():
        print(f"  no runner beside the suite ({runner}) — nothing to record")
        return 1

    kept = Path(args.work) / "suite-run.json"
    if args.reuse_run and kept.exists():
        outcomes = json.loads(kept.read_text())
        print(f"\n  reusing {len(outcomes)} outcome(s) from {kept} "
              "— no sessions run")
        return _catalog_run(args, cfg, dataset_id, outcomes)

    print(f"\n  running {runner.name} — one headless session per scenario")
    out_path = Path(tempfile.mkstemp(suffix=".json", prefix="suite-run-")[1])
    proc = subprocess.run(
        [sys.executable, str(runner), "--suite", str(args.suite),
         "--json-out", str(out_path)],
        capture_output=True, text=True, timeout=3600)
    if not out_path.exists() or not out_path.read_text().strip():
        print(f"  runner produced no outcomes: "
              f"{(proc.stderr or proc.stdout)[-300:]}")
        return 1
    outcomes = json.loads(out_path.read_text())
    out_path.unlink(missing_ok=True)
    # Kept before anything is sent. The first version of this went straight to
    # the registry, hit a 400, and threw away five headless sessions that had
    # already run.
    kept.write_text(json.dumps(outcomes, indent=1))
    print(f"  outcomes kept in {kept}")
    return _catalog_run(args, cfg, dataset_id, outcomes)


def _result_item(experiment_name: str, o: dict, record_ids: dict,
                 workspace_id: str) -> dict:
    """One experiment result, in the shape the DEPLOYED gateway accepts.

    `scores` and `case_id` are declared on `eval.ExperimentResultCreate` in the
    published OpenAPI and refused by the running service — "neither an envelope
    field nor a property this kind declares". A boolean is rejected even on a
    field it does take. So both ride inside `output` with numeric scores, which
    it accepts, and this function is the single place to undo that when the
    gateway catches up.
    """
    matched = bool(o["fired"]) == bool(o["expected_fire"])
    return {
        "name": f"{experiment_name}-{o['scenario']}",
        "display_name": o["scenario"],
        "dataset_record_id": record_ids.get(o.get("prompt", "")),
        "input": {"prompt": o.get("prompt", ""), "case_id": o["scenario"]},
        "expected": {"fires": o["expected_fire"]},
        "output": {
            "fired": o["fired"],
            "skills": o.get("skills", []),
            "tools": o.get("tools", [])[:12],
            "case_id": o["scenario"],
            "scores": {"fired": int(bool(o["fired"])),
                       "matches_expectation": int(matched)},
        },
        "error": o.get("error") or None,
        "workspace_id": workspace_id,
    }


def _existing_case_ids(experiment_id: str) -> set[str]:
    from eval_builder.registry import api

    page = api("GET", f"/eval/v1/experiments/{experiment_id}/results?limit=200")
    ids = set()
    for i in page.get("items", []):
        cid = i.get("case_id") or (i.get("output") or {}).get("case_id")
        if cid:
            ids.add(cid)
    return ids


def _catalog_run(args, cfg, dataset_id, outcomes) -> int:
    """Turn executed outcomes into an experiment and its per-case results.

    Split from the execution on purpose: an upload that fails must be
    retryable without paying for the sessions again.
    """
    from eval_builder.registry import (finalize_experiment, push_results,
                                     start_experiment)

    # The per-case join back to the dataset row. Results carry it individually,
    # which also means the run survives an experiment that could not be bound to
    # its dataset.
    record_ids = _dataset_record_ids(dataset_id) if dataset_id else {}
    # Artifact names are unique per ACCOUNT and the index covers archived rows,
    # so a date is not enough — a second run on the same day collides with the
    # first, and archiving the first does not free the name. The registry's own
    # bench solved it the same way: `bench-<epoch ms>`.
    experiment_name = (f"{args.name or 'eval-builder'}-trigger-"
                       f"{int(time.time() * 1000)}")
    experiment, warning = start_experiment(
        name=experiment_name,
        display_name=f"{args.name or 'eval-builder'} · trigger run {_today()}",
        description="Whether the harness invokes the skill for each scenario, "
                    "settled by the tool-use stream.",
        dataset_id=dataset_id,
        config={"harness": "claude-code", "scorer": "skill-fires",
                "case_count": len(outcomes)},
        workspace_id=cfg["workspace_id"],
    )
    print(f"  experiment {experiment}")
    if warning:
        print(f"  NOTE: {warning}")

    items = [_result_item(experiment_name, o, record_ids, cfg["workspace_id"])
             for o in outcomes]

    # An interrupted upload leaves cases already there; the endpoint rejects a
    # repeated case_id, so filter rather than collect five "already exists".
    done = _existing_case_ids(experiment)
    if done:
        print(f"  {len(done)} case(s) already uploaded — skipping those")
    items = [i for i in items if i["output"]["case_id"] not in done]

    created, problems = push_results(experiment, items)
    for p in problems:
        print(f"    rejected: {p}")

    passed = sum(1 for o in outcomes
                 if bool(o["fired"]) == bool(o["expected_fire"]))
    problem = finalize_experiment(
        experiment,
        status="completed" if passed == len(outcomes) else "failed",
        summary={"scores": {"matches_expectation": {
            "mean": round(passed / max(len(outcomes), 1), 3),
            "count": len(outcomes)}}},
    )
    if problem:
        print(f"  NOTE: {problem}")
    print(f"  {created} result(s) uploaded · {passed}/{len(outcomes)} matched "
          f"expectation")
    return 0 if passed == len(outcomes) else 1


# ---------------------------------------------------------------------------
# The scorers. Cataloged, not invented at push time.
#
# Both are `code`: the registry stores the definition and the scores a runner
# reports, and never executes anything. So each spec names the module and entry
# point that implements it — a scorer artifact whose implementation cannot be
# located is a claim nobody can reproduce.
#
# There is deliberately no `llm_judge` here. Both of these are decidable from
# the record and the run, and a judge introduces variance into the one part of
# an eval system that has to be stable.
# ---------------------------------------------------------------------------

SCORERS = [
    {
        "name": "eval-question-quality",
        "display_name": "Eval question quality",
        "description": (
            "Grades the eval, not the agent: how many decisions the natural "
            "question leaves for the agent to resolve, how many buried "
            "decisions the golden SQL makes, and how much of the answer the "
            "question already hands over. Leakage caps the score rather than "
            "averaging with it, because a question that states its own answer "
            "is not a hard eval with a flaw — it is not an eval."),
        "scorer_kind": "code",
        "scope": "result",
        "outputs": {
            "eval_quality": {"type": "numeric", "range": [0, 1],
                             "description": "earned difficulty, capped by leakage"},
            "intent_hops": {"type": "numeric",
                            "description": "decisions the question does not state"},
            "sql_depth": {"type": "numeric",
                          "description": "failure modes the golden SQL exercises"},
            "gives_away": {"type": "numeric", "range": [0, 1],
                           "description": "share of decisions the question restates"},
        },
        "spec": {
            "language": "python",
            "entrypoint": "eval_builder.grade:grade_record",
            "package": "eval-builder skill bundle",
            "reads": ["question", "question_spec", "decisions", "golden_sql"],
            "grounding": "skills/eval-generation/SKILL.md",
        },
    },
    {
        "name": "skill-fires",
        "display_name": "Skill actually fired",
        "description": (
            "Did the harness invoke the skill for this prompt? Settled by the "
            "tool-use stream, not by the reply's wording — an earlier version "
            "checked whether the text mentioned the skill by name and reported "
            "a fire on a run where it was never invoked."),
        "scorer_kind": "code",
        "scope": "session",
        "outputs": {
            "fired": {"type": "boolean",
                      "description": "the Skill tool was invoked with this skill"},
            "matches_expectation": {"type": "boolean",
                                    "description": "fired iff the case expected it to"},
        },
        "spec": {
            "language": "python",
            "entrypoint": "evals/run_trigger_evals.py",
            "package": "atlan-eval-agents repository (not the skill bundle)",
            "reads": ["session transcript (stream-json tool_use events)"],
        },
    },
]


def cmd_scorers(args) -> int:
    """Catalog the graders in the registry."""
    from eval_builder.registry import register_scorer

    cfg = _run_config(Path(args.work))
    for s in SCORERS:
        if args.dry_run:
            print(f"  would register {s['name']} ({s['scorer_kind']}/{s['scope']}) "
                  f"-> {', '.join(s['outputs'])}")
            continue
        sid, created = register_scorer(workspace_id=cfg["workspace_id"], **s)
        print(f"  {'registered' if created else 'already there'}  "
              f"{s['name']:<24} {sid}")
    return 0


def cmd_grade(args) -> int:
    """Score the set this run produced, before anyone is asked to approve it.

    Placed before review rather than after push on purpose: the point of the
    grade is to stop a weak set reaching a reviewer's attention budget, not to
    decorate one that already shipped.
    """
    from eval_builder.grade import grade_all, summarise
    from eval_builder.spec import EvalSpec

    work = Path(args.work)
    path = work / "spec.json"
    if not path.exists():
        raise SystemExit("no spec.json — run `review` first")

    spec = EvalSpec.from_json(path.read_text())
    grades = grade_all(spec.records)
    for g in sorted(grades, key=lambda g: g.score):
        print(g.render())

    s = summarise(grades)
    q = s["scores"]["eval_quality"]
    print(f"\n  mean {q['mean']:.2f} across {q['count']} record(s); "
          f"{s['scores']['hands_over_answer']['count']} hand over their own answer")
    print(f"  modes covered: {', '.join(s['modes_covered']) or 'none'}")

    (work / "grades.json").write_text(json.dumps(
        {"summary": s, "grades": [vars(g) | {"score": g.score,
                                             "verdict": g.verdict} for g in grades]},
        indent=2))
    print(f"  written to {work / 'grades.json'}")
    return 0


def _review_next(work: Path, spec) -> int:
    """One record, weakest first, for a reviewer working through them in turn.

    Approval is the only human checkpoint in this workflow, and `--approve all`
    makes skipping it a single keystroke. Handing over one pair at a time makes
    reading them the path of least resistance instead.

    Weakest first, by the grade if one has been run: attention goes where it is
    most needed, and a reviewer who stops early has still seen the questions
    most likely to be wrong.
    """
    import collections

    pending = [r for r in spec.records if r.review_status == "pending"]
    if not pending:
        done = collections.Counter(r.review_status for r in spec.records)
        print(f"  nothing pending — {done['approved']} approved, "
              f"{done['rejected']} rejected")
        if done["approved"]:
            print("  next: eval-builder push")
        return 0

    scores = {}
    grades_file = work / "grades.json"
    if grades_file.exists():
        try:
            scores = {g["record_id"]: g for g in
                      json.loads(grades_file.read_text()).get("grades", [])}
        except (json.JSONDecodeError, KeyError):
            scores = {}

    pending.sort(key=lambda r: scores.get(r.id, {}).get("score", 1.0))
    r = pending[0]
    seen = len(spec.records) - len(pending) + 1

    print(f"  record {seen} of {len(spec.records)}   ({len(pending) - 1} after this)")
    print(f"\n  {r.id}    modes: {', '.join(r.modes) or '—'}")
    g = scores.get(r.id)
    if g:
        print(f"  grade {g['score']:.2f} — {g['verdict']}")
        if g.get("leaked"):
            print(f"  the question already states: {', '.join(g['leaked'][:3])}")
    header = _tile_header(r.element)
    if header:
        print(f"\n  TILE       {header[0]}")
        for line in header[1:]:
            print(f"             {line}")
    print(f"\n  QUESTION   {r.question}")
    print("             ^ this is what the agent sees, and all it sees.")
    if r.question_spec:
        print(f"\n  PINNED     {r.question_spec}")
        print("             ^ never shown to the agent. It exists to prove the")
        print("               question has exactly one right answer.")
    if r.decisions:
        print("  DECISIONS  a person answering the question could get these wrong:")
        for d in r.decisions:
            print(f"               · {d}")
    print(f"\n  GOLDEN SQL\n{_indent(r.golden_sql, 4)}")
    if r.review_note:
        mark = "⚠  " if r.review_note.startswith("UNVERIFIED") else ""
        print(f"\n  CHECK      {mark}{r.review_note}")
    print(f"\n  Does this question sound like something a person would ask,")
    print("  and does the SQL answer exactly it?")
    print(f"    keep    eval-builder review --approve {r.id}")
    print(f"    drop    eval-builder review --reject {r.id}")
    return 0


def _tile_header(element: dict | None) -> list[str]:
    """The tile a record was built from, in the words its drafter saw, so a
    reviewer holds the question against the tile and not only the SQL."""
    if not element:
        return []
    from eval_builder.elements import VisualElement

    return VisualElement.from_dict(element).summary().splitlines()


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.strip().splitlines())


def cmd_status(args) -> int:
    work = Path(args.work)
    p = work / "run.json"
    if p.exists():
        cfg = json.loads(p.read_text())
        print(f"  bound to {cfg['workspace']}"
              f"{' · agent ' + cfg['agent'] if cfg.get('agent') else ''}")
    else:
        print("  not bound — run `eval-builder init --workspace <name>`")
    print(f"  work dir: {work.resolve()}")
    for name, step in (("candidates.json", "build"), ("answers.json", "drafted"),
                       ("spec.json", "checked")):
        path = work / name
        if not path.exists():
            print(f"  [ ] {step:<10} ({name} absent)")
            continue
        data = json.loads(path.read_text())
        items = data if isinstance(data, list) else data.get("records", data)
        if name == "answers.json":
            done = sum(1 for v in data.values()
                       if isinstance(v, dict) and (v.get("question_natural") or "").strip())
            print(f"  [{'x' if done else ' '}] {step:<10} {done}/{len(data)} drafted")
        else:
            print(f"  [x] {step:<10} {len(items)} item(s)")
    sp = work / "spec.json"
    if sp.exists():
        recs = json.loads(sp.read_text()).get("records", [])
        ok = sum(1 for r in recs if r.get("review_status") == "approved")
        print(f"  [{'x' if ok else ' '}] review     {ok}/{len(recs)} approved")
    return 0


# --------------------------------------------------------------------------

def _add_work(parser: argparse.ArgumentParser, root: bool = False) -> None:
    """`--work` on every subparser as well as the root.

    argparse only accepts a root-level flag BEFORE the subcommand, so
    `eval-builder status --work X` fails while `eval-builder --work X status`
    succeeds. Nobody types it the second way.

    The subparser copy uses SUPPRESS rather than a default: a real default on
    the subparser overwrites whatever the root already parsed, so
    `eval-builder --work X status` would silently fall back to ./work.
    """
    parser.add_argument(
        "--work",
        default=str(DEFAULT_WORK) if root else argparse.SUPPRESS,
        help="run directory (default: ./work)",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval-builder", description=__doc__.split("\n")[0])
    _add_work(ap, root=True)
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("init", help="bind this run to a workspace and agent")
    n.add_argument("--workspace", default="",
                   help="defaults to your own (user_default) workspace — "
                        "a run that says nothing about destination should not "
                        "land somewhere shared")
    n.add_argument("--agent", default="", help="the agent this set grades")
    n.add_argument("--force", action="store_true",
                   help="bind despite an org-wide workspace or a missing agent")
    n.add_argument("--skip-access-probe", action="store_true",
                   help="do not read the workspace's permission matrix")
    n.add_argument("--local", action="store_true",
                   help="bind with no registry read, for a host that publishes the set itself; push refuses")
    _add_work(n)
    n.set_defaults(fn=cmd_init)

    d = sub.add_parser("doctor", help="what can I build from?")
    d.add_argument("--mcp-config", default="", metavar="FILE",
                   help="the --mcp-config this session was started with; without "
                        "it the MCP check reads your global config, which a "
                        "contained session does not use")
    d.add_argument("--tables", default="", metavar="A.B.C,D.E.F",
                   help="also probe whether you can actually read these tables — "
                        "a grant on the schema does not imply one on the table")
    d.add_argument("--from-bi", default="", metavar="DIR",
                   help="also count the Tableau workbooks and datasources in the "
                        "folder you will pass to `build --from-bi`")
    d.add_argument("--from-powerbi", default="", metavar="PATH",
                   help="also check what a Power BI file or folder holds — the "
                        "path you will pass to `build --from-powerbi`")
    _add_work(d)
    d.set_defaults(fn=cmd_doctor)

    b = sub.add_parser("build", help="query history or BI tiles -> candidates + prompts")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--pool", type=int, default=4000, help="rows to pull before selection")
    b.add_argument("--limit", type=int, default=25, help="candidates to keep")
    b.add_argument("--id-prefix", dest="prefix", default="mined",
                   help="prefix for generated record ids and prompt filenames "
                        "(cosmetic; use --about to filter which queries are mined)")
    b.add_argument("--tables", default="", metavar="A.B.C,D.E.F",
                   help="the exact tables this eval set is about — matched on "
                        "fully-qualified name. Use this when you know the ground; "
                        "--about is a regex for when you do not")
    b.add_argument("--allow-sensitive", action="store_true",
                   help="keep candidates carrying customer-identifying values. "
                        "For an eval set over your OWN estate; never for a set "
                        "that leaves the company")
    b.add_argument("--about", default="",
                   help="scope to tables matching this regex, e.g. "
                        "'SALES_PROD.MARTS' or 'FCT_ORDERS' — an eval set "
                        "is built FOR something")
    b.add_argument("--from-files", default="", metavar="DIR",
                   help="read queries from .sql/.json files instead of the "
                        "warehouse; no credentials needed")
    b.add_argument("--from-sigma", default="", metavar="WORKBOOK",
                   help="a Sigma workbook's guid or qualifiedName in Atlan — one "
                        "candidate per tile; reads ATLAN_BASE_URL/ATLAN_API_KEY")
    b.add_argument("--sigma-export", default="", metavar="DIR",
                   help="with --from-sigma: replay a saved export instead of calling Atlan")
    b.add_argument("--from-bi", default="", metavar="DIR",
                   help="read Tableau exports (.twb/.twbx with their .tds/.tdsx) — "
                        "one candidate per dashboard tile")
    b.add_argument("--from-powerbi", default="", metavar="PATH",
                   help="read Power BI reports (.pbix, .pbip, .pbit, or a folder of them / of "
                        "*.Report + *.SemanticModel) — one candidate per visual; no Power BI "
                        "Desktop needed (.pbix models need pbixray)")
    b.add_argument("--dialect", default="", metavar="NAME",
                   help="the warehouse dialect when the source does not name one (snowflake, databricks, "
                        "bigquery, redshift, …); with --from-bi or --from-powerbi it overrides the "
                        "connector's. "
                        "overriding the one the datasource's connection implies")
    _add_work(b)
    b.add_argument("--exclude-users", nargs="*", default=[], metavar="USER",
                   help="service accounts specific to your warehouse, excluded "
                        "at the source; shape-based exclusion (svc_*, *_etl, "
                        "fivetran*, …) already runs without this")
    b.set_defaults(fn=cmd_build)

    r = sub.add_parser("review", help="check the drafts, then read the pairs and approve")
    r.add_argument("--next", action="store_true",
                   help="show one record at a time, weakest first — the review "
                        "flow that does not invite rubber-stamping")
    r.add_argument("--reject", default="", metavar="ID,ID",
                   help="record that you read these and they should not ship; "
                        "without it a rejected record is indistinguishable from "
                        "one nobody looked at")
    r.add_argument("--approve", default="", help='"all" or a comma-separated list of ids')
    r.add_argument("--name", default="", help="dataset handle, kebab-case (required first time)")
    r.add_argument("--source", default="", help="one line on where these came from")
    _add_work(r)
    r.set_defaults(fn=cmd_review)

    p = sub.add_parser("push", help="approved records -> registry")
    p.add_argument("--agent", default="",
                   help="link the pushed set to this agent, for a set that was "
                        "built before its agent existed")
    p.add_argument("--allow-sensitive", action="store_true",
                   help="publish records carrying customer identifiers (reviewed exception)")
    p.add_argument("--quiet-unreviewed", dest="force", action="store_true",
                   help="do not warn about records that were never approved "
                        "(they are skipped either way — this does not override "
                        "the approval gate)")
    _add_work(p)
    p.set_defaults(fn=cmd_push)

    e = sub.add_parser("evals", help="register a skill's eval suite as a dataset")
    e.add_argument("--suite", default="evals/evals.json")
    e.add_argument("--name", default="", help="dataset handle (default: <skill>-behaviour)")
    e.add_argument("--approve", action="store_true",
                   help="mark records approved; otherwise they land pending review")
    e.add_argument("--dry-run", action="store_true")
    _add_work(e)
    e.add_argument("--reuse-run", action="store_true",
                   help="catalog the outcomes already in suite-run.json "
                        "instead of running the sessions again")
    e.add_argument("--record-run", action="store_true",
                   help="run the suite and catalog the run as an experiment")
    e.set_defaults(fn=cmd_evals)

    sc = sub.add_parser("scorers", help="catalog the graders in the registry")
    _add_work(sc)
    sc.add_argument("--dry-run", action="store_true")
    sc.set_defaults(fn=cmd_scorers)

    g = sub.add_parser("grade", help="score the set before a human reads it")
    _add_work(g)
    g.set_defaults(fn=cmd_grade)

    st = sub.add_parser("status", help="where this run is up to")
    _add_work(st)
    st.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
