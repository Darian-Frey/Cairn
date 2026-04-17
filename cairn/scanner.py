"""CAIRN_V1 scanner — schema validation, integrity, capsule certification, index audit."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import jsonschema


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_st_h(snapshot: dict) -> str:
    payload = {k: v for k, v in snapshot.items() if k != "ST_H"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16].upper()


def _bundled_schema_path() -> Path:
    return Path(resources.files("cairn") / "schemas" / "cairn_v1.json")


def _bundled_projects_dir() -> Path:
    return Path(resources.files("cairn") / "schemas" / "projects")


class CapsuleError(Exception):
    """Raised when capsule certification fails."""


class CairnScanner:
    """Validates, hashes, audits, certifies, and indexes CAIRN_V1 snapshots.

    Parameters
    ----------
    schema_path:
        Path to the CAIRN_V1 base schema. Defaults to the bundled package schema.
    projects_dir:
        Directory containing per-project schema fragments. Defaults to the bundled
        directory. Pass `None` to disable project-extension validation entirely.
    repo_path:
        Target repository root (holds `snapshots/` and `capsules/`). Defaults to
        the current working directory. Used by `certify_capsule` and index helpers.
    """

    def __init__(
        self,
        schema_path: Path | str | None = None,
        projects_dir: Path | str | None | bool = True,
        repo_path: Path | str | None = None,
    ) -> None:
        self.schema_path = Path(schema_path) if schema_path else _bundled_schema_path()
        with self.schema_path.open("r", encoding="utf-8") as f:
            self.schema = json.load(f)
        self._validator = jsonschema.Draft7Validator(self.schema)

        if projects_dir is True:
            self.projects_dir: Path | None = _bundled_projects_dir()
        elif projects_dir is False or projects_dir is None:
            self.projects_dir = None
        else:
            self.projects_dir = Path(projects_dir)

        self._project_validators: dict[str, jsonschema.Draft7Validator | None] = {}

        self.repo_path = Path(repo_path) if repo_path is not None else Path.cwd()
        self.capsules_dir = self.repo_path / "capsules"
        self.capsule_registry = self.capsules_dir / "registry.json"
        self.snapshots_dir = self.repo_path / "snapshots"
        self.snapshot_index = self.snapshots_dir / "index.json"

    # ---- project-extension loading ----

    def _project_validator(self, project: str) -> jsonschema.Draft7Validator | None:
        if not self.projects_dir or not project:
            return None
        if project in self._project_validators:
            return self._project_validators[project]
        fragment_path = self.projects_dir / f"{project}.json"
        if not fragment_path.exists():
            self._project_validators[project] = None
            return None
        with fragment_path.open("r", encoding="utf-8") as f:
            fragment = json.load(f)
        validator = jsonschema.Draft7Validator(fragment)
        self._project_validators[project] = validator
        return validator

    # ---- validation ----

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

    # ---- structural audits ----

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
                    orphans.append({
                        "d_task": batch.get("d_task"),
                        "task_id": task.get("id"),
                        "missing_deps": missing,
                    })
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
            "critical": [], "high": [], "medium": [], "info": [],
        }
        for risk in snapshot.get("RSK", []):
            if not isinstance(risk, dict):
                continue
            level = risk.get("level")
            if level in grouped:
                grouped[level].append(risk)
        return grouped

    # ---- capsule certification ----

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

        self.capsules_dir.mkdir(parents=True, exist_ok=True)
        capsule_path = self.capsules_dir / f"{capsule_id}.json"
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
        if self.capsule_registry.exists():
            with self.capsule_registry.open("r", encoding="utf-8") as f:
                registry = json.load(f)
        else:
            registry = {"version": "1", "capsules": []}
        registry["capsules"].append(record)
        self.capsule_registry.parent.mkdir(parents=True, exist_ok=True)
        with self.capsule_registry.open("w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, sort_keys=True)

    def _index_add_capsule(self, sealed: dict, capsule_path: Path, record: dict) -> None:
        try:
            rel = str(capsule_path.resolve().relative_to(self.repo_path.resolve()))
        except ValueError:
            rel = capsule_path.name

        entry = {
            "file": rel,
            "capsule_id": record["capsule_id"],
            "ST_H": sealed["ST_H"],
            "project": sealed["project"],
            "phase": record["phase"],
        }
        index = self._load_index(self.snapshot_index)
        index["capsules"] = [
            e for e in index.get("capsules", []) if e.get("capsule_id") != record["capsule_id"]
        ]
        index["capsules"].append(entry)
        index["updated_at"] = _utc_iso_now()
        self._write_index(index, self.snapshot_index)

    # ---- index operations ----

    def update_index(
        self,
        snapshot: dict,
        snapshot_path: Path | str,
        *,
        tags: list[str] | None = None,
        index_path: Path | str | None = None,
        root: Path | str | None = None,
    ) -> dict:
        snapshot_path = Path(snapshot_path)
        target_index = Path(index_path) if index_path else self.snapshot_index
        target_root = Path(root) if root is not None else self.repo_path
        try:
            rel = str(snapshot_path.resolve().relative_to(target_root.resolve()))
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

        index = self._load_index(target_index)
        index["snapshots"] = [e for e in index.get("snapshots", []) if e.get("file") != rel]
        index["snapshots"].append(entry)
        index["updated_at"] = _utc_iso_now()
        self._write_index(index, target_index)
        return entry

    def validate_index(
        self,
        index: dict,
        snapshot_dir: Path | str,
        root: Path | str | None = None,
    ) -> list[str]:
        snapshot_dir = Path(snapshot_dir)
        target_root = Path(root) if root is not None else self.repo_path
        discrepancies: list[str] = []

        indexed_files = {entry.get("file") for entry in index.get("snapshots", [])}
        for entry in index.get("snapshots", []):
            file_rel = entry.get("file")
            if not file_rel:
                discrepancies.append("snapshot entry missing 'file'")
                continue
            path = target_root / file_rel
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
                    rel = str(path.relative_to(target_root))
                except ValueError:
                    rel = path.name
                if rel not in indexed_files:
                    discrepancies.append(f"snapshot on disk not in index: {rel}")

        for entry in index.get("capsules", []):
            file_rel = entry.get("file")
            if not file_rel:
                discrepancies.append("capsule entry missing 'file'")
                continue
            if not (target_root / file_rel).exists():
                discrepancies.append(f"indexed capsule missing on disk: {file_rel}")

        return discrepancies

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
