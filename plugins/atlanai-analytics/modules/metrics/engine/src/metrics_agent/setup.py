# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The set-up interview: `metrics-agent setup` writes `run.yml` by asking.

    metrics-agent setup [--out run.yml]     ask, test, show, write
    metrics-agent setup --check run.yml     test an existing run.yml's plugs, ask nothing

Four steps:

1. **Detect** the installed plugs and, for each, whether every environment variable it reads
   is set (names only, never values). An existing `run.yml` at `--out` supplies defaults.
2. **Ask** only what detection cannot answer, skipping a question whose answer is forced:
   whether data may be read through a third party's systems, where the query history comes from, which BI
   tool, which plug checks SQL (offered by default, with what is lost without it; declining
   is recorded as `witness_mode: parse`), where results go (never defaulted), whether to trace, then
   the run settings with no safe default (scope, dialect, tables, window).
   A plug that signs in several ways (`credential_routes`) is never asked which: its routes
   are listed with which variables are set, and the least to set when none is.
3. **Test** every chosen plug with its read-only `check()`, and a SQL-check plug also with
   its `check_sql_session()` (can its session resolve `SCHEMA.TABLE`?). A failure prints what
   failed and what fixes it, and goes back to the question that chose that plug.
4. **Write and confirm**: the file is printed, and written only on a yes.

Asked only at a terminal. Anywhere else the interview refuses (exit 2) and lists its
questions, so an assistant driving the run can ask the person instead. It never calls a model
and never reads a secret's value.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import re
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO

import yaml

from metrics_agent import plugs as _plugs
from metrics_agent import run_config as RC
from metrics_agent import sockets as S

REFUSED = 2
NONE = "none"

