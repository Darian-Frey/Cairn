"""Tests for tools/cairn_scanner.py — covers priority cases 1, 2, 5 from CLAUDE.md §8."""

from __future__ import annotations

import json

import pytest

from tools.cairn_scanner import (
    CairnScanner,
    CapsuleError,
    compute_st_h,
)
from tests.conftest import OLD_CODE_FIXTURES


# ---------- compute_st_h ----------

class TestComputeStH:
    def test_is_deterministic(self, minimal_snapshot):
        h1 = compute_st_h(minimal_snapshot)
        h2 = compute_st_h(minimal_snapshot)
        assert h1 == h2

    def test_excludes_st_h_field(self, minimal_snapshot):
        without = {k: v for k, v in minimal_snapshot.items() if k != "ST_H"}
        assert compute_st_h(minimal_snapshot) == compute_st_h(without)

    def test_16_uppercase_hex(self, minimal_snapshot):
        h = compute_st_h(minimal_snapshot)
        assert len(h) == 16
        assert h == h.upper()
        assert all(c in "0123456789ABCDEF" for c in h)

    def test_changes_when_content_changes(self, minimal_snapshot, clone_snapshot):
        mutated = clone_snapshot(minimal_snapshot, OBJ="different objective")
        assert mutated["ST_H"] != minimal_snapshot["ST_H"]


# ---------- validate_schema ----------

class TestValidateSchema:
    def test_minimal_snapshot_valid(self, minimal_snapshot):
        assert CairnScanner().validate_schema(minimal_snapshot) is True

    def test_missing_required_field_rejected(self, minimal_snapshot):
        del minimal_snapshot["project"]
        scanner = CairnScanner()
        assert scanner.validate_schema(minimal_snapshot) is False
        assert any("project" in e for e in scanner.schema_errors(minimal_snapshot))

    def test_bad_priority_rejected(self, minimal_snapshot):
        minimal_snapshot["UV"][0]["tasks"][0]["priority"] = "p9"
        assert CairnScanner().validate_schema(minimal_snapshot) is False

    def test_bad_rsk_level_rejected(self, minimal_snapshot):
        minimal_snapshot["RSK"][0]["level"] = "catastrophic"
        assert CairnScanner().validate_schema(minimal_snapshot) is False

    def test_pct_out_of_range_rejected(self, minimal_snapshot):
        minimal_snapshot["PAY"]["pct"] = 150
        assert CairnScanner().validate_schema(minimal_snapshot) is False

    def test_capsule_without_id_rejected(self, minimal_snapshot):
        minimal_snapshot["capsule"] = True
        assert CairnScanner().validate_schema(minimal_snapshot) is False

    def test_st_h_pattern_enforced(self, minimal_snapshot):
        minimal_snapshot["ST_H"] = "not-a-valid-hash"
        assert CairnScanner().validate_schema(minimal_snapshot) is False


# ---------- verify_integrity ----------

class TestVerifyIntegrity:
    def test_fresh_snapshot_passes(self, minimal_snapshot):
        assert CairnScanner().verify_integrity(minimal_snapshot) is True

    def test_tampered_content_fails(self, minimal_snapshot):
        minimal_snapshot["OBJ"] = "tampered"
        assert CairnScanner().verify_integrity(minimal_snapshot) is False

    def test_tampered_st_h_fails(self, minimal_snapshot):
        minimal_snapshot["ST_H"] = "0000000000000000"
        assert CairnScanner().verify_integrity(minimal_snapshot) is False

    def test_missing_st_h_fails(self, minimal_snapshot):
        del minimal_snapshot["ST_H"]
        assert CairnScanner().verify_integrity(minimal_snapshot) is False


# ---------- detect_orphans ----------

