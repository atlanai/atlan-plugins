# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The Power BI definition layout on disk: which model is which, and which reports use it.

The Power BI reader's own helper, not the pipeline's: nothing outside the reader (and the plug
packages that write this layout) imports it. A crawler extract and a Fabric fetch both land a
tenant's definitions in this one shape — `semanticModels/<ws>/<model>/` beside
`reports/<ws>/<report>/` — and each item may carry an `envelope.json` listing its parts. This
module READS that layout. Writing envelopes, verifying checksums and inventorying an extract
belong to the plug that fetches one (`metrics-agent-atlan`).

PROVENANCE. Ported from an earlier project (guardrail 6: a COPY naming its origin) —
its model inventory, its report index, and the `.platform` display-name read. Deliberately left
behind: the earlier project's presigned-URL OBJECT-STORE prefix layout (which belongs to the extract
fetch, not to reading a directory) and its TMDL parser itself — the base already parses TMDL
and porting the earlier project's parser wholesale would duplicate it.

WHY A CRAWLER EXTRACT NEEDS ITS OWN MODULE AT ALL. Pointing `--bi` at one is not like
pointing it at a workbook:

  * **An extract is a whole TENANT, not a model.** The reference extract held 345 semantic
    models across 29 workspaces, 302 of them copies of one. A run builds ONE model
    (`bi_facts.schema.json` describes one), so the root is not a runnable input — it is a
    thing to inventory and choose from.
  * **A model and its reports live in SEPARATE TREES.** `semanticModels/<ws>/<model>/` holds
    the TMDL; the visuals that use it are under `reports/<ws>/<report>/`. Nothing inside the
    model directory points at them. A parser handed only the model directory therefore finds
    no visuals and reports `usage: []` — which reads as "nothing in this estate is on a
    dashboard" when it means "we never looked in the right tree". Since `a2_question_units`
    iterates `usage`, empty usage means no question units, no compiled SQL, and no metrics.
  * **The join is a connection string.** A report's `definition.pbir` binds it to its model
    with `semanticmodelid=<guid>` inside `datasetReference.byConnection`. Crawler output
    always uses `byConnection`, never `byPath`, so that substring is the only join available.
