# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Databricks metric view reader -> the `bi_facts` contract, declarations included.

PROVENANCE. Written for this repo; not ported. The precedent it follows is the Snowflake
package's `semantic_view.py`: a governed model with no report layer, read into the same
contract every other BI reader writes, so nothing downstream branches on vendor. Design:
`docs/design/declared-models-contract.md`.

FOUR INPUT SHAPES, ONE READER. All carry the same YAML definition:

  * **describe**: the document `DESCRIBE TABLE EXTENDED <view> AS JSON` returns (`view_text`
    is the YAML), or the one Unity Catalog's table API returns (`view_definition`). Its
    `columns` carry each field's and measure's type.
  * **extended**: a description with what the YAML leaves out beside it,
    `{"metric_view": <describe>, "source_columns": {table: [column]}, "primary_keys":
    {table: [column]}, "workspace_host": "<host>"}`: the columns of the tables the view reads
    (a join written without table prefixes cannot be read without them), each table's key,
    and the workspace the view lives in.
  * **yaml**: the definition alone (`version`, `source`, `fields` or `dimensions`,
    `measures`), as it is kept in a repository.
  * **ddl**: `CREATE VIEW ... WITH METRICS LANGUAGE YAML AS $$ ... $$`.

The shape is detected from the content and never guessed. A file is UTF-8 text of at most
1 MiB. A folder is read as every file in it that is a metric view with a measure (`models`):
any other file there is left out, never an error of the run, and a hidden folder or a symbolic
link is not read at all.

**ONE SOURCE, NAMED `source`.** The view's source table is the role `source`, as the view
itself calls it. Each join is a role under its own name; a nested join is named by its path
(`customer.nation` is the role `customer__nation`). A many-to-one join is one declared
relationship on every key column pair it names; a one-to-many join is carried as one and never
walked, so a measure that needs it is refused by name.

**A NAME IS A NAME, OR IT IS NOT READ.** A table is `catalog.schema.table`, each part a plain
identifier, in backticks or not. A `using` column and a key column are one plain identifier.
Nothing else is taken as a name, because a name is written into SQL as it stands: a query, a
name with anything after it, or a name that needs its quoting refuses the view (its source)
or the join (its source, its `using`). The definition cannot tell a table from another metric
view, so a source that is a metric view is read as a table; the run's live check is what
finds that out.

**EVERY COLUMN REFERENCE IS QUALIFIED HERE.** A metric view writes a source column bare
(`SUM(o_totalprice)`); a statement with joins cannot. Each expression is parsed in the
Databricks dialect and every column is bound to its role, so `lib/declared` places it like any
other SQL measure. In a field or a measure, a bare name that an EARLIER field defines is that
field; any other bare name is a column of the source. In the view's filter a bare name is
always a column of the source, never a field. Both are what Databricks does, observed on a
workspace on 2026-11-26: with a field `status` defined as `LOWER(status)`, the filter
`status = 'f'` compared the column and a measure's `status = 'f'` compared the field.

**`MEASURE(x)` IS REPLACED HERE, AND A MEASURE IS NOT A NAME.** A metric view names another
measure only as `MEASURE(x)`; a bare `x` is always a column or a field. So this reader puts
the referenced measure's expression in place of each `MEASURE(x)` and marks every measure
`referable: false`: `lib/declared` then never reads `source.revenue` as the measure `revenue`,
which it would otherwise (`revenue: SUM(revenue)` is a common spelling).

**THE VIEW'S FILTER IS PART OF EVERY MEASURE.** A top-level `filter` applies to every query of
the view. The declaration has no field for a model-wide predicate, so it is written into each
measure: every aggregate gains `FILTER (WHERE <filter>)`, after `WITHIN GROUP (...)` where
there is one. The number is the view's. A measure the filter cannot be shown to reach is not
read, and a note says so: one that calls an unknown function, or reads a column outside every
aggregate this reader recognises (`ANY(flag)`). A filter that cannot be read leaves no measure
read. A field that redefines a column the filter reads cannot share the model with that
column, so it is not listed as a dimension, and a measure that reads it carries its expression.

**A FIXED DATE IS A PARAMETER ON THE CARD.** Atlan keeps no fixed date in a metric's formula,
and one metric that holds one stops the export of every metric in the run. A view's filter
often holds one (`order_date >= DATE'2021-01-01'`). The measure's own formula keeps the date,
so the statement compiled from it runs. The declaration writes it as `{{name}}` and lists the
parameter with the view's date as its default, so the card's number is the view's until a
reader moves the date. A date compared with one column is named for the column and the side
it bounds (`order_date_from`); two dates that would share a name each carry their date too,
and any other is named for the date (`date_2021_01_01`). Two measures that differ only in a
date so declare one formula with two defaults: downstream they are one metric with a date a
reader may move, and the card gate refuses them as two cards. A text value that starts like a
date and is not a plain date or timestamp cannot be lifted out, and that measure is not read.

