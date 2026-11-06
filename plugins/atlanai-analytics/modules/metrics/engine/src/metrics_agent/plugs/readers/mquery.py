# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Power Query (M) lineage mining, shared by every Power BI reader.

PROVENANCE. A COPY of the M lineage miner from an earlier project (guardrail 6:
ported, never imported). Its measured claim, kept because it is the reason this file exists: on the
1,644 M-backed tables of the reference extract this names the warehouse object on **963**,
where the inline `M_SQL`/`M_DB`/`M_ITEM` regexes previously in `readers/powerbi.py` named
**32**. Those knew only the `[Name=..., Kind=...]` navigation form; the dominant form in the
wild is `Value.NativeQuery` with the target inside the SQL, and on that form the old code
named no object at all.

WHY IT IS SHARED. `powerbi.py` (TMDL), `pbix.py` (a .pbix zip) and `tabular.py` (.bim) read
the SAME Power Query expressions out of three different containers, so a warehouse target one
can name and another cannot is a parser bug, not a format difference. One implementation is
also what keeps the redaction honest: every caller gets `scrub_m` whether it remembers to ask
or not.

DELIBERATELY left behind from the earlier project: nothing of the miner itself — it comes across
whole. What did NOT come with it is the earlier project's caller-side plumbing (its validated-write
helper, its `Manifest`) and its repo-root path constants; this base has its own stage harness,
and importing across the two trees is what guardrail 6 forbids.

ADDED HERE, not in the earlier project: the bare `{[Name="X"]}[Data]` navigation chain. See
`_NAV_CHAIN` — the port WITHOUT it was a coverage regression against the base, because
Snowflake's connector emits navigation steps with no `Kind=` and the earlier project recognises only
the Kind-bearing form.

