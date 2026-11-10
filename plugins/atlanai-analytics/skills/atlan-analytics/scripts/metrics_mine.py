#!/usr/bin/env python3
"""metrics_mine: the deterministic half of building a metric layer.

The skill's judgement (what a statement measures, which proposals are one metric, how a card
reads) is the assistant's. Everything that must come out the same every time lives here:
grouping a cleaned query log into ranked query shapes, checking authored metric and saved-query
bodies against the registry's contracts before anything is previewed, matching them against
what the workspace already holds, and reading a finished metrics-agent run so its output is
published the same way. Standard library only, Python 3.10+.

    python3 metrics_mine.py <command> [options]        # every command prints one JSON object

Exit 0 ok, 1 error, 2 BLOCKED ({"status":"BLOCKED","reason","fix"}: something the user must fix).

Run directory (<context-dir>), written by the skill:
  querylog.jsonl         {sql, exec, users}               <- `querylog`, from the saved history pull
  querylog.clean.jsonl   {sql, exec, users}               <- after `analytics_eval.py filter-querylog`
  shapes.jsonl           one ranked shape per line        <- `shapes`
  metrics.json           [metric body]                    <- authored, or `import-run`
  queries.json           [sql_query body]                 <- authored, or `import-run`
  links.json             [{from, to, rel_type, attributes}]
  existing.json          [{id, kind, name, source_id, definition}]  <- the workspace, read before checking
  plan.json              what `check` decided: create, revise or reuse, per item, and every refusal
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import re
import sys

# The identifying-literal rules live in analytics_eval, beside the query-log filter that uses them
# first: a statement that filter keeps is judged the same way here.
# No __pycache__ is written into the installed plugin's folder.
sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from analytics_eval import PII as _SHARED_PII, full_name_hit  # noqa: E402
import registry_contract  # noqa: E402

VERSION = "0.1.0"
METRIC_SPEC = "1.0-draft.4"
QUERY_SPEC = "1.0-draft.1"


class Blocked(Exception):
    def __init__(self, reason: str, fix: str = ""):
        super().__init__(reason)
        self.reason, self.fix = reason, fix


def read_jsonl(path) -> list:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def load_json(path, default=None):
    p = pathlib.Path(path) if path else None
    return json.loads(p.read_text()) if p and p.exists() else default


def dump_json(path, obj) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(p)


# ====================================================================== query log intake
# A query-history pull can be 5,000 rows of SQL. These turn whatever holds it (a tool result the
# host saved to a file, CLI output, an export) into querylog.jsonl without the rows passing back
# through the conversation. Only the statement, its run count and a distinct-user count are kept:
# user names are counted while reading and never written.
_SQL_COLS = ("sql", "query_text", "statement_text", "query", "text")
_EXEC_COLS = ("exec", "execs", "executions", "count", "run_count", "runs")
_USERS_COLS = ("users", "user_count", "distinct_users")
_USER_COLS = ("user_name", "executed_by", "executed_by_user_id", "executed_as", "user_id", "user")
_STATUS_COLS = ("execution_status", "status", "state")
_TYPE_COLS = ("query_type", "statement_type")
_OK_STATUS = {"success", "succeeded", "finished"}


def _records(value) -> list:
    """Rows as dicts from any shape a warehouse tool, CLI or export returns them in."""
    if isinstance(value, str):
        try:
            return _records(json.loads(value))
        except ValueError:
            return []
    if isinstance(value, list):
        if not all(isinstance(r, dict) for r in value):
            return []
        if value and all("type" in r and "text" in r for r in value):  # MCP content blocks
            return [x for r in value for x in _records(r["text"])]
        return value
    if not isinstance(value, dict):
        return []
    # A JSONL line is itself a row, not a result envelope. Prefer explicit SQL
    # fields so an MCP {type: text, text: ...} block remains an envelope.
    low = {str(k).lower(): v for k, v in value.items()}
    if any(isinstance(low.get(k), str) for k in _SQL_COLS[:-1]):
        return [value]
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    cols = value.get("columns") or ((value.get("manifest") or {}).get("schema") or {}).get("columns")
    rows = value.get("rows") if isinstance(value.get("rows"), list) else (value.get("data_array") or result.get("data_array"))
    if isinstance(cols, list) and isinstance(rows, list) and all(isinstance(r, list) for r in rows):
        names = [c.get("name") if isinstance(c, dict) else c for c in cols]
        return [dict(zip(names, r)) for r in rows]
    for key in ("structuredContent", "content", "data", "result", "results", "items", "rows"):
        if key in value:
            got = _records(value[key])
            if got:
                return got
    return []


def _pick(row: dict, names: tuple):
    low = {str(k).lower(): v for k, v in row.items()}
    return next((low[n] for n in names if n in low and low[n] not in (None, "")), None)


def read_querylog_source(path: pathlib.Path) -> list:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        import csv
        import io
        return list(csv.DictReader(io.StringIO(text)))
    try:
        return _records(json.loads(text))
    except ValueError:
        return [r for ln in text.splitlines() if ln.strip() for r in _records(ln)]


def cmd_querylog(a) -> dict:
    """Group a history pull into {sql, exec, users} lines; counts only, never the statements."""
    groups: dict = {}
    dst = pathlib.Path(a.dst)
    if a.append:
        for r in read_jsonl(dst):
            groups[r["sql"]] = {"exec": int(r.get("exec") or 0), "users": int(r.get("users") or 0), "seen": set()}
    read = skipped = 0
    for src in a.src:
        rows = read_querylog_source(pathlib.Path(src))
        if not rows:
            raise Blocked(f"No rows found in {src}.", "Pass the saved tool result, CLI output or export that holds "
                          "the history pull, or check it is not an error message.")
        for row in rows:
            read += 1
            sql = _pick(row, _SQL_COLS)
            status = str(_pick(row, _STATUS_COLS) or "success").lower()
            kind = str(_pick(row, _TYPE_COLS) or "select").lower()
            if not isinstance(sql, str) or status not in _OK_STATUS or kind != "select":
                skipped += 1
                continue
            g = groups.setdefault(sql, {"exec": 0, "users": 0, "seen": set()})
            g["exec"] += int(_pick(row, _EXEC_COLS) or 1)
            given = _pick(row, _USERS_COLS)
            who = _pick(row, _USER_COLS)
            if given is not None:
                g["users"] = max(g["users"], int(given))
            elif who is not None:
                g["seen"].add(hashlib.sha256(str(who).encode()).hexdigest())
                g["users"] = max(g["users"], len(g["seen"]))
    ranked = sorted(groups.items(), key=lambda kv: -kv[1]["exec"])[: a.limit]
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w") as f:
        for sql, g in ranked:
            f.write(json.dumps({"sql": sql, "exec": g["exec"], "users": g["users"]}) + "\n")
    return {"status": "ok", "rows_read": read, "skipped": skipped, "statements": len(ranked),
            "dropped_over_limit": max(0, len(groups) - len(ranked)), "out": str(dst)}


# ====================================================================== SQL scanning
# Comments are removed by a scanner that knows where strings end, never by a line regex: a `--`
# inside a string is text, and flattening a statement that still holds a `--` comments out the
# rest of it.
_STR_QUOTES = {"'": "'", '"': '"', "`": "`"}


def scan(sql: str):
    """Yield (kind, text): kind is code, str ('...'), ident ("..." or `...`), dollar ($$...$$) or comment."""
    i, n, start = 0, len(sql), 0
    while i < n:
        c = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            if start < i:
                yield "code", sql[start:i]
            j = sql.find("\n", i)
            j = n if j < 0 else j
            yield "comment", sql[i:j]
            i = start = j
            continue
        if two == "/*":
            if start < i:
                yield "code", sql[start:i]
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            yield "comment", sql[i:j]
            i = start = j
            continue
        if two == "$$":
            if start < i:
                yield "code", sql[start:i]
            j = sql.find("$$", i + 2)
            j = n if j < 0 else j + 2
            yield "dollar", sql[i:j]
            i = start = j
            continue
        if c in _STR_QUOTES:
            if start < i:
                yield "code", sql[start:i]
            q, j = c, i + 1
            while j < n:
                if sql[j] == "\\" and q == "'":
                    j += 2
                    continue
                if sql[j] == q:
                    if j + 1 < n and sql[j + 1] == q:
                        j += 2
                        continue
                    break
                j += 1
            j = min(j + 1, n)
            yield ("str" if q == "'" else "ident"), sql[i:j]
            i = start = j
            continue
        i += 1
    if start < n:
        yield "code", sql[start:n]


def strip_comments(sql: str) -> str:
    out = []
    for kind, text in scan(sql or ""):
        if kind == "comment":
            out.append("\n" if text.startswith("--") else " ")
        else:
            out.append(text)
    return "".join(out).strip().rstrip(";").strip()


_NUM = re.compile(r"(?<![\w.$])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?(?![\w$])", re.I)
_INLIST = re.compile(r"\bin\s*\(\s*\?(?:\s*,\s*\?)*\s*\)", re.I)
_WS = re.compile(r"\s+")


def normalize(sql: str) -> str:
    """The text a shape is keyed on: no comments, literals as ?, one space, lower case."""
    parts = []
    for kind, text in scan(sql or ""):
        if kind == "comment":
            parts.append(" ")
        elif kind in ("str", "dollar"):
            parts.append("?")
        elif kind == "ident":
            parts.append(text.lower())
        else:
            parts.append(_NUM.sub("?", text).lower())
    out = _WS.sub(" ", "".join(parts)).strip().rstrip(";").strip()
    return _INLIST.sub("in (?)", out)


_DATELIKE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[ T][\d:.]+)?(?:Z|[+-]\d{2}:?\d{2})?$")


def predicate_literals(sql: str) -> list:
    """The quoted values that pick a population, in order: `status = 'paid'` keeps 'paid'. Dates
    and bare numbers are left out, since a window or a limit changes when a query ran, not what it
    measures. Shapes key on these as well as on normalize(), so paid and refunded revenue stay two
    shapes of one family."""
    out = []
    for kind, text in scan(sql or ""):
        if kind != "str":
            continue
        value = text[1:-1].replace("''", "'") if len(text) > 1 else ""
        if not value.strip() or _DATELIKE.match(value.strip()) or _NUM.fullmatch(value.strip()):
            continue
        out.append(value)
    return out


def _blank(kind: str, text: str) -> str:
    if kind == "comment":
        return re.sub(r"[^\n]", " ", text)
    if kind in ("str", "dollar"):
        return text[0] + "x" * max(len(text) - 2, 0) + text[-1] if len(text) > 1 else text
    return text


def _code_only(sql: str) -> str:
    """Comments and string contents blanked at the same offsets, so keyword searches never match
    inside a literal and a match's position is its position in the original text."""
    return "".join(_blank(k, t) for k, t in scan(sql or ""))


def _balanced(text: str, open_at: int) -> int:
    """Index just past the parenthesis that closes the one at `open_at`."""
    depth = 0
    for k in range(open_at, len(text)):
        if text[k] == "(":
            depth += 1
        elif text[k] == ")":
            depth -= 1
            if depth == 0:
                return k + 1
    return len(text)


