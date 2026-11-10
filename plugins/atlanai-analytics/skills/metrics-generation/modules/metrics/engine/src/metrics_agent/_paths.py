# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Where the agent's own files live, and how one stage starts another.

The schemas and `agent_policy.yml` ship INSIDE the package, under `metrics_agent/data/`, so an
installed wheel finds them with no source checkout beside it. `DATA` is the one place that
knows where they are; nothing else builds a path to them. A BI compiler's rule table and
regression corpus are its reader's (`sockets.BIReader.compiler_rules`/`compiler_corpus`); an
operator may put their own beside the policy, under the names below.

`ROOT` is the source checkout (the directory above `src/`) and exists only there. Code that
uses it must tolerate an installed package, where it is just some directory in site-packages.
"""
from __future__ import annotations

import os
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parent
SRC = PACKAGE.parent
ROOT = SRC.parent
DATA = PACKAGE / "data"
SCHEMAS = DATA / "schemas"
POLICY_NAME = "agent_policy.yml"
RULES_NAME = "bi_compiler_rules.yml"
CORPUS_NAME = "compiler_corpus"


def beside_policy(policy_path: str | os.PathLike[str], name: str) -> pathlib.Path | None:
    """`name` (a rule table or a corpus) in the directory of the run's policy, when the
    operator put one there, so a user's `--policy` keeps its sibling rules; else None, and the
    installed readers' own are used (`readers.rule_table_paths`, `readers.corpus_dirs`)."""
    sibling = pathlib.Path(policy_path).parent / name
    return sibling if sibling.exists() else None


STAGE_GROUP = "metrics_agent.stages"


def stage_module(name: str) -> str:
    """The module that runs stage `name` (`x3_emit`, or `x3_emit.py`). A core stage lives in
    `metrics_agent.stages`; a plug package adds its own under the `metrics_agent.stages`
    entry points, so the core never names it."""
    stem = pathlib.Path(name).stem
    if (PACKAGE / "stages" / f"{stem}.py").is_file():
        return f"metrics_agent.stages.{stem}"
    from importlib import metadata  # noqa: PLC0415

    for ep in metadata.entry_points(group=STAGE_GROUP):
        if ep.name == stem:
            return ep.value
    return f"metrics_agent.stages.{stem}"


def stage_names() -> list[str]:
    """Every stage: the core's and those plug packages add."""
    from importlib import metadata  # noqa: PLC0415

    core = {p.stem for p in (PACKAGE / "stages").glob("*.py") if p.stem not in ("__init__",)}
    return sorted(core | {ep.name for ep in metadata.entry_points(group=STAGE_GROUP)})


def stage_command(script: str) -> list[str]:
    """The command that runs one stage, given its historical file name (`x3_emit.py`)."""
    return [sys.executable, "-m", stage_module(script)]


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """`env` (default: this process's) with this package importable in a child process,
    whether or not it was installed."""
    out = dict(os.environ if env is None else env)
    parts = [p for p in out.get("PYTHONPATH", "").split(os.pathsep) if p]
    if str(SRC) not in parts:
        out["PYTHONPATH"] = os.pathsep.join([str(SRC), *parts])
    return out
