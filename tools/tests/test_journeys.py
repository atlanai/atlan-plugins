"""Golden journeys: offline checks of tools/tests/fixtures/journeys.json against the skills.

Each journey is the route one use case from the 3 November Registry and Analytics review should
take, cold (a new session) and warm (later in the same session). These checks keep the routes
honest without a tenant: every tool a journey calls is one its skill names, each call goes
through the runner its tier needs, the counts match the budget, and warm never costs more than
cold. A live run in a sandbox tenant fills each journey's `measured` block from the same file.

    python3 tools/tests/test_journeys.py --report   # calls, turns and workarounds per journey
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "journeys.json"
SHARED = ROOT / "plugins" / "atlanai" / "skills" / "atlan-get-started" / "references" / "calling-tools.md"
PLUGINS = ("atlanai", "atlanai-analytics")
RUNNERS = {"call_read_tool", "call_write_tool", "call_tool", "direct", "warehouse"}
DIRECT = {"mcp__search_tools", "mcp__describe_tools"}
# A read never goes through call_tool, so it skips the Codex and Cursor publish hook; a
# destructive call goes only through call_tool, so the host can ask first.
READ = re.compile(r"(?:^mcp__retrieve_artifacts$|__(?:get|list|search|read|retrieve)(?:_|$))")
DESTRUCTIVE = re.compile(r"__(?:archive|release|approve|delete|withdraw|move|revoke|uninstall)(?:_|$)")
OWN = re.compile(r"^(?:registry|extension__[a-z]+|mcp)__")
ASKS = {f"T{n}" for n in range(1, 18)}


def load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def by_id(doc: dict) -> dict:
    return {j["id"]: j for j in doc["journeys"]}


def calls(doc: dict, journey: dict, which: str):
    """The journey's cold or warm calls, with `includes` expanded; None when it has no such run."""
    if journey.get("includes"):
        if which != "cold":
            return None  # a composite flow is measured cold, end to end
        out: list = []
        for ref in journey["includes"]:
            out.extend(calls(doc, by_id(doc)[ref], "cold") or [])
        return out
    return (journey.get("calls") or {}).get(which)


def count(run) -> int:
    return sum(int(c.get("repeat", 1)) for c in run)


def turns(run) -> int:
    """Serial turns: contiguous calls sharing a `group` go out together as parallel calls; a
    repeated call with no group is that many turns, one after another."""
    n, last = 0, object()
    for c in run:
        g = c.get("group")
        if g is None:
            n += int(c.get("repeat", 1))
        elif g != last:
            n += 1
        last = g
    return n


def skill_text(plugin: str, skill: str) -> str:
    folder = ROOT / "plugins" / plugin / "skills" / skill
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.rglob("*.md")))


def workarounds(doc: dict) -> dict:
    """Calls that exist only until a tool ask ships, by ask: what milestone D can delete."""
    out: dict = {}
    for j in doc["journeys"]:
        if j.get("workaround_for"):
            out.setdefault(j["workaround_for"], []).append(f"{j['id']} (whole publish)")
        for which in ("cold", "warm"):
            for c in (j.get("calls") or {}).get(which) or []:
                if c.get("workaround_for"):
                    out.setdefault(c["workaround_for"], []).append(f"{j['id']} {which}: {c['tool']}")
    return out


