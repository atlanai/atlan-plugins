#!/usr/bin/env python3
"""analytics_eval — the deterministic half of the talk-to-data build/improve skills.

Everything a model should not do by judgment lives here: filtering identifying literals out of a
query log, the train/test split, result-set grading, the eval-hillclimb results layout, the
harness-integrity gate, and publishing a run to the Atlan registry (dataset, experiment, results,
and one trace + one session per case as the agent). Python 3.10+. Local grading uses the standard library; publishing uses the pinned Atlan SDK.

    python3 analytics_eval.py <command> [options]        # every command prints one JSON object

Exit 0 ok, 1 error, 2 BLOCKED ({"status":"BLOCKED","reason","fix"}: something the user must fix).
Tokens and agent keys are held in memory only and never printed.

Project directory (--dir), written by the skills:
  state.json                   ids: workspace_id, agent_id, agent_name, eval_dataset_id, scorer_id, experiments{}
  eval/cases.jsonl             {case_id, question, tags[]}          <- the only text the agent ever sees
  eval/answers.jsonl           {case_id, gold_sql}                  <- LOCAL ONLY; never pushed, never shown
  eval/split.json              {train_ids, test_ids, seed}
  hillclimb/_state.json        harness_sha (+ the loop's own keys)
  hillclimb/<variant>/results.jsonl, summary.json, traces/<id>_rep<k>.json, results/<id>_rep<k>.{gold,agent}.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import hashlib
import json
import math
import os
import pathlib
import random
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter

VERSION = "0.1.0"
SCORER_NAME = "result-match"
UA = f"atlan-analytics-eval/{VERSION}"


class Blocked(Exception):
    def __init__(self, reason: str, fix: str = "", **extra):
        super().__init__(reason)
        self.reason, self.fix, self.extra = reason, fix, extra


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path) -> list:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def load_json(path, default=None):
    p = pathlib.Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def dump_json(path, obj) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(p)


# ====================================================================== identifying literals
_PERSON = (r"(?:assignee|assigned_agent|agent|requester|submitter|reviewer|author|owner|user|updater|actor|"
           r"creator|employee|csm|manager)")
_CUSTOMER = r"(?:customer|account|organization|organisation|org|company|tenant|domain|client)"
_OPS = r"(?:=|<>|!=|\bi?like\b|\bin\s*\()"
_COL = r"[\w.\"]*?"
PII_RULES = [
    ("email", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
    ("person_name", re.compile(rf"\b{_COL}{_PERSON}(?:_name|_email|_full_name)?\"?\s*{_OPS}\s*'", re.I)),
    ("person_id", re.compile(rf"\b{_COL}{_PERSON}_?id\"?\s*{_OPS}\s*[\d,\s']{{7,}}", re.I)),
    ("customer_name", re.compile(rf"\b{_COL}{_CUSTOMER}(?:_account)?(?:_name)?\"?\s*{_OPS}\s*'", re.I)),
    ("record_id", re.compile(r"\b[\w.\"]*?(?:ticket|record|case|order|invoice|incident|issue)_?id\"?\s*"
                             r"(?:=|in\s*\()\s*'?\d{4,}", re.I)),
    ("history_value", re.compile(r"(?:\bvalue\"?|prev_assignee)\s*(?:=|<>|!=|in\s*\()\s*'?\d{10,}", re.I)),
    ("full_name", re.compile(r"'%?[A-Z][a-z]+ [A-Z][a-z]+%?'")),
]
_FULL_NAME_OK = re.compile(r"'%?(?:Pending Ruling|Not Applicable|Closed Won|Closed Lost|United States|"
                           r"New York|Human Assisted|First Response)%?'")


def pii_reason(sql: str):
    for name, pat in PII_RULES:
        m = pat.search(sql or "")
        if not m:
            continue
        if name == "full_name" and _FULL_NAME_OK.search(m.group(0)):
            continue
        return name
    return None


def filter_querylog(src: str, dst: str) -> dict:
    """Copy JSONL rows whose SQL carries no identifying literal; counts by reason, never the literals."""
    kept = dropped = 0
    by: dict = {}
    with open(src) as fin, open(dst, "w") as fout:
        for line in fin:
            if not line.strip():
                continue
            row = json.loads(line)
            why = pii_reason(row.get("sql") or row.get("query_text") or "")
            if why:
                dropped += 1
                by[why] = by.get(why, 0) + 1
            else:
                kept += 1
                fout.write(line if line.endswith("\n") else line + "\n")
    return {"status": "ok", "kept": kept, "dropped": dropped, "by_reason": by, "out": dst}


# ====================================================================== grading (the trust anchor)
SIG = 4
MAX_ASSIGNMENTS = 20000
_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_ORDERED = re.compile(r"\border\s+by\b[\s\S]*\blimit\s+\d+\s*;?\s*$", re.I)


def _round_sig(x: float) -> float:
    """Half-up to SIG significant figures (round() is half-to-even: 12345 -> 12340)."""
    if x == 0 or not math.isfinite(x):
        return 0.0 if x == 0 else x
    d = decimal.Decimal(repr(x))
    exp = d.adjusted() - (SIG - 1)
    return float(d.quantize(decimal.Decimal(1).scaleb(exp), rounding=decimal.ROUND_HALF_UP))


def norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float, decimal.Decimal)):
        f = float(v)
        return _round_sig(f) if math.isfinite(f) else str(f)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    s = str(v).strip()
    if _NUM.match(s):
        return _round_sig(float(s))
    return s


def is_ordered(gold_sql: str) -> bool:
    return bool(_ORDERED.search((gold_sql or "").strip()))


def normalize_result(obj) -> dict:
    """Accept {columns, rows}, a list of row dicts, {"data"|"rows"|"results"|"resultSet": ...},
    {"error": ...} or a Databricks statement {manifest, result.data_array} (the shapes warehouse MCP
    tools return) and give {columns, rows} or {error}."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except json.JSONDecodeError:
            return {"error": obj[:500]}
    if isinstance(obj, dict):
        if obj.get("error") or obj.get("isError"):
            return {"error": str(obj.get("error") or obj.get("message") or obj)[:500]}
        status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
        if status.get("state") in ("FAILED", "CANCELED", "CLOSED"):
            return {"error": str(status.get("error") or status["state"])[:500]}
        manifest = obj.get("manifest") if isinstance(obj.get("manifest"), dict) else None
        if manifest is not None:  # a Databricks SQL statement execution response
            schema = manifest.get("schema") or {}
            cols = [c.get("name") for c in schema.get("columns") or [] if isinstance(c, dict)]
            data = (obj.get("result") or {}).get("data_array") or []
            return {"columns": cols, "rows": [list(r) for r in data],
                    "truncated": bool(manifest.get("truncated"))}
        cols = obj.get("columns") or obj.get("column_names")
        rows = obj.get("rows")
        if cols is not None and isinstance(rows, list):
            cols = [c.get("name") if isinstance(c, dict) else c for c in cols]
            if rows and isinstance(rows[0], dict):
                return {"columns": cols, "rows": [[r.get(c) for c in cols] for r in rows],
                        "truncated": bool(obj.get("truncated"))}
            return {"columns": cols, "rows": [list(r) for r in rows], "truncated": bool(obj.get("truncated"))}
        for key in ("data", "rows", "results", "resultSet", "result", "records"):
            if key in obj:
                return normalize_result(obj[key])
        if "content" in obj and isinstance(obj["content"], list):  # MCP tool result envelope
            texts = [c.get("text", "") for c in obj["content"] if isinstance(c, dict)]
            return normalize_result("\n".join(texts))
        return {"error": "unrecognised result shape"}
    if isinstance(obj, list):
        if not obj:
            return {"columns": [], "rows": []}
        if isinstance(obj[0], dict):
            cols: list = []
            for r in obj:
                for k in r:
                    if k not in cols:
                        cols.append(k)
            return {"columns": cols, "rows": [[r.get(c) for c in cols] for r in obj]}
        if isinstance(obj[0], (list, tuple)):
            return {"columns": [f"c{i + 1}" for i in range(len(obj[0]))], "rows": [list(r) for r in obj]}
    return {"error": "unrecognised result shape"}


