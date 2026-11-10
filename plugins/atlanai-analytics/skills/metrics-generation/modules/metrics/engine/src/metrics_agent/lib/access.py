# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The run's access report: every access, permission or availability limit the run met, one
named blocker per item, collected into `<run>/access_report.json` (`access_report.schema.json`).

A LIMIT NEVER STOPS THE RUN. A plug that meets one (a sign-in refused, an item this identity
may not read, a warehouse suspended, a table with no SELECT grant) hands it over as a blocker,
raised in `sockets.AccessBlocked` or returned by its `access_blockers` hook; the stage that
met it records it here and goes on with what is readable. Each blocker says:

  * `code`: the neutral name of the limit (data/access_codes.yml holds the defaults);
  * `item`: what was not read, and `not_read`: what exactly is missing;
  * `did_instead`: what the run did instead;
  * `ask`: what to ask for, and from whom (by role).

`finalize` then follows each blocker to the statements, widgets and metrics it leaves
unverified, and `summary_lines` is what the run prints at its end. The custody and read-only
checks are not limits: they still refuse, as before, and are never recorded here.

The core names no tool: a plug converts its tool's answer into a code.
"""
from __future__ import annotations

import functools
import json
import pathlib
from collections import Counter
from typing import Any, Iterable

import yaml

from metrics_agent._paths import DATA
from metrics_agent.lib import artifacts as _artifacts

REPORT = "access_report.json"
SCHEMA = "access_report.schema.json"
CODES = DATA / "access_codes.yml"
# A tool's own answer is kept short: the status and the error code are what the operator
# forwards; the rest of a long message is noise.
DETAIL_CHARS = 400

WHO_WORDS = {
    "operator": "you (the person running the agent)",
    "bi_item_owner": "the owner of the BI item",
    "bi_workspace_admin": "a BI workspace or project admin",
    "bi_site_admin": "a BI site admin",
    "bi_tenant_admin": "a BI tenant admin",
    "warehouse_admin": "the warehouse admin",
}


class AccessError(ValueError):
    """A blocker that is not in the common format: a plug's defect, named."""


@functools.lru_cache(maxsize=1)
def codes() -> dict[str, dict[str, str]]:
    """{code: {layer, scope, effect, who, did_instead}} from data/access_codes.yml."""
    doc = yaml.safe_load(CODES.read_text()) or {}
    return {str(k): dict(v) for k, v in doc.items()}