**WHAT THE SQL WRITER WOULD CHANGE IS NOT READ.** Every expression is written back by the
parser's SQL writer, and the writer renders some forms as other SQL. An expression is read
only when what is written reads back as the same expression and calls no function with a
number of arguments the text did not give it (`_faithful`). That refuses a name in backticks
where a date part belongs (written out bare, as SQL), `TO_DATE` with a format (the format is
dropped), `UNIX_DATE` (rewritten around a date of the writer's own) and two-argument
`DATEDIFF` (written as the three-argument form, which counts whole days). A path inside a
value (`column:key`), `IDENTIFIER()`, a measure with a field's name and a name used twice are
not read either, each by name.

**WHAT IS NOT READ, EACH SAID BY NAME** in `_reader_notes`, never dropped silently: a window
measure and every measure built on one (its frame is applied when the view is queried), an
expression that names a `parameters` entry or holds a parameter marker (`:name`, `?`), a
wildcard field, a column that needs quoting, an expression that is not one expression or that
holds a query or a lambda, a string literal with a quote or a backslash in it (`lib/declared`
reads a literal to the next quote), an ordered aggregate the SQL writer would change
(`PERCENTILE_CONT ... WITHIN GROUP`, `ARRAY_AGG(x ORDER BY y)`), a field or measure longer
than 20,000 characters once what it reads is written out, a join whose source is not a table
name, and a join condition that is not equalities between the two tables. A view whose own
source is not a table name is refused whole.

A metric view declares no key. The extended shape carries its source table's; otherwise the
grain is the card author's to state.

THE VIEW IS THE MODEL'S ONE ASSET, named catalog.schema.view. No definition says which
workspace it was read from, so its address in Catalog Explorer is given only when the extended
shape names the host (`workspace_host`) and the view has a three-part name: a link is never
guessed from a name alone.

Deterministic: the same file always yields the same facts, in the file's own order.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from collections import Counter
from typing import Any

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.tokens import TokenType

from metrics_agent import sockets as S
from metrics_agent.lib import declared as _declared
from metrics_agent.plugs.readers.bicompile import FormulaCompiler

VENDOR = "databricks-metric-view"
DIALECT = "databricks"
SUFFIXES = (".json", ".yaml", ".yml", ".sql")
SOURCE_ROLE = "source"
VERSIONS = ("0.1", "1.1")
MAX_BYTES = 1 << 20          # one definition file: 1 MiB, far above any real view's
MAX_EXPANDED = 20_000        # characters of one field or measure, written out in full
MAX_VIEW_EXPANDED = 1_000_000  # characters of every measure of one view, written out in full
MAX_PAIRS = 100_000          # fields times measures: each measure is offered every field
FILTER = "the view's filter"

_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
# One part of a name as it may be written: plain, or a plain name inside backticks.
_NAME = r"(?:[A-Za-z_][A-Za-z0-9_$]*|`[A-Za-z_][A-Za-z0-9_$]*`)"
_COLUMN = re.compile(_NAME)
_TABLE = re.compile(rf"{_NAME}(?:\.{_NAME}){{0,2}}")
# A DDL file is read in one pass, never searched: what may come before the statement, the
# statement's head (matched where the text starts), and the first `WITH METRICS` after it.
_LEAD = re.compile(r"(?s)\s*(?:--[^\n]*|/\*.*?\*/)?")
_DDL_HEAD = re.compile(r"(?i)CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                       r"(?P<name>(?:`[^`\n]+`|[\w$-]+)(?:\.(?:`[^`\n]+`|[\w$-]+))*)")
_WITH_METRICS = re.compile(r"(?i)\bWITH\s+METRICS\b")
_STRINGS = frozenset({TokenType.STRING, TokenType.RAW_STRING, TokenType.NATIONAL_STRING,
                      TokenType.BYTE_STRING, TokenType.HEX_STRING, TokenType.BIT_STRING,
                      TokenType.UNICODE_STRING, TokenType.HEREDOC_STRING})
# What one field, measure or filter may be: a value, not a statement and not `x AS y`.
_VALUE = (exp.Condition, exp.Filter, exp.WithinGroup, exp.IgnoreNulls, exp.RespectNulls,
          exp.Interval, exp.Star)
_CARD = {"many_to_one": "M:1", "one_to_many": "1:M"}
# A workspace's host, as the connector's own sign-in accepts it: no scheme, path or credentials.
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*\.(?:cloud\.databricks\.com|azuredatabricks\.net"
                   r"|gcp\.databricks\.com)")


class MetricViewError(S.ReaderRefused):
    """A file this reader cannot read as a metric view, said in words an operator can act on
    (`sockets.ReaderRefused`)."""


class _Skip(Exception):
    """One field, measure or join this reader does not read; the message is the note."""


# ── loading ──────────────────────────────────────────────────────────────────────────────

def _yaml(text: str, where: str) -> dict:
    try:
        doc = yaml.safe_load(text)
    except RecursionError:
        raise MetricViewError(f"{where} is nested too deeply to read") from None
    except (yaml.YAMLError, ValueError, OverflowError) as exc:
        raise MetricViewError(f"{where} is not readable YAML: {type(exc).__name__}") from None
    if not isinstance(doc, dict):
        raise MetricViewError(f"{where} is not a YAML mapping")
    return doc


def _text(src: pathlib.Path) -> str:
    """The file's text. UTF-8, and a byte order mark is not content. No more than
    `MAX_BYTES` is ever read."""
    if src.is_dir():
        raise MetricViewError(f"{src} is a folder that holds no metric view this reader reads "
                              f"with a measure ({', '.join(SUFFIXES)}): name one file to be "
                              "told why it is not read")
    try:
        if not src.is_file():
            raise MetricViewError(f"{src} is not a file")
        with src.open("rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError as exc:
        raise MetricViewError(f"{src} is not readable: {type(exc).__name__}") from None
    if len(data) > MAX_BYTES:
        raise MetricViewError(f"{src} is larger than 1 MiB, which no metric view definition "
                              "is; it is not read")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MetricViewError(f"{src} is not UTF-8 text") from None


def _ddl(text: str, src: pathlib.Path) -> tuple[str, str] | None:
    """(the view's name, its YAML) when `text` is a `CREATE VIEW ... WITH METRICS` statement,
    else None. The statement starts the text, after blank space and comments, and its YAML
    lies between the first `$$` and the next. One statement to a file."""
    at = 0
    while True:
        end = _LEAD.match(text, at).end()
        if end == at:
            break
        at = end
    head = _DDL_HEAD.match(text, at)
    metrics = _WITH_METRICS.search(text, head.end()) if head else None
    if not metrics:
        return None
    start = text.find("$$", metrics.end())
    stop = text.find("$$", start + 2) if start >= 0 else -1
    if stop < 0:
        raise MetricViewError(f"{src}: the `CREATE VIEW ... WITH METRICS` statement has no "
                              "definition between `$$` and `$$`")
    if text[stop + 2:].strip(" \t\r\n;"):
        raise MetricViewError(f"{src} holds text after the view's definition: this reader "
                              "reads one `CREATE VIEW ... WITH METRICS` statement to a file, "
                              "and `$$` cannot appear inside the definition")
    return head.group("name").replace("`", ""), text[start + 2:stop]


def _by_table(raw: Any, what: str, notes: list[str]) -> dict[str, list[str]]:
    """{table (lower): [column]} from `source_columns` or `primary_keys`: a mapping of each
    table to a list of column names. One name alone is a list of one. Anything else is left
    out, and a note says so."""
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        notes.append(f"`{what}` is not a mapping of table to columns; it is ignored")
        return {}
    out: dict[str, list[str]] = {}
    for table, cols in raw.items():
        cols = [cols] if isinstance(cols, (str, dict)) else cols
        names = ([c.get("name") if isinstance(c, dict) else c for c in cols]
                 if isinstance(cols, list) else None)
        if names is None or not all(isinstance(n, str) and n.strip() for n in names):
            notes.append(f"`{what}` for {str(table)[:120]!r} is not a list of column names; "
                         "it is ignored")
            continue
        out[str(table).strip().lower()] = [n.strip() for n in names]
    return out


def load(path: pathlib.Path) -> tuple[dict, dict, str]:
    """(the view's YAML definition, what came with it, the input shape). What came with it:
    `name`, `columns` (the view's own, typed), `source_columns` and `primary_keys` (by table),
    each present only when the input carries it in the shape this reader reads, and `notes`
    naming what it carried in another shape."""
    src = pathlib.Path(path)
    text = _text(src)
    notes: list[str] = []
    if src.suffix.lower() == ".json":
        try:
            doc = json.loads(text)
        except RecursionError:
            raise MetricViewError(f"{src} is nested too deeply to read") from None
        except ValueError:
            raise MetricViewError(f"{src} is not readable JSON") from None
        if not isinstance(doc, dict):
            raise MetricViewError(f"{src} is not a JSON object")
        shape = "extended" if isinstance(doc.get("metric_view"), dict) else "describe"
        desc = doc["metric_view"] if shape == "extended" else doc
        text = desc.get("view_text") or desc.get("view_definition")
        if not isinstance(text, str):
            raise MetricViewError(f"{src}: not a metric view description: expected the JSON of "
                                  "`DESCRIBE TABLE EXTENDED <view> AS JSON`, with `view_text`")
        parts = [p.strip() if isinstance(p, str) and p.strip() else None
                 for p in (desc.get("catalog_name"), desc.get("schema_name"),
                           desc.get("table_name") or desc.get("name"))]
        table_id = desc.get("table_id")
        extra = {"name": ".".join(p for p in parts if p) or src.stem,
                 "columns": desc.get("columns"),
                 "source_columns": _by_table(doc.get("source_columns"), "source_columns", notes),
                 "primary_keys": _by_table(doc.get("primary_keys"), "primary_keys", notes),
                 "three_part": parts if all(parts) else None,
                 "table_id": table_id if type(table_id) in (str, int) else None,
                 "workspace_host": doc.get("workspace_host") if shape == "extended" else None,
                 "notes": notes}
        return _yaml(text, f"{src}: the view's YAML"), extra, shape
    ddl = _ddl(text, src)
    if ddl:
        return _yaml(ddl[1], f"{src}: the view's YAML"), {"name": ddl[0]}, "ddl"
    return _yaml(text, str(src)), {"name": src.stem}, "yaml"


def asset(extra: dict, name: str) -> tuple[dict, str | None]:
    """(the view as the model's one asset, a note when a stated host was not used). The
    address is the view's page in Catalog Explorer, built only from a host the input states
    and a catalog.schema.view name whose parts are plain identifiers."""
    parts = extra.get("three_part")
    plain = bool(parts) and all(_PLAIN.fullmatch(p) for p in parts)
    host = extra.get("workspace_host")
    out: dict[str, Any] = {"role": "semantic_model", "name": name,
                           "vendor_id": str(extra["table_id"]) if extra.get("table_id") else None,
                           "path": "/".join(parts) if plain else None, "site": None}
    if host is None:
        return out, None
    if not isinstance(host, str) or not _HOST.fullmatch(host.strip().lower()):
        return out, ("`workspace_host` is not a Databricks workspace host (no scheme, path or "
                     "credentials), so no link to the view is recorded")
    out["site"] = host.strip().lower()
    if not plain:
        return out, ("the view has no plain catalog.schema.view name, so no link to it is "
                     "recorded")
    out["url"] = f"https://{out['site']}/explore/data/{out['path']}"
    return out, None


def is_metric_view(doc: Any) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("source"), str)
            and any(isinstance(doc.get(k), list) for k in ("measures", "fields", "dimensions")))


def models(path: pathlib.Path) -> list[pathlib.Path]:
    """The views a BI input holds, one model each, sorted: the file itself, or every file
    under a folder that this reader reads as a metric view with at least one measure.

    A folder is a place other files live too (a bundle's own YAML, a dimension-only view, a
    query), so a file this reader does not read is left out, never an error of the run: name
    that file alone to be told why. A hidden folder is not entered and a symbolic link is not
    followed, so nothing outside the folder is read."""
    path = pathlib.Path(path)
    if not path.is_dir():
        return [path]
    found = []
    for root, dirs, files in os.walk(path):           # never follows a link to a folder
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in files:
            file = pathlib.Path(root) / name
            if file.suffix.lower() in SUFFIXES and not file.is_symlink() and _reads(file):
                found.append(file)
    return sorted(found)


