# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""`run.yml`: which plugs a run uses, with their settings. The only way a run is described.

Every plug is served through its methods: S0 records each with its validated settings
(`socket_entry`) and `plug_runtime` builds it with them at every stage. Nothing here names a
plug, a source or a destination (AGENT_GUARDRAILS rule 22): a plug is found by the name
run.yml gives it, among the installed ones, and asked what it offers.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import pathlib
from typing import Any

import yaml

from metrics_agent.lib import artifacts
from metrics_agent.lib import bi_capture as _bi_capture


class ConfigError(ValueError):
    """`run.yml` is malformed, names an unknown plug, or asks for something not built yet."""


# How a SQL-check plug checks each statement: explain (plans it) or execute (runs it,
# read-only, with a row limit); parse and none check nothing against a warehouse.
WITNESS_MODES = ("explain", "execute", "parse", "none")


def _lane_of(name: str) -> str:
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    try:
        return _plugs.load(name).lane
    except _plugs.PlugError as exc:
        raise ConfigError(f"publish: {exc}") from None


# ── loading ──────────────────────────────────────────────────────────────────────────────
def _plain(obj: Any) -> Any:
    """YAML reads an unquoted 2026-09-01 as a date; the contract is a string."""
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def load(path: str | pathlib.Path) -> dict[str, Any]:
    path = pathlib.Path(path).expanduser()
    if not path.is_file():
        raise ConfigError(f"no run configuration at {path}")
    try:
        doc = _plain(yaml.safe_load(path.read_text()))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    try:
        artifacts.validate(doc, "run_config.schema.json", what=str(path))
    except artifacts.ArtifactError as exc:
        raise ConfigError(str(exc)) from None
    from metrics_agent.lib import dialect as _dialect  # noqa: PLC0415

    try:
        _dialect.normalise(doc.get("dialect"))
    except _dialect.DialectError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    _refuse_credentials(doc, path)
    named = _named(doc)
    broken: dict[str, str] = {}
    found = _installed(set(named), broken)
    unknown = sorted(n for n in named if n not in found)
    if unknown:
        # An installed plug that fails to load cannot be used, so it reads as unknown; its
        # load error is the actionable part, so it is said rather than swallowed.
        from metrics_agent import plugs as _plugs  # noqa: PLC0415

        why = "".join(f" {n} is installed but unusable: {broken[n]}"
                      for n in unknown if n in broken)
        raise ConfigError(f"{path} names plug(s) that are not installed: "
                          f"{', '.join(unknown)}.{why} Installed: "
                          f"{', '.join(sorted(_plugs.installed())) or 'none'} "
                          "(`metrics-agent plugs` lists them).")
    return doc


# A settings key that names a credential. Secrets never go in run.yml (guardrail 19): each plug
# reads its own from the environment variables it lists in `requires_env`. One definition,
# shared with the stage harness that keeps credential values out of what a stage prints.
from metrics_agent.lib.redact import (CREDENTIAL_KEY as _CREDENTIAL_KEY,  # noqa: E402
                                      CREDENTIAL_URL as _CREDENTIAL_URL,
                                      key_words as _key_words)


def _refuse_credentials(doc: dict, path) -> None:
    """Refuse a setting that names or carries a credential, at any depth under `settings`."""
    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            for key in sorted(node, key=str):
                here = f"{where}.{key}"
                if _CREDENTIAL_KEY.search(_key_words(key)):
                    raise ConfigError(
                        f"{path}: {here} names a credential. Secrets never go in run.yml; set "
                        "it in the environment variable the plug reads (see `metrics-agent "
                        "plugs`), and remove it from the file.")
                walk(node[key], here)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{where}[{i}]")
        elif isinstance(node, str) and _CREDENTIAL_URL.search(node):
            raise ConfigError(
                f"{path}: {where} carries a password inside a URL. Secrets never go in "
                "run.yml; the plug reads its connection from the environment.")

    for plug, values in sorted(((doc.get("settings") or {})).items()):
        walk(values or {}, f"settings.{plug}")


# ── BI the run fetches itself ────────────────────────────────────────────────────────────
# A BI plug that can FETCH its definitions (from the vendor's own service) declares
# the settings that ask it to, as `fetch_settings`. A run.yml naming one of them and no `bi`
# path is fetched by `s_fetch_bi` at the start of the run, into the run directory; S0 then
# reads what was fetched, recorded in FETCH_RECORD. Nothing is fetched behind a `bi` path:
# an extract on disk is used as it stands.
FETCH_RECORD = "bi_fetch.json"


