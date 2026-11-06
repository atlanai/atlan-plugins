# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""H5 — emit the verified-query layer from this agent's own history catalog.

The record mapping is `e6_import.py`'s, unchanged: a certified card becomes ONE verified query
— the analyst's statement plus the prose that says what it decides — because that is what a
consumer reads before trusting a number. What changed is where the cards come from. They are
now H3's output, produced by this agent from the estate's own query history, instead of a
catalog.json copied out of another repository.

TWO THINGS THE IMPORT HARD-CODED AND THIS DOES NOT:

  * **The clone map.** `e6_import` carries seven production->clone database names in a module
    constant. That is one estate's naming written into the program; on any other warehouse it
    rewrites nothing and says nothing about it. It is an optional input here, and a run
    without one performs no rewrite and records that on the manifest.

  * **The recovered-SQL side file — but only from THIS stage.** The import repaired the
    corpus's single-line flattening here, at the end. It belongs at the start: selection,
    authoring and witnessing each parse the statement, and repairing it last means every one
    of them read a different text from the one that finally ships. H1 now takes
    `--sql-source` and repairs before anything reads the shape.

    (An earlier draft of this file claimed the repair was unnecessary because H1 "reads the
    shape corpus directly, so statements keep the newlines they were mined with". That was
    asserted, not checked, and it is false: the corpus stores every statement on one line,
    and five of the first six verified queries built from it failed to parse.)

`pending_ruling` is NEVER served, exactly as before: a conflict awaiting a human ruling is not
a verified query, and serving one would make the review queue decorative.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import re
import sys
import time
from collections import Counter


import sqlglot

from metrics_agent.lib import artifacts, sqlshape as S, slugs, stage  # noqa: E402
from metrics_agent import plug_runtime as _runtime  # noqa: E402
from metrics_agent import sockets as _sockets  # noqa: E402
from metrics_agent.lib import access as _access  # noqa: E402
from metrics_agent.lib import whgate, witness as _witness  # noqa: E402

STAGE = "h5_verified"
OUT = "verified_queries.history.json"

IN = "query_shapes.json"
MEASURES = "history_measures.json"
OVERLAY = "described_measures.json"

_WS = re.compile(r"\s+")


def _extra(p) -> None:
    p.add_argument("--clone-map", default=None,
                   help="optional {production_db: clone_db} JSON. Absent, no rewrite happens "
                        "and the manifest says so.")


def _norm(s: str) -> str:
    return _WS.sub(" ", str(s or "")).strip()


def _to_clone(sql: str, clone_map: dict, dialect: str | None = None) -> str:
    """Rewrite production database names to their clones — IDENTIFIERS ONLY.

    A database name is the first part of a dotted name of three or more parts
    (`DB.SCHEMA.TABLE`, `DB.SCHEMA.TABLE.COLUMN`), and only a token in that position is
    rewritten, matched whole and case-insensitively. The statement is read with the dialect's
    tokenizer, so a string literal (`WHERE note = 'PROD.x'`) and a comment are never touched:
    a regex over the text rewrote both, changing what the statement filters on and what its
    comments say. Everything outside the rewritten tokens is kept byte for byte (rule 17).
    A quoted name stays quoted. Text the tokenizer cannot read is returned unchanged.
    """
    if not clone_map or not sql:
        return sql
    lookup = {str(k).lower(): str(v) for k, v in clone_map.items()}
    try:
        toks = list(sqlglot.tokenize(sql, read=dialect))
    except Exception:
        return sql
    dot = sqlglot.TokenType.DOT
    edits = []
    for i, t in enumerate(toks):
        if t.token_type in (dot, sqlglot.TokenType.STRING) or t.text.lower() not in lookup:
            continue
        if i and toks[i - 1].token_type == dot:
            continue                                 # not the first part of the name
        if not (i + 3 < len(toks) and toks[i + 1].token_type == dot
                and toks[i + 2].token_type != dot and toks[i + 3].token_type == dot):
            continue                                 # not a three-part (or longer) name
        raw = sql[t.start:t.end + 1]
        new = lookup[t.text.lower()]
        if raw[:1] in "\"`[" and len(raw) >= 2:
            new = raw[0] + new + raw[-1]
        edits.append((t.start, t.end + 1, new))
    out = sql
    for start, end, new in sorted(edits, reverse=True):
        out = out[:start] + new + out[end:]
    return out


