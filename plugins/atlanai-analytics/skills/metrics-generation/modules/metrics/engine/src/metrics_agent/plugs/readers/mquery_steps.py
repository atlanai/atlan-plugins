# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Power Query (M) steps -> the SQL the model table actually holds.

`mquery` says WHERE a table comes from. This module says WHAT the table is: a Power Query
expression is a pipeline of steps, and a step that keeps some rows (`Table.SelectRows`), adds
a computed column (`Table.AddColumn`), changes values (`Table.TransformColumnTypes` to a whole
number), groups (`Table.Group`), merges (`Table.NestedJoin`), appends (`Table.Combine`) or
de-duplicates (`Table.Distinct`) changes every number a visual shows. Compiled against the bare
warehouse table, those numbers were silently wrong.

So each table's M is parsed (a small M parser, below), evaluated step by step into a relational
tree, and the tree is written as ONE SQL statement over the warehouse that returns exactly the
rows and columns the model table holds. Or it is refused BY NAME: a step with no faithful SQL,
a source that is no warehouse (an Excel workbook, a file, a web address, data typed into the
model), native SQL built from an expression the reader cannot evaluate.

The result is the common format (`source_lineage.statement`, `.refusal`, `.reads`): no stage
reads M. Semantics are M's, per the Power Query M language specification:

* `=` and `<>` never return null: `null = null` is true and `null <> "a"` is true, so
  `[Region] <> "West"` KEEPS the rows whose region is null. `<`, `>`, `<=`, `>=` against null
  are null, and `Table.SelectRows` drops a row whose condition is null.
* `and`, `or`, `not` are three-valued, as in SQL.
* text comparison is ordinal: case-sensitive, a trailing space is a character.
* an `if` whose condition is null takes its `else` branch (seen live, Snowflake source with the
  steps folded: the null amounts fell in the last band).
* a conversion to a whole number (`Int64.Type`) rounds half away from zero, as the warehouse's
  own cast does (seen live, folded); `Currency.Type` keeps four decimals the same way.
  `Number.Round` rounds half to even, its documented default.
* join keys match when equal, null matching null.
* a cell that is an M error fails the whole refresh (seen live), so a refreshed model holds
  none: an error matters only where a step handles it (`try ... otherwise`,
  `Table.ReplaceErrorValues`, `Table.RemoveRowsWithErrors`). No connector folds a `try`:
  Power Query evaluates it, so inside one an `if` over null raises.
* a division by zero is infinity, minus infinity or NaN (seen live, evaluated by Power
  Query); folded to Snowflake it fails the refresh. `Number.IntegerDivide` by zero is NaN.
* `DateTime.LocalNow()` is the refresh's moment, UTC in the service (seen live); a week
  starts on Sunday.
* rows have an order only after a `Table.Sort` (and the steps that keep it): FillDown,
  AddIndexColumn, Distinct on some columns and Group with a comparer read it (seen live: the
  first row in the sort is kept).

Every statement is written in the dialect of the connector the table reads through: it runs on
that warehouse. A construct with no form in that dialect is refused by name.
"""
from __future__ import annotations

import datetime as _dt
import functools
import pathlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import yaml

# ── tokens ─────────────────────────────────────────────────────────────────────────────────

_KEYWORDS = {"let", "in", "each", "if", "then", "else", "and", "or", "not", "null", "true",
             "false", "meta", "type", "as", "is", "try", "otherwise", "error", "section",
             "shared"}
_PUNCT = ["=>", "<>", "<=", ">=", "??", "...", "..", "=", "<", ">", "+", "-", "*", "/", "&",
          "(", ")", "[", "]", "{", "}", ",", ";", "@", "?", "!"]


class MSyntaxError(ValueError):
    """The M text is outside what this parser reads."""


class _MError(Exception):
    """An M error raised while the reader evaluates a value (`error "..."`), caught by `try`."""


class Refused(Exception):
    """A step, source or expression with no faithful SQL. Always carries the reason.

    `hard`: refused whatever else the M holds. A soft refusal (a function the reader does not
    know, in a table with no step that changes rows or values) leaves the table to the
    lineage reader, which names where it comes from as before."""

    def __init__(self, reason: str, hard: bool = False):
        super().__init__(reason)
        self.hard = hard


@dataclass
class _Tok:
    kind: str          # ident, str, num, hash, kw, op, eof
    value: Any
    pos: int
    end: int


def _decode_escapes(s: str) -> str:
    from metrics_agent.plugs.readers.mquery import decode_m_escapes  # noqa: PLC0415
    return decode_m_escapes(s)


def tokenize(text: str) -> list[_Tok]:
    out: list[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == '"':
                    if text[j + 1:j + 2] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            if j >= n:
                raise MSyntaxError("an unterminated text literal")
            out.append(_Tok("str", _decode_escapes("".join(buf)), i, j + 1))
            i = j + 1
            continue
        if ch == "#":
            if text[i + 1:i + 2] == '"':
                j, buf = i + 2, []
                while j < n:
                    if text[j] == '"':
                        if text[j + 1:j + 2] == '"':
                            buf.append('"')
                            j += 2
                            continue
                        break
                    buf.append(text[j])
                    j += 1
                out.append(_Tok("ident", "".join(buf), i, j + 1))
                i = j + 1
                continue
            m = re.match(r"#[A-Za-z]+", text[i:])
            if not m:
                raise MSyntaxError(f"an unknown # form at {i}")
            out.append(_Tok("hash", m.group(0).lower(), i, i + len(m.group(0))))
            i += len(m.group(0))
            continue
        m = re.match(r"0[xX][0-9A-Fa-f]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", text[i:])
        if m and (ch.isdigit() or (ch == "." and text[i + 1:i + 2].isdigit())):
            lit = m.group(0)
            val: Any = int(lit, 16) if lit[:2].lower() == "0x" else (
                Decimal(lit) if any(c in lit for c in ".eE") else int(lit))
            out.append(_Tok("num", val, i, i + len(lit)))
            i += len(lit)
            continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", text[i:])
        if m:
            word = m.group(0)
            kind = "kw" if word in _KEYWORDS else "ident"
            out.append(_Tok(kind, word, i, i + len(word)))
            i += len(word)
            continue
        for p in _PUNCT:
            if text.startswith(p, i):
                out.append(_Tok("op", p, i, i + len(p)))
                i += len(p)
                break
        else:
            raise MSyntaxError(f"an unexpected character {ch!r}")
    out.append(_Tok("eof", None, n, n))
    return out


# ── the parser: M text -> a small AST of tuples ────────────────────────────────────────────
#
#   ("let", [(name, node)], body)      ("ident", name)         ("str"|"num"|"bool", value)
#   ("null",)                          ("list", [node])        ("record", [(key, node)])
#   ("field", base|None, name, opt)    ("item", base, node, opt)
#   ("call", fn, [node])               ("bin", op, l, r)       ("un", op, node)
#   ("if", c, a, b)                    ("fn", [params], body)  ("type", name)
#   ("hash", name, [node])             ("try", node, other)    ("error", node)

class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.t = tokenize(text)
        self.i = 0

    def peek(self, k: int = 0) -> _Tok:
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self) -> _Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def at(self, kind: str, value: Any = None, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok.kind == kind and (value is None or tok.value == value)

    def expect(self, kind: str, value: Any = None) -> _Tok:
        if not self.at(kind, value):
            tok = self.peek()
            raise MSyntaxError(f"expected {value or kind} at {tok.pos}, found {tok.value!r}")
        return self.next()

    def parse(self) -> tuple:
        if self.at("kw", "section"):
            raise MSyntaxError("a section document")
        node = self.expr()
        if not self.at("eof"):
            raise MSyntaxError(f"text after the expression at {self.peek().pos}")
        return node

    # expression := let | if | each | function | try | error | coalesce
    def expr(self) -> tuple:
        if self.at("kw", "let"):
            self.next()
            binds = []
            while True:
                name = self._gen_ident(stop={"="})
                self.expect("op", "=")
                binds.append((name, self.expr()))
                if self.at("op", ","):
                    self.next()
                    continue
                break
            self.expect("kw", "in")
            return ("let", binds, self.expr())
        if self.at("kw", "if"):
            self.next()
            c = self.expr()
            self.expect("kw", "then")
            a = self.expr()
            self.expect("kw", "else")
            return ("if", c, a, self.expr())
        if self.at("kw", "each"):
            self.next()
            return ("fn", ["_"], self.expr())
        if self.at("kw", "try"):
            self.next()
            body = self.expr()
            other = None
            if self.at("kw", "otherwise"):
                self.next()
                other = self.expr()
            return ("try", body, other)
        if self.at("kw", "error"):
            self.next()
            return ("error", self.expr())
        if self.at("op", "(") and self._is_function():
            return self._function()
        return self.coalesce()

    def _is_function(self) -> bool:
        depth, k = 0, 0
        while True:
            tok = self.peek(k)
            if tok.kind == "eof":
                return False
            if tok.kind == "op" and tok.value in "([{":
                depth += 1
            elif tok.kind == "op" and tok.value in ")]}":
                depth -= 1
                if depth == 0:
                    nxt = self.peek(k + 1)
                    if nxt.kind == "kw" and nxt.value == "as":
                        return True
                    return nxt.kind == "op" and nxt.value == "=>"
            k += 1

    def _function(self) -> tuple:
        self.expect("op", "(")
        params = []
        while not self.at("op", ")"):
            if self.at("ident", "optional"):
                self.next()
            params.append(self.expect("ident").value)
            if self.at("kw", "as"):
                self.next()
                self._type_body()
            if self.at("op", ","):
                self.next()
        self.expect("op", ")")
        if self.at("kw", "as"):
            self.next()
            self._type_body()
        self.expect("op", "=>")
        return ("fn", params, self.expr())

    def coalesce(self) -> tuple:
        left = self.logical_or()
        while self.at("op", "??"):
            self.next()
            left = ("bin", "??", left, self.logical_or())
        return left

    def logical_or(self) -> tuple:
        left = self.logical_and()
        while self.at("kw", "or"):
            self.next()
            left = ("bin", "or", left, self.logical_and())
        return left

    def logical_and(self) -> tuple:
        left = self.is_as()
        while self.at("kw", "and"):
            self.next()
            left = ("bin", "and", left, self.is_as())
        return left

    def is_as(self) -> tuple:
        left = self.equality()
        while self.at("kw", "as") or self.at("kw", "is"):
            op = self.next().value
            left = ("bin", op, left, ("type", self._type_body()))
        return left

    def equality(self) -> tuple:
        left = self.relational()
        while self.at("op", "=") or self.at("op", "<>"):
            op = self.next().value
            left = ("bin", op, left, self.relational())
        return left

    def relational(self) -> tuple:
        left = self.additive()
        while any(self.at("op", o) for o in ("<", ">", "<=", ">=")):
            op = self.next().value
            left = ("bin", op, left, self.additive())
        return left

    def additive(self) -> tuple:
        left = self.multiplicative()
        while any(self.at("op", o) for o in ("+", "-", "&")):
            op = self.next().value
            left = ("bin", op, left, self.multiplicative())
        return left

    def multiplicative(self) -> tuple:
        left = self.metadata()
        while self.at("op", "*") or self.at("op", "/"):
            op = self.next().value
            left = ("bin", op, left, self.metadata())
        return left

    def metadata(self) -> tuple:
        left = self.unary()
        while self.at("kw", "meta"):
            self.next()
            self.unary()
        return left

    def unary(self) -> tuple:
        if self.at("op", "-") or self.at("op", "+") or self.at("kw", "not"):
            op = self.next().value
            return ("un", op, self.unary())
        if self.at("kw", "type"):
            self.next()
            fields: list[str] = []
            name = self._type_body(fields)
            return ("type", name, tuple(fields)) if fields else ("type", name)
        return self.postfix(self.primary())

    def _type_body(self, fields: list[str] | None = None) -> str:
        """A type, read to its name: `nullable number` -> number, `table [...]` -> table.
        A table type's column names are put in `fields` when one is given."""
        if self.at("ident", "nullable"):
            self.next()
        if self.at("op", "[") or self.at("op", "{"):
            self._skip_balanced()
            return "record"
        if self.at("kw", "type"):
            self.next()
        tok = self.next()
        name = str(tok.value)
        if name == "table" and self.at("op", "[") and fields is not None:
            self._table_fields(fields)
        elif name in ("table", "function") and (self.at("op", "[") or self.at("op", "(")):
            self._skip_balanced()
            if name == "function" and self.at("kw", "as"):
                self.next()
                self._type_body()
        return name

    def _table_fields(self, fields: list[str]) -> None:
        """`[a = type, #"b c" = type, ...]`: the names before each `=`, the types skipped."""
        self.next()                                            # [
        while not self.at("op", "]"):
            if self.at("eof"):
                raise MSyntaxError("an unbalanced bracket")
            fields.append(self._gen_ident({"="}))
            self.next()                                        # =
            depth = 0
            while not (depth == 0 and self.peek().kind == "op"
                       and self.peek().value in (",", "]")):
                tok = self.next()
                if tok.kind == "eof":
                    raise MSyntaxError("an unbalanced bracket")
                if tok.kind == "op" and tok.value in "([{":
                    depth += 1
                elif tok.kind == "op" and tok.value in ")]}":
                    depth -= 1
            if self.at("op", ","):
                self.next()
        self.next()                                            # ]

    def _skip_balanced(self) -> None:
        depth = 0
        while True:
            tok = self.next()
            if tok.kind == "eof":
                raise MSyntaxError("an unbalanced bracket")
            if tok.kind == "op" and tok.value in "([{":
                depth += 1
            elif tok.kind == "op" and tok.value in ")]}":
                depth -= 1
                if depth == 0:
                    return

    def _gen_ident(self, stop: set[str]) -> str:
        """A generalised identifier (`Order Amount`, `#"x"`), up to a token in `stop`."""
        if self.at("ident") and self.peek().value and self.t[self.i].kind == "ident" and (
                self.peek(1).kind == "op" and self.peek(1).value in stop):
            return self.next().value
        start = self.peek().pos
        end = start
        while not (self.peek().kind == "op" and self.peek().value in stop) and not self.at("eof"):
            tok = self.next()
            if tok.kind == "ident" and self.text[tok.pos] == "#":
                return tok.value
            end = tok.end
        name = self.text[start:end].strip()
        if not name:
            raise MSyntaxError(f"a missing name at {start}")
        return name

    def _record_or_field(self) -> tuple:
        """`[` opens a record (`[a = 1]`), a field of `_` (`[Amount]`) or a projection."""
        depth, k = 0, 0
        is_record = False
        while True:
            tok = self.peek(k)
            if tok.kind == "eof":
                break
            if tok.kind == "op" and tok.value in "([{":
                depth += 1
            elif tok.kind == "op" and tok.value in ")]}":
                depth -= 1
                if depth == 0:
                    break
            elif depth == 1 and tok.kind == "op" and tok.value == "=":
                is_record = True
                break
            k += 1
        self.expect("op", "[")
        if is_record:
            items = []
            while not self.at("op", "]"):
                key = self._gen_ident(stop={"="})
                self.expect("op", "=")
                items.append((key, self.expr()))
                if self.at("op", ","):
                    self.next()
            self.expect("op", "]")
            return ("record", items)
        if self.at("op", "]"):
            self.next()
            return ("record", [])
        if self.at("op", "["):
            raise MSyntaxError("a column projection")
        name = self._gen_ident(stop={"]"})
        self.expect("op", "]")
        opt = False
        if self.at("op", "?"):
            self.next()
            opt = True
        return ("field", None, name, opt)

    def primary(self) -> tuple:
        tok = self.peek()
        if tok.kind == "str":
            self.next()
            return ("str", tok.value)
        if tok.kind == "num":
            self.next()
            return ("num", tok.value)
        if tok.kind == "kw" and tok.value in ("true", "false"):
            self.next()
            return ("bool", tok.value == "true")
        if tok.kind == "kw" and tok.value == "null":
            self.next()
            return ("null",)
        if tok.kind == "op" and tok.value == "@":
            self.next()
            return ("ident", self.expect("ident").value)
        if tok.kind == "ident":
            self.next()
            return ("ident", tok.value)
        if tok.kind == "hash":
            self.next()
            if tok.value in ("#infinity", "#nan", "#shared", "#sections"):
                return ("hash", tok.value, [])
            self.expect("op", "(")
            args = self._args(")")
            return ("hash", tok.value, args)
        if tok.kind == "op" and tok.value == "(":
            self.next()
            node = self.expr()
            self.expect("op", ")")
            return node
        if tok.kind == "op" and tok.value == "{":
            self.next()
            return ("list", self._args("}"))
        if tok.kind == "op" and tok.value == "[":
            return self._record_or_field()
        raise MSyntaxError(f"an unexpected {tok.value!r} at {tok.pos}")

    def _args(self, close: str) -> list[tuple]:
        args = []
        while not self.at("op", close):
            node = self.expr()
            if self.at("op", ".."):
                self.next()
                node = ("range", node, self.expr())
            args.append(node)
            if self.at("op", ","):
                self.next()
            elif not self.at("op", close):
                raise MSyntaxError(f"expected , or {close} at {self.peek().pos}")
        self.expect("op", close)
        return args

    def postfix(self, node: tuple) -> tuple:
        while True:
            if self.at("op", "("):
                self.next()
                node = ("call", node, self._args(")"))
            elif self.at("op", "{"):
                self.next()
                sel = self.expr()
                self.expect("op", "}")
                opt = False
                if self.at("op", "?"):
                    self.next()
                    opt = True
                node = ("item", node, sel, opt)
            elif self.at("op", "["):
                f = self._record_or_field()
                if f[0] != "field":
                    raise MSyntaxError("a record after a value")
                node = ("field", node, f[2], f[3])
            else:
                return node


def parse(text: str) -> tuple:
    return _Parser(text).parse()


def snippet(node: Any, limit: int = 80) -> str:
    """A short, readable rendering of an AST node, for a refusal."""
    def r(n: Any) -> str:
        if not isinstance(n, tuple):
            return str(n)
        k = n[0]
        if k == "ident":
            return n[1]
        if k == "str":
            return '"' + n[1] + '"'
        if k in ("num", "bool"):
            return str(n[1]).lower() if k == "bool" else str(n[1])
        if k == "null":
            return "null"
        if k == "call":
            return f"{r(n[1])}({', '.join(r(a) for a in n[2])})"
        if k == "field":
            return (r(n[1]) if n[1] else "") + f"[{n[2]}]"
        if k == "bin":
            return f"{r(n[2])} {n[1]} {r(n[3])}"
        if k == "un":
            return f"{n[1]} {r(n[2])}"
        if k == "fn":
            return "each " + r(n[2]) if n[1] == ["_"] else "(" + ", ".join(n[1]) + ") => " + r(n[2])
        if k == "list":
            return "{" + ", ".join(r(a) for a in n[1]) + "}"
        if k == "record":
            return "[" + ", ".join(f"{a} = {r(b)}" for a, b in n[1]) + "]"
        if k == "item":
            return f"{r(n[1])}{{{r(n[2])}}}"
        if k == "if":
            return f"if {r(n[1])} then {r(n[2])} else {r(n[3])}"
        if k == "hash":
            return f"{n[1]}({', '.join(r(a) for a in n[2])})"
        if k == "type":
            return f"type {n[1]}"
        return k
    s = r(node)
    return s if len(s) <= limit else s[:limit - 3] + "..."


# ── sources ────────────────────────────────────────────────────────────────────────────────

# What each warehouse's SQL looks like is data (`data/reader_dialects.yml`): no module keeps
# a list of engines.
_DIALECTS_FILE = pathlib.Path(__file__).parent / "data" / "reader_dialects.yml"


@functools.lru_cache(maxsize=1)
def _dialects() -> dict[str, Any]:
    doc = yaml.safe_load(_DIALECTS_FILE.read_text()) or {}
    if not all(isinstance(doc.get(k), t) for k, t in
               (("connectors", dict), ("round_half_even", dict), ("no_boolean", list),
                ("no_nulls_last", list))):
        raise ValueError(f"{_DIALECTS_FILE.name}: needs `connectors`, `round_half_even`, "
                         f"`no_boolean` and `no_nulls_last`")
    return doc


# A warehouse connector -> (the dialect its SQL is written in, the index of an argument that
# fixes the database, or None when the navigation starts at the database).
CONNECTORS: dict[str, tuple[str | None, int | None]] = {
    name: (row.get("dialect"), row.get("database_arg"))
    for name, row in _dialects()["connectors"].items()}

