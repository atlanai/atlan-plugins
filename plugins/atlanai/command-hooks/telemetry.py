#!/usr/bin/env python3
"""Usage telemetry for the atlanai command hooks (Codex, Cursor, Copilot). Standard library only.

    python3 telemetry.py send    # a detached child of publish_hook.py: POST one batch read on stdin

publish_hook.py calls `observe` after it has answered the harness. It is claude-hooks/telemetry.ts
for the harnesses that run commands, and both are held to command-hooks/telemetry_events.json:
an event or property that file does not list is never sent, and a value that does not fit its
type drops the whole event. So no path, prompt, tool argument, result or error text can leave,
whatever a call site passes.

Nothing is sent unless every one of these holds:

- `[telemetry] write_key` is set in plugin.toml (telemetry_config.json, written by the build), as the
  dashboard's analytics load only with a Segment write key;
- none of DO_NOT_TRACK, DISABLE_TELEMETRY, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is 1 or true,
  ATLAN_PLUGIN_TELEMETRY is not 0, false or off, and CI is unset;
- the file `telemetry-off` is absent from the state folder (for apps that pass a hook no environment);
- this copy of the plugin is the released one: its name and MCP URL are the build's own, so a
  copy another environment serves stays silent.

The state folder is `$XDG_CONFIG_HOME/atlan-plugin` or `~/.config/atlan-plugin`. It holds a
random install id and the last day each harness was reported active, and nothing else.

The batch goes out in a detached child with a 3 second limit, so a hook never waits on the
network. Every fault here is swallowed: telemetry never changes what a hook answers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
SCHEMA = HERE / "telemetry_events.json"
CONFIG = HERE / "telemetry_config.json"
STATE_FOLDER = "atlan-plugin"
OPT_OUT_FILE = "telemetry-off"
SEND_TIMEOUT_SECONDS = 3
MAX_BODY_BYTES = 32 * 1024
OFF_FLAGS = ("DO_NOT_TRACK", "DISABLE_TELEMETRY", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
OWN_SWITCH = "ATLAN_PLUGIN_TELEMETRY"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MCP_SESSION = re.compile(r"^mcp_session_[0-9A-Za-z]{8,64}$")
RELEASED = re.compile(r"is at (\d+\.\d+\.\d+) and (\d+\.\d+\.\d+) is released")
RULE = re.compile(r"(?:^|:\s)([a-z]+(?:-[a-z]+)*):\s")
PUBLISH = "extension__skill__create"
BULK_PUBLISH = "registry__create_artifacts"
OUTPUT = re.compile(r"^registry__publish_output$|^extension__[a-z0-9_]+__publish_output$")
# check_bundle.py's rule names, folded to the schema's `refusal` values.
RULE_CLASS = {
    **{rule: rule for rule in (
        "files-from", "symbolic-link", "credential-folder", "credential-file", "credential", "unsafe-path",
        "not-a-file", "too-many-files", "too-large", "missing-skill-md", "frontmatter", "not-text")},
    "frontmatter-name": "frontmatter", "frontmatter-description": "frontmatter", "frontmatter-yaml": "frontmatter",
    "no-body": "frontmatter",
    **{rule: "hidden-text" for rule in (
        "tag-character", "variation-selector", "zero-width-joiner", "direction-control", "zero-width",
        "line-separator", "control-character")},
}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def is_on(config: dict) -> bool:
    """On whenever the build carries a write key and an HTTPS endpoint, as the dashboard's is."""
    return str(config.get("endpoint", "")).startswith("https://") and re.fullmatch(r"[A-Za-z0-9]{8,64}", str(config.get("write_key", ""))) is not None


