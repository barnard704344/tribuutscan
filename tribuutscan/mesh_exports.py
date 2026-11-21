"""Retire generated meshes when their scan changes; leave source capture files untouched."""

import json
from pathlib import Path
from tempfile import mkdtemp

NAMES = ("mesh.ply", "mesh.stl", "solid.ply", "mesh.json")


def retire(directory):
    """Move current exports out of the active paths without deleting previous results.

    Hidden history stays with the scan, even if generating replacement files fails.
    """
    directory = Path(directory)
    current = [directory / name for name in NAMES if (directory / name).is_file()]
    if not current:
        return
    history = directory / ".mesh-history"
    history.mkdir(exist_ok=True)
    archive = Path(mkdtemp(prefix="export-", dir=history))
    for path in current:
        path.replace(archive / path.name)


def stl_state(directory):
    """Whether the current STL was reported closed, open, or has no reliable closure report."""
    try:
        report = json.loads((Path(directory) / "mesh.json").read_text())
        # No solid in a successful full export means closing was skipped or left open.
        return "closed" if report.get("solid", {}).get("edges_closed") else "open"
    except (OSError, ValueError, AttributeError):
        return "unknown"
