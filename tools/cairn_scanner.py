"""CAIRN_V1 scanner — schema validation, integrity, capsule certification, index audit."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "cairn_v1.json"
CAPSULES_DIR = REPO_ROOT / "capsules"
CAPSULE_REGISTRY = CAPSULES_DIR / "registry.json"


def compute_st_h(snapshot: dict) -> str:
    payload = {k: v for k, v in snapshot.items() if k != "ST_H"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16].upper()


class CapsuleError(Exception):
    """Raised when capsule certification fails."""


class CairnScanner:
    def __init__(self, schema_path: Path | str = SCHEMA_PATH) -> None:
        self.schema_path = Path(schema_path)
        with self.schema_path.open("r", encoding="utf-8") as f:
            self.schema = json.load(f)
        self._validator = jsonschema.Draft7Validator(self.schema)

    def validate_schema(self, snapshot: dict) -> bool:
        return self._validator.is_valid(snapshot)

    def schema_errors(self, snapshot: dict) -> list[str]:
        return [
            f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in self._validator.iter_errors(snapshot)
        ]

    def verify_integrity(self, snapshot: dict) -> bool:
        claimed = snapshot.get("ST_H")
        if not isinstance(claimed, str):
            return False
        return compute_st_h(snapshot) == claimed

    def detect_orphans(self, snapshot: dict) -> list[dict]:
        dep_ids = {d.get("id") for d in snapshot.get("DEP", []) if isinstance(d, dict)}
        orphans: list[dict] = []
        for batch in snapshot.get("UV", []):
            if not isinstance(batch, dict):
                continue
            for task in batch.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                missing = [ref for ref in task.get("dep_refs", []) if ref not in dep_ids]
                if missing:
                    orphans.append(
                        {
                            "d_task": batch.get("d_task"),
                            "task_id": task.get("id"),
                            "missing_deps": missing,
                        }
                    )
        return orphans

    def detect_circular_deps(self, snapshot: dict) -> list[list[str]]:
        graph: dict[str, list[str]] = {}
        for dep in snapshot.get("DEP", []):
            if not isinstance(dep, dict):
                continue
            dep_id = dep.get("id")
            if not dep_id:
                continue
            graph[dep_id] = [r for r in dep.get("requires", []) if isinstance(r, str)]

        cycles: list[list[str]] = []
        seen_cycles: set[tuple[str, ...]] = set()

        def dfs(node: str, stack: list[str], on_stack: set[str]) -> None:
            for nxt in graph.get(node, []):
                if nxt in on_stack:
                    cycle = stack[stack.index(nxt):] + [nxt]
                    key = tuple(sorted(cycle[:-1]))
                    if key and key not in seen_cycles:
                        seen_cycles.add(key)
                        cycles.append(cycle)
                    continue
                if nxt not in graph:
                    continue
                on_stack.add(nxt)
                stack.append(nxt)
                dfs(nxt, stack, on_stack)
                stack.pop()
                on_stack.discard(nxt)

        for node in graph:
            dfs(node, [node], {node})
        return cycles

    def estimate_token_cost(self, snapshot: dict) -> int:
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        return len(canonical) // 4

    def audit_risks(self, snapshot: dict) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {
            "critical": [],
            "high": [],
            "medium": [],
            "info": [],
        }
        for risk in snapshot.get("RSK", []):
            if not isinstance(risk, dict):
                continue
            level = risk.get("level")
            if level in grouped:
                grouped[level].append(risk)
        return grouped

    def certify_capsule(self, snapshot: dict, capsule_id: str) -> dict:
        if not self.validate_schema(snapshot):
            raise CapsuleError(
                f"Snapshot fails schema validation: {self.schema_errors(snapshot)}"
            )
        if not self.verify_integrity(snapshot):
            raise CapsuleError("Snapshot ST_H does not match recomputed hash.")

        sealed = dict(snapshot)
        sealed["capsule"] = True
        sealed["capsule_id"] = capsule_id
        sealed["ST_H"] = compute_st_h(sealed)

        CAPSULES_DIR.mkdir(parents=True, exist_ok=True)
        capsule_path = CAPSULES_DIR / f"{capsule_id}.json"
        if capsule_path.exists():
            raise CapsuleError(f"Capsule already exists: {capsule_path}")

        with capsule_path.open("w", encoding="utf-8") as f:
            json.dump(sealed, f, indent=2, sort_keys=True)

        record = {
            "capsule_id": capsule_id,
            "sealed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ST_H": sealed["ST_H"],
            "project": sealed["project"],
            "phase": sealed.get("PAY", {}).get("phase", ""),
            "certified": True,
        }
        self._append_registry(record)
        return record

    def _append_registry(self, record: dict) -> None:
        if CAPSULE_REGISTRY.exists():
            with CAPSULE_REGISTRY.open("r", encoding="utf-8") as f:
                registry = json.load(f)
        else:
            registry = {"version": "1", "capsules": []}
        registry["capsules"].append(record)
        with CAPSULE_REGISTRY.open("w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, sort_keys=True)

    def validate_index(
        self,
        index: dict,
        snapshot_dir: str | Path,
        root: str | Path | None = None,
    ) -> list[str]:
        snapshot_dir = Path(snapshot_dir)
        root = Path(root) if root is not None else REPO_ROOT
        discrepancies: list[str] = []

        indexed_files = {entry.get("file") for entry in index.get("snapshots", [])}
        for entry in index.get("snapshots", []):
            file_rel = entry.get("file")
            if not file_rel:
                discrepancies.append("snapshot entry missing 'file'")
                continue
            path = root / file_rel
            if not path.exists():
                discrepancies.append(f"indexed snapshot missing on disk: {file_rel}")
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    snap = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                discrepancies.append(f"unreadable snapshot {file_rel}: {exc}")
                continue
            if snap.get("ST_H") != entry.get("ST_H"):
                discrepancies.append(
                    f"ST_H mismatch for {file_rel}: index={entry.get('ST_H')} file={snap.get('ST_H')}"
                )

        if snapshot_dir.exists():
            for path in sorted(snapshot_dir.glob("*.json")):
                if path.name == "index.json":
                    continue
                try:
                    rel = str(path.relative_to(root))
                except ValueError:
                    rel = path.name
                if rel not in indexed_files:
                    discrepancies.append(f"snapshot on disk not in index: {rel}")

        for entry in index.get("capsules", []):
            file_rel = entry.get("file")
            if not file_rel:
                discrepancies.append("capsule entry missing 'file'")
                continue
            if not (root / file_rel).exists():
                discrepancies.append(f"indexed capsule missing on disk: {file_rel}")

        return discrepancies


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="CAIRN_V1 scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate", help="Validate a snapshot against the schema")
    p_val.add_argument("snapshot", type=Path)

    p_int = sub.add_parser("integrity", help="Verify snapshot ST_H")
    p_int.add_argument("snapshot", type=Path)

    p_audit = sub.add_parser("audit", help="Full audit of a snapshot")
    p_audit.add_argument("snapshot", type=Path)

    p_cert = sub.add_parser("certify", help="Certify a snapshot as a capsule")
    p_cert.add_argument("snapshot", type=Path)
    p_cert.add_argument("capsule_id")

    p_idx = sub.add_parser("index", help="Validate snapshots/index.json")
    p_idx.add_argument("index", type=Path)
    p_idx.add_argument("snapshot_dir", type=Path)

    args = parser.parse_args()
    scanner = CairnScanner()

    def load(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    if args.cmd == "validate":
        snap = load(args.snapshot)
        ok = scanner.validate_schema(snap)
        if ok:
            print("OK")
            return 0
        for err in scanner.schema_errors(snap):
            print(f"  - {err}")
        return 1

    if args.cmd == "integrity":
        snap = load(args.snapshot)
        ok = scanner.verify_integrity(snap)
        print("OK" if ok else f"MISMATCH (expected {compute_st_h(snap)})")
        return 0 if ok else 1

    if args.cmd == "audit":
        snap = load(args.snapshot)
        report = {
            "schema_valid": scanner.validate_schema(snap),
            "schema_errors": scanner.schema_errors(snap),
            "integrity": scanner.verify_integrity(snap),
            "orphans": scanner.detect_orphans(snap),
            "circular_deps": scanner.detect_circular_deps(snap),
            "est_tokens": scanner.estimate_token_cost(snap),
            "risks": scanner.audit_risks(snap),
        }
        print(json.dumps(report, indent=2))
        return 0 if report["schema_valid"] and report["integrity"] else 1

    if args.cmd == "certify":
        snap = load(args.snapshot)
        record = scanner.certify_capsule(snap, args.capsule_id)
        print(json.dumps(record, indent=2))
        return 0

    if args.cmd == "index":
        idx = load(args.index)
        issues = scanner.validate_index(idx, args.snapshot_dir)
        if not issues:
            print("OK")
            return 0
        for issue in issues:
            print(f"  - {issue}")
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
