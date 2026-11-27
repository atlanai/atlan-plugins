#!/usr/bin/env python3
"""scope.py: the use case and the metric-aware question pool for an eval set.

The analytics run already holds what an eval set needs to be about something: its use case
(`interaction.json`), the metrics built for it (`context/metrics.json`), and every dashboard
tile and history shape the engine verified, each linked to the metrics it computes
(`context/queries.json`, `engine/metric_cards.json`). This script turns that into eval-builder
input, picks the set (the focus metric first, then breadth) and writes it. Standard library only.

    python3 scope.py inputs --project <project> [--run <run-dir>] [--use-case "<text>"]
    python3 scope.py pick --gen <gen-dir> [--limit 20] [--spare 4] [--focus-count 2]
    python3 scope.py emit --gen <gen-dir> --name <set name>
    python3 scope.py ready --set <set.jsonl>

`emit` writes <project>/.atlan-analytics/evals/sets/<name>.jsonl, one question per line: {"id", "question",
"sql", "tests", "from", "generated"}, the same shape as a set a person brings, so whatever runs a set runs
this one. Every command prints one JSON object. Exit 2 means something the person must supply.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "analytics-build" / "scripts"))
import python_version  # noqa: E402

python_version.require()

from eval_runs import Blocked, built_runs, engine_dir, execution, find_run, question_counts, set_key, use_case  # noqa: E402
import layout  # noqa: E402

# Words that say nothing about which metric a use case needs.
_STOP = {"a", "an", "and", "the", "of", "to", "in", "on", "for", "by", "or", "is", "it", "its", "with",
         "which", "what", "why", "how", "that", "this", "build", "agent", "answers", "answer", "each",
         "per", "as", "at", "from", "be", "are", "was", "were", "their", "they", "not", "all", "one"}


def load(path: pathlib.Path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def dump(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def gen_dir(project: pathlib.Path, run: pathlib.Path) -> pathlib.Path:
    """Where a set is drafted, outside the run: answering agents read the run."""
    return layout.evals(project) / "gen" / run.name


def cmd_inputs(a) -> dict:
    project = pathlib.Path(a.project)
    run = find_run(project, a.run)
    ev = gen_dir(project, run)
    interaction = load(run / "interaction.json", {}) or {}
    intent = use_case(run, a.use_case or "")
    if not (intent.get("domain") or intent.get("purpose")):
        raise Blocked("The run records no use case. When the conversation does not say, ask once: \"What should "
                      "your agent answer, and for whom? One line is enough.\" Pass the reply with --use-case.")
    metrics = load(run / "context" / "metrics.json", []) or []
    queries = load(run / "context" / "queries.json", []) or []
    engine = engine_dir(run)
    cards = load(engine / "metric_cards.json", {}) if engine else {}
    if not metrics or not queries:
        raise Blocked("Your agent has no metrics or saved queries yet. Once I build them, I can write questions to test it.")
    if not (cards or {}).get("metrics"):
        raise Blocked("I can't tie questions to your agent's metrics here. Bring your own questions instead: one "
                      "question and its SQL per line, in .atlan-analytics/evals/sets/.")
    if ev.exists():
        shutil.rmtree(ev)
    refs_by_vq = cards.get("metric_refs_by_vq") or {}
    names = {c.get("metric_ref"): c.get("display_name") or c.get("name") for c in cards.get("metrics") or []}
    focus = (interaction.get("focus") or "").removeprefix("metric:")
    focus_ref = next((r for r, n in names.items() if n == focus), None)

    pool, rows = {}, []
    for q in queries:
        ext = ((q.get("definition") or {}).get("extensions") or {}).get("metrics_agent") or {}
        evid = ext.get("evidence") or {}
        vq = ext.get("vq_id") or evid.get("vq_id")
        sql = (q.get("definition") or {}).get("sql")
        if not vq or not sql:
            continue
        prov = evid.get("provenance") or {}
        hist = prov.get("history") or {}
        lane = "tile" if prov.get("lane") == "bi" else "history"
        pool[vq] = {"source": lane, "metrics": refs_by_vq.get(vq, []),
                    "runs": hist.get("n_exec"), "tile": ((prov.get("bi") or {}).get("visual"))}
        rows.append({"id": vq, "sql": sql, "dialect": (q.get("definition") or {}).get("dialect"),
                     "exec": hist.get("n_exec") or (1 if lane == "history" else None),
                     "users": hist.get("n_distinct_users")})
    src = ev / "src"
    src.mkdir(parents=True)
    (src / "pool.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for schema in ("database-schema.json", "table-schema.json"):  # a local build, a warehouse build
        if (run / "context" / schema).is_file():
            shutil.copy(run / "context" / schema, src / schema)
    tables = sorted({t for m in metrics for t in _tables(m)})
    text = {c.get("metric_ref"): f"{c.get('display_name') or ''} {c.get('description') or ''}"
            for c in cards.get("metrics") or []}
    want = _words(f"{intent.get('domain') or ''} {intent.get('purpose') or ''}")
    scope = {"run": str(run.resolve()), "use_case": intent, "focus": focus_ref,
             "metrics": [{"ref": r, "name": n, "relevance": len(want & _words(text.get(r, "")))}
                         for r, n in names.items()],
             "related": _related(engine, focus_ref), "pool": pool}
    dump(ev / "scope.json", scope)
    dialect = next((r["dialect"] for r in rows if r.get("dialect")), None) or execution(run).get("dialect") \
        or execution(run).get("kind")
    return {"status": "ok", "run": str(run), "gen": str(ev), "use_case": intent, "focus": names.get(focus_ref) or None,
            "pool": len(rows), "tiles": sum(1 for p in pool.values() if p["source"] == "tile"),
            "dialect": dialect, "tables": tables, "src": str(src), "runtime": str(layout.evals(project) / "runtime")}


def _words(text: str) -> set:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2}


def _tables(metric: dict) -> list:
    entity = ((metric.get("definition") or {}).get("source") or {}).get("entity")
    return [entity] if entity else []


def _related(engine: pathlib.Path, focus_ref) -> list:
    """Metrics one edge away from the focus, nearest first."""
    edges = (load(engine / "metric_relationships.json", {}) or {}).get("edges") or []
    out = []
    for e in sorted(edges, key=lambda e: -(e.get("confidence") or 0)):
        for a, b in ((e.get("from_ref"), e.get("to_ref")), (e.get("to_ref"), e.get("from_ref"))):
            if a == focus_ref and b and b not in out:
                out.append(b)
    return out


_SQL_NOISE = re.compile(r"""'(?:[^']|'')*'|--[^\n]*|/\*[\s\S]*?\*/""")
_AGGREGATE = re.compile(r"\b(count|count_if|sum|avg|min|max|median|mode|stddev\w*|var\w*|approx_\w+|percentile_\w+|"
                        r"listagg|array_agg|string_agg|any_value|div0)\s*\(|\bgroup\s+by\b|\bdistinct\b", re.I)
