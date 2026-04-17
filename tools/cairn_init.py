"""`cairn init` — seed a fresh project with the Cairn directory layout.

Creates snapshots/, capsules/, schemas/projects/ (if requested), a seeded
snapshots/index.json and capsules/registry.json, an optional CLAUDE.md stub,
and a .gitignore fragment for Cairn runtime artefacts.

Idempotent: safe to run in a directory that already has some of the layout —
existing files are never overwritten without --force.
"""

from __future__ import annotations

import json
import sys
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
    target: Path,
    project: str,
    *,
    force: bool = False,
    write_claude_md: bool = False,
    write_projects_dir: bool = False,
) -> dict:
    """Seed a Cairn layout under `target`. Returns a report dict."""
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    # Directories
    for sub in ("snapshots", "capsules"):
        d = target / sub
        if not d.exists():
            d.mkdir(parents=True)
            created.append(f"{sub}/")
    if write_projects_dir:
        d = target / "schemas" / "projects"
        if not d.exists():
            d.mkdir(parents=True)
            created.append("schemas/projects/")

    # Seeded files
    _seed(target / "snapshots" / "index.json", EMPTY_INDEX, force, created, skipped)
    _seed(target / "capsules" / "registry.json", EMPTY_REGISTRY, force, created, skipped)

    # Optional CLAUDE.md stub
    if write_claude_md:
        claude_md = target / "CLAUDE.md"
        if claude_md.exists() and not force:
            skipped.append("CLAUDE.md (exists)")
        else:
            claude_md.write_text(CLAUDE_MD_STUB.format(project=project), encoding="utf-8")
            created.append("CLAUDE.md")

    # .gitignore fragment (append, don't overwrite)
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
    rel = path.name if path.parent.name == "" else f"{path.parent.name}/{path.name}"
    if path.exists() and not force:
        skipped.append(f"{rel} (exists)")
        return
    with path.open("w", encoding="utf-8") as f:
        json.dump(content, f, indent=2, sort_keys=True)
        f.write("\n")
    created.append(rel)


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Seed a Cairn layout in a target directory")
    parser.add_argument("project", help="Project name (e.g. 'coda', 'terra-siege')")
    parser.add_argument("--target", type=Path, default=Path("."),
                        help="Target directory (default: current dir)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing seeded files")
    parser.add_argument("--with-claude-md", action="store_true",
                        help="Also generate a CLAUDE.md stub")
    parser.add_argument("--with-projects-dir", action="store_true",
                        help="Also create schemas/projects/ (for project-extension fragments)")

    args = parser.parse_args()
    report = init_project(
        args.target,
        args.project,
        force=args.force,
        write_claude_md=args.with_claude_md,
        write_projects_dir=args.with_projects_dir,
    )

    print(f"[+] Cairn initialised at {report['target']}")
    print(f"    project: {report['project']}")
    if report["created"]:
        print("    created:")
        for c in report["created"]:
            print(f"      + {c}")
    if report["skipped"]:
        print("    skipped:")
        for s in report["skipped"]:
            print(f"      - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