def _reads(path: pathlib.Path) -> bool:
    try:
        parse(path)
    except MetricViewError:
        return False
    return True


def _table(raw: Any) -> str | None:
    """A table's name with its backticks removed, or None when `raw` is not one: one to three
    parts joined by dots, each a plain identifier or a plain identifier in backticks. A query,
    a name with anything after it and a name that needs its quoting are not names here: what
    is taken is written into SQL as it stands."""
    text = raw.strip() if isinstance(raw, str) else ""
    return text.replace("`", "") if _TABLE.fullmatch(text) else None


def _column(raw: Any) -> str | None:
    """One column's name with its backticks removed, or None when `raw` is not a plain one."""
    text = raw.strip() if isinstance(raw, str) else ""
    return text.strip("`") if _COLUMN.fullmatch(text) else None


# ── names and expressions ────────────────────────────────────────────────────────────────

def _ident(name: str) -> str:
    """One join name as part of a role name: a plain identifier, as `lib/declared` reads one."""
    out = re.sub(r"[^A-Za-z0-9_$]", "_", str(name)).strip("_") or "join"
    return out if not out[0].isdigit() else f"j_{out}"


def _strings(text: str, what: str) -> None:
    """Refuse SQL whose string literals `lib/declared` would misread. It reads a formula as
    text and takes a literal to end at the next quote, so a literal with a quote or a
    backslash in it (which Databricks SQL writes with a backslash) would leave the rest of
    the formula read as a literal, and `source` unbound. Two literals side by side are the
    same hazard under another spelling (`'it''s'`)."""
    try:
        tokens = sqlglot.tokenize(text, read=DIALECT)
    except Exception:  # noqa: BLE001 - text the tokenizer cannot read is not read
        raise _Skip(f"{what} is not SQL the parser reads") from None
    before = False
    for token in tokens:
        literal = token.token_type in _STRINGS
        if ((literal or token.token_type == TokenType.IDENTIFIER)
                and ("'" in token.text or "\\" in token.text)):
            raise _Skip(f"{what} holds a string literal with a quote or a backslash in it, "
                        f"which this reader does not carry")
        if literal and before:
            raise _Skip(f"{what} writes two string literals side by side (a doubled quote, "
                        f"or two literals to be joined), which this reader does not carry")
        before = literal


def _parse(sql: Any, what: str) -> exp.Expression:
    """ONE expression, as the Databricks dialect reads it. Raises _Skip, naming why."""
    if sql is None or (isinstance(sql, str) and not sql.strip()):
        raise _Skip(f"{what} has no expression")
    if not isinstance(sql, str):
        raise _Skip(f"{what} has an expression that is not text (YAML reads it as "
                    f"{type(sql).__name__})")
    text = sql.strip()
    if len(text) > MAX_EXPANDED:
        raise _Skip(f"{what} is longer than {MAX_EXPANDED:,} characters")
    _strings(text, what)
    try:
        tree = sqlglot.parse_one(text, read=DIALECT)
    except Exception:  # noqa: BLE001 - an expression the parser cannot read is not read
        raise _Skip(f"{what} is not SQL the parser reads") from None
    if not isinstance(tree, _VALUE):                  # `a; DROP TABLE t`, `SUM(a) AS total`
        raise _Skip(f"{what} is not one SQL expression")
    _faithful(tree, text, what)
    return tree


# Words that can stand before `(` without being a function call.
_NOT_CALLS = frozenset("AND OR NOT IN WHEN THEN ELSE ON WHERE AS BY OVER FILTER USING FROM BETWEEN "
                       "LIKE IS CASE END EXISTS INTERVAL DISTINCT SELECT".split())
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _calls(sql: str) -> dict[str, set[int]]:
    """{function name: the numbers of arguments it is called with}, read from the text."""
    try:
        tokens = list(sqlglot.tokenize(sql, read=DIALECT))
    except Exception:  # noqa: BLE001 - text that does not tokenize names no call
        return {}
    found: dict[str, set[int]] = {}
    stack: list[list] = []                            # [name or None, commas, has an argument]
    for i, tok in enumerate(tokens):
        if tok.token_type == TokenType.L_PAREN:
            prev = tokens[i - 1] if i else None
            name = None
            if prev is not None and prev.token_type not in _STRINGS \
                    and _WORD.fullmatch(prev.text or "") and prev.text.upper() not in _NOT_CALLS:
                name = prev.text.upper()
            stack.append([name, 0, False])
        elif tok.token_type == TokenType.R_PAREN:
            if stack:
                name, commas, any_arg = stack.pop()
                if name:
                    found.setdefault(name, set()).add(commas + 1 if any_arg else 0)
        elif stack:
            if tok.token_type == TokenType.COMMA:
                stack[-1][1] += 1
            stack[-1][2] = True
    return found


def _faithful(tree: exp.Expression, text: str, what: str) -> None:
    """Raises _Skip unless this reader's SQL writer writes `tree` back as the expression it
    read. The writer renders some forms as other SQL: a name in backticks where a date part
    belongs is written out bare, as SQL; `TO_DATE(x, format)` loses its format; `UNIX_DATE`
    gains a date of the writer's own; `DATEDIFF(end, start)` becomes the three-argument form,
    which counts whole days where the two-argument form counts calendar days. Each is another
    expression, so what is written is checked two ways: it reads back as the same tree, and no
    function is called with a number of arguments the text did not give it. An ordered
    aggregate is checked where the formula is written (`_written`), with its own words."""
    for var in tree.find_all(exp.Var):
        if not _WORD.fullmatch(str(var.name)):
            raise _Skip(f"{what} names a date part or unit that is not a plain word")
    if tree.find(exp.WithinGroup, exp.Order):
        return
    written = _sql(tree)
    try:
        same = sqlglot.parse_one(written, read=DIALECT) == tree
    except Exception:  # noqa: BLE001 - text that does not parse is not the expression
        same = False
    if not same:
        raise _Skip(f"{what} uses a form this reader's SQL writer does not write back "
                    f"unchanged")
    given, kept = _calls(text), _calls(written)
    for name in sorted(given):
        if name in kept and not given[name] <= kept[name]:
            raise _Skip(f"{what} calls {name} with {min(given[name] - kept[name])} argument(s), "
                        f"which this reader's SQL writer writes in another form whose result "
                        f"can differ")


def _sql(tree: exp.Expression) -> str:
    # A comment is not part of the number, and its text is not SQL `lib/declared` can read.
    return tree.sql(dialect=DIALECT, comments=False)


def _written(tree: exp.Expression, what: str) -> str:
    """`tree` as Databricks SQL, for a formula. An aggregate with an ordering (`WITHIN GROUP`,
    or `ORDER BY` inside the call) is written only when the text reads back as the same
    expression: the SQL writer turns `PERCENTILE_CONT(...) WITHIN GROUP (...)` into an
    approximate percentile and drops the ordering of `ARRAY_AGG(x ORDER BY y)`, and either is
    another result. Raises _Skip."""
    sql = _sql(tree)
    _strings(sql, what)
    if tree.find(exp.WithinGroup, exp.Order):
        try:
            same = sqlglot.parse_one(sql, read=DIALECT) == tree
        except Exception:  # noqa: BLE001 - text that does not parse is not the expression
            same = False
        if not same:
            raise _Skip(f"{what} uses an ordered aggregate (WITHIN GROUP, or ORDER BY inside "
                        f"the call) that this reader's SQL writer does not write back "
                        f"unchanged")
    return sql


def _is_measure_call(node: exp.Expression) -> bool:
    return isinstance(node, exp.Anonymous) and str(node.name).upper() == "MEASURE"


