"""CAIRN_V1 ↔ CLAUDE.md converter.

Export emits a human-readable Markdown file suitable for handoff to a Claude Code
session; the HTML-comment header preserves metadata fields (project, ST_H,
parent_ST_H, PAY, CON, ALN, BC) that have no natural Markdown representation.

Import parses the Markdown back into a CAIRN_V1 snapshot. ST_H is always
recomputed — an imported snapshot reflects the Markdown's current state, not
the original snapshot's identity.
"""

from __future__ import annotations

import json
import re
from typing import Any

from cairn.scanner import compute_st_h


META_OPEN = "<!-- cairn-v1"
META_CLOSE = "-->"


def snapshot_to_markdown(snap: dict) -> str:
    meta = {
        "project": snap["project"],
        "ST_H": snap["ST_H"],
    }
    if "parent_ST_H" in snap:
        meta["parent_ST_H"] = snap["parent_ST_H"]
    if "capsule" in snap:
        meta["capsule"] = snap["capsule"]
    if "capsule_id" in snap:
        meta["capsule_id"] = snap["capsule_id"]
    meta["PAY"] = snap.get("PAY", {"pct": 0})
    for extra in ("ALN", "CON", "BC"):
        if extra in snap:
            meta[extra] = snap[extra]

    parts: list[str] = []
    parts.append(META_OPEN)
    parts.append(json.dumps(meta, indent=2, sort_keys=True))
    parts.append(META_CLOSE)
    parts.append("")

    phase = snap.get("PAY", {}).get("phase") or ""
    title = f"{snap['project']}"
    if phase:
        title = f"{title} — {phase}"
    parts.append(f"# {title}")
    parts.append("")

    pct = snap.get("PAY", {}).get("pct", 0)
    parts.append(f"**Progress:** {pct}%")
    parts.append("")

    parts.append("## Objective")
    parts.append("")
    parts.append(snap.get("OBJ", "").strip())
    parts.append("")

    parts.append("## Project Overview")
    parts.append("")
    parts.append(snap.get("CTX", "").strip())
    parts.append("")

    parts.append("## Unresolved Vectors")
    parts.append("")
    uv = snap.get("UV", []) or []
    if not uv:
        parts.append("_No pending work._")
    else:
        for batch in uv:
            blocking = "blocking" if batch.get("blocking") else "non-blocking"
            parts.append(f"### {batch['d_task']} — {batch.get('desc', '')} ({blocking})")
            parts.append("")
            for task in batch.get("tasks", []) or []:
                pri = task.get("priority", "p?")
                line = f"- [{pri}] `{task['id']}` — {task.get('obj', '')}"
                refs = task.get("dep_refs") or []
                if refs:
                    line += f" _(deps: {', '.join(refs)})_"
                parts.append(line)
            parts.append("")

    parts.append("## Risk Register")
    parts.append("")
    rsk = snap.get("RSK", []) or []
    if not rsk:
        parts.append("_No risks logged._")
    else:
        parts.append("| ID | Level | Blocking | Description |")
        parts.append("| --- | --- | --- | --- |")
        for r in rsk:
            blocking = "yes" if r.get("blocking") else "no"
            desc = r.get("desc", "").replace("|", "\\|").replace("\n", " ")
            parts.append(f"| `{r['id']}` | {r['level']} | {blocking} | {desc} |")
    parts.append("")

    parts.append("## Environment")
    parts.append("")
    dep = snap.get("DEP", []) or []
    if not dep:
        parts.append("_No dependencies declared._")
    else:
        for d in dep:
            line = f"- `{d['id']}` — {d['comp']} @ `{d['ver']}` ({d['role']})"
            requires = d.get("requires") or []
            if requires:
                line += f" _(requires: {', '.join(requires)})_"
            parts.append(line)
    parts.append("")

    parts.append("## External Resources")
    parts.append("")
    mr = snap.get("MR", []) or []
    if not mr:
        parts.append("_No external resources linked._")
    else:
        for m in mr:
            line = f"- `{m['id']}` — {m['ref']}"
            if m.get("kind"):
                line += f" _({m['kind']})_"
            if m.get("desc"):
                line += f" — {m['desc']}"
            parts.append(line)
    parts.append("")

    return "\n".join(parts).rstrip() + "\n"


