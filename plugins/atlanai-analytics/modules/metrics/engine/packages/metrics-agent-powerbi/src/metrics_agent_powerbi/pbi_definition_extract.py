# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Extract Power BI item definitions (PBIR reports, TMDL semantic models) via the
Fabric REST API, writing both the decoded files and a payload-free envelope.

PROVENANCE. A COPY of the standalone `pbi-definition-extract` tool from an earlier project
(rule 6: copied, never imported). It stays a copy on purpose: it is a self-contained,
standard-library-only tool with no install step, so a rewrite would carry all of the risk and
none of the benefit. The one change: the envelope carries `partsWritten`, counted by re-reading
every part from disk after the save, and the saved envelope is read back and checked
(`verify_parts`, `write_envelope`). An item whose parts or envelope fail that check is reported
as FAILED, so a partial item never passes as whole.

STATUS: VERIFIED against a live Fabric tenant with a person's own sign-in: a device-code
sign-in (`--auth device`, the default public client, the 0600 token cache) fetched a report
and its semantic model through `getDefinition` with `--with-model`. Service-principal
sign-in (`--auth spn`: `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`), a handed-over
token (`--auth token`: `PBI_ACCESS_TOKEN`) and following a composite model's chain are
exercised offline only. A person's sign-in in their own browser (`--auth browser`: the OAuth
authorization code with PKCE, RFC 7636, answered on a loopback address) is tested offline
against a stand-in token endpoint; it needs a person to sign in, so it is not run unattended. Every Fabric identity is a different one from any Atlan credential.

COMPOSITE MODELS. With `--with-model`, a fetched model that reads another semantic model by
DirectQuery (`AnalysisServices.Database("powerbi://…/<workspace>", "<model>")` in its
definition) has that model fetched too, and so on up the chain, any depth; a model already
fetched is not fetched again, so a chain that loops ends. A link this identity cannot find is
recorded in `errors.json` (`UpstreamNotFound`) and the run exits 1; the reader then refuses
the model by name.

WHAT IT IS FOR. Fetching Power BI definitions straight from Microsoft Fabric, with no Atlan
tenant involved. The `powerbi-fabric` plug runs it for you at the start of a run. It writes
Power BI's own folder layout, which the core's Power BI reader
(`src/metrics_agent/plugs/readers/powerbi.py`) reads, plus an `envelope.json` describing each
response with the payloads stripped.

Targets can be given as ids, portal URLs, or a whole workspace, mixed freely:


    python3 pbi_definition_extract.py \\
        https://app.powerbi.com/groups/<ws>/reports/<id>/pagename --out ./extract

    python3 pbi_definition_extract.py --workspace <wsId>            # everything
    python3 pbi_definition_extract.py --workspace <wsId> --report <id> --report <id>
    python3 pbi_definition_extract.py --targets-file targets.txt

Output:

    out/
      manifest.json                                  run coverage
      reports/<workspaceId>/<reportId>/
          definition/…  .platform  definition.pbir   the parts, at their own paths
          envelope.json                              format + part paths, no payloads
      semanticModels/<workspaceId>/<modelId>/
          definition/…  .platform  definition.pbism
          envelope.json

