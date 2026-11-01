#!/usr/bin/env python3
"""analytics_eval — the deterministic half of the talk-to-data build/improve skills.

Everything a model should not do by judgment lives here: filtering identifying literals out of a
query log, the train/test split, result-set grading, the eval-hillclimb results layout, the
harness-integrity gate, and planning how a run is published to the Atlan registry (dataset,
experiment, results, and one session per case). Python 3.10+, standard library only. It never
touches the network: the assistant sends each registry write through the Atlan MCP server.

    python3 analytics_eval.py <command> [options]        # every command prints one JSON object

Exit 0 ok, 1 error, 2 BLOCKED ({"status":"BLOCKED","reason","fix"}: something the user must fix).
It reads no credentials.

Project directory (--dir), written by the skills:
  state.json                   ids: workspace_id, agent_id, agent_name, eval_dataset_id, scorer_id, experiments{}
  eval/cases.jsonl             {case_id, question, tags[]}          <- the only text the agent ever sees
  eval/answers.jsonl           {case_id, gold_sql}                  <- LOCAL ONLY; never pushed, never shown
  eval/split.json              {train_ids, test_ids, seed}
  hillclimb/_state.json        harness_sha (+ the loop's own keys)
  hillclimb/<variant>/results.jsonl, summary.json, traces/<id>_rep<k>.json, results/<id>_rep<k>.{gold,agent}.json
  hillclimb/<variant>/publish.json  session, message and result ids already in the registry, and the pending step
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
import statistics
import sys
import tempfile
from collections import Counter

VERSION = "0.1.0"
SCORER_NAME = "result-match"


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
    """The trace id the desktop sync gives a harness session: registry-cli's identity.v1 traceId."""
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


# ====================================================================== publishing (registry MCP)
# The assistant makes every registry write through the Atlan MCP server with the person's own
# sign-in, as metrics mode does. This script never touches the network: `publish` names the next
# batch of tool calls, and `publish-ack` reads back what the registry returned. Trace spans have no
# MCP write, so each case is published as a session with its full message list instead.
BATCH = 100        # create_artifacts takes at most 100 items
SESSION_BATCH = 20  # session creates per step: each needs its own create_artifact call
LIST_TOOLS = ("registry__list_artifacts", "registry__search_artifacts")


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


def _idem_key(*parts) -> str:
    """A deterministic idempotency key (registry: 1-128 of [A-Za-z0-9_-])."""
    return "ae-" + hashlib.sha256("\0".join(str(p) for p in parts).encode()).hexdigest()[:40]


def _create(kind: str, metadata: dict, key: str) -> dict:
    return {"tool": "registry__create_artifact",
            "arguments": {"kind": kind, "metadata": metadata, "idempotency_key": key}}


def _create_many(kind: str, items: list) -> dict:
    return {"tool": "registry__create_artifacts", "arguments": {"kind": kind, "items": items}}


def _find_id(obj, kind: str):
    """The `<kind>_…` handle in one tool result, wherever the envelope puts it."""
    if isinstance(obj, str):
        return obj if obj.startswith(kind + "_") else None
    if isinstance(obj, dict):
        for k in ("id", "artifact_id"):
            v = obj.get(k)
            if isinstance(v, str) and v.startswith(kind + "_"):
                return v
        for k in ("artifact", "result", "data", "structuredContent", "item"):
            if k in obj:
                hit = _find_id(obj[k], kind)
                if hit:
                    return hit
    return None


def _failed(obj) -> str | None:
    if isinstance(obj, dict):
        if obj.get("error"):
            return str(obj["error"])[:300]
        status = obj.get("status")
        if isinstance(status, int) and status >= 400:
            return str(obj.get("message") or obj.get("detail") or status)[:300]
        if obj.get("isError"):
            return json.dumps(obj.get("content"))[:300]
    return None


