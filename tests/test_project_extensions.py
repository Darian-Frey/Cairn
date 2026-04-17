"""Tests for project-specific schema extensions (priority #8)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.cairn_scanner import CairnScanner, compute_st_h


REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / "schemas" / "projects"


def _base_snapshot(project: str, proj_ext: dict) -> dict:
    snap = {
        "project": project,
        "CTX": f"{project} work in progress",
        "OBJ": f"advance {project}",
        "UV": [
            {
                "d_task": "DT-01",
                "desc": "initial batch",
                "blocking": False,
                "tasks": [
                    {"id": "uv_x", "obj": "do it", "priority": "p1"},
                ],
            }
        ],
        "RSK": [],
        "DEP": [],
        "PAY": {"phase": "development", "pct": 25},
        "PROJ_EXT": proj_ext,
    }
    snap["ST_H"] = compute_st_h(snap)
    return snap


# ---------- fragment files exist ----------

def test_project_fragments_are_valid_json():
    """All four project fragments parse as JSON."""
    for name in ["coda.json", "terra-siege.json", "nyx-audio.json", "lumina.json"]:
        with (PROJECTS_DIR / name).open("r", encoding="utf-8") as f:
            json.load(f)


# ---------- unknown project: fragment absent => base-only validation ----------

class TestUnknownProject:
    def test_cairn_without_proj_ext_still_valid(self):
        """The 'cairn' project has no fragment; PROJ_EXT is not required."""
        snap = _base_snapshot("cairn", {})
        del snap["PROJ_EXT"]
        snap["ST_H"] = compute_st_h(snap)
        assert CairnScanner().validate_schema(snap)

    def test_unknown_project_arbitrary_proj_ext_allowed(self):
        """Projects without a fragment can attach any PROJ_EXT shape."""
        snap = _base_snapshot("some-experimental-project", {"anything": [1, 2, 3]})
        assert CairnScanner().validate_schema(snap)


# ---------- CODA ----------

class TestCodaExtension:
    def _snap(self, proj_ext: dict) -> dict:
        return _base_snapshot("coda", proj_ext)

    def test_full_valid(self):
        snap = self._snap({
            "pipeline_status": "complete",
            "eigenvalues": [1.2, 3.4, 5.6],
            "sparc_galaxies_analysed": 175,
            "krylov_dim": 256,
            "last_run_id": "run-2026-04-17-01",
        })
        assert CairnScanner().validate_schema(snap)

    def test_minimal_valid(self):
        """pipeline_status is the only required field."""
        snap = self._snap({"pipeline_status": "idle"})
        assert CairnScanner().validate_schema(snap)

    def test_missing_proj_ext_rejected(self):
        snap = self._snap({"pipeline_status": "idle"})
        del snap["PROJ_EXT"]
        snap["ST_H"] = compute_st_h(snap)
        assert not CairnScanner().validate_schema(snap)

    def test_invalid_pipeline_status_rejected(self):
        snap = self._snap({"pipeline_status": "boom"})
        scanner = CairnScanner()
        assert not scanner.validate_schema(snap)
        assert any("pipeline_status" in e for e in scanner.schema_errors(snap))

    def test_negative_galaxy_count_rejected(self):
        snap = self._snap({"pipeline_status": "idle", "sparc_galaxies_analysed": -3})
        assert not CairnScanner().validate_schema(snap)

    def test_additional_property_rejected(self):
        """Fragments use additionalProperties: false inside PROJ_EXT."""
        snap = self._snap({"pipeline_status": "idle", "unknown_field": 7})
        assert not CairnScanner().validate_schema(snap)


# ---------- terra-siege ----------

class TestTerraSiegeExtension:
    def _snap(self, proj_ext: dict) -> dict:
        return _base_snapshot("terra-siege", proj_ext)

    def test_valid_snapshot(self):
        snap = self._snap({
            "build_phase": "alpha",
            "systems": [
                {"id": "renderer", "desc": "raylib pipeline", "completion_pct": 70},
                {"id": "input", "desc": "keyboard + joystick", "completion_pct": 100},
            ],
            "target_platforms": ["linux", "windows"],
        })
        assert CairnScanner().validate_schema(snap)

    def test_invalid_build_phase(self):
        snap = self._snap({
            "build_phase": "gold",
            "systems": [],
        })
        assert not CairnScanner().validate_schema(snap)

    def test_system_missing_required_field(self):
        snap = self._snap({
            "build_phase": "prototype",
            "systems": [{"id": "audio", "desc": "no pct"}],
        })
        assert not CairnScanner().validate_schema(snap)

    def test_completion_pct_out_of_range(self):
        snap = self._snap({
            "build_phase": "prototype",
            "systems": [{"id": "x", "desc": "y", "completion_pct": 150}],
        })
        assert not CairnScanner().validate_schema(snap)


# ---------- Nyx-Audio ----------

class TestNyxAudioExtension:
    def _snap(self, proj_ext: dict) -> dict:
        return _base_snapshot("nyx-audio", proj_ext)

    def test_valid_snapshot(self):
        snap = self._snap({
            "modules": [
                {"id": "osc", "name": "Oscillators", "completion_pct": 90,
                 "api_stability": "stable"},
                {"id": "filt", "name": "Filters", "completion_pct": 30,
                 "api_stability": "experimental"},
            ],
            "msrv": "1.75",
            "crate_version": "0.3.0",
        })
        assert CairnScanner().validate_schema(snap)

    def test_invalid_api_stability(self):
        snap = self._snap({
            "modules": [
                {"id": "osc", "name": "Oscillators", "completion_pct": 90,
                 "api_stability": "rock-solid"},
            ]
        })
        scanner = CairnScanner()
        assert not scanner.validate_schema(snap)
        assert any("api_stability" in e for e in scanner.schema_errors(snap))

    def test_empty_modules_list_allowed(self):
        snap = self._snap({"modules": []})
        assert CairnScanner().validate_schema(snap)


# ---------- Lumina ----------

class TestLuminaExtension:
    def _snap(self, proj_ext: dict) -> dict:
        return _base_snapshot("lumina", proj_ext)

    def test_valid_snapshot(self):
        snap = self._snap({
            "modules": [
                {"id": "mech", "name": "Newtonian Mechanics", "topic": "mechanics",
                 "curriculum_stage": "A-level", "completion_pct": 80},
                {"id": "em", "name": "Electromagnetism", "topic": "em",
                 "curriculum_stage": "undergrad"},
            ],
            "curriculum_alignment": "AQA-7408",
        })
        assert CairnScanner().validate_schema(snap)

    def test_module_missing_topic(self):
        snap = self._snap({
            "modules": [{"id": "x", "name": "X"}],
        })
        assert not CairnScanner().validate_schema(snap)


# ---------- project-const check ----------

class TestProjectConstEnforced:
    def test_coda_fragment_rejects_mismatched_project(self):
        """A snapshot that fills PROJ_EXT for CODA but declares project: 'other'
        should only validate against the 'other' path (no fragment) — the CODA
        fragment is only loaded when project == 'coda'."""
        snap = _base_snapshot("other-project", {"pipeline_status": "running"})
        assert CairnScanner().validate_schema(snap)

    def test_coda_project_with_coda_proj_ext_enforced(self):
        """Confirms the fragment IS loaded when project matches."""
        snap = _base_snapshot("coda", {"pipeline_status": "running"})
        # Mutate: declare CODA but provide a malformed CODA PROJ_EXT
        snap["PROJ_EXT"] = {"pipeline_status": "not-a-real-status"}
        snap["ST_H"] = compute_st_h(snap)
        assert not CairnScanner().validate_schema(snap)


# ---------- validator caching ----------

class TestValidatorCaching:
    def test_project_validators_cached(self):
        scanner = CairnScanner()
        scanner.validate_schema(_base_snapshot("coda", {"pipeline_status": "idle"}))
        scanner.validate_schema(_base_snapshot("coda", {"pipeline_status": "running"}))
        # One entry per project, cached after first load
        assert "coda" in scanner._project_validators
        # Unknown projects also cached (as None) to avoid repeated disk checks
        scanner.validate_schema(_base_snapshot("unknown-x", {}))
        assert "unknown-x" in scanner._project_validators
        assert scanner._project_validators["unknown-x"] is None


# ---------- smoke: scanner still works with no projects dir ----------

class TestMissingProjectsDir:
    def test_scanner_without_projects_dir(self, tmp_path):
        """Passing projects_dir=None disables extension loading entirely."""
        scanner = CairnScanner(projects_dir=None)
        snap = _base_snapshot("coda", {"pipeline_status": "not-real"})
        # Without the fragment, CODA's invalid pipeline_status can't be caught
        assert scanner.validate_schema(snap) is True

    def test_scanner_with_empty_projects_dir(self, tmp_path):
        scanner = CairnScanner(projects_dir=tmp_path)
        snap = _base_snapshot("coda", {"pipeline_status": "running"})
        assert scanner.validate_schema(snap) is True