def _cols(res: dict) -> list:
    rows = res.get("rows") or []
    width = len(res.get("columns") or (rows[0] if rows else []))
    return [[norm(r[j]) if j < len(r) else None for r in rows] for j in range(width)]


def _key(vec, ordered):
    return tuple(vec) if ordered else tuple(sorted(vec, key=lambda x: (x is None, str(type(x)), str(x))))


def _assignments(cands):
    order = sorted(range(len(cands)), key=lambda j: len(cands[j]))
    chosen: dict = {}
    used: set = set()

    def rec(k):
        if k == len(order):
            yield [chosen[j] for j in range(len(cands))]
            return
        j = order[k]
        for i in cands[j]:
            if i in used:
                continue
            chosen[j] = i
            used.add(i)
            yield from rec(k + 1)
            used.discard(i)
        chosen.pop(j, None)

    yield from rec(0)


def compare(gold: dict, agent: dict, gold_sql: str = "") -> dict:
    ordered = is_ordered(gold_sql)
    g_rows, a_rows = gold.get("rows") or [], agent.get("rows") or []
    g_cols, a_cols = _cols(gold), _cols(agent)
    if not g_rows:
        ok = not a_rows
        return {"pass": int(ok), "extra_cols": 0,
                "reason": "both empty" if ok else "gold is empty but the agent returned rows"}
    if len(g_rows) != len(a_rows):
        return {"pass": 0, "extra_cols": max(0, len(a_cols) - len(g_cols)),
                "reason": f"row count {len(a_rows)} vs gold {len(g_rows)}"}
    if len(a_cols) < len(g_cols):
        return {"pass": 0, "extra_cols": 0,
                "reason": f"agent returned {len(a_cols)} column(s), gold has {len(g_cols)}"}
    a_keys = [_key(c, ordered) for c in a_cols]
    cands = []
    for j, gc in enumerate(g_cols):
        k = _key(gc, ordered)
        cj = [i for i, ak in enumerate(a_keys) if ak == k]
        if not cj:
            name = (gold.get("columns") or ["?"] * (j + 1))[j]
            return {"pass": 0, "extra_cols": len(a_cols) - len(g_cols),
                    "reason": f"no agent column matches gold column {j + 1} ({name})"}
        cands.append(cj)
    gold_multiset = Counter(tuple(g_cols[j][r] for j in range(len(g_cols))) for r in range(len(g_rows)))
    tried = 0
    for mapping in _assignments(cands):
        tried += 1
        if tried > MAX_ASSIGNMENTS:
            break
        if ordered:
            ok = all(a_cols[mapping[j]] == g_cols[j] for j in range(len(g_cols)))
        else:
            got = Counter(tuple(a_cols[mapping[j]][r] for j in range(len(g_cols))) for r in range(len(a_rows)))
            ok = got == gold_multiset
        if ok:
            return {"pass": 1, "extra_cols": len(a_cols) - len(g_cols), "reason": "match"}
    return {"pass": 0, "extra_cols": len(a_cols) - len(g_cols),
            "reason": "columns match individually but the rows do not line up" if tried else "no mapping"}


def grade(gold_res, agent_res, gold_sql: str = "", agent_sql: str = "x") -> dict:
    """Both results already executed (by the skill, through the warehouse MCP). -> {grade, class, reason}."""
    zero = {"pass": 0, "exec_ok": 0, "extra_cols": 0}
    gold = normalize_result(gold_res)
    if gold.get("error"):
        return {"grade": zero, "class": "gold_error", "reason": gold["error"]}
    if not (agent_sql or "").strip():
        return {"grade": zero, "class": "no_sql", "reason": "the agent gave no SQL"}
    if agent_res is None:
        return {"grade": zero, "class": "agent_sql_error", "reason": "no agent result was recorded"}
    agent = normalize_result(agent_res)
    if agent.get("error"):
        return {"grade": zero, "class": "agent_sql_error", "reason": agent["error"]}
    cmp = compare(gold, agent, gold_sql)
    truncated = bool(gold.get("truncated") or agent.get("truncated"))
    return {"grade": {"pass": cmp["pass"], "exec_ok": 1, "extra_cols": cmp["extra_cols"]},
            "class": "pass" if cmp["pass"] else "wrong_result",
            "reason": cmp["reason"] + (" (results truncated)" if truncated else "")}


# ====================================================================== project layout
class Proj:
    def __init__(self, d: str):
        self.dir = pathlib.Path(d).expanduser()

    def p(self, *parts) -> pathlib.Path:
        return self.dir.joinpath(*parts)

    def state(self) -> dict:
        return load_json(self.p("state.json"), {}) or {}

    def set_id(self, key: str, value) -> None:
        st = self.state()
        cur = st.setdefault("ids", {})
        parts = key.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
        st["updated_at"] = now()
        dump_json(self.p("state.json"), st)

    def cases(self) -> list:
        rows = read_jsonl(self.p("eval", "cases.jsonl"))
        if not rows:
            raise Blocked("No eval cases yet.", "Write eval/cases.jsonl ({case_id, question, tags}).")
        return rows

    def answers(self) -> dict:
        return {r["case_id"]: r for r in read_jsonl(self.p("eval", "answers.jsonl"))}

    def split(self) -> dict:
        sp = load_json(self.p("eval", "split.json"))
        if not sp:
            raise Blocked("The eval set has not been split yet.", "Run `analytics_eval.py split --dir D`.")
        return sp

    def vdir(self, variant: str) -> pathlib.Path:
        d = self.p("hillclimb", variant)
        (d / "traces").mkdir(parents=True, exist_ok=True)
        (d / "results").mkdir(parents=True, exist_ok=True)
        return d


