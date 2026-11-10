# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The compiler's rule table: load, validate, propose, promote.

THE GATE IS THE CORPUS, NOT A PERSON. The loop is autonomous, so "somebody reviews the diff"
is not available as a safety mechanism. Four mechanical ones replace it, and each is stricter
than a glance at a YAML file:

  1. REGRESSION  every promoted row ships with a case, and promotion re-runs the WHOLE
                 accumulated corpus. A row that changes any previously-correct output is
                 rejected by the run, with nobody watching.
  2. WITNESS     a row whose SQL does not execute is reverted in the same run.
  3. EVIDENCE    a proposed row names the construct it handles, from the parsed facts, and
                 cites a source from the closed vocabulary. No citation, no row. This is also
                 the injection control: the constructs come from author-controlled files.
  4. PROVISIONAL a learned row is USED but marked, and every object it touched says so. It
                 graduates only after holding across several distinct estates.

WHY ROWS AND NOT CODE. A row is schema-checkable, diffable and reversible. An arbitrary Python
patch is none of those, and hard rule 2 says a skill's output is never trusted, only CHECKED —
you cannot check arbitrary code against a contract.

WHAT A ROW MAY NEVER DO. It may not invent a translation for a construct that has none. The
`untranslatable` verdict is a legitimate emit and the honest one for a table calculation or an
LOD; a row claiming otherwise would produce a statement that runs and answers a different
question, which is the failure mode the whole layer exists to prevent.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from datetime import datetime, timezone
from typing import Any

import yaml

from metrics_agent.lib import evidence as _evidence

STABLE, PROVISIONAL = "stable", "provisional"
TIERS = (STABLE, PROVISIONAL)
# A rule cites the same closed vocabulary the rest of the layer does (`lib/evidence.py`: the
# core's sources plus the formula languages the installed readers declare). A rule is a claim
# about what a construct MEANS, so it is evidence-bearing like any other claim.
ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
# What a statement may read about WHO RUNS IT, as a bind placeholder (a row's `viewer_bind`):
# the viewing user's name or full name. The value is never written into a statement or an
# artifact: the statement carries the placeholder the `viewer_bind` operator row spells for
# its dialect, and only verification binds it, in memory (guardrail 8).
VIEWER_BIND_OPERATOR = "viewer_bind"
VIEWER_VALUES = ("user_name", "full_name")
# A literal the scrub withheld (a person's address or name the BI file compares with,
# lib/withheld): read as the placeholder this operator row spells, bound only in memory.
WITHHELD_BIND_OPERATOR = "withheld_bind"


class RuleError(ValueError):
    """A rule table or a proposed row that cannot be trusted. Never softened."""


