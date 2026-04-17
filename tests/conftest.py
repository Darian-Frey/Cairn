"""Shared fixtures for the Cairn test suite."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from cairn.scanner import compute_st_h

REPO_ROOT = Path(__file__).resolve().parent.parent
OLD_CODE_FIXTURES = REPO_ROOT / "old_code" / "ltm_bridge" / "snapshots"


@pytest.fixture
def minimal_snapshot() -> dict:
    snap = {
        "project": "cairn",
        "CTX": "minimal test snapshot",
        "OBJ": "exercise the scanner",
        "UV": [
            {
                "d_task": "DT-01",
                "desc": "scanner foundation",
                "blocking": True,
                "tasks": [
                    {
                        "id": "uv_schema_validation",
                        "obj": "Implement validate_schema()",
                        "priority": "p1",
                        "dep_refs": ["dep_python"],
                    },
                    {
                        "id": "uv_integrity_check",
                        "obj": "Implement verify_integrity()",
                        "priority": "p1",
                    },
                ],
            }
        ],
        "RSK": [
            {"id": "r1", "level": "info", "desc": "no material risks", "blocking": False},
        ],
        "DEP": [
            {"id": "dep_python", "comp": "python", "ver": "3.12", "role": "runtime"},
        ],
        "PAY": {"phase": "Foundation", "pct": 25},
    }
    snap["ST_H"] = compute_st_h(snap)
    return snap


@pytest.fixture
def clone_snapshot():
    def _clone(snap: dict, **overrides) -> dict:
        new = copy.deepcopy(snap)
        new.update(overrides)
        new["ST_H"] = compute_st_h(new)
        return new

    return _clone


@pytest.fixture
def tmp_capsule_env(tmp_path):
    """Construct a scanner pointed at a tmp repo — returns (scanner, tmp_path, capsules, registry, snaps_dir, index)."""
    from cairn.scanner import CairnScanner

    (tmp_path / "snapshots").mkdir()
    caps_dir = tmp_path / "capsules"
    registry = caps_dir / "registry.json"
    snaps_dir = tmp_path / "snapshots"
    index = snaps_dir / "index.json"
    scanner = CairnScanner(repo_path=tmp_path)
    return scanner, tmp_path, caps_dir, registry, snaps_dir, index