def make_split(case_rows: list, test_frac: float = 0.5, seed: int = 17) -> dict:
    """Random, stratified by tags[0] — never by score."""
    rnd = random.Random(seed)
    groups: dict = {}
    for c in case_rows:
        groups.setdefault((c.get("tags") or ["untagged"])[0], []).append(c["case_id"])
    train, test = [], []
    for key in sorted(groups):
        ids = sorted(groups[key])
        rnd.shuffle(ids)
        k = int(round(len(ids) * test_frac))
        if len(ids) > 1:
            k = min(max(k, 1), len(ids) - 1)
        test += ids[:k]
        train += ids[k:]
    return {"train_ids": sorted(train), "test_ids": sorted(test), "seed": seed, "test_frac": test_frac,
            "stratified_by": "tags[0]"}


def session_trace_id(harness: str, session_id: str) -> str:
    """The trace id the desktop sync gives a harness session: registry CLI's identity.v1 traceId."""
    digest = hashlib.sha256("\0".join(("identity.v1", harness, session_id)).encode()).digest()
    return digest[:16].hex()


def cmd_session_run(a) -> dict:
    """This session as a run a proposal can cite. The trace exists in the registry once the session is synced."""
    session = a.session_id or (os.environ.get("CLAUDE_CODE_SESSION_ID", "") if a.harness == "claude-code" else "")
    if not session:
        raise Blocked("This session's id is unknown.",
                      "Run it from the Claude Code session itself, or pass --session-id.")
    return {"status": "ok", "harness": a.harness, "session_id": session,
            "trace_id": session_trace_id(a.harness, session)}


def cmd_init(a) -> dict:
    """Create (or report) the project's state.json. Idempotent: an existing project is returned as is."""
    pr = Proj(a.dir)
    st = pr.state()
    if st:
        return {"status": "ok", "created": False, "state": st}
    st = {"slug": a.slug or pr.dir.name, "phase": "preflight", "answers": {}, "ids": {}, "decisions": [],
          "created_at": now(), "updated_at": now()}
    for sub in ("eval", "hillclimb"):
        pr.p(sub).mkdir(parents=True, exist_ok=True)
    dump_json(pr.p("state.json"), st)
    return {"status": "ok", "created": True, "state": st, "dir": str(pr.dir)}


def cmd_split(a) -> dict:
    pr = Proj(a.dir)
    path = pr.p("eval", "split.json")
    if path.exists() and not a.redraw:
        return {"status": "ok", "note": "split already fixed; --redraw only before the baseline", **load_json(path)}
    out = make_split(pr.cases(), a.test_frac, a.seed)
    dump_json(path, out)
    return {"status": "ok", **out}


# ====================================================================== harness integrity
def harness_sha() -> str:
    return hashlib.sha256(pathlib.Path(__file__).resolve().read_bytes()).hexdigest()


def check_harness(pr: Proj) -> None:
    approved = (load_json(pr.p("hillclimb", "_state.json"), {}) or {}).get("harness_sha")
    if approved and approved != harness_sha():
        raise Blocked("analytics_eval.py (the grader) changed since you last approved it.",
                      "Review what changed, then run `analytics_eval.py approve-harness --dir D` yourself.")


def cmd_approve_harness(a) -> dict:
    pr = Proj(a.dir)
    sp = pr.p("hillclimb", "_state.json")
    st = load_json(sp, {}) or {}
    st["harness_sha"] = harness_sha()
    st["harness_paths"] = ["skills/atlan-analytics/scripts/analytics_eval.py"]
    dump_json(sp, st)
    return {"status": "ok", "harness_sha": st["harness_sha"]}


# ====================================================================== transcripts
EVAL_KINDS = {"dataset", "dataset_record", "experiment", "experiment_result", "scorer"}
# A gateway in search mode reaches every tool through one dispatcher; the named tool is in its input.
_DISPATCH = re.compile(r"(?:^|__)call_tool$")
_WRITE_TOOL = re.compile(r"__(?:create|update|patch|put|delete|archive|restore|relate|unrelate|append|publish"
                         r"|release|withdraw|upload|install|uninstall|grant|revoke|rotate|set)(?:_|$)", re.I)
_EVAL_ID = re.compile(r"\b(?:dataset|dataset_record|experiment|experiment_result|scorer)_[0-9a-z]{10,}\b")
_EVAL_PATH = re.compile(r"answers\.jsonl|\.atlan-analytics|/eval/v1/|gold_results", re.I)


def _blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in _blocks(content):
        if b.get("type") == "text":
            out.append(b.get("text", ""))
        elif b.get("type") == "tool_result":
            out.append(_text_of(b.get("content")))
    return "\n".join(x for x in out if x)


def parse_transcript(path) -> dict:
    """A Claude Code subagent transcript (JSONL; each line carries a `message` with role + content blocks)
    -> {turns, tool_calls, usage, model, contaminated}. Tolerant of unknown line shapes."""
    turns, tool_calls, usage, model, hits = [], 0, {"input_tokens": 0, "output_tokens": 0}, None, []
    seen_msg_ids: set = set()
    for line in pathlib.Path(path).read_text().splitlines() if path and pathlib.Path(path).exists() else []:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        role = msg.get("role") or obj.get("type")
        if role not in ("user", "assistant"):
            continue
        mid = msg.get("id")
        if role == "assistant":
            model = msg.get("model") or model
            u = msg.get("usage") or {}
            if u and mid not in seen_msg_ids:  # a streamed message repeats its usage per content line
                usage["input_tokens"] += int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) \
                    + int(u.get("cache_creation_input_tokens") or 0)
                usage["output_tokens"] += int(u.get("output_tokens") or 0)
            if mid:
                seen_msg_ids.add(mid)
        for b in _blocks(msg.get("content")):
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                turns.append({"role": role, "content": b["text"]})
            elif t == "thinking" and b.get("thinking"):
                turns.append({"role": "assistant", "content": "", "thinking": b["thinking"]})
            elif t == "tool_use":
                tool_calls += 1
                name, inp = b.get("name") or "", b.get("input") or {}
                if _DISPATCH.search(name) and isinstance(inp.get("name"), str):
                    name, inp = inp["name"], inp.get("arguments") or {}
                blob = json.dumps(inp, default=str)
                if (str(inp.get("kind", "")).lower() in EVAL_KINDS if isinstance(inp, dict) else False) \
                        or _EVAL_ID.search(blob) or _EVAL_PATH.search(blob):
                    hits.append(f"{name}: {blob[:160]}")
                elif _WRITE_TOOL.search(name):
                    hits.append(f"{name}: a write tool")
                turns.append({"role": "tool_call", "name": name, "content": blob})
            elif t == "tool_result":
                turns.append({"role": "tool_result", "name": b.get("tool_use_id"),
                              "content": _text_of(b.get("content"))[:20000]})
    return {"turns": turns, "tool_calls": tool_calls, "usage": usage, "model": model, "contaminated": hits}


# ====================================================================== record / todo / scores
def _done(res_path) -> set:
    return {(r["prompt_id"], int(r["rep"])) for r in read_jsonl(res_path)}


def cmd_todo(a) -> dict:
    pr = Proj(a.dir)
    cases = {c["case_id"]: c for c in pr.cases()}
    ids = sorted(cases) if a.ids == "all" else sorted(pr.split()[f"{a.ids}_ids"])
    done = _done(pr.vdir(a.variant) / "results.jsonl")
    todo = [{"case_id": i, "rep": k, "question": cases[i]["question"], "tags": cases[i].get("tags") or []}
            for i in ids for k in range(a.reps) if (i, k) not in done]
    return {"status": "ok", "variant": a.variant, "remaining": len(todo), "done": len(done), "todo": todo}


