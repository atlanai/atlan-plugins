#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""S0 — collect the run's inputs, settle the dialect and the MODE, name the estate profile.

    metrics-agent stage s0_inputs --run <dir> --config run.yml

THE RUN IS DESCRIBED BY run.yml, AND ONLY BY IT. Every input is a plug, named in run.yml and
served through its methods with its settings; S0 records each with the settings it is built
with at every later stage. Nothing here names a plug, a source format, a warehouse or a
destination (AGENT_GUARDRAILS rule 22): S0 asks each plug what it offers and records what it
answered.

Records PATHS AND NAMES ONLY, never contents (rule 8): the query log carries real user names
and customer-id literals, and `run_inputs.json` is an artifact anyone may read.

Four things are settled here and never revisited:

  * THE DIALECT is blocking. It is consumed at four separate points — A3 compiling, the
    history parse, every witness, and every published statement — and there is no safe
    default at any of them. A history whose recorded engine contradicts it is refused.

  * THE PROFILE (bi-only | history-only | both) names which definitional inputs were given.
    All three are first-class; none is degraded. What changes downstream is which evidence
    each stage leans on, and every substitution is recorded on the object that used it.

  * THE MODE (standalone | connected) names WHERE THE INPUTS MAY COME FROM — never where
    output goes. A standalone run reads only the customer's own systems: every input plug's
    custody is `customer`, and one whose data passes through a third party's store is refused
    by name before anything is read. A connected run reads through at least one third-party
    plug; declaring it with none is a contradiction, also refused.

  * THE PUBLISH TARGET is where output MAY go: the lanes of the output plugs `publish` lists.
    It is the operator's decision, never defaulted. Every write stage enforces it
    (lib/publish_target.py).

THE HARD RULE, ENFORCED HERE RATHER THAN DESCRIBED SOMEWHERE. **A standalone run never reads
through a third party's systems.** It binds the plug's CUSTODY, not the shape of the data: a
file the customer exported and handed over is theirs, and a file a third party's store
produced says so on its sidecar, which makes the plug reading it a third party's too. NO FLAG
RELAXES IT: not a policy value, not an override, not an "advanced" switch (AGENT_GUARDRAILS
rule 4).

At least one of the BI definitions and the query history is REQUIRED — they are the two
definitional inputs. Everything else is evidence about objects that already exist.
"""
from __future__ import annotations

import datetime as _dt
import os
import pathlib


from metrics_agent.lib import access as _access  # noqa: E402
from metrics_agent.lib import bi_capture as _bi_capture  # noqa: E402
from metrics_agent.lib import bi_models as _bi_models  # noqa: E402
from metrics_agent.lib import dialect as _dialect  # noqa: E402
from metrics_agent.lib import pii as _pii  # noqa: E402
from metrics_agent.lib import publish_target as _pt  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402
from metrics_agent import plug_runtime as _runtime  # noqa: E402
from metrics_agent import readers as _readers  # noqa: E402
from metrics_agent import run_config as _run_config  # noqa: E402
from metrics_agent import socket_gates as _gates  # noqa: E402
from metrics_agent import sockets as S  # noqa: E402

STAGE = "s0_inputs"

# What a run with no SQL check costs, recorded in run_inputs.json (`sql_check.warning`) and
# repeated by `metrics-agent run` when it ends, so it is not only a line on stderr.
NOT_COMPILED = ("NO SQL CHECK: nothing was compiled against a warehouse. Every statement ships "
                "label: parse_only, and SQL that parses but cannot run (a column its inner query "
                "does not return, a table that is not there) goes unnoticed. Add a SQL check "
                "(`inputs.sql_check`) where the warehouse can be reached.")


def _extra(p) -> None:
    p.add_argument("--config", default="run.yml",
                   help="run.yml: the plugs this run uses and their settings (default "
                        "./run.yml). `metrics-agent setup` writes one.")


def _refuse(msg: str) -> None:
    raise SystemExit(f"s0: {msg}")


def _failed(what: str, exc: BaseException) -> str:
    """Why a plug's call failed, in words safe to print. A plug's own refusal (`ValueError`,
    `NotSupported`, `ReaderRefused`) is its message; anything else (an SDK or the network) is
    named by TYPE only: its text could carry a credential or a host, and a stack tells the
    operator nothing they can act on (guardrail 8). Nothing is recorded either way."""
    from metrics_agent import sockets as _S  # noqa: PLC0415

    if isinstance(exc, (ValueError, _S.NotSupported)):
        return f"{what}: {exc}"
    return (f"{what}: the call failed: {type(exc).__name__}. Nothing was recorded. Check that "
            "the system is reachable and that the credentials in the environment are for it.")


# ── destinations ─────────────────────────────────────────────────────────────────────────
def _settle_outputs(ctx, doc: dict, dialect: str) -> dict:
    """{plug: the target it settled} for every output plug the run publishes to that records a
    target (`s0_settings`): each value from `settings.<plug>.<key>` in run.yml, else the
    variable the plug names. The plug checks and resolves it (`settle_target`); a refusal is
    the plug's, named."""
    from metrics_agent.lib.redact import is_credential_name  # noqa: PLC0415

    out = {}
    for name in doc["publish"]:
        plug = _runtime.plug(name, _run_config.plug_settings(doc, name), dialect=dialect,
                             purpose="the run's destination")
        declared = getattr(plug, "s0_settings", None) or ()
        if not declared:
            continue
        settings = _run_config.plug_settings(doc, name)
        values = {key: settings.get(key) or os.environ.get(var) or None
                  for key, var, _help in declared}
        try:
            settled = plug.settle_target(values, required=True)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - named, never a traceback
            _refuse(_failed(f"{name} could not settle its target", exc))
        if settled is None:
            continue
        # TARGET IDENTIFIERS ONLY. A credential comes from the environment and is never
        # recorded, whatever a plug hands back.
        leaked = [k for k in settled if is_credential_name(str(k))]
        if leaked:
            _refuse(f"{name} returned {leaked} as a target; a credential is never recorded.")
        if getattr(plug, "note", None):
            ctx.say(f"s0: {plug.note}")
        out[name] = settled
    return out


