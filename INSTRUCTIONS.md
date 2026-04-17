# Cairn — Usage Instructions

Practical guide for using Cairn inside a project. For the protocol specification see [CLAUDE.md](CLAUDE.md); for the rehydration algorithm see [specs/rehydration.md](specs/rehydration.md).

---

## 1. Install

### Consumer install (dropping Cairn into an existing project)

```bash
uv pip install git+https://github.com/Darian-Frey/Cairn.git
# or
pip install git+https://github.com/Darian-Frey/Cairn.git
```

After install you have a `cairn` command available.

### Dev install (for hacking on Cairn itself)

```bash
git clone https://github.com/Darian-Frey/Cairn.git
cd Cairn
uv venv
uv pip install -e ".[dev]"
```

Or via the shipped requirements files (equivalent):

```bash
uv pip install -r requirements-dev.txt   # dev + runtime
uv pip install -r requirements.txt       # runtime only
```

---

## 2. Initialise a project

```bash
cd path/to/your/project
cairn init <project-name>
```

Common flags:

| Flag | Effect |
| --- | --- |
| `--target DIR` | Target directory (default: `.`) |
| `--with-claude-md` | Generate a starter `CLAUDE.md` stub |
| `--with-projects-dir` | Create an empty `schemas/projects/` for custom fragments |
| `--with-bundled-projects` | Also copy the four bundled fragments (coda / terra-siege / nyx-audio / lumina) into `schemas/projects/` |
| `--force` | Overwrite existing seeded files |

**Result:**

```text
your-project/
├── snapshots/
│   └── index.json            # empty — grows on each commit
├── capsules/
│   └── registry.json         # empty — grows on each certify
├── schemas/projects/         # only with --with-projects-dir
│   └── <project>.json        # only with --with-bundled-projects
├── CLAUDE.md                 # only with --with-claude-md
└── .gitignore                # Cairn block appended (idempotent)
```

`cairn init` is idempotent. Running it again on an existing tree skips anything already present (use `--force` to overwrite).

---

## 3. Author a snapshot

A CAIRN_V1 snapshot is a JSON object. Minimum required shape:

```json
{
  "project": "your-project",
  "CTX": "Human-readable state summary.",
  "OBJ": "Goal of this session or phase.",
  "UV": [
    {
      "d_task": "DT-01",
      "desc": "Batch description",
      "blocking": false,
      "tasks": [
        { "id": "uv_do_thing", "obj": "Do the thing", "priority": "p1" }
      ]
    }
  ],
  "RSK": [],
  "DEP": [],
  "PAY": { "phase": "Starting", "pct": 0 },
  "ST_H": "<computed>"
}
```

Compute `ST_H` via Python:

```python
from cairn import compute_st_h
snap["ST_H"] = compute_st_h(snap)
```

`ST_H` is the first 16 hex chars of SHA-256 over the canonical JSON (keys sorted, minimal separators) with the `ST_H` field excluded. See [specs/rehydration.md §3](specs/rehydration.md) for the precise algorithm.

### Snapshot field reference

| Field | Type | Required | Purpose |
| --- | --- | --- | --- |
| `project` | string | yes | Project identifier; must match a fragment filename if one exists |
| `ST_H` | string (16 hex) | yes | Integrity hash |
| `parent_ST_H` | string or null | no | Link to prior snapshot in the diff chain |
| `CTX` | string | yes | Project state narrative |
| `OBJ` | string | yes | Current objective |
| `UV[]` | array of D_TASK batches | yes | Unresolved vectors (work to do) |
| `RSK[]` | array of risks | yes | Known risks with severity / blocking |
| `DEP[]` | array of dependency objects | yes | Environment requirements |
| `PAY.phase` / `PAY.pct` | string / int (0–100) | `pct` required | Progress metadata |
| `CON[]` | array of strings | no | Hard execution constraints |
| `BC[]` | array of strings | no | Backward-compatibility constraints |
| `ALN[]` | array of strings | no | Active Logic Nodes |
| `MR[]` | array of resource objects | no | External resource pointers |
| `PROJ_EXT` | object | only if fragment requires | Project-specific payload (see §8) |
| `capsule` / `capsule_id` | bool / string | capsule-only | Set by `certify_capsule`, not by hand |