class TestDetectOrphans:
    def test_clean_snapshot_has_none(self, minimal_snapshot):
        assert CairnScanner().detect_orphans(minimal_snapshot) == []

    def test_missing_dep_flagged(self, minimal_snapshot):
        minimal_snapshot["UV"][0]["tasks"][0]["dep_refs"].append("dep_ghost")
        orphans = CairnScanner().detect_orphans(minimal_snapshot)
        assert len(orphans) == 1
        assert orphans[0]["d_task"] == "DT-01"
        assert orphans[0]["task_id"] == "uv_schema_validation"
        assert orphans[0]["missing_deps"] == ["dep_ghost"]

    def test_multiple_tasks_with_missing_deps(self, minimal_snapshot):
        minimal_snapshot["UV"][0]["tasks"][0]["dep_refs"] = ["dep_missing_a"]
        minimal_snapshot["UV"][0]["tasks"][1]["dep_refs"] = ["dep_missing_b"]
        orphans = CairnScanner().detect_orphans(minimal_snapshot)
        assert len(orphans) == 2

    def test_task_without_dep_refs_is_fine(self, minimal_snapshot):
        # Second task in the fixture has no dep_refs — shouldn't appear in orphans.
        assert all(
            o["task_id"] != "uv_integrity_check"
            for o in CairnScanner().detect_orphans(minimal_snapshot)
        )


# ---------- detect_circular_deps ----------

class TestCircularDeps:
    def test_acyclic_graph(self, minimal_snapshot):
        minimal_snapshot["DEP"].append(
            {"id": "dep_a", "comp": "a", "ver": "1", "role": "lib", "requires": ["dep_python"]}
        )
        assert CairnScanner().detect_circular_deps(minimal_snapshot) == []

    def test_two_node_cycle(self, minimal_snapshot):
        minimal_snapshot["DEP"].extend([
            {"id": "dep_a", "comp": "a", "ver": "1", "role": "lib", "requires": ["dep_b"]},
            {"id": "dep_b", "comp": "b", "ver": "1", "role": "lib", "requires": ["dep_a"]},
        ])
        cycles = CairnScanner().detect_circular_deps(minimal_snapshot)
        assert len(cycles) == 1
        assert set(cycles[0][:-1]) == {"dep_a", "dep_b"}

    def test_three_node_cycle(self, minimal_snapshot):
        minimal_snapshot["DEP"].extend([
            {"id": "dep_a", "comp": "a", "ver": "1", "role": "lib", "requires": ["dep_b"]},
            {"id": "dep_b", "comp": "b", "ver": "1", "role": "lib", "requires": ["dep_c"]},
            {"id": "dep_c", "comp": "c", "ver": "1", "role": "lib", "requires": ["dep_a"]},
        ])
        cycles = CairnScanner().detect_circular_deps(minimal_snapshot)
        assert len(cycles) == 1
        assert set(cycles[0][:-1]) == {"dep_a", "dep_b", "dep_c"}


# ---------- estimate_token_cost ----------

class TestTokenCost:
    def test_returns_positive_int(self, minimal_snapshot):
        cost = CairnScanner().estimate_token_cost(minimal_snapshot)
        assert isinstance(cost, int) and cost > 0

    def test_larger_snapshot_costs_more(self, minimal_snapshot, clone_snapshot):
        bigger = clone_snapshot(minimal_snapshot, CTX="x" * 1000)
        scanner = CairnScanner()
        assert scanner.estimate_token_cost(bigger) > scanner.estimate_token_cost(minimal_snapshot)


# ---------- audit_risks ----------

class TestAuditRisks:
    def test_groups_by_severity(self, minimal_snapshot):
        minimal_snapshot["RSK"] = [
            {"id": "r1", "level": "critical", "desc": "x", "blocking": True},
            {"id": "r2", "level": "high", "desc": "x", "blocking": False},
            {"id": "r3", "level": "high", "desc": "x", "blocking": False},
            {"id": "r4", "level": "info", "desc": "x", "blocking": False},
        ]
        grouped = CairnScanner().audit_risks(minimal_snapshot)
        assert len(grouped["critical"]) == 1
        assert len(grouped["high"]) == 2
        assert len(grouped["medium"]) == 0
        assert len(grouped["info"]) == 1

    def test_empty_risks(self, minimal_snapshot):
        minimal_snapshot["RSK"] = []
        grouped = CairnScanner().audit_risks(minimal_snapshot)
        assert all(v == [] for v in grouped.values())
        assert set(grouped.keys()) == {"critical", "high", "medium", "info"}


# ---------- certify_capsule ----------

