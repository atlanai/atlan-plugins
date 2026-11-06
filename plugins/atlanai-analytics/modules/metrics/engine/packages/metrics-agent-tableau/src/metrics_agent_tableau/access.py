# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a Tableau fetch could not read, as the run's access blockers (`lib/access`).

The fetch records how it signed in, what that sign-in may do (`capabilities`: the site role,
the Metadata API) and every object it could not obtain, with why and the access to ask for
(`manifest.json` `failed`, `datasources[].fidelity`). This module turns those into neutral
access codes. Tableau's own answers it reads (checked live through the connected app as
Creator, Explorer, Viewer and Unlicensed, and on items whose permissions were changed; see
tests/test_access_matrix):

    401001                  sign-in refused (wrong secret, a disabled connected app, a user
                            who is Unlicensed)
    401002                  the connected app's token has no scope for this call
    403019 / 403028         workbook / data source download refused: the site role (Viewer,
                            Unlicensed) never downloads; otherwise the item's Download
                            permission is missing
    404                     not found, or not visible to this user
    Metadata API off        no rebuild for a data source whose download is refused
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

from metrics_agent.lib import access as _access

SOURCE = "tableau-cloud"
STAGE = "s_fetch_bi"
NO_DOWNLOAD_ROLES = {"viewer", "unlicensed", "readonly"}


def _role(caps: dict[str, Any]) -> str:
    return str(caps.get("siteRole") or "")


def _seat(caps: dict[str, Any]) -> bool:
    return _role(caps).replace(" ", "").lower() in NO_DOWNLOAD_ROLES


def _make(code: str, item: dict, not_read: str, ask: str, **kw) -> dict[str, Any]:
    return _access.blocker(code, source=SOURCE, stage=STAGE, item=item, not_read=not_read,
                           ask=ask, **kw)


def failed_blocker(f: dict[str, Any], caps: dict[str, Any]) -> dict[str, Any]:
    """One object the fetch could not obtain, as a blocker."""
    kind = {"datasource": "data source", "virtualConnection": "virtual connection"}.get(
        str(f.get("kind")), str(f.get("kind") or "item"))
    item = {"kind": kind, "name": f.get("name"), "id": f.get("luid")}
    status, reason, fix = f.get("status"), str(f.get("reason") or ""), str(f.get("fix") or "")
    detail = (f"{status} " if status else "") + reason
    partial = f.get("capability") == "partial"
    what = (f"the {kind}'s file" if kind != "workbook" else "the workbook")
    if status == 403 and _seat(caps):
        return _make("seat_cannot_download", item,
                     f"{what} (site role {_role(caps)} can never download)",
                     fix or "an Explorer or Creator seat for the fetching user",
                     detail=detail)
    if status == 403:
        rebuilt = "Metadata API" in reason
        return _make("download_refused", item,
                     what + (" (its download was refused and its rebuild from the Metadata "
                             "API failed)" if rebuilt else " (its download was refused)"),
                     fix or f"the Download permission on the {kind} for the fetching user",
                     detail=detail)
    if status == 404:
        return _make("no_access_to_item", item, what,
                     f"View permission on the {kind} for the fetching user (or check its name)",
                     who="bi_item_owner", detail=detail)
    if status == 401:
        return _make("missing_scope", item, what,
                     fix or "the connected app's scopes for this call, from a site admin",
                     detail=detail)
    return _make("fetch_incomplete", item,
                 what + (" (only part of it: " + reason + ")" if partial else ""),
                 fix or "forward this answer to the site admin", detail=detail,
                 effect="partial" if partial else None)