# Sources that are no warehouse: the label a refusal names them by. Matched on the function
# name; `Web.Contents` of a SharePoint or OneDrive address is named as such.
NON_WAREHOUSE: list[tuple[str, str]] = [
    # Most specific first: a label names what the rows ARE, not the wrapper that decodes them.
    # Rows written in the M itself (Table.FromList / FromRecords / FromRows, #table) are
    # evaluated (`_Eval.literal_rows`) and are not on this list; the compressed form Desktop's
    # Enter Data saves, and a table from a record of columns or a value, are.
    (r"Table\.FromValue", "data entered in the model itself"),
    (r"SharePoint\.\w+|OneDrive\.\w+", "a file on SharePoint or OneDrive"),
    (r"Excel\.(?:Workbook|CurrentWorkbook)", "an Excel workbook"),
    (r"Web\.(?:Contents|Page|BrowserContents)", "a web address"),
    (r"AzureStorage\.\w+|AzureDataLakeStorage\.\w+", "files in cloud storage"),
    (r"Csv\.Document|Lines\.From\w+|Parquet\.Document|Json\.Document|Xml\.(?:Tables|Document)"
     r"|Pdf\.Tables|File\.Contents|Folder\.(?:Files|Contents)|Hdfs\.\w+", "a file"),
    (r"OData\.Feed", "an OData feed"),
    (r"PowerPlatform\.Dataflows|PowerBI\.Dataflows|Dataflows\.\w+", "a dataflow"),
    (r"Python\.Execute|R\.Execute", "a script"),
    (r"Salesforce\.\w+|CommonDataService\.\w+|Dataverse\.\w+|Cds\.\w+|GoogleAnalytics\.\w+"
     r"|Exchange\.\w+|ActiveDirectory\.\w+|Access\.Database|Lakehouse\.Contents"
     r"|Fabric\.Warehouse",
     "a source the reader does not read as a warehouse table"),
]
_NON_WAREHOUSE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(" + "|".join(p for p, _ in NON_WAREHOUSE) + r")\s*\(")
_SP_URL = re.compile(r"sharepoint\.com|onedrive|1drv\.ms|my\.sharepoint", re.I)
_ENTERED_ROWS = re.compile(r"Json\.Document\s*\(\s*Binary\.Decompress\s*\(\s*Binary\.FromText"
                           r"\s*\((?=\s*\")")


def non_warehouse_source(m_text: str) -> str | None:
    """`"<label> (<function>)"` for the first non-warehouse source the M reads, else None."""
    from metrics_agent.plugs.readers.mquery import strip_m_comments  # noqa: PLC0415
    clean = strip_m_comments(m_text or "")
    # Rows Desktop's Enter Data saves in the M itself (a JSON document of the rows, deflated
    # and written as base64 text) are no file: `_Eval` decodes them while the model is read.
    clean = _ENTERED_ROWS.sub("(", clean)
    hits = [m.group(1) for m in _NON_WAREHOUSE_RE.finditer(clean)]
    if not hits:
        return None
    if any(h.startswith("Web.") for h in hits) and _SP_URL.search(clean):
        return f"a file on SharePoint or OneDrive ({next(h for h in hits if h.startswith('Web.'))})"
    for pat, label in NON_WAREHOUSE:
        for h in hits:
            if re.fullmatch(pat, h):
                return f"{label} ({h})"
    return f"a non-warehouse source ({hits[0]})"


# Step functions that change rows or values. With one present the table is not the bare
# warehouse table, so M the parser cannot read is refused rather than read as that table.
SEMANTIC_STEPS = re.compile(
    r"\bTable\.(?:SelectRows|AddColumn|Group|NestedJoin|Join|Combine|Distinct|FirstN|LastN"
    r"|Range|Skip|RemoveFirstN|RemoveLastN|ReplaceValue|FillDown|FillUp|AddIndexColumn"
    r"|TransformRows|TransformColumns|TransformColumnTypes|Pivot|Unpivot|UnpivotOtherColumns"
    r"|RemoveRowsWithErrors|ReplaceErrorValues|ExpandTableColumn|ExpandRecordColumn"
    r"|AggregateTableColumn|SplitColumn|CombineColumns|DuplicateColumn|AddRankColumn"
    r"|AlternateRows|RemoveMatchingRows|InsertRows|ReverseRows|MaxN|MinN|Max|Min)\s*\(")


# ── values ─────────────────────────────────────────────────────────────────────────────────

@dataclass
class Nav:
    """A connector, or a point in its navigation (`Source{[Name="DB"]}[Data]`)."""
    connector: str
    dialect: str | None
    fixed_db: str | None
    steps: list[dict] = field(default_factory=list)

    def where(self, target: bool = False) -> tuple[str | None, str | None, str | None]:
        """(database, schema, object) the navigation has reached. A bare chain is read from
        its end as a table (`target` False) or as the database (and schema) a native query
        runs against (`target` True)."""
        db, schema, obj = self.fixed_db, None, None
        names: list[str] = []
        for s in self.steps:
            kind = str(s.get("Kind") or "").lower()
            if s.get("Schema") is not None and s.get("Item") is not None:
                schema, obj = str(s["Schema"]), str(s["Item"])
                continue
            name = s.get("Name") if s.get("Name") is not None else s.get("Item")
            if name is None:
                continue
            if kind == "database":
                db = str(name)
            elif kind == "schema":
                schema = str(name)
            elif kind in ("table", "view"):
                obj = str(name)
            else:
                names.append(str(name))
        # A bare chain is positional: database (unless the connector fixed it), schema, object.
        order = (["schema"] if self.fixed_db is not None or db is not None else
                 ["database", "schema"])
        for i, nm in enumerate(names):
            slot = next((o for o in order if {"database": db, "schema": schema}[o] is None),
                        None)
            if slot == "database" and (target or i < len(names) - 1):
                db = nm
                order.remove("database")
            elif slot == "schema" and obj is None and (target or i < len(names) - 1):
                schema = nm
                order.remove("schema")
            elif obj is None:
                obj = nm
            else:
                raise Refused(f"navigation goes past the table {obj!r} to {nm!r}")
        return db, schema, obj


@dataclass
class Fn:
    params: list[str]
    body: tuple
    env: "Env"


@dataclass
class Builtin:
    name: str


@dataclass
class TypeV:
    name: str
    fields: tuple = ()      # a table type's column names, in order (`type table [a = _t]`)


@dataclass
class Const:
    name: str     # JoinKind.LeftOuter, Order.Ascending, Comparer.OrdinalIgnoreCase, ...


class Env:
    def __init__(self, binds: dict[str, tuple] | None, parent: "Env | None", ctx: "_Ctx"):
        self.binds = binds or {}
        self.parent = parent
        self.ctx = ctx
        self.memo: dict[str, Any] = {}

    def lookup(self, name: str) -> tuple["Env", tuple] | None:
        e: Env | None = self
        while e is not None:
            if name in e.binds:
                return e, e.binds[name]
            e = e.parent
        return None


# ── the relational tree ────────────────────────────────────────────────────────────────────

@dataclass
class Rel:
    op: str                      # base, native, filter, extend, rename, cast, remove, select,
    #                              transform, group, distinct, join, union, dup
    child: "Rel | None" = None
    args: dict = field(default_factory=dict)
    dialect: str | None = None
    semantic: bool = False

    def chain(self) -> list["Rel"]:
        out, r = [], self
        while r is not None:
            out.append(r)
            r = r.child
        return out


def _rel(op: str, child: Rel, semantic: bool = True, **args) -> Rel:
    return Rel(op, child, args, child.dialect, semantic or child.semantic)


# ── the evaluator ──────────────────────────────────────────────────────────────────────────

@dataclass
class _Ctx:
    queries: dict[str, str]          # shared queries by name -> M text
    params: dict[str, Any]           # parameter name -> its current value
    stack: list[str]                 # queries being evaluated (cycle check)
    parsed: dict[str, tuple] = field(default_factory=dict)
    reads: set[str] = field(default_factory=set)
    origin: str | None = None        # the M text the warehouse navigation is written in
    origin_params: set[str] = field(default_factory=set)
    depth: int = 0                   # custom function calls being evaluated


_IGNORED_ARG = object()
# How deep custom M functions may call each other (a recursive one is read to this depth).
_MAX_CALL_DEPTH = 32
# How many values List.Generate may make while the model is read: past it the list is refused
# by name (a condition that never turns false would loop for ever).
_MAX_GENERATED = 100_000
# How many bytes entered rows (Binary.Decompress) may inflate to while the model is read: a
# guard against a compressed bomb, far above what Enter Data holds (a few thousand cells).
_MAX_INFLATED = 16 * 1024 * 1024


class _Eval:
    def __init__(self, ctx: _Ctx):
        self.ctx = ctx

    # -- generic evaluation
    def ev(self, node: tuple, env: Env) -> Any:
        k = node[0]
        if k == "let":
            inner = Env({n: e for n, e in node[1]}, env, self.ctx)
            return self.ev(node[2], inner)
        if k == "ident":
            return self.ident(node[1], env)
        if k == "str":
            return node[1]
        if k == "num":
            return node[1]
        if k == "bool":
            return node[1]
        if k == "null":
            return None
        if k == "list":
            return [self.ev(a, env) for a in node[1]]
        if k == "record":
            return {key: self.ev(v, env) for key, v in node[1]}
        if k == "type":
            return TypeV(node[1], tuple(node[2]) if len(node) > 2 else ())
        if k == "fn":
            return Fn(node[1], node[2], env)
        if k == "hash":
            return self.hash(node, env)
        if k == "item":
            return self.item(node, env)
        if k == "field":
            # `[f]` alone is a field of `_`, the value an `each` function is applied to.
            base = (self.ev(node[1], env) if node[1] is not None
                    else self.ev(("ident", "_"), env) if env.lookup("_") is not None else None)
            if isinstance(base, Nav):
                if node[2] == "Data":
                    return base
                raise Refused(f"navigation reads the field [{node[2]}]")
            if isinstance(base, dict):
                if node[2] in base:
                    return base[node[2]]
                if node[3]:
                    return None
                raise Refused(f"a record has no field [{node[2]}]")
            raise Refused(f"the step {snippet(node)} reads a field of a value the reader "
                          f"cannot evaluate")
        if k == "val":
            return node[1]
        if k == "call":
            return self.call(node, env)
        if k == "bin":
            return self.binop(node, env)
        if k == "un":
            v = self.ev(node[2], env)
            if node[1] == "not":
                return None if v is None else (not v)
            if node[1] == "-":
                return None if v is None else -v
            return v
        if k == "if":
            c = self.ev(node[1], env)
            if not isinstance(c, bool):
                raise Refused(f"an if over {snippet(node[1])}, which is not a logical value")
            return self.ev(node[2] if c else node[3], env)
        if k == "try":
            # Evaluated while the model is read (a parameter, native SQL, a step argument): an
            # error of the body is the `otherwise` value; a record (`try x` alone) is not one.
            try:
                return self.ev(node[1], env)
            except _MError:
                if node[2] is None:
                    raise Refused(f"error handling that returns a record ({snippet(node)})") \
                        from None
                return self.ev(node[2], env)
        if k == "error":
            raise _MError(snippet(node[1]))
        raise Refused(f"the M expression {snippet(node)}")

    def ident(self, name: str, env: Env) -> Any:
        hit = env.lookup(name)
        if hit is not None:
            where, node = hit
            if name not in where.memo:
                where.memo[name] = _IGNORED_ARG      # a binding that reads itself
                where.memo[name] = self.ev(node, where)
            val = where.memo[name]
            if val is _IGNORED_ARG:
                raise Refused(f"the step {name!r} refers to itself")
            return val
        if name in self.ctx.params:
            self.ctx.origin_params.add(name)
            return self.ctx.params[name]
        if name in self.ctx.queries:
            return self.query(name)
        if name in ("JoinKind.Inner", "JoinKind.LeftOuter", "JoinKind.RightOuter",
                    "JoinKind.FullOuter", "JoinKind.LeftAnti", "JoinKind.RightAnti",
                    "Order.Ascending", "Order.Descending", "Comparer.Ordinal",
                    "Comparer.OrdinalIgnoreCase", "Comparer.FromCulture", "GroupKind.Local",
                    "GroupKind.Global", "MissingField.Ignore", "MissingField.UseNull",
                    "MissingField.Error", "RoundingMode.ToEven", "RoundingMode.AwayFromZero",
                    "RoundingMode.Up", "RoundingMode.Down", "RoundingMode.TowardZero",
                    "JoinAlgorithm.Dynamic", "JoinAlgorithm.PairwiseHash",
                    "JoinAlgorithm.SortMerge", "JoinAlgorithm.LeftHash",
                    "JoinAlgorithm.RightHash", "JoinAlgorithm.LeftIndex",
                    "JoinAlgorithm.RightIndex", "JoinSide.Left", "JoinSide.Right",
                    "Day.Sunday", "Day.Monday", "Day.Tuesday", "Day.Wednesday", "Day.Thursday",
                    "Day.Friday", "Day.Saturday", "Occurrence.First", "Occurrence.Last",
                    "Occurrence.All", "Replacer.ReplaceValue", "Replacer.ReplaceText",
                    "BinaryEncoding.Base64", "BinaryEncoding.Hex", "Compression.Deflate",
                    "Compression.GZip"):
            return Const(name)
        if re.fullmatch(r"[A-Z][A-Za-z0-9]*\.Type", name):
            return TypeV(name)
        if "." in name:
            return Builtin(name)
        raise Refused(f"the step reads {name!r}, which is neither a step, a query nor a "
                      f"parameter of the model")

    def query(self, name: str) -> Any:
        if name in self.ctx.stack:
            raise Refused("queries reference each other in a cycle: "
                          + " -> ".join([*self.ctx.stack, name]), hard=True)
        if name not in self.ctx.parsed:
            try:
                self.ctx.parsed[name] = parse(self.ctx.queries[name])
            except MSyntaxError as exc:
                raise Refused(f"the query {name!r} it reads is Power Query the reader cannot "
                              f"parse ({exc})", hard=True) from None
        self.ctx.stack.append(name)
        try:
            val = self.ev(self.ctx.parsed[name], Env({}, None, self.ctx))
        finally:
            self.ctx.stack.pop()
        if isinstance(val, Fn):
            return val
        return val

    def hash(self, node: tuple, env: Env) -> Any:
        name = node[1]
        args = [self.ev(a, env) for a in node[2]]
        try:
            if name == "#date":
                return _dt.date(*(int(a) for a in args))
            if name == "#datetime":
                return _dt.datetime(*(int(a) for a in args[:5]), int(Decimal(args[5])))
        except (TypeError, ValueError):
            raise Refused(f"{snippet(node)} is not a date the reader can evaluate") from None
        if name == "#table" and len(args) == 2:
            cols = args[0]
            if isinstance(cols, TypeV):
                if not cols.fields:
                    raise Refused("#table with a table type whose columns are not written")
                cols = list(cols.fields)
            return self.literal_rows(cols, args[1], node)
        raise Refused(f"the M value {name}")

    # -- rows written in the M itself
    def literal_rows(self, columns: Any, rows: Any, node: tuple) -> Rel:
        """A table of rows the M holds (each a list of values, or a record): the statement
        of its literal rows, in order. Every value must be one the reader evaluated (a
        number, text, a logical, a date, null)."""
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise Refused(f"{snippet(node)} names its columns with an expression the reader "
                          f"cannot evaluate")
        if not isinstance(rows, list):
            raise Refused(f"{snippet(node)}: its rows are not a list the reader evaluated")
        out: list[list] = []
        for r in rows:
            if isinstance(r, dict):
                r = [r.get(c) for c in columns]
            if not isinstance(r, list) or len(r) != len(columns):
                raise Refused(f"{snippet(node)}: a row with another number of values than "
                              f"its {len(columns)} column(s)")
            for v in r:
                if not (v is None or isinstance(v, (bool, int, Decimal, str, _dt.date))):
                    raise Refused(f"{snippet(node)}: a value that is not a literal")
            out.append(list(r))
        from metrics_agent.plugs.readers import mquery as _mq  # noqa: PLC0415
        dialect = _mq._RUN_DIALECT.get() or None
        # Each column under its exact name, quoted: the steps after it read it as `"name"`,
        # and the statement's own output names are written where it ends (`read_table`).
        names = [_ident(c) for c in columns]
        if not out:
            sel = ", ".join(f"NULL AS {n}" for n in names)
            sql = f"SELECT *\nFROM   (SELECT {sel}) z\nWHERE  1 = 0"
        else:
            sql = "\nUNION ALL\n".join(
                "SELECT " + ", ".join(_sql_literal(v, dialect) + (f" AS {n}" if i == 0 else "")
                                      for v, n in zip(r, names))
                for i, r in enumerate(out))
        return Rel("native", None, {"sql": sql, "db": None, "schema": None, "literal": True,
                                    "columns": list(columns), "rows": out}, dialect, False)

    def f_List_Generate(self, args, env, node):
        """List.Generate(initial, condition, next[, selector]): evaluated while the model is
        read, value by value, as M does: the initial value, then `next` of the last while
        `condition` holds; each kept value passed through `selector`."""
        if len(args) not in (3, 4):
            raise Refused(f"{snippet(node)} takes three or four functions")
        fns = [self.ev(a, env) for a in args]
        if not all(isinstance(f, Fn) for f in fns):
            raise Refused(f"{snippet(node)}: an argument that is not a function")
        start, cond, step = fns[:3]
        sel = fns[3] if len(fns) == 4 else None
        state = self.invoke(start, [], node)
        out = []
        while True:
            ok = self.invoke(cond, [state], node)
            if ok is not True:
                if ok is False or ok is None:
                    break
                raise Refused(f"{snippet(node)}: its condition is not a logical value")
            out.append(self.invoke(sel, [state], node) if sel is not None else state)
            if len(out) > _MAX_GENERATED:
                raise Refused(f"{snippet(node)} makes more than {_MAX_GENERATED} values "
                              f"while the model is read", hard=True)
            state = self.invoke(step, [state], node)
        return out

    def f_Table_FromList(self, args, env, node):
        """Table.FromList(list, Splitter.SplitByNothing(), columns): one row per value."""
        if not args:
            raise Refused(f"{snippet(node)} has no list")
        values = self.ev(args[0], env)
        if not isinstance(values, list):
            raise Refused(f"{snippet(node)}: its list is not one the reader evaluated")
        split = args[1] if len(args) > 1 else ("null",)
        if not (split[0] == "null" or (split[0] == "call" and split[1] == (
                "ident", "Splitter.SplitByNothing") and not split[2])):
            if not all(isinstance(v, list) for v in values):
                raise Refused(f"{snippet(node)} splits its values with "
                              f"{snippet(split)}")
        cols = self.ev(args[2], env) if len(args) > 2 and args[2][0] != "null" else None
        if isinstance(cols, int):
            cols = [f"Column{i + 1}" for i in range(cols)]
        if cols is None:
            cols = ["Column1"]
        rows = [v if isinstance(v, list) and len(cols) > 1 else [v] for v in values]
        return self.literal_rows(cols, rows, node)

    def f_Table_FromRecords(self, args, env, node):
        if not args:
            raise Refused(f"{snippet(node)} has no records")
        recs = self.ev(args[0], env)
        if not isinstance(recs, list) or not all(isinstance(r, dict) for r in recs):
            raise Refused(f"{snippet(node)}: its records are not ones the reader evaluated")
        cols = self.ev(args[1], env) if len(args) > 1 and args[1][0] != "null" else None
        if cols is None or isinstance(cols, TypeV):
            cols = list(recs[0].keys()) if recs else []
        return self.literal_rows(cols, recs, node)

    def f_Table_FromRows(self, args, env, node):
        if len(args) < 2:
            raise Refused(f"{snippet(node)} names no columns")
        rows = self.ev(args[0], env)
        cols = self.ev(args[1], env)
        if isinstance(cols, int):
            cols = [f"Column{i + 1}" for i in range(cols)]
        if isinstance(cols, TypeV) and cols.fields:
            # Enter Data: `let _t = ... in type table [Region = _t, ...]`.
            cols = list(cols.fields)
        return self.literal_rows(cols, rows, node)

    def f_Table_FromColumns(self, args, env, node):
        """Table.FromColumns(lists[, columns]): the i-th value of each list is row i (a
        shorter list gives null there)."""
        if not args:
            raise Refused(f"{snippet(node)} has no lists")
        lists = self.ev(args[0], env)
        if not isinstance(lists, list) or not all(isinstance(x, list) for x in lists):
            raise Refused(f"{snippet(node)}: its lists are not ones the reader evaluated")
        cols = self.ev(args[1], env) if len(args) > 1 and args[1][0] != "null" else None
        if isinstance(cols, TypeV) and cols.fields:
            cols = list(cols.fields)
        if isinstance(cols, int) or cols is None:
            cols = [f"Column{i + 1}" for i in range(cols if isinstance(cols, int)
                                                    else len(lists))]
        n = max((len(x) for x in lists), default=0)
        rows = [[x[i] if i < len(x) else None for x in lists] for i in range(n)]
        return self.literal_rows(cols, rows, node)

    def f_Binary_FromText(self, args, env, node):
        """Binary.FromText(text[, BinaryEncoding.Base64 | Hex]): the bytes the text writes."""
        text = self.ev(args[0], env) if args else None
        enc = self.ev(args[1], env) if len(args) > 1 else Const("BinaryEncoding.Base64")
        if not isinstance(text, str) or not isinstance(enc, Const):
            raise Refused(f"{snippet(node)}: text the reader cannot evaluate")
        import base64  # noqa: PLC0415
        import binascii  # noqa: PLC0415
        try:
            if enc.name == "BinaryEncoding.Base64":
                return base64.b64decode(text, validate=True)
            if enc.name == "BinaryEncoding.Hex":
                return bytes.fromhex(text)
        except (binascii.Error, ValueError):
            raise Refused(f"{snippet(node)}: text that is not {enc.name}", hard=True) from None
        raise Refused(f"{snippet(node)}: the encoding {enc.name}")

    def f_Binary_Decompress(self, args, env, node):
        """Binary.Decompress(binary, Compression.Deflate | GZip): the bytes inflated, capped
        at `_MAX_INFLATED` (a model's entered rows are small)."""
        data = self.ev(args[0], env) if args else None
        kind = self.ev(args[1], env) if len(args) > 1 else None
        if not isinstance(data, bytes) or not isinstance(kind, Const):
            raise Refused(f"{snippet(node)}: bytes the reader cannot evaluate")
        import zlib  # noqa: PLC0415
        wbits = {"Compression.Deflate": -15, "Compression.GZip": 31}.get(kind.name)
        if wbits is None:
            raise Refused(f"{snippet(node)}: the compression {kind.name}")
        d = zlib.decompressobj(wbits)
        try:
            out = d.decompress(data, _MAX_INFLATED + 1)
        except zlib.error:
            raise Refused(f"{snippet(node)}: bytes that do not inflate", hard=True) from None
        if len(out) > _MAX_INFLATED:
            raise Refused(f"{snippet(node)} inflates past {_MAX_INFLATED} bytes", hard=True)
        return out

    def f_Json_Document(self, args, env, node):
        """Json.Document(bytes or text) of rows the M itself holds (Enter Data): lists,
        records, text, numbers, logicals and nulls."""
        data = self.ev(args[0], env) if args else None
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise Refused(f"{snippet(node)}: bytes that are not UTF-8", hard=True) from None
        if not isinstance(data, str):
            raise Refused(f"{snippet(node)}: a document the reader cannot evaluate")
        import json  # noqa: PLC0415
        try:
            return json.loads(data, parse_float=Decimal, parse_int=int)
        except ValueError:
            raise Refused(f"{snippet(node)}: text that is not JSON", hard=True) from None

    def item(self, node: tuple, env: Env) -> Any:
        base = self.ev(node[1], env)
        sel = self.ev(node[2], env)
        if isinstance(base, Nav) and isinstance(sel, dict):
            return Nav(base.connector, base.dialect, base.fixed_db, [*base.steps, sel])
        if isinstance(base, list) and isinstance(sel, int):
            if 0 <= sel < len(base):
                return base[sel]
            raise Refused(f"{snippet(node)} reads past the end of a list")
        raise Refused(f"the step {snippet(node)} selects from a value the reader cannot "
                      f"evaluate")

    def binop(self, node: tuple, env: Env) -> Any:
        op = node[1]
        a = self.ev(node[2], env)
        if op == "and" and a is False:
            return False
        if op == "or" and a is True:
            return True
        if op in ("as", "is"):
            return a
        b = self.ev(node[3], env)
        if op == "&":
            if a is None or b is None:
                return None
            if isinstance(a, str) and isinstance(b, str):
                return a + b
            if isinstance(a, list) and isinstance(b, list):
                return a + b
            raise Refused(f"{snippet(node)} joins values that are not both text")
        if op == "??":
            return b if a is None else a
        if op in ("=", "<>"):
            eq = a == b
            return eq if op == "=" else not eq
        if a is None or b is None:
            return None
        try:
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return Decimal(a) / Decimal(b)
            if op in ("<", ">", "<=", ">="):
                return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
            if op == "and":
                return bool(a) and bool(b)
            if op == "or":
                return bool(a) or bool(b)
        except (TypeError, ArithmeticError):
            pass
        raise Refused(f"the expression {snippet(node)}")

    # -- calls
    def call(self, node: tuple, env: Env) -> Any:
        fn = self.ev(node[1], env)
        args = node[2]
        if isinstance(fn, Fn):
            return self.invoke(fn, [self.ev(a, env) for a in args], node)
        if not isinstance(fn, Builtin):
            raise Refused(f"the call {snippet(node)}")
        name = fn.name
        handler = getattr(self, "f_" + name.replace(".", "_"), None)
        if handler is not None:
            return handler(args, env, node)
        if name in CONNECTORS:
            return self.connector(name, args, env)
        hit = non_warehouse_source(name + "(")
        if hit:
            raise Refused(f"its rows come from {hit}, not a warehouse table, so no SQL is "
                          f"faithful to it")
        if name.startswith("Table."):
            raise Refused(f"the Power Query step {name} has no SQL the reader can write",
                          hard=True)
        raise Refused(f"the M function {name} has no SQL the reader can write")

    def invoke(self, fn: Fn, vals: list[Any], node: tuple) -> Any:
        """A custom M function (`(t as table) => ...`, let-bound or a shared query) applied to
        values: its body, evaluated with its parameters bound (a missing optional one null)."""
        if len(vals) > len(fn.params):
            raise Refused(f"{snippet(node)} passes more arguments than the function takes")
        if self.ctx.depth >= _MAX_CALL_DEPTH:
            raise Refused(f"{snippet(node)} calls functions deeper than the reader reads "
                          f"(a recursive function)", hard=True)
        binds = {p: ("val", vals[i] if i < len(vals) else None)
                 for i, p in enumerate(fn.params)}
        self.ctx.depth += 1
        try:
            return self.ev(fn.body, Env(binds, fn.env, self.ctx))
        finally:
            self.ctx.depth -= 1

    def connector(self, name: str, args: list[tuple], env: Env) -> Nav:
        dialect, db_arg = CONNECTORS[name]
        vals = []
        for a in args[:2]:
            try:
                vals.append(self.ev(a, env))
            except Refused:
                vals.append(None)
        db = vals[db_arg] if db_arg is not None and db_arg < len(vals) else None
        if db_arg is not None and not isinstance(db, str):
            raise Refused(f"{name} names its database through an expression the reader "
                          f"cannot evaluate")
        opt_at = (db_arg + 1) if db_arg is not None else 2
        if len(args) > opt_at and args[opt_at][0] == "record":
            keys = {k for k, _ in args[opt_at][1]}
            if "Query" in keys:
                # A native query given as the connector's own option (`[Query="SELECT …"]`).
                q = dict(args[opt_at][1])["Query"]
                try:
                    sql = self.ev(q, env)
                except Refused as exc:
                    raise Refused(f"its native SQL is built from an expression the reader "
                                  f"cannot evaluate ({exc})", hard=True) from None
                if not isinstance(sql, str):
                    raise Refused("its native SQL is built from an expression the reader "
                                  "cannot evaluate", hard=True)
                return Rel("native", None, {"sql": sql, "db": db, "schema": None}, dialect,
                           False)
        return Nav(name, dialect, db)

    def table(self, node: tuple, env: Env) -> Rel:
        v = self.ev(node, env)
        return self.as_rel(v, node)

    def as_rel(self, v: Any, node: tuple | None = None) -> Rel:
        if isinstance(v, Rel):
            return v
        if isinstance(v, Nav):
            db, schema, obj = v.where()
            if not obj:
                raise Refused(f"the navigation {snippet(node) if node else ''} stops before a "
                              f"table")
            name = ".".join(p for p in (db, schema, obj) if p)
            self.ctx.reads.add(name.lower())
            return Rel("base", None, {"table": name, "db": db, "schema": schema, "object": obj},
                       v.dialect, False)
        raise Refused(f"the step {snippet(node) if node else ''} is not a table the reader "
                      f"can evaluate")

    def lam(self, node: tuple, env: Env) -> tuple[str, tuple, Env]:
        """An `each` / `(x) =>` argument: (its parameter, its body, the env it closes over)."""
        if node[0] == "ident":
            v = self.ev(node, env)
            if isinstance(v, Fn) and len(v.params) == 1:
                return v.params[0], v.body, v.env
            if isinstance(v, Builtin):
                return "_", ("call", ("ident", v.name), [("ident", "_")]), env
        if node[0] == "fn" and len(node[1]) == 1:
            return node[1][0], node[2], env
        raise Refused(f"the step argument {snippet(node)} is not a one-argument function")

    def names(self, node: tuple, env: Env) -> list[str]:
        v = self.ev(node, env)
        if isinstance(v, str):
            return [v]
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return list(v)
        raise Refused(f"the column list {snippet(node)} is not a list of names")

    # -- connectors' own steps and native SQL
    def f_Value_NativeQuery(self, args, env, node):
        if len(args) < 2:
            raise Refused("Value.NativeQuery without a query")
        target = self.ev(args[0], env)
        if not isinstance(target, Nav):
            raise Refused("Value.NativeQuery runs against a target the reader cannot evaluate")
        try:
            sql = self.ev(args[1], env)
        except Refused as exc:
            raise Refused(f"its native SQL is built from an expression the reader cannot "
                          f"evaluate ({exc})", hard=True) from None
        if not isinstance(sql, str):
            raise Refused(f"its native SQL is built from an expression the reader cannot "
                          f"evaluate: {snippet(args[1])}", hard=True)
        if len(args) >= 3 and args[2][0] != "null":
            try:
                params = self.ev(args[2], env)
            except Refused as exc:
                raise Refused(f"Value.NativeQuery binds parameters the reader cannot evaluate "
                              f"({exc})", hard=True) from None
            sql = bind_parameters(sql, params, target.dialect)
        db, schema, _obj = target.where(target=True)
        return Rel("native", None, {"sql": sql, "db": db, "schema": schema},
                   target.dialect, False)

    # -- text functions (used to build native SQL and parameter values)
    def _scalar_args(self, args, env):
        return [self.ev(a, env) for a in args]

    def f_Text_From(self, args, env, node):
        v = self._scalar_args(args, env)[0]
        if v is None:
            return None
        return _m_text(v, node)

    def f_Number_ToText(self, args, env, node):
        vals = self._scalar_args(args, env)
        if len(vals) > 1 and vals[1] is not None:
            raise Refused(f"{snippet(node)} formats a number with a format string")
        return None if vals[0] is None else _m_text(vals[0], node)

    def f_Date_ToText(self, args, env, node):
        vals = self._scalar_args(args, env)
        d = vals[0]
        fmt = vals[1] if len(vals) > 1 else None
        if isinstance(fmt, dict):
            fmt = fmt.get("Format")
        if d is None:
            return None
        if not isinstance(d, _dt.date) or fmt not in ("yyyy-MM-dd", "yyyyMMdd", "yyyy/MM/dd"):
            raise Refused(f"{snippet(node)} formats a date the reader cannot reproduce")
        return d.strftime(fmt.replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d"))

    f_DateTime_ToText = f_Date_ToText

    def f_Text_Replace(self, args, env, node):
        s, old, new = self._scalar_args(args, env)[:3]
        if not all(isinstance(x, str) for x in (s, old, new)):
            raise Refused(f"{snippet(node)} replaces text the reader cannot evaluate")
        return s.replace(old, new)

    def f_Text_Combine(self, args, env, node):
        vals = self._scalar_args(args, env)
        parts = vals[0]
        sep = vals[1] if len(vals) > 1 and vals[1] is not None else ""
        if not isinstance(parts, list) or not all(isinstance(p, (str, type(None)))
                                                  for p in parts):
            raise Refused(f"{snippet(node)} combines values the reader cannot evaluate")
        return sep.join(p for p in parts if p is not None)

    def f_Text_Upper(self, args, env, node):
        v = self._scalar_args(args, env)[0]
        return v.upper() if isinstance(v, str) else None

    def f_Text_Lower(self, args, env, node):
        v = self._scalar_args(args, env)[0]
        return v.lower() if isinstance(v, str) else None

    def f_Text_Proper(self, args, env, node):
        """Each word's first letter upper case and the rest lower case; a word starts after
        any character that is not a letter."""
        v = self._scalar_args(args, env)[0]
        if not isinstance(v, str):
            return None
        out, prev = [], False
        for ch in v:
            out.append(ch.lower() if prev else ch.upper())
            prev = ch.isalpha()
        return "".join(out)

    def f_Text_Trim(self, args, env, node):
        vals = self._scalar_args(args, env)
        if len(vals) > 1:
            raise Refused(f"{snippet(node)} trims characters the reader does not evaluate")
        return vals[0].strip(" ") if isinstance(vals[0], str) else None

    def f_Text_Start(self, args, env, node):
        v, n = self._scalar_args(args, env)[:2]
        return v[:int(n)] if isinstance(v, str) and isinstance(n, int) else None

    def f_Text_End(self, args, env, node):
        v, n = self._scalar_args(args, env)[:2]
        return (v[-int(n):] if n else "") if isinstance(v, str) and isinstance(n, int) else None

    def f_DateTime_LocalNow(self, args, env, node):
        raise Refused(f"{snippet(node)} depends on when the model was refreshed")

    f_DateTime_FixedLocalNow = f_DateTime_LocalNow
    f_DateTimeZone_LocalNow = f_DateTime_LocalNow
    f_DateTimeZone_UtcNow = f_DateTime_LocalNow
    f_DateTimeZone_FixedUtcNow = f_DateTime_LocalNow
    f_Date_From = None  # set below: Date.From of a literal

    # -- table steps
    def f_Table_SelectRows(self, args, env, node):
        t = self.table(args[0], env)
        p, body, cenv = self.lam(args[1], env)
        return _rel("filter", t, pred=(p, body, cenv))

    def f_Table_AddColumn(self, args, env, node):
        t = self.table(args[0], env)
        name = self.ev(args[1], env)
        if not isinstance(name, str):
            raise Refused(f"Table.AddColumn names its column with {snippet(args[1])}")
        p, body, cenv = self.lam(args[2], env)
        return _rel("extend", t, name=name, expr=(p, body, cenv))

    def f_Table_DuplicateColumn(self, args, env, node):
        t = self.table(args[0], env)
        src, new = self.ev(args[1], env), self.ev(args[2], env)
        return _rel("dup", t, src=src, new=new)

    def f_Table_RenameColumns(self, args, env, node):
        t = self.table(args[0], env)
        pairs = self.ev(args[1], env)
        if isinstance(pairs, list) and pairs and all(isinstance(x, str) for x in pairs):
            pairs = [pairs]
        if not (isinstance(pairs, list) and all(
                isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x)
                for x in pairs)):
            raise Refused(f"Table.RenameColumns with {snippet(args[1])}")
        return _rel("rename", t, semantic=False, pairs=[(a, b) for a, b in pairs])

    def f_Table_PrefixColumns(self, args, env, node):
        t = self.table(args[0], env)
        prefix = self.ev(args[1], env)
        if not isinstance(prefix, str):
            raise Refused(f"Table.PrefixColumns with {snippet(args[1])}")
        return _rel("prefix", t, semantic=False, prefix=prefix)

    def f_Table_TransformColumnNames(self, args, env, node):
        """Every column renamed by a function of its name. The model names the columns the
        function made; each is read back from the warehouse column the function maps to it
        (`preimage`), a name the function is evaluated on to check."""
        t = self.table(args[0], env)
        if len(args) < 2:
            raise Refused("Table.TransformColumnNames without a function")
        if len(args) > 2 and args[2][0] != "null":
            raise Refused(f"Table.TransformColumnNames with options ({snippet(args[2])})")
        return _rel("rename_fn", t, semantic=False, fn=self.lam(args[1], env))

    def rename_by(self, fn: tuple, name: str) -> str:
        p, body, env = fn
        try:
            v = self.ev(body, Env({p: ("val", name)}, env, self.ctx))
        except _MError:
            raise Refused(f"Table.TransformColumnNames' function raises an error on the column "
                          f"{name!r}") from None
        if not isinstance(v, str):
            raise Refused(f"Table.TransformColumnNames' function does not return a name for "
                          f"{name!r}")
        return v

    def preimage(self, fn: tuple, out: str, dialect: str | None) -> str:
        """The column `fn` renames to `out`: candidates undo each text step the function holds
        (a replacement, a prefix or suffix, a change of case), and the one `fn` maps to `out`
        is kept, preferring the spelling the warehouse folds an unquoted name to."""
        p, body, env = fn
        swaps: list[tuple[str, str]] = []
        affixes: list[str] = []

        def walk(n: Any) -> None:
            if isinstance(n, tuple) and n:
                if n[0] == "call" and n[1] == ("ident", "Text.Replace") and len(n[2]) == 3:
                    try:
                        old, new = self.ev(n[2][1], env), self.ev(n[2][2], env)
                    except (Refused, _MError):
                        old = new = None
                    if isinstance(old, str) and isinstance(new, str):
                        swaps.append((old, new))
                if n[0] == "bin" and n[1] == "&":
                    for side in (n[2], n[3]):
                        if side[0] == "str":
                            affixes.append(side[1])
                for x in n[1:]:
                    walk(x)
            elif isinstance(n, list):
                for x in n:
                    walk(x)
        walk(body)
        cands = {out}
        for _round in range(3):
            more = set(cands)
            for c in cands:
                more |= {c.upper(), c.lower()}
                for old, new in swaps:
                    if new:
                        more.add(c.replace(new, old))
                for a in affixes:
                    if a and c.startswith(a):
                        more.add(c[len(a):])
                    if a and c.endswith(a):
                        more.add(c[:-len(a)])
            cands = more
        hits = []
        for c in sorted(cands):
            try:
                if self.rename_by(fn, c) == out:
                    hits.append(c)
            except Refused:
                continue
        if not hits:
            raise Refused(f"Table.TransformColumnNames: no column name the reader can find is "
                          f"renamed to {out!r} by {snippet(body)}")
        # A name the function leaves as it is would need no renaming: the warehouse column is
        # one the function changes, in the case an unquoted name folds to, with the steps
        # undone (the replaced text back, the prefix off).
        fold = _IDENT_FOLD.get(str(dialect or "").lower())
        hits.sort(key=lambda c: (c == out,
                                 not (fold == "upper" and c == c.upper()
                                      or fold == "lower" and c == c.lower()),
                                 -sum(c.count(o) for o, _n in swaps if o), len(c), c))
        return hits[0]

    def f_Table_RemoveColumns(self, args, env, node):
        t = self.table(args[0], env)
        return _rel("remove", t, semantic=False, names=self.names(args[1], env))

    def f_Table_SelectColumns(self, args, env, node):
        t = self.table(args[0], env)
        if len(args) > 2:
            mf = self.ev(args[2], env)
            if isinstance(mf, Const) and mf.name != "MissingField.Error":
                raise Refused("Table.SelectColumns names columns that may be missing")
        return _rel("select", t, semantic=False, names=self.names(args[1], env))

    def f_Table_ReorderColumns(self, args, env, node):
        return self.table(args[0], env)

    def f_Table_Buffer(self, args, env, node):
        return self.table(args[0], env)

    f_Table_StopFolding = f_Table_Buffer
    f_Table_ConformToPageReader = f_Table_Buffer

    def f_Table_Sort(self, args, env, node):
        # Row order does not reach a model table; only a step that keeps the first rows after
        # it (Table.FirstN / LastN) makes it matter, and that step reads the keys kept here.
        keys = None
        spec = self.ev(args[1], env) if len(args) > 1 else None
        items = spec if isinstance(spec, list) else [spec]
        if items and isinstance(items[0], str) and len(items) == 2 and isinstance(
                items[1], Const):
            items = [items]
        if all(isinstance(it, str) or (isinstance(it, list) and len(it) == 2
                                       and isinstance(it[0], str) and isinstance(it[1], Const))
               for it in items):
            keys = [(it, False) if isinstance(it, str)
                    else (it[0], it[1].name.endswith("Descending")) for it in items]
        return _rel("sort", self.table(args[0], env), semantic=False, keys=keys)

    def f_Table_TransformColumnTypes(self, args, env, node):
        t = self.table(args[0], env)
        pairs = self.ev(args[1], env)
        if isinstance(pairs, list) and len(pairs) == 2 and isinstance(pairs[0], str):
            pairs = [pairs]
        if len(args) > 2 and args[2][0] != "null":
            raise Refused("Table.TransformColumnTypes with a culture: text is parsed as that "
                          "locale writes it")
        out = t
        for pr in pairs or []:
            if not (isinstance(pr, list) and len(pr) == 2 and isinstance(pr[0], str)
                    and isinstance(pr[1], TypeV)):
                raise Refused(f"Table.TransformColumnTypes with {snippet(args[1])}")
        if t.op == "native" and t.args.get("literal"):
            # Rows the M itself holds (Enter Data keeps them as text): converted while the
            # model is read, value by value, as M converts them.
            cols = list(t.args["columns"])
            rows = [list(r) for r in t.args["rows"]]
            for col, typ in pairs or []:
                if col not in cols:
                    raise Refused(f"Table.TransformColumnTypes converts {col!r}, which the "
                                  f"rows do not have", hard=True)
                kind = _type_kind(typ.name)
                i = cols.index(col)
                for r in rows:
                    r[i] = _convert_literal(r[i], kind, node)
            return self.literal_rows(cols, rows, node)
        for pr in pairs or []:
            kind = _type_kind(pr[1].name)
            out = _rel("cast", out, semantic=kind not in _IDENTITY_TYPES, col=pr[0], to=kind)
        return out

    def f_Table_TransformColumns(self, args, env, node):
        t = self.table(args[0], env)
        spec = args[1]
        items = spec[1] if spec[0] == "list" else None
        if items and items[0][0] != "list":
            items = [spec]
        if not items:
            raise Refused(f"Table.TransformColumns with {snippet(spec)}")
        out = t
        for it in items:
            if it[0] != "list" or len(it[1]) < 2:
                raise Refused(f"Table.TransformColumns with {snippet(it)}")
            col = self.ev(it[1][0], env)
            p, body, cenv = self.lam(it[1][1], env)
            out = _rel("transform", out, col=col, expr=(p, body, cenv))
        return out

    def f_Table_Group(self, args, env, node):
        t = self.table(args[0], env)
        keys = self.names(args[1], env)
        local = False
        if len(args) > 3 and args[3][0] != "null":
            kind = self.ev(args[3], env)
            if not (isinstance(kind, Const) and kind.name in ("GroupKind.Global",
                                                              "GroupKind.Local")):
                raise Refused(f"Table.Group with the group kind {snippet(args[3])}")
            local = kind.name == "GroupKind.Local"
            if local and _order_of(t) is None:
                # One group per run of adjacent rows with the same key: which rows are
                # adjacent is the table's order, which a Table.Sort before it fixes.
                raise Refused("Table.Group with GroupKind.Local groups runs of adjacent rows, "
                              "and the rows have no fixed order without a Table.Sort before it")
        ci = False
        if len(args) > 4 and args[4][0] != "null":
            ci = self.ignore_case(args[4], env, "Table.Group")
        spec = args[2]
        aggs_nodes = spec[1] if spec[0] == "list" else []
        if aggs_nodes and aggs_nodes[0][0] != "list":
            aggs_nodes = [spec]
        aggs = []
        for a in aggs_nodes:
            if a[0] != "list" or len(a[1]) < 2:
                raise Refused(f"Table.Group with the aggregation {snippet(a)}")
            name = self.ev(a[1][0], env)
            p, body, cenv = self.lam(a[1][1], env)
            aggs.append((name, (p, body, cenv)))
        return _rel("group", t, keys=keys, aggs=aggs, ci=ci, order=_order_of(t), local=local)

    def ignore_case(self, node: tuple, env: Env, step: str) -> bool:
        """A comparer argument of a step: True when it ignores case."""
        cmp = self.ev(node, env)
        if isinstance(cmp, list) and len(cmp) == 1:
            cmp = cmp[0]
        if isinstance(cmp, Const) and cmp.name in ("Comparer.Ordinal",
                                                   "Comparer.OrdinalIgnoreCase"):
            return cmp.name.endswith("IgnoreCase")
        raise Refused(f"{step} with the comparer {snippet(node)}, which compares text by "
                      f"culture")

    def f_Table_Distinct(self, args, env, node):
        """Rows distinct by every column, or by the columns named (each `{"col", comparer}`
        or a column's name). By some columns, the row kept for each key is the first one in
        the table's order: its sort (seen live: the first by the sort's keys)."""
        t = self.table(args[0], env)
        cols, ci = None, set()
        if len(args) > 1 and args[1][0] != "null":
            crit = self.ev(args[1], env)
            items = crit if isinstance(crit, list) else [crit]
            if (len(items) == 2 and isinstance(items[0], str)
                    and isinstance(items[1], Const)):
                items = [items]
            cols = []
            for it in items:
                if isinstance(it, str):
                    cols.append(it)
                elif (isinstance(it, list) and len(it) == 2 and isinstance(it[0], str)
                      and isinstance(it[1], Const)):
                    cols.append(it[0])
                    if self.ignore_case(("val", it[1]), env, "Table.Distinct"):
                        ci.add(it[0])
                elif isinstance(it, Const) and len(items) == 1:
                    cols = None
                    if self.ignore_case(("val", it), env, "Table.Distinct"):
                        ci.add("*")
                else:
                    raise Refused(f"Table.Distinct with {snippet(args[1])}")
        return _rel("distinct", t, cols=cols, ci=ci, order=_order_of(t))

    def f_Table_NestedJoin(self, args, env, node):
        if len(args) < 5:
            raise Refused("Table.NestedJoin without a new column name")
        left = self.table(args[0], env)
        lk = self.names(args[1], env)
        right = self.table(args[2], env)
        rk = self.names(args[3], env)
        new = self.ev(args[4], env)
        kind = self.ev(args[5], env) if len(args) > 5 else Const("JoinKind.LeftOuter")
        ci = False
        if len(args) > 6 and args[6][0] != "null":
            # Keys compared ignoring case. Power BI refreshes no such merge from a warehouse
            # (seen live: "Local evaluation of Table.Join or Table.NestedJoin with key
            # equality comparers is not supported"); the statement is M's own meaning.
            ci = self.ignore_case(args[6], env, "Table.NestedJoin")
        if not isinstance(kind, Const) or len(lk) != len(rk) or not lk:
            raise Refused(f"Table.NestedJoin with {snippet(node)}")
        return Rel("join", left, {"right": right, "lk": lk, "rk": rk, "new": new,
                                  "kind": kind.name.split(".")[-1], "expand": None, "ci": ci},
                   left.dialect, True)

    def f_Table_ExpandTableColumn(self, args, env, node):
        t = self.table(args[0], env)
        col = self.ev(args[1], env)
        cols = self.names(args[2], env)
        new = self.names(args[3], env) if len(args) > 3 else list(cols)
        if t.op != "join" or t.args.get("new") != col or t.args.get("expand") is not None:
            raise Refused(f"Table.ExpandTableColumn of {col!r}, which is not a merge the "
                          f"reader read")
        if len(new) != len(cols):
            raise Refused("Table.ExpandTableColumn names a different number of new columns")
        return Rel("join", t.child, {**t.args, "expand": list(zip(cols, new))}, t.dialect,
                   True)

    def f_Table_Combine(self, args, env, node):
        parts = self.ev(args[0], env)
        if len(args) > 1:
            raise Refused("Table.Combine with column names")
        if not isinstance(parts, list) or not parts:
            raise Refused(f"Table.Combine of {snippet(args[0])}")
        rels = [self.as_rel(p) for p in parts]
        dialects = {r.dialect for r in rels}
        if len(dialects) != 1:
            raise Refused("Table.Combine appends tables read through different connectors")
        return Rel("union", None, {"parts": rels}, rels[0].dialect, True)

    def f_Table_FirstN(self, args, env, node):
        """Rows kept by their POSITION in a SORTED table, as the connector folds them (the
        sort's keys, the warehouse's own NULL order): FirstN / LastN keep the first or last n,
        Skip / RemoveFirstN / RemoveLastN drop them (1 when no count is given), Range keeps
        `count` rows from `offset` (to the end without a count). MaxN / MinN sort by their own
        criteria (a column, or a list of columns), largest or smallest first, and keep n.
        With no sort, or a condition instead of a count, which rows are kept is not fixed."""
        name = str(node[1][1]).rsplit(".", 1)[-1]
        t = self.table(args[0], env)

        def count(i: int, default=None):
            v = self.ev(args[i], env) if len(args) > i and args[i][0] != "null" else default
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and float(v).is_integer() and v >= 0:
                return int(v)
            return None if v is default and default is None else False

        if name in ("MaxN", "MinN"):
            crit = self.ev(args[1], env) if len(args) > 1 else None
            cols = crit if isinstance(crit, list) else [crit]
            n = count(2)
            if cols and all(isinstance(c, str) for c in cols) and n not in (None, False):
                return _rel("limit", t, n=n, skip=0,
                            keys=[(c, name == "MaxN") for c in cols])
            raise Refused(f"Table.{name} with {snippet(node)}: only a column (or a list of "
                          f"columns) and a whole-number count are compiled")
        keys = t.args.get("keys") if t.op == "sort" else None
        if name in ("FirstN", "LastN"):
            n, skip = count(1), 0
        elif name in ("Skip", "RemoveFirstN", "RemoveLastN"):
            n, skip = None, count(1, 1)
        else:                                                   # Range(table, offset, count)
            n, skip = count(2), count(1, 0)
        if keys and n is not False and skip is not False and (n is not None or skip):
            if name in ("LastN", "RemoveLastN"):
                keys = [(c, not d) for c, d in keys]
            return _rel("limit", t.child, n=n, skip=skip, keys=keys)
        raise Refused(f"Table.{name} keeps rows by their position, and which rows those are "
                      f"is not fixed when values tie (or there is no sort)")

    f_Table_LastN = f_Table_FirstN
    f_Table_Range = f_Table_FirstN
    f_Table_Skip = f_Table_FirstN
    f_Table_RemoveFirstN = f_Table_FirstN
    f_Table_RemoveLastN = f_Table_FirstN
    f_Table_MaxN = f_Table_FirstN
    f_Table_MinN = f_Table_FirstN

    def f_Table_ReplaceValue(self, args, env, node):
        """`Table.ReplaceValue(t, old, new, replacer, columns)`: in each column, a value equal
        to `old` becomes `new` (Replacer.ReplaceValue), or each occurrence of the text `old`
        in a text value is replaced (Replacer.ReplaceText, case-sensitive). `old` and `new`
        may be functions of the row (`each [x]`); the replacer may be a custom function of
        (value, old, new)."""
        if len(args) < 5:
            raise Refused("Table.ReplaceValue without its columns")
        t = self.table(args[0], env)
        cols = self.names(args[4], env)
        rep = self.ev(args[3], env)
        if isinstance(rep, Const) and rep.name in ("Replacer.ReplaceValue",
                                                   "Replacer.ReplaceText"):
            how: Any = rep.name.split(".")[-1]
        elif isinstance(rep, Fn) and len(rep.params) >= 3:
            how = rep
        else:
            raise Refused(f"Table.ReplaceValue with the replacer {snippet(args[3])}")
        return _rel("replace", t, cols=cols, old=self.operand(args[1], env),
                    new=self.operand(args[2], env), how=how)

    def operand(self, node: tuple, env: Env) -> tuple:
        """A step argument that is a value or a function of the row: ("value", v) or
        ("row", param, body, env)."""
        v = self.ev(node, env)
        if isinstance(v, Fn):
            if len(v.params) != 1:
                raise Refused(f"the step argument {snippet(node)} is not a one-argument function")
            return ("row", v.params[0], v.body, v.env)
        return ("value", v)

    def f_Table_AddIndexColumn(self, args, env, node):
        t = self.table(args[0], env)
        name = self.ev(args[1], env)
        start = self.ev(args[2], env) if len(args) > 2 and args[2][0] != "null" else 0
        step = self.ev(args[3], env) if len(args) > 3 and args[3][0] != "null" else 1
        order = _order_of(t)
        if not isinstance(name, str) or not all(
                isinstance(v, (int, Decimal)) and not isinstance(v, bool) for v in (start, step)):
            raise Refused(f"Table.AddIndexColumn with {snippet(node)}")
        if not order:
            raise Refused("Table.AddIndexColumn numbers the rows in their order, and the rows "
                          "have no fixed order without a Table.Sort before it")
        return _rel("index", t, name=name, start=start, step=step, keys=order)

    def f_Table_ReplaceErrorValues(self, args, env, node):
        t = self.table(args[0], env)
        pairs = self.ev(args[1], env)
        if isinstance(pairs, list) and len(pairs) == 2 and isinstance(pairs[0], str):
            pairs = [pairs]
        if not (isinstance(pairs, list) and all(isinstance(p, list) and len(p) == 2
                                                and isinstance(p[0], str) for p in pairs)):
            raise Refused(f"Table.ReplaceErrorValues with {snippet(args[1])}")
        return _rel("on_error", t, cols=[p[0] for p in pairs],
                    replace={p[0]: p[1] for p in pairs})

    def f_Table_RemoveRowsWithErrors(self, args, env, node):
        t = self.table(args[0], env)
        cols = self.names(args[1], env) if len(args) > 1 and args[1][0] != "null" else None
        return _rel("on_error", t, cols=cols, replace=None)

    def f_Table_FillDown(self, args, env, node):
        """Each null in the columns takes the last value above it, in the table's order: its
        sort (seen live). With no sort, the rows have no fixed order."""
        name = str(node[1][1]).rsplit(".", 1)[-1]
        t = self.table(args[0], env)
        order = _order_of(t)
        if not order:
            raise Refused(f"Table.{name} fills from the rows above (or below), and the rows "
                          f"have no fixed order without a Table.Sort before it")
        return _rel("fill", t, cols=self.names(args[1], env), up=name == "FillUp",
                    keys=order)

    f_Table_FillUp = f_Table_FillDown

    def f_Table_Unpivot(self, args, env, node):
        """Columns into rows: one row per (row, column), the column's name in `attribute`, its
        value in `value`; a null value makes no row (seen live)."""
        if len(args) < 4:
            raise Refused("Table.Unpivot without the attribute and value columns")
        t = self.table(args[0], env)
        name = str(node[1][1]).rsplit(".", 1)[-1]
        cols = self.names(args[1], env)
        attr, value = self.ev(args[2], env), self.ev(args[3], env)
        if not (isinstance(attr, str) and isinstance(value, str)):
            raise Refused(f"Table.{name} names its columns with an expression")
        if name == "Unpivot":
            return _rel("unpivot", t, cols=cols, keep=None, attr=attr, value=value)
        return _rel("unpivot", t, cols=None, keep=cols, attr=attr, value=value)

    f_Table_UnpivotOtherColumns = f_Table_Unpivot

    def f_Table_Pivot(self, args, env, node):
        """Rows into columns: one row per combination of the other columns, a column per
        value of `attribute` named (`List.Distinct(t[attribute])`: every value the data
        holds, the model's own columns), each the aggregate of the rows' `value` (a missing
        combination null, or 0 for List.Count; seen live)."""
        if len(args) < 4:
            raise Refused("Table.Pivot without its attribute and value columns")
        t = self.table(args[0], env)
        attr, value = self.ev(args[2], env), self.ev(args[3], env)
        if not (isinstance(attr, str) and isinstance(value, str)):
            raise Refused("Table.Pivot names its columns with an expression")
        vals = None
        lst = args[1]
        if not (lst[0] == "call" and lst[1] == ("ident", "List.Distinct") and lst[2]
                and lst[2][0][0] == "field" and lst[2][0][2] == attr):
            vals = self.ev(lst, env)
            if not (isinstance(vals, list) and all(isinstance(v, str) for v in vals)):
                raise Refused(f"Table.Pivot with the values {snippet(lst)}")
        agg = None
        if len(args) > 4 and args[4][0] != "null":
            f = self.ev(args[4], env)
            agg = f.name if isinstance(f, Builtin) else None
            if agg not in _PIVOT_AGGS:
                raise Refused(f"Table.Pivot aggregates with {snippet(args[4])}")
        return _rel("pivot", t, values=vals, attr=attr, value=value, agg=agg)


# Steps that keep the order of the rows they read: a sort before them still orders the rows
# after them (for a step that reads rows by position).
_ORDER_KEEPING = {"filter", "extend", "rename", "rename_fn", "prefix", "cast", "remove",
                  "select", "transform", "replace", "dup", "fill", "index", "on_error"}

# Table.Pivot's aggregation functions -> the SQL aggregate over the cell's rows.
_PIVOT_AGGS = {"List.Sum": "SUM", "List.Average": "AVG", "List.Min": "MIN", "List.Max": "MAX",
               "List.Count": "COUNT", "List.NonNullCount": "COUNT_NONNULL"}


def _order_of(r: Rel) -> list[tuple[str, bool]] | None:
    """The sort keys that order `r`'s rows, through steps that keep the order; else None."""
    while r is not None:
        if r.op == "sort":
            return r.args.get("keys")
        if r.op == "limit":
            return r.args.get("keys")
        if r.op not in _ORDER_KEEPING:
            return None
        r = r.child
    return None


def _date_from(self, args, env, node):
    v = self._scalar_args(args, env)[0]
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date) or v is None:
        return v
    raise Refused(f"{snippet(node)} converts a value the reader cannot evaluate")