# ── the mode ─────────────────────────────────────────────────────────────────────────────
def _settle_mode(doc: dict, entries: dict[str, list[dict]]) -> str:
    """The mode run.yml declares, held to the custody of every input plug."""
    mode = doc["mode"]
    plugs = _run_config.selected(entries)
    try:
        _gates.check_custody(mode, plugs)
    except _gates.GateRefused as exc:
        _refuse(str(exc))
    if mode == "connected" and not any(_gates.third_party(p) for p in plugs):
        _refuse("mode is `connected`, but no input plug of this run reads through a third "
                "party's systems: every one has custody `customer`.\n"
                "  The mode describes where the inputs come from, never where output goes. "
                "Declare `mode: standalone`, or list the plug that reads through a third "
                "party.")
    return mode


# ── BI ───────────────────────────────────────────────────────────────────────────────────
def _bi_assets(ctx, name: str, plug, refs: list[str], scrubber) -> list[dict]:
    """The catalogue identities of the BI assets a plug delivered, when it declares them
    (`bi_assets`), each name scrubbed: tenant text is customer-authored."""
    hook = getattr(plug, "bi_assets", None)
    if not callable(hook):
        return []
    try:
        rows = list(hook(refs) or [])
    except Exception as exc:  # noqa: BLE001 - named, never a traceback
        _refuse(_failed(f"plug {name!r} could not resolve its BI assets", exc))
    out = []
    for r in rows:
        out.append({"role": r.get("role"), "name": scrubber.text(str(r.get("name") or "")),
                    "id": r.get("id"),
                    "qualified_name": scrubber.text(str(r.get("qualified_name") or ""))
                    or None})
    if out:
        ctx.say(f"s0: {name} resolved {len(out)} BI asset(s) to catalogue identities.")
    return out