def is_flag_on(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in ("1", "true")


def state_dir(env) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / STATE_FOLDER


def is_opted_out(env, state: Path) -> bool:
    if any(is_flag_on(env.get(name)) for name in OFF_FLAGS):
        return True
    if str(env.get(OWN_SWITCH, "")).strip().lower() in ("0", "false", "off"):
        return True
    if env.get("CI"):
        return True
    return os.path.lexists(state / OPT_OUT_FILE)


def is_released_copy(config: dict, root: Path = PLUGIN_ROOT) -> bool:
    """Whether this folder is the build's own plugin, talking to the build's own server."""
    try:
        if read_json(root / "plugin.json").get("name") != config.get("plugin"):
            return False
        servers = read_json(root / "mcp.json").get("mcpServers", {})
        return isinstance(servers, dict) and any(
            isinstance(server, dict) and server.get("url") == config.get("mcp_url") for server in servers.values())
    except Exception:  # noqa: BLE001 - an unreadable manifest is not the released copy
        return False


def read_state(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64:
            return None
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def write_state(path: Path, text: str) -> None:
    """A private file in a private folder, never through a link."""
    folder = path.parent
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink():
        raise OSError("the state folder is a link")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as handle:
        handle.write(text)


def install(state: Path) -> str:
    """This machine's random install id, made on first use."""
    found = read_state(state / "install-id")
    if found and UUID.match(found):
        return found
    made = str(uuid.uuid4())
    write_state(state / "install-id", made)
    return made


def mark_active(state: Path, harness: str, today: str) -> tuple[bool, bool]:
    """(first report today, first report ever) for this harness; the day is kept for the next call."""
    stamp = state / f"active-{harness}"
    last = read_state(stamp)
    if last == today:
        return False, False
    write_state(stamp, today)
    return True, last is None or not DAY.match(last)


def os_name(platform: str = sys.platform) -> str:
    return "macos" if platform == "darwin" else "windows" if platform.startswith("win") else "linux" if platform.startswith("linux") else "unknown"


def fits(value, spec: dict, schema: dict, config: dict) -> bool:
    kind = spec.get("type")
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "version":
        return isinstance(value, str) and VERSION.match(value) is not None
    if kind == "enum":
        return isinstance(value, str) and value in schema.get("enums", {}).get(spec.get("of"), [])
    if kind == "mcp_session":
        return isinstance(value, str) and MCP_SESSION.match(value) is not None
    if kind == "listed":
        return isinstance(value, str) and value in config.get(spec.get("of"), [])
    return False


def compose(name: str, props: dict, common: dict, schema: dict, config: dict, at: str, message_id: str) -> dict | None:
    """One Segment `track` message, or None when the event or any value is outside the schema."""
    event = schema.get("events", {}).get(name)
    harness = common.get("harness")
    if not isinstance(event, dict) or harness not in event.get("hosts", []):
        return None
    if not UUID.match(str(common.get("install_id", ""))) or not VERSION.match(str(common.get("app_version", ""))):
        return None
    if harness not in schema["enums"]["harness"] or common.get("os") not in schema["enums"]["os"]:
        return None
    kept = {}
    for key, spec in event.get("props", {}).items():
        if key not in props or props[key] is None:
            if spec.get("optional"):
                continue
            return None
        if not fits(props[key], spec, schema, config):
            return None
        kept[key] = props[key]
    category = schema["category"]
    sep = name.find("_")
    properties = {
        "category": category,
        "object": name if sep < 0 else name[:sep],
        "action": "" if sep < 0 else name[sep + 1:],
        "app_version": common["app_version"],
        "plugin": config["plugin"],
        "harness": harness,
        "os": common["os"],
        "install_id": common["install_id"],
        **({"session": common["session"]} if re.fullmatch(r"[0-9a-f]{16}", str(common.get("session", ""))) else {}),
        **kept,
    }
    return {
        "type": "track",
        "event": f"{category}_{name}",
        "anonymousId": common["install_id"],
        "messageId": message_id,
        "timestamp": at,
        "properties": properties,
        # Segment records the sender's address unless the message names one.
        "context": {"ip": "0.0.0.0", "library": {"name": "atlan-plugin", "version": common["app_version"]}},
    }


def session_key(install_id: str, session) -> str | None:
    if not isinstance(session, str) or not session:
        return None
    return hashlib.sha256(f"{install_id}{session}".encode()).hexdigest()[:16]


def failure_reason(text: str) -> str:
    """An error's text as one of the schema's `failure` values. The text itself goes nowhere."""
    if re.search(r"\b401\b|authentication required|needs login|unauthori[sz]ed", text, re.IGNORECASE):
        return "auth"
    if re.search(r"\b403\b|forbidden|permission[ _]denied|not[ _]allowed", text, re.IGNORECASE):
        return "permission_denied"
    if re.search(r"\b404\b|not[ _]found", text, re.IGNORECASE):
        return "not_found"
    if re.search(r"\b409\b|conflict", text, re.IGNORECASE):
        return "conflict"
    if re.search(r"\b429\b|rate.?limit", text, re.IGNORECASE):
        return "rate_limited"
    if re.search(r"timed?[ _]?out|\b504\b", text, re.IGNORECASE):
        return "timeout"
    if re.search(r"\b5\d\d\b|bad gateway|service unavailable|downstream_unavailable", text, re.IGNORECASE):
        return "server_error"
    if re.search(r"\b4\d\d\b|invalid|-3260[0-3]", text, re.IGNORECASE):
        return "client_error"
    return "unknown"


def operation_of(name) -> str | None:
    if name == PUBLISH:
        return "skill_create"
    if name == BULK_PUBLISH:
        return "bulk_create"
    if isinstance(name, str) and OUTPUT.match(name):
        return "output"
    return None


def refusal_of(text, operation: str) -> str:
    """Why the helper held a call back, as one of the schema's `refusal` values."""
    text = text if isinstance(text, str) else ""
    for line in text.splitlines()[:1]:
        for match in RULE.finditer(line):
            if match.group(1) in RULE_CLASS:
                return RULE_CLASS[match.group(1)]
    if re.search(r"\breceipt\b", text, re.IGNORECASE):
        return "receipt"
    if operation == "bulk_create":
        return "local-batch"
    return "other"


def call_of(event: dict) -> dict:
    """The gateway call a hook event carries, under either harness spelling, or {}."""
    for key in ("tool_input", "toolArgs"):
        value = event.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            return value
    return {}


def inner_name(event: dict):
    name = call_of(event).get("name")
    return name if isinstance(name, str) else None


def note_of(result: dict) -> str:
    """The context a hook answer adds beside the model's input, under any harness's key."""
    specific = result.get("hookSpecificOutput") if isinstance(result.get("hookSpecificOutput"), dict) else {}
    for value in (specific.get("additionalContext"), result.get("additional_context"), result.get("additionalContext")):
        if isinstance(value, str):
            return value
    return ""


def decision_of(result: dict) -> tuple[str | None, str]:
    """('refused', why), ('prepared', '') or (None, '') for what a pre answer did to the call."""
    specific = result.get("hookSpecificOutput") if isinstance(result.get("hookSpecificOutput"), dict) else {}
    if "deny" in (specific.get("permissionDecision"), result.get("permission"), result.get("permissionDecision")):
        why = specific.get("permissionDecisionReason") or result.get("agent_message") or result.get("permissionDecisionReason") or ""
        return "refused", why
    if "updatedInput" in specific or "updated_input" in result or "modifiedArgs" in result:
        return "prepared", ""
    return None, ""


def events_of(entry: str, event: dict, result, first_seen: bool, is_new_day: bool, installed) -> list[tuple[str, dict]]:
    """What one hook run reports, as (name, properties). Reads the answer, never the call's contents."""
    found: list[tuple[str, dict]] = []
    result = result if isinstance(result, dict) else {}
    if entry == "session-start":
        if is_new_day:
            found.append(("install_active", {"first_seen": first_seen}))
        released = RELEASED.search(note_of(result))
        if released and released.group(1) == installed:
            found.append(("update_prompted", {"from_version": released.group(1), "to_version": released.group(2), "forced": False}))
    elif entry == "pre":
        operation = operation_of(inner_name(event))
        decision, why = decision_of(result)
        # The gateway's own session id, when the call names one that reads as one: the join to its records.
        session = call_of(event).get("session_id")
        joined = {"mcp_session_id": session} if isinstance(session, str) and MCP_SESSION.match(session) else {}
        if operation and decision == "refused":
            found.append(("publish_refused", {"operation": operation, "reason": refusal_of(why, operation), **joined}))
        elif operation and operation != "output" and decision == "prepared":
            found.append(("publish_prepared", {"operation": operation, **joined}))
    return found


def send_detached(body: str) -> None:
    """Hand the batch to a child that outlives this hook and holds none of its pipes."""
    options = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "send"], **options)  # noqa: S603 - argv list, own script
    child.stdin.write(body.encode("utf-8"))
    child.stdin.close()