def _read_answer(path) -> dict:
    raw = pathlib.Path(path).read_text().strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}\s*$", raw)  # a final JSON object after prose
        obj = json.loads(m.group(0)) if m else {"answer": raw, "sql": ""}
    return obj if isinstance(obj, dict) else {"answer": str(obj), "sql": ""}


def cmd_record(a) -> dict:
    pr = Proj(a.dir)
    check_harness(pr)
    if not a.gold_result and a.question:  # smoke: an ad-hoc question outside the eval set
        cases = {a.case: {"case_id": a.case, "question": a.question, "tags": ["smoke"]}}
    else:
        cases = {c["case_id"]: c for c in pr.cases()}
    if a.case not in cases:
        raise Blocked(f"Unknown case '{a.case}'.", "Use a case_id from eval/cases.jsonl.")
    gold_sql = (pr.answers().get(a.case) or {}).get("gold_sql") or ""
    if not gold_sql and a.gold_result:
        raise Blocked(f"Case '{a.case}' has no gold SQL.", "Add it to eval/answers.jsonl.")
    vd = pr.vdir(a.variant)
    res_path = vd / "results.jsonl"
    if (a.case, a.rep) in _done(res_path):
        return {"status": "ok", "exists": True, "case": a.case, "rep": a.rep}
    ans = _read_answer(a.answer_file)
    gold_res = load_json(a.gold_result) if a.gold_result else None
    agent_res = load_json(a.agent_result) if a.agent_result and pathlib.Path(a.agent_result).exists() else None
    # keep the executed results beside the row so a re-grade needs no warehouse
    tag = f"{a.case}_rep{a.rep}"
    if gold_res is not None:
        dump_json(vd / "results" / f"{tag}.gold.json", gold_res)
    if agent_res is not None:
        dump_json(vd / "results" / f"{tag}.agent.json", agent_res)
    if a.gold_result:
        g = grade(gold_res, agent_res, gold_sql, ans.get("sql") or "")
    else:  # smoke runs: nothing to grade against, the row only carries the trace and session
        g = {"grade": {"pass": 0, "exec_ok": int(agent_res is not None), "extra_cols": 0}, "class": "ungraded",
             "reason": "no gold result (smoke run)"}
    tr = parse_transcript(a.transcript) if a.transcript else {"turns": [], "tool_calls": None,
                                                              "usage": {}, "model": None, "contaminated": []}
    if tr["contaminated"]:
        g = {"grade": {"pass": 0, "exec_ok": g["grade"]["exec_ok"], "extra_cols": 0},
             "class": "invalid_contaminated",
             "reason": "the agent reached eval data: " + "; ".join(tr["contaminated"][:3])}
    turns = tr["turns"] or [{"role": "user", "content": cases[a.case]["question"]},
                            {"role": "assistant", "content": ans.get("answer", "")}]
    dump_json(vd / "traces" / f"{tag}.json", turns)
    row = {"prompt_id": a.case, "rep": a.rep, "prompt": cases[a.case]["question"],
           "tags": cases[a.case].get("tags") or [], "grade": g["grade"], "class": g["class"],
           "reason": g.get("reason"), "model": tr["model"] or a.model, "latency_s": a.latency_s,
           "tool_calls": tr["tool_calls"], "usage": tr["usage"], "answer": ans.get("answer", ""),
           "sql": ans.get("sql") or "", "used_context_ids": ans.get("used_context_ids") or [],
           "used_context_refs": ans.get("used_context_refs") or [], "ts": now()}
    with res_path.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return {"status": "ok", "case": a.case, "rep": a.rep, "class": g["class"], "pass": g["grade"]["pass"],
            "reason": g.get("reason")}


def cmd_grade(a) -> dict:
    gold = load_json(a.gold)
    agent = load_json(a.agent) if a.agent else None
    return {"status": "ok", **grade(gold, agent, pathlib.Path(a.gold_sql).read_text() if a.gold_sql and
                                    pathlib.Path(a.gold_sql).exists() else (a.gold_sql or ""), "x")}


def case_means(rows: list, metric: str = "pass") -> dict:
    by: dict = {}
    for r in rows:
        if r.get("class") in ("harness_error", "gold_error"):
            continue  # not the agent's fault: out of the scored denominator
        by.setdefault(r["prompt_id"], []).append(float((r.get("grade") or {}).get(metric, 0)))
    return {k: sum(v) / len(v) for k, v in by.items()}


def rate(means: dict, ids) -> dict:
    vals = [means[i] for i in ids if i in means]
    n = len(vals)
    p = sum(vals) / n if n else None
    half = 1.96 * math.sqrt(p * (1 - p) / n) if n and p is not None else None
    return {"n": n, "pass_rate": round(p, 4) if p is not None else None,
            "ci95": round(half, 4) if half is not None else None}


def summarize(pr: Proj, variant: str) -> dict:
    vd = pr.vdir(variant)
    rows = read_jsonl(vd / "results.jsonl")
    means = case_means(rows)
    sp = load_json(pr.p("eval", "split.json")) or {"train_ids": [], "test_ids": sorted(means)}
    classes = Counter(r.get("class", "?") for r in rows)
    reps = max((int(r["rep"]) for r in rows), default=0) + 1
    lat = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    n_test = len(sp["test_ids"]) or 1
    out = {"status": "ok", "variant": variant, "rows": len(rows), "cases": len(means), "reps": reps,
           "train": rate(means, sp["train_ids"]), "test": rate(means, sp["test_ids"]),
           "all": rate(means, sorted(means)), "classes": dict(classes),
           "noise_floor_test": round(1 / math.sqrt(n_test * reps), 4),
           "tokens": {"input": sum(int((r.get("usage") or {}).get("input_tokens") or 0) for r in rows),
                      "output": sum(int((r.get("usage") or {}).get("output_tokens") or 0) for r in rows)},
           "latency_s_median": round(statistics.median(lat), 2) if lat else None,
           "models": sorted({r["model"] for r in rows if r.get("model")})}
    summ = load_json(vd / "summary.json", {}) or {}
    summ.update({"variant": variant, "scored_at": now(), "headline_test": out["test"]["pass_rate"]})
    dump_json(vd / "summary.json", summ)
    return out


def cmd_summary(a) -> dict:
    return summarize(Proj(a.dir), a.variant)


