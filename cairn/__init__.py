"""Cairn — deterministic state-serialisation protocol for AI-driven development."""

from cairn.claude_md import markdown_to_snapshot, snapshot_to_markdown
from cairn.client import CairnClient, CommitError, DiffReport
from cairn.init import init_project
from cairn.scanner import CairnScanner, CapsuleError, compute_st_h
from cairn.server import CairnServer

__version__ = "0.1.0"

__all__ = [
    "CairnScanner",
    "CairnClient",
    "CairnServer",
    "CapsuleError",
    "CommitError",
    "DiffReport",
    "compute_st_h",
    "snapshot_to_markdown",
    "markdown_to_snapshot",
    "init_project",
    "__version__",
]