class JourneysTest(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = load()
        self.journeys = self.doc["journeys"]

    def test_ids_are_unique_and_includes_resolve(self) -> None:
        ids = [j["id"] for j in self.journeys]
        self.assertEqual(len(ids), len(set(ids)))
        for j in self.journeys:
            for ref in j.get("includes") or []:
                self.assertIn(ref, ids, j["id"])

    def test_every_skill_has_a_journey(self) -> None:
        for plugin in PLUGINS:
            skills = {p.name for p in (ROOT / "plugins" / plugin / "skills").iterdir() if (p / "SKILL.md").is_file()}
            covered = {j["skill"] for j in self.journeys if j["plugin"] == plugin}
            self.assertEqual(skills - covered, set(), plugin)
            self.assertEqual(covered - skills, set(), f"{plugin}: journeys name skills that do not exist")

    def test_each_call_is_one_its_skill_names_through_the_right_runner(self) -> None:
        shared = SHARED.read_text(encoding="utf-8")
        problems = []
        for j in self.journeys:
            if j.get("includes"):
                continue
            text = skill_text(j["plugin"], j["skill"]) + shared
            for which in ("cold", "warm"):
                searched = False
                for c in calls(self.doc, j, which) or []:
                    where = f"{j['id']} {which} {c['tool']}"
                    tool, runner = c["tool"], c["runner"]
                    if runner not in RUNNERS:
                        problems.append(f"{where}: unknown runner {runner}")
                        continue
                    if runner == "warehouse":
                        continue
                    if (tool in DIRECT) != (runner == "direct"):
                        problems.append(f"{where}: only search and describe are called directly")
                    searched = searched or tool == "mcp__search_tools"
                    if OWN.match(tool):
                        if tool not in text:
                            problems.append(f"{where}: the skill never names this tool")
                    elif which == "cold" and not searched:
                        problems.append(f"{where}: a downstream tool is found by mcp__search_tools first")
                    if runner == "direct":
                        continue
                    if READ.search(tool) and runner != "call_read_tool":
                        problems.append(f"{where}: a read goes through call_read_tool, not {runner}")
                    elif DESTRUCTIVE.search(tool) and runner != "call_tool":
                        problems.append(f"{where}: a destructive call goes through call_tool")
                    elif not READ.search(tool) and runner == "call_read_tool":
                        problems.append(f"{where}: a write cannot go through call_read_tool")
        self.assertEqual(problems, [])

    def test_counts_match_the_budget_and_warm_never_costs_more(self) -> None:
        for j in self.journeys:
            cold, warm = calls(self.doc, j, "cold"), calls(self.doc, j, "warm")
            self.assertTrue(cold, f"{j['id']}: no cold route")
            budget = j.get("budget") or {}
            for which, run in (("cold", cold), ("warm", warm)):
                if budget.get(which) is not None:
                    self.assertIsNotNone(run, f"{j['id']} {which}")
                    self.assertEqual(count(run), budget[which], f"{j['id']} {which}")
            if warm is not None and not j.get("includes"):
                self.assertLessEqual(count(warm), count(cold), j["id"])
                self.assertLessEqual(turns(warm), turns(cold), j["id"])

    def test_workarounds_name_a_tool_ask(self) -> None:
        listed = workarounds(self.doc)
        self.assertTrue(set(listed) <= ASKS, set(listed) - ASKS)
        self.assertTrue(listed, "no workaround calls are marked")

    def test_measured_runs_carry_the_date_and_versions(self) -> None:
        for j in self.journeys:
            m = j.get("measured")
            if m is None:
                continue
            for runs in m if isinstance(m, list) else [m]:
                self.assertRegex(runs.get("date", ""), r"^\d{4}-\d{2}-\d{2}$", j["id"])
                self.assertTrue(set(runs.get("versions") or {}) & set(PLUGINS), j["id"])


def report() -> None:
    doc = load()
    for j in doc["journeys"]:
        cold, warm = calls(doc, j, "cold"), calls(doc, j, "warm")
        w = f"{count(warm)} calls / {turns(warm)} turns" if warm is not None else "-"
        print(f"{j['id']:32} cold {count(cold):3} calls / {turns(cold):2} turns   warm {w}")
    print()
    for ask, where in sorted(workarounds(doc).items(), key=lambda kv: int(kv[0][1:])):
        print(f"{ask}: {len(where)} call(s) to drop once it ships")
        for w in where:
            print(f"  {w}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        unittest.main()
