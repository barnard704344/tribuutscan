"""Viewer help and launcher work without a desktop or scanner."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


REPO = Path(__file__).resolve().parents[1]


def test_viewer_help_does_not_open_a_display():
    env = {key: value for key, value in os.environ.items() if key not in ("DISPLAY", "WAYLAND_DISPLAY")}
    proc = subprocess.run([sys.executable, "-m", "tribuutscan.viewer", "--help"],
                          cwd=REPO, env=env, capture_output=True, text=True, check=True)
    assert "--force-cpu" in proc.stdout


def test_launcher_finds_its_checkout_from_another_directory():
    with TemporaryDirectory(prefix="scan launcher ") as tmp:
        work = Path(tmp)
        shim = work / "python3"
        shim.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
        shim.chmod(0o755)
        env = dict(os.environ, PATH=f"{work}:{os.environ['PATH']}", HOME=tmp)
        proc = subprocess.run([str(REPO / "tribuutscan-viewer.sh"), "--help"], cwd=work,
                              env=env, capture_output=True, text=True, check=True)
        assert proc.stdout.splitlines() == [str(REPO), "-m", "tribuutscan.viewer", "--help"]
