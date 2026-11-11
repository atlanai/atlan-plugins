"""A table's Power Query (M) partition as a SQL relation, or a refusal.

Two shapes are translated, which is what an imported or DirectQuery warehouse
table almost always is:

    native   Value.NativeQuery(<connector>(...)…, "SELECT …", …)
    table    <connector>(...){[Name="db"]}[Data]{[Name="schema"]}[Data]{[Name="t"]}[Data]
             (Sql.Database uses {[Schema="s", Item="t"]})

followed by steps that do not change what a row means: a type change, column
removal, an identity rename, and Table.SelectRows with a simple predicate,
which becomes a WHERE. Any other step (a merge, a pivot, a value replacement,
a computed column, a real rename) changes the rows or the columns after the
SQL, and the table is refused with the step named.

The connector fixes the dialect the relation's SQL is written in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# M connector function -> sqlglot dialect
CONNECTORS = {
    "Databricks.Catalogs": "databricks", "Databricks.Contents": "databricks",
    "Databricks.Query": "databricks", "AzureDatabricks.Catalogs": "databricks",
    "Snowflake.Databases": "snowflake",
    "Sql.Database": "tsql", "Sql.Databases": "tsql", "AzureSqlDatabase.Database": "tsql",
    "AmazonRedshift.Database": "redshift", "PostgreSQL.Database": "postgres",
    "GoogleBigQuery.Database": "bigquery", "Oracle.Database": "oracle",
    "MySQL.Database": "mysql", "Teradata.Database": "teradata",
}
# steps that keep every row and the meaning of every column
_NEUTRAL = ("Table.TransformColumnTypes", "Table.RemoveColumns", "Table.SelectColumns",
            "Table.ReorderColumns", "Table.Buffer")


class MRefuse(Exception):
    pass


@dataclass
class Relation:
    sql: str                                    # SELECT … in `dialect`, ready to sit in a CTE
    dialect: str
    kind: str                                   # native | table
    tables: list[str] = field(default_factory=list)      # physical tables it reads
    where: list[str] = field(default_factory=list)       # Power Query row filters, as SQL
    notes: list[str] = field(default_factory=list)
    renames: dict[str, str] = field(default_factory=dict)    # name after Power Query -> the SQL's column
    computed: dict[str, str] = field(default_factory=dict)   # columns Power Query adds -> their M

    def physical(self, name: str) -> str:
        """The relation's own column for a column the model sees, or a refusal."""
        if name in self.computed:
            raise MRefuse(f"Power Query: column {name} is computed in M ({self.computed[name][:50]})")
        return self.renames.get(name, name)


def _m_string(s: str) -> str:
    """An M text literal's content: "" is a quote, #(lf)/#(cr)/#(tab) escapes."""
    return (s.replace('""', '"').replace("#(lf)", "\n").replace("#(cr)", "")
            .replace("#(tab)", "\t").replace("#(cr,lf)", "\n"))


def _steps(m: str) -> list[tuple[str, str]]:
    """(name, expression) for each step of a `let … in …` block."""
    body = re.search(r"\blet\b(.*)\bin\b\s*(?:#\"[^\"]+\"|\w+)\s*$", m.strip(), re.S)
    if not body:
        return [("", m.strip())]
    text, out, depth, cur, in_str, i = body.group(1), [], 0, "", False, 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            if in_str and text[i:i + 2] == '""':
                cur += '""'
                i += 2
                continue
            in_str = not in_str
        elif not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(cur)
                cur = ""
                i += 1
                continue
        cur += ch
        i += 1
    out.append(cur)
    steps = []
    for s in out:
        mm = re.match(r'\s*(#"(?:[^"]|"")+"|[A-Za-z_]\w*)\s*=\s*(.*)$', s, re.S)
        if mm:
            steps.append((mm.group(1).strip(), mm.group(2).strip()))
    return steps


def parameters(expressions: dict[str, str]) -> dict[str, str]:
    """M parameters: `"SILVER" meta [IsParameterQuery=true, …]` -> SILVER."""
    out = {}
    for name, body in expressions.items():
        mm = re.match(r'\s*"((?:[^"]|"")*)"\s*(?:meta\b|$)', body or "")
        if mm:
            out[name.strip('#"')] = mm.group(1).replace('""', '"')
    return out


def relation(m: str, quote, params: dict[str, str] | None = None) -> Relation:
    """`quote(name, dialect)` quotes an identifier; supplied by the caller so
    this module stays free of sqlglot. `params` resolves M parameters."""
    params = params or {}
    if not (m or "").strip():
        raise MRefuse("Power Query: the table has no M source")
    dialect = next((d for fn, d in CONNECTORS.items() if re.search(rf"\b{re.escape(fn)}\s*\(", m)), None)
    if dialect is None:
        fn = re.search(r"\b([A-Z][A-Za-z]+\.(?:Database|Databases|Contents|Files?|Workbook|Tables?|Document|FromRows|FromList))\s*\(", m)
        raise MRefuse(f"non-warehouse source: {fn.group(1) if fn else 'an M expression with no warehouse connector'}")
    steps = _steps(m)
    native = _native_sql(m)
    if native is not None:
        rel = Relation(_m_string(native).strip().rstrip(";"), dialect, "native")
        start = next(i for i, (_, e) in enumerate(steps) if "Value.NativeQuery" in e)
    else:
        rel, start = _navigation(m, steps, dialect, quote, params)
    for name, expr in steps[start + 1:]:
        fn = re.match(r"(Table\.\w+)\s*\(", expr)
        fn = fn.group(1) if fn else ""
        if fn in _NEUTRAL:
            continue
        if fn == "Table.RenameColumns":
            for old, new in re.findall(r'\{\s*"((?:[^"]|"")*)"\s*,\s*"((?:[^"]|"")*)"\s*\}', expr):
                if old in rel.computed:
                    rel.computed[new] = rel.computed.pop(old)
                else:
                    rel.renames[new] = rel.renames.pop(old, old)
            continue
        if fn == "Table.AddColumn":
            added = re.match(r'Table\.AddColumn\s*\(\s*[^,]+,\s*"((?:[^"]|"")*)"\s*,(.*)\)\s*$', expr, re.S)
            if not added:
                raise MRefuse(f"Power Query: step {name} adds a column this adapter cannot read")
            rel.computed[added.group(1)] = added.group(2).strip()
            continue
        if fn == "Table.SelectRows":
            rel.where.append(_predicate(expr, dialect, quote, rel))
            continue
        raise MRefuse(f"Power Query: step {name} ({fn or 'expression'}) changes the rows after the SQL")
    return rel