# Nothing here names a plug. What each plug offers, which settings it needs, the dialect its
# runner speaks and the variables it needs for each use are the plug's own declarations
# (`sockets.SetupQuestion` and the notes beside it), so a plug from any package is asked
# about exactly as the core's own are. `files` is the core's "publish nowhere" output.
NOWHERE = "files"
NO_SQL_CHECK = ("Without a SQL check nothing is compiled: every statement ships `parse_only`, "
                "and SQL that parses but cannot run (a column its inner query does not return, "
                "a table that is not there) goes unnoticed.")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TABLE = re.compile(r"^[^.\s]+\.[^.\s]+\.[^.\s]+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Aborted(RuntimeError):
    """The person stopped the interview (end of input, or no at the confirmation)."""


# ── asking ───────────────────────────────────────────────────────────────────────────────
class Prompter:
    """Questions at a terminal. Input and output are injectable, so tests answer through a
    string exactly as a person types."""

    def __init__(self, stdin: TextIO | None = None, out: TextIO | None = None) -> None:
        self.stdin = stdin or sys.stdin
        self.out = out or sys.stderr

    def say(self, msg: str = "") -> None:
        print(msg, file=self.out)

    def _line(self, prompt: str) -> str:
        print(prompt, end="", file=self.out, flush=True)
        raw = self.stdin.readline()
        if raw == "":
            print("", file=self.out)
            raise Aborted("end of input")
        return raw.strip()

    def choose(self, question: str, options: list[tuple[str, str]], *, multi: bool = False,
               default: Any = None) -> Any:
        """One value (or, with `multi`, a list) from `options` [(value, label)]. Answered by
        number or by value; several are comma-separated."""
        self.say(f"\n{question}")
        for i, (value, label) in enumerate(options, 1):
            self.say(f"  {i}) {value}" + (f"  {label}" if label else ""))
        values = [v for v, _ in options]
        shown = (",".join(default) if isinstance(default, list) else default) or ""
        hint = "numbers or names, comma-separated" if multi else "a number or a name"
        while True:
            raw = self._line(f"[{hint}]" + (f" (Enter for {shown})" if shown else "") + ": ")
            if not raw and default not in (None, [], ""):
                return default
            picked = []
            for part in [p.strip() for p in raw.split(",") if p.strip()]:
                if part.isdigit() and 1 <= int(part) <= len(values):
                    picked.append(values[int(part) - 1])
                elif part in values:
                    picked.append(part)
                else:
                    picked = []
                    break
            if picked and (multi or len(picked) == 1):
                return list(dict.fromkeys(picked)) if multi else picked[0]
            self.say("  not one of the options" + ("" if multi else ", or more than one"))

    def text(self, question: str, *, default: str | None = None, required: bool = True,
             valid: Callable[[str], str | None] | None = None) -> str:
        while True:
            raw = self._line(f"{question}" + (f" [{default}]" if default else "") + ": ")
            value = raw or (default or "")
            if not value and not required:
                return ""
            if not value:
                self.say("  an answer is needed (there is no default)")
                continue
            why = valid(value) if valid else None
            if why:
                self.say(f"  {why}")
                continue
            return value

    def confirm(self, question: str) -> bool:
        while True:
            raw = self._line(f"{question} [y/n]: ").lower()
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            self.say("  please answer y or n")


def at_terminal() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


# ── detecting ────────────────────────────────────────────────────────────────────────────
@dataclass
class Plug:
    name: str
    kind: str
    cls: Any
    package: str
    custody: str | None = None
    capabilities: tuple[str, ...] = ()
    env_override: tuple[str, ...] | None = None        # tests: pretend these are unset
    custodian: str | None = None                       # who holds a third-party plug's data

    def missing(self, use: str | None = None) -> tuple[str, ...]:
        """The variables still unset for `use`. Names only; never raises."""
        if self.env_override is not None:
            return self.env_override
        try:
            return S.env_missing_for(self.cls, use)
        except Exception as exc:  # noqa: BLE001 - a broken declaration is said, not raised
            return (f"(could not tell: {type(exc).__name__})",)

    def routes(self) -> tuple[S.CredentialRoute, ...]:
        """The ways the plug signs in, as it declares them (`credential_routes`)."""
        return tuple(getattr(self.cls, "credential_routes", None) or ())

    def offered(self, use: str) -> bool:
        """A plug that signs in several ways is offered whatever is set: the interview lists
        its routes and its check finds the one in use. Any other is offered once every
        variable it needs for `use` is set."""
        return bool(self.routes()) or not self.missing(use)

    def questions(self, role: str) -> tuple[S.SetupQuestion, ...]:
        return tuple((getattr(self.cls, "setup_questions", None) or {}).get(role) or ())


@dataclass
class Detected:
    plugs: dict[str, Plug] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def inputs(self, capability: str, *, third_party_ok: bool) -> list[Plug]:
        return [p for p in self.plugs.values()
                if p.kind == "input" and capability in p.capabilities
                and (third_party_ok or not _third_party(p))]

    def custody(self, name: str) -> str | None:
        p = self.plugs.get(name)
        return p.custody if p else None

    def of_kind(self, kind: str) -> list[Plug]:
        return [p for p in self.plugs.values() if p.kind == kind]


def detect() -> Detected:
    """The installed plugs, and which environment variables each still needs (names only)."""
    det = Detected()
    try:
        installed = _plugs.installed()
    except _plugs.PlugError as exc:
        det.errors.append(str(exc))
        return det
    for name, item in sorted(installed.items()):
        try:
            cls = item.load()
        except _plugs.PlugError as exc:
            det.errors.append(str(exc))
            continue
        det.plugs[name] = Plug(name, cls.kind, cls, item.package,
                               getattr(cls, "custody", None),
                               S.capabilities(cls) if cls.kind == "input" else (),
                               custodian=getattr(cls, "custodian", None))
    return det


def _third_party(p: Plug) -> bool:
    return p.custody is not None and p.custody != S.CUSTOMER


def _custodians(det: Detected) -> str:
    """Who holds the data the installed third-party plugs read, as they name it."""
    names = sorted({p.custodian or "a third party" for p in det.of_kind("input")
                    if _third_party(p)})
    return " or ".join(names) or "a third party"


# ── the interview ────────────────────────────────────────────────────────────────────────
@dataclass
class Answers:
    third_party_ok: bool = False
    # Several plugs may serve one capability: each list is in the order chosen.
    history: list[str] = field(default_factory=list)
    bi: list[str] = field(default_factory=list)
    sql_check: list[str] = field(default_factory=list)
    schema_extra: list[str] = field(default_factory=list)
    dialect: str = ""
    publish: list[str] = field(default_factory=list)
    observers: list[str] = field(default_factory=list)
    scope: str = ""
    tables: list[str] = field(default_factory=list)
    window: dict[str, str] = field(default_factory=dict)
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)