def _as_notes(v) -> list[str]:
    """Exclusions as a list of notes, whether the card wrote prose or a list."""
    if isinstance(v, str):
        return [p.strip() for p in v.split(";") if p.strip()]
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def _klass(card: dict, tree) -> str:
    """Which of the layer's five shapes this statement is — read off the statement itself."""
    from sqlglot import exp
    sel = tree.find(exp.Select)
    aggs = [e for e in (sel.expressions if sel else []) if list(e.find_all(
        exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max))]
    grouped = tree.find(exp.Group) is not None
    if not aggs:
        return "lookup"
    if grouped:
        return "segmentation"
    return "metric"


def _unique(name: str, vq_id: str, seen: dict) -> str:
    """`name`, or `name` with this object's own id appended when the name is already taken."""
    if name not in seen:
        seen[name] = 1
        return name
    seen[name] += 1
    return f"{name}-{vq_id.rsplit('-', 1)[-1][:8]}"


def _humanise(card: dict) -> str:
    """The estate's own word for a measure, or its formula when the analysts never named it.

    An alias is what the people who wrote the query called the column, so it is the best name
    available without asking anyone — and unlike a coinage it is a word they will search with.
    """
    for a in (card.get("aliases") or []):
        a = str(a).strip()
        if a and not a.isdigit():
            return a.replace("_", " ")
    return str(card.get("formula") or "measure")[:80]