class _View:
    """One metric view being read: its roles, and the fields and measures read so far."""

    def __init__(self, doc: dict, extra: dict) -> None:
        self.doc = doc
        self.extra = extra
        self.notes: list[str] = []
        # role (lower) -> {"name", "physical", "path": the join path as the view writes it}
        self.roles: dict[str, dict] = {}
        self.by_path: dict[tuple[str, ...], str] = {}
        self.relationships: list[dict] = []
        self.parameters: set[str] = set()
        # field name (lower) -> {"name", "role", "tree", "reads", "plain"}; in definition order.
        self.fields: dict[str, dict] = {}
        # measure name (lower) -> the measure's expression, columns bound and every
        # `MEASURE()` replaced, before the view's filter is written in.
        self.measures: dict[str, exp.Expression] = {}
        self.measure_names: dict[str, str] = {}
        self.sizes: dict[str, int] = {}               # measure name (lower) -> `size` of it
        self.unread: set[str] = set()                 # measures named but not read
        self.unread_fields: set[str] = set()
        # what reads what: {"measure 'a'": {"field 'b'", "measure 'c'"}}. Something that is
        # dropped takes everything built on it with it.
        self.deps: dict[str, set[str]] = {}
        # (role, COLUMN) every expression reads as a COLUMN OF THE TABLE, by what read it.
        self.physical: dict[tuple[str, str], list[str]] = {}

    # -- roles ---------------------------------------------------------------------------
    def columns_of(self, role: str) -> set[str] | None:
        """The table's own columns (lower), when the input carries them."""
        got = (self.extra.get("source_columns") or {}).get(self.roles[role]["physical"].lower())
        return {c.strip("`").lower() for c in got} if got else None

    def add_parameters(self) -> None:
        """The names `parameters` declares. Each is a name no expression may read, so an
        entry this reader cannot name leaves no bare name it can trust to be a column."""
        declared = self.doc.get("parameters")
        if declared is None or declared == []:
            return
        if not isinstance(declared, list):
            raise MetricViewError("its `parameters` is not a list, so the names it declares "
                                  "cannot be told from columns")
        for p in declared:
            name = p.get("name") if isinstance(p, dict) else p
            if not isinstance(name, str) or not name.strip():
                raise MetricViewError("a `parameters` entry has no name, so the name it "
                                      "declares cannot be told from a column")
            self.parameters.add(name.strip().lower())

    def add_source(self) -> None:
        physical = _table(self.doc.get("source"))
        if not physical:
            raise MetricViewError("its source is not a table name this reader can read: a "
                                  "query, or a name that needs quoting")
        if physical.count(".") != 2:
            raise MetricViewError("its source is not a CATALOG.SCHEMA.TABLE name; this reader "
                                  "reads a view over one table, with joins to others")
        self.roles[SOURCE_ROLE] = {"name": SOURCE_ROLE, "physical": physical, "path": ()}
        self.by_path[(SOURCE_ROLE,)] = SOURCE_ROLE

    def add_joins(self, joins: Any, parent: str, path: tuple[str, ...]) -> None:
        if joins is not None and not isinstance(joins, list):
            self.notes.append("`joins` is not a list; no join under it is read")
        for j in joins if isinstance(joins, list) else []:
            if not isinstance(j, dict) or not isinstance(j.get("name"), str) \
                    or not j["name"].strip():
                self.notes.append("a join without a name that is text is not read")
                continue
            name = j["name"].strip()
            here = path + (name.lower(),)
            role = "__".join(_ident(p) for p in here)
            label = ".".join(here)
            physical = _table(j.get("source"))
            if not physical or physical.count(".") != 2:
                why = ("is not a CATALOG.SCHEMA.TABLE name" if physical else
                       "is not a table name this reader can read: a query, or a name that "
                       "needs quoting")
                self.notes.append(f"join {label!r} has a source that {why}; it is not read, "
                                  f"and nothing that needs it is placed")
                continue
            if role.lower() in self.roles:
                self.notes.append(f"join {label!r} has the name of another join; it is not read")
                continue
            self.roles[role.lower()] = {"name": role, "physical": physical, "path": here}
            self.by_path[here] = role.lower()
            try:
                pairs = self._pairs(j, parent, role.lower(), name)
            except _Skip as exc:
                self.notes.append(f"join {label!r} {exc}; nothing that needs it is placed")
                pairs = []
            if pairs:
                card = str(j.get("cardinality") or "many_to_one").lower()
                self.relationships.append({
                    "from_table": self.roles[parent]["physical"], "from_column": pairs[0][0],
                    "to_table": physical, "to_column": pairs[0][1], "is_active": True,
                    "cardinality": _CARD.get(card, "unknown"), "kind": "relationship",
                    "name": f"{self.roles[parent]['name']}_to_{role}",
                    "from_role": self.roles[parent]["name"], "to_role": role,
                    "columns": [{"from_column": a, "to_column": b} for a, b in pairs]})
                if card != "many_to_one":
                    self.notes.append(f"join {label!r} is {card}: it is not walked, and a "
                                      f"measure that reads it is not placed")
            self.add_joins(j.get("joins"), role.lower(), here)

    def _pairs(self, join: dict, parent: str, role: str, name: str) -> list[tuple[str, str]]:
        """Every (parent column, joined column) pair of one join."""
        using = join.get("using")
        if using is not None:
            cols = [_column(c) for c in using] if isinstance(using, list) else []
            if not cols or not all(cols):
                raise _Skip("has a `using` that is not a list of plain column names")
            return [(c, c) for c in cols]
        # YAML 1.1 reads a bare `on:` key as the boolean true.
        cond = join.get("on", join.get(True))
        if cond in (None, ""):
            raise _Skip("has neither `on` nor `using`")
        tree = _parse(cond, "its condition")
        terms, stack = [], [tree]
        while stack:
            node = stack.pop()
            if isinstance(node, exp.Paren):
                stack.append(node.this)
            elif isinstance(node, exp.And):
                stack += [node.expression, node.this]
            else:
                terms.append(node)
        parent_names = {SOURCE_ROLE} if parent == SOURCE_ROLE else \
            {self.roles[parent]["path"][-1], ".".join(self.roles[parent]["path"])}
        joined_names = {name.lower(), ".".join(self.roles[role]["path"])}
        out = []
        for term in terms:
            if not (isinstance(term, exp.EQ) and isinstance(term.this, exp.Column)
                    and isinstance(term.expression, exp.Column)):
                raise _Skip("is not written as equalities between the two tables")
            sides = [self._side(c, parent, role, parent_names, joined_names)
                     for c in (term.this, term.expression)]
            known = [s for s, _ in sides if s]
            if not known:
                raise _Skip("names its columns without a table prefix, and the tables' "
                            "columns did not come with the view: prefix each side, or name "
                            "them in `source_columns`")
            if len(known) == 1:                       # the other column is the other table's
                sides = [(s or ("joined" if known[0] == "parent" else "parent"), c)
                         for s, c in sides]
            if {s for s, _ in sides} != {"parent", "joined"}:
                raise _Skip("compares two columns of one table")
            by = dict(sides)
            out.append((by["parent"], by["joined"]))
        return out

    def _side(self, col: exp.Column, parent: str, role: str, parent_names: set,
              joined_names: set) -> tuple[str | None, str]:
        parts = [p.name for p in col.parts]
        name = parts[-1]
        if not _PLAIN.fullmatch(name):
            raise _Skip(f"names the column `{name}`, which needs quoting")
        prefix = ".".join(parts[:-1]).lower()
        if prefix:
            if prefix in joined_names:
                return "joined", name
            if prefix in parent_names:
                return "parent", name
            raise _Skip(f"names `{prefix}`, which is neither of the two tables")
        mine, theirs = self.columns_of(role), self.columns_of(parent)
        if mine is None or theirs is None:
            return None, name
        # Databricks reads a bare name in `on` as the joined table's when it has one.
        if name.lower() in mine:
            return "joined", name
        return ("parent", name) if name.lower() in theirs else (None, name)

    # -- expressions ---------------------------------------------------------------------
    def bind(self, raw: Any, what: str, *, own: str | None = None, measures: bool = False,
             in_filter: bool = False) -> tuple[exp.Expression, set[str]]:
        """(`raw` with every column bound to its role and every `MEASURE()` replaced, the
        roles it reads). `own` is the field being defined: its own name inside its own
        expression is the column. `in_filter` is the view's filter, which reads the tables'
        columns and never a field: a bare name there is a column of the source, whatever
        field has that name. Raises _Skip, naming why."""
        tree = _parse(raw, what)
        if isinstance(tree, exp.Star) or (isinstance(tree, exp.Column)
                                          and isinstance(tree.this, exp.Star)):
            raise _Skip(f"{what} is a wildcard; its columns are not listed in the view")
        if tree.find(exp.Window):
            raise _Skip(f"{what} uses a window function")
        if tree.find(exp.Placeholder, exp.Parameter, exp.SessionParameter):
            raise _Skip(f"{what} reads a parameter (a marker such as `:name` or `?`), whose "
                        f"value is given when the view is queried")
        if tree.find(exp.Query):
            raise _Skip(f"{what} holds a query of its own; this reader reads expressions over "
                        f"the view's tables")
        if tree.find(exp.Lambda):
            raise _Skip(f"{what} uses a lambda, whose names cannot be told from columns")
        if tree.find(exp.JSONExtract, exp.JSONExtractScalar, exp.JSONPath):
            raise _Skip(f"{what} reads a path inside a value (`column:key`), whose keys cannot "
                        f"be told from the view's tables")
        if any(str(n.name).upper() == "IDENTIFIER" for n in tree.find_all(exp.Anonymous)):
            raise _Skip(f"{what} uses IDENTIFIER(), which names a column in text this reader "
                        f"cannot bind")
        reads: set[str] = set()
        deps = self.deps.setdefault(what, set())
        spent = [len(_sql(tree))]

        def spend(more: int) -> None:
            # Checked BEFORE the copy is made: a chain of measures that each read the last
            # one twice doubles at every step, and is stopped at the step that crosses the cap.
            spent[0] += more
            if spent[0] > MAX_EXPANDED:
                raise _Skip(f"{what} is longer than {MAX_EXPANDED:,} characters once the "
                            f"fields and measures it reads are written out")

        def measure(node: exp.Expression) -> exp.Expression:
            args = node.expressions
            ref = args[0] if len(args) == 1 and isinstance(args[0], exp.Column) else None
            key = ref.name.lower() if ref is not None and len(ref.parts) == 1 else None
            if not measures:
                raise _Skip(f"{what} reads a measure; only a measure can")
            if key is None or key not in self.measures:
                named = _sql(args[0]) if args else ""
                why = ("which is not read" if key in self.unread else
                       "which is not an earlier measure of this view")
                raise _Skip(f"{what} reads MEASURE({named}), {why}")
            spend(self.sizes[key])
            inner = self.measures[key].copy()
            reads.update(_declared.qualifiers(_sql(inner)))
            deps.add(f"measure {self.measure_names[key]!r}")
            return exp.Paren(this=inner)

        def column(node: exp.Column) -> exp.Expression:
            parts = [p.name for p in node.parts]
            lower = [p.lower() for p in parts]
            role, rest = None, parts
            for k in range(len(parts) - 1, 0, -1):    # the longest join path that prefixes it
                if tuple(lower[:k]) in self.by_path:
                    role, rest = self.by_path[tuple(lower[:k])], parts[k:]
                    break
            if role is None and len(parts) == 1:
                name = lower[0]
                if name in self.parameters:
                    raise _Skip(f"{what} reads the parameter `{parts[0]}`")
                if name in self.unread_fields and name != own and not in_filter:
                    raise _Skip(f"{what} reads the field `{parts[0]}`, which is not read")
                field = None if in_filter else self.fields.get(name)
                if field is not None and name != own:
                    spend(field["size"])
                    reads.update(field["reads"])
                    deps.add(f"field {field['name']!r}")
                    if field["plain"]:
                        named = exp.column(field["name"], table=field["role_name"])
                        named.meta["field"] = name    # `lib/declared` writes it out later
                        return named
                    # A field whose name needs quoting, or whose name is a column the
                    # view's filter reads, cannot be named in a formula `lib/declared` reads:
                    # its expression stands in its place.
                    return exp.Paren(this=field["tree"].copy())
            if role is None and len(parts) > 1:
                raise _Skip(f"{what} names `{'.'.join(parts)}`: `{parts[0]}` is neither the "
                            f"source nor a join this reader read")
            role = role or SOURCE_ROLE
            if len(rest) > 1:
                raise _Skip(f"{what} reads a nested value, `{'.'.join(rest)}`")
            if not _PLAIN.fullmatch(rest[0]):
                raise _Skip(f"{what} names the column `{rest[0]}`, which needs quoting")
            reads.add(role)
            self.physical.setdefault((role, rest[0].upper()), []).append(what)
            bound = exp.column(rest[0], table=self.roles[role]["name"])
            bound.meta["physical"] = True
            return bound

        def visit(node: exp.Expression) -> exp.Expression:
            if _is_measure_call(node):
                return measure(node)
            if isinstance(node, exp.Column):
                return column(node)
            return node

        bound = tree.transform(visit, copy=True)
        self.room(bound, what)
        return bound, reads

    def size(self, tree: exp.Expression) -> int:
        """How long `tree` is as a formula once every field it names is written out, which
        `lib/declared` does for each one, however deep the chain."""
        return len(_sql(tree)) + sum(self.fields[c.meta["field"]]["size"]
                                     for c in tree.find_all(exp.Column)
                                     if c.meta.get("field") in self.fields)

    def room(self, tree: exp.Expression, what: str, more: int = 0) -> None:
        if self.size(tree) + more > MAX_EXPANDED:
            raise _Skip(f"{what} is longer than {MAX_EXPANDED:,} characters once the fields "
                        f"and measures it reads are written out")

    def filtered(self, tree: exp.Expression, cond: exp.Expression, what: str) -> exp.Expression:
        """`tree` with the view's filter written into every aggregate. Raises _Skip when the
        filter cannot be shown to reach all of it: an aggregate could be hidden in a function
        the parser does not know, or a column is read outside every aggregate."""
        if tree.find(exp.Any, exp.All):
            raise _Skip(f"{what} uses ANY, SOME or ALL, which this reader does not recognise "
                        f"as an aggregate: the view's filter cannot be written into it")
        unknown = sorted({str(n.name).upper() for n in tree.find_all(exp.Anonymous)})
        if unknown:
            raise _Skip(f"{what} calls {', '.join(unknown)}, which may aggregate: the view's "
                        f"filter cannot be written into it")
        # Checked before the filter is copied into each aggregate, for the same reason.
        self.room(tree, what, sum(1 for _ in tree.find_all(exp.AggFunc)) * (self.size(cond) + 16))

        def visit(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Filter):
                where = node.expression
                where.set("this", exp.and_(exp.paren(where.this.copy()), exp.paren(cond.copy())))
                return node
            if isinstance(node.parent, (exp.Filter, exp.WithinGroup)) \
                    or node.find_ancestor(exp.AggFunc) is not None:
                return node
            # An ordered-set aggregate takes its FILTER after `WITHIN GROUP (...)`, which is
            # where Databricks reads it, so the whole clause is what the filter wraps.
            if isinstance(node, exp.AggFunc) or (isinstance(node, exp.WithinGroup)
                                                 and isinstance(node.this, exp.AggFunc)):
                return exp.Filter(this=node.copy(), expression=exp.Where(this=cond.copy()))
            return node

        out = tree.transform(visit, copy=True)
        # An aggregate this reader did not recognise as one (`ANY(flag)`) is left unfiltered
        # above. What gives it away is its column, read outside every FILTER.
        loose = sorted({c.name for c in out.find_all(exp.Column)
                        if c.find_ancestor(exp.Filter) is None})
        if loose:
            raise _Skip(f"{what} reads {', '.join(f'`{c}`' for c in loose[:3])} outside every "
                        f"aggregate this reader recognises, so the view's filter could not be "
                        f"applied to all of it")
        self.room(out, what)
        return out