_PROBE = re.compile(r"\bwhere\s+(false|1\s*=\s*0)\b|\blimit\s+0\b", re.I)
# A data test (dbt's wrapper names its result `failures` and `should_warn`) and a freshness check
# (the latest load time of a table) watch the pipeline, not the business: no person asks them.
_DATA_TEST = re.compile(r"\bshould_(warn|error)\b|\bas\s+failures\b", re.I)
_FRESHNESS = re.compile(r"\bmax\s*\(\s*(\w+\.)?(_loaded_at|_fivetran_synced|_airbyte_extracted_at|_sdc_batched_at|"
                        r"_etl_loaded_at|_dbt_updated_at|loaded_at|inserted_at)\s*\)", re.I)


def unfit(sql: str):
    """Why a query cannot anchor a question, or None. A statement that returns no row by design
    is a probe; one with no aggregate lists records, and no one-line question reproduces a list;
    a data test or a freshness check watches the pipeline, and no person asks it."""
    if not (sql or "").strip():
        return None  # nothing to read: the engine's own gate decides
    bare = _SQL_NOISE.sub(" ", sql or "")
    if _PROBE.search(bare):
        return "a test query that returns nothing"
    if _DATA_TEST.search(bare):
        return "a data test, not a business question"
    if _FRESHNESS.search(bare):
        return "a freshness check on when tables last loaded"
    if not _AGGREGATE.search(bare):
        return "lists records, with no total or count"
    return None


