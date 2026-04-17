"""Tests for tools/cairn_init.py — `cairn init` project seeder."""

from __future__ import annotations

import json

import pytest

from tools.cairn_init import init_project


class TestFreshInit:
    def test_creates_core_layout(self, tmp_path):
        report = init_project(tmp_path, "testproj")
        assert (tmp_path / "snapshots" / "index.json").exists()
        assert (tmp_path / "capsules" / "registry.json").exists()
        assert (tmp_path / ".gitignore").exists()
        assert report["project"] == "testproj"

    def test_seeded_index_is_empty(self, tmp_path):
        init_project(tmp_path, "testproj")
        idx = json.loads((tmp_path / "snapshots" / "index.json").read_text())
        assert idx == {
            "version": "1",
            "updated_at": None,
            "snapshots": [],
            "capsules": [],
        }

    def test_seeded_registry_is_empty(self, tmp_path):
        init_project(tmp_path, "testproj")
        reg = json.loads((tmp_path / "capsules" / "registry.json").read_text())
        assert reg == {"version": "1", "capsules": []}

    def test_gitignore_has_cairn_block(self, tmp_path):
        init_project(tmp_path, "testproj")
        content = (tmp_path / ".gitignore").read_text()
        assert "# Cairn runtime artefacts" in content
        assert "snapshots/*.tmp" in content


class TestOptionalFlags:
    def test_claude_md_not_written_by_default(self, tmp_path):
        init_project(tmp_path, "testproj")
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_claude_md_stub_when_requested(self, tmp_path):
        init_project(tmp_path, "terra-siege", write_claude_md=True)
        md = (tmp_path / "CLAUDE.md").read_text()
        assert "CAIRN_V1" in md
        assert "terra-siege" in md

    def test_projects_dir_not_written_by_default(self, tmp_path):
        init_project(tmp_path, "testproj")
        assert not (tmp_path / "schemas" / "projects").exists()

    def test_projects_dir_when_requested(self, tmp_path):
        init_project(tmp_path, "testproj", write_projects_dir=True)
        assert (tmp_path / "schemas" / "projects").is_dir()


class TestIdempotence:
    def test_existing_index_not_overwritten(self, tmp_path):
        (tmp_path / "snapshots").mkdir()
        idx_path = tmp_path / "snapshots" / "index.json"
        existing = {"version": "1", "snapshots": [{"file": "keep-me.json"}],
                    "capsules": [], "updated_at": "2026-01-01T00:00:00Z"}
        idx_path.write_text(json.dumps(existing))

        report = init_project(tmp_path, "testproj")
        reloaded = json.loads(idx_path.read_text())
        assert reloaded == existing
        assert any("index.json" in s for s in report["skipped"])

    def test_force_overwrites_index(self, tmp_path):
        (tmp_path / "snapshots").mkdir()
        idx_path = tmp_path / "snapshots" / "index.json"
        idx_path.write_text(json.dumps({"bogus": True}))

        init_project(tmp_path, "testproj", force=True)
        reloaded = json.loads(idx_path.read_text())
        assert "bogus" not in reloaded
        assert reloaded["snapshots"] == []

    def test_gitignore_not_duplicated_on_rerun(self, tmp_path):
        init_project(tmp_path, "testproj")
        init_project(tmp_path, "testproj")
        content = (tmp_path / ".gitignore").read_text()
        assert content.count("# Cairn runtime artefacts") == 1

    def test_existing_gitignore_gets_cairn_block_appended(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("__pycache__/\n*.pyc\n")
        init_project(tmp_path, "testproj")
        content = gi.read_text()
        assert "__pycache__/" in content
        assert "# Cairn runtime artefacts" in content

    def test_existing_claude_md_preserved(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Custom CLAUDE.md\nkeep me")
        report = init_project(tmp_path, "testproj", write_claude_md=True)
        assert "keep me" in claude_md.read_text()
        assert any("CLAUDE.md" in s for s in report["skipped"])

    def test_force_overwrites_claude_md(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("old content")
        init_project(tmp_path, "testproj", write_claude_md=True, force=True)
        content = claude_md.read_text()
        assert "old content" not in content
        assert "testproj" in content


class TestDeployable:
    """Confirm that after init, a fresh scanner+client can operate on the tree."""

    def test_scanner_and_client_work_on_init_tree(self, tmp_path):
        from src.cairn_client import CairnClient
        from tools.cairn_scanner import compute_st_h

        init_project(tmp_path, "testproj", write_projects_dir=True)

        # Author a minimal valid snapshot and commit it
        snap = {
            "project": "testproj",
            "CTX": "first snapshot after init",
            "OBJ": "test the init pipeline",
            "UV": [{
                "d_task": "DT-01", "desc": "x", "blocking": False,
                "tasks": [{"id": "t1", "obj": "do", "priority": "p1"}],
            }],
            "RSK": [], "DEP": [],
            "PAY": {"phase": "init-test", "pct": 0},
        }
        snap["ST_H"] = compute_st_h(snap)
        client = CairnClient(repo_path=tmp_path)
        result = client.commit_snapshot(snap, push=False)
        assert result["ok"]

        idx = json.loads((tmp_path / "snapshots" / "index.json").read_text())
        assert len(idx["snapshots"]) == 1
