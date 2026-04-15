# Cairn

**Protocol:** CAIRN_V1
**Status:** Early development — foundation phase

Cairn is a deterministic, agent-agnostic state serialisation protocol for AI-driven development. It serialises a project's logical state into a structured JSON snapshot, commits it to a git-backed ledger, and provides tooling to rehydrate that state into a new agent session with high fidelity.

It is a direct evolution of two prior projects: **LTM-Bridge** (snapshot/client/git pipeline) and **SCHEMA_V5** (capsule architecture, D_TASK batching, multi-agent handover). Both are retained under `old_code/` as reference material only.

## What Cairn Is Not

- Not a memory system that makes LLMs deterministic
- Not a naive token-reduction tool — it improves signal density, not raw token count
- Does not guarantee "zero-loss fidelity" — the honest claim is **structured rehydration with high fidelity**

## Repository Layout

```
cairn/
├── src/          # Cairn client + IPC server
├── tools/        # cairn_scanner.py — schema validator / integrity auditor
├── schemas/      # cairn_v1.json — canonical schema
├── snapshots/    # Live project state snapshots + index.json
├── capsules/     # Sealed, immutable milestone snapshots + registry.json
├── specs/        # Protocol specifications
├── tests/        # pytest suite with fixtures
└── old_code/     # LTM-Bridge + SCHEMA_V5 (reference only — gitignored)
```

## Core Concepts

- **Snapshot** — JSON document capturing project state (`ST_H`, `UV`, `RSK`, `DEP`, `CTX`, `OBJ`, etc.)
- **ST_H** — first 16 hex chars of SHA-256 over the canonical snapshot (with `ST_H` excluded)
- **Capsule** — sealed, immutable milestone snapshot; never pruned or overwritten
- **D_TASK** — atomic batch of UV tasks that succeed or fail together
- **RSK severity** — `critical` (blocking) / `high` / `medium` / `info`
- **Diff chain** — each snapshot references its parent via `parent_ST_H`

See [CLAUDE.md](CLAUDE.md) for the full protocol specification and development priorities.

## Development Priority

1. `tools/cairn_scanner.py` — foundation
2. `schemas/cairn_v1.json` — JSON Schema definition
3. Test suite using `old_code/` fixtures
4. `snapshots/index.json` wiring
5. `src/cairn_client.py` — evolved client
6. CLAUDE.md export / import
7. `src/cairn_server.py` — IPC server

## Lead

Shane Hartley — [github.com/Darian-Frey](https://github.com/Darian-Frey)