def _slots(resp) -> list:
    """The per-item slots of a create_artifacts result, in the order the items were sent."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("items", "results", "slots", "created"):
            if isinstance(resp.get(k), list):
                return resp[k]
        for k in ("result", "data", "structuredContent"):
            if k in resp:
                return _slots(resp[k])
    return []


def _listed(resp, kind: str) -> dict:
    """name -> id from a list or search result, for reconciling a step whose response was lost."""
    out = {}
    for row in _slots(resp):
        if isinstance(row, dict):
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            name = row.get("name") or meta.get("name")
            aid = _find_id(row, kind)
            if name and aid:
                out[name] = aid
    return out


class Publication:
    """One variant's publishing state: `hillclimb/<variant>/publish.json`."""

    def __init__(self, pr: Proj, variant: str):
        self.pr, self.variant = pr, variant
        self.path = pr.vdir(variant) / "publish.json"
        self.s = load_json(self.path, {}) or {}
        for k in ("sessions", "messages", "results"):
            self.s.setdefault(k, {})

    def save(self) -> None:
        dump_json(self.path, self.s)


def _context(pr: Proj, a) -> dict:
    st = pr.state()
    ids = st.get("ids") or {}
    ws = getattr(a, "workspace", None) or ids.get("eval_workspace_id") or ids.get("workspace_id")
    agent_id = getattr(a, "agent_id", None) or ids.get("agent_id")
    if not ws or not agent_id:
        raise Blocked("state.json has no workspace_id / agent_id.", "Register the agent first (build phase 5).")
    rows = read_jsonl(pr.vdir(a.variant) / "results.jsonl")
    if not rows:
        raise Blocked(f"No recorded results for {a.variant}.", "Record cases with `analytics_eval.py record` first.")
    return {"ids": ids, "ws": ws, "agent_id": agent_id, "rows": rows, "slug": st.get("slug") or pr.dir.name,
            "agent_name": ids.get("agent_name") or st.get("slug") or "talk-to-data-agent"}


def _tag(r: dict) -> str:
    return f"{r['prompt_id']}_rep{r['rep']}"


def _case_id(r: dict) -> str:
    return r["prompt_id"] if int(r["rep"]) == 0 else f"{r['prompt_id']}~rep{r['rep']}"


