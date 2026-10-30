#!/usr/bin/env python3
"""Write the core plugin's skills as an Agent Skills discovery index for a website to serve.

    python3 tools/skill_index.py dist/site   # writes dist/site/.well-known/agent-skills/

`npx skills add https://<site> --skill <name>` reads the index, downloads the skill, and checks
its sha256 digest before writing anything. A skill that is one SKILL.md is served as that file;
one with references is a .tar.gz with SKILL.md at its root. The bytes depend only on the files,
so one commit always publishes the same digests.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import shutil
import sys
import tarfile
from pathlib import Path

from build import ROOT, core_dir, load, render
from lint import frontmatter

SCHEMA = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
PREFIX = Path(".well-known/agent-skills")


def skill_files(directory: Path) -> list[Path]:
    return sorted(p.relative_to(directory) for p in directory.rglob("*") if p.is_file())


def archive(directory: Path, files: list[Path]) -> bytes:
    """A gzipped tarball with fixed metadata, so its digest follows the content alone."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for relative in files:
            data = (directory / relative).read_bytes()
            info = tarfile.TarInfo(relative.as_posix())
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), compresslevel=9, mtime=0)


def skills_dir() -> Path:
    return ROOT / core_dir(load()) / "skills"


def payloads(skills: Path | None = None) -> tuple[dict, dict[str, bytes]]:
    """The index document, and every file it points at, keyed by its URL relative to the index."""
    entries, served = [], {}
    for directory in sorted(p for p in (skills or skills_dir()).iterdir() if p.is_dir()):
        parsed = frontmatter((directory / "SKILL.md").read_text(encoding="utf-8"))
        if parsed is None:
            raise SystemExit(f"{directory.name}/SKILL.md has no frontmatter")
        fields, _ = parsed
        files = skill_files(directory)
        if files == [Path("SKILL.md")]:
            kind, url, data = "skill-md", f"{fields['name']}/SKILL.md", (directory / "SKILL.md").read_bytes()
        else:
            kind, url, data = "archive", f"{fields['name']}.tar.gz", archive(directory, files)
        served[url] = data
        entries.append(
            {
                "name": fields["name"],
                "type": kind,
                "description": fields["description"],
                "url": url,
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
            }
        )
    return {"$schema": SCHEMA, "skills": entries}, served


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="the site root to write .well-known/ under")
    args = parser.parse_args(argv)

    target = args.out / PREFIX
    if target.exists():
        shutil.rmtree(target)
    index, served = payloads()
    for url, data in served.items():
        path = target / url
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (target / "index.json").write_text(render(index), encoding="utf-8")
    print(f"wrote {target / 'index.json'} with {len(index['skills'])} skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