class Interview:
    def __init__(self, p: Prompter, det: Detected, prior: dict | None = None) -> None:
        self.p, self.det, self.prior = p, det, prior or {}
        self.a = Answers()
        self.tables_required = False

    # -- helpers ------------------------------------------------------------------------
    def _prior_input(self, cap: str) -> str | None:
        """The earlier file's answer; `none` when it chose nothing for `cap`."""
        ins = self.prior.get("inputs")
        if not ins:
            return None
        v = ins.get(cap)
        v = (v[0] if v else None) if isinstance(v, list) else v
        return v or NONE

    def _prior_inputs(self, cap: str) -> list[str] | None:
        """The earlier file's answers for `cap`, in order; [`none`] when it chose nothing."""
        ins = self.prior.get("inputs")
        if not ins:
            return None
        v = ins.get(cap)
        v = [x for x in (v if isinstance(v, list) else [v]) if x]
        return v or [NONE]

    def _prior_setting(self, plug: str, key: str) -> Any:
        return ((self.prior.get("settings") or {}).get(plug) or {}).get(key)

    def _offer(self, question: str, plugs: list[Plug], use: str, *, none_label: str | None,
               default: str | None = None) -> str | None:
        ready = [p for p in plugs if p.offered(use)]
        for p in plugs:
            if not p.offered(use):
                self.p.say(f"  (not offered: {p.name} needs {', '.join(p.missing(use))})")
        options = [(p.name, f"({p.package})") for p in ready]
        if none_label:
            options.append((NONE, none_label))
        if not options:
            return None
        if len(options) == 1 and none_label is None:
            self.p.say(f"\n{question}\n  only {options[0][0]} is available: using it")
            return options[0][0]
        valid = [v for v, _ in options]
        picked = self.p.choose(question, options,
                               default=default if default in valid else None)
        return None if picked == NONE else picked

    def _offer_many(self, question: str, plugs: list[Plug], use: str, *, none_label: str,
                    default: list[str] | None = None) -> list[str]:
        """One or more plugs, in the order given; `none` alone for none. Where several plugs
        may serve one capability, the person may pick several."""
        ready = [p for p in plugs if p.offered(use)]
        for p in plugs:
            if not p.offered(use):
                self.p.say(f"  (not offered: {p.name} needs {', '.join(p.missing(use))})")
        options = [(p.name, f"({p.package})") for p in ready] + [(NONE, none_label)]
        valid = [v for v, _ in options]
        default = [d for d in (default or []) if d in valid] or None
        while True:
            picked = self.p.choose(question + " (one or more, in order)", options, multi=True,
                                   default=default)
            if NONE in picked and len(picked) > 1:
                self.p.say(f"  `{NONE}` means none; pick it alone, or pick plugs")
                continue
            return [] if NONE in picked else picked

    def _say_routes(self, plug: str) -> None:
        """A plug that signs in several ways is not asked which: its routes are listed, with
        which variables are set, and its check (at the end) says the route it found."""
        p = self.det.plugs.get(plug)
        if p and p.routes():
            for line in route_lines(plug, p.routes()):
                self.p.say(line)

    def _spec(self, plug: str, role: str) -> tuple[S.SetupQuestion, ...]:
        p = self.det.plugs.get(plug)
        return p.questions(role) if p else ()

    def _clear(self, plug: str | None, role: str) -> None:
        """Forget what an earlier answer to this question set, before it is asked again."""
        if not plug:
            return
        extra = ("witness_mode",) if role == "sql_check" else ()
        for key in [q.key for q in self._spec(plug, role)] + list(extra):
            self.a.settings.get(plug, {}).pop(key, None)

    def _settings(self, plug: str, role: str) -> None:
        mine = self.a.settings.setdefault(plug, {})
        for q in self._spec(plug, role):
            prior = self._prior_setting(plug, q.key)
            if q.kind == "choice":
                value = self.p.choose(f"  {q.text}", [(c, "") for c in q.choices],
                                      default=prior if prior in q.choices else None)
            else:
                shown = ",".join(prior) if isinstance(prior, list) else prior
                value = self.p.text(f"  {q.text}", default=shown, required=q.required,
                                    valid=_path_check if q.kind == "path" else None)
                if q.kind == "list" and value:
                    value = _split(value)
            if value:
                mine[q.key] = value
            else:
                mine.pop(q.key, None)

    # -- the questions, in order --------------------------------------------------------
    def ask_custody(self) -> None:
        if not any(_third_party(p) for p in self.det.of_kind("input")):
            self.a.third_party_ok = False
            return
        prior = self.prior.get("mode")
        who = _custodians(self.det)
        self.a.third_party_ok = self.p.choose(
            f"Can this run read data through {who} (their systems hold or relay it)?",
            [("no", f"standalone: nothing passes through {who}"),
             ("yes", "their plugs may be offered below (a connected run)")],
            default={"standalone": "no", "connected": "yes"}.get(prior)) == "yes"

    def ask_history(self) -> None:
        for plug in self.a.history:
            self._clear(plug, "history")
        self.a.history = self._offer_many(
            "Where does the query history come from? Several sources are read as ONE corpus",
            self.det.inputs("query_history", third_party_ok=self.a.third_party_ok), "query_history",
            none_label="no query history (BI definitions only)",
            default=self._prior_inputs("history"))
        for plug in self.a.history:
            self._say_routes(plug)
            self._settings(plug, "history")

    def ask_bi(self) -> None:
        for plug in self.a.bi:
            self._clear(plug, "bi")
        self.a.bi = self._offer_many(
            "Do you use a BI tool? Where are its definitions? Several tools may be read together",
            self.det.inputs("bi_definitions", third_party_ok=self.a.third_party_ok), "bi_definitions",
            none_label="no BI tool", default=self._prior_inputs("bi"))
        for plug in self.a.bi:
            self._say_routes(plug)
            self._settings(plug, "bi")

    def ask_sql_check(self) -> None:
        for plug in self.a.sql_check:
            self._clear(plug, "sql_check")
            self.a.settings.get(plug, {}).pop("databases", None)
        runners = self.det.inputs("sql_runner", third_party_ok=self.a.third_party_ok)
        # ASKED FOR BY DEFAULT. A run with BI definitions or query history and no SQL check
        # ships every statement unchecked; the default is the check, and declining it is the
        # person's explicit answer (recorded in run.yml), never a silent fallback. It stays a
        # choice: some estates have no warehouse access.
        self.p.say(f"\n{NO_SQL_CHECK}")
        ready = [p.name for p in runners if p.offered("sql_runner")]
        default = self._prior_inputs("sql_check")
        if default is None and ready:
            # The plug already chosen to read this run's inputs, else the first ready one.
            mine = [n for n in ready if n in (*self.a.history, *self.a.bi)]
            default = [(mine or ready)[0]]
        self.a.sql_check = self._offer_many(
            "Which plug checks the SQL (runs EXPLAIN on a read-only connection)? Several "
            "plugs each check the databases they hold",
            runners, "sql_runner", none_label="none: parse only, nothing is compiled",
            default=default)
        if not self.a.sql_check:
            self.p.say("  No SQL check: run.yml records it (`witness_mode: parse`), and the "
                       "run says so in its inputs and when it ends.")
        for plug in self.a.sql_check:
            self._settings(plug, "sql_check")
            if len(self.a.sql_check) > 1:
                # Each statement goes to the plug whose databases hold every table it reads.
                prior = self._prior_setting(plug, "databases")
                shown = ",".join(prior) if isinstance(prior, list) else prior
                dbs = self.p.text(f"  The databases {plug} checks (comma-separated)",
                                  default=shown)
                self.a.settings.setdefault(plug, {})["databases"] = _split(dbs)
        if self.a.sql_check:
            first = self.a.sql_check[0]
            prior = self._prior_setting(first, "witness_mode")
            mode = self.p.choose(
                "  How should each query be checked?",
                [("explain", "plan it, read nothing"),
                 ("execute", "run it read-only with a row limit")], default=prior)
            self.a.settings.setdefault(first, {})["witness_mode"] = mode

    def ask_schema(self) -> None:
        """Column types and descriptions from a plug other than the SQL check: offered for
        every input plug that supplies schemas and declares the settings to find them."""
        for plug in self.a.schema_extra:
            self._clear(plug, "schema")
        self.a.schema_extra = []
        others = [p for p in self.det.inputs("table_schema", third_party_ok=self.a.third_party_ok)
                  if p.name not in self.a.sql_check and p.questions("schema")
                  and not p.missing("table_schema")]
        if not others:
            return
        prior = [n for n in ((self.prior.get("inputs") or {}).get("schema") or [])
                 if n in {p.name for p in others}]
        picked = self.p.choose(
            "Also read column types and descriptions from:",
            [(p.name, f"({p.package})") for p in others] + [(NONE, "no")], multi=True,
            default=prior or ([NONE] if self.prior else None))
        self.a.schema_extra = [] if NONE in picked else picked
        for plug in self.a.schema_extra:
            self._settings(plug, "schema")

    def ask_dialect(self) -> None:
        runner = self.det.plugs.get((self.a.sql_check or [""])[0])
        offered = (getattr(runner.cls, "default_dialect", None) if runner else None) \
            or self.prior.get("dialect")
        self.a.dialect = self.p.text(
            "\nThe SQL dialect of this estate (" + ", ".join(sorted(_known_dialects())) + ")",
            default=offered, valid=_dialect_check)

    def ask_publish(self) -> None:
        for plug in self.a.publish:
            self._clear(plug, "publish")
        outs = [p for p in self.det.of_kind("output") if p.name != NOWHERE]
        for p in outs:
            if p.missing("publish"):
                self.p.say(f"  (not offered: {p.name} needs {', '.join(p.missing('publish'))})")
        options = [(p.name, f"({p.package})") for p in outs if not p.missing("publish")]
        options.append((NOWHERE, "nowhere: results stay in the run directory"))
        while True:
            picked = self.p.choose("Where should results go? (there is no default)", options,
                                   multi=True)
            if NOWHERE in picked and len(picked) > 1:
                self.p.say(f"  `{NOWHERE}` means nowhere; pick it alone, or pick destinations")
                continue
            break
        self.a.publish = picked
        for plug in picked:
            self._settings(plug, "publish")

    def ask_observers(self) -> None:
        obs = [p for p in self.det.of_kind("observer")
               if not getattr(p.cls, "offered_with", None)
               or set(p.cls.offered_with) & set(self.a.publish)]
        if not obs:
            self.a.observers = []
            return
        options = [(p.name, f"({p.package})" + (f" needs {', '.join(p.missing('observe'))}"
                                                if p.missing("observe") else "")) for p in obs]
        options.append((NONE, "no tracing"))
        prior = [n for n in (self.prior.get("observers") or []) if n in {p.name for p in obs}]
        picked = self.p.choose("Trace this run? (pick any; tracing never stops a run)",
                               options, multi=True,
                               default=prior or ([NONE] if self.prior else None))
        self.a.observers = [] if NONE in picked else picked

    def ask_run(self) -> None:
        self.p.say("\nThe run itself.")
        self.a.scope = self.p.text(
            "  A short name for this estate (it prefixes every id the run writes)",
            default=self.a.scope or self.prior.get("scope"),
            valid=lambda v: None if _SLUG.match(v) else "letters, digits, - _ . only")
        tables = self.p.text(
            "  The tables in scope, DB.SCHEMA.TABLE, comma-separated"
            + ("" if self.tables_required else " (Enter for no bound)"),
            default=",".join(self.a.tables or self.prior.get("tables") or []) or None,
            required=self.tables_required,
            valid=lambda v: next((f"{t} is not DB.SCHEMA.TABLE" for t in _split(v)
                                  if not _TABLE.match(t)), None))
        self.a.tables = _split(tables)
        window = self.a.window or self.prior.get("window") or {}
        self.a.window = {}
        if self.a.history:
            for end in ("start", "end"):
                v = self.p.text(f"  History window {end}, YYYY-MM-DD (Enter for none)",
                                default=window.get(end), required=False,
                                valid=lambda d, end=end: _window_check(d, end, self.a.window))
                if v:
                    self.a.window[end] = v

    STEPS = ("ask_history", "ask_bi", "ask_sql_check", "ask_schema", "ask_dialect",
             "ask_publish", "ask_observers", "ask_run")

    # -- the document -------------------------------------------------------------------
    def chosen(self) -> dict[str, list[str]]:
        """Each chosen plug and the steps that chose it."""
        out: dict[str, list[str]] = {}
        for plug, step in (*((p, "ask_history") for p in self.a.history),
                           *((p, "ask_bi") for p in self.a.bi),
                           *((p, "ask_sql_check") for p in self.a.sql_check),
                           *((p, "ask_schema") for p in self.a.schema_extra),
                           *((p, "ask_publish") for p in self.a.publish),
                           *((p, "ask_observers") for p in self.a.observers)):
            if plug:
                out.setdefault(plug, []).append(step)
        return out

    def document(self) -> dict[str, Any]:
        a = self.a
        schema = [n for n in a.sql_check if "table_schema" in
                  (self.det.plugs[n].capabilities if n in self.det.plugs else ())]
        schema += [n for n in a.schema_extra if n not in schema]
        inputs: dict[str, Any] = {"history": list(a.history), "schema": schema,
                                  "bi": list(a.bi),
                                  "sql_check": (a.sql_check[0] if len(a.sql_check) == 1
                                                else list(a.sql_check) or None)}
        used = {*a.history, *a.bi, *a.sql_check, *schema}
        held = any(self.det.plugs[p].custody not in (None, S.CUSTOMER)
                   for p in used if p in self.det.plugs)
        doc: dict[str, Any] = {"version": 1, "scope": a.scope, "dialect": a.dialect,
                               "mode": "connected" if held else "standalone",
                               "inputs": inputs, "publish": a.publish,
                               "observers": a.observers}
        if not a.sql_check:
            # Declining the SQL check is recorded, not left implicit: the run reads it and says
            # in run_inputs.json and at its end that nothing was compiled.
            prior = self.prior.get("witness_mode")
            doc["witness_mode"] = prior if prior in ("parse", "none") else "parse"
        keep = used | set(a.publish) | set(a.observers)
        settings = {p: dict(v) for p, v in sorted(a.settings.items()) if p in keep and v}
        if settings:
            doc["settings"] = settings
        if a.window:
            doc["window"] = a.window
        if a.tables:
            doc["tables"] = a.tables
        return doc

    # -- running it ---------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        self.ask_custody()
        redo = list(self.STEPS)
        while True:
            for step in self.STEPS:
                if step in redo:
                    getattr(self, step)()
                while step == "ask_bi" and not (self.a.history or self.a.bi):
                    self.p.say("  A run needs query history, BI definitions, or both. "
                               "Choose again.")
                    self.ask_history()
                    self.ask_bi()
            doc = self.document()
            problem = RC.table_bound_problem(doc)
            if problem:
                self.p.say(f"  The tables in scope are needed: {problem}.")
                self.tables_required = True
                redo = ["ask_run"]
                continue
            failed = test(doc, self.p.say)
            if not failed:
                return doc
            redo = sorted({s for plug, fix in failed.items() for s in self._steps_for(plug, fix)},
                          key=self.STEPS.index)
            self.p.say("  Going back to the question that chose "
                       + ", ".join(sorted(failed)) + ".")

    _ROLE_STEP = {"history": "ask_history", "bi": "ask_bi", "sql_check": "ask_sql_check",
                  "schema": "ask_schema", "publish": "ask_publish"}

    def _steps_for(self, plug: str, fix: str) -> list[str]:
        """The questions to ask again for a failed plug: the one whose setting the fix
        names, else every question that chose the plug."""
        chose = self.chosen().get(plug, [])
        named_key = re.match(rf"settings\.{re.escape(plug)}\.([A-Za-z0-9_]+)", fix or "")
        key = named_key.group(1) if named_key else None
        p = self.det.plugs.get(plug)
        named = [self._ROLE_STEP[role] for role in self._ROLE_STEP
                 if p and any(q.key == key for q in p.questions(role))]
        return [s for s in named if s in chose] or chose


