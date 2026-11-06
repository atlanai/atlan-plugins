# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Fetch Tableau workbooks and every published data source they use, into the folder layout
the core's Tableau reader reads.

    <dest>/
      manifest.json                         what was fetched, from where, what failed; no content
      workbooks/<workbook>/
          <workbook>.twb(x)                 downloaded with includeExtract=false
          <data source>.tds(x)              each published data source it uses, beside it,
                                            and any published data source those are built on

WHY THE DATA SOURCES. A workbook on a PUBLISHED data source carries no warehouse connection:
its data source is a `sqlproxy` connection to the server, and the real connection lives in
the data source's own file. The reader resolves the workbook's reference against the .tds/.tdsx
files beside it, by name; without them every worksheet parses and resolves to nothing.

HOW A REFERENCE IS FOUND. In the workbook's XML, every `<datasource>` whose connection has
class `sqlproxy`: its `repository-location` id (the data source's content URL), the
connection's `dbname` (the same), and its `server-ds-friendly-name` or caption (its name). The
data source is looked up on the site by content URL, then by name, and downloaded. A
downloaded data source that is itself on a `sqlproxy` connection (a chain) is followed the
same way, to `tableau.max_datasource_depth`.

WHEN A DOWNLOAD IS REFUSED (HTTP 403: no Download Data Source permission), the data source is
rebuilt from the Metadata API (`metadata.py`): its fields, calculated-field formulas and
upstream tables, written as a .tds the reader reads, and marked in the manifest as reduced
fidelity. When that is unavailable too, the data source is recorded as unavailable, with why
and what access to ask for, and the fetch goes on with what it obtained. Never silently: each
object in the manifest says the capability it came through (`rest-download`, `metadata-api`
or `unavailable`), the manifest says how the run signed in, what that sign-in may do and its
coverage, and the plug says out loud what is missing.

UNTRUSTED DATA. Everything a server sends is data: names become file names only after
`safe_file_name`, ids become URL segments only as LUIDs, a reference to another server is
never followed, and XML that declares a DTD is refused before it is parsed.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from xml.parsers import expat
from typing import Any

from metrics_agent_tableau import metadata as M
from metrics_agent_tableau import rest as R

PRIVATE_DIR, PRIVATE_FILE = 0o700, 0o600
WORKBOOK_SUFFIXES = (".twb", ".twbx")
DATASOURCE_SUFFIXES = (".tds", ".tdsx")
MAX_XML_MEMBER_BYTES = 64 * 1024 * 1024        # the reader's own cap on one XML member


# ── XML, read safely ─────────────────────────────────────────────────────────────────────
class UnsafeXML(R.TableauError):
    pass


def _refuse_declarations(label: str, data: bytes) -> None:
    """Refuse a document that declares a DTD before the tree parser sees it.

    PROVENANCE: a copy of the Tableau reader's `_refuse_declarations`
    (`metrics_agent/plugs/readers/tableau.py`, rule 6). Tableau never writes a DOCTYPE, so one
    is refused outright: entity expansion and external entities start there."""
    def refuse(what: str):
        def handler(*_args) -> None:
            raise UnsafeXML(f"{label}: declares {what}; a Tableau file never carries one. "
                            "Refused before parsing.")
        return handler

    p = expat.ParserCreate()
    p.StartDoctypeDeclHandler = refuse("a DOCTYPE")
    p.EntityDeclHandler = refuse("an ENTITY")
    p.ExternalEntityRefHandler = refuse("an external entity reference")
    try:
        p.Parse(data, True)
    except expat.ExpatError:
        return


def tableau_root(label: str, data: bytes, inner: tuple[str, ...]) -> tuple[str, ET.Element]:
    """(member name, XML root) of a downloaded file: a packaged one (a zip holding a .twb or
    .tds) or the XML itself. `inner` is the suffix the member must have."""
    if data[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise R.TableauError(f"{label} is not a readable package") from None
        with z:
            for info in z.infolist():
                if not info.filename.lower().endswith(inner) or "/" in info.filename.strip("/"):
                    continue
                if info.file_size > MAX_XML_MEMBER_BYTES:
                    raise R.TableauError(f"{label}: {info.filename!r} is over the "
                                         f"{MAX_XML_MEMBER_BYTES}-byte cap")
                with z.open(info) as src:
                    xml = src.read(MAX_XML_MEMBER_BYTES + 1)
                if len(xml) > MAX_XML_MEMBER_BYTES:
                    raise R.TableauError(f"{label}: {info.filename!r} inflates past the cap")
                return info.filename, _parse(label, xml)
        raise R.TableauError(f"{label} is a package with no {'/'.join(inner)} inside")
    return label, _parse(label, data)


def _parse(label: str, data: bytes) -> ET.Element:
    _refuse_declarations(label, data)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise R.TableauError(f"{label} is not readable XML ({exc.msg})") from None


def vc_refs(root: ET.Element) -> list[dict[str, str]]:
    """Every virtual connection an XML root reads through: `{luid, name}`, once each. Tableau
    writes the connection as `class='publishedConnection' resourceId=… resourceName=…`."""
    out: list[dict[str, str]] = []
    for c in root.iter("connection"):
        if (c.get("class") or "").lower() != "publishedconnection" or not c.get("resourceId"):
            continue
        if c.get("resourceId") not in {r["luid"] for r in out}:
            out.append({"luid": c.get("resourceId"), "name": c.get("resourceName") or ""})
    return out


# The REST API version from which a virtual connection's content is served
# (`virtualConnections/<id>/content`, checked live: 3.22 answers 404, 3.23 the .tvcx).
VC_CONTENT_API = (3, 23)


def published_refs(root: ET.Element) -> list[dict[str, str]]:
    """Every published data source an XML root uses: `{content_url, name, server}`, one per
    distinct data source, in document order."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ds in root.iter("datasource"):
        conns = [c for c in ds.iter("connection")
                 if (c.get("class") or "").lower() == "sqlproxy"]
        if not conns:
            continue
        conn = conns[0]
        # The connection's dbname first: in a workbook it equals the reference's
        # repository-location id, but in a data source's own file the repository-location is
        # that data source itself, and only the dbname names the one it is built on.
        rl = ds.find("repository-location")
        content_url = (conn.get("dbname") or (rl.get("id") if rl is not None else None) or "")
        # A caption names the reference in a workbook; a data source file's root caption and
        # formatted-name are its own.
        name = (conn.get("server-ds-friendly-name")
                or ("" if ds.get("formatted-name") else ds.get("caption")) or "")
        name = re.sub(r"\s*\|\s*project\s*:.*$", "", name, flags=re.I).strip()
        key = (content_url.strip(), name)
        if not any(key) or key in seen:
            continue
        seen.add(key)
        out.append({"content_url": content_url.strip(), "name": name,
                    "server": (conn.get("server") or "").strip().lower()})
    return out


# ── file names ───────────────────────────────────────────────────────────────────────────
def filename_from_disposition(header: str | None) -> str | None:
    """The file name in a Content-Disposition header, decoded. Tableau URL-encodes it
    (`WWI+Analytics.tdsx`); RFC 5987's `filename*=UTF-8''…` wins when both are given."""
    if not header:
        return None
    star = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", header, re.I)
    if star:
        return urllib.parse.unquote(star.group(2).strip().strip('"'),
                                    encoding=star.group(1) or "utf-8", errors="replace")
    plain = re.search(r'filename\s*=\s*"([^"]*)"', header, re.I) or \
        re.search(r"filename\s*=\s*([^;]+)", header, re.I)
    if plain:
        return urllib.parse.unquote_plus(plain.group(1).strip())
    return None


def safe_file_name(name: str | None, fallback: str, suffixes: tuple[str, ...],
                   packaged: bool) -> str:
    """One plain file name with one of `suffixes`, from a name a server sent. The suffix is
    set from what the bytes ARE (a zip is the packaged form), whatever the name said."""
    base = pathlib.PurePosixPath((name or "").replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", base).strip().lstrip(".").strip()
    stem = base
    for s in (*WORKBOOK_SUFFIXES, *DATASOURCE_SUFFIXES):
        if stem.lower().endswith(s):
            stem = stem[: -len(s)]
            break
    stem = stem.strip()[:150] or re.sub(r"[^\w .-]", "", fallback)[:150].strip() or "tableau"
    plain_suffix, packaged_suffix = suffixes
    return stem + (packaged_suffix if packaged else plain_suffix)


def _unique(name: str, taken: set[str], tag: str) -> str:
    if name.lower() not in taken:
        taken.add(name.lower())
        return name
    p = pathlib.PurePosixPath(name)
    out = f"{p.stem}-{tag[:8]}{p.suffix}"
    taken.add(out.lower())
    return out


# ── writing ──────────────────────────────────────────────────────────────────────────────
def tag_of(role: str, rec: dict) -> dict[str, Any]:
    """One asset of `bi_tags.json` (the core's common format) from a REST workbook or data
    source record: its certification (`isCertified`, `certificationNote`) and description.
    Who certified it is never read. Matched by the reader on the name."""
    certified = str(rec.get("isCertified") or "").lower() == "true"
    out: dict[str, Any] = {"role": role, "name": rec.get("name") or None,
                           "vendor_id": rec.get("contentUrl") or None,
                           "endorsement": "certified" if certified else None}
    if certified and rec.get("certificationNote"):
        out["endorsement_note"] = str(rec["certificationNote"])[:2000]
    if rec.get("description"):
        out["description"] = str(rec["description"])[:2000]
    # The tool's own tags on the item (`<tags><tag label=…/>`), as labels.
    tags = (rec.get("tags") or {}).get("tag") if isinstance(rec.get("tags"), dict) else None
    labels = sorted({str(x.get("label")) for x in (tags if isinstance(tags, list) else [tags])
                     if isinstance(x, dict) and x.get("label")})
    if labels:
        out["labels"] = labels[:50]
    return out


def write_private(path: pathlib.Path, data: bytes) -> None:
    """Write `data`, owner-only (0600) even over an older file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, PRIVATE_FILE)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def makedirs_private(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, PRIVATE_DIR)


# ── targets ──────────────────────────────────────────────────────────────────────────────
_URL_WB_ID = re.compile(r"/workbooks/(\d+)(?:/|$)")
_URL_VIEW = re.compile(r"/views/([^/?#]+)(?:/|$)")
_URL_SITE = re.compile(r"/site/([^/?#]+)")


def parse_target(raw: str, server: str, site: str) -> tuple[str, Any]:
    """A workbook target, understood without any request: `("luid", id)`,
    `("content_url", url)`, `("url_id", number)` or `("project_name", (project, name))`.
    A URL must be on the configured server and site; it is never requested itself."""
    text = str(raw or "").strip()
    if not text:
        raise R.TableauError("an empty workbook target", fix="settings.tableau-cloud.workbooks")
    if R.LUID.match(text):
        return "luid", text.lower()
    if "://" in text:
        parts = urllib.parse.urlsplit(text)
        if f"https://{parts.netloc.lower()}" != server:
            raise R.TableauError(
                f"workbook URL on {parts.hostname or 'no host'}, not on the configured "
                "server: a URL is only ever read for the workbook it names on "
                f"{R.SERVER_VAR}", fix="settings.tableau-cloud.workbooks")
        route = parts.fragment or parts.path            # Tableau's routes live in the fragment
        m = _URL_SITE.search(route)
        url_site = urllib.parse.unquote(m.group(1)) if m else ""
        if url_site.lower() != site.lower():
            raise R.TableauError(f"workbook URL on site {url_site or '(default)'!r}, not on "
                                 f"{R.SITE_VAR}", fix="settings.tableau-cloud.workbooks")
        if (m := _URL_WB_ID.search(route)):
            return "url_id", m.group(1)
        if (m := _URL_VIEW.search(route)):
            return "content_url", urllib.parse.unquote(m.group(1))
        raise R.TableauError("a workbook URL that names no workbook (expected …/workbooks/<id> "
                             "or …/views/<workbook>/<sheet>)",
                             fix="settings.tableau-cloud.workbooks")
    if "/" in text:
        project, _, name = text.rpartition("/")
        if project.strip() and name.strip():
            return "project_name", (project.strip(), name.strip())
    raise R.TableauError(f"workbook target {text[:80]!r} is not a URL, a LUID or "
                         "project/name", fix="settings.tableau-cloud.workbooks")


def _filter_value(value: str) -> str | None:
    """A value Tableau's `filter=field:eq:value` syntax can carry, or None: a comma would
    start another expression."""
    return None if "," in value or not value.strip() else value


class Fetcher:
    def __init__(self, session: R.Session, *, max_depth: int, max_bytes: int,
                 capabilities: dict[str, Any] | None = None,
                 record_viewer: bool = False, time_zone: str | None = None) -> None:
        self.s = session
        # `viewer: true` in the settings: the signed-in identity is written beside each
        # workbook for its user functions (personal data: never by default).
        self.record_viewer = bool(record_viewer)
        self.time_zone = time_zone
        # What the sign-in may do (`rest.probe`); a capability probed as unavailable is not
        # tried per object, and each object records the one it came through.
        self.capabilities = dict(capabilities or {})
        self.max_depth = int(max_depth)
        self.max_bytes = int(max_bytes)
        self.failed: list[dict[str, Any]] = []
        self._ds_cache: dict[str, dict[str, Any]] = {}     # luid -> what was fetched
        self._vc_cache: dict[str, dict[str, Any]] = {}     # virtual connection luid -> fetched
        self._pulse: dict[str, list[dict]] | None = None   # data source luid -> definitions
        self.pulse_status: dict[str, Any] = {}

    # -- lookups -------------------------------------------------------------------------
    def viewer(self) -> dict[str, Any] | None:
        """The signed-in user as the core's viewer file (`bi_viewer`): its domain and groups,
        read once, and that its name can be bound at verification; never its name
        (guardrail 8). None when the settings do not ask for it or
        the user cannot be read (the workbook's user functions are then refused by name)."""
        if not self.record_viewer:
            return None
        if hasattr(self, "_viewer"):
            return self._viewer
        self._viewer = None
        uid = self.s.user_id or ""
        if not R.LUID.match(uid):
            return None
        try:
            user = (self.s.get_json(f"/sites/{self.site}/users/{R.segment(uid, 'user id')}")
                    .get("user") or {})
        except R.TableauError:
            return None
        if not user.get("name"):
            return None
        try:
            groups: list[str] | None = sorted({str(g.get("name")) for g in self.s.paged(
                f"/sites/{self.site}/users/{R.segment(uid, 'user id')}/groups", "groups",
                "group", max_pages=20) if g.get("name")})
        except R.TableauError:
            groups = None       # unread: a group test is then refused, never guessed
        domain = user.get("domain")
        self._viewer = {"contract_version": "1.0",
                        **({"domain": str(domain.get("name"))}
                           if isinstance(domain, dict) and domain.get("name") else {}),
                        **({"groups": groups} if groups is not None else {}),
                        "source": f"the fetch's sign-in ({self.s.route})",
                        # The plug can read this identity's name again at verification, in
                        # memory (`viewer_names`); the name itself is never written.
                        "user_name_bindable": True,
                        # The time zone the operator says the site reads "now" in.
                        **({"time_zone": self.time_zone} if self.time_zone else {})}
        return self._viewer

    @property
    def site(self) -> str:
        return R.segment(self.s.site_id, "site id")

    def _query_one(self, kind: str, field: str, value: str) -> list[dict]:
        v = _filter_value(value)
        if v is None:
            return []
        return list(self.s.paged(f"/sites/{self.site}/{kind}s", f"{kind}s", kind,
                                 {"filter": f"{field}:eq:{v}"}, max_pages=5))

    def resolve_workbook(self, raw: str) -> dict:
        how, value = parse_target(raw, self.s.server, self.s.site_content_url)
        if how == "luid":
            doc = self.s.get_json(f"/sites/{self.site}/workbooks/{R.segment(value, 'workbook')}")
            return doc.get("workbook") or {}
        if how == "content_url":
            hits = self._query_one("workbook", "contentUrl", value)
        elif how == "project_name":
            project, name = value
            hits = [w for w in self._query_one("workbook", "name", name)
                    if ((w.get("project") or {}).get("name") or "") == project]
        else:                                   # a numeric id: only webpageUrl carries it
            hits = [w for w in self.s.paged(f"/sites/{self.site}/workbooks", "workbooks",
                                            "workbook")
                    if re.search(rf"/workbooks/{value}(?:/|$)", w.get("webpageUrl") or "")]
        if len(hits) != 1:
            # NONE VISIBLE is an access limit as much as a wrong name (Tableau lists only what
            # the user may view): 404, so the run names it and goes on. SEVERAL is the
            # settings' ambiguity, refused as before.
            raise R.TableauError(
                f"workbook target {raw[:80]!r} matches {len(hits)} workbooks this token can "
                "see" + ("; name it by URL or LUID" if hits else
                         " (a wrong name, or no permission to view it)"),
                status=None if hits else 404,
                fix="settings.tableau-cloud.workbooks" if hits else
                    "the View permission on the workbook for the fetching user, or check the "
                    "name in settings.tableau-cloud.workbooks")
        return hits[0]

    def _with_note(self, rec: dict, luid: str) -> dict:
        """A certified data source's note: the list answer leaves it out and the item's own
        answer carries it (seen live). One read, only for a certified one; none on failure."""
        certified = str(rec.get("isCertified") or "").lower() == "true"
        if not certified or rec.get("certificationNote"):
            return rec
        try:
            one = self.s.get_json(f"/sites/{self.site}/datasources/{luid}").get("datasource") or {}
        except R.TableauError:
            return rec
        return {**rec, **{k: one[k] for k in ("certificationNote",) if one.get(k)}}

    def resolve_datasource(self, ref: dict[str, str]) -> dict:
        hits = self._query_one("datasource", "contentUrl", ref["content_url"]) \
            if ref["content_url"] else []
        if not hits and ref["name"]:
            hits = self._query_one("datasource", "name", ref["name"])
        if len(hits) != 1:
            raise R.TableauError(
                f"published data source {ref['name'] or ref['content_url']!r} matches "
                f"{len(hits)} data sources this token can see",
                status=None if hits else 404,
                fix="a View permission on the data source, for the token's user")
        return hits[0]

    # -- downloads ---------------------------------------------------------------------
    def download(self, kind: str, luid: str) -> tuple[bytes, str | None]:
        _, headers, raw = self.s.request(
            "GET", f"/sites/{self.site}/{kind}s/{R.segment(luid, kind)}/content",
            query={"includeExtract": "false"}, accept="*/*", limit=self.max_bytes)
        disposition = next((v for k, v in headers.items()
                            if k.lower() == "content-disposition"), None)
        return raw, filename_from_disposition(disposition)

    def datasource(self, ref: dict[str, str], depth: int, chain: tuple[str, ...]) -> list[str]:
        """Fetch one referenced data source and, through it, any it is built on. Returns the
        LUIDs fetched (this one first), [] when it failed (recorded in `self.failed`)."""
        label = ref["name"] or ref["content_url"]
        if ref["server"] and ref["server"] not in (urllib.parse.urlsplit(self.s.server)
                                                   .hostname or "",):
            self.failed.append({"kind": "datasource", "name": label, "status": None,
                                "reason": "published on another server; never followed",
                                "fix": "download it from that server by hand"})
            return []
        try:
            rec = self.resolve_datasource(ref)
            luid = R.segment(rec.get("id"), "data source")
        except R.TableauError as exc:
            self.failed.append({"kind": "datasource", "name": label, "status": exc.status,
                                "reason": str(exc), "fix": exc.fix})
            return []
        if luid in chain:
            return []                                          # a cycle: already on the path
        if luid in self._ds_cache:
            got = self._ds_cache[luid]
            return [luid, *got.get("upstream", [])] if got.get("ok") else []
        entry: dict[str, Any] = {"luid": luid, "name": rec.get("name"),
                                 "contentUrl": rec.get("contentUrl"),
                                 "project": (rec.get("project") or {}).get("name"),
                                 "depth": depth, "upstream": [], "ok": False,
                                 "_tags": tag_of("datasource", self._with_note(rec, luid))}
        self._ds_cache[luid] = entry
        try:
            data, sent_name = self.download("datasource", luid)
            entry.update(capability="rest-download", source="download", fidelity="full",
                         lost=[])
        except R.TableauError as exc:
            if exc.status != 403:
                entry.update(capability="unavailable", why=str(exc))
                self.failed.append({"kind": "datasource", "name": rec.get("name"),
                                    "luid": luid, "status": exc.status, "reason": str(exc),
                                    "fix": exc.fix})
                return []
            meta = (self.capabilities.get("metadataApi") or {})
            try:
                if meta.get("available") == "no":
                    raise R.TableauError(f"the Metadata API is not available to this sign-in "
                                         f"({meta.get('why')})")
                data, sent_name = M.rebuild_tds(self.s, rec), None
            except R.TableauError as meta_exc:
                why = ("the download was refused (no Download Data Source permission) and "
                       f"the Metadata API fallback failed: {meta_exc}")
                entry.update(capability="unavailable", why=why)
                self.failed.append({
                    "kind": "datasource", "name": rec.get("name"), "luid": luid,
                    "status": 403, "reason": why,
                    "fix": meta_exc.fix or R.seat_refusal(self.capabilities) or (
                        "ask the site admin for the Download Data Source permission on "
                        f"{rec.get('name')!r} for the signed-in user; or for the Metadata "
                        "API (on Tableau Cloud with the Data Management add-on, on "
                        "Tableau Server with the metadata services enabled)")})
                return []
            entry.update(capability="metadata-api", source="metadata-api",
                         fidelity="reduced", lost=list(M.LOST))
        packaged = data[:4] == b"PK\x03\x04"
        try:
            _member, root = tableau_root(f"data source {rec.get('name')!r}", data, (".tds",))
        except R.TableauError as exc:
            entry.update(capability="unavailable", why=str(exc))
            self.failed.append({"kind": "datasource", "name": rec.get("name"), "luid": luid,
                                "status": None, "reason": str(exc), "fix": ""})
            return []
        entry.update(data=data, sent_name=sent_name, packaged=packaged, bytes=len(data),
                     sha256=hashlib.sha256(data).hexdigest(), ok=True, vcs=vc_refs(root))
        refs = published_refs(root)
        if refs and depth >= self.max_depth:
            self.failed.append({
                "kind": "datasource", "name": rec.get("name"), "luid": luid, "status": None,
                "reason": f"a chain of published data sources deeper than {self.max_depth}: "
                          "the data sources it is built on were not fetched",
                "fix": "tableau.max_datasource_depth"})
            return [luid]
        for up in refs:
            got = self.datasource(up, depth + 1, (*chain, luid))
            entry["upstream"] += [g for g in got if g not in entry["upstream"]]
        return [luid, *entry["upstream"]]

    def virtual_connection(self, ref: dict[str, str]) -> dict[str, Any]:
        """A virtual connection's own file, saved beside the data sources that read through
        it so the reader can say which warehouse object each of its tables is. The content
        route (`.tvcx`, REST 3.23+) first; else the connection's record, whose `content`
        field holds the same JSON (saved as a loose `.tvc`)."""
        luid = R.segment(ref.get("luid"), "virtual connection")
        if luid in self._vc_cache:
            return self._vc_cache[luid]
        entry: dict[str, Any] = {"luid": luid, "name": ref.get("name") or luid, "ok": False}
        self._vc_cache[luid] = entry
        path = f"/sites/{self.site}/virtualConnections/{luid}"
        current = tuple(int(x) for x in self.s.api.rsplit("/", 1)[-1].split("."))
        version = ".".join(map(str, max(current, VC_CONTENT_API)))
        try:
            _, _, data = self.s.request("GET", path + "/content", accept="*/*",
                                        limit=self.max_bytes,
                                        base=f"{self.s.server}/api/{version}")
            if data[:4] != b"PK\x03\x04":
                raise R.TableauError("the virtual connection's content is not a .tvcx")
            entry.update(data=data, suffix=".tvcx", capability="rest-download", ok=True)
        except R.TableauError as first:
            try:
                rec = self.s.get_json(path).get("virtualConnection") or {}
                doc = json.loads(rec.get("content") or "")
                doc.setdefault("name", rec.get("name") or entry["name"])
                entry.update(data=(json.dumps(doc) + "\n").encode(), suffix=".tvc",
                             capability="rest-record", ok=True)
            except (R.TableauError, ValueError, TypeError) as second:
                why = (f"the virtual connection {entry['name']!r} could not be read: "
                       f"content: {first}; record: {second}")
                entry.update(capability="unavailable", why=why)
                self.failed.append({
                    "kind": "virtual connection", "name": entry["name"], "luid": luid,
                    "status": getattr(second, "status", None), "reason": why,
                    "fix": ("ask for the Connect permission on the virtual connection "
                            f"{entry['name']!r}, or save its .tvcx beside the data source")})
        if entry["ok"]:
            entry.update(bytes=len(entry["data"]),
                         sha256=hashlib.sha256(entry["data"]).hexdigest())
        return entry

    def pulse_definitions(self) -> dict[str, list[dict]]:
        """The site's Pulse metric definitions, by the data source each is built on. Read once
        per fetch (`GET /api/-/pulse/definitions`, paged). A site without Pulse, or a sign-in
        it refuses, gives none, said in `pulse_status`; it never fails the fetch."""
        if self._pulse is not None:
            return self._pulse
        by_ds: dict[str, list[dict]] = {}
        token, pages = "", 0
        try:
            while True:
                query = {"page_size": "100", "view": "DEFINITION_VIEW_FULL"}
                if token:
                    query["page_token"] = token
                _, _, raw = self.s.request("GET", "/pulse/definitions", query=query,
                                           base=f"{self.s.server}/api/-",
                                           limit=self.max_bytes)
                doc = json.loads(raw.decode("utf-8") or "{}")
                for d in doc.get("definitions") or []:
                    ds = str(((d.get("specification") or {}).get("datasource") or {})
                             .get("id") or "").lower()
                    if ds:
                        by_ds.setdefault(ds, []).append(d)
                token, pages = str(doc.get("next_page_token") or ""), pages + 1
                if not token or pages >= 100:
                    break
            self.pulse_status = {"available": "yes",
                                 "definitions": sum(len(v) for v in by_ds.values())}
        except (R.TableauError, ValueError) as exc:
            by_ds = {}
            self.pulse_status = {"available": "no",
                                 "why": f"Pulse did not answer ({exc}); it may be off on the site"}
        self._pulse = by_ds
        return by_ds

    # -- one workbook --------------------------------------------------------------------
    def workbook(self, raw: str, dest: pathlib.Path, folders: set[str]) -> dict | None:
        try:
            wb = self.resolve_workbook(raw)
            luid = R.segment(wb.get("id"), "workbook")
            data, sent_name = self.download("workbook", luid)
            _member, root = tableau_root(f"workbook {wb.get('name')!r}", data, (".twb",))
        except R.TableauError as exc:
            fix = exc.fix
            if exc.status == 403:
                fix = R.seat_refusal(self.capabilities) or (
                    "ask for the Download/Save a Copy permission on the workbook for the "
                    "token's user")
            self.failed.append({"kind": "workbook", "name": raw, "status": exc.status,
                                "reason": str(exc), "fix": fix})
            return None
        name = str(wb.get("name") or luid)
        folder_name = _unique(safe_file_name(name, luid, ("", ""), False), folders, luid)
        folder = dest / "workbooks" / folder_name
        makedirs_private(folder)
        taken: set[str] = set()
        wb_file = _unique(safe_file_name(sent_name, name, WORKBOOK_SUFFIXES,
                                         data[:4] == b"PK\x03\x04"), taken, luid)
        write_private(folder / wb_file, data)
        viewer = self.viewer()
        if viewer:
            # Who the workbook's user functions are evaluated for: the identity this fetch
            # signed in as (the core reader reads `viewer.json` beside the workbook).
            write_private(folder / "viewer.json",
                          (json.dumps(viewer, indent=2) + "\n").encode())
        used: list[str] = []
        missing: list[str] = []
        for ref in published_refs(root):
            before = len(self.failed)
            got_any = self.datasource(ref, 1, ())
            if len(self.failed) > before:
                missing.append(ref["name"] or ref["content_url"])
            for got in got_any:
                if got not in used:
                    used.append(got)
        files = []
        # What the REST API says about the workbook and its data sources that their files do
        # not hold (a certification and its note, a description), for the reader.
        tags = [tag_of("workbook", wb)] + [self._ds_cache[d]["_tags"] for d in used
                                           if self._ds_cache[d].get("_tags")]
        tags = [x for x in tags if x.get("endorsement") or x.get("description")
                or x.get("labels")]
        if tags:
            write_private(folder / "bi_tags.json", (json.dumps(
                {"contract_version": "1.0", "source": "tableau REST API", "assets": tags},
                indent=2) + "\n").encode())
        for ds_luid in used:
            entry = self._ds_cache[ds_luid]
            if not entry.get("ok"):
                continue
            fname = _unique(safe_file_name(entry["sent_name"], entry["name"] or ds_luid,
                                           DATASOURCE_SUFFIXES, entry["packaged"]),
                            taken, ds_luid)
            write_private(folder / fname, entry["data"])
            entry.setdefault("files", []).append(f"workbooks/{folder_name}/{fname}")
            entry.setdefault("referencedBy", []).append(name)
            files.append(fname)
            # The Pulse metrics built on it, named for it as the reader expects.
            defs = self.pulse_definitions().get(ds_luid.lower())
            if defs:
                pname = _unique(safe_file_name(entry["name"], ds_luid, ("", ""), False)
                                + ".pulse.json", taken, ds_luid)
                write_private(folder / pname,
                              (json.dumps({"definitions": defs}, indent=1) + "\n").encode())
                entry["pulseDefinitions"] = len(defs)
                files.append(pname)
        # The virtual connections the workbook or any of its data sources read through.
        vcs = vc_refs(root) + [v for ds_luid in used
                               for v in self._ds_cache[ds_luid].get("vcs", [])]
        for ref in {v["luid"]: v for v in vcs}.values():
            got = self.virtual_connection(ref)
            if not got.get("ok"):
                missing.append(got["name"])
                continue
            fname = _unique(safe_file_name(got["name"], got["luid"], ("", ""), False)
                            + got["suffix"], taken, got["luid"])
            write_private(folder / fname, got["data"])
            got.setdefault("files", []).append(f"workbooks/{folder_name}/{fname}")
            files.append(fname)
        return {"luid": luid, "name": name,
                "project": (wb.get("project") or {}).get("name"),
                "folder": f"workbooks/{folder_name}", "file": wb_file, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "capability": "rest-download",
                "datasources": used, "datasourceFiles": files,
                "datasourcesIncomplete": missing}

    # -- the whole fetch -----------------------------------------------------------------
    def run(self, targets: list[str], dest: pathlib.Path) -> dict:
        dest = pathlib.Path(dest)
        makedirs_private(dest)
        folders: set[str] = set()
        workbooks = [w for w in (self.workbook(t, dest, folders) for t in targets) if w]
        for f in self.failed:
            # What each failure left out: the object itself, or (a chain cut by depth) only
            # what it is built on.
            got = self._ds_cache.get(f.get("luid") or "", {}).get("ok")
            f["capability"] = "partial" if got else "unavailable"
        datasources = []
        for luid, e in self._ds_cache.items():
            datasources.append({k: e.get(k) for k in (
                "luid", "name", "contentUrl", "project", "depth", "capability", "why",
                "source", "fidelity", "lost", "bytes", "sha256", "files", "referencedBy",
                "upstream", "pulseDefinitions")
                if k in e})
        virtual_connections = [{k: e.get(k) for k in (
            "luid", "name", "capability", "why", "bytes", "sha256", "files") if k in e}
            for e in self._vc_cache.values()]
        manifest = {
            "contract_version": "1.0",
            "fetchedAt": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat(),
            "server": urllib.parse.urlsplit(self.s.server).hostname,
            "site": self.s.site_content_url,
            "apiVersion": self.s.api.rsplit("/", 1)[-1],
            "includeExtract": False,
            # How the run signed in, what that sign-in may do, and how much it obtained: a
            # run reads here how complete its input was.
            "signIn": {"route": self.s.route},
            "capabilities": self.capabilities,
            "complete": not self.failed,
            "coverage": {
                "workbooksAsked": len(targets), "workbooksFetched": len(workbooks),
                "datasourcesDownloaded": sum(1 for d in datasources
                                             if d.get("capability") == "rest-download"),
                "datasourcesFromMetadataApi": sum(1 for d in datasources
                                                  if d.get("capability") == "metadata-api"),
                "virtualConnections": sum(1 for v in virtual_connections
                                          if v.get("capability") != "unavailable"),
                "unavailable": len(self.failed)},
            "workbooks": workbooks,
            "datasources": datasources,
            "virtualConnections": virtual_connections,
            "pulse": self.pulse_status or {"available": "unknown",
                                           "why": "no data source was fetched"},
            "reducedFidelity": sorted(d["name"] or d["luid"] for d in datasources
                                      if d.get("fidelity") == "reduced"),
            "failed": self.failed,
        }
        write_private(dest / "manifest.json",
                      (json.dumps(manifest, indent=2, default=str) + "\n").encode())
        return manifest
