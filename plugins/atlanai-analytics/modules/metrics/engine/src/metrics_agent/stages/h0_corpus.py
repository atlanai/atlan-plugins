# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""H0 — build the query-history corpus from the history plugs the run lists.

THE HISTORY IS READ THROUGH ITS PLUGS, AND ONLY THEM. S0 records the history plugs run.yml
lists, each with its settings; this stage asks each one for its query records
(`query_history`), validated at the boundary, and folds them into ONE corpus in the order
listed. Whether a plug reads a log file, a warehouse's shape table or a catalogue's pull is the
plug's business: the core reads query records (AGENT_GUARDRAILS rule 22).

PROVENANCE. Copied (never imported) from an earlier benchmark tool's corpus builder: the same
admission and the same sqlglot feature extraction (`lib/corpus.py`, checked feature-for-feature
against the original on identical input), so the agent builds its own corpus and depends on
no other repository.

What it deliberately left behind: hard-coded constants (now policy, rule 5), local paths and
dotfiles, the notion of a "sealed question" (an evaluation's concern, supplied here as an
input), and single-line flattening of every statement — see below.

Admission, in this order — the cheap string prunes before the parse, because parsing
every row of a warehouse-scale shape table to then discard most of it is the difference between
minutes and hours:

    length cap -> mentions a bounded table -> not a tool preview -> not automation
    -> no harvest signature -> parses -> is a pure read -> actually READS a bounded table
    -> not excluded

THE STATEMENT IS STORED AS THE ANALYST WROTE IT. Flattened to one line, a statement loses its
newlines and an inline `--` comment comments out the rest of it. Rule 17 forbids it, the flattened form is still used for the
dedup and family keys so selection is unchanged, and the consequence is that a downstream stage
no longer needs a side file of recovered SQL to undo damage this stage did.

`--exclude-sql` is how an evaluation keeps its own answers out of a corpus mined from the same
warehouse. It is an INPUT, not a built-in: excluding a benchmark's goldens is a property of that
benchmark, and an agent that carried the notion would be carrying one harness's concerns into
every estate.
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib


from metrics_agent import plug_runtime as _runtime  # noqa: E402
from metrics_agent.lib import artifacts, corpus as C, pii as _pii, stage
from metrics_agent import observe as _observe  # noqa: E402

STAGE = "h0_corpus"
OUT = "query_corpus.jsonl"


def _extra(p) -> None:
    p.add_argument("--exclude-sql", default=None,
                   help="optional JSON list of statements to keep OUT of the corpus (an "
                        "evaluation's own answers). Verbatim matches and near-duplicates.")


def _ids_block(ids, cap: int, first_seen=None, last_seen=None) -> dict:
    """The query ids a shape was executed as, CAPPED BUT NEVER LOSSY ABOUT IT.

    A popular shape can carry tens of thousands of executions and the corpus is streamed, so
    every id cannot ride on every row. The first `cap` (sorted, so a re-run keeps the same
    ones) are kept verbatim; the count and a SHA-256 over the FULL sorted list are kept beside
    them, so a reader can tell a complete list from a truncated one and check either against
    the log. A block that says `query_ids: []` with `n_query_ids: 0` is an honest "the source
    named no ids", not a missing field.
    """
    full = sorted({str(i) for i in ids if i not in (None, "")})
    return {"query_ids": full[:cap], "n_query_ids": len(full),
            "query_ids_sha256": hashlib.sha256("\n".join(full).encode()).hexdigest()
                                if full else None,
            "first_seen": first_seen, "last_seen": last_seen}