def cmd_pick(a) -> dict:
    ev = pathlib.Path(a.gen)
    work = ev / "work"
    scope = load(ev / "scope.json")
    cands = load(work / "candidates.json")
    if not scope or cands is None:
        raise Blocked("Run `inputs`, then eval-builder `build`, before `pick`.")
    skipped = [{"id": c["id"], "why": unfit(c.get("sql"))} for c in cands if unfit(c.get("sql"))]
    cands = [c for c in cands if not unfit(c.get("sql"))]
    want = a.limit + max(0, a.spare)  # spares: a draft can find a question unanswerable
    pool = scope["pool"]
    names = {m["ref"]: m["name"] for m in scope["metrics"]}

    def metric_of(c):
        refs = (pool.get(c.get("query_id")) or {}).get("metrics") or []
        return refs[0] if refs else None

    def strength(c):  # a tile first, then how often analysts ran it, then the engine's own score
        p = pool.get(c.get("query_id")) or {}
        return (p.get("source") != "tile", -(p.get("runs") or 0), -sum((c.get("scores") or {}).values()))

    by_metric: dict = {}
    for c in cands:
        m = metric_of(c)
        if m:
            by_metric.setdefault(m, []).append(c)
    for lst in by_metric.values():
        lst.sort(key=strength)
    focus = scope.get("focus")
    relevance = {m["ref"]: m.get("relevance") or 0 for m in scope["metrics"]}
    related = [m for m in scope.get("related") or [] if m in by_metric and m != focus]
    order = ([focus] if focus in by_metric else []) + related + \
        sorted((m for m in by_metric if m != focus and m not in related),
               key=lambda m: (-relevance.get(m, 0), strength(by_metric[m][0])))
    chosen, taken = [], {m: 0 for m in by_metric}
    if focus in by_metric:
        for c in by_metric[focus][:a.focus_count]:
            chosen.append((focus, c))
            taken[focus] += 1
    breadth = [m for m in order if m != focus] + ([focus] if focus in by_metric else [])
    while len(chosen) < want:  # breadth: one per metric per pass, the focus metric last
        added = False
        for m in breadth:
            if len(chosen) < want and taken[m] < len(by_metric[m]):
                chosen.append((m, by_metric[m][taken[m]]))
                taken[m] += 1
                added = True
        if not added:
            break
    # Still short: queries the analysts run that no metric computes yet, the most run first. They
    # test what the context does not define, so a set is not only what it was built from.
    loose = sorted((c for c in cands if not metric_of(c)), key=strength)
    chosen += [(None, c) for c in loose[:max(0, want - len(chosen))]]

    keep = {c["id"] for _, c in chosen}
    answers = load(work / "answers.json", {}) or {}
    missing = sorted(keep - set(answers)) if answers else []
    if missing:
        # An earlier, smaller pick pruned the draft slots: drafting now would leave these unanswered.
        raise Blocked(f"{len(missing)} picked question(s) have no draft slot. Run eval-builder `build` again, then `pick`.")
    dump(work / "candidates.json", [c for c in cands if c["id"] in keep])
    dump(work / "answers.json", {k: v for k, v in answers.items() if k in keep})
    for p in (work / "prompts").glob("*.txt") if (work / "prompts").exists() else []:
        if p.stem not in keep:
            p.unlink()
    selection = [{"id": c["id"], "metric": names.get(m, m) or "", "focus": bool(m) and m == focus,
                  "from": "dashboard tile" if (pool.get(c["query_id"]) or {}).get("source") == "tile" else
                  f"past query, used {(n := (pool.get(c['query_id']) or {}).get('runs') or 1)} time{'' if n == 1 else 's'}"}
                 for m, c in chosen]
    dump(work / "selection.json", selection)
    dump(work / "pick.json", {"limit": a.limit})
    return {"status": "ok", "picked": len(selection), "limit": a.limit, "of": len(cands), "skipped": skipped,
            "metrics_covered": len({s["metric"] for s in selection if s["metric"]}),
            "no_metric": sum(1 for s in selection if not s["metric"]), "selection": selection}