def fetch_plans(doc: dict) -> list[dict]:
    """`[{"plug", "settings"}]`: every BI plug of the run asked to fetch (one of its
    `fetch_settings` set, and no `bi` on disk), in the order listed."""
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    out = []
    for bi in inputs_of(doc, "bi"):
        settings = ((doc.get("settings") or {}).get(bi) or {})
        if settings.get("bi"):
            continue
        try:
            cls = _plugs.load(bi)
        except _plugs.PlugError:
            continue
        asks = getattr(cls, "fetch_settings", None) or ()
        if any(settings.get(k) for k in asks):
            out.append({"plug": bi, "settings": dict(settings)})
    return out


def fetch_plan(doc: dict) -> dict | None:
    """The first of `fetch_plans`, or None: whether the run fetches at all."""
    plans = fetch_plans(doc)
    return plans[0] if plans else None


def _fetch_record(run: str | pathlib.Path | None) -> dict:
    if not run:
        return {}
    try:
        import json  # noqa: PLC0415

        doc = json.loads((pathlib.Path(run) / FETCH_RECORD).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def fetched_bi(run: str | pathlib.Path | None) -> str | None:
    """The extract `s_fetch_bi` recorded for this run directory (the first plug's), or None."""
    return _fetch_record(run).get("bi") or None


def blocked_by_plug(run: str | pathlib.Path | None) -> dict[str, list[str]]:
    """{plug: the access codes that blocked its whole fetch} for this run directory: the
    plugs `s_fetch_bi` could read nothing through (recorded in the run's access report)."""
    rec = _fetch_record(run)
    return {p: [str(c) for c in v.get("blocked") or []]
            for p, v in (rec.get("fetched") or {}).items()
            if isinstance(v, dict) and v.get("blocked")}


def fetched_by_plug(run: str | pathlib.Path | None) -> dict[str, str]:
    """{plug: the extract `s_fetch_bi` fetched for it} for this run directory."""
    rec = _fetch_record(run)
    out = {p: str(v.get("bi")) for p, v in (rec.get("fetched") or {}).items()
           if isinstance(v, dict) and v.get("bi")}
    if not out and rec.get("plug") and rec.get("bi"):
        out[rec["plug"]] = str(rec["bi"])
    return out


def _installed(names: set[str], broken: dict[str, str] | None = None) -> dict[str, str]:
    """{name: kind} of the plugs `run.yml` names that are installed: any package's plug can be
    named. A named plug that is installed but fails to load is left out, and its error put in
    `broken` when given."""
    if not names:
        return {}
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    found = {}
    for name, item in _plugs.installed().items():
        if name in names:
            try:
                cls = item.load()
            except _plugs.PlugError as exc:
                if broken is not None:
                    broken[name] = str(exc)
                continue
            found[name] = cls.kind
    return found


def _kind(name: str) -> str:
    kind = _installed({name}).get(name)
    if kind is None:
        raise ConfigError(f"no plug named {name!r} is installed")
    return kind


def fingerprint(path: str | pathlib.Path) -> dict[str, str]:
    path = pathlib.Path(path).expanduser().resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


INPUT_ROLES = ("history", "schema", "bi", "scope", "sql_check")


def inputs_of(doc: dict, cap: str) -> list[str]:
    """The plugs `doc` lists for an input capability, in order. `sql_check` may be one plug,
    a list, or nothing."""
    value = (doc.get("inputs") or {}).get(cap)
    if value is None:
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value]) if v]


def _named(doc: dict) -> set[str]:
    names = {n for cap in INPUT_ROLES for n in inputs_of(doc, cap)}
    return names | set(doc.get("publish") or []) | set(doc.get("observers") or [])


def plug_settings(doc: dict, plug: str) -> dict[str, Any]:
    """`settings.<plug>`, as written (never a credential: `load` refused that)."""
    return dict(((doc.get("settings") or {}).get(plug) or {}))


# Settings the CORE reads from a plug's block rather than the plug: how the SQL check checks,
# and the engine the history came out of. Not the plug's business, so never validated as its.
CORE_KEYS = ("witness_mode", "engine")


def _class(name: str):
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    try:
        return _plugs.load(name)
    except _plugs.PlugError as exc:
        raise ConfigError(str(exc)) from None


def socket_plugs(doc: dict) -> dict[str, list[str]]:
    """{capability: [plug, …]}: every input plug `doc` lists, in order. Each is served through
    its methods."""
    return {cap: inputs_of(doc, cap) for cap in INPUT_ROLES}


