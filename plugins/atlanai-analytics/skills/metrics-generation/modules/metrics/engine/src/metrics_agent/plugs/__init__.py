# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Finding the installed plugs.

A plug package registers each plug class under the entry-point group `metrics_agent.plugs`,
with the plug's name as the entry-point name:

    [project.entry-points."metrics_agent.plugs"]
    local-files = "metrics_agent_files:LocalFiles"

The core imports a plug's module only when the plug is used (`load`); listing what is
installed (`installed`) reads package metadata and nothing else, so a broken or heavy plug
package cannot break `metrics-agent plugs`.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import Any

from metrics_agent import sockets as S

GROUP = "metrics_agent.plugs"
KINDS = ("input", "output", "observer")


class PlugError(RuntimeError):
    """A plug is missing, registered twice, or does not meet its contract."""


@dataclass(frozen=True)
class Installed:
    name: str
    package: str
    version: str
    target: str                            # "module:Class"
    entry_point: Any

    def load(self) -> type:
        try:
            cls = self.entry_point.load()
        # SystemExit too: a module that exits while it is imported must not take the command
        # listing every other plug down with it. Only an interrupt is the person's.
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - any import failure is the plug's
            raise PlugError(f"plug {self.name!r} from {self.package} {self.version} could not "
                            f"be loaded ({self.target}): {type(exc).__name__}: {exc}") from exc
        _check_class(self, cls)
        return cls


def _dist_of(ep) -> tuple[str, str]:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return "unknown", "unknown"
    return dist.metadata["Name"] or "unknown", dist.version or "unknown"


def installed() -> dict[str, Installed]:
    """Every registered plug by name. Two packages registering one name is an error that names
    both, never a silent choice."""
    found: dict[str, Installed] = {}
    for ep in metadata.entry_points(group=GROUP):
        package, version = _dist_of(ep)
        item = Installed(ep.name, package, version, ep.value, ep)
        if ep.name in found:
            other = found[ep.name]
            if (other.package, other.target) == (package, ep.value):
                continue                   # the same distribution seen twice on sys.path
            raise PlugError(f"plug {ep.name!r} is registered twice: by {other.package} "
                            f"{other.version} ({other.target}) and by {package} {version} "
                            f"({ep.value}). Uninstall one of them.")
        found[ep.name] = item
    return found


def load(name: str) -> type:
    """The plug class registered as `name`."""
    plugs = installed()
    if name not in plugs:
        known = ", ".join(sorted(plugs)) or "none"
        raise PlugError(f"no plug named {name!r} is installed (installed: {known})")
    return plugs[name].load()


def check_class(cls: Any, name: str | None = None) -> None:
    """Refuse `cls` exactly as `load` would, without installing it first.

    `name` is the entry-point name the class is (or will be) registered under; by default the
    class's own `name`. This is the check the conformance kit runs, so a plug that passes the
    kit is not refused when it is installed."""
    module = getattr(cls, "__module__", None) or "?"
    target = f"{module}:{getattr(cls, '__qualname__', None) or getattr(cls, '__name__', '?')}"
    declared = getattr(cls, "name", None)
    if name is None:
        if not isinstance(declared, str) or not declared.strip():
            raise PlugError(f"plug {target} declares no `name`; every plug needs the name it "
                            "is registered under")
        name = declared
    _check_class(Installed(name, module.split(".")[0], "", target, None), cls)


def _check_class(item: Installed, cls: Any) -> None:
    where = f"plug {item.name!r} ({item.target}, from {item.package})"
    kind = getattr(cls, "kind", None)
    if kind not in KINDS:
        raise PlugError(f"{where} declares kind {kind!r}; expected one of {', '.join(KINDS)}")
    if getattr(cls, "name", None) != item.name:
        raise PlugError(f"{where} calls itself {getattr(cls, 'name', None)!r}; the entry-point "
                        "name and the plug's `name` must match")
    env = getattr(cls, "requires_env", None)
    if not isinstance(env, tuple) or not all(isinstance(e, str) for e in env):
        raise PlugError(f"{where}: `requires_env` must be a tuple of variable names")
    if kind == "input" and getattr(cls, "custody", None) not in (S.CUSTOMER, S.THIRD_PARTY):
        raise PlugError(f"{where}: an input plug must declare custody '{S.CUSTOMER}' or "
                        f"'{S.THIRD_PARTY}'")
    lane = getattr(cls, "lane", None)
    if kind == "output" and not (isinstance(lane, str) and lane.strip()):
        raise PlugError(f"{where}: an output plug must declare its `lane` (the publish target "
                        "that admits it)")
    if kind == "observer":
        missing = [m for m in S.OBSERVER_REQUIRED if not callable(getattr(cls, m, None))]
        if missing:
            raise PlugError(f"{where}: an observer must define {', '.join(missing)} "
                            "(see sockets.Observer)")


def describe(cls: type) -> dict[str, Any]:
    """What `metrics-agent plugs` prints about one plug class."""
    out: dict[str, Any] = {"name": cls.name, "kind": cls.kind,
                           "requires_env": list(cls.requires_env)}
    if cls.kind == "input":
        out["custody"] = cls.custody
        out["capabilities"] = list(S.capabilities(cls))
    if cls.kind == "output":
        out["lane"] = cls.lane
    return out


def listing() -> list[dict[str, Any]]:
    """Every installed plug, loaded and described; a plug that fails to load is listed with
    its error instead of hiding the others."""
    rows = []
    for name, item in sorted(installed().items()):
        row: dict[str, Any] = {"name": name, "package": item.package, "version": item.version}
        try:
            row.update(describe(item.load()))
        except PlugError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def declared(attribute: str) -> list[tuple[str, Any]]:
    """[(plug name, value)] of an attribute every installed plug that declares it holds, in
    name order. A plug that cannot load declares nothing; it is listed with its error by
    `metrics-agent plugs`, never here."""
    out = []
    for name, item in sorted(installed().items()):
        try:
            value = getattr(item.load(), attribute, None)
        except PlugError:
            continue
        if value:
            out.append((name, value))
    return out


def issued_markers() -> tuple[str, ...]:
    """Every marker an installed plug declares for the statements its own system issues
    (`sockets.issued_markers`): a preview or a crawl is never evidence of analysis."""
    return tuple(dict.fromkeys(str(m) for _n, ms in declared("issued_markers") for m in ms))
