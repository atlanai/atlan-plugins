#!/usr/bin/env python3
"""Explicit one-time installation of the pinned PBIXRay runtime. Never called by MCP."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

root=Path(__file__).resolve().parents[1]
runtime=Path.home()/'.trident/runtimes/pbixray-0.15.5'
uv=shutil.which('uv')
if not uv: raise SystemExit('uv is required for explicit setup; no tool was installed automatically.')
if runtime.is_symlink(): raise SystemExit('Runtime must be an ordinary local directory.')
if not (runtime/'bin/python').is_file():
    subprocess.run([uv,'venv',str(runtime),'--python','3.12','--no-python-downloads'],check=True)
lock=root/'dependencies/pbixray.lock'
subprocess.run([uv,'pip','sync',str(lock),'--python',str(runtime/'bin/python'),
                '--require-hashes','--only-binary',':all:'],check=True)
(runtime/'trident-runtime.json').write_text(json.dumps({'pbixray':'0.15.5','lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}))
print(json.dumps({'runtime':str(runtime),'pbixray':'0.15.5','ready':True}))