DELIBERATELY CHANGED FROM the earlier project, and why — `RAW_MAX` bounds the SQL SCAN but no longer
truncates what is recorded. The earlier project capped `raw_expression` at 12000 characters. This base
forbids that (AGENT_GUARDRAILS rule 8: "scrub always; truncate never"), because
the M expression carries the WHERE-clause population rules the report author applied before any
visual saw the data, and an expression cut mid-clause corrupts exactly the evidence the
compiler reads. Scrubbing is unconditional; nothing is truncated.
"""
from __future__ import annotations

import contextlib
import contextvars
import pathlib
import re

# Kept for callers that publish an M expression rather than parse it. NOTHING in this module
# truncates any more: `raw_expression` is recorded whole (guardrail 8) and the FROM scan is
# linear, so the "a target past the cut is a target this module cannot find" problem that the
# bound used to create is gone rather than merely documented.
RAW_MAX = 12000

# `""X""` is the M-escaped form of a quoted SQL identifier: the native query is itself a
# double-quoted M string, so every inner quote arrives doubled.
_IDENT = r'(?:""[^"]+""|"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z0-9_$]+)'
_FROM_RE = re.compile(rf'\bFROM\s+({_IDENT})\.({_IDENT})(?:\.({_IDENT}))?', re.I)

# THE BARE NAVIGATION CHAIN — `Source{[Name="DB"]}[Data]{[Name="SCH"]}[Data]{[Name="T"]}[Data]`.
#
# ADDED HERE; the earlier project's copy of this module does NOT have it, and porting it wholesale
# would have been a coverage REGRESSION against the base it is merging onto. The earlier project
# recognises a navigation step only when it carries `Kind="Table"`, but the form Snowflake's
# connector actually emits has no `Kind` at all — so on a Snowflake navigation model the
# earlier project names nothing, while the inline
# regexes previously in `readers/powerbi.py` named the table correctly. Caught by
# `tests/test_validators.py`, which stopped compiling any DAX measure the moment the naive
# port landed: a table with no object is a table `a2` cannot map to a physical name.
#
# This is a UNION of the two, not a replacement: the Kind-bearing form and the native query
# both still outrank it, and it only fills what they left empty.
_NAV_CHAIN = re.compile(r'\{\s*\[\s*(?:Name|Item)\s*=\s*"([^"]+)"[^\]]*\]\s*\}')

# A blob read named through a navigation step rather than File.Contents.
_BLOB_NAME_RE = re.compile(
    r'Name\s*=\s*"([^"]+\.(?:csv|csv\.gz|parquet|txt|xlsx?))"', re.I)

# Connection first-arguments. The structured `connection` field already carries the host
# for lineage, so the copy inside raw_expression is redundant exposure, not evidence.
# WHAT GETS SCRUBBED, AND WHY THAT LINE IS WHERE IT IS (operator decision, 2026-09-03).
#
# KEPT — the connection endpoint, the Snowflake warehouse name, AND the role. All three are
# join keys for correlating a Power BI table against the query history extracted from
# Snowflake, which is the `s1c` evidence stream: QUERY_HISTORY carries both WAREHOUSE_NAME
# and ROLE_NAME. Redacting them would cost a real correlation capability to hide identifiers
# that are not secrets.
#
# SCRUBBED — credential VALUES only. There were zero in the reference extract, because Power
# BI keeps secrets out of a model definition, but a secret value must never be carried into
# `facts.json` whatever the policy on identifiers is.
#
# CONSEQUENCE, recorded deliberately rather than left implicit: `facts.json` therefore
# carries warehouse endpoints, warehouse names and role names (909 x ACCOUNTADMIN in the
# reference extract), and `facts.json` is read by the LLM stages. That is an accepted
# trade for the correlation it buys, not an oversight -- revisit it if `facts.json` ever
# starts flowing somewhere less controlled than a run directory.
# The keyword must START a name (`(?<![A-Za-z0-9_])`): without that, `company_code = x` read
# as a `code=` credential and a join condition inside a custom-SQL statement became
# `<redacted>`, which no longer parses.
_SECRET_RE = re.compile(
    r'((?<![A-Za-z0-9_])(?:Password|PWD|AccountKey|SharedAccessSignature|ApiKey|api[_-]?key|x-api-key'
    r'|client[_-]?secret|access[_-]?token|refresh[_-]?token|token|secret'
    # The optional `"` before `=` matters: M spells a non-identifier header name as a
    # QUOTED IDENTIFIER, `#"x-api-key"="…"`, so the keyword is followed by a quote and then
    # the equals. Without it the commonest custom-header spelling of an API key rode through.
    r'|AccessKey|SecretAccessKey|sig|code)"?\s*=\s*)'
    r'("[^"]*"|\'[^\']*\'|[^,;)\]\s"\']+)', re.I)

# A CREDENTIAL CARRIED AS A HEADER VALUE, which is the dominant real shape and which the
# keyword=value pattern above cannot see:
#
#     Web.Contents(url, [Headers=[Authorization="Bearer eyJ0eXAi…"]])
#
# `Bearer` is inside the value, not followed by `=`, so the token rode through unredacted.
# This matters more since `raw_expression` stopped being capped: the WHOLE expression is now
# recorded, and facts.json is read by the authored stages.
_BEARER_RE = re.compile(r'((?:Bearer|Basic)\s+)([A-Za-z0-9\-._~+/=]{8,})', re.I)

# An Authorization header whose value is a bare token with no scheme word.
_AUTH_HEADER_RE = re.compile(r'((?:Authorization|Proxy-Authorization)"?\s*=\s*)'
                             r'("[^"]*"|\'[^\']*\')', re.I)


# A CREDENTIAL BUILT BY CONCATENATION, the commonest way M spells Basic auth:
#
#     [Headers=[Authorization="Basic " & Binary.ToText(Text.ToBinary("user:pass"), …)]]
#     [Headers=[Authorization="Bearer " & "eyJ0eXAi…"]]
#
# The patterns above see only a single literal, so they redacted the scheme word and left the
# secret — the literal AFTER the `&` — in place. Both the header's value and anything joined
# onto a scheme word are therefore redacted as a whole EXPRESSION, read with M's own nesting
# and string rules, and replaced by one string literal so the M still parses.
_AUTH_ASSIGN_RE = re.compile(r'(?<![A-Za-z0-9_-])(?:Proxy-)?Authorization"?\s*=\s*', re.I)
_SCHEME_CONCAT_RE = re.compile(r'"(?:Bearer|Basic|Token)\s*"\s*&\s*', re.I)
_M_STEP_END = re.compile(r'\s*\bin\b')


def _m_expr_end(s: str, i: int) -> int:
    """Index just past the M expression starting at `s[i]`: the first depth-0 `,`, the
    closing bracket of the enclosing record or call, or the `in` that ends a let block."""
    depth, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif depth == 0 and (ch == "," or (ch == "\n" and _M_STEP_END.match(s, i))):
            return i
        i += 1
    return n


def _redact_expressions(text: str, start_re: "re.Pattern", *, only_compound: bool) -> str:
    out, pos = [], 0
    for m in start_re.finditer(text):
        if m.start() < pos:
            continue
        end = _m_expr_end(text, m.end())
        expr = text[m.end():end].rstrip()
        stop = m.end() + len(expr)
        if not expr or (only_compound and not re.search(r'&|\(', expr)):
            continue
        out.append(text[pos:m.end()])
        out.append('"<redacted>"')
        pos = stop
    out.append(text[pos:])
    return "".join(out)


def scrub_m(m_expr: str) -> str:
    """Redact credential VALUES from an M expression. Identifiers are preserved by design
    -- see the note above the pattern for what is kept and why."""
    def _repl(m: "re.Match") -> str:
        val = m.group(2)
        # preserve the original quoting style: adding quotes inside a URL query string
        # produces malformed M
        q = val[0] if val[:1] in ("\"", "'") else ""
        return f"{m.group(1)}{q}<redacted>{q}"

    # Compound values first, while the expression is still intact: a single literal is left
    # to the patterns below, which keep its quoting.
    out = _redact_expressions(m_expr, _AUTH_ASSIGN_RE, only_compound=True)
    out = _redact_expressions(out, _SCHEME_CONCAT_RE, only_compound=False)
    out = _SECRET_RE.sub(_repl, out)
    out = _AUTH_HEADER_RE.sub(_repl, out)
    # Applied last, and to the whole string: a scheme-prefixed token can sit inside a value
    # the two patterns above already left alone.
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)}<redacted>", out)
    return out


_M_ESCAPE = re.compile(r"#\(([^()]*)\)")
_M_NAMED = {"cr": "\r", "lf": "\n", "tab": "\t", "#": "#"}


def decode_m_escapes(s: str) -> str:
    """Turn M's character escapes back into the characters the warehouse receives.

    Power BI writes a multi-line native query as ONE M string with `#(lf)` for each line
    break (also `#(cr)`, `#(tab)`, `#(#)`, a comma list such as `#(cr,lf)`, and 4- or 8-digit
    hex such as `#(000D)`). Left encoded, the whole statement is a single line, so the first
    `--` comment either blanks everything after it or — the case measured on a real model —
    a `-- ... flag from index.html` comment is read as `FROM index.html` and the model gains
    a warehouse table named ACME_PROD.INDEX.HTML. An escape this function does not
    recognise is left exactly as written rather than guessed at.
    """
    def _one(m: "re.Match") -> str:
        out = []
        for tok in m.group(1).split(","):
            t = tok.strip()
            if t.lower() in _M_NAMED:
                out.append(_M_NAMED[t.lower()])
            elif re.fullmatch(r"[0-9A-Fa-f]{4}|[0-9A-Fa-f]{8}", t):
                out.append(chr(int(t, 16)))
            else:
                return m.group(0)
        return "".join(out)
    return _M_ESCAPE.sub(_one, s) if "#(" in s else s


def native_sql(m_expr: str) -> str | None:
    """The SQL a `Value.NativeQuery` actually runs, or None.

    NOT A REGEX, and the regex it replaces is why. `readers/powerbi.py` matched
    `Value\\.NativeQuery\\s*\\(\\s*[^,]+,\\s*"(.*?)"` — a pattern that assumes the first argument
    contains no comma. The first argument is normally a connector call:

        Value.NativeQuery(Snowflake.Databases("acct","WH"), "SELECT ... FROM D.S.T")

    so `[^,]+` stops inside `Databases("acct`, the next `"..."` it finds is `"WH"`, and the
    table's recorded evidence becomes the single character `w`. Latent until now only because
    the test fixture used the navigation form; every real Snowflake native query hits it.

    So: find the call, then walk it once, tracking bracket depth and M string state, and
    take its SECOND top-level argument. Depth counts `[` and `{` too: the first argument is
    often a navigation step, `Source{[Name="RETAIL"]}[Data]`, and counting parentheses
    alone read its `"RETAIL"` as the statement. `""` is M's escape for a quote inside a
    string and is unescaped on the way out. The SQL may also be passed BY NAME:

        SqlBase = "-- ...#(lf)SELECT ...", Sql = SqlBase,
        Query = Value.NativeQuery(Db, Sql, null, [EnableFolding = true])

    and the name is then followed back to the literal it is bound to.
    """
    marker = "Value.NativeQuery"
    start = m_expr.find(marker)
    if start < 0:
        return None
    open_at = m_expr.find("(", start + len(marker))
    if open_at < 0:
        return None
    i, depth, n = open_at + 1, 1, len(m_expr)
    while i < n:
        ch = m_expr[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if m_expr[j] == '"':
                    if j + 1 < n and m_expr[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        return None
    args = _split_args(m_expr[open_at + 1:i])
    if len(args) < 2:
        return None
    arg = args[1].strip()
    if arg.startswith('"'):
        text = _read_m_string(arg, 0)
        return decode_m_escapes(text) if text is not None else None
    return _resolve_m_string(m_expr, arg)


def _split_args(inner: str) -> list[str]:
    """Top-level comma-separated arguments of a call body, respecting nesting and strings."""
    out, buf, depth, in_str, k = [], [], 0, False, 0
    while k < len(inner):
        ch = inner[k]
        if in_str:
            buf.append(ch)
            if ch == '"':
                if k + 1 < len(inner) and inner[k + 1] == '"':
                    buf.append('"')
                    k += 1
                else:
                    in_str = False
        elif ch == '"':
            in_str = True
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        k += 1
    out.append("".join(buf))
    return out


def _read_m_string(text: str, pos: int) -> str | None:
    """The M string literal starting at `text[pos] == '"'`, `""` unescaped, or None."""
    if pos >= len(text) or text[pos] != '"':
        return None
    j, buf = pos + 1, []
    while j < len(text):
        if text[j] == '"':
            if j + 1 < len(text) and text[j + 1] == '"':
                buf.append('"')
                j += 2
                continue
            return "".join(buf)
        buf.append(text[j])
        j += 1
    return None


_M_IDENT = re.compile(r'^(?:#"[^"]+"|[A-Za-z_][A-Za-z0-9_.]*)$')


def _resolve_m_string(m_expr: str, ref: str, hops: int = 5) -> str | None:
    """Follow `name = other`, `other = "..."` inside a let block to the string literal.

    Bounded, and exact: only a bare identifier (or `#"quoted name"`) is followed, and only to
    a binding whose right-hand side is itself a literal or another bare identifier. Anything
    else — concatenation, a function call, a parameter — returns None and the caller keeps
    its existing fallback, rather than this guessing at SQL it cannot see.
    """
    for _ in range(hops):
        if ref.startswith('"'):
            lit = _read_m_string(ref, 0)
            return decode_m_escapes(lit) if lit is not None else None
        if not _M_IDENT.match(ref):
            return None
        m = re.search(r'(?m)(?:^|[\s,])' + re.escape(ref) + r'\s*=\s*', m_expr)
        if not m:
            return None
        rhs_start = m.end()
        if m_expr[rhs_start:rhs_start + 1] == '"':
            lit = _read_m_string(m_expr, rhs_start)
            if lit is None:
                return None
            # Only the literal ALONE: `"a" & b` is a concatenation we cannot evaluate.
            after = m_expr[rhs_start + len(lit) + 2 + lit.count('"'):].lstrip()
            if after.startswith("&"):
                return None
            return decode_m_escapes(lit)
        # `Text.Replace(Base, "/*{token}*/", Param)` — a template whose placeholder is ITSELF
        # a SQL block comment. With the parameter unset, the placeholder stays in the text and
        # is a comment the warehouse ignores, so `Base` is exactly the default statement. Any
        # other placeholder would change the SQL when replaced, so it is not followed.
        tr = re.match(r'Text\.Replace\(\s*(#"[^"]+"|[A-Za-z_][A-Za-z0-9_.]*)\s*,\s*"(/\*[^"]*\*/)"\s*,',
                      m_expr[rhs_start:])
        if tr:
            ref = tr.group(1)
            continue
        nxt = re.match(r'(#"[^"]+"|[A-Za-z_][A-Za-z0-9_.]*)\s*(,|\n|\bin\b)',
                       m_expr[rhs_start:])
        if not nxt:
            return None
        ref = nxt.group(1)
    return None



def strip_m_comments(m_expr: str) -> str:
    """Blank M's `//` line comments, preserving length and newlines.

    M's comment is `//`; SQL's is `--`. `_strip_sql_noise` knew only the second, so a
    commented-out PRIOR STEP was still scanned — and because the native-query branch fires on
    a bare `SELECT` anywhere in the expression and then overwrites unconditionally, a dead
    step beat the live navigation chain:

        // Source = Value.NativeQuery(…, "SELECT * FROM DEV.SCRATCH.OLD_JUNK")
        Source = Snowflake.Databases(…), D = Source{[Name="PROD",Kind="Database"]}[Data], …

    resolved to DEV.SCRATCH.OLD_JUNK. Commented-out prior steps are ordinary in
    author-maintained M, and nothing downstream checks that the object exists.

    Only OUTSIDE string literals: a connection address is a string holding `//`
    (`"https://…"`), and blanking from there to the end of the line lost the native query
    that followed it on the same line.
    """
    return _strip_m_comments_keeping_strings(m_expr)


def _strip_sql_noise(sql: str) -> str:
    """Blank out SQL comments and string literals, preserving length and newlines.

    Both hide a `FROM` that is not the query's target: `-- FROM DEAD.OLD.TABLE` and
    `SELECT 'FROM A.B.C' AS label` both beat the real table under a first-match search.
    """
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        two = sql[i:i + 2]
        if two == "--":
            while i < n and sql[i] != "\n":
                out[i] = " "
                i += 1
        elif two == "/*":
            while i < n and sql[i:i + 2] != "*/":
                if sql[i] != "\n":
                    out[i] = " "
                i += 1
            for k in range(i, min(i + 2, n)):
                out[k] = " "
            i += 2
        elif sql[i] in "[\"":
            # A QUOTED IDENTIFIER is not a string literal, and an apostrophe inside one is
            # part of a name: `SELECT [John's col] FROM D.S.T`. Treating that `'` as a string
            # delimiter opened a run to the end of the statement and blanked the real FROM.
            #
            # SKIPPED OVER, NOT BLANKED. `_FROM_RE` deliberately matches quoted identifiers
            # (`"x"`, `[x]`), so erasing them would lose a legitimately quoted FROM target —
            # the very thing this function exists to protect.
            closing = "]" if sql[i] == "[" else '"'
            i += 1
            while i < n and sql[i] != closing:
                i += 1
            i += 1
        elif sql[i] == "'":
            out[i] = " "
            i += 1
            while i < n and sql[i] != "'":
                if sql[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


def _target_from_sql(sql: str) -> tuple[str, str, str] | None:
    """The table a query actually reads FROM.

    Takes the first FROM at parenthesis depth 0, so a CTE body or a subquery -- both
    nested -- cannot outrank the outer target. Falls back to the first FROM at any depth
    when the outer query selects from a derived table.
    """
    clean = _strip_sql_noise(sql)
    # ONE PASS. The earlier project recomputed the depth with `clean.count("(", pos, m.start())` per
    # match, which is O(n*m) — and that quadratic cost is the only reason it bounded the scan to
    # RAW_MAX characters. The bound then silently hid the target of any query whose SELECT
    # list ran past 12000 characters, which on a wide fact table is ordinary. Walking the
    # string once is linear, so the whole expression can be scanned and the bound goes away.
    depth_of: list[tuple[int, re.Match]] = []
    depth = 0
    pos = 0
    for m in _FROM_RE.finditer(clean):
        for ch in clean[pos:m.start()]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        pos = m.start()
        depth_of.append((depth, m))
    if not depth_of:
        return None
    # Depth is relative: the SQL arrives still wrapped in `Value.NativeQuery(...)`, so
    # the outermost query sits at whatever the minimum observed depth is, not at zero.
    floor = min(d for d, _ in depth_of)
    chosen = next(m for d, m in depth_of if d == floor)
    parts = [_unquote_ident(p) for p in chosen.groups() if p]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    return None


def _unquote_ident(part: str) -> str:
    """Strip SQL identifier quoting, repeatedly — the M-escaped form doubles it."""
    part = part.strip()
    while len(part) >= 2 and (
            (part[0] == part[-1] and part[0] in "\"`") or (part[0] == "[" and part[-1] == "]")):
        part = part[1:-1].strip()
    return part


_RENAME_CALL = re.compile(r'\bTable\.RenameColumns\s*\(')
_RENAME_PAIR = re.compile(r'\{\s*"((?:[^"]|"")*)"\s*,\s*"((?:[^"]|"")*)"\s*\}')


def _call_args(text: str, open_paren: int) -> str:
    """The text between `(` at `open_paren` and its matching `)`, string literals skipped."""
    depth, i, n = 0, open_paren, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and not (text[i] == '"' and text[i + 1:i + 2] != '"'):
                i += 2 if text[i:i + 2] == '""' else 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
        i += 1
    return text[open_paren + 1:]


def _split_args(args: str) -> list[str]:
    """A call's argument text split at its top-level commas, string literals and brackets kept
    whole: `T, {"a", "b"}, each [x] + 1` -> three arguments."""
    out, depth, start, i, n = [], 0, 0, 0, len(args)
    while i < n:
        ch = args[i]
        if ch == '"':
            i += 1
            while i < n and not (args[i] == '"' and args[i + 1:i + 2] != '"'):
                i += 2 if args[i:i + 2] == '""' else 1
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(args[start:i])
            start = i + 1
        i += 1
    out.append(args[start:])
    return [a.strip() for a in out]


_STRING = re.compile(r'"((?:[^"]|"")*)"')
# Steps that bring a column into the table that the warehouse table does not have, and which
# argument names it: an expanded merge (`Table.NestedJoin` then expand), an added or a
# duplicated column. Index into the call's arguments; -1 is the last.
_INTRODUCES = {
    "Table.ExpandTableColumn": -1,
    "Table.ExpandRecordColumn": -1,
    "Table.AddColumn": 1,
    "Table.DuplicateColumn": 2,
}
_INTRODUCE_CALL = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _INTRODUCES) + r")\s*\(")


def _introduced_columns(m_expr: str) -> set[str]:
    """Column names a step ADDS to the table: another query's columns expanded into it, a
    computed column, a duplicate. None of them is a column of the warehouse table."""
    out: set[str] = set()
    for call in _INTRODUCE_CALL.finditer(m_expr):
        args = _split_args(_call_args(m_expr, call.end() - 1))
        idx = _INTRODUCES[call.group(1)]
        # An expand names its new columns last, after the table, the column and the fields.
        if len(args) < (3 if idx < 0 else idx + 1):
            continue
        out |= {v.replace('""', '"') for v in _STRING.findall(args[idx])}
    return out


def column_renames(m_expr: str) -> dict[str, str]:
    """Model column name -> the source column it was renamed from, by `Table.RenameColumns`.

    Power Query renames columns between the warehouse and the model (`CAL_MONTH_KEY` shown as
    `Cal-Month`). The model, and every visual, then name the column by its NEW name, which the
    warehouse does not have: compiled SQL selected `Cal-Month` and Snowflake read it as a
    subtraction. Renames compose in step order, so a column renamed twice maps back to the
    original. A rename this cannot read is simply absent: the model name stays the answer.

    A rename of a column some step ADDED (another query's column expanded into this one, a
    computed or duplicated column) is not a warehouse rename: the column it names is not on
    this table, so it is left out and the model name stays, which the compiler refuses by name.
    Renames by function (`Table.TransformColumnNames`, `Table.PrefixColumns`) are not read."""
    current: dict[str, str] = {}
    for call in _RENAME_CALL.finditer(m_expr or ""):
        args = _call_args(m_expr, call.end() - 1)
        for old, new in _RENAME_PAIR.findall(args):
            old, new = old.replace('""', '"'), new.replace('""', '"')
            current[new] = current.pop(old, old)
    added = _introduced_columns(m_expr or "") if current else set()
    return {new: orig for new, orig in current.items() if new != orig and orig not in added}


# A text parameter's declaration: `"PROD" meta [IsParameterQuery=true, Type="Text", …]`.
_TEXT_PARAMETER = re.compile(r'^\s*"((?:[^"]|"")*)"\s+meta\s*\[[^\]]*IsParameterQuery\s*=\s*true',
                             re.I | re.S)


def text_parameters(expressions: dict[str, str]) -> dict[str, str]:
    """The model's text parameters (`IsParameterQuery=true` with a string literal), by name:
    the values a navigation step may name instead of a literal."""
    out = {}
    for name, expr in (expressions or {}).items():
        m = _TEXT_PARAMETER.match(expr or "")
        if m:
            out[str(name)] = m.group(1).replace('""', '"')
    return out


def substitute_parameters(m_expr: str, params: dict[str, str]) -> str:
    """`Name=#"Database Name"`, `Name=Schema` or `Name=@param` in a navigation step, where the
    name is a text parameter of the model, written as the literal it holds, so the lineage
    names the database, schema and object instead of losing them (seen live)."""
    if not params or not m_expr:
        return m_expr or ""
    def lit(v: str) -> str:
        return '"' + v.replace('"', '""') + '"'
    def sub(m: re.Match) -> str:
        key = m.group(1) if m.group(1) is not None else (m.group(2) or m.group(3))
        return f"Name={lit(params[key])}" if key in params else m.group(0)
    out = re.sub(r'Name\s*=\s*(?:#"((?:[^"]|"")*)"|@([A-Za-z_][A-Za-z0-9_]*)'
                 r'|([A-Za-z_][A-Za-z0-9_]*)(?=\s*[,\]]))', sub, m_expr)

    # A connector's own arguments may be parameters too: `Snowflake.Databases(SfServer,
    # SfWarehouse)`, `Sql.Database(Server, #"Database Name")`. Only the first two, and only a
    # bare name that is a text parameter: anything else stays as written.
    def conn(m: re.Match) -> str:
        args = []
        for a in (m.group(2), m.group(3)):
            if a is None:
                args.append(None)
                continue
            key = a[2:-1].replace('""', '"') if a.startswith('#"') else a
            args.append(lit(params[key]) if key in params else a)
        tail = f", {args[1]}" if args[1] is not None else ""
        return f"{m.group(1)}({args[0]}{tail}{m.group(4)}"
    ident = r'(#"(?:[^"]|"")*"|"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_]*)'
    return re.sub(r'\b(Snowflake\.Databases|Databricks\.\w+|Sql\.Databases?|AmazonRedshift\.'
                  r'Database|GoogleBigQuery\.\w+|PostgreSQL\.Database)\(\s*' + ident
                  + r'(?:\s*,\s*' + ident + r')?(\s*[,)])', conn, out)


def source_lineage(m_expr: str) -> dict:
    """A table's `source_lineage` entry on the shared `bi_facts` contract, the SAME for every
    Power BI reader (TMDL, .pbix, .bim). A native query is `object_kind: native-sql` and its
    evidence is the scrubbed SQL itself, not the whole M expression: the readers disagreed
    here, so the same model read from two containers described two different sources."""
    lin = lineage_from_m(m_expr or "")
    out = {
        "connection": lin["connection"], "database": lin["database"], "schema": lin["schema"],
        "object": lin["object"].lower() if lin["object"] else None,
        "object_kind": lin["object_kind"],
        "raw_expression": lin["raw_expression"] or None,
        "published_datasource": None,
    }
    nq = native_sql(m_expr or "")
    if nq:
        out["object_kind"] = "native-sql"
        out["raw_expression"] = scrub_m(nq) or None
    return out


def lineage_from_m(m_expr: str) -> dict:
    """Best-effort warehouse target from a Power Query expression.

    Ordered most-specific first: a native query names its own table, so it wins over the
    navigation-step form. Everything is best-effort and unvalidated -- no stage in this
    pipeline checks that the object exists.
    """
    # `connection` carries the endpoint, and so does raw_expression: both are lineage.
    out = {"connection": None, "database": None, "schema": None,
           "object": None, "object_kind": None,
           # SCRUBBED, NEVER CUT. See the module docstring: the cap bounds the scan below,
           # not the evidence recorded here.
           "raw_expression": scrub_m(m_expr)}
    if not m_expr:
        return out

    # Every pattern below reads the COMMENT-STRIPPED expression. `raw_expression` above keeps
    # the author's own text, comments and all — that is evidence, and it is not this
    # function's business to edit it.
    clean = strip_m_comments(m_expr)

    conn = re.search(r'(?:Snowflake\.Databases|Databricks\.\w+|Sql\.Databases?|'
                     r'AmazonRedshift\.Database|GoogleBigQuery\.\w+)\(\s*"([^"]+)"', clean)
    if conn:
        out["connection"] = conn.group(1)

    # `Sql.Database("server","db")` states the database in the connection itself, unlike
    # `Snowflake.Databases(account, warehouse)` where neither argument is one. So does
    # `AmazonRedshift.Database("host:port","db")`: read as a Snowflake-style chain, its
    # two-step `public`/`orders` navigation put the SCHEMA in the database field.
    sqldb = re.search(r'(?:Sql\.Databases?|AmazonRedshift\.Database)\(\s*"[^"]+"\s*,\s*'
                      r'"([^"]+)"', clean)
    db_from_connection = bool(sqldb)
    if sqldb:
        out["database"] = sqldb.group(1)

    # NOTE: there is deliberately no standalone "first {[Name=X]}[Data] is the database"
    # read here any more. That pattern is the same one `_NAV_CHAIN` matches, so it fired on a
    # ONE-step chain too and reported `ORDERS.ORDERS` — the object recorded as its own
    # database. Positions in a bare chain depend on how many steps there are, so the chain
    # branch below owns all three of them.

    # Navigation steps: [Name="X", Kind="Schema"|"Table"|"View"] and [Schema=, Item=]
    for name, kind in re.findall(
            r'\[\s*Name\s*=\s*"([^"]+)"\s*,\s*Kind\s*=\s*"([^"]+)"\s*\]', clean):
        k = kind.lower()
        if k == "database":
            out["database"] = name
        elif k == "schema":
            out["schema"] = name
        elif k in ("table", "view"):
            out["object"] = name
            out["object_kind"] = k
    nav = re.search(r'\[\s*Schema\s*=\s*"([^"]+)"\s*,\s*Item\s*=\s*"([^"]+)"\s*\]', clean)
    if nav:
        out["schema"], out["object"] = nav.group(1), nav.group(2)
        out["object_kind"] = out["object_kind"] or "table"

    # A native query states its own target and outranks the navigation form.
    #
    # SCANNED ON THE EXTRACTED SQL, NOT THE RAW M. `_strip_sql_noise` treats `'` as a SQL
    # string delimiter, so a single apostrophe ANYWHERE in the surrounding M — a comment, a
    # label, `Q1'25`, a possessive — opened a string that ran to the end of the expression
    # and blanked the real FROM. One apostrophe destroyed the lineage of the whole table.
    # `native_sql()` already extracts the statement properly and was sitting unused by this
    # function; scanning its output confines the SQL lexer to actual SQL.
    sql = native_sql(clean)
    if sql is None and re.search(r"\bSELECT\b", clean, re.I):
        # A native query the extractor could not isolate (a non-standard wrapper). Fall back
        # to the whole expression rather than losing the target entirely.
        sql = clean
    if sql is not None:
        target = _target_from_sql(sql)
        if target:
            db, schema, obj = target
            out["database"] = db or out["database"]
            out["schema"], out["object"] = schema, obj
            out["object_kind"] = "native_query"

    # Parameterised object name: `Name=@param`. Carried over from the pbix copy of this
    # function — the param token is a weaker answer than a literal name, but it is the
    # only name the expression has, so it beats emitting nothing.
    if out["object"] is None:
        q = re.search(r'\[\s*Name\s*=\s*(@\w+)\s*,\s*Kind\s*=\s*"(Table|View)"', clean)
        if q:
            out["object"] = q.group(1)
            out["object_kind"] = q.group(2).lower()

    # Blob/file reads, in the two forms M writes them. object_kind stays "file" so a
    # downstream reader can never mistake one for a catalog.schema.table reference. The
    # navigation form came from readers/tabular.py, where it is the COMMON case: on a
    # Power BI import ("extract") model every partition is a CSV or parquet read.
    if out["object"] is None:
        f = re.search(r'File\.Contents\(\s*"([^"]+)"', clean)
        if f:
            out["connection"] = out["connection"] or f.group(1)
            out["object"] = pathlib.PurePath(f.group(1)).name
            out["object_kind"] = "file"

    if out["object"] is None:
        f = _BLOB_NAME_RE.search(clean)
        if f:
            out["object"] = f.group(1)
            out["object_kind"] = "file"

    # The bare chain, LAST among the object forms and filling only what the others left
    # empty. It runs after the file branches on purpose: `Blob{[Name="sales.parquet"]}`
    # matches this shape too, and calling a parquet file a warehouse table is worse than
    # naming nothing. Positions are read rather than counted off the front: three steps are
    # database/schema/object, two are database/object, one is the object alone. The
    # earlier project's copy has none of this; the base's inline
    # version took items[0]/items[1]/items[-1] unconditionally, which made the OBJECT the
    # schema on a two-step chain.
    if out["object"] is None:
        chain = _NAV_CHAIN.findall(clean)
        if len(chain) >= 3:
            out["database"] = out["database"] or chain[0]
            out["schema"] = out["schema"] or chain[1]
            out["object"], out["object_kind"] = chain[-1], "table"
        elif len(chain) == 2:
            # TWO STEPS ARE AMBIGUOUS, and which reading is right depends on the connector.
            # `Sql.Database("server","db")` FIXES the database in the connection, so the
            # chain that follows is schema/table. `Snowflake.Databases(account, warehouse)`
            # names neither, so its chain starts at the database. Calling both
            # database/object put the SCHEMA in the database field on every SQL Server model.
            # The test is where the database CAME FROM, not merely whether one is set: the
            # `{[Name=X]}[Data]` reader above already took chain[0] as the database on a
            # Snowflake model, so asking "is database set?" would then record chain[0] as
            # the schema as well and report RETAIL.RETAIL.ORDERS.
            if db_from_connection:
                out["schema"] = out["schema"] or chain[0]
            else:
                out["database"] = out["database"] or chain[0]
            out["object"], out["object_kind"] = chain[1], "table"
        elif len(chain) == 1:
            out["object"], out["object_kind"] = chain[0], "table"

    # Last resort: a named connection ("AzureBlobConnection"), when no connector literal
    # was found. Deliberately NOT the bare-identifier alternative the tabular parser used
    # alongside this -- `Source = Value.NativeQuery(...)` matches it and yields a
    # "connection" of "Value", which is noise dressed as lineage.
    if out["connection"] is None:
        c = re.search(r'Source\s*=\s*#?"?([A-Za-z0-9_ ]+Connection)"?', clean)
        if c:
            out["connection"] = c.group(1).strip()

    return out


# ── entity partitions: Direct Lake, and DirectQuery to another semantic model ─────────────
#
# A partition `= entity` has NO Power Query of its own. It names a table (`entityName`, and
# `schemaName` where the source has schemas) and a SHARED expression (`expressionSource`) that
# says where that table lives. Two shapes of that expression matter:
#
#   Direct Lake      Sql.Database("<sql endpoint>", "<lakehouse or warehouse>")
#                    AzureStorage.DataLake("https://onelake.../<workspace>/<item>")
#   a composite      AnalysisServices.Database("powerbi://.../myorg/<workspace>", "<model>")
#   model's chain    the table is ANOTHER semantic model's table, read by DirectQuery
def _strip_m_comments_keeping_strings(m_expr: str) -> str:
    """Blank M's `//` and `/* */` comments OUTSIDE string literals. A connection address is a
    string holding `//` (`powerbi://…`, `https://onelake…`), which a line-comment regex
    would blank along with everything after it."""
    out, i, n = list(m_expr or ""), 0, len(m_expr or "")
    s = m_expr or ""
    while i < n:
        if s[i] == '"':
            i += 1
            while i < n and not (s[i] == '"' and s[i + 1:i + 2] != '"'):
                i += 2 if s[i:i + 2] == '""' else 1
            i += 1
        elif s[i:i + 2] == "//":
            while i < n and s[i] != "\n":
                out[i] = " "
                i += 1
        elif s[i:i + 2] == "/*":
            while i < n and s[i:i + 2] != "*/":
                if s[i] != "\n":
                    out[i] = " "
                i += 1
            for k in range(i, min(i + 2, n)):
                out[k] = " "
            i += 2
        else:
            i += 1
    return "".join(out)


_REMOTE_MODEL = re.compile(
    r'AnalysisServices\.Databases?\(\s*"((?:[^"]|"")*)"\s*,\s*"((?:[^"]|"")*)"', re.I)
_ONELAKE = re.compile(r'AzureStorage\.DataLake\(\s*"([^"]+)"', re.I)
_SQL_ENDPOINT = re.compile(r'Sql\.Databases?\(\s*"[^"]+"\s*,\s*"[^"]+"', re.I)


def remote_model(m_expr: str) -> dict | None:
    """The semantic model a shared expression reads by DirectQuery, or None.

    `{"server", "workspace", "model"}`: the server as written, the workspace as the last
    segment of the server address (URL-decoded; None when the address names none, such as
    `powerbi://…/myorg` alone), and the model's name. Comments are ignored, so a commented-out
    prior source never names a chain link."""
    from urllib.parse import unquote  # noqa: PLC0415

    m = _REMOTE_MODEL.search(_strip_m_comments_keeping_strings(m_expr or ""))
    if not m:
        return None
    server = m.group(1).replace('""', '"')
    tail = server.rstrip("/").rsplit("/", 1)[-1] if "/" in server else ""
    workspace = unquote(tail) if tail and tail.lower() != "myorg" else None
    return {"server": server, "workspace": workspace, "model": m.group(2).replace('""', '"')}


def entity_lineage(m_expr: str | None, entity: str, schema: str | None) -> dict:
    """A table's `source_lineage` for an entity partition that reads a physical table (Direct
    Lake): the table is `entity`, in `schema`, in the lakehouse or warehouse the shared
    expression connects to (`database`), at the endpoint it names (`connection`).

    On a SQL endpoint with no `schemaName` the table is in `dbo`, the endpoint's only schema
    for a lakehouse without schemas; nothing is assumed for a OneLake path. The evidence is
    the scrubbed shared expression."""
    expr = m_expr or ""
    base = lineage_from_m(expr) if expr else {"connection": None, "database": None}
    connection, database = base.get("connection"), base.get("database")
    clean = _strip_m_comments_keeping_strings(expr)
    lake = _ONELAKE.search(clean)
    if lake and not database:
        connection = lake.group(1)
        segs = [s for s in lake.group(1).split("?", 1)[0].rstrip("/").split("/")
                if s and s not in ("Tables", "Files")]
        database = segs[-1] if len(segs) > 3 else None   # scheme, '', host, then the path
    if not schema and _SQL_ENDPOINT.search(clean):
        schema = "dbo"
    return {"connection": connection, "database": database, "schema": schema or None,
            "object": entity.lower() if entity else None,
            "object_kind": "table" if entity else None,
            "raw_expression": scrub_m(expr) or None,
            "published_datasource": None}


def qualified(lineage: dict) -> str | None:
    """The warehouse name a table's lineage resolves to, `database.schema.object` lower-cased.

    Shared by every Power BI reader so one model read from a TMDL directory, a `.bim`/`.tmsl`
    or a `.pbix` carries the same physical table name. A reader that kept the logical name
    compiled `FROM <logical>` unqualified, which a warehouse resolves against the session's
    default schema — a different table, or none."""
    obj = lineage.get("object")
    if not obj:
        return None
    if obj.count(".") >= 2:
        return obj
    parts = [p for p in (lineage.get("database"), lineage.get("schema")) if p]
    return ".".join([*(p.lower() for p in parts), obj]) if parts else obj


def attach_native_statements(tables: list[dict]) -> list[dict]:
    """Say, in the common format, which tables are a SQL statement — so no stage reads M.

    A partition whose source is a native query is the query's RESULT SET, and A3 compiles
    against the query itself. Two spellings reach here: `native-sql`, where `raw_expression`
    IS the statement, and `native_query`, where it is the partition's M and the statement is
    still inside it. Both resolve here, once, to `source_lineage.native_sql`; and each model
    column to the name it has in the statement's output, `source_lineage.native_columns`.

    The reader LOWERCASES source_column, which is right for comparison and wrong for a quoted
    alias: a query that emits `"AE Name"` has no column `"ae name"`, and 10 of 14 witnessed
    statements on one model failed on exactly that. The exact spelling is recovered from the
    query's own quoted identifiers; an unquoted name is emitted bare, which the warehouse
    resolves case-insensitively.

    Only a table with a logical name is keyed (A2 finds a native table by the measure's
    declared home, which is the logical name)."""
    for t in tables:
        lin = t.get("source_lineage") or {}
        kind = lin.get("object_kind")
        if kind not in ("native_query", "native-sql") or not t.get("logical_name"):
            continue
        raw = lin.get("raw_expression") or ""
        sql = raw if kind == "native-sql" else native_sql(strip_m_comments(raw))
        if not sql:
            continue
        quoted = {q.lower(): q for q in re.findall(r'"([^"]+)"', sql)}
        lin["native_sql"] = sql
        lin["native_columns"] = [
            {"name": c["name"],
             "column": quoted.get((c.get("source_column") or c["name"]).lower(),
                                  c.get("source_column") or c["name"])}
            for c in t.get("columns") or []]
    return tables


# ── what the table IS: Power Query steps (readers/mquery_steps) ───────────────────────────

# The run's SQL dialect, while a Power BI reader reads a model for a run (`run_dialect`): a
# table's statement is run on the run's warehouse, so it is written in its dialect. Without one
# the dialect of the connector the table is read through is used.
_RUN_DIALECT: contextvars.ContextVar[str] = contextvars.ContextVar("run_dialect", default="")


@contextlib.contextmanager
def run_dialect(dialect: str | None):
    token = _RUN_DIALECT.set(dialect or "")
    try:
        yield
    finally:
        _RUN_DIALECT.reset(token)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower()).strip("_") or "table"


def read_power_query(tables: list[dict], expressions: dict[str, str] | None = None) -> list[str]:
    """Say, in the common format, what each table's Power Query makes of its warehouse table.

    Every Power BI reader (TMDL, `.bim`/scan, `.pbix`) leaves a table's M on `_m` and each
    column's M output name on `_source_exact`; this reads them ONCE, for all of them, so one
    model compiles the same from any container (and pops both keys). Per table:

    * the bare warehouse table (navigation, a native query, renames, a column selection):
      the lineage as before, a renamed column read by its warehouse name, a table read
      through another query (`Source = #"Staging"`) named by that query's navigation, and a
      native query built from parameters written out with their values;
    * a table whose steps change its rows or values: `source_lineage.statement`, the SQL
      returning exactly the model table, each column under the name `source_column` says,
      and the warehouse tables it reads (`reads`). Its `name` is the reader's own, never a
      warehouse table's, so nothing reads the bare table by mistake;
    * no faithful SQL (a workbook, a file, a step the reader cannot write):
      `source_lineage.refusal`, by name, and no warehouse object.

    Returns notes for the operator, one per refused table."""
    from metrics_agent.plugs.readers import mquery_steps as _steps  # noqa: PLC0415

    notes: list[str] = []
    expressions = expressions or {}
    for t in tables:
        m = t.pop("_m", None)
        cols = t.get("columns") or []
        exact = {id(c): c.pop("_source_exact", None) for c in cols}
        if not m:
            continue
        plain_cols = [c for c in cols if not c.get("is_calculated")]
        outs = [(exact[id(c)] or c.get("source_column") or c["name"], c.get("data_type"))
                for c in plain_cols]
        res = _steps.read_table(m, expressions=expressions, columns=outs,
                                dialect=_RUN_DIALECT.get() or None)
        logical = t.get("logical_name") or t["name"]
        lin = dict(t.get("source_lineage") or source_lineage(m))
        if res.kind == "legacy":
            renames = column_renames(m)
            for c in plain_cols:
                key = exact[id(c)] or c.get("source_column") or c["name"]
                if key in renames:
                    c["source_column"] = renames[key].lower()
            continue
        if res.kind == "plain":
            if res.native_sql is not None and res.native_sql != native_sql(strip_m_comments(m)):
                target = _target_from_sql(res.native_sql)
                lin.update(object_kind="native-sql", raw_expression=scrub_m(res.native_sql))
                if target:
                    lin.update(database=target[0] or res.db, schema=target[1],
                               object=target[2].lower())
            if not lin.get("object") and res.base_object:
                lin.update(database=res.db, schema=res.schema,
                           object=res.base_object.lower(), object_kind="table")
            for c in plain_cols:
                key = exact[id(c)] or c.get("source_column") or c["name"]
                if key in res.renames:
                    c["source_column"] = res.renames[key].lower()
            t["source_lineage"] = lin
            q = qualified(lin)
            t["name"] = q or logical
            if q and q != logical:
                t["logical_name"] = logical
            continue
        base = {k: lin.get(k) for k in ("connection", "database", "schema", "object",
                                        "object_kind", "raw_expression",
                                        "published_datasource")}
        base["raw_expression"] = scrub_m(m) or None
        t["logical_name"] = logical
        if res.kind == "statement":
            base.update(database=res.db, schema=res.schema, object=None,
                        object_kind="statement", statement=res.sql, reads=res.reads)
            for c in plain_cols:
                key = exact[id(c)] or c.get("source_column") or c["name"]
                c["source_column"] = res.outputs.get(key, key).lower()
            t["source_lineage"] = base
            # The reader's own name for the statement: the warehouse database and schema it
            # reads, and a table name no warehouse table has.
            t["name"] = ".".join([(res.db or "power_query").lower(),
                                  (res.schema or "power_query").lower(),
                                  _slug(logical) + "__power_query"])
            continue
        base.update(object=None, refusal=f"table {logical!r}: {res.reason}")
        t["source_lineage"] = base
        t["name"] = logical
        notes.append(f"table {logical!r} has no faithful SQL: {res.reason}")
    _split_roles(tables)
    # A DAX calculated table: the statement of its rows, in the run's dialect.
    from metrics_agent.plugs.readers import daxtables as _daxtables  # noqa: PLC0415
    notes += _daxtables.read_calculated_tables(tables, _RUN_DIALECT.get() or None)
    return notes


def _split_roles(tables: list[dict]) -> None:
    """ROLE-PLAYING TABLES. Two model tables that read one warehouse table ('Order Date' and
    'Ship Date' over one date dimension) are two tables of the model: each has its own
    relationships and its own filters. Under one warehouse name they would be one node of the
    join graph, so every table after the first reads it as a statement of its own
    (`SELECT *` of that table) under its own name."""
    seen: set[str] = set()
    for t in tables:
        lin = t.get("source_lineage") or {}
        q = str(t.get("name") or "")
        if (not lin.get("object") or lin.get("statement") or lin.get("native_sql")
                or lin.get("object_kind") not in (None, "table", "view") or q.count(".") < 2):
            # A native query or a statement is its own rows already; only a plain table read
            # under two names is split.
            continue
        if q.lower() not in seen:
            seen.add(q.lower())
            continue
        logical = t.get("logical_name") or q.split(".")[-1]
        db, schema, _obj = q.split(".", 2)
        t["source_lineage"] = {**lin, "object": None, "object_kind": "statement",
                               "statement": f"SELECT *\nFROM   {q}", "reads": [q]}
        t["logical_name"] = logical
        t["name"] = ".".join([db.lower(), schema.lower(), _slug(logical) + "__power_query"])
