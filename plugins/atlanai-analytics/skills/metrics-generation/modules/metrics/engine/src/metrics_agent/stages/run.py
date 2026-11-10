#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The pipeline, end to end, from one command — run.yml in, one run out.

    metrics-agent run --run output/acme [--config run.yml]

THE RUN IS DESCRIBED BY run.yml, AND ONLY BY IT: the inputs, where results go (`publish`) and
whether the run is traced (`observers`). With no `--config`, a started run reads the file it
was started from, a new one reads ./run.yml, and with neither the set-up interview asks (at a
terminal) or the run is refused with its questions. What this adds is what no single stage
can own:

  * THE ORDER. S0, the core's stages for the profile S0 settled, then each destination's own
    steps in the order `publish` lists them, then the steps that close the run.
  * THE HUMAN GATES. At each authored stage the run PAUSES (exit 3), naming the bundle, the
    skill that answers it and where the answer goes; re-running the same command resumes it.
    An answer the stage refuses (or d4b's contradictions) pauses it the same way: the fix is
    a corrected answer, so the run is not closed as failed.
  * THE RUN'S LIFECYCLE, AS THE PLUGS DECLARE IT. An output plug or an observer may need
    something before the first stage (an identity a trace reports as), on a failure (closing
    what it opened) and at the end (reading back what it wrote): its `run_start`,
    `run_failed` and `run_complete`. The core calls them and knows nothing of what they do.
  * ACCESS LIMITS NEVER STOP IT. A stage that meets one (an item the BI identity may not
    read, a suspended warehouse, a table with no SELECT grant) records a named blocker in
    `access_report.json` and goes on with what is readable; what it touches is unverified.
    Every end of the run (complete, paused, stopped, refused) ends with the report's
    summary: each blocker, what to ask for and from whom.
  * TRACING IS OPTIONAL UNLESS THE OPERATOR REQUIRES IT. A run is traced when run.yml lists an
    observer that records the run (`traces_run`), unless `--no-trace` turns it off; a tracing
    failure is said once and the run goes on. With `METRICS_AGENT_TRACE_REQUIRED=1` in the operator's
    environment, a tracing failure STOPS the run instead: it PAUSES (exit 3, like a human gate)
    at the stage that failed, naming the failure and the two ways on — fix tracing and re-run
    (the run resumes from that stage), or unset `METRICS_AGENT_TRACE_REQUIRED` (or set it to 0) to
    continue untraced, which the manifest records as the operator's decision.

Exit codes: 0 done · 1 a stage failed (closed as failed) · 2 refused before starting (a bad
run.yml, or S0 not accepting the inputs) · 3 paused at a gate, at an answer a stage refused,
or at a stage where required tracing failed (answer it, or decide, and re-run).

Where results go is the run's output plugs: each lists its own steps (`run_steps`), which
convert the core's bundle into its destination's format and publish it, and the options it
adds to this command (`run_options`). The core names no destination (AGENT_GUARDRAILS rule 22).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import signal
import subprocess
import sys

from metrics_agent import run_config as _run_config
from metrics_agent import sockets as S
from metrics_agent import plugs as _plugs
from metrics_agent._paths import child_env, stage_command
from metrics_agent import observe as _observe
from metrics_agent import plug_runtime as _runtime
from metrics_agent.lib import access as _access
from metrics_agent.lib import artifacts as _artifacts
from metrics_agent.lib import publish_target as _pt
from metrics_agent.lib import stage as _stage

PAUSED = 3
REFUSED = 2

# The human gates: the stage that emits the bundle, where its answer goes under the answers
# directory, whether the stage takes one file or a glob, and the skill that answers it.
GATES = {
    "a3b": ("a3b_review.py", "a3b/a3b.json", False, "review-compiler"),
    "c3": ("c3_questions.py", "c3/c3.json", False, "author-questions"),
    "m2": ("m2_describe.py", "m2/*.json", True, "describe-verified-sql"),
    "x1": ("x1_propose.py", "x1/*.json", True, "propose-metrics"),
    "x2": ("x2_merge.py", "x2/*.json", True, "merge-metrics"),
    "x3": ("x3_emit.py", "x3/*.json", True, "author-metric-card"),
    "x4": ("x4_relate.py", "x4/*.json", True, "relate-metrics"),
}


class Step:
    def __init__(self, label: str, script: str, args: list | None = None, *,
                 gate: str = "", ingest: bool = False) -> None:
        self.label, self.script, self.args = label, script, list(args or [])
        self.gate, self.ingest = gate, ingest


class Paused(Exception):
    pass


class StageFailed(Exception):
    pass


def _say(msg: str) -> None:
    print(f"run: {msg}", file=sys.stderr)


# ── tracing ──────────────────────────────────────────────────────────────────────────────
def _traces_run(observers: list, publish: list | None = None) -> bool:
    """Whether run.yml lists an observer that records the run itself (`traces_run`) and that
    is active beside this run's destinations: one that declares `offered_with` counts only
    when `publish` names one of those output plugs."""
    for name in observers:
        try:
            cls = _plugs.load(name)
        except _plugs.PlugError:
            continue
        if not getattr(cls, "traces_run", False):
            continue
        offered = tuple(getattr(cls, "offered_with", None) or ())
        if not offered or set(offered) & set(publish or ()):
            return True
    return False


def _trace_credentials() -> set[str]:
    """Every variable an installed observer declares as holding its tracing credential
    (`trace_env`). An untraced run hands none of them to its stages."""
    out: set[str] = set()
    for _name, names in _plugs.declared("trace_env"):
        out |= {str(n) for n in names}
    return out


# The core's tracing switches (`observe.py`). Names only; the tracing itself is the
# observer's, and what it reads besides is its own.
ENV_OFF, ENV_REQUIRED = _observe.OFF_ENV, _observe.REQUIRED_ENV


def stage_env(trace_on: bool, extra: dict | None = None) -> dict:
    """The environment every stage runs in: this process's, with what the run's plugs added
    before the first stage (`run_start`), and tracing turned off unless the run is traced.
    `METRICS_AGENT_TRACE_REQUIRED` is the operator's own setting and passes through unchanged: unset, a
    tracing failure is said and the run goes on; `1`, it stops the run at that stage (a pause)
    for the operator to decide."""
    env = dict(os.environ)
    env.update(extra or {})
    if trace_on:
        env.pop(ENV_OFF, None)
    else:
        env[ENV_OFF] = "1"
        # A stale credential in the operator's shell cannot change that.
        for name in _trace_credentials():
            env.pop(name, None)
    return env


# ── the plan ─────────────────────────────────────────────────────────────────────────────
# The model that writes the authored answers, named by whoever drives the run (an assistant
# sets it to its own model id). Recorded on every ingest so a later regression can be traced
# to a model version; the agent itself never calls a model (guardrail 3).
ENV_MODEL = "METRICS_AGENT_MODEL"


def gate_steps(name: str, answers: pathlib.Path) -> list:
    script, rel, multi, _skill = GATES[name]
    target = str(answers / rel)
    model = (os.environ.get(ENV_MODEL) or "").strip()
    return [Step(f"{name}_emit", script, ["--emit-prompt"], gate=name),
            Step(f"{name}_ingest", script, ["--from", target, *(["--model", model] if model
                                                                 else [])],
                 gate=name, ingest=True)]


def plan(profile: str, args) -> list:
    """The stages after S0, in order, for the profile S0 settled: the core's, then each
    destination's (`publish_steps`)."""
    answers = pathlib.Path(args.answers_dir or (pathlib.Path(args.run) / "answers"))
    before, after, last = publish_steps(args)
    steps: list = list(before)
    if profile not in ("history-only", "bi-only", "both"):
        raise StageFailed(f"S0 recorded profile {profile!r}; expected history-only, bi-only "
                          "or both")
    if profile in ("history-only", "both"):
        steps += [Step("h0_corpus", "h0_corpus.py"),
                  Step("h1_shapes", "h1_shapes.py"),
                  Step("h2_measures", "h2_measures.py"), Step("h5_verified", "h5_verified.py")]
    if profile in ("bi-only", "both"):
        steps += [Step("a1_parse_bi", "a1_parse_bi.py"),
                  Step("a2_question_units", "a2_question_units.py"),
                  Step("a3_translate", "a3_translate.py")]
        steps += ([Step("a3b_review", "a3b_review.py", ["--skip"])] if args.skip_a3b_review
                  else gate_steps("a3b", answers)
                  + [Step("a3_recompile", "a3_translate.py", ["--recompile"])])
        steps += [Step("a4_witness_bi", "a4_witness_bi.py"),
                  Step("c1_normalise", "c1_normalise.py"), Step("c2_merge", "c2_merge.py")]
        steps += ([Step("c3_questions", "c3_questions.py", ["--deterministic-draft"])]
                  if args.c3_deterministic else gate_steps("c3", answers))
        steps += [Step("c4_gates", "c4_gates.py"), Step("c5_emit", "c5_emit.py")]
    steps.append(Step("m1_merge_pre", "m1_merge.py", ["--pre-describe"]))
    steps += gate_steps("m2", answers)
    steps.append(Step("m1_merge", "m1_merge.py"))
    steps.append(Step("d1_candidates", "d1_candidates.py"))
    steps += gate_steps("x1", answers) + gate_steps("x2", answers) + gate_steps("x3", answers)
    steps.append(Step("d4b_consistency", "d4b_consistency.py"))
    steps += gate_steps("x4", answers)
    return steps + after + last


def _output_plugs(args) -> list:
    """The run's output plugs, in the order run.yml's `publish` lists them, each built with
    its settings once per command. The core never names one."""
    built = getattr(args, "_built_outputs", None)
    if built is None:
        settings = getattr(args, "plug_settings", None) or {}
        built = [(n, _runtime.plug(n, settings.get(n) or {}, dialect="", purpose="publish"))
                 for n in getattr(args, "publish_plugs", None) or []]
        args._built_outputs = built
    return built


def _lifecycle_plugs(args) -> list:
    """The output plugs and observers whose run hooks the run calls, in that order."""
    out = list(_output_plugs(args))
    built = getattr(args, "_built_observers", None)
    if built is None:
        # An observer is built the way the stage harness builds one (`observe.py`): its class,
        # called with no arguments. One that cannot be built is said and skipped.
        built = []
        for n in getattr(args, "observer_plugs", None) or []:
            try:
                built.append((n, _plugs.load(n)()))
            except Exception as exc:  # noqa: BLE001 - an observer never stops a run
                _say(f"observer {n} could not be built ({type(exc).__name__}: {exc}); "
                     "its run hooks are not called")
        args._built_observers = built
    return out + built


def options(args, name: str) -> dict:
    """What a plug's hooks are told about the run: this command's options (its own
    `run_options` among them), whether the run is traced, and its run.yml settings."""
    return {**{k: v for k, v in vars(args).items() if not k.startswith("_")},
            "push": True, "trace": bool(getattr(args, "trace_on", False)),
            "settings": (getattr(args, "plug_settings", None) or {}).get(name) or {}}


def lifecycle(args, hook: str, *rest) -> dict:
    """Call `hook` (`run_start`, `run_failed`, `run_complete`) on every plug that declares
    it. `run_start` returns what each plug adds to the stages' environment; the others return
    nothing. A plug's own refusal (SystemExit) stops the run; any other failure of a closing
    hook is said and the run goes on."""
    env: dict = {}
    for name, plug in _lifecycle_plugs(args):
        fn = getattr(plug, hook, None)
        if not callable(fn):
            continue
        if hook == "run_start":
            env.update(fn(options(args, name)) or {})
            continue
        try:
            fn(options(args, name), *rest)
        except Exception as exc:  # noqa: BLE001 - closing never hides the run's own outcome
            _say(f"{name}: {hook} failed ({type(exc).__name__}: {exc})")
    return env


def publish_steps(args) -> tuple[list, list, list]:
    """(steps before the pipeline, each destination's steps after it, steps after them all)
    that the run's output plugs ask for, destinations in the order the run lists them.

    EACH DESTINATION CONVERTS THE CORE'S BUNDLE ITSELF. A plug lists its own stages: what it
    builds from the bundle (its payloads), what it sends, and what it closes. The core runs
    them in order and knows none of their names. A plug that cannot run as configured says why
    (`NotSupported`), and the run is refused before it starts."""
    before, after, last = [], [], []
    for name, plug in _output_plugs(args):
        opts = options(args, name)
        for hook, into in (("run_steps_before", before), ("run_steps", after),
                           ("run_steps_last", last)):
            fn = getattr(plug, hook, None)
            if fn is not None:
                into += [Step(label, f"{stage}.py", list(a)) for label, stage, a in fn(opts)]
    return before, after, last


# ── running ──────────────────────────────────────────────────────────────────────────────
def _manifest(run: pathlib.Path) -> dict:
    try:
        return {s["stage"]: s for s in
                json.loads((run / "manifest.json").read_text()).get("stages") or []}
    except Exception:
        return {}


def _answers_present(step: Step, args) -> bool:
    answers = pathlib.Path(args.answers_dir or (pathlib.Path(args.run) / "answers"))
    _script, rel, multi, _skill = GATES[step.gate]
    return bool(list(answers.glob(rel))) if multi else (answers / rel).is_file()


def _answers_newer(step: Step, args, prior: dict) -> bool:
    """True when an answer file for `step`'s gate changed after `prior` (its last ingest)
    started. An entry with no start time is taken as current."""
    started = prior.get("started_at")
    if not started:
        return False
    try:
        when = _dt.datetime.fromisoformat(started)
    except ValueError:
        return False
    since = when.timestamp()
    if when.microsecond == 0:
        # The manifest records the start to the SECOND. An answer written earlier within that
        # same second (the run resumed straight after it was written) is not a correction made
        # after the ingest; read as one, it re-ran the ingest and every stage after it.
        since += 1
    answers = pathlib.Path(args.answers_dir or (pathlib.Path(args.run) / "answers"))
    _script, rel, multi, _skill = GATES[step.gate]
    files = list(answers.glob(rel)) if multi else [answers / rel]
    return any(f.is_file() and f.stat().st_mtime > since for f in files)


def run_step(step: Step, args, env: dict, *, runner=subprocess.run) -> None:
    cmd = [*stage_command(step.script), "--run", str(args.run),
           "--as", step.label, *step.args]
    if args.policy:
        cmd += ["--policy", args.policy]
    print(f"\n=== {step.label} ===", file=sys.stderr, flush=True)
    rc = runner(cmd, env=child_env(env)).returncode
    if rc == _observe.TRACING_STOPPED:
        # Not a failure to close the run over: the operator decides how it goes on.
        raise Paused(f"tracing is required ({ENV_REQUIRED}=1) and it failed at "
                     f"{step.label} (the reason is above). Either fix tracing and re-run this "
                     f"same command (it resumes from {step.label}), or unset {ENV_REQUIRED} "
                     "(or set it to 0) and re-run to continue untraced; the manifest records "
                     "that decision.")
    if rc == _stage.ANSWER_REFUSED:
        # The answers were refused, not the run: the fix is a corrected answer, so this is a
        # pause like a gate's, and the run (and its trace) is not closed as failed.
        raise Paused(f"{step.label} refused what it was given (the reasons are above). "
                     "Correct the answers, or decide as it says, then re-run this same "
                     f"command: it resumes from {step.label}.")
    if rc != 0:
        raise StageFailed(f"{step.label} exited {rc}")


def execute(steps: list, args, env: dict, *, runner=subprocess.run) -> None:
    run = pathlib.Path(args.run)
    rerun = False   # once a step runs again, every step after it reads its new output
    before: list[str] = []   # the labels of the steps already passed, in plan order
    for step in steps:
        done = _manifest(run)
        prior = done.get(step.label)
        # A STEP OLDER THAN ONE BEFORE IT IS STALE. The steps run in plan order, so each
        # entry starts no earlier than the entries before it. One that started earlier was
        # recorded by an earlier pass, and something upstream has run since: a pass that
        # re-ran from an upstream change and was killed part way leaves exactly this behind,
        # with this step's old `ok` (and, if it was the one killed, a half-written artifact)
        # still on the manifest. `rerun` alone cannot see it: it lives in one process.
        newest = max((str(done[b].get("started_at") or "") for b in before if b in done),
                     default="")
        if prior and newest and str(prior.get("started_at") or "") < newest:
            rerun = True
        before.append(step.label)
        if step.ingest and (done.get(f"{step.gate}_emit") or {}).get("verdict") == "skipped":
            continue   # the emit had nothing to ask: there is no answer to wait for
        # An answer corrected after its ingest (the fix a blocking check asks for upstream) is
        # ingested again. Skipping it re-ran the check on the stale artifact, which blocked
        # with the same message however the answer was fixed.
        if step.ingest and prior and _answers_newer(step, args, prior):
            rerun = True
        if not rerun and prior and prior.get("verdict") in ("ok", "stub", "skipped") and \
                prior.get("notes") != "awaiting_decision":
            continue
        if step.ingest and not _answers_present(step, args):
            _script, rel, _multi, skill = GATES[step.gate]
            raise Paused(f"waiting at the {step.gate} gate: answer "
                         f"{pathlib.Path(args.run) / (step.gate + '_prompt.json')} with the "
                         f"`{skill}` skill into "
                         f"{pathlib.Path(args.answers_dir or pathlib.Path(args.run) / 'answers') / rel}, "
                         "then re-run this same command.")
        if prior and prior.get("notes") == "awaiting_decision" and step.gate \
                and not step.ingest and not rerun:
            continue   # the bundle is already out; the ingest decides
        # Any other step left awaiting a decision (a blocking check, such as the consistency
        # gate) is NOT done: it has no ingest to settle it, so it runs again, and passes only
        # when the cause is fixed or a card is withheld. Skipping it let a blocked layer run on.
        run_step(step, args, env, runner=runner)
        rerun = rerun or prior is not None


def _plug_options(p: argparse.ArgumentParser) -> None:
    """The options an installed output plug adds to this command (`run_options`): a
    destination's own settings, such as its workspace or a destructive flag, are its own. A
    plug that cannot be loaded adds none; using it is refused where it is used."""
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    seen: set[str] = set()
    for name, item in sorted(_plugs.installed().items()):
        try:
            cls = item.load()
        except _plugs.PlugError:
            continue
        if getattr(cls, "kind", None) != "output":
            continue
        for flag, kwargs in getattr(cls, "run_options", ()) or ():
            if flag not in seen:
                seen.add(flag)
                p.add_argument(flag, **kwargs)


def parse(argv: list) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True, help="the run directory")
    p.add_argument("--config", default=None,
                   help="run.yml (default: the one a started run was described by, else "
                        "./run.yml)")
    p.add_argument("--policy", default=None)
    p.add_argument("--no-trace", action="store_true",
                   help="run untraced although run.yml lists a tracing observer")
    p.add_argument("--answers-dir", default=None,
                   help="where the human gates' answers are (default <run>/answers)")
    p.add_argument("--skip-a3b-review", action="store_true",
                   help="no compiler review this run (recorded by a3b as a choice)")
    p.add_argument("--c3-deterministic", action="store_true",
                   help="structural BI questions with no author, every one flagged")
    p.add_argument("--fresh", action="store_true",
                   help="start over: the existing run directory is moved aside, not deleted")
    _plug_options(p)
    a = p.parse_args(argv)
    a.run = str(pathlib.Path(a.run).expanduser().resolve())
    return a