# ── fixed dates ──────────────────────────────────────────────────────────────────────────

# A date or a timestamp as a text value, in ASCII digits: `2024-01-01`, `2024-01-01 09:30:00.5`,
# `2024-01-01T09:30:00Z`, `2024-01-01 09:30:00+05:30`.
_DATE_VALUE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
                         r"(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,9})?)?)?"
                         r"(?: ?(?:Z|UTC|[+-][0-9]{2}(?::?[0-9]{2})?))?")
# What Atlan's own check reads as a fixed date: a text value that starts like one.
_DATE_START = re.compile(r"'\d{4}-\d{2}-\d{2}")
_TYPED = re.compile(r"(?i)(?<![A-Za-z0-9_$.`])(DATE|TIMESTAMP)\s*$")
_SIDES = {exp.GTE: ("from", "to"), exp.GT: ("from", "to"), exp.LTE: ("to", "from"),
          exp.LT: ("to", "from"), exp.EQ: ("on", "on")}
_THROUGH = (exp.Cast, exp.TryCast, exp.Paren, exp.TsOrDsToDate, exp.TsOrDsToTimestamp,
            exp.StrToDate, exp.StrToTime, exp.Date, exp.Timestamp, exp.Anonymous)
_TAG = {"T": "t", " ": "_", ":": "_", ".": "_", "+": "p", "-": "m"}