def route_lines(plug: str, routes: tuple[S.CredentialRoute, ...]) -> list[str]:
    """What the interview says about a plug that signs in several ways: every route with the
    variables it reads (names only, set or not), then the one found, or the least to set."""
    out = [f"  {plug} signs in whichever way is set up; there is nothing to choose:"]
    for r in routes:
        if r.env:
            unset = set(S.missing_env(r.env))
            out.append(f"    - {r.name}: " + ", ".join(
                f"{v} ({'not set' if v in unset else 'set'})" for v in r.env)
                + (f". {r.note}" if r.note else ""))
        else:
            out.append(f"    - {r.name}: nothing to set" + (f". {r.note}" if r.note else ""))
    found = next((r for r in routes if S.route_set(r)), None)
    if found:
        out.append(f"  Found: {found.name}. The check at the end says how complete the input "
                   "will be.")
        return out
    with_env = sorted((r for r in routes if r.env), key=lambda r: len(S.missing_env(r.env)))
    free = [r for r in routes if not r.env]
    least = []
    if with_env:
        least.append(f"set {', '.join(S.missing_env(with_env[0].env))} ({with_env[0].name})")
    if free:
        least.append(f"use {free[0].name}, with nothing to set")
    out.append("  Nothing is set yet. The least to do: " + ", or ".join(least) + ".")
    return out


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _known_dialects() -> list[str]:
    from metrics_agent.lib import dialect as _dialect  # noqa: PLC0415

    return list(_dialect.SUPPORTED)