def _deliver_bi(ctx, doc: dict, entries: list[dict], dialect: str, scrubber) -> list[dict]:
    """[{plug, path, vendor, assets}]: where each BI plug put its definitions. A plug asked to
    fetch was fetched before S0 (`s_fetch_bi`), so S0 never reaches the network for BI; any
    other plug is asked here, into `<run>/inputs/bi/<plug>/` or in place."""
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    fetched = _run_config.fetched_by_plug(ctx.run)
    blocked = _run_config.blocked_by_plug(ctx.run)
    asked = {p["plug"] for p in _run_config.fetch_plans(doc)}
    out = []
    for e in entries:
        name, settings = e["name"], dict(e.get("settings") or {})
        cls = _plugs.load(name)
        vendor = getattr(cls, "bi_vendor", None) or settings.get("bi_vendor")
        known = _readers.names()
        if vendor not in known:
            _refuse(f"plug {name!r} delivers BI definitions but names no vendor an installed "
                    f"reader reads. Each reader reads different files; set "
                    f"settings.{name}.bi_vendor ({' or '.join(known) or 'no reader is installed'}), "
                    "or declare `bi_vendor` on the plug.")
        plug = _runtime.build(e, dialect=dialect)
        refs = _runtime.as_list(settings.get("bi_assets"))
        if name in blocked:
            # AN ACCESS LIMIT NEVER STOPS THE RUN: the fetch recorded why in the access
            # report; the run goes on with its other inputs.
            ctx.say(f"s0: WARNING {name} could not be read ({', '.join(blocked[name])}); "
                    f"the run goes on without it. What to ask for: {_access.REPORT}")
            continue
        if name in fetched:
            root = pathlib.Path(fetched[name])
        elif name in asked:
            _refuse(f"settings.{name} asks for a fetch, and nothing was fetched into this run. "
                    "`metrics-agent run` fetches it first; by hand, run `metrics-agent stage "
                    "s_fetch_bi --run <dir> --config <run.yml>` before S0.")
        else:
            dest = ctx.run / "inputs" / "bi" / name
            dest.mkdir(parents=True, exist_ok=True)
            try:
                files = plug.bi_definitions(refs, dest)
            except S.AccessBlocked as exc:
                _access.record(ctx.run, [dict(b, stage=STAGE) for b in exc.blockers])
                ctx.say(f"s0: WARNING {name} could not be read "
                        f"({', '.join(sorted({b['code'] for b in exc.blockers}))}); the run "
                        f"goes on without it. What to ask for: {_access.REPORT}")
                continue
            except Exception as exc:  # noqa: BLE001 - named, never a traceback
                _refuse(_failed(f"plug {name!r} could not deliver its BI definitions", exc))
            finally:
                if dest.is_dir() and not any(dest.iterdir()):
                    dest.rmdir()
            root = _runtime.bi_root(files, dest, settings)
            if root is None:
                _refuse(f"plug {name!r} delivered no BI definition file.")
            hook = getattr(plug, "access_blockers", None)
            found = [dict(b, stage=STAGE) for b in (hook(root) or [])] if callable(hook) else []
            if found:
                # What this delivery could not read, item by item, though it read the rest.
                _access.record(ctx.run, found)
                for b in found:
                    ctx.say(f"s0: WARNING {name}: [{b['code']}] {b['not_read']}; ask: "
                            f"{b['ask']['what']}")
        root = root.expanduser().resolve()
        if not root.exists():
            _refuse(f"plug {name!r} delivered BI definitions at {root}, which does not exist")
        # A capture (browser or MCP) is read through its manifest: the reader parses its
        # `definitions`, never its oracle files, and the run records where they came from and
        # every gap the route left, each said out loud: what the run cannot verify.
        capture = None
        if _bi_capture.find(root) is not None:
            try:
                doc = _bi_capture.load(root)
            except _bi_capture.CaptureError as exc:
                _refuse(f"plug {name!r}: {exc}")
            if doc["tool"] != vendor:
                _refuse(f"plug {name!r} reads {vendor} definitions, and the capture at {root} "
                        f"was made for {doc['tool']}: set settings.{name}.bi_vendor to "
                        f"{doc['tool']}, or point `bi` at a {vendor} capture.")
            capture = _bi_capture.summary(doc)
            root = _bi_capture.definition_root(root)
            ctx.say(f"s0: {name} reads a {capture['route']} capture taken as "
                    f"{capture['role']}: {root}")
            for line in _bi_capture.gap_lines(capture):
                ctx.say(f"s0: WARNING {name}'s capture has a gap, {line}")
            if capture.get("gaps"):
                # Each gap is also a named blocker in the run's access report, with what to
                # capture to close it.
                _access.record(ctx.run, [_access.from_gap(g, source=name, stage=STAGE)
                                         for g in capture["gaps"]])
        # A container of models (a whole tenant's extract) that holds exactly ONE model a run
        # could read leaves no choice: the run reads that model, and says which.
        only = _readers.one_model(root)
        if only is not None and only.resolve() != root:
            ctx.say(f"s0: {root} holds one model; the run reads {only.resolve()}")
            root = only.resolve()
        # A path that is not ONE model (a whole tenant's extract, and a run builds one) is
        # refused HERE rather than at A1, so the run is never recorded at all. Every installed
        # reader is asked; the core does not know which layouts exist.
        # A plug whose settings named every model it fetched (`fetch_reads_all`) is read whole,
        # one model each (A1); only a container nobody asked for is refused.
        why = None if getattr(plug, "fetch_reads_all", False) else _readers.refusal(root)
        if why:
            _refuse(why)
        # A fetched extract carries its custody at its root: the entry is as strict as it.
        held = _run_config.held_custody([str(root)])
        if held:
            e["custody"] = held
        # The ONE model to read when the definitions hold several (`bi_model`): handed to
        # the reader as it is, which matches it against what the file holds or refuses.
        model = settings.get("bi_model")
        if model is not None and (not isinstance(model, (str, int)) or not str(model).strip()):
            _refuse(f"settings.{name}.bi_model must name one model (an id or a name); "
                    f"it is {model!r}")
        out.append({"plug": name, "path": str(root), "vendor": vendor,
                    **({"model": str(model).strip()} if model is not None else {}),
                    **({"capture": capture} if capture else {}),
                    "assets": _bi_assets(ctx, name, plug, refs, scrubber)})
        ctx.say(f"s0: BI definitions from {name} ({vendor}): {root}"
                + (f", model {str(model).strip()!r}" if model is not None else ""))
    return out