_Eval.f_Date_From = _date_from


def bind_parameters(sql: str, params: Any, dialect: str | None) -> str:
    """Value.NativeQuery's parameters written into its SQL as literals: a record binds each
    `@name`, a list each `?` in order (text outside quotes, identifiers and comments only).
    A marker with no value, or a value with no literal, is refused by name."""
    if isinstance(params, dict):
        named, pos = params, None
    elif isinstance(params, list):
        named, pos = None, list(params)
    else:
        raise Refused("Value.NativeQuery binds parameters that are neither a record nor a list",
                      hard=True)
    out, i, n, used = [], 0, len(sql), 0
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n and not (sql[j] == ch and sql[j + 1:j + 2] != ch):
                j += 2 if sql[j] == ch else 1
            out.append(sql[i:j + 1])
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(sql[i:j])
            i = j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(sql[i:j])
            i = j
            continue
        if ch == "@" and named is not None:
            mt = re.match(r"@([A-Za-z_][A-Za-z0-9_]*)", sql[i:])
            if mt:
                if mt.group(1) not in named:
                    raise Refused(f"Value.NativeQuery's SQL names the parameter @{mt.group(1)}, "
                                  f"which it does not bind", hard=True)
                out.append(_sql_literal(named[mt.group(1)], dialect))
                i += mt.end()
                continue
        if ch == "?" and pos is not None:
            if used >= len(pos):
                raise Refused("Value.NativeQuery's SQL has more ? markers than parameters",
                              hard=True)
            out.append(_sql_literal(pos[used], dialect))
            used += 1
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _safe_name(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else name


def _sql_literal(v: Any, dialect: str | None) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, Decimal)):
        return str(v)
    if isinstance(v, str):
        return _quote_text(v, dialect)
    if isinstance(v, _dt.datetime):
        return f"CAST('{v.isoformat(sep=' ')}' AS TIMESTAMP)"
    if isinstance(v, _dt.date):
        return f"CAST('{v.isoformat()}' AS DATE)"
    raise Refused("Value.NativeQuery binds a value with no SQL literal", hard=True)