---

## 4. Validate a snapshot before using it

```bash
cairn validate my_snap.json        # schema check
cairn integrity my_snap.json       # ST_H verification
cairn audit my_snap.json           # full report: schema + integrity + orphans + cycles + tokens + risks
```

`audit` returns JSON; useful when scripting. Exit codes: `0` = clean, `1` = problem detected.

---

## 5. Commit a snapshot

```bash
cairn commit my_snap.json                 # validate + write + index + git-sync
cairn commit my_snap.json --no-push       # skip git push (keep local)
cairn commit my_snap.json --dry-run       # everything except writing / git
cairn commit my_snap.json --force         # override a blocking critical RSK
cairn commit my_snap.json --tag foundation --tag milestone
```

**What `commit` does, in order:**

1. Schema-validate (base + project fragment if one exists)
2. Verify `ST_H` matches canonical hash
3. Escalate blocking critical risks — halts unless `--force` is passed
4. Compute a diff against the previous snapshot
5. Write to `snapshots/<project>_<timestamp>.json`
6. Update `snapshots/index.json`
7. `git add` / `git commit` / `git push` (skip with `--no-push`)

If step 1, 2, or 3 fails, `commit` exits non-zero without touching disk or git.

### Library equivalent

```python
from cairn import CairnClient, compute_st_h

client = CairnClient(repo_path=".")
snap["ST_H"] = compute_st_h(snap)
result = client.commit_snapshot(snap, push=False, tags=["foundation"])
# result["ok"], result["filepath"], result["diff"].summary()
```

---

## 6. Diff, prune, export

```bash
cairn diff old.json new.json              # semantic diff
cairn prune -n 20                         # keep last 20 snapshots
cairn prune -n 20 --dry-run               # preview what would be deleted
```

`prune` never touches `capsules/` or any file with `capsule: true`, no matter where it sits.

### CLAUDE.md export / import

```bash
cairn export my_snap.json -o CLAUDE.md        # snapshot → handoff Markdown
cairn import CLAUDE.md -o restored.json       # Markdown → snapshot (ST_H recomputed)
```

Workflow:

1. At session end, `cairn export <latest snapshot> -o CLAUDE.md`
2. In the next Claude Code session, Claude reads CLAUDE.md at startup and has full context
3. If Claude (or you) edits CLAUDE.md during the session, `cairn import CLAUDE.md -o new.json` reconstructs a snapshot from the edited state — `ST_H` is recomputed, so the new snapshot reflects the edits, not the original identity

Round-trip is byte-faithful: an unedited export → import → export cycle produces identical `ST_H`.

---

## 7. Seal a capsule (milestone)

```bash
cairn certify my_snap.json CAP-001
```

Certification:

1. Validates and verifies the source snapshot
2. Adds `capsule: true` + `capsule_id` and recomputes `ST_H`
3. Writes to `capsules/CAP-001.json`
4. Appends a metadata record to `capsules/registry.json`
5. Adds a navigation entry to `snapshots/index.json.capsules[]`

**Capsules are immutable.** `prune` never touches them. Re-certifying the same ID raises an error — to supersede a milestone, use a new ID (`CAP-002`, `CAP-003`, …).

---

## 8. Project-specific extensions (`PROJ_EXT`)

When your project's state includes data that doesn't fit the base schema (e.g. eigenvalue results, build phases, module registries), use a project fragment.

### Option A — use a bundled fragment

If your project is `coda`, `terra-siege`, `nyx-audio`, or `lumina`, run:

```bash
cairn init <project> --with-bundled-projects
```

This drops the fragment into `schemas/projects/<project>.json`. Then include a matching `PROJ_EXT` in every snapshot:

```json
{
  "project": "coda",
  "...": "...",
  "PROJ_EXT": {
    "pipeline_status": "running",
    "eigenvalues": [1.23, 4.56, 7.89],
    "sparc_galaxies_analysed": 175,
    "krylov_dim": 256
  }
}
```