# ── the bounded table set ────────────────────────────────────────────────────────────────
def _check_tables(tables: list[str], what: str) -> list[str]:
    """Lower-cased, deduplicated, each DB.SCHEMA.TABLE. A shorter entry would bound nothing:
    the history lane matches statements to bounded tables by their three-part name."""
    out = sorted({str(t).strip().lower() for t in tables if str(t).strip()})
    short = sorted(t for t in out if len(t.split(".")) != 3 or not all(t.split(".")))
    if short:
        _refuse(f"{what}: a bounded table must be fully qualified as DB.SCHEMA.TABLE; these "
                "are not:\n" + "\n".join(f"    {t}" for t in short) + "\n"
                "  The history lane matches statements to bounded tables by their three-part "
                "name, so a shorter entry would bound nothing. Name each table in full.")
    return out


def _resolve_plug_scopes(ctx, entries: list[dict], dialect: str) -> list[str]:
    """The union of the tables each scope plug resolves its `assets` to."""
    tables: set[str] = set()
    for e in entries:
        refs = _runtime.as_list((e.get("settings") or {}).get("assets"))
        try:
            scope = _runtime.build(e, dialect=dialect).resolve_scope(refs)
        except Exception as exc:  # noqa: BLE001 - named, never a traceback
            _refuse(_failed(f"plug {e['name']!r} could not resolve its scope", exc))
        got = {str(t).strip().lower() for t in scope.tables if str(t).strip()}
        ctx.say(f"s0: the {e['name']} scope resolved to {len(got)} table(s)"
                + (f" ({scope.note})" if scope.note else "") + ".")
        tables |= got
    out = _check_tables(sorted(tables), "a scope plug")
    if not out:
        _refuse("the scope plugs resolved NO tables. Give `tables`, or a scope that names "
                "warehouse objects.")
    return out


