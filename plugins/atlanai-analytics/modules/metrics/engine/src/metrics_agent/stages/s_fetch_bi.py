#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Fetch the run's BI definitions through its BI plug, before S0.

    metrics-agent stage s_fetch_bi --run <dir> --config run.yml

For a run.yml whose BI plug is asked to fetch (it names one of the plug's `fetch_settings`,
and no `bi` path). `metrics-agent run` runs
this first, on a new run only; a resumed run reads what it fetched before.

What it does, in order:

  * asks the plug for its definitions (`bi_definitions`) into `<run>/inputs/bi/<plug>/`. The
    plug does the network and holds the credential; this stage opens no connection itself;
  * picks the ONE model the run reads, among the model folders the plug says the fetch wrote
    (`fetched_models`; a plug that declares none fetched one model, the folder itself): the
    only one, or the one `settings.<plug>.model` names. Several and no choice is refused,
    listing them: choosing would decide the run's scope for the operator;
  * records it in `<run>/bi_fetch.json`, which S0 reads in place of `settings.<plug>.bi`;
  * records every access limit the fetch met in `<run>/access_report.json`: an item the plug
    could not read (its `access_blockers`), or a plug that could read nothing
    (`sockets.AccessBlocked`: a refused sign-in, a seat that cannot download). A limit never
    stops the run: a blocked plug is recorded as `blocked`, S0 leaves it out, and the run
    goes on with its other inputs.

NOT A PIPELINE STAGE. It runs before S0 settles the run, so it writes no manifest entry;
`bi_fetch.json` is its record, and S0 records the path it settled on in `run_inputs.json`.
Exit 0 fetched · 1 the fetch failed · 2 refused (nothing to fetch, or no single model).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import sys

from metrics_agent import plug_runtime as _runtime
from metrics_agent import run_config as _run_config
from metrics_agent import sockets as S
from metrics_agent.lib import access as _access

STAGE = "s_fetch_bi"


def _models(plug, root: pathlib.Path) -> list[pathlib.Path]:
    """The model folders a fetch wrote under `root`, as the plug lays them out
    (`fetched_models`); [] when it declares none: the fetch is one model, the folder itself.
    The core knows no tool's layout."""
    hook = getattr(plug, "fetched_models", None)
    return sorted(pathlib.Path(p) for p in hook(root)) if callable(hook) else []


def blockers_of(plug, root: pathlib.Path) -> list[dict]:
    """What the plug's fetch could not read, as blockers (`access_blockers`, an optional
    capability: each item a limit kept out, read from the plug's own fetch record). [] when
    it declares none."""
    hook = getattr(plug, "access_blockers", None)
    if not callable(hook):
        return []
    return [dict(b, stage=STAGE) for b in hook(root) or []]


def choose_model(plug, root: pathlib.Path, wanted: str | None) -> pathlib.Path:
    models = _models(plug, root)
    if not models and not wanted:
        return root           # one model: the plug's files are read as one folder
    if wanted:
        hit = [m for m in models if m.name == str(wanted).strip().rsplit("/", 1)[-1]]
        if len(hit) != 1:
            print(f"s_fetch_bi: REFUSED — settings model {wanted!r} is not one of the "
                  f"{len(models)} fetched: " + ", ".join(m.name for m in models),
                  file=sys.stderr)
            raise SystemExit(2)
        return hit[0]
    if len(models) == 1:
        return models[0]
    if getattr(plug, "fetch_reads_all", False):
        # The plug fetched exactly what its settings named (each one asked for), so the run
        # reads every model it brought; A1 reads one model each and merges them.
        return root
    listed = "\n".join(f"    {m.parent.name}/{m.name}" for m in models) or "    (none)"
    print(f"s_fetch_bi: REFUSED — the fetch brought {len(models)} models. Choosing "
          f"would decide the run's scope; name one as settings.<plug>.model:\n{listed}",
          file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s_fetch_bi", description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--config", required=True)
    # Passed to every step by `metrics-agent run`; a fetch has no label of its own, and its
    # policy is the plugs' (below).
    ap.add_argument("--as", dest="label", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--policy", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.policy:
        # The plugs read the run's policy (`fetch.*`, their own sections) from the environment,
        # as every stage resolves it: --policy first, then $METRICS_AGENT_POLICY.
        os.environ["METRICS_AGENT_POLICY"] = str(pathlib.Path(a.policy).expanduser().resolve())
    try:
        doc = _run_config.load(a.config)
    except _run_config.ConfigError as exc:
        print(f"s_fetch_bi: REFUSED — {exc}", file=sys.stderr)
        return 2
    plans = _run_config.fetch_plans(doc)
    if not plans:
        print("s_fetch_bi: REFUSED — run.yml asks for no fetch (its BI plug names no fetch "
              "setting, or names an extract on disk as `bi`)", file=sys.stderr)
        return 2
    run = pathlib.Path(a.run)
    # What a fetch met on an earlier pass is replaced by what it meets now (the access may
    # have been granted since).
    _access.clear_stage(run, STAGE)
    # Every BI plug asked to fetch, in the order listed; the first is also recorded at the
    # top level, as a run with one fetching plug always recorded it.
    fetched: dict[str, dict] = {}
    for want in plans:
        dest = run / "inputs" / "bi" / want["plug"]
        if dest.exists() and any(dest.iterdir()):
            print(f"s_fetch_bi: REFUSED — {dest} is not empty; a fetch never mixes into an "
                  "earlier one. Start the run again with --fresh.", file=sys.stderr)
            return 2
        dest.mkdir(parents=True, exist_ok=True)
        plug = _runtime.plug(want["plug"], want["settings"], dialect=doc.get("dialect") or "",
                             purpose="fetching the run's BI definitions")
        assets = _runtime.as_list(want["settings"].get("bi_assets"))
        try:
            files = plug.bi_definitions(assets, dest)
        except S.AccessBlocked as exc:
            # AN ACCESS LIMIT NEVER STOPS THE RUN: named in the access report, with what to
            # ask for; the run goes on with its other inputs (S0 leaves this plug out).
            _access.record(run, [dict(b, stage=STAGE) for b in exc.blockers])
            codes = sorted({b["code"] for b in exc.blockers})
            fetched[want["plug"]] = {"root": str(dest), "blocked": codes}
            print(f"s_fetch_bi: WARNING — {want['plug']} could not be read "
                  f"({', '.join(codes)}): {exc}. The run goes on without it; "
                  f"{_access.REPORT} says what to ask for.", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001 - by name; the plug never prints secrets
            print(f"s_fetch_bi: FAILED — {want['plug']}: {exc}", file=sys.stderr)
            return 1
        found = blockers_of(plug, dest)
        if found:
            # What the fetch could not read, item by item, though it read the rest.
            _access.record(run, found)
            for b in found:
                print(f"s_fetch_bi: WARNING — {want['plug']}: [{b['code']}] {b['not_read']}; "
                      f"ask: {b['ask']['what']}", file=sys.stderr)
        model = choose_model(plug, dest, want["settings"].get("model"))
        fetched[want["plug"]] = {"root": str(dest), "bi": str(model), "n_files": len(files),
                                 "models": [m.name for m in _models(plug, dest)]}
        print(f"s_fetch_bi: fetched {len(files)} files through {want['plug']}; the run "
              f"reads {model.name}")
    readable = [p["plug"] for p in plans if not fetched[p["plug"]].get("blocked")]
    first = readable[0] if readable else plans[0]["plug"]
    record = {"contract_version": "1.0", "plug": first,
              "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
              **(fetched[first] if readable else {}), "fetched": fetched}
    (run / _run_config.FETCH_RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
