"""Shared fixtures for the Cairn test suite."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.cairn_scanner import compute_st_h  # noqa: E402

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
def tmp_capsule_env(tmp_path, monkeypatch):
    """Redirect capsule + index writes into a temp dir for isolation."""
    import tools.cairn_scanner as scanner_mod

    caps_dir = tmp_path / "capsules"
    registry = caps_dir / "registry.json"
    snaps_dir = tmp_path / "snapshots"
    index = snaps_dir / "index.json"
    snaps_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(scanner_mod, "CAPSULES_DIR", caps_dir)
    monkeypatch.setattr(scanner_mod, "CAPSULE_REGISTRY", registry)
    monkeypatch.setattr(scanner_mod, "SNAPSHOTS_DIR", snaps_dir)
    monkeypatch.setattr(scanner_mod, "SNAPSHOT_INDEX", index)
    monkeypatch.setattr(scanner_mod, "REPO_ROOT", tmp_path)
    return caps_dir, registry, snaps_dir, index
