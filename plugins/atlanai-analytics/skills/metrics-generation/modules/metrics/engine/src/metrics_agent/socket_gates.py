# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The checks every plug passes through. No plug can opt out of them.

* **Read-only.** `ReadOnlyRunner` wraps any plug's SQL runner; a statement that writes is
  refused before the runner sees it.
* **Custody.** `check_custody` refuses, by name and before any capability is called, a plug
  whose data passes through Atlan's store when the run is standalone.
* **Schema validation.** `validated` checks every record a plug returns against its schema,
  one at a time, so a large history is never held in memory to be checked.
* **Enough input.** `check_enough_input` refuses a run with neither query history nor BI
  definitions.

None of these takes a parameter, flag or policy key that relaxes it, and none ever will:
`tests/test_gates.py` scans for force-flag spellings and `tests/test_socket_gates.py` checks
the signatures.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Iterator, Mapping, Sequence

import jsonschema

from metrics_agent.lib import artifacts, sqlshape
from metrics_agent.sockets import SqlRunner


class GateRefused(RuntimeError):
    """A socket gate refused. The message says which gate, which plug and why."""


class ReadOnlyViolation(GateRefused):
    """A statement that is not a pure read was about to reach a plug."""


class CustodyRefused(GateRefused):
    """A standalone run selected a plug whose data passes through Atlan's custody."""


class NotEnoughInput(GateRefused):
    """No selected plug supplies query history or BI definitions."""


class InvalidRecord(GateRefused):
    """A plug returned a record that does not meet its schema."""


# ── read-only ────────────────────────────────────────────────────────────────────────────
_READ_ONLY = ("statement is not a pure read; DML/DDL is refused before it reaches the "
              "plug's runner. This gate is never weakened.")


# One part of a table name: letters, digits, `_` and `$`, and single hyphens BETWEEN them (a
# BigQuery project id such as `my-project`). Never a quote, a backtick, `;`, whitespace, `--`
# (a comment), `/*` or a control character, so a part that passes cannot break out of a
# catalogue query. Matched whole (`fullmatch`): `$` alone would let a trailing newline in.
_IDENT = re.compile(r"[A-Za-z0-9_$]+(?:-[A-Za-z0-9_$]+)*")

def plain_identifier(part: str) -> bool:
    """True when one part of a table name may be put into a catalogue query."""
    return bool(_IDENT.fullmatch(str(part or "")))


def plain_table_name(table: str) -> bool:
    """True when every part of a dotted table name is a plain identifier. Table names come
    from SQL the estate wrote, so they are data: only a plain one is put into a catalogue
    query the runner builds, and a part with a hyphen is quoted there (`quote_part`)."""
    parts = str(table or "").split(".")
    return 1 <= len(parts) <= 3 and all(plain_identifier(p) for p in parts)


def quote_part(part: str, dialect: str | None) -> str:
    """One plain table-name part, ready to stand as an IDENTIFIER in a catalogue query of
    `dialect`. A part without a hyphen is left bare, so the warehouse's own case rules apply
    as they always did; a hyphenated one (`my-project`) cannot stand bare and is quoted the
    dialect's own way, as the SQL parser spells a quoted identifier in it (backticks, double
    quotes or brackets): the core names no dialect. A part that is not plain is refused, never
    escaped."""
    if not plain_identifier(part):
        raise ReadOnlyViolation(f"table-name part {part!r} is not a plain identifier; it is "
                                "not put into a catalogue query")
    if "-" not in part:
        return part
    from sqlglot import exp  # noqa: PLC0415

    d = (dialect or "").strip().lower() or None
    try:
        return exp.to_identifier(part, quoted=True).sql(dialect=d)
    except Exception:  # noqa: BLE001 - a dialect the parser does not know: ANSI quoting
        return exp.to_identifier(part, quoted=True).sql()


