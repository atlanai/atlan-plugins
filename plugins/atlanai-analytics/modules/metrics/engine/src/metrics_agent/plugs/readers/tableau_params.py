# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Tableau parameters, user functions and calcs for the BI compiler.

A Tableau calc names other calcs and `[Parameters].[X]` by name. A3 compiles the DEFAULT view
(every parameter at its declared default, trap 4). `overrides` moves a parameter to another
value of its declared domain, so any other branch of the same widget is compiled by the same
rules as the default one. A user function of the viewer's domain or groups (USERDOMAIN(),
ISMEMBEROF("g")) is what it returns for the run's viewer (`bi_facts.viewer`); the viewer's
name and full name (USERNAME(), FULLNAME()) are bind placeholders, never values.
"""
from __future__ import annotations

import re

PARAM_RE = re.compile(r"\[Parameters\]\.\[([^\[\]]+)\]")
_REF_RE = re.compile(r"\[([^\[\]]+)\]")
# The user functions: what they return depends on who views the workbook.
_STRING = r"(?:\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*')"
# A string (kept as it is: a user function inside a string is text), or a user function.
_USER_FN = re.compile(r"(?i)(?P<str>" + _STRING + r")|\b(?P<fn>USERNAME|FULLNAME|USERDOMAIN)"
                      r"\s*\(\s*\)|\bISMEMBEROF\s*\(\s*(?P<group>" + _STRING + r")\s*\)")


def _uses_user_fn(formula: str) -> bool:
    return any((m.group("fn") or "").upper() == "USERDOMAIN" or m.group("group")
               for m in _USER_FN.finditer(formula))


def _quoted(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def viewer_fix(formula: str | None, viewer: dict | None) -> str | None:
    """A formula with each user function replaced by what it returns for `viewer` (the
    run's identity): USERDOMAIN() its domain, ISMEMBEROF("g") whether it is in group g.
    USERNAME() and FULLNAME() are left as they are: a person's name is never written into an
    artifact (guardrail 8), so the compiler reads them as a bind placeholder
    (`tableau.username`, `tableau.fullname`) whatever the viewer. A domain or group test with
    no viewer is left as it is and refused by name where it is compiled."""
    if not formula or not viewer or not _uses_user_fn(formula):
        return formula
    # Groups unread (absent, not empty): a group test is left as it is, refused by name.
    groups = ({str(g).casefold() for g in viewer["groups"]} if "groups" in viewer else None)

    def one(m: re.Match) -> str:
        if m.group("str"):
            return m.group("str")
        fn = (m.group("fn") or "ISMEMBEROF").upper()
        if fn in ("USERNAME", "FULLNAME"):
            return m.group(0)
        if fn == "USERDOMAIN":
            return _quoted(str(viewer.get("domain") or "")) if "domain" in viewer \
                else m.group(0)
        if groups is None:
            return m.group(0)
        q = m.group("group")
        group = q[1:-1].replace(q[0] * 2, q[0])
        return "TRUE" if group.casefold() in groups else "FALSE"
    return _USER_FN.sub(one, formula)


class Params:
    """Declared parameters of a workbook: name -> default literal, plus each list domain."""

    def __init__(self, facts: dict, overrides: dict[str, str] | None = None) -> None:
        self.default: dict[str, str] = {}
        self.domain: dict[str, list[dict]] = {}
        for usage in facts.get("usage") or []:
            for p in usage.get("parameters") or []:
                if p.get("default") is not None:
                    self.default.setdefault(p["name"], str(p["default"]))
                if p.get("domain"):
                    self.domain.setdefault(p["name"], list(p["domain"]))
        self.value = {**self.default, **(overrides or {})}
        self.viewer = facts.get("viewer") or None
        self.calcs = {c["name"].lower(): c["formula"] for t in facts.get("tables") or []
                      for c in t.get("columns") or []
                      if c.get("formula") and c.get("formula_language") == "tableau-calc"}
        self.calcs.update({m["name"].lower(): m["formula"] for m in facts.get("measures") or []
                           if m.get("formula") and m.get("formula_language") == "tableau-calc"})

    def fix(self, formula: str | None) -> str | None:
        """Substitute every `[Parameters].[X]` with its current value."""
        if not formula:
            return formula
        return viewer_fix(PARAM_RE.sub(lambda m: self.value.get(m.group(1), m.group(0)),
                                       formula), self.viewer)

    def viewer_used(self, formulas: list[str]) -> bool:
        """Whether any of these formulas (or a calc they inline) calls a user function."""
        seen, todo = set(), list(formulas)
        while todo:
            f = todo.pop() or ""
            if _uses_user_fn(f):
                return True
            for ref in _REF_RE.findall(f):
                k = ref.lower()
                if k in self.calcs and k not in seen:
                    seen.add(k)
                    todo.append(self.calcs[k])
        return False

    def inline_index(self) -> dict[str, str]:
        """Calc name (lower) -> formula with parameters substituted, for the inliner."""
        return {k: self.fix(f) for k, f in self.calcs.items()}

    def used(self, formulas: list[str]) -> list[str]:
        """Parameters reachable from these formulas, through every calc they inline."""
        seen, out, todo = set(), set(), list(formulas)
        while todo:
            f = todo.pop() or ""
            out |= {n for n in PARAM_RE.findall(f) if n in self.default}
            for ref in _REF_RE.findall(f):
                k = ref.lower()
                if k in self.calcs and k not in seen:
                    seen.add(k)
                    todo.append(self.calcs[k])
        return sorted(out)
