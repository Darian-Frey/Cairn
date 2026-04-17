"""Generate CAIRN_V1 test fixtures with correctly-computed ST_H values.

Run this to (re)produce the JSON fixtures in tests/fixtures/.
The JSON files are checked in — this script is the canonical authorship.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.cairn_scanner import compute_st_h  # noqa: E402


def _write(name: str, snap: dict) -> None:
    snap["ST_H"] = compute_st_h(snap)
    path = HERE / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {path.name}  ST_H={snap['ST_H']}")


# --- chaos: critical RSK blocks execution ---
# Mirrors the intent of old_code/ltm_bridge/snapshots/chaos_test_v1.json:
# a critical RSK that must halt UV execution until resolved. Also exercises
# the orphan path — uv_deploy depends on dep_core_validator which exists,
# but the risk language ties the dep to the blocking rationale.
chaos = {
    "project": "cairn",
    "ALN": ["logic_trap", "dependency_hell"],
    "CTX": "Simulating a critical infrastructure migration under duress.",
    "OBJ": "Complete the 'Omni-Sync' deployment without data loss.",
    "CON": ["Zero downtime", "No data loss"],
    "UV": [
        {
            "d_task": "DT-01",
            "desc": "Omni-Sync cutover batch",
            "blocking": True,
            "tasks": [
                {
                    "id": "uv_deploy",
                    "obj": "Execute production cutover",
                    "priority": "p1",
                    "dep_refs": ["dep_core_validator"],
                },
                {
                    "id": "uv_cleanup",
                    "obj": "Decommission legacy nodes",
                    "priority": "p2",
                },
            ],
        }
    ],
    "RSK": [
        {
            "id": "r_logic_trap",
            "level": "critical",
            "desc": (
                "Omni-Sync logic contains a feedback loop that will corrupt the "
                "database if uv_deploy runs before dep_core_validator is patched."
            ),
            "blocking": True,
        }
    ],
    "DEP": [
        {
            "id": "dep_core_validator",
            "comp": "Core-Validator",
            "ver": "v2.0-patch-A",
            "role": "Anti-Corruption Gate",
        }
    ],
    "BC": ["v1.0 must be maintained for failback"],
    "PAY": {"phase": "CHAOS_STRESS_TEST", "pct": 0},
    "MR": [{"id": "LTM-Bridge-Core", "ref": "github.com/Darian-Frey/LTM-Bridge", "kind": "gh"}],
}


# --- diff baseline ---
# Clean Foundation-phase snapshot. No parent (first in chain).
diff_baseline = {
    "project": "cairn",
    "parent_ST_H": None,
    "CTX": "Foundation phase in progress. Scanner and schema complete.",
    "OBJ": "Wire the index and begin client work.",
    "CON": ["No SCHEMA_V5 naming", "No clipboard watcher"],
    "UV": [
        {
            "d_task": "DT-04",
            "desc": "Index wiring",
            "blocking": True,
            "tasks": [
                {"id": "uv_seed_index", "obj": "Seed snapshots/index.json", "priority": "p1"},
                {"id": "uv_update_index", "obj": "Implement update_index()", "priority": "p1"},
            ],
        }
    ],
    "RSK": [
        {"id": "r_no_fixtures", "level": "medium",
         "desc": "No CAIRN_V1 fixtures yet — §8 cases partially covered.",
         "blocking": False},
    ],
    "DEP": [
        {"id": "dep_python", "comp": "python", "ver": "3.12", "role": "runtime"},
        {"id": "dep_jsonschema", "comp": "jsonschema", "ver": ">=4.0", "role": "validation"},
    ],
    "PAY": {"phase": "Foundation — index wiring", "pct": 35},
    "MR": [
        {"id": "cairn-repo", "ref": "github.com/Darian-Frey/Cairn", "kind": "gh"},
    ],
}


# --- diff phase change: advances phase, completes one UV task, adds another, chained ---
diff_phase_change = {
    "project": "cairn",
    "parent_ST_H": compute_st_h(diff_baseline),
    "CTX": "Index wiring complete. Client scaffolding next.",
    "OBJ": "Begin cairn_client.py evolution from LTM-Bridge base.",
    "CON": ["No SCHEMA_V5 naming", "No clipboard watcher"],
    "UV": [
        {
            "d_task": "DT-05",
            "desc": "Client foundation",
            "blocking": True,
            "tasks": [
                {"id": "uv_client_diff", "obj": "Port get_diff() from LTM-Bridge",
                 "priority": "p1"},
                {"id": "uv_client_commit", "obj": "Port commit_snapshot()", "priority": "p1"},
                {"id": "uv_client_prune", "obj": "Implement --prune (preserve capsules)",
                 "priority": "p2"},
            ],
        }
    ],
    "RSK": [
        {"id": "r_no_fixtures", "level": "info",
         "desc": "CAIRN_V1 fixtures authored; §8 cases #1, #3, #4 unblocked.",
         "blocking": False},
    ],
    "DEP": [
        {"id": "dep_python", "comp": "python", "ver": "3.12", "role": "runtime"},
        {"id": "dep_jsonschema", "comp": "jsonschema", "ver": ">=4.0", "role": "validation"},
        {"id": "dep_git", "comp": "git", "ver": ">=2.30", "role": "commit-pipeline"},
    ],
    "PAY": {"phase": "Foundation — client scaffolding", "pct": 45},
    "MR": [
        {"id": "cairn-repo", "ref": "github.com/Darian-Frey/Cairn", "kind": "gh"},
        {"id": "ltm-bridge-client", "ref": "old_code/ltm_bridge/ltm_bridge_client.py",
         "kind": "path"},
    ],
}


# --- diff no-change: semantically identical to diff_phase_change ---
# Intent: when the semantic diff path runs on these two, it should report
# "no material change". The scanner's ST_H will differ only if any field
# differs; since we want true semantic-equivalence, this fixture is a byte-
# level duplicate of diff_phase_change. The client's get_diff() must treat
# identical content as a no-op, even if e.g. a timestamp in a non-semantic
# field were added (future).
diff_no_change = json.loads(json.dumps(diff_phase_change))


if __name__ == "__main__":
    _write("chaos_critical_rsk.json", chaos)
    _write("diff_baseline.json", diff_baseline)
    _write("diff_phase_change.json", diff_phase_change)
    _write("diff_no_change.json", diff_no_change)