def _capability_check(doc: dict) -> None:
    """Every input plug is listed only for what it offers, and is an input plug."""
    from metrics_agent import sockets as S  # noqa: PLC0415

    what = {"history": "supply query history", "schema": "supply table schemas",
            "bi": "supply BI definitions", "scope": "resolve asset scopes",
            "sql_check": "check SQL"}
    for cap in INPUT_ROLES:
        for plug in inputs_of(doc, cap):
            _check_kind(plug, "input", f"inputs.{cap}")
            offered = S.capabilities(_class(plug))
            if S.ROLES[cap] in offered:
                continue
            # A schema plug that is also the SQL check answers column questions through its
            # SQL check's own connection.
            if cap == "schema" and plug in inputs_of(doc, "sql_check") \
                    and "sql_runner" in offered:
                continue
            if cap == "sql_check":
                raise ConfigError(f"plug {plug!r} cannot check SQL")
            raise ConfigError(f"plug {plug!r} does not {what[cap]}")


def _dialect_check(doc: dict) -> None:
    """One dialect per run. With several SQL-check plugs, each must speak the run's."""
    checks = inputs_of(doc, "sql_check")
    if len(checks) < 2:
        return
    want = str(doc.get("dialect") or "").strip().lower()
    for plug in checks:
        cls = _class(plug)
        spoken = {str(d).lower() for d in (getattr(cls, "dialects", None) or ())}
        if getattr(cls, "default_dialect", None):
            spoken.add(str(cls.default_dialect).lower())
        if spoken and want not in spoken:
            raise ConfigError(
                f"inputs.sql_check lists {plug!r}, whose runner speaks "
                f"{', '.join(sorted(spoken))}, and the run's dialect is {want!r}. A run has one "
                "SQL dialect; check each statement with plugs that speak it, or drop this one.")


def _socket_settings_check(doc: dict) -> None:
    """Each plug served through its methods has the settings its own declarations ask for."""
    from metrics_agent import sockets as S  # noqa: PLC0415

    for cap, plugs in socket_plugs(doc).items():
        for plug in plugs:
            settings = {k: v for k, v in plug_settings(doc, plug).items() if k not in CORE_KEYS}
            problem = S.settings_problem(_class(plug), cap, settings)
            if cap == "schema" and plug in inputs_of(doc, "sql_check") and problem \
                    and problem.startswith(f"plug {plug!r} needs"):
                # Its SQL check's own connection answers column questions, so a schema
                # setting is not required; one that IS given is still checked.
                continue
            if problem:
                raise ConfigError(problem)


def _setting(doc: dict, plug: str, key: str, *, required: bool = True) -> Any:
    value = ((doc.get("settings") or {}).get(plug) or {}).get(key)
    if required and value in (None, "", []):
        raise ConfigError(f"plug {plug!r} needs settings.{plug}.{key}")
    return value


def _check_kind(plug: str, kind: str, where: str) -> None:
    if _kind(plug) != kind:
        raise ConfigError(f"{where} names {plug!r}, which is an {_kind(plug)} plug")


def witness_mode(doc: dict) -> str:
    """How the run checks each statement: the SQL-check plug's `witness_mode` setting, else
    run.yml's own `witness_mode`, else `explain` with a SQL check and `parse` without one. A
    run with no SQL check cannot explain or execute anything."""
    checks = inputs_of(doc, "sql_check")
    where = f"settings.{checks[0]}.witness_mode" if checks else "witness_mode"
    mode = ((_setting(doc, checks[0], "witness_mode", required=False) if checks else None)
            or doc.get("witness_mode") or ("explain" if checks else "parse"))
    if mode not in WITNESS_MODES:
        raise ConfigError(f"{where} is {mode!r}; it must be one of {', '.join(WITNESS_MODES)}")
    if not checks and mode in ("explain", "execute"):
        raise ConfigError(f"witness_mode is {mode!r}, and run.yml lists no SQL check "
                          "(`inputs.sql_check`) to explain or execute against")
    return mode


def history_engine(doc: dict) -> str | None:
    """The engine the history came out of (a history plug's `engine` setting), one per run."""
    return next((str(plug_settings(doc, p)["engine"]) for p in inputs_of(doc, "history")
                 if plug_settings(doc, p).get("engine")), None)


def check_window(doc: dict) -> dict:
    """`window`, checked: absolute YYYY-MM-DD dates, start not after end."""
    window = doc.get("window") or {}
    for end in ("start", "end"):
        if window.get(end):
            try:
                _dt.date.fromisoformat(str(window[end]))
            except ValueError:
                raise ConfigError(f"window.{end} is {window[end]!r}, not a YYYY-MM-DD date") \
                    from None
    if window.get("start") and window.get("end") and str(window["start"]) > str(window["end"]):
        raise ConfigError(f"window.start {window['start']} is after window.end {window['end']}")
    return {"start": window.get("start"), "end": window.get("end")}


