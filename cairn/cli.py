"""Unified `cairn` CLI — dispatches to scanner / client / server / init subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cairn.scanner import CairnScanner, CapsuleError, compute_st_h


def _load(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _cmd_init(args: argparse.Namespace) -> int:
    from cairn.init import init_project

    report = init_project(
        args.target,
        args.project,
        force=args.force,
        write_claude_md=args.with_claude_md,
        write_projects_dir=args.with_projects_dir,
        copy_bundled_projects=args.with_bundled_projects,
    )
    print(f"[+] Cairn initialised at {report['target']}")
    print(f"    project: {report['project']}")
    if report["created"]:
        print("    created:")
        for c in report["created"]:
            print(f"      + {c}")
    if report["skipped"]:
        print("    skipped:")
        for s in report["skipped"]:
            print(f"      - {s}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    scanner = CairnScanner(repo_path=args.repo)
    snap = _load(args.snapshot)
    if scanner.validate_schema(snap):
        print("OK")
        return 0
    for err in scanner.schema_errors(snap):
        print(f"  - {err}")
    return 1


def _cmd_integrity(args: argparse.Namespace) -> int:
    scanner = CairnScanner(repo_path=args.repo)
    snap = _load(args.snapshot)
    ok = scanner.verify_integrity(snap)
    print("OK" if ok else f"MISMATCH (expected {compute_st_h(snap)})")
    return 0 if ok else 1


def _cmd_audit(args: argparse.Namespace) -> int:
    scanner = CairnScanner(repo_path=args.repo)
    snap = _load(args.snapshot)
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


def _cmd_certify(args: argparse.Namespace) -> int:
    scanner = CairnScanner(repo_path=args.repo)
    snap = _load(args.snapshot)
    try:
        record = scanner.certify_capsule(snap, args.capsule_id)
    except CapsuleError as exc:
        print(f"[!] {exc}")
        return 1
    print(json.dumps(record, indent=2))
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    scanner = CairnScanner(repo_path=args.repo)
    idx = _load(args.index)
    issues = scanner.validate_index(idx, args.snapshot_dir, root=args.repo)
    if not issues:
        print("OK")
        return 0
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _cmd_commit(args: argparse.Namespace) -> int:
    from cairn.client import CairnClient, CommitError

    client = CairnClient(repo_path=args.repo)
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


def _cmd_diff(args: argparse.Namespace) -> int:
    from cairn.client import CairnClient

    client = CairnClient(repo_path=args.repo)
    ref = _load(args.reference)
    new = _load(args.new)
    report = client.get_diff(new, reference=ref)
    print(report.summary())
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    from cairn.client import CairnClient

    client = CairnClient(repo_path=args.repo)
    deleted = client.prune(args.keep, dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    print(f"[#] {verb} {len(deleted)} snapshot(s)")
    for p in deleted:
        print(f"  - {p}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from cairn.claude_md import snapshot_to_markdown

    md = snapshot_to_markdown(_load(args.snapshot))
    if args.output:
        args.output.write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from cairn.claude_md import markdown_to_snapshot

    snap = markdown_to_snapshot(args.markdown.read_text(encoding="utf-8"))
    payload = json.dumps(snap, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from cairn.server import CairnServer

    server = CairnServer(host=args.host, port=args.port, repo_path=args.repo)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] shutting down")
        server.shutdown()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn", description="CAIRN_V1 toolchain")
    parser.add_argument("--repo", type=Path, default=None,
                        help="Target repo root (default: cwd)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Seed a Cairn layout in the target directory")
    p_init.add_argument("project")
    p_init.add_argument("--target", type=Path, default=Path("."))
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--with-claude-md", action="store_true")
    p_init.add_argument("--with-projects-dir", action="store_true")
    p_init.add_argument("--with-bundled-projects", action="store_true",
                        help="Also copy bundled project fragments (coda, etc.) into target")
    p_init.set_defaults(func=_cmd_init)

    p_val = sub.add_parser("validate", help="Validate a snapshot against the schema")
    p_val.add_argument("snapshot", type=Path)
    p_val.set_defaults(func=_cmd_validate)

    p_int = sub.add_parser("integrity", help="Verify a snapshot's ST_H")
    p_int.add_argument("snapshot", type=Path)
    p_int.set_defaults(func=_cmd_integrity)

    p_audit = sub.add_parser("audit", help="Full audit of a snapshot")
    p_audit.add_argument("snapshot", type=Path)
    p_audit.set_defaults(func=_cmd_audit)

    p_cert = sub.add_parser("certify", help="Certify a snapshot as a capsule")
    p_cert.add_argument("snapshot", type=Path)
    p_cert.add_argument("capsule_id")
    p_cert.set_defaults(func=_cmd_certify)

    p_idx = sub.add_parser("index", help="Validate snapshots/index.json")
    p_idx.add_argument("index", type=Path)
    p_idx.add_argument("snapshot_dir", type=Path)
    p_idx.set_defaults(func=_cmd_index)

    p_commit = sub.add_parser("commit", help="Validate, audit, write, index, and sync a snapshot")
    p_commit.add_argument("snapshot", type=Path)
    p_commit.add_argument("--dry-run", action="store_true")
    p_commit.add_argument("--force", action="store_true")
    p_commit.add_argument("--no-push", action="store_true")
    p_commit.add_argument("--tag", action="append", default=[], dest="tags")
    p_commit.set_defaults(func=_cmd_commit)

    p_diff = sub.add_parser("diff", help="Semantic diff between two snapshots")
    p_diff.add_argument("reference", type=Path)
    p_diff.add_argument("new", type=Path)
    p_diff.set_defaults(func=_cmd_diff)

    p_prune = sub.add_parser("prune", help="Keep last N snapshots, delete older ones")
    p_prune.add_argument("-n", "--keep", type=int, required=True)
    p_prune.add_argument("--dry-run", action="store_true")
    p_prune.set_defaults(func=_cmd_prune)

    p_export = sub.add_parser("export", help="Export a snapshot as CLAUDE.md markdown")
    p_export.add_argument("snapshot", type=Path)
    p_export.add_argument("-o", "--output", type=Path)
    p_export.set_defaults(func=_cmd_export)

    p_import = sub.add_parser("import", help="Import a CLAUDE.md markdown file into a snapshot")
    p_import.add_argument("markdown", type=Path)
    p_import.add_argument("-o", "--output", type=Path)
    p_import.set_defaults(func=_cmd_import)

    p_serve = sub.add_parser("serve", help="Run the Cairn IPC server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=7331)
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
