# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The `powerbi-fabric` input plug: Power BI definitions straight from Microsoft Fabric.

Custody `customer`: it signs in to the customer's own Fabric tenant, as their service
principal or as a person, and calls Fabric's public `getDefinition` API. No Atlan tenant is
involved.

It runs the standalone extractor (`pbi_definition_extract.py`, standard library only) in a
child process and returns the files it wrote: report (PBIR) and semantic-model (TMDL)
definitions in Power BI's own folder layout, which the core's Power BI parser reads.

Sign-in, the first that applies (`sign_in()`):
    1. a service principal, when PBI_TENANT_ID, PBI_CLIENT_ID and PBI_CLIENT_SECRET are all
       set: for CI and unattended runs;
    2. PBI_ACCESS_TOKEN, a token already issued for the Power BI API, used as is;
    3. a person's own sign-in with a device code, reusing the extractor's owner-only (0600)
       token cache, ONLY when the run is at a terminal. A run that is not refuses by name
       instead of waiting for a code nobody will see. PBI_PUBLIC_CLIENT_ID, when set, is the
       public client (an app registration of the customer's) it signs in through, for a
       tenant that blocks the default first-party one. With the setting `sign_in: browser`
       the person signs in in their own browser instead (the OAuth authorization code with
       PKCE, answered on a loopback address): for a tenant whose policy blocks the device
       code flow. It waits `fetch.max_sign_in_wait_seconds` (policy) for them.
A handed-over token is read (its claims, never its signature) before it is used: one that
has expired, or was issued for another API or another tenant, is refused by name. A service
principal with only a certificate (PBI_CLIENT_CERTIFICATE_PATH) is refused by name: not
supported.

What the identity may reach is probed first (`X.probe`): each workspace's items, its own role
there (when the role is assigned to it directly) and what that role allows, and the admin
scanner. Checked live as a workspace Admin; the lower roles' limits come from Microsoft's
workspace-role table (getDefinition needs Contributor or higher).

Settings:
    targets      report or workspace URLs or ids (a list), or
    workspace    a workspace id or URL (on its own: the whole workspace)
    with_model   also fetch the model each report binds to, and the models a composite
                 model chains to (default true)
    bi           an extract already on disk: returned as is, nothing is fetched
    sign_in      a person's sign-in: `device` (the default: a code to enter on another
                 device) or `browser` (in their own browser, authorization code with PKCE)
    model        when the fetch brings several semantic models a run could read, the one
                 this run reads (its id); one model needs no choice, and the upstream models
                 of a composite model's chain are read through it, not chosen

With `targets` or `workspace` and no `bi`, `metrics-agent run` fetches at the start of the
run (`s_fetch_bi`) and S0 reads the fetched model.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import Any

from metrics_agent import sockets as S

EXTRACTOR = pathlib.Path(__file__).resolve().parent / "pbi_definition_extract.py"

SPN_ENV = ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET")
TOKEN_ENV = "PBI_ACCESS_TOKEN"
# A service principal that has a certificate and no secret. Not supported: named, never tried.
CERT_ENV = "PBI_CLIENT_CERTIFICATE_PATH"
# The device-code sign-in's own public client, for a tenant that blocks the default one.
PUBLIC_CLIENT_ENV = "PBI_PUBLIC_CLIENT_ID"
CERT_REFUSED = ("certificate sign-in for a service principal is not supported: give the "
                "principal a client secret (PBI_CLIENT_SECRET), or hand over a token "
                "(PBI_ACCESS_TOKEN) the principal obtained with its certificate")
SIGN_IN_KINDS = ("device", "browser")
NO_SIGN_IN = ("no Fabric sign-in for a run that is not at a terminal: set PBI_TENANT_ID, "
              "PBI_CLIENT_ID and PBI_CLIENT_SECRET (a service principal) or PBI_ACCESS_TOKEN, "
              "or run it at a terminal to sign in as yourself with a device code")


class SignInRefused(RuntimeError):
    """No way to sign in that does not need a person, and no person to ask."""