def _next_step(pr: Proj, pub: Publication, c: dict, a) -> tuple:
    """(step, calls, items): the first unfinished step and the tool calls that finish it."""
    ids, ws, slug, rows, V = c["ids"], c["ws"], c["slug"], c["rows"], a.variant
    full = not a.sessions_only
    if full and not ids.get("scorer_id"):
        return "scorer", [_create("scorer", {
            "name": SCORER_NAME, "workspace_id": ws, "display_name": "Result-set match", "scorer_kind": "code",
            "scope": "result", "spec": {"type": "result_set_match", "source": "atlan-analytics/analytics_eval.py",
                                        "rules": "gold and agent SQL on the same warehouse; 4 significant figures; "
                                                 "column order/names ignored; row order only for ORDER BY … LIMIT; "
                                                 "extra columns allowed"},
            "outputs": {"result_match": {"type": "number", "range": [0, 1]}}}, _idem_key(ws, "scorer", SCORER_NAME))], [SCORER_NAME]
    if full and not ids.get("eval_dataset_id"):
        name = f"{slug}-eval"
        return "dataset", [_create("dataset", {
            "name": name, "workspace_id": ws, "display_name": f"{slug} eval", "input_key": "question",
            "description": "Talk-to-data questions. The trusted SQL is kept locally by the grader and is not "
                           "stored here, so the agent cannot retrieve it."}, _idem_key(ws, "dataset", name))], [name]
    if full:
        records = load_json(pr.p("eval", "records.json"), {}) or {}
        new = [cs for cs in pr.cases() if cs["case_id"] not in records][:BATCH]
        if new:
            gold = pr.answers()
            items = [{"name": cs["case_id"], "workspace_id": ws, "dataset_id": ids["eval_dataset_id"],
                      "input": {"question": cs["question"], "tags": cs.get("tags") or []},
                      "expected": {"gold_sql_sha256": hashlib.sha256(
                          ((gold.get(cs["case_id"]) or {}).get("gold_sql") or "").strip().encode()).hexdigest(),
                          "note": "gold SQL kept locally so the agent cannot retrieve it"},
                      "label": (cs.get("tags") or ["untagged"])[0], "categories": cs.get("tags") or [],
                      "source_kind": "manual"} for cs in new]
            return "records", [_create_many("dataset_record", items)], [cs["case_id"] for cs in new]
    exps = ids.get("experiments") or {}
    if full and not exps.get(V):
        name = f"{slug}-{V}"
        body = {"name": name, "workspace_id": ws, "display_name": f"{slug} · {V}",
                "description": (load_json(pr.vdir(V) / "summary.json", {}) or {}).get("description") or f"analytics eval {V}",
                "dataset_id": ids["eval_dataset_id"], "subject_kind": "agent", "subject_id": c["agent_id"],
                "experiment_status": "running",
                "config": {"variant": V, "runner": "atlan-analytics-eval", "grader": SCORER_NAME,
                           "harness_sha": harness_sha(), "traces": "sessions only"}}
        if a.baseline and exps.get(a.baseline):
            body["baseline_experiment_id"] = exps[a.baseline]
        return "experiment", [_create("experiment", body, _idem_key(ws, "experiment", name))], [name]
    todo = [r for r in rows if _tag(r) not in pub.s["sessions"]][:SESSION_BATCH]
    if todo:
        calls, names = [], []
        for r in todo:
            tag = _tag(r)
            conv = f"analytics-eval-{slug}-{V}-{tag}"
            turns = load_json(pr.vdir(V) / "traces" / f"{tag}.json", []) or []
            u = r.get("usage") or {}
            calls.append(_create("session", {
                "name": conv, "workspace_id": ws, "subject_kind": "agent", "subject_id": c["agent_id"],
                "session_status": "completed" if r.get("answer") else "failed", "external_session_id": conv,
                "model_id": r.get("model"), "title": ("[eval] " + r["prompt"])[:200],
                "message_count": len(session_messages(r, turns)),
                "stop_reason": "final_answer" if r.get("answer") else "no_answer",
                "usage": {"input_tokens": int(u.get("input_tokens") or 0),
                          "output_tokens": int(u.get("output_tokens") or 0)}}, _idem_key(ws, "session", conv)))
            names.append(tag)
        return "sessions", calls, names
    for r in rows:
        tag = _tag(r)
        sent = set(pub.s["messages"].get(tag) or [])
        turns = load_json(pr.vdir(V) / "traces" / f"{tag}.json", []) or []
        msgs = session_messages(r, turns)
        if len(sent) < len(msgs):
            sess = pub.s["sessions"][tag]
            items = [{"name": f"{sess['conversation_id']}-msg-{i}", "workspace_id": ws, "session_id": sess["id"],
                      "sequence_number": i, **m} for i, m in enumerate(msgs) if i not in sent][:BATCH]
            return "messages", [_create_many("session_message", items)], [f"{tag}#{it['sequence_number']}" for it in items]
    if not full:
        return "done", [], []
    eid = exps[V]
    records = load_json(pr.p("eval", "records.json"), {}) or {}
    todo = [r for r in rows if _case_id(r) not in pub.s["results"]][:BATCH]
    if todo:
        items = []
        for r in todo:
            cid = _case_id(r)
            items.append({"name": f"{slug}-{V}-{cid}", "workspace_id": ws, "experiment_id": eid, "case_id": cid,
                          **({"dataset_record_id": records[r["prompt_id"]]} if records.get(r["prompt_id"]) else {}),
                          "session_id": pub.s["sessions"][_tag(r)]["id"],
                          "input": {"question": r["prompt"]},
                          "output": {"answer": r.get("answer"), "sql": r.get("sql"), "class": r.get("class")},
                          "scores": {"result_match": float((r.get("grade") or {}).get("pass", 0))},
                          "duration_ms": int(float(r.get("latency_s") or 0) * 1000)})
        return "results", [_create_many("experiment_result", items)], [it["case_id"] for it in items]
    if not pub.s.get("completed"):
        s = summarize(pr, V)
        return "complete", [{"tool": "registry__patch_artifact", "arguments": {"artifact_id": eid, "patch": {
            "experiment_status": "completed", "summary": {
                "cases": s["cases"], "rows": s["rows"], "accuracy": s["all"]["pass_rate"],
                "train_accuracy": s["train"]["pass_rate"], "test_accuracy": s["test"]["pass_rate"],
                "test_ci95": s["test"]["ci95"], "classes": s["classes"], "models": s["models"], "variant": V,
                "scorer_id": ids["scorer_id"], "runner": "atlan-analytics-eval", "finished_at": now()}}}}], [eid]
    return "done", [], []


