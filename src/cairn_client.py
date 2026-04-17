"""CAIRN_V1 client — validates, diffs, commits, prunes snapshots.

Evolved from old_code/ltm_bridge/ltm_bridge_client.py.
Drops the clipboard watcher (superseded by cairn_server.py, priority #7).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT_DEFAULT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_DEFAULT))

from tools.cairn_scanner import CairnScanner, compute_st_h  # noqa: E402


class CommitError(Exception):
    """Raised when commit_snapshot refuses to proceed."""


@dataclass
class DiffReport:
    first_snapshot: bool = False
    new_risks: list[str] = field(default_factory=list)
    resolved_risks: list[str] = field(default_factory=list)
    completed_tasks: list[str] = field(default_factory=list)
    started_tasks: list[str] = field(default_factory=list)
    pct_from: int | None = None
    pct_to: int | None = None
    phase_from: str | None = None
    phase_to: str | None = None
    parent_st_h_link: bool = False

    @property
    def empty(self) -> bool:
        if self.first_snapshot:
            return False
        return not (
            self.new_risks
            or self.resolved_risks
            or self.completed_tasks
            or self.started_tasks
            or self.pct_from != self.pct_to
            or self.phase_from != self.phase_to
        )

    def summary(self) -> str:
        if self.first_snapshot:
            return "[first snapshot of session]"
        parts: list[str] = []
        if self.new_risks:
            parts.append(f"NEW RISKS: {', '.join(sorted(self.new_risks))}")
        if self.resolved_risks:
            parts.append(f"RESOLVED: {', '.join(sorted(self.resolved_risks))}")
        if self.completed_tasks:
            parts.append(f"COMPLETED: {len(self.completed_tasks)} task(s)")
        if self.started_tasks:
            parts.append(f"STARTED: {len(self.started_tasks)} task(s)")
        if self.phase_from != self.phase_to:
            parts.append(f"PHASE: {self.phase_from!r} -> {self.phase_to!r}")
        if self.pct_from != self.pct_to:
            parts.append(f"PROGRESS: {self.pct_from}% -> {self.pct_to}%")
        if not parts:
            return "no semantic changes detected"
        return " | ".join(parts)


def _uv_task_ids(snap: dict) -> set[str]:
    ids: set[str] = set()
    for batch in snap.get("UV", []) or []:
        if not isinstance(batch, dict):
            continue
        for task in batch.get("tasks", []) or []:
            if isinstance(task, dict) and task.get("id"):
                ids.add(task["id"])
    return ids


def _risk_ids(snap: dict) -> set[str]:
    return {
        r["id"] for r in snap.get("RSK", []) or []
        if isinstance(r, dict) and "id" in r
    }


class CairnClient:
    def __init__(
        self,
        repo_path: str | Path = REPO_ROOT_DEFAULT,
        scanner: CairnScanner | None = None,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.scanner = scanner or CairnScanner()
        self.last_state: dict | None = None

    # ---- git plumbing ----

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
        )

    # ---- diff ----

    def get_diff(self, new_snap: dict, reference: dict | None = None) -> DiffReport:
        """Semantic diff between a reference snapshot and the new one.

        reference defaults to self.last_state. If both are None, report first-snapshot.
        """
        ref = reference if reference is not None else self.last_state
        if ref is None:
            return DiffReport(first_snapshot=True)

        report = DiffReport()
        old_rsk, new_rsk = _risk_ids(ref), _risk_ids(new_snap)
        report.new_risks = sorted(new_rsk - old_rsk)
        report.resolved_risks = sorted(old_rsk - new_rsk)

        old_uv, new_uv = _uv_task_ids(ref), _uv_task_ids(new_snap)
        report.completed_tasks = sorted(old_uv - new_uv)
        report.started_tasks = sorted(new_uv - old_uv)

        report.pct_from = ref.get("PAY", {}).get("pct", 0)
        report.pct_to = new_snap.get("PAY", {}).get("pct", 0)
        report.phase_from = ref.get("PAY", {}).get("phase", "")
        report.phase_to = new_snap.get("PAY", {}).get("phase", "")
        report.parent_st_h_link = new_snap.get("parent_ST_H") == ref.get("ST_H")
        return report

    # ---- risk gate ----

    def blocking_critical_risks(self, snap: dict) -> list[dict]:
        return [
            r for r in snap.get("RSK", []) or []
            if isinstance(r, dict) and r.get("level") == "critical" and r.get("blocking")
        ]

    # ---- commit pipeline ----

    def commit_snapshot(
        self,
        snap: dict,
        *,
        dry_run: bool = False,
        force: bool = False,
        push: bool = True,
        tags: list[str] | None = None,
    ) -> dict:
        """Validate, audit, diff, write, index, and (optionally) git-sync a snapshot.

        Returns a result dict with: ok, filepath, diff, blocking_risks, dry_run.
        Raises CommitError on refusal.
        """
        if not self.scanner.validate_schema(snap):
            raise CommitError(
                "Snapshot fails schema validation:\n  "
                + "\n  ".join(self.scanner.schema_errors(snap))
            )
        if not self.scanner.verify_integrity(snap):
            raise CommitError(
                f"ST_H mismatch. Expected {compute_st_h(snap)}, got {snap.get('ST_H')}."
            )

        blocking = self.blocking_critical_risks(snap)
        if blocking:
            self._alert_critical(blocking)
            if not force:
                raise CommitError(
                    f"Commit blocked by {len(blocking)} critical risk(s). "
                    "Resolve the risk(s) or pass force=True."
                )

        diff = self.get_diff(snap)
        project = snap["project"]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filepath = self.repo_path / "snapshots" / f"{project}_{timestamp}.json"

        result = {
            "ok": False,
            "filepath": str(filepath),
            "diff": diff,
            "blocking_risks": blocking,
            "dry_run": dry_run,
        }
        if dry_run:
            result["ok"] = True
            return result

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2, sort_keys=True)

        self.scanner.update_index(
            snap,
            filepath,
            tags=tags,
            index_path=self.repo_path / "snapshots" / "index.json",
            root=self.repo_path,
        )

        if push:
            self._git_sync(filepath, project, diff)

        self.last_state = snap
        result["ok"] = True
        return result

    def _git_sync(self, filepath: Path, project: str, diff: DiffReport) -> None:
        rel = filepath.relative_to(self.repo_path)
        index_rel = Path("snapshots") / "index.json"
        self._run_git(["add", str(rel), str(index_rel)])
        summary = diff.summary().replace("\n", " ")[:60]
        msg = f"sync({project}): {summary}"
        commit = self._run_git(["commit", "-m", msg], check=False)
        if commit.returncode != 0:
            # Nothing staged or hook refused — surface but don't crash.
            print(f"[!] git commit returned {commit.returncode}: {commit.stderr.strip()}")
            return
        push = self._run_git(["push", "origin", "main"], check=False)
        if push.returncode != 0:
            print(f"[!] git push failed: {push.stderr.strip()}")

    @staticmethod
    def _alert_critical(risks: list[dict]) -> None:
        print("\033[91m\033[1m[!!!] CRITICAL BLOCKING RISK DETECTED [!!!]\033[0m")
        for r in risks:
            print(f"  >> {r['id']}: {r.get('desc', '')}")

    # ---- prune ----

    def prune(self, keep_n: int, *, dry_run: bool = False) -> list[Path]:
        """Keep the last N snapshots in snapshots/; delete older ones.

        Never touches capsules/ or any file declaring capsule=true.
        Returns the list of files that were (or would be) deleted.
        """
        if keep_n < 0:
            raise ValueError("keep_n must be >= 0")
        snap_dir = self.repo_path / "snapshots"
        if not snap_dir.exists():
            return []
        candidates: list[Path] = []
        for path in snap_dir.glob("*.json"):
            if path.name == "index.json":
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    snap = json.load(f)
            except (OSError, json.JSONDecodeError):
                # Unreadable files are left alone — prune doesn't own cleanup.
                continue
            if snap.get("capsule") is True:
                # Belt-and-braces: capsules shouldn't be here, but never delete if they are.
                continue
            candidates.append(path)

        candidates.sort(key=lambda p: p.stat().st_mtime)
        to_delete = candidates[:-keep_n] if keep_n > 0 else candidates
        if dry_run:
            return to_delete

        index_path = self.repo_path / "snapshots" / "index.json"
        index = None
        if index_path.exists():
            with index_path.open("r", encoding="utf-8") as f:
                index = json.load(f)

        for path in to_delete:
            path.unlink()
            if index is not None:
                try:
                    rel = str(path.relative_to(self.repo_path))
                except ValueError:
                    rel = path.name
                index["snapshots"] = [
                    e for e in index.get("snapshots", []) if e.get("file") != rel
                ]

        if index is not None:
            index["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with index_path.open("w", encoding="utf-8") as f:
                json.dump(index, f, indent=2, sort_keys=True)

        return to_delete


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="CAIRN_V1 client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_commit = sub.add_parser("commit", help="Validate, audit, write, index, and sync a snapshot")
    p_commit.add_argument("snapshot", type=Path)
    p_commit.add_argument("--dry-run", action="store_true")
    p_commit.add_argument("--force", action="store_true", help="Override blocking critical RSK")
    p_commit.add_argument("--no-push", action="store_true")
    p_commit.add_argument("--tag", action="append", default=[], dest="tags")

    p_diff = sub.add_parser("diff", help="Report semantic diff between two snapshots")
    p_diff.add_argument("reference", type=Path)
    p_diff.add_argument("new", type=Path)

    p_prune = sub.add_parser("prune", help="Keep last N snapshots, delete older ones")
    p_prune.add_argument("-n", "--keep", type=int, required=True)
    p_prune.add_argument("--dry-run", action="store_true")

    p_export = sub.add_parser("export", help="Export a snapshot as Markdown (for CLAUDE.md handoff)")
    p_export.add_argument("snapshot", type=Path)
    p_export.add_argument("--format", choices=["claude-md"], default="claude-md")
    p_export.add_argument("-o", "--output", type=Path, help="Write to file (default: stdout)")

    p_import = sub.add_parser("import", help="Import a CLAUDE.md Markdown file into a snapshot")
    p_import.add_argument("markdown", type=Path)
    p_import.add_argument("--from", dest="src_format", choices=["claude-md"], default="claude-md")
    p_import.add_argument("-o", "--output", type=Path, help="Write to file (default: stdout)")

    args = parser.parse_args()
    client = CairnClient()

    def _load(p: Path) -> dict:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    if args.cmd == "commit":
        try:
            result = client.commit_snapshot(
                _load(args.snapshot),
                dry_run=args.dry_run,
                force=args.force,
                push=not args.no_push,
                tags=args.tags,
            )
        except CommitError as exc:
            print(f"[!] {exc}")
            return 1
        print(f"[#] DIFF: {result['diff'].summary()}")
        if result["dry_run"]:
            print(f"[#] DRY RUN — would write {result['filepath']}")
        else:
            print(f"[+] wrote {result['filepath']}")
        return 0

    if args.cmd == "diff":
        ref = _load(args.reference)
        new = _load(args.new)
        report = client.get_diff(new, reference=ref)
        print(report.summary())
        return 0

    if args.cmd == "prune":
        deleted = client.prune(args.keep, dry_run=args.dry_run)
        verb = "would delete" if args.dry_run else "deleted"
        print(f"[#] {verb} {len(deleted)} snapshot(s)")
        for p in deleted:
            print(f"  - {p}")
        return 0

    if args.cmd == "export":
        from src.claude_md import snapshot_to_markdown

        md = snapshot_to_markdown(_load(args.snapshot))
        if args.output:
            args.output.write_text(md, encoding="utf-8")
        else:
            sys.stdout.write(md)
        return 0

    if args.cmd == "import":
        from src.claude_md import markdown_to_snapshot

        snap = markdown_to_snapshot(args.markdown.read_text(encoding="utf-8"))
        payload = json.dumps(snap, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            sys.stdout.write(payload + "\n")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
