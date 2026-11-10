# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The `tableau-cloud` input plug: Tableau workbooks and their published data sources, fetched
from Tableau Cloud or Tableau Server at the start of a run.

Custody `customer`: it signs in to the customer's own Tableau site with a Personal Access Token
of theirs and calls Tableau's public REST API (and, only when a data source download is
refused, its Metadata API). No Atlan tenant is involved.

It writes, under the run's `inputs/bi/tableau-cloud/`, one folder per workbook holding the
workbook and every published data source it uses (and those they are built on), which is the
layout the core's Tableau reader reads, plus a payload-free `manifest.json` (`fetch.py`).

Settings (`settings.tableau-cloud` in run.yml; never a credential):
    workbooks    the workbooks to fetch: URLs on the configured server, LUIDs, or
                 `project/name` (a list)
    bi           an extract already on disk: returned as is, nothing is fetched
    model        when several workbooks are fetched, the folder (workbook name) this run reads
    api_version  the REST API version (default: `tableau.api_version` in the policy)
    metadata_api false: never ask the Metadata API (a site without Data Management, or an
                 operator who wants the downloads only); the check and the manifest say so
    viewer       true: compile the user functions of a domain or a group (USERDOMAIN(),
                 ISMEMBEROF(...)) for the identity the fetch signs in as: its domain and groups
                 are written to `viewer.json` beside each workbook (never its name). Default
                 false: such a widget is refused by name. USERNAME() and FULLNAME() are
                 always compiled as bind placeholders; with `viewer: true` verification binds
                 them to this identity's names, read again in memory (`viewer_names`), never
                 written
    time_zone    with `viewer: true`, the IANA time zone the site reads the current moment in
                 (for example America/Los_Angeles), written to `viewer.json`: a relative date
                 filter counts its periods from the date there, and "to date" on a date and
                 time ends at that moment there. Unset: the warehouse session's own clock

Environment: `TABLEAU_SERVER_URL` (https://host, no path), `TABLEAU_SITE` (the site's content
URL; unset or empty for the Default site of a Tableau Server), and ONE complete set of
credentials. Nobody says which kind: the plug signs in by the first route the environment
completes (`rest.ROUTES`):
    a Personal Access Token      TABLEAU_PAT_NAME + TABLEAU_PAT_SECRET
    a connected app (JWT)        TABLEAU_CONNECTED_APP_CLIENT_ID + _SECRET_ID + _SECRET_VALUE
                                 + TABLEAU_USERNAME (the user it acts for)
    a user name and password     TABLEAU_USERNAME + TABLEAU_PASSWORD (Tableau Server; Tableau
                                 Cloud only without multi-factor authentication or SSO)
After the sign-in it probes what that sign-in may do (`rest.probe`) and takes the richest path
per object: the download, else the Metadata API, else the object is recorded as unavailable
and the fetch goes on with what it obtained.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
from typing import Any

from metrics_agent import sockets as S
from metrics_agent_tableau import rest as R

NAME = "tableau-cloud"


def describe(caps: dict[str, Any]) -> str:
    """The probed capabilities in one sentence, for the check."""
    words = {"downloadWorkbooks": "download workbooks",
             "downloadDatasources": "download data sources", "metadataApi": "Metadata API"}
    parts = [f"{label}: {(caps.get(key) or {}).get('available', 'unknown')} "
             f"({(caps.get(key) or {}).get('why', '')})" for key, label in words.items()]
    return "; ".join(parts)