class TestCertifyCapsule:
    def test_writes_file_and_registry(self, minimal_snapshot, tmp_capsule_env):
        caps_dir, registry = tmp_capsule_env
        scanner = CairnScanner()
        record = scanner.certify_capsule(minimal_snapshot, "CAP-001")

        capsule_path = caps_dir / "CAP-001.json"
        assert capsule_path.exists()
        sealed = json.loads(capsule_path.read_text())
        assert sealed["capsule"] is True
        assert sealed["capsule_id"] == "CAP-001"
        assert sealed["ST_H"] == compute_st_h(sealed)

        assert record["capsule_id"] == "CAP-001"
        assert record["certified"] is True
        assert record["project"] == "cairn"

        reg = json.loads(registry.read_text())
        assert len(reg["capsules"]) == 1
        assert reg["capsules"][0]["capsule_id"] == "CAP-001"

    def test_rejects_duplicate(self, minimal_snapshot, tmp_capsule_env):
        scanner = CairnScanner()
        scanner.certify_capsule(minimal_snapshot, "CAP-001")
        with pytest.raises(CapsuleError, match="already exists"):
            scanner.certify_capsule(minimal_snapshot, "CAP-001")

    def test_rejects_invalid_schema(self, minimal_snapshot, tmp_capsule_env):
        del minimal_snapshot["OBJ"]
        with pytest.raises(CapsuleError, match="schema validation"):
            CairnScanner().certify_capsule(minimal_snapshot, "CAP-002")

    def test_rejects_integrity_failure(self, minimal_snapshot, tmp_capsule_env):
        minimal_snapshot["ST_H"] = "0000000000000000"
        with pytest.raises(CapsuleError, match="ST_H"):
            CairnScanner().certify_capsule(minimal_snapshot, "CAP-003")

    def test_appends_to_existing_registry(self, minimal_snapshot, clone_snapshot, tmp_capsule_env):
        _, registry = tmp_capsule_env
        scanner = CairnScanner()
        scanner.certify_capsule(minimal_snapshot, "CAP-001")
        second = clone_snapshot(minimal_snapshot, OBJ="different phase")
        scanner.certify_capsule(second, "CAP-002")
        reg = json.loads(registry.read_text())
        assert [c["capsule_id"] for c in reg["capsules"]] == ["CAP-001", "CAP-002"]


# ---------- validate_index ----------

class TestValidateIndex:
    def test_reports_on_disk_missing_from_index(self, tmp_path, minimal_snapshot):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "loose.json").write_text(json.dumps(minimal_snapshot))
        index = {"version": "1", "snapshots": [], "capsules": []}
        issues = CairnScanner().validate_index(index, snap_dir, root=tmp_path)
        assert any("not in index" in i for i in issues)

    def test_reports_indexed_missing_on_disk(self, tmp_path):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        index = {
            "version": "1",
            "snapshots": [
                {"file": "snapshots/ghost.json", "ST_H": "ABCDEF0123456789"}
            ],
            "capsules": [],
        }
        issues = CairnScanner().validate_index(index, snap_dir, root=tmp_path)
        assert any("missing on disk" in i for i in issues)

    def test_clean_index_returns_empty(self, tmp_path, minimal_snapshot):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        snap_file = snap_dir / "s1.json"
        snap_file.write_text(json.dumps(minimal_snapshot))
        index = {
            "version": "1",
            "snapshots": [
                {"file": "snapshots/s1.json", "ST_H": minimal_snapshot["ST_H"]}
            ],
            "capsules": [],
        }
        assert CairnScanner().validate_index(index, snap_dir, root=tmp_path) == []


# ---------- old_code fixtures (skipped until dropped in) ----------

OLD_FIXTURES = [
    "chaos_test_v1.json",
    "Diff_Test_20260218_2136.json",
    "Diff_Test_20260218_2137.json",
    "Diff_Test_20260218_2138.json",
]


@pytest.mark.parametrize("name", OLD_FIXTURES)
def test_old_code_fixture_loads(name):
    """Sanity: old_code fixtures parse as JSON once dropped in.

    These fixtures predate CAIRN_V1, so they are NOT expected to validate
    against the current schema — the test suite for §8 case #1 will need
    either fixture migration or a legacy-schema shim. For now, just confirm
    the file is syntactically JSON when present.
    """
    path = OLD_CODE_FIXTURES / name
    if not path.exists():
        pytest.skip(f"{name} not yet present in old_code/snapshots/")
    with path.open("r", encoding="utf-8") as f:
        json.load(f)