class ReadOnlyRunner:
    """Any `SqlRunner`, with every statement checked by `sqlshape.has_dml` first."""

    def __init__(self, runner: SqlRunner, dialect: str) -> None:
        self._runner = runner
        self.dialect = dialect
        # Which backend this is, for the record ("duckdb", "snowflake", …); never a control.
        self.kind = getattr(runner, "kind", None)

    def _guard(self, sql: str) -> None:
        if sqlshape.has_dml(sql, self.dialect):
            raise ReadOnlyViolation(_READ_ONLY)

    def connect(self) -> None:
        self._runner.connect()

    def explain(self, sql: str) -> int:
        self._guard(sql)
        return self._runner.explain(sql)

    def fetch(self, sql: str, limit: int):
        self._guard(sql)
        return self._runner.fetch(sql, limit)

    def scan(self, sql: str, batch: int):
        self._guard(sql)
        return self._runner.scan(sql, batch)

    def columns_of(self, table: str) -> list[str]:
        if not plain_table_name(table):
            raise ReadOnlyViolation(f"table name {table!r} is not a plain DB.SCHEMA.TABLE "
                                    "identifier; its columns are not looked up")
        return self._runner.columns_of(table)

    def columns_of_many(self, tables: list[str]) -> dict[str, list[str]]:
        # A name that is not a plain identifier is never spliced into a catalogue query; it
        # stays absent evidence, as an unreadable table does.
        return self._runner.columns_of_many([t for t in tables if plain_table_name(t)])

    def close(self) -> None:
        self._runner.close()

    def __enter__(self) -> "ReadOnlyRunner":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ── custody ──────────────────────────────────────────────────────────────────────────────
def third_party(plug: Any) -> bool:
    """Whether a plug's data passes through a third party's systems: any custody that is not
    the customer's own. Fail-safe: an unknown value is a third party's."""
    custody = getattr(plug, "custody", None)
    return custody is not None and custody != "customer"


def check_custody(mode: str, plugs: Iterable[Any]) -> None:
    """Refuse, in a standalone run, every plug whose data passes through a third party.

    `plugs` are plug classes or instances (anything with `name` and, for input plugs,
    `custody`). Output plugs and observers carry no custody: publishing somewhere does not
    make a run connected, because the mode describes where inputs come from.
    """
    from metrics_agent.sockets import MODES  # noqa: PLC0415

    if mode not in MODES:
        raise GateRefused(f"unknown run mode {mode!r}; expected {' or '.join(MODES)}")
    if mode != "standalone":
        return
    held = [p for p in plugs if third_party(p)]
    refused = sorted({p.name for p in held})
    if refused:
        holders = sorted({getattr(p, "custodian", None) or "a third party" for p in held})
        raise CustodyRefused(
            "REFUSED: a standalone run may not use " + ", ".join(refused) + ".\n"
            "  A standalone agent reads only the customer's own systems, and the data these "
            f"plugs read is held by {' and '.join(holders)}.\n"
            "  This is not a tunable. No policy value, override or advanced switch relaxes "
            "it.\n"
            "  Use plugs that read on the customer's own grant (for example the warehouse "
            "directly, or files\n  the customer exported), or run connected.")


# ── enough input ─────────────────────────────────────────────────────────────────────────
def check_enough_input(selected: Mapping[str, Sequence[str]]) -> None:
    """`selected` maps a capability (`history`, `bi`, …) to the plug names chosen for it."""
    if not (selected.get("history") or selected.get("bi")):
        raise NotEnoughInput(
            "nothing to build from: no plug supplies query history or BI definitions. "
            "Select at least one of them.")


# ── schema validation ────────────────────────────────────────────────────────────────────
def validated(records: Iterable[Any], schema_name: str, *, plug: str = "?") -> Iterator[Any]:
    """Yield each record after checking it against `data/schemas/<schema_name>`.

    Dataclass records are checked through their `to_dict()`. Stream-safe: records are checked
    and passed on one at a time, never collected first.
    """
    validator = jsonschema.Draft202012Validator(artifacts.schema(schema_name))
    for i, rec in enumerate(records):
        doc = rec.to_dict() if hasattr(rec, "to_dict") else rec
        err = next(iter(sorted(validator.iter_errors(doc), key=lambda e: list(e.path))), None)
        if err is not None:
            where = "/".join(str(p) for p in err.path) or "(root)"
            raise InvalidRecord(
                f"plug {plug!r} returned record {i} that does not meet {schema_name} at "
                f"{where}: {err.message}. Record keys: {json.dumps(sorted(doc))}")
        yield rec