class TableauCloud:
    name = NAME
    kind = "input"
    custody = "customer"
    capabilities = ("bi_definitions",)
    bi_vendor = "tableau"
    # With `workbooks` and no `bi`, the run fetches the definitions itself (`s_fetch_bi`, the
    # first step of `metrics-agent run`) into the run directory.
    fetch_settings = ("workbooks",)
    # `workbooks` names each workbook to read, so a run reads every one fetched (one model
    # each, merged by A1); `model` still narrows it to one.
    fetch_reads_all = True
    requires_env: tuple[str, ...] = R.ENV
    # The ways it signs in, in the order it tries them; the interview lists them, never asks.
    credential_routes = (
        S.CredentialRoute("personal access token",
                          (R.SERVER_VAR, R.PAT_NAME_VAR, R.PAT_SECRET_VAR)),
        S.CredentialRoute('supplied JWT', (R.SERVER_VAR, R.JWT_VAR)),
        S.CredentialRoute('unified access token JWT', (R.SERVER_VAR, R.UAT_VAR)),
        S.CredentialRoute("connected app", (R.SERVER_VAR, R.CA_CLIENT_VAR, R.CA_SECRET_ID_VAR,
                                            R.CA_SECRET_VALUE_VAR, R.USERNAME_VAR)),
        S.CredentialRoute("username and password",
                          (R.SERVER_VAR, R.USERNAME_VAR, R.PASSWORD_VAR),
                          note="Tableau Server, or Tableau Cloud without multi-factor sign-in"),
    )
    setup_questions = {
        "bi": (S.SetupQuestion("workbooks", "Workbook URLs, LUIDs or project/name to fetch from "
                               "Tableau at the start of the run (blank: you have files on "
                               "disk)", required=False, kind="list"),
               S.SetupQuestion("bi", "Path to a Tableau workbook with its data sources on disk "
                               "(blank when the run fetches)", required=False, kind="path")),
    }

    def __init__(self, settings: dict[str, Any] | None = None, *, dialect: str = "") -> None:
        self.settings = dict(settings or {})

    @classmethod
    def validate_settings(cls, role: str, settings: dict[str, Any]) -> str | None:
        """Files on disk (`bi`) OR a fetch at the start of the run (`workbooks`)."""
        if role == "bi" and not (settings.get("bi") or settings.get("workbooks")):
            return (f"plug '{NAME}' needs settings.{NAME}.bi (files on disk), or "
                    f"settings.{NAME}.workbooks to fetch them")
        return None

    @classmethod
    def env_missing(cls, use: str | None = None) -> tuple[str, ...]:
        """Files already on disk need no credential; only a fetch does, and `check` says
        which variables it is missing. `TABLEAU_SITE` is optional (the Default site)."""
        return ()

    @staticmethod
    def fetched_models(root: pathlib.Path) -> list[pathlib.Path]:
        """One folder per fetched workbook (`workbooks/<name>/`), each a model the reader
        reads: the workbook with its data sources beside it."""
        return sorted(p for p in (pathlib.Path(root) / "workbooks").glob("*") if p.is_dir())

    # -- settings ------------------------------------------------------------------------
    def _targets(self) -> list[str]:
        t = self.settings.get("workbooks") or []
        if isinstance(t, str):
            t = [x.strip() for x in t.split(",")]
        return [str(x) for x in t if str(x).strip()]

    def _metadata_api(self) -> bool:
        """`metadata_api: false` never asks the Metadata API (default: asked)."""
        return self.settings.get("metadata_api", True) is not False

    def _viewer(self) -> bool:
        """`viewer: true` records the signed-in identity's domain and groups for the user
        functions (default: not recorded)."""
        return self.settings.get("viewer") is True

    def _time_zone(self) -> str | None:
        """`time_zone`: the IANA name of the time zone the site reads "now" in, or None."""
        tz = self.settings.get("time_zone")
        if tz is None:
            return None
        if not isinstance(tz, str) or not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+)*", tz) \
                or len(tz) > 64:
            raise S.NotSupported(f"{NAME}: `time_zone` is an IANA time zone name such as "
                                 f"America/Los_Angeles")
        return tz

    def _api_version(self) -> str:
        default = '3.27' if R.detect_route() == 'unified-access-token' else R.policy_value('api_version')
        version = str(self.settings.get("api_version") or default)
        if R.detect_route() == 'unified-access-token' and (not R.API_VERSION.fullmatch(version) or tuple(map(int, version.split('.'))) < (3, 27)):
            raise R.TableauError('Unified access token sign-in needs REST API 3.27 or newer', fix='settings.tableau-cloud.api_version')
        return version

    # -- check ---------------------------------------------------------------------------
    def check(self, settings: dict[str, Any]) -> S.CheckResult:
        """Files on disk exist; otherwise the server URL is checked, every workbook target is
        understood, the credentials the environment completes sign in once, what that
        sign-in may do is probed (site role, Metadata API), and it signs out. No content is
        read."""
        if settings.get("bi"):
            ok = pathlib.Path(str(settings["bi"])).expanduser().exists()
            return S.CheckResult(ok, "files found" if ok else "the `bi` path does not exist",
                                 fix="" if ok else f"settings.{NAME}.bi")
        missing = R.missing_for_sign_in()
        if missing:
            return S.env_refusal(self.name, missing)
        if not self._targets():
            return S.CheckResult(False, "no workbook to fetch", fix=f"settings.{NAME}.workbooks")
        try:
            server = R.server_from_env()
            from metrics_agent_tableau import fetch as F  # noqa: PLC0415

            site = (os.environ.get(R.SITE_VAR) or "").strip()
            for t in self._targets():
                F.parse_target(t, server, site)
            with R.open_session(api_version=self._api_version()) as session:
                site_name = session.site_content_url or "the Default site"
                caps = R.probe(session, metadata_api=settings.get("metadata_api", True)
                               is not False)
        except R.TableauError as exc:
            return S.CheckResult(False, str(exc), fix=exc.fix or R.SERVER_VAR)
        except Exception as exc:  # noqa: BLE001 - the check reports, it never raises
            return S.CheckResult(False, f"the Tableau check failed: {type(exc).__name__}",
                                 fix=R.SERVER_VAR)
        seat = R.seat_refusal(caps)
        if seat:
            # The site role, not a permission, rules every download out: the fetch would
            # obtain nothing. Said now, before a run starts.
            return S.CheckResult(False, f"signed in to {site_name} by {caps['route']}, but "
                                        f"{seat}", fix="the user's site role")
        return S.CheckResult(True, f"signed in to {site_name} by {caps['route']} and out "
                                   f"again; {len(self._targets())} workbook target(s) "
                                   f"understood; {describe(caps)}",
                             route=str(caps["route"]).replace("-", " "),
                             coverage=describe(caps))

    # -- the capability ------------------------------------------------------------------
    def bi_definitions(self, assets: list[str], dest: pathlib.Path) -> list[pathlib.Path]:
        if self.settings.get("bi"):
            root = pathlib.Path(str(self.settings["bi"])).expanduser()
        else:
            targets = self._targets()
            if not targets:
                raise S.NotSupported(f"{NAME}: set `workbooks`, or `bi` for files on disk")
            from metrics_agent_tableau import fetch as F  # noqa: PLC0415

            from metrics_agent_tableau import access as _tab_access  # noqa: PLC0415

            root = pathlib.Path(dest)
            try:
                session_cm = R.open_session(api_version=self._api_version())
                session = session_cm.__enter__()
            except R.TableauError as exc:
                if exc.status not in (401, 403):
                    raise
                # The sign-in itself was refused: an access limit, named, with what to ask
                # for. The run goes on with its other inputs.
                route = ("connected-app" if os.environ.get("TABLEAU_CONNECTED_APP_CLIENT_ID")
                         else None)
                raise S.AccessBlocked(_tab_access.sign_in_refused(exc, route),
                                      f"{NAME}: the sign-in was refused: {exc}") from None
            try:
                manifest = F.Fetcher(session,
                                     max_depth=R.policy_value("max_datasource_depth"),
                                     max_bytes=R.policy_value("max_download_bytes"),
                                     capabilities=R.probe(
                                         session, metadata_api=self._metadata_api()),
                                     record_viewer=self._viewer(),
                                     time_zone=self._time_zone()
                                     ).run(targets, root)
            finally:
                session_cm.__exit__(None, None, None)
            lines = [f"  - {f['kind']} {f.get('name')!r}: {f['reason']}"
                     + (f" -> {f['fix']}" if f.get("fix") else "")
                     for f in manifest["failed"]]
            if not manifest["workbooks"] and not any(
                    f.get("status") in (401, 403, 404) for f in manifest["failed"]):
                # Nothing read, and no access limit said why (a target that names no
                # workbook on this site): the settings are wrong, refused as before.
                raise R.TableauError(
                    "the Tableau fetch obtained no workbook, so there is nothing to read:\n"
                    + "\n".join(lines) + f"\nDetails: {root / 'manifest.json'}")
            if not manifest["workbooks"]:
                # Every workbook was refused by an access limit: named, the run goes on.
                raise S.AccessBlocked(
                    _tab_access.blockers(root) or _tab_access.nothing_fetched(targets),
                    "the Tableau fetch obtained no workbook, so there is nothing to read:\n"
                    + "\n".join(lines) + f"\nDetails: {root / 'manifest.json'}")
            if lines:
                # Going on with what was obtained, said out loud: a worksheet on a missing
                # data source resolves to no table, and the run must not read as complete.
                print(f"{NAME}: the fetch is partial; it continues with what it obtained. "
                      "Not available, and the access to ask for:\n" + "\n".join(lines)
                      + f"\nDetails: {root / 'manifest.json'}", file=sys.stderr)
        return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]

    @staticmethod
    def access_blockers(root: pathlib.Path) -> list[dict[str, Any]]:
        """What the fetch under `root` could not read, item by item, as the run's access
        blockers (`sockets.access_blockers`), from the fetch's manifest."""
        from metrics_agent_tableau import access as _tab_access  # noqa: PLC0415

        return _tab_access.blockers(root)

    def viewer_names(self) -> dict[str, str] | None:
        """The identity the fetch signs in as, by the names Tableau's USERNAME() and
        FULLNAME() return for it (checked live: the user's site name and display name), for
        verification to bind a statement filtered to the viewing user. Only with
        `viewer: true`. Returned IN MEMORY to the caller, never written, logged or kept on
        the plug (guardrail 8). None when not asked, files on disk, or the user is unread."""
        if not self._viewer() or self.settings.get("bi"):
            return None
        try:
            with R.open_session(api_version=self._api_version()) as session:
                uid = session.user_id or ""
                if not R.LUID.match(uid):
                    return None
                user = (session.get_json(f"/sites/{R.segment(session.site_id, 'site id')}"
                                         f"/users/{R.segment(uid, 'user id')}")
                        .get("user") or {})
        except R.TableauError:
            return None
        names = {"user_name": user.get("name"), "full_name": user.get("fullName")}
        return {k: str(v) for k, v in names.items() if v} or None

    def query_history(self, tables: list[str], window: S.Window):
        raise S.NotSupported(f"{NAME} holds no query history")

    def table_schema(self, tables: list[str], *, max_description_chars: int) -> S.SchemaResult:
        raise S.NotSupported(f"{NAME} holds no warehouse schema")

    def sql_runner(self):
        return None

    def resolve_scope(self, refs: list[str]) -> S.Scope:
        raise S.NotSupported(f"{NAME} does not resolve asset names")
