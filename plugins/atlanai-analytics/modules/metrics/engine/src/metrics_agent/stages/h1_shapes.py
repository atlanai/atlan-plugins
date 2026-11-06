# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""H1 — select the estate's CANONICAL QUERY SHAPES from the history corpus.

PROVENANCE. The selection is copied (never imported) from an earlier benchmark tool's shape
selector: composite trust, skeleton dedup, unconditional multi-analyst admission, then a
score-ranked single-analyst fill. What it deliberately left behind, so it holds on any estate:

  * **No constants in code.** The trust weights, the adoption gate, the budget and the
    minimum slice size are admission and ranking, so rule 5 puts them in `agent_policy.yml`;
    they are facts about an ESTATE, not about the algorithm. An estate whose analytics team
    is three people cannot use a two-analyst gate.

  * **No table-family keyword map.** A map from table names to families is one estate's
    vocabulary written into code: on any other warehouse every shape falls through to
    "other" and slicing stops working silently. Families are derived from the shapes' own
    table names instead, so the stage carries no estate vocabulary at all.

  * **The corpus is streamed.** The selection needs five fields per row, so the file is read
    a row at a time and the rest dropped as it goes; a large corpus never sits in memory.

The manifest records the counts; stdout says only what degraded.

The corpus contract: one JSON object per line carrying `sql`, `tables`,
`users`, `exec` and a `feat` block. Here H0 builds it (`query_corpus.jsonl`); this stage
takes it as an input path and never reaches for a warehouse.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import pathlib
import re
import sys


from metrics_agent.lib import observed as _observed, pii as _pii, sqlshape as S, stage  # noqa: E402

STAGE = "h1_shapes"
OUT = "query_shapes.json"


def _extra(p) -> None:
    p.add_argument("--corpus", default=None,
                   help="query-history shape corpus, one JSON object per line. Defaults to "
                        "H0's own `query_corpus.jsonl` in the run directory.")
    p.add_argument("--sql-source", default=None,
                   help="{normalised flattened sql: original sql} recovered from the mining "
                        "source. Repairs a corpus whose statements arrived single-line.")


def trust(weights: dict, users: float, execs: float,
          recency: float = 0.0, dashboard: float = 0.0) -> float:
    """Composite adoption score. Every term is log1p'd so one runaway signal cannot dominate.

    `recency` and `dashboard` are accepted and zero-weighted by default exactly as the origin
    left them: an aggregated shape corpus carries neither, and weighting a signal that is not
    in the data is how a ranking starts asserting things nobody measured.
    """
    return (float(weights.get("users", 0.0)) * math.log1p(max(0.0, users))
            + float(weights.get("exec", 0.0)) * math.log1p(max(0.0, execs))
            + float(weights.get("recency", 0.0)) * recency
            + float(weights.get("dashboard", 0.0)) * dashboard)


def analytical(row: dict) -> bool:
    """A shape earns consideration by computing something — an aggregate, a grouping, or a
    filter. A bare projection is a table dump and certifies nothing."""
    f = row.get("feat") or {}
    return bool(f.get("aggs") or f.get("groups") or f.get("filter_cols"))


def skeleton(row: dict) -> tuple:
    """What makes two queries THE SAME SHAPE: same tables, aggregates, groupings and filter
    columns — the structure, with the literals dropped. Two analysts asking the same question
    of two quarters are one shape, and only the most-adopted of them is carried.

    Tables by their FULL name: the same table name in two databases is two relations, and
    keying on the leaf collapsed a query on one into the other's shape."""
    f = row.get("feat") or {}
    return (tuple(sorted(str(t).lower() for t in row.get("tables", []))),
            tuple(sorted((f.get("aggs") or [])[:6])),
            tuple(sorted((f.get("groups") or [])[:6])),
            tuple(sorted((f.get("filter_cols") or [])[:8])))


