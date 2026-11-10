# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Read a Power BI template's model and report together, without importing data rows."""
from __future__ import annotations

import json
from pathlib import Path
import zipfile

from metrics_agent.plugs.readers import pbix, powerbi, tabular
from metrics_agent.sockets import ReaderRefused

MAX_SCHEMA_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 10000


def parse(path: Path, report_index: dict | None = None) -> dict:
    """Templates carry TMSL JSON in DataModelSchema, plus the normal report tree.

    Use the existing model parser and report walkers so a template retains its page,
    visual and filter evidence. No fallback silently substitutes a sibling model.
    """
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > MAX_ENTRIES or len(names) != len(set(names)):
                raise ReaderRefused('The Power BI template contains too many or duplicate archive entries.')
            try:
                info = archive.getinfo('DataModelSchema')
            except KeyError:
                raise ReaderRefused('The Power BI template has no readable DataModelSchema. Export its model and report definitions together.') from None
            if info.file_size > MAX_SCHEMA_BYTES:
                raise ReaderRefused('The Power BI template model exceeds the 32 MiB reader limit.')
            with archive.open(info) as stream:
                raw = stream.read(MAX_SCHEMA_BYTES + 1)
            if len(raw) > MAX_SCHEMA_BYTES:
                raise ReaderRefused('The Power BI template model exceeds the 32 MiB reader limit.')
            document = json.loads(pbix._decode_layout(raw))
    except (OSError, zipfile.BadZipFile, UnicodeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, ReaderRefused):
            raise
        raise ReaderRefused('The Power BI template model could not be decoded. Keep the original and export its model and report definitions together.') from None
    if not isinstance(document, dict):
        raise ReaderRefused('The Power BI template model must be a JSON object.')
    model = document.get('model', document)
    if not isinstance(model, dict) or not isinstance(model.get('tables'), list):
        raise ReaderRefused('The Power BI template has no tabular model tables.')
    facts = tabular.parse_model(model, str(path), model.get('name') or document.get('name') or path.stem, path=path)
    usage = pbix.report_usage(path, facts['tables'])
    if usage:
        facts['usage'] = usage
        notes = powerbi.finish_usage(usage, facts['tables'], facts['measures'])
        if notes:
            facts.setdefault('_reader_notes', []).extend(notes)
    else:
        facts.setdefault('_reader_notes', []).append('The template has no report placements; usage describes model curation only.')
    return facts