APIs used, richest first (see `probe`): nobody says what kind of credential they hold, so
each item takes the richest route its identity's rights allow, and `manifest.json` records
the sign-in, the probed capabilities, the route that produced each item (`items[].route`)
and what was not available and why (`unavailable`):

    POST /v1/workspaces/{workspaceId}/reports/{reportId}/getDefinition         Fabric, LRO
    POST /v1/workspaces/{workspaceId}/semanticModels/{semanticModelId}/getDefinition
    GET  /v1.0/myorg/groups/{workspaceId}/reports/{reportId}/Export    a refused report's file
    GET  /v1.0/myorg/admin/workspaces/modified                         probe: an admin?
    POST /v1.0/myorg/admin/workspaces/getInfo, scanStatus, scanResult  the admin scanner,
                                                                       saved as scans/*.json

Standard library only. Python 3.9+. No install step.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import datetime
import hashlib
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

FABRIC_API = "https://api.fabric.microsoft.com/v1"
# The Power BI REST API: the .pbix export and the admin (scanner) API. The same token serves.
PBI_API = "https://api.powerbi.com/v1.0/myorg"
SCAN_BATCH = 100            # workspaces per scanner request, the API's own limit
SCAN_QUERY = "lineage=True&datasourceDetails=True&datasetSchema=True&datasetExpressions=True"
LOGIN_HOST = "https://login.microsoftonline.com"
# The Power BI audience is accepted by the Fabric endpoints used here, so one
# sign-in covers everything.
DEFAULT_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
# Microsoft Azure PowerShell: a first-party public client, pre-consented in most
# tenants, so interactive sign-in needs no app registration.
DEFAULT_PUBLIC_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"

# The Fabric and Power BI error codes a refusal is named by (`ApiError.denial_reason`).
# Checked live: a token for another audience or a malformed one gives 401 InvalidToken; a
# workspace this identity has no role on gives 404 EntityNotFound, an item it cannot see 404
# ItemNotFound; executeQueries on a composite model whose upstream model this identity cannot
# query gives 401 PowerBINotAuthorizedException.
TOKEN_ERROR_CODES = frozenset({"InvalidToken", "TokenExpired"})
NOT_AUTHORIZED_CODES = frozenset({"PowerBINotAuthorizedException"})
NOT_FOUND_CODES = frozenset({"EntityNotFound", "ItemNotFound", "WorkspaceNotFound"})
# The audiences a Power BI / Fabric token may carry (`token_problem`).
POWERBI_AUDIENCES = frozenset({"https://analysis.windows.net/powerbi/api",
                               "00000009-0000-0000-c000-000000000000",
                               "https://api.fabric.microsoft.com"})
# The device-code sign-in's public client, when the tenant does not allow the default one:
# an app registration of the customer's own (a client id, not a secret).
PUBLIC_CLIENT_VAR = "PBI_PUBLIC_CLIENT_ID"

# Said after any device-code failure: a tenant whose policy blocks the flow answers with its own
# AADSTS code and text (passed on as it is), and the browser sign-in is the way round it.
DEVICE_BLOCKED_HINT = (" -- where the tenant blocks the device code flow, sign in in your "
                       "browser instead (--auth browser; the plug's `sign_in: browser`)")
# A browser sign-in (authorization code with PKCE) waits this long for the person to finish,
# by default; a run passes its own (`fetch.max_sign_in_wait_seconds` in its policy).
SIGN_IN_WAIT_SECONDS = 300
# The longest the loopback redirect waits for one connection's request line.
REDIRECT_READ_SECONDS = 5

MAX_ATTEMPTS = 6
# The longest one operation (a getDefinition, an export, a scan) is waited for, and the longest
# single wait a Retry-After header is honoured for. A run passes its own (the plug reads them
# from the run's policy, `fetch.max_operation_wait_seconds` and
# `fetch.max_retry_after_seconds`); these are the standalone tool's defaults. Seen live: a fetch
# sat 18 minutes in one sleep, past the operation timeout, because neither wait was capped.
LRO_TIMEOUT_SECONDS = 900
MAX_RETRY_AFTER_SECONDS = 120
# One HTTP call that sends nothing back for this long is retried like a network error; without
# it a connection the service stops answering holds the fetch forever.
REQUEST_TIMEOUT_SECONDS = 120
# Fabric's LRO endpoints share a 200 calls/min per-identity quota on a fixed 60s
# window, so throughput is set by request spacing rather than by how many
# operations are in flight.
CALLS_PER_MINUTE = 180

# Files this tool writes into an item directory that are not API parts.
SIDECARS = {"envelope.json"}

# A definition can carry connection strings and data values, so everything this tool writes is
# owner-only: another account on the same machine reads none of it.
PRIVATE_DIR, PRIVATE_FILE = 0o700, 0o600

KIND_REPORT = "reports"
KIND_MODEL = "semanticModels"
ITEM_TYPE = {KIND_REPORT: "Report", KIND_MODEL: "SemanticModel"}

_print_lock = threading.Lock()


def log(msg: str = "", *, verbose_only: bool = False, verbose: bool = False) -> None:
    if verbose_only and not verbose:
        return
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# pacing
# --------------------------------------------------------------------------


class RatePacer:
    """Space outgoing calls so a burst cannot exceed the per-minute quota."""

    def __init__(self, calls_per_minute: int):
        self._interval = 60.0 / max(int(calls_per_minute), 1)
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self) -> None:
        with self._lock:
            slot = max(time.monotonic(), self._next_slot)
            self._next_slot = slot + self._interval
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class AdaptiveLimiter:
    """Bound in-flight work, halving on a throttle and easing back up (AIMD)."""

    def __init__(self, max_limit: int, min_limit: int = 1):
        self._max = max(int(max_limit), 1)
        self._min = max(int(min_limit), 1)
        self._limit = self._max
        self._in_flight = 0
        self._successes = 0
        self._cv = threading.Condition()

    def acquire(self) -> None:
        with self._cv:
            while self._in_flight >= self._limit:
                self._cv.wait()
            self._in_flight += 1

    def release(self) -> None:
        with self._cv:
            self._in_flight -= 1
            self._cv.notify()

    def note_throttle(self) -> None:
        with self._cv:
            self._limit = max(self._min, self._limit // 2)

    def record_success(self) -> None:
        with self._cv:
            self._successes += 1
            if self._successes >= self._limit and self._limit < self._max:
                self._limit += 1
                self._successes = 0
                self._cv.notify()


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


class AuthError(RuntimeError):
    pass


def load_env_file(path: Optional[str] = None) -> None:
    """Read a local .env without overriding what is already exported.

    Only the file named by `path` or PBI_ENV_FILE is read (the standalone script sets that to
    the .env beside itself). There is no fallback beside this module: installed, that is
    inside site-packages, a place nobody means to keep credentials.

    Values are never logged; only variable names ever appear in output.
    """
    path = path or os.environ.get("PBI_ENV_FILE")
    if not path:
        return
    try:
        with open(path) as handle:
            lines = handle.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _form_post(url: str, fields: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"error_description": raw[:400]}


def _cache_path(tenant: str, client_id: str, scope: str) -> str:
    key = hashlib.sha256(f"{tenant}|{client_id}|{scope}".encode()).hexdigest()[:16]
    base = os.path.join(
        os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
        "pbi_definition_extract",
    )
    return os.path.join(base, f"token-{key}.json")


def _read_cached_token(path: str) -> Optional[str]:
    try:
        with open(path) as handle:
            blob = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if float(blob.get("expires_at", 0)) - 60 <= time.time():
        return None
    return blob.get("access_token") or None


def open_private(path: str, mode: str = "w"):
    """Open `path` for writing, owner-only (0600).

    O_CREAT's mode applies only to a new file: a file left readable by an older version (or a
    different umask) would keep its mode and receive the new content. Set it on the open
    descriptor, so there is no window between the check and the write."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, PRIVATE_FILE)
    else:
        os.chmod(path, PRIVATE_FILE)
    return os.fdopen(fd, mode)


def makedirs_private(path: str, root: str) -> None:
    """Create `path` and every directory between it and `root`, owner-only (0700).

    Those directories are this tool's own, so one an older version left readable is tightened
    too. `root` itself, and everything above it, belongs to the caller and is left as found."""
    path, root = os.path.abspath(path), os.path.abspath(root)
    os.makedirs(path, exist_ok=True)
    while path != root and os.path.commonpath([path, root]) == root:
        os.chmod(path, PRIVATE_DIR)
        path = os.path.dirname(path)


def _write_cached_token(path: str, token: str, expires_in: int) -> None:
    """Cache the access token 0600 so repeat runs skip the sign-in prompt."""
    try:
        os.makedirs(os.path.dirname(path), mode=PRIVATE_DIR, exist_ok=True)
        with open_private(path) as handle:
            json.dump(
                {"access_token": token, "expires_at": time.time() + int(expires_in)}, handle
            )
    except OSError:
        pass


def token_client_credentials(scope: str) -> str:
    tenant = os.environ.get("PBI_TENANT_ID", "").strip()
    client_id = os.environ.get("PBI_CLIENT_ID", "").strip()
    secret = os.environ.get("PBI_CLIENT_SECRET", "").strip()
    missing = [
        name
        for name, value in (
            ("PBI_TENANT_ID", tenant),
            ("PBI_CLIENT_ID", client_id),
            ("PBI_CLIENT_SECRET", secret),
        )
        if not value
    ]
    if missing:
        raise AuthError("--auth spn needs these environment variables: " + ", ".join(missing))

    status, payload = _form_post(
        f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/token",
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": scope,
        },
    )
    if status != 200 or "access_token" not in payload:
        raise AuthError(
            f"service principal sign-in failed ({status}): "
            f"{payload.get('error_description') or payload.get('error')}"
        )
    return payload["access_token"]


def token_device_code(tenant: str, client_id: str, scope: str) -> Tuple[str, int]:
    status, payload = _form_post(
        f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/devicecode",
        {"client_id": client_id, "scope": scope},
    )
    if status != 200 or "device_code" not in payload:
        raise AuthError(
            f"device-code request failed ({status}): "
            f"{payload.get('error_description') or payload.get('error')}" + DEVICE_BLOCKED_HINT
        )

    log()
    log(payload.get("message", "Complete sign-in in the browser."))
    log()

    interval = int(payload.get("interval", 5))
    deadline = time.time() + int(payload.get("expires_in", 900))
    while time.time() < deadline:
        # The server's polling interval, capped like any Retry-After.
        time.sleep(min(max(interval, 1), MAX_RETRY_AFTER_SECONDS))
        status, tok = _form_post(
            f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": payload["device_code"],
            },
        )
        if status == 200 and "access_token" in tok:
            log("Signed in.")
            return tok["access_token"], int(tok.get("expires_in", 3600))
        error = tok.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise AuthError(f"sign-in failed: {tok.get('error_description') or error}"
                        + DEVICE_BLOCKED_HINT)
    raise AuthError("sign-in timed out -- the device code expires after ~15 minutes")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_pair() -> Tuple[str, str]:
    """(code_verifier, code_challenge) for PKCE with S256 (RFC 7636 4.1-4.2): a verifier of
    64 random bytes, base64url (86 characters, inside the 43-128 the RFC allows), and its
    SHA-256, base64url."""
    verifier = _b64url(secrets.token_bytes(64))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


class _Redirect(http.server.BaseHTTPRequestHandler):
    """The loopback address the identity provider sends the browser back to. It takes the
    first request to the redirect path, answers a fixed page (nothing from the request is
    echoed), and ignores anything else (a favicon).

    A connection that sends nothing (a browser's speculative preconnect, or any local
    process) is dropped after `timeout` seconds: the server answers one connection at a
    time, so without it an idle socket would hold the real redirect, queued behind it, past
    the sign-in's own deadline."""

    timeout = REDIRECT_READ_SECONDS

    def do_GET(self) -> None:  # noqa: N802 - the http.server name
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/" or self.server.answer is not None:  # type: ignore[attr-defined]
            self.send_response(404)
            self.end_headers()
            return
        self.server.answer = dict(urllib.parse.parse_qsl(url.query))  # type: ignore[attr-defined]
        body = (b"<!doctype html><title>Signed in</title><p>The sign-in is complete. You can "
                b"close this tab.</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # the query holds the code: never logged
        return


def token_auth_code(tenant: str, client_id: str, scope: str, *,
                    open_url: Optional[Any] = None,
                    timeout: int = SIGN_IN_WAIT_SECONDS) -> Tuple[str, int]:
    """A person's sign-in in their own browser: the OAuth 2.0 authorization code flow with
    PKCE (S256) and a loopback redirect, for a public client (no secret).

    A one-request HTTP server listens on 127.0.0.1 at a free port; the sign-in address is
    printed on stderr (and handed to `open_url`, when given, to open it); the identity provider
    sends the browser back to http://localhost:<port>/ with `code` and `state`. A `state` that
    is not the one sent, an `error` answer, or no answer within `timeout` seconds is refused by
    name. The code is exchanged at the token endpoint with the verifier; neither the code, the
    verifier nor the token is printed or written (the caller caches the token, 0600)."""
    verifier, challenge = pkce_pair()
    state = _b64url(secrets.token_bytes(24))
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirect)
    server.answer = None  # type: ignore[attr-defined]
    server.timeout = 1
    redirect = f"http://localhost:{server.server_port}/"
    url = f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect,
        "response_mode": "query", "scope": scope, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "prompt": "select_account"})
    log()
    log("Sign in in your browser at this address (it opens by itself where it can):")
    log(url)
    log()
    if open_url is not None:
        try:
            open_url(url)
        except Exception:  # noqa: BLE001 - the address is printed; opening is a convenience
            pass
    deadline = time.time() + max(int(timeout), 1)
    try:
        while server.answer is None and time.time() < deadline:  # type: ignore[attr-defined]
            server.handle_request()
    finally:
        server.server_close()
    answer = server.answer  # type: ignore[attr-defined]
    if answer is None:
        raise AuthError(f"browser sign-in timed out: no answer within {int(timeout)} seconds")
    if answer.get("error"):
        raise AuthError(f"browser sign-in refused: {answer.get('error')}: "
                        f"{(answer.get('error_description') or '')[:300]}")
    if not secrets.compare_digest(str(answer.get("state") or ""), state):
        raise AuthError("browser sign-in refused: the answer's state is not the one sent "
                        "(a stale tab, or an answer that is not this sign-in's)")
    if not answer.get("code"):
        raise AuthError("browser sign-in refused: the answer carries no authorization code")
    status, tok = _form_post(f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/token", {
        "grant_type": "authorization_code", "client_id": client_id, "code": answer["code"],
        "redirect_uri": redirect, "code_verifier": verifier, "scope": scope})
    if status != 200 or "access_token" not in tok:
        raise AuthError(f"browser sign-in failed at the token endpoint ({status}): "
                        f"{tok.get('error_description') or tok.get('error')}")
    log("Signed in.")
    return tok["access_token"], int(tok.get("expires_in", 3600))


def token_claims(token: str) -> Dict[str, Any]:
    """The claims of a JWT access token, decoded WITHOUT checking its signature: for naming
    why a token will be refused before it is used, never for trusting it (the API checks it).
    {} when it is not a JWT. Nothing here is printed or written."""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def token_problem(token: str, tenant: Optional[str] = None,
                  now: Optional[float] = None) -> Optional[str]:
    """Why a handed-over token will be refused, from its own claims, or None when nothing is
    wrong that can be seen: expired (or within a minute of it), issued for another API (its
    `aud`), or for another tenant than PBI_TENANT_ID (its `tid`). An opaque token (not a JWT)
    is let through: the API decides."""
    claims = token_claims(token)
    if not claims:
        return None
    now = time.time() if now is None else now
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp < now + 60:
        return "PBI_ACCESS_TOKEN has expired: get a new one"
    aud = str(claims.get("aud") or "").rstrip("/")
    if aud and aud not in POWERBI_AUDIENCES:
        return ("PBI_ACCESS_TOKEN was issued for another API (its audience is not Power BI): "
                "get one for https://analysis.windows.net/powerbi/api")
    tid = str(claims.get("tid") or "").lower()
    want = str(tenant or "").strip().lower()
    if tid and want and want not in ("organizations", "common") and tid != want:
        return "PBI_ACCESS_TOKEN was issued in another tenant than PBI_TENANT_ID"
    return None


def public_client_id(default: str = DEFAULT_PUBLIC_CLIENT_ID) -> str:
    """The device-code sign-in's client: PBI_PUBLIC_CLIENT_ID when set (the customer's own
    app registration, for a tenant that blocks the default one), else `default`. A value
    that is not a GUID is refused, never sent."""
    value = os.environ.get(PUBLIC_CLIENT_VAR, "").strip()
    if not value:
        return default
    if not GUID_RE.match(value):
        raise AuthError(f"{PUBLIC_CLIENT_VAR} is not an application (client) id")
    return value


def acquire_token(args: argparse.Namespace) -> str:
    if args.auth == "token":
        token = os.environ.get("PBI_ACCESS_TOKEN", "").strip()
        if not token:
            raise AuthError("--auth token needs PBI_ACCESS_TOKEN in the environment")
        problem = token_problem(token, args.tenant)
        if problem:
            raise AuthError(problem)
        return token
    if args.auth == "spn":
        return token_client_credentials(args.scope)

    cache = (
        None
        if args.no_token_cache
        else _cache_path(args.tenant, args.client_id, args.scope)
    )
    if cache:
        cached = _read_cached_token(cache)
        if cached:
            log("Reusing cached sign-in.")
            return cached
    if args.auth == "browser":
        import webbrowser  # noqa: PLC0415 - only a person's own sign-in opens a browser

        token, expires_in = token_auth_code(args.tenant, args.client_id, args.scope,
                                            open_url=webbrowser.open,
                                            timeout=args.sign_in_timeout)
    else:
        token, expires_in = token_device_code(args.tenant, args.client_id, args.scope)
    if cache:
        _write_cached_token(cache, token, expires_in)
    return token


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message

    @property
    def denial_reason(self) -> Optional[str]:
        """Why this item is permanently unavailable, or None if it may retry.

        Fabric answers a per-item refusal with a status rather than a retryable
        error. Treating these as failures would make a run look broken on
        estates where a few items are simply out of reach.
        """
        if self.status == 400:
            blob = f"{self.code} {self.message}".lower()
            if "support" in blob:
                return "400 item kind has no definition"
            return "400 rejected by the definition API"
        code = str(self.code or "")
        if code in TOKEN_ERROR_CODES:
            # Checked live: Fabric answers a token for another audience, a malformed or an
            # expired one with 401 InvalidToken on every call. Not a role problem.
            return (f"{self.status} {code}: the token is not valid for Fabric (expired, or "
                    "issued for another API); sign in again")
        if code in NOT_AUTHORIZED_CODES:
            return (f"{self.status} {code}: this identity may not use this item (no workspace "
                    "role, no Build permission, or, for a composite model, no access to a "
                    "model it reads by DirectQuery)")
        if self.status == 401:
            return f"{self.status} {code or 'Unauthorized'}: no workspace role on this item"
        if self.status == 403:
            return (f"{self.status} {code or 'Forbidden'}: the definition API needs write "
                    "permission on the item (workspace Contributor or higher; a Viewer is "
                    "refused), or the item has an encrypted sensitivity label this identity "
                    "has no usage rights to")
        if self.status == 404:
            if code in NOT_FOUND_CODES:
                return (f"404 {code}: not found, or not visible to this identity (no "
                        "workspace role)")
            return "404 item not found, or a Premium-Per-User workspace"
        if self.status == 415:
            return "415 unsupported media type for this item"
        return None


def _retry_after(headers: Dict[str, str], attempt: int, cap: int, status: int = 429) -> int:
    """Seconds to wait before retrying a throttled or failed call: the server's Retry-After,
    else a backoff. A Retry-After over `cap` is refused by name (`RetryAfterOverCap`) rather
    than slept through: a wait longer than the run allows is a failure the operator should
    see, not a silent stall."""
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        asked = max(int(raw), 1)
    except (TypeError, ValueError):
        return min(2**attempt, 60, max(int(cap), 1))
    if asked > cap:
        raise ApiError(status, "RetryAfterOverCap",
                       f"the service asked to wait {asked}s before a retry, over the "
                       f"{cap}s this run allows (fetch.max_retry_after_seconds)")
    return asked


def _poll_delay(raw: Optional[str], default: int, cap: int) -> int:
    """Seconds to wait before polling an operation again, never over `cap` (polling sooner
    than asked is harmless; the deadline still ends the wait). Retry-After may be a date or a
    fraction; anything not a whole number keeps the current delay rather than raising, which
    would end the whole batch from inside a worker thread."""
    try:
        asked = max(int(raw), 1)
    except (TypeError, ValueError):
        asked = max(int(default), 1)
    return min(asked, max(int(cap), 1))


def _sleep_until(delay: float, deadline: float) -> bool:
    """Sleep `delay` seconds, never past `deadline`. False when the deadline has passed."""
    left = deadline - time.time()
    if left <= 0:
        return False
    time.sleep(min(delay, left))
    return True


def _error_fields(raw: str) -> Tuple[str, str]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return "HttpError", raw[:300]
    error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    return (
        str(error.get("errorCode") or error.get("code") or "HttpError"),
        str(error.get("message") or raw[:300]),
    )


class FabricClient:
    def __init__(
        self,
        token: str,
        pacer: Optional[RatePacer] = None,
        limiter: Optional[AdaptiveLimiter] = None,
        verbose: bool = False,
    ):
        self._token = token
        self.pacer = pacer
        self.limiter = limiter
        self.verbose = verbose
        # Fewer attempts for a quick probe (a check takes seconds); None: MAX_ATTEMPTS.
        self.max_attempts: Optional[int] = None
        # The run's caps on waiting (`--operation-timeout`, `--max-retry-after`).
        self.operation_timeout: int = LRO_TIMEOUT_SECONDS
        self.max_retry_after: int = MAX_RETRY_AFTER_SECONDS

    def _attempts(self) -> int:
        return self.max_attempts or MAX_ATTEMPTS

    def _request(
        self, method: str, url: str, body: Optional[dict] = None
    ) -> Tuple[int, Dict[str, str], Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(1, self._attempts() + 1):
            if self.pacer is not None:
                self.pacer.wait()
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    raw = resp.read().decode() or ""
                    parsed = json.loads(raw) if raw.strip() else None
                    return resp.status, dict(resp.headers), parsed
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode(errors="replace")
                if exc.code == 429 and self.limiter is not None:
                    self.limiter.note_throttle()
                if exc.code in (429, 500, 502, 503, 504) and attempt < self._attempts():
                    time.sleep(_retry_after(dict(exc.headers), attempt, self.max_retry_after,
                                            exc.code))
                    continue
                code, message = _error_fields(raw)
                raise ApiError(exc.code, code, message) from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self._attempts():
                    time.sleep(min(2**attempt, 30))
                    continue
                reason = getattr(exc, "reason", None) or "no answer within the request timeout"
                raise ApiError(0, "NetworkError", str(reason)) from None
        raise ApiError(0, "RetryExhausted", f"gave up on {method}")

    def download(self, url: str, target: str) -> Tuple[int, bytes]:
        """GET a file to `target` (owner-only), retrying a throttle or a 5xx like any call.
        Returns (bytes written, the first bytes, to tell a .pbix zip from a .rdl)."""
        headers = {"Authorization": f"Bearer {self._token}"}
        for attempt in range(1, self._attempts() + 1):
            if self.pacer is not None:
                self.pacer.wait()
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp, open_private(target, "wb") as out:
                    head, size = b"", 0
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        head = head or chunk[:8]
                        out.write(chunk)
                        size += len(chunk)
                    return size, head
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode(errors="replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < self._attempts():
                    time.sleep(_retry_after(dict(exc.headers), attempt, self.max_retry_after,
                                            exc.code))
                    continue
                code, message = _error_fields(raw)
                raise ApiError(exc.code, code, message) from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self._attempts():
                    time.sleep(min(2**attempt, 30))
                    continue
                reason = getattr(exc, "reason", None) or "no answer within the request timeout"
                raise ApiError(0, "NetworkError", str(reason)) from None
        raise ApiError(0, "RetryExhausted", "gave up on GET")

    def get_paged(self, path: str) -> Iterable[dict]:
        url = f"{FABRIC_API}{path}"
        while url:
            _, _, body = self._request("GET", url)
            body = body or {}
            for item in body.get("value", []):
                yield item
            next_uri, token = body.get("continuationUri"), body.get("continuationToken")
            if next_uri:
                url = next_uri
            elif token:
                sep = "&" if "?" in path else "?"
                url = f"{FABRIC_API}{path}{sep}continuationToken={urllib.parse.quote(token)}"
            else:
                url = ""

    def post_lro(self, path: str) -> dict:
        """POST a long-running operation and return its final result."""
        status, headers, body = self._request("POST", f"{FABRIC_API}{path}", body={})
        if status == 200 and body:
            return body

        operation = headers.get("x-ms-operation-id")
        location = headers.get("Location") or headers.get("location")
        if not operation and location:
            operation = location.rstrip("/").rsplit("/", 1)[-1]
        if not operation:
            raise ApiError(status, "NoOperationId", "LRO response carried no operation id")

        delay = _poll_delay(headers.get("Retry-After"), 3, self.max_retry_after)
        deadline = time.time() + self.operation_timeout
        while _sleep_until(delay, deadline):
            _, op_headers, state = self._request(
                "GET", f"{FABRIC_API}/operations/{operation}"
            )
            state = state or {}
            status_value = (state.get("status") or "").lower()
            if status_value == "succeeded":
                _, _, result = self._request(
                    "GET", f"{FABRIC_API}/operations/{operation}/result"
                )
                return result or {}
            if status_value == "failed":
                error = state.get("error") or {}
                raise ApiError(
                    400,
                    error.get("errorCode", "OperationFailed"),
                    error.get("message", "operation failed"),
                )
            delay = _poll_delay(op_headers.get("Retry-After"), delay, self.max_retry_after)
        raise ApiError(0, "OperationTimeout",
                       f"operation {operation} did not finish within {self.operation_timeout}s "
                       "(fetch.max_operation_wait_seconds)")


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def safe_join(root: str, relative: str) -> str:
    """Join a server-supplied part path under root, refusing to escape it.

    REFUSED, never rewritten: an absolute path, a drive, a backslash or a `..` segment has no
    legitimate reading in a definition part, and stripping it into something harmless would
    hide that it was there."""
    if (not relative or relative.startswith("/") or "\\" in relative
            or re.match(r"^[A-Za-z]:", relative) or "\x00" in relative):
        raise ValueError(f"refusing unsafe part path: {relative!r}")
    cleaned = relative
    segments = [s for s in cleaned.split("/") if s not in ("", ".")]
    if any(s == ".." for s in segments) or os.path.splitdrive(cleaned)[0]:
        raise ValueError(f"refusing unsafe part path: {relative!r}")
    target = os.path.abspath(os.path.join(root, *segments))
    if os.path.commonpath([target, os.path.abspath(root)]) != os.path.abspath(root):
        raise ValueError(f"refusing unsafe part path: {relative!r}")
    return target


def prune_stale(item_dir: str, kept: Set[str]) -> None:
    """Remove files this definition no longer contains, and empty directories.

    Without this a part deleted upstream -- a removed page, a dropped table --
    survives from an earlier run and the extract overstates the item.
    """
    for root, _, files in os.walk(item_dir, topdown=False):
        for name in files:
            if name in SIDECARS and root == item_dir:
                continue
            path = os.path.join(root, name)
            if os.path.abspath(path) not in kept:
                try:
                    os.remove(path)
                except OSError:
                    pass
        if root != item_dir and not os.listdir(root):
            try:
                os.rmdir(root)
            except OSError:
                pass


def write_parts(item_dir: str, definition: dict) -> int:
    written, kept = 0, set()
    for part in definition.get("parts") or []:
        path, payload = part.get("path"), part.get("payload")
        if not path or payload is None:
            continue
        if (part.get("payloadType") or "InlineBase64").lower() != "inlinebase64":
            raise ValueError(f"unexpected payloadType {part.get('payloadType')!r}")
        target = safe_join(item_dir, path)
        makedirs_private(os.path.dirname(target), item_dir)
        with open_private(target, "wb") as handle:
            handle.write(base64.b64decode(payload))
        kept.add(os.path.abspath(target))
        written += 1
    prune_stale(item_dir, kept)
    return written


def verify_parts(item_dir: str, definition: dict) -> Tuple[int, List[str]]:
    """Re-read every listed part from DISK and compare it with the response.

    Returns (parts verified on disk, problems). This is what `partsWritten`
    records, so it counts what is actually there after the save, not what the
    write loop believed it wrote: a part with no payload, a file that did not
    land, or bytes that differ from the decoded payload all count as unwritten.
    Problems name paths only, never content -- the parts can carry connection
    strings.
    """
    verified, problems = 0, []
    for part in definition.get("parts") or []:
        path, payload = part.get("path"), part.get("payload")
        if not path:
            continue
        if payload is None:
            problems.append(f"{path}: the response carried no payload")
            continue
        target = safe_join(item_dir, path)
        try:
            with open(target, "rb") as handle:
                on_disk = hashlib.sha256(handle.read()).digest()
        except OSError:
            problems.append(f"{path}: not on disk after the save")
            continue
        if on_disk != hashlib.sha256(base64.b64decode(payload)).digest():
            problems.append(f"{path}: on-disk bytes differ from the response")
            continue
        verified += 1
    return verified, problems


def write_envelope(item_dir: str, envelope: dict) -> List[str]:
    """Write `envelope.json`, then read it back and check it says what was meant.

    Problems, empty when the saved envelope parses and its `partCount` and
    `partsWritten` match the values it was built with.
    """
    path = os.path.join(item_dir, "envelope.json")
    with open_private(path) as handle:
        json.dump(envelope, handle, indent=2)
    try:
        with open(path) as handle:
            saved = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"envelope.json unreadable after the save: {type(exc).__name__}"]
    problems = []
    for field in ("partCount", "partsWritten"):
        if saved.get(field) != envelope[field]:
            problems.append(
                f"envelope.json {field} reads back as {saved.get(field)!r}, "
                f"not {envelope[field]!r}"
            )
    return problems


def build_envelope(
    result: dict,
    workspace_id: str,
    item_id: str,
    kind: str,
    display_name: Optional[str],
    parts_written: int,
    workspace_name: Optional[str] = None,
) -> dict:
    """The response with payloads stripped.

    `format` and the part-path list are the only things in the response that
    are NOT recoverable from the extracted files: consumers otherwise re-derive
    the format by probing the layout, and rebuild the part index by walking the
    tree. Carries no report content, so it is safe to share.

    `partCount` is what the response listed; `partsWritten` is how many of those
    were verified on disk after the save (`verify_parts`). They are different
    claims, matching the crawler's envelope, and a reader that finds them unequal
    must treat the item as partial.
    """
    definition = result.get("definition") or {}
    envelope = {
        "workspaceId": workspace_id,
        "itemId": item_id,
        "itemKind": kind,
        "displayName": display_name,
        "retrievedAt": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "partCount": len(definition.get("parts") or []),
        "partsWritten": parts_written,
        "definition": {
            "format": definition.get("format"),
            "parts": [
                {"path": part.get("path")}
                for part in definition.get("parts") or []
                if part.get("path")
            ],
        },
    }
    if workspace_name:
        # Known for a model fetched by following a composite model's chain, which names its
        # upstream model by workspace NAME: the reader tells two same-named models apart by it.
        envelope["workspaceName"] = workspace_name
    return envelope


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# One path segment, nothing that can climb: what an item or workspace id may be on disk.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
URL_WS_RE = re.compile(r"/groups/([0-9a-fA-F-]{36})")
URL_ITEM_RE = re.compile(r"/(reports|datasets|semanticmodels)/([0-9a-fA-F-]{36})", re.I)
URL_KIND = {"reports": KIND_REPORT, "datasets": KIND_MODEL, "semanticmodels": KIND_MODEL}
KIND_WORDS = {
    "report": KIND_REPORT,
    "reports": KIND_REPORT,
    "model": KIND_MODEL,
    "dataset": KIND_MODEL,
    "datasets": KIND_MODEL,
    "semanticmodel": KIND_MODEL,
    "semanticmodels": KIND_MODEL,
}


class Target:
    """One thing to extract: a whole workspace, or one item within it."""

    def __init__(self, workspace: Optional[str], kind: Optional[str], item: Optional[str]):
        self.workspace = workspace
        self.kind = kind
        self.item = item

    def __repr__(self) -> str:
        return f"Target(ws={self.workspace}, kind={self.kind}, item={self.item})"


def parse_target(raw: str, default_workspace: Optional[str]) -> Target:
    """Accept a portal URL, a bare id, or `kind:id`."""
    text = raw.strip()
    if not text:
        raise ValueError("empty target")

    if "://" in text or "app.powerbi.com" in text or "/groups/" in text:
        ws_match = URL_WS_RE.search(text)
        item_match = URL_ITEM_RE.search(text)
        workspace = ws_match.group(1) if ws_match else default_workspace
        if not workspace:
            raise ValueError(f"no workspace id in URL: {raw}")
        if item_match:
            return Target(
                workspace, URL_KIND[item_match.group(1).lower()], item_match.group(2)
            )
        return Target(workspace, None, None)  # the whole workspace

    if ":" in text:
        kind_token, _, item = text.partition(":")
        kind = KIND_WORDS.get(kind_token.strip().lower())
        if kind and GUID_RE.match(item.strip()):
            if not default_workspace:
                raise ValueError(f"`{raw}` needs --workspace")
            return Target(default_workspace, kind, item.strip())

    if GUID_RE.match(text):
        # A bare id is an item when a workspace is known, else a workspace.
        if default_workspace:
            return Target(default_workspace, None, text)
        return Target(text, None, None)

    raise ValueError(f"could not understand target: {raw}")


def resolve(client: FabricClient, targets: List[Target], verbose: bool) -> List[dict]:
    """Turn targets into a concrete work list, naming each item."""
    inventory: Dict[str, Dict[str, dict]] = {}

    def workspace_items(workspace: str) -> Dict[str, dict]:
        if workspace not in inventory:
            found: Dict[str, dict] = {}
            for kind, item_type in ITEM_TYPE.items():
                for item in client.get_paged(
                    f"/workspaces/{workspace}/items?type={item_type}"
                ):
                    found[item["id"].lower()] = {
                        "kind": kind,
                        "id": item["id"],
                        "displayName": item.get("displayName"),
                    }
            inventory[workspace] = found
            log(
                f"  workspace {workspace}: {len(found)} item(s)",
                verbose_only=True,
                verbose=verbose,
            )
        return inventory[workspace]

    work: Dict[Tuple[str, str, str], dict] = {}
    for target in targets:
        if not target.workspace:
            raise SystemExit(f"no workspace for target {target!r}")

        if target.item is None:
            for entry in workspace_items(target.workspace).values():
                work[(target.workspace, entry["kind"], entry["id"])] = {
                    "workspace": target.workspace,
                    **entry,
                }
            continue

        known = workspace_items(target.workspace).get(target.item.lower())
        if known is None:
            if target.kind is None:
                raise SystemExit(
                    f"{target.item} is not a report or semantic model in workspace "
                    f"{target.workspace} (or this identity cannot see it)"
                )
            known = {"kind": target.kind, "id": target.item, "displayName": None}
        work[(target.workspace, known["kind"], known["id"])] = {
            "workspace": target.workspace,
            **known,
        }

    return sorted(work.values(), key=lambda e: (e["kind"], e["displayName"] or e["id"]))


def model_for_report(item_dir: str) -> Optional[str]:
    """The semantic model a report binds to, from its definition.pbir."""
    try:
        with open(os.path.join(item_dir, "definition.pbir")) as handle:
            blob = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    connection = (
        blob.get("datasetReference", {}).get("byConnection", {}).get("connectionString", "")
    )
    for part in connection.split(";"):
        if part.strip().lower().startswith("semanticmodelid="):
            return part.split("=", 1)[1].strip()
    return None


# A composite model's link to another semantic model, as its definition spells it.
REMOTE_MODEL_RE = re.compile(
    r'AnalysisServices\.Databases?\(\s*"((?:[^"]|"")*)"\s*,\s*"((?:[^"]|"")*)"', re.I)
MAX_DEFINITION_FILE_BYTES = 50_000_000


def upstream_refs(model_dir: str) -> List[Tuple[Optional[str], str]]:
    """(workspace name or None, model name) for every semantic model this model reads by
    DirectQuery, from its own definition files (`.tmdl`, `.bim`). The workspace is the last
    segment of the `powerbi://…/myorg/<workspace>` address, URL-decoded; None for `myorg`
    alone (the signed-in identity's own workspace)."""
    refs: List[Tuple[Optional[str], str]] = []
    for base, _, files in os.walk(model_dir):
        for name in sorted(files):
            if not name.lower().endswith((".tmdl", ".bim")):
                continue
            path = os.path.join(base, name)
            try:
                if os.path.islink(path) or os.path.getsize(path) > MAX_DEFINITION_FILE_BYTES:
                    continue
                with open(path, errors="ignore") as handle:
                    text = handle.read()
            except OSError:
                continue
            for server, model in REMOTE_MODEL_RE.findall(text):
                tail = server.replace('""', '"').rstrip("/").rsplit("/", 1)[-1]
                workspace = (urllib.parse.unquote(tail)
                             if "/" in server and tail.lower() != "myorg" else None)
                ref = (workspace, model.replace('""', '"'))
                if ref not in refs:
                    refs.append(ref)
    return refs


def find_upstream(client: FabricClient, workspace: Optional[str], model: str,
                  cache: Dict[str, Any]) -> Optional[dict]:
    """The work entry for the semantic model a chain link names, or None when this identity
    cannot see it. Workspaces are matched by name (or id), models by name, ignoring case."""
    if "workspaces" not in cache:
        cache["workspaces"] = list(client.get_paged("/workspaces"))
    ws_id, ws_name = None, workspace
    for ws in cache["workspaces"]:
        if workspace is None and str(ws.get("type") or "").lower() == "personal":
            ws_id, ws_name = ws.get("id"), ws.get("displayName")
        elif workspace is not None and (
                str(ws.get("displayName") or "").casefold() == workspace.casefold()
                or str(ws.get("id") or "").lower() == workspace.lower()):
            ws_id, ws_name = ws.get("id"), ws.get("displayName")
            break
    if not ws_id:
        return None
    key = f"models:{ws_id}"
    if key not in cache:
        cache[key] = list(client.get_paged(f"/workspaces/{ws_id}/items?type=SemanticModel"))
    for item in cache[key]:
        if str(item.get("displayName") or "").casefold() == model.casefold():
            return {"workspace": ws_id, "kind": KIND_MODEL, "id": item["id"],
                    "displayName": item.get("displayName"), "workspaceName": ws_name}
    return None


def follow_chain(client: FabricClient, out_root: str, models: List[Tuple[str, str]],
                 fetch: Any) -> List[dict]:
    """Fetch the semantic models the fetched `models` read by DirectQuery, then theirs, until
    none is new: any depth, and a model already fetched is never fetched again, so a loop
    ends. `fetch(entries)` fetches a batch. Returns an error entry per link not found."""
    done = set(models)
    frontier = list(models)
    cache: Dict[str, Any] = {}
    errors: List[dict] = []
    while frontier:
        found: Dict[Tuple[str, str], dict] = {}
        for workspace, model_id in frontier:
            try:
                model_dir = item_dir_of(out_root, KIND_MODEL, workspace, model_id)
            except ValueError:
                continue
            for ws_name, name in upstream_refs(model_dir):
                entry = find_upstream(client, ws_name, name, cache)
                if entry is None:
                    errors.append({
                        "kind": KIND_MODEL, "workspaceId": None, "itemId": None,
                        "displayName": name, "status": None, "errorCode": "UpstreamNotFound",
                        "message": (f"semantic model {name!r}"
                                    + (f" in workspace {ws_name!r}" if ws_name else "")
                                    + f", which model {model_id} reads by DirectQuery, is not "
                                      "visible to this identity"),
                        "reason": None})
                    log(f"  failed: chain link {name!r} of model {model_id} not found")
                    continue
                if not SAFE_ID_RE.match(str(entry["id"])) or \
                        not SAFE_ID_RE.match(str(entry["workspace"])):
                    continue
                key = (entry["workspace"], entry["id"])
                if key not in done:
                    done.add(key)
                    found[key] = entry
        if found:
            log(f"--with-model: {len(found)} upstream semantic model(s) of a composite model")
            fetch(list(found.values()))
        frontier = list(found)
    return errors


# --------------------------------------------------------------------------
# what this identity may read: the routes, richest first
# --------------------------------------------------------------------------
#
# Nobody is asked what kind of credential they hold. Whatever signed in, the richest route
# its rights allow is taken, item by item, and the manifest records which one produced what:
#
#   getDefinition   the report (PBIR) and model (TMDL) definitions. Needs a Contributor (or
#                   higher) role on the workspace.
#   export          a report the definition API refused, downloaded as its .pbix (or a
#                   paginated report's .rdl). Needs build/download rights, and a report that
#                   allows download. A .pbix carries the model of an import report.
#   scanner         the admin scanner API's metadata for every workspace fetched from (tables,
#                   columns, measures, expressions, lineage), saved under `scans/`. Only for an
#                   identity with Power BI admin API access.
#
# What none of them could read is recorded with the reason, and the run goes on with what it got.
ROUTE_DEFINITION, ROUTE_EXPORT, ROUTE_SCANNER = "getDefinition", "export", "scanner"


def probe(client: FabricClient, workspaces: List[str]) -> dict:
    """What this identity can reach, read without fetching any content: whether each
    workspace's item list is visible, and whether the admin API answers. The per-item routes
    (getDefinition, then export) are decided when each item is fetched, since a role can
    differ per workspace and a download setting per report."""
    caps: dict = {
        "getDefinition": "tried for every item (needs Contributor or higher on the workspace)",
        "export": "tried for a report whose definition is refused (needs build or download "
                  "rights, and a report that allows download)",
        "workspaces": {},
    }
    try:
        client._request("GET", f"{PBI_API}/admin/workspaces/modified")
        caps["adminScanner"] = {"available": True}
    except ApiError as exc:
        caps["adminScanner"] = {"available": False,
                                "why": f"{exc.status} {exc.code}: not a Power BI admin"
                                if exc.status in (401, 403) else f"{exc.status} {exc.code}"}
    # The Power BI workspace list says whether each is on dedicated capacity (Premium/Fabric)
    # or shared (Pro): context for a refusal, never a reason to skip a route.
    dedicated: Dict[str, Any] = {}
    try:
        _, _, groups = client._request("GET", f"{PBI_API}/groups")
        dedicated = {str(g.get("id") or "").lower(): g.get("isOnDedicatedCapacity")
                     for g in (groups or {}).get("value") or [] if isinstance(g, dict)}
    except ApiError:
        pass
    me = str(token_claims(getattr(client, "_token", "")).get("oid") or "").lower()
    for ws in workspaces:
        if not SAFE_ID_RE.match(str(ws or "")):
            continue
        try:
            items = list(client.get_paged(f"/workspaces/{ws}/items"))
            caps["workspaces"][ws] = {"visible": True, "items": len(items)}
        except ApiError as exc:
            caps["workspaces"][ws] = {"visible": False,
                                      "why": exc.denial_reason or f"{exc.status} {exc.code}"}
        else:
            caps["workspaces"][ws].update(workspace_role(client, ws, me))
        if str(ws).lower() in dedicated:
            caps["workspaces"][ws]["dedicatedCapacity"] = dedicated[str(ws).lower()]
    return caps


# What each workspace role lets the routes do (Microsoft's workspace-role table; Admin checked
# live, the lower roles from the documentation): getDefinition needs write on the item.
ROLE_ROUTES = {
    "admin": "getDefinition and export",
    "member": "getDefinition and export",
    "contributor": "getDefinition and export",
    "viewer": "no getDefinition (it needs Contributor or higher); export only where the "
              "report allows download",
}


def workspace_role(client: Any, workspace: str, me: str) -> dict:
    """This identity's own role on `workspace`, read from the workspace's role assignments
    (matched by the token's `oid`), and what that role lets the routes do. {"role": None}
    with why when it cannot be read: the list is refused, or the role comes through a group."""
    if not me:
        return {}           # an opaque token: nothing to match the assignments by
    try:
        assigned = list(client.get_paged(f"/workspaces/{workspace}/roleAssignments"))
    except ApiError as exc:
        return {"role": None, "roleWhy": f"the role list was refused ({exc.status} {exc.code})"}
    for entry in assigned:
        principal = entry.get("principal") if isinstance(entry, dict) else None
        if isinstance(principal, dict) and str(principal.get("id") or "").lower() == me:
            role = str(entry.get("role") or "")
            return {"role": role or None,
                    "roleAllows": ROLE_ROUTES.get(role.lower(), "unknown role")}
    return {"role": None, "roleWhy": "not assigned directly (a group's role, or none)"}


def export_report(client: FabricClient, out_root: str, entry: dict) -> dict:
    """A report the definition API refused, downloaded as a file: a `.pbix` (a zip), or a
    paginated report's `.rdl`. Written owner-only into the report's own directory."""
    workspace, item_id = entry["workspace"], entry["id"]
    item_dir = item_dir_of(out_root, KIND_REPORT, workspace, item_id)
    makedirs_private(item_dir, out_root)
    partial = os.path.join(item_dir, f"{item_id}.download")
    try:
        size, head = client.download(
            f"{PBI_API}/groups/{workspace}/reports/{item_id}/Export", partial)
    except ApiError:
        if os.path.exists(partial):
            os.remove(partial)
        raise
    target = os.path.join(item_dir, f"{item_id}{'.pbix' if head[:2] == b'PK' else '.rdl'}")
    os.replace(partial, target)
    return {"outcome": "fetched", "parts": 1, "route": ROUTE_EXPORT, "bytes": size,
            "file": os.path.relpath(target, out_root)}


def run_scan(client: FabricClient, out_root: str, workspaces: List[str]) -> List[str]:
    """The admin scanner API's metadata for `workspaces`, one JSON file per scan under
    `scans/` (owner-only). Returns the files written, relative to `out_root`."""
    written: List[str] = []
    ids = [w for w in workspaces if SAFE_ID_RE.match(str(w or ""))]
    for start in range(0, len(ids), SCAN_BATCH):
        _, _, body = client._request("POST", f"{PBI_API}/admin/workspaces/getInfo?{SCAN_QUERY}",
                                     body={"workspaces": ids[start:start + SCAN_BATCH]})
        scan_id = str((body or {}).get("id") or "")
        if not SAFE_ID_RE.match(scan_id):
            raise ApiError(0, "NoScanId", "the scanner returned no scan id")
        deadline = time.time() + getattr(client, "operation_timeout", LRO_TIMEOUT_SECONDS)
        delay = 3
        while True:
            _, _, state = client._request("GET", f"{PBI_API}/admin/workspaces/scanStatus/{scan_id}")
            status = str((state or {}).get("status") or "").lower()
            if status == "succeeded":
                break
            if status == "failed" or not _sleep_until(delay, deadline):
                raise ApiError(0, "ScanFailed", f"scan {scan_id} {status or 'timed out'}")
        _, _, result = client._request("GET", f"{PBI_API}/admin/workspaces/scanResult/{scan_id}")
        scans = os.path.join(out_root, "scans")
        makedirs_private(scans, out_root)
        with open_private(os.path.join(scans, f"scan-{scan_id}.json")) as handle:
            json.dump(result or {}, handle, indent=2)
        written.append(f"scans/scan-{scan_id}.json")
    return written


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def item_dir_of(out_root: str, kind: str, workspace: str, item_id: str) -> str:
    """Where an item is written. The ids come from the API listing and from a report's own
    definition.pbir (which any workspace member can edit), so each must be ONE plain path
    segment (a Fabric id is a GUID): an id of `../../x` would otherwise move the write, and the
    stale-file prune after it, outside the extract. Refused, never rewritten."""
    for what, value in (("workspace", workspace), ("item", item_id)):
        if not SAFE_ID_RE.match(str(value or "")):
            raise ValueError(f"refusing {what} id {value!r}: not a plain id")
    if kind not in (KIND_REPORT, KIND_MODEL):
        raise ValueError(f"refusing item kind {kind!r}")
    return os.path.join(out_root, kind, workspace, item_id)


def fetch_item(client: FabricClient, out_root: str, entry: dict, verbose: bool) -> dict:
    workspace, kind, item_id = entry["workspace"], entry["kind"], entry["id"]
    name = entry.get("displayName") or item_id
    try:
        item_dir = item_dir_of(out_root, kind, workspace, item_id)
    except ValueError as exc:
        log(f"  failed: {kind[:-1]} {name} -- {exc}")
        return {"outcome": "failed", "parts": 0,
                "error": {"kind": kind, "workspaceId": workspace, "itemId": item_id,
                          "displayName": entry.get("displayName"), "status": None,
                          "errorCode": "UnsafeId", "message": str(exc), "reason": None}}
    label = kind[:-1]

    if client.limiter is not None:
        client.limiter.acquire()
    try:
        result = client.post_lro(f"/workspaces/{workspace}/{kind}/{item_id}/getDefinition")
    except ApiError as exc:
        reason = exc.denial_reason
        refused = {ROUTE_DEFINITION: reason or f"{exc.status} {exc.code}: {exc.message}"}
        if reason and kind == KIND_REPORT and getattr(client, "export_fallback", True):
            # The next route down: the report as a file, which build or download rights allow
            # where the definition API (a Contributor's) does not.
            try:
                got = export_report(client, out_root, entry)
                log(f"  {label}: {name} -- getDefinition refused ({reason}); exported "
                    f"{got['file'].rsplit('.', 1)[-1]}")
                return {**got, "refused": refused}
            except ApiError as exp:
                refused[ROUTE_EXPORT] = exp.denial_reason or f"{exp.status} {exp.code}"
            except (OSError, ValueError) as exp:
                refused[ROUTE_EXPORT] = type(exp).__name__
        outcome = "unsupported" if reason else "failed"
        log(f"  {outcome}: {label} {name} -- "
            + "; ".join(f"{route}: {why}" for route, why in refused.items()))
        return {
            "outcome": outcome,
            "parts": 0,
            "refused": refused,
            "error": {
                "kind": kind,
                "workspaceId": workspace,
                "itemId": item_id,
                "displayName": entry.get("displayName"),
                "status": exc.status,
                "errorCode": exc.code,
                "message": exc.message,
                "reason": reason,
            },
        }
    finally:
        if client.limiter is not None:
            client.limiter.release()
    if client.limiter is not None:
        client.limiter.record_success()

    definition = result.get("definition") or {}
    makedirs_private(item_dir, out_root)
    try:
        write_parts(item_dir, definition)
    except ValueError as exc:
        # One item's unexpected payload (a payload type this reader does not know, base64
        # that does not decode) fails THAT item. Raised, it ended the whole batch.
        log(f"  failed: {label} {name} -- {exc}")
        return {
            "outcome": "failed",
            "parts": 0,
            "error": {
                "kind": kind,
                "workspaceId": workspace,
                "itemId": item_id,
                "displayName": entry.get("displayName"),
                "status": None,
                "errorCode": "UnreadablePayload",
                "message": str(exc),
                "reason": None,
            },
        }
    parts, problems = verify_parts(item_dir, definition)

    envelope = build_envelope(
        result, workspace, item_id, kind, entry.get("displayName"), parts,
        entry.get("workspaceName"),
    )
    problems += write_envelope(item_dir, envelope)
    if parts != envelope["partCount"]:
        problems.append(
            f"partsWritten {parts} != partCount {envelope['partCount']} -- the item is partial"
        )

    if problems:
        log(f"  failed: {label} {name} -- {len(problems)} problem(s) after the save")
        for problem in problems[:10]:
            log(f"    {problem}")
        return {
            "outcome": "failed",
            "parts": parts,
            "error": {
                "kind": kind,
                "workspaceId": workspace,
                "itemId": item_id,
                "displayName": entry.get("displayName"),
                "status": None,
                "errorCode": "IncompleteWrite",
                "message": "; ".join(problems[:10]),
                "reason": None,
            },
        }

    log(f"  {label}: {name} -- {definition.get('format') or '?'}, {parts} parts")
    return {"outcome": "fetched", "parts": parts, "route": ROUTE_DEFINITION}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract Power BI report and semantic model definitions.",
        epilog="Targets may be portal URLs, bare ids, or `report:<id>` / `model:<id>`.",
    )
    parser.add_argument("targets", nargs="*", help="report/workspace URLs or ids")
    parser.add_argument(
        "--workspace", help="workspace id or URL (on its own: the whole workspace)"
    )
    parser.add_argument("--report", action="append", default=[], metavar="ID")
    parser.add_argument("--semantic-model", action="append", default=[], metavar="ID")
    parser.add_argument("--targets-file", help="file with one target per line (# comments ok)")
    parser.add_argument("--out", default="./pbi-definitions", help="output directory")
    parser.add_argument(
        "--with-model",
        action="store_true",
        help="also extract the semantic model each report binds to, and every model a "
             "composite model reads by DirectQuery, up its chain",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--calls-per-minute",
        type=int,
        default=CALLS_PER_MINUTE,
        help=f"pace Fabric calls (default {CALLS_PER_MINUTE}; the quota is 200/min). 0 disables.",
    )
    parser.add_argument("--list-only", action="store_true", help="show what would be fetched")
    parser.add_argument("--probe", action="store_true",
                        help="sign in and print, as JSON, what this identity can reach "
                             "(workspaces, the admin API); fetch no content")
    parser.add_argument("--auth", choices=["device", "browser", "spn", "token"],
                        default="device",
                        help="device: a person, with a device code; browser: a person, in "
                             "their browser (authorization code with PKCE); spn; token")
    parser.add_argument("--sign-in-timeout", type=int, default=SIGN_IN_WAIT_SECONDS,
                        metavar="SECONDS",
                        help=f"how long a browser sign-in waits (default "
                             f"{SIGN_IN_WAIT_SECONDS})")
    parser.add_argument("--tenant", default="organizations")
    parser.add_argument("--client-id", default=None,
                        help=f"the device-code sign-in's public client (default: "
                             f"{PUBLIC_CLIENT_VAR}, else a first-party client)")
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--no-token-cache", action="store_true")
    parser.add_argument("--operation-timeout", type=int, default=LRO_TIMEOUT_SECONDS,
                        metavar="SECONDS",
                        help=f"the longest one operation is waited for (default "
                             f"{LRO_TIMEOUT_SECONDS})")
    parser.add_argument("--max-retry-after", type=int, default=MAX_RETRY_AFTER_SECONDS,
                        metavar="SECONDS",
                        help="the longest Retry-After honoured; a longer one fails the call "
                             f"by name (default {MAX_RETRY_AFTER_SECONDS})")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    if args.operation_timeout < 1 or args.max_retry_after < 1:
        raise SystemExit("--operation-timeout and --max-retry-after must be at least 1 second")

    load_env_file()
    if not args.client_id:
        args.client_id = public_client_id()

    default_workspace = None
    if args.workspace:
        match = URL_WS_RE.search(args.workspace)
        default_workspace = match.group(1) if match else args.workspace.strip()

    raw_targets: List[str] = list(args.targets)
    if args.targets_file:
        with open(args.targets_file) as handle:
            raw_targets += [
                line.strip()
                for line in handle
                if line.strip() and not line.strip().startswith("#")
            ]
    raw_targets += [f"report:{r}" for r in args.report]
    raw_targets += [f"model:{m}" for m in args.semantic_model]

    targets: List[Target] = []
    for raw in raw_targets:
        try:
            targets.append(parse_target(raw, default_workspace))
        except ValueError as exc:
            raise SystemExit(str(exc))
    if not targets:
        if not default_workspace:
            raise SystemExit("nothing to do. Pass a report URL, an id, or --workspace <id>.")
        targets.append(Target(default_workspace, None, None))

    out_root = os.path.abspath(args.out)
    # A directory this run creates is owner-only; one the caller already had (`--out .`) keeps
    # its mode, and what is written inside it is owner-only regardless.
    if not os.path.isdir(out_root):
        os.makedirs(out_root)
        os.chmod(out_root, PRIVATE_DIR)

    pacer = RatePacer(args.calls_per_minute) if args.calls_per_minute > 0 else None
    limiter = AdaptiveLimiter(max(args.concurrency, 1))
    client = FabricClient(acquire_token(args), pacer, limiter, args.verbose)
    client.operation_timeout, client.max_retry_after = args.operation_timeout, args.max_retry_after

    # What this identity can reach, before anything is read: the manifest records it, and
    # `--probe` stops here.
    capabilities = probe(client, sorted({t.workspace for t in targets if t.workspace}))
    capabilities["signIn"] = args.auth
    if args.probe:
        print(json.dumps(capabilities, indent=2))
        return 0

    try:
        work = resolve(client, targets, args.verbose)
    except ApiError as exc:
        # The listing itself refused (no role on a workspace, a token Fabric does not take):
        # a manifest that says so, and the "every item refused" exit, never a traceback.
        why = exc.denial_reason or f"{exc.status} {exc.code}"
        manifest = {"extractedAt": datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat(),
                    "parts_written": 0, "complete": False, "signIn": args.auth,
                    "capabilities": capabilities, "items": [],
                    "unavailable": [{"route": "list", "why": why}]}
        with open_private(os.path.join(out_root, "manifest.json")) as handle:
            json.dump(manifest, handle, indent=2)
        log(f"PERMISSION_DENIED_DEFINITION_API: the item listing was refused: {why}")
        return 3
    report_count = sum(1 for e in work if e["kind"] == KIND_REPORT)
    model_count = len(work) - report_count
    log(
        f"{len(work)} item(s) to extract: {report_count} report(s), "
        f"{model_count} semantic model(s)"
    )

    if args.list_only:
        for entry in work:
            print(
                f"{entry['kind']}\t{entry['workspace']}\t{entry['id']}\t"
                f"{entry.get('displayName') or ''}"
            )
        return 0

    counts = {
        KIND_REPORT: {"found": report_count, "fetched": 0, "failed": 0, "unsupported": 0},
        KIND_MODEL: {"found": model_count, "fetched": 0, "failed": 0, "unsupported": 0},
    }
    parts_written = 0
    errors: List[dict] = []
    # One line per item: which route produced it, or what each route said when none did.
    items: List[dict] = []
    unavailable: List[dict] = []
    scans: List[str] = []
    interrupted = False

    def run_batch(entries: List[dict]) -> None:
        nonlocal parts_written
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(args.concurrency, 1)
        ) as pool:
            futures = {
                pool.submit(fetch_item, client, out_root, entry, args.verbose): entry
                for entry in entries
            }
            for future in concurrent.futures.as_completed(futures):
                entry = futures[future]
                result = future.result()
                counts[entry["kind"]][result["outcome"]] += 1
                parts_written += result["parts"]
                items.append({"kind": entry["kind"], "workspaceId": entry["workspace"],
                              "itemId": entry["id"], "displayName": entry.get("displayName"),
                              "outcome": result["outcome"], "route": result.get("route"),
                              **({"file": result["file"]} if result.get("file") else {}),
                              **({"refused": result["refused"]}
                                 if result.get("refused") else {})})
                if "error" in result:
                    errors.append(result["error"])

    try:
        run_batch(work)

        if args.with_model:
            already = {(e["workspace"], e["id"]) for e in work if e["kind"] == KIND_MODEL}
            extra: Dict[Tuple[str, str], dict] = {}
            for entry in work:
                if entry["kind"] != KIND_REPORT:
                    continue
                try:
                    item_dir = item_dir_of(out_root, KIND_REPORT, entry["workspace"],
                                           entry["id"])
                except ValueError:
                    continue
                model_id = model_for_report(item_dir)
                if model_id and not SAFE_ID_RE.match(model_id):
                    log(f"  refusing semantic model id {model_id!r} named by report "
                        f"{entry['id']}: not a plain id")
                    continue
                if model_id and (entry["workspace"], model_id) not in already:
                    extra[(entry["workspace"], model_id)] = {
                        "workspace": entry["workspace"],
                        "kind": KIND_MODEL,
                        "id": model_id,
                        "displayName": None,
                    }
            if extra:
                log(f"--with-model: {len(extra)} bound semantic model(s)")
                counts[KIND_MODEL]["found"] += len(extra)
                run_batch(list(extra.values()))

            # A composite model's chain: the models the fetched ones read by DirectQuery.
            def fetch_upstream(entries: List[dict]) -> None:
                counts[KIND_MODEL]["found"] += len(entries)
                run_batch(entries)

            fetched_models = sorted(already | set(extra))
            missing = follow_chain(client, out_root, fetched_models, fetch_upstream)
            counts[KIND_MODEL]["found"] += len(missing)
            counts[KIND_MODEL]["failed"] += len(missing)
            errors.extend(missing)
            unavailable.extend({"route": ROUTE_DEFINITION, "kind": "upstreamModel",
                                "displayName": m["displayName"],
                                "why": "upstream_model_not_visible: " + m["message"]}
                               for m in missing)

        # The admin scanner, where this identity may use it: the model metadata of every
        # workspace fetched from, whatever the item routes above could read.
        scanned_ws = sorted({e["workspaceId"] for e in items} | {e["workspace"] for e in work})
        if capabilities["adminScanner"].get("available") and scanned_ws:
            try:
                scans = run_scan(client, out_root, scanned_ws)
                items.extend({"kind": "scan", "workspaceIds": scanned_ws, "outcome": "fetched",
                              "route": ROUTE_SCANNER, "file": f} for f in scans)
                log(f"scanner: {len(scans)} scan result(s) for {len(scanned_ws)} workspace(s)")
            except ApiError as exc:
                unavailable.append({"route": ROUTE_SCANNER,
                                    "why": exc.denial_reason or f"{exc.status} {exc.code}"})
        else:
            unavailable.append({"route": ROUTE_SCANNER,
                                "why": capabilities["adminScanner"].get("why")
                                or "no workspace to scan"})
    except KeyboardInterrupt:
        interrupted = True
        log("interrupted -- writing manifest for what completed")

    failed = counts[KIND_REPORT]["failed"] + counts[KIND_MODEL]["failed"]
    fetched = counts[KIND_REPORT]["fetched"] + counts[KIND_MODEL]["fetched"]
    found = counts[KIND_REPORT]["found"] + counts[KIND_MODEL]["found"]
    for item in items:
        for route, why in (item.get("refused") or {}).items():
            unavailable.append({"route": route, "kind": item["kind"],
                                "itemId": item["itemId"], "why": why})

    manifest = {
        "extractedAt": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "parts_written": parts_written,
        "complete": not interrupted and failed == 0,
        "reports": counts[KIND_REPORT],
        "semanticModels": counts[KIND_MODEL],
        # How it signed in, what that identity could reach, which route produced each item,
        # and what was not available and why.
        "signIn": args.auth,
        "capabilities": capabilities,
        "items": sorted(items, key=lambda i: (str(i["kind"]), str(i.get("itemId") or
                                                                   i.get("file")))),
        "unavailable": unavailable,
    }
    with open_private(os.path.join(out_root, "manifest.json")) as handle:
        json.dump(manifest, handle, indent=2)
    if errors:
        with open_private(os.path.join(out_root, "errors.json")) as handle:
            json.dump(errors, handle, indent=2)

    log()
    log(json.dumps(manifest, indent=2))
    log(f"output: {out_root}")
    if errors:
        log(f"{len(errors)} item(s) detailed in errors.json")

    if found and not fetched and not failed and not scans:
        log()
        log(
            "PERMISSION_DENIED_DEFINITION_API: every item was refused.\n"
            "  Not an empty estate -- the definition API declined all of them.\n"
            "  Usually this identity has no role on the workspace, the tenant\n"
            "  blocks the definition APIs, or the workspace is Premium-Per-User."
        )
        return 3

    log(
        "note: extracted files carry live connection strings and can contain data "
        "values. envelope.json does not."
    )
    return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AuthError as exc:
        print(f"auth error: {exc}", file=sys.stderr)
        sys.exit(2)
