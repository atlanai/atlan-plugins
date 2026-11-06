# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""PII scrubbing for the query-history lane.

The raw log carries real user names and email / UUID / customer-id literals. Scrubbing is
UNCONDITIONAL: literals matching `pii.redact_patterns` are replaced in place, and no user name
ever reaches a PUBLISH DESTINATION. `run_inputs.json` records the log's PATH, never a byte of
its contents.

USER NAMES, by `pii.redact_user_names`:
  true   (the default) the readers keep a distinct-user COUNT, never a name: no artifact holds
         one.
  false  the history artifacts in the run directory (`HISTORY_ARTIFACTS`) also keep WHO ran
         each statement, which is how an operator finds a metric's owners. Nothing else may
         hold a name, and every way out of the run directory refuses one: each write stage
         (through `publish_target.require`), every output plug (a `sockets.Bundle` that holds
         a name cannot be built), and every observer (`without_user_names` on what a stage
         reports).

OBSERVED LITERALS, by `pii.literal_min_shapes` / `pii.literal_min_users`: a literal the history
captured is kept when it appears in at least that many distinct shapes OR was used by at least
that many distinct people. A value one analyst typed into one query is that analyst's lookup
(an order id, a customer id); a value several shapes or several people use is the estate's
vocabulary.

There is NO LENGTH CAP. Redaction leaves a parseable statement; a cut mid-clause does not, and
a published statement that does not parse is worse than a long one. See `text()` below.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from typing import Any, Iterable

PLACEHOLDER = "<redacted>"

# The only artifacts that may hold user names, and only with `pii.redact_user_names: false`.
HISTORY_ARTIFACTS = ("query_corpus.jsonl", "query_shapes.json")
# Where a name is carried in them; and the keys no outbound object may carry at all.
NAMES_KEY = "user_names"
IDENTITY_KEYS = frozenset({
    NAMES_KEY, "user_name", "username", "users_named", "principal", "executed_by",
    "user_email", "login_name", "session_user", "warehouse_user"})
# Containers whose KEYS are schema, not fields: a BI model's native query maps each result
# column to its source (`native_source.columns`), so a model with a column named `Username`
# carries the key `Username` with no person in it. Inside these the keys are not read as
# identity fields; every value is still checked against the run's known names.
SCHEMA_KEYED = frozenset({"columns"})
# A name shorter than this is not looked for as a value: a two-letter login equal to a
# two-letter code would refuse a publish over a coincidence.
MIN_NAME_CHARS = 3


class Scrubber:
    def __init__(self, policy: Any) -> None:
        self.patterns = [re.compile(p) for p in policy.get("pii.redact_patterns")]
        redact = policy.get("pii.redact_user_names")
        if not isinstance(redact, bool):
            raise SystemExit(f"pii.redact_user_names={redact!r}; expected true or false")
        self.keep_user_names = not redact
        self.min_shapes = _whole(policy, "pii.literal_min_shapes")
        self.min_users = _whole(policy, "pii.literal_min_users")

    def text(self, sql: str) -> str:
        """Redact matching literals in place. Never truncates.

        Redaction replaces a matched literal and leaves a statement that still parses. A
        length cap would cut mid-clause, and a statement cut mid-clause does not parse: on
        the working corpus that silently destroys exactly the longest, most complex queries
        (measured on a real estate: 13% of the corpus truncated, 10.9% then dropped as
        `parse_failed`), and on published text it ships SQL nobody can run. So nothing here
        cuts, for any caller.
        """
        out = sql
        for pat in self.patterns:
            out = pat.sub(PLACEHOLDER, out)
        return out

    def floor_literals(self, by_shape: dict) -> tuple[dict, int, int]:
        """Keep an observed literal seen in >= `min_shapes` distinct shapes OR used by >=
        `min_users` distinct people -> (the kept values, n literals kept, n dropped).

        `by_shape` is H1's capture: {shape key: {column: [{value, n_users, user_names?}]}}.
        Distinct people are the union of the names where the corpus kept them, and otherwise
        the largest per-statement user count — the most that can be proven without names."""
        shapes: dict[str, set] = {}
        names: dict[str, set] = {}
        counted: dict[str, int] = {}
        for key, cols in by_shape.items():
            for vals in cols.values():
                for v in vals:
                    lit = str(v["value"])
                    shapes.setdefault(lit, set()).add(key)
                    names.setdefault(lit, set()).update(v.get(NAMES_KEY) or [])
                    counted[lit] = max(counted.get(lit, 0), int(v.get("n_users") or 0))
        keep = {lit for lit in shapes
                if len(shapes[lit]) >= self.min_shapes
                or max(len(names[lit]), counted[lit]) >= self.min_users}
        out: dict = {}
        for key, cols in by_shape.items():
            for col, vals in cols.items():
                kept = [{k: x for k, x in v.items() if k != NAMES_KEY}
                        for v in vals if str(v["value"]) in keep]
                if kept:
                    out.setdefault(key, {})[col] = kept
        return out, len(keep), len(shapes) - len(keep)