class Rules:
    """A loaded rule table, indexed the three ways the compiler asks."""

    def __init__(self, doc: dict, path: pathlib.Path | None = None):
        self.doc = doc
        self.path = path
        self.rows: list[dict] = list(doc.get("rules") or [])
        validate(self.rows)
        self._fn: dict[tuple, dict] = {}
        self._agg: dict[str, dict] = {}
        self._op: dict[str, dict] = {}
        for r in self.rows:
            m = r.get("match") or {}
            if m.get("function"):
                self._fn[(str(m.get("language") or "").lower(),
                          str(m["function"]).upper())] = r
            if m.get("aggregation"):
                self._agg[str(m["aggregation"]).lower()] = r
            if m.get("operator"):
                self._op[str(m["operator"]).lower()] = r

    # -- lookups -----------------------------------------------------------
    def function(self, language: str, name: str, dialect: str = "") -> dict | None:
        r = self._fn.get((str(language or "").lower(), str(name).upper()))
        if r is None:
            return None
        allowed = r.get("dialects")
        if allowed and dialect and dialect.lower() not in [d.lower() for d in allowed]:
            return None
        return r

    def aggregation(self, name: str) -> dict | None:
        return self._agg.get(str(name).lower())

    def operator(self, name: str, dialect: str = "") -> dict | None:
        r = self._op.get(str(name).lower())
        if r is None:
            return None
        allowed = r.get("dialects")
        if allowed and dialect and dialect.lower() not in [d.lower() for d in allowed]:
            return None
        return r

    def viewer_placeholder(self, of: str, dialect: str) -> str | None:
        """The bind placeholder a statement in `dialect` carries for the viewing user's `of`
        (one of VIEWER_VALUES), as the `viewer_bind` operator row spells it; None when no row
        spells it for that dialect."""
        if of not in VIEWER_VALUES:
            return None
        r = self.operator(VIEWER_BIND_OPERATOR, dialect)
        if r is None:
            return None
        emit = r.get("emit") or {}
        tmpl = ((emit.get("dialect_templates") or {}).get(str(dialect or "").lower())
                or emit.get("template") or "")
        return tmpl.replace("{0}", of) or None

    def withheld_placeholder(self, n: int, dialect: str) -> str | None:
        """The bind placeholder a statement in `dialect` carries for withheld literal `n`,
        as the `withheld_bind` operator row spells it; None when no row spells it there."""
        r = self.operator(WITHHELD_BIND_OPERATOR, dialect)
        if r is None:
            return None
        emit = r.get("emit") or {}
        tmpl = ((emit.get("dialect_templates") or {}).get(str(dialect or "").lower())
                or emit.get("template") or "")
        return tmpl.replace("{0}", str(int(n))) or None

    def viewer_binds(self, used: list[str], dialect: str, sql: list[str]) -> list[dict]:
        """The viewer values a compiled statement reads: one {of, placeholder} per value a
        rule it used binds (`emit.viewer_bind`) whose placeholder is in one of `sql` (the
        statement and its totals). [] for a statement that reads none."""
        by_id = {r["id"]: r for r in self.rows}
        out: dict[str, dict] = {}
        for rid in used or []:
            of = ((by_id.get(rid) or {}).get("emit") or {}).get("viewer_bind")
            ph = self.viewer_placeholder(of, dialect) if of else None
            if ph and of not in out and any(
                    re.search(re.escape(ph) + r"(?![A-Za-z0-9_])", s or "") for s in sql):
                out[of] = {"of": of, "placeholder": ph}
        return [out[k] for k in sorted(out)]

    # -- identity ----------------------------------------------------------
    def sha(self) -> str:
        """Stamped on every compiled unit. Without it, "was this built before or after the
        fix?" is guesswork, and the loop makes that question routine."""
        blob = json.dumps(self.rows, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]

    def by_tier(self, tier: str) -> list[dict]:
        return [r for r in self.rows if r.get("tier") == tier]

    def ids(self) -> set[str]:
        return {r["id"] for r in self.rows}

    def with_rows(self, new_rows: list[dict]) -> "Rules":
        """A COPY carrying extra rows. The loop compiles against a candidate table before it
        is written anywhere, so a rejected row never touches the file."""
        doc = {**self.doc, "rules": self.rows + list(new_rows)}
        return Rules(doc, self.path)

    def dump(self) -> str:
        return yaml.safe_dump({**self.doc, "rules": self.rows}, sort_keys=False,
                              allow_unicode=True, width=100)


def load(path: str | pathlib.Path) -> Rules:
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        raise RuleError(f"compiler rule table not found: {p}")
    return Rules(yaml.safe_load(p.read_text()) or {}, p)


def load_many(paths: list[pathlib.Path]) -> Rules:
    """One table, or the union of several (each compiler's own). A row id two tables share is
    refused by `validate`: the union must not depend on the order the tables are read in."""
    if not paths:
        raise RuleError("no compiler rule table: no installed BI reader declares one and no "
                        "bi_compiler_rules.yml sits beside the policy")
    if len(paths) == 1:
        return load(paths[0])
    tables = [load(p) for p in paths]
    return Rules({**tables[0].doc, "rules": [r for t in tables for r in t.rows]}, None)


