# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The Tableau REST and Metadata API transport: one server, over TLS, signed in by whichever
credentials the environment completes (`ROUTES`: a Personal Access Token, a connected app's
JWT, or a user name and password). Standard library only (`urllib`, `hmac` for the JWT).

WHERE THE CREDENTIAL GOES. The secret is sent once, in the body of the sign-in request,
to the server `TABLEAU_SERVER_URL` names. That URL is checked before anything is sent
(`check_server_url`): https only, no user or password in it, no path, never an internal or
metadata address, and the host under the policy's `tableau.allowed_host_suffixes`. Every
later request carries the session token (`X-Tableau-Auth`) to that same server, on a path this
module builds from checked segments: no URL a server returned, and no URL in a workbook, is
ever requested. A redirect is refused rather than followed, because `urllib` would repeat the
session header to wherever it points.

NEVER PRINTED. Neither the secret nor the session token is logged, returned or put in a
message: every error this module raises says the HTTP status and Tableau's error code, and
passes the server's own words through `redact` first.

PROVENANCE of `internal_host` and `_address`: a COPY of the same checks in the Atlan package
(`metrics_agent_atlan.atlan_assets`, AGENT_GUARDRAILS rule 6: copied, never imported; this
package does not depend on that one). Left behind: its Atlan host allowlists and its scope
errors.
"""
from __future__ import annotations

import functools
import ipaddress
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SERVER_VAR = "TABLEAU_SERVER_URL"
SITE_VAR = "TABLEAU_SITE"
PAT_NAME_VAR = "TABLEAU_PAT_NAME"
PAT_SECRET_VAR = "TABLEAU_PAT_SECRET"
USERNAME_VAR = "TABLEAU_USERNAME"
PASSWORD_VAR = "TABLEAU_PASSWORD"
CA_CLIENT_VAR = "TABLEAU_CONNECTED_APP_CLIENT_ID"
CA_SECRET_ID_VAR = "TABLEAU_CONNECTED_APP_SECRET_ID"
CA_SECRET_VALUE_VAR = "TABLEAU_CONNECTED_APP_SECRET_VALUE"

# The sign-in routes, in the order one is chosen when several are complete: the first whose
# variables are all set wins. No setting names the route; the environment does.
ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("personal-access-token", (PAT_NAME_VAR, PAT_SECRET_VAR)),
    ("connected-app", (CA_CLIENT_VAR, CA_SECRET_ID_VAR, CA_SECRET_VALUE_VAR, USERNAME_VAR)),
    ("username-password", (USERNAME_VAR, PASSWORD_VAR)),
)
ENV = (SERVER_VAR, SITE_VAR, PAT_NAME_VAR, PAT_SECRET_VAR, USERNAME_VAR, PASSWORD_VAR,
       CA_CLIENT_VAR, CA_SECRET_ID_VAR, CA_SECRET_VALUE_VAR)
# What a missing route is reported as: alternatives, in `env_missing`'s "A+B or C" form.
ANY_ROUTE = " or ".join("+".join(names) for _route, names in ROUTES)
# The scopes a connected-app token asks for: one per kind of call the plug makes, nothing that
# writes. Checked live, each call refused with 401002 (or 403) without its scope and answered
# with it: content listings, the Metadata API and virtual connections (content:read); the
# workbook and data source downloads; the user's own site role (users:read, for `probe`); the
# Pulse definitions (insight_definitions_metrics:read).
CONNECTED_APP_SCOPES = ("tableau:content:read", "tableau:workbooks:download",
                        "tableau:datasources:download", "tableau:users:read",
                        "tableau:insight_definitions_metrics:read")
# Tableau's code for a call a connected app's token has no scope for.
MISSING_SCOPE_CODE = "401002"


def _set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def detect_route() -> str | None:
    """The sign-in route the environment completes, or None. Names only are read here."""
    return next((route for route, names in ROUTES if all(map(_set, names))), None)


def missing_for_sign_in() -> tuple[str, ...]:
    """What is unset for a sign-in: the server, and a route when none is complete."""
    out = () if _set(SERVER_VAR) else (SERVER_VAR,)
    return out + (() if detect_route() else (ANY_ROUTE,))

POLICY_FILE = pathlib.Path(__file__).resolve().parent / "policy.yml"
ALLOWLIST_KEY = "tableau.allowed_host_suffixes"

# A Tableau LUID, a site id: the only server-supplied values that become a URL path segment.
LUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                  r"[0-9a-fA-F]{12}$")
API_VERSION = re.compile(r"^\d{1,2}\.\d{1,2}$")


class TableauError(RuntimeError):
    """A refusal or a failed call, in words an operator can act on. Never holds a secret."""

    def __init__(self, message: str, *, status: int | None = None, code: str = "",
                 fix: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.fix = fix


# ── policy ───────────────────────────────────────────────────────────────────────────────
def _own_defaults() -> dict[str, Any]:
    import yaml  # noqa: PLC0415 - the core's own dependency

    return (yaml.safe_load(POLICY_FILE.read_text()) or {}).get("tableau") or {}


@functools.lru_cache(maxsize=8)
def _run_policy(_which: str):
    """The run's policy, loaded once per process for each `$METRICS_AGENT_POLICY` (a plug
    process reads several of its keys; each load scans every package's defaults)."""
    from metrics_agent.lib import policy as _policy  # noqa: PLC0415

    return _policy.load(None)