"""
from __future__ import annotations

import json
import os
import pathlib
import re

MODEL_FILE = "definition/model.tmdl"
_SEMANTIC_MODEL_ID = re.compile(r"semanticmodelid=([0-9a-fA-F-]{36})", re.I)

# `definition.pbir` and `.platform` are small metadata files. A cap keeps a malformed or
# hostile extract from being read into memory whole; it is not a security boundary, just the
# refusal to trust a file's size because of where it sits.
MAX_METADATA_BYTES = 1_000_000


class ExtractError(ValueError):
    """The extract is not shaped the way a crawler extract is shaped."""


def is_extract_root(path: pathlib.Path) -> bool:
    """True when this directory is an extract ROOT rather than one model inside it."""
    return (path / "semanticModels").is_dir()


# ---------------------------------------------------------------------------
# symlinks — an extract is a customer-supplied tree, not a trusted one
# ---------------------------------------------------------------------------

def is_inside(root: pathlib.Path, path: pathlib.Path) -> bool:
    """Is `path` genuinely under `root` once every link is resolved?"""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):        # RuntimeError: a symlink loop
        return False


def has_symlink(root: pathlib.Path, path: pathlib.Path) -> bool:
    """Is any component of `path`, from `root` downwards, a symlink?

    `lstat` per component rather than one `resolve()`, because a link pointing back INSIDE
    the tree is still a link: it makes one file appear at two paths, so the same bytes get
    listed twice in an envelope and hashed twice by `--verify`.
    """
    try:
        rel = path.resolve(strict=False).relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return True
    cur = root
    for part in rel.parts:
        cur = cur / part
        if os.path.islink(cur):
            return True
    return False


def safe_paths(root: pathlib.Path, paths):
    """Drop anything that is, or sits under, a symlink, or that escapes `root`.

    WHY, with the two escapes it closes — both proven against a hostile fixture tree. An
    extract is a directory someone handed over, and `glob`/`rglob` plus `is_file()`/`is_dir()`
    FOLLOW symlinks:

      * an item directory symlinked out of the tree made the envelope writer create a file
        OUTSIDE the extract root — an arbitrary-file-create primitive that needed nothing
        more than pointing the tool at a hostile extract;
      * a definition part symlinked at a credential file elsewhere on the machine was
        accepted as a part, listed in the envelope, hashed by `--verify`, and PARSED — at
        which point fragments of it reach `bi_facts.json`, which the authored stages read.

    Nothing legitimate in a crawler extract is a symlink; the crawler writes regular files.
    So they are dropped rather than resolved, and the caller reports how many.
    """
    return [p for p in paths if not has_symlink(root, p) and is_inside(root, p)]


def find_models(root: pathlib.Path) -> list[pathlib.Path]:
    """Every `<root>/semanticModels/<workspace>/<model>` holding a real model."""
    sm = root / "semanticModels"
    if not sm.is_dir():
        raise ExtractError(
            f"no semanticModels/ under {root}. Point at the root of a native-crawler "
            "extract — the directory holding semanticModels/ and reports/.")
    return sorted(p.parent.parent
                  for p in safe_paths(root, sm.glob(f"*/*/{MODEL_FILE}")))


def refuse_extract_root(path: pathlib.Path, readable=None) -> pathlib.Path | None:
    """An extract root passed where ONE model was required: the model, when there is only one
    to read, else refused with what to run.

    `readable(root)` names the models a run could read on its own (the reader leaves out a
    model another one reads through a chain); without it, every model counts. With exactly ONE
    there is no choice to make, and it is returned so the caller reads it and says which. With
    several the root is refused with the inventory instruction: parsing it would either pick a
    model silently — deciding the run's scope for the operator — or glob every `.tmdl` in the
    tenant into one incoherent model. None when `path` is not an extract root at all.
    """
    if not is_extract_root(path):
        return None
    try:
        models = readable(path) if readable is not None else find_models(path)
    except ExtractError:
        models = []
    if len(models) == 1:
        return models[0]
    raise ExtractError(
        f"{path} is a crawler extract ROOT, not a semantic model, and a run builds ONE "
        f"model.\n"
        f"  It holds {len(models)} model(s) across "
        f"{len({m.parent.name for m in models})} workspace(s). Choosing for you would "
        "silently decide the run's scope.\n"
        "  Inventory it, then pass ONE model directory as --bi:\n"
        f"    python3 scripts/s_extract_inventory.py --extract {path} "
        "--out inventory.json --unique-only")


def build_report_index(root: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """model guid (lowercased) -> the report DIRECTORIES bound to it, in ONE pass.

    Directories rather than `report.json` paths, because an extract carries both report
    formats and only `definition.pbir` is common to the two — indexing on `report.json`
    silently drops every PBIR-format report.

    Built once and handed to each parse: rebuilding it per model is a full re-scan of every
    report in the extract, which on the reference extract was 345 x 341 file reads.
    """
    index: dict[str, list[pathlib.Path]] = {}
    reports = root / "reports"
    if not reports.is_dir():
        return index
    for pbir in sorted(safe_paths(root, reports.glob("*/*/definition.pbir"))):
        try:
            if pbir.stat().st_size > MAX_METADATA_BYTES:
                continue
            txt = pbir.read_text(errors="ignore")
        except OSError:
            continue
        m = _SEMANTIC_MODEL_ID.search(txt)
        if m:
            index.setdefault(m.group(1).lower(), []).append(pbir.parent)
    return index


def reports_for(model_dir: pathlib.Path,
                index: dict[str, list[pathlib.Path]] | None = None) -> list[pathlib.Path]:
    """The report directories bound to one model. Builds the index if not given one."""
    root = model_dir.parent.parent.parent          # <root>/semanticModels/<ws>/<model>
    idx = index if index is not None else build_report_index(root)
    return idx.get(model_dir.name.lower(), [])


def display_name(model_dir: pathlib.Path) -> str | None:
    """The model's own name, from `.platform`.

    Without this a model is known only by its GUID — `8f864a92-…` rather than "Context Layer
    Accounts" — and the GUID is what would reach every card a person reads.
    """
    plat = model_dir / ".platform"
    try:
        if not plat.is_file() or plat.stat().st_size > MAX_METADATA_BYTES:
            return None
        meta = (json.loads(plat.read_text(errors="ignore")) or {}).get("metadata") or {}
    except (OSError, json.JSONDecodeError):
        return None
    name = meta.get("displayName")
    return str(name) if name else None


# ---------------------------------------------------------------------------
# envelopes — the part LISTING, not the parts
# ---------------------------------------------------------------------------

ENVELOPE_NAME = "envelope.json"


class UnreadableEnvelope(ValueError):
    """An `envelope.json` is PRESENT but cannot be read as one.

    DISTINCT FROM ABSENT, and the distinction is load-bearing. This used to return None for
    both, and `s_build_envelopes` reads None as "this item has no envelope" and writes one —
    so a corrupt, truncated, oversized or list-shaped envelope was SILENTLY OVERWRITTEN. A
    truncated crawler envelope is exactly the artifact that must never be replaced: it is the
    only record able to notice a part that failed to download, and truncation is how it gets
    that way.
    """


def read_envelope(item_dir: pathlib.Path) -> dict | None:
    """An item's `envelope.json`, or None when it has none.

    WHAT AN ENVELOPE IS, because the name suggests otherwise: a LISTING of the definition
    parts an item is made of — `partCount`, `definition.format`, `definition.parts[].path` —
    never the parts' contents. Nothing here needs it to find visuals; the parser walks the
    directory. It matters for two other things: the extract fetch uses it to know what to
    download, and a CRAWLER-written one lets us tell a missing file from a file that
    never existed.
    """
    env = item_dir / ENVELOPE_NAME
    if not env.exists():
        return None
    if os.path.islink(env):
        raise UnreadableEnvelope(f"{env} is a symlink; an envelope is a regular file")
    try:
        size = env.stat().st_size
    except OSError as exc:
        raise UnreadableEnvelope(f"{env}: {type(exc).__name__}") from None
    if size > MAX_METADATA_BYTES:
        raise UnreadableEnvelope(
            f"{env} is {size} bytes, over the {MAX_METADATA_BYTES} cap for a part listing")
    try:
        doc = json.loads(env.read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError, RecursionError) as exc:
        raise UnreadableEnvelope(
            f"{env} is not readable JSON: {type(exc).__name__}") from None
    if not isinstance(doc, dict):
        raise UnreadableEnvelope(f"{env} is a {type(doc).__name__}, not an envelope object")
    return doc


def envelope_parts(envelope: dict) -> list[str]:
    """The part paths an envelope lists, relative and forward-slashed."""
    parts = ((envelope or {}).get("definition") or {}).get("parts") or []
    return sorted(str(p.get("path")) for p in parts if isinstance(p, dict) and p.get("path"))


def is_reconstructed(envelope: dict) -> bool:
    """True when this envelope was rebuilt from what is on disk rather than written by the
    crawler. `s_build_envelopes.py` stamps `generatedBy` for exactly this question."""
    return bool((envelope or {}).get("generatedBy"))


def missing_parts(item_dir: pathlib.Path) -> list[str]:
    """Parts the envelope LISTS that are not on disk — i.e. a download that did not finish.

    THIS IS THE ONE CLAIM ONLY A CRAWLER ENVELOPE CAN MAKE. A reconstructed envelope is an
    observation of what is PRESENT, so by construction it can never disagree with the disk:
    a missing file and a file that never existed look identical from there. Returns nothing
    for a reconstructed envelope rather than an empty "all good", and the caller says which
    kind it had.
    """
    try:
        env = read_envelope(item_dir)
    except UnreadableEnvelope:
        return []
    if env is None or is_reconstructed(env):
        return []
    return [rel for rel in envelope_parts(env) if not (item_dir / rel).is_file()]


def fetch_integrity(model_dir: pathlib.Path,
                    report_dirs: list[pathlib.Path]) -> dict:
    """What the envelopes say about whether this model's files all actually arrived.

    Reported as a property of the FETCH. A report parsing with empty `usage` must read as "we
    could not list its visuals", never as "this report has no widgets" — and the difference is
    only visible here.
    """
    items = [("semanticModel", model_dir)] + [("report", d) for d in report_dirs]
    described = reconstructed = 0
    missing: list[str] = []
    for kind, d in items:
        try:
            env = read_envelope(d)
        except UnreadableEnvelope:
            described += 1
            missing.append(f"{kind} {d.name}: envelope.json is present but unreadable")
            continue
        if env is None:
            continue
        described += 1
        if is_reconstructed(env):
            reconstructed += 1
        missing += [f"{kind} {d.name}: {rel}" for rel in missing_parts(d)]
    return {"items": len(items), "with_envelope": described,
            "reconstructed": reconstructed, "missing_parts": missing}