def body(ctx: stage.Ctx) -> None:
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    dialect = ri["dialect"]
    # NOT GATED ON `witness_mode`, deliberately. That flag governs whether a STATEMENT is
    # execution-verified, and refusing here would have made mining the history conditional on
    # intending to witness it — two different permissions, and a run that only wants a
    # verified-query layer needs the first without the second. What protects this read is the
    # engine's own gate: `Engine.fetch` refuses anything that is not a pure read, and that is
    # never weakened. A run with no warehouse supplies `--corpus` to H1 instead and skips H0.
    if not _runtime.served(ri, "history"):
        raise SystemExit("h0: run_inputs records no history plug. The history is read through "
                         "the plugs S0 recorded from run.yml (`inputs.history`); re-run S0 "
                         "with one.")

    bounded = (ri.get("bounded_tables") or {}).get("tables") or []
    if not bounded:
        raise SystemExit("h0: run_inputs names no bounded tables. The corpus is scoped to the "
                         "estate; without a bound every query in the warehouse is in scope.")
    by_st, by_t, bare = C.bounded_index(bounded)

    # SCRUBBED BEFORE ANYTHING IS KEPT (guardrail 8). A query log is the customer's own
    # text, and an email or a UUID in a literal is a person or a customer id: it is replaced
    # in place, here, so no artifact of this lane ever holds it. Never capped: redaction
    # leaves a statement that parses, a cut does not.
    scrubber = _pii.Scrubber(ctx.policy)
    exclude_norm, exclude_by_table = set(), collections.defaultdict(list)
    if ctx.args.exclude_sql:
        payload = json.loads(pathlib.Path(ctx.args.exclude_sql).read_text())
        ctx.note_input_path(ctx.args.exclude_sql)
        for item in payload:
            sql = item if isinstance(item, str) else (item or {}).get("sql")
            if not sql:
                continue
            sql = scrubber.text(sql)
            exclude_norm.add(C.normalise(sql))
            toks = C.tokens(C.flatten(sql))
            tabs = (item or {}).get("tables") if isinstance(item, dict) else None
            for tb in (tabs or bounded):
                exclude_by_table[str(tb).upper()].append(toks)

    max_chars = int(ctx.policy.get("history_corpus.max_sql_chars"))
    auto_users = int(ctx.policy.get("history_corpus.automation.max_users"))
    auto_exec = int(ctx.policy.get("history_corpus.automation.min_exec"))
    neardup = float(ctx.policy.get("history_corpus.near_duplicate_min"))
    id_cap = int(ctx.policy.get("history_corpus.query_ids_per_shape"))
    unattributed_automation = ctx.policy.get("history_corpus.automation.unattributed")
    scope_match = str(ctx.policy.get("querylog.scope.match"))
    # The statements a tool issues itself, by the markers the installed plugs declare.
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    markers = _plugs.issued_markers()
    if scope_match not in ("any", "all"):
        raise SystemExit(f"h0: querylog.scope.match={scope_match!r}; expected any | all")
    if unattributed_automation not in ("apply", "skip"):
        raise SystemExit(f"h0: history_corpus.automation.unattributed="
                         f"{unattributed_automation!r}; expected apply | skip")
    # What the source said about WHO ran each statement. Filled in by the reader, because only
    # the reader sees the raw rows: `attributed` is True the moment one row names a user or
    # carries a user count.
    source = {"attributed": False, "rows_with_user": 0, "rows_with_id": 0}

    # Records folded into shapes here: whatever the history plugs return.
    folded = True
    several = len(_runtime.served(ri, "history")) > 1
    cross_plug = collections.Counter()

    def _from_plugs(pairs):
        """THE RECORDS AGGREGATED INTO SHAPES — which is what a shape table already holds.

        One row per execution says nothing about adoption; the same question asked by six
        analysts is six rows. Grouping on the normalised statement recovers the two signals
        the whole ranking rests on: how many distinct people ran it, and how often.
        """
        agg: dict[str, dict] = {}
        # Through the socket: every history plug's records in the order listed, each with its
        # plug, validated one at a time.
        for plug, rec in pairs:
            sql = rec.query_text
            if rec.execution_status.upper() not in ("SUCCESS", "OK", ""):
                continue        # a statement that errored is not evidence of anything
            if rec.query_type.upper() != "SELECT":
                continue
            # Keyed on the session database too: the same text run in prod and in a
            # benchmark copy is two pieces of evidence, and merging them would file both
            # under whichever database was seen first.
            k = (C.normalise(sql), str(rec.catalog or "").upper())
            # ONE CORPUS FROM SEVERAL PLUGS. Two sources over the same warehouse see the same
            # executions; a statement an earlier plug already supplied is not counted again.
            if k in agg and agg[k]["plug"] != plug:
                cross_plug[plug] += 1
                continue
            # The statement's SESSION database/schema, when the source records it (a
            # lakehouse pull does). It is what tells `SELECT … FROM MARTS.T` run in a
            # benchmark copy apart from the same text run against prod.
            e = agg.setdefault(k, {"sql": sql, "users": set(), "n": 0, "pre_users": 0,
                                   "db": rec.catalog, "schema": rec.schema,
                                   "ids": set(), "first": None, "last": None,
                                   "plug": plug})
            # THE EXECUTIONS KEEP THEIR NAMES. Folding the log into shapes used to keep the
            # statement and two counts, so no artifact downstream could name the logged
            # queries a verified statement came from.
            if rec.query_id:
                e["ids"].add(rec.query_id)
                source["rows_with_id"] += 1
            ts = rec.start_time
            if ts:
                e["first"] = ts if e["first"] is None else min(e["first"], ts)
                e["last"] = ts if e["last"] is None else max(e["last"], ts)
            # TWO ROW SHAPES. A raw log has one row per EXECUTION and names its user; a
            # pre-aggregated log (a lakehouse pull, an earlier project's `exec`/`users`
            # contract) has one row per distinct statement and carries the counts. Counting
            # a pre-aggregated row as one execution would invent adoption; so its own counts
            # are summed, and a lakehouse row's honest 0 stays 0.
            if rec.pre_aggregated:
                e["n"] += rec.exec_count or 0
                e["pre_users"] = max(e["pre_users"], rec.user_count or 0)
                if rec.user_count:
                    source["rows_with_user"] += 1
            else:
                e["n"] += 1
                if rec.user_name:
                    e["users"].add(rec.user_name)
                    source["rows_with_user"] += 1
        source["attributed"] = source["rows_with_user"] > 0
        for e in agg.values():
            yield (e["sql"], e["n"], max(len(e["users"]), e["pre_users"]), e["db"], e["schema"],
                   _ids_block(e["ids"], id_cap, e["first"], e["last"]), e["plug"],
                   tuple(sorted(e["users"])))

    def reader():
        return _from_plugs(_runtime.history_by_plug(ri))
    for e in _runtime.served(ri, "history"):
        for path in _runtime.input_paths(e, "history"):
            ctx.note_input_path(path)

    seen = kept = n_truncated = n_redacted = n_named = 0
    dropped: collections.Counter = collections.Counter()
    out_path = ctx.run / OUT
    check = artifacts.line_checker("query_corpus.schema.json", what=OUT)
    with out_path.open("w") as fh:
        for rec in reader():
            sql, n_exec, n_users, sess_db, sess_schema, ids, plug, names = rec
            seen += 1
            clean = scrubber.text(sql or "")
            n_redacted += int(clean != (sql or ""))
            sql = clean
            # NOBODY NAMED, SO "FEW PEOPLE" IS UNKNOWN. The automation gate reads few users
            # running something very often as a scheduled job; on a log that names no user at
            # all every row has zero users, and the gate would then drop every statement run
            # more than `min_exec` times — the most-used queries in the estate. Policy decides
            # whether the gate still applies. Known only once the raw log has been folded,
            # which is why the querylog reader settles `attributed` before its first yield.
            # A shape table is aggregated already and carries its user count by contract, so
            # A pre-aggregated record carries its user count by contract.
            gate_users = (0 if folded and not source["attributed"]
                          and unattributed_automation == "skip" else auto_users)
            row, why = C.admit(
                sql, n_users, n_exec, bare=bare, by_schema_table=by_st, by_table=by_t,
                dialect=dialect, max_sql_chars=max_chars, auto_max_users=gate_users,
                auto_min_exec=auto_exec, exclude_norm=exclude_norm,
                exclude_tokens_by_table=exclude_by_table, neardup_min=neardup,
                default_db=sess_db, default_schema=sess_schema, scope_match=scope_match,
                issued_markers=markers)
            if row is None:
                dropped[why] += 1
                continue
            row.update(ids)
            if scrubber.keep_user_names and names:
                # WHO ran it, kept in the run directory only (pii.redact_user_names: false):
                # how an operator finds a metric's owners. Never published — see lib/pii.
                row[_pii.NAMES_KEY] = list(names)
                n_named += 1
            if folded:
                row["source_user_attributed"] = bool(source["attributed"])
            if several and plug:
                row["plug"] = plug
            n_truncated += int(ids["n_query_ids"] > len(ids["query_ids"]))
            kept += 1
            check(row, kept)
            fh.write(json.dumps(row) + "\n")

    ctx.entry.outputs.append(str(out_path))
    # Written line by line, not through ctx.write, so it reports itself here: by shape and
    # digest (a corpus does not fit in a span), so the trace is still complete.
    _observe.get().artifact(OUT, out_path, None, stage_name=ctx.entry_stage)
    ctx.count(scanned=seen, kept=kept, user_attributed=bool(source["attributed"]),
              rows_with_query_id=source["rows_with_id"], query_ids_cap=id_cap,
              query_ids_truncated=n_truncated, pii_redacted=n_redacted,
              user_names_kept=n_named,
              **{f"dropped_{k}": v for k, v in dropped.items()},
              **({"history_plugs": len(_runtime.served(ri, "history")),
                  "cross_plug_duplicates": sum(cross_plug.values())} if several else {}))
    ctx.say(f"h0: scanned {seen:,} shape(s), kept {kept:,}.")
    if several:
        ctx.say("h0: ONE corpus from " + ", ".join(_runtime._names(ri, "history"))
                + f" in that order; {sum(cross_plug.values()):,} record(s) of a statement an "
                "earlier plug already supplied were not counted again"
                + (" (" + ", ".join(f"{p}={n:,}" for p, n in sorted(cross_plug.items())) + ")"
                   if cross_plug else "") + ".")
    ctx.say("h0: dropped — " + ", ".join(f"{k}={v:,}" for k, v in dropped.most_common()))
    if not source["attributed"]:
        ctx.say("h0: the source names NO user on any row — an UNATTRIBUTED log. Adoption "
                "cannot be counted, only executions; "
                + ("the automation gate was NOT applied (history_corpus.automation."
                   "unattributed: skip)." if folded
                   and unattributed_automation == "skip" else
                   "the automation gate was applied as usual, so every statement run more "
                   "than "
                   f"{auto_exec} times was dropped.")
                + " H1 admits these shapes under history_shapes.admission.unattributed.")
    if not source["rows_with_id"]:
        ctx.say("h0: the source carries no query id, so no verified query will be able to "
                "name the logged executions it came from.")
    if n_truncated:
        ctx.say(f"h0: {n_truncated} shape(s) ran as more than {id_cap} logged queries; each "
                f"keeps the first {id_cap} ids, the full count and a SHA-256 over the full list "
                f"(history_corpus.query_ids_per_shape).")
    if not kept:
        raise SystemExit("h0: nothing survived admission. Check the history plugs' settings "
                         "and the bounded tables; an empty corpus is a configuration error, "
                         "not an estate with no queries.")


def main() -> None:
    stage.main(STAGE, "h0_corpus.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
