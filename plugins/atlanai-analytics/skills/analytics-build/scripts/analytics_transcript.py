"""Normalize native Codex JSONL into the existing grader's message format.

Unknown executable events fail closed. Keep raw events on disk for audit; never
replace a missing transcript with a hand-authored assistant answer.
"""
import json


def normalize(obj):
    if not isinstance(obj, dict):
        return []
    kind = obj.get("type")
    payload = obj.get("payload") or {}
    stamp = obj.get("timestamp")

    def row(role, blocks):
        return {"timestamp": stamp, "message": {"role": role, "content": blocks}}

    def call(name, args, ident):
        return row("assistant", [{"type": "tool_use", "id": ident, "name": name, "input": args}])

    if kind == "event_msg" and payload.get("type") == "item_completed":
        item = payload.get("item") or {}
        typ = item.get("type")
        if typ == "AgentMessage":
            return [row("assistant", [{"type": "text", "text": b.get("text", "")}
                        for b in item.get("content", [])
                        if b.get("type") in ("Text", "text", "output_text", "input_text")])]
        if typ == "McpToolCall":
            return normalize({"type": "item.completed", "timestamp": stamp,
                              "item": {**item, "type": "mcp_tool_call"}})
        if typ not in ("Reasoning", "Plan", "TodoList"):
            return [call("unsupported_codex_execution", item, item.get("id"))]
        return []

    if kind == "response_item":
        if payload.get("type") == "message":
            return [row(payload.get("role"), [{"type": "text", "text": b.get("text", "")}
                        for b in payload.get("content", []) if b.get("type") in ("output_text", "input_text")])]
        if payload.get("type") == "function_call":
            try:
                args = json.loads(payload.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {"unparsed_arguments": payload.get("arguments")}
            return [call(payload.get("name", "unknown_tool"), args, payload.get("call_id"))]
        if payload.get("type") == "function_call_output":
            return [row("user", [{"type": "tool_result", "tool_use_id": payload.get("call_id"),
                                  "content": payload.get("output", "")}])]
        # Outputs and tool declarations do not execute code. The corresponding
        # custom call still fails closed when its execution cannot be audited.
        if payload.get("type") in ("custom_tool_call_output", "additional_tools"):
            return []
        if payload.get("type") == "agent_message":
            return [row("user", [{"type": "text", "text": b.get("text", "")}
                        for b in payload.get("content", []) if b.get("type") == "input_text"])]
    if kind == "item.completed":
        item = obj.get("item") or {}
        typ = item.get("type")
        if typ == "agent_message":
            return [row("assistant", [{"type": "text", "text": item.get("text", "")}])]
        if typ == "mcp_tool_call":
            name = item.get("tool") or item.get("name") or "unknown_tool"
            result = item.get("result")
            failed = bool(item.get("error")) or item.get("status") in ("failed", "Failed") \
                or (isinstance(result, dict) and bool(result.get("isError")))
            return [call(name, item.get("arguments") or {}, item.get("id")),
                    row("user", [{"type": "tool_result", "tool_use_id": item.get("id"),
                                   "content": json.dumps(result), "is_error": failed}])]
        if typ not in ("reasoning", "todo_list", "plan", "error"):
            return [call("unsupported_codex_execution", item, item.get("id"))]
    if kind == "response_item" and payload.get("type") not in ("reasoning", "message"):
        return [call("unsupported_codex_execution", payload, payload.get("call_id"))]
    return [obj] if kind not in ("response_item", "item.started", "item.updated") else []