def _date_tag(value: str) -> str:
    """`value` as the end of a parameter's name. Two different values give two different
    tags: the date reads as itself, and what follows it keeps its separator, its fraction and
    its zone (`2024-01-01T09:30Z` is `2024_01_01t09_30z`)."""
    rest = value[10:].replace("UTC", "u").replace("Z", "z")
    return value[:10].replace("-", "_") + "".join(_TAG.get(ch, ch) for ch in rest)


def _date_names(formula: str, values: list[str]) -> dict[str, str]:
    """{date: parameter name}. A date compared with one column in one way is named for the
    column and the side it bounds (`order_date_from`). Two dates that would share that name
    each also carry their date (`order_date_from_2024_01_01`), and a date not compared with
    one column in one way is named for the date alone (`date_2024_01_01`).

    The name says what the date is for, not what it is. Two measures that differ only in a
    date therefore declare ONE formula with two defaults, which is what they are downstream:
    one metric whose date a reader may move. The card gate holds them to that: it refuses two
    cards with one formula and says they are one metric under two names."""
    said: dict[str, set[str]] = {}
    try:
        tree = sqlglot.parse_one(formula, read=DIALECT)
    except Exception:                                 # noqa: BLE001 - names fall back to `date`
        tree = None
    for lit in (tree.find_all(exp.Literal) if tree is not None else []):
        if not lit.is_string or lit.this not in values:
            continue
        node: exp.Expression = lit
        while isinstance(node.parent, _THROUGH):
            node = node.parent
        cmp_ = node.parent
        other, side = None, None
        if isinstance(cmp_, exp.Between):
            other = cmp_.this
            side = "from" if cmp_.args.get("low") is node else \
                "to" if cmp_.args.get("high") is node else None
        elif type(cmp_) in _SIDES:
            right = cmp_.expression is node
            other = cmp_.this if right else cmp_.expression
            side = _SIDES[type(cmp_)][0 if right else 1]
        cols = {c.name for c in other.find_all(exp.Column)} if other is not None else set()
        base = re.sub(r"[^A-Za-z0-9_]", "_", cols.pop()).lower() if len(cols) == 1 else ""
        said.setdefault(lit.this, set()).add(
            f"{base}_{side}" if side and re.match(r"[a-z_]", base) else "")
    bases = {}
    for v in values:
        one = said.get(v) or set()
        bases[v] = next(iter(one)) if len(one) == 1 and "" not in one else "date"
    shared = Counter(bases.values())
    return {v: base if base != "date" and shared[base] == 1 else f"{base}_{_date_tag(v)}"
            for v, base in bases.items()}


def date_parameters(formula: str) -> tuple[str, list[dict]]:
    """`formula` with every fixed date written as `{{name}}`, and the parameters that give each
    its date as the default. Atlan keeps no fixed date in a metric's formula: a date is a value
    a reader may move. Raises _Skip for a text value that starts like a date and is not a
    plain date or timestamp: it cannot be lifted out, and Atlan would refuse it where it is."""
    parts = _declared.outside_literals(formula)
    values = list(dict.fromkeys(
        text[1:-1] for is_code, text in parts if not is_code and _DATE_VALUE.fullmatch(text[1:-1])))
    for is_code, text in parts:
        if not is_code and _DATE_START.match(text) and not _DATE_VALUE.fullmatch(text[1:-1]):
            raise _Skip("holds a text value that starts like a date and is not a plain date or "
                        "timestamp, which a saved metric cannot keep")
    if not values:
        return formula, []
    names = _date_names(formula, values)
    if len(set(names.values())) != len(names):       # cannot happen while the tag tells dates apart
        raise _Skip("holds two fixed dates this reader cannot name apart")
    out: list[str] = []
    for is_code, text in parts:
        if is_code or text[1:-1] not in names:
            out.append(text)
            continue
        marker = "{{" + names[text[1:-1]] + "}}"
        # `DATE '2024-01-01'` is one typed value: a marker cannot follow the keyword, so the
        # pair is written as the cast it means.
        typed = _TYPED.search(out[-1]) if out else None
        if typed:
            out[-1] = out[-1][:typed.start()]
            marker = f"CAST({marker} AS {typed.group(1).upper()})"
        out.append(marker)
    return "".join(out), [{"name": names[v], "datatype": "date" if len(v) == 10 else "timestamp",
                           "default": v} for v in values]


# ── types ────────────────────────────────────────────────────────────────────────────────

def _types(columns: Any, notes: list[str]) -> dict[str, str]:
    """{field or measure name (lower): its type, as the view's description declares it}."""
    if columns is None or columns == []:
        return {}
    if not isinstance(columns, list):
        notes.append("`columns` is not a list; no field's or measure's type is read")
        return {}
    out, odd = {}, 0
    for c in columns:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str):
            odd += 1
            continue
        t = c.get("type") or c.get("type_text")       # DESCRIBE's, or the table API's
        name = t.get("name") if isinstance(t, dict) else t
        if not isinstance(name, str) or not name.strip():
            odd += t is not None
            continue
        name = name.strip().upper()
        if isinstance(t, dict) and name == "DECIMAL" and type(t.get("precision")) is int:
            scale = t.get("scale") if type(t.get("scale")) is int else 0
            name = f"DECIMAL({t['precision']},{scale})"
        out[c["name"].lower()] = "BIGINT" if name == "LONG" else name
    if odd:
        notes.append(f"{odd} entr{'y' if odd == 1 else 'ies'} of `columns` did not name a "
                     "column and its type; ignored")
    return out


def _said(value: Any) -> str | None:
    """A comment or display name: text, or nothing. Another YAML value is not prose."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _words(item: dict, what: str, notes: list[str]) -> list[str]:
    raw = item.get("synonyms")
    if raw is not None and not isinstance(raw, list):
        notes.append(f"{what} has `synonyms` that are not a list; they are ignored")
    out = [x.strip() for x in (raw if isinstance(raw, list) else []) if _said(x)]
    shown = _said(item.get("display_name"))
    if shown and shown.lower() not in {str(item.get("name") or "").lower(),
                                       *(w.lower() for w in out)}:
        out.insert(0, shown)
    return out


def _names(items: list) -> Counter:
    """{name, lower-cased: how many items of the list carry it}."""
    return Counter(item["name"].strip().lower() for item in items
                   if isinstance(item, dict) and isinstance(item.get("name"), str))


def _name(item: dict, kind: str) -> str:
    """An item's name, or the name Databricks derives when it has none: a column's own."""
    name, expr = item.get("name"), item.get("expr")
    if isinstance(name, str) and name.strip():
        return name
    if name not in (None, ""):
        # `name: no` is a boolean to YAML and `name: 2024-01-01` a date. Which name
        # Databricks gave such an item is not something to guess.
        raise _Skip(f"a {kind} whose name is not text (YAML reads it as "
                    f"{type(name).__name__}) is not read")
    try:
        tree = sqlglot.parse_one(expr, read=DIALECT) if isinstance(expr, str) else None
    except Exception:  # noqa: BLE001
        tree = None
    if isinstance(tree, exp.Column):
        return tree.name
    shown = expr[:60] if isinstance(expr, str) else ""
    raise _Skip(f"a {kind} without a name ({shown!r}) is not read")


# ── the parse ────────────────────────────────────────────────────────────────────────────

def parse(path: pathlib.Path, report_index: dict | None = None, *,
          dialect: str | None = None) -> dict[str, Any]:
    """`report_index` is accepted and ignored: a metric view has no report layer. A metric
    view's SQL is Databricks SQL, whatever the run's `dialect`.

    Every refusal is a `MetricViewError`. Each shape this reader expects is checked where it
    is read; the last clause is the backstop for a malformed definition that slips past those
    checks, and it names the file and the kind of error, never the file's contents."""
    src = pathlib.Path(path)
    try:
        return _read(src)
    except MetricViewError:
        raise
    except (TypeError, AttributeError, KeyError, IndexError, ValueError, RecursionError) as exc:
        raise MetricViewError(f"{src}: the definition is malformed in a way this reader does "
                              f"not read ({type(exc).__name__}); it is not read") from None


