"""The gateway emulator's 2.21.0 surfaces (evals/atlan/emulator/atlan_emulator.py), driven in process:
batch reads, direct `registry__` calls, the `gateway` block, disabled servers, the richer registry
reads, the publish version conflict and the absent `withdraw_release`. No subprocess, no network."""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

EMULATOR = Path(__file__).resolve().parents[2] / "evals" / "atlan" / "emulator" / "atlan_emulator.py"


def load(**env: str):
    """A fresh emulator module, with its switches read from `env` alone."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ATLAN_EMULATOR_")}
    with mock.patch.dict(os.environ, clean | env, clear=True):
        spec = importlib.util.spec_from_file_location("atlan_emulator_under_test", EMULATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def call(module, name: str, arguments: dict) -> tuple[dict, dict]:
    """One `tools/call`: the JSON-RPC result and its decoded body (the text itself when it is bare)."""
    reply = module.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    text = reply["result"]["content"][0]["text"]
    try:
        return reply["result"], json.loads(text)
    except json.JSONDecodeError:
        return reply["result"], text


class BatchReadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load(ATLAN_EMULATOR_LIVE_SURFACE="1")
        self.rails = {"session_id": self.m.SESSION, "rationale": "Testing the emulator."}

    def test_the_default_surface_lists_neither_the_batch_runner_nor_the_direct_tools(self) -> None:
        default = load()
        tools = {t["name"] for t in default.handle({"id": 1, "method": "tools/list"})["result"]["tools"]}
        self.assertNotIn("mcp__call_read_tools", tools)
        self.assertFalse(tools & set(default.DIRECT_TOOLS))
        self.assertNotIn("mcp__call_read_tools", default.HANDLERS)

    def test_a_direct_call_needs_no_rationale(self) -> None:
        result, body = call(self.m, "registry__list_workspaces", {"session_id": self.m.SESSION, "kind": "user_default"})
        self.assertFalse(result["isError"], body)
        self.assertEqual([w["kind"] for w in body["items"]], ["user_default"])

    def test_tools_list_names_the_batch_runner_and_the_direct_registry_tools_with_rails(self) -> None:
        tools = {t["name"]: t for t in self.m.handle({"id": 1, "method": "tools/list"})["result"]["tools"]}
        self.assertIn("mcp__call_read_tools", tools)
        self.assertEqual(tools["mcp__call_read_tools"]["inputSchema"]["properties"]["calls"]["maxItems"], 8)
        for name in self.m.DIRECT_TOOLS:
            self.assertIn(name, tools, name)
            self.assertLessEqual({"session_id", "rationale"}, set(tools[name]["inputSchema"]["properties"]), name)
        self.assertNotIn("registry__withdraw_release", tools)

    def test_a_batch_answers_each_slot_in_order_and_fails_only_the_slot_that_is_refused(self) -> None:
        result, body = call(self.m, "mcp__call_read_tools", self.rails | {"calls": [
            {"name": "registry__get_artifact_counts", "arguments": {"group_by": "kind"}},
            {"name": "registry__list_artifacts", "arguments": {"kind": "skill", "nonsense": 1}},
            {"name": "registry__create_artifact", "arguments": {"kind": "file", "metadata": {}}},
            {"name": "registry__list_workspaces", "arguments": {}}]})
        self.assertFalse(result["isError"])
        slots = body["results"]
        self.assertEqual([s["name"] for s in slots], ["registry__get_artifact_counts", "registry__list_artifacts",
                                                       "registry__create_artifact", "registry__list_workspaces"])
        self.assertEqual([s["isError"] for s in slots], [False, True, True, False])
        self.assertEqual(slots[0]["_meta"], {"com.atlan/called-tool": "registry__get_artifact_counts",
                                             "com.atlan/called-via": "mcp__call_read_tool"})
        self.assertEqual(slots[0]["resultType"], "complete")
        self.assertIn("total", json.loads(slots[0]["content"][0]["text"]))
        self.assertEqual(json.loads(slots[1]["content"][0]["text"])["code"], "invalid_arguments")
        self.assertEqual(json.loads(slots[2]["content"][0]["text"])["code"], "runner_tier")
        self.assertEqual(self.m.state["created"], {}, "a write slot writes nothing")

    def test_a_batch_outside_one_to_eight_calls_or_without_rationale_is_refused_whole(self) -> None:
        for arguments in ({"calls": [], **self.rails},
                          {"calls": [{"name": "registry__list_workspaces", "arguments": {}}] * 9, **self.rails},
                          {"calls": [{"name": "registry__list_workspaces", "arguments": {}}], "session_id": self.m.SESSION}):
            result, body = call(self.m, "mcp__call_read_tools", arguments)
            self.assertTrue(result["isError"], arguments)
            self.assertIn(body["code"], ("invalid_arguments", "rationale_required"))

    def test_the_first_batch_opens_the_session_and_returns_its_receipt(self) -> None:
        _, body = call(self.m, "mcp__call_read_tools", {"objective": "o", "rationale": "r",
                                                        "calls": [{"name": "registry__list_workspaces", "arguments": {}}]})
        self.assertEqual(body["mcp_session_id"], self.m.SESSION)
        self.assertEqual(len(body["results"]), 1)

    def test_a_single_runner_result_carries_meta(self) -> None:
        result, _ = call(self.m, "mcp__call_read_tool", self.rails | {"name": "registry__list_workspaces", "arguments": {}})
        self.assertEqual(result["_meta"], {"com.atlan/called-tool": "registry__list_workspaces",
                                           "com.atlan/called-via": "mcp__call_read_tool"})

    def test_a_direct_registry_call_takes_the_rails_as_its_own_arguments(self) -> None:
        result, body = call(self.m, "registry__search_artifacts", self.rails | {"query": "security review", "kind": "skill"})
        self.assertFalse(result["isError"])
        self.assertEqual(body["items"][0]["id"], "skill_01evalsecrev01")
        # Gateway 2.21.0 lists `rationale` as optional on a direct call.
        result, body = call(self.m, "registry__search_artifacts", {"query": "security review"})
        self.assertFalse(result["isError"], body)
        _, body = call(self.m, "registry__search_artifacts", {"query": "security review", "objective": "o", "rationale": "r"})
        self.assertEqual(body["mcp_session_id"], self.m.SESSION)
        # The same tool still runs behind its runner.
        result, body = call(self.m, "mcp__call_read_tool", self.rails | {"name": "registry__search_artifacts",
                                                                          "arguments": {"query": "security review"}})
        self.assertFalse(result["isError"])
        self.assertEqual(body["items"][0]["id"], "skill_01evalsecrev01")


class SearchReplyTest(unittest.TestCase):
    def test_the_first_search_carries_the_gateway_block_and_later_ones_do_not(self) -> None:
        m = load()
        _, first = call(m, "mcp__search_tools", {"query": "search artifacts", "objective": "o"})
        self.assertEqual(first["gateway"]["app_version"], "2.21.0")
        self.assertIn("batch_reads", first["gateway"]["features"])
        self.assertEqual(first["gateway"]["runners"]["read"], "mcp__call_read_tool")
        self.assertEqual(set(first["tools"][0]), {"name", "server", "description", "annotations", "input_schema", "run_with"})
        _, later = call(m, "mcp__search_tools", {"query": "search artifacts", "session_id": m.SESSION})
        self.assertNotIn("gateway", later)
        self.assertNotIn("unavailable", later)

    def test_a_disabled_server_is_listed_under_unavailable_and_its_tools_are_not_served(self) -> None:
        m = load(ATLAN_EMULATOR_DISABLED_SERVERS="slack")
        _, body = call(m, "mcp__search_tools", {"query": "send a slack message", "objective": "o"})
        self.assertEqual(body["unavailable"], [{"note": m.DISABLED_NOTE, "reason": "disabled", "server": "slack"}])
        self.assertTrue(body["next"].startswith("The servers in `unavailable` are registered in Atlan but disabled"))
        self.assertEqual([t for t in body["tools"] if t["server"] == "slack"], [])
        result, body = call(m, "mcp__call_write_tool", {"name": "slack__send_message", "arguments": {"channel": "c", "text": "t"},
                                                        "session_id": m.SESSION, "rationale": "r"})
        self.assertTrue(result["isError"])
        self.assertEqual(body["code"], "tool_unavailable")
        hidden = load(ATLAN_EMULATOR_HIDE_SERVERS="slack")
        _, body = call(hidden, "mcp__search_tools", {"query": "send a slack message", "objective": "o"})
        self.assertNotIn("unavailable", body)


class RegistryReadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load()
        self.rails = {"session_id": self.m.SESSION, "rationale": "Testing the emulator."}

    def read(self, name: str, arguments: dict) -> dict:
        result, body = call(self.m, "mcp__call_read_tool", self.rails | {"name": name, "arguments": arguments})
        self.assertFalse(result["isError"], body)
        return body

    def test_list_workspaces_names_the_personal_workspace_and_each_row_says_whether_it_is_org_wide(self) -> None:
        body = self.read("registry__list_workspaces", {})
        self.assertEqual(body["personal_workspace_id"], "workspace_01evaldana")
        rows = {w["id"]: w for w in body["items"]}
        self.assertTrue(rows["workspace_01evalorg"]["org_wide"])
        self.assertFalse(rows["workspace_01evalplatform"]["org_wide"])
        self.assertIsNone(rows["workspace_01evalplatform"]["parent_id"])

    def test_search_hits_carry_a_similarity_in_the_unit_interval_and_the_page_says_whether_more_exist(self) -> None:
        body = self.read("registry__search_artifacts", {"query": "security review", "kind": "skill", "limit": 1})
        hit = body["items"][0]
        self.assertTrue(hit["match"]["keyword"])
        self.assertTrue(0 < hit["match"]["similarity"] <= 1)
        self.assertEqual(hit["workspace_display_name"], "Platform Engineering")
        self.assertEqual(hit["workspace_kind"], "default")
        self.assertIn("is_released", hit)
        self.assertTrue(body["page"]["has_more"])
        again = self.read("registry__search_artifacts", {"query": "security review", "kind": "skill", "limit": 1})
        self.assertEqual(again["items"][0]["match"], hit["match"], "the similarity is deterministic")
        self.assertFalse(self.read("registry__search_artifacts", {"query": "security review", "kind": "skill"})["page"]["has_more"])

    def test_get_artifact_keeps_only_the_named_properties_and_sizes_the_rest(self) -> None:
        body = self.read("registry__get_artifact", {"artifact_id": "skill_01evaloncall", "properties": ["file_count"], "verbose": True})
        self.assertEqual(body["metadata"], {"file_count": 4})
        self.assertEqual(set(body["omitted_properties"]), {"files"})
        self.assertGreater(body["omitted_properties"]["files"], 100)
        whole = self.read("registry__get_artifact", {"artifact_id": "skill_01evaloncall", "verbose": False})
        self.assertNotIn("omitted_properties", whole)
        self.assertEqual(len(whole["metadata"]["files"]), 4)


class PublishVersionConflictTest(unittest.TestCase):
    REPORT = "October incident review. The outage lasted 38 minutes. Follow-up: expiry alerts page 14 days ahead.\n"

    def write(self, m, name: str, arguments: dict) -> tuple[dict, dict]:
        return call(m, "mcp__call_write_tool", {"name": name, "arguments": arguments, "session_id": m.SESSION, "rationale": "r"})

    def publish(self, m, **extra) -> tuple[dict, dict]:
        return self.write(m, "extension__agent__publish_output", {
            "metadata": {"name": "october-incident-review", "workspace_id": "workspace_01evalplatform"},
            "source_type": "local", "source_id": "claude/october-incident-review",
            "file": {"path": "october-incident-review.md", "content": self.REPORT}, **extra})

    def test_a_new_version_through_publish_output_is_the_bare_409_text_and_writes_nothing(self) -> None:
        m = load(ATLAN_EMULATOR_PUBLISH_VERSION_CONFLICT="1")
        for extra in ({}, {"edges": [{"rel_type": "used_skill", "to_id": "skill_01evaloncall"}]}):
            result, body = self.publish(m, **extra)
            self.assertTrue(result["isError"])
            self.assertEqual(body, "The request conflicts with the current state of the resource.")
        self.assertEqual(m.state["versions"], {})
        self.assertEqual(m.state["heads"], {})
        _, first = self.write(m, "extension__agent__publish_output", {
            "metadata": {"name": "fresh", "workspace_id": "workspace_01evalplatform"}, "source_type": "local",
            "source_id": "claude/fresh", "file": {"path": "fresh.md", "content": "Fresh.\n"}})
        self.assertTrue(first["created"], "a first share under a new source key still succeeds")
        self.assertEqual(first["file"]["written"], "created")

    def test_the_older_route_lands_the_next_version_under_the_same_handle(self) -> None:
        m = load(ATLAN_EMULATOR_PUBLISH_VERSION_CONFLICT="1")
        rails = {"session_id": m.SESSION, "rationale": "r"}
        _, head = call(m, "mcp__call_read_tool", rails | {"name": "registry__get_artifact", "arguments": {"artifact_id": "file_01evalincidentmd"}})
        result, appended = self.write(m, "registry__append_artifact_version", {
            "artifact_id": "file_01evalincidentmd", "if_match": head["etag"], "content": self.REPORT,
            "metadata": {"path": head["metadata"]["path"]}})
        self.assertFalse(result["isError"], appended)
        self.assertEqual(appended["version_ordinal"], 2)
        result, created = self.write(m, "registry__create_artifact", {"kind": "output", "metadata": {
            "name": "october-incident-review", "workspace_id": "workspace_01evalplatform", "source_type": "local",
            "source_id": "claude/october-incident-review", "mode": "upload", "file_artifact_id": "file_01evalincidentmd",
            "file_version_ordinal": 2}})
        self.assertFalse(result["isError"], created)
        self.assertEqual((created["id"], created["version_ordinal"]), ("output_01evalincident", 2))
        _, shown = call(m, "mcp__call_read_tool", rails | {"name": "registry__get_artifact", "arguments": {"artifact_id": "output_01evalincident"}})
        self.assertEqual(shown["version_ordinal"], 2)
        self.assertEqual(shown["metadata"]["file_version_ordinal"], 2)

    def test_without_the_switch_publish_output_still_makes_the_next_version(self) -> None:
        result, body = self.publish(load())
        self.assertFalse(result["isError"], body)
        self.assertEqual((body["output"]["id"], body["output"]["version_ordinal"], body["created"]), ("output_01evalincident", 2, False))


class WithdrawReleaseTest(unittest.TestCase):
    def test_withdraw_release_is_absent_by_default_and_describe_tools_says_so_by_name(self) -> None:
        m = load()
        _, body = call(m, "mcp__describe_tools", {"names": ["registry__withdraw_release", "registry__archive_artifact"], "session_id": m.SESSION})
        self.assertEqual([t["name"] for t in body["tools"]], ["registry__archive_artifact"])
        self.assertEqual(body["unknown"], [{"name": "registry__withdraw_release", "note": m.UNKNOWN_TOOL}])
        result, body = call(m, "mcp__call_tool", {"name": "registry__withdraw_release", "arguments": {"release_id": "rel_01evaloncall110"},
                                                 "session_id": m.SESSION, "rationale": "r"})
        self.assertTrue(result["isError"])
        self.assertEqual(body["code"], "tool_unavailable")
        _, found = call(m, "mcp__search_tools", {"query": "withdraw a release roll back", "objective": "o"})
        self.assertNotIn("registry__withdraw_release", [t["name"] for t in found["tools"]])

    def test_the_switch_restores_it(self) -> None:
        m = load(ATLAN_EMULATOR_SERVE_WITHDRAW="1")
        _, body = call(m, "mcp__describe_tools", {"names": ["registry__withdraw_release"], "session_id": m.SESSION})
        self.assertEqual([t["name"] for t in body["tools"]], ["registry__withdraw_release"])
        self.assertNotIn("unknown", body)
        result, body = call(m, "mcp__call_tool", {"name": "registry__withdraw_release", "arguments": {"release_id": "rel_01evaloncall110"},
                                                 "session_id": m.SESSION, "rationale": "r"})
        self.assertFalse(result["isError"], body)
        self.assertEqual(body["current_version_label"], "1.0.0")


class RegistryToolCoverageTest(unittest.TestCase):
    """The registry tools the skills name beyond the first set: several files in one read, a bulk
    create, an edge, a create with its edge, and a scan run again."""

    def setUp(self) -> None:
        self.m = load()
        self.rails = {"session_id": self.m.SESSION, "rationale": "Testing the emulator."}

    def run_tool(self, runner: str, name: str, arguments: dict) -> tuple[dict, dict]:
        return call(self.m, runner, self.rails | {"name": name, "arguments": arguments})

    def test_every_tool_is_served_and_found_on_the_runner_its_tier_needs(self) -> None:
        names = ["registry__get_file_contents", "registry__create_artifacts", "registry__relate_artifact",
                 "registry__create_artifact_linked", "registry__retrigger_artifact_action"]
        _, body = call(self.m, "mcp__describe_tools", {"names": names, "session_id": self.m.SESSION})
        self.assertEqual(sorted(t["name"] for t in body["tools"]), sorted(names))
        self.assertNotIn("unknown", body)
        result, body = self.run_tool("mcp__call_read_tool", "registry__relate_artifact",
                                     {"artifact_id": "skill_01evalsecrev01", "subject_id": "skill_01evalsecrev02", "rel_type": "replaced_by"})
        self.assertTrue(result["isError"])
        self.assertEqual(body["code"], "runner_tier")

    def test_several_files_read_in_order_and_one_unreadable_entry_fails_alone(self) -> None:
        result, body = self.run_tool("mcp__call_read_tool", "registry__get_file_contents", {"files": [
            {"file_id": "file_01evalsecrevmd", "version_ordinal": 1}, {"file_id": "file_01evalmissing"},
            {"file_id": "file_01evalsecrevref"}]})
        self.assertFalse(result["isError"], body)
        self.assertEqual([f["file_id"] for f in body["files"]], ["file_01evalsecrevmd", "file_01evalmissing", "file_01evalsecrevref"])
        self.assertIn("# Security review", body["files"][0]["content"])
        self.assertEqual((body["files"][1]["code"], "content" in body["files"][1]), ("not_found", False))
        self.assertEqual(body["files"][2]["path"], "reference.md")
        result, body = self.run_tool("mcp__call_read_tool", "registry__get_file_contents", {"files": [{"file_id": "file_01evalmissing"}]})
        self.assertTrue(result["isError"])
        result, body = self.run_tool("mcp__call_read_tool", "registry__get_file_contents",
                                     {"files": [{"file_id": "file_01evalsecrevmd"}] * 9})
        self.assertEqual(body["code"], "invalid_arguments")

    def test_an_edge_is_written_once_however_often_it_is_stated_and_reads_back(self) -> None:
        edge = {"artifact_id": "skill_01evalsecrev01", "subject_id": "skill_01evalsecrev02", "rel_type": "replaced_by"}
        for attributes in ({"note": "first"}, {"note": "second"}):
            result, body = self.run_tool("mcp__call_write_tool", "registry__relate_artifact", edge | {"attributes": attributes})
            self.assertFalse(result["isError"], body)
        self.assertEqual((body["from_id"], body["to_id"], body["attributes"]), ("skill_01evalsecrev01", "skill_01evalsecrev02", {"note": "second"}))
        self.assertEqual(len(self.m.state["edges"]), 1)
        _, related = self.run_tool("mcp__call_read_tool", "registry__get_related_artifacts", {"artifact_id": "skill_01evalsecrev02"})
        self.assertIn({"declared": True, "rel_type": "replaced_by", "direction": "incoming", "artifact_id": "skill_01evalsecrev01",
                       "kind": "skill", "is_readable": True}, related["items"])
        _, undeclared = self.run_tool("mcp__call_read_tool", "registry__get_related_artifacts",
                                      {"artifact_id": "skill_01evalsecrev02", "declared": False})
        self.assertEqual(undeclared["items"], [])
        result, body = self.run_tool("mcp__call_write_tool", "registry__relate_artifact",
                                     {"artifact_id": "skill_01evalsecrev01", "rel_type": "replaced_by"})
        self.assertEqual(body["code"], "invalid_arguments")
        result, body = self.run_tool("mcp__call_write_tool", "registry__relate_artifact", edge | {"subject_id": "skill_01evalnone"})
        self.assertEqual(body["code"], "not_found")

    def test_a_bulk_create_answers_one_slot_per_item_and_refuses_kinds_that_need_their_own_call(self) -> None:
        result, body = self.run_tool("mcp__call_write_tool", "registry__create_artifacts", {"kind": "knowledge", "items": [
            {"name": "refund-policy", "workspace_id": "workspace_01evalplatform"}, {"name": "no-workspace"}]})
        self.assertFalse(result["isError"], body)
        self.assertEqual([(s["index"], s["ok"]) for s in body["items"]], [(0, True), (1, False)])
        self.assertEqual((body["created"], body["failed"]), (1, 1))
        self.assertEqual(body["items"][0]["artifact"]["kind"], "knowledge")
        result, body = self.run_tool("mcp__call_write_tool", "registry__create_artifacts",
                                     {"kind": "agent", "items": [{"name": "bot", "workspace_id": "workspace_01evalplatform"}]})
        self.assertEqual(body["code"], "unsupported_kind")

    def test_a_linked_create_writes_the_artifact_and_its_edge_or_neither(self) -> None:
        linked = {"kind": "knowledge", "metadata": {"name": "refund-policy", "workspace_id": "workspace_01evalplatform"},
                  "rel_type": "documents"}
        result, body = self.run_tool("mcp__call_write_tool", "registry__create_artifact_linked", linked | {"subject_id": "skill_01evalnone"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.m.state["edges"], {}, "a refused edge creates nothing")
        result, body = self.run_tool("mcp__call_write_tool", "registry__create_artifact_linked", linked | {"subject_id": "skill_01evalsecrev01"})
        self.assertFalse(result["isError"], body)
        self.assertEqual((body["kind"], body["edges"][0]["to_id"], body["edges"][0]["rel_type"]), ("knowledge", "skill_01evalsecrev01", "documents"))

    def test_a_scan_run_again_is_a_new_run_each_time_and_approves_nothing(self) -> None:
        rerun = {"artifact_id": "skill_01evalpending", "action_name": "scan"}
        _, first = self.run_tool("mcp__call_write_tool", "registry__retrigger_artifact_action", rerun)
        _, second = self.run_tool("mcp__call_write_tool", "registry__retrigger_artifact_action", rerun)
        self.assertEqual((first["attempt"], first["reopened"], second["attempt"]), (1, False, 2))
        self.assertNotEqual(first["run_id"], second["run_id"])
        _, head = self.run_tool("mcp__call_read_tool", "registry__get_artifact", {"artifact_id": "skill_01evalpending"})
        self.assertEqual(head["artifact_status"], "pending_review")
        _, blocked = self.run_tool("mcp__call_write_tool", "registry__retrigger_artifact_action", rerun | {"artifact_id": "skill_01evalblocked"})
        self.assertTrue(blocked["reopened"])
        result, body = self.run_tool("mcp__call_write_tool", "registry__retrigger_artifact_action", rerun | {"artifact_id": "skill_01evalsecrev01"})
        self.assertEqual(body["code"], "conflict")
        result, body = self.run_tool("mcp__call_write_tool", "registry__retrigger_artifact_action", rerun | {"action_name": "publish"})
        self.assertEqual(body["code"], "unknown_action")


class GatewayLabelsAndChecksTest(unittest.TestCase):
    """What the gateway's tool definitions promise beyond the first set, which the synced
    `world.json` now advertises: workspace names on counts and browse rows, paging by offset, an
    output's file in the same read, a create checked without writing, and an approval pinned to
    the head that was read."""

    def setUp(self) -> None:
        self.m = load()
        self.rails = {"session_id": self.m.SESSION, "rationale": "Testing the emulator."}

    def run_tool(self, runner: str, name: str, arguments: dict) -> tuple[dict, dict]:
        return call(self.m, runner, self.rails | {"name": name, "arguments": arguments})

    def read(self, name: str, arguments: dict) -> dict:
        result, body = self.run_tool("mcp__call_read_tool", name, arguments)
        self.assertFalse(result["isError"], body)
        return body

    def test_a_workspace_count_group_and_a_browse_row_name_their_workspace(self) -> None:
        counted = self.read("registry__get_artifact_counts", {"kind": "skill", "group_by": "workspace_id", "limit": 2})
        self.assertEqual(len(counted["groups"]), 2)
        for group in counted["groups"]:
            self.assertTrue(group["workspace_display_name"], group)
            self.assertIn(group["workspace_kind"], ("default", "org_default", "user_default"))
        self.assertRegex(counted["note"], r"^showing the 2 largest of \d+ groups; raise `limit` to see more$")
        by_kind = self.read("registry__get_artifact_counts", {"group_by": "kind"})
        self.assertNotIn("workspace_display_name", by_kind["groups"][0])
        row = self.read("registry__list_artifacts", {"kind": "output", "limit": 1})["items"][0]
        self.assertTrue(row["workspace_display_name"])
        # One artifact read names it too, so a reply says "Marketing", not `workspace_…`.
        one = self.read("registry__get_artifact", {"artifact_id": row["id"]})
        self.assertEqual(one["workspace_display_name"], row["workspace_display_name"])
        self.assertEqual(one["workspace_kind"], row["workspace_kind"])

    def test_a_stuck_scan_answers_not_scanned_however_often_it_is_read(self) -> None:
        findings = ("registry__get_security_findings", {"artifact_id": "skill_01evalpending"})
        self.assertEqual(self.read(*findings)["scan_status"], "scanned")
        self.m = load(ATLAN_EMULATOR_NOT_SCANNED="skill_01evalpending")
        for _ in range(2):
            body = self.read(*findings)
            body = body.get("result", body)
            self.assertEqual((body["scan_status"], body["findings"], body["artifact_status"]), ("not_scanned", [], "pending_review"))

    def test_usage_statistics_count_runs_in_the_window_asked_for(self) -> None:
        now = int(self.m.time.time())
        week = {"start_time_unix_seconds": now - 7 * 86400, "end_time_unix_seconds": now}
        stats = "extension__skill__query_trace_stats"
        asked = week | {"request_type": "scalar", "queries": [
            {"name": "runs", "measure": "traces"}, {"name": "people", "measure": "active_users"},
            {"name": "spend", "measure": "cost"}]}
        body = self.read(stats, {"skill_id": "skill_01evalsecrev01", "query": asked})
        results = {row["query_name"]: row for row in body["data"]["results"]}
        self.assertEqual(results["runs"], {"query_name": "runs", "measure": "traces", "value": 42.0})
        self.assertEqual(results["people"]["value"], 9.0)
        self.assertNotIn("value", results["spend"], "a measure with no data has no value, which is not zero")
        self.assertEqual(body["meta"]["window"], week)
        # The same week a year early holds nothing: a window counted from memory finds no runs.
        early = {k: v - 365 * 86400 for k, v in week.items()}
        body = self.read(stats, {"subject_id": "skill_01evalsecrev01", "query": asked | early})
        self.assertEqual(body["data"]["results"][0]["value"], 0.0)
        days = self.read(stats, {"skill_id": "skill_01evalsecrev01", "query": week | {
            "request_type": "time_series", "bucket": "day", "compare": "previous_period",
            "queries": [{"name": "runs", "measure": "traces"}]}})
        self.assertEqual([point["value"] for point in days["data"]["series"][0]["points"]], [6.0] * 7)
        self.assertEqual(days["comparison"]["end_time_unix_seconds"], week["start_time_unix_seconds"])
        table = self.read(stats, {"skill_id": "skill_01evalsecrev01", "query": week | {
            "request_type": "table", "queries": [{"name": "runs", "measure": "traces", "group_by": ["skill_version"]}]}})
        self.assertEqual([(row["groups"], row["values"]) for row in table["data"]["rows"]],
                         [({"skill_version": "2"}, {"runs": 35.0}), ({"skill_version": "1"}, {"runs": 7.0})])
        # A skill nobody ran answers zero; an agent is asked through its own tool, by `subject_id`.
        unused = self.read(stats, {"skill_id": "skill_01evaltfplan01", "query": asked})
        self.assertEqual(unused["data"]["results"][0]["value"], 0.0)
        agent = self.read("extension__agent__query_trace_stats", {"subject_id": "agent_01evalreviewbot", "query": asked})
        self.assertEqual(agent["data"]["results"][0]["value"], 84.0)
        for arguments in ({"query": asked}, {"skill_id": "agent_01evalreviewbot", "query": asked},
                          {"skill_id": "skill_01evalsecrev01", "query": week | {"request_type": "time_series", "queries": []}}):
            result, refused = self.run_tool("mcp__call_read_tool", stats, arguments)
            self.assertTrue(result["isError"], refused)

    def test_offset_pages_a_browse_and_a_search_and_name_narrows_a_browse_to_one_line(self) -> None:
        first = self.read("registry__list_artifacts", {"kind": "skill", "limit": 2})
        second = self.read("registry__list_artifacts", {"kind": "skill", "limit": 2, "offset": 2})
        self.assertEqual(second["page"]["offset"], 2)
        self.assertFalse({i["id"] for i in first["items"]} & {i["id"] for i in second["items"]})
        named = self.read("registry__list_artifacts", {"kind": "skill", "name": "security-review"})
        self.assertEqual({i["name"] for i in named["items"]}, {"security-review"})
        page = self.read("registry__search_artifacts", {"query": "security review", "kind": "skill", "limit": 1})["page"]
        self.assertEqual(page["next_offset"], 1)
        rest = self.read("registry__search_artifacts", {"query": "security review", "kind": "skill", "offset": page["next_offset"]})
        self.assertNotIn("next_offset", rest["page"])
        self.assertEqual(self.read("registry__list_workspaces", {"limit": 1, "offset": 1})["page"]["offset"], 1)

    def test_a_project_reads_by_handle_or_by_exact_name_and_never_both(self) -> None:
        by_name = self.read("registry__get_project", {"name": "payments"})
        self.assertEqual(by_name, self.read("registry__get_project", {"project_id": "project_01evalpayments"}))
        for arguments in ({}, {"name": "payments", "project_id": "project_01evalpayments"}):
            result, body = self.run_tool("mcp__call_read_tool", "registry__get_project", arguments)
            self.assertEqual(body["code"], "invalid_arguments")
        result, body = self.run_tool("mcp__call_read_tool", "registry__get_project", {"name": "no-such"})
        self.assertEqual(body["code"], "not_found")

    def test_an_output_reads_with_its_file_in_one_call(self) -> None:
        body = self.read("registry__get_artifact", {"artifact_id": "output_01evalchurn", "include_linked_file": True})
        self.assertEqual(body["included_file"]["file_id"], "file_01evalchurnmd")
        self.assertTrue(body["included_file"]["content"])
        skill = self.read("registry__get_artifact", {"artifact_id": "skill_01evaloncall", "include_linked_file": True})
        self.assertIn("points at no file", skill["included_file"]["error"])
        result, body = self.run_tool("mcp__call_read_tool", "registry__get_artifact",
                                     {"artifact_id": "output_01evalchurn", "include_linked_file": True, "include_file": "SKILL.md"})
        self.assertEqual(body["code"], "invalid_arguments")

    def test_a_dry_run_says_what_a_create_would_do_and_writes_nothing(self) -> None:
        before = len(self.m.artifacts())
        publish = {"metadata": {"name": "october-incident-review", "workspace_id": "workspace_01evalplatform"},
                   "source_type": "local", "source_id": "claude/october-incident-review",
                   "file": {"path": "review.md", "content": "# Review"}, "dry_run": True}
        result, body = self.run_tool("mcp__call_write_tool", "extension__agent__publish_output", publish)
        self.assertFalse(result["isError"], body)
        self.assertEqual((body["dry_run"], body["committed"], body["would"], body["artifact_id"]),
                         (True, False, "append_version", "output_01evalincident"))
        self.assertIn("the inline file's own create, which runs the file kind's hook", body["not_checked"])
        result, body = self.run_tool("mcp__call_write_tool", "registry__create_artifact",
                                     {"kind": "knowledge", "metadata": {"name": "runbooks", "workspace_id": "workspace_01evaldana"},
                                      "dry_run": True})
        self.assertEqual((body["would"], body["artifact_id"]), ("create", None))
        self.assertEqual((len(self.m.artifacts()), self.m.state["versions"], self.m.state["created"]), (before, {}, {}))

    def test_an_approval_holds_only_for_the_head_that_was_read(self) -> None:
        head = self.read("registry__get_artifact", {"artifact_id": "skill_01evalpending"})
        named = {"artifact_id": head["id"], "expected_version_id": head["version_id"], "if_match": head["etag"],
                 "expected_status": head["artifact_status"]}
        result, body = self.run_tool("mcp__call_tool", "registry__approve_artifact", named | {"if_match": 'W/"v1"'})
        self.assertEqual((result["isError"], body["code"]), (True, "precondition_failed"))
        result, body = self.run_tool("mcp__call_tool", "registry__approve_artifact", {"artifact_id": head["id"], "version_ordinal": 2})
        self.assertEqual(body["code"], "invalid_arguments")
        result, body = self.run_tool("mcp__call_tool", "registry__approve_artifact", named)
        self.assertFalse(result["isError"], body)
        self.assertEqual(self.read("registry__get_artifact", {"artifact_id": head["id"]})["artifact_status"], "active")
        result, body = self.run_tool("mcp__call_tool", "registry__approve_artifact", named | {"expected_status": "active"})
        self.assertEqual(body["code"], "conflict")


if __name__ == "__main__":
    unittest.main()


class OwnerTransferTest(unittest.TestCase):
    """`registry__list_users` and an owner-moving `registry__patch_artifact`, held to the registry's
    rule: the current owner or a workspace admin may move it, an artifact with no owner takes one
    only from an admin, the new owner is a person in the directory, and `if_match` fences it."""

    def setUp(self) -> None:
        self.m = load()
        self.rails = {"session_id": self.m.SESSION, "rationale": "Testing the emulator."}

    def run_tool(self, runner: str, name: str, arguments: dict) -> tuple[dict, dict]:
        return call(self.m, runner, self.rails | {"name": name, "arguments": arguments})

    def head(self, handle: str) -> dict:
        result, body = self.run_tool("mcp__call_read_tool", "registry__get_artifact", {"artifact_id": handle})
        self.assertFalse(result["isError"], body)
        return body

    def test_people_are_found_by_name_or_email_in_name_order(self) -> None:
        result, body = self.run_tool("mcp__call_read_tool", "registry__list_users", {"query": "sam"})
        self.assertFalse(result["isError"], body)
        self.assertEqual([u["name"] for u in body["items"]], ["Sam Okafor", "Sam Patel"])
        _, body = self.run_tool("mcp__call_read_tool", "registry__list_users", {"query": "stefan@acme"})
        self.assertEqual([u["user_id"] for u in body["items"]], ["user_stefan"])
        _, body = self.run_tool("mcp__call_read_tool", "registry__list_users", {"query": "nobody"})
        self.assertEqual((body["items"], body["page"]["total"]), ([], 0))

    def test_a_sign_in_without_members_read_is_told_the_scope(self) -> None:
        self.m = load(ATLAN_EMULATOR_NO_MEMBERS_READ="1")
        self.rails = {"session_id": self.m.SESSION, "rationale": "Testing the emulator."}
        result, body = self.run_tool("mcp__call_read_tool", "registry__list_users", {"query": "stefan"})
        self.assertTrue(result["isError"])
        self.assertEqual(body["code"], "insufficient_scope")
        self.assertIn("members:read", body["detail"])

    def test_an_admin_moves_the_owner_on_the_etag_read_and_no_version_is_added(self) -> None:
        before = self.head("skill_01evaltfplan01")
        self.assertEqual(before["owner_id"], "user_priya")
        move = {"artifact_id": "skill_01evaltfplan01", "patch": {"owner_id": "user_stefan"}, "if_match": before["etag"]}
        result, body = self.run_tool("mcp__call_write_tool", "registry__patch_artifact", move)
        self.assertFalse(result["isError"], body)
        after = self.head("skill_01evaltfplan01")
        self.assertEqual((after["owner_id"], after["version_ordinal"]), ("user_stefan", before["version_ordinal"]))
        self.assertNotEqual(after["etag"], before["etag"], "the etag covers the owner")
        # The tag read before the move fences nothing now.
        result, body = self.run_tool("mcp__call_write_tool", "registry__patch_artifact",
                                     move | {"patch": {"owner_id": "user_priya"}})
        self.assertEqual((result["isError"], body["code"]), (True, "precondition_failed"))

    def test_a_weak_or_wildcard_tag_is_refused_before_anything_moves(self) -> None:
        for tag in ("*", 'W/"v1"', "a, b", " "):
            result, body = self.run_tool("mcp__call_write_tool", "registry__patch_artifact",
                                         {"artifact_id": "skill_01evaltfplan01", "patch": {"owner_id": "user_stefan"},
                                          "if_match": tag})
            self.assertEqual((result["isError"], body["code"]), (True, "invalid_arguments"), tag)
        self.assertEqual(self.head("skill_01evaltfplan01")["owner_id"], "user_priya")

    def test_an_unowned_artifact_outside_your_admin_workspaces_takes_no_owner(self) -> None:
        blocked = self.head("skill_01evalblocked")
        self.assertIsNone(blocked.get("owner_id"))
        result, body = self.run_tool("mcp__call_write_tool", "registry__patch_artifact",
                                     {"artifact_id": "skill_01evalblocked", "patch": {"owner_id": "user_dana"},
                                      "if_match": blocked["etag"]})
        self.assertEqual((result["isError"], body["code"]), (True, "forbidden"))
        self.assertIsNone(self.head("skill_01evalblocked").get("owner_id"))

    def test_the_new_owner_must_be_a_person_in_the_directory(self) -> None:
        result, body = self.run_tool("mcp__call_write_tool", "registry__patch_artifact",
                                     {"artifact_id": "skill_01evaltfplan01", "patch": {"owner_id": "user_ghost"}})
        self.assertEqual((result["isError"], body["code"]), (True, "unprocessable"))