def _split_args(text: str) -> list[str]:
    """Top-level comma-separated arguments of a call body (no outer parens)."""
    out, depth, cur, in_str, i = [], 0, "", False, 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            if in_str and text[i:i + 2] == '""':
                cur += '""'
                i += 2
                continue
            in_str = not in_str
        elif not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    out.append(cur)
                    return out
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(cur)
                cur = ""
                i += 1
                continue
        cur += ch
        i += 1
    out.append(cur)
    return out


def _native_sql(m: str) -> str | None:
    """The SQL text: the second argument of Value.NativeQuery, a string literal."""
    at = re.search(r"Value\.NativeQuery\s*\(", m)
    if not at:
        return None
    args = _split_args(m[at.end():])
    if len(args) < 2:
        raise MRefuse("Power Query: Value.NativeQuery without a query argument")
    q = args[1].strip()
    if not (q.startswith('"') and q.endswith('"')):
        raise MRefuse("Power Query: the native query is built from an expression, not a literal")
    return q[1:-1]


def _navigation(m: str, steps, dialect: str, quote, params) -> tuple[Relation, int]:
    parts: list[str] = []
    last = 0
    for i, (_, expr) in enumerate(steps):
        found = re.findall(r'\{\s*\[([^\]]*)\]\s*\}\s*\[Data\]', expr)
        for item in found:
            kv = {}
            for k, quoted, bare in re.findall(r'(\w+)\s*=\s*(?:"((?:[^"]|"")*)"|(#"[^"]+"|[A-Za-z_]\w*))', item):
                if bare:
                    if bare.strip('#"') not in params:
                        raise MRefuse(f"Power Query: navigation uses M parameter {bare} with no value")
                    kv[k] = params[bare.strip('#"')]
                else:
                    kv[k] = quoted
            if "Schema" in kv and "Item" in kv:
                parts += [kv["Schema"], kv["Item"]]
            elif "Name" in kv:
                parts.append(kv["Name"])
        if found:
            last = i
    if not parts:
        raise MRefuse("Power Query: neither a native query nor a navigation to a table")
    if dialect == "tsql":
        db = re.search(r'Sql\.Databases?\s*\(\s*"[^"]*"\s*,\s*"([^"]+)"', m)
        if db:
            parts = [db.group(1)] + parts
    name = ".".join(quote(p, dialect) for p in parts)
    return Relation(f"SELECT * FROM {name}", dialect, "table", tables=[".".join(parts)]), last


def _predicate(expr: str, dialect: str, quote, rel: Relation | None = None) -> str:
    """Table.SelectRows(x, each <predicate>) for =, <>, <, >, and, or, null."""
    body = re.search(r"\beach\s+(.*)\)\s*$", expr, re.S)
    if not body:
        raise MRefuse("Power Query: a row filter that is not `each …`")
    p = body.group(1).strip()
    if p.startswith("(") and p.endswith(")"):
        p = p[1:-1]
    p = re.sub(r"#date\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)",
               lambda d: f"@D{int(d.group(1)):04d}-{int(d.group(2)):02d}-{int(d.group(3)):02d}", p)
    toks = re.findall(r'\[[^\]]+\]|"(?:[^"]|"")*"|@D\d{4}-\d{2}-\d{2}|<>|>=|<=|[=<>()]|\band\b|\bor\b|\bnot\b|null|true|false|-?\d+(?:\.\d+)?', p)
    if re.sub(r"\s+", "", "".join(toks)) != re.sub(r"\s+", "", p):
        raise MRefuse(f"Power Query: row filter not translatable: {p[:60]}")
    out = []
    for i, t in enumerate(toks):
        if t.startswith("["):
            out.append(quote(rel.physical(t[1:-1]) if rel else t[1:-1], dialect))
        elif t.startswith("@D"):
            out.append(f"DATE '{t[2:]}'")
        elif t.startswith('"'):
            out.append("'" + t[1:-1].replace('""', '"').replace("'", "''") + "'")
        elif t == "null":
            prev = out[-1] if out else ""
            if prev in ("=", "<>"):
                out[-1] = "IS" if prev == "=" else "IS NOT"
            out.append("NULL")
        else:
            out.append(t.upper() if t in ("and", "or", "not", "true", "false") else t)
    return "(" + " ".join(out) + ")"