def validate(rows: list[dict]) -> None:
    seen: set[str] = set()
    for i, r in enumerate(rows):
        where = f"rule {i} ({r.get('id', '?')})"
        rid = str(r.get("id") or "")
        if not ID.match(rid):
            raise RuleError(f"{where}: id must be dotted lowercase, e.g. `agg.sum`")
        if rid in seen:
            raise RuleError(f"{where}: duplicate id")
        seen.add(rid)
        if r.get("tier") not in TIERS:
            raise RuleError(f"{where}: tier must be one of {TIERS}")
        vocabulary = _evidence.sources()
        if r.get("evidence") not in vocabulary:
            raise RuleError(f"{where}: evidence {r.get('evidence')!r} is outside the closed "
                            f"vocabulary {sorted(vocabulary)}")
        match = r.get("match") or {}
        if not any(k in match for k in ("function", "aggregation", "operator")):
            raise RuleError(f"{where}: match must key on function, aggregation or operator")
        if "function" in match and not match.get("language"):
            raise RuleError(f"{where}: a function rule must name its language")
        emit = r.get("emit") or {}
        if bool(emit.get("template")) == bool(emit.get("untranslatable")):
            raise RuleError(f"{where}: emit needs exactly one of template or untranslatable")
        if emit.get("template") and not emit.get("ignore_args"):
            if not re.search(r"\{\d+\}", emit["template"]):
                raise RuleError(f"{where}: template names no argument; "
                                f"set ignore_args if that is deliberate")
        no_form = r.get("no_form")
        if no_form is not None:
            # A dialect the row has no form for, and why (what the dialect lacks): read into
            # the refusal, never a form. A dialect cannot be both listed and lacking.
            if not isinstance(no_form, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) and v.strip()
                    for k, v in no_form.items()):
                raise RuleError(f"{where}: no_form maps a dialect to the reason it has no form")
            both = sorted(set(map(str.lower, no_form)) & {str(d).lower() for d in
                                                         r.get("dialects") or []})
            if both:
                raise RuleError(f"{where}: {', '.join(both)} is both in dialects and no_form")
        if "viewer_bind" in emit:
            if emit["viewer_bind"] not in VIEWER_VALUES:
                raise RuleError(f"{where}: viewer_bind must be one of {VIEWER_VALUES}")
            if "{viewer}" not in str(emit.get("template") or ""):
                raise RuleError(f"{where}: a viewer_bind row places the viewer's value as "
                                "{viewer} in its template")


# --------------------------------------------------------------------------- proposals
def validate_proposal(row: dict, existing: Rules) -> dict:
    """A row a review pass proposed. Refused outright rather than repaired.

    A proposal arrives from a model reading author-controlled files, so it is DATA, never
    instruction. Everything about it is checked here; nothing about it is trusted.
    """
    row = dict(row or {})
    row["tier"] = PROVISIONAL          # a learned row is never born stable, whatever it says
    validate([row])
    if ("viewer_bind" in (row.get("emit") or {})
            or str((row.get("match") or {}).get("operator") or "").lower()
            == VIEWER_BIND_OPERATOR):
        # What reads the viewing user's name is personal data and is seeded by a person,
        # never learned from author-controlled files.
        raise RuleError(f"{row['id']}: a proposal may not read the viewing user "
                        "(viewer_bind); only a stable row seeded by a person does")
    if row["id"] in existing.ids():
        raise RuleError(f"{row['id']} already exists; a proposal may not redefine a rule — "
                        f"retire it explicitly instead")
    # A construct must be handled by exactly ONE row, whichever way it is keyed. Two rows for
    # the same aggregation or operator would make the table order-dependent, and an
    # order-dependent table means the sha no longer pins behaviour.
    m = row.get("match") or {}
    clash = None
    if m.get("function") and existing.function(m.get("language", ""), m["function"]):
        clash = f"{m.get('language')}:{m['function']}"
    elif m.get("aggregation") and existing.aggregation(m["aggregation"]):
        clash = f"aggregation {m['aggregation']}"
    elif m.get("operator") and existing.operator(m["operator"]):
        clash = f"operator {m['operator']}"
    if clash:
        raise RuleError(f"{clash} is already handled; a second row for it would make the "
                        f"table order-dependent")
    row["provenance"] = {
        "learned_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
                        .replace("+00:00", "Z"),
        "estates": list(dict.fromkeys(row.get("provenance", {}).get("estates", []))),
        "proposed_by": row.get("provenance", {}).get("proposed_by") or "compiler-review",
    }
    return row