def cmd_compare(a) -> dict:
    pr = Proj(a.dir)
    ma = case_means(read_jsonl(pr.vdir(a.a) / "results.jsonl"))
    mb = case_means(read_jsonl(pr.vdir(a.b) / "results.jsonl"))
    sp = pr.split()

    def paired(ids):
        both = [i for i in ids if i in ma and i in mb]
        ds = [mb[i] - ma[i] for i in both]
        n = len(ds)
        if not n:
            return {"n": 0}
        mean = sum(ds) / n
        sd = statistics.stdev(ds) if n > 1 else 0.0
        half = 1.96 * sd / math.sqrt(n) if n > 1 else None
        return {"n": n, "a": round(sum(ma[i] for i in both) / n, 4), "b": round(sum(mb[i] for i in both) / n, 4),
                "delta": round(mean, 4), "ci95": round(half, 4) if half is not None else None,
                "significant": bool(half is not None and half > 0 and abs(mean) > half),
                "flipped_up": sorted(i for i in both if mb[i] > ma[i]),
                "flipped_down": sorted(i for i in both if mb[i] < ma[i])}

    return {"status": "ok", "a": a.a, "b": a.b, "headline": "test",
            "test": paired(sp["test_ids"]), "train": paired(sp["train_ids"])}


# ====================================================================== registry (REST)
def _base_url() -> str:
    if os.environ.get("ATLAN_GATEWAY_URL"):
        return os.environ["ATLAN_GATEWAY_URL"].rstrip("/")
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        pj = load_json(pathlib.Path(root) / ".claude-plugin" / "plugin.json", {}) or {}
        url = ((pj.get("mcpServers") or {}).get("atlan") or {}).get("url") or ""
        if url:
            return re.sub(r"/mcp/?$", "", url).rstrip("/")
    return "https://api.dev.example"


