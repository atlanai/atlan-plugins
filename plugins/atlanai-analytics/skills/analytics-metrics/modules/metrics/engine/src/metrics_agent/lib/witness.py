# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The witness — and the honest statement of what it proves.

It proves a statement PARSES and EXECUTES and returns a shape. It does NOT prove the number
is right: a metric computing the wrong thing passes, and a correct one fails on artifacts.
So every published object carries the verdict it actually earned, and neither `verified` nor
`parse_only` is ever `approved`.

`empty` is a SEPARATE verdict from `pass`: executed but every column NULL or zero almost
always means a filter is wrong rather than the SQL broken, and collapsing the two hides the
more common defect.

Witnessing NEVER aborts a run. A requested-but-unreachable substrate records
`skipped_no_substrate` / `missing_object` and the pipeline continues — an unwitnessed object
is simply not `verified`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any

from metrics_agent import socket_gates as _gates
from metrics_agent import sockets as _sockets
from . import sqlshape

MODES = ("none", "parse", "explain", "execute")


def bind_parameters(sql: str, parameters: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Substitute `:name` placeholders with each parameter's DECLARED default.

    A parameterized canonical statement cannot execute unbound, so without this every
    parameterized metric would record `fail` and the verdict would describe our binding
    discipline rather than the estate. The defaults are the producer's own declaration, not
    invented dummies, and the bindings used are recorded ON the verdict so a reader can see
    exactly which question was executed.
    """
    bound: dict[str, Any] = {}
    out = sql
    # A PLAIN replace of `:name` matches a PREFIX of a longer placeholder: a parameter called
    # `s` rewrites `:subject_pattern` into `'%wave%'ubject_pattern`, and the statement stops
    # parsing — so the verdict then reports `parse_failed` about SQL the author never wrote.
    # Require a placeholder to end at a non-identifier character, and bind the longest names
    # first so no parameter can eat another's prefix. Seen once on this estate, and once is
    # enough: the failure looks exactly like a broken canonical statement.
    seen: set[str] = set()
    for p in sorted(parameters or [], key=lambda x: -len(str(x.get("name") or ""))):
        name = str(p.get("name") or "")
        if not name or p.get("default") is None or name in seen:
            continue
        seen.add(name)
        val = p["default"]
        try:
            float(val)
            lit = str(val)
        except (TypeError, ValueError):
            lit = "'" + str(val).replace("'", "''") + "'"
        pattern = re.compile(r":" + re.escape(name) + r"(?![A-Za-z0-9_])")
        out, n = pattern.subn(lit.replace("\\", "\\\\"), out)
        if n:
            bound[name] = val
    return out, bound


def bind_viewer(sql: str, binds: list[dict[str, Any]], names: dict[str, str] | None,
                dialect: str) -> tuple[str | None, list[str], list[str]]:
    """Substitute each viewer placeholder (`binds`: {of, placeholder}) with the fetching
    identity's value for it (`names`: of -> value), as a string literal of the dialect.

    IN MEMORY ONLY (guardrail 8): the bound statement is executed and dropped; the verdict
    records which values were bound (`viewer_bound`), never what they were. Returns (bound
    statement or None when a value is missing, the values bound, the values missing)."""
    out, bound, missing = sql, [], []
    for b in binds or []:
        of, ph = str(b.get("of") or ""), str(b.get("placeholder") or "")
        val = (names or {}).get(of)
        if not ph:
            continue
        if not val:
            missing.append(of)
            continue
        lit = sqlshape.string_literal(val, dialect)
        out = re.sub(re.escape(ph) + r"(?![A-Za-z0-9_])", lambda _m: lit, out)
        bound.append(of)
    return (None if missing else out), sorted(set(bound)), sorted(set(missing))


def mask_viewer(text: Any, names: dict[str, str] | None) -> Any:
    """`text` with every occurrence of a bound viewer value (in any case) replaced by its
    label: what a verdict may keep of a result read as one person."""
    if not isinstance(text, str) or not names:
        return text
    for of, val in sorted((names or {}).items(), key=lambda kv: -len(str(kv[1] or ""))):
        if val:
            text = re.sub(re.escape(str(val)), f"<viewer {of}>", text, flags=re.IGNORECASE)
    return text


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _cells(rows: list[tuple[Any, ...]]) -> list[list[str]]:
    return [["" if c is None else str(c) for c in r] for r in rows]


def _row_hash(cols: list[str], rows: list[tuple[Any, ...]]) -> str:
    """An ORDER-INDEPENDENT fingerprint of the result set.

    A statement with no ORDER BY has no defined row order, so a positional hash changes
    between two executions over identical data — which makes it useless as evidence and makes
    two runs look different when nothing is. Sorting first makes the hash describe the SET.
    """
    body = sorted("|".join(r) for r in _cells(rows))
    blob = "|".join(cols) + "||" + "||".join(body)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _all_blank(rows: list[tuple[Any, ...]]) -> bool:
    if not rows:
        return True
    for r in rows:
        for c in r:
            if c is None:
                continue
            if isinstance(c, (int, float)) and c == 0:
                continue
            return False
    return True


def blocked(exc: BaseException, _mask=None) -> dict[str, Any]:
    """The verdict of a statement an access limit kept from being checked: parsed, nothing
    ran (`skipped_no_substrate`, labelled `parse_only`), with the limit's code kept on it."""
    first = (getattr(exc, "blockers", None) or [{}])[0]
    msg = str(first.get("detail") or exc)
    msg = _mask(msg) if _mask else msg
    out = {"result": "skipped_no_substrate", "rows": None, "hash": None, "executed_at": None,
           "error": msg[:500], "columns": [], "sample": []}
    if first.get("code"):
        out["access"] = {"code": str(first["code"]), "detail": msg[:500]}
    return out


def _masked_blocker(b: dict[str, Any], _mask=None) -> dict[str, Any]:
    """A blocker as the access report keeps it: with `_mask` (a bound statement), every text
    the warehouse's answer may have filled (its detail, what was not read, the item) masked."""
    out = dict(b)
    if _mask is None:
        return out
    for key in ("detail", "not_read"):
        if isinstance(out.get(key), str):
            out[key] = _mask(out[key])
    if isinstance(out.get("item"), dict):
        out["item"] = {k: (_mask(v) if isinstance(v, str) else v)
                       for k, v in out["item"].items()}
    return out


def disabled() -> dict[str, Any]:
    return {"result": "disabled", "rows": None, "hash": None, "executed_at": None,
            "error": None, "columns": [], "sample": []}


def witness(
    sql: str,
    dialect: str,
    mode: str,
    *,
    eng: _sockets.SqlRunner | None = None,
    limit: int = 50,
    sample_rows: int = 2,
    parameters: list[dict[str, Any]] | None = None,
    viewer_binds: list[dict[str, Any]] | None = None,
    viewer_names: dict[str, str] | None = None,
    blockers: list[dict[str, Any]] | None = None,
    _mask=None,
) -> dict[str, Any]:
    """Return one verdict dict conforming to the shared witness shape.

    `parse` is the default and needs no warehouse at all. `explain` and `execute` are opt-in
    and need a substrate that actually holds the objects.

    A statement FILTERED TO THE VIEWING USER (`viewer_binds`) is parsed as it is published,
    with its placeholders, and executed bound to `viewer_names` (the fetching identity's,
    in memory). Every bound value is masked out of what the verdict keeps.

    An access limit the warehouse answers with (`sockets.AccessBlocked`) gives a
    `skipped_no_substrate` verdict carrying the limit's code, and its blockers are appended to
    `blockers`, for the stage to record in the run's access report.
    """
    if mode not in MODES:
        raise ValueError(f"unknown witness mode {mode!r}; known: {', '.join(MODES)}")
    if mode == "none":
        return disabled()
    if viewer_binds:
        out = _witness_bound(sql, dialect, mode, eng=eng, limit=limit,
                             sample_rows=sample_rows, parameters=parameters,
                             viewer_binds=viewer_binds, viewer_names=viewer_names or {},
                             blockers=blockers)
        return out

    sql, bound = bind_parameters(sql, parameters or [])

    # Every mode parses first: a statement that does not parse cannot be executed either, and
    # the parse verdict is the cheaper, more specific message.
    try:
        sqlshape.parse(sql, dialect)
    except sqlshape.ParseError as exc:
        # executed_at stays None: nothing ran. Stamping a time here would claim an
        # execution that never happened, and it is the only thing that would make this
        # artifact differ between two otherwise identical runs.
        return {"result": "parse_failed", "rows": None, "hash": None,
                "executed_at": None, "error": str(exc), "columns": [], "sample": []}

    if mode == "parse":
        return {"result": "parse_ok", "mode": mode, "rows": None, "hash": None,
                "executed_at": None, "error": None, "columns": [], "sample": []}

    if eng is None:
        return {"result": "skipped_no_substrate", "rows": None, "hash": None,
                "executed_at": None,
                "error": "witness mode requested a warehouse but none was configured",
                "columns": [], "sample": []}

    try:
        if mode == "explain":
            # Compile-only: the engine resolves every table and column and never scans. Its
            # own method, because `fetch` wraps its argument in a subquery and nothing can
            # select from an EXPLAIN.
            eng.explain(sql)
            out = {"result": "pass", "mode": mode, "rows": None, "hash": None,
                   "executed_at": _now(), "error": None, "columns": [], "sample": []}
            if bound:
                out["bound_parameters"] = bound
            return out
        cols, rows = eng.fetch(sql, limit)
    except _gates.ReadOnlyViolation:
        raise
    except _sockets.NotRouted as exc:
        # Several SQL-check plugs, and no one of them holds every table this statement
        # reads: it parsed, nothing ran, and the verdict says why (labelled parse_only).
        return {"result": "skipped_no_substrate", "rows": None, "hash": None,
                "executed_at": None, "error": str(exc), "columns": [], "sample": []}
    except _sockets.AccessBlocked as exc:
        # AN ACCESS LIMIT, NOT A FAILED STATEMENT: the warehouse refused this identity (a
        # suspended warehouse, no SELECT grant), so nothing was checked. Unverified for that
        # named reason, never quarantined as if the SQL were wrong; the stage records the
        # blocker in the run's access report.
        if blockers is not None:
            # The blockers go to the run's access report: a statement bound to a person's
            # name keeps it out of them as it does out of the verdict.
            blockers.extend(_masked_blocker(b, _mask) for b in exc.blockers)
        return blocked(exc, _mask)
    except Exception as exc:
        msg = _mask(str(exc)) if _mask else str(exc)
        low = msg.lower()
        missing = any(
            tok in low for tok in
            ("does not exist", "not found", "unknown table", "invalid identifier",
             "no such table", "cannot be found", "undefined table")
        )
        # A real attempt was made against a real engine, so this one IS stamped.
        return {"result": "missing_object" if missing else "fail", "rows": None,
                "hash": None, "executed_at": _now(), "error": msg[:500],
                "columns": [], "sample": []}

    if _mask is not None:
        cols = [_mask(str(c)) for c in cols]
        rows = [tuple(_mask(c) for c in r) for r in rows]
    # Sampled from the SORTED rows for the same reason: an arbitrary slice of an unordered
    # result is not reproducible, and a sample that changes every run cannot be diffed.
    sample = sorted(_cells(rows))[:sample_rows]
    result = "empty" if _all_blank(rows) else "pass"
    out = {"result": result, "mode": mode, "rows": len(rows), "hash": _row_hash(cols, rows),
           "executed_at": _now(), "error": None, "columns": cols,
           "sample": sample}
    if bound:
        out["bound_parameters"] = bound
    return out


def _witness_bound(sql: str, dialect: str, mode: str, *, eng, limit: int, sample_rows: int,
                   parameters, viewer_binds: list[dict], viewer_names: dict[str, str],
                   blockers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """`witness` of a statement filtered to the viewing user: parsed with its placeholders,
    executed bound to the identity's names, every name masked out of the verdict."""
    try:
        sqlshape.parse(bind_parameters(sql, parameters or [])[0], dialect)
    except sqlshape.ParseError as exc:
        return {"result": "parse_failed", "rows": None, "hash": None, "executed_at": None,
                "error": str(exc), "columns": [], "sample": []}
    if mode == "parse":
        return {"result": "parse_ok", "mode": mode, "rows": None, "hash": None,
                "executed_at": None, "error": None, "columns": [], "sample": []}
    names = {k: v for k, v in viewer_names.items() if v}
    bound_sql, bound, missing = bind_viewer(sql, viewer_binds, names, dialect)
    if bound_sql is None:
        return {"result": "skipped_no_substrate", "rows": None, "hash": None,
                "executed_at": None,
                "error": ("the statement is filtered to the viewing user and this run has no "
                          "identity's " + " and ".join(m.replace("_", " ") for m in missing
                                                       if not m.startswith("withheld_"))
                          + " to bind it to (a BI plug that fetched as an identity and offers "
                          "viewer_names); parsed, not executed"
                          if any(not m.startswith("withheld_") for m in missing) else
                          "the statement compares with a literal the BI file holds and this "
                          "run could not read it again (" + ", ".join(missing) + "); parsed, "
                          "not executed"),
                "columns": [], "sample": []}
    try:
        # NOTHING READ AS ONE PERSON IS KEPT AS IT CAME: the error, the column names and every
        # cell are masked before the sample and the fingerprint are taken.
        out = witness(bound_sql, dialect, mode, eng=eng, limit=limit,
                      sample_rows=sample_rows, parameters=parameters, blockers=blockers,
                      _mask=lambda v: mask_viewer(v, names))
    except _gates.ReadOnlyViolation as exc:
        raise _gates.ReadOnlyViolation(mask_viewer(str(exc), names)) from None
    viewer = [b for b in bound if not b.startswith("withheld_")]
    if viewer:
        out["viewer_bound"] = viewer
    if len(viewer) < len(bound):
        # A literal the scrub withheld (lib/withheld), bound from the BI file in memory.
        out["withheld_bound"] = [b for b in bound if b.startswith("withheld_")]
    return out


PASSING = {"pass", "parse_ok"}


def label_for(verdict: dict[str, Any] | None, flagged: bool) -> str:
    """Map a witness verdict to the FROZEN record label vocabulary.

    verified     — THE WAREHOUSE ACCEPTED IT. How strongly depends on the substrate the run
                   had, and the verdict records which: under `execute` it ran and returned a
                   shape (`rows` and `hash` are present); under `explain` it compiled, so every
                   table, column and grant resolved, but nothing ran (`rows` and `hash` are
                   null). Both are real evidence from the warehouse and neither is a guess —
                   but a consumer that needs execution evidence must read `witness.mode`, not
                   the label. The vocabulary is frozen at three values, so the label cannot
                   carry that distinction and does not pretend to.
    parse_only   — parsed only. Either no warehouse was reachable (`skipped_no_substrate`) or
                   none was asked for. Syntax, and nothing else: on a real estate 38% of
                   statements that parsed then failed to compile.
    needs_review — a gate flagged it, the witness returned `empty`, or the statement did not
                   compile. NOT a rejection: a history statement comes verbatim from the query
                   log and ran on the estate, so a failure here is usually this run's substrate
                   rather than the question.

    Never `approved`: the agent certifies nothing.
    """
    if flagged:
        return "needs_review"
    if not verdict:
        return "parse_only"
    r = verdict.get("result")
    if r == "pass":
        return "verified"
    if r == "empty":
        return "needs_review"
    if r in ("parse_ok", "disabled", "skipped_no_substrate"):
        return "parse_only"
    return "needs_review"


def result_for_registry(verdict: dict[str, Any] | None) -> dict[str, Any] | None:
    """`examples[].result` may be emitted ONLY from a witness that actually passed with every
    field present. An absent or failed witness yields no result at all — never a zero-filled
    one, which would claim evidence nobody has."""
    if not verdict or verdict.get("result") != "pass":
        return None
    needed = ("columns", "rows", "hash", "executed_at")
    if any(verdict.get(k) in (None, [], "") for k in needed):
        return None
    sample = verdict.get("sample") or []
    cols = verdict["columns"]
    if any(len(row) != len(cols) for row in sample):
        return None  # the gateway refuses a row whose width differs from columns
    return {
        "columns": cols,
        "row_count": int(verdict["rows"]),
        "hash": verdict["hash"],
        "executed_at": verdict["executed_at"],
        "rows": sample,
    }