def family_of(row: dict, strategy: str) -> str:
    """The slice a shape belongs to, derived from the shape rather than from a vocabulary.

    The origin mapped hand-picked table-name keywords to family names. That is this estate's
    nouns compiled into the program: run it on a warehouse whose fact table is not called
    OPPORTUNITY and every shape lands in "other". The dominant base table names the family
    instead — it is always present, always in the estate's own words, and needs no upkeep.
    """
    if strategy == "none":
        return "all"
    names = [t.split(".")[-1].lower() for t in (row.get("tables") or []) if t]
    if not names:
        return "other"
    # The shortest name breaks ties: a shape reading FCT_OPPORTUNITY and
    # FCT_OPPORTUNITY_FIELD_HISTORY belongs with the opportunity family, not its own.
    return sorted(names, key=lambda n: (len(n), n))[0]


def _unflatten(sql: str, source: dict) -> tuple[str, bool]:
    """The statement as it was written, where the corpus holds a flattened copy.

    A CORPUS THAT LOST ITS NEWLINES IS NOT A COSMETIC PROBLEM. An inline `--` comment runs to
    end of line, so on a statement flattened to ONE line it comments out everything after it.
    The statement still looks like SQL and still fails to parse, and the loss then reads as a
    property of the estate's queries rather than of the pipeline — measured in the origin, 153
    of 505 cards and 223 of 296 witness failures. It cannot be repaired by guessing where the
    comment ended, so it is repaired from the source text or not at all.
    """
    original = source.get(_normalised(sql))
    return (original, True) if original else (sql, False)


def _normalised(sql: str) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip()


def flattened_with_comment(sql: str) -> bool:
    """A one-line statement carrying an inline comment — the shape that silently truncates."""
    return "\n" not in (sql or "") and "--" in (sql or "")


def _stream(path: pathlib.Path):
    """Yield only the fields selection needs. The corpus is hundreds of megabytes and the
    other fields are never read; holding them is how this stage would OOM a laptop."""
    keep = ("sql", "tables", "users", "exec", "feat", "n_tables_read", "query_ids",
            "n_query_ids", "query_ids_sha256", "first_seen", "last_seen", _pii.NAMES_KEY)
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue          # a corrupt line is one shape lost, not a failed run
            yield {k: row.get(k) for k in keep}


