# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The conformance kit: the socket contract as tests a plug author runs against their plug.

Subclass the class for your plug's kind in one of your test modules, say what the plug is and
how to configure it, and run pytest. Every test below is inherited:

    from metrics_agent.testing.conformance import InputPlugConformance
    from my_package import MyWarehouse

    class TestMyWarehouse(InputPlugConformance):
        plug = MyWarehouse
        settings = {"project": "example-project"}             # settings that work
        env = {"MY_WAREHOUSE_KEY": "a-test-key"}              # the environment that works
        broken = {                                            # each way the check can fail
            "no project": {"settings": {"project": None},
                           "fix": "settings.my-warehouse.project"},
            "no key": {"unset": ("MY_WAREHOUSE_KEY",), "fix": "MY_WAREHOUSE_KEY"},
        }
        tables = ["example-project.sales.orders"]             # what capabilities are asked for

The kit runs offline. Every call into the plug happens inside a sandbox that refuses network
connections and child processes and records them, records any statement the read-only check
refuses, runs from an empty working directory with an empty home directory, and compares the
files under the test's temporary directory and under every path named in the settings before
and after, so a write anywhere it should not be fails the test. A plug that talks to a remote
system is therefore tested against a fake of it, which the test module provides (by
monkeypatching the function that connects, for example). A capability that can only be shown
against the real system goes in `needs_live` with the reason, and is skipped, saying why.

Author hooks, all optional except `plug`:

    plug          the plug class
    settings      settings under which `check` passes and every declared capability answers
    env           environment variables (names to values) for those settings
    broken        {label: case}: a case may set `settings` (merged over the working ones; a
                  value of None removes the key), `env` (set), `unset` (names removed), and
                  must set `fix`, a piece of text the check's `fix` must contain
    needs_live    {name: reason}: `check`, `check_sql_session`, an input capability,
                  `resolve`, `plan`, `publish` or `events`, skipped with the reason
    dialect       the dialect the plug is built with (default: its `default_dialect`, else
                  duckdb)
    tables        tables passed to `query_history`, `table_schema` and `columns_of_many`
    window        the `Window` passed to `query_history`
    assets        asset references passed to `bi_definitions`
    scope_refs    references passed to `resolve_scope`
    probe_sql     the read the SQL runner is asked to run (default `SELECT 1 AS one`)

Overridable fixtures, for values that need the test's temporary directory:

    plug_settings(tmp_path)      default: a copy of `settings`
    plug_environment(tmp_path)   default: a copy of `env`
    bundle(tmp_path)             (outputs) default: a minimal valid bundle in tmp_path/run
"""
from __future__ import annotations

import argparse
import copy
import inspect
import itertools
import json
import os
import pathlib
import re
import secrets
import socket
import stat
import subprocess
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar
from unittest import mock

import jsonschema
import pytest

from metrics_agent import plugs as _plugs
from metrics_agent import socket_gates as G
from metrics_agent import sockets as S
from metrics_agent.lib import artifacts, dialect as _dialect, sqlshape

CHECK_SECONDS = 10
"""How long `check` may take. The set-up interview runs it while a person waits."""

ROLES = {"history": "query_history", "bi": "bi_definitions", "sql_check": "sql_runner",
         "schema": "table_schema", "scope": "resolve_scope"}
"""The set-up interview's input roles, and the capability each one asks a plug for."""

QUESTION_KINDS = ("text", "path", "list", "choice")
ENV_USES = (None, "query_history", "table_schema", "bi_definitions", "sql_runner",
            "resolve_scope", "publish", "observe")
MAX_DESCRIPTION_CHARS = 40
MAX_RECORDS = 1000

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


# ── the sandbox ──────────────────────────────────────────────────────────────────────────
@dataclass
class Observed:
    """What a plug did inside the sandbox that it should not have."""
    network: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)     # statements that are not pure reads
    changed: list[str] = field(default_factory=list)    # files changed where it may not write
    seconds: float = 0.0

    def problems(self) -> list[str]:
        out = []
        if self.network:
            out.append("reached for the network (the kit runs offline; fake the remote "
                       "system): " + "; ".join(self.network[:5]))
        if self.processes:
            out.append("started a process (the kit cannot see what a child process sends; "
                       "fake it): " + "; ".join(self.processes[:5]))
        if self.writes:
            out.append("sent a statement that is not a pure read: "
                       + "; ".join(w[:120] for w in self.writes[:5]))
        if self.changed:
            out.append("wrote outside where it may: " + ", ".join(self.changed[:10]))
        return out


def _snapshot(roots: list[pathlib.Path], limit: int = 20000) -> dict[str, tuple]:
    seen: dict[str, tuple] = {}
    for root in roots:
        if not root.exists():
            continue
        items = [root] if not root.is_dir() else itertools.chain([root], root.rglob("*"))
        for p in items:
            if "__pycache__" in p.parts:
                continue
            try:
                st = p.lstat()
            except OSError:
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            # A directory's own time changes with what is in it, which is compared anyway.
            seen[str(p)] = (True, 0, 0) if is_dir else (False, st.st_size, st.st_mtime_ns)
            if len(seen) >= limit:
                break
    return seen


def _under(path: str, roots: list[pathlib.Path]) -> bool:
    p = pathlib.Path(path)
    return any(p == r or r in p.parents for r in roots)


