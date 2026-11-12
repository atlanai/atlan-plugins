#!/usr/bin/env python3
"""Compose a Metrics app proof URL from verified, non-secret connection metadata."""

import argparse
import json
import subprocess
import re
from urllib.parse import quote, urlencode, urlsplit


def definition_link(origin: str, org: str, kind: str, handle: str, workspace: str) -> str:
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or any(c.isspace() for c in origin)
            or any(c in origin for c in "\\<>\"")):
        raise ValueError("Use the verified HTTPS dashboard origin without a path or credentials")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", org):
        raise ValueError("Use the organization's verified URL slug")
    routes = {"metric": ("metric_", "/m/"), "sql_query": ("sql_query_", "/queries/")}
    if kind not in routes:
        raise ValueError("Expected metric or sql_query")
    prefix, route = routes[kind]
    if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]+", handle):
        raise ValueError("The artifact handle must match its kind")
    if not re.fullmatch(r"workspace_[A-Za-z0-9_-]+", workspace):
        raise ValueError("Use the artifact's workspace handle")
    inner = route + quote(handle, safe="") + "?" + urlencode({"workspace": workspace})
    outer = urlencode({"path": inner, "ws": workspace}, quote_via=quote)
    return origin.rstrip("/") + "/" + quote(org, safe="") + "/apps/metric?" + outer


class CliReadError(ValueError):
    """A failed CLI read. Its message stays generic; `code` is the gateway's error code only."""

    def __init__(self, code: str):
        super().__init__("The authenticated Atlan metadata read failed")
        self.code = code


def _cli_json(arguments):
    """Let the approved CLI own authentication; retain only selected public metadata."""
    try:
        result = subprocess.run(["atlanai", *arguments], capture_output=True, text=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("The authenticated Atlan CLI is unavailable") from exc
    # Never echo transport stderr or a response body into a user-facing error.
    if result.returncode == 4:
        raise ValueError("The Atlan CLI sign-in has expired; run `atlanai auth login`")
    if result.returncode:
        # Keep only the gateway's error code: a not_found and an auth failure need different next steps.
        code = re.search(r"\(HTTP \d{3}, ([a-z_]+)\)", result.stderr or "")
        raise CliReadError(code.group(1) if code else "read_failed")
    try:
        value = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Atlan returned invalid metadata") from exc
    if not isinstance(value, dict):
        raise ValueError("Atlan returned invalid metadata")
    return value


def resolve_link(gateway: str, origin: str, kind: str, handle: str, workspace: str) -> str:
    return _resolve_link(gateway, origin, kind, handle, workspace)


def _resolve_link(gateway: str, origin: str, kind: str, handle: str, workspace: str) -> str:
    # Validate all URL/path components before invoking the CLI.
    definition_link(origin, "validated-later", kind, handle, workspace)
    definition_link(gateway, "validated-later", kind, handle, workspace)
    status_args = ["auth", "status", "--json", "state,gateway,orgID"]
    status = _cli_json(status_args)
    if status.get("state") != "authenticated" or not status.get("orgID"):
        raise ValueError("The Atlan CLI is not signed in to an organization; run `atlanai auth login`")
    if status.get("gateway", "").rstrip("/") != gateway.rstrip("/"):
        raise ValueError("The CLI connection does not match the MCP gateway")
    identity = _cli_json([
        "api", "get", "registry:/users/me", "--jq",
        "{org_id, orgs: [.orgs[] | {id, slug}]}",
    ])
    org_id = identity.get("org_id")
    orgs = identity.get("orgs")
    if org_id != status["orgID"] or not isinstance(orgs, list):
        raise ValueError("The active organization could not be verified")
    matches = [org for org in orgs if isinstance(org, dict) and org.get("id") == org_id]
    if len(matches) != 1 or not isinstance(matches[0].get("slug"), str):
        raise ValueError("The active organization has no unambiguous slug")
    link = definition_link(origin, matches[0]["slug"], kind, handle, workspace)
    try:
        artifact = _cli_json([
            "api", "get", "registry:/artifacts/" + kind + "/" + handle,
            "--jq", "{id, workspace_id}",
        ])
    except CliReadError as exc:
        # A PERSON CAN BELONG TO SEVERAL ORGANIZATIONS, and the CLI and the MCP connection can
        # be signed in to different ones. Name the organization the CLI reads and the others,
        # so the fix (sign the CLI in to the artifact's organization) is one step.
        others = sorted(o["slug"] for o in orgs if isinstance(o, dict) and o.get("id") != org_id
                        and isinstance(o.get("slug"), str))
        if exc.code == "not_found" and others:
            raise ValueError(
                "The Atlan CLI is signed in to organization '" + matches[0]["slug"] + "', which "
                "cannot see this artifact; it is likely in another of your organizations ("
                + ", ".join(others) + "). Sign the CLI in to that organization with "
                "`atlanai auth login`, then run this again.") from None
        raise
    if artifact.get("id") != handle or artifact.get("workspace_id") != workspace:
        raise ValueError("The CLI cannot verify the artifact in the MCP workspace")
    # A concurrent login switch must not compose a link from two different accounts.
    after = _cli_json(status_args)
    if any(after.get(key) != status.get(key) for key in ("state", "gateway", "orgID")):
        raise ValueError("The active connection changed during link resolution")
    return link


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="https://api.atlan.com",
                        help="Gateway origin of the current MCP connection")
    parser.add_argument("--origin", default="https://app.atlan.com",
                        help="Verified dashboard origin for that environment")
    for name in ("kind", "handle", "workspace"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        print(resolve_link(**vars(args)))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
