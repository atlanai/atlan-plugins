"""The core eval harness (evals/atlan/run.py and metrics.py) and the emulator's sign-in line, driven
in process: no `claude` run, no network."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EVALS = Path(__file__).resolve().parents[2] / "evals" / "atlan"
ATLAN = "mcp__plugin_atlanai_atlan__"


def load(name: str):
    path = EVALS / ("emulator" if name == "atlan_emulator" else "") / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def use(ident: str, tool: str, tool_input: dict) -> dict:
    return {"type": "assistant", "timestamp": "2026-11-10T10:00:00Z",
            "message": {"id": f"msg-{ident}", "content": [{"type": "tool_use", "id": ident, "name": tool, "input": tool_input}]}}


def answer(ident: str, body: dict, is_error: bool = False) -> dict:
    return {"type": "user", "timestamp": "2026-11-10T10:00:05Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": ident, "is_error": is_error,
                                     "content": [{"type": "text", "text": json.dumps(body)}]}]}}


def result(cases: dict[str, list[tuple[str, bool]]]) -> dict:
    """A `claude plugin eval` result: each case's one run, with its graders as (name, passed)."""
    out = []
    for name, graders in cases.items():
        passed = all(ok for _, ok in graders)
        run = {"score": sum(ok for _, ok in graders) / len(graders), "passed": passed,
               "graders": [{"name": grader, "passed": ok} for grader, ok in graders]}
        out.append({"name": name, "arms": {"with": [run]}})
    return {"cases": out, "aggregates": {"casesTotal": len(out), "casesPassed": sum(c["arms"]["with"][0]["passed"] for c in out),
                                         "overallScore": 1.0}}


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metrics = load("metrics")

    def trace(self, events: list[dict]) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        self.addCleanup(Path(handle.name).unlink)
        with handle:
            handle.write("\n".join(json.dumps(event) for event in events))
        return Path(handle.name)

    def test_a_refused_call_and_a_refused_batch_slot_each_count_as_one_error(self) -> None:
        batch = {"results": [{"name": "registry__get_artifact", "isError": True, "error": "not found"},
                             {"name": "registry__list_workspaces", "isError": False}]}
        got = self.metrics.trace_metrics(self.trace([
            use("t1", f"{ATLAN}mcp__call_read_tool", {"name": "registry__get_artifact"}),
            answer("t1", {"status": 404, "code": "not_found", "detail": "no such artifact"}, is_error=True),
            use("t2", f"{ATLAN}mcp__call_read_tools", {"calls": [{"name": "registry__get_artifact"}, {"name": "registry__list_workspaces"}]}),
            answer("t2", batch),
        ]))
        self.assertEqual(got["gateway"], 2)
        self.assertEqual(got["gateway_errors"], 2)

    def test_a_successful_answer_that_holds_the_word_error_is_not_an_error(self) -> None:
        got = self.metrics.trace_metrics(self.trace([
            use("t1", f"{ATLAN}mcp__call_read_tool", {"name": "registry__get_file_content"}),
            answer("t1", {"content": 'the runbook says: on "error" page the owner'}),
        ]))
        self.assertEqual(got["gateway_errors"], 0)

    def test_a_case_past_its_budget_is_named_with_the_measure_and_both_numbers(self) -> None:
        budgets = {"find-existing-skill": {"gateway": 2, "turns": 5, "cost_usd": 0.20}}
        rows = [
            {"case": "find-existing-skill", "arm": "with", "gateway": 9, "turns": 6, "cost_usd": 0.21},
            {"case": "unlisted-case", "arm": "with", "gateway": 40, "turns": 40, "cost_usd": 4.0},
            {"case": "find-existing-skill", "arm": "without", "gateway": 30, "turns": 30, "cost_usd": 3.0},
        ]
        self.assertEqual(self.metrics.over_budget(rows, budgets), ["find-existing-skill: gateway 9, budget 2"])

    def test_budgets_written_from_a_run_hold_each_case_at_its_costliest_run(self) -> None:
        rows = [{"case": "a", "arm": "with", "gateway": 2, "turns": 5, "cost_usd": 0.1},
                {"case": "a", "arm": "with", "gateway": 4, "turns": 4, "cost_usd": 0.3},
                {"case": "a", "arm": "without", "gateway": 9, "turns": 9, "cost_usd": 0.9}]
        self.assertEqual(self.metrics.budgets_from(rows), {"a": {"gateway": 4, "turns": 5, "cost_usd": 0.3}})


class RunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load("run")

    def test_a_case_filter_is_read_in_both_spellings_and_never_passed_on(self) -> None:
        extra = ["--runs", "1", "--case", "use-skill-*", "--case=share-*", "-j", "4"]
        self.assertEqual(self.runner.case_filters(extra), ["use-skill-*", "share-*"])
        self.assertEqual(self.runner.without_case_filters(extra), ["--runs", "1", "-j", "4"])

    def summary(self, groups: dict[str, dict | None]) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, data in groups.items():
                folder = Path(tmp) / name
                folder.mkdir()
                if data is not None:
                    (folder / "result.json").write_text(json.dumps(data), encoding="utf-8")
                paths[folder / "result.json"] = ["lost-case"] if data is None else [case["name"] for case in data["cases"]]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = self.runner.summarize(paths)
            return code, out.getvalue()

    def test_the_suite_line_counts_every_group_and_sets_tone_failures_apart(self) -> None:
        code, text = self.summary({
            "group-0": result({"clean": [("reads-the-head", True), ("reply-length", True)],
                               "wordy": [("reads-the-head", True), ("reply-length", False)]}),
            "group-1": result({"wrong": [("reads-the-head", False), ("reply-length", True)]}),
        })
        self.assertEqual(code, 0)
        self.assertIn("suite: 1/3 cases passed every grader, 2/3 passed every outcome grader", text)
        self.assertRegex(text, r"wordy\s+reply-length \[tone only\]")
        self.assertNotRegex(text, r"wrong\s+reads-the-head \[tone only\]")

    def test_a_group_that_left_no_result_is_named_and_fails_the_run(self) -> None:
        code, text = self.summary({"group-0": result({"clean": [("reads-the-head", True)]}), "group-1": None})
        self.assertEqual(code, 1)
        self.assertIn("no result for group-1: lost-case", text)
        self.assertIn("suite: 1/2 cases passed every grader", text)

    def test_a_filter_that_matches_no_case_leaves_no_results_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "cases").mkdir()
            with mock.patch.object(self.runner, "HERE", Path(tmp)), mock.patch("sys.argv", ["run.py", "--", "--case", "nothing-*"]), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.runner.main(), 2)
            self.assertFalse((Path(tmp) / "results").exists())

    def test_every_run_keeps_its_traces_so_its_cost_can_be_measured(self) -> None:
        self.assertIn("--keep-temp", self.runner.measured(["--runs", "1"]))
        self.assertEqual(self.runner.measured(["--keep-temp", "--runs", "1"]).count("--keep-temp"), 1)


class EmulatorIdentityTest(unittest.TestCase):
    def test_the_server_says_who_is_signed_in_so_a_run_does_not_take_the_host_account_for_the_caller(self) -> None:
        emulator = load("atlan_emulator")
        reply = emulator.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIn("signed in to Atlan as Dana", reply["result"]["instructions"])
        self.assertIn("user_dana", reply["result"]["instructions"])


if __name__ == "__main__":
    unittest.main()