def setting_paths(settings: Any) -> list[pathlib.Path]:
    """Every existing file or folder a settings value names, at any depth."""
    out: list[pathlib.Path] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, (str, os.PathLike)) and str(v) and (
                os.sep in str(v) or str(v).startswith((".", "~"))):
            try:
                p = pathlib.Path(str(v)).expanduser().resolve()
            except (OSError, RuntimeError):
                return
            if p.exists() and p != pathlib.Path(p.anchor):
                out.append(p)
    walk(settings)
    return out


class Sandbox:
    """Run plug code offline, read-only and confined. Use as `with Sandbox(...) as seen:`.

    `root` is the test's temporary directory (watched, and holding the empty working and home
    directories); `watch` adds paths whose contents must not change; `allowed` are the
    folders the plug may write into (`dest` for BI definitions, the run directory for an
    output or an observer). `guard` is a plug class whose `sql_runner` is wrapped in the
    socket's `ReadOnlyRunner` for the duration, as the core always wraps it.
    """

    def __init__(self, root: pathlib.Path, *, watch: list[pathlib.Path] = (),
                 allowed: list[pathlib.Path] = (), guard: type | None = None,
                 dialect: str = "duckdb") -> None:
        self.root = pathlib.Path(root).resolve()
        self.watch = [pathlib.Path(p).resolve() for p in watch]
        self.allowed = [pathlib.Path(p).resolve() for p in allowed]
        self.guard = guard
        self.dialect = dialect
        self.seen = Observed()
        self._patches: list[Any] = []

    # -- what is refused and recorded ----------------------------------------------------
    def _refuse_network(self, what: str) -> Callable:
        def refuse(*args: Any, **kwargs: Any):
            target = args[1] if what.startswith("socket.") and len(args) > 1 else (
                args[0] if args else kwargs.get("host", ""))
            self.seen.network.append(f"{what}({target!r})")
            raise ConnectionRefusedError(f"conformance kit: {what} refused, the kit runs "
                                         "offline")
        return refuse

    def _refuse_process(self, what: str) -> Callable:
        def refuse(*args: Any, **kwargs: Any):
            cmd = args[0] if args else kwargs.get("args", "")
            self.seen.processes.append(f"{what}({str(cmd)[:120]})")
            raise OSError(f"conformance kit: {what} refused, the kit runs no child process")
        return refuse

    def _watch_reads(self) -> Callable:
        real = sqlshape.has_dml

        def has_dml(sql: str, dialect: str) -> bool:
            flagged = real(sql, dialect)
            if flagged:
                self.seen.writes.append(str(sql))
            return flagged
        return has_dml

    def _guarded_runner(self, cls: type) -> Callable:
        original = cls.sql_runner
        default = self.dialect

        def sql_runner(plug: Any) -> Any:
            runner = original(plug)
            if runner is None:
                return None
            return G.ReadOnlyRunner(runner, getattr(plug, "dialect", None) or default)
        return sql_runner

    # -- lifecycle -----------------------------------------------------------------------
    def __enter__(self) -> Observed:
        work, home = self.root / ".conformance-cwd", self.root / ".conformance-home"
        work.mkdir(exist_ok=True)
        home.mkdir(exist_ok=True)
        self._roots = [self.root, *self.watch]
        self._before = _snapshot(self._roots)
        targets: list[Any] = [
            mock.patch.object(socket.socket, "connect", self._refuse_network("socket.connect")),
            mock.patch.object(socket.socket, "connect_ex",
                              self._refuse_network("socket.connect_ex")),
            mock.patch.object(socket, "create_connection",
                              self._refuse_network("create_connection")),
            mock.patch.object(socket, "getaddrinfo", self._refuse_network("getaddrinfo")),
            mock.patch.object(subprocess, "Popen", self._refuse_process("subprocess.Popen")),
            mock.patch.object(os, "system", self._refuse_process("os.system")),
            mock.patch.object(sqlshape, "has_dml", self._watch_reads()),
            mock.patch.dict(os.environ, {"HOME": str(home)}),
        ]
        if hasattr(os, "posix_spawn"):
            targets.append(mock.patch.object(os, "posix_spawn",
                                             self._refuse_process("os.posix_spawn")))
        cls = self.guard
        if cls is not None and isinstance(inspect.getattr_static(cls, "sql_runner", None),
                                          types.FunctionType):
            targets.append(mock.patch.object(cls, "sql_runner", self._guarded_runner(cls)))
        for p in targets:
            p.start()
            self._patches.append(p)
        self._cwd = os.getcwd()
        os.chdir(work)
        self._t0 = time.monotonic()
        return self.seen

    def __exit__(self, *exc: Any) -> None:
        self.seen.seconds = time.monotonic() - self._t0
        os.chdir(self._cwd)
        for p in reversed(self._patches):
            p.stop()
        self._patches.clear()
        after = _snapshot(self._roots)
        changed = sorted(k for k in set(self._before) | set(after)
                         if self._before.get(k) != after.get(k))
        self.seen.changed = [k for k in changed if not _under(k, self.allowed)]


