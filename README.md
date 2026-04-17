# Cairn

**Protocol:** CAIRN_V1
**Status:** Foundation complete — ready for deployment

Cairn is a deterministic, agent-agnostic state-serialisation protocol for AI-driven development. It serialises a project's logical state into a structured JSON snapshot, commits it to a git-backed ledger, and provides tooling to rehydrate that state into a new agent session with high fidelity.

Evolved from **LTM-Bridge** (snapshot / client / git pipeline) and **SCHEMA_V5** (capsule architecture, D_TASK batching, multi-agent handover). Both are retained under `old_code/` as reference material only.

## What Cairn Is Not

- Not a memory system that makes LLMs deterministic
- Not a naive token-reduction tool — it improves signal density, not raw token count
- Does not guarantee "zero-loss fidelity" — the honest claim is **structured rehydration with high fidelity**

## Install

```bash
pip install git+https://github.com/Darian-Frey/Cairn.git
# or, inside a project:
uv pip install git+https://github.com/Darian-Frey/Cairn.git
```

Dev install (for hacking on Cairn itself):

```bash
git clone https://github.com/Darian-Frey/Cairn.git
cd Cairn
uv venv && uv pip install -e ".[dev]"
```

## Quick start — drop Cairn into a project

```bash
cd path/to/your/project
cairn init <project-name> --with-claude-md --with-bundled-projects
```

Creates `snapshots/`, `capsules/`, `schemas/projects/`, seeds `index.json` / `registry.json`, appends a Cairn block to `.gitignore`, and copies the bundled project fragments (coda, terra-siege, nyx-audio, lumina).

Subsequent operations:

```bash
cairn validate <snapshot.json>          # schema check
cairn integrity <snapshot.json>         # ST_H verification
cairn audit <snapshot.json>             # full audit (validate + orphans + risks + tokens)
cairn commit <snapshot.json> --no-push  # commit locally, skip git push
cairn diff <old.json> <new.json>        # semantic diff
cairn prune -n 20                       # keep last 20 snapshots (capsules untouched)
cairn certify <snapshot.json> CAP-001   # seal as an immutable milestone
cairn export <snapshot.json> -o CLAUDE.md   # handoff Markdown for a fresh session
cairn import CLAUDE.md -o restored.json     # reverse path
cairn serve                             # local IPC server on 127.0.0.1:7331
```

## Library use

```python
from cairn import CairnClient, CairnScanner, compute_st_h

client = CairnClient(repo_path=".")
snap = {...}  # CAIRN_V1 snapshot
snap["ST_H"] = compute_st_h(snap)
result = client.commit_snapshot(snap, push=False)
```

## Core Concepts

- **Snapshot** — JSON document capturing project state (`ST_H`, `UV`, `RSK`, `DEP`, `CTX`, `OBJ`, `PAY`, etc.)
- **ST_H** — first 16 hex chars of SHA-256 over the canonical snapshot (with `ST_H` excluded)
- **Capsule** — sealed, immutable milestone snapshot; never pruned or overwritten
- **D_TASK** — atomic batch of UV tasks that succeed or fail together
- **RSK severity** — `critical` (blocking) / `high` / `medium` / `info`
- **Diff chain** — each snapshot references its parent via `parent_ST_H`
- **Project extensions** — per-project schema fragments under `schemas/projects/<name>.json` constrain a `PROJ_EXT` payload

See [CLAUDE.md](CLAUDE.md) for the full protocol and [specs/rehydration.md](specs/rehydration.md) for the normative rehydration algorithm.

## Repository Layout

```text
Cairn/
├── cairn/                   # installable package
│   ├── scanner.py           # schema / integrity / capsule certification
│   ├── client.py            # commit / diff / prune
│   ├── server.py            # IPC HTTP server
│   ├── claude_md.py         # CLAUDE.md export / import
│   ├── init.py              # `cairn init` project seeder
│   ├── cli.py               # unified `cairn` CLI dispatcher
│   └── schemas/             # bundled CAIRN_V1 schema + project fragments
├── tests/                   # pytest suite (150 tests)
├── specs/                   # protocol specs (rehydration.md)
├── snapshots/               # Cairn's own dogfood snapshots
├── capsules/                # Cairn's own capsules (CAP-001 sealed)
├── old_code/                # LTM-Bridge + SCHEMA_V5 (reference only — gitignored)
└── pyproject.toml           # package metadata + entry point
```

## Lead

Shane Hartley — [github.com/Darian-Frey](https://github.com/Darian-Frey)