def _s0_done(args) -> bool:
    run = pathlib.Path(args.run)
    if args.fresh or not (run / "manifest.json").is_file():
        return False
    return (_manifest(run).get("s0_inputs") or {}).get("verdict") == "ok"


def _damaged(args) -> str:
    """Why a started run directory cannot be resumed, or "". S0 recorded as done promises a
    readable run_inputs.json naming a profile; a directory without one was damaged after."""
    if not _s0_done(args):
        return ""
    path = pathlib.Path(args.run) / "run_inputs.json"
    try:
        ri = json.loads(path.read_text())
    except OSError:
        return f"{path} is missing, though the manifest records S0 as done."
    except ValueError:
        return f"{path} is not valid JSON."
    if not isinstance(ri, dict):
        return f"{path} does not hold S0's record."
    return ""


def _config_of_run(args) -> str | int | None:
    """The run.yml a started run was described by, so a resume reads the same file: its
    path, or a refusal when the file is gone or changed."""
    try:
        recorded = json.loads((pathlib.Path(args.run) / "run_inputs.json").read_text()) \
            .get("config")
    except (OSError, ValueError):
        return None
    if not recorded:
        return None
    path = pathlib.Path(recorded["path"])
    if not path.is_file():
        _say(f"REFUSED — this run was described by {path}, which is gone. Restore it, or "
             "re-run with --fresh.")
        return REFUSED
    if _run_config.fingerprint(path)["sha256"] != recorded.get("sha256"):
        _say(f"REFUSED — {path} changed since this run started, so resuming would mix two "
             "descriptions of one run. Restore it, or re-run with --fresh.")
        return REFUSED
    return str(path)