def policy_value(key: str) -> Any:
    """`tableau.<key>` from the run's policy (the package's defaults merged under it), else
    this package's own file: a plug building outside a stage still gets the shipped value,
    never a default written in code."""
    try:
        return _run_policy(os.environ.get("METRICS_AGENT_POLICY") or "").get(f"tableau.{key}")
    except Exception:  # noqa: BLE001 - a policy without this package's section
        defaults = _own_defaults()
        if key not in defaults:
            raise TableauError(f"policy key tableau.{key} is absent from {POLICY_FILE}")
        return defaults[key]


# ── where a credential may go ────────────────────────────────────────────────────────────
_METADATA_HOSTS = ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")
_INTERNAL_NAMES = ("localhost", "metadata", "metadata.google.internal", "instance-data")
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_SHARED = ipaddress.ip_network("100.64.0.0/10")


def _address(host: str):
    """The IP address a host string IS, in any spelling a resolver accepts, or None."""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None
        if re.fullmatch(r"[0-9a-fA-Fx.]+", host) and any(c.isdigit() for c in host):
            import socket  # noqa: PLC0415 - parsing only, no connection

            try:
                ip = ipaddress.ip_address(socket.inet_aton(host))
            except OSError:
                ip = None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def internal_host(host: str) -> str | None:
    """Why `host` is not a remote service a credential may be sent to, or None."""
    h = (host or "").strip().lower().rstrip(".")
    if h in _METADATA_HOSTS or h in _INTERNAL_NAMES:
        return "the cloud metadata service or the local machine"
    if h.endswith(_INTERNAL_SUFFIXES):
        return "a local or cluster-internal name"
    ip = _address(h)
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local
                           or ip.is_reserved or ip.is_multicast or ip.is_unspecified
                           or (ip.version == 4 and ip in _SHARED)):
        return "a private, loopback, link-local or reserved address"
    return None