def _read(src: pathlib.Path) -> dict[str, Any]:
    doc, extra, shape = load(src)
    if not is_metric_view(doc):
        raise MetricViewError(f"{src}: not a metric view: expected `source` and `measures` "
                              "(with `fields` or `dimensions`)")
    name = str(extra.get("name") or src.stem)
    version = str(doc.get("version") or "").strip()
    view = _View(doc, extra)
    view.notes += extra.get("notes") or []
    if version not in VERSIONS:
        view.notes.append(f"specification version {version or '(none)'!r} is not one this "
                          f"reader was written for ({', '.join(VERSIONS)}); read as 1.1")
    try:
        view.add_source()
        view.add_parameters()
    except MetricViewError as exc:
        raise MetricViewError(f"{src}: the metric view is not read: {exc}") from None
    view.add_joins(doc.get("joins"), SOURCE_ROLE, ())
    types = _types(extra.get("columns"), view.notes)
    columns: dict[str, list[dict]] = {r: [] for r in view.roles}
    not_carried = {"formats": 0}

    # THE VIEW'S FILTER, bound first and over the tables' columns alone: a bare name in it is
    # a column of the source, never a field (`in_filter`). It is part of every number the
    # view gives, so when it cannot be read no measure is.
    cond = None
    if doc.get("filter") not in (None, ""):
        try:
            cond, _reads = view.bind(doc["filter"], FILTER, in_filter=True)
        except (_Skip, RecursionError) as exc:
            why = exc if isinstance(exc, _Skip) else f"{FILTER} is nested too deeply to read"
            raise MetricViewError(f"{src}: {why}; the filter is part of every measure of the "
                                  "view, so none is read") from None

    # FIELDS, in the view's order: a field may read only the ones before it.
    if doc.get("fields") and doc.get("dimensions"):
        raise MetricViewError(f"{src}: the metric view lists both `fields` and `dimensions`, "
                              "two names for one list; which one its measures read cannot be "
                              "told, so it is not read")
    fields = doc.get("fields") or doc.get("dimensions") or []
    if not isinstance(fields, list):
        view.notes.append("the view's fields are not a list; none is read")
        fields = []
    listed = doc.get("measures") if isinstance(doc.get("measures"), list) else []
    if sum(len(i["expr"]) for i in listed
           if isinstance(i, dict) and isinstance(i.get("expr"), str)) > MAX_VIEW_EXPANDED:
        raise MetricViewError(f"{src}: the view's measures come to more than "
                              f"{MAX_VIEW_EXPANDED:,} characters as written; it is not read")
    if len(fields) * len(listed) > MAX_PAIRS:
        raise MetricViewError(f"{src}: the metric view lists {len(fields):,} fields and "
                              f"{len(listed):,} measures, more than this reader reads from one "
                              "view; it is not read")
    # A NAME USED TWICE names two things, and nothing here can tell which one an expression
    # means. A measure with a field's name is the same trouble: inside it Databricks reads the
    # field, and the declaration would read a column of that name.
    field_names = _names(fields)
    twice_fields = {n for n, k in field_names.items() if k > 1}
    twice_measures = {n for n, k in _names(listed).items() if k > 1}
    for item in fields:
        if not isinstance(item, dict):
            view.notes.append("a field that is not a mapping is not read")
            continue
        what = "a field"
        try:
            fname = _name(item, "field")
            what = f"field {fname!r}"
            if fname.lower() in twice_fields:
                raise _Skip(f"{what} shares its name with another field; which one an "
                            f"expression reads cannot be told")
            tree, reads = view.bind(item.get("expr"), what, own=fname.lower())
            formula = _written(tree, what)
        except (_Skip, RecursionError) as exc:
            why = exc if isinstance(exc, _Skip) else f"{what} is nested too deeply to read"
            view.notes.append(f"{why}; it is not read")
            if isinstance(item.get("name"), str):
                view.unread_fields.add(item["name"].lower())
            continue
        others = [r for r in sorted(reads) if r != SOURCE_ROLE]
        role = others[0] if others else SOURCE_ROLE
        if len(others) > 1:
            view.notes.append(f"field {fname!r} reads {len(others)} joined tables; it is "
                              f"listed under {view.roles[role]['name']}")
        role_name = view.roles[role]["name"]
        # One column of one table, as written: a plain column under the field's name.
        simple = isinstance(tree, exp.Column) and bool(tree.meta.get("physical"))
        col: dict[str, Any] = {"name": fname, "data_type": types.get(fname.lower()),
                               "role": "dimension", "is_calculated": not simple,
                               "source_column": tree.name if simple else fname}
        if not simple:
            col["formula"] = formula
            col["formula_language"] = "sql"
        if _said(item.get("comment")):
            col["description"] = _said(item.get("comment"))
        words = _words(item, what, view.notes)
        if words:
            col["synonyms"] = words
        not_carried["formats"] += bool(item.get("format"))
        computed = not simple or tree.name.lower() != fname.lower()
        # A FIELD THAT REDEFINES A COLUMN THE FILTER READS. The filter reads the column and a
        # measure reads the field, under one name. `lib/declared` reads `role.name` as the
        # field wherever it appears, so the model cannot hold both: the column keeps the
        # name, the field is not listed, and what reads the field carries its expression.
        listed = not (computed and FILTER in view.physical.get((role, fname.upper()), []))
        if listed:
            columns[role].append(col)
        else:
            view.notes.append(f"{what} redefines a column the view's filter reads. The filter "
                              f"reads the column, so the field is not listed as a dimension; "
                              f"a measure that reads the field carries its expression")
        view.fields[fname.lower()] = {"name": fname, "role": role, "role_name": role_name,
                                      "tree": tree, "reads": reads | {role},
                                      "plain": listed and bool(_PLAIN.fullmatch(fname)),
                                      "size": view.size(tree), "listed": listed,
                                      "computed": computed}

    # THE FILTER NAMES NO FIELD. A field the source has no column for cannot be what the
    # filter reads, so Databricks would not have accepted the view; that is only knowable
    # when the source's columns came with the view.
    known = view.columns_of(SOURCE_ROLE)
    for (of, column), readers in sorted(view.physical.items()):
        read = column.lower()
        if (cond is not None and of == SOURCE_ROLE and set(readers) == {FILTER}
                and known is not None and read not in known
                and (read in view.fields or read in view.unread_fields)):
            raise MetricViewError(
                f"{src}: {FILTER} reads `{read}`, which is a field of the view and not a "
                "column of its source (`source_columns`). Databricks reads a view's filter "
                "over the source's columns and accepts no such view, so the filter is not "
                "read; it is part of every measure of the view, so none is read")

    declared = doc.get("measures") or []
    if not isinstance(declared, list):
        view.notes.append("the view's measures are not a list; none is read")
        declared = []
    measures: list[dict] = []
    written_out = 0
    for item in declared:
        if not isinstance(item, dict):
            view.notes.append("a measure that is not a mapping is not read")
            continue
        try:
            mname = _name(item, "measure")
        except _Skip as exc:
            view.notes.append(f"{exc}")
            continue
        what = f"measure {mname!r}"
        try:
            if item.get("window") not in (None, [], {}, ""):
                raise _Skip(f"{what} is a window measure: its frame is applied when the view "
                            f"is queried")
            if mname.lower() in twice_measures:
                raise _Skip(f"{what} shares its name with another measure")
            if mname.lower() in field_names:
                raise _Skip(f"{what} has the name of a field: inside it that name reads the "
                            f"field, and a formula written out would read a column")
            tree, _reads = view.bind(item.get("expr"), what, measures=True)
            final = view.filtered(tree, cond, what) if cond is not None else tree
            formula = _written(final, what)
            written_out += len(formula)
            if written_out > MAX_VIEW_EXPANDED:
                raise MetricViewError(
                    f"{src}: the view's measures come to more than {MAX_VIEW_EXPANDED:,} "
                    f"characters once the fields, measures and filter they read are written "
                    f"out; it is not read")
        except (_Skip, RecursionError) as exc:
            why = exc if isinstance(exc, _Skip) else f"{what} is nested too deeply to read"
            view.notes.append(f"{why}; it is not read")
            view.unread.add(mname.lower())
            continue
        view.measures[mname.lower()] = tree
        view.measure_names[mname.lower()] = mname
        view.sizes[mname.lower()] = view.size(tree)
        m: dict[str, Any] = {"name": mname, "home_table": SOURCE_ROLE, "formula": formula,
                             "formula_language": "sql", "aggregation_at_use_site": False,
                             # A metric view names a measure only as MEASURE(x), replaced
                             # above: `source.<name>` in a formula is always a column.
                             "referable": False}
        if _said(item.get("comment")):
            m["description"] = _said(item.get("comment"))
        words = _words(item, what, view.notes)
        if words:
            m["synonyms"] = words
        not_carried["formats"] += bool(item.get("format"))
        measures.append(m)

    if not measures:
        why = f" ({len(view.unread)} not read: see the reader's notes)" if view.unread else ""
        raise MetricViewError(f"{src}: the metric view declares no measure this reader "
                              f"reads{why}" + "".join(f"\n  {n}" for n in view.notes))

    # A COMPUTED FIELD WITH A COLUMN'S NAME. `lib/declared` reads `role.name` as the field
    # wherever it appears. Where an expression reads the COLUMN of that name (written before
    # the field is defined, or under its table's name), the two cannot be told apart, so what
    # reads it is dropped by name rather than computed from the wrong one.
    clash: dict[str, str] = {}
    for f in view.fields.values():
        if not f["computed"] or not f["listed"]:      # an unlisted field is no name in the model
            continue
        own = f"field {f['name']!r}"
        for what in view.physical.get((f["role"], f["name"].upper()), []):
            if what != own:
                clash[what] = f["name"]
    for what, fname in sorted(clash.items()):
        view.notes.append(f"{what} reads the column `{fname}`, and a field of that name "
                          f"redefines it; it is not read")
    dropped = set(clash)
    grew = True
    while grew:                                       # and everything built on what was dropped
        grew = False
        for what, needs in sorted(view.deps.items()):
            hit = sorted(needs & dropped)
            if hit and what not in dropped:
                dropped.add(what)
                grew = True
                view.notes.append(f"{what} reads {hit[0]}, which is not read; it is not read")
    measures = [m for m in measures if f"measure {m['name']!r}" not in dropped]
    for role in columns:
        columns[role] = [c for c in columns[role] if f"field {c['name']!r}" not in dropped]
    if not measures:
        raise MetricViewError(f"{src}: the metric view declares no measure this reader reads"
                              + "".join(f"\n  {n}" for n in view.notes))

    keys = extra.get("primary_keys") or {}
    tables: list[dict] = []
    for role, r in view.roles.items():
        cols = columns[role]
        # A key column is written into the card's grain as it stands, so it is a plain name.
        pk = [_column(k) for k in keys.get(r["physical"].lower()) or []]
        if not all(pk):
            view.notes.append(f"`primary_keys` for {r['physical']} names a column that needs "
                              "quoting; the key is ignored")
            pk = []
        have = {str(c["source_column"]).lower() for c in cols}
        joined = [p["from_column"] for rel in view.relationships
                  if rel["from_role"].lower() == role for p in rel["columns"]]
        joined += [p["to_column"] for rel in view.relationships
                   if rel["to_role"].lower() == role for p in rel["columns"]]
        for k in [*pk, *joined]:
            if k.lower() not in have:
                have.add(k.lower())
                cols.append({"name": k, "data_type": None, "role": None,
                             "is_calculated": False, "source_column": k})
        cat, sch, obj = r["physical"].split(".")
        entry: dict[str, Any] = {
            "name": r["physical"], "logical_name": r["name"], "columns": cols,
            "source_lineage": {"connection": None, "database": cat, "schema": sch,
                               "object": obj.lower(), "object_kind": "table",
                               "raw_expression": r["physical"], "published_datasource": None}}
        if pk:
            entry["primary_key"] = pk
        tables.append(entry)

    facts: dict[str, Any] = {
        "contract_version": "1.0", "vendor": VENDOR, "source_file": str(src),
        "model_name": name, "tables": tables, "measures": measures,
        "relationships": view.relationships, "usage": [], "parameters": [],
    }
    own, unlinked = asset(extra, name)
    facts["assets"] = [own]
    if unlinked:
        view.notes.append(unlinked)
    try:
        notes = _declared.attach(facts, dialect=DIALECT)
    except RecursionError:
        raise MetricViewError(f"{src}: the view's fields build on each other more deeply than "
                              "this reader follows; it is not read") from None
    # A FIXED DATE IS A PARAMETER ON THE CARD. The statement keeps the date as the view wrote
    # it, so it runs; the declaration names it, with the view's date as the default.
    dated: dict[str, str] = {}
    for m in list(measures):
        decl = m.get("declared")
        if not decl:
            continue
        try:
            decl["formula"], params = date_parameters(decl["formula"])
        except _Skip as exc:
            what = f"measure {m['name']!r}"
            view.notes.append(f"{what} {exc}; it is not read")
            dropped.add(what)
            measures.remove(m)
            continue
        if params:
            decl["parameters"] = params
            dated.update({p_["name"]: p_["default"] for p_ in params})
    if not measures:
        raise MetricViewError(f"{src}: the metric view declares no measure this reader reads"
                              + "".join(f"\n  {n}" for n in view.notes))

    facts["usage"] = [{
        "unit_id": f"databricks-mv::{name}::{m['name']}",
        "container": name,
        "container_kind": "perspective",
        "dashboards": [],
        "visual_id": m["name"],
        "visual_type": "metric_view_measure",
        "title": m["name"],
        # The unit IS the measure, so the measure's own comment is the unit's prose.
        # Untrusted author text: data only.
        "description": m.get("description"),
        "measures": [m["name"]],
        "columns": [],
        "filters": [],
        "sort": None, "top_n": None, "parameters": [],
        "source_fragments": {"widget": None, "dashboard": None, "prose": [name]},
        "_aggregations": {},
        "_siblings": [],
    } for m in measures]

    n_fields = sum(1 for cols in columns.values() for c in cols if c.get("role") == "dimension")
    head = (f"{shape} metric view {name!r} (specification {version or 'unstated'}): "
            f"{len(tables)} table(s), {n_fields} field(s), {len(measures)} measure(s) "
            f"({sum(1 for m in measures if m.get('declared'))} declared, "
            f"{len(view.unread) + sum(1 for w in dropped if w.startswith('measure '))} not read), "
            f"{len(view.relationships)} join(s)")
    reader_notes = [head, *view.notes, *notes]
    if cond is not None:
        reader_notes.append("the view's filter is written into every measure as "
                            f"FILTER (WHERE {_sql(cond)})")
    if dated:
        reader_notes.append("a fixed date is a parameter on the metric, with the view's date "
                            "as its default: "
                            + ", ".join(f"{k} = {v}" for k, v in sorted(dated.items())))
    if not any(t.get("primary_key") for t in tables[:1]):
        reader_notes.append("the view declares no key and none came with it: the grain of "
                            "each metric is the card author's to state")
    if not_carried["formats"]:
        reader_notes.append(f"not carried by the contract: {not_carried['formats']} format(s)")
    if doc.get("materialization"):
        reader_notes.append("not carried by the contract: the view's materialization")
    if _said(doc.get("comment")):
        reader_notes.append("not carried by the contract: the view's own comment")
    facts["_unresolved_published"] = []
    facts["_reader_notes"] = reader_notes
    return facts