def _whole(policy: Any, key: str) -> int:
    value = policy.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SystemExit(f"{key}={value!r}; expected a whole number >= 1")
    return value


def scan_text(text: str, patterns: list[re.Pattern[str]]) -> Counter:
    """Count PII-pattern hits in a blob. Used by the P7 scanner test, which walks every file
    in a completed run directory including the fixtures."""
    hits: Counter = Counter()
    for pat in patterns:
        n = len(pat.findall(text))
        if n:
            hits[pat.pattern] += n
    return hits


# ── user names never leave the run directory ─────────────────────────────────────────────
def _records(path: pathlib.Path) -> Iterable[Any]:
    try:
        if path.suffix == ".jsonl":
            with path.open() as fh:
                for line in fh:
                    if line.strip():
                        yield json.loads(line)
        else:
            yield json.loads(path.read_text())
    except (OSError, ValueError):
        return


def run_user_names(run_dir: pathlib.Path) -> set[str]:
    """Every user name the run directory's history artifacts hold (none, by default)."""
    out: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == NAMES_KEY and isinstance(v, list):
                    out.update(str(x) for x in v if x)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    for name in HISTORY_ARTIFACTS:
        p = pathlib.Path(run_dir) / name
        if p.is_file():
            for rec in _records(p):
                walk(rec)
    return out


def user_name_hits(obj: Any, names: set[str], where: str = "$") -> list[str]:
    """JSON paths in `obj` that carry a user name: an identity key, or a string value equal
    to a known name. The paths, never the names: the refusal must not leak what it refuses."""
    want = {n.casefold() for n in names if len(n) >= MIN_NAME_CHARS}
    hits: list[str] = []

    def walk(o: Any, at: str, schema_keys: bool = False) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if (not schema_keys and str(k).lower() in IDENTITY_KEYS
                        and v not in (None, [], "")):
                    hits.append(f"{at}.{k}")
                else:
                    walk(v, f"{at}.{k}", str(k).lower() in SCHEMA_KEYED and isinstance(v, dict))
        elif isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, f"{at}[{i}]")
        elif isinstance(o, str) and o.strip().casefold() in want:
            hits.append(at)
    walk(obj, where)
    return hits


def outbound_paths(run_dir: pathlib.Path) -> list[pathlib.Path]:
    """Every artifact a destination could be fed from: each one a stage of this run wrote
    (the manifest's outputs), except the history artifacts that may hold names. An input the
    operator keeps in the directory (a raw log) is not an artifact and is not read."""
    root = pathlib.Path(run_dir)
    try:
        stages = json.loads((root / "manifest.json").read_text()).get("stages") or []
    except (OSError, ValueError):
        return sorted(p for p in root.glob("*.json") if p.name not in HISTORY_ARTIFACTS) \
            if root.is_dir() else []
    out = {pathlib.Path(o) for st in stages for o in (st.get("outputs") or [])}
    return sorted(p if p.is_absolute() else root / p for p in out
                  if pathlib.Path(p).name not in HISTORY_ARTIFACTS
                  and pathlib.Path(p).suffix in (".json", ".jsonl"))


def refuse_user_names(run_dir: pathlib.Path, paths: Iterable[pathlib.Path], what: str) -> None:
    """Refuse `what` when any of `paths` carries a user name. Unconditional: with
    `pii.redact_user_names: true` no name exists to find, and the identity keys are still
    refused."""
    names = run_user_names(run_dir)
    found: list[str] = []
    for p in paths:
        p = pathlib.Path(p)
        if not p.is_file() or p.name in HISTORY_ARTIFACTS:
            continue
        for i, rec in enumerate(_records(p)):
            found += [f"{p.name}:{h}" for h in user_name_hits(
                rec, names, "$" if p.suffix != ".jsonl" else f"line{i + 1}")]
    if found:
        raise SystemExit(
            f"{what}: REFUSED — {len(found)} place(s) outside the history artifacts carry a "
            f"user name, and no user name may leave the run directory: "
            f"{', '.join(found[:5])}. User names may be kept only in "
            f"{' and '.join(HISTORY_ARTIFACTS)}, and only with pii.redact_user_names: false.")


def without_user_names(data: Any) -> Any:
    """`data` as an observer may see it: every name list replaced by its count."""
    if isinstance(data, dict):
        return {k: (len(v) if k == NAMES_KEY and isinstance(v, list) else without_user_names(v))
                for k, v in data.items()}
    if isinstance(data, list):
        return [without_user_names(x) for x in data]
    return data
