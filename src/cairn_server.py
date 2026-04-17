"""Local IPC server for Cairn — HTTP replacement for the old clipboard watcher.

Binds to 127.0.0.1 only; intended for the host agent to POST snapshots into
the commit pipeline without spawning a CLI per call.

  POST /snapshot?force=&dry_run=&push=&tags=a,b   — body: CAIRN_V1 snapshot JSON
  GET  /status                                    — summary of last-indexed snapshot
  GET  /health                                    — liveness check
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT_DEFAULT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_DEFAULT))

from src.cairn_client import CairnClient, CommitError  # noqa: E402


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7331


def _truthy(v: str | None) -> bool:
    return (v or "").lower() in {"1", "true", "yes", "on"}


class _Handler(BaseHTTPRequestHandler):
    # Injected by CairnServer
    client: CairnClient = None  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence default stderr logging; tests run quieter this way.
        return

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ---- POST /snapshot ----

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/snapshot":
            self._send_json(404, {"error": f"unknown endpoint: {parsed.path}"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            snap = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"invalid JSON: {exc}"})
            return
        if not isinstance(snap, dict):
            self._send_json(400, {"error": "body must be a JSON object"})
            return

        params = parse_qs(parsed.query)
        force = _truthy((params.get("force") or [None])[0])
        dry_run = _truthy((params.get("dry_run") or [None])[0])
        push = _truthy((params.get("push") or ["false"])[0])  # default: local-only
        raw_tags = (params.get("tags") or [""])[0]
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] or None

        try:
            result = self.client.commit_snapshot(
                snap, dry_run=dry_run, force=force, push=push, tags=tags
            )
        except CommitError as exc:
            msg = str(exc)
            status = 409 if "critical risk" in msg else 400
            self._send_json(status, {"error": msg})
            return

        diff = result["diff"]
        self._send_json(200, {
            "ok": result["ok"],
            "filepath": result["filepath"],
            "dry_run": result["dry_run"],
            "blocking_risks": len(result["blocking_risks"]),
            "diff": {
                "summary": diff.summary(),
                "first_snapshot": diff.first_snapshot,
                "completed_tasks": diff.completed_tasks,
                "started_tasks": diff.started_tasks,
                "new_risks": diff.new_risks,
                "resolved_risks": diff.resolved_risks,
                "phase_from": diff.phase_from,
                "phase_to": diff.phase_to,
                "pct_from": diff.pct_from,
                "pct_to": diff.pct_to,
                "parent_st_h_link": diff.parent_st_h_link,
            },
        })

    # ---- GET /status and /health ----

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "repo": str(self.client.repo_path)})
            return
        if parsed.path != "/status":
            self._send_json(404, {"error": f"unknown endpoint: {parsed.path}"})
            return

        index_path = self.client.repo_path / "snapshots" / "index.json"
        if not index_path.exists():
            self._send_json(404, {"error": "no snapshot index"})
            return
        with index_path.open("r", encoding="utf-8") as f:
            index = json.load(f)
        entries = index.get("snapshots", [])
        if not entries:
            self._send_json(404, {"error": "no snapshots yet"})
            return
        latest = entries[-1]
        self._send_json(200, {
            "project": latest.get("project"),
            "ST_H": latest.get("ST_H"),
            "parent_ST_H": latest.get("parent_ST_H"),
            "phase": latest.get("phase"),
            "pct": latest.get("pct"),
            "file": latest.get("file"),
            "tags": latest.get("tags", []),
            "capsule_count": len(index.get("capsules", [])),
            "index_updated_at": index.get("updated_at"),
        })


class CairnServer:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        repo_path: str | Path = REPO_ROOT_DEFAULT,
        client: CairnClient | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client = client or CairnClient(repo_path=repo_path)

        handler_cls = type("_BoundHandler", (_Handler,), {"client": self.client})
        self._server = HTTPServer((host, port), handler_cls)
        self._thread: threading.Thread | None = None
        # Resolve the actual bound port (useful when port=0 for tests)
        self.port = self._server.server_address[1]

    def serve_forever(self) -> None:
        print(f"[*] Cairn IPC server listening on http://{self.host}:{self.port}")
        self._server.serve_forever()

    def start_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()
        self._thread = t
        return t

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Cairn IPC server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT_DEFAULT)
    args = parser.parse_args()

    server = CairnServer(host=args.host, port=args.port, repo_path=args.repo)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] shutting down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