def _m_text(v: Any, node: tuple) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, Decimal):
        s = format(v.normalize(), "f")
        return s
    if isinstance(v, _dt.date):
        raise Refused(f"{snippet(node)} writes a date as text in the model's locale")
    raise Refused(f"{snippet(node)} writes a value as text the reader cannot reproduce")


# ── types ──────────────────────────────────────────────────────────────────────────────────

_TYPE_KINDS = {
    "text": "text", "Text.Type": "text", "number": "number", "Number.Type": "number",
    "Decimal.Type": "number", "Double.Type": "number", "Single.Type": "number",
    "Percentage.Type": "number", "Int64.Type": "int", "Int32.Type": "int", "Int16.Type": "int",
    "Int8.Type": "int", "Byte.Type": "int", "Currency.Type": "currency", "date": "date",
    "Date.Type": "date", "datetime": "datetime", "DateTime.Type": "datetime",
    "datetimezone": "datetimezone", "DateTimeZone.Type": "datetimezone", "time": "time",
    "Time.Type": "time", "logical": "logical", "Logical.Type": "logical", "any": "any",
    "Any.Type": "any", "duration": "duration", "Duration.Type": "duration",
}
# Conversions that leave a warehouse value as it is: text stays text, a number a number.
# (A NUMBER converted to `type text` would be written in M's own number format: open issue.)
_IDENTITY_TYPES = {"text", "number", "logical", "any", "datetime"}


_PLAIN_NUMBER = re.compile(r"\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*")


def _convert_literal(v: Any, kind: str, node: tuple) -> Any:
    """One value M holds, converted to `kind` as Power Query converts it: text of a plain
    number (digits, a point, an exponent: no culture's separators) to a number, a whole
    number rounded half to even, a currency to 4 places; ISO text to a date; "true" /
    "false" to a logical. Any other conversion is refused by name (M would raise an error, or
    read the text as the model's culture writes it)."""
    if v is None or kind in ("any",):
        return v
    if kind == "text":
        if isinstance(v, str):
            return v
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
    if kind in ("number", "int", "currency"):
        if isinstance(v, bool):
            raise Refused(f"{snippet(node)} converts a logical to a number", hard=True)
        if isinstance(v, str):
            if not _PLAIN_NUMBER.fullmatch(v):
                raise Refused(f"{snippet(node)} converts the text {v!r} to a number, which "
                              f"M reads as the model's culture writes numbers", hard=True)
            v = Decimal(v.strip())
        if isinstance(v, (int, Decimal)):
            d = Decimal(v)
            if kind == "int":
                return int(d.quantize(Decimal(1), rounding="ROUND_HALF_EVEN"))
            if kind == "currency":
                return d.quantize(Decimal("0.0001"), rounding="ROUND_HALF_EVEN")
            return d
    if kind == "date":
        if isinstance(v, _dt.date) and not isinstance(v, _dt.datetime):
            return v
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
            return _dt.date.fromisoformat(v.strip())
    if kind == "logical" and isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    if kind == "logical" and isinstance(v, bool):
        return v
    raise Refused(f"{snippet(node)} converts {v!r} to {kind}, which the reader does not "
                  f"evaluate", hard=True)