# ── helpers ──────────────────────────────────────────────────────────────────────────────
def env_names(items: Any) -> set[str]:
    """The variable names in what `env_missing` or `missing_env` reports. An entry may name
    alternatives ("A+B or C"), so every name in it is taken."""
    out: set[str] = set()
    for item in items or ():
        for token in re.split(r"[^A-Za-z0-9_]+", str(item)):
            if token and token.upper() == token and _ENV_NAME.match(token) and \
                    token not in ("OR", "AND") and not token.isdigit():
                out.add(token)
    return out


def _fail(what: str, problems: list[str]) -> None:
    if problems:
        pytest.fail(f"{what}: " + " | ".join(problems), pytrace=False)


def _jsonable(value: Any, what: str) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        pytest.fail(f"{what} is not plain JSON ({exc}); the core records it as JSON",
                    pytrace=False)


def _validate(doc: Any, schema_name: str, what: str) -> None:
    schema = artifacts.schema(schema_name)
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        where = "/".join(str(p) for p in errors[0].path) or "(root)"
        pytest.fail(f"{what} does not meet {schema_name} at {where}: {errors[0].message}",
                    pytrace=False)


def minimal_bundle(run_dir: pathlib.Path) -> S.Bundle:
    """The smallest bundle the core can hand an output plug: no queries, no metrics, both
    files valid against the core's schemas."""
    run_dir.mkdir(parents=True, exist_ok=True)
    queries = {"contract_version": "1.0", "layer_sha": "conformance", "dialect": "duckdb",
               "generated_at": "2026-01-01T00:00:00Z", "scope": "conformance",
               "profile": "history-only", "objects": []}
    metrics = {"contract_version": "1.0", "spec_version": "1.0-draft.1", "metrics": []}
    _validate(queries, "verified_queries.schema.json", "the kit's minimal bundle")
    _validate(metrics, "metric_spec.schema.json", "the kit's minimal bundle")
    (run_dir / "verified_queries.json").write_text(json.dumps(queries, indent=2))
    (run_dir / "metric_cards.json").write_text(json.dumps(metrics, indent=2))
    return S.Bundle(run_dir, run_dir / "verified_queries.json", run_dir / "metric_cards.json")


class _StageFailed(Exception):
    """Raised inside a stage span to check the observer lets a stage's failure through."""