def _dialect_check(value: str) -> str | None:
    from metrics_agent.lib import dialect as _dialect  # noqa: PLC0415

    try:
        _dialect.normalise(value)
    except _dialect.DialectError:
        return "not a dialect this agent reads; known: " + ", ".join(sorted(_dialect.SUPPORTED))
    return None


def _window_check(value: str, end: str, window: dict[str, str]) -> str | None:
    """A calendar date, and an end that is not before the start: the rules `run.yml` is
    loaded with, said at the question rather than after the whole interview."""
    if not _DATE.match(value):
        return "YYYY-MM-DD"
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        return f"{value} is not a calendar date (YYYY-MM-DD)"
    if end == "end" and window.get("start") and value < window["start"]:
        return f"the end {value} is before the start {window['start']}"
    return None


def _path_check(value: str) -> str | None:
    return None if pathlib.Path(value).expanduser().exists() else f"{value} does not exist"


# ── testing ──────────────────────────────────────────────────────────────────────────────
def _check_settings(doc: dict, plug: str) -> dict[str, Any]:
    settings = dict((doc.get("settings") or {}).get(plug) or {})
    if doc.get("tables") and plug in RC.inputs_of(doc, "sql_check"):
        settings.setdefault("tables", doc["tables"])
    return settings


def plugs_of(doc: dict) -> list[str]:
    ins = doc.get("inputs") or {}
    names = [n for cap in ("history", "schema", "bi", "scope") for n in ins.get(cap) or []]
    names += RC.inputs_of(doc, "sql_check")
    names += list(doc.get("publish") or []) + list(doc.get("observers") or [])
    return list(dict.fromkeys(names))