def blockers(root: pathlib.Path) -> list[dict[str, Any]]:
    """Every limit the fetch under `root` met, from its manifest. [] with no manifest."""
    try:
        manifest = json.loads((pathlib.Path(root) / "manifest.json").read_text())
    except (OSError, ValueError):
        return []
    caps = manifest.get("capabilities") or {}
    out = [failed_blocker(f, caps) for f in manifest.get("failed") or []
           if isinstance(f, dict)]
    for d in manifest.get("datasources") or []:
        if isinstance(d, dict) and d.get("fidelity") == "reduced":
            out.append(_make(
                "download_refused",
                {"kind": "data source", "name": d.get("name"), "id": d.get("luid")},
                "the data source's file: rebuilt from the Metadata API instead, without "
                + (", ".join(d.get("lost") or []) or "what only the file holds"),
                "the Download permission on the data source for the fetching user",
                effect="partial",
                did_instead="rebuilt the data source's fields, formulas and tables from the "
                            "Metadata API and read that (reduced fidelity)"))
    meta = caps.get("metadataApi") or {}
    refused = any(f.get("status") == 403 and f.get("kind") == "datasource"
                  for f in manifest.get("failed") or [] if isinstance(f, dict))
    if meta.get("available") == "no" and refused:
        out.append(_make("catalog_api_unavailable", {"kind": "site", "name": None},
                         "the Metadata API, the fallback for a data source whose download "
                         "is refused",
                         "the Metadata API for this site (Tableau Cloud: the Data Management "
                         "add-on; Tableau Server: the metadata services), or the Download "
                         "permission on each data source named here",
                         who="bi_site_admin",
                         detail=str(meta.get("why") or "")[:300] or None))
    if not _role(caps) and caps.get("route") == "connected-app":
        out.append(_make("missing_scope", {"kind": "user", "name": None},
                         "the fetching user's site role (a refused download cannot be told "
                         "apart: seat or permission)",
                         "the scope tableau:users:read on the connected app, from a site admin",
                         effect="partial", did_instead="decided each refused download by its "
                                                       "own answer"))
    pulse = manifest.get("pulse") or {}
    why = str(pulse.get("why") or "")
    if pulse.get("available") == "no" and ("401" in why or "403" in why):
        out.append(_make("missing_scope", {"kind": "metric definitions", "name": "Pulse"},
                         "the site's Pulse metric definitions",
                         "the scope tableau:insight_definitions_metrics:read on the connected "
                         "app (or Pulse access for the user), from a site admin",
                         detail=why[:300], did_instead="read the workbooks and data sources "
                                                       "without the Pulse metrics"))
    return _access.merge([], out)


def sign_in_refused(exc: Exception, route: str | None) -> list[dict[str, Any]]:
    """A refused sign-in (no session at all) as the one blocker it is."""
    status, code = getattr(exc, "status", None), getattr(exc, "code", "")
    fix = getattr(exc, "fix", "") or ("check the connected app (enabled, its secret, the "
                                      "user's site role) or the personal access token")
    return [_make("sign_in_failed", {"kind": "site", "name": None},
                  "everything this source would have fetched",
                  fix, who="bi_site_admin" if route == "connected-app" else "operator",
                  detail=" ".join(str(x) for x in (status, code) if x) or str(exc)[:200])]


def nothing_fetched(targets: list[str]) -> list[dict[str, Any]]:
    """The blocker for a fetch that obtained no workbook and named no reason."""
    return [_make("fetch_incomplete", {"kind": "workbook", "name": ", ".join(targets) or None},
                  "every workbook the settings name", "check the workbook names and the "
                  "fetching user's View permission on them", who="bi_item_owner")]


# The value tools' refusals (the MCP route's get-view-data and query-datasource), as the
# access code a capture's gap carries. Matched on the words Tableau's documentation gives for
# each; the exact answers are not recorded on our site (no site setting is changed for a test).
_VALUE_REFUSALS = (
    ("value_service_off", ("vizql data service", "viz_data_service", "data api access",
                           "vizqldataapiaccess")),
    ("source_credentials_missing", ("embedded credential", "credentials are missing",
                                    "missing credential", "authentication required",
                                    "sign in to the data source")),
    ("missing_scope", ("401002", "scope")),
)


def value_refusal_code(text: str) -> str | None:
    """The access code for a value tool's error text, or None (an ordinary tool error)."""
    low = str(text or "").lower()
    for code, words in _VALUE_REFUSALS:
        if any(w in low for w in words):
            return code
    return None
