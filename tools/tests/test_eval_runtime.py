"""The eval engine's environment: one pinned, hash-checked package, set up once per project."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "plugins/atlanai-analytics/skills/eval-generation"
sys.path.insert(0, str(SKILL / "scripts"))
import setup_runtime  # noqa: E402


class EvalRuntimeTest(unittest.TestCase):
    def test_the_lock_pins_one_exact_wheel_by_hash(self) -> None:
        # The directory's validator flags a skill that downloads a package and runs it straight away
        # (`uv run --with`); the engine's one package is installed ahead of time, exact and hash-checked.
        text = setup_runtime.LOCK.read_text()
        self.assertEqual(re.findall(r"^([A-Za-z0-9_.-]+)==([0-9.]+)", text, re.M), [("sqlglot", "30.20.0")])
        self.assertEqual(len(re.findall(r"--hash=sha256:[0-9a-f]{64}", text)), 1)
        pinned = re.search(r'"sqlglot==([0-9.]+)"', (SKILL / "modules/evals/engine/pyproject.toml").read_text())
        self.assertEqual(pinned.group(1), "30.20.0", "the engine's pyproject and the lock name the same release")

    def test_the_skill_runs_the_engine_on_its_own_environment_never_a_launcher(self) -> None:
        skill = (SKILL / "SKILL.md").read_text()
        self.assertNotIn("uv run", skill)
        self.assertIn('scripts/setup_runtime.py" --runtime "<runtime>"', skill)

    def test_install_is_exact_hash_checked_and_reused(self) -> None:
        root = Path(tempfile.mkdtemp()) / "runtime"
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def create(self, env_dir):
            python = setup_runtime.interpreter(Path(env_dir).parent)
            python.parent.mkdir(parents=True)
            python.write_text("")

        with mock.patch.object(setup_runtime.subprocess, "run", side_effect=run), \
                mock.patch.object(setup_runtime.venv.EnvBuilder, "create", create):
            first = setup_runtime.setup(root)
            again = setup_runtime.setup(root)
        self.assertEqual((first["status"], first["reused"], again["reused"]), ("ready", False, True))
        self.assertEqual(len(calls), 1, "a second call with the same lock installs nothing")
        for flag in ("--require-hashes", "--only-binary", "--no-deps"):
            self.assertIn(flag, calls[0])
        self.assertEqual(calls[0][calls[0].index("-r") + 1], str(setup_runtime.LOCK))

    def test_a_failed_install_says_what_to_check_and_leaves_no_receipt(self) -> None:
        root = Path(tempfile.mkdtemp()) / "runtime"

        def create(self, env_dir):
            python = setup_runtime.interpreter(Path(env_dir).parent)
            python.parent.mkdir(parents=True)
            python.write_text("")

        failed = subprocess.CompletedProcess([], 1, "", "ERROR: Could not fetch sqlglot")
        with mock.patch.object(setup_runtime.subprocess, "run", return_value=failed), \
                mock.patch.object(setup_runtime.venv.EnvBuilder, "create", create):
            result = setup_runtime.setup(root)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("package index", result["message"])
        self.assertFalse((root / "runtime.json").exists())


if __name__ == "__main__":
    unittest.main()