def validate(doc: dict, *, settings: bool = True) -> None:
    """Every check a run.yml must pass before anything runs: each plug of the right kind,
    listed only for what it offers, with the settings its own declarations ask for; one
    dialect; a well-formed window; a destination for every result. `settings=False` leaves
    the settings to each plug's own `check` (`metrics-agent setup --check`, which reports a
    failing setting with the fix that names it)."""
    _capability_check(doc)
    _dialect_check(doc)
    if settings:
        _socket_settings_check(doc)
    witness_mode(doc)
    check_window(doc)
    if not doc.get("publish"):
        raise ConfigError("`publish` lists no output plug: where results go is the operator's "
                          "decision, never a default (list `files` to keep them in the run "
                          "directory)")
    for plug in doc["publish"]:
        _check_kind(plug, "output", "publish")
    for plug in doc.get("observers") or []:
        _check_kind(plug, "observer", "observers")


def table_qualifier(doc: dict):
    """How the run's SQL-check plug names a table in full (`qualify_table(settings, name)`), or
    None when no plug says. The plug knows its estate's naming; the core only applies it."""
    found = []
    for name in inputs_of(doc, "sql_check"):
        fn = getattr(_class(name), "qualify_table", None)
        if callable(fn):
            found.append((fn, plug_settings(doc, name)))
    if not found:
        return None

    def qualify(table: str) -> str:
        for fn, settings in found:
            table = fn(settings, table)
        return table
    return qualify


def table_bound_problem(doc: dict) -> str | None:
    """Why S0 would refuse `doc` for want of a bounded table set, or None. The same rule S0
    applies: a run that reads query history admits a statement only when it reads a bounded
    table, so it needs `tables`, an asset scope, or a BI model whose lineage names tables."""
    if not inputs_of(doc, "history") or doc.get("tables") or inputs_of(doc, "scope"):
        return None
    bis = inputs_of(doc, "bi")
    if fetch_plan(doc) is not None:
        return None     # the lineage is known once fetched; S0 applies the rule then
    if any(not _setting(doc, bi, "bi", required=False) for bi in bis):
        return None     # a plug that delivers its files itself: S0 applies the rule then
    if bis:
        from metrics_agent import sockets as S  # noqa: PLC0415

        for bi in bis:
            path = _setting(doc, bi, "bi")
            try:
                from metrics_agent import readers as _readers  # noqa: PLC0415
                from metrics_agent.lib import bi_scope  # noqa: PLC0415 - reader deps are heavy

                where = pathlib.Path(str(path)).expanduser()
                # Undeclared, the vendor is the one installed reader that recognises the files.
                vendor = (_setting(doc, bi, "bi_vendor", required=False)
                          or getattr(_class(bi), "bi_vendor", None) or _readers.detect(where))
                if not vendor:
                    raise SystemExit(f"no installed BI reader recognises {where.name}; set "
                                     f"settings.{bi}.bi_vendor")
                model = _setting(doc, bi, "bi_model", required=False)
                if bi_scope.tables_of(where, vendor,
                                      **({"model": str(model)} if model else {}),
                                      dialect=str(doc.get("dialect") or ""),
                                      qualify=table_qualifier(doc)):
                    return None
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - say why, never raise here
                if isinstance(exc, (ConfigError, S.NotSupported)):
                    raise
                # The reader's own refusal (an extract root instead of one model, an
                # unreadable file) is the actionable reason; "lineage names none" would send
                # the operator looking in the wrong place.
                why = (str(exc).strip().splitlines()[0] if str(exc).strip()
                       else type(exc).__name__)
                return (f"the run reads query history and names no `tables`, and the BI "
                        f"definitions could not be read for their lineage: {why}")
        return ("the run reads query history, names no `tables`, and the BI model's lineage "
                "names none either: a statement is admitted only when it reads a bounded "
                "table")
    return ("the run reads query history and names no `tables`: a statement is admitted "
            "only when it reads a bounded table")


def publish_target(doc: dict) -> list[str]:
    """The lanes `doc`'s output plugs declare, sorted: the run's publish target."""
    return sorted({_lane_of(n) for n in doc["publish"]})


# The files that stamp custody at a folder's ROOT: a fetched extract's, and a capture's
# (`lib/bi_capture.MANIFEST`).
_ROOT_STAMPS = ("fetch_manifest.json", _bi_capture.MANIFEST)