# Single creates carry idempotency keys and the completion patch is idempotent, so these can be resent.
RESENDABLE = ("scorer", "dataset", "experiment", "sessions", "complete")
STEP_KIND = {"scorer": "scorer", "dataset": "dataset", "records": "dataset_record", "experiment": "experiment",
             "sessions": "session", "messages": "session_message", "results": "experiment_result",
             "complete": "experiment"}


def cmd_publish(a) -> dict:
    """The next registry writes for this variant, as MCP tool calls. Run it again after each ack."""
    pr = Proj(a.dir)
    c = _context(pr, a)
    pub = Publication(pr, a.variant)
    pending = pub.s.get("pending")
    if pending and a.resend and pending["step"] in RESENDABLE:
        step, calls, names = _next_step(pr, pub, c, a)
        if step == pending["step"] and names == pending["names"]:
            return {"status": "ok", "step": step, "workspace_id": c["ws"], "calls": calls, "resent": True,
                    "next": f"Send each call again unchanged, then run "
                            f"`publish-ack --variant {a.variant} --step {step} --responses <file>`."}
    if pending:
        kind = STEP_KIND[pending["step"]]
        return {"status": "BLOCKED", "reason": f"The `{pending['step']}` step was sent but never acknowledged.",
                "fix": "Pass that step's tool results to `publish-ack`. If they are lost and the step is "
                       f"{', '.join(RESENDABLE)}, run `publish --resend`: its idempotency keys recover what "
                       "landed within 24 hours. Otherwise list what landed with "
                       f"`registry__list_artifacts` (kind `{kind}`, workspace `{c['ws']}`) and pass that result "
                       "with `publish-ack --reconcile`. Never resend a batch blindly: it duplicates what worked.",
                "pending": pending}
    step, calls, names = _next_step(pr, pub, c, a)
    if step == "done":
        exp = ((c["ids"].get("experiments") or {}).get(a.variant))
        return {"status": "ok", "step": "done", "experiment_id": exp, "sessions": len(pub.s["sessions"]),
                "results": len(pub.s["results"]), "traces": "sessions only: trace spans have no MCP write"}
    pub.s["pending"] = {"step": step, "names": names, "calls": len(calls), "at": now()}
    pub.save()
    return {"status": "ok", "step": step, "workspace_id": c["ws"], "calls": calls,
            "next": f"Send each call in order, save the results as a JSON list, then run "
                    f"`publish-ack --variant {a.variant} --step {step} --responses <file>`."}