def _type_kind(name: str) -> str:
    kind = _TYPE_KINDS.get(name)
    if kind is None:
        raise Refused(f"a conversion to type {name}")
    return kind


# Dialect forms. Rounding half to even is what `Int64.Type`, `Currency.Type` and
# `Number.Round`'s default do; each warehouse spells it its own way, or not at all.
_ROUND_EVEN: dict[str, str] = dict(_dialects()["round_half_even"])


def _quote_text(s: str, dialect: str | None) -> str:
    from metrics_agent.plugs.readers.bicompile import _quote  # noqa: PLC0415
    return _quote(s, dialect)


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ── the M expression compiler (an `each` body -> SQL over one row) ─────────────────────────

@dataclass
class _Row:
    """The columns one row of a layer offers: name -> SQL."""
    cols: dict[str, str]
    alias: str
    known: list[str] | None          # every column, in order, when known
    gone: set[str] = field(default_factory=set)
    types: dict[str, str] = field(default_factory=dict)
    # A column's value is an M error where this condition holds (a step's expression raised).
    errs: dict[str, str] = field(default_factory=dict)

    def ref(self, name: str) -> str:
        if name in self.cols:
            return self.cols[name]
        if name in self.gone or (self.known is not None and name not in self.known):
            raise Refused(f"a step reads the column {name!r}, which is not a column of the "
                          f"table at that step")
        return f"{self.alias}.{_ident(name)}"

    def copy(self) -> "_Row":
        return _Row(dict(self.cols), self.alias,
                    list(self.known) if self.known is not None else None, set(self.gone),
                    dict(self.types), dict(self.errs))


class _ExprCompiler:
    """One M row expression -> (SQL, kind). Kinds: text, number, date, logical, null, None."""

    def __init__(self, ev: _Eval, dialect: str | None, row: _Row, param: str,
                 env: Env, column: str | None = None,
                 subst: dict[str, tuple[str, str | None]] | None = None, depth: int = 0):
        self.ev, self.dialect, self.row, self.param, self.env = ev, dialect, row, param, env
        self.column = column        # `_` is this column's value (Table.TransformColumns)
        # A custom function's parameters, bound to the SQL of the arguments it was called with.
        self.subst = dict(subst or {})
        self.depth = depth
        # Inside a `try`: the conditions under which the expression raises an M error (None
        # outside one: a model whose refresh succeeded loaded no row that raised, so no
        # condition is needed there), and the branch conditions that lead to the current spot.
        self.raises: list[str] | None = None
        self.guard: list[str] = []
        # Evaluated by Power Query itself (inside a `try`, which no connector folds), where an
        # `if` over null raises; folded to the warehouse it takes the `else` branch.
        self.local = False

    def sub(self, param: str, env: Env, subst: dict, column: str | None = None
            ) -> "_ExprCompiler":
        """A compiler for a nested body (an inlined function) that shares this one's `try`."""
        out = _ExprCompiler(self.ev, self.dialect, self.row, param, env, column, subst,
                            self.depth + 1)
        out.raises, out.guard, out.local = self.raises, self.guard, self.local
        return out

    def errors_of(self, body: tuple) -> tuple[str, str | None, str | None]:
        """(SQL, kind, the condition under which the value is an M error or None)."""
        self.raises, self.guard = [], []
        x, kind = self.c(body)
        conds = list(dict.fromkeys(self.raises))
        self.raises = None
        return x, kind, (" OR ".join(f"({c})" for c in conds) if conds else None)

    def raise_when(self, cond: str) -> None:
        """Record that the expression raises an M error when `cond` holds (on this branch)."""
        if self.raises is None:
            return
        self.raises.append(" AND ".join(f"({g})" for g in [*self.guard, cond]))

    def truth(self, cond: str) -> str:
        """`cond` as a two-valued test: true exactly where it is true."""
        if self.dialect in _NO_BOOLEAN:
            return f"(CASE WHEN {cond} THEN 1 ELSE 0 END = 1)"
        return f"COALESCE({cond}, FALSE)"

    def lit(self, v: Any) -> tuple[str, str | None]:
        if v is None:
            return "NULL", "null"
        if isinstance(v, bool):
            return ("TRUE" if v else "FALSE"), "logical"
        if isinstance(v, (int, Decimal)):
            return str(v), "number"
        if isinstance(v, str):
            return _quote_text(v, self.dialect), "text"
        if isinstance(v, _dt.datetime):
            return f"CAST('{v.isoformat(sep=' ')}' AS TIMESTAMP)", "datetime"
        if isinstance(v, _dt.date):
            return f"CAST('{v.isoformat()}' AS DATE)", "date"
        raise Refused("a value with no SQL literal")

    def c(self, node: tuple) -> tuple[str, str | None]:
        k = node[0]
        if k == "ident" and node[1] in self.subst:
            return self.subst[node[1]]
        if k == "try":
            return self.try_(node)
        if k == "error":
            self.raise_when("TRUE")
            return "NULL", "null"
        if k == "field" and node[1] is not None and node[1][0] == "try":
            return self.try_field(node)
        if k == "field" and (node[1] is None or node[1] == ("ident", self.param)):
            name = node[2]
            if name in self.row.errs:
                self.raise_when(self.row.errs[name])
            try:
                return self.row.ref(name), self.row.types.get(name)
            except Refused:
                if node[3]:
                    return "NULL", "null"
                raise
        if k == "ident" and node[1] == self.param:
            if self.column is None:
                raise Refused(f"a row expression uses the whole row ({self.param})")
            return self.row.ref(self.column), self.row.types.get(self.column)
        if k in ("str", "num", "bool", "null", "hash"):
            return self.lit(self.ev.ev(node, self.env))
        if k == "ident":
            v = self.ev.ev(node, self.env)
            if isinstance(v, (str, int, Decimal, bool, _dt.date)) or v is None:
                return self.lit(v)
            raise Refused(f"a row expression reads {node[1]!r}, which is not a value")
        if k == "un":
            s, kind = self.c(node[2])
            if node[1] == "not":
                return f"NOT ({s})", "logical"
            if node[1] == "-":
                return f"-({s})", kind
            return s, kind
        if k == "bin":
            return self.binop(node)
        if k == "if":
            # `if … then … else if …` as one CASE. A null condition takes the `else` branch
            # (seen live), as a CASE does.
            # Inside a `try` (evaluated by Power Query itself, not folded), an `if` over null
            # raises an error; an error in a branch raises only where that branch is taken.
            whens, kinds = [], []
            saved = self.guard
            taken: list[str] = []
            while node[0] == "if":
                self.guard = [*saved, *(f"NOT {self.truth(t)}" for t in taken)]
                cond, _ = self.c(node[1])
                if self.local:
                    self.raise_when(f"({cond}) IS NULL")
                self.guard = [*saved, *(f"NOT {self.truth(t)}" for t in taken), self.truth(cond)]
                a, ka = self.c(node[2])
                whens.append(f"WHEN {cond} THEN {a}")
                kinds.append(ka)
                taken.append(cond)
                node = node[3]
            self.guard = [*saved, *(f"NOT {self.truth(t)}" for t in taken)]
            b, kb = self.c(node)
            self.guard = saved
            kinds.append(kb)
            kind = next((x for x in kinds if x not in (None, "null")), None)
            return "CASE " + " ".join(whens) + f" ELSE {b} END", kind
        if k == "call":
            return self.call(node)
        if k == "list":
            raise Refused(f"a row expression builds a list ({snippet(node)})")
        raise Refused(f"the row expression {snippet(node)} has no SQL the reader can write")

    def try_(self, node: tuple) -> tuple[str, str | None]:
        """`try x otherwise y`: y on the rows where x raises an M error, else x. An `x` that
        raises on no row is x itself."""
        if node[2] is None:
            raise Refused(f"error handling that returns a record ({snippet(node)}) where a "
                          f"value is expected")
        x, kx, conds = self._attempt(node[1])
        if not conds:
            return x, kx
        y, ky = self.c(node[2])
        kind = kx if kx not in (None, "null") else ky
        return f"CASE WHEN {' OR '.join(conds)} THEN {y} ELSE {x} END", kind

    def try_field(self, node: tuple) -> tuple[str, str | None]:
        """`(try x)[HasError]` and `(try x)[Value]`."""
        if node[1][2] is not None:
            raise Refused(f"a field of a value ({snippet(node)})")
        x, kx, conds = self._attempt(node[1][1])
        if node[2] == "HasError":
            return (self.truth("(" + " OR ".join(conds) + ")") if conds else "FALSE"), "logical"
        if node[2] == "Value":
            for c in conds:
                self.raise_when(c)
            return x, kx
        raise Refused(f"the error record's field [{node[2]}]")

    def _attempt(self, body: tuple) -> tuple[str, str | None, list[str]]:
        saved, saved_guard, saved_local = self.raises, self.guard, self.local
        self.raises, self.guard, self.local = [], [], True
        try:
            x, kx = self.c(body)
            return x, kx, list(dict.fromkeys(self.raises))
        finally:
            self.raises, self.guard, self.local = saved, saved_guard, saved_local

    def binop(self, node: tuple) -> tuple[str, str | None]:
        op, ln, rn = node[1], node[2], node[3]
        if op in ("as", "is"):
            return self.c(ln) if op == "as" else (_ for _ in ()).throw(
                Refused(f"a type test ({snippet(node)})"))
        a, ka = self.c(ln)
        b, kb = self.c(rn)
        if op in ("=", "<>"):
            # Never null in M: null equals null, and differs from any value.
            if kb == "null" or ka == "null":
                x = a if kb == "null" else b
                if ka == "null" and kb == "null":
                    return ("TRUE" if op == "=" else "FALSE"), "logical"
                return f"{x} IS {'NOT ' if op == '<>' else ''}NULL", "logical"
            if self._is_literal(rn) or self._is_literal(ln):
                x, y = (a, b) if self._is_literal(rn) else (b, a)
                return self.two_valued(f"{x} {op} {y}", x, op == "<>"), "logical"
            return f"{a} IS {'NOT ' if op == '=' else ''}DISTINCT FROM {b}", "logical"
        if op in ("<", ">", "<=", ">="):
            return f"{a} {op} {b}", "logical"
        if op == "and":
            return f"({a} AND {b})", "logical"
        if op == "or":
            return f"({a} OR {b})", "logical"
        if op == "??":
            return f"COALESCE({a}, {b})", ka if ka not in (None, "null") else kb
        if op == "&":
            if "number" in (ka, kb) or "date" in (ka, kb):
                raise Refused(f"{snippet(node)} joins a non-text value as text")
            return f"({a} || {b})", "text"
        if op in ("+", "-", "*"):
            if "text" in (ka, kb):
                raise Refused(f"{snippet(node)} does arithmetic on text")
            if "date" in (ka, kb) or "datetime" in (ka, kb):
                raise Refused(f"{snippet(node)} does date arithmetic")
            return f"({a} {op} {b})", "number"
        if op == "/":
            v = self.ev.ev(rn, self.env) if self._is_literal(rn) else None
            if isinstance(v, (int, Decimal)) and v != 0:
                return f"({a} / {b})", "number"
            return self.by_zero(a, b, f"{a} / {b}", node), "number"
        raise Refused(f"the operator {op} in {snippet(node)}")

    def by_zero(self, a: str, b: str, quotient: str, node: tuple, zero: str | None = None) -> str:
        """A division by a value that may be zero, as M divides: by zero, a positive number
        is infinity, a negative one minus infinity and zero NaN (seen live: evaluated by Power
        Query and loaded as such; folded to the warehouse, the division raises its error and
        the refresh fails, so a refreshed model holds no such row). `zero`: the value of every
        division by zero (Number.IntegerDivide: NaN)."""
        sp = _FLOAT_SPECIALS.get(str(self.dialect or "").lower())
        if not sp:
            why = ((_dialects().get("no_form") or {}).get("float_specials") or {}).get(
                str(self.dialect or "").lower())
            raise Refused(f"{snippet(node)} divides by a value that may be zero, which gives "
                          f"infinity in M and has no form for the {self.dialect or 'unknown'} "
                          f"dialect" + (f" ({why})" if why else ""))
        on_zero = (f"CASE WHEN {a} IS NOT NULL THEN {sp[zero]} END" if zero else
                   f"CASE WHEN {a} > 0 THEN {sp['inf']} WHEN {a} < 0 THEN {sp['ninf']} "
                   f"WHEN {a} = 0 THEN {sp['nan']} END")
        return f"(CASE WHEN {b} = 0 THEN {on_zero} ELSE {quotient} END)"

    def two_valued(self, pred: str, x: str, when_null: bool) -> str:
        """`pred` (a comparison of `x` with a literal) as M reads it: never null, and
        `when_null` where `x` is null. One predicate where the dialect has logical values."""
        if self.dialect in _NO_BOOLEAN:
            return (f"({x} IS NULL OR {pred})" if when_null
                    else f"({x} IS NOT NULL AND {pred})")
        return f"COALESCE({pred}, {'TRUE' if when_null else 'FALSE'})"

    def _is_literal(self, node: tuple) -> bool:
        if node[0] in ("str", "num", "bool", "hash"):
            return True
        if node[0] == "un" and node[1] == "-" and node[2][0] == "num":
            return True
        if node[0] == "ident" and node[1] != self.param and node[1] not in self.subst:
            try:
                v = self.ev.ev(node, self.env)
            except Refused:
                return False
            return v is not None and isinstance(v, (str, int, Decimal, bool, _dt.date))
        return False

    def _args(self, node: tuple) -> list[tuple[str, str | None]]:
        return [self.c(a) for a in node[2]]

    def call(self, node: tuple) -> tuple[str, str | None]:
        fnode = node[1]
        name = fnode[1] if fnode[0] == "ident" else None
        if name is None:
            raise Refused(f"the call {snippet(node)}")
        args = node[2]
        d = self.dialect
        fn = self._function(name)
        if fn is not None:
            return self.inline(fn, args, node)

        def one() -> str:
            return self.c(args[0])[0]

        if name in ("Comparer.Ordinal", "Comparer.OrdinalIgnoreCase"):
            return self.compare(args[0], args[1], name.endswith("IgnoreCase")), "int"
        if name == "Comparer.Equals":
            ci = self.comparer(args[0], node)
            a, b = self.c(args[1])[0], self.c(args[2])[0]
            if ci:
                a, b = f"UPPER({a})", f"UPPER({b})"
            return self.truth(f"({a} IS NOT DISTINCT FROM {b})"), "logical"
        if name in _ROUND_FUNCTIONS:
            digits = (self._int_arg(args[1], node)
                      if len(args) > 1 and args[1][0] != "null" else 0)
            return self.round(one(), digits, _ROUND_FUNCTIONS[name], node), "number"
        if name in _RELATIVE_TESTS or name in _DATE_SHIFTS or name in _NOW_FUNCTIONS:
            return self.dates(name, args, node)

        if name in ("Text.Upper", "Text.Lower"):
            return f"{name.split('.')[1].upper()}({one()})", "text"
        if name == "Text.Trim" and len(args) == 1:
            return f"TRIM({one()})", "text"
        if name == "Text.TrimStart" and len(args) == 1:
            return f"LTRIM({one()})", "text"
        if name == "Text.TrimEnd" and len(args) == 1:
            return f"RTRIM({one()})", "text"
        if name == "Text.Length":
            return f"LENGTH({one()})", "number"
        if name in ("Text.Start", "Text.End"):
            n = self._int_arg(args[1], node)
            return f"{'LEFT' if name == 'Text.Start' else 'RIGHT'}({one()}, {n})", "text"
        if name == "Text.Middle":
            s = self._int_arg(args[1], node)
            if len(args) > 2:
                return f"SUBSTR({one()}, {s + 1}, {self._int_arg(args[2], node)})", "text"
            return f"SUBSTR({one()}, {s + 1})", "text"
        if name in ("Text.Contains", "Text.StartsWith", "Text.EndsWith"):
            x = one()
            sub = self.ev.ev(args[1], self.env)
            if not isinstance(sub, str):
                raise Refused(f"{snippet(node)} looks for text the reader cannot evaluate")
            ci = False
            if len(args) > 2:
                cmp = self.ev.ev(args[2], self.env)
                if isinstance(cmp, Const) and cmp.name == "Comparer.OrdinalIgnoreCase":
                    ci = True
                elif not (isinstance(cmp, Const) and cmp.name == "Comparer.Ordinal"):
                    raise Refused(f"{snippet(node)} compares text by culture")
            lit = _quote_text(sub.upper() if ci else sub, d)
            xx = f"UPPER({x})" if ci else x
            if name == "Text.Contains":
                return f"(POSITION({lit} IN {xx}) > 0)", "logical"
            side = "LEFT" if name == "Text.StartsWith" else "RIGHT"
            return f"({side}({xx}, {len(sub)}) = {lit})", "logical"
        if name == "Text.From":
            x, kind = self.c(args[0])
            if kind == "text":
                return x, "text"
            if kind == "int":
                return f"CAST({x} AS VARCHAR)", "text"
            raise Refused(f"{snippet(node)} writes a {kind or 'value'} as text in M's own "
                          f"format")
        if name == "List.Contains":
            vals = self.ev.ev(args[0], self.env)
            x, _ = self.c(args[1])
            ci = self.comparer(args[2], node) if len(args) > 2 else False
            if not isinstance(vals, list):
                raise Refused(f"{snippet(node)} looks in a list the reader cannot evaluate")
            if ci:
                # Ordinal, ignoring case (seen live: a null value is in no list of text).
                x = f"UPPER({x})"
                vals = [v.upper() if isinstance(v, str) else v for v in vals]
            lits = [self.lit(v)[0] for v in vals if v is not None]
            has_null = any(v is None for v in vals)
            inner = f"{x} IN ({', '.join(lits)})" if lits else "FALSE"
            # Never null in M: a null value is in the list only if the list holds null.
            if has_null:
                return f"({x} IS NULL OR {inner})", "logical"
            return self.two_valued(inner, x, False), "logical"
        if name in ("Date.Year", "Date.Month", "Date.Day", "Date.QuarterOfYear"):
            part = {"Date.Year": "YEAR", "Date.Month": "MONTH", "Date.Day": "DAY",
                    "Date.QuarterOfYear": "QUARTER"}[name]
            return f"EXTRACT({part} FROM {one()})", "int"
        if name in ("Date.From", "DateTime.Date"):
            return f"CAST({one()} AS DATE)", "date"
        if name in ("Date.StartOfMonth", "Date.StartOfYear", "Date.StartOfQuarter"):
            part = name.split("StartOf")[1].lower()
            return f"CAST(DATE_TRUNC('{part}', {one()}) AS DATE)", "date"
        if name == "Number.Round":
            x = one()
            digits = self._int_arg(args[1], node) if len(args) > 1 and args[1][0] != "null" else 0
            mode = (self.ev.ev(args[2], self.env) if len(args) > 2 and args[2][0] != "null"
                    else Const("RoundingMode.ToEven"))
            if not (isinstance(mode, Const) and mode.name in _ROUNDING_MODES):
                raise Refused(f"{snippet(node)} rounds in a mode the reader cannot evaluate")
            return self.round(x, digits, _ROUNDING_MODES[mode.name], node), "number"
        if name == "Number.Abs":
            return f"ABS({one()})", "number"
        if name == "Number.IntegerDivide" and len(args) == 2:
            # The quotient's integer part (truncated); by zero NaN (seen live, no error).
            a, b = self.c(args[0])[0], self.c(args[1])[0]
            q = f"{a} / {b}"
            trunc = f"CASE WHEN {q} < 0 THEN CEIL({q}) ELSE FLOOR({q}) END"
            return self.by_zero(a, b, trunc, node, zero="nan"), "number"
        if name in ("Number.From", "Decimal.From", "Currency.From", "Int64.From"):
            x, kind = self.c(args[0])
            if kind not in ("number", "int"):
                raise Refused(f"{snippet(node)} converts a {kind or 'value'} to a number, "
                              f"which may fail")
            if name == "Int64.From":
                return round_even(x, 0, d), "int"
            if name == "Currency.From":
                return round_even(x, 4, d), "number"
            return x, kind
        raise Refused(f"the M function {name} has no SQL the reader can write")

    def _function(self, name: str) -> "Fn | None":
        """The custom M function `name` names (a let-bound step, a shared query), or None."""
        if name in self.subst or name == self.param or "." in name and self.env.lookup(
                name) is None:
            return None
        try:
            v = self.ev.ev(("ident", name), self.env)
        except Refused:
            return None
        return v if isinstance(v, Fn) else None

    def inline(self, fn: Fn, args: list[tuple], node: tuple) -> tuple[str, str | None]:
        """A custom function called per row: its body, compiled with each parameter bound to
        the SQL of its argument (a missing optional one null). An argument that is the row
        itself (`fn(_)`) makes the parameter that row."""
        if len(args) > len(fn.params):
            raise Refused(f"{snippet(node)} passes more arguments than the function takes")
        if self.depth >= _MAX_CALL_DEPTH:
            raise Refused(f"{snippet(node)} calls functions deeper than the reader reads "
                          f"(a recursive function)")
        subst: dict[str, tuple[str, str | None]] = {}
        row_param = "\0none"
        for i, p in enumerate(fn.params):
            if i >= len(args):
                subst[p] = ("NULL", "null")
            elif args[i] == ("ident", self.param) and self.column is None:
                row_param = p
            else:
                subst[p] = self.c(args[i])
        return self.sub(row_param, fn.env, subst).c(fn.body)

    def comparer(self, node: tuple, call: tuple) -> bool:
        """A comparer argument: True when it ignores case (ordinal either way)."""
        cmp = self.ev.ev(node, self.env)
        if isinstance(cmp, Const) and cmp.name in ("Comparer.Ordinal",
                                                   "Comparer.OrdinalIgnoreCase"):
            return cmp.name.endswith("IgnoreCase")
        raise Refused(f"{snippet(call)} compares text by culture")

    def compare(self, an: tuple, bn: tuple, ci: bool) -> str:
        """`Comparer.Ordinal(a, b)` / `OrdinalIgnoreCase`: -1, 0 or 1, by code point (after
        upper-casing). A null against text raises an M error (seen live); null against null
        is 0."""
        a, b = self.c(an)[0], self.c(bn)[0]
        self.raise_when(f"(({a}) IS NULL) <> (({b}) IS NULL)")
        x, y = (f"UPPER({a})", f"UPPER({b})") if ci else (a, b)
        return (f"CASE WHEN {x} IS NOT DISTINCT FROM {y} THEN 0 WHEN {x} IS NULL OR {x} < {y} "
                f"THEN -1 ELSE 1 END")

    def round(self, x: str, digits: int, mode: str, call: tuple) -> str:
        """`Number.Round(x, d, mode)` and the Number.Round* functions (seen live on decimal
        values, folded and evaluated by Power Query alike). Number.Round goes to the nearest
        value, a tie to_even, away (from zero), half_up (toward +infinity), half_down or
        half_toward_zero; the functions round every value: up / down (ceiling / floor),
        toward_zero (truncation) and magnitude (away from zero whatever the digit)."""
        if mode == "to_even":
            return round_even(x, digits, self.dialect)
        if mode == "away":
            return f"ROUND({x}, {digits})"
        scale = Decimal(10) ** digits
        sx = f"{x} * {_num_literal(scale)}" if digits else x
        back = f" / {_num_literal(scale)}" if digits else ""
        up, down = f"CEIL({sx}){back}", f"FLOOR({sx}){back}"
        tie_up, tie_down = f"FLOOR({sx} + 0.5){back}", f"CEIL({sx} - 0.5){back}"
        if mode == "half_up":
            return f"({tie_up})"
        if mode == "half_down":
            return f"({tie_down})"
        if mode == "half_toward_zero":
            return f"(CASE WHEN {x} < 0 THEN {tie_up} ELSE {tie_down} END)"
        if mode == "up":
            return f"({up})"
        if mode == "down":
            return f"({down})"
        if mode == "toward_zero":
            return f"(CASE WHEN {x} < 0 THEN {up} ELSE {down} END)"
        if mode == "magnitude":
            return f"(CASE WHEN {x} < 0 THEN {down} ELSE {up} END)"
        raise Refused(f"{snippet(call)} rounds in a mode the reader does not write")

    def dates(self, name: str, args: list[tuple], node: tuple) -> tuple[str, str | None]:
        """Dates relative to the moment the model is refreshed (`DateTime.LocalNow()`, UTC in
        the service, seen live), read as the moment the statement runs: `Date.IsInPrevious
        NDays` and the other Date.IsIn* tests, `Date.AddDays` and the other shifts, the
        current date and time. A week starts on Sunday (the en-US default, seen live)."""
        clock = _Clock(self.dialect)
        if name in _NOW_FUNCTIONS:
            return clock.now(), "datetime"
        if name in _DATE_SHIFTS:
            x, kind = self.c(args[0])
            n = self._int_arg(args[1], node)
            unit, k = _DATE_SHIFTS[name]
            return clock.shift(unit, n * k, x, as_date=kind not in ("datetime",)), kind or "date"
        unit, span, when = _RELATIVE_TESTS[name]
        x = f"CAST({self.c(args[0])[0]} AS DATE)"
        n = self._int_arg(args[1], node) if span == "n" else 1
        today = clock.today()
        if unit == "day":
            start = today
        elif unit == "week":
            start = clock.week_start(today)
        else:
            start = clock.trunc(unit, today)
        step, k = ("day", 7) if unit == "week" else (
            ("month", {"month": 1, "quarter": 3, "year": 12}[unit]) if unit != "day"
            else ("day", 1))
        if when == "to_date":
            lo, hi, hi_op = start, today, "<="
        elif when == "previous":
            lo, hi, hi_op = clock.shift(step, -n * k, start), start, "<"
        elif when == "current":
            lo, hi, hi_op = start, clock.shift(step, k, start), "<"
        else:                                                                   # next
            lo = clock.shift(step, k, start)
            hi, hi_op = clock.shift(step, (n + 1) * k, start), "<"
        if unit == "day" and when == "next":
            lo, hi, hi_op = clock.shift("day", 1, today), clock.shift("day", n, today), "<="
        if unit == "day" and when == "current":
            return self.truth(f"({x} = {today})"), "logical"
        return self.truth(f"({x} >= {lo} AND {x} {hi_op} {hi})"), "logical"

    def _int_arg(self, node: tuple, call: tuple) -> int:
        v = self.ev.ev(node, self.env)
        if not isinstance(v, int) or isinstance(v, bool):
            raise Refused(f"{snippet(call)} takes a count the reader cannot evaluate")
        return v