def _find_or_ask_config() -> str | int:
    """./run.yml when it exists; otherwise the set-up interview at a terminal, or a refusal
    that lists its questions."""
    from metrics_agent import setup as _setup  # noqa: PLC0415

    here = pathlib.Path("run.yml").resolve()
    if here.is_file():
        _say(f"inputs: {here}")
        return str(here)
    if not _setup.at_terminal():
        _setup.refusal(_setup.detect())
        return REFUSED
    _say("no run.yml: starting the set-up interview")
    code = _setup.interview(here)
    return str(here) if code == 0 else (code or 1)


def main(argv: list | None = None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    damaged = _damaged(args)
    if damaged:
        # S0 is recorded as done but what it settled cannot be read. Every answer below (the
        # run.yml it started from, its publish target, its profile) comes from that file, so
        # carrying on would compare against nothing and refuse for the wrong reason.
        _say(f"STOPPED — {damaged} Re-run with --fresh to start over (the directory is moved "
             "aside, not deleted).")
        return 1
    if args.config:
        config: str | int | None = str(pathlib.Path(args.config).expanduser().resolve())
    else:
        config = _config_of_run(args) if _s0_done(args) else _find_or_ask_config()
    if isinstance(config, int):
        return config
    if not config:
        _say("REFUSED — no run.yml describes this run.")
        return REFUSED
    try:
        doc = _run_config.load(config)
        _run_config.validate(doc)
        target = _run_config.publish_target(doc)
    except _run_config.ConfigError as exc:
        _say(f"REFUSED — {exc}")
        return REFUSED
    args.publish_plugs = list(doc["publish"])
    args.observer_plugs = list(doc.get("observers") or [])
    args.plug_settings = doc.get("settings") or {}
    args.trace_on = _traces_run(args.observer_plugs, args.publish_plugs) and not args.no_trace
    try:
        # Each output plug lists its steps now, so one that cannot run as configured (a
        # destination setting missing) refuses before anything is written.
        publish_steps(args)
    except (S.NotSupported, _run_config.ConfigError, _plugs.PlugError) as exc:
        _say(f"REFUSED — {exc}")
        return REFUSED
    _say("publish to " + (", ".join(str(getattr(plug, "destination", name))
                                    for name, plug in _output_plugs(args)))
         + f" · traced: {'yes' if args.trace_on else 'no'}"
         + (" (--no-trace)" if args.no_trace
            and _traces_run(args.observer_plugs, args.publish_plugs) else ""))

    run = pathlib.Path(args.run)
    if _s0_done(args):
        started = _started_target(run)
        if started != target:
            _say(f"REFUSED — this run directory was started publishing to "
                 f"{_pt.describe(started)}; run.yml now says {_pt.describe(target)}. Re-run "
                 "with --fresh to start over under the new answer.")
            return REFUSED

    if args.fresh and run.exists():
        stamp = f"{run.name}.prev-{_dt.datetime.now():%Y%m%d-%H%M%S}"
        aside, n = run.with_name(stamp), 1
        while aside.exists():
            # Two fresh starts in one second: never rename onto an earlier copy.
            n += 1
            aside = run.with_name(f"{stamp}-{n}")
        run.rename(aside)
        _say(f"previous run moved to {aside}")
    _artifacts.ensure_run(run)

    try:
        added = lifecycle(args, "run_start")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else REFUSED
    except Exception as exc:  # noqa: BLE001 - a plug's refusal, not a traceback
        _say(f"REFUSED — {exc}")
        return REFUSED
    env = stage_env(args.trace_on, added)

    def _on_signal(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        done = _manifest(run)
        if not (done.get("s0_inputs") or {}).get("verdict") == "ok":
            if _run_config.fetch_plan(doc) is not None and not _run_config.fetched_bi(run):
                # The BI plug fetches its definitions into the run directory first; S0 then
                # reads the fetched model. A resumed run, and one whose fetch already landed,
                # reads what is there.
                try:
                    run_step(Step("s_fetch_bi", "s_fetch_bi.py", ["--config", config]),
                             args, env)
                except StageFailed:
                    _say("REFUSED — the BI fetch did not complete (its reason is above)."
                         + _access_summary(run))
                    return REFUSED
            try:
                run_step(Step("s0_inputs", "s0_inputs.py", ["--config", config]), args, env)
            except StageFailed:
                # S0 settles the inputs: when it refuses, the run never started.
                _say("REFUSED — S0 did not accept these inputs (its reason is above)."
                     + _access_summary(run))
                return REFUSED
        ri = json.loads((run / "run_inputs.json").read_text())
        if ri.get("publish_target") != target:
            _say(f"REFUSED — this run directory was started publishing to "
                 f"{_pt.describe(ri.get('publish_target'))}; run.yml now says "
                 f"{_pt.describe(target)}. Re-run with --fresh to start over under the new "
                 "answer.")
            return REFUSED
        execute(plan(ri.get("profile") or "", args), args, env)
    except Paused as e:
        _say(f"PAUSED — {e}" + _unchecked(run) + _access_summary(run))
        return PAUSED
    except (StageFailed, KeyboardInterrupt, Exception) as e:
        why = str(e) or type(e).__name__
        _say(f"STOPPED — {why}" + _access_summary(run))
        lifecycle(args, "run_failed", env, why)
        return 1
    _say("COMPLETE" + _unchecked(run) + _access_summary(run))
    lifecycle(args, "run_complete", env)
    return 0


def _access_summary(run: pathlib.Path) -> str:
    """The run's access report, followed to the widgets and metrics each limit leaves
    unverified, as lines that end the run's summary; empty when the run met no limit. Said at
    every end (complete, paused, stopped, refused): a limit never stops the run, and the
    operator is always told what to ask for, and from whom."""
    try:
        lines = _access.summary_lines(_access.finalize(run))
    except Exception as exc:  # noqa: BLE001 - the summary never hides the run's own outcome
        return f"\nrun: the access report could not be summarised ({type(exc).__name__})"
    return "".join(f"\nrun: {line}" for line in lines)


def _unchecked(run: pathlib.Path) -> str:
    """S0's warning when nothing in this run is compiled against a warehouse, as a line to
    end the run's summary with; empty when the run has a SQL check (or no S0 record)."""
    try:
        warning = (json.loads((run / "run_inputs.json").read_text()).get("sql_check")
                   or {}).get("warning")
    except (OSError, ValueError):
        return ""
    return f"\nrun: {warning}" if warning else ""


def _started_target(run: pathlib.Path) -> str | None:
    try:
        return json.loads((run / "run_inputs.json").read_text()).get("publish_target")
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