def test(doc: dict, say: Callable[[str], None] = print) -> dict[str, str]:
    """Check every plug `doc` names. Returns each failed plug with its fix; prints each
    result."""
    say("\nTesting each plug (read-only):")
    failed: dict[str, str] = {}
    checks = RC.inputs_of(doc, "sql_check")
    for name in plugs_of(doc):
        try:
            cls = _plugs.load(name)
        except _plugs.PlugError as exc:
            say(f"  FAIL {name}: {exc}")
            failed[name] = ""
            continue
        if not callable(getattr(cls, "check", None)):
            say(f"  ok   {name}: nothing to check")
            continue
        settings = _check_settings(doc, name)
        plug = None
        try:
            plug = cls(settings, dialect=doc.get("dialect") or "") if cls.kind != "observer" \
                else cls()
            result = plug.check(settings)
        except Exception as exc:  # noqa: BLE001 - a check that raises is a failed check
            result = S.CheckResult(False, f"the check raised {type(exc).__name__}")
        if result.ok and name in checks and callable(getattr(plug, "check_sql_session", None)):
            # As the SQL check, the plug's session must also resolve a statement that names
            # SCHEMA.TABLE (logged and BI statements often do). How to ask is the plug's own.
            say(f"  ok   {name}: {result.message}")
            try:
                result = plug.check_sql_session(settings)
            except Exception as exc:  # noqa: BLE001 - a check that raises is a failed check
                result = S.CheckResult(False, f"the SQL-check session check raised "
                                              f"{type(exc).__name__}")
            name_shown = f"{name} (as the SQL check)"
        else:
            name_shown = name
        detail = "; ".join(x for x in (getattr(result, "route", "") and f"route: {result.route}",
                                       getattr(result, "coverage", "")
                                       and f"coverage: {result.coverage}") if x)
        if detail:
            name_shown += f" [{detail}]"
        if result.ok:
            say(f"  ok   {name_shown}: {result.message}")
        else:
            say(f"  FAIL {name_shown}: {result.message}" + (f"\n       fix: {result.fix}"
                                                             if result.fix else ""))
            failed[name] = result.fix
    return failed


