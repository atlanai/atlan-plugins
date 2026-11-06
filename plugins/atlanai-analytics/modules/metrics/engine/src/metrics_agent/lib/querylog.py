# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The common query-log format, and a file's custody sidecar.

A warehouse's shape table (one row per distinct statement, with its counts) is read here too.

A query log handed over as a file is JSON Lines (optionally gzipped), or one `.json` document,
of query records (`query_record.schema.json`). That is a COMMON format: every plug that hands
the core a log file, and the core reading one a run recorded, read it here, the same way. A
file's custody rides beside it in `<file>.manifest.json`, which is the core's safety contract,
not any plug's.
"""
from __future__ import annotations

import gzip
import json
import pathlib
from typing import Iterator

from metrics_agent import sockets as S


def stamped_custody(stamp: pathlib.Path) -> str | None:
    """The custody a sidecar or a fetch manifest records, or None when it records none.
    FAIL-SAFE: any value that is not the customer's own is a third party's, so a stamp written
    by an older version, or by a plug that names its custodian, can never read as customer."""
    try:
        doc = json.loads(stamp.read_text())
    except (OSError, ValueError):
        return None
    custody = doc.get("custody") if isinstance(doc, dict) else None
    if custody in (None, ""):
        return None
    return S.CUSTOMER if custody == S.CUSTOMER else S.THIRD_PARTY


def file_custody(path: pathlib.Path) -> str:
    """`third-party` when the file's sidecar (`<file>.manifest.json`) says the data passed
    through a third party's systems; the customer's otherwise."""
    side = path.with_name(path.name + ".manifest.json")
    if side.is_file() and stamped_custody(side) == S.THIRD_PARTY:
        return S.THIRD_PARTY
    return S.CUSTOMER


_LIST_KEYS = ("queries", "statements", "items", "rows")


def _json_document_rows(path: pathlib.Path) -> list | None:
    """The rows of a `.json` file holding ONE document: a list of statements (plain SQL
    strings) or of row objects, or an object wrapping such a list. None when it is not one."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict):
        doc = next((doc[k] for k in _LIST_KEYS if isinstance(doc.get(k), list)), None)
    return doc if isinstance(doc, list) else None


def read_log(path: pathlib.Path) -> Iterator[S.QueryRecord]:
    """Every usable row of one log file. A line that is not JSON, or that carries no
    statement, is skipped, exactly as H0 always skipped it.

    A `.json` file may instead hold one document: a list of SQL statements (each read as one
    execution by an unknown user), a list of row objects, or an object wrapping either under
    `queries`, `statements`, `items` or `rows`. That is how statements are most often handed
    over, and it needs no conversion first."""
    if path.suffix.lower() == ".json":
        rows = _json_document_rows(path)
        if rows is not None:
            for item in rows:
                row = {"query_text": item} if isinstance(item, str) else item
                try:
                    yield S.QueryRecord.from_row(row)
                except (ValueError, TypeError, AttributeError):
                    continue
            return
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            try:
                yield S.QueryRecord.from_row(row)
            except (ValueError, TypeError, AttributeError):
                continue


def shape_table_records(runner: S.SqlRunner, dialect: str, table: str,
                        columns: str | None = None, batch: int = 5000) -> Iterator[S.QueryRecord]:
    """A warehouse shape table (one row per distinct statement, with its counts) as records,
    read through the read-only runner. Shared by every plug that reads one."""
    from metrics_agent.socket_gates import (ReadOnlyRunner, plain_identifier,  # noqa: PLC0415
                                            plain_table_name, quote_part)

    # A comma-separated string, or the list run.yml holds when the interview wrote it.
    parts = columns if isinstance(columns, (list, tuple)) else str(columns or "").split(",")
    cols = [str(c).strip() for c in parts if str(c).strip()] or ["sql", "exec", "users"]
    if len(cols) not in (3, 4):
        raise ValueError("`columns` takes sql,exec,users[,query_id]")
    # INTERPOLATED INTO A SELECT, so refused here, before any query, unless plain: a name
    # carrying a quote, a space or a semicolon is a SQL break-out rather than a typo.
    if not plain_table_name(table):
        raise ValueError(f"shape table {table!r} is not a plain [DB.]SCHEMA.TABLE identifier")
    bad = [c for c in cols if not plain_identifier(c)]
    if bad:
        raise ValueError(f"shape table columns {bad} are not plain identifiers")
    with ReadOnlyRunner(runner, dialect) as ro:
        # Each part as the dialect writes an identifier (a hyphenated project is quoted).
        name = ".".join(quote_part(part, dialect) for part in str(table).split("."))
        for rows in ro.scan(f"SELECT {', '.join(cols)} FROM {name}", batch):
            for r in rows:
                yield S.QueryRecord(
                    query_text=r[0], exec_count=max(int(r[1] or 0), 0),
                    user_count=max(int(r[2] or 0), 0),
                    query_id=(str(r[3]) if len(cols) == 4 and r[3] not in (None, "")
                              else None))