def body(ctx: stage.Ctx) -> None:
    # What this stage met on an earlier pass is replaced by what it meets now.
    _access.clear_stage(ctx.run, STAGE)
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    dialect = ri["dialect"]
    # THE LAYER IS DETERMINISTIC AND DOES NOT WAIT FOR PROSE. H2's measures already carry
    # everything a verified query needs to exist: the statement, its tables, its adoption and
    # the estate's own word for what it computes. Description used to be a PRECONDITION, which
    # made a 429-measure layer unavailable until 429 paragraphs had been written — so the
    # deterministic work sat behind the slowest possible gate.
    #
    # Now the prose is an OVERLAY. Every measure becomes a verified query here; where a
    # description exists it is merged, and where it does not the object says so on
    # `name_provisional` rather than inventing a sentence to fill the field.
    # ONE VERIFIED QUERY PER ANALYST QUERY, which is what a verified query IS.
    #
    # This read H2's MEASURES and emitted one object each. A statement computing eight
    # measures therefore appeared eight times, and a statement computing none did not appear
    # at all: 429 objects carrying 95 distinct statements, against the origin's 472 carrying 377.
    # 355 of the 450 selected shapes — real analyst queries, already admitted and parsed —
    # were simply absent from the layer. Measures are what lane D derives FROM these; they
    # are not what this layer is made of.
    doc = ctx.read(IN, "query_shapes.schema.json")
    # The measures stay, as an index: a shape carries the measures it computes, so a consumer
    # can see what a query measures without re-parsing it.
    by_shape: dict[str, list] = {}
    if (ctx.run / MEASURES).exists():
        for m in (ctx.read(MEASURES, "history_measures.schema.json").get("measures") or []):
            for sid in (m.get("shape_ids") or []):
                by_shape.setdefault(sid, []).append(m)
    # NO DESCRIPTION OVERLAY HERE. Prose is written once, after the merge, over the whole
    # layer (`m2_describe`) — a statement both lanes hold would otherwise be described twice,
    # in two vocabularies, neither written with the other in view. So every object leaves
    # this stage undescribed, with a provisional name.
    clone_map = {}
    if ctx.args.clone_map:
        clone_map = json.loads(pathlib.Path(ctx.args.clone_map).read_text())
        ctx.note_input_path(ctx.args.clone_map)

    cards = []
    for sh in doc["shapes"]:
        ms = by_shape.get(sh["shape_id"]) or []
        # The most-adopted measure speaks for the statement, as everywhere else here.
        lead = sorted(ms, key=lambda x: (-(x.get("users") or 0), -(x.get("exec") or 0)))
        card = {
            "measure_id": sh["shape_id"],
            "sql": sh["sql"],
            "tables": sh.get("tables") or [],
            "aliases": [a for m in ms for a in (m.get("aliases") or [])][:8],
            "formula": "; ".join(m.get("formula", "") for m in ms)[:400],
            "provenance": {"users": sh.get("users"), "exec": sh.get("exec"),
                           "trust_score": sh.get("trust_score"),
                           "adoption": sh.get("adoption")},
            "history_ids": {k: sh.get(k) for k in ("query_ids", "n_query_ids",
                                                    "query_ids_digest", "first_seen",
                                                    "last_seen")},
            "measures": [m["measure_id"] for m in ms],
            "observed_values": sh.get("observed_values"),
            # The table the measure is taken over: the lead measure's entity, else the
            # statement's first table. The fallback prose names it.
            "entity": ((lead[0].get("entity") if lead else "")
                       or (sh.get("tables") or [""])[0]),
            "described": False,
        }
        cards.append(card)
    skipped = 0

    # THE HISTORY LANE IS WITNESSED TOO. A history statement is the analyst's own text and ran
    # on the estate once — but not necessarily against the substrate this layer will be served
    # from, and a statement nobody re-checked was shipping as `parse_only` whatever the run's
    # `witness_mode` said. Same contract as A4: parse by default, explain/execute on request,
    # and a missing substrate never aborts the run — it records `skipped_no_substrate`.
    mode = ri.get("witness_mode") or "parse"
    limit = ctx.policy.get("witness.limit_rows")
    sample = ctx.policy.get("witness.sample_rows")
    id_cap = int(ctx.policy.get("history_corpus.query_ids_per_shape"))
    eng = None
    meter = _access.Meter(STAGE)
    if whgate.allows_warehouse(mode):
        try:
            eng = _runtime.sql_runner(ri, dialect)
            eng.connect()
        except Exception as exc:
            eng = None
            if meter.connect_failed(exc):
                ctx.say(f"h5: the warehouse refused this identity "
                        f"({meter.run_wide['code']}); every statement is checked by parsing "
                        "only and the run continues.")
            else:
                ctx.say(f"h5: warehouse unreachable ({type(exc).__name__}); every statement "
                        f"records skipped_no_substrate and the run continues.")
    else:
        ctx.say("h5: " + (whgate.degrade(mode, STAGE, "live execution") or ""))

    # NAMES MUST BE UNIQUE PER WORKSPACE AND KIND — the registry enforces it with an index,
    # and a collision is refused for the whole batch. Two analyst queries legitimately compute
    # the same measure over different populations, so with names derived from the estate's own
    # column alias collisions are expected rather than exceptional. Disambiguated by the
    # object's own identity, which is stable across runs, so a re-run does not renumber them.
    seen_names: dict[str, int] = {}
    objects, n_parse_fail, n_ids_cut = [], 0, 0
    try:
        objects, n_parse_fail, n_ids_cut = _emit(ctx, cards, clone_map, dialect, mode, eng,
                                                 meter,
                                                 limit, sample, id_cap, seen_names)
    finally:
        if eng is not None:
            eng.close()

    layer = {
        "contract_version": "1.0",
        "layer_sha": hashlib.sha256(
            json.dumps([o["vq_id"] for o in objects], sort_keys=True).encode()).hexdigest()[:12],
        "dialect": dialect,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "scope": ri["scope"],
        "profile": "history-only",
        "objects": objects,
    }
    ctx.write(OUT, layer, "verified_queries.schema.json")
    found = meter.blockers()
    if found:
        _access.record(ctx.run, found)
        ctx.entry.outputs.append(str(ctx.run / _access.REPORT))
        for b in found:
            n = len((b.get("affects") or {}).get("statements") or [])
            ctx.say(f"h5: WARNING access [{b['code']}] {b['not_read']}: {n} statement(s) "
                    f"unverified; ask: {b['ask']['what']}")
    tally = Counter(o["witness"]["result"] for o in objects)
    labels = Counter(o["label"] for o in objects)
    ctx.count(objects=len(objects), skipped_pending_ruling=skipped,
              parse_failed=n_parse_fail, clone_rewrite=bool(clone_map),
              filters=sum(len(o["filters"]) for o in objects),
              exclusions=sum(1 for o in objects for f in o["filters"] if f.get("negated")),
              without_value=sum(1 for o in objects for f in o["filters"]
                                if f.get("value") is None),
              witness_mode=mode, query_ids_truncated=n_ids_cut,
              with_query_ids=sum(1 for o in objects
                                 if o["provenance"]["history"].get("query_ids")),
              **{f"verdict_{k}": v for k, v in tally.items()},
              **{f"label_{k}": v for k, v in labels.items()})
    ctx.say(f"h5: {len(objects)} verified query/queries; {skipped} pending_ruling NOT served. "
            f"{n_parse_fail} statement(s) do not parse.")
    ctx.say(f"h5: witness mode={mode}; "
            f"{', '.join(f'{k}={v}' for k, v in sorted(tally.items())) or 'nothing'}; labels "
            f"{', '.join(f'{k}={v}' for k, v in sorted(labels.items())) or 'none'}.")
    if n_ids_cut:
        ctx.say(f"h5: {n_ids_cut} statement(s) carry only the first {id_cap} of their logged "
                f"query ids; the full count and digest are on provenance.history.")
    if not clone_map:
        ctx.say("h5: no --clone-map given, so database names are carried through verbatim. "
                "If this estate serves a clone, the statements will name the production "
                "database and will not run.")