The scanner auto-loads the fragment matching `snapshot["project"]` and validates `PROJ_EXT` against it. Invalid shapes are rejected with `[coda] PROJ_EXT/...` error paths.

### Option B — write your own fragment

Create `schemas/projects/<project>.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "<project> extension",
  "required": ["PROJ_EXT"],
  "properties": {
    "project": { "const": "<project>" },
    "PROJ_EXT": {
      "type": "object",
      "required": ["my_required_field"],
      "additionalProperties": false,
      "properties": {
        "my_required_field": { "type": "string" }
      }
    }
  }
}
```

Projects without a fragment keep `PROJ_EXT` free-form (or omit it entirely — it's not required by the base schema).

---

## 9. Local IPC server

Run a background HTTP server so the host agent can POST snapshots without spawning a CLI per call:

```bash
cairn serve                          # 127.0.0.1:7331
cairn serve --host 0.0.0.0 --port 9000
```

Endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/snapshot` | Full commit pipeline; body = snapshot JSON |
| `GET` | `/status` | Summary of latest-indexed snapshot |
| `GET` | `/health` | Liveness probe |

Query parameters on `POST /snapshot`: `force`, `dry_run`, `push` (default `false`), `tags` (comma-separated).

Status codes: `200` ok, `400` schema / integrity / bad body, `409` blocking critical risk, `404` unknown endpoint / no snapshots yet.

Server binds to `127.0.0.1` by default — intended for local IPC only, no auth.

---

## 10. End-to-end example

```bash
# 1. New project
mkdir ~/myproject && cd ~/myproject
git init -q
uv pip install git+https://github.com/Darian-Frey/Cairn.git
cairn init myproject --with-claude-md

# 2. Author first snapshot (in Python)
cat > first.py <<'PY'
from cairn import compute_st_h
import json

snap = {
    "project": "myproject",
    "parent_ST_H": None,
    "CTX": "Just spun up.",
    "OBJ": "Ship the MVP.",
    "UV": [{
        "d_task": "DT-01", "desc": "setup", "blocking": False,
        "tasks": [{"id": "uv_design", "obj": "Draft the API", "priority": "p1"}],
    }],
    "RSK": [], "DEP": [],
    "PAY": {"phase": "Start", "pct": 0},
}
snap["ST_H"] = compute_st_h(snap)
with open("first_snap.json", "w") as f:
    json.dump(snap, f, indent=2)
PY
python first.py

# 3. Commit locally
cairn commit first_snap.json --no-push --tag initial

# 4. Export as CLAUDE.md for the next Claude Code session
cairn export first_snap.json -o CLAUDE.md

# 5. When you hit a milestone, seal it
cairn certify first_snap.json CAP-001
```

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ST_H mismatch` on commit | Snapshot content was edited after `ST_H` was computed | Re-run `compute_st_h(snap)` and re-save |
| `Snapshot fails schema validation` | Missing required field or wrong type | Run `cairn audit <snap>` — the `schema_errors` list shows exact paths |
| `Commit blocked by N critical risk(s)` | Snapshot has a `critical + blocking: true` risk | Resolve the risk at source, OR pass `--force` (logged; risk stays visible) |
| `Capsule already exists` | Trying to certify a duplicate `CAP-NNN` | Use the next number — capsules are immutable by design |
| `indexed snapshot missing on disk` (from `cairn index`) | Index references a file that was deleted outside of `prune` | Run `prune` to let Cairn clean the index, or edit `snapshots/index.json` |
| IDE says `Cannot find module cairn.*` after editable install | Some checkers don't follow PEP 660 editable finders | `pyproject.toml` already has a `[tool.pyright]` block pointing at `cairn/`; reload the IDE window if diagnostics persist |

---

## 12. Further reading

- [CLAUDE.md](CLAUDE.md) — full protocol specification (field semantics, severity levels, capsule contract)
- [specs/rehydration.md](specs/rehydration.md) — normative rehydration algorithm
- [README.md](README.md) — project overview
- [ROADMAP.md](ROADMAP.md) — development history and status
