"""Candidate queries from files, for when the warehouse is out of reach.

The warehouse is the better source — it carries run counts, distinct users, and
who issued each query, none of which a file has. But requiring it makes the
whole workflow unavailable to anyone without `ACCOUNT_USAGE`, a Snowflake role,
or a browser to complete SSO in. That was not a hypothetical: a session stalled
here, having been told by the doctor that `--from-files` existed when it did not.

What a file source is actually good for:

  - a use-case where someone can hand you the SQL behind a dashboard
  - a warehouse you can read but not mine (no query-history grant)
  - reproducing a build somewhere the original warehouse is not reachable

Provenance is recorded honestly: `run_count` and `distinct_users` are 1, because
a file says nothing about how often anyone asked. Selection still ranks on
discrimination, which is the signal that matters most, but a file-sourced set
will rank flatter than a mined one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from eval_builder.select import Candidate

# `.sql` holds statements; `.json` may hold a list of objects with a sql field.
SQL_SUFFIXES = (".sql", ".txt")
JSON_SUFFIXES = (".json", ".jsonl")

# Keys a JSON record might use for its statement, in preference order.
_SQL_KEYS = ("sql", "query", "golden_sql", "canonical_sql", "statement", "query_text")
_ID_KEYS = ("id", "qid", "name", "new_id", "query_id")
# An MCP export of query_history carries these; a hand-written .sql file does
# not. Where they exist the service-account gate and the verifiability score
# work exactly as they do on a warehouse mine.
_USER_KEYS = ("user_name", "user", "issued_by")
_ROWS_KEYS = ("rows_produced", "rows", "row_count")
# A normalised query log (`querylog.jsonl`, {sql, exec, users}) keeps demand as counts.
_RUNS_KEYS = ("exec", "run_count", "executions")
_DISTINCT_USERS_KEYS = ("users", "distinct_users")


@dataclass
class MinedFiles:
    candidates: list[Candidate]
    source: str = "files"
    note: str = ""


def _split_statements(text: str) -> list[str]:
    """Split a .sql file into statements.

    Semicolons inside string literals and line comments do not end a statement,
    so a naive split on ';' cuts real queries in half.
    """
    out, buf, in_str, quote = [], [], False, ""
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == quote:
                # doubled quote is an escape, not a terminator
                if i + 1 < len(text) and text[i + 1] == quote:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_str, quote = True, ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and text[i:i + 2] == "--":
            nl = text.find("\n", i)
            nl = len(text) if nl == -1 else nl
            buf.append(text[i:nl])
            i = nl
            continue
        if ch == "/" and text[i:i + 2] == "/*":
            end = text.find("*/", i)
            end = len(text) if end == -1 else end + 2
            buf.append(text[i:end])
            i = end
            continue
        if ch == ";":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _from_json(path: Path) -> list[tuple[str, str, dict]]:
    """(id, sql, extras) from a JSON or JSONL file of records."""
    text = path.read_text()
    if path.suffix == ".jsonl":
        rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    else:
        data = json.loads(text)
        rows = data if isinstance(data, list) else [data]

    pairs: list[tuple[str, str, dict]] = []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        sql = next((row[k] for k in _SQL_KEYS if isinstance(row.get(k), str)), None)
        if not sql or not sql.strip():
            continue
        rid = next((str(row[k]) for k in _ID_KEYS if row.get(k)), f"{path.stem}-{n:03d}")
        extras = {
            "user_name": next((row[k] for k in _USER_KEYS if row.get(k)), None),
            "rows_produced": next((row[k] for k in _ROWS_KEYS
                                   if isinstance(row.get(k), int)), None),
            "run_count": next((row[k] for k in _RUNS_KEYS if isinstance(row.get(k), int)), None),
            "distinct_users": next((row[k] for k in _DISTINCT_USERS_KEYS
                                    if isinstance(row.get(k), int)), None),
            # the warehouse the row ran on, as its exporter recorded it
            "dialect": next((row[k] for k in ("dialect", "sql_dialect", "connector", "connector_name")
                             if isinstance(row.get(k), str) and row[k].strip()), None),
            # A BI export's element context: filter inference groups by page,
            # and pruning leaves a table element's rows alone.
            "page": row.get("page") if isinstance(row.get("page"), str) else None,
            "element_type": (row.get("element_type")
                             if isinstance(row.get("element_type"), str) else None),
        }
        pairs.append((rid, sql.strip(), extras))
    return pairs


def read_queries(root: Path) -> list[tuple[str, str, str, dict]]:
    """(id, sql, origin, extras) for every statement found under `root`."""
    found: list[tuple[str, str, str, dict]] = []
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    for p in paths:
        suffix = p.suffix.lower()
        try:
            if suffix in JSON_SUFFIXES:
                for rid, sql, extras in _from_json(p):
                    found.append((rid, sql, p.name, extras))
            elif suffix in SQL_SUFFIXES:
                statements = _split_statements(p.read_text())
                for n, sql in enumerate(statements, 1):
                    rid = p.stem if len(statements) == 1 else f"{p.stem}-{n:02d}"
                    found.append((rid, sql, p.name, {}))
        except Exception as e:  # noqa: BLE001 - one unreadable file is not fatal
            found.append((p.stem, f"-- unreadable: {e}", p.name, {}))
    return found


def mine_files(root: Path) -> MinedFiles:
    """Read every query under a directory as a candidate."""
    if not root.exists():
        raise FileNotFoundError(f"no such directory: {root}")

    rows = read_queries(root)
    candidates = [
        Candidate(
            query_id=rid,
            sql=sql,
            run_count=extras.get("run_count") or 1,          # most files record no demand
            distinct_users=extras.get("distinct_users") or 1,
            user_name=extras.get("user_name"),
            rows_returned=extras.get("rows_produced"),
            client_app=None,
            page=extras.get("page"),
            element_type=extras.get("element_type"),
            dialect=_dialect(extras.get("dialect")),
        )
        for rid, sql, _origin, extras in rows
        if not sql.startswith("-- unreadable")
    ]
    unreadable = [o for _, s, o, _ in rows if s.startswith("-- unreadable")]
    with_user = sum(1 for c in candidates if c.user_name)
    counted = sum(1 for _, _, _, extras in rows if extras.get("run_count"))
    note = (
        f"{len(candidates)} statement(s) from {len({o for _, _, o, _ in rows})} file(s); "
        + (f"{counted} carry run counts from a query log" if counted else
           "run counts are unavailable from files so representativeness is flat")
    )
    if with_user:
        note += f"; {with_user} carry an issuing user, so the service-account gate applies"
    if unreadable:
        note += f"; {len(unreadable)} file(s) unreadable"
    return MinedFiles(candidates, note=note)


_DDL_TABLE = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:transient\s+|temporary\s+)?table\s+"
    r"(?:if\s+not\s+exists\s+)?([\w.\"]+)\s*\((.*?)\)\s*;",
    re.I | re.S,
)
_DDL_COL = re.compile(r"^\s*\"?(\w+)\"?\s+([A-Za-z][\w()., ]*)")


def read_schema(root: Path) -> dict[str, list[dict]]:
    """Schema from DDL or a JSON dump, so questions can use real column names.

    Accepts `schema.json` in the same shape the warehouse miner produces, the
    Analytics plugin's `database-schema.json` (`columns` of schema, table, column,
    type), or any `.sql` file containing CREATE TABLE statements.
    """
    schema: dict[str, list[dict]] = {}
    if not root.exists():
        return schema

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "database-schema.json":
            try:
                loaded = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
            for c in (loaded.get("columns") or []) if isinstance(loaded, dict) else []:
                if isinstance(c, dict) and c.get("table") and c.get("column"):
                    name = ".".join(x for x in (c.get("catalog"), c.get("schema"), c["table"]) if x)
                    schema.setdefault(name.upper(), []).append(
                        {"column": c["column"], "type": c.get("type"), "comment": None})
        elif p.name == "schema.json":
            try:
                loaded = json.loads(p.read_text())
                if isinstance(loaded, dict):
                    schema.update(loaded)
            except Exception:  # noqa: BLE001
                pass
        elif p.suffix.lower() == ".sql":
            try:
                text = p.read_text()
            except Exception:  # noqa: BLE001
                continue
            for table, body in _DDL_TABLE.findall(text):
                cols = []
                for line in body.split(","):
                    m = _DDL_COL.match(line)
                    if m:
                        cols.append({"column": m.group(1),
                                     "type": m.group(2).strip(),
                                     "comment": None})
                if cols:
                    schema[table.replace('"', "").upper()] = cols
    return schema


def _dialect(name: str | None) -> str | None:
    from eval_builder import dialect as D

    if name and D.normalize(name) is None:
        raise ValueError(f"unknown dialect {name!r} on a row; known: "
                         f"{', '.join(sorted(set(D.CONNECTORS.values())))}")
    return D.normalize(name)
