# CAIRN_V1 Rehydration Specification

**Protocol:** CAIRN_V1
**Status:** Normative
**Last updated:** 2026-04-17

Specifies the algorithm by which an agent ingests a CAIRN_V1 snapshot and reconstructs project state with high fidelity. Formalises the four-step sequence introduced in [CLAUDE.md §3.2](../CLAUDE.md).

Rehydration is **structured** — it establishes a verified starting state and a prioritised action queue. It is not deterministic replay: agent behaviour during step 4 (UV execution) is not constrained by this protocol.

---

## 1. Inputs

A rehydration pass requires:

| Input | Source | Required |
| --- | --- | --- |
| Snapshot | CAIRN_V1 JSON document | yes |
| Base schema | [schemas/cairn_v1.json](../schemas/cairn_v1.json) | yes |
| Project extension schema | [schemas/projects/](../schemas/projects/)`<project>.json` | optional |
| Environment description | Agent-provided (installed tooling, versions, accessible services) | yes — for step 2 |
| Operator acknowledgement channel | Agent-provided — may be interactive or logged | yes — for step 3 |

The snapshot must conform to the structure defined by [cairn_v1.json](../schemas/cairn_v1.json) (see §3.1 in CLAUDE.md for field semantics).

---

## 2. Algorithm Overview

```
rehydrate(snapshot, env):
    1. verify_integrity(snapshot)      # ST_H check → HALT on mismatch
    2. check_dependencies(snapshot, env) # DEP satisfaction → HALT on unsatisfied
    3. audit_risks(snapshot)             # RSK surface → HALT on unacknowledged blocking
    4. execute_vectors(snapshot)         # UV work in priority order
```

Each step is a gate. A failure at step *n* prevents step *n+1* from beginning. Steps 1 and 2 are non-negotiable and cannot be overridden. Steps 3 and 4 admit explicit operator override (see §5 and §6).

---

## 3. Step 1 — ST_H Verification

### 3.1 Purpose

Confirm the snapshot has not been corrupted or tampered with in transit.

### 3.2 Procedure

1. Produce a copy of the snapshot with the `ST_H` field removed.
2. Serialise that copy as canonical JSON: keys sorted lexicographically, minimal separators (`,` and `:`), no surrounding whitespace.
3. Compute `SHA-256` over the UTF-8 encoded canonical JSON.
4. Take the first 16 hexadecimal characters of the digest and uppercase them.
5. Compare the result to the snapshot's declared `ST_H`.

Reference implementation: [`compute_st_h()`](../tools/cairn_scanner.py) in `tools/cairn_scanner.py`. The same function is used by both the scanner and the client so producers and consumers compute identical hashes.

### 3.3 Exit

- **Match:** proceed to step 2.
- **Mismatch:** halt rehydration. Report the expected and actual `ST_H`. Do not attempt recovery — a mismatch indicates corruption and any dependent decisions based on snapshot content are suspect.
- **Missing `ST_H`:** halt. An unhashed snapshot is not a CAIRN_V1 artefact.

---

## 4. Step 2 — Dependency Check

### 4.1 Purpose

Confirm the rehydrating environment can satisfy the dependencies the snapshot declares in `DEP[]` before any work begins. Dependencies describe environmental expectations, not in-project code. Circular dependency detection is performed by the scanner at authorship time and need not be repeated at rehydration.

### 4.2 Procedure

For each entry `d` in `DEP`:

1. Inspect the environment for a component matching `d.comp`.
2. Confirm the installed version satisfies `d.ver`. Version-range semantics follow the declared component's conventions (e.g. semver-compatible strings for language runtimes; pinned versions for artefacts). The protocol does not prescribe a single version-range grammar.
3. Confirm the component fulfils the declared `d.role` in whatever sense applies to the project.
4. If `d.requires[]` is present, recursively confirm those dependencies before marking `d` satisfied.

### 4.3 Orphan Detection

For each UV task `t` with a non-empty `t.dep_refs[]`, confirm every referenced id exists in `DEP`. An unsatisfied orphan is a structural defect in the snapshot itself (not an environment gap) and halts rehydration regardless of whether the dependency *could* be provided.

### 4.4 Exit

- **All satisfied:** proceed to step 3.
- **Any `DEP` unsatisfied:** halt. Report which entries failed and the expected vs. observed state. Operators may resolve the gap and re-run rehydration — this step has no override.
- **Orphan present:** halt. Correcting the snapshot is the only remedy.

---

## 5. Step 3 — Risk Audit

### 5.1 Purpose

Surface all known risks before work resumes, and gate on critical blockers.

### 5.2 Severity Semantics

| Level | Halts step 4? | Notes |
| --- | --- | --- |
| `critical` | Yes, if `blocking: true` | Must be explicitly acknowledged to proceed. |
| `high` | No | Must be surfaced to the operator before step 4 begins. |
| `medium` | No | Logged and tracked; no gate. |
| `info` | No | Informational. |

`blocking` is orthogonal to `level`: a critical risk may be non-blocking (e.g. historical record of a resolved incident). The gate is `level == "critical" AND blocking == true`.

### 5.3 Procedure

1. Group `RSK[]` by `level` (see [`CairnScanner.audit_risks`](../tools/cairn_scanner.py)).
2. Emit all `critical` and `high` risks to the operator.
3. For each critical-blocking risk, require explicit acknowledgement before continuing. The acknowledgement channel is agent-specific (interactive prompt, signed override record, config flag) but must be auditable.
4. Record all acknowledgements so a subsequent snapshot can reference them.

### 5.4 Exit

- **No critical-blocking risks, or all acknowledged:** proceed to step 4.
- **Unacknowledged critical-blocking risk:** halt. An explicit `force=true` from the operator may override the gate — the override is logged and the risk remains visible in the next snapshot's `RSK[]` until resolved at source.

