"""Tests for src/cairn_server.py — end-to-end against a real HTTP server."""

from __future__ import annotations

import copy
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.cairn_client import CairnClient
from src.cairn_server import CairnServer
from tools.cairn_scanner import compute_st_h


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def repo(tmp_path):
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
def server(repo):
    client = CairnClient(repo_path=repo)
    srv = CairnServer(host="127.0.0.1", port=0, client=client)
    srv.start_in_thread()
    yield srv
    srv.shutdown()


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post_raw(url: str, data: bytes, content_type: str = "application/json") -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------- /health and /status ----------

class TestHealthAndStatus:
    def test_health_ok(self, server):
        status, body = _get(f"http://127.0.0.1:{server.port}/health")
        assert status == 200
        assert body["ok"] is True

    def test_status_empty_repo(self, server):
        status, body = _get(f"http://127.0.0.1:{server.port}/status")
        assert status == 404
        assert "no snapshots" in body["error"]

    def test_status_after_commit(self, server):
        snap = _load("diff_baseline.json")
        _post(f"http://127.0.0.1:{server.port}/snapshot", snap)
        status, body = _get(f"http://127.0.0.1:{server.port}/status")
        assert status == 200
        assert body["ST_H"] == snap["ST_H"]
        assert body["project"] == "cairn"
        assert body["pct"] == 35
        assert body["capsule_count"] == 0

    def test_unknown_get_path_returns_404(self, server):
        status, _ = _get(f"http://127.0.0.1:{server.port}/nope")
        assert status == 404


# ---------- POST /snapshot ----------

class TestCommitEndpoint:
    def test_accepts_valid_snapshot(self, server, repo):
        snap = _load("diff_baseline.json")
        status, body = _post(f"http://127.0.0.1:{server.port}/snapshot", snap)
        assert status == 200
        assert body["ok"] is True
        assert body["dry_run"] is False
        assert Path(body["filepath"]).exists()
        # Index updated
        idx = json.loads((repo / "snapshots" / "index.json").read_text())
        assert len(idx["snapshots"]) == 1

    def test_dry_run_writes_nothing(self, server, repo):
        snap = _load("diff_baseline.json")
        status, body = _post(
            f"http://127.0.0.1:{server.port}/snapshot?dry_run=true", snap
        )
        assert status == 200
        assert body["dry_run"] is True
        assert not Path(body["filepath"]).exists()
        idx = json.loads((repo / "snapshots" / "index.json").read_text())
        assert idx["snapshots"] == []

    def test_schema_failure_returns_400(self, server):
        snap = _load("diff_baseline.json")
        del snap["OBJ"]
        snap["ST_H"] = compute_st_h(snap)
        status, body = _post(f"http://127.0.0.1:{server.port}/snapshot", snap)
        assert status == 400
        assert "schema" in body["error"].lower()

    def test_integrity_failure_returns_400(self, server):
        snap = _load("diff_baseline.json")
        snap["ST_H"] = "0000000000000000"
        status, body = _post(f"http://127.0.0.1:{server.port}/snapshot", snap)
        assert status == 400
        assert "ST_H" in body["error"]

    def test_critical_risk_blocks_with_409(self, server):
        snap = _load("chaos_critical_rsk.json")
        status, body = _post(f"http://127.0.0.1:{server.port}/snapshot", snap)
        assert status == 409
        assert "critical risk" in body["error"]

    def test_force_param_bypasses_critical(self, server):
        snap = _load("chaos_critical_rsk.json")
        status, body = _post(
            f"http://127.0.0.1:{server.port}/snapshot?force=true", snap
        )
        assert status == 200
        assert body["ok"] is True
        assert body["blocking_risks"] == 1

    def test_tags_query_param(self, server, repo):
        snap = _load("diff_baseline.json")
        _post(
            f"http://127.0.0.1:{server.port}/snapshot?tags=alpha,beta", snap
        )
        idx = json.loads((repo / "snapshots" / "index.json").read_text())
        assert idx["snapshots"][0]["tags"] == ["alpha", "beta"]

    def test_invalid_json_body_returns_400(self, server):
        status, body = _post_raw(
            f"http://127.0.0.1:{server.port}/snapshot", b"not json at all"
        )
        assert status == 400
        body_json = json.loads(body)
        assert "invalid JSON" in body_json["error"]

    def test_non_object_body_returns_400(self, server):
        status, body = _post(f"http://127.0.0.1:{server.port}/snapshot", [])  # type: ignore[arg-type]
        assert status == 400
        assert "object" in body["error"]

    def test_unknown_post_endpoint_returns_404(self, server):
        status, body = _post(f"http://127.0.0.1:{server.port}/wrong", {})
        assert status == 404

    def test_diff_reflects_chain(self, server):
        """Two consecutive commits over HTTP should produce a correct diff payload."""
        base = _load("diff_baseline.json")
        child = _load("diff_phase_change.json")
        _post(f"http://127.0.0.1:{server.port}/snapshot", base)
        status, body = _post(f"http://127.0.0.1:{server.port}/snapshot", child)
        assert status == 200
        assert body["diff"]["parent_st_h_link"] is True
        assert body["diff"]["pct_from"] == 35
        assert body["diff"]["pct_to"] == 45
        assert set(body["diff"]["completed_tasks"]) == {
            "uv_seed_index", "uv_update_index"
        }

    def test_no_op_diff(self, server):
        snap = _load("diff_phase_change.json")
        _post(f"http://127.0.0.1:{server.port}/snapshot", snap)
        dup = copy.deepcopy(snap)
        status, body = _post(f"http://127.0.0.1:{server.port}/snapshot", dup)
        assert status == 200
        assert "no semantic changes" in body["diff"]["summary"]
