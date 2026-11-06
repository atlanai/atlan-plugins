#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A1 — parse the BI model. ONE READER PER TOOL, ONE CONTRACT.

BI tools are not variations on a theme: they disagree about what a metric IS. A DAX measure
is declared once on the model and is already a closure; a Tableau field is a pill on a shelf
whose aggregation is chosen at the use site. A single parser with vendor branches would encode
one tool's model and bolt the others on, so each tool gets its own READER, registered under
the `metrics_agent.readers` entry points (`sockets.BIReader`), and every reader writes
`bi_facts.schema.json`. A1 asks the registry for the reader the run names and never knows which
tools exist. NO STAGE AFTER A1 BRANCHES ON VENDOR.

No LLM in either. A .tmdl file and a .twb are both grammars, and a model that "reads" one
instead of parsing it will hallucinate a column on a long file.

Refuses by name on zero measures rather than writing `measures: []` — an empty measure list
three stages later fails with a much worse message.
"""
from __future__ import annotations

import pathlib
import sys


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import bi_models as _models  # noqa: E402
from metrics_agent.lib import pii as _pii  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402
from metrics_agent.lib import withheld as _withheld  # noqa: E402
from metrics_agent import readers as _readers  # noqa: E402
from metrics_agent import sockets as S  # noqa: E402

STAGE = "a1_parse_bi"
OUT = "bi_facts.json"


# The scrub of what a reader read (guardrail 8), and the numbering of each withheld literal:
# lib/withheld. `scrub_prose` stays importable from here.
PROSE_KEYS = _withheld.PROSE_KEYS
scrub_prose = _withheld.scrub_prose


def _report_empty_usage(ctx: stage.Ctx, reader, model_dir: pathlib.Path,
                        offered: int = 0) -> None:
    """Zero usage is a property of the FETCH, and must be said that way — loudly.

    The design rule: "A report that parses with empty `usage` must be reported as a PROPERTY
    OF THE FETCH, not as a finding about the estate. Make it loud."

    It matters more than it sounds. `a2_question_units` iterates `usage`, so empty usage
    means no question units, no compiled SQL, and NO METRICS from this model — a total,
    silent loss. Left unexplained it reads as "nothing in this estate is on a dashboard".
    Only the reader knows where its files keep their widgets, so it is asked
    (`explain_empty_usage`) what its files say about which case this is. A reader that
    offers one question per measure instead (`units_without_usage`) saves the metrics, not
    the dashboards: it is still zero usage, said as loudly, with what was offered.
    """
    counts, lines = reader.explain_empty_usage(model_dir)
    ctx.count(usage_zero=True, **counts)
    ctx.entry.notes = ("zero usage: no visuals were enumerated for this model. A property "
                       "of the fetch, not a finding about the estate.")

    ctx.count(units_without_usage=offered)
    if offered:
        ctx.say(f"a1: ZERO USAGE for this model. No visuals were enumerated; the reader "
                f"offers one question per measure instead ({offered}), and none of them "
                f"counts as placed on a dashboard.")
    else:
        ctx.say("a1: ZERO USAGE for this model. No visuals were enumerated, so A2 will build "
                "no question units, nothing will be compiled, and this model will produce NO "
                "METRICS.")
    ctx.say("    This is a property of the FETCH, not a finding that the estate has no "
            "dashboards.")
    for line in lines:
        ctx.say(line)


class NoMeasures(SystemExit):
    """A model with no measure. Alone it refuses the run; among several it is said and
    skipped, so one empty model never stops the rest."""


def _one_model_each(sources: list[dict]) -> list[dict]:
    """The sources, each path a reader says holds several models read as one model each
    (lib/withheld.model_sources, which verification reads the inputs again through)."""
    try:
        return _withheld.model_sources({"sources": sources})
    except _readers.ReaderError as exc:
        raise SystemExit(f"a1: {exc}") from None


def _parse_one(ctx: stage.Ctx, path: pathlib.Path, vendor: str, dialect: str = "",
               model: str | None = None, numbering: "_withheld.Numbering | None" = None):
    """(facts, per-unit side data, resolved and unresolved published datasources, (reader,
    form)) for ONE model, read by its vendor's registered reader and scrubbed.

    Every reader is handed the same options: the run's `dialect` (a model whose formulas are
    SQL is read in it, never in one of its own), the run's `policy` (an admission choice a
    reader makes, such as inferring a join the model does not declare, is policy) and `model`
    (the BI input's `bi_model` setting: the one model to read when a file holds several)."""
    ctx.note_input_path(path)
    try:
        reader = _readers.load(vendor)
        form = _readers.form(reader, path)
        raw = _readers.parse(path, vendor, dialect=dialect, policy=ctx.policy, model=model)
    except (_readers.ReaderError, S.ReaderRefused) as exc:
        raise SystemExit(f"a1: {exc}") from None
    # What a reader read but the contract cannot carry is SAID, never silently dropped.
    for note in raw.pop("_reader_notes", []):
        ctx.say(f"a1: {note}")
    # Each withheld literal NUMBERED (lib/withheld): a statement comparing with one reads it
    # as a bind placeholder, which verification binds in memory from these same files.
    facts = scrub_prose(raw, _pii.Scrubber(ctx.policy), "", numbering)
    # `parameters` is optional on the contract and only Power BI declares any; a reader that
    # emits none must not leave the key absent for the stages that read it.
    facts.setdefault("parameters", [])

    unresolved = facts.pop("_unresolved_published", [])
    resolved_pub = facts.pop("_resolved_published", [])
    # Carried out of the contract: `_aggregations` and `_siblings` are A2's inputs, not
    # facts about the model.
    side = {u["unit_id"]: {"aggregations": u.pop("_aggregations", {}),
                           "siblings": u.pop("_siblings", None)}
            for u in facts["usage"]}

    if not facts["measures"]:
        raise NoMeasures(
            f"a1: parsed zero measures from {path}. Refusing by name rather than writing "
            f"measures: [] — an empty list fails three stages later with a worse message. "
            f"Check the extract actually contains the semantic model.")
    # No widgets: a reader may offer one question per measure instead (an optional hook).
    offered = 0
    if not facts["usage"] and callable(getattr(reader, "units_without_usage", None)):
        facts["usage"] = reader.units_without_usage(facts)
        offered = len(facts["usage"])
        side.update({u["unit_id"]: {"aggregations": {}, "siblings": None}
                     for u in facts["usage"]})
    return facts, side, resolved_pub, unresolved, (reader, form), offered


def body(ctx: stage.Ctx) -> None:
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    bi = ri.get("bi")
    if bi is None:
        # bi-absent is a NAMED mode. The stub is well-formed and says, in the file, that it
        # is a stand-in rather than a finding about the estate.
        # `vendor` names no reader: there is none for an absent model.
        doc = {"contract_version": "1.0", "vendor": "none", "source_file": "<absent>",
               "model_name": "", "tables": [], "measures": [], "relationships": [],
               "usage": []}
        ctx.write(OUT, doc, "bi_facts.schema.json")
        ctx.entry.verdict = "stub"
        ctx.entry.notes = ("bi-absent: this run was given no BI model. A STAND-IN so "
                           "downstream stages have a well-formed input; not a finding that "
                           "the estate has no BI.")
        ctx.count(mode="bi-absent", measures=0, usage=0)
        ctx.say("a1: bi-absent — emitting an empty, disclaimed stand-in.")
        return

    # One model, or several (one per BI plug, possibly different vendors): each is parsed on
    # its own, then merged with every unit, container and model name prefixed by its plug.
    sources = bi.get("sources") or [{"plug": None, "path": bi["path"], "vendor": bi["vendor"]}]
    sources = _one_model_each(sources)
    parts, side, resolved_pub, unresolved, readers = [], {}, [], [], []
    # One numbering for the run, in source order (lib/withheld.recover reads them the same).
    numbering = _withheld.Numbering()
    kept, empty, offered = [], [], []
    for src in sources:
        try:
            f, s, rp, un, reader, off = _parse_one(ctx, pathlib.Path(src["path"]), src["vendor"],
                                              ri.get("dialect") or "", src.get("model"),
                                              numbering)
        except NoMeasures:
            if len(sources) == 1:
                raise
            empty.append(src["plug"])
            continue
        kept.append(src)
        offered.append(off)
        if len(sources) > 1:
            s = _models.prefix_ids(src["plug"], s)
            rp = [f"{src['plug']}:{x}" for x in rp]
            un = [f"{src['plug']}:{x}" for x in un]
        parts.append((src["plug"], f))
        side.update(s)
        resolved_pub += rp
        unresolved += un
        readers.append(reader)
    if not parts:
        raise SystemExit(f"a1: none of the {len(sources)} BI models holds a measure "
                         f"({', '.join(empty)}). Check the fetch holds the semantic models.")
    if empty:
        ctx.say(f"a1: {len(empty)} of {len(sources)} BI model(s) hold no measure and are "
                f"left out: {', '.join(empty)}.")
    sources = kept
    facts = _models.merge(parts)
    form = readers[0][1]

    ctx.write(OUT, facts, "bi_facts.schema.json")
    import json
    sidecar = {"contract_version": "1.0", "per_unit": side,
               "resolved_published_datasources": resolved_pub,
               "unresolved_published_datasources": unresolved}
    artifacts.validate(sidecar, "a1_sidecar.schema.json", what="a1_sidecar.json")
    (ctx.run / "a1_sidecar.json").write_text(json.dumps(sidecar, indent=2) + "\n")
    ctx.entry.outputs.append(str(ctx.run / "a1_sidecar.json"))
    ctx.count(vendor=bi["vendor"], reader=form,
              tables=len(facts["tables"]),
              measures=len(facts["measures"]), usage=len(facts["usage"]),
              relationships=len(facts["relationships"]),
              resolved_published=len(resolved_pub),
              unresolved_published=len(unresolved),
              **({"models": len(sources),
                  "vendors": ",".join(sorted({s["vendor"] for s in sources}))}
                 if len(sources) > 1 else {}))
    ctx.say(f"a1: {bi['vendor']} — {len(facts['tables'])} tables, "
            f"{len(facts['measures'])} measures, {len(facts['usage'])} usage entries."
            if len(sources) == 1 else
            f"a1: {len(sources)} BI models ("
            + ", ".join(f"{s['plug']}: {s['vendor']}" for s in sources)
            + f") — {len(facts['tables'])} tables, {len(facts['measures'])} measures, "
            f"{len(facts['usage'])} usage entries; every unit id and container is prefixed "
            "with its plug.")

    for src, (_plug, f), (rdr, _form), off in zip(sources, parts, readers, offered):
        if (off or not f["usage"]) and callable(getattr(rdr, "explain_empty_usage", None)):
            _report_empty_usage(ctx, rdr, pathlib.Path(src["path"]), off)
    if resolved_pub:
        ctx.say(f"a1: {len(resolved_pub)} PUBLISHED datasource(s) resolved against their "
                f".tds by name: {', '.join(resolved_pub[:4])}.")
    if unresolved:
        ctx.say(f"a1: {len(unresolved)} PUBLISHED datasource(s) resolved to no warehouse "
                f"target: {', '.join(unresolved[:4])}. A .twb referencing a published "
                f"datasource carries no connection — the .tds does: put the datasource's "
                f".tds/.tdsx beside the workbook (matched by name) and re-run. This is a "
                f"named ACQUISITION GAP, not a finding that the estate lacks lineage.")


def main() -> None:
    stage.main(STAGE, "a1_parse_bi.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