def check_server_url(url: str, allowed_suffixes: list[str] | None) -> str:
    """`TABLEAU_SERVER_URL`, validated: `https://host[:port]`, returned without a trailing
    slash. Raises `TableauError` naming the variable otherwise. The value itself is never
    repeated: only the host, once it is known to be a plain host."""
    raw = (url or "").strip()
    fix = SERVER_VAR
    if not raw:
        raise TableauError(f"{SERVER_VAR} is not set.", fix=fix)
    if not raw.lower().startswith("https://"):
        raise TableauError(f"{SERVER_VAR} is not an https:// URL. The sign-in carries a "
                           "credential, so it goes over TLS or it does not go.", fix=fix)
    parts = urllib.parse.urlsplit(raw)
    if parts.username or parts.password or "@" in parts.netloc:
        raise TableauError(f"{SERVER_VAR} carries a user or password in the URL. Refused: the "
                           "token is read from its own variables.", fix=fix)
    if parts.path.strip("/") or parts.query or parts.fragment:
        raise TableauError(f"{SERVER_VAR} must be the server only (https://host), with no "
                           "path, site or query: the site goes in TABLEAU_SITE.", fix=fix)
    host = (parts.hostname or "").lower()
    if not host:
        raise TableauError(f"{SERVER_VAR} has no host in it.", fix=fix)
    try:
        parts.port
    except ValueError:
        raise TableauError(f"{SERVER_VAR} has a port that is not a number.", fix=fix) from None
    why = internal_host(host)
    if why:
        raise TableauError(f"{SERVER_VAR} points at {why}. Refused: the sign-in would carry a "
                           "live credential there.", fix=fix)
    if allowed_suffixes:
        if not any(host == s.lstrip(".") or host.endswith(s if s.startswith(".") else "." + s)
                   for s in allowed_suffixes):
            raise TableauError(
                f"{SERVER_VAR}'s host {host} is not under any allowed suffix "
                f"({', '.join(allowed_suffixes)}). Refused by default rather than trusted: a "
                "typo in a host name otherwise sends the token to whoever owns the "
                f"misspelling. For your own Tableau Server, add its domain to "
                f"`{ALLOWLIST_KEY}` in your policy file.", fix=f"{SERVER_VAR}, or {ALLOWLIST_KEY}")
    return f"https://{parts.netloc.lower()}"


def server_from_env() -> str:
    return check_server_url(os.environ.get(SERVER_VAR, ""),
                            policy_value("allowed_host_suffixes"))


def segment(value: str, what: str) -> str:
    """A server-supplied id as ONE path segment: a LUID, or refused."""
    if not LUID.match(str(value or "")):
        raise TableauError(f"refusing {what} {str(value)[:60]!r}: not a Tableau LUID")
    return str(value)


# ── the transport ────────────────────────────────────────────────────────────────────────
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would repeat `X-Tableau-Auth` to wherever it points: refused, unfollowed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _open(request: urllib.request.Request, timeout: float):
    """The one place a request leaves the process. Tests replace it with a fake server."""
    return _OPENER.open(request, timeout=timeout)


def _error_fields(raw: bytes) -> tuple[str, str]:
    """(Tableau's error code, its summary) from an error body, JSON or XML."""
    text = raw.decode("utf-8", "replace")
    try:
        err = (json.loads(text or "{}") or {}).get("error") or {}
        return str(err.get("code") or ""), str(err.get("summary") or "")
    except (ValueError, AttributeError):
        code = re.search(r'<error[^>]*\bcode="([^"]*)"', text)
        summary = re.search(r"<summary>([^<]*)</summary>", text)
        return (code.group(1) if code else ""), (summary.group(1) if summary else "")