def _split_top(text: str, sep: re.Pattern) -> list:
    """Split on `sep` only where no parenthesis is open."""
    out, last, depth_at, d = [], 0, [], 0
    for ch in text:
        depth_at.append(d)
        d += 1 if ch == "(" else -1 if ch == ")" else 0
    for m in sep.finditer(text):
        if depth_at[m.start()] == 0:
            out.append(text[last:m.start()])
            last = m.end()
    out.append(text[last:])
    return [p.strip() for p in out if p.strip()]


AGG = re.compile(r"\b(sum|count|count_if|avg|min|max|median|mode|stddev|stddev_samp|stddev_pop|variance|var_samp"
                 r"|approx_count_distinct|percentile_cont|percentile_disc|approx_percentile|listagg|array_agg"
                 r"|string_agg|any_value|bool_or|bool_and|sumif|countif|avgif)\s*\(", re.I)
_FROM = re.compile(r"\b(?:from|join)\s+(?!\()(?:lateral\s+)?([\w$\"`]+(?:\s*\.\s*[\w$\"`]+){0,3})", re.I)
_CTE = re.compile(r"(?:\bwith\s+(?:recursive\s+)?|,\s*)([\w$\"`]+)\s+as\s*\(", re.I)
_CLAUSE_END = r"(?=\bhaving\b|\border\s+by\b|\blimit\b|\bqualify\b|\bunion\b|\bwindow\b|\)|$)"
_GROUP = re.compile(r"\bgroup\s+by\s+(.+?)" + _CLAUSE_END, re.I | re.S)
_WHERE = re.compile(r"\bwhere\s+(.+?)(?=\bgroup\s+by\b|\bhaving\b|\border\s+by\b|\blimit\b|\bqualify\b|\bunion\b|\)\s*(?:select|$)|$)",
                    re.I | re.S)
_AND = re.compile(r"\band\b", re.I)
_COMMA = re.compile(r",")
_KEYWORDS = {"select", "lateral", "table", "unnest", "flatten", "values"}


def _ident(name: str) -> str:
    return re.sub(r"\s*\.\s*", ".", name).replace('"', "").replace("`", "").lower()


def features(sql: str) -> dict:
    """What a statement reads, computes, cuts by and restricts on. Hints for the author, not a parse."""
    code = _code_only(sql)
    ctes = {_ident(m.group(1)) for m in _CTE.finditer(code)}
    tables = []
    for m in _FROM.finditer(code):
        t = _ident(m.group(1))
        if t and t not in ctes and t.split(".")[-1] not in _KEYWORDS and t not in tables:
            tables.append(t)
    aggs = []
    for m in AGG.finditer(code):
        end = _balanced(code, m.end() - 1)
        expr = _WS.sub(" ", strip_comments(sql[m.start():end]))
        if expr not in aggs:
            aggs.append(expr)
    norm = normalize(sql)
    group_by, filters = [], []
    for m in _GROUP.finditer(norm):
        for g in _split_top(m.group(1), _COMMA):
            if g not in group_by:
                group_by.append(g)
    for m in _WHERE.finditer(norm):
        for f in _split_top(m.group(1), _AND):
            if f not in filters:
                filters.append(f)
    return {"tables": tables, "aggregates": aggs[:12], "group_by": group_by[:10], "filters": filters[:12],
            "window": bool(re.search(r"\bover\s*\(", code, re.I))}


def _in_scope(tables: list, scope: list) -> bool:
    if not scope:
        return True
    for t in tables:
        for s in scope:
            if t == s or t.endswith("." + s) or s.endswith("." + t):
                return True
    return False


def cmd_shapes(a) -> dict:
    """Group a cleaned query log by normalized text and rank the shapes worth authoring from."""
    rows = read_jsonl(a.src)
    if not rows:
        raise Blocked(f"{a.src} is empty or missing.", "Pull the query history first, then run filter-querylog on it.")
    scope = []
    if a.tables:
        scope = [_ident(ln.split("#")[0]) for ln in pathlib.Path(a.tables).read_text().splitlines() if ln.split("#")[0].strip()]
    groups: dict = {}
    unreadable = 0
    for r in rows:
        sql = r.get("sql") or r.get("query_text") or r.get("statement_text") or ""
        if not sql.strip():
            unreadable += 1
            continue
        family = normalize(sql)
        key = family + "\x00" + json.dumps(predicate_literals(sql))
        execs = int(r.get("exec") or r.get("count") or 1)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"key": key, "family": family, "rep": sql, "rep_exec": -1, "exec": 0, "users": 0,
                               "texts": 0}
        g["exec"] += execs
        g["users"] = max(g["users"], int(r.get("users") or 0))
        g["texts"] += 1
        if execs > g["rep_exec"] or (execs == g["rep_exec"] and sql < g["rep"]):
            g["rep"], g["rep_exec"] = sql, execs
    shapes, out_of_scope = [], 0
    for g in groups.values():
        sql = strip_comments(g["rep"])
        f = features(sql)
        if not _in_scope(f["tables"], scope):
            out_of_scope += 1
            continue
        shapes.append({"shape_id": "s-" + hashlib.sha1(g["key"].encode()).hexdigest()[:12],
                       "family": "f-" + hashlib.sha1(g["family"].encode()).hexdigest()[:12],
                       "predicates": predicate_literals(g["rep"]), "sql": sql, "exec": g["exec"], "users": g["users"], "texts": g["texts"],
                       "has_aggregate": bool(f["aggregates"]), **f})
    shapes.sort(key=lambda s: (not s["has_aggregate"], -s["users"], -s["exec"], s["shape_id"]))
    kept = shapes[: a.top]
    out = pathlib.Path(a.dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(s) + "\n" for s in kept))
    return {"status": "ok", "rows": len(rows), "unreadable": unreadable, "distinct_shapes": len(groups),
            "families": len({g["family"] for g in groups.values()}),
            "out_of_scope": out_of_scope, "in_scope": len(shapes),
            "with_aggregates": sum(1 for s in shapes if s["has_aggregate"]), "written": len(kept), "out": str(out)}


# ====================================================================== authoring coverage

def candidate_inventory(shapes, declarations=()):
    """Complete source evidence, not a rule that every aggregate is a business metric."""
    statements, candidates = [], []
    for shape in sorted(shapes, key=lambda row: row['shape_id']):
        sql = strip_comments(shape['sql'])
        ref = 'shape:' + shape['shape_id']
        found = []
        code = _code_only(sql)
        for match in AGG.finditer(code):
            end = _balanced(code, match.end() - 1)
            expression = sql[match.start():end]
            key = ref + '\0' + sql + '\0' + str(match.start())
            candidate = {'candidate_id': 'c-' + hashlib.sha256(key.encode()).hexdigest()[:16],
                         'source_ref': ref, 'expression': expression, 'kind': 'aggregate'}
            found.append(candidate['candidate_id'])
            candidates.append(candidate)
        statements.append({'source_ref': ref, 'sql': sql, 'candidates': found,
                           'observations': {**features(sql),
                                            'status': 'lexical_hints_read_the_full_sql'}})
    for declaration in sorted(declarations, key=lambda row: row['source_ref']):
        candidate = {**declaration, 'kind': 'declared'}
        candidate['candidate_id'] = 'c-' + hashlib.sha256(json.dumps(
            {k: declaration[k] for k in ('source_ref', 'name', 'expression') if k in declaration},
            sort_keys=True).encode()).hexdigest()[:16]
        candidates.append(candidate)
    body = {'version': 1, 'statements': statements, 'candidates': candidates}
    body['input_hash'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


def review_candidates(inventory, review, metric_names):
    """Reconcile every source quantity and every card without deciding its business meaning."""
    if review.get('input_hash') != inventory['input_hash']:
        raise ValueError('Candidate evidence changed. Read the current inventory and review it again.')
    expected = {c['candidate_id']: c for c in inventory['candidates']}
    decisions, covered, seen = review.get('decisions', []), set(), set()
    for decision in decisions:
        key = decision.get('candidate_id')
        if key not in expected or key in seen:
            raise ValueError('Use each current candidate identifier exactly once.')
        seen.add(key)
        disposition = decision.get('disposition')
        if disposition not in ('metric', 'component', 'non_metric', 'held') or not decision.get('reason'):
            raise ValueError('Give each candidate its disposition and an evidence-based reason.')
        targets = decision.get('metrics', [])
        if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets) or not set(targets) <= metric_names:
            raise ValueError('Candidate decisions must refer to existing metric names.')
        if disposition in ('metric', 'component') and not targets:
            raise ValueError('A metric or component must name the metric cards it supports.')
        if disposition in ('non_metric', 'held') and targets:
            raise ValueError('Held and non-metric candidates must not claim usable metric cards.')
        if expected[key]['kind'] == 'declared' and disposition not in ('metric', 'held'):
            raise ValueError('A declared measure is a metric or an explicitly held declaration.')
        if expected[key]['kind'] == 'declared' and disposition == 'metric' and len(targets) != 1:
            raise ValueError('A declared measure supports exactly one metric card; do not turn its decomposition into new declarations.')
        covered.update(targets)
    if seen != set(expected):
        raise ValueError('Review every candidate, including nested components and excluded aggregates.')
    if covered != metric_names:
        raise ValueError('Every authored metric needs a source candidate; reconcile unsupported cards.')
    return {'status': 'complete', 'input_hash': inventory['input_hash'],
            'candidates': len(expected), 'metrics': len(metric_names),
            'held': sum(d['disposition'] == 'held' for d in decisions), 'decisions': decisions}


def review_policy_hash():
    """Changing maintained review instructions invalidates completed annotations."""
    root = pathlib.Path(__file__).resolve().parents[3]
    paths = ('modules/metrics/engine/skills/references/review-metric-card.md',
             'modules/metrics/engine/skills/references/metric-consistency.md',
             'skills/atlan-analytics/references/metrics/authoring.md')
    return hashlib.sha256(json.dumps({p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                                     for p in paths}, sort_keys=True).encode()).hexdigest()