# Dialects with no logical value type, where a comparison cannot be an argument.
_NO_BOOLEAN = frozenset(_dialects()["no_boolean"])

# How each dialect spells an unquoted identifier (the case it folds it to), and its unpivot
# of every column but some (a column list the M does not state); the M's floating-point
# values with no number literal (a division by zero).
_IDENT_FOLD: dict[str, str] = dict(_dialects().get("unquoted_identifier_case") or {})
_UNPIVOT_OTHERS: dict[str, str] = dict(_dialects().get("unpivot_other_columns") or {})
_FLOAT_SPECIALS: dict[str, dict[str, str]] = dict(_dialects().get("float_specials") or {})

# Number.Round's rounding modes, and the rounding functions, by the rounding they do.
# A RoundingMode only says which way a TIE goes (seen live: Number.Round(12.345, 1,
# RoundingMode.Up) is 12.3); the Number.Round* functions round every value one way.
_ROUNDING_MODES = {"RoundingMode.ToEven": "to_even", "RoundingMode.AwayFromZero": "away",
                   "RoundingMode.Up": "half_up", "RoundingMode.Down": "half_down",
                   "RoundingMode.TowardZero": "half_toward_zero"}
_ROUND_FUNCTIONS = {"Number.RoundUp": "up", "Number.RoundDown": "down",
                    "Number.RoundTowardZero": "toward_zero",
                    "Number.RoundAwayFromZero": "magnitude"}

# Date.IsIn* tests: (unit, "n" when it takes a count, which period).
_RELATIVE_TESTS: dict[str, tuple[str, str, str]] = {}
for _u, _U in (("day", "Day"), ("week", "Week"), ("month", "Month"), ("quarter", "Quarter"),
               ("year", "Year")):
    _RELATIVE_TESTS[f"Date.IsInPrevious{_U}"] = (_u, "1", "previous")
    _RELATIVE_TESTS[f"Date.IsInPreviousN{_U}s"] = (_u, "n", "previous")
    _RELATIVE_TESTS[f"Date.IsInCurrent{_U}"] = (_u, "1", "current")
    _RELATIVE_TESTS[f"Date.IsInNext{_U}"] = (_u, "1", "next")
    _RELATIVE_TESTS[f"Date.IsInNextN{_U}s"] = (_u, "n", "next")
_RELATIVE_TESTS["Date.IsInYearToDate"] = ("year", "1", "to_date")
# Date.Add*: (unit, how many of it one step is).
_DATE_SHIFTS = {"Date.AddDays": ("day", 1), "Date.AddWeeks": ("day", 7),
                "Date.AddMonths": ("month", 1), "Date.AddQuarters": ("month", 3),
                "Date.AddYears": ("month", 12)}
_NOW_FUNCTIONS = {"DateTime.LocalNow", "DateTime.FixedLocalNow", "DateTimeZone.UtcNow",
                  "DateTimeZone.FixedUtcNow", "DateTimeZone.LocalNow",
                  "DateTimeZone.FixedLocalNow"}


def _num_literal(v: Decimal) -> str:
    return format(v.normalize(), "f")


@functools.lru_cache(maxsize=1)
def _clock_rules():
    from metrics_agent.lib import rules as _rules  # noqa: PLC0415
    from metrics_agent.plugs.readers.bicompile import RULES  # noqa: PLC0415
    return _rules.load(RULES)


class _Clock:
    """The current moment and date arithmetic, as the dialect spells them: the compiler
    rules' `op.relative_range` row (`now`, UTC; `shift`; `trunc`; `weekday_iso`)."""

    def __init__(self, dialect: str | None):
        row = _clock_rules().operator("relative_range", dialect or "") or {}
        emit = row.get("emit") or {}
        d = str(dialect or "").lower()
        self.dialect = d
        self.forms = {k: (emit.get(k) or {}).get(d) for k in ("now", "shift", "trunc",
                                                             "weekday_iso")}
        if not all(self.forms.values()):
            raise Refused(f"a date relative to the refresh, which has no form for the "
                          f"{dialect or 'unknown'} dialect (the op.relative_range row)")

    def now(self) -> str:
        return self.forms["now"]

    def today(self) -> str:
        return f"CAST({self.forms['now']} AS DATE)"

    def shift(self, unit: str, n: int | str, x: str, as_date: bool = True) -> str:
        out = self.forms["shift"].format(p=unit, n=n, d=x)
        return f"CAST({out} AS DATE)" if as_date else out

    def trunc(self, unit: str, x: str) -> str:
        return f"CAST({self.forms['trunc'].format(p=unit, d=x)} AS DATE)"

    def week_start(self, x: str) -> str:
        """The Sunday on or before `x`."""
        iso = self.forms["weekday_iso"].format(x)
        from metrics_agent.plugs.readers.bicompile import _mod  # noqa: PLC0415
        return self.shift("day", "-" + _mod(iso, "7", self.dialect), x)


def round_even(x: str, digits: int, dialect: str | None) -> str:
    form = _ROUND_EVEN.get(dialect or "")
    if form is None:
        raise Refused(f"rounding half to even, which the reader does not write in "
                      f"{dialect or 'an unknown dialect'}")
    return form.format(x=x, d=digits, p=_num_literal(Decimal(10) ** digits))


# ── the tree -> one SQL statement ──────────────────────────────────────────────────────────

