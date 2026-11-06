# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Several BI models in one run: merged into ONE `bi_facts` document, read model by model.

A run may list several BI plugs (a Tableau workbook and a Power BI model, say). A1 parses each
plug's definitions on its own and `merge` folds the facts into one document:

* every item (table, measure, relationship, parameter, usage entry, asset) carries `model`, the
  plug it came from, and the document lists the models under `models`;
* every unit id and container is prefixed with the plug (`<plug>:`), and each model's name with
  it too, so two models can never produce the same unit, container or domain.

Measures, tables and parameters are NAMES inside one model: two models may both define
"Sell Through Pct" with different formulas. So nothing is looked up across models: `partitions`
gives each model's facts back on their own, and A2 and A3 read each partition exactly as they
read a run with one model. A document with no `models` is one partition, itself, so a run with
one BI model is read exactly as before.
"""
from __future__ import annotations

from typing import Any

ITEMS = ("tables", "measures", "relationships", "parameters", "usage", "assets",
         "interactions")


def vendor_of(vendors: list[str]) -> str:
    """The one vendor a run's BI models share, or `mixed` when they differ."""
    distinct = set(vendors)
    return distinct.pop() if len(distinct) == 1 else "mixed"


def merge(parts: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """One `bi_facts` document from [(plug, facts)], in the order given. With one part, the
    facts themselves, unchanged. `vendor` is the models' shared vendor or `mixed`, and
    `vendors` lists each distinct one."""
    if len(parts) == 1:
        return parts[0][1]
    first = parts[0][1]
    vendors = [facts["vendor"] for _plug, facts in parts]
    out: dict[str, Any] = {"contract_version": first.get("contract_version", "1.0"),
                           "vendor": vendor_of(vendors), "vendors": sorted(set(vendors)),
                           "source_file": first["source_file"],
                           "model_name": "", "models": []}
    for key in ITEMS:
        out[key] = []
    viewers = [facts["viewer"] for _plug, facts in parts if facts.get("viewer")]
    if viewers:
        # The identity the run's inputs were fetched as: one run, one viewer.
        out["viewer"] = viewers[0]
    for plug, facts in parts:
        out["models"].append({"id": plug, "plug": plug, "vendor": facts["vendor"],
                              "model_name": facts.get("model_name") or "",
                              "source_file": facts["source_file"]})
        for key in ITEMS:
            for item in facts.get(key) or []:
                item = {**item, "model": plug}
                if key == "usage":
                    item["unit_id"] = f"{plug}:{item['unit_id']}"
                    item["container"] = f"{plug}:{item['container']}"
                out[key].append(item)
    return out


def prefix_ids(plug: str, ids: dict[str, Any]) -> dict[str, Any]:
    """A per-unit map (A1's sidecar) keyed by the merged unit ids."""
    return {f"{plug}:{k}": v for k, v in ids.items()}


def partitions(facts: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    """[(model id, that model's facts)]: each model of a merged document on its own, its
    `model_name` namespaced by its plug; or [(None, facts)] for a document of one model."""
    models = facts.get("models") or []
    if not models:
        return [(None, facts)]
    out = []
    for m in models:
        part = {k: v for k, v in facts.items() if k not in ITEMS and k not in ("models", "vendors")}
        part.update(vendor=m["vendor"], source_file=m["source_file"],
                    model_name=f"{m['plug']}:{m['model_name'] or m['vendor']}")
        for key in ITEMS:
            if key in facts:
                # The tag is the merged document's; within its own partition an item is read
                # exactly as a one-model run reads it.
                part[key] = [{k: v for k, v in i.items() if k != "model"}
                             for i in facts[key] if i.get("model") == m["id"]]
        out.append((m["id"], part))
    return out


def model_of_units(facts: dict[str, Any]) -> dict[str, str | None]:
    """unit id -> the model it belongs to (None for a document of one model)."""
    return {u["unit_id"]: u.get("model") for u in facts.get("usage") or []}
