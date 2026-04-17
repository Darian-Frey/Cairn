"""Tests for src/cairn_client.py — §8 cases #3, #4, #6, #7."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from cairn.client import CairnClient, CommitError
from cairn.scanner import compute_st_h


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Bare Cairn repo shell — snapshots/ and capsules/ dirs, seeded index."""
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "capsules").mkdir()
    (tmp_path / "snapshots" / "index.json").write_text(
        json.dumps({"version": "1", "updated_at": None, "snapshots": [], "capsules": []})
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def client(repo):
    return CairnClient(repo_path=repo)


# ---------- get_diff / §8 case #3 ----------

class TestDiff:
    def test_first_snapshot(self, client):
        snap = _load_fixture("diff_baseline.json")
        report = client.get_diff(snap)
        assert report.first_snapshot is True
        assert "first snapshot" in report.summary()

    def test_phase_change_and_progress(self, client):
        base = _load_fixture("diff_baseline.json")
        child = _load_fixture("diff_phase_change.json")
        report = client.get_diff(child, reference=base)
        assert not report.empty
        assert report.pct_from == 35
        assert report.pct_to == 45
        assert report.phase_from == "Foundation — index wiring"
        assert report.phase_to == "Foundation — client scaffolding"
        assert report.parent_st_h_link is True

    def test_completed_and_started_tasks(self, client):
        base = _load_fixture("diff_baseline.json")
        child = _load_fixture("diff_phase_change.json")
        report = client.get_diff(child, reference=base)
        # baseline had uv_seed_index, uv_update_index; child replaces them with three new ones
        assert set(report.completed_tasks) == {"uv_seed_index", "uv_update_index"}
        assert set(report.started_tasks) == {
            "uv_client_diff", "uv_client_commit", "uv_client_prune"
        }

    def test_no_op_diff(self, client):
        a = _load_fixture("diff_phase_change.json")
        b = _load_fixture("diff_no_change.json")
        report = client.get_diff(b, reference=a)
        assert report.empty
        assert report.summary() == "no semantic changes detected"

    def test_new_and_resolved_risks(self, client):
        base = _load_fixture("diff_baseline.json")
        child = copy.deepcopy(base)
        child["RSK"] = [
            {"id": "r_new", "level": "high", "desc": "new", "blocking": False},
        ]
        child["ST_H"] = compute_st_h(child)
        report = client.get_diff(child, reference=base)
        assert report.new_risks == ["r_new"]
        assert report.resolved_risks == ["r_no_fixtures"]


# ---------- critical-RSK blocks commit / §8 case #4 ----------

class TestCriticalRiskBlock:
    def test_blocks_without_force(self, client):
        snap = _load_fixture("chaos_critical_rsk.json")
        with pytest.raises(CommitError, match="critical risk"):
            client.commit_snapshot(snap, push=False)

    def test_allows_with_force(self, client, repo):
        snap = _load_fixture("chaos_critical_rsk.json")
        result = client.commit_snapshot(snap, push=False, force=True)
        assert result["ok"] is True
        assert len(result["blocking_risks"]) == 1
        assert Path(result["filepath"]).exists()

    def test_non_blocking_critical_passes(self, client):
        snap = _load_fixture("chaos_critical_rsk.json")
        snap["RSK"][0]["blocking"] = False
        snap["ST_H"] = compute_st_h(snap)
        result = client.commit_snapshot(snap, push=False)
        assert result["ok"] is True


# ---------- commit pipeline validation ----------

class TestCommitValidation:
    def test_rejects_invalid_schema(self, client):
        snap = _load_fixture("diff_baseline.json")
        del snap["OBJ"]
        snap["ST_H"] = compute_st_h(snap)
        with pytest.raises(CommitError, match="schema"):
            client.commit_snapshot(snap, push=False)

    def test_rejects_bad_integrity(self, client):
        snap = _load_fixture("diff_baseline.json")
        snap["ST_H"] = "0000000000000000"
        with pytest.raises(CommitError, match="ST_H"):
            client.commit_snapshot(snap, push=False)

    def test_dry_run_does_not_write(self, client, repo):
        snap = _load_fixture("diff_baseline.json")
        result = client.commit_snapshot(snap, push=False, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert not Path(result["filepath"]).exists()
        idx = json.loads((repo / "snapshots" / "index.json").read_text())
        assert idx["snapshots"] == []


# ---------- index updated on commit / §8 case #7 ----------

class TestCommitUpdatesIndex:
    def test_commit_appends_to_index(self, client, repo):
        snap = _load_fixture("diff_baseline.json")
        client.commit_snapshot(snap, push=False)
        idx = json.loads((repo / "snapshots" / "index.json").read_text())
        assert len(idx["snapshots"]) == 1
        assert idx["snapshots"][0]["ST_H"] == snap["ST_H"]
        assert idx["snapshots"][0]["project"] == "cairn"
        assert idx["updated_at"] is not None

    def test_diff_chain_two_commits(self, client, repo):
        base = _load_fixture("diff_baseline.json")
        child = _load_fixture("diff_phase_change.json")
        client.commit_snapshot(base, push=False)
        client.commit_snapshot(child, push=False)
        idx = json.loads((repo / "snapshots" / "index.json").read_text())
        assert len(idx["snapshots"]) == 2
        # child's parent_ST_H should link to baseline
        child_entry = next(e for e in idx["snapshots"] if e["ST_H"] == child["ST_H"])
        assert child_entry["parent_ST_H"] == base["ST_H"]


# ---------- prune / §8 case #6 ----------

class TestPrune:
    def _make_snap(self, n: int) -> dict:
        snap = _load_fixture("diff_baseline.json")
        snap["OBJ"] = f"snap-{n}"
        snap["ST_H"] = compute_st_h(snap)
        return snap

    def test_keep_n_deletes_older(self, client, repo):
        import time
        for i in range(5):
            client.commit_snapshot(self._make_snap(i), push=False)
            time.sleep(0.01)  # ensure distinct mtimes
        deleted = client.prune(keep_n=2)
        assert len(deleted) == 3
        remaining = list((repo / "snapshots").glob("cairn_*.json"))
        assert len(remaining) == 2

    def test_prune_preserves_capsules(self, client, repo):
        """§8 case #6: capsules/ must never be touched by prune."""
        # Seed a capsule file that the client must not delete
        (repo / "capsules" / "CAP-001.json").write_text(json.dumps({"capsule": True}))
        # Also drop a stray capsule-flagged file into snapshots/ — defensive check
        stray = repo / "snapshots" / "stray_capsule.json"
        stray.write_text(json.dumps({"capsule": True, "project": "cairn"}))
        for i in range(3):
            client.commit_snapshot(self._make_snap(i), push=False)
        client.prune(keep_n=1)
        assert (repo / "capsules" / "CAP-001.json").exists()
        assert stray.exists()

    def test_prune_updates_index(self, client, repo):
        import time
        for i in range(3):
            client.commit_snapshot(self._make_snap(i), push=False)
            time.sleep(0.01)
        client.prune(keep_n=1)
        idx = json.loads((repo / "snapshots" / "index.json").read_text())
        remaining_files = {e["file"] for e in idx["snapshots"]}
        actual_files = {
            f"snapshots/{p.name}" for p in (repo / "snapshots").glob("cairn_*.json")
        }
        assert remaining_files == actual_files

    def test_dry_run_deletes_nothing(self, client, repo):
        import time
        for i in range(3):
            client.commit_snapshot(self._make_snap(i), push=False)
            time.sleep(0.01)
        deleted = client.prune(keep_n=1, dry_run=True)
        assert len(deleted) == 2
        remaining = list((repo / "snapshots").glob("cairn_*.json"))
        assert len(remaining) == 3

    def test_prune_zero_keeps_nothing(self, client, repo):
        client.commit_snapshot(self._make_snap(0), push=False)
        client.prune(keep_n=0)
        remaining = list((repo / "snapshots").glob("cairn_*.json"))
        assert remaining == []

    def test_prune_negative_raises(self, client):
        with pytest.raises(ValueError):
            client.prune(keep_n=-1)
