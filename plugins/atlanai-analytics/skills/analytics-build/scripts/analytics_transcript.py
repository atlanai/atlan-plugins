"""Normalize native Codex JSONL into the existing grader's message format.

Unknown executable events fail closed. Keep raw events on disk for audit; never
replace a missing transcript with a hand-authored assistant answer.
"""
import json
import re
import shlex


def read_command(command):
    """Recognize simple file reads; never execute or interpret arbitrary scripts."""
    try:
        args = shlex.split(command) if isinstance(command, str) else list(command)
        if len(args) == 3 and args[0] in ("/bin/sh", "/bin/bash", "/bin/zsh") and args[1] in ("-c", "-lc"):
            command = args[2]
            args = shlex.split(command)
        text = command if isinstance(command, str) else " ".join(command)
        # Separate shell operators while preserving quoted regex alternations.
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        shell_tokens = list(lexer)
        if not args or any(c in text for c in "\n\r`$") or any(
                token and all(c in ";&|<>" for c in token) for token in shell_tokens):
            return False
        name, rest = args[0], args[1:]
        if name in ("cat", "/bin/cat"):
            return bool(rest) and all(not x.startswith("-") or x == "--" for x in rest)
        if name == "sed":
            return len(rest) >= 3 and rest[0] == "-n" and bool(re.fullmatch(r"\d+(?:,\d+)?p", rest[1])) \
                and all(not x.startswith("-") for x in rest[2:])
        if name == "rg":
            allowed = {"-n", "-i", "-F", "-l", "--fixed-strings", "--files", "--files-with-matches", "--no-heading", "--"}
            index = 0
            while index < len(rest):
                token = rest[index]
                if token in ("-A", "-B", "-C", "--after-context", "--before-context", "--context"):
                    index += 1
                    if index >= len(rest) or not rest[index].isdigit():
                        return False
                elif token.startswith("-") and token not in allowed:
                    return False
                index += 1
            return bool(rest) and any(not x.startswith("-") for x in rest)
    except (ValueError, TypeError):
        pass
    return False


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
        if typ == "CommandExecution" and read_command(item.get("command")):
            return [call("codex_read_command", {"command": item["command"]}, item.get("id"))]
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
            name = payload.get("name", "unknown_tool")
            if name == "exec_command" and isinstance(args, dict) and read_command(args.get("cmd")):
                name = "codex_read_command"
            return [call(name, args, payload.get("call_id"))]
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
        if typ == "command_execution" and read_command(item.get("command")):
            return [call("codex_read_command", {"command": item["command"]}, item.get("id"))]
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