def _token() -> str:
    tok = os.environ.get("ATLAN_GATEWAY_TOKEN", "").strip()
    if tok:
        return tok
    for name in ("atlanai-dev", "atlanai"):
        exe = shutil.which(name) or os.path.expanduser(f"~/.local/bin/{name}")
        if not os.path.exists(exe):
            continue
        try:
            out = subprocess.run([exe, "auth", "token"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = out.stdout.strip().splitlines() if out.returncode == 0 else []
        if lines and lines[-1].strip():
            return lines[-1].strip()
    raise Blocked("No Atlan sign-in found for publishing the eval run.",
                  "Run `atlanai auth login` (dev: `atlanai-dev auth login`), or set ATLAN_GATEWAY_TOKEN.")


def Gateway(token: str, base: str):
    """The pinned management SDK owns authentication, transport and publishing mechanics."""
    try:
        from atlanai import AtlanClient
        from atlanai.recorded import RecordedEvaluationPublisher
    except ImportError:
        raise Blocked("The publishing SDK is not installed.",
                      "Run push with `uv run --project <skill>/scripts analytics_eval.py push ...`.") from None
    key = os.environ.get("ATLAN_EVAL_AGENT_API_KEY", "").strip()
    return RecordedEvaluationPublisher(
        AtlanClient(base, bearer_token=token),
        AtlanClient(base, bearer_token=key) if key else None,
    )


def _target(tool: str) -> str:
    t = (tool or "").lower()
    if "registry" in t or "metric" in t or "ontology" in t:
        return "Atlan registry"
    if "snowflake" in t or "databricks" in t or "sql" in t or "query" in t:
        return "warehouse"
    return tool or "tool"


def trace_payload(row: dict, turns: list, *, agent_name: str, conversation_id: str, trace_id: str,
                  extra: dict) -> dict:
    """Root invoke_agent span + one execute_tool span per tool call, timed across the case's latency."""
    try:
        end = dt.datetime.fromisoformat(row["ts"]).timestamp()
    except (KeyError, ValueError):
        end = time.time()
    lat = float(row.get("latency_s") or 30.0)
    s_ns, e_ns = int((end - lat) * 1e9), int(end * 1e9)
    from atlanai.recorded import recorded_trace
    u = row.get("usage") or {}
    calls = [t for t in turns if t.get("role") == "tool_call"]
    results = [t for t in turns if t.get("role") == "tool_result"]
    tools = [{"name": call.get("name"), "target": _target(call.get("name")),
              "arguments": call.get("content", "")[:8000],
              "result": (results[i].get("content") or "")[:8000] if i < len(results) else ""}
             for i, call in enumerate(calls)]
    return recorded_trace(trace_id=trace_id, session_id=conversation_id, service_name=agent_name,
                          start_ns=s_ns, end_ns=e_ns, title=("[eval] " + row["prompt"])[:200], tools=tools,
                          success=bool(row.get("answer")), attributes={
        **extra, "gen_ai.request.model": row.get("model"),
        "gen_ai.input.messages": [{"role": "user", "content": [{"type": "text", "text": row["prompt"]}]}],
        "gen_ai.output.messages": [{"role": "assistant", "content": [{"type": "text", "text": row.get("answer") or ""}]}],
        "gen_ai.usage.input_tokens": int(u.get("input_tokens") or 0),
        "gen_ai.usage.output_tokens": int(u.get("output_tokens") or 0),
        "db.statement": row.get("sql") or None, "atlan.eval.class": row.get("class"),
        "atlan.eval.score": float((row.get("grade") or {}).get("pass", 0)),
        "atlan.context.used": row.get("used_context_ids") or None,
        "atlan.context.refs": json.dumps(row["used_context_refs"]) if row.get("used_context_refs") else None,
    })


def session_messages(row: dict, turns: list) -> list:
    msgs = [{"message_role": "user", "content": row["prompt"]}]
    for t in turns:
        if t.get("role") == "tool_call":
            cid = f"call_{len(msgs)}"
            msgs.append({"message_role": "assistant", "model_id": row.get("model"), "content": "",
                         "tool_calls": [{"id": cid, "name": t.get("name"), "arguments": t.get("content", "")}]})
        elif t.get("role") == "tool_result":
            prev = msgs[-1].get("tool_calls") if msgs else None
            cid = prev[0]["id"] if prev else f"call_{len(msgs)}"
            msgs.append({"message_role": "tool", "tool_call_id": cid, "content": (t.get("content") or "")[:20000]})
    u = row.get("usage") or {}
    msgs.append({"message_role": "assistant", "model_id": row.get("model"), "content": row.get("answer") or "(no answer)",
                 "usage": {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0)}})
    return msgs


def _publisher_key() -> str:
    """An operator-supplied agent credential; evidence publishing never changes identity."""
    key = os.environ.get("ATLAN_EVAL_AGENT_API_KEY", "").strip()
    if not key:
        raise Blocked("Trace publishing requires an explicit agent credential.",
                      "Set ATLAN_EVAL_AGENT_API_KEY to a credential for the evaluated agent. "
                      "Publishing does not create or rotate credentials.")
    return key


def _ship_rows(pr, gw, vd, rows, variant, ws, agent_id, agent_name, slug, *, eid, records) -> dict:
    """One OTLP trace + one session per row using an explicit agent credential. Resume via shipped.json."""
    a = argparse.Namespace(variant=variant)
    shipped_p = vd / "shipped.json"
    shipped = load_json(shipped_p, {}) or {}
    pending_p = vd / "publishing.json"
    pending = load_json(pending_p, {}) or {}
    todo = [r for r in rows if f"{r['prompt_id']}_rep{r['rep']}" not in shipped]
    if todo:
        for r in todo:
            tag = f"{r['prompt_id']}_rep{r['rep']}"
            turns = load_json(vd / "traces" / f"{tag}.json", []) or []
            tid = secrets.token_hex(16)
            conv = f"analytics-eval-{slug}-{a.variant}-{tag}-{tid[:8]}"
            extra = {"atlan.eval.experiment_id": eid, "atlan.eval.dataset_record_id": records.get(r["prompt_id"]),
                     "atlan.eval.attempt": int(r["rep"]) + 1, "atlan.eval.variant": a.variant}
            trace = trace_payload(r, turns, agent_name=agent_name, conversation_id=conv, trace_id=tid, extra=extra)
            msgs = session_messages(r, turns)
            u = r.get("usage") or {}
            # A transport failure may follow accepted evidence. Keep intent before any upload
            # so a later push cannot blindly replay a trace or partially recorded session.
            pending[tag] = {"trace_id": tid, "conversation_id": conv}
            dump_json(pending_p, pending)
            sess = gw.case(trace=trace, session={
                "name": f"{agent_name} {conv[-14:]}", "workspace_id": ws, "subject_kind": "agent",
                "subject_id": agent_id, "session_status": "completed" if r.get("answer") else "failed",
                "external_session_id": conv, "model_id": r.get("model"), "title": ("[eval] " + r["prompt"])[:200],
                "message_count": len(msgs), "stop_reason": "final_answer" if r.get("answer") else "no_answer",
                "usage": {"input_tokens": int(u.get("input_tokens") or 0),
                          "output_tokens": int(u.get("output_tokens") or 0)}}, messages=msgs)
            shipped[tag] = {"trace_id": tid, "session_id": sess["id"], "conversation_id": conv}
            dump_json(shipped_p, shipped)
            pending.pop(tag)
            dump_json(pending_p, pending)
    return {"status": "ok", "shipped": len(todo), "variant": variant, "experiment_id": eid,
            "sessions_only": eid is None, "receipts": shipped}


def cmd_push(a) -> dict:
    pr = Proj(a.dir)
    st = pr.state()
    ids = st.get("ids") or {}
    ws = a.workspace or ids.get("eval_workspace_id") or ids.get("workspace_id")
    agent_id = a.agent_id or ids.get("agent_id")
    agent_name = ids.get("agent_name") or st.get("slug") or "talk-to-data-agent"
    if not ws or not agent_id:
        raise Blocked("state.json has no workspace_id / agent_id.", "Register the agent first (build phase 5).")
    vd = pr.vdir(a.variant)
    rows = read_jsonl(vd / "results.jsonl")
    if not rows:
        raise Blocked(f"No recorded results for {a.variant}.", "Record cases with `analytics_eval.py record` first.")
    shipped = load_json(vd / "shipped.json", {}) or {}
    pending = load_json(vd / "publishing.json", {}) or {}
    unresolved = [tag for tag in pending if tag not in shipped]
    if unresolved:
        raise Blocked("A previous evidence publish has an unresolved outcome.",
                      "Reconcile the trace and session using the IDs in publishing.json. "
                      "Save confirmed complete receipts in shipped.json, or clear the pending "
                      "entry only after confirming nothing was accepted, before retrying.",
                      pending=unresolved)
    if any(f"{r['prompt_id']}_rep{r['rep']}" not in shipped for r in rows):
        _publisher_key()  # Fail before creating any experiment/dataset if traces cannot be sent.
    gw = Gateway(_token(), _base_url())
    try:
        slug = st.get("slug") or pr.dir.name
        if a.sessions_only:
            shipping = _ship_rows(pr, gw, vd, rows, a.variant, ws, agent_id, agent_name, slug, eid=None, records={})
            shipping.pop("receipts")
            return shipping
        # scorer
        sid = ids.get("scorer_id")
        if not sid:
            hit = gw.find("scorer", SCORER_NAME, ws)
            sid = hit["id"] if hit else gw.create("scorer", {
                "name": SCORER_NAME, "workspace_id": ws, "display_name": "Result-set match", "scorer_kind": "code",
                "scope": "result", "spec": {"type": "result_set_match", "source": "atlan-analytics-eval/analytics_eval.py",
                                            "rules": "gold and agent SQL on the same warehouse; 4 significant figures; "
                                                     "column order/names ignored; row order only for ORDER BY … LIMIT; "
                                                     "extra columns allowed"},
                "outputs": {"result_match": {"type": "number", "range": [0, 1]}}})["id"]
            pr.set_id("scorer_id", sid)
        # dataset: questions only; the gold SQL stays on disk, a hash proves which one graded
        did = ids.get("eval_dataset_id")
        if not did:
            name = f"{slug}-eval"
            hit = gw.find("dataset", name, ws)
            did = hit["id"] if hit else gw.create("dataset", {
                "name": name, "workspace_id": ws, "display_name": f"{slug} eval", "input_key": "question",
                "description": "Talk-to-data questions. The trusted SQL is kept locally by the grader and is not "
                               "stored here, so the agent cannot retrieve it."})["id"]
            pr.set_id("eval_dataset_id", did)
        rec_path = pr.p("eval", "records.json")
        records = load_json(rec_path, {}) or {}
        gold = pr.answers()
        new = [c for c in pr.cases() if c["case_id"] not in records]
        if new:
            items = [{"name": c["case_id"], "workspace_id": ws,
                      "input": {"question": c["question"], "tags": c.get("tags") or []},
                      "expected": {"gold_sql_sha256": hashlib.sha256(
                          ((gold.get(c["case_id"]) or {}).get("gold_sql") or "").strip().encode()).hexdigest(),
                          "note": "gold SQL kept locally so the agent cannot retrieve it"},
                      "label": (c.get("tags") or ["untagged"])[0], "categories": c.get("tags") or [],
                      "source_kind": "manual"} for c in new]
            landed, rejected = gw.dataset_records(did, items)
            records.update({k: v for k, v in landed.items() if isinstance(v, str)})
            dump_json(rec_path, records)
            if rejected:
                return {"status": "DONE_WITH_CONCERNS", "stage": "dataset", "rejected": rejected[:10],
                        "fix": "re-run push to reconcile recorded rows; resolve conflicting dataset content before retrying"}
        # experiment
        exps = ids.get("experiments") or {}
        eid = exps.get(a.variant)
        if not eid:
            name = f"{slug}-{a.variant}"
            hit = gw.find("experiment", name, ws)
            if hit:
                eid = hit["id"]
            else:
                body = {"name": name, "workspace_id": ws, "display_name": f"{slug} · {a.variant}",
                        "description": (load_json(vd / "summary.json", {}) or {}).get("description") or f"analytics eval {a.variant}",
                        "dataset_id": did, "subject_kind": "agent", "subject_id": agent_id,
                        "experiment_status": "running",
                        "config": {"variant": a.variant, "runner": "atlan-analytics-eval", "grader": SCORER_NAME,
                                   "harness_sha": harness_sha()}}
                if a.baseline and exps.get(a.baseline):
                    body["baseline_experiment_id"] = exps[a.baseline]
                eid = gw.create("experiment", body)["id"]
            pr.set_id(f"experiments.{a.variant}", eid)
        shipping = _ship_rows(pr, gw, vd, rows, a.variant, ws, agent_id, agent_name, slug, eid=eid, records=records)
        shipped = shipping["receipts"]
        # results, then finalize (a completed experiment refuses results)
        have = {(it.get("metadata") or it).get("case_id") or it.get("name") for it in gw.results(eid)}
        items = []
        for r in rows:
            cid = r["prompt_id"] if int(r["rep"]) == 0 else f"{r['prompt_id']}~rep{r['rep']}"
            if cid in have:
                continue
            tag = f"{r['prompt_id']}_rep{r['rep']}"
            items.append({"name": f"{slug}-{a.variant}-{cid}", "workspace_id": ws, "case_id": cid,
                          **({"dataset_record_id": records[r["prompt_id"]]} if records.get(r["prompt_id"]) else {}),
                          "input": {"question": r["prompt"]},
                          "output": {"answer": r.get("answer"), "sql": r.get("sql"), "class": r.get("class")},
                          "scores": {"result_match": float((r.get("grade") or {}).get("pass", 0))},
                          "duration_ms": int(float(r.get("latency_s") or 0) * 1000),
                          **({"trace_id": shipped[tag]["trace_id"]} if tag in shipped else {})})
        landed, rejected = gw.publish_results(eid, items)
        if rejected:
            return {"status": "DONE_WITH_CONCERNS", "experiment_id": eid, "accepted": len(have) + len(landed),
                    "rejected": rejected[:10], "note": "left running so a re-run can finish it"}
        s = summarize(pr, a.variant)
        gw.complete(eid, {
            "cases": s["cases"], "rows": s["rows"], "accuracy": s["all"]["pass_rate"],
            "train_accuracy": s["train"]["pass_rate"], "test_accuracy": s["test"]["pass_rate"],
            "test_ci95": s["test"]["ci95"], "classes": s["classes"], "models": s["models"], "variant": a.variant,
            "scorer_id": sid, "runner": "atlan-analytics-eval", "finished_at": now()})
        return {"status": "ok", "experiment_id": eid, "dataset_id": did, "results": len(have) + len(landed),
                "traces_shipped": shipping["shipped"], "summary": s}

    finally:
        gw.close()

# ====================================================================== selftest
def selftest() -> dict:
    checks = []

    def ok(name, cond):
        checks.append({"check": name, "ok": bool(cond)})

    g = {"columns": ["grp", "n"], "rows": [["NOC", 1], ["Support", 2]]}
    ok("column order and names ignored", compare(g, {"columns": ["c", "g"], "rows": [[2, "Support"], [1, "NOC"]]})["pass"])
    ok("4 significant figures", compare({"columns": ["x"], "rows": [[0.123449]]}, {"columns": ["y"], "rows": [["0.1234"]]})["pass"])
    ok("half-up rounding", _round_sig(12345.0) == 12350.0)
    ok("extra agent columns allowed", compare(g, {"columns": ["g", "n", "pct"], "rows": [["NOC", 1, 0.3], ["Support", 2, 0.7]]})["extra_cols"] == 1)
    ok("wrong value fails", not compare(g, {"columns": ["g", "n"], "rows": [["NOC", 1], ["Support", 3]]})["pass"])
    top = "select grp, n from t order by n desc limit 2"
    ok("ordered top-N checks order", not compare({"columns": ["g", "n"], "rows": [["S", 2], ["N", 1]]},
                                                {"columns": ["g", "n"], "rows": [["N", 1], ["S", 2]]}, top)["pass"])
    ok("empty vs empty passes", compare({"columns": ["n"], "rows": []}, {"columns": ["n"], "rows": []})["pass"])
    ok("list-of-dicts normalized", grade([{"N": 3}], {"data": [{"COUNT": 3}]}, "", "select 1")["class"] == "pass")
    dbx = {"statement_id": "s1", "status": {"state": "SUCCEEDED"},
           "manifest": {"schema": {"columns": [{"name": "n", "position": 0}]}}, "result": {"data_array": [["3"]]}}
    ok("databricks statement normalized", grade([{"N": 3}], dbx, "", "select 1")["class"] == "pass")
    ok("databricks failure is an agent error",
       grade(g, {"status": {"state": "FAILED", "error": {"message": "no table"}}}, "", "select nope")["class"]
       == "agent_sql_error")
    ok("agent error class", grade(g, {"error": "invalid identifier"}, "", "select nope")["class"] == "agent_sql_error")
    ok("no sql class", grade(g, None, "", "")["class"] == "no_sql")
    cs = [{"case_id": f"c{i}", "tags": ["a" if i % 2 else "b"]} for i in range(10)]
    s1, s2 = make_split(cs, 0.5, 7), make_split(cs, 0.5, 7)
    ok("split deterministic + disjoint", s1 == s2 and not set(s1["train_ids"]) & set(s1["test_ids"]))
    ok("pii email", pii_reason("select * from t where email = 'a.b@x.com'") == "email")
    ok("pii person name", pii_reason("select * from t where assignee_name ilike '%sam%'") == "person_name")
    ok("pii clean", pii_reason("select count(*) from t where status = 'solved'") is None)
    # The value registry CLI's identity.v1 traceId gives this session, so a proposal names the trace the desktop sync uploads.
    ok("session trace id matches the desktop sync",
       session_trace_id("claude-code", "0b3f7c2e-5d41-4a8e-9c6b-2f1e8d7a4c90") == "807a0b2e8d4753667014b60e5c11bd75")
    ok("session trace id depends on the harness",
       session_trace_id("codex", "0b3f7c2e-5d41-4a8e-9c6b-2f1e8d7a4c90") != "807a0b2e8d4753667014b60e5c11bd75")
    with tempfile.TemporaryDirectory() as d:
        pr = pathlib.Path(d)
        (pr / "eval").mkdir()
        (pr / "eval" / "cases.jsonl").write_text(json.dumps({"case_id": "c1", "question": "How many solved?", "tags": ["v"]}) + "\n")
        (pr / "eval" / "answers.jsonl").write_text(json.dumps({"case_id": "c1", "gold_sql": "select count(*) from t"}) + "\n")
        dump_json(pr / "gold.json", {"columns": ["n"], "rows": [[3]]})
        dump_json(pr / "agent.json", [{"N": 3}])
        (pr / "ans.json").write_text(json.dumps({"answer": "3", "sql": "select count(*) n from t"}))
        clean = pr / "clean.jsonl"
        clean.write_text("\n".join(json.dumps(x) for x in [
            {"message": {"role": "assistant", "id": "m1", "model": "claude-opus-5-5", "usage": {"input_tokens": 10, "output_tokens": 5},
                         "content": [{"type": "tool_use", "id": "t1", "name": "registry__search_artifacts",
                                      "input": {"query": "solved tickets", "kind": "metric"}}]}},
            {"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1 item"}]}}]))
        dirty = pr / "dirty.jsonl"
        dirty.write_text(json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "registry__list_artifacts", "input": {"kind": "dataset_record"}}]}}))
        dispatched = pr / "dispatched.jsonl"
        dispatched.write_text(json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t3", "name": "mcp__atlan__mcp__call_tool",
             "input": {"name": "registry__list_artifacts", "arguments": {"kind": "dataset_record"},
                       "session_id": "s"}}]}}))
        writer = pr / "writer.jsonl"
        writer.write_text(json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t4", "name": "mcp__call_tool",
             "input": {"name": "registry__create_artifact", "arguments": {"kind": "metric"}}}]}}))
        ok("search-mode call names the dispatched tool",
           parse_transcript(dispatched)["turns"][0]["name"] == "registry__list_artifacts")

        def rec(variant, transcript, rep=0):
            ns = argparse.Namespace(dir=d, variant=variant, case="c1", rep=rep, answer_file=str(pr / "ans.json"),
                                    gold_result=str(pr / "gold.json"), agent_result=str(pr / "agent.json"),
                                    transcript=str(transcript), model=None, latency_s=12.0)
            return cmd_record(ns)

        r1 = rec("v1", clean)
        ok("record grades a clean run", r1["class"] == "pass")
        ok("record is resume-idempotent", rec("v1", clean).get("exists"))
        ok("contamination invalidates", rec("v2", dirty)["class"] == "invalid_contaminated")
        ok("search-mode contamination invalidates", rec("v3", dispatched)["class"] == "invalid_contaminated")
        ok("a write call invalidates", rec("v4", writer)["class"] == "invalid_contaminated")
        row = read_jsonl(pr / "hillclimb" / "v1" / "results.jsonl")[0]
        ok("usage + tool calls parsed", row["tool_calls"] == 1 and row["usage"]["input_tokens"] == 10)
        ok("legacy answers keep empty revision citations", row["used_context_refs"] == [])
        refs = [{"artifact_id": "metric_solved", "version_id": "ver_published",
                 "release_id": "rel_solved", "channel": "latest"},
                {"artifact_id": "sql_query_solved", "version_id": "ver_query"}]
        dump_json(pr / "ans.json", {"answer": "3", "sql": "select count(*) n from t",
                                    "used_context_ids": [r["artifact_id"] for r in refs],
                                    "used_context_refs": refs})
        ok("revision citations do not change answer grading", rec("cited", clean)["class"] == "pass")
        cited = read_jsonl(pr / "hillclimb" / "cited" / "results.jsonl")[0]
        ok("record retains exact published and query revision citations", cited["used_context_refs"] == refs
           and cited["used_context_ids"] == [r["artifact_id"] for r in refs])
        cmd_approve_harness(argparse.Namespace(dir=d))
        st = load_json(pr / "hillclimb" / "_state.json")
        st["harness_sha"] = "0" * 64
        dump_json(pr / "hillclimb" / "_state.json", st)
        try:
            rec("v3", clean)
            ok("changed harness blocks record", False)
        except Blocked:
            ok("changed harness blocks record", True)
    failed = [c["check"] for c in checks if not c["ok"]]
    return {"status": "ok" if not failed else "ERROR", "checks": len(checks), "failed": failed}