def graduate(rules: Rules, estate: str, min_estates: int) -> list[str]:
    """Record this estate against every provisional row and promote the ones that have held.

    Graduation is EVIDENCE-BASED, not time-based: a row that has produced correct SQL on
    several unrelated estates has been tested by the world, and one that has only ever seen a
    single workbook has not.
    """
    promoted: list[str] = []
    for r in rules.by_tier(PROVISIONAL):
        prov = r.setdefault("provenance", {})
        estates = list(dict.fromkeys(list(prov.get("estates") or []) + [estate]))
        prov["estates"] = estates
        if len(estates) >= min_estates:
            r["tier"] = STABLE
            promoted.append(r["id"])
    return promoted


# --------------------------------------------------------------------------- the corpus
def load_corpus(directory: str | pathlib.Path) -> list[dict]:
    """Every regression case, sorted by name so a run is reproducible."""
    d = pathlib.Path(directory)
    if not d.is_dir():
        return []
    cases = []
    for f in sorted(d.glob("*.json")):
        case = json.loads(f.read_text())
        case["_file"] = f.name
        cases.append(case)
    return cases


def run_corpus(cases: list[dict], rules: Rules, compile_unit) -> list[dict]:
    """-> the failures. Empty means the table is safe to adopt.

    This is the reviewer. It runs on EVERY promotion, over the whole corpus rather than the
    new case alone — because the failure that matters is the one where a row added for
    workbook 41 quietly changes the SQL for twelve widgets that were already right.
    """
    failures = []
    for case in cases:
        try:
            if not case.get("dialect"):
                failures.append({"case": case.get("_file"),
                                 "why": "the case names no dialect: a statement is compiled "
                                        "in one, and a case must pin it"})
                continue
            # Optional, and passed only when the case names it: a case with `formula_language:
            # sql` measures names the model's roles (logical tables), which a compiler reads
            # through the `logical` keyword of `compile_unit`.
            extra = {"logical": case["logical"]} if case.get("logical") else {}
            # A case whose measures read another table's columns names the model's tables
            # (`table_of`: model table -> warehouse table) and, where text or dates matter,
            # each column's type (`types`: [table, column, type] rows).
            if case.get("table_of"):
                extra["table_of"] = case["table_of"]
            if case.get("types"):
                extra["types"] = {(str(t).lower(), str(c).lower()): str(ty)
                                  for t, c, ty in case["types"]}
            got = compile_unit(case["unit"], rules, case["dialect"],
                               columns={k: set(v) for k, v in
                                        (case.get("columns") or {}).items()}, **extra)
        except Exception as exc:                                  # pragma: no cover - defence
            failures.append({"case": case.get("_file"), "why": f"raised {exc!r}"})
            continue
        want_sql = _norm(case.get("expect_sql"))
        if want_sql and _norm(got.sql) != want_sql:
            failures.append({"case": case.get("_file"), "why": "SQL changed",
                             "expected": case.get("expect_sql"), "got": got.sql})
        want_cov = case.get("expect_coverage")
        if want_cov and got.coverage != want_cov:
            failures.append({"case": case.get("_file"), "why": "coverage changed",
                             "expected": want_cov, "got": got.coverage})
    return failures


def _norm(sql: str | None) -> str:
    return re.sub(r"\s+", " ", str(sql or "").strip())


def case_from_unit(unit: dict, result, dialect: str, name: str) -> dict:
    """A regression case built from what the compiler just produced.

    Pinned to the OUTPUT, not to an intention: the next change must reproduce this statement
    or explain itself. And it carries the unit VERBATIM, so promotion of a case derived from a
    customer workbook is a deliberate step, never a side effect of a run.
    """
    return {
        "name": name,
        "dialect": dialect,
        "unit": unit,
        "expect_sql": result.sql,
        "expect_coverage": result.coverage,
        "rules_used": sorted(set(result.rules_used)),
    }