def fetch_wait(key: str) -> int:
    """The policy key `key` (a `fetch.` key) from the run's policy ($METRICS_AGENT_POLICY,
    else the one the core ships): a whole number of seconds, at least 1."""
    from metrics_agent.lib import policy as _policy  # noqa: PLC0415

    value = _policy.load(None).get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _policy.PolicyError(f"policy key {key} must be a whole number of seconds "
                                  f"(at least 1), not {value!r}")
    return value


def at_terminal() -> bool:
    """A person can answer: both stdin and stderr are a terminal (the code is printed on
    stderr)."""
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):     # closed or replaced streams
        return False


def sign_in() -> str | None:
    """How a fetch signs in, the first that applies: `spn` (all three service-principal
    variables set), `token` (PBI_ACCESS_TOKEN set), `device` (a person at a terminal), or None
    when none can. Names only are read here; no value is kept."""
    if all(os.environ.get(n, "").strip() for n in SPN_ENV):
        return "spn"
    if os.environ.get(CERT_ENV, "").strip() and not os.environ.get(TOKEN_ENV, "").strip():
        return "certificate"            # recognised so it is refused by name, never tried
    if os.environ.get(TOKEN_ENV, "").strip():
        return "token"
    if at_terminal():
        return "device"
    return None


class PowerBIFabric:
    name = "powerbi-fabric"
    capabilities = ("bi_definitions",)
    bi_vendor = "powerbi"
    # With one of these and no `bi`, the run fetches the definitions itself (`s_fetch_bi`,
    # the first step of `metrics-agent run`) into the run directory.
    fetch_settings = ("targets", "workspace")
    setup_questions = {
        # Either the reports to fetch at the start of each run, or an extract already on disk.
        "bi": (S.SetupQuestion("targets", "Report or workspace URLs to fetch from Fabric at the "
                               "start of the run; it signs in as a service principal "
                               "(PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET), with "
                               "PBI_ACCESS_TOKEN, or as you with a device code at a terminal "
                               "(blank: you have an extract on disk)",
                               required=False, kind="list"),
               S.SetupQuestion("bi", "Path to a Fabric extract on disk (blank when the run "
                               "fetches)", required=False, kind="path")),
    }

    @classmethod
    def validate_settings(cls, role: str, settings: dict[str, Any]) -> str | None:
        """BI definitions come from an extract on disk (`bi`) OR a fetch at the start of the
        run (`targets` or `workspace`); one of them is required."""
        if role == "bi" and not (settings.get("bi") or any(settings.get(k)
                                                             for k in cls.fetch_settings)):
            return ("plug 'powerbi-fabric' needs settings.powerbi-fabric.bi (an extract on "
                    "disk), or settings.powerbi-fabric.targets to fetch one")
        if settings.get("sign_in") not in (None, *SIGN_IN_KINDS):
            return ("settings.powerbi-fabric.sign_in is `device` or `browser`, not "
                    f"{settings.get('sign_in')!r}")
        return None

    @classmethod
    def env_missing(cls, use: str | None = None) -> tuple[str, ...]:
        """Nothing is required: an extract already on disk needs no credential, and a fetch
        signs in with whichever applies (`sign_in()`): a service principal (PBI_TENANT_ID,
        PBI_CLIENT_ID, PBI_CLIENT_SECRET), PBI_ACCESS_TOKEN, or a person's device-code sign-in
        at a terminal. `check()` refuses a fetch that has none of them."""
        return ()
    # `targets` or `workspace` names what to read, so a run reads every model fetched (one
    # each, merged by A1); `model` narrows it to one.
    fetch_reads_all = True
    kind = "input"
    custody = "customer"
    # Every variable it reads, none of them required on its own (see `env_missing`).
    requires_env: tuple[str, ...] = (*SPN_ENV, TOKEN_ENV, PUBLIC_CLIENT_ENV,
                                       CERT_ENV)
    # The ways it signs in, in the order it tries them; the interview lists them, never asks.
    credential_routes = (
        S.CredentialRoute("service principal", SPN_ENV),
        S.CredentialRoute("access token", (TOKEN_ENV,)),
        S.CredentialRoute("your own sign-in", (),
                          note="nothing to set: run at a terminal and sign in with the code "
                               "the run shows, or in your browser with `sign_in: browser` "
                               "(authorization code with PKCE, for a tenant that blocks the "
                               "device code); where the tenant blocks the default sign-in "
                               f"app, set {PUBLIC_CLIENT_ENV} to an app registration of "
                               "your own"),
    )

    def __init__(self, settings: dict[str, Any] | None = None, *, dialect: str = "") -> None:
        self.settings = dict(settings or {})

    @staticmethod
    def fetched_models(root: pathlib.Path) -> list[pathlib.Path]:
        """The semantic models a fetch wrote under `root`, in Power BI's own layout
        (`semanticModels/<workspace>/<model>/`), one folder each (`sockets.fetched_models`):
        those a run could read. A model another fetched model reads by DirectQuery (an
        upstream link of a composite model's chain) is read through that model, not chosen.
        A report the definition API refused and the fetch exported instead is its own file
        (`reports/<workspace>/<report>/<report>.pbix`), one model each."""
        from metrics_agent.plugs.readers import powerbi as _powerbi  # noqa: PLC0415
        from metrics_agent.plugs.readers import powerbi_layout as _layout  # noqa: PLC0415

        try:
            models = list(_powerbi.readable_models(root))
        except _layout.ExtractError:
            models = []
        exported = [p for p in (root / "reports").glob("*/*/*.pbix")
                    if p.is_file() and p.stem == p.parent.name]
        return sorted(models + _layout.safe_paths(root, exported))

    def check(self, settings: dict[str, Any]) -> S.CheckResult:
        """An extract already on disk exists; otherwise a target is named and there is a way
        to sign in: the service principal signs in (one token request, nothing read), a set
        PBI_ACCESS_TOKEN is used as is, and at a terminal a person signs in with a device code
        when the run starts (a cached sign-in is reused). Nothing here asks for a code."""
        if settings.get("bi"):
            ok = pathlib.Path(str(settings["bi"])).expanduser().exists()
            return S.CheckResult(ok, "extract found" if ok else "the `bi` path does not exist",
                                 fix="" if ok else "settings.powerbi-fabric.bi")
        mode = sign_in()
        if mode is None:
            return S.CheckResult(False, NO_SIGN_IN, S.missing_env(self.requires_env),
                                 fix="PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET or "
                                     "PBI_ACCESS_TOKEN (or run at a terminal)")
        if mode == "certificate":
            return S.CheckResult(False, CERT_REFUSED, fix="PBI_CLIENT_SECRET or PBI_ACCESS_TOKEN")
        if not (settings.get("targets") or settings.get("workspace")):
            return S.CheckResult(False, "no report or workspace to fetch",
                                 fix="settings.powerbi-fabric.targets or .workspace")
        from metrics_agent_powerbi import pbi_definition_extract as X  # noqa: PLC0415

        partial = [n for n in SPN_ENV if os.environ.get(n, "").strip()] if mode != "spn" else []
        note = (f" ({', '.join(partial)} set, but not all three: the service principal is "
                "not used)") if partial else ""
        if mode == "spn":
            route = "signs in as the service principal"
            try:
                token = X.token_client_credentials(X.DEFAULT_SCOPE)
            except Exception as exc:  # noqa: BLE001 - the check reports, it never raises
                return S.CheckResult(False, f"Fabric sign-in failed: {exc}",
                                     fix="PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET")
        elif mode == "token":
            route, token = "signs in with PBI_ACCESS_TOKEN", os.environ[TOKEN_ENV].strip()
            problem = X.token_problem(token, os.environ.get("PBI_TENANT_ID"))
            if problem:
                return S.CheckResult(False, problem, fix=TOKEN_ENV)
        else:
            route = ("signs in as you in your browser (authorization code with PKCE)"
                     if settings.get("sign_in") == "browser" else
                     "signs in as you (device code)")
            try:
                client_id = X.public_client_id()
            except X.AuthError as exc:
                return S.CheckResult(False, str(exc), fix=PUBLIC_CLIENT_ENV)
            if client_id != X.DEFAULT_PUBLIC_CLIENT_ID:
                route += f" with the sign-in app {PUBLIC_CLIENT_ENV} names"
            token = X._read_cached_token(X._cache_path(self._tenant(), client_id,
                                                       X.DEFAULT_SCOPE))
            if not token:
                # No sign-in yet, and a check never asks for one: what it can reach is
                # probed when the run starts.
                return S.CheckResult(True, f"the fetch {route}{note}; you will be asked to "
                                           "sign in when the run starts, and what you can "
                                           "reach is probed then",
                                     route="your own sign-in",
                                     coverage="probed when the run starts")
        named = {"spn": "service principal", "token": "access token"}.get(mode,
                                                                          "your own sign-in")
        return self._probe(X, token, route + note, settings, named)

    def _probe(self, X: Any, token: str, route: str, settings: dict[str, Any],
               named: str = "") -> S.CheckResult:
        """What this identity can reach, read without fetching content (`X.probe`): the
        workspaces it can see, and whether the admin scanner is open to it."""
        targets = settings.get("targets") or []
        targets = [targets] if isinstance(targets, str) else list(targets)
        workspace = settings.get("workspace")
        if workspace:
            match = X.URL_WS_RE.search(str(workspace))
            workspace = match.group(1) if match else str(workspace).strip()
        spaces = {workspace} if workspace else set()
        for raw in targets:
            try:
                spaces.add(X.parse_target(str(raw), workspace).workspace)
            except ValueError as exc:
                return S.CheckResult(False, f"a target is not understood: {exc}",
                                     fix="settings.powerbi-fabric.targets")
        client = X.FabricClient(token)
        client.max_attempts = 1                  # a check takes seconds, never minutes
        try:
            caps = X.probe(client, sorted(s for s in spaces if s))
        except Exception as exc:  # noqa: BLE001 - the check reports, it never raises
            return S.CheckResult(False, f"the fetch {route}, and Fabric could not be reached: "
                                        f"{type(exc).__name__}",
                                 fix="settings.powerbi-fabric.targets or the network")
        seen = caps.get("workspaces") or {}
        hidden = sorted(w for w, c in seen.items() if not c.get("visible"))
        scanner = caps.get("adminScanner") or {}
        roles = "; ".join(f"role on {w}: {c['role']} ({c.get('roleAllows')})"
                          for w, c in sorted(seen.items()) if c.get("role"))
        said = (f"the fetch {route}; {len(seen) - len(hidden)} of {len(seen)} workspace(s) "
                "visible; " + (roles + "; " if roles else "")
                + "per item it tries getDefinition, then the report's export; admin "
                "scanner " + ("available" if scanner.get("available") else
                              f"not available ({scanner.get('why')})"))
        if hidden:
            return S.CheckResult(False, said + f"; not visible: {', '.join(hidden)} ("
                                 + "; ".join(str(seen[w].get("why")) for w in hidden) + ")",
                                 fix="a workspace role for this identity, or "
                                     "settings.powerbi-fabric.targets")
        return S.CheckResult(True, said, route=named,
                             coverage=("definitions, export fallback, admin scanner"
                                       if scanner.get("available") else
                                       "definitions, export fallback"))

    @staticmethod
    def _tenant() -> str:
        return os.environ.get("PBI_TENANT_ID", "").strip() or "organizations"

    def command(self, dest: pathlib.Path, *, list_only: bool = False) -> list[str]:
        """The extractor command for these settings, signing in as `sign_in()` says. A run
        that is not at a terminal and has neither a service principal nor a token is refused
        (`SignInRefused`): it never waits for a device code nobody will see."""
        targets = self.settings.get("targets") or []
        targets = [targets] if isinstance(targets, str) else list(targets)
        if not targets and not self.settings.get("workspace"):
            raise S.NotSupported("powerbi-fabric: set `targets` or `workspace`")
        mode = sign_in()
        if mode is None:
            raise SignInRefused(f"powerbi-fabric: {NO_SIGN_IN}")
        if mode == "certificate":
            raise S.NotSupported(f"powerbi-fabric: {CERT_REFUSED}")
        if mode == "device" and self.settings.get("sign_in") == "browser":
            mode = "browser"
        cmd = [sys.executable, str(EXTRACTOR), *targets, "--out", str(dest), "--auth", mode,
               "--tenant", self._tenant()]
        if mode not in SIGN_IN_KINDS:
            # A service principal or a handed-over token is never cached; a person's sign-in
            # is, owner-only, so the next run does not ask again.
            cmd.append("--no-token-cache")
        if mode == "browser":
            cmd += ["--sign-in-timeout", str(fetch_wait("fetch.max_sign_in_wait_seconds"))]
        if self.settings.get("workspace"):
            cmd += ["--workspace", str(self.settings["workspace"])]
        if self.settings.get("with_model", True):
            cmd.append("--with-model")
        if list_only:
            cmd.append("--list-only")
        # How long the fetch may wait on Fabric, from the run's policy: never a wait in code.
        cmd += ["--operation-timeout", str(fetch_wait("fetch.max_operation_wait_seconds")),
                "--max-retry-after", str(fetch_wait("fetch.max_retry_after_seconds"))]
        return cmd

    def bi_definitions(self, assets: list[str], dest: pathlib.Path) -> list[pathlib.Path]:
        if self.settings.get("bi"):
            root = pathlib.Path(str(self.settings["bi"])).expanduser()
        else:
            root = pathlib.Path(dest)
            cmd = self.command(root)
            interactive = cmd[cmd.index("--auth") + 1] in SIGN_IN_KINDS
            # A person's sign-in prints its code or its address on stderr, so a person must
            # see stderr.
            proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                                  stderr=None if interactive else subprocess.PIPE, text=True)
            if proc.returncode in (2, 3):
                # The sign-in (2) or every item (3) was refused: an access limit, named, with
                # what to ask for. The run goes on with its other inputs.
                from metrics_agent_powerbi import access as _pbi_access  # noqa: PLC0415

                tail = (proc.stderr or "").strip().splitlines()[-3:]
                raise S.AccessBlocked(
                    _pbi_access.whole_fetch_refused(root, proc.stderr or "", proc.returncode),
                    "the Fabric extract failed: " + (" | ".join(tail) if tail else
                                                     f"exit {proc.returncode}; its messages "
                                                     "are above"))
            if proc.returncode not in (0, 1):      # 1: fetched, but not every part
                tail = (proc.stderr or "").strip().splitlines()[-3:]
                raise RuntimeError("the Fabric extract failed: " + (
                    " | ".join(tail) if tail else f"exit {proc.returncode}; its messages are "
                                                  "above"))
        return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]

    @staticmethod
    def access_blockers(root: pathlib.Path) -> list[dict[str, Any]]:
        """What the fetch under `root` could not read, item by item, as the run's access
        blockers (`sockets.access_blockers`), from the extractor's manifest."""
        from metrics_agent_powerbi import access as _pbi_access  # noqa: PLC0415

        return _pbi_access.blockers(root)

    def query_history(self, tables: list[str], window: S.Window):
        raise S.NotSupported("powerbi-fabric holds no query history")

    def table_schema(self, tables: list[str], *, max_description_chars: int) -> S.SchemaResult:
        raise S.NotSupported("powerbi-fabric holds no warehouse schema")

    def sql_runner(self):
        return None

    def resolve_scope(self, refs: list[str]) -> S.Scope:
        raise S.NotSupported("powerbi-fabric does not resolve asset names")