class _Writer:
    def __init__(self, ev: _Eval, types: dict[str, str]):
        self.ev = ev
        self.n = 0
        self.types = types           # M output name -> kind, from the model's columns

    def alias(self) -> str:
        self.n += 1
        return f"q{self.n}"

    # exact column sets, where the M states them
    def exact(self, r: Rel) -> list[str] | None:
        op = r.op
        if op in ("base", "native"):
            return r.args.get("_exact")
        if op == "union":
            sets = [self.exact(p) for p in r.args["parts"]]
            if any(s is None for s in sets):
                return None
            out: list[str] = []
            for s in sets:
                out += [c for c in s if c not in out]
            return out
        below = self.exact(r.child)
        if op == "group":
            return [*r.args["keys"], *(a for a, _ in r.args["aggs"])]
        if op == "select":
            return list(r.args["names"])
        if op == "unpivot" and r.args["keep"] is not None:
            return [*r.args["keep"], r.args["attr"], r.args["value"]]
        if below is None:
            return None
        if op == "rename_fn":
            return [self.ev.rename_by(r.args["fn"], c) for c in below]
        if op == "unpivot":
            return [*(c for c in below if c not in r.args["cols"]), r.args["attr"],
                    r.args["value"]]
        if op == "pivot":
            if r.args["values"] is None:
                return None
            return [*(c for c in below if c not in (r.args["attr"], r.args["value"])),
                    *r.args["values"]]
        if op == "extend":
            return [*below, r.args["name"]] if r.args["name"] not in below else below
        if op == "dup":
            return [*below, r.args["new"]]
        if op == "index":
            return [*below, r.args["name"]]
        if op == "rename":
            m = dict(r.args["pairs"])
            return [m.get(c, c) for c in below]
        if op == "prefix":
            return [f"{r.args['prefix']}.{c}" for c in below]
        if op == "remove":
            return [c for c in below if c not in r.args["names"]]
        if op == "join":
            exp = r.args.get("expand") or []
            return [*below, *(n for _, n in exp)]
        return below

    def infer(self, r: Rel, out: list[str] | None) -> None:
        """Carry the model's own column list (the root's exact set) down the tree, through
        every step that changes it predictably, so a step that needs the whole column list
        (a full-row Table.Distinct) finds it."""
        if out is None or r is None:
            return
        op = r.op
        if op in ("base", "native"):
            r.args.setdefault("_exact", list(out))
            return
        if op in ("pivot", "unpivot"):
            r.args["_out"] = list(out)
            a = r.args
            if op == "unpivot" and a["cols"] is not None:
                self.infer(r.child, [*(c for c in out if c not in (a["attr"], a["value"])),
                                     *a["cols"]])
            elif op == "pivot" and a["values"] is not None:
                self.infer(r.child, [*(c for c in out if c not in a["values"]), a["attr"],
                                     a["value"]])
            return
        if op == "rename_fn":
            self.infer(r.child, [self.ev.preimage(r.args["fn"], c, r.dialect) for c in out])
            return
        if op in ("group", "union"):
            return
        if op == "select":
            return
        if op == "extend":
            self.infer(r.child, [c for c in out if c != r.args["name"]])
        elif op == "dup":
            self.infer(r.child, [c for c in out if c != r.args["new"]])
        elif op == "index":
            self.infer(r.child, [c for c in out if c != r.args["name"]])
        elif op == "rename":
            back = {new: old for old, new in r.args["pairs"]}
            self.infer(r.child, [back.get(c, c) for c in out])
        elif op == "prefix":
            p = r.args["prefix"] + "."
            if all(c.startswith(p) for c in out):
                self.infer(r.child, [c[len(p):] for c in out])
        elif op == "remove":
            self.infer(r.child, [*out, *(c for c in r.args["names"] if c not in out)])
        elif op == "join":
            exp = {n for _, n in (r.args.get("expand") or [])}
            self.infer(r.child, [c for c in out if c not in exp])
        else:
            self.infer(r.child, out)

    def render(self, r: Rel, demand: list[str]) -> "_Layer":
        op = r.op
        if op == "base":
            a = self.alias()
            return _Layer(f"{r.args['table'].lower()} {a}", _Row({}, a, None,
                                                                  types=dict(self.types)))
        if op == "native":
            a = self.alias()
            return _Layer(f"(\n{_indent(r.args['sql'].strip().rstrip(';'))}\n) {a}",
                          _Row({}, a, None, types=dict(self.types)))
        if op == "union":
            return self.union(r, demand)
        if op == "group":
            return self.group(r, demand)
        if op == "distinct":
            return self.distinct(r, demand)
        if op == "limit":
            return self.limit(r, demand)
        if op == "join":
            return self.join(r, demand)
        if op == "fill":
            return self.fill(r, demand)
        if op == "unpivot":
            return self.unpivot(r, demand)
        if op == "pivot":
            return self.pivot(r, demand)
        if op == "replace":
            return self.replace(r, demand)
        if op == "rename_fn":
            return self.rename_fn(r, demand)
        if op == "index":
            return self.index(r, demand)
        if op == "on_error":
            return self.on_error(r, demand)
        if op == "filter":
            p, body, env = r.args["pred"]
            lay = self.open(self.render(r.child, self._grow(demand, body, p)), demand, body, p)
            comp = _ExprCompiler(self.ev, r.dialect, lay.row, p, env)
            # Each top-level `and` on a line of its own: the same condition, readable.
            for part in _conjuncts(body):
                lay.where.append(comp.c(part)[0])
            return lay
        if op == "extend":
            name = r.args["name"]
            p, body, env = r.args["expr"]
            below = [c for c in demand if c != name]
            lay = self.open(self.render(r.child, self._grow(below, body, p)), below, body, p)
            sql, kind, err = _ExprCompiler(self.ev, r.dialect, lay.row, p, env).errors_of(body)
            lay.row.cols[name] = sql
            lay.row.errs.pop(name, None)
            if err:
                lay.row.errs[name] = err
            lay.row.gone.discard(name)
            if lay.row.known is not None and name not in lay.row.known:
                lay.row.known.append(name)
            if kind:
                lay.row.types[name] = kind
            return lay
        if op == "transform":
            col = r.args["col"]
            p, body, env = r.args["expr"]
            lay = self.open(self.render(r.child, demand + [col]), demand + [col])
            sql, kind, err = _ExprCompiler(self.ev, r.dialect, lay.row, p, env,
                                           column=col).errors_of(body)
            lay.row.cols[col] = sql
            lay.row.errs.pop(col, None)
            if err:
                lay.row.errs[col] = err
            if kind:
                lay.row.types[col] = kind
            return lay
        if op == "dup":
            src, new = r.args["src"], r.args["new"]
            lay = self.render(r.child, [c for c in demand if c != new] + [src])
            lay.row.cols[new] = lay.row.ref(src)
            if lay.row.known is not None:
                lay.row.known.append(new)
            return lay
        if op == "rename":
            pairs = r.args["pairs"]
            back = {new: old for old, new in pairs}
            lay = self.render(r.child, [back.get(c, c) for c in demand])
            refs = {old: lay.row.ref(old) for old, _ in pairs}
            for old, _new in pairs:
                lay.row.cols.pop(old, None)
                lay.row.gone.add(old)
            for old, new in pairs:
                lay.row.cols[new] = refs[old]
                lay.row.gone.discard(new)
                if old in lay.row.types:
                    lay.row.types[new] = lay.row.types.pop(old)
            if lay.row.known is not None:
                m = dict(pairs)
                lay.row.known = [m.get(c, c) for c in lay.row.known]
            return lay
        if op == "prefix":
            p = r.args["prefix"] + "."
            lay = self.render(r.child, [c[len(p):] if c.startswith(p) else c for c in demand])
            for c in demand:
                if c.startswith(p):
                    lay.row.cols[c] = lay.row.ref(c[len(p):])
            return lay
        if op == "cast":
            col, kind = r.args["col"], r.args["to"]
            lay = self.render(r.child, demand + [col])
            x = lay.row.ref(col)
            lay.row.cols[col] = _cast(x, kind, r.dialect)
            lay.row.types[col] = "int" if kind == "int" else kind
            return lay
        if op == "remove":
            lay = self.render(r.child, demand)
            for c in r.args["names"]:
                lay.row.cols.pop(c, None)
                lay.row.gone.add(c)
                if lay.row.known is not None and c in lay.row.known:
                    lay.row.known.remove(c)
            return lay
        if op == "select":
            names = r.args["names"]
            lay = self.render(r.child, [c for c in demand if c in names] or names)
            keep = {c: lay.row.ref(c) for c in names}
            lay.row.cols = keep
            lay.row.known = list(names)
            return lay
        if op == "sort":
            return self.render(r.child, demand)
        raise Refused(f"the step {op}")

    @staticmethod
    def _grow(demand: list[str], body: tuple, param: str) -> list[str]:
        return list(dict.fromkeys(demand + _fields_of(body, param)))

    def open(self, lay: "_Layer", demand: list[str], body: tuple | None = None,
             param: str = "_") -> "_Layer":
        """A layer that is closed (grouped, distinct, a union, a join's output) is wrapped so
        the next step reads its rows."""
        if not lay.closed:
            return lay
        need = self._grow(demand, body, param) if body is not None else list(demand)
        return self.wrap(lay, need)

    def wrap(self, lay: "_Layer", need: list[str]) -> "_Layer":
        cols = lay.row.known if lay.row.known is not None else need
        sql = lay.sql(cols)
        a = self.alias()
        # A column whose value is an M error where a condition holds keeps it through the
        # wrap: the condition is a hidden flag column of the inner statement.
        flags = {c: e for c, e in lay.row.errs.items() if c in cols}
        if flags:
            head, sep, tail = sql.partition("\nFROM   ")
            head += "".join(f",\n       {_err_flag(e)} AS {_ident(_ERR + c)}"
                            for c, e in flags.items())
            sql = head + sep + tail
        return _Layer(f"(\n{_indent(sql)}\n) {a}",
                      _Row({}, a, list(cols) if lay.row.known is not None else None,
                           types=dict(lay.row.types),
                           errs={c: f"{a}.{_ident(_ERR + c)} = 1" for c in flags}))

    def group(self, r: Rel, demand: list[str]) -> "_Layer":
        keys, aggs = r.args["keys"], r.args["aggs"]
        need = list(keys)
        for _n, (p, body, _e) in aggs:
            need = self._grow(need, body, p)
        order = r.args.get("order")
        if r.args.get("local"):
            need = list(dict.fromkeys(need + [k for k, _d in order or []]))
        lay = self.render(r.child, need)
        lay = self.open(lay, need)
        if r.args.get("ci") and keys:
            lay = self._representatives(lay, need, keys, order)
        island = None
        if r.args.get("local"):
            lay, island = self._runs(lay, need, keys, order, bool(r.args.get("ci")))
        sel = [f"{lay.row.ref(k)} AS {_ident(k)}" for k in keys]
        types = {k: lay.row.types[k] for k in keys if k in lay.row.types}
        flags: dict[str, str] = {}
        for name, (p, body, env) in aggs:
            sel.append(f"{self.aggregate(r, lay.row, p, body, env)} AS {_ident(name)}")
            types[name] = "number"
            # An aggregate over a column holding an M error is an error (the list holds it).
            read = [lay.row.errs[c] for c in _fields_of(body, p) if c in lay.row.errs]
            if read:
                flags[name] = " OR ".join(f"({e})" for e in read)
        for name, e in flags.items():
            sel.append(f"MAX({_err_flag(e)}) AS {_ident(_ERR + name)}")
        sql = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {lay.from_sql}" + lay.tail()
        if keys or island:
            sql += "\nGROUP BY " + ", ".join([lay.row.ref(k) for k in keys]
                                             + ([island] if island else []))
        a = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {a}",
                      _Row({}, a, [*keys, *(n for n, _ in aggs)], types=types,
                           errs={n: f"{a}.{_ident(_ERR + n)} = 1" for n in flags}))

    def _runs(self, lay: "_Layer", need: list[str], keys: list[str],
              order: list[tuple[str, bool]], ci: bool) -> tuple["_Layer", str]:
        """GroupKind.Local: each run of adjacent rows (along the sort) with one key is a
        group. A row's place in the table minus its place among the rows of its key is the
        same along a run and differs between runs (gaps and islands); a null key is a key."""
        ob = ", ".join(f"{lay.row.ref(k)}{' DESC' if d else ''}" for k, d in order)
        part = ", ".join(self._fold(lay.row.ref(k), lay.row.types.get(k)) if ci
                         else lay.row.ref(k) for k in keys)
        sel = [f"{lay.row.ref(c)} AS {_ident(c)}" for c in need]
        sel.append(f"ROW_NUMBER() OVER (ORDER BY {ob})"
                   + (f" - ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY {ob})" if keys
                      else "") + " AS local_run")
        sql = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {lay.from_sql}" + lay.tail()
        a = self.alias()
        return (_Layer(f"(\n{_indent(sql)}\n) {a}",
                       _Row({}, a, list(need), types=dict(lay.row.types))), f"{a}.local_run")

    def _fold(self, x: str, kind: str | None) -> str:
        """A key as a comparer ignoring case compares it."""
        return f"UPPER({x})" if kind in (None, "text") else x

    def _representatives(self, lay: "_Layer", need: list[str], keys: list[str],
                         order: list[tuple[str, bool]] | None) -> "_Layer":
        """Keys compared ignoring case: every row's key replaced by its group's first spelling
        in the table's order (the sort; seen live), or by the least one when the rows have
        no order (Import shows one spelling per value then anyway)."""
        part = ", ".join(self._fold(lay.row.ref(k), lay.row.types.get(k)) for k in keys)
        sel = []
        for c in need:
            x = lay.row.ref(c)
            if c in keys:
                if order:
                    ob = ", ".join(f"{lay.row.ref(k)}{' DESC' if d else ''}" for k, d in order)
                    x = f"FIRST_VALUE({x}) OVER (PARTITION BY {part} ORDER BY {ob})"
                else:
                    x = f"MIN({x}) OVER (PARTITION BY {part})"
            sel.append(f"{x} AS {_ident(c)}")
        sql = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {lay.from_sql}" + lay.tail()
        a = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {a}",
                      _Row({}, a, list(need), types=dict(lay.row.types)))

    def aggregate(self, r: Rel, row: _Row, p: str, body: tuple, env: Env) -> str:
        """`each List.Sum([AMOUNT])`, `each Table.RowCount(_)` ... over the group's rows."""
        if body[0] != "call" or body[1][0] != "ident":
            raise Refused(f"the Table.Group aggregation {snippet(body)}")
        fn, args = body[1][1], body[2]
        if fn in ("Table.RowCount", "List.Count") and len(args) == 1:
            inner = args[0]
            if fn == "Table.RowCount" and inner == ("ident", p):
                return "COUNT(*)"
            if fn == "List.Count" and inner[0] == "field":
                return "COUNT(*)"
            if (fn == "List.Count" and inner[0] == "call" and inner[1] == ("ident", "List.Distinct")
                    and inner[2] and inner[2][0][0] == "field"):
                x = row.ref(inner[2][0][2])
                # M's List.Distinct keeps null as a value of its own.
                return (f"(COUNT(DISTINCT {x}) + CASE WHEN COUNT({x}) < COUNT(*) THEN 1 "
                        f"ELSE 0 END)")
        if fn == "List.NonNullCount" and len(args) == 1 and args[0][0] == "field":
            return f"COUNT({row.ref(args[0][2])})"
        sql_fn = {"List.Sum": "SUM", "List.Average": "AVG", "List.Min": "MIN",
                  "List.Max": "MAX"}.get(fn)
        if sql_fn and len(args) == 1 and args[0][0] == "field":
            return f"{sql_fn}({row.ref(args[0][2])})"
        raise Refused(f"the Table.Group aggregation {snippet(body)}")

    def limit(self, r: Rel, demand: list[str]) -> "_Layer":
        """ORDER BY the sort's keys, LIMIT n, over every column the table holds."""
        keys = r.args["keys"]
        # Which rows are kept depends on the sort keys alone: the columns asked for and the
        # keys are enough (the whole column list where the M states it).
        full = self.exact(r.child) or list(dict.fromkeys(list(demand) + [c for c, _d in keys]))
        lay = self.open(self.render(r.child, list(dict.fromkeys(
            list(full) + [c for c, _d in keys]))), list(full))
        order = ", ".join(f"{lay.row.ref(c)}{' DESC' if d else ''}" for c, d in keys)
        cols = ",\n       ".join(f"{lay.row.ref(c)} AS {_ident(c)}" for c in full)
        n, skip = r.args.get("n"), int(r.args.get("skip") or 0)
        if not skip:
            sql = (f"SELECT {cols}\nFROM   {lay.from_sql}" + lay.tail()
                   + f"\nORDER BY {order}\nLIMIT {int(n)}")
        else:
            # Rows past the first `skip` (and at most `n` of them): their position in the
            # sort, as a row number, which every dialect spells the same way.
            inner = (f"SELECT {cols},\n       ROW_NUMBER() OVER (ORDER BY {order}) AS row_pos"
                     f"\nFROM   {lay.from_sql}" + lay.tail())
            keep = f"row_pos > {skip}" + (f" AND row_pos <= {skip + int(n)}"
                                          if n is not None else "")
            b = self.alias()
            sql = ("SELECT " + ",\n       ".join(f"{b}.{_ident(c)}" for c in full)
                   + f"\nFROM   (\n{_indent(inner)}\n) {b}\nWHERE  {keep}")
        a = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {a}",
                      _Row({}, a, list(full), types=dict(lay.row.types)))

    def distinct(self, r: Rel, demand: list[str]) -> "_Layer":
        cols = r.args.get("cols")
        ci = r.args.get("ci") or set()
        order = r.args.get("order")
        full = self.exact(r.child)
        if cols is not None and (full is None or set(cols) != set(full)):
            if not order:
                raise Refused(f"Table.Distinct on {cols} keeps one row per key, and which row "
                              f"is not fixed: the rows have no order without a Table.Sort "
                              f"before it")
            return self._first_per_key(r, demand, cols, ci, order)
        if full is None:
            raise Refused("Table.Distinct over the whole row needs the table's full column "
                          "list, which the M does not state")
        if ci:
            return self._first_per_key(r, list(full), list(full), {"*"}, order)
        lay = self.open(self.render(r.child, list(full)), list(full))
        _distinct_errors(lay, list(full))
        sql = ("SELECT DISTINCT " + ",\n       ".join(
            f"{lay.row.ref(c)} AS {_ident(c)}" for c in full) + f"\nFROM   {lay.from_sql}"
               + lay.tail())
        a = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {a}",
                      _Row({}, a, list(full), types=dict(lay.row.types)))

    def _first_per_key(self, r: Rel, demand: list[str], keys: list[str], ci: set[str],
                       order: list[tuple[str, bool]] | None) -> "_Layer":
        """One row per key: the first in the table's order (the sort's keys; seen live), keys
        compared ignoring case where a comparer says so."""
        need = list(dict.fromkeys(list(demand) + list(keys) + [c for c, _d in order or []]))
        lay = self.open(self.render(r.child, need), need)
        part = ", ".join(self._fold(lay.row.ref(k), lay.row.types.get(k))
                         if ("*" in ci or k in ci) else lay.row.ref(k) for k in keys)
        ob = ", ".join(f"{lay.row.ref(c)}{' DESC' if d else ''}" for c, d in order or []) or \
            ", ".join(lay.row.ref(k) for k in keys)
        _distinct_errors(lay, list(keys))
        errs = {c: lay.row.errs[c] for c in need if c in lay.row.errs}
        inner = ("SELECT " + ",\n       ".join(
            [f"{lay.row.ref(c)} AS {_ident(c)}" for c in need]
            + [f"{_err_flag(e)} AS {_ident(_ERR + c)}" for c, e in errs.items()])
                 + f",\n       ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY {ob}) AS row_pos"
                 + f"\nFROM   {lay.from_sql}" + lay.tail())
        b = self.alias()
        sql = ("SELECT " + ",\n       ".join([f"{b}.{_ident(c)}" for c in need]
                                            + [f"{b}.{_ident(_ERR + c)}" for c in errs])
               + f"\nFROM   (\n{_indent(inner)}\n) {b}\nWHERE  {b}.row_pos = 1")
        a = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {a}",
                      _Row({}, a, list(need), types=dict(lay.row.types),
                           errs={c: f"{a}.{_ident(_ERR + c)} = 1" for c in errs}))

    def join(self, r: Rel, demand: list[str]) -> "_Layer":
        args = r.args
        expand = args.get("expand") or []
        new_names = {n for _, n in expand}
        if args["new"] in demand and not expand:
            raise Refused(f"the merged column {args['new']!r} is a nested table the model "
                          f"cannot hold")
        kind = args["kind"]
        left_need = list(dict.fromkeys([c for c in demand if c not in new_names] + args["lk"]))
        right_need = list(dict.fromkeys([o for o, _ in expand] + args["rk"]))
        left = self.wrap(self._close(self.render(r.child, left_need)), left_need)
        right = self.wrap(self._close(self.render(args["right"], right_need)), right_need)
        la, ra = left.row.alias, right.row.alias
        fold = (lambda x, k: self._fold(x, k)) if args.get("ci") else (lambda x, k: x)
        on = " AND ".join(
            f"{fold(left.row.ref(lk), left.row.types.get(lk))} IS NOT DISTINCT FROM "
            f"{fold(right.row.ref(rk), right.row.types.get(rk))}"
            for lk, rk in zip(args["lk"], args["rk"]))
        if kind in ("LeftAnti", "RightAnti"):
            if expand:
                raise Refused(f"an anti merge ({kind}) whose columns are expanded")
            keep, other = (left, right) if kind == "LeftAnti" else (right, left)
            from_sql = (f"{keep.from_sql}\nWHERE  NOT EXISTS (\n  SELECT 1 FROM "
                        f"{other.from_sql}\n  WHERE {on})")
            row = keep.row.copy()
            if kind == "RightAnti":
                raise Refused("a right anti merge keeps the other table's rows")
            return _Layer(from_sql, row, where_in_from=True)
        kw = {"LeftOuter": "LEFT JOIN", "Inner": "INNER JOIN", "FullOuter": "FULL OUTER JOIN",
              "RightOuter": "RIGHT JOIN"}.get(kind)
        if kw is None:
            raise Refused(f"a merge of kind {kind}")
        row = _Row({c: left.row.ref(c) for c in left_need}, la, None,
                   types=dict(left.row.types),
                   errs={c: e for c, e in left.row.errs.items() if c in left_need})
        for orig, new in expand:
            row.cols[new] = right.row.ref(orig)
            if orig in right.row.types:
                row.types[new] = right.row.types[orig]
            if orig in right.row.errs:
                # A merged row's expanded column is an error where the other table's is.
                row.errs[new] = right.row.errs[orig]
        row.gone.add(args["new"])
        known = self.exact(r)
        row.known = known
        return _Layer(f"{left.from_sql}\n{kw} {right.from_sql}\n  ON {on}", row)

    def index(self, r: Rel, demand: list[str]) -> "_Layer":
        """Table.AddIndexColumn: each row's position in the table's order (its sort), from
        `start` by `step`."""
        a = r.args
        name = a["name"]
        below = [c for c in demand if c != name]
        full = self.exact(r.child)
        need = list(dict.fromkeys((full or below) + [c for c, _d in a["keys"]]))
        lay = self.open(self.render(r.child, need), need)
        order = ", ".join(f"{lay.row.ref(c)}{' DESC' if d else ''}" for c, d in a["keys"])
        pos = f"ROW_NUMBER() OVER (ORDER BY {order})"
        sel = [f"{lay.row.ref(c)} AS {_ident(c)}" for c in need]
        sel.append(f"({_num_literal(Decimal(a['start']))} + ({pos} - 1) * "
                   f"{_num_literal(Decimal(a['step']))}) AS {_ident(name)}")
        sql = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {lay.from_sql}" + lay.tail()
        al = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {al}",
                      _Row({}, al, [*need, name], types={**lay.row.types, name: "int"}))

    def on_error(self, r: Rel, demand: list[str]) -> "_Layer":
        """Table.ReplaceErrorValues / RemoveRowsWithErrors: the rows where a column's value
        is an M error (as the reader models the steps' errors) take the value / are dropped.
        A column no step makes an error is left as it is."""
        a = r.args
        need = list(dict.fromkeys(demand + list(a["cols"] or [])))
        lay = self.render(r.child, need)
        cols = a["cols"] if a["cols"] is not None else list(lay.row.errs)
        if a["replace"] is not None:
            for col, v in a["replace"].items():
                err = lay.row.errs.pop(col, None)
                if err:
                    lit = _ExprCompiler(self.ev, r.dialect, lay.row, "\0none",
                                        Env({}, None, self.ev.ctx)).lit(v)[0]
                    lay.row.cols[col] = f"CASE WHEN {err} THEN {lit} ELSE {lay.row.ref(col)} END"
            return lay
        errs = [lay.row.errs.pop(c) for c in cols if c in lay.row.errs]
        if errs:
            lay = self.open(lay, need)
            lay.where.append("NOT COALESCE(" + " OR ".join(f"({e})" for e in errs) + ", FALSE)")
        return lay

    def rename_fn(self, r: Rel, demand: list[str]) -> "_Layer":
        fn = r.args["fn"]
        pre = {c: self.ev.preimage(fn, c, r.dialect) for c in demand}
        lay = self.render(r.child, list(dict.fromkeys(pre.values())))
        refs = {c: lay.row.ref(pre[c]) for c in demand}
        types = {c: lay.row.types[pre[c]] for c in demand if pre[c] in lay.row.types}
        for old in pre.values():
            lay.row.cols.pop(old, None)
            lay.row.gone.add(old)
        for c in demand:
            lay.row.cols[c] = refs[c]
            lay.row.gone.discard(c)
        lay.row.types.update(types)
        if lay.row.known is not None:
            lay.row.known = [self.ev.rename_by(fn, c) for c in lay.row.known]
        return lay

    def _operand(self, op: tuple, row: _Row, dialect: str | None) -> tuple[str, str | None]:
        if op[0] == "value":
            return _ExprCompiler(self.ev, dialect, row, "\0none", Env({}, None, self.ev.ctx)
                                 ).lit(op[1])
        _k, param, body, env = op
        return _ExprCompiler(self.ev, dialect, row, param, env).c(body)

    def replace(self, r: Rel, demand: list[str]) -> "_Layer":
        a = r.args
        need = list(dict.fromkeys(demand + a["cols"]))
        for o in (a["old"], a["new"]):
            if o[0] == "row":
                need = self._grow(need, o[2], o[1])
        lay = self.open(self.render(r.child, need), need)
        row = lay.row
        old, ko = self._operand(a["old"], row, r.dialect)
        new, kn = self._operand(a["new"], row, r.dialect)
        out: dict[str, tuple[str, str | None]] = {}
        for col in a["cols"]:
            x, kx = row.ref(col), row.types.get(col)
            how = a["how"]
            if isinstance(how, Fn):
                params = how.params
                subst = {params[0]: (x, kx), params[1]: (old, ko), params[2]: (new, kn)}
                for p in params[3:]:
                    subst[p] = ("NULL", "null")
                out[col] = (_ExprCompiler(self.ev, r.dialect, row, "\0none", how.env, None,
                                          subst).c(how.body)[0], kx or kn)
            elif how == "ReplaceText":
                if kx not in (None, "text"):
                    raise Refused(f"Table.ReplaceValue replaces text in the {kx} column {col!r}")
                if a["old"][0] == "value" and not (isinstance(a["old"][1], str) and a["old"][1]):
                    raise Refused("Table.ReplaceValue with Replacer.ReplaceText looks for "
                                  "something that is not text")
                out[col] = (f"REPLACE({x}, {old}, {new})", "text")
            elif a["old"][0] == "value" and a["old"][1] is None:
                out[col] = (f"CASE WHEN {x} IS NULL THEN {new} ELSE {x} END", kx or kn)
            elif a["old"][0] == "value" and kx and ko and (kx == "text") != (ko == "text"):
                out[col] = (x, kx)              # M's `=` across text and a number is false
            elif a["old"][0] == "value":
                out[col] = (f"CASE WHEN {x} = {old} THEN {new} ELSE {x} END", kx or kn)
            else:
                out[col] = (f"CASE WHEN {x} IS NOT DISTINCT FROM {old} THEN {new} ELSE {x} END",
                            kx or kn)
        for col, (sql, kind) in out.items():
            row.cols[col] = sql
            if kind and kind != "null":
                row.types[col] = kind
        return lay

    def fill(self, r: Rel, demand: list[str]) -> "_Layer":
        """FillDown / FillUp: a running count of the column's values along the sort (backwards
        for FillUp) numbers each run of nulls with the value that starts it; the run's one
        value fills it. Every dialect spells both windows the same way."""
        a = r.args
        cols, keys, up = a["cols"], a["keys"], a["up"]
        full = self.exact(r.child)
        need = list(dict.fromkeys((full or demand) + cols + [c for c, _d in keys]))
        lay = self.open(self.render(r.child, need), need)
        order = ", ".join(f"{lay.row.ref(c)}{' DESC' if d != up else ''}" for c, d in keys)
        sel = [f"{lay.row.ref(c)} AS {_ident(c)}" for c in need]
        # An M error in a cell is no null: it starts a run of its own, and the nulls below it
        # are filled with it (the error), so the flag follows the run as the value does.
        errs = {c: lay.row.errs[c] for c in need if c in lay.row.errs}
        sel += [f"{_err_flag(e)} AS {_ident(_ERR + c)}" for c, e in errs.items()]
        runs = {}
        for i, c in enumerate(cols):
            runs[c] = f"fill_run_{i}"
            held = (f"CASE WHEN {lay.row.ref(c)} IS NOT NULL OR COALESCE(({errs[c]}), FALSE) "
                    f"THEN 1 END" if c in errs else lay.row.ref(c))
            sel.append(f"COUNT({held}) OVER (ORDER BY {order} ROWS BETWEEN UNBOUNDED "
                       f"PRECEDING AND CURRENT ROW) AS {runs[c]}")
        inner = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {lay.from_sql}" + lay.tail()
        b = self.alias()
        outer = [(f"MAX({b}.{_ident(c)}) OVER (PARTITION BY {b}.{runs[c]}) AS {_ident(c)}"
                  if c in runs else f"{b}.{_ident(c)}") for c in need]
        outer += [(f"MAX({b}.{_ident(_ERR + c)}) OVER (PARTITION BY {b}.{runs[c]})"
                   if c in runs else f"{b}.{_ident(_ERR + c)}") + f" AS {_ident(_ERR + c)}"
                  for c in errs]
        sql = ("SELECT " + ",\n       ".join(outer) + f"\nFROM   (\n{_indent(inner)}\n) {b}")
        al = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {al}",
                      _Row({}, al, list(need), types=dict(lay.row.types),
                           errs={c: f"{al}.{_ident(_ERR + c)} = 1" for c in errs}))

    def unpivot(self, r: Rel, demand: list[str]) -> "_Layer":
        a = r.args
        attr, value = a["attr"], a["value"]
        cols = a["cols"]
        full = self.exact(r.child)
        if cols is None and full is not None:
            cols = [c for c in full if c not in a["keep"]]
        if cols is None:
            return self._unpivot_dynamic(r, demand)
        out = a.get("_out") or demand
        others = ([c for c in full if c not in cols] if full is not None
                  else [c for c in out if c not in (attr, value) and c not in cols])
        parts, kind = [], None
        for col in cols:
            lay = self.open(self.render(r.child, others + [col]), others + [col])
            kind = kind or lay.row.types.get(col)
            err = lay.row.errs.get(col)
            # A cell holding an M error is no null: its row is kept, its value the error.
            lay.where.append(f"({lay.row.ref(col)} IS NOT NULL OR COALESCE(({err}), FALSE))"
                             if err else f"{lay.row.ref(col)} IS NOT NULL")
            sel = [f"{lay.row.ref(o)} AS {_ident(o)}" for o in others]
            sel += [f"{_quote_text(col, r.dialect)} AS {_ident(attr)}",
                    f"{lay.row.ref(col)} AS {_ident(value)}"]
            flags = {o: lay.row.errs[o] for o in others if o in lay.row.errs}
            if err:
                flags[value] = err
            parts.append((sel, flags, f"\nFROM   {lay.from_sql}" + lay.tail()))
        # Every part carries the same flags (0 where a part has none).
        carry = list(dict.fromkeys(c for _s, f, _t in parts for c in f))
        bodies = ["SELECT " + ",\n       ".join(
            sel + [(_err_flag(f[c]) if c in f else "0") + f" AS {_ident(_ERR + c)}"
                   for c in carry]) + tail for sel, f, tail in parts]
        al = self.alias()
        types = {attr: "text", **({value: kind} if kind else {})}
        return _Layer(f"(\n{_indent(_union_all(bodies))}\n) {al}",
                      _Row({}, al, [*others, attr, value], types=types,
                           errs={c: f"{al}.{_ident(_ERR + c)} = 1" for c in carry}))

    def _unpivot_dynamic(self, r: Rel, demand: list[str]) -> "_Layer":
        """UnpivotOtherColumns of a table whose column list the M does not state: the
        dialect's own unpivot of every column but the kept ones (reader_dialects.yml)."""
        a = r.args
        form = _UNPIVOT_OTHERS.get(str(r.dialect or "").lower())
        if not form:
            raise Refused(f"Table.UnpivotOtherColumns of a table whose columns the M does not "
                          f"list, which has no form for the {r.dialect or 'unknown'} dialect")
        keep = a["keep"]
        lay = self.render(r.child, list(keep))
        extra = [c for c in lay.row.cols if c not in keep]
        if lay.closed or extra:
            raise Refused(f"Table.UnpivotOtherColumns after a step that adds or changes "
                          f"columns ({', '.join(extra) or 'a closed step'}) of a table whose "
                          f"columns the M does not list")
        al_in = lay.row.alias
        gone = sorted(lay.row.gone)
        star = (f"{al_in}.* EXCLUDE ({', '.join(_ident(g) for g in gone)})" if gone
                else f"{al_in}.*")
        keep_refs = {k: lay.row.ref(k) for k in keep}
        if any(v != f"{al_in}.{_ident(k)}" for k, v in keep_refs.items()):
            raise Refused("Table.UnpivotOtherColumns keeps a column a step before it renamed, "
                          "of a table whose columns the M does not list")
        source = f"SELECT {star}\nFROM   {lay.from_sql}" + lay.tail()
        body = form["sql"].format(
            source=_indent(source, "        "), attr=_ident(a["attr"]),
            value=_ident(a["value"]), keep_list=", ".join(_ident(k) for k in keep),
            keep_names=", ".join(_quote_text(k, r.dialect) for k in keep),
            keep_cols=", ".join(f"u.{_ident(k)}" for k in keep)).rstrip()
        al = self.alias()
        row = _Row({}, al, [*keep, a["attr"], a["value"]], types={a["attr"]: "text"})
        kind = self.types.get(a["value"])
        cast = (form.get("value_cast") or {}).get(kind or "")
        if cast:
            row.cols[a["value"]] = cast.format(x=f"{al}.{_ident(a['value'])}")
        if kind:
            row.types[a["value"]] = kind
        lay2 = _Layer(f"(\n{_indent(body)}\n) {al}", row)
        return self.wrap(lay2, [*keep, a["attr"], a["value"]]) if cast else lay2

    def pivot(self, r: Rel, demand: list[str]) -> "_Layer":
        a = r.args
        attr, value, values = a["attr"], a["value"], a["values"]
        full = self.exact(r.child)
        out = a.get("_out") or demand
        if full is not None:
            others = [c for c in full if c not in (attr, value)]
        elif values is not None:
            others = [c for c in out if c not in values]
        else:
            raise Refused("Table.Pivot takes its columns from the values in the data "
                          "(List.Distinct) of a table whose other columns the M does not "
                          "list, so which model columns are pivoted values is not stated")
        if values is None:
            values = [c for c in out if c not in others]
        lay = self.open(self.render(r.child, others + [attr, value]), others + [attr, value])
        at, va = lay.row.ref(attr), lay.row.ref(value)
        agg = _PIVOT_AGGS.get(a["agg"] or "", "MAX")
        sel = [f"{lay.row.ref(o)} AS {_ident(o)}" for o in others]
        types = {o: lay.row.types[o] for o in others if o in lay.row.types}
        for v in values:
            hit = f"{at} = {_quote_text(v, r.dialect)}"
            if agg == "COUNT":
                expr = f"COUNT(CASE WHEN {hit} THEN 1 END)"
                types[v] = "int"
            elif agg == "COUNT_NONNULL":
                expr = f"COUNT(CASE WHEN {hit} THEN {va} END)"
                types[v] = "int"
            else:
                expr = f"{agg}(CASE WHEN {hit} THEN {va} END)"
                if va and lay.row.types.get(value):
                    types[v] = lay.row.types[value]
            sel.append(f"{expr} AS {_ident(v)}")
        # An aggregate over a cell holding an M error is an error (the list holds it); a
        # count of the cells is not.
        err = lay.row.errs.get(value)
        flags = []
        if err and agg not in ("COUNT", "COUNT_NONNULL"):
            for v in values:
                hit = f"{at} = {_quote_text(v, r.dialect)}"
                sel.append(f"MAX(CASE WHEN {hit} THEN {_err_flag(err)} ELSE 0 END) AS "
                           f"{_ident(_ERR + v)}")
                flags.append(v)
        if any(o in lay.row.errs for o in others):
            raise Refused("Table.Pivot keyed by a column holding an M error (a row per key "
                          "value: the error's own row is not one M writes)", hard=True)
        sql = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {lay.from_sql}" + lay.tail()
        if others:
            sql += "\nGROUP BY " + ", ".join(lay.row.ref(o) for o in others)
        al = self.alias()
        return _Layer(f"(\n{_indent(sql)}\n) {al}",
                      _Row({}, al, [*others, *values], types=types,
                           errs={v: f"{al}.{_ident(_ERR + v)} = 1" for v in flags}))

    def _close(self, lay: "_Layer") -> "_Layer":
        lay.closed = True
        return lay

    def union(self, r: Rel, demand: list[str]) -> "_Layer":
        parts = r.args["parts"]
        cols = self.exact(r) or demand
        bodies = []
        types: dict[str, str] = {}
        layers = []
        for p in parts:
            known = self.exact(p)
            lay = self.render(p, [c for c in cols if known is None or c in known])
            layers.append((known, self.open(lay, cols)))
        # A column some part makes an M error carries a flag in every part (0 where none).
        flagged = [c for c in cols if any(c in lay.row.errs for _k, lay in layers)]
        for known, lay in layers:
            sel = []
            for c in cols:
                if known is not None and c not in known:
                    sel.append(f"NULL AS {_ident(c)}")
                else:
                    sel.append(f"{lay.row.ref(c)} AS {_ident(c)}")
                    if c in lay.row.types:
                        types.setdefault(c, lay.row.types[c])
            for c in flagged:
                e = lay.row.errs.get(c)
                sel.append(f"{_err_flag(e) if e else '0'} AS {_ident(_ERR + c)}")
            bodies.append("SELECT " + ",\n       ".join(sel) + f"\nFROM   {lay.from_sql}"
                          + lay.tail())
        a = self.alias()
        union = "\nUNION ALL\n".join(bodies)
        return _Layer(f"(\n{_indent(union)}\n) {a}",
                      _Row({}, a, list(cols), types=types,
                           errs={c: f"{a}.{_ident(_ERR + c)} = 1" for c in flagged}))