# ── what every plug kind shares ──────────────────────────────────────────────────────────
class _PlugConformance:
    kind: ClassVar[str] = ""
    plug: ClassVar[Any] = None
    settings: ClassVar[dict] = {}
    env: ClassVar[dict] = {}
    broken: ClassVar[dict] = {}
    needs_live: ClassVar[dict] = {}
    dialect: ClassVar[str | None] = None

    # -- fixtures an author may override -------------------------------------------------
    @pytest.fixture
    def plug_settings(self, tmp_path):
        return copy.deepcopy(dict(self.settings))

    @pytest.fixture
    def plug_environment(self, tmp_path):
        return dict(self.env)

    # -- plumbing ------------------------------------------------------------------------
    def _cls(self) -> type:
        if self.plug is None:
            pytest.fail(f"{type(self).__name__} sets no `plug`", pytrace=False)
        return self.plug

    def _dialect(self) -> str:
        return self.dialect or getattr(self._cls(), "default_dialect", None) or "duckdb"

    def _declared_env(self) -> tuple[str, ...]:
        return tuple(getattr(self._cls(), "requires_env", ()) or ())

    def _set_env(self, monkeypatch, values: dict | None, unset: tuple = ()) -> None:
        """Nothing the plug declares is taken from the shell running the tests."""
        for name in self._declared_env():
            monkeypatch.delenv(name, raising=False)
        for name, value in (values or {}).items():
            monkeypatch.setenv(name, str(value))
        for name in unset:
            monkeypatch.delenv(name, raising=False)

    def _make(self, settings: dict) -> Any:
        return self._cls()(settings, dialect=self._dialect())

    def _sandbox(self, tmp_path, settings: Any = None, *, allowed=(), guard=True) -> Sandbox:
        return Sandbox(tmp_path, watch=setting_paths(settings), allowed=list(allowed),
                       guard=self._cls() if guard else None, dialect=self._dialect())

    def _skip_live(self, name: str) -> None:
        if name in (self.needs_live or {}):
            pytest.skip(f"needs the live system: {self.needs_live[name]}")

    def _check(self, tmp_path, settings: dict, what: str) -> S.CheckResult:
        with self._sandbox(tmp_path, settings) as seen:
            try:
                result = self._make(settings).check(settings)
            except Exception as exc:  # noqa: BLE001 - the contract: a check never raises
                pytest.fail(f"check() {what} raised {type(exc).__name__}: {exc}; a check "
                            "returns a failed CheckResult instead", pytrace=False)
        _fail(f"check() {what}", seen.problems())
        assert seen.seconds <= CHECK_SECONDS, \
            f"check() {what} took {seen.seconds:.1f}s; a check takes seconds, not minutes"
        self._well_formed(result, what)
        return result

    def _well_formed(self, result: Any, what: str) -> None:
        assert isinstance(result, S.CheckResult), \
            f"check() {what} returned {type(result).__name__}, not a CheckResult"
        assert isinstance(result.ok, bool) and isinstance(result.message, str) and \
            isinstance(result.fix, str), f"check() {what}: ok, message and fix are bool, str, str"
        assert isinstance(result.missing_env, tuple) and \
            all(isinstance(n, str) for n in result.missing_env), \
            f"check() {what}: missing_env is a tuple of names"
        assert isinstance(getattr(result, "route", ""), str) and \
            isinstance(getattr(result, "coverage", ""), str), \
            f"check() {what}: route and coverage are str"
        if not result.ok:
            assert result.message.strip(), f"check() {what} failed without saying what failed"
            assert result.fix.strip() or result.missing_env, \
                f"check() {what} failed without naming the setting or variable that fixes it"
        undeclared = env_names(result.missing_env) - set(self._declared_env())
        assert not undeclared, (f"check() {what} reports {sorted(undeclared)} missing, which "
                                "`requires_env` does not declare")

    # -- the class -----------------------------------------------------------------------
    def test_meets_the_class_contract(self):
        cls = self._cls()
        try:
            _plugs.check_class(cls)
        except _plugs.PlugError as exc:
            pytest.fail(str(exc), pytrace=False)
        assert cls.kind == self.kind, \
            f"{cls.name} declares kind {cls.kind!r}; this kit is for {self.kind!r} plugs"
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", cls.name), \
            f"plug name {cls.name!r}: lower case, digits and hyphens (it is a run.yml key)"

    def test_credential_routes_are_declared(self):
        """Each route the interview lists is a `CredentialRoute` with a name, reads only
        variables `requires_env` declares, and at most one route needs nothing set."""
        routes = getattr(self._cls(), "credential_routes", None)
        if routes is None:
            pytest.skip("this plug declares no credential_routes")
        assert isinstance(routes, tuple) and routes, \
            "`credential_routes` is a non-empty tuple of CredentialRoute"
        declared = set(self._declared_env())
        for r in routes:
            assert isinstance(r, S.CredentialRoute) and r.name.strip(), \
                f"{r!r} is not a named CredentialRoute"
            undeclared = sorted(set(r.env) - declared)
            assert not undeclared, (f"route {r.name!r} reads {undeclared}, which "
                                    "`requires_env` does not declare")
            assert r.env or r.note.strip(), \
                f"route {r.name!r} needs nothing set; its `note` must say how it signs in"
        assert len({r.name for r in routes}) == len(routes), "two routes share a name"

    def test_requires_env_declares_names(self):
        env = self._declared_env()
        bad = [n for n in env if not _ENV_NAME.match(n)]
        assert not bad, f"`requires_env` holds {bad}: variable NAMES only, never values"
        assert len(set(env)) == len(env), "`requires_env` names a variable twice"

    def test_env_missing_reports_only_declared_names(self):
        """What `metrics-agent plugs` and the interview show is `requires_env`; a variable
        the plug needs but does not declare is one nobody is told to set."""
        cls, declared = self._cls(), set(self._declared_env())
        hook = getattr(cls, "env_missing", None)
        if hook is not None and not inspect.ismethod(hook):
            pytest.fail("`env_missing` must be a classmethod: the interview asks it before "
                        "any instance exists", pytrace=False)
        with mock.patch.dict(os.environ, {}, clear=True):
            for use in ENV_USES:
                reported = S.env_missing_for(cls, use)
                assert all(isinstance(n, str) for n in reported), \
                    f"env_missing({use!r}) returned something other than names"
                undeclared = env_names(reported) - declared
                assert not undeclared, (f"env_missing({use!r}) reports {sorted(undeclared)}, "
                                        "which `requires_env` does not declare")
        with mock.patch.dict(os.environ, {n: f"conformance-{n.lower()}" for n in declared},
                             clear=True):
            for use in ENV_USES:
                still = S.env_missing_for(cls, use)
                assert not still, (f"with every declared variable set, env_missing({use!r}) "
                                   f"still reports {list(still)}: it reads one that "
                                   "`requires_env` does not declare")

    # -- check ---------------------------------------------------------------------------
    def _has_check(self) -> bool:
        return callable(getattr(self._cls(), "check", None))

    def test_check_with_nothing_configured(self, tmp_path, monkeypatch):
        if not self._has_check():
            pytest.skip("this plug defines no check()")
        self._set_env(monkeypatch, {})
        self._check(tmp_path, {}, "with nothing configured")

    def test_check_passes_with_working_settings(self, tmp_path, monkeypatch, plug_settings,
                                                plug_environment):
        if not self._has_check():
            pytest.skip("this plug defines no check()")
        self._skip_live("check")
        self._set_env(monkeypatch, plug_environment)
        result = self._check(tmp_path, plug_settings, "with the working settings")
        assert result.ok, f"check() with the working settings failed: {result}"

    def _broken_cases(self, working: dict, environment: dict):
        for label, case in (self.broken or {}).items():
            assert isinstance(case, dict) and case.get("fix"), \
                f"broken case {label!r} needs a `fix`: what the check's fix must name"
            settings = copy.deepcopy(working)
            for key, value in (case.get("settings") or {}).items():
                if value is None:
                    settings.pop(key, None)
                else:
                    settings[key] = value
            yield label, case, settings, {**environment, **(case.get("env") or {})}

    def test_check_refuses_each_broken_setting(self, tmp_path, monkeypatch, plug_settings,
                                               plug_environment):
        if not self._has_check():
            pytest.skip("this plug defines no check()")
        if not self.broken:
            pytest.skip("this plug's tests declare no broken setting")
        for label, case, settings, environment in self._broken_cases(plug_settings,
                                                                    plug_environment):
            with monkeypatch.context() as mp:
                self._set_env(mp, environment, tuple(case.get("unset") or ()))
                result = self._check(tmp_path, settings, f"broken ({label})")
            assert not result.ok, f"check() passed with a broken setting ({label})"
            assert case["fix"] in result.fix, (f"check() broken ({label}): fix {result.fix!r} "
                                               f"does not name {case['fix']!r}")

    def test_check_never_shows_an_environment_value(self, tmp_path, monkeypatch,
                                                    plug_settings, plug_environment):
        """Every declared variable is set to a value nobody could guess; none may come back
        in what the check says, whichever way it ends."""
        if not self._has_check() or not self._declared_env():
            pytest.skip("this plug reads no environment in check()")
        values = {n: f"conformance-{secrets.token_hex(6)}" for n in self._declared_env()}
        runs = [("with nothing configured", {})]
        if "check" not in (self.needs_live or {}):
            runs.append(("with the working settings", plug_settings))
            runs += [(f"broken ({label})", s) for label, _, s, _ in
                     self._broken_cases(plug_settings, plug_environment)]
        self._set_env(monkeypatch, values)
        for what, settings in runs:
            with self._sandbox(tmp_path, settings):
                try:
                    result = self._make(settings).check(settings)
                except Exception as exc:  # noqa: BLE001
                    pytest.fail(f"check() {what} raised {type(exc).__name__}", pytrace=False)
            said = " ".join([result.message, result.fix, *result.missing_env,
                             getattr(result, "route", ""), getattr(result, "coverage", "")])
            leaked = sorted(n for n, v in values.items() if v in said)
            assert not leaked, f"check() {what} shows the value of {leaked}; names only"