def body(ctx: stage.Ctx) -> None:
    dialect = ctx.read("run_inputs.json", "run_inputs.schema.json")["dialect"]
    weights = ctx.policy.get("history_shapes.trust_weights")
    floor = int(ctx.policy.get("history_shapes.admission.multi_analyst_min"))
    target = int(ctx.policy.get("history_shapes.admission.target_n"))
    min_family = int(ctx.policy.get("history_shapes.slices.min_family_size"))
    # NEVER DEFAULTED IN CODE: a null or misspelt value is refused, not read as some other
    # setting (rule 5 — a threshold whose value came from nowhere cannot be audited).
    strategy = ctx.policy.get("history_shapes.slices.strategy")
    if strategy not in ("dominant_table", "none"):
        raise SystemExit(f"h1: history_shapes.slices.strategy={strategy!r}; "
                         "expected dominant_table | none")
    unattributed_rule = ctx.policy.get("history_shapes.admission.unattributed")
    if unattributed_rule not in ("never", "when-source-has-none"):
        raise SystemExit(f"h1: history_shapes.admission.unattributed={unattributed_rule!r}; "
                         "expected never | when-source-has-none")

    corpus = (pathlib.Path(ctx.args.corpus).expanduser().resolve()
              if ctx.args.corpus else ctx.run / "query_corpus.jsonl")
    ctx.note_input_path(corpus)
    # Scrubbed here too: `--corpus` and `--sql-source` may be files H0 never wrote, and a
    # repaired statement is the source text again (guardrail 8). Scrubbing is idempotent.
    scrubber = _pii.Scrubber(ctx.policy)
    sql_source = {}
    if ctx.args.sql_source:
        raw = json.loads(pathlib.Path(ctx.args.sql_source).read_text())
        sql_source = {_normalised(scrubber.text(k)): scrubber.text(v)
                      for k, v in raw.items()}
        ctx.note_input_path(ctx.args.sql_source)

    id_cap = int(ctx.policy.get("history_corpus.query_ids_per_shape"))
    n_rows = n_analytical = n_recovered = n_still_flat = 0
    any_adoption = any_user = False
    # OBSERVED VALUES SURVIVE THE COLLAPSE (rule 16). Two queries differing only in a literal
    # share one skeleton, and only the representative's value stays in the statement — so this
    # is the ONLY stage where the collapsed group still exists and the others' values can be
    # captured here, or the guarantee is silently lost.
    best: dict[tuple, tuple[float, dict]] = {}
    seen_values: dict[tuple, dict[str, list]] = {}
    # THE EXECUTIONS OF EVERY COLLAPSED MEMBER, for the same reason: the skeleton is carried
    # by its most-adopted query, but the logged queries that asked it are all of the group's.
    seen_ids: dict[tuple, dict] = {}
    # WHO asked each skeleton, across its collapsed members — only where the policy keeps
    # names (pii.redact_user_names: false), and only in this run directory's history artifact.
    seen_names: dict[tuple, set] = {}
    for row in _stream(corpus):
        names = row.pop(_pii.NAMES_KEY, None) or []
        if not scrubber.keep_user_names:
            names = []    # a corpus written under another policy: the names stop here
        n_rows += 1
        if not analytical(row):
            continue
        n_analytical += 1
        if int(row.get("users") or 0) or int(row.get("exec") or 0):
            any_adoption = True
        if int(row.get("users") or 0):
            any_user = True
        # REPAIRED BEFORE ANYTHING READS IT. Selection, authoring and witnessing all parse
        # this text; repairing it downstream would mean each of them seeing a different
        # statement from the one before.
        row["sql"] = scrubber.text(row.get("sql") or "")
        fixed, did = _unflatten(row["sql"], sql_source)
        if did:
            row["sql"] = fixed
            n_recovered += 1
        elif flattened_with_comment(row.get("sql") or ""):
            n_still_flat += 1
        score = trust(weights, int(row.get("users") or 0), int(row.get("exec") or 0))
        key = skeleton(row)
        try:
            # The values the statement restricts a column TO: IN members one by one, BETWEEN
            # bounds, comparison literals. A whole `('A', 'B')` list, a column compared to a
            # column and an excluded value are none of them observed values.
            for col, v in S.observed_literals(S.parse(row["sql"], dialect)):
                seen_values.setdefault(key, {}).setdefault(col, []).append(
                    {"value": v, "n_exec": int(row.get("exec") or 0),
                     "n_users": int(row.get("users") or 0), _pii.NAMES_KEY: names})
        except Exception:
            pass          # a shape whose literals we cannot read still selects normally
        if names:
            seen_names.setdefault(key, set()).update(names)
        g = seen_ids.setdefault(key, {"ids": set(), "n": 0, "digests": [], "first": None,
                                      "last": None})
        g["ids"].update(row.get("query_ids") or [])
        g["n"] += int(row.get("n_query_ids") or 0)
        if row.get("query_ids_sha256"):
            g["digests"].append(row["query_ids_sha256"])
        for k_, pick in (("first", min), ("last", max)):
            v = row.get(f"{k_}_seen")
            if v:
                g[k_] = v if g[k_] is None else pick(g[k_], v)
        if key not in best or score > best[key][0]:
            best[key] = (score, row)

    # SORTED ON SCORE ALONE, as the origin does. Adding a tie-break on the statement text
    # looked like an improvement — a total order is reproducible where a stable sort is not —
    # but it REORDERS every tie and so changes which single-analyst shapes fill the budget.
    # Python's sort is stable, so ties keep corpus order, and the corpus is a file: the same
    # file gives the same answer, which is the determinism that was actually needed.
    # THE LITERAL FLOOR, before the shaper: a literal is kept when several shapes OR several
    # people use it (pii.literal_min_shapes / literal_min_users). A value one analyst typed
    # once is that analyst's lookup, not the estate's vocabulary. History lane only: a BI
    # filter value is placed by an author and evidenced by the visual itself.
    seen_values, n_lit_kept, n_lit_dropped = scrubber.floor_literals(seen_values)
    shaper = _observed.Shaper(ctx.policy)
    shapes = []
    for k, (sc, r) in best.items():
        rec = {"trust_score": round(sc, 3), **r}
        vals = seen_values.get(k) or {}
        rec["observed_values"] = shaper.apply(vals) if vals else None
        if seen_names.get(k):
            rec[_pii.NAMES_KEY] = sorted(seen_names[k])
        g = seen_ids.get(k) or {}
        ids = sorted(g.get("ids") or [])
        # One digest for the group: over the members' own full-list digests, sorted, so it is
        # stable and checkable against the corpus rows without carrying every id here.
        rec["history_ids"] = {
            "query_ids": ids[:id_cap], "n_query_ids": int(g.get("n") or 0),
            "query_ids_digest": (hashlib.sha256("\n".join(sorted(g["digests"])).encode())
                                 .hexdigest() if g.get("digests") else None),
            "first_seen": g.get("first"), "last_seen": g.get("last")}
        shapes.append(rec)
    shapes.sort(key=lambda x: -x["trust_score"])

    # ADOPTION IS NOT DISPLACED BY SCORE. Every multi-analyst shape is kept whatever its
    # score; the budget is then filled with the best single-analyst shapes. Reversing this —
    # ranking everything together and cutting at N — drops corroborated shapes in favour of
    # one analyst's heavily re-run query, which is the opposite of what adoption means.
    multi = [s for s in shapes if int(s.get("users") or 0) >= floor]
    # BELOW THE GATE BUT STILL ADOPTED BY SOMEONE. The origin writes `== 1`, which quietly
    # excludes shapes carrying no analyst count at all — a corpus that could not attribute a
    # query is not evidence that one person ran it. Written as the range it is, so the
    # exclusion survives a floor other than two instead of being an artifact of the literal.
    single = [s for s in shapes if 1 <= int(s.get("users") or 0) < floor]
    kept = multi + single[:max(0, target - len(multi))]
    kept.sort(key=lambda x: -x["trust_score"])
    for s in multi:
        s["adoption"] = "multi-analyst"
    for s in single:
        s["adoption"] = "single-analyst"
    # NO ADOPTION IN THE SOURCE AT ALL. Admitted after every attributed shape, into what the
    # budget has left, in corpus order — there is no score to rank them by, and inventing one
    # is how an unmeasured signal starts deciding things. Labelled so no consumer can read
    # them as adopted.
    # THE TRIGGER IS "NO ROW NAMES A USER". A lakehouse pull carries neither users nor
    # executions; an unattributed raw log counts executions but names nobody. In both, who ran
    # it is unknown rather than "nobody", and the strict rule would admit nothing. The
    # remaining shapes are already sorted on trust, which with no users is execution count
    # alone (and corpus order where there is none of that either).
    unattributed: list[dict] = []
    if unattributed_rule == "when-source-has-none" and not any_user:
        room = max(0, target - len(kept))
        # STRICTEST SCOPE, because there is no adoption to justify breadth: every relation
        # the statement reads must be a bounded one. Measured without it, a BI-scoped pull of
        # CRM opportunity and task history admitted 286 shapes and minted 276 history-only
        # cards, including a scalar subquery over an unrelated support table that named a
        # customer's account id. A row from an older corpus with no count is not admitted.
        unattributed = [s for s in shapes if not int(s.get("users") or 0)
                        and s.get("n_tables_read") is not None
                        and int(s["n_tables_read"]) <= len(s.get("tables") or [])][:room]
        for s in unattributed:
            s["adoption"] = "unattributed"
        kept = kept + unattributed

    by_family: dict[str, list] = collections.defaultdict(list)
    for s in kept:
        by_family[family_of(s, strategy)].append(s)

    # A family too small to be worth its own authoring bundle joins `misc`.
    slices: dict[str, list] = collections.defaultdict(list)
    for fam, rows in sorted(by_family.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        slices["misc" if len(rows) < min_family else fam].extend(rows)

    out = []
    for fam, rows in sorted(slices.items()):
        for i, s in enumerate(rows):
            f = s.get("feat") or {}
            out.append({
                "shape_id": f"h-{fam}-{i}",
                "slice": fam,
                "sql": s.get("sql") or "",
                "tables": s.get("tables") or [],
                "users": int(s.get("users") or 0),
                "exec": int(s.get("exec") or 0),
                "trust_score": s["trust_score"],
                "adoption": s.get("adoption") or "single-analyst",
                "aggs": f.get("aggs") or [],
                "groups": f.get("groups") or [],
                "filter_cols": f.get("filter_cols") or [],
                "joins": f.get("joins") or [],
                "enum_vals": f.get("enum_vals") or {},  # shape preserved: dict or list, per the corpus
                "observed_values": s.get("observed_values"),
                "win_funcs": f.get("win_funcs") or [],
                **(s.get("history_ids") or {}),
                **({_pii.NAMES_KEY: s[_pii.NAMES_KEY]} if s.get(_pii.NAMES_KEY) else {}),
            })

    ctx.write(OUT, {"contract_version": "1.0", "shapes": out,
                    "slices": {k: len(v) for k, v in sorted(slices.items())}},
              "query_shapes.schema.json")
    kept_flat = sum(1 for s in kept if flattened_with_comment(s.get("sql") or ""))
    ctx.count(corpus_rows=n_rows, analytical=n_analytical, canonical_shapes=len(shapes),
              kept=len(kept), multi_analyst=len(multi), unattributed=len(unattributed),
              source_names_users=any_user,
              with_query_ids=sum(1 for o in out if o.get("query_ids")),
              slices=len(slices),
              sql_recovered=n_recovered, still_flattened=n_still_flat,
              observed_literals_kept=n_lit_kept, observed_literals_floored=n_lit_dropped,
              with_user_names=sum(1 for o in out if o.get(_pii.NAMES_KEY)),
              kept_unparseable_by_flattening=kept_flat)
    ctx.say(f"h1: {n_rows} corpus rows -> {n_analytical} analytical -> {len(shapes)} canonical "
            f"shapes -> {len(kept)} kept ({len(multi)} multi-analyst admitted unconditionally, "
            f"{len(kept) - len(multi) - len(unattributed)} single-analyst admitted on composite "
            f"score, {len(unattributed)} unattributed).")
    if unattributed:
        ctx.say(f"h1: no corpus row names a user ("
                + ("a log that counts executions but not who ran them" if any_adoption else
                   "a pre-aggregated source with no adoption signal at all")
                + f"), so {len(unattributed)} shape(s) were admitted UNATTRIBUTED — observed "
                f"statements, ranked by execution count only, and not a claim about who ran "
                f"them (history_shapes.admission.unattributed).")
    n_cut = sum(1 for o in out if int(o.get("n_query_ids") or 0) > len(o.get("query_ids") or []))
    if n_cut:
        ctx.say(f"h1: {n_cut} shape(s) were asked by more than {id_cap} logged queries; each "
                f"keeps {id_cap} ids, the full count and a digest "
                f"(history_corpus.query_ids_per_shape).")
    if not kept and shapes:
        # AN EMPTY LAYER IS EXPLAINED, never left as a bare zero: every canonical shape was
        # refused, and the reason is a policy setting the operator can see and change.
        if not any_user and unattributed_rule == "never":
            why = ("no corpus row names a user, and history_shapes.admission.unattributed is "
                   "`never`, so a shape nobody can be shown to have run is not admitted. Set it "
                   "to `when-source-has-none` to admit them, labelled `unattributed`")
        elif not any_user:
            why = ("no corpus row names a user, and no shape reads only bounded tables — the "
                   "one licence an unattributed shape is admitted under")
        else:
            why = (f"no shape was adopted by an analyst within the budget "
                   f"(history_shapes.admission.target_n={target})")
        ctx.say(f"h1: EMPTY — {len(shapes)} canonical shape(s), none admitted: {why}.")
    if len(multi) >= target:
        ctx.say(f"h1: the multi-analyst set ({len(multi)}) already meets the budget "
                f"({target}); no single-analyst shape was admitted. Adoption was never "
                f"displaced — the budget was.")
    if n_recovered:
        ctx.say(f"h1: recovered the original text of {n_recovered} statement(s) from the "
                f"mining source.")
    if kept_flat:
        # Said as a corpus defect with its size, because the alternative is that these
        # statements quietly fail to parse downstream and the loss is blamed on the estate.
        ctx.say(f"h1: DEGRADED — {kept_flat} kept shape(s) are still single-line with an "
                f"inline `--` comment, which comments out the rest of the statement and will "
                f"not parse. This is a defect in the CORPUS, not in these analysts' queries. "
                f"Pass --sql-source with the original text to repair them; without it they "
                f"are carried and labelled, never silently dropped.")
    ctx.say("h1: slices " + ", ".join(f"{k}={len(v)}" for k, v in sorted(slices.items())))


def main() -> None:
    stage.main(STAGE, "h1_shapes.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