# ── writing ──────────────────────────────────────────────────────────────────────────────
def render(doc: dict) -> str:
    head = (f"# run.yml, written by `metrics-agent setup` on {_dt.date.today().isoformat()}.\n"
            "# Secrets never go here: each plug reads them from the environment.\n")
    if not RC.inputs_of(doc, "sql_check"):
        head += ("# No SQL check: nothing is compiled, and every statement "
                 "ships parse_only.\n")
    return head + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def validate(doc: dict) -> None:
    """Load the document exactly as a run will. Raises `run_config.ConfigError`."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "run.yml"
        path.write_text(render(doc))
        RC.validate(RC.load(path))


def open_questions(det: Detected) -> list[str]:
    """What the interview would ask on this machine, for an assistant to put to the person:
    the questions, the plugs each offers (and what the others still need), and the settings
    each plug asks for. Built from the plugs' own declarations."""
    def names(ps: list[Plug], use: str) -> str:
        return ", ".join(p.name + (f" (needs {', '.join(p.missing(use))})"
                                   if not p.offered(use) else "") for p in ps) or "none installed"
    outs = [p for p in det.of_kind("output") if p.name != NOWHERE]
    qs = []
    if any(_third_party(p) for p in det.of_kind("input")):
        qs.append(f"Can this run read data through {_custodians(det)}? (yes / no)")
    qs += [
        "Where does the query history come from? One or more, read as ONE corpus in order "
        f"({names(det.inputs('query_history', third_party_ok=True), 'query_history')}, or none)",
        "Do you use a BI tool, and where are its definitions? One or more "
        f"({names(det.inputs('bi_definitions', third_party_ok=True), 'bi_definitions')}, or none)",
        "Which plug checks the SQL? One, or several that each check the databases they hold "
        f"({names(det.inputs('sql_runner', third_party_ok=True), 'sql_runner')}, or none: parse "
        "only); with several, the databases each checks; then explain or execute. Offer a "
        f"ready plug as the default and say first: {NO_SQL_CHECK} If the person declines, "
        "write `witness_mode: parse` in run.yml",
    ]
    extra = [p for p in det.inputs("table_schema", third_party_ok=True) if p.questions("schema")]
    if extra:
        qs.append("Also read column types and descriptions from: "
                  f"({names(extra, 'table_schema')}, or no)")
    qs += [
        "The SQL dialect (one of: " + ", ".join(sorted(_known_dialects())) + ")",
        f"Where should results go? ({names(outs, 'publish')}, or {NOWHERE}: nowhere). "
        "There is no default.",
    ]
    if det.of_kind("observer"):
        qs.append(f"Trace this run? ({names(det.of_kind('observer'), 'observe')}, or none)")
    for p in det.of_kind("input"):
        if p.routes():
            qs.append(f"Do NOT ask which credentials the person has for {p.name}. Tell them "
                      "any one of these works, then run `metrics-agent setup --check` and "
                      "report the route and coverage it prints:\n     "
                      + "\n     ".join(line.strip() for line in route_lines(p.name, p.routes())[1:]))
    qs += ["A short name for this estate (the run's scope)",
           "The tables in scope (DB.SCHEMA.TABLE), and the history window, if any"]
    settings = []
    for p in det.plugs.values():
        for role, questions in (getattr(p.cls, "setup_questions", None) or {}).items():
            for q in questions:
                how = {"path": "a path that exists", "list": "comma-separated",
                       "choice": " | ".join(q.choices)}.get(q.kind, "text")
                settings.append(f"settings.{p.name}.{q.key} (as {role}; {how}"
                                f"{'' if q.required else ', optional'}): {q.text}")
    if settings:
        qs.append("Settings each plug asks for, under `settings.<plug>` in run.yml:\n     "
                  + "\n     ".join(settings))
    return qs