The client's commit pipeline already enforces the inverse of this gate at authorship time: a snapshot with a critical-blocking risk cannot be committed without `--force`. See [`CairnClient.commit_snapshot`](../src/cairn_client.py).

---

## 6. Step 4 — UV Execution

### 6.1 Purpose

Work through `UV[]` (Unresolved Vectors) in a prioritised, batch-aware order.

### 6.2 Ordering Rules

1. `UV[]` is a list of **D_TASK batches**. Each batch has `d_task`, `desc`, `tasks[]`, and `blocking`.
2. Within a batch, tasks are ordered by `priority` (`p1` before `p2` before `p3`). Ties retain source order.
3. Batches are processed in source order unless a batch carries `blocking: true`, in which case no subsequent batch may begin until that batch is fully complete.
4. A batch is **complete** when every task in `tasks[]` is either finished or explicitly cancelled. Partial completion does not unblock downstream batches.

### 6.3 Task-Level Dependency Check

Before starting task `t`:

1. For each id in `t.dep_refs[]`, confirm the corresponding `DEP` entry is still satisfied (step 2 was a snapshot-wide gate; this is a just-in-time recheck that matters if the environment changes during rehydration).
2. If any `dep_ref` has become unsatisfied, skip the task and flag it in the next snapshot's `RSK[]` at `medium` or higher.

### 6.4 Project Extensions

If a fragment exists at [schemas/projects/](../schemas/projects/)`<project>.json`, the `PROJ_EXT` payload has been shape-validated during step 1 and its contents are assumed well-formed. The agent may consult `PROJ_EXT` for project-specific context during UV execution (e.g. a CODA snapshot's `pipeline_status` determines whether eigenvalue work should continue or restart).

### 6.5 Exit

Step 4 has no single exit point — it runs until:
- all batches complete, or
- the operator halts the session (normal stop), or
- a newly-discovered critical-blocking risk is appended to `RSK[]`, which loops the agent back to step 3 for the next snapshot.

Every non-trivial change to state during step 4 should produce a new snapshot whose `parent_ST_H` points at the one just rehydrated. See §7.

---

## 7. Diff Chain

Rehydration is stateless in the sense that each pass is independent, but snapshots form a linked chain via `parent_ST_H`. A rehydrating agent should:

1. Record the rehydrated snapshot's `ST_H` as its session's parent.
2. On the next commit, set `parent_ST_H` to that recorded value (the client does this automatically when `commit_snapshot()` is called sequentially).
3. Never rewrite or discard a committed snapshot — the chain is append-only. Pruning removes old snapshots from local storage but does not break the chain's logical integrity (older entries persist in git history regardless).

Capsules (see §8) are the sole exception: they are immutable once sealed and may exist anywhere in the chain as fixed waypoints.

---

## 8. Capsule Rehydration

A capsule (`capsule: true`, `capsule_id: CAP-NNN`) is a sealed milestone snapshot. The rehydration algorithm is unchanged — capsules must pass steps 1–3 exactly like regular snapshots. Differences are operational rather than algorithmic:

1. Step 1's `ST_H` verification is doubly critical: a tampered capsule invalidates the milestone's provenance.
2. A capsule's `UV[]` may legitimately be empty (milestones often represent completion rather than a work plan). An empty `UV[]` makes step 4 a no-op.
3. Capsules are never pruned. An agent may rehydrate a historical capsule to reproduce a prior milestone's context without fear that the file has been rewritten.

Cross-reference: [`CairnScanner.certify_capsule`](../tools/cairn_scanner.py), [capsules/registry.json](../capsules/registry.json).

---

## 9. Failure Modes

| Failure | Detected at | Remedy |
| --- | --- | --- |
| `ST_H` mismatch | Step 1 | Reject snapshot; request re-transmission from the producer. |
| Missing required field | Step 1 (schema) | Reject; author must fix upstream. |
| `PROJ_EXT` shape invalid | Step 1 (project fragment) | Reject; fragment is authoritative. |
| Unsatisfied `DEP` | Step 2 | Install / provision the missing component; re-run rehydration. |
| Orphan `dep_ref` | Step 2 | Repair the snapshot; no override exists. |
| Unacknowledged blocking critical risk | Step 3 | Acknowledge interactively, or override with explicit `force=true` (logged). |
| Task-level dep_ref unsatisfied at start time | Step 4 | Skip task; raise a new risk in the next snapshot. |

---

## 10. Non-goals

- Rehydration does not guarantee identical agent behaviour. LLMs are non-deterministic in general, and this protocol makes no attempt to constrain model sampling.
- Rehydration does not compress tokens in the naive sense. The protocol's value is increased signal density per token, not raw token count reduction.
- Rehydration does not validate *the truth of assertions* inside free-text fields (`CTX`, `OBJ`, `RSK.desc`, etc.). Those are operator-readable prose, not machine-verifiable claims.

---

## 11. Cross-references

| Topic | Location |
| --- | --- |
| Snapshot field semantics | [CLAUDE.md §3.1](../CLAUDE.md) |
| RSK severity levels | [CLAUDE.md §3.3](../CLAUDE.md) |
| D_TASK batching rules | [CLAUDE.md §3.6](../CLAUDE.md) |
| Capsule contract | [CLAUDE.md §3.5](../CLAUDE.md) |
| ST_H hashing convention | [CLAUDE.md §4](../CLAUDE.md), [compute_st_h()](../tools/cairn_scanner.py) |
| Scanner reference implementation | [tools/cairn_scanner.py](../tools/cairn_scanner.py) |
| Client reference implementation | [src/cairn_client.py](../src/cairn_client.py) |
| Project extensions | [schemas/projects/](../schemas/projects/) |