class Session:
    """A signed-in session on one site. Use as a context manager: it signs out on exit."""

    def __init__(self, server: str, site: str, api_version: str, timeout: float) -> None:
        if not API_VERSION.match(str(api_version)):
            raise TableauError(f"api_version {api_version!r} is not a REST API version such "
                               "as 3.19", fix="settings.tableau-cloud.api_version")
        self.server = server
        self.site_content_url = site
        self.api = f"{server}/api/{api_version}"
        self.timeout = timeout
        self.site_id = ""
        self.user_id = ""
        self.route = ""
        self._token = ""
        self._secrets: list[str] = []

    # -- secrets never leave ---------------------------------------------------------
    def redact(self, text: str) -> str:
        for s in self._secrets:
            if s:
                text = text.replace(s, "[redacted]")
        return text

    def _fail(self, what: str, status: int | None, raw: bytes, *, fix: str = "") -> TableauError:
        code, summary = _error_fields(raw)
        said = self.redact(f"{code} {summary}".strip())[:200]
        message = f"{what}: HTTP {status}" + (f" ({said})" if said else "")
        if code == MISSING_SCOPE_CODE and self.route == "connected-app":
            message += ("; the connected app's token has no scope for this call: the app "
                        "must allow the scopes rest.CONNECTED_APP_SCOPES lists")
            fix = fix or "the connected app's allowed scopes"
        return TableauError(message, status=status, code=code, fix=fix)

    # -- requests --------------------------------------------------------------------
    def request(self, method: str, path: str, *, query: dict | None = None,
                body: dict | None = None, accept: str = "application/json",
                limit: int | None = None, base: str | None = None) -> tuple[int, dict, bytes]:
        """One call to this server. `path` is joined to the API root (or `base`); every
        variable part of it was checked with `segment` by the caller."""
        url = (base or self.api) + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": accept}
        if self._token:
            headers["X-Tableau-Auth"] = self._token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _open(req, self.timeout) as resp:
                raw = resp.read(limit + 1) if limit else resp.read()
                if limit and len(raw) > limit:
                    raise TableauError(f"a response from {path.split('?')[0]} is over the "
                                       f"{limit}-byte cap (tableau.max_download_bytes); "
                                       "refused rather than held in memory",
                                       fix="tableau.max_download_bytes")
                return resp.status, dict(resp.headers.items()), raw
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp is not None else b""
            raise self._fail(f"{method} {self._label(path)}", exc.code, raw) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise TableauError(f"could not reach the Tableau server "
                               f"({type(reason).__name__})", fix=SERVER_VAR) from None
        except (TimeoutError, OSError) as exc:
            raise TableauError(f"could not reach the Tableau server ({type(exc).__name__})",
                               fix=SERVER_VAR) from None

    @staticmethod
    def _label(path: str) -> str:
        """The call, for a message: the path with every id replaced by its kind."""
        return re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}", "{id}", path.split("?")[0])

    def get_json(self, path: str, query: dict | None = None) -> dict:
        _, _, raw = self.request("GET", path, query=query)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise TableauError(f"GET {self._label(path)} did not answer JSON") from None

    def paged(self, path: str, outer: str, inner: str, query: dict | None = None,
              *, max_pages: int = 1000):
        """Every item of a paged REST listing."""
        page = 1
        while page <= max_pages:
            doc = self.get_json(path, {**(query or {}), "pageSize": 100, "pageNumber": page})
            items = ((doc.get(outer) or {}).get(inner)) or []
            yield from items
            pag = doc.get("pagination") or {}
            total = int(pag.get("totalAvailable") or 0)
            if page * 100 >= total or not items:
                return
            page += 1

    # -- sign in and out ---------------------------------------------------------------
    def sign_in(self, route: str) -> None:
        """Sign in by `route` (`detect_route`), reading its variables from the environment.
        Every value is added to what `redact` removes before it can reach a message."""
        env = {n: (os.environ.get(n) or "").strip() for n in ENV}
        site = {"contentUrl": self.site_content_url}
        if route == "personal-access-token":
            creds = {"personalAccessTokenName": env[PAT_NAME_VAR],
                     "personalAccessTokenSecret": env[PAT_SECRET_VAR], "site": site}
            self._secrets = [env[PAT_SECRET_VAR], env[PAT_NAME_VAR]]
        elif route == "connected-app":
            jwt = connected_app_jwt(env[CA_CLIENT_VAR], env[CA_SECRET_ID_VAR],
                                    env[CA_SECRET_VALUE_VAR], env[USERNAME_VAR])
            creds = {"jwt": jwt, "site": site}
            self._secrets = [env[CA_SECRET_VALUE_VAR], jwt, env[CA_SECRET_ID_VAR]]
        elif route == "username-password":
            creds = {"name": env[USERNAME_VAR], "password": env[PASSWORD_VAR], "site": site}
            self._secrets = [env[PASSWORD_VAR]]
        else:
            raise TableauError(f"no sign-in route is complete: set {ANY_ROUTE}",
                               fix=ANY_ROUTE)
        self.route = route
        try:
            _, _, raw = self.request("POST", "/auth/signin", body={"credentials": creds})
        except TableauError as exc:
            if exc.status in (400, 401, 403, 404):
                names = dict(ROUTES)[route]
                exc.fix = ", ".join((*names, SITE_VAR))
                why = {"personal-access-token": "a revoked or expired token, a token of "
                                                "another site, or a user who is Unlicensed "
                                                "(no site role)",
                       "connected-app": "a disabled connected app, a wrong secret, a user "
                                        "the app may not act for, a user who is Unlicensed "
                                        "(no site role; checked live: 401001), or a scope the "
                                        "app does not allow",
                       "username-password": "a wrong user or password; on Tableau Cloud, "
                                            "password sign-in does not work when the user "
                                            "has multi-factor authentication or single "
                                            "sign-on: use a Personal Access Token "
                                            f"({PAT_NAME_VAR}, {PAT_SECRET_VAR}) instead"}[route]
                exc.args = (f"Tableau refused the {route} sign-in: {exc} ({why}, or a site "
                            "name that is wrong)",)
            raise
        try:
            creds = (json.loads(raw.decode("utf-8")) or {}).get("credentials") or {}
        except ValueError:
            creds = {}
        token, site_id = creds.get("token") or "", (creds.get("site") or {}).get("id") or ""
        if not token or not LUID.match(site_id):
            raise TableauError("the sign-in answered without a session token or site id",
                               fix=SERVER_VAR)
        self._token, self.site_id = token, site_id
        self.user_id = str((creds.get("user") or {}).get("id") or "")
        self._secrets.append(token)

    def sign_out(self) -> None:
        if not self._token:
            return
        try:
            self.request("POST", "/auth/signout")
        except TableauError:
            pass            # the session expires on its own; a failed sign-out is no failure
        finally:
            self._token = ""

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.sign_out()

    # -- the Metadata API --------------------------------------------------------------
    def graphql(self, query: str, variables: dict) -> dict:
        _, _, raw = self.request("POST", "/api/metadata/graphql", base=self.server,
                                 body={"query": query, "variables": variables})
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise TableauError("the Metadata API did not answer JSON") from None