def card_review_bundle(inventory, candidate_review, metrics, catalog=()):
    """Give the author evidence and its draft together; never synthesize a card."""
    if candidate_review.get('input_hash') != inventory['input_hash']:
        raise ValueError('Reconcile the current source candidates before reviewing cards.')
    review_candidates(inventory, candidate_review, {m['name'] for m in metrics})
    candidates = {c['candidate_id']: c for c in inventory['candidates']}
    cards, field_catalog = [], {}
    policy_hash = review_policy_hash()
    for metric in sorted(metrics, key=lambda row: row['name']):
        refs = sorted({candidates[d['candidate_id']]['source_ref']
                       for d in candidate_review['decisions'] if metric['name'] in d.get('metrics', [])})
        fields = []
        declarations = [c for c in candidates.values() if c['kind'] == 'declared' and c['source_ref'] in refs]
        for declaration in declarations:
            context = declaration.get('source_context', {})
            table = context.get('table', {})
            for column in table.get('columns', []):
                fields.append({'source_ref': declaration['source_ref'], 'entity': context.get('entity'),
                               'column': column['name'], 'metadata': column,
                               'scope': 'captured_declared_role'})
        source = metric['definition']['source']
        frm = source.get('sql') or source.get('table') or source['entity']
        if not declarations and not frm.lstrip().startswith('('):
            tables = set(features('SELECT 1 FROM ' + frm)['tables'])
            for relation in catalog:
                if _ident(relation['table']) not in tables:
                    continue
                for column in relation['columns']:
                    fields.append({'source_ref': relation['source_ref'], 'entity': relation['table'],
                                   'column': column['column'], 'metadata': column,
                                   'scope': 'catalog_field_resolve_actual_sql_role'})
        for field in fields:
            key = [field['source_ref'], field['entity'], field['column']]
            field['field_id'] = 'field-' + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
        for field in fields:
            field_catalog[field['field_id']] = field
        cards.append({'name': metric['name'], 'definition_hash': definition_hash(metric),
                      'body': metric, 'supporting_source_refs': refs, 'source_fields': [f['field_id'] for f in fields],
                      'field_inventory_limit': 'Resolve SQL roles before using catalog fields. An undeclared derived projection requires separate source inspection.',
                      'prospective_identity': identity(metric)})
    # Invalidate connected reviews when a component or its consumer changes. All source
    # evidence is deliberately included, so uncertain source changes never reuse notes.
    bodies = {m['name']: m for m in metrics}
    neighbors = {name: set() for name in bodies}
    for name, body in bodies.items():
        definition = body['definition']
        related = definition.get('depends_on', []) + [
            r.get('metric') for r in definition.get('extensions', {}).get('related', [])
            if isinstance(r, dict)]
        for target in related:
            if isinstance(target, str) and target in bodies:
                neighbors[name].add(target)
                neighbors[target].add(name)
    for entry in cards:
        connected, pending = set(), [entry['name']]
        while pending:
            name = pending.pop()
            if name not in connected:
                connected.add(name)
                pending.extend(neighbors[name] - connected)
        evidence = {'version': 4, 'review_policy_hash': policy_hash, 'inventory': inventory['input_hash'],
                    'candidate_review': candidate_review, 'catalog': list(catalog),
                    'cards': [bodies[n] for n in sorted(connected)]}
        entry['review_hash'] = hashlib.sha256(json.dumps(
            evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    bundle = {'version': 4, 'review_policy_hash': policy_hash,
              'inventory': inventory, 'catalog': list(catalog),
              'field_catalog': field_catalog, 'cards': cards,
              'evidence_limits': ['SQL observations are lexical hints, not complete parsed facts.',
                                  'Column types alone do not establish keys, relationships or business meaning.',
                                  'Review annotations never certify source parity or authorize publication.']}
    bundle['input_hash'] = hashlib.sha256(json.dumps(bundle, sort_keys=True).encode()).hexdigest()
    return bundle


def review_fields(bundle, card):
    """Resolve lossless shared field records; accept already captured version-two packets."""
    if bundle.get('version', 2) < 3:
        return card['source_fields']
    return [bundle['field_catalog'][key] for key in card['source_fields']]


def reusable_card_notes(bundle, previous):
    """Reuse only source- and dependency-bound completed reviews, never SQL results."""
    current = {c['name']: c for c in bundle['cards']}
    return [note for note in previous.get('metrics', [])
            if note.get('name') in current and note.get('review_hash')
            and note['review_hash'] == current[note['name']].get('review_hash')]


def review_card_notes(bundle, review):
    """Validate current, source-traced review coverage without choosing the author's fields."""
    if review.get('input_hash') != bundle['input_hash']:
        raise ValueError('Cards or evidence changed. Read the current review bundle and review again.')
    expected = {c['name']: c for c in bundle['cards']}
    refs = {s['source_ref'] for s in bundle['inventory']['statements']}
    refs.update(c['source_ref'] for c in bundle['inventory']['candidates'])
    refs.update(c['source_ref'] for c in bundle['catalog'])
    seen = set()
    for note in review.get('metrics', []):
        name = note.get('name')
        if name not in expected or name in seen:
            raise ValueError('Review each current metric exactly once.')
        seen.add(name)
        if note.get('review_hash') not in (None, expected[name].get('review_hash')):
            raise ValueError('The card or its review dependencies changed. Review the current evidence.')
        if note.get('definition_hash') != expected[name]['definition_hash']:
            raise ValueError('Review must describe the current metric revision.')
        if not note.get('binding_reason') or not note.get('description_reason') or not note.get('grain_reason'):
            raise ValueError('Explain the source binding, description and grain from their evidence.')
        if note.get('grain_basis') not in ('declared_key', 'source_structure', 'verified_snapshot', 'unknown'):
            raise ValueError('Distinguish declared, structural, snapshot-verified and unknown grain.')
        source_refs = note.get('source_refs', [])
        if not source_refs or not all(isinstance(r, str) and r in refs for r in source_refs):
            raise ValueError('Cite current source references for every card review.')
        dimensions = expected[name]['body']['definition'].get('dimensions', [])
        names = {d['name'] for d in dimensions}
        included = set()
        for dimension in note.get('dimensions', []):
            key = dimension.get('name')
            if key not in names or key in included or not dimension.get('reason'):
                raise ValueError('Explain each actual dimension exactly once.')
            included.add(key)
        if included != names:
            raise ValueError('Review every dimension, not only the displayed examples.')
        expected_fields = {f['field_id'] for f in review_fields(bundle, expected[name])}
        seen_fields = set()
        for field in note.get('field_decisions', []):
            field_id = field.get('field_id')
            if field_id not in expected_fields or field_id in seen_fields or not field.get('reason'):
                raise ValueError('Account for each current source field exactly once with a reason.')
            seen_fields.add(field_id)
            decision = field.get('decision')
            targets = field.get('dimensions', [])
            if decision not in ('included', 'equivalent', 'excluded', 'unresolved'):
                raise ValueError('Classify source fields as included, equivalent, excluded or unresolved.')
            if not isinstance(targets, list) or not all(isinstance(t, str) and t in names for t in targets):
                raise ValueError('Source-field review must refer to actual dimension names.')
            if (decision in ('included', 'equivalent')) != bool(targets):
                raise ValueError('Included or equivalent fields need their dimensions; other fields need a reason without a dimension target.')
        if seen_fields != expected_fields:
            raise ValueError('Review all listed source fields, including fields on already joined relations.')
        for exclusion in note.get('excluded_breakdowns', []):
            if not exclusion.get('expression') or exclusion.get('source_ref') not in refs or not exclusion.get('reason'):
                raise ValueError('Trace excluded or unresolved breakdowns to their source and reason.')
        if not isinstance(note.get('limitations'), list):
            raise ValueError('Record review limitations explicitly, including an empty list when none remain.')
    if seen != set(expected):
        raise ValueError('Finish the review of all cards before the final handoff.')
    return {'status': 'reviewed', 'input_hash': bundle['input_hash'],
            'metrics': [{**note, **({'review_hash': expected[note['name']]['review_hash']}
                         if 'review_hash' in expected[note['name']] else {})} for note in review['metrics']],
            'coverage': 'assistant_source_review_not_certification'}


# ====================================================================== registry contracts
METRIC_FIELDS = set(registry_contract.schema('metric')['properties'])
RETIRED = {"status", "approved_by", "approved_at", "owner", "domain"}
AGG_TYPES = {"sum", "count", "count_distinct", "min", "max", "ratio", "custom"}
DATATYPES = {"integer", "decimal", "string", "boolean", "date", "timestamp"}
STRUCT_FIELDS = {
    "aggregation": ({"type"}, {"reaggregation", "null_handling", "zero_denominator"}),
    "output": ({"alias", "datatype"}, {"unit"}),
    "source": ({"entity", "alias"}, {"table", "sql", "timeframe_column", "parameters"}),
    "parameters": ({"name", "datatype", "default"}, set()),
    "joins": ({"name", "to", "sql", "cardinality", "origin"}, set()),
    "dimensions": ({"name", "kind", "sql", "datatype"}, {"filter_form", "values", "values_truncated", "value_map",
                                                         "required_join", "description"}),
    "examples": ({"source", "sql"}, {"question", "group_by", "filters", "parameters", "result"}),
    "result": ({"columns", "row_count", "hash", "executed_at", "rows"}, set()),
}
BODY_FIELDS = {"name", "display_name", "description", "search_text", "source_id", "source_type", "source_version",
               "definition", "owner_id"}
_DATE = re.compile(r"'\d{4}-\d{2}-\d{2}")
_PARAM = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_EMAIL = _SHARED_PII["email"]
_SQL_IN_PROSE = re.compile(r"\bselect\b[\s\S]+\bfrom\b|\b(?:count|sum)\s*\(", re.I)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


def _struct(path: str, obj, kind: str, errs: list) -> None:
    need, may = STRUCT_FIELDS[kind]
    if not isinstance(obj, dict):
        errs.append(f"{path} must be an object")
        return
    for k in need:
        if obj.get(k) in (None, "") and not (kind == "parameters" and k == "default" and k in obj):
            errs.append(f"{path}.{k} is required")
    for k in obj:
        if k not in need | may:
            errs.append(f"{path}.{k} is not a field the registry declares")


def outer_function(formula: str):
    """The aggregate that wraps the whole formula, with DISTINCT folded in, or None."""
    f = formula.strip()
    m = re.match(r"(\w+)\s*\(", f)
    if not m or _balanced(f, m.end() - 1) != len(f):
        return None
    fn = m.group(1).lower()
    if fn == "count" and re.match(r"count\s*\(\s*distinct\b", f, re.I):
        return "count_distinct"
    return fn


def _top_level_divide(formula: str) -> bool:
    depth = 0
    for kind, text in scan(formula):
        if kind != "code":
            continue
        for ch in text:
            depth += 1 if ch == "(" else -1 if ch == ")" else 0
            if ch == "/" and depth == 0:
                return True
    return False


def _sql_fields(d: dict) -> list:
    src = d.get("source") or {}
    out = [("formula", d.get("formula") or ""), ("source.sql", src.get("sql") or "")]
    out += [(f"filters[{i}]", f) for i, f in enumerate(d.get("filters") or [])]
    out += [(f"mandatory_filters[{i}]", f) for i, f in enumerate(d.get("mandatory_filters") or [])]
    return out


# A literal compared with a person's, customer's or record's ID, whatever its length; a UUID; a
# prefixed ID such as 'cus_8Hk2Lm9'. A heuristic, as the query-log filter is: the person still
# reviews the preview before anything is saved.
_ENTITY_ID = _SHARED_PII["entity_id"]
_UUID = _SHARED_PII["uuid"]
_PREFIXED_ID = _SHARED_PII["prefixed_id"]
# A column whose values are people: its sample values are refused whatever they look like.
_PERSON_COLUMN = re.compile(r"(?:^|_)(?:full_|first_|last_)?(?:name|email|phone)$", re.I)
_NOT_PERSON = re.compile(r"(?:product|item|sku|table|column|metric|region|country|city|state|stage|status|"
                         r"category|segment|plan|tier|team|channel|campaign|brand|store|event|file|host|"
                         r"domain|display|field|feature|model|schema|database|source)_?(?:full_)?name$", re.I)


def _pii(text: str):
    if _EMAIL.search(text or ""):
        return "an email address"
    name = full_name_hit(text)
    if name:
        return f"a person's name ({name})"
    if _ENTITY_ID.search(text or "") or _UUID.search(text or "") or _PREFIXED_ID.search(text or ""):
        return "an identifier literal: make it a parameter, or leave the row out"
    return None


def _pii_value(column: str, value) -> str | None:
    """A sample value under a named column: a dimension's values, an example's result cell."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value)
    if _EMAIL.search(text):
        return "an email address"
    if _UUID.search(text) or _PREFIXED_ID.search(f"'{text}'") or _ENTITY_ID.search(f"{column} = '{text}'"):
        return "an identifier value"
    if text.strip() and _PERSON_COLUMN.search(column or "") and not _NOT_PERSON.search(column or ""):
        return f"a value of the person column {column!r}"
    return None


def _pii_prose(text: str) -> str | None:
    if _EMAIL.search(text or ""):
        return "an email address"
    if _UUID.search(text or ""):
        return "an identifier literal"
    return None


def _privacy_fields(body: dict) -> list:
    """Every field of a metric card that is published, as (where, how, text): `sql` is checked as
    SQL, `value` as a sample under its column, `prose` for addresses and identifiers. The fields
    _sql_fields covers are checked there already."""
    d = body.get("definition") or {}
    out: list = []
    for k in ("display_name", "description", "search_text"):
        out.append((k, "prose", body.get(k) or ""))
    src = d.get("source") if isinstance(d.get("source"), dict) else {}
    for i, p in enumerate(src.get("parameters") or []):
        if isinstance(p, dict) and isinstance(p.get("default"), str):
            out.append((f"source.parameters[{i}].default", "sql", f"'{p['default']}'"))
    for i, j in enumerate(d.get("joins") or []):
        if isinstance(j, dict):
            out.append((f"joins[{i}].sql", "sql", j.get("sql") or ""))
    for i, dim in enumerate(d.get("dimensions") or []):
        if not isinstance(dim, dict):
            continue
        col = str(dim.get("name") or "")
        out.append((f"dimensions[{i}].sql", "sql", dim.get("sql") or ""))
        out.append((f"dimensions[{i}].description", "prose", dim.get("description") or ""))
        for v in dim.get("values") or []:
            out.append((f"dimensions[{i}].values", "value", (col, v)))
        vm = dim.get("value_map") if isinstance(dim.get("value_map"), dict) else {}
        for k, v in vm.items():
            out.append((f"dimensions[{i}].value_map", "value", (col, k)))
            out.append((f"dimensions[{i}].value_map", "value", (col, v)))
    for i, ex in enumerate(d.get("examples") or []):
        if not isinstance(ex, dict):
            continue
        out.append((f"examples[{i}].sql", "sql", ex.get("sql") or ""))
        out.append((f"examples[{i}].question", "prose", ex.get("question") or ""))
        for f in ex.get("filters") or []:
            out.append((f"examples[{i}].filters", "sql", f if isinstance(f, str) else json.dumps(f)))
        params = ex.get("parameters") if isinstance(ex.get("parameters"), dict) else {}
        for k, v in params.items():
            out.append((f"examples[{i}].parameters.{k}", "value", (k, v)))
        res = ex.get("result") if isinstance(ex.get("result"), dict) else {}
        cols = [c.get("name") if isinstance(c, dict) else c for c in res.get("columns") or []]
        for row in res.get("rows") or []:
            for c, v in zip(cols, row if isinstance(row, list) else []):
                out.append((f"examples[{i}].result", "value", (str(c or ""), v)))
    return out


def _privacy_errors(body: dict) -> list:
    errs, seen = [], set()
    for where, how, text in _privacy_fields(body):
        why = _pii(text) if how == "sql" else _pii_value(*text) if how == "value" else _pii_prose(text)
        if why and (where, why) not in seen:
            seen.add((where, why))
            errs.append(f"{where} holds {why}")
    return errs


# Statements that change data or grants. Read-only checks look for them anywhere in the code, not
# only at the start: `WITH d AS (DELETE ... RETURNING *) SELECT ...` begins with WITH.
# SELECT ... INTO makes a table in PostgreSQL and SQL Server.
_WRITES = re.compile(r"\b(insert|update|delete|merge|upsert|truncate|drop|create|alter|grant|revoke|copy|call|into)\b",
                     re.I)


def _writes(sql: str):
    """The first data- or grant-changing keyword in the SQL's code, outside strings, quoted names
    and comments, or None."""
    for kind, text in scan(sql or ""):
        if kind == "code":
            m = _WRITES.search(text)
            if m:
                return m.group(1).upper()
    return None


def check_metric(body: dict) -> tuple:
    errs, warns = [], []
    if not isinstance(body, dict):
        return ['artifact must be an object'], warns
    for field in ('name', 'display_name', 'description'):
        if field in body and body[field] is not None and not isinstance(body[field], str):
            errs.append(field + ' must be a string')
    errs.extend(registry_contract.definition_errors('metric', body.get('definition')))
    if errs:
        return errs, warns
    for k in body:
        if k not in BODY_FIELDS:
            errs.append(f"{k} is not an artifact field")
    for k in ("name", "display_name", "description", "definition"):
        if not body.get(k):
            errs.append(f"{k} is required")
    d = body.get("definition") or {}
    if not isinstance(d, dict):
        return ["definition must be an object"], warns
    for k in d:
        if k in RETIRED:
            errs.append(f"definition.{k} is retired and refused by the registry")
        elif k not in METRIC_FIELDS:
            errs.append(f"definition.{k} is not a field the registry declares")
    if d.get("spec_version") != METRIC_SPEC:
        errs.append(f"definition.spec_version must be {METRIC_SPEC}")
    if not isinstance(d.get("version"), int) or d.get("version", 0) <= 0:
        errs.append("definition.version must be an integer above 0")
    for k in ("dialect", "formula"):
        if not d.get(k):
            errs.append(f"definition.{k} is required")
    for k in ("aggregation", "output", "source"):
        if k in d:
            _struct(f"definition.{k}", d[k], k, errs)
        else:
            errs.append(f"definition.{k} is required")
    src = d.get("source") or {}
    if isinstance(src, dict):
        if bool(src.get("table")) == bool(src.get("sql")):
            errs.append("definition.source takes exactly one of table and sql")
        if re.match(r"\s*(select|with)\b", src.get("sql") or "", re.I):
            errs.append("definition.source.sql is the FROM clause, never a SELECT")
        for i, p in enumerate(src.get("parameters") or []):
            _struct(f"definition.source.parameters[{i}]", p, "parameters", errs)
    names = {}
    for kind in ("joins", "dimensions", "examples"):
        seen = set()
        for i, item in enumerate(d.get(kind) or []):
            _struct(f"definition.{kind}[{i}]", item, kind, errs)
            if kind != "examples" and isinstance(item, dict):
                n = item.get("name")
                if n in seen:
                    errs.append(f"definition.{kind}: {n!r} appears twice")
                seen.add(n)
            if kind == "examples" and isinstance(item, dict) and item.get("result"):
                _struct(f"definition.examples[{i}].result", item["result"], "result", errs)
                res = item["result"]
                if isinstance(res, dict) and any(len(r) != len(res.get("columns") or []) for r in res.get("rows") or []):
                    errs.append(f"definition.examples[{i}].result rows and columns differ in width")
        names[kind] = seen
    for i, dim in enumerate(d.get("dimensions") or []):
        rj = isinstance(dim, dict) and dim.get("required_join")
        if rj and rj not in names["joins"]:
            errs.append(f"definition.dimensions[{i}].required_join {rj!r} is not a declared join")
        if isinstance(dim, dict) and dim.get("datatype") not in DATATYPES | {"unknown"}:
            warns.append(f"definition.dimensions[{i}].datatype {dim.get('datatype')!r} is outside the vocabulary")
    for i, ex in enumerate(d.get("examples") or []):
        for g in (ex.get("group_by") or []) if isinstance(ex, dict) else []:
            if g not in names["dimensions"]:
                errs.append(f"definition.examples[{i}].group_by names {g!r}, which is not a declared dimension")
    agg = (d.get("aggregation") or {}).get("type") if isinstance(d.get("aggregation"), dict) else None
    formula = d.get("formula") or ""
    if agg == "avg":
        errs.append("aggregation.type has no avg: a mean is custom")
    elif agg and agg not in AGG_TYPES:
        warns.append(f"aggregation.type {agg!r} is outside the vocabulary and is stored with a permanent warning")
    fn = outer_function(formula)
    if agg and formula:
        if _top_level_divide(formula):
            if agg not in ("ratio", "custom"):
                errs.append(f"aggregation.type {agg!r} disagrees with a formula that divides: use ratio")
        elif fn in ("count", "count_distinct", "sum", "min", "max") and agg in AGG_TYPES and agg != fn and agg != "custom":
            errs.append(f"aggregation.type {agg!r} disagrees with the formula's {fn.upper()}")
    if agg == "ratio" and not (d.get("aggregation") or {}).get("zero_denominator"):
        warns.append("a ratio with no aggregation.zero_denominator is stored with a permanent warning")
    if not d.get("examples"):
        warns.append("no examples: the registry stores a permanent warning")
    out = d.get("output") or {}
    if isinstance(out, dict) and out.get("datatype") and out["datatype"] not in DATATYPES:
        warns.append(f"output.datatype {out['datatype']!r} is outside the vocabulary")
    if _SQL_IN_PROSE.search(body.get("description") or ""):
        errs.append("description holds SQL: describe the number in words")
    dims = {str(x.get("name", "")).lower() for x in d.get("dimensions") or [] if isinstance(x, dict)}
    m = re.search(r"\s(?:by|per)\s+(.+)$", body.get("display_name") or body.get("name") or "", re.I)
    if m:
        if m.group(1).strip().lower() in dims:
            errs.append(f"the name carries its own cut ({m.group(0).strip()!r}): name the measure, keep the cut a dimension")
        else:
            warns.append(f"the name reads like a cut ({m.group(0).strip()!r}); keep it only if that is the measure")
    declared = {p.get("name") for p in src.get("parameters") or [] if isinstance(p, dict)} if isinstance(src, dict) else set()
    used = set()
    for where, text in _sql_fields(d):
        used |= set(_PARAM.findall(text))
        if _DATE.search(text):
            errs.append(f"{where} holds a fixed date: make it a parameter with that date as its default")
        why = _pii(text)
        if why:
            errs.append(f"{where} holds {why}")
        if _writes(text):
            errs.append(f"{where} holds {_writes(text)}: a metric reads, it never changes data")
    errs += _privacy_errors(body)
    for p in sorted(used - declared):
        errs.append(f"{{{{{p}}}}} is used but not declared in source.parameters")
    for p in sorted(declared - used):
        errs.append(f"parameter {p!r} is declared but nothing uses it")
    ev = ((d.get("extensions") or {}).get("atlan_analytics") or {}) if isinstance(d.get("extensions"), dict) else {}
    ma = ((d.get("extensions") or {}).get("metrics_agent") or {}) if isinstance(d.get("extensions"), dict) else {}
    if not (ev.get("shapes") or ev.get("declared") or ev.get("by_hand") or ma.get("derived_from")):
        errs.append("no evidence: extensions.atlan_analytics needs shapes, declared or by_hand")
    return errs, warns


def check_query(body: dict) -> tuple:
    errs, warns = [], []
    if not isinstance(body, dict):
        return ['artifact must be an object'], warns
    for field in ('name', 'display_name', 'description'):
        if field in body and body[field] is not None and not isinstance(body[field], str):
            errs.append(field + ' must be a string')
    errs.extend(registry_contract.definition_errors('sql_query', body.get('definition')))
    if errs:
        return errs, warns
    for k in ("name", "display_name", "definition"):
        if not body.get(k):
            errs.append(f"{k} is required")
    if body.get("name") and not _SLUG.match(body["name"]):
        errs.append("name must be a lower-case slug")
    d = body.get("definition") or {}
    if not isinstance(d, dict):
        return errs + ['definition must be an object'], warns
    if d.get('extensions') is not None and not isinstance(d['extensions'], dict):
        errs.append('definition.extensions must be an object')
    if d.get("spec_version") != QUERY_SPEC:
        errs.append(f"definition.spec_version must be {QUERY_SPEC}")
    for k in ("sql", "question", "dialect"):
        if not d.get(k):
            errs.append(f"definition.{k} is required")
    sql = strip_comments(d.get("sql") or "")
    if sql and not re.match(r"(select|with)\b", sql, re.I):
        errs.append("definition.sql must be one SELECT or WITH statement")
    if ";" in _code_only(sql):
        errs.append("definition.sql holds more than one statement")
    if sql and _writes(sql):
        errs.append(f"definition.sql holds {_writes(sql)}: a saved query only reads")
    why = _pii(d.get("sql") or "")
    if why:
        errs.append(f"definition.sql holds {why}")
    for k in ("question", "description"):
        why = _pii_prose(d.get(k) or body.get(k) or "")
        if why:
            errs.append(f"{k} holds {why}")
    if d.get('review_status') == 'rejected':
        errs.append('The saved query was rejected in review; repair and review it before publication.')
    if d.get("review_status") not in (None, "unreviewed", "reviewed", "verified", "needs_review", "rejected"):
        warns.append(f"definition.review_status {d.get('review_status')!r} is unusual")
    return errs, warns


def _canon(text: str) -> str:
    """Compare tokens without merging names or changing quoted identifiers/literals."""
    parts = []
    for kind, text in scan(text or ""):
        if kind == "comment":
            continue
        if kind in ('str', 'dollar', 'ident'):
            parts.append((kind, text))
        else:
            parts.extend(('code', token.lower()) for token in re.findall(
                r'[\w$]+|<>|!=|<=|>=|::|\|\||[^\s]', text))
    if parts and parts[-1] == ('code', ';'):
        parts.pop()
    return json.dumps(parts, ensure_ascii=False)


def identity(body: dict) -> str:
    """What a metric measures: entity, FROM clause, formula and mandatory filters, literals
    included. Never its name."""
    d = body.get("definition") or {}
    src = d.get("source") or {}
    parts = [_canon(src.get("entity") or ""), _canon(src.get("sql") or src.get("table") or ""),
             _canon(d.get("formula") or ""),
             "|".join(sorted(_canon(f) for f in d.get("mandatory_filters") or []))]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def _same(a: dict, b: dict) -> bool:
    # The same contract rules preserve unknown constraints and producer bindings.
    return query_identity(a) == query_identity(b)


def definition_hash(body):
    return hashlib.sha256(json.dumps(body.get('definition'), sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def reconciliation_hash(body):
    """Bind a local review to identity, prose and the complete definition, never just SQL."""
    value = {key: body.get(key) for key in ('name', 'display_name', 'description', 'source_id', 'definition')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def query_identity(body: dict) -> str:
    """Exact executable contract, independent of producer scope and descriptive prose.

    Do not use the history shape normalizer: it removes literal values. SQL is kept
    byte-for-byte (apart from surrounding whitespace), including quoted identifiers.
    Unknown definition fields and extension namespaces remain part of the contract.
    Binding constraints nested in producer evidence also remain part of it.
    """
    definition = body.get('definition') or {}
    if not isinstance(definition, dict):
        return 'invalid-definition'
    contract = {k: v for k, v in definition.items() if k not in (
        'question', 'description', 'review_status', 'version', 'extensions')}
    contract['sql'] = str(contract.get('sql') or '').strip()
    extensions = definition.get('extensions') or {}
    if not isinstance(extensions, dict):
        return 'invalid-extensions'
    contract['extensions'] = {k: v for k, v in extensions.items()
                              if k not in ('metrics_agent', 'atlan_analytics')}
    constraints = []
    def bindings(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('viewer_binds', 'withheld_binds', 'parameters', 'bindings',
                           'mandatory_filters', 'required_filters') and child:
                    constraints.append((path + (key,), child))
                elif isinstance(child, (dict, list)):
                    bindings(child, path + (key,))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                bindings(child, path + (str(i),))
    bindings({k: extensions[k] for k in ('metrics_agent', 'atlan_analytics') if k in extensions})
    contract['producer_constraints'] = constraints
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def parameterized_candidate(candidate, live):
    """Find exact template coverage for review, never prove equivalence or authorize reuse."""
    a, b = candidate.get('definition', {}), live.get('definition', {})
    if a.get('dialect') != b.get('dialect') or a.get('parameters') or not b.get('parameters'):
        return False
    names, pieces = [], []
    for kind, text in scan(b.get('sql') or ''):
        if kind != 'code':
            pieces.append(re.escape(text))
            continue
        start = 0
        for marker in re.finditer(r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}', text):
            pieces.append(re.escape(text[start:marker.start()]))
            pieces.append(r"('(?:''|[^'])*'|[-+]?\d+(?:\.\d+)?|TRUE|FALSE|NULL)")
            names.append(marker.group(1))
            start = marker.end()
        pieces.append(re.escape(text[start:]))
    declared = {p.get('name') for p in b['parameters'] if isinstance(p, dict)}
    if not names or set(names) != declared:
        return False
    match = re.fullmatch(''.join(pieces), a.get('sql') or '')
    if not match:
        return False
    literals = {}
    for name, literal in zip(names, match.groups()):
        if name in literals and literals[name] != literal:
            return False
        literals[name] = literal
    return True


# The edges a layer may ask for, as (source kind, target kind). `metric` declares only
# depends_on and replaced_by, and the metric app writes both from the definition.
LINK_TYPES = {"depends_on": ("metric", "metric"), "source_of": ("sql_query", "metric"),
              "uses_metric": ("sql_query", "metric")}


def check_publication_holds(directory, metrics=None, queries=None, links=None):
    """Enforce engine hold evidence without adding a dependency to legacy authored contexts."""
    root = pathlib.Path(directory)
    if not (root / "metrics-source.json").exists() and not (root / "publication-holds.json").exists():
        return None
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
    try:
        from modules.metrics.holds import enforce_context
        return enforce_context(root, metrics, queries, links)
    except (ValueError, OSError, ImportError) as exc:
        raise Blocked(str(exc), "Repair the reviewed engine outputs and export into a new context; do not edit hold records.") from None


def cmd_check(a) -> dict:
    """Check every authored body, decide create, revise or reuse against the workspace, write plan.json."""
    d = pathlib.Path(a.dir)
    metrics = load_json(d / "metrics.json", []) or []
    queries = load_json(d / "queries.json", []) or []
    links = load_json(d / "links.json", []) or []
    check_publication_holds(d, metrics, queries, links)
    existing = load_json(d / "existing.json", []) or []
    local_checks = load_json(d / "candidate-checks.json", {}) or {}
    from reconciliation import Review
    review = Review(d, load_json, dump_json, reconciliation_hash, definition_hash)
    def held_execution(kind, body):
        record = local_checks.get(kind + ':' + str(body.get('name')), {})
        revision = hashlib.sha256(json.dumps(body.get('definition'), sort_keys=True, ensure_ascii=False,
                                            allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        if record.get('definition_hash') == revision and any(
                sample.get('status') == 'failed' for sample in record.get('samples', {}).values()):
            return ['A current parameter sample failed execution; correct and recheck it before publishing.']
        return []
    shapes = {s["shape_id"] for s in read_jsonl(d / "shapes.jsonl")}
    if not metrics and not queries:
        raise Blocked("Nothing to check: metrics.json and queries.json are both empty or missing.",
                      "Author the metrics and saved queries first, or import a finished run.")
    live_m = [e for e in existing if e.get("kind", "metric") == "metric"]
    live_q = [e for e in existing if e.get("kind") == "sql_query"]
    by_sid = {}
    for row in live_m:
        if row.get('source_id'):
            by_sid.setdefault(row['source_id'], []).append(row)
    by_ident: dict = {}
    for e in live_m:
        by_ident.setdefault(identity(e), []).append(e)
    live_names = {(e.get("kind", "metric"), e.get("name")): e for e in existing}

    plan, refused, warnings = {"metrics": [], "queries": [], "links": []}, [], []
    seen_names, seen_display, seen_ident = {}, {}, {}
    adopted = {}
    metric_matches = []
    for i, body in enumerate(metrics):
        label = body.get("name") or f"metrics[{i}]"
        errs, warns = check_metric(body)
        errs += held_execution('metric', body)
        ident = identity(body)
        if not body.get("source_id"):
            body["source_id"] = f"metric:{ident}"
        for key, store, what in ((body.get("name"), seen_names, "name"),
                                 (body.get("display_name"), seen_display, "display_name"),
                                 (ident, seen_ident, "what it measures")):
            if key in store:
                errs.append(f"same {what} as {store[key]!r}: one metric, or two names that say how they differ")
            store[key] = label
        shp = (((body.get("definition") or {}).get("extensions") or {}).get("atlan_analytics") or {}).get("shapes") or []
        missing = [s for s in shp if shapes and s not in shapes]
        if missing:
            errs.append(f"cites shapes that are not in shapes.jsonl: {', '.join(missing[:5])}")
        action, match = "create", None
        decision, selected, error = review.decision('metric', body, live_m)
        if error:
            errs.append(error)
        sid_matches = by_sid.get(body['source_id'], [])
        if len(sid_matches) > 1 and not decision:
            errs.append('multiple live metrics have this source identity; resolve the ambiguity before publishing')
        hit = sid_matches[0] if len(sid_matches) == 1 else None
        from_sid = hit is not None
        if decision:
            hit = selected if decision['action'] != 'create' else None
        if hit is None and len(by_ident.get(ident, [])) == 1:
            if not decision:
                hit = by_ident[ident][0]
        elif hit is None and len(by_ident.get(ident, [])) > 1:
            if not decision:
                errs.append('multiple live metrics match this calculation; compare their definitions before selecting one')
                review.request('metric', body, by_ident[ident])
        if hit is None and not decision:
            names = {str(body.get(k) or '').casefold().replace('_', ' ').replace('-', ' ')
                     for k in ('name', 'display_name')} - {''}
            possible = [row for row in live_m if any(
                str(row.get(k) or '').casefold().replace('_', ' ').replace('-', ' ') in names
                for k in ('name', 'display_name'))]
            if possible:
                review.request('metric', body, possible)
                errs.append('An existing metric may already answer this question; compare meaning and coverage before creating another.')
        if hit is not None:
            match = hit.get("id")
            if not match:
                errs.append('Read the complete existing metric with its artifact id before reconciling.')
            live_errors, _ = check_metric({k: v for k, v in hit.items() if k in BODY_FIELDS})
            if live_errors:
                errs.append('The existing metric fails local validation; review its definition before adopting it.')
            if not decision and review.stale_adoption('metric', body, hit):
                review.request('metric', body, [hit])
                errs.append('The reused metric changed remotely; review the new definition before continuing.')
            action = decision['action'] if decision else "reuse" if _same(body, hit) else "revise"
            if identity(hit) != ident:
                warns.append(f"changes what live {hit.get('name')!r} measures (its source, formula or mandatory "
                             "filters): show the person both definitions before it is revised")
            if from_sid and not decision and hit.get("name") != body.get("name"):
                errs.append(f"revises {hit.get('name')!r} under a new name: a name is frozen at create")
        elif ("metric", body.get("name")) in live_names:
            errs.append("a different live metric already holds this name")
        plan["metrics"].append({"name": body.get("name"), "action": action, "match_id": match,
                                "source_id": body["source_id"], "ok": not errs})
        metric_matches.append((body, hit, label, errs, warns, decision, from_sid))
    # Resolve dependency names for comparison without changing candidate definitions first.
    proposed_names = {label: hit['name'] for _, hit, label, errs, _, _, _ in metric_matches if hit and not errs}
    for (body, hit, label, errs, warns, decision, from_sid), item in zip(metric_matches, plan['metrics']):
        original = copy.deepcopy(body)
        compared = copy.deepcopy(body)
        if isinstance(compared['definition'].get('depends_on'), list):
            compared['definition']['depends_on'] = [proposed_names.get(n, n) for n in compared['definition']['depends_on']]
        if hit and not decision and not from_sid and not _same(compared, hit):
            review.request('metric', body, [hit])
            errs.append('The calculation matches but definition coverage differs; review reuse or an intentional revision.')
        if hit:
            item['action'] = decision['action'] if decision else 'reuse' if _same(compared, hit) else 'revise'
        if hit and not errs:
            if item['action'] == 'reuse':
                review.adopt('metric', body, hit, local_checks, decision, original)
            else:
                body['name'] = hit['name']
                body['display_name'] = hit.get('display_name') or body['display_name']
                body['source_id'] = hit.get('source_id') or body['source_id']
                current_version = hit.get('definition', {}).get('version')
                if type(current_version) is not int or current_version < 1:
                    errs.append('Read the current positive metric version before preparing a revision.')
                else:
                    body['definition']['version'] = current_version + 1
            adopted[('metric', label)] = body['name']
        item.update(name=body['name'], source_id=body['source_id'], ok=not errs)
        refused += [{'kind': 'metric', 'item': label, 'error': e} for e in errs]
        warnings += [{'kind': 'metric', 'item': label, 'warning': w} for w in warns]
    q_names = set()
    q_by_sid = {}
    for row in live_q:
        if row.get('source_id'):
            q_by_sid.setdefault(row['source_id'], []).append(row)
    q_by_contract = {}
    for row in live_q:
        if isinstance(row.get('definition'), dict) and isinstance(row['definition'].get('sql'), str):
            q_by_contract.setdefault(query_identity(row), []).append(row)
    for i, body in enumerate(queries):
        label = body.get("name") or f"queries[{i}]"
        errs, warns = check_query(body)
        errs += held_execution('sql_query', body)
        if not body.get("source_id"):
            errs.append("source_id is required: <slug>/<shape or vq id>")
        if body.get("name") in q_names:
            errs.append("name appears twice")
        q_names.add(body.get("name"))
        original = copy.deepcopy(body)
        decision, selected, error = review.decision('sql_query', body, live_q)
        if error:
            errs.append(error)
        sid_matches = q_by_sid.get(body.get('source_id'), [])
        if len(sid_matches) > 1 and not decision:
            errs.append('multiple live saved queries have this source identity; resolve the ambiguity before publishing')
        hit = sid_matches[0] if len(sid_matches) == 1 else None
        if decision:
            hit = selected if decision['action'] != 'create' else None
        equivalent = q_by_contract.get(query_identity(body), [])
        if hit is None and not decision and len(equivalent) == 1:
            hit = equivalent[0]
            warns.append('same SQL and binding contract as live ' + repr(hit['name']) + ': reuse without replacing its documentation')
        elif hit is None and not decision and len(equivalent) > 1:
            review.request('sql_query', body, equivalent)
            errs.append('multiple live saved queries have this execution contract: choose the existing artifact before publishing')
        action = ("reuse" if hit and query_identity(body) == query_identity(hit) else "revise") if hit else "create"
        if decision:
            action = decision['action']
        if hit and not hit.get('id'):
            errs.append('Read the complete existing query with its artifact id before reconciling.')
        if hit and check_query(hit)[0]:
            errs.append('The existing query fails local validation; review its definition before adopting it.')
        if hit and not decision and review.stale_adoption('sql_query', body, hit):
            review.request('sql_query', body, [hit])
            errs.append('The reused query changed remotely; review the new definition before continuing.')
        if hit and hit.get('definition', {}).get('review_status') == 'rejected':
            errs.append('The matching live query was rejected in review; do not reuse or replace it automatically.')
        if not hit and ("sql_query", body.get("name")) in live_names:
            errs.append("a different live saved query already holds this name")
        if not hit and not decision:
            possible = [row for row in live_q if parameterized_candidate(body, row)]
            if possible:
                review.request('sql_query', body, possible)
                errs.append('An existing parameterized query may cover this statement; review its bindings and constraints before creating another.')
        if hit and not errs:
            if action == 'reuse':
                review.adopt('sql_query', body, hit, local_checks, decision, original)
            else:
                body['source_id'] = hit.get('source_id') or body['source_id']
                body['name'] = hit['name']
                body['display_name'] = hit.get('display_name') or body['display_name']
            adopted[('sql_query', label)] = body['name']
        for e in errs:
            refused.append({"kind": "sql_query", "item": label, "error": e})
        warnings += [{"kind": "sql_query", "item": label, "warning": w} for w in warns]
        plan["queries"].append({"name": body.get("name"), "action": action, "match_id": hit.get("id") if hit else None,
                                "source_id": body.get("source_id"), "ok": not errs})
    # Adopt identities as a graph operation, not just a change to the metric body.
    # Kind is part of every key: a query and a metric may share a name.
    for body, item in zip(metrics, plan['metrics']):
        definition = body.get('definition') or {}
        if item['action'] != 'reuse' and isinstance(definition.get('depends_on'), list):
            definition['depends_on'] = [adopted.get(('metric', name), name)
                                        for name in definition['depends_on']]
    for link in links:
        for end in ('from', 'to'):
            endpoint = link.get(end) or {}
            key = (endpoint.get('kind'), endpoint.get('name'))
            if key in adopted:
                endpoint['name'] = adopted[key]
    for kind, items in (('metric', plan['metrics']), ('sql_query', plan['queries'])):
        targets = {}
        for item in items:
            targets.setdefault(item['name'], []).append(item)
        for name, matches in targets.items():
            if len(matches) > 1:
                for item in matches:
                    item['ok'] = False
                refused.append({'kind': kind, 'item': name,
                                'error': 'multiple candidates resolve to this identity; combine their evidence before publishing'})
    m_names = {m.get("name") for m in metrics} | {e.get("name") for e in live_m}
    all_q = {q.get('name') for q in queries} | {e.get("name") for e in live_q}
    refused_ends = {(kind, item['name']) for kind, items in
                    (('metric', plan['metrics']), ('sql_query', plan['queries']))
                    for item in items if not item['ok']}
    pairs = set()
    for i, ln in enumerate(links):
        frm, to, rel = ln.get("from") or {}, ln.get("to") or {}, ln.get("rel_type")
        errs = []
        for end, who in (("from", frm), ("to", to)):
            pool = m_names if who.get("kind") == "metric" else all_q if who.get("kind") == "sql_query" else None
            if pool is None or who.get("name") not in pool:
                errs.append(f"{end} {who.get('kind')}:{who.get('name')} is not in this layer or the workspace")
            elif (who.get('kind'), who.get('name')) in refused_ends:
                errs.append(f"{end} endpoint is refused in this plan; resolve it before writing the relationship")
        if frm == to:
            errs.append("a link from a metric to itself")
        if rel == "alternative":
            errs.append("metric declares no alternative edge: record a grain variant or alternative on both "
                        "metrics' definition.extensions.related instead")
        elif rel not in LINK_TYPES:
            errs.append(f"rel_type {rel!r} is not one the metric and sql_query kinds declare")
        elif LINK_TYPES[rel] != (frm.get("kind"), to.get("kind")):
            errs.append(f"{rel} runs from a {LINK_TYPES[rel][0]} to a {LINK_TYPES[rel][1]}")
        elif rel == "depends_on":
            # The metric app writes this edge from the definition, so the definition must name it.
            src = next((m for m in metrics if m.get("name") == frm.get("name")), None)
            named = {str(n).strip() for n in ((src or {}).get("definition") or {}).get("depends_on") or []}
            if src is None:
                errs.append("depends_on from a metric outside this layer needs a revision of that metric")
            elif to.get("name") not in named:
                errs.append(f"add {to.get('name')!r} to {frm.get('name')!r}'s definition.depends_on: "
                            "the metric app writes depends_on from the definition")
        key = (frm.get("name"), to.get("name"), rel)
        if key in pairs:
            errs.append("asserted twice")
        pairs.add(key)
        for e in errs:
            refused.append({"kind": "link", "item": f"links[{i}]", "error": e})
        plan["links"].append({**ln, "ok": not errs})
    check_publication_holds(d, metrics, queries, links)
    review.save()
    if (d / 'candidate-checks.json').exists():
        dump_json(d / 'candidate-checks.json', local_checks)
    dump_json(d / "metrics.json", metrics)
    dump_json(d / "queries.json", queries)
    dump_json(d / "links.json", links)
    counts = {k: {a_: sum(1 for x in plan[k] if x.get("action") == a_ and x["ok"]) for a_ in ("create", "revise", "reuse")}
              for k in ("metrics", "queries")}
    counts["links"] = sum(1 for x in plan["links"] if x["ok"])
    plan.update({"counts": counts, "refused": refused, "warnings": warnings,
                 'coverage': review.coverage(metrics, queries)})
    dump_json(d / "plan.json", plan)
    return {"status": "ok" if not refused else "REFUSED", "counts": counts, "refused": len(refused),
            "warnings": len(warnings), "first_refusals": refused[:10], "plan": str(d / "plan.json")}


# ====================================================================== a finished metrics-agent run
def _chunks(doc) -> list:
    if isinstance(doc, list):
        return doc
    out = []
    for c in (doc or {}).get("chunks") or []:
        out += c
    return out


def cmd_import_run(a) -> dict:
    """Read a finished metrics-agent run's registry payloads into metrics.json, queries.json and links.json."""
    run, d = pathlib.Path(a.run), pathlib.Path(a.dir)
    pay = run / "registry_payloads"
    mp, qp = pay / "registry_metrics_v2.json", pay / "registry_sql_queries.json"
    if not mp.exists() and not qp.exists():
        raise Blocked(f"{run} has no registry_payloads/: the run did not reach its publish dry run.",
                      "Finish the run with metrics-agent (or start from query history here instead).")
    metrics = []
    for b in _chunks(load_json(mp, {})):
        b = {k: v for k, v in b.items() if k in BODY_FIELDS}
        metrics.append(b)
    queries = [{k: v for k, v in b.items() if k in BODY_FIELDS} for b in _chunks(load_json(qp, {}))]
    cards = load_json(run / "metric_cards.json", {}) or {}
    ref_to_name = {c.get("metric_ref"): c.get("name") for c in cards.get("metrics") or []}
    vq_to_query = {((b.get("definition") or {}).get("extensions") or {}).get("metrics_agent", {}).get("vq_id"): b.get("name")
                   for b in queries}
    links = []
    for vq, refs in (cards.get("metric_refs_by_vq") or {}).items():
        for ref in refs:
            if vq_to_query.get(vq) and ref_to_name.get(ref):
                links.append({"from": {"kind": "sql_query", "name": vq_to_query[vq]},
                              "to": {"kind": "metric", "name": ref_to_name[ref]}, "rel_type": "source_of",
                              "attributes": {"origin": "metrics-agent"}})
    for e in (load_json(run / "metric_relationships.json", {}) or {}).get("edges") or []:
        # Grain variants and alternatives are no edge: x4 writes them to definition.extensions.related.
        if (e.get("rel_type") or "depends_on") != "depends_on" or e.get("relation") in ("grain_variant", "alternative"):
            continue
        links.append({"from": {"kind": "metric", "name": e.get("from")}, "to": {"kind": "metric", "name": e.get("to")},
                      "rel_type": "depends_on",
                      "attributes": {k: v for k, v in {**(e.get("attributes") or {}), "relation": e.get("relation"),
                                                       "confidence": e.get("confidence")}.items() if v is not None}})
    dump_json(d / "metrics.json", metrics)
    dump_json(d / "queries.json", queries)
    dump_json(d / "links.json", links)
    return {"status": "ok", "metrics": len(metrics), "queries": len(queries), "links": len(links),
            "dialect": (load_json(mp, {}) or {}).get("dialect"), "dir": str(d)}


# ====================================================================== selftest
def selftest() -> dict:
    import tempfile
    checks, failed = [], []

    def ok(name, cond):
        checks.append(name)
        if not cond:
            failed.append(name)

    sql = "-- weekly: 'x'\nSELECT region, SUM(o.amount) AS rev /* gross */ FROM db.s.orders o WHERE o.status = 'paid' -- note\n AND o.dt >= '2026-01-01' GROUP BY region"
    ok("comments go, strings stay", strip_comments(sql).count("'paid'") == 1 and "gross" not in strip_comments(sql)
       and "note" not in strip_comments(sql))
    ok("a -- inside a string is text", "--" in strip_comments("select '--x' as a from t"))
    ok("literals normalize away", normalize(sql) == normalize(sql.replace("'paid'", "'open'").replace("2026", "2025")))
    ok("population values are kept apart from windows",
       predicate_literals(sql) == ["paid"] and predicate_literals("select 1 from t where s in ('a', 'b') and n = '42'")
       == ["a", "b"])
    ok("in-lists collapse", normalize("select 1 from t where a in (1,2,3)") == normalize("select 1 from t where a in (4)"))
    f = features(strip_comments(sql))
    ok("features: table", f["tables"] == ["db.s.orders"])
    ok("features: aggregate", f["aggregates"] == ["SUM(o.amount)"])
    ok("aggregates keep their place after a literal", features(
        "select sum(a) from t where b = 'long literal text' having sum(c) > 1")["aggregates"] == ["sum(a)", "sum(c)"])
    ok("features: group by", f["group_by"] == ["region"])
    ok("features: filters", len(f["filters"]) == 2)
    ok("ctes are not tables", features("with x as (select count(*) c from a.b) select c from x")["tables"] == ["a.b"])
    ok("outer function", outer_function("COUNT(DISTINCT t.id)") == "count_distinct" and outer_function("SUM(a)/SUM(b)") is None)
    ok("top-level divide", _top_level_divide("SUM(a) / NULLIF(SUM(b), 0)") and not _top_level_divide("SUM(a/b)"))

    good = {"name": "paid_revenue", "display_name": "Paid Revenue", "description": "Revenue from paid orders.",
            "definition": {"spec_version": METRIC_SPEC, "version": 1, "dialect": "snowflake",
                           "formula": "SUM(DB.S.ORDERS.AMOUNT)", "aggregation": {"type": "sum"},
                           "output": {"alias": "paid_revenue", "datatype": "decimal"},
                           "source": {"entity": "DB.S.ORDERS", "alias": "orders", "table": "DB.S.ORDERS"},
                           "mandatory_filters": ["DB.S.ORDERS.STATUS = 'paid'"],
                           "dimensions": [{"name": "Region", "kind": "categorical", "sql": "DB.S.ORDERS.REGION",
                                           "datatype": "string"}],
                           "examples": [{"source": "shape:s-1", "sql": "select 1", "group_by": ["Region"]}],
                           "extensions": {"atlan_analytics": {"shapes": ["s-1"]}}}}
    e, _ = check_metric(good)
    ok("a good card passes", not e)
    ok("a business value of two capitalised words is not a person's name",
       _pii("where stage = 'Closed Won'") is None and _pii("where rep = 'Jane Smith'"))
    bad = json.loads(json.dumps(good))
    bad["display_name"] = "Paid Revenue by Region"
    bad["definition"].update({"aggregation": {"type": "count"}, "owner": "x", "filters": ["DB.S.ORDERS.DT >= '2026-01-01'"]})
    bad["definition"]["extensions"] = {}
    e, _ = check_metric(bad)
    ok("a bad card is refused: retired", any("retired" in x for x in e))
    # Typed admission precedes semantic inspection. Check semantics on a typed body.
    bad["definition"].pop("owner")
    e, _ = check_metric(bad)
    for frag in ("disagrees", "its own cut", "fixed date", "no evidence"):
        ok(f"a bad card is refused: {frag}", any(frag in x for x in e))
    ok("identity ignores the name", identity(good) == identity({**good, "name": "other"}))
    def variant(**changes):
        body = json.loads(json.dumps(good))
        body["definition"].update(changes)
        return body
    for what, a_def, b_def in (
            ("paid vs refunded", {}, {"mandatory_filters": ["DB.S.ORDERS.STATUS = 'refunded'"]}),
            ("included vs excluded", {}, {"mandatory_filters": ["DB.S.ORDERS.STATUS <> 'paid'"]}),
            ("thresholds", {"formula": "SUM(CASE WHEN AMOUNT > 100 THEN AMOUNT END)"},
             {"formula": "SUM(CASE WHEN AMOUNT > 1000 THEN AMOUNT END)"}),
            ("currency", {"mandatory_filters": ["DB.S.ORDERS.CURRENCY = 'USD'"]},
             {"mandatory_filters": ["DB.S.ORDERS.CURRENCY = 'EUR'"]}),
            ("conditional formula", {"formula": "SUM(IFF(CHANNEL = 'web', AMOUNT, 0))"},
             {"formula": "SUM(IFF(CHANNEL = 'store', AMOUNT, 0))"})):
        ok(f"identity keeps literals: {what}", identity(variant(**a_def)) != identity(variant(**b_def)))
    ok("identity ignores spacing, case and comments",
       identity(variant(formula="sum( db.s.orders.amount ) -- gross")) == identity(good))
    for text in ("with d as (delete from orders returning *) select count(*) from d",
                 "with x as (select 1) insert into t select * from x",
                 "select * into backup from orders"):
        q = {"name": "q", "display_name": "Q", "definition": {"spec_version": QUERY_SPEC, "question": "x",
                                                              "dialect": "postgres", "sql": text}}
        ok(f"a writing statement is refused: {text[:24]}", any("only reads" in x for x in check_query(q)[0]))
    q = {"name": "q", "display_name": "Q", "definition": {"spec_version": QUERY_SPEC, "question": "x", "dialect": "snowflake",
         "sql": "select count(*) as updates, 'delete' as label from t where \"update\" > 0 -- insert"}}
    ok("write words in names, strings and comments are not writes", not check_query(q)[0])
    for text in ("select * from t where customer_id = 'cus_8Hk2Lm9'", "select * from t where user_id = 42",
                 "select * from t where ref = 'ord_ab12cd'",
                 "select * from t where k = '3f2b8c1e-9a4d-4e7b-8c2a-1d5e6f7a8b9c'"):
        ok(f"identifier literal refused: {text[22:]}", _pii(text) is not None)
    def leaks(**changes) -> list:
        body = json.loads(json.dumps(good))
        body["definition"].update(changes.pop("definition", {}))
        body.update(changes)
        return [x for x in check_metric(body)[0] if " holds " in x]
    ok("an email in an example's SQL is refused",
       leaks(definition={"examples": [{"source": "shape:s-1", "sql": "select 1 from o where e = 'ann@example.com'"}]}))
    ok("an email in an example's result is refused",
       leaks(definition={"examples": [{"source": "shape:s-1", "sql": "select 1",
                                       "result": {"columns": ["contact"], "rows": [["ann@example.com"]],
                                                  "row_count": 1, "hash": "h", "executed_at": "t"}}]}))
    ok("a customer's name under a person column is refused",
       leaks(definition={"examples": [{"source": "shape:s-1", "sql": "select 1",
                                       "result": {"columns": ["customer_name", "revenue"], "rows": [["Ann Lee", "3"]],
                                                  "row_count": 1, "hash": "h", "executed_at": "t"}}]}))
    ok("an id in a join, a dimension value, a default or the description is refused", all((
        leaks(definition={"joins": [{"name": "c", "to": "C", "cardinality": "many_to_one", "origin": "declared",
                                     "sql": "o.c = c.id and c.id = '3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90'"}]}),
        leaks(definition={"dimensions": [{"name": "Account", "kind": "categorical", "sql": "o.account_id",
                                          "datatype": "string", "values": ["cus_8Hk2Lm9"]}]}),
        leaks(definition={"source": {**good["definition"]["source"],
                                     "parameters": [{"name": "who", "datatype": "string", "default": "ann@example.com"}]}}),
        leaks(description="Revenue for ann@example.com."))))
    ok("business values in dimensions and results pass",
       not leaks(definition={"dimensions": [{"name": "Region", "kind": "categorical", "sql": "DB.S.ORDERS.REGION",
                                             "datatype": "string", "values": ["North America", "United States"]}],
                             "examples": [{"source": "shape:s-1", "sql": "select 1", "group_by": ["Region"],
                                           "result": {"columns": ["region_name", "rev"], "rows": [["North America", "3"]],
                                                      "row_count": 1, "hash": "h", "executed_at": "t"}}]}))
    ok("an allowed value does not hide a name after it",
       _pii("where stage = 'Closed Won' and rep = 'Jane Smith'") is not None)
    ok("joins on ids are not literals",
       _pii("select 1 from o join c on o.customer_id = c.customer_id where o.status = 'paid'") is None)

    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        (t / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
            {"sql": sql, "exec": 5, "users": 2},
            {"sql": sql.replace("2026", "2025"), "exec": 3, "users": 3},
            {"sql": sql.replace("'paid'", "'refunded'"), "exec": 4, "users": 1},
            {"sql": "select * from other.t", "exec": 9, "users": 1}]))
        (t / "tables.txt").write_text("DB.S.ORDERS\n")
        r = cmd_shapes(argparse.Namespace(src=str(t / "log.jsonl"), dst=str(t / "run" / "shapes.jsonl"),
                                          tables=str(t / "tables.txt"), top=50))
        rows = read_jsonl(t / "run" / "shapes.jsonl")
        ok("shapes: two windows, one shape, scope applied", r["distinct_shapes"] == 3 and r["written"] == 2
           and rows[0]["exec"] == 8 and rows[0]["users"] == 3 and rows[0]["texts"] == 2)
        ok("shapes: paid and refunded stay two shapes of one family", r["families"] == 2
           and rows[1]["predicates"] == ["refunded"] and rows[1]["exec"] == 4
           and rows[0]["family"] == rows[1]["family"] and rows[0]["shape_id"] != rows[1]["shape_id"])
        good["definition"]["extensions"]["atlan_analytics"]["shapes"] = [rows[0]["shape_id"]]
        dump_json(t / "run" / "metrics.json", [good])
        dump_json(t / "run" / "existing.json", [{"id": "metric_1", "kind": "metric", "name": "paid_rev",
                                                  "display_name": good['display_name'], "description": good['description'],
                                                  "source_id": "x", "definition": good["definition"]}])
        r = cmd_check(argparse.Namespace(dir=str(t / "run")))
        plan = load_json(t / "run" / "plan.json")
        ok("check adopts a live metric that measures the same", r["status"] == "ok"
           and plan["metrics"][0]["action"] == "reuse" and plan["metrics"][0]["name"] == "paid_rev")
        refunded = variant(mandatory_filters=["DB.S.ORDERS.STATUS = 'refunded'"])
        refunded.update({"name": "refunded_revenue", "display_name": "Refunded Revenue",
                         "description": "Revenue from refunded orders."})
        refunded["definition"]["output"]["alias"] = "refunded_revenue"
        refunded.pop("source_id", None)
        dump_json(t / "run" / "metrics.json", [refunded])
        r = cmd_check(argparse.Namespace(dir=str(t / "run")))
        plan = load_json(t / "run" / "plan.json")
        ok("a different population is a new metric, never a revision", plan["metrics"][0]["action"] == "create"
           and plan["metrics"][0]["match_id"] is None and plan["metrics"][0]["name"] == "refunded_revenue")
        moved = json.loads(json.dumps(refunded))
        moved.update({"name": "paid_rev", "source_id": "x"})
        dump_json(t / "run" / "metrics.json", [moved])
        cmd_check(argparse.Namespace(dir=str(t / "run")))
        ok("revising a live metric's population is called out", any(
            "changes what live 'paid_rev' measures" in w["warning"] for w in load_json(t / "run" / "plan.json")["warnings"]))
        base = json.loads(json.dumps(refunded))
        child = json.loads(json.dumps(refunded))
        child.update({"name": "refunded_share", "display_name": "Refunded Share",
                      "description": "Share of revenue refunded."})
        child["definition"]["output"]["alias"] = "refunded_share"
        child["definition"]["formula"] = "SUM(DB.S.ORDERS.AMOUNT) / COUNT(*)"
        child["definition"]["aggregation"] = {"type": "custom"}
        dump_json(t / "run" / "metrics.json", [base, child])
        link = {"from": {"kind": "metric", "name": "refunded_share"}, "to": {"kind": "metric", "name": "refunded_revenue"},
                "attributes": {"why": "numerator"}}
        dump_json(t / "run" / "links.json", [{**link, "rel_type": "alternative"}, {**link, "rel_type": "depends_on"}])
        cmd_check(argparse.Namespace(dir=str(t / "run")))
        errors = [x["error"] for x in load_json(t / "run" / "plan.json")["refused"] if x["kind"] == "link"]
        ok("links: alternative is refused toward extensions.related", any("extensions.related" in e for e in errors))
        ok("links: depends_on the definition does not name is refused", any("definition.depends_on" in e for e in errors))
        child["definition"]["depends_on"] = ["refunded_revenue"]
        dump_json(t / "run" / "metrics.json", [base, child])
        dump_json(t / "run" / "links.json", [{**link, "rel_type": "depends_on"},
                                             {"from": {"kind": "metric", "name": "refunded_revenue"},
                                              "to": {"kind": "metric", "name": "refunded_share"}, "rel_type": "uses_metric",
                                              "attributes": {"why": "x"}}])
        cmd_check(argparse.Namespace(dir=str(t / "run")))
        plan = load_json(t / "run" / "plan.json")
        ok("links: a depends_on the definition names passes; uses_metric runs from a saved query",
           [x["ok"] for x in plan["links"]] == [True, False])
        dump_json(t / "run" / "links.json", [])
    with tempfile.TemporaryDirectory() as d:
        t = pathlib.Path(d)
        (t / "tool.json").write_text(json.dumps({"isError": False, "content": [{"type": "text", "text": json.dumps(
            {"columns": ["SQL", "EXEC", "USERS"], "rows": [["select 1 from t", 3, 2], ["select 2 from t", 1, 1]]})}]}))
        (t / "dbx.json").write_text(json.dumps({"manifest": {"schema": {"columns": [{"name": "sql"}, {"name": "exec"},
                                     {"name": "users"}]}}, "result": {"data_array": [["select 1 from t", "2", "1"]]}}))
        (t / "api.json").write_text(json.dumps([
            {"query_text": "select 3 from t", "user_name": "ann@example.com", "status": "FINISHED", "statement_type": "SELECT"},
            {"query_text": "select 3 from t", "user_name": "bob@example.com", "status": "FINISHED", "statement_type": "SELECT"},
            {"query_text": "delete from t", "user_name": "eve", "status": "FINISHED", "statement_type": "DELETE"},
            {"query_text": "select 4 from t", "user_name": "eve", "status": "FAILED", "statement_type": "SELECT"}]))
        (t / "export.csv").write_text("QUERY_TEXT,COUNT,USERS\nselect 5 from t,7,3\n")
        out = t / "querylog.jsonl"
        res = cmd_querylog(argparse.Namespace(src=[str(t / n) for n in ("tool.json", "dbx.json", "api.json")],
                                              dst=str(out), append=False, limit=5000))
        log = {r["sql"]: r for r in read_jsonl(out)}
        ok("querylog reads a saved tool result, a Databricks statement and the history API", res["statements"] == 3
           and log["select 1 from t"]["exec"] == 5 and log["select 3 from t"] == {"sql": "select 3 from t", "exec": 2, "users": 2})
        ok("querylog keeps only successful SELECTs", res["skipped"] == 2 and "delete from t" not in log)
        ok("querylog never writes a user name", "example.com" not in out.read_text() and "eve" not in out.read_text())
        cmd_querylog(argparse.Namespace(src=[str(t / "export.csv")], dst=str(out), append=True, limit=5000))
        ok("querylog --append merges the next page", len(read_jsonl(out)) == 4 and read_jsonl(out)[0]["exec"] == 7)
        (t / "err.json").write_text(json.dumps({"isError": True, "content": [{"type": "text", "text": "permission denied"}]}))
        try:
            cmd_querylog(argparse.Namespace(src=[str(t / "err.json")], dst=str(out), append=True, limit=5000))
            ok("an error result is refused, not read as an empty log", False)
        except Blocked:
            ok("an error result is refused, not read as an empty log", True)
    return {"status": "ok" if not failed else "ERROR", "checks": len(checks), "failed": failed}