# ── input plugs ──────────────────────────────────────────────────────────────────────────
class InputPlugConformance(_PlugConformance):
    """The input socket's contract. See the module docstring for the hooks."""
    kind = "input"
    tables: ClassVar[list] = []
    window: ClassVar[S.Window] = S.Window()
    assets: ClassVar[list] = []
    scope_refs: ClassVar[list] = []
    probe_sql: ClassVar[str] = "SELECT 1 AS one"

    def test_declares_custody(self):
        assert getattr(self._cls(), "custody", None) in (S.CUSTOMER, S.THIRD_PARTY), \
            "an input plug declares custody: 'customer' (the customer's own systems) or " \
            "'third-party' (data held in a third party's store; refused in a standalone run)"

    def test_declared_capabilities_are_known(self):
        cls = self._cls()
        declared = getattr(cls, "capabilities", None)
        if declared is None:
            return
        assert isinstance(declared, (tuple, list)), "`capabilities` is a tuple of names"
        unknown = [c for c in declared if c not in S.INPUT_CAPABILITIES]
        assert not unknown, f"`capabilities` names {unknown}; known: {S.INPUT_CAPABILITIES}"
        absent = [c for c in declared if not callable(getattr(cls, c, None))]
        assert not absent, f"`capabilities` declares {absent}, which the class does not define"

    def test_default_dialect_is_supported(self):
        d = getattr(self._cls(), "default_dialect", None)
        if d is None:
            return
        assert d in _dialect.SUPPORTED, \
            f"default_dialect {d!r} is not one the core knows: {sorted(_dialect.SUPPORTED)}"

    def test_setup_questions_match_capabilities(self):
        cls = self._cls()
        questions = getattr(cls, "setup_questions", None)
        if questions is None:
            return
        assert isinstance(questions, dict), "`setup_questions` maps a role to its questions"
        offered = S.capabilities(cls)
        for role, qs in questions.items():
            assert role in ROLES, f"setup_questions role {role!r}; input roles: {list(ROLES)}"
            assert ROLES[role] in offered, (f"setup_questions asks for role {role!r}, but the "
                                            f"plug does not offer {ROLES[role]}")
            _questions_ok(role, qs)

    # -- check_sql_session (optional, a SQL-check plug) ---------------------------------
    def _session(self, tmp_path, settings: dict, what: str) -> S.CheckResult:
        hook = getattr(self._cls(), "check_sql_session", None)
        if not callable(hook):
            pytest.skip("this plug defines no check_sql_session()")
        assert "sql_runner" in S.capabilities(self._cls()), \
            "check_sql_session() is for a SQL-check plug; this one offers no sql_runner"
        with self._sandbox(tmp_path, settings) as seen:
            try:
                result = self._make(settings).check_sql_session(settings)
            except Exception as exc:  # noqa: BLE001 - the contract: a check never raises
                pytest.fail(f"check_sql_session() {what} raised {type(exc).__name__}: {exc}; "
                            "it returns a failed CheckResult instead", pytrace=False)
        _fail(f"check_sql_session() {what}", seen.problems())
        assert seen.seconds <= CHECK_SECONDS, \
            f"check_sql_session() {what} took {seen.seconds:.1f}s; it takes seconds"
        self._well_formed(result, f"(check_sql_session) {what}")
        return result

    def test_check_sql_session_with_nothing_configured(self, tmp_path, monkeypatch):
        """Like `check`: read-only, quick, never raises, and a failure names its fix."""
        self._set_env(monkeypatch, {})
        self._session(tmp_path, {}, "with nothing configured")

    def test_check_sql_session_passes_with_working_settings(self, tmp_path, monkeypatch,
                                                            plug_settings, plug_environment):
        """With the working settings, the session resolves a statement naming
        `SCHEMA.TABLE`: what `metrics-agent setup --check` asks of every SQL check."""
        self._skip_live("check_sql_session")
        self._skip_live("check")
        self._set_env(monkeypatch, plug_environment)
        result = self._session(tmp_path, plug_settings, "with the working settings")
        assert result.ok, f"check_sql_session() with the working settings failed: {result}"

    def test_its_settings_reach_it(self, tmp_path, monkeypatch, plug_settings,
                                   plug_environment):
        """The core builds the plug with its own `settings.<plug>` block, exactly as a run
        records it: the working settings pass the plug's own declarations for every role it
        offers, are plain JSON, and are what the built plug holds."""
        from metrics_agent import run_config as RC  # noqa: PLC0415

        cls = self._cls()
        self._set_env(monkeypatch, plug_environment)
        _jsonable(plug_settings, "the working settings")
        offered = S.capabilities(cls)
        for role, capability in ROLES.items():
            if capability not in offered or not (getattr(cls, "setup_questions", None) or {}
                                                 ).get(role):
                continue
            asked = {q.key for q in cls.setup_questions[role]}
            required = {q.key for q in cls.setup_questions[role] if q.required}
            if not asked & set(plug_settings) or not required <= set(plug_settings):
                continue            # the working settings do not configure this role
            problem = S.settings_problem(cls, role, plug_settings)
            assert problem is None, (f"the working settings fail the plug's own declaration "
                                     f"for {role!r}: {problem}")
            recorded = RC.recorded_settings(cls, role, plug_settings)
            _jsonable(recorded, f"the settings recorded for {role!r}")
            plug = self._make(recorded)
            held = getattr(plug, "settings", None)
            if isinstance(held, dict):
                lost = sorted(k for k in recorded if held.get(k) != recorded[k])
                assert not lost, (f"built with its settings, the plug does not hold {lost}; "
                                  "the core hands each plug its own block and nothing else")

    @pytest.mark.parametrize("capability", S.INPUT_CAPABILITIES)
    def test_each_capability_meets_the_contract(self, capability, tmp_path, monkeypatch,
                                                plug_settings, plug_environment):
        """A declared capability answers, in the socket's shape; one that is not declared is
        absent or refuses with `NotSupported` (a SQL runner may also be None)."""
        declared = capability in S.capabilities(self._cls())
        if declared:
            self._skip_live(capability)
        self._set_env(monkeypatch, plug_environment)
        plug = self._make(plug_settings)
        method = getattr(plug, capability, None)
        if not declared:
            if not callable(method):
                return
            dest = tmp_path / "dest"
            dest.mkdir(exist_ok=True)
            with self._sandbox(tmp_path, plug_settings, allowed=[dest]) as seen:
                try:
                    answer = self._invoke(plug, capability, dest)
                except S.NotSupported:
                    answer = "refused"
            _fail(f"{capability} (not declared)", seen.problems())
            ok = answer == "refused" or (capability == "sql_runner" and answer is None)
            assert ok, (f"{capability} is not declared, so it must raise NotSupported"
                        f"{' or return None' if capability == 'sql_runner' else ''}; it "
                        f"returned {type(answer).__name__}")
            return
        getattr(self, f"_exercise_{capability}")(plug, tmp_path, plug_settings)

    def _invoke(self, plug: Any, capability: str, dest: pathlib.Path) -> Any:
        if capability == "query_history":
            return list(itertools.islice(iter(plug.query_history(list(self.tables),
                                                                 self.window)), 1))
        if capability == "table_schema":
            return plug.table_schema(list(self.tables),
                                     max_description_chars=MAX_DESCRIPTION_CHARS)
        if capability == "bi_definitions":
            return plug.bi_definitions(list(self.assets), dest)
        if capability == "sql_runner":
            return plug.sql_runner()
        return plug.resolve_scope(list(self.scope_refs))

    def _exercise_query_history(self, plug, tmp_path, settings) -> None:
        with self._sandbox(tmp_path, settings) as seen:
            records = list(itertools.islice(
                G.validated(plug.query_history(list(self.tables), self.window),
                            "query_record.schema.json", plug=self._cls().name), MAX_RECORDS))
        _fail("query_history", seen.problems())
        assert records, "query_history declared, but the working settings yield no record"
        assert all(isinstance(r, S.QueryRecord) for r in records), \
            "query_history yields sockets.QueryRecord"

    def _exercise_table_schema(self, plug, tmp_path, settings) -> None:
        with self._sandbox(tmp_path, settings) as seen:
            result = plug.table_schema(list(self.tables),
                                       max_description_chars=MAX_DESCRIPTION_CHARS)
        _fail("table_schema", seen.problems())
        assert isinstance(result, S.SchemaResult), "table_schema returns sockets.SchemaResult"
        _validate(result.to_dict(), "table_schema.schema.json", "table_schema's result")
        assert any(result.columns.values()), \
            "table_schema declared, but the working settings give no column"
        long = [f"{t}.{c.name}" for t, cols in result.columns.items() for c in cols
                if c.description and len(c.description) > MAX_DESCRIPTION_CHARS]
        assert not long, (f"descriptions longer than max_description_chars="
                          f"{MAX_DESCRIPTION_CHARS}: {long[:5]}")

    def _exercise_bi_definitions(self, plug, tmp_path, settings) -> None:
        dest = tmp_path / "dest"
        dest.mkdir(exist_ok=True)
        with self._sandbox(tmp_path, settings, allowed=[dest]) as seen:
            files = plug.bi_definitions(list(self.assets), dest)
        _fail("bi_definitions", seen.problems())
        assert isinstance(files, list) and files, \
            "bi_definitions declared, but the working settings give no file"
        roots = [dest.resolve(), *setting_paths(settings)]
        for f in files:
            assert isinstance(f, pathlib.Path), "bi_definitions returns pathlib.Path objects"
            assert f.is_file(), f"bi_definitions returned {f}, which is not a file"
            assert _under(str(f.resolve()), roots), \
                f"bi_definitions returned {f}, which is neither under dest nor a named setting"

    def _exercise_sql_runner(self, plug, tmp_path, settings) -> None:
        with self._sandbox(tmp_path, settings, guard=False) as seen:
            runner = plug.sql_runner()
            if runner is not None:
                assert isinstance(runner, S.SqlRunner), (
                    f"sql_runner returned {type(runner).__name__}, which is not a "
                    "sockets.SqlRunner (dialect, connect, explain, fetch, scan, columns_of, "
                    "columns_of_many, close, and a context manager)")
                with G.ReadOnlyRunner(runner, self._dialect()) as ro:
                    cols, rows = ro.fetch(self.probe_sql, 1)
                    plan = ro.explain(self.probe_sql)
                    scanned = [r for batch in ro.scan(self.probe_sql, 10) for r in batch]
                    found = ro.columns_of_many(list(self.tables))
        _fail("sql_runner", seen.problems())
        if runner is None:
            return
        assert isinstance(cols, list) and len(list(rows)) == 1, \
            f"fetch({self.probe_sql!r}, 1) returns (columns, rows) with one row"
        assert isinstance(plan, int) and plan >= 0, "explain returns a count of plan rows"
        assert len(scanned) == 1, f"scan({self.probe_sql!r}) yields batches holding one row"
        assert isinstance(found, dict) and set(found) <= set(self.tables), \
            "columns_of_many returns only tables it was asked about"
        assert all(isinstance(c, str) for cs in found.values() for c in cs), \
            "columns_of_many returns column names"

    def _exercise_resolve_scope(self, plug, tmp_path, settings) -> None:
        with self._sandbox(tmp_path, settings) as seen:
            scope = plug.resolve_scope(list(self.scope_refs))
        _fail("resolve_scope", seen.problems())
        assert isinstance(scope, S.Scope), "resolve_scope returns sockets.Scope"
        assert isinstance(scope.tables, tuple) and scope.tables, \
            "resolve_scope declared, but the working settings resolve to no table"
        bad = [t for t in scope.tables if not isinstance(t, str) or len(t.split(".")) != 3]
        assert not bad, f"resolve_scope returns DB.SCHEMA.TABLE names; got {bad[:5]}"