def _bounded_tables(ctx, doc, entries, bi_sources, profile, dialect) -> tuple[list[str], str]:
    if doc.get("tables"):
        return _check_tables(doc["tables"], "tables"), "run.yml"
    if entries.get("scope"):
        return _resolve_plug_scopes(ctx, entries["scope"], dialect), "plug-scope"
    if profile == "both":
        # A BI + HISTORY RUN NEEDS THE SET NOW. H0 admits a query only when it reads a
        # bounded table and refuses an empty set outright, and nothing between here and lane
        # H writes one back into run_inputs. Derived from the models' own lineage by the
        # reader that read them (`lib/bi_scope`), and recorded as `bi-lineage` — a
        # derivation, never a default.
        from metrics_agent.lib import bi_scope as _bi_scope  # noqa: PLC0415 — reader deps are heavy

        tables = sorted({t.lower() for s in bi_sources
                         for t in _bi_scope.tables_of(
                             pathlib.Path(s["path"]), s["vendor"],
                             **({"model": s["model"]} if s.get("model") else {}),
                             dialect=dialect)})
        if not tables:
            _refuse("this BI + history run names no bounded tables, and the lineage of its BI "
                    "models resolves to none either.\n  The history lane would admit nothing "
                    "(or, unbounded, the whole warehouse). Give `tables`, or a scope plug.")
        ctx.say(f"s0: bounded table set derived from the lineage of {len(bi_sources)} BI "
                f"model(s): {len(tables)} table(s).")
        return tables, "bi-lineage"
    if profile == "history-only":
        _refuse("a history-only run needs a bounded table set (`tables`, or a scope plug): "
                "the history lane admits a query only when it reads a bounded table, and an "
                "unbounded scope admits the whole warehouse.")
    ctx.say("s0: no bounded table set given; it will be derived at A2 from the BI model's "
            "own lineage (bounded_tables.source = bi-lineage).")
    return [], "bi-lineage"


def _refuse_a_file_named_twice(entries: dict[str, list[dict]]) -> None:
    """A `path` setting that names one file twice would read it twice: every execution in a
    log counted twice. Refused, naming the file, whichever plug it is."""
    for cap, es in entries.items():
        for e in es:
            paths = [p.expanduser().resolve() for p in _runtime.input_paths(e, cap)]
            twice = sorted({str(p) for p in paths if paths.count(p) > 1})
            if twice:
                _refuse(f"settings.{e['name']} names the same file more than once: "
                        + ", ".join(twice) + ". Each file is read once; list it once.")