def cmd_publish_ack(a) -> dict:
    """Record what the registry returned for the pending step; failed items are retried by the next `publish`."""
    pr = Proj(a.dir)
    pub = Publication(pr, a.variant)
    pending = pub.s.get("pending")
    if not pending or pending["step"] != a.step:
        raise Blocked(f"No `{a.step}` step is waiting for results.", "Run `publish` to see the next step.")
    raw = load_json(a.responses)
    if raw is None:
        raise Blocked("The responses file is missing or not JSON.", "Save the tool results as one JSON list.")
    kind, names = STEP_KIND[a.step], pending["names"]
    if a.reconcile:
        listed = {}
        for page in (raw if isinstance(raw, list) else [raw]):
            listed.update(_listed(page, kind))
        got = {n: listed.get(_reconcile_name(pr, a, a.step, n)) for n in names}
        failed = {}
    else:
        resps = raw if isinstance(raw, list) else [raw]
        if a.step in ("records", "messages", "results"):
            slots = _slots(resps[0] if resps else {})
            if len(slots) != len(names):
                raise Blocked(f"Expected {len(names)} result slots, got {len(slots)}.",
                              "Pass the create_artifacts result unchanged, or reconcile with --reconcile.")
            got = {n: _find_id(sl, kind) for n, sl in zip(names, slots)}
            failed = {n: _failed(sl) or "no id returned" for n, sl in zip(names, slots) if not got[n]}
        else:
            if len(resps) != len(names):
                raise Blocked(f"Expected {len(names)} results, got {len(resps)}.", "Pass one result per call, in order.")
            got = {n: _find_id(rp, kind) or (n if a.step == "complete" and not _failed(rp) else None)
                   for n, rp in zip(names, resps)}
            failed = {n: _failed(rp) or "no id returned" for n, rp in zip(names, resps) if not got[n]}
    _apply(pr, pub, a.variant, a.step, got)
    pub.s.pop("pending", None)
    pub.save()
    landed = sum(1 for v in got.values() if v)
    out = {"status": "ok" if not failed else "DONE_WITH_CONCERNS", "step": a.step, "landed": landed,
           "next": "Run `publish` again for the next step."}
    if failed:
        out["failed"] = dict(list(failed.items())[:10])
        out["fix"] = "The next `publish` resends only the items that failed. Fix the named field first if one is named."
    return out


def _reconcile_name(pr: Proj, a, step: str, n: str) -> str:
    """The artifact `name` a step gave item n, to match a listing against."""
    slug = pr.state().get("slug") or pr.dir.name
    if step == "sessions":
        return f"analytics-eval-{slug}-{a.variant}-{n}"
    if step == "messages":
        tag, seq = n.rsplit("#", 1)
        return f"analytics-eval-{slug}-{a.variant}-{tag}-msg-{seq}"
    if step == "results":
        return f"{slug}-{a.variant}-{n}"
    return n


def _apply(pr: Proj, pub: Publication, V: str, step: str, got: dict) -> None:
    ok = {n: v for n, v in got.items() if v}
    if not ok:
        return
    if step == "scorer":
        pr.set_id("scorer_id", next(iter(ok.values())))
    elif step == "dataset":
        pr.set_id("eval_dataset_id", next(iter(ok.values())))
    elif step == "experiment":
        pr.set_id(f"experiments.{V}", next(iter(ok.values())))
    elif step == "records":
        path = pr.p("eval", "records.json")
        rec = load_json(path, {}) or {}
        rec.update(ok)
        dump_json(path, rec)
    elif step == "sessions":
        slug = pr.state().get("slug") or pr.dir.name
        for tag, sid in ok.items():
            pub.s["sessions"][tag] = {"id": sid, "conversation_id": f"analytics-eval-{slug}-{V}-{tag}"}
    elif step == "messages":
        for n in ok:
            tag, seq = n.rsplit("#", 1)
            pub.s["messages"].setdefault(tag, [])
            if int(seq) not in pub.s["messages"][tag]:
                pub.s["messages"][tag].append(int(seq))
    elif step == "results":
        pub.s["results"].update(ok)
    elif step == "complete":
        pub.s["completed"] = True


