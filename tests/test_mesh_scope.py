import subprocess
from unittest.mock import patch

from tribuutscan import viewer


def test_mesh_scope_requires_reachable_user_manager():
    log = []
    with patch.object(viewer.shutil, "which", return_value="/usr/bin/systemd-run"), \
         patch.object(viewer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "systemctl")):
        assert viewer.mesh_scope(log.append) == []
        assert "without the separate memory limit" in log[-1]
    with patch.object(viewer.shutil, "which", return_value="/usr/bin/systemd-run"), \
         patch.object(viewer.subprocess, "run"):
        assert viewer.mesh_scope(log.append)[-1] == "MemoryMax=8G"
    with patch.object(viewer.shutil, "which", return_value=None), patch.object(viewer.subprocess, "run") as run:
        assert viewer.mesh_scope(log.append) == []
        run.assert_not_called()