def _questions_ok(role: str, questions: Any) -> None:
    assert isinstance(questions, tuple) and questions, \
        f"setup_questions[{role!r}] is a non-empty tuple of SetupQuestion"
    keys = []
    for q in questions:
        assert isinstance(q, S.SetupQuestion), f"setup_questions[{role!r}] holds {q!r}"
        assert q.key and q.text, f"setup_questions[{role!r}]: every question has a key and text"
        assert q.kind in QUESTION_KINDS, f"question {q.key!r}: kind {q.kind!r}"
        if q.kind == "choice":
            assert q.choices, f"question {q.key!r} is a choice with no choices"
        else:
            assert not q.choices, f"question {q.key!r} lists choices but is not a choice"
        keys.append(q.key)
    assert len(set(keys)) == len(keys), f"setup_questions[{role!r}] asks one key twice"


# ── output plugs ─────────────────────────────────────────────────────────────────────────
class OutputPlugConformance(_PlugConformance):
    """The output socket's contract: `resolve`, `plan` (sends nothing) and `publish` (leaves a
    ledger). The bundle is the kit's minimal one unless the `bundle` fixture is overridden."""
    kind = "output"

    @pytest.fixture
    def bundle(self, tmp_path):
        return minimal_bundle(tmp_path / "run")

    def test_declares_a_lane(self):
        lane = getattr(self._cls(), "lane", None)
        assert isinstance(lane, str) and lane.strip(), \
            "an output plug declares its `lane`: the publish target that admits it"

    def test_setup_questions_are_for_publishing(self):
        questions = getattr(self._cls(), "setup_questions", None)
        if questions is None:
            return
        assert isinstance(questions, dict) and set(questions) <= {"publish"}, \
            "an output plug's setup_questions use the role 'publish'"
        for role, qs in questions.items():
            _questions_ok(role, qs)

    def test_resolve_returns_what_s0_records(self, tmp_path, monkeypatch, plug_settings,
                                            plug_environment):
        self._skip_live("resolve")
        self._set_env(monkeypatch, plug_environment)
        with self._sandbox(tmp_path, plug_settings) as seen:
            settled = self._make(plug_settings).resolve(plug_settings)
        _fail("resolve", seen.problems())
        assert isinstance(settled, dict), "resolve returns a dict"
        _jsonable(settled, "resolve's answer")

    def test_plan_sends_nothing(self, tmp_path, monkeypatch, plug_settings, plug_environment,
                                bundle):
        self._skip_live("plan")
        self._set_env(monkeypatch, plug_environment)
        with self._sandbox(tmp_path, plug_settings, allowed=[bundle.run_dir]) as seen:
            plan = self._make(plug_settings).plan(bundle)
        _fail("plan", seen.problems())
        assert isinstance(plan, S.PublishPlan), "plan returns sockets.PublishPlan"
        assert plan.lane == self._cls().lane, \
            f"plan's lane {plan.lane!r} is not the plug's lane {self._cls().lane!r}"
        assert isinstance(plan.creates, tuple) and all(isinstance(c, dict)
                                                       for c in plan.creates), \
            "plan.creates is a tuple of dicts, one per record it would create"
        _jsonable(list(plan.creates), "plan.creates")
        for c in plan.creates:
            assert isinstance(c.get("kind"), str) and c["kind"], \
                f"plan.creates entry {c!r} names no `kind`: one entry per RECORD, saying what it is"
            assert len(c) > 1, f"plan.creates entry {c!r} names its kind and nothing that " \
                               "identifies the record"

    def test_publish_leaves_a_ledger(self, tmp_path, monkeypatch, plug_settings,
                                     plug_environment, bundle):
        self._skip_live("publish")
        self._set_env(monkeypatch, plug_environment)
        with self._sandbox(tmp_path, plug_settings, allowed=[bundle.run_dir]) as seen:
            ledger = self._make(plug_settings).publish(bundle)
        _fail("publish", seen.problems())
        assert isinstance(ledger, dict), "publish returns a ledger (a dict)"
        _jsonable(ledger, "the ledger")
        _validate(ledger, "push_ledger.schema.json", "the ledger")