# ── the reader ───────────────────────────────────────────────────────────────────────────

class MetricViewReader(FormulaCompiler):
    """The `databricks-metric-view` BI reader (`sockets.BIReader`), registered under
    `metrics_agent.readers` by this package.

    Reads a Databricks metric view (a saved `DESCRIBE TABLE EXTENDED ... AS JSON`, its YAML,
    or its `CREATE VIEW ... WITH METRICS` DDL) into the common `bi_facts` format,
    with what the view DECLARES about each measure in `measures[].declared` (lib/declared).
    Its measures are SQL, the core's own language, so it declares no other `languages`; they
    compile through the shared engine, which places a SQL measure in its model by role. A
    folder of views is several models, one per view (`models`)."""

    name = VENDOR
    suffixes = SUFFIXES
    languages: tuple[str, ...] = ()

    def detect(self, path: pathlib.Path) -> bool:
        """Whether ONE file is a metric view. A folder is never detected: it is read only
        when the run names this reader, and then through `models`."""
        path = pathlib.Path(path)
        if path.is_dir() or path.suffix.lower() not in SUFFIXES:
            return False
        try:
            doc, _extra, _shape = load(path)
        except MetricViewError:
            return False
        return is_metric_view(doc)

    def models(self, path: pathlib.Path) -> list[pathlib.Path]:
        """One path per view: a single file is itself, and a folder is every file in it that
        reads as a metric view with a measure. It never raises for a file it leaves out; a
        folder with none is refused by name when it is parsed."""
        return models(pathlib.Path(path))

    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]:
        """ONE view."""
        return parse(pathlib.Path(path), dialect=opts.get("dialect") or None)