def blocker(code: str, *, source: str, stage: str, not_read: str, ask: str,
            who: str | None = None, item: dict[str, Any] | None = None,
            did_instead: str | None = None, detail: str | None = None,
            effect: str | None = None, scope: str | None = None,
            affects: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """One blocker in the common format, with the code's defaults filled in."""
    table = codes()
    if code not in table:
        raise AccessError(f"unknown access code {code!r}; known: {', '.join(sorted(table))}")
    row = table[code]
    out: dict[str, Any] = {
        "code": code,
        "layer": row["layer"],
        "scope": scope or row["scope"],
        "effect": effect or row["effect"],
        "source": str(source),
        "stage": str(stage),
        "item": ({k: (None if item.get(k) is None else str(item.get(k)))
                  for k in ("kind", "name", "id") if k in item} if item else None),
        "not_read": str(not_read),
        "did_instead": str(did_instead or row["did_instead"]),
        "ask": {"what": str(ask), "who": who or row["who"]},
    }
    if detail:
        out["detail"] = str(detail)[:DETAIL_CHARS]
    if affects:
        out["affects"] = {k: sorted({str(x) for x in v}) for k, v in affects.items() if v}
    return out


def _key(b: dict[str, Any]) -> tuple:
    item = b.get("item") or {}
    return (b["code"], b["source"], b["layer"], item.get("kind"), item.get("name"),
            item.get("id"), b["not_read"])


def empty() -> dict[str, Any]:
    return {"contract_version": "1.0", "blockers": [], "summary": _summary([])}


def load(run: str | pathlib.Path) -> dict[str, Any]:
    """The run's access report, or an empty one."""
    path = pathlib.Path(run) / REPORT
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return empty()
    return doc if isinstance(doc, dict) and "blockers" in doc else empty()


def _summary(blockers: list[dict[str, Any]]) -> dict[str, Any]:
    def union(kind: str) -> int:
        return len({x for b in blockers for x in (b.get("affects") or {}).get(kind, [])})
    return {"n_blockers": len(blockers),
            "by_code": dict(sorted(Counter(b["code"] for b in blockers).items())),
            "by_who": dict(sorted(Counter(b["ask"]["who"] for b in blockers).items())),
            "statements_unverified": union("statements"),
            "widgets_unverified": union("widgets"),
            "metrics_unverified": union("metrics")}


def _write(run: pathlib.Path, doc: dict[str, Any]) -> dict[str, Any]:
    doc["summary"] = _summary(doc["blockers"])
    _artifacts.dump(run / REPORT, doc, SCHEMA)
    return doc


def merge(into: list[dict[str, Any]], new: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """`into` with each of `new` added once: the same limit on the same item, met twice, is
    one blocker whose `affects` lists are the union."""
    index = {_key(b): b for b in into}
    for b in new:
        k = _key(b)
        if k not in index:
            index[k] = dict(b)
            into.append(index[k])
            continue
        got = index[k]
        for kind, ids in (b.get("affects") or {}).items():
            have = got.setdefault("affects", {}).setdefault(kind, [])
            got["affects"][kind] = sorted(set(have) | set(ids))
    return into


def record(run: str | pathlib.Path, blockers: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Add `blockers` to the run's access report (validated) and return the report. Each one
    is checked against the common format first, so a plug's malformed blocker is named."""
    run = pathlib.Path(run)
    new = list(blockers)
    for b in new:
        try:
            _artifacts.validate({"contract_version": "1.0", "blockers": [b],
                                 "summary": _summary([b])}, SCHEMA, what="a blocker")
        except _artifacts.ArtifactError as exc:
            raise AccessError(str(exc)) from None
    doc = load(run)
    merge(doc["blockers"], new)
    return _write(run, doc)


def clear_stage(run: str | pathlib.Path, stage: str) -> None:
    """Drop the blockers `stage` recorded on an earlier pass. A stage that runs again (a
    resumed run, a re-run after the access was granted) reports what it meets now, not what
    it met before; what other stages recorded stays."""
    run = pathlib.Path(run)
    if not (run / REPORT).is_file():
        return
    doc = load(run)
    kept = [b for b in doc["blockers"] if b.get("stage") != stage]
    if len(kept) != len(doc["blockers"]):
        doc["blockers"] = kept
        _write(run, doc)


def from_exception(exc: BaseException) -> list[dict[str, Any]]:
    """The blockers an `AccessBlocked` carries ([] for any other exception)."""
    return [dict(b) for b in (getattr(exc, "blockers", None) or [])]


GAP_ASK = {
    "report_definition": "capture the report's own definition: its download, or a fetch "
                         "through the BI plug with an API credential",
    "model_definition": "capture the model's definition: its download, or a fetch through "
                        "the BI plug with an API credential",
    "data_source_definition": "capture each data source named here: its download, or a fetch "
                              "through the BI plug, which downloads it",
    "displayed_values": "export the values each widget shows (the browser route's Export "
                        "data, or the MCP server's view-data tool)",
    "warehouse_lineage": "capture the data source's own file, which names the warehouse tables",
    "row_security_context": "run the capture as an identity whose row-level security is known",
}


def from_gap(gap: dict[str, Any], *, source: str, stage: str) -> dict[str, Any]:
    """A capture's gap (`bi_capture` gap) as a blocker: its access code when the route met an
    access limit, else `capture_gap`."""
    code = gap.get("access_code") or "capture_gap"
    if code not in codes():
        code = "capture_gap"
    objs = [str(o) for o in gap.get("objects") or []]
    return blocker(code, source=source, stage=stage,
                   item={"kind": gap["what"], "name": ", ".join(objs) or None},
                   not_read=f"{gap['what'].replace('_', ' ')}: {gap['cannot_verify']}",
                   ask=GAP_ASK.get(gap["what"], "capture what this gap names"),
                   detail=gap.get("detail"))


def verdict_access(b: dict[str, Any]) -> dict[str, Any]:
    """What a witness verdict keeps of a blocker: its code and the tool's own answer."""
    return {"code": b["code"], "detail": b.get("detail")}


def is_run_wide(b: dict[str, Any]) -> bool:
    return b.get("scope") == "run"


class Meter:
    """The access limits one witnessing stage meets, statement by statement.

    `sink` is handed to `witness.witness(blockers=...)`; `seen(statement)` files whatever it
    caught under that statement. The first RUN-WIDE limit (a suspended or over-quota
    warehouse, a refused sign-in) ends the sending: `run_wide` is then set, and the stage
    gives every remaining statement `witness.blocked` without asking the warehouse again, so
    one refusal is one blocker, not one per statement."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.sink: list[dict[str, Any]] = []
        self.run_wide: dict[str, Any] | None = None
        self._found: list[dict[str, Any]] = []

    def _own(self, b: dict[str, Any], statement: str | None) -> dict[str, Any]:
        b = dict(b)
        b["stage"] = self.stage
        if statement:
            aff = dict(b.get("affects") or {})
            aff["statements"] = sorted(set(aff.get("statements") or []) | {statement})
            b["affects"] = aff
        return b

    def connect_failed(self, exc: BaseException) -> bool:
        """True when a failed connection was an access limit (now `run_wide`)."""
        got = from_exception(exc)
        if not got:
            return False
        self.run_wide = self._own(got[0], None)
        merge(self._found, [self.run_wide] + [self._own(b, None) for b in got[1:]])
        return True

    def seen(self, statement: str) -> None:
        for b in self.sink:
            mine = self._own(b, statement)
            merge(self._found, [mine])
            if is_run_wide(mine) and self.run_wide is None:
                self.run_wide = mine
        self.sink.clear()

    def skipped(self, statement: str) -> None:
        """A statement not sent because of `run_wide`: filed under that blocker."""
        if self.run_wide is not None:
            merge(self._found, [self._own(self.run_wide, statement)])

    def blockers(self) -> list[dict[str, Any]]:
        return list(self._found)


# ── what each blocker leaves unverified ─────────────────────────────────────────────────
def _read(run: pathlib.Path, name: str) -> Any:
    try:
        return json.loads((run / name).read_text())
    except (OSError, ValueError):
        return None


def _widgets_by_statement(run: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("bi_candidates.json", "candidates.json"):
        doc = _read(run, name) or {}
        for c in doc.get("candidates") or []:
            unit = (((c.get("provenance") or {}).get("bi") or {}).get("unit_id"))
            if c.get("candidate_id") and unit:
                out.setdefault(str(c["candidate_id"]), str(unit))
    return out


def _metrics_by_statement(run: pathlib.Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    doc = _read(run, "metric_cards.json") or {}
    for m in doc.get("metrics") or []:
        name = m.get("name") or m.get("metric_ref")
        for vq in ((m.get("provenance") or {}).get("derived_from") or []):
            if name:
                out.setdefault(str(vq), set()).add(str(name))
    return out


def finalize(run: str | pathlib.Path) -> dict[str, Any] | None:
    """Follow every blocker to the widgets and metrics its statements feed, and rewrite the
    report. None when the run met no limit (no report is written then)."""
    run = pathlib.Path(run)
    if not (run / REPORT).is_file():
        return None
    doc = load(run)
    widgets = _widgets_by_statement(run)
    metrics = _metrics_by_statement(run)
    for b in doc["blockers"]:
        aff = b.get("affects") or {}
        stmts = aff.get("statements") or []
        w = set(aff.get("widgets") or []) | {widgets[s] for s in stmts if s in widgets}
        m = set(aff.get("metrics") or []) | {x for s in stmts for x in metrics.get(s, ())}
        if w:
            aff["widgets"] = sorted(w)
        if m:
            aff["metrics"] = sorted(m)
        if aff:
            b["affects"] = aff
    return _write(run, doc)


def _item_words(b: dict[str, Any]) -> str:
    item = b.get("item") or {}
    if not item:
        return b["source"]
    return " ".join(x for x in (item.get("kind"), repr(item["name"]) if item.get("name")
                                else item.get("id")) if x)


def summary_lines(doc: dict[str, Any] | None) -> list[str]:
    """What the run prints at its end: one line per blocker, grouped by whom to ask."""
    if not doc or not doc.get("blockers"):
        return []
    s = doc["summary"]
    head = (f"access: {s['n_blockers']} blocker(s); the run went on with what it could read. "
            f"Unverified because of them: {s['statements_unverified']} statement(s), "
            f"{s['widgets_unverified']} widget(s), {s['metrics_unverified']} metric(s). "
            f"Details: {REPORT}")
    lines = [head]
    for who in sorted({b["ask"]["who"] for b in doc["blockers"]}):
        lines.append(f"  ask {WHO_WORDS.get(who, who)}:")
        for b in doc["blockers"]:
            if b["ask"]["who"] != who:
                continue
            n = len((b.get("affects") or {}).get("statements") or [])
            lines.append(f"    - [{b['code']}] {_item_words(b)}: {b['ask']['what']}"
                         + (f" ({n} statement(s))" if n else ""))
            lines.append(f"      not read: {b['not_read']}; instead: {b['did_instead']}")
    return lines