_META_RE = re.compile(
    re.escape(META_OPEN) + r"\s*(.*?)\s*" + re.escape(META_CLOSE), re.DOTALL
)
_SECTION_RE = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
_DTASK_RE = re.compile(
    r"^### +(DT-\d+)\s*—\s*(.*?)\s*\((blocking|non-blocking)\)\s*$", re.MULTILINE
)
_TASK_LINE_RE = re.compile(
    r"^- +\[(p[123])\]\s+`([^`]+)`\s+—\s+(.+?)(?:\s+_\(deps:\s*([^)]+?)\s*\)_)?\s*$"
)
_RSK_ROW_RE = re.compile(
    r"^\|\s*`([^`]+)`\s*\|\s*(critical|high|medium|info)\s*\|\s*(yes|no)\s*\|\s*(.+?)\s*\|\s*$"
)
_DEP_LINE_RE = re.compile(
    r"^- +`([^`]+)`\s+—\s+(.+?)\s+@\s+`([^`]+)`\s+\(([^)]+)\)"
    r"(?:\s+_\(requires:\s*([^)]+?)\s*\)_)?\s*$"
)
_MR_LINE_RE = re.compile(
    r"^- +`([^`]+)`\s+—\s+(.+?)(?:\s+_\(([^)]+)\)_)?(?:\s+—\s+(.+))?\s*$"
)


def markdown_to_snapshot(md: str) -> dict:
    meta_match = _META_RE.search(md)
    if not meta_match:
        raise ValueError(f"missing {META_OPEN} metadata header")
    meta = json.loads(meta_match.group(1))

    body = md[meta_match.end():]
    sections = _split_sections(body)

    snap: dict[str, Any] = {
        "project": meta["project"],
        "OBJ": sections.get("Objective", "").strip(),
        "CTX": sections.get("Project Overview", "").strip(),
        "UV": _parse_uv(sections.get("Unresolved Vectors", "")),
        "RSK": _parse_rsk(sections.get("Risk Register", "")),
        "DEP": _parse_dep(sections.get("Environment", "")),
        "MR": _parse_mr(sections.get("External Resources", "")),
        "PAY": meta.get("PAY", {"pct": 0}),
    }
    for extra in ("ALN", "CON", "BC"):
        if extra in meta:
            snap[extra] = meta[extra]
    if "parent_ST_H" in meta:
        snap["parent_ST_H"] = meta["parent_ST_H"]
    if meta.get("capsule"):
        snap["capsule"] = True
        if "capsule_id" in meta:
            snap["capsule_id"] = meta["capsule_id"]

    snap["ST_H"] = compute_st_h(snap)
    return snap


def _split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name] = body[start:end]
    return sections


def _parse_uv(text: str) -> list[dict]:
    if "_No pending work._" in text or not text.strip():
        return []
    batches: list[dict] = []
    matches = list(_DTASK_RE.finditer(text))
    for i, m in enumerate(matches):
        d_task, desc, blocking_word = m.group(1), m.group(2).strip(), m.group(3)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        batch_body = text[start:end]
        tasks: list[dict] = []
        for line in batch_body.splitlines():
            tm = _TASK_LINE_RE.match(line)
            if not tm:
                continue
            task = {"id": tm.group(2), "obj": tm.group(3).strip(), "priority": tm.group(1)}
            if tm.group(4):
                task["dep_refs"] = [r.strip() for r in tm.group(4).split(",") if r.strip()]
            tasks.append(task)
        batches.append({
            "d_task": d_task,
            "desc": desc,
            "blocking": blocking_word == "blocking",
            "tasks": tasks,
        })
    return batches


def _parse_rsk(text: str) -> list[dict]:
    if "_No risks logged._" in text:
        return []
    risks: list[dict] = []
    for line in text.splitlines():
        rm = _RSK_ROW_RE.match(line)
        if not rm:
            continue
        risks.append({
            "id": rm.group(1),
            "level": rm.group(2),
            "blocking": rm.group(3) == "yes",
            "desc": rm.group(4).replace("\\|", "|").strip(),
        })
    return risks


def _parse_dep(text: str) -> list[dict]:
    if "_No dependencies declared._" in text:
        return []
    deps: list[dict] = []
    for line in text.splitlines():
        dm = _DEP_LINE_RE.match(line)
        if not dm:
            continue
        dep = {
            "id": dm.group(1),
            "comp": dm.group(2).strip(),
            "ver": dm.group(3),
            "role": dm.group(4).strip(),
        }
        if dm.group(5):
            dep["requires"] = [r.strip() for r in dm.group(5).split(",") if r.strip()]
        deps.append(dep)
    return deps


def _parse_mr(text: str) -> list[dict]:
    if "_No external resources linked._" in text:
        return []
    resources: list[dict] = []
    for line in text.splitlines():
        mm = _MR_LINE_RE.match(line)
        if not mm:
            continue
        entry: dict[str, Any] = {"id": mm.group(1), "ref": mm.group(2).strip()}
        if mm.group(3):
            entry["kind"] = mm.group(3).strip()
        if mm.group(4):
            entry["desc"] = mm.group(4).strip()
        resources.append(entry)
    return resources
