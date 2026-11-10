# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a Power BI fetch could not read, as the run's access blockers (`lib/access`).

The extractor records, per item, the route that produced it and what each route answered
when none did (`manifest.json` `unavailable`, `items[].refused`). This module turns those
answers into neutral access codes, with what was not read, what the fetch did instead and
exactly what to ask for, and from whom. The answers it reads (checked live as a workspace
Admin, the rest from Microsoft's documentation, each recorded in tests/test_access_matrix):

    401 InvalidToken / TokenExpired      the token: expired, another API's, another tenant's
    404 EntityNotFound / ItemNotFound    no role on the workspace, or the item is not visible
    403 on getDefinition                 needs write on the item: Contributor or higher
                                         (a Viewer), or an encrypted sensitivity label
    403 on export                        the report does not allow download (no Build), or
                                         the tenant's "download reports" setting is off
    401 PowerBINotAuthorizedException    no permission on the item (no role, no Build), or,
                                         for a composite model, on a model it reads
    upstream_model_not_visible           a composite model's upstream model is unreachable
    scanner 401/403                      not a Power BI admin: no scanner metadata

A model whose definition carries row-level security roles is read as it is; the values a
viewer sees depend on their role, which the run cannot check (`row_level_security`).
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from metrics_agent.lib import access as _access

SOURCE = "powerbi-fabric"
STAGE = "s_fetch_bi"
_STATUS = re.compile(r"^\s*(\d{3})\s+([A-Za-z]+)?")
TOKEN_CODES = ("InvalidToken", "TokenExpired")
ROLE_FIX = ("a workspace role of Contributor or higher on the workspace (getDefinition reads "
            "an item's definition only with write permission)")
LABEL_FIX = ("or, for an item with an encrypted sensitivity label, usage rights to the label")


def _kind(kind: str | None) -> str:
    return {"reports": "report", "semanticModels": "semantic model",
            "upstreamModel": "semantic model", None: "workspace"}.get(kind, str(kind))


def _answer(why: str) -> tuple[int | None, str]:
    m = _STATUS.match(why or "")
    return (int(m.group(1)), m.group(2) or "") if m else (None, "")


def _bound_models(root: pathlib.Path) -> set[str]:
    """The semantic model ids the fetched reports bind to (`definition.pbir`)."""
    out: set[str] = set()
    for pbir in pathlib.Path(root).glob("reports/*/*/definition.pbir"):
        try:
            blob = json.loads(pbir.read_text())
        except (OSError, ValueError):
            continue
        conn = (((blob.get("datasetReference") or {}).get("byConnection") or {})
                .get("connectionString") or "")
        for part in str(conn).split(";"):
            if part.strip().lower().startswith("semanticmodelid="):
                out.add(part.split("=", 1)[1].strip().lower())
    return out


def _rls_models(root: pathlib.Path) -> list[tuple[str, list[str]]]:
    """(model folder, role names) for each fetched model whose definition declares
    row-level security roles."""
    out = []
    for model in sorted(pathlib.Path(root).glob("semanticModels/*/*")):
        roles = sorted(p.stem for p in model.glob("definition/roles/*.tmdl"))
        if roles:
            out.append((model.name, roles))
    return out


def blocker_for(why: str, *, route: str, kind: str | None, item_id: str | None,
                name: str | None, fetched: bool, bound: bool = False) -> dict[str, Any]:
    """One refused route of one item, as a blocker."""
    status, code = _answer(why)
    item = {"kind": _kind(kind), "name": name, "id": item_id}
    what = f"the {_kind(kind)}'s definition"

    def make(acode: str, not_read: str, ask: str, **kw):
        kw.setdefault("item", item)
        return _access.blocker(acode, source=SOURCE, stage=STAGE, not_read=not_read,
                               ask=ask, detail=why, **kw)

    if kind == "upstreamModel" or why.startswith("upstream_model_not_visible"):
        return make("upstream_model_unavailable",
                    "the tables and measures the composite model reads from this model",
                    "access to the upstream model: a workspace role (Contributor or higher) "
                    "on its workspace, or Build permission on the model, for the fetching "
                    "identity")
    if code in TOKEN_CODES:
        return make("sign_in_failed", "everything this identity would have fetched",
                    "a fresh sign-in (the token expired, or was issued for another API or "
                    "another tenant): set PBI_ACCESS_TOKEN again, or use the service "
                    "principal", who="operator")
    if route == "export":
        return make("download_refused", "the report's .pbix file (its definition was refused "
                    "too, so the report is not read)",
                    "Build permission on the report's model or the report's download "
                    "permission, from the item owner; where every download is refused, the "
                    "Power BI tenant setting that lets users download reports, from a tenant "
                    "admin", effect="not_read")
    if route == "scanner":
        return make("catalog_api_unavailable", "the admin scanner's model metadata (a fallback "
                    "for an item whose definition is refused)",
                    "a Power BI admin to run the scan, or the Fabric administrator role for "
                    "the fetching service principal (tenant setting: service principals can "
                    "use read-only admin APIs)", item={"kind": "tenant", "name": None})
    if status == 404:
        if bound:
            return make("bound_model_unreachable",
                        "the semantic model the report binds to (in another workspace, or "
                        "one this identity has no role on)",
                        "a workspace role (Contributor or higher) on the model's workspace "
                        "for the fetching identity, and that workspace's id in `targets`")
        return make("no_access_to_item", what,
                    f"a workspace role on the {_kind(kind)}'s workspace for the fetching "
                    "identity (or check the id)", who="bi_workspace_admin")
    if status == 403:
        return make("definition_needs_edit_rights", what,
                    f"{ROLE_FIX}, {LABEL_FIX}",
                    effect="partial" if fetched else "not_read",
                    did_instead=("exported the report's .pbix and read it instead"
                                 if fetched else
                                 "tried the item's file download; it was not available"))
    if status == 401:
        return make("no_access_to_item", what,
                    "a workspace role on the item's workspace, or Build permission on the "
                    "model, for the fetching identity", who="bi_workspace_admin")
    return make("fetch_incomplete", what,
                "re-run the fetch; if it fails again, forward this answer to the workspace "
                "admin", who="operator")


def blockers(root: pathlib.Path) -> list[dict[str, Any]]:
    """Every limit the fetch under `root` met, from its manifest. [] with no manifest."""
    root = pathlib.Path(root)
    try:
        manifest = json.loads((root / "manifest.json").read_text())
    except (OSError, ValueError):
        return []
    items = {str(i.get("itemId") or ""): i for i in manifest.get("items") or []
             if isinstance(i, dict)}
    bound = _bound_models(root)
    out: list[dict[str, Any]] = []
    lost_definition = False
    scanner = []
    for u in manifest.get("unavailable") or []:
        if not isinstance(u, dict):
            continue
        route, why = str(u.get("route") or ""), str(u.get("why") or "")
        if route == "scanner":
            scanner.append(u)
            continue
        if route == "list":
            out.append(blocker_for(why, route=route, kind=None, item_id=None, name=None,
                                   fetched=False))
            continue
        item_id = str(u.get("itemId") or "") or None
        rec = items.get(item_id or "", {})
        fetched = rec.get("outcome") == "fetched"
        if route == "getDefinition" and not fetched:
            lost_definition = True
        if route == "export" and not fetched:
            lost_definition = True
        out.append(blocker_for(why, route=route, kind=u.get("kind"), item_id=item_id,
                               name=u.get("displayName") or rec.get("displayName"),
                               fetched=fetched,
                               bound=(u.get("kind") == "semanticModels"
                                      and (item_id or "").lower() in bound)))
    if lost_definition:
        # The scanner matters only when an item's definition was refused: it is then the
        # one route left to its model metadata.
        out += [blocker_for(str(u.get("why") or ""), route="scanner", kind=None,
                            item_id=None, name=None, fetched=False) for u in scanner[:1]]
    for model, roles in _rls_models(root):
        out.append(_access.blocker(
            "row_level_security", source=SOURCE, stage=STAGE,
            item={"kind": "semantic model", "id": model, "name": None},
            not_read=("the values each viewer sees: the model filters rows by role ("
                      + ", ".join(roles) + ")"),
            ask=("which role applies to which viewers, from the model owner; the SQL is "
                 "compiled without the role filter")))
    return _access.merge([], out)


def whole_fetch_refused(root: pathlib.Path, stderr: str, code: int) -> list[dict[str, Any]]:
    """The blockers for an extractor that read nothing: exit 2 (the sign-in itself was
    refused) or 3 (every item, or the listing, was refused)."""
    if code == 3:
        got = blockers(root)
        if got:
            return got
        return [_access.blocker("no_access_to_item", source=SOURCE, stage=STAGE,
                                item={"kind": "workspace"},
                                not_read="every item the targets name (the definition API "
                                         "refused them all)",
                                ask="a workspace role (Contributor or higher) on the "
                                    "targets' workspace for the fetching identity",
                                who="bi_workspace_admin")]
    last = next((ln for ln in reversed((stderr or "").splitlines())
                 if ln.lower().startswith("auth error")), "")
    return [_access.blocker("sign_in_failed", source=SOURCE, stage=STAGE,
                            item={"kind": "tenant"},
                            not_read="everything this source would have fetched",
                            ask="a working sign-in: the service principal's secret, a fresh "
                                "PBI_ACCESS_TOKEN for the Power BI API in the right tenant, "
                                "or a person's sign-in at a terminal",
                            detail=last.split(":", 1)[-1].strip() or None)]


# The value tools' refusals (the MCP route's Execute Query / dax_query_operations), as the
# access code a capture's gap carries. Recorded live: the remote MCP endpoint answers JSON-RPC
# -32003 "The feature is not available" (FeatureNotAvailable) while the tenant setting is off;
# executeQueries answers 401 PowerBINotAuthorizedException without Build permission (and, on a
# composite model, without access to a model it reads).
_VALUE_REFUSALS = (
    ("value_service_off", ("featurenotavailable", "feature is not available", "-32003")),
    ("model_not_queryable", ("powerbinotauthorized", "build permission", "not authorized")),
)


def value_refusal_code(text: str) -> str | None:
    """The access code for a value tool's error text, or None (an ordinary tool error)."""
    low = str(text or "").lower()
    for code, words in _VALUE_REFUSALS:
        if any(w in low for w in words):
            return code
    return None