# The hidden column that carries a column's M-error condition through a closed step.
_ERR = "err__"


def _distinct_errors(lay: "_Layer", compared: list[str]) -> None:
    """Table.Distinct compares the rows' values, and comparing an M error raises it: seen live,
    the refresh fails even with the errors replaced after the step. A compared column that
    holds an error is refused by name; no SQL value stands for a failed refresh."""
    held = [c for c in compared if c in lay.row.errs]
    if held:
        raise Refused(f"Table.Distinct compares {', '.join(held)}, which holds an M error "
                      f"where a step before it raises one; comparing it raises the error, so "
                      f"the model's refresh fails there", hard=True)


def _err_flag(cond: str) -> str:
    return f"CASE WHEN COALESCE(({cond}), FALSE) THEN 1 ELSE 0 END"


@dataclass
class _Layer:
    from_sql: str
    row: _Row
    where: list[str] = field(default_factory=list)
    closed: bool = False
    where_in_from: bool = False

    def sql(self, cols: list[str], aliases: dict[str, str] | None = None) -> str:
        sel = [f"{self.row.ref(c)} AS {(aliases or {}).get(c) or _ident(c)}" for c in cols]
        return "SELECT " + ",\n       ".join(sel) + f"\nFROM   {self.from_sql}" + self.tail()

    def tail(self) -> str:
        if not self.where:
            return ""
        kw = "\n  AND  " if self.where_in_from else "\nWHERE  "
        return kw + "\n  AND  ".join(self.where)


def _union_all(bodies: list[str]) -> str:
    return "\nUNION ALL\n".join(bodies)


def _indent(sql: str, pad: str = "    ") -> str:
    return "\n".join(pad + ln if ln.strip() else ln for ln in sql.splitlines())


def _conjuncts(node: tuple) -> list[tuple]:
    if node[0] == "bin" and node[1] == "and":
        return _conjuncts(node[2]) + _conjuncts(node[3])
    return [node]


def _fields_of(body: tuple, param: str) -> list[str]:
    out: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, tuple):
            if n and n[0] == "field" and (n[1] is None or n[1] == ("ident", param)):
                out.append(n[2])
            for x in n[1:]:
                walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)
    walk(body)
    return list(dict.fromkeys(out))


def _cast(x: str, kind: str, dialect: str | None) -> str:
    """A column conversion. Folded to the warehouse, as Power Query folds it from a warehouse
    source, a whole number is the warehouse's cast: half away from zero (seen live)."""
    if kind in _IDENTITY_TYPES:
        return x
    if kind == "int":
        return f"ROUND({x}, 0)"
    if kind == "currency":
        return f"ROUND({x}, 4)"
    if kind == "date":
        return f"CAST({x} AS DATE)"
    raise Refused(f"a conversion to {kind}, which the reader does not write")


# ── the entry point ────────────────────────────────────────────────────────────────────────

@dataclass
class TableSource:
    kind: str                         # legacy | plain | statement | refused
    reason: str | None = None
    sql: str | None = None            # statement, or the native SQL of a plain native table
    native_sql: str | None = None
    renames: dict[str, str] = field(default_factory=dict)   # M output -> warehouse column
    outputs: dict[str, str] = field(default_factory=dict)   # model source column -> output
    reads: list[str] = field(default_factory=list)
    dialect: str | None = None
    db: str | None = None
    schema: str | None = None
    base_object: str | None = None
    origin: str | None = None         # the query whose M holds the navigation


def parameter_values(expressions: dict[str, str]) -> dict[str, Any]:
    """The model's parameters (`<value> meta [IsParameterQuery=true, ...]`) -> their values:
    text, numbers, logicals, dates and lists of them."""
    out: dict[str, Any] = {}
    for name, text in (expressions or {}).items():
        if not re.search(r"IsParameterQuery\s*=\s*true", text or "", re.I):
            continue
        try:
            node = parse(text)
            out[name] = _Eval(_Ctx({}, {}, [])).ev(node, Env({}, None, _Ctx({}, {}, [])))
        except (MSyntaxError, Refused):
            continue
    return out


def read_table(m_text: str, *, expressions: dict[str, str] | None = None,
               columns: list[tuple[str, str | None]] | None = None,
               dialect: str | None = None) -> TableSource:
    """One table's M -> what the model table is, over the warehouse.

    `expressions` are the model's shared queries and parameters by name; `columns` the model
    table's columns as (M output name, data type), in order (calculated columns excluded).
    `dialect` is the run's: the statement is written in it, since it runs on the run's
    warehouse; without one, in the dialect of the connector the table is read through."""
    expressions = expressions or {}
    params = parameter_values(expressions)
    queries = {k: v for k, v in expressions.items() if k not in params}
    hit = non_warehouse_source(_with_references(m_text, queries))
    if hit:
        return TableSource("refused", reason=f"its rows come from {hit}, not a warehouse table, "
                                             f"so no SQL is faithful to it")
    try:
        node = parse(m_text)
    except MSyntaxError as exc:
        from metrics_agent.plugs.readers.mquery import strip_m_comments  # noqa: PLC0415
        step = SEMANTIC_STEPS.search(strip_m_comments(m_text))
        if step:
            return TableSource("refused", reason=f"its Power Query uses "
                                                 f"{step.group(0).rstrip('( ')} in M the reader "
                                                 f"cannot parse ({exc})")
        return TableSource("legacy")
    ctx = _Ctx(queries, params, [])
    ev = _Eval(ctx)
    try:
        val = ev.ev(node, Env({}, None, ctx))
        rel = ev.as_rel(val, node)
    except Refused as exc:
        from metrics_agent.plugs.readers.mquery import strip_m_comments  # noqa: PLC0415
        if exc.hard or SEMANTIC_STEPS.search(strip_m_comments(
                _with_references(m_text, queries))):
            return TableSource("refused", reason=str(exc))
        return TableSource("legacy")
    except RecursionError:
        return TableSource("refused", reason="its Power Query nests deeper than the reader reads")
    renames = _plain_renames(rel)
    base = rel.chain()[-1]
    if renames is not None and base.op in ("base", "native") and not base.args.get("literal"):
        out = TableSource("plain", renames=renames, reads=sorted(ctx.reads),
                          dialect=base.dialect, db=base.args.get("db"),
                          schema=base.args.get("schema"))
        if base.op == "native":
            try:
                out.native_sql = qualify_native(base.args["sql"], base.dialect,
                                                base.args.get("db"), base.args.get("schema"))
            except Refused as exc:
                return TableSource("refused", reason=str(exc))
        else:
            out.base_object = base.args["object"]
            out.schema = base.args.get("schema")
        return out
    # A statement.
    types = {}
    for name, dtype in columns or []:
        kind = _model_kind(dtype)
        if kind:
            types[name] = kind
    try:
        _qualify_natives(rel)
        if dialect:
            _set_dialect(rel, dialect)
        elif rel.dialect is None:
            raise Refused("its Power Query changes the rows, and the connector it reads through "
                          "does not say which SQL dialect the warehouse speaks")
        w = _Writer(ev, types)
        names = [c for c, _ in (columns or [])]
        w.infer(rel, names or None)
        lay = w.render(rel, names)
        outputs = _output_names(names)
        sql = lay.sql(names, {c: outputs[c] for c in names})
    except Refused as exc:
        return TableSource("refused", reason=str(exc))
    first = _first_base(rel)
    return TableSource("statement", sql=sql, outputs=outputs, reads=sorted(ctx.reads),
                       dialect=rel.dialect, db=first.args.get("db") if first else None,
                       schema=first.args.get("schema") if first else None,
                       base_object=first.args.get("object") if first else None)


def _with_references(m_text: str, queries: dict[str, str]) -> str:
    """The table's M and every shared query it reads, transitively (for the source scan)."""
    seen: set[str] = set()
    out = [m_text or ""]
    todo = [m_text or ""]
    while todo:
        text = todo.pop()
        for name, q in queries.items():
            if name in seen:
                continue
            if re.search(r'#"' + re.escape(name.replace('"', '""')) + r'"', text) or (
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    and re.search(r"(?<![A-Za-z0-9_.\"])" + re.escape(name)
                                  + r"(?![A-Za-z0-9_])", text)):
                seen.add(name)
                out.append(q or "")
                todo.append(q or "")
    return "\n".join(out)


def _plain_renames(rel: Rel) -> dict[str, str] | None:
    """For a table that is its warehouse table under other names: M output -> warehouse
    column. None when a step changes rows or values."""
    if rel.semantic:
        return None
    chain = rel.chain()
    current: dict[str, str] = {}
    for r in reversed(chain):
        if r.op == "rename":
            for old, new in r.args["pairs"]:
                current[new] = current.pop(old, old)
        elif r.op == "prefix":
            return None
        elif r.op in ("base", "native", "remove", "select", "sort", "cast"):
            continue
        else:
            return None
    return {new: orig for new, orig in current.items() if new != orig}


def _first_base(rel: Rel) -> Rel | None:
    stack = [rel]
    while stack:
        r = stack.pop()
        if r is None:
            continue
        if r.op == "base":
            return r
        if r.op == "union":
            stack.extend(reversed(r.args["parts"]))
        elif r.op == "join":
            stack.extend([r.args["right"], r.child])
        else:
            stack.append(r.child)
    return None


def _set_dialect(rel: Rel, dialect: str) -> None:
    stack = [rel]
    while stack:
        r = stack.pop()
        if r is None:
            continue
        r.dialect = dialect
        if r.op == "union":
            stack.extend(r.args["parts"])
        elif r.op == "join":
            stack.extend([r.args["right"], r.child])
        else:
            stack.append(r.child)


def _qualify_natives(rel: Rel) -> None:
    stack = [rel]
    while stack:
        r = stack.pop()
        if r is None:
            continue
        if r.op == "native":
            r.args["sql"] = qualify_native(r.args["sql"], r.dialect, r.args.get("db"),
                                           r.args.get("schema"))
        if r.op == "union":
            stack.extend(r.args["parts"])
        elif r.op == "join":
            stack.extend([r.args["right"], r.child])
        else:
            stack.append(r.child)


def qualify_native(sql: str, dialect: str | None, db: str | None, schema: str | None) -> str:
    """A native query's table names, qualified from the database (and schema) the query is
    navigated to. Its SQL runs in that context, so `SALES.ORDERS` reads `<db>.SALES.ORDERS`;
    the agent's session has a context of its own, and the same text there reads another table
    or none. A name with no schema, where no schema is navigated, is refused by name. SQL the
    parser cannot read is returned unchanged."""
    if not db or not dialect:
        return sql
    try:
        import sqlglot  # noqa: PLC0415
        from sqlglot import exp  # noqa: PLC0415
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:                                              # noqa: BLE001
        return sql
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    changed = False
    for t in tree.find_all(exp.Table):
        if not t.name or t.name.lower() in ctes and not t.db:
            continue
        if isinstance(t.this, exp.Func) or t.find_ancestor(exp.Lateral):
            continue
        if t.catalog:
            continue
        if not t.db:
            if not schema:
                raise Refused(f"its native SQL reads {t.name!r} with no schema, and the schema "
                              f"its database context resolves that to is not stated",
                              hard=True)
            t.set("db", exp.to_identifier(schema))
        t.set("catalog", exp.to_identifier(db))
        changed = True
    if not changed:
        return sql
    return tree.sql(dialect=dialect, pretty=True)


def _model_kind(dtype: str | None) -> str | None:
    d = str(dtype or "").lower()
    if d in ("string", "text", "object"):
        return "text"
    if d in ("int64", "integer", "whole", "int32"):
        return "int"
    if d in ("double", "decimal", "number", "float64", "currency"):
        return "number"
    if d in ("datetime", "date", "datetime64[ns]"):
        return "date" if d == "date" else "datetime"
    if d in ("boolean", "bool", "logical"):
        return "logical"
    return None


_PLAIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Words a bare output name cannot be: SQL's reserved words (a model column named `In` or
# `Order` is output under another name).
_RESERVED = frozenset("""all and any as asc between both by case cast check column constraint
create cross current_date current_time current_timestamp default delete desc distinct drop
else end except exists false fetch for foreign from full grant group having in inner insert
intersect into is join lateral leading left like limit natural not null offset on or order
outer primary qualify references right select set some table then to trailing true union
unique update using values when where window with""".split())


def _output_names(names: list[str]) -> dict[str, str]:
    """Each model column's name in the statement's output: a bare identifier the compiler can
    name unquoted (its own spelling where it is one, otherwise a slug), unique ignoring case."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for n in names:
        base = n if _PLAIN_NAME.match(n) else (re.sub(r"[^A-Za-z0-9_]+", "_", n).strip("_")
                                                or "col")
        if base[0].isdigit():
            base = "c_" + base
        if base.lower() in _RESERVED:
            base += "_col"
        cand, k = base, 2
        while cand.lower() in used:
            cand = f"{base}_{k}"
            k += 1
        used.add(cand.lower())
        out[n] = cand
    return out