def held_custody(paths: list[str]) -> str | None:
    """`third-party` when any of these inputs is stamped as passing through a third party,
    else None (the plug's own custody stands).

    A file carries its custody on a sidecar (`<file>.manifest.json`); a fetched extract
    carries it in `fetch_manifest.json` at its ROOT, and a capture (browser or MCP) in its
    `capture_manifest.json` (`lib/bi_capture`). The stamp binds every file under that
    root, however the files are handed over: the folder itself, a folder or a file inside it
    (one model of several), or a bigger folder holding it, since A1 reads everything under
    the folder it is given. So an input is held when it, or any folder above it, is stamped,
    or when anything stamped lies inside it."""
    from metrics_agent import sockets as S  # noqa: PLC0415
    from metrics_agent.lib.querylog import file_custody, stamped_custody  # noqa: PLC0415

    def _held(path: pathlib.Path) -> bool:
        path = path.expanduser().resolve()
        if path.is_file() and file_custody(path) == S.THIRD_PARTY:
            return True
        for folder in ([path] if path.is_dir() else []) + list(path.parents):
            if any(stamped_custody(folder / name) == S.THIRD_PARTY for name in _ROOT_STAMPS):
                return True
        if path.is_dir():
            for stamp in path.rglob("*manifest.json"):
                if stamp.name in _ROOT_STAMPS or stamp.name.endswith(".manifest.json"):
                    if stamped_custody(stamp) == S.THIRD_PARTY:
                        return True
        return False

    return S.THIRD_PARTY if any(_held(pathlib.Path(p)) for p in paths if p) else None


def recorded_settings(cls: Any, role: str, settings: dict[str, Any]) -> dict[str, Any]:
    """A plug's settings as S0 records them: as written, with every setting the plug declares
    as a `path` made absolute, so a stage run from another directory reads the same files."""
    out = dict(settings)
    for q in (getattr(cls, "setup_questions", None) or {}).get(role) or ():
        if q.kind != "path" or out.get(q.key) in (None, "", []):
            continue
        value = out[q.key]
        absolute = [str(pathlib.Path(str(v)).expanduser().resolve())
                    for v in (value if isinstance(value, list) else [value])]
        out[q.key] = absolute if isinstance(value, list) else absolute[0]
    return out


def socket_entry(doc: dict, name: str, role: str) -> dict[str, Any]:
    """The `run_inputs.plugs` entry of a plug: name, custody (the instance's, which may be
    stricter than the class's: a file whose sidecar says it passed through a third party),
    the installed package and version, and the settings it is built with."""
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    item = _plugs.installed().get(name)
    if item is None:
        raise ConfigError(f"no plug named {name!r} is installed (`metrics-agent plugs` lists "
                          "what is)")
    cls = item.load()
    settings = recorded_settings(cls, role, plug_settings(doc, name))
    custody = getattr(cls, "custody", None)
    try:
        instance = cls(settings, dialect=str(doc.get("dialect") or ""))
        custody = getattr(instance, "custody", None) or custody
    except Exception as exc:  # noqa: BLE001 - a plug that cannot be built is refused, by name
        raise ConfigError(f"plug {name!r} could not be built with its settings: "
                          f"{type(exc).__name__}: {exc}") from None
    out = {"name": name, "custody": custody, "package": item.package,
           "version": item.version, "settings": settings}
    if getattr(cls, "custodian", None):
        out["custodian"] = str(cls.custodian)
    return out


def entry(name: str) -> dict[str, Any]:
    """The `run_inputs.plugs` entry of an output plug or an observer: no custody, no
    settings of its own here (a destination's target is under `outputs`)."""
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    item = _plugs.installed().get(name)
    if item is None:
        raise ConfigError(f"no plug named {name!r} is installed")
    return {"name": name, "custody": None, "package": item.package, "version": item.version}


def selected(plugs: dict[str, Any]) -> list[Any]:
    """Every selected plug, once, as an object `socket_gates` can check. A plug listed for
    several capabilities is as restrictive as its most restrictive entry."""
    from types import SimpleNamespace

    from metrics_agent import socket_gates as _gates  # noqa: PLC0415

    seen: dict[str, Any] = {}
    for _cap, value in plugs.items():
        for e in (value if isinstance(value, list) else [value] if value else []):
            got = seen.setdefault(e["name"], SimpleNamespace(
                name=e["name"], custody=e["custody"], custodian=e.get("custodian")))
            if _gates.third_party(SimpleNamespace(custody=e["custody"])):
                got.custody = e["custody"]
    return list(seen.values())