def cmd_emit(a) -> dict:
    """The picked, drafted questions as a set file beside the project's other sets."""
    gen = pathlib.Path(a.gen)
    spec = load(gen / "work" / "spec.json")
    selection = {s["id"]: s for s in load(gen / "work" / "selection.json", []) or []}
    if not spec:
        raise Blocked("No drafted set: run eval-builder `review` first.")
    rows = [{"id": r["id"], "question": r.get("question"), "sql": r.get("golden_sql"),
             "tests": (selection.get(r["id"]) or {}).get("metric") or "",
             "from": (selection.get(r["id"]) or {}).get("from") or "", "generated": True}
            for r in spec.get("records") or []
            if r.get("review_status") != "rejected" and r.get("question") and r.get("golden_sql")]
    if not rows:
        raise Blocked("No question could be kept with SQL that answers it.")
    # In the order they were picked, and no more than asked for: the spares fill in for a draft
    # that found a question unanswerable.
    order = {i: n for n, i in enumerate(selection)}
    rows.sort(key=lambda r: order.get(r["id"], len(order)))
    limit = (load(gen / "work" / "pick.json", {}) or {}).get("limit")
    spare = len(rows) - limit if limit and len(rows) > limit else 0
    rows = rows[:limit] if limit else rows
    name = re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-") or "eval-set"
    out = gen.parents[1] / "sets" / f"{name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    # The questions in the set, in its order: the chip and the side page list these, not the unused spares
    # (a Codex run, 2026-11-26, said "12 test questions written" for a set of 8).
    dump(gen / "work" / "emitted.json", [r["id"] for r in rows])
    scope = load(gen / "scope.json", {}) or {}
    scope["emitted"] = {**question_counts(name, [r["from"] for r in rows]), "set_key": set_key(out, rows)}
    dump(gen / "scope.json", scope)
    return {"status": "ok", "set": str(out), "questions": len(rows), "spares_unused": spare,
            "metrics": sorted({r["tests"] for r in rows if r["tests"]}), **_questions_chip(gen, scope.get("run"))}


def _questions_chip(gen: pathlib.Path, run) -> dict:
    """Written questions are an output: their chip comes with the result, drawn whole and marked shown, and the side
    page lists them. Chips a guide only asked for went undrawn in the dry run (2026-11-25)."""
    import side_view
    if not run:  # Drafts made before inputs recorded the selected run, in <project>/.atlan-analytics/evals/gen.
        matches = [r for r in built_runs(gen.parents[3]) if r.name == gen.name]
        run = matches[0] if len(matches) == 1 else None
    return side_view.handed(side_view.show_card(run, 'questions', questions_dir=gen)) if run else {}


def cmd_ready(a) -> dict:
    """A set file, generated or brought, as the approved spec a save publishes from."""
    src = pathlib.Path(a.set)
    rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    if not rows or not all(r.get("question") and r.get("sql") for r in rows):
        raise Blocked(f"{src.name} needs a question and its SQL on every line.")
    out = src.parents[1] / "saved" / src.stem
    if out.exists():
        shutil.rmtree(out)
    dump(out / "work" / "spec.json", {"name": src.stem, "source_description": f"eval set {src.name}", "records": [
        {"id": str(r.get("id") or f"q{i}"), "question": r["question"], "question_spec": r.get("note") or "",
         "golden_sql": r["sql"], "review_status": "approved", "provenance": {"source": r.get("from") or "eval set"},
         "modes": []} for i, r in enumerate(rows, 1)]})
    return {"status": "ok", "dir": str(out), "questions": len(rows), "slug": src.stem}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    s = p.add_subparsers(dest="cmd", required=True)
    c = s.add_parser("inputs", help="eval-builder input and scope.json from the analytics run")
    c.add_argument("--project", required=True, help="the folder holding .atlan-analytics/")
    c.add_argument("--run", help="the run folder to use, when the project holds several")
    c.add_argument("--use-case", help="which agent, answering what, when the run recorded none")
    c.set_defaults(func=cmd_inputs)
    c = s.add_parser("pick", help="the set: the focus metric first, then breadth")
    c.add_argument("--gen", required=True)
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--spare", type=int, default=4, help="extra questions drafted in case a draft drops some")
    c.add_argument("--focus-count", type=int, default=2)
    c.set_defaults(func=cmd_pick)
    c = s.add_parser("emit", help="write the drafted set to .atlan-analytics/evals/sets/<name>.jsonl")
    c.add_argument("--gen", required=True)
    c.add_argument("--name", required=True)
    c.set_defaults(func=cmd_emit)
    c = s.add_parser("ready", help="a set file as the approved spec a save publishes from")
    c.add_argument("--set", required=True)
    c.set_defaults(func=cmd_ready)
    a = p.parse_args(argv)
    try:
        print(json.dumps(a.func(a), indent=1))
        return 0
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