def refusal(det: Detected, out: TextIO | None = None) -> int:
    out = out or sys.stderr
    print("metrics-agent setup: REFUSED — no terminal to ask at. Put these questions to the "
          "person, then write run.yml (or run `metrics-agent setup` at a terminal):",
          file=out)
    for i, q in enumerate(open_questions(det), 1):
        print(f"  {i}. {q}", file=out)
    # A plug that could not be loaded is said here too, as the interview says it: without it
    # an impostor or a broken package explains nothing about why its plug is missing.
    for err in det.errors:
        print(f"  (a plug could not be loaded: {err})", file=out)
    return REFUSED


def interview(out_path: pathlib.Path, p: Prompter | None = None,
              det: Detected | None = None) -> int:
    """Ask, test, show, confirm, write. 0 written; 1 stopped by the person; 2 refused."""
    det = det or detect()
    p = p or Prompter()
    for err in det.errors:
        p.say(f"  (a plug could not be loaded: {err})")
    prior = None
    if out_path.is_file():
        try:
            prior = RC.load(out_path)
            p.say(f"Using {out_path} for defaults; nothing is written until you confirm.")
        except RC.ConfigError as exc:
            p.say(f"  ({out_path} is not a valid run.yml, so it is not used for defaults: {exc})")
    try:
        iv = Interview(p, det, prior)
        while True:
            doc = iv.run()
            try:
                validate(doc)
                break
            except RC.ConfigError as exc:
                p.say(f"  This combination cannot run: {exc}\n  Choose again.")
        p.say("\n" + render(doc))
        if not p.confirm(f"Write this to {out_path}?"):
            p.say("Nothing written.")
            return 1
    except Aborted:
        p.say("Stopped. Nothing written.")
        return 1
    except KeyboardInterrupt:
        # Ctrl-C is the person stopping, as end of input is: said, never a traceback. The
        # cursor is still on the prompt's line.
        p.say("\nStopped. Nothing written.")
        return 1
    out_path.write_text(render(doc))
    p.say(f"Wrote {out_path}. Run it with: metrics-agent run --run output/<name> "
          f"--config {out_path}")
    return 0


def check_file(path: pathlib.Path, out: TextIO | None = None) -> int:
    """`--check`: test an existing run.yml's plugs. 0 all pass, 1 one failed, 2 invalid."""
    out = out or sys.stdout
    try:
        doc = RC.load(path)
        RC.validate(doc, settings=False)      # each plug's check says what is wrong with its own
    except RC.ConfigError as exc:
        print(f"metrics-agent setup --check: {exc}", file=out)
        return REFUSED
    failed = test(doc, lambda m: print(m, file=out))
    problem = RC.table_bound_problem(doc)
    if problem:
        print(f"  FAIL run: {problem}\n       fix: tables (DB.SCHEMA.TABLE, in run.yml)",
              file=out)
    print("all plugs passed" if not (failed or problem)
          else f"{len(failed) + bool(problem)} check(s) failed", file=out)
    return 1 if failed or problem else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="metrics-agent setup",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="run.yml", help="where to write (default ./run.yml)")
    ap.add_argument("--check", metavar="RUN_YML", default=None,
                    help="test an existing run.yml's plugs and ask nothing")
    a = ap.parse_args(argv)
    if a.check:
        return check_file(pathlib.Path(a.check).expanduser())
    if not at_terminal():
        return refusal(detect())
    return interview(pathlib.Path(a.out).expanduser())