# ── observers ────────────────────────────────────────────────────────────────────────────
class ObserverConformance(_PlugConformance):
    """The observer socket's contract: every event of a stage, the optional hooks it defines,
    and a stage that fails, all without raising, offline, writing only in the run folder."""
    kind = "observer"

    def _make(self, settings: dict) -> Any:
        return self._cls()()

    def test_offered_with_names_outputs(self):
        offered = getattr(self._cls(), "offered_with", None)
        if offered is None:
            return
        assert isinstance(offered, tuple) and all(isinstance(o, str) for o in offered), \
            "`offered_with` is a tuple of output plug names"

    def test_not_wanted_when_nothing_is_configured(self, monkeypatch):
        wanted = getattr(self._cls(), "wanted", None)
        if wanted is None:
            return
        self._set_env(monkeypatch, {})
        assert wanted() is False, "with none of its variables set, wanted() is False: " \
                                  "tracing is never on by accident"

    def test_hears_every_event_of_a_run(self, tmp_path, monkeypatch, plug_environment):
        self._skip_live("events")
        self._set_env(monkeypatch, plug_environment)
        from metrics_agent.lib import policy as _policy  # noqa: PLC0415

        policy = _policy.load(None)
        run = tmp_path / "run"
        run.mkdir()
        (run / "conformance.json").write_text("{}")
        with self._sandbox(tmp_path, {}, allowed=[run], guard=False) as seen:
            obs = self._make({})
            self._one_stage(obs, run, policy)
            self._failing_stage(obs, run, policy)
        _fail("the observer", seen.problems())

    @staticmethod
    def _entry(verdict: str = "ok") -> types.SimpleNamespace:
        return types.SimpleNamespace(verdict=verdict, notes=None, counts={}, inputs=[],
                                     outputs=[], model=None)

    def _one_stage(self, obs: Any, run: pathlib.Path, policy: Any) -> None:
        """What the stage harness calls, in its order, with its arguments."""
        stage, run_id = "conformance_stage", "conformance-run"
        obs.start_run(run, run_id, {}, policy)
        cm = obs.stage_span(stage, f"{stage}.py", self._entry(), run_id)
        assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__"), \
            "stage_span returns a context manager"
        with cm as span:
            if callable(getattr(obs, "human_args", None)):
                obs.human_args(argparse.Namespace(run=str(run)), stage)
            obs.artifact("conformance.json", run / "conformance.json", {}, stage_name=stage)
            obs.gate("publish_target", "Where should the results go?", "none",
                     stage_name=stage, options=None, decided_by="")
            obs.decision("witness", "vq_1", "admitted", because="the kit decided",
                         policy_key="", policy_value=None, evidence=None, stage=stage)
            if callable(getattr(obs, "update", None)):
                obs.update(span, said=["the kit said this"])
            if callable(getattr(obs, "remember_said", None)):
                obs.remember_said(stage, ["the kit said this"])
            if callable(getattr(obs, "ensure_experiment", None)):
                obs.ensure_experiment(run, policy)
        obs.finish_run(verdict="ok")
        if callable(getattr(obs, "close_pending", None)):
            obs.close_pending()

    def _failing_stage(self, obs: Any, run: pathlib.Path, policy: Any) -> None:
        stage, run_id = "conformance_failing_stage", "conformance-run"
        obs.start_run(run, run_id, {}, policy)
        with pytest.raises(_StageFailed):
            with obs.stage_span(stage, f"{stage}.py", self._entry("blocked"), run_id):
                raise _StageFailed("the stage failed")
        obs.finish_run(verdict="blocked")