# ====================================================================== CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="metrics_mine.py", description=__doc__.split("\n\n")[0])
    s = p.add_subparsers(dest="cmd", required=True)

    c = s.add_parser("shapes", help="group a cleaned query log into ranked query shapes")
    c.add_argument("src", help="querylog.clean.jsonl: {sql, exec, users} per line")
    c.add_argument("dst", help="shapes.jsonl")
    c.add_argument("--tables", help="tables.txt: keep shapes that read one of these tables")
    c.add_argument("--top", type=int, default=400)
    c.set_defaults(func=cmd_shapes)

    c = s.add_parser("querylog", help="turn a saved history pull into querylog.jsonl without printing it")
    c.add_argument("src", nargs="+", help="a tool result saved to a file, CLI JSON output, or a JSONL or CSV export")
    c.add_argument("dst", help="querylog.jsonl: {sql, exec, users} per line")
    c.add_argument("--append", action="store_true", help="merge into dst's existing lines, for the next page")
    c.add_argument("--limit", type=int, default=5000)
    c.set_defaults(func=cmd_querylog)

    c = s.add_parser("check", help="check authored bodies against the registry's contracts and the workspace")
    c.add_argument("--dir", required=True)
    c.set_defaults(func=cmd_check)

    c = s.add_parser("import-run", help="read a finished metrics-agent run's registry payloads")
    c.add_argument("--run", required=True, help="the metrics-agent run directory")
    c.add_argument("--dir", required=True, help="this skill's run directory")
    c.set_defaults(func=cmd_import_run)

    c = s.add_parser("selftest", help="offline checks of scanning, shapes and the contracts")
    c.set_defaults(func=lambda a: selftest())
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        out = args.func(args)
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": e.reason, "fix": e.fix}, indent=1))
        return 2
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "ERROR", "error": f"{type(e).__name__}: {e}"}, indent=1))
        return 1
    print(json.dumps(out, indent=1, default=str))
    return {"ERROR": 1, "BLOCKED": 2, "REFUSED": 1}.get(out.get("status"), 0)


if __name__ == "__main__":
    sys.exit(main())