def observe(entry: str, host: str, event: dict, result, env=None, spawn=send_detached, now=None, root: Path = PLUGIN_ROOT,
            config: dict | None = None) -> str | None:
    """Report what one hook run did. Returns the batch sent, or None. Never raises."""
    try:
        env = os.environ if env is None else env
        config = read_json(CONFIG) if config is None else config
        if not is_on(config) or entry not in ("session-start", "pre"):
            return None
        schema = read_json(SCHEMA)
        if host not in schema["enums"]["harness"]:
            return None
        state = state_dir(env)
        if is_opted_out(env, state) or not is_released_copy(config, root):
            return None
        moment = time.gmtime(now)
        install_id = install(state)
        is_new_day, first_seen = mark_active(state, host, time.strftime("%Y-%m-%d", moment)) if entry == "session-start" else (False, False)
        found = events_of(entry, event, result, first_seen, is_new_day, config.get("version"))
        session = session_key(install_id, event.get("session_id") or event.get("sessionId") or event.get("conversation_id"))
        common = {"harness": host, "os": os_name(), "install_id": install_id, "app_version": config.get("version"),
                  **({"session": session} if session else {})}
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", moment)
        batch = [m for m in (compose(name, props, common, schema, config, at, str(uuid.uuid4())) for name, props in found) if m]
        if not batch:
            return None
        body = json.dumps({"batch": batch, "writeKey": config["write_key"]})
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            return None
        spawn(body)
        return body
    except Exception:  # noqa: BLE001 - telemetry never changes what a hook does
        return None


def send(body: str, config: dict | None = None, post=None) -> bool:
    """POST one batch to the build's endpoint. The endpoint comes from the build, never from the caller."""
    config = read_json(CONFIG) if config is None else config
    endpoint = str(config.get("endpoint", ""))
    if not is_on(config) or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        return False
    if not isinstance(json.loads(body).get("batch"), list):
        return False
    request = urllib.request.Request(endpoint, data=body.encode("utf-8"), method="POST",  # noqa: S310 - https, from the build
                                     headers={"Content-Type": "application/json", "User-Agent": f"atlan-plugin/{config.get('version', '')}"})
    if post is not None:
        return post(request)
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:  # noqa: S310
        return 200 <= response.status < 300


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["send"]:
            send(sys.stdin.read(MAX_BODY_BYTES + 1))
    except Exception:  # noqa: BLE001 - a batch that cannot be sent is dropped
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
