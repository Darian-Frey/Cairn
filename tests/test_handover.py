"""§8 case #8 — multi-agent handover simulation.

Models two agents working sequentially on a shared snapshot ledger:
  1. Agent A loads the baseline, commits to the repo
  2. Agent B loads A's state, modifies UV (completes tasks, adds new ones),
     commits with parent_ST_H pointing at A's ST_H
  3. Verify chain integrity, diff correctness, index consistency

Pattern adapted from old_code/schema_v5/test/multi_agent_handover_sim.py,
but reframed around the CAIRN_V1 diff chain rather than SCHEMA_V5's .s5h packets.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from cairn.client import CairnClient
from cairn.scanner import CairnScanner, compute_st_h


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def shared_repo(tmp_path):
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "capsules").mkdir()
    (tmp_path / "snapshots" / "index.json").write_text(
        json.dumps({"version": "1", "updated_at": None, "snapshots": [], "capsules": []})
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _rehash(snap: dict) -> dict:
    snap["ST_H"] = compute_st_h(snap)
    return snap


def _load_latest_from_index(repo: Path) -> dict:
    """Agent handover step: load the newest snapshot via the index."""
    idx = json.loads((repo / "snapshots" / "index.json").read_text())
    assert idx["snapshots"], "index has no snapshots"
    latest_entry = idx["snapshots"][-1]
    path = repo / latest_entry["file"]
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class TestMultiAgentHandover:
    def test_full_handover_chain(self, shared_repo):
        """Two-agent sequence: A commits baseline, B picks it up, modifies, commits."""
        # --- Agent A ---
        agent_a = CairnClient(repo_path=shared_repo, scanner=CairnScanner())
        baseline = _load_fixture("diff_baseline.json")
        result_a = agent_a.commit_snapshot(baseline, push=False, tags=["agent-a"])
        assert result_a["ok"]

        # --- Handover: Agent B loads A's latest state from the index ---
        agent_b = CairnClient(repo_path=shared_repo, scanner=CairnScanner())
        inherited = _load_latest_from_index(shared_repo)
        assert inherited["ST_H"] == baseline["ST_H"]

        # Agent B modifies UV: completes the index-wiring batch, starts client batch
        handed_over = copy.deepcopy(inherited)
        handed_over["parent_ST_H"] = inherited["ST_H"]
        handed_over["OBJ"] = "Begin cairn_client.py evolution"
        handed_over["PAY"]["phase"] = "Foundation — client scaffolding"
        handed_over["PAY"]["pct"] = 50
        handed_over["UV"] = [
            {
                "d_task": "DT-05",
                "desc": "Client foundation",
                "blocking": True,
                "tasks": [
                    {"id": "uv_client_diff", "obj": "Port get_diff()", "priority": "p1"},
                    {"id": "uv_client_commit", "obj": "Port commit_snapshot()", "priority": "p1"},
                ],
            }
        ]
        _rehash(handed_over)

        # Seed agent_b's last_state so the diff it computes is against what it inherited
        agent_b.last_state = inherited
        result_b = agent_b.commit_snapshot(handed_over, push=False, tags=["agent-b"])
        assert result_b["ok"]

        diff_b = result_b["diff"]
        assert diff_b.parent_st_h_link is True
        assert set(diff_b.completed_tasks) == {"uv_seed_index", "uv_update_index"}
        assert set(diff_b.started_tasks) == {"uv_client_diff", "uv_client_commit"}
        assert diff_b.pct_from == 35 and diff_b.pct_to == 50
        assert diff_b.phase_from == "Foundation — index wiring"
        assert diff_b.phase_to == "Foundation — client scaffolding"

        # --- Chain integrity assertions ---
        idx = json.loads((shared_repo / "snapshots" / "index.json").read_text())
        assert len(idx["snapshots"]) == 2
        first, second = idx["snapshots"]
        assert first["ST_H"] == baseline["ST_H"]
        assert first["tags"] == ["agent-a"]
        assert second["ST_H"] == handed_over["ST_H"]
        assert second["parent_ST_H"] == baseline["ST_H"]
        assert second["tags"] == ["agent-b"]

        # Index must reference real files with matching ST_H
        scanner = CairnScanner()
        issues = scanner.validate_index(idx, shared_repo / "snapshots", root=shared_repo)
        assert issues == []

    def test_handover_preserves_diff_chain_across_three_agents(self, shared_repo):
        """Three sequential handovers — each link must point to the prior ST_H."""
        scanner = CairnScanner()
        clients = [CairnClient(repo_path=shared_repo, scanner=scanner) for _ in range(3)]

        snap = _load_fixture("diff_baseline.json")
        chain = [snap["ST_H"]]
        for i, agent in enumerate(clients):
            if i > 0:
                inherited = _load_latest_from_index(shared_repo)
                snap = copy.deepcopy(inherited)
                snap["parent_ST_H"] = inherited["ST_H"]
                snap["OBJ"] = f"step-{i}"
                snap["PAY"]["pct"] = 35 + i * 5
                _rehash(snap)
                agent.last_state = inherited
                chain.append(snap["ST_H"])
            agent.commit_snapshot(snap, push=False, tags=[f"agent-{i}"])

        idx = json.loads((shared_repo / "snapshots" / "index.json").read_text())
        assert len(idx["snapshots"]) == 3
        assert [e["ST_H"] for e in idx["snapshots"]] == chain

        # Each entry after the first must link to its predecessor
        for prev, curr in zip(idx["snapshots"], idx["snapshots"][1:]):
            assert curr["parent_ST_H"] == prev["ST_H"]

        # Scanner integrity pass on every stored snapshot
        for entry in idx["snapshots"]:
            with (shared_repo / entry["file"]).open("r", encoding="utf-8") as f:
                stored = json.load(f)
            assert scanner.verify_integrity(stored)

    def test_handover_fork_is_detectable(self, shared_repo):
        """Two agents working off the same parent create a fork — chain parents diverge."""
        scanner = CairnScanner()
        agent_root = CairnClient(repo_path=shared_repo, scanner=scanner)
        baseline = _load_fixture("diff_baseline.json")
        agent_root.commit_snapshot(baseline, push=False, tags=["root"])

        def _fork_from(parent: dict, obj: str) -> dict:
            child = copy.deepcopy(parent)
            child["parent_ST_H"] = parent["ST_H"]
            child["OBJ"] = obj
            _rehash(child)
            return child

        fork_a = _fork_from(baseline, "fork-A")
        fork_b = _fork_from(baseline, "fork-B")

        CairnClient(repo_path=shared_repo, scanner=scanner).commit_snapshot(
            fork_a, push=False, tags=["fork-a"]
        )
        CairnClient(repo_path=shared_repo, scanner=scanner).commit_snapshot(
            fork_b, push=False, tags=["fork-b"]
        )

        idx = json.loads((shared_repo / "snapshots" / "index.json").read_text())
        siblings = [e for e in idx["snapshots"] if e.get("parent_ST_H") == baseline["ST_H"]]
        assert len(siblings) == 2
        assert siblings[0]["ST_H"] != siblings[1]["ST_H"]
