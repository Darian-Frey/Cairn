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
PROJECTS_SCHEMA_DIR = REPO_ROOT / "schemas" / "projects"
CAPSULES_DIR = REPO_ROOT / "capsules"
CAPSULE_REGISTRY = CAPSULES_DIR / "registry.json"
SNAPSHOTS_DIR = REPO_ROOT / "snapshots"
SNAPSHOT_INDEX = SNAPSHOTS_DIR / "index.json"


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_st_h(snapshot: dict) -> str:
    payload = {k: v for k, v in snapshot.items() if k != "ST_H"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16].upper()


class CapsuleError(Exception):
    """Raised when capsule certification fails."""


class CairnScanner:
    def __init__(
        self,
        schema_path: Path | str = SCHEMA_PATH,
        projects_dir: Path | str | None = PROJECTS_SCHEMA_DIR,
    ) -> None:
        self.schema_path = Path(schema_path)
        with self.schema_path.open("r", encoding="utf-8") as f:
            self.schema = json.load(f)
        self._validator = jsonschema.Draft7Validator(self.schema)
        self.projects_dir = Path(projects_dir) if projects_dir else None
        self._project_validators: dict[str, jsonschema.Draft7Validator] = {}

    def _project_validator(self, project: str) -> jsonschema.Draft7Validator | None:
        """Return a validator for the project extension, or None if no fragment exists."""
        if not self.projects_dir or not project:
            return None
        if project in self._project_validators:
            return self._project_validators[project]
        fragment_path = self.projects_dir / f"{project}.json"
        if not fragment_path.exists():
            self._project_validators[project] = None  # type: ignore[assignment]
            return None
        with fragment_path.open("r", encoding="utf-8") as f:
            fragment = json.load(f)
        validator = jsonschema.Draft7Validator(fragment)
        self._project_validators[project] = validator
        return validator

    def validate_schema(self, snapshot: dict) -> bool:
        if not self._validator.is_valid(snapshot):
            return False
        ext = self._project_validator(snapshot.get("project", ""))
        if ext is None:
            return True
        return ext.is_valid(snapshot)

    def schema_errors(self, snapshot: dict) -> list[str]:
        errors = [
            f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in self._validator.iter_errors(snapshot)
        ]
        ext = self._project_validator(snapshot.get("project", ""))
        if ext is not None:
            project = snapshot.get("project", "")
            for e in ext.iter_errors(snapshot):
                path = "/".join(str(p) for p in e.absolute_path) or "<root>"
                errors.append(f"[{project}] {path}: {e.message}")
        return errors

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
            "sealed_at": _utc_iso_now(),
            "ST_H": sealed["ST_H"],
            "project": sealed["project"],
            "phase": sealed.get("PAY", {}).get("phase", ""),
            "certified": True,
        }
        self._append_registry(record)
        self._index_add_capsule(sealed, capsule_path, record)
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

    def update_index(
        self,
        snapshot: dict,
        snapshot_path: str | Path,
        *,
        tags: list[str] | None = None,
        index_path: Path = SNAPSHOT_INDEX,
        root: Path | None = None,
    ) -> dict:
        """Upsert a snapshot entry in snapshots/index.json.

        Keyed on the relative file path — re-committing the same file updates
        its entry in place rather than duplicating.
        """
        snapshot_path = Path(snapshot_path)
        root = Path(root) if root is not None else REPO_ROOT
        try:
            rel = str(snapshot_path.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = snapshot_path.name

        entry = {
            "file": rel,
            "ST_H": snapshot["ST_H"],
            "project": snapshot["project"],
            "phase": snapshot.get("PAY", {}).get("phase", ""),
            "pct": snapshot.get("PAY", {}).get("pct", 0),
            "tags": tags or [],
            "capsule": bool(snapshot.get("capsule", False)),
        }
        parent = snapshot.get("parent_ST_H")
        if parent is not None:
            entry["parent_ST_H"] = parent

        index = self._load_index(index_path)
        index["snapshots"] = [e for e in index.get("snapshots", []) if e.get("file") != rel]
        index["snapshots"].append(entry)
        index["updated_at"] = _utc_iso_now()
        self._write_index(index, index_path)
        return entry

    def _index_add_capsule(self, sealed: dict, capsule_path: Path, record: dict) -> None:
        try:
            rel = str(capsule_path.resolve().relative_to(REPO_ROOT.resolve()))
        except ValueError:
            rel = capsule_path.name

        entry = {
            "file": rel,
            "capsule_id": record["capsule_id"],
            "ST_H": sealed["ST_H"],
            "project": sealed["project"],
            "phase": record["phase"],
        }
        index = self._load_index(SNAPSHOT_INDEX)
        index["capsules"] = [
            e for e in index.get("capsules", []) if e.get("capsule_id") != record["capsule_id"]
        ]
        index["capsules"].append(entry)
        index["updated_at"] = _utc_iso_now()
        self._write_index(index, SNAPSHOT_INDEX)

    @staticmethod
    def _load_index(path: Path) -> dict:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {"version": "1", "updated_at": None, "snapshots": [], "capsules": []}

    @staticmethod
    def _write_index(index: dict, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, sort_keys=True)

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