# ── the body ─────────────────────────────────────────────────────────────────────────────
def body(ctx: stage.Ctx) -> None:
    # What this stage met on an earlier pass is replaced by what it meets now.
    _access.clear_stage(ctx.run, STAGE)
    config = pathlib.Path(ctx.args.config).expanduser().resolve()
    try:
        doc = _run_config.load(config)
        _run_config.validate(doc)
        witness_mode = _run_config.witness_mode(doc)
        window = _run_config.check_window(doc)
        engine = _run_config.history_engine(doc)
        entries = {cap: [_run_config.socket_entry(doc, n, cap) for n in plugs]
                   for cap, plugs in _run_config.socket_plugs(doc).items() if plugs}
        publish_target = _run_config.publish_target(doc)
    except _run_config.ConfigError as exc:
        _refuse(str(exc))
    _refuse_a_file_named_twice(entries)

    # ---- the dialect, first: nothing else is worth resolving without it --------
    try:
        declared = _dialect.normalise(doc["dialect"])
        _dialect.check_against_log(declared, engine)
        _dialect.check_single([declared])
    except _dialect.DialectError as exc:
        _refuse(str(exc))
    if engine and engine.lower() not in _dialect.SUPPORTED:
        # Not a dialect the SQL parser knows, so there is nothing to check it against. Said,
        # so a misspelt engine is not taken for a check that passed.
        ctx.say(f"s0: the history's engine {engine!r} is not a known dialect, so it was NOT "
                f"checked against the dialect {declared}.")

    # ---- the definitional inputs, and the mode their custody allows ----------
    scrubber = _pii.Scrubber(ctx.policy)
    bi_sources = _deliver_bi(ctx, doc, entries.get("bi") or [], declared, scrubber)
    mode = _settle_mode(doc, entries)
    has_bi, has_history = bool(bi_sources), bool(entries.get("history"))
    if entries.get("bi") and not has_bi and not has_history:
        _refuse("nothing in this run could be read: every BI input was blocked by an access "
                f"limit, and the run reads no query history. {_access.REPORT} names each "
                "limit and what to ask for, and from whom; when it is granted, start the run "
                "again with --fresh.")
    try:
        _gates.check_enough_input({cap: [e["name"] for e in entries.get(cap) or []]
                                   for cap in ("history", "bi")})
    except _gates.GateRefused as exc:
        _refuse(str(exc))
    profile = ("both" if has_bi and has_history
               else "bi-only" if has_bi else "history-only")
    if (window["start"] or window["end"]) and not has_history:
        ctx.say("s0: a window was given, but this run reads no query history, which is all a "
                "window bounds. It is dropped and not recorded.")
        window = {"start": None, "end": None}

    tables, table_source = _bounded_tables(ctx, doc, entries, bi_sources, profile, declared)

    # ---- where the output may go: the operator's decision, in run.yml ----------
    ctx.gate("publish_target", "Where should this run publish? (run.yml `publish`)",
             publish_target, options=publish_target, decided_by="run.yml")
    outputs = _settle_outputs(ctx, doc, declared)

    plugs = {cap: entries.get(cap) or [] for cap in ("history", "schema", "bi", "scope")}
    checks = entries.get("sql_check") or []
    plugs["sql_check"] = checks[0] if len(checks) == 1 else (checks or None)
    plugs["publish"] = [_run_config.entry(n) for n in doc["publish"]]
    plugs["observers"] = [_run_config.entry(n) for n in doc.get("observers") or []]

    compiled = witness_mode in ("explain", "execute")
    sql_check = {"compiled": compiled,
                 "declared": bool(checks) or doc.get("witness_mode") is not None,
                 "warning": None if compiled else NOT_COMPILED}

    record = {
        "contract_version": "1.0",
        "run_id": ctx.run.name,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "scope": doc["scope"],
        "dialect": declared,
        "profile": profile,
        "mode": mode,
        "witness_mode": witness_mode,
        "sql_check": sql_check,
        "bi": ({"path": bi_sources[0]["path"],
                "vendor": _bi_models.vendor_of([s["vendor"] for s in bi_sources]),
                "sources": bi_sources} if bi_sources else None),
        # Read by H0 through the history plugs, in this order, as ONE corpus.
        "querylog": ({"plugs": [e["name"] for e in entries["history"]], "engine": engine,
                      "window_start": window["start"], "window_end": window["end"]}
                     if has_history else None),
        "bounded_tables": {"source": table_source, "tables": tables},
        "outputs": outputs,
        "publish_target": publish_target,
        "policy": {"path": str(ctx.policy.path), "sha256": ctx.policy.sha256},
        "plugs": plugs,
        "config": _run_config.fingerprint(config),
    }
    # Record the external inputs as PATHS on the manifest. Never their contents.
    for s in bi_sources:
        ctx.note_input_path(pathlib.Path(s["path"]))
    for cap in ("history", "schema"):
        for e in entries.get(cap) or []:
            for path in _runtime.input_paths(e, cap):
                ctx.note_input_path(path)
                if path.is_file():
                    e.setdefault("inputs", []).append(_run_config.fingerprint(path))

    ctx.write("run_inputs.json", record, "run_inputs.schema.json")
    ctx.count(publish_target=_pt.describe(publish_target), profile=profile, mode=mode,
              dialect=declared, bounded_tables=len(tables), witness_mode=witness_mode)
    ctx.say(f"s0: publish to {_pt.describe(publish_target)} (run.yml)")
    ctx.say(f"s0: mode={mode} profile={profile} dialect={declared} "
            f"witness={witness_mode} bounded_tables={len(tables)} ({table_source})")
    ctx.say("s0: plugs " + "; ".join(
        f"{cap}={','.join(e['name'] for e in es)}" for cap, es in entries.items()))
    if not entries.get("schema"):
        ctx.say("s0: no schema plug, so no column metadata lookup. Dimensions are still "
                "declared; their datatype comes from the SQL where it forces one and is "
                "admitted as `unknown` otherwise. Nothing is dropped for it.")
    if not compiled:
        ctx.say(f"s0: the witness is {witness_mode!r}, so NOTHING IS COMPILED against a "
                "warehouse this run. Every record ships label: parse_only, and a canonical "
                "metric statement that parses may still be unrunnable — measured on a real "
                "estate, 38% of them were. This is a legitimate build only where no warehouse "
                "exists; it is not the same as a missing lane, and it is not evidence. "
                "Recorded as run_inputs.json `sql_check.warning`"
                + ("" if sql_check["declared"] else
                   "; run.yml does not state this choice (set `witness_mode: parse`, or list "
                   "`inputs.sql_check`)") + ".")


def main() -> None:
    stage.main(STAGE, "s0_inputs.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
