"""`cairn init` — seed a fresh project with the Cairn directory layout."""

from __future__ import annotations

import json
import shutil
from importlib import resources
from pathlib import Path

EMPTY_INDEX = {
    "version": "1",
    "updated_at": None,
    "snapshots": [],
    "capsules": [],
}

EMPTY_REGISTRY = {
    "version": "1",
    "capsules": [],
}

GITIGNORE_BLOCK = """
# Cairn runtime artefacts
snapshots/*.tmp
snapshots/*.bak
""".lstrip()

CLAUDE_MD_STUB = """\
# CLAUDE.md — {project}

**Protocol:** CAIRN_V1
**Project:** {project}
**Status:** fresh — no snapshots committed yet

---

## Project Overview

_Describe the project's current state here._

## Objective

_State the primary goal of this session._

## Unresolved Vectors

_List pending work._

## Risks

_Known risks (none yet)._

## Environment

_Declare required dependencies._

---

See the Cairn repo for the full protocol spec and rehydration algorithm.
"""


def init_project(
    target: Path | str,
    project: str,
    *,
    force: bool = False,
    write_claude_md: bool = False,
    write_projects_dir: bool = False,
    copy_bundled_projects: bool = False,
) -> dict:
    """Seed a Cairn layout under `target`. Returns a report dict.

    Parameters
    ----------
    copy_bundled_projects:
        If True, copy the bundled project fragments (coda, terra-siege,
        nyx-audio, lumina) into `<target>/schemas/projects/`. Implies
        write_projects_dir.
    """
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    for sub in ("snapshots", "capsules"):
        d = target / sub
        if not d.exists():
            d.mkdir(parents=True)
            created.append(f"{sub}/")

    if write_projects_dir or copy_bundled_projects:
        projects = target / "schemas" / "projects"
        if not projects.exists():
            projects.mkdir(parents=True)
            created.append("schemas/projects/")

    _seed(target / "snapshots" / "index.json", EMPTY_INDEX, force, created, skipped)
    _seed(target / "capsules" / "registry.json", EMPTY_REGISTRY, force, created, skipped)

    if copy_bundled_projects:
        bundled = Path(resources.files("cairn") / "schemas" / "projects")
        dest = target / "schemas" / "projects"
        for src in sorted(bundled.glob("*.json")):
            out = dest / src.name
            if out.exists() and not force:
                skipped.append(f"schemas/projects/{src.name} (exists)")
                continue
            shutil.copy(src, out)
            created.append(f"schemas/projects/{src.name}")

    if write_claude_md:
        claude_md = target / "CLAUDE.md"
        if claude_md.exists() and not force:
            skipped.append("CLAUDE.md (exists)")
        else:
            claude_md.write_text(CLAUDE_MD_STUB.format(project=project), encoding="utf-8")
            created.append("CLAUDE.md")

    gi = target / ".gitignore"
    if gi.exists():
        existing = gi.read_text(encoding="utf-8")
        if "# Cairn runtime artefacts" in existing:
            skipped.append(".gitignore (already has Cairn block)")
        else:
            sep = "" if existing.endswith("\n") else "\n"
            gi.write_text(existing + sep + "\n" + GITIGNORE_BLOCK, encoding="utf-8")
            created.append(".gitignore (appended Cairn block)")
    else:
        gi.write_text(GITIGNORE_BLOCK, encoding="utf-8")
        created.append(".gitignore")

    return {
        "target": str(target),
        "project": project,
        "created": created,
        "skipped": skipped,
    }


def _seed(path: Path, content: dict, force: bool, created: list[str], skipped: list[str]) -> None:
    rel = f"{path.parent.name}/{path.name}" if path.parent.name else path.name
    if path.exists() and not force:
        skipped.append(f"{rel} (exists)")
        return
    with path.open("w", encoding="utf-8") as f:
        json.dump(content, f, indent=2, sort_keys=True)
        f.write("\n")
    created.append(rel)