def _one_name_per_table(names: set[str]) -> list[str]:
    """Each table once, by its most qualified name. A statement that writes `SCHEMA.TABLE`
    against a bound of `CATALOG.SCHEMA.TABLE` names one table, not two; listing both made
    every such statement look like a two-table read."""
    out = sorted(names)
    return [n for n in out if not any(o != n and o.endswith("." + n) for o in out)]


def _domain_of(tables: list[str]) -> str:
    """The warehouse schema the statement reads, verbatim: `db.schema` for a three-part name,
    `schema` for a two-part one (where the engine's catalog is implicit)."""
    best = max(tables, key=lambda t: t.count("."), default="")
    parts = best.split(".")
    if len(parts) >= 3:
        return ".".join(parts[-3:-1]).lower()
    return parts[0].lower() if len(parts) == 2 else "unknown"


def _emit(ctx, cards, clone_map, dialect, mode, eng, meter, limit, sample, id_cap,
          seen_names):
    """One verified query per card, witnessed, with its verdict recorded as a decision."""
    objects, n_parse_fail, n_ids_cut = [], 0, 0
    minted: dict[str, str] = {}
    for card in cards:
        sql = _to_clone(card.get("sql") or "", clone_map, dialect)

        try:
            tree = sqlglot.parse_one(sql, read=dialect)
        except Exception:
            tree = None
            n_parse_fail += 1

        feat = S.extract_feat(tree, []) if tree is not None else {}
        declared = card.get("tables") or card.get("base_tables") or []
        tables = [_to_clone(t, clone_map, dialect) for t in declared]
        # THE STATEMENT'S OWN TABLES WIN WHERE IT PARSED. A declared list is a summary, and a
        # statement that reads four tables while listing one cannot be scoped.
        #
        # This used to read `feat.get("tables")`, and `extract_feat` has never returned that
        # key — so the branch never once executed and every object was scoped by whatever the
        # card happened to declare. Carried over from `e6_import` with its comment intact,
        # which is how a claim outlives the code that made it true. Read off the tree here.
        if tree is not None:
            # `sqlshape.extract_tables` rather than a second walk here: it already excludes
            # CTE names and keeps a qualified reference that merely SHARES a CTE's name,
            # which a naive walk drops.
            parsed = {_to_clone(t.upper(), clone_map, dialect) for t in S.extract_tables(tree)}
            if parsed:
                tables = _one_name_per_table({*(t.upper() for t in tables), *parsed})

        vq = "h-" + hashlib.sha256(
            # IDENTITY IS THE STATEMENT'S, NOT THE PROSE'S. Keying on an authored name would
            # give the same statement a new vq_id the moment someone reworded it, and a
            # re-run would then append a duplicate instead of revising in place.
            #
            # NOR ITS PLACE IN THE RUN. This used to key on the shape id too, which is the
            # shape's position in its family (`h-<family>-<i>`): one statement added to the
            # log renumbered every shape after it, so statements that had not changed got
            # new ids — their answers no longer matched and the registry was sent them as
            # new artifacts. The statement and the tables it reads are what it is.
            (_norm(sql) + "|" + ",".join(sorted(tables))).encode()).hexdigest()[:16]
        # H1 keeps one shape per skeleton (tables and structure), so two cards never carry
        # the same statement over the same tables. Should they, two objects would share an
        # id and one would silently replace the other: stop and say which.
        if vq in minted:
            raise SystemExit(f"h5: shapes {minted[vq]} and {card['measure_id']} carry the same "
                             f"statement over the same tables ({vq}); H1 should have kept one.")
        minted[vq] = card["measure_id"]
        # SENTENCES, NOT A CONCATENATION. Joined on a bare space these four fields read as
        # "Distinct contacts registered for a campaign the campaign's final progression list
        # count of distinct contacts" — one run-on that a person skims past and a retriever
        # tokenises no better for.
        # NOTHING INVENTED. Undescribed, the object still has to say what it is, so it says
        # only what the parser knows: the measure and the table it is taken over.
        over = str(card.get("entity") or "").split(".")[-1].lower().replace("_", " ")
        prose = (f"{_humanise(card)}" + (f" over {over}." if over else ".")
                 + " No description has been written for this measure yet.")
        # THE VERDICT THIS STATEMENT EARNED, and the label that follows from it. The label
        # used to read a `witness` key the card never had, so every history statement shipped
        # as `parse_only` however it had been checked.
        t0 = time.monotonic()
        if meter.run_wide is not None and whgate.allows_warehouse(mode):
            # The warehouse already refused this identity for everything: not asked again.
            verdict = _witness.witness(sql, dialect, "parse")
            if verdict["result"] == "parse_ok":
                verdict = _witness.blocked(_sockets.AccessBlocked([meter.run_wide]))
            meter.skipped(vq)
        else:
            verdict = _witness.witness(sql, dialect, mode, eng=eng, limit=limit,
                                       sample_rows=sample, blockers=meter.sink)
            meter.seen(vq)
        verdict["mode"] = verdict.get("mode") or mode
        label = _witness.label_for(verdict, flagged=False)
        ctx.decide("witness", vq, verdict["result"],
                   because=f"history statement witnessed under witness_mode {mode}; "
                           f"label {label}",
                   policy_key="witness_mode", policy_value=mode,
                   evidence={"mode": mode, "engine": dialect if eng is not None else None,
                             "error": verdict.get("error"), "rows": verdict.get("rows"),
                             "hash": verdict.get("hash"),
                             "duration_ms": int((time.monotonic() - t0) * 1000),
                             "shape_id": card["measure_id"]})
        # The logged executions this statement came from, capped by policy with the cap
        # visible: `n_query_ids` beside a shorter list says the list was cut, never hides it.
        hist_ids = dict(card.get("history_ids") or {})
        hist_ids["query_ids"] = list(hist_ids.get("query_ids") or [])[:id_cap]
        hist_ids["n_query_ids"] = int(hist_ids.get("n_query_ids") or 0)
        hist_ids["query_ids_truncated"] = hist_ids["n_query_ids"] > len(hist_ids["query_ids"])
        n_ids_cut += int(hist_ids["query_ids_truncated"])
        objects.append({
            "vq_id": vq,
            "name": _unique(slugs.artifact_name("vq", card.get("intent") or card.get("name")
                                                or _humanise(card)), vq, seen_names),
            # The card's intent IS the question an analyst asked. It is what retrieval matches.
            "question": (card.get("intent") or card.get("name")
                         or _humanise(card))[:500],
            "description": prose[:2000],
            "sql": sql,
            "domain": _domain_of(tables),
            "tables": tables,
            "joins": feat.get("joins") or [],
            # The layer declares column/op/value/stage/negated/value_kind; `is_identity` is
            # a B-stage concern and the schema rejects it here.
            "filters": [{k: v for k, v in f.items() if k != "is_identity"}
                        for f in (feat.get("filters") or [])],
            "group_by": feat.get("group_by") or [],
            "grain": card.get("grain") or "unstated",
            # Said on the object, because a consumer must be able to tell a grain nobody has
            # stated from one that was considered and found to be per-row.
            "name_provisional": not card.get("described"),
            "klass": _klass(card, tree) if tree is not None else "lookup",
            "aliases": [x for x in ([card.get("name")] + list(card.get("aliases") or []))
                        if x][:8],
            # The decision the card encodes, and what it excludes, are the two things a
            # consumer most needs and cannot recover from the SQL alone.
            # A CARD'S EXCLUSIONS ARE PROSE AS OFTEN AS THEY ARE A LIST. 328 of 446 cards
            # write them as one semicolon-delimited sentence, and `list()` over a string
            # yields its CHARACTERS — so the gotchas of those objects read "I", "S", "_",
            # "D". Split a string on its delimiter; take a list as it stands.
            "gotchas": [x for x in ([card.get("decision")] + _as_notes(card.get("exclusions")))
                        if x][:8],
            "variants": [],
            "metrics": [card.get("name") or card["measure_id"]],
            "label": label,
            "provenance": {
                "lane": "history",
                "bi": None,
                "history": {"shape_key": vq[3:],
                            "n_exec": (card.get("provenance") or {}).get("exec"),
                            "n_distinct_users": (card.get("provenance") or {}).get("users"),
                            "adoption": (card.get("provenance") or {}).get("adoption"),
                            **hist_ids},
                "evidence_citations": [
                    {"claim": "certified from this estate's query history by lane H",
                     "source": "query-log",
                     "detail": f"measure {card['measure_id']}, "
                               f"described={card.get('described')}, "
                               f"trust {(card.get('provenance') or {}).get('trust_score')}"}],
                "ranking_basis": "composite adoption trust (h1): "
                                 f"users={(card.get('provenance') or {}).get('users')}, "
                                 f"exec={(card.get('provenance') or {}).get('exec')}"
            },
            "witness": verdict,
            "observed_values": card.get("observed_values"),
        })
    return objects, n_parse_fail, n_ids_cut


def main() -> None:
    stage.main(STAGE, "h5_verified.py", __doc__.splitlines()[0], body, extra=_extra)


if __name__ == "__main__":
    main()
