"""Tests for src/claude_md.py — CLAUDE.md export / import round-trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn.claude_md import markdown_to_snapshot, snapshot_to_markdown
from cairn.scanner import CairnScanner, compute_st_h


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ROUND_TRIP_FIXTURES = [
    "diff_baseline.json",
    "diff_phase_change.json",
    "chaos_critical_rsk.json",
]


def _load(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------- export structure ----------

class TestExportStructure:
    def test_has_required_sections(self):
        md = snapshot_to_markdown(_load("diff_phase_change.json"))
        for header in [
            "## Objective",
            "## Project Overview",
            "## Unresolved Vectors",
            "## Risk Register",
            "## Environment",
            "## External Resources",
        ]:
            assert header in md

    def test_metadata_header_is_valid_json(self):
        md = snapshot_to_markdown(_load("diff_phase_change.json"))
        assert md.startswith("<!-- cairn-v1")
        meta_start = md.index("<!-- cairn-v1") + len("<!-- cairn-v1")
        meta_end = md.index("-->")
        payload = md[meta_start:meta_end].strip()
        meta = json.loads(payload)
        assert meta["project"] == "cairn"
        assert meta["ST_H"] == _load("diff_phase_change.json")["ST_H"]

    def test_title_includes_phase_when_present(self):
        md = snapshot_to_markdown(_load("diff_phase_change.json"))
        assert "# cairn — Foundation — client scaffolding" in md

    def test_uv_batch_renders_blocking_flag(self):
        md = snapshot_to_markdown(_load("diff_phase_change.json"))
        assert "### DT-05 — Client foundation (blocking)" in md

    def test_critical_risk_rendered_in_table(self):
        md = snapshot_to_markdown(_load("chaos_critical_rsk.json"))
        assert "| `r_logic_trap` | critical | yes |" in md

    def test_pipe_in_risk_description_escaped(self):
        snap = _load("diff_baseline.json")
        snap["RSK"] = [
            {"id": "r_pipe", "level": "info", "blocking": False,
             "desc": "text with a | pipe in it"},
        ]
        snap["ST_H"] = compute_st_h(snap)
        md = snapshot_to_markdown(snap)
        assert "text with a \\| pipe in it" in md

    def test_empty_sections_render_placeholder(self):
        snap = _load("diff_baseline.json")
        snap["RSK"] = []
        snap["MR"] = []
        snap["ST_H"] = compute_st_h(snap)
        md = snapshot_to_markdown(snap)
        assert "_No risks logged._" in md
        assert "_No external resources linked._" in md


# ---------- round-trip fidelity ----------

class TestRoundTrip:
    @pytest.mark.parametrize("name", ROUND_TRIP_FIXTURES)
    def test_st_h_matches(self, name):
        original = _load(name)
        md = snapshot_to_markdown(original)
        restored = markdown_to_snapshot(md)
        assert restored["ST_H"] == original["ST_H"], (
            f"round-trip diverged for {name}"
        )

    @pytest.mark.parametrize("name", ROUND_TRIP_FIXTURES)
    def test_restored_snapshot_validates(self, name):
        original = _load(name)
        md = snapshot_to_markdown(original)
        restored = markdown_to_snapshot(md)
        scanner = CairnScanner()
        assert scanner.validate_schema(restored), scanner.schema_errors(restored)
        assert scanner.verify_integrity(restored)

    def test_parent_st_h_preserved(self):
        original = _load("diff_phase_change.json")
        md = snapshot_to_markdown(original)
        restored = markdown_to_snapshot(md)
        assert restored["parent_ST_H"] == original["parent_ST_H"]

    def test_dep_refs_on_tasks_survive(self):
        original = _load("chaos_critical_rsk.json")
        md = snapshot_to_markdown(original)
        restored = markdown_to_snapshot(md)
        restored_task = restored["UV"][0]["tasks"][0]
        original_task = original["UV"][0]["tasks"][0]
        assert restored_task["dep_refs"] == original_task["dep_refs"]


# ---------- import edge cases ----------

class TestImportEdgeCases:
    def test_missing_header_raises(self):
        with pytest.raises(ValueError, match="metadata header"):
            markdown_to_snapshot("# no header here\n\n## Objective\nfoo\n")

    def test_import_recomputes_st_h_when_body_edited(self):
        """Editing the Markdown body must change ST_H even if the header's
        original ST_H is retained (the header is for provenance only)."""
        original = _load("diff_baseline.json")
        md = snapshot_to_markdown(original)
        tampered = md.replace(
            "Foundation phase in progress. Scanner and schema complete.",
            "EDITED: phase advanced.",
        )
        assert tampered != md, "replacement pattern did not match fixture text"
        restored = markdown_to_snapshot(tampered)
        assert restored["ST_H"] != original["ST_H"]
        assert restored["CTX"] == "EDITED: phase advanced."

    def test_import_handles_no_parent(self):
        snap = _load("diff_baseline.json")  # parent_ST_H = None
        md = snapshot_to_markdown(snap)
        restored = markdown_to_snapshot(md)
        assert restored["parent_ST_H"] is None

    def test_capsule_metadata_preserved(self):
        snap = _load("diff_baseline.json")
        snap["capsule"] = True
        snap["capsule_id"] = "CAP-042"
        snap["ST_H"] = compute_st_h(snap)
        md = snapshot_to_markdown(snap)
        restored = markdown_to_snapshot(md)
        assert restored["capsule"] is True
        assert restored["capsule_id"] == "CAP-042"