def _b64url(data: bytes) -> str:
    import base64  # noqa: PLC0415

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def connected_app_jwt(client_id: str, secret_id: str, secret_value: str, username: str,
                      *, lifetime_seconds: int = 300) -> str:
    """A Tableau connected-app (direct trust) token, HS256-signed with the app's secret:
    `iss` the client id, `kid` the secret id, `sub` the user it acts for, `aud` "tableau",
    the read and download scopes, and a five-minute life (Tableau's own maximum is ten)."""
    import hashlib  # noqa: PLC0415
    import hmac  # noqa: PLC0415
    import time  # noqa: PLC0415
    import uuid  # noqa: PLC0415

    header = {"alg": "HS256", "typ": "JWT", "kid": secret_id, "iss": client_id}
    claims = {"iss": client_id, "sub": username, "aud": "tableau",
              "exp": int(time.time()) + int(lifetime_seconds), "jti": str(uuid.uuid4()),
              "scp": list(CONNECTED_APP_SCOPES)}
    signing = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + "."
               + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    sig = hmac.new(secret_value.encode(), signing.encode(), hashlib.sha256).digest()
    return signing + "." + _b64url(sig)


def open_session(*, api_version: str) -> Session:
    """A session on the server and site the environment names, signed in by the route the
    environment completes (`detect_route`). The caller signs out (`with open_session(...)`)."""
    missing = missing_for_sign_in()
    if missing:
        raise TableauError("tableau-cloud needs " + ", ".join(missing),
                           fix=", ".join(missing))
    server = server_from_env()
    session = Session(server, (os.environ.get(SITE_VAR) or "").strip(), api_version,
                      float(policy_value("timeout_seconds")))
    session.sign_in(detect_route() or "")
    return session