# ====================================================================== CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="analytics_eval.py", description=__doc__.split("\n\n")[0])
    s = p.add_subparsers(dest="cmd", required=True)

    c = s.add_parser("filter-querylog", help="drop statements with identifying literals")
    c.add_argument("src")
    c.add_argument("dst")
    c.set_defaults(func=lambda a: filter_querylog(a.src, a.dst))

    c = s.add_parser("init", help="create the project's state.json (idempotent)")
    c.add_argument("--dir", required=True)
    c.add_argument("--slug", default="")
    c.set_defaults(func=cmd_init)

    c = s.add_parser("split", help="random train/test split, stratified by tags[0], fixed once")
    c.add_argument("--dir", required=True)
    c.add_argument("--test-frac", type=float, default=0.5)
    c.add_argument("--seed", type=int, default=17)
    c.add_argument("--redraw", action="store_true")
    c.set_defaults(func=cmd_split)

    c = s.add_parser("grade", help="compare two executed result sets")
    c.add_argument("--gold", required=True)
    c.add_argument("--agent")
    c.add_argument("--gold-sql", default="", help="the gold SQL text or a file holding it (for ORDER BY … LIMIT)")
    c.set_defaults(func=cmd_grade)

    c = s.add_parser("todo", help="(case, rep) pairs still to run, with the question only")
    c.add_argument("--dir", required=True)
    c.add_argument("--variant", required=True)
    c.add_argument("--ids", choices=["all", "train", "test"], default="all")
    c.add_argument("--reps", type=int, default=1)
    c.set_defaults(func=cmd_todo)

    c = s.add_parser("record", help="grade one case and append its row + trace")
    c.add_argument("--dir", required=True)
    c.add_argument("--variant", required=True)
    c.add_argument("--case", required=True)
    c.add_argument("--rep", type=int, default=0)
    c.add_argument("--answer-file", required=True)
    c.add_argument("--gold-result", help="omit only for smoke runs (the row is recorded ungraded)")
    c.add_argument("--question", help="smoke runs only: the ad-hoc question (no gold, not in the eval set)")
    c.add_argument("--agent-result")
    c.add_argument("--transcript")
    c.add_argument("--model")
    c.add_argument("--latency-s", type=float, default=None)
    c.set_defaults(func=cmd_record)

    c = s.add_parser("summary", help="pass rates for one variant (test is the headline)")
    c.add_argument("--dir", required=True)
    c.add_argument("--variant", required=True)
    c.set_defaults(func=cmd_summary)

    c = s.add_parser("compare", help="paired per-case delta b − a")
    c.add_argument("--dir", required=True)
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.set_defaults(func=cmd_compare)

    c = s.add_parser("approve-harness", help="record this script's digest (the user runs this)")
    c.add_argument("--dir", required=True)
    c.set_defaults(func=cmd_approve_harness)

    c = s.add_parser("push", help="publish a variant to the registry: dataset, experiment, results, traces, sessions")
    c.add_argument("--dir", required=True)
    c.add_argument("--variant", required=True)
    c.add_argument("--baseline")
    c.add_argument("--agent-id")
    c.add_argument("--workspace")
    c.add_argument("--sessions-only", action="store_true",
                   help="ship traces + sessions only (smoke runs): no dataset, experiment or results")
    c.set_defaults(func=cmd_push)

    c = s.add_parser("session-run", help="this harness session's trace id, for a proposal's evidence")
    c.add_argument("--harness", choices=["claude-code", "codex"], default="claude-code")
    c.add_argument("--session-id", default="", help="defaults to CLAUDE_CODE_SESSION_ID in Claude Code")
    c.set_defaults(func=cmd_session_run)

    c = s.add_parser("selftest", help="offline checks of grading, split, filter and record")
    c.set_defaults(func=lambda a: selftest())
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        out = args.func(args)
    except Blocked as e:
        print(json.dumps({"status": "BLOCKED", "reason": e.reason, "fix": e.fix, **e.extra}, indent=1))
        return 2
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "ERROR", "error": f"{type(e).__name__}: {e}"}, indent=1))
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 1 if out.get("status") == "ERROR" else (2 if out.get("status") == "BLOCKED" else 0)


if __name__ == "__main__":
    sys.exit(main())