# ====================================================================== selftest

def _selftest_publish(pr: pathlib.Path) -> bool:
    """Drive `publish` and `publish-ack` to done against a fake registry, with one failed slot and one lost step."""
    proj = Proj(str(pr))
    proj.set_id("workspace_id", "workspace_t")
    proj.set_id("agent_id", "agent_t")
    dump_json(pr / "eval" / "split.json", {"train_ids": [], "test_ids": ["c1"], "seed": 1})
    n = Counter()
    resp_path = pr / "resp.json"

    def made(kind):
        n[kind] += 1
        return {"artifact": {"id": f"{kind}_{n[kind]}", "name": "x"}}

    def ns(**kw):
        base = dict(dir=str(pr), variant="cited", baseline=None, agent_id=None, workspace=None,
                    sessions_only=False, resend=False, reconcile=False)
        return argparse.Namespace(**{**base, **kw})

    calls_seen, failed_once, lost_once = [], False, False
    for _ in range(40):
        out = cmd_publish(ns())
        if out.get("step") == "done":
            break
        if out.get("status") == "BLOCKED":
            return False
        step, resps = out["step"], []
        for call in out["calls"]:
            calls_seen.append(call["tool"])
            kind = call["arguments"].get("kind") or "experiment"
            if call["tool"] == "registry__create_artifacts":
                slots = [{"status": 201, **made(kind)} for _ in call["arguments"]["items"]]
                if step == "messages" and not failed_once:
                    failed_once, slots[0] = True, {"status": 422, "error": "bad field"}
                resps.append({"items": slots})
            elif call["tool"] == "registry__create_artifact":
                resps.append(made(kind))
            else:
                resps.append({"id": call["arguments"]["artifact_id"]})
        if step == "sessions" and not lost_once:
            lost_once = True
            if cmd_publish(ns()).get("status") != "BLOCKED":
                return False
            if not cmd_publish(ns(resend=True)).get("resent"):
                return False
        dump_json(resp_path, resps)
        cmd_publish_ack(ns(step=step, responses=str(resp_path)))
    pub = load_json(pr / "hillclimb" / "cited" / "publish.json")
    st = proj.state()["ids"]
    return (out.get("step") == "done" and failed_once and lost_once and pub.get("completed")
            and st.get("scorer_id") and st.get("eval_dataset_id") and st["experiments"]["cited"]
            and len(pub["results"]) == 1 and len(pub["sessions"]) == 1
            and all(t.startswith("registry__") for t in calls_seen))

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
    # The value registry-cli's identity.v1 traceId gives this session, so a proposal names the trace the desktop sync uploads.
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
        ok("publishing plans every write as MCP calls and reaches done", _selftest_publish(pr))
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

    c = s.add_parser("publish", help="the next registry writes for a variant, as MCP tool calls")
    c.add_argument("--dir", required=True)
    c.add_argument("--variant", required=True)
    c.add_argument("--baseline")
    c.add_argument("--agent-id")
    c.add_argument("--workspace")
    c.add_argument("--sessions-only", action="store_true",
                   help="sessions only (smoke runs): no dataset, experiment or results")
    c.add_argument("--resend", action="store_true",
                   help="repeat an unacknowledged step whose calls carry idempotency keys")
    c.set_defaults(func=cmd_publish)

    c = s.add_parser("publish-ack", help="record the registry's results for the pending publish step")
    c.add_argument("--dir", required=True)
    c.add_argument("--variant", required=True)
    c.add_argument("--step", required=True, choices=sorted(STEP_KIND))
    c.add_argument("--responses", required=True, help="a JSON list of the step's tool results, in call order")
    c.add_argument("--reconcile", action="store_true",
                   help="the responses are registry__list_artifacts results for a step whose results were lost")
    c.set_defaults(func=cmd_publish_ack)

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