# ── what this sign-in may do ─────────────────────────────────────────────────────────────
# Site roles that can never download a workbook or a data source, whatever an object's
# permissions say. Every other role can, where the object allows it: checked live, an Explorer
# downloads a workbook it is permitted to and is refused a data source it is not.
_NO_DOWNLOAD_ROLES = {"viewer", "unlicensed", "readonly"}


# Why the Metadata API may be missing, said whenever it is: the downloads do not need it.
NO_METADATA_API = ("on Tableau Cloud the Metadata API needs Data Management (Tableau Catalog), "
                   "on Tableau Server its metadata services; the fetch uses the downloads only, "
                   "and a data source whose download is refused cannot be rebuilt from it")


def probe(session: Session, *, metadata_api: bool = True) -> dict[str, Any]:
    """What this sign-in may do, read without reading any content: the user's site role (the
    upper bound on downloads; each object's permissions are only known when it is asked for)
    and whether the Metadata API answers. Each value is "yes", "no" or "unknown", with why.
    `metadata_api=False` (settings.tableau-cloud.metadata_api) never asks the Metadata API:
    the route a site without Data Management takes."""
    out: dict[str, Any] = {"route": session.route}
    role = ""
    if LUID.match(session.user_id or ""):
        try:
            doc = session.get_json(f"/sites/{segment(session.site_id, 'site id')}/users/"
                                   f"{segment(session.user_id, 'user id')}")
            role = str((doc.get("user") or {}).get("siteRole") or "")
        except TableauError:
            role = ""
    out["siteRole"] = role or None
    if not role:
        dl = ("unknown", "the site role could not be read; decided per object at fetch time")
    elif role.replace(" ", "").lower() in _NO_DOWNLOAD_ROLES:
        dl = ("no", f"site role {role} cannot download")
    else:
        dl = ("yes", f"site role {role} allows it, subject to each object's permissions")
    out["downloadWorkbooks"] = {"available": dl[0], "why": dl[1]}
    out["downloadDatasources"] = {"available": dl[0], "why": dl[1]}
    if not metadata_api:
        out["metadataApi"] = {"available": "no", "why": "turned off in the settings "
                              "(metadata_api: false); the fetch uses the downloads only"}
        return out
    try:
        doc = session.graphql("query metricsAgentProbe { __typename }", {})
        ok = not doc.get("errors") and bool(doc.get("data"))
        out["metadataApi"] = {"available": "yes" if ok else "no",
                              "why": "it answers" if ok else
                              f"it answered with errors: {NO_METADATA_API}"}
    except TableauError as exc:
        out["metadataApi"] = {"available": "no",
                              "why": (f"HTTP {exc.status}" if exc.status else str(exc))
                              + f": {NO_METADATA_API}"}
    return out


def seat_refusal(caps: dict[str, Any]) -> str | None:
    """The fix for a refused download when the SITE ROLE, not a permission, forbids it: a
    Viewer or an Unlicensed user can never download, whatever an object's permissions say,
    so asking for a permission would not help. None when the role allows downloads."""
    role = str(caps.get("siteRole") or "")
    if role and role.replace(" ", "").lower() in _NO_DOWNLOAD_ROLES:
        return (f"site role {role} can never download workbooks or data sources: ask for an "
                "Explorer or Creator seat for this user")
    return None
