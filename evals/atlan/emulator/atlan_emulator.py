#!/usr/bin/env python3
"""A stdio stand-in for Atlan's MCP gateway in search mode, for the `atlanai` plugin's evals.

`claude plugin eval` mocks cannot name a tool with `__` in it, and every gateway tool has one
(`mcp__call_tool`, `registry__get_artifact`), so the eval runner points the plugin's `atlan`
server at this file instead. It serves the three search-mode tools with the live gateway's
schemas and refusals, over one fixed organization (`world.json`) built from what the
production registry returned on 30 October 2026: duplicate skill names, a skill held for
review, a blocked one, archived workspaces, a Claw-only session reader, an unconnected
Linear server and a lookalike `create_issue` tool on another server.

Standard library only. Every request and reply is appended to $ATLAN_EMULATOR_LOG when set.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORLD = json.loads((HERE / "world.json").read_text(encoding="utf-8"))
SESSION = "mcp_session_01evalsession00000000000000"
LOG = os.environ.get("ATLAN_EMULATOR_LOG")

STOP = set("a an and the of for to in on my our me i we is are be it this that with from by at as do".split())

META_TOOLS = [
    {
        "name": "mcp__search_tools",
        "description": (
            "Find the tools you can call for a task. Skip this when you already know a tool's exact name "
            "and input schema: run it with `mcp__call_tool`. Send `query`: what you need right now, in one "
            "sentence, naming the app or product if you know it, rather than bare keywords. Your first search, "
            "the one without a `session_id`, must also send `objective`: what the whole session is for. It "
            "starts your session and returns a `session_id`; pass that on every later search and call. The "
            "result lists the best matches with their exact names, descriptions and input schemas. Run a match "
            "with `mcp__call_tool`. Servers you have not connected yet come back as connect prompts, not as "
            "tools: ask for one with `mcp__call_tool`, `name` `mcp__connect_server` and `arguments` "
            "`{\"server\": \"<server>\"}`."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "objective": {"type": "string"},
                "session_id": {"type": "string"},
                "servers": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "detail": {"type": "string", "enum": ["schema", "summary"]},
            },
        },
    },
    {
        "name": "mcp__describe_tools",
        "description": (
            "Get the full definitions of tools you already know by name, for example from a prompt or an "
            "earlier search, or of every tool on the servers you list. Pass `names`, `servers`, or both. Run "
            "them with `mcp__call_tool`."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
                "servers": {"type": "array", "items": {"type": "string"}},
                "session_id": {"type": "string"},
            },
        },
    },
    {
        "name": "mcp__call_tool",
        "description": (
            "Run a tool whose exact name and input schema you already know, or discover it with "
            "`mcp__search_tools` / `mcp__describe_tools` first. Pass its exact `name` and matching `arguments`; "
            "never invent either. Pass top-level `session_id` when available. Otherwise, on the first call "
            "supply top-level `objective` (else your `rationale` is used) to open a session without discovery; "
            "the result adds an `mcp_session_id` receipt to reuse as `session_id`. Do not repeat a "
            "side-effecting call to obtain a receipt. Put `rationale` at the top level too. This runs the tool "
            "with the same permission checks as calling it directly. Its side effects are the tool's own and "
            "may create, change, send or delete data."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "arguments", "rationale"],
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
                "objective": {"type": ["string", "null"]},
                "rationale": {"type": "string"},
                "session_id": {"type": "string"},
            },
        },
    },
]


class Refusal(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.body = {"status": status, "code": code, "detail": detail}


state = {"session": False}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


def tool_server(name: str) -> str:
    return name.rsplit("__", 1)[0]


def public(tool: dict, detail: str = "schema") -> dict:
    out = {"name": tool["name"], "server": tool_server(tool["name"]), "description": tool["description"],
           "annotations": tool.get("annotations", {})}
    if detail == "schema":
        out["input_schema"] = tool["input_schema"]
    return out


def open_session(args: dict, *, objective_key: str = "objective") -> str | None:
    """The session rules the gateway enforces on every meta-tool."""
    given = args.get("session_id")
    if given:
        if given != SESSION:
            raise Refusal(403, "session_unknown", "That session_id names no session of yours. Use the one this connection returned.")
        return None
    if args.get(objective_key) or args.get("rationale"):
        state["session"] = True
        return SESSION
    raise Refusal(400, "objective_required", "Send `objective` on the first search or call to open a session, or pass your `session_id`.")


def search_tools(args: dict) -> dict:
    new = open_session(args)
    query = words(args.get("query", ""))
    servers = set(args.get("servers") or [])
    scored = []
    for tool in WORLD["tools"]:
        server = tool_server(tool["name"])
        if servers and server not in servers:
            continue
        score = len(query & words(tool["name"].replace("_", " ") + " " + tool["description"]))
        if score:
            scored.append((score, tool["name"], tool))
    scored.sort(key=lambda item: (-item[0], item[1]))
    limit = args.get("limit") or 5
    result = {"session_id": new or args["session_id"],
              "tools": [public(t, args.get("detail", "schema")) for _, _, t in scored[:limit]]}
    connect = [c for c in WORLD["connect"] if words(" ".join(c["keywords"])) & query and (not servers or c["server"] in servers)]
    if connect:
        result["connect"] = [{k: v for k, v in c.items() if k != "keywords"} | {
            "note": "Ask the user to connect their account for this server with `mcp__call_tool`, `name` "
                    "`mcp__connect_server` and `arguments` `{\"server\": \"<server>\"}`, then search again."} for c in connect]
    if not result["tools"] and not connect:
        result["next"] = "No tool you can call matched. Search again with different words."
    else:
        result["next"] = ("Run a tool with `mcp__call_tool`, passing its `name` and `arguments`, and this "
                          "`session_id` (and a `rationale`) beside them at the top level.")
    return result


def describe_tools(args: dict) -> dict:
    new = open_session(args) if args.get("session_id") else None
    names = set(args.get("names") or [])
    servers = set(args.get("servers") or [])
    if not names and not servers:
        raise Refusal(400, "invalid_arguments", "Pass `names`, `servers`, or both.")
    tools = [public(t) for t in WORLD["tools"]
             if (names and t["name"] in names) or (servers and tool_server(t["name"]) in servers)]
    out = {"tools": tools}
    if new or args.get("session_id"):
        out["session_id"] = new or args["session_id"]
    return out


def artifact(handle: str) -> dict:
    found = WORLD["artifacts"].get(handle)
    if not found:
        raise Refusal(404, "not_found", "No such resource, or it is not visible to you.")
    return found


def search_artifacts(a: dict) -> dict:
    query = words(a.get("query", ""))
    hits = []
    for item in WORLD["artifacts"].values():
        if a.get("kind") and item["kind"] != a["kind"]:
            continue
        if not a.get("kind") and item["kind"] in ("file", "session"):
            continue
        if a.get("workspace_id") and item["workspace_id"] != a["workspace_id"]:
            continue
        score = len(query & words(" ".join(str(item.get(k, "")) for k in ("name", "display_name", "description"))))
        if score:
            hits.append((score, item))
    hits.sort(key=lambda h: -h[0])
    keep = ("id", "kind", "name", "display_name", "description", "workspace_id", "artifact_status", "is_released",
            "version_ordinal", "updated_at")
    return {"items": [{k: h.get(k) for k in keep if k in h} | {"score": s / 100} for s, h in hits[: a.get("limit", 50)]],
            "page": {"limit": a.get("limit", 50), "offset": 0}}


def file_content(a: dict) -> dict:
    found = WORLD["files"].get(a.get("file_id", ""))
    if not found or found.get("withheld"):
        raise Refusal(404, "not_found", "No such resource, or it is not visible to you.")
    if found.get("binary"):
        raise Refusal(422, "not_text", f"{found['path']} is {found['size_bytes']} bytes of {found['media_type']}, not UTF-8 "
                      "text. Read it over HTTP: GET /file/v1/files/{file_id}/content.")
    return {"file_id": a["file_id"], "path": found["path"], "media_type": found["media_type"],
            "version_ordinal": a.get("version_ordinal", 1), "content": found["content"]}


def write(kind: str, a: dict) -> dict:
    return {"status": "created", "kind": kind, "id": f"{kind}_01evalnew0000000000000000", "note": "eval write recorded"}


def dispatch(name: str, a: dict) -> dict:
    tool = next((t for t in WORLD["tools"] if t["name"] == name), None)
    if name == "mcp__connect_server":
        server = a.get("server")
        if server not in {c["server"] for c in WORLD["connect"]}:
            raise Refusal(404, "not_found", f"No server named {server!r} needs connecting.")
        return {"state": "pending_user", "sign_in_url": f"https://app.atlan.com/connect/{server}",
                "note": "Show the user this sign-in link; search again once they say they have connected."}
    if tool is None:
        raise Refusal(404, "tool_unavailable", f"No tool named {name!r} for this connection. Search for it with mcp__search_tools.")
    allowed = set(tool["input_schema"].get("properties", {}))
    unknown = set(a) - allowed
    if unknown:
        raise Refusal(400, "invalid_arguments", f"{name} does not take {', '.join(sorted(unknown))}. "
                      "session_id and rationale go beside name and arguments, not inside them.")
    missing = [r for r in tool["input_schema"].get("required", []) if r not in a]
    if missing:
        raise Refusal(400, "invalid_arguments", f"{name} requires {', '.join(missing)}.")
    if "refuse" in tool:
        raise Refusal(**tool["refuse"])
    if name == "registry__list_workspaces":
        return {"items": WORLD["workspaces"], "page": {"limit": a.get("limit", 50), "offset": 0, "total": len(WORLD["workspaces"])}}
    if name == "registry__search_artifacts":
        return search_artifacts(a)
    if name == "registry__list_artifacts":
        items = [i for i in WORLD["artifacts"].values() if (not a.get("kind") or i["kind"] == a["kind"]) and i["kind"] not in ("session",)]
        return {"items": items[: a.get("limit", 50)], "page": {"limit": a.get("limit", 50), "offset": 0, "total": len(items)}}
    if name == "registry__get_artifact":
        return artifact(a["artifact_id"])
    if name == "registry__get_related_artifacts":
        artifact(a["artifact_id"])
        return {"items": WORLD["edges"].get(a["artifact_id"], [])}
    if name == "registry__get_kind_schema":
        if a["kind"] not in WORLD["kinds"]:
            raise Refusal(404, "not_found", f"No registered kind {a['kind']!r}.")
        return WORLD["kinds"][a["kind"]]
    if name == "registry__get_file_content":
        return file_content(a)
    if name in ("extension__agent__list_session_traces",):
        return WORLD["traces"].get(a["session_id"], {"items": [], "page": {"limit": 50}})
    if name in ("extension__agent__get_trace", "registry__get_trace"):
        run = visible_run(name, a)
        out = {k: v for k, v in run.items() if k not in ("content", "spans", "session_id")}
        if "content" in (a.get("include") or []):
            out |= run["content"]
        return out
    if name in ("extension__agent__get_trace_spans", "registry__get_trace_spans"):
        run = visible_run(name, a)
        spans = run["spans"]
        if a.get("names"):
            spans = [s for s in spans if any(n in s["name"] for n in a["names"])]
        if a.get("errors_only"):
            spans = [s for s in spans if s.get("span_status") == "error"]
        include = a.get("include") or []
        # As app-agent: `content` adds input and output, `errors` adds the error message.
        hidden = {"content", "error_message"} - {"content" if "content" in include else "", "error_message" if "errors" in include else ""}
        spans = [{k: v for k, v in s.items() if k not in hidden} for s in spans]
        return {"items": spans[: a.get("limit", 20)], "page": {"limit": a.get("limit", 20), "total_spans": run["span_count"]}}
    if tool["annotations"].get("readOnlyHint"):
        return tool.get("result", {"items": []})
    return write(tool.get("writes", name), a)


def visible_run(name: str, a: dict) -> dict:
    """The run `trace_id` names, scoped as app-agent scopes it: by the agent or harness that ran
    it, or by the `session_…` handle `list_session_traces` listed it under."""
    run = WORLD["runs"].get(a["trace_id"])
    scope = a.get("subject_id")
    if not run or (name.startswith("extension") and scope not in (run["subject_id"], run.get("session_id"))):
        raise Refusal(404, "not_found", "No such resource, or it is not visible to you.")
    return run


def call_tool(args: dict) -> dict:
    if not args.get("rationale"):
        raise Refusal(400, "rationale_required", "Add `rationale` beside `name`: one first-person sentence on why you are making this call.")
    new = open_session(args)
    result = dispatch(args["name"], args.get("arguments") or {})
    if new:
        result = {"mcp_session_id": new, "result": result}
    return result


HANDLERS = {"mcp__search_tools": search_tools, "mcp__describe_tools": describe_tools, "mcp__call_tool": call_tool}


def log(entry: dict) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def handle(msg: dict) -> dict | None:
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
        return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "atlan-eval-emulator", "version": "1"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": META_TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        handler = HANDLERS.get(params.get("name"))
        if handler is None:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "tool is unavailable; refresh tools/list"}}
        try:
            body, error = handler(params.get("arguments") or {}), False
        except Refusal as refusal:
            body, error = refusal.body, True
        except (KeyError, TypeError) as exc:
            body, error = {"status": 400, "code": "invalid_arguments", "detail": f"malformed arguments: {exc}"}, True
        log({"tool": params.get("name"), "arguments": params.get("arguments"), "error": error, "result": body})
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": json.dumps(body)}], "isError": error}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        reply = handle(json.loads(line))
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
