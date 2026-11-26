#!/usr/bin/env python3
"""setup_runtime.py: the eval engine's own Python environment, set up once per project.

    python3 setup_runtime.py --runtime <dir>

Builds a virtual environment in <dir> with the standard library's venv, then installs the one
package in ../dependencies/evals.lock with pip: the exact wheel, checked against its hash, and
nothing else (`--require-hashes --only-binary :all: --no-deps`). A later call with the same lock
reuses it. Prints one JSON object, `python` being the interpreter that runs the eval engine.
Exit 2 means something the person must supply.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import venv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "analytics-build" / "scripts"))
import python_version  # noqa: E402

python_version.require()

LOCK = pathlib.Path(__file__).resolve().parents[1] / "dependencies" / "evals.lock"


def interpreter(root: pathlib.Path) -> pathlib.Path:
    return root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def setup(root: pathlib.Path) -> dict:
    lock = hashlib.sha256(LOCK.read_bytes()).hexdigest()
    receipt = root / "runtime.json"
    python = interpreter(root)
    if python.is_file() and receipt.is_file() and json.loads(receipt.read_text()).get("lock_sha256") == lock:
        return {"status": "ready", "python": str(python), "reused": True}
    if root.is_symlink():
        return {"status": "blocked", "message": "The eval tools' folder must be an ordinary folder, not a link."}
    root.mkdir(parents=True, exist_ok=True)
    if not python.is_file():
        venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(root / "venv")
    install = subprocess.run([str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
                              "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(LOCK)],
                             capture_output=True, text=True)
    if install.returncode:
        return {"status": "blocked", "message": "The eval tools couldn't be installed: pip couldn't fetch the pinned "
                "package from your package index. Check this machine can reach it, then ask again.",
                "detail": install.stderr.strip().splitlines()[-1:]}
    receipt.write_text(json.dumps({"lock_sha256": lock}))
    return {"status": "ready", "python": str(python), "reused": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", required=True)
    result = setup(pathlib.Path(parser.parse_args().runtime).expanduser().resolve())
    print(json.dumps(result))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    sys.exit(main())
