# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Run policy — the single home for every admission threshold and ranking weight.

Rule 4: policy governs ADMISSION (what evidence enters the pool) and RANKING (what an author
sees first). It NEVER makes a claim true. A dirty verify report, an uncited literal and a
column that does not exist stay refusals in code, where policy cannot reach them.

A missing policy file RAISES. A run whose thresholds came from nowhere cannot say what
produced its artifacts.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
from typing import Any

import yaml

from metrics_agent._paths import DATA, POLICY_NAME

DEFAULT_NAME = POLICY_NAME


class PolicyError(RuntimeError):
    """Raised when policy cannot be resolved or a required key is absent."""


class Policy:
    """Read-only view over agent_policy.yml, with its identity attached."""

    def __init__(self, path: pathlib.Path, data: dict[str, Any], sha256: str) -> None:
        self.path = path
        self._data = data
        self.sha256 = sha256

    def get(self, dotted: str) -> Any:
        """Fetch by dotted path. A missing key RAISES — it is never defaulted here, because a
        silently defaulted threshold is exactly the untraceable run this module exists to
        prevent."""
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise PolicyError(
                    f"policy key {dotted!r} is absent from {self.path} "
                    f"(failed at {part!r}) — add it rather than defaulting it in code"
                )
            node = node[part]
        return node

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Policy({self.path}, sha256={self.sha256[:12]})"


def resolve(explicit: str | None = None) -> pathlib.Path:
    """--policy <path>  ->  $METRICS_AGENT_POLICY  ->  the file the package ships."""
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    env = os.environ.get("METRICS_AGENT_POLICY")
    if env:
        return pathlib.Path(env).expanduser().resolve()
    return (DATA / DEFAULT_NAME).resolve()


# A PLUG PACKAGE'S TUNABLES ARE ITS OWN. A package registered under `metrics_agent.artifacts`
# may name its policy defaults (`POLICY`, a YAML file of top-level sections). They are merged
# UNDER the run's policy: a section the run's file names wins whole, and the core's file names
# no plug's section (AGENT_GUARDRAILS rule 22).
_ARTIFACT_GROUP = "metrics_agent.artifacts"


def _plug_defaults() -> list[tuple[str, dict[str, Any], bytes]]:
    """[(package entry, its policy sections, the file's bytes)] in entry-point order."""
    from importlib import metadata  # noqa: PLC0415

    out = []
    for ep in sorted(metadata.entry_points(group=_ARTIFACT_GROUP), key=lambda e: e.name):
        try:
            mod = ep.load()
        except Exception:  # noqa: BLE001 - the artifact registry names a broken package
            continue
        path = getattr(mod, "POLICY", None)
        if not path:
            continue
        raw = pathlib.Path(path).read_bytes()
        doc = yaml.safe_load(raw.decode("utf-8")) or {}
        if not isinstance(doc, dict):
            raise PolicyError(f"{path} (policy defaults of {ep.name!r}) did not parse to a "
                              "mapping")
        out.append((ep.name, doc, raw))
    return out


def load(explicit: str | None = None) -> Policy:
    path = resolve(explicit)
    if not path.is_file():
        raise PolicyError(
            f"no policy file at {path}. Pass --policy, set $METRICS_AGENT_POLICY, or restore "
            f"{DEFAULT_NAME}; this is never defaulted."
        )
    raw = path.read_bytes()
    data = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise PolicyError(f"{path} did not parse to a mapping")
    digest = hashlib.sha256(raw)
    for _name, sections, plug_raw in _plug_defaults():
        used = False
        for key, value in sections.items():
            if key not in data:
                data[key] = value
                used = True
        if used:
            # The run's thresholds include the package's defaults it used, so its identity
            # does too: a changed package default is a changed policy.
            digest.update(b"\0" + plug_raw)
    return Policy(path, data, digest.hexdigest())
