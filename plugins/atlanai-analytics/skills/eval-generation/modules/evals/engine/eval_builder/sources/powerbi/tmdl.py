"""TMDL (Tabular Model Definition Language) as plain objects.

A PBIP semantic model saved in TMDL is a folder of indented text files:

    definition/
      model.tmdl            model properties, `ref table` lines
      relationships.tmdl    relationship <id> / fromColumn / toColumn / isActive
      expressions.tmdl      shared M expressions and parameters
      tables/<name>.tmdl    table, its columns, measures, partitions

The grammar that matters here is small. An object line is
`<keyword> <name>[ = <expression>]`, its properties are indented one level
deeper as `<key>: <value>` (or a bare flag), and an expression continues on
the more-indented lines after `=`, or sits between ``` fences. Names may be
single-quoted with '' as the escape. Anything else (annotations,
lineageTag, changedProperty) is read and ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Node:
    keyword: str
    name: str = ""
    expression: str | None = None
    props: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)

    def all(self, keyword: str) -> list["Node"]:
        return [c for c in self.children if c.keyword == keyword]


_OBJECT = re.compile(r"^(createOrReplace\s+)?(?P<kw>[A-Za-z]+)(?:\s+(?P<name>'(?:[^']|'')*'|[^=:\s][^=]*?))?\s*(?:=\s*(?P<expr>.*))?$")
_PROP = re.compile(r"^(?P<key>[A-Za-z][\w]*)\s*:\s*(?P<val>.*)$")
_OBJECTS = {"model", "table", "column", "measure", "partition", "relationship", "expression",
            "hierarchy", "level", "calculationGroup", "calculationItem", "role", "perspective",
            "culture", "annotation", "extendedProperty", "ref", "database", "dataSource",
            "tablePermission", "formatStringDefinition", "queryGroup", "calendar", "function"}


def unquote(name: str) -> str:
    name = name.strip()
    return name[1:-1].replace("''", "'") if name.startswith("'") and name.endswith("'") else name


def _indent(line: str) -> int:
    n = 0
    for ch in line:
        if ch == "\t":
            n += 1
        elif ch == " ":
            n += 0.25            # 4 spaces count as one tab
        else:
            break
    return int(n)


def parse(text: str) -> list[Node]:
    """Top-level objects of one TMDL file."""
    lines = text.replace("\r\n", "\n").split("\n")
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("///") or stripped.startswith("//"):
            i += 1
            continue
        depth = _indent(raw)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        m = _OBJECT.match(stripped)
        prop = _PROP.match(stripped)
        is_object = m and m.group("kw") in _OBJECTS and not (prop and m.group("kw") not in _OBJECTS)
        if is_object and prop and not m.group("name") and not m.group("expr"):
            is_object = False
        if is_object:
            node = Node(m.group("kw"), unquote(m.group("name") or ""))
            expr, i = _expression(m.group("expr"), lines, i, depth)
            node.expression = expr
            (parent.children if parent else roots).append(node)
            stack.append((depth, node))
            continue
        if parent is not None:
            pm = re.match(r"^(?P<key>[A-Za-z][\w]*)\s*(?:(?P<sep>[:=])\s*(?P<val>.*))?$", stripped)
            if pm:
                key, sep, val = pm.group("key"), pm.group("sep"), pm.group("val")
                if sep == "=":           # `source = …` on a partition, `expression = …`
                    expr, i = _expression(val, lines, i, depth)
                    parent.props[key] = expr or ""
                    continue
                parent.props[key] = (val or "true").strip()
        i += 1
    return roots


def _expression(first: str | None, lines: list[str], i: int, depth: int) -> tuple[str | None, int]:
    """The expression after `=` on line i, with its continuation lines.
    Returns (expression, index of the next unread line)."""
    if first is None:
        return None, i + 1
    first = first.strip()
    if first.startswith("```"):
        body, j = [first[3:]] if first[3:].strip() else [], i + 1
        while j < len(lines) and not lines[j].strip().startswith("```"):
            body.append(lines[j])
            j += 1
        return _dedent(body), j + 1
    body, j = ([first] if first else []), i + 1
    # continuation: lines indented deeper than the object line, until a line at
    # or above it, or a property at exactly one level deeper after the body
    while j < len(lines):
        line = lines[j]
        if line.strip() and _indent(line) <= depth:
            break
        if line.strip() and _indent(line) == depth + 1 and _PROP.match(line.strip()) and body \
                and not body[-1].rstrip().endswith((",", "(", "=")):
            break
        if line.strip() and _indent(line) == depth + 1 and _OBJECT.match(line.strip()) \
                and _OBJECT.match(line.strip()).group("kw") in _OBJECTS and body:
            break
        body.append(line)
        j += 1
    return (_dedent(body) or None), j


def _dedent(body: list[str]) -> str:
    while body and not body[-1].strip():
        body.pop()
    ind = [len(l) - len(l.lstrip()) for l in body if l.strip()]
    cut = min(ind) if ind else 0
    return "\n".join(l[cut:] if len(l) >= cut else l.lstrip() for l in body).strip()


def load_folder(definition: Path) -> list[Node]:
    """Every object in a TMDL `definition/` folder."""
    out: list[Node] = []
    for p in sorted(Path(definition).rglob("*.tmdl")):
        out.extend(parse(p.read_text(encoding="utf-8-sig")))
    return out


def column_ref(ref: str) -> tuple[str, str]:
    """`'My Table'.'My Col'`, `Table.Col` or `'Table'[Col]` -> (table, column)."""
    ref = ref.strip()
    m = re.match(r"^('(?:[^']|'')*'|[^.\[]+)\s*(?:\.|\[)\s*('(?:[^']|'')*'|[^\]]+)\]?$", ref)
    if not m:
        raise ValueError(f"not a column reference: {ref!r}")
    return unquote(m.group(1)), unquote(m.group(2))
