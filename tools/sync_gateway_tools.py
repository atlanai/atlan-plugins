#!/usr/bin/env python3
"""Make the eval emulator and the tool-name fixture follow the gateway's registry tool catalogue.

    python3 tools/sync_gateway_tools.py           # rewrite the two files
    python3 tools/sync_gateway_tools.py --check   # fail when either differs (run by `make check`)

`tools/tests/fixtures/gateway-catalogue.json` holds the registry tools as the gateway defines
them. The emulator answers `mcp__search_tools` and `mcp__describe_tools` from
`evals/atlan/emulator/world.json`, and a model picks tools and fills arguments from those
descriptions and schemas, so an eval run against stale ones measures a gateway that no longer
exists. This writes each catalogue tool's description, annotations and input schema into
`world.json`, keeping the emulator's own keys on a tool (`writes`, `refuse`, `result`) and every
tool the catalogue does not hold, and sets each catalogue tool's tier in
`tools/tests/fixtures/gateway-tools.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "tools" / "tests" / "fixtures" / "gateway-catalogue.json"
WORLD = ROOT / "evals" / "atlan" / "emulator" / "world.json"
NAMES = ROOT / "tools" / "tests" / "fixtures" / "gateway-tools.json"
# The fields of a tool the gateway owns. Everything else on an emulator tool is the emulator's.
OWNED = ("description", "annotations", "input_schema")
# Offered only where the deployment has a feedback destination, so the emulator leaves it out
# until a skill sends feedback through it.
UNSERVED = {"registry__submit_feedback"}


def tier(annotations: dict) -> str:
    """The runner tier a tool's annotations give it, as the gateway's `run_with` does."""
    if annotations.get("readOnlyHint"):
        return "read"
    return "destructive" if annotations.get("destructiveHint") else "write"


def catalogue() -> list[dict]:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))["tools"]


def synced_world(world: dict, tools: list[dict]) -> dict:
    """`world` with each catalogue tool's owned fields replaced, in the emulator's own order."""
    defined = {tool["name"]: tool for tool in tools}
    out = []
    for tool in world["tools"]:
        source = defined.get(tool["name"])
        if source is None:
            out.append(tool)
            continue
        out.append({"name": tool["name"]} | {key: source[key] for key in OWNED}
                   | {key: value for key, value in tool.items() if key != "name" and key not in OWNED})
    served = {tool["name"] for tool in out}
    for name, source in defined.items():
        if name not in served and name not in UNSERVED:
            out.append({"name": name} | {key: source[key] for key in OWNED})
    return world | {"tools": out}


def synced_names(names: dict, tools: list[dict]) -> dict:
    """`names` with each catalogue tool at its tier; a tool the catalogue lacks keeps its row."""
    tiers = names["registry_tools"] | {tool["name"]: tier(tool["annotations"]) for tool in tools}
    return names | {"registry_tools": dict(sorted(tiers.items()))}


def rendered(path: Path, data: dict) -> str:
    indent = 1 if path == WORLD else 2
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def drift() -> dict[Path, str]:
    """Each file whose synced text differs from what is on disk, with that text."""
    tools = catalogue()
    wanted = {
        WORLD: synced_world(json.loads(WORLD.read_text(encoding="utf-8")), tools),
        NAMES: synced_names(json.loads(NAMES.read_text(encoding="utf-8")), tools),
    }
    return {path: rendered(path, data) for path, data in wanted.items()
            if rendered(path, data) != path.read_text(encoding="utf-8")}


def main(argv: list[str]) -> int:
    stale = drift()
    if "--check" in argv:
        for path in stale:
            print(f"{path.relative_to(ROOT)} does not follow {CATALOGUE.relative_to(ROOT)}: "
                  "run python3 tools/sync_gateway_tools.py", file=sys.stderr)
        return 1 if stale else 0
    for path, text in stale.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
