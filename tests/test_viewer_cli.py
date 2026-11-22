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
        assert proc.stdout.splitlines() == [str(REPO), "-m", "tribuutscan.webapp", "--help"]



def test_repeat_launch_preserves_explicit_bind_address_and_private_session():
    from unittest.mock import Mock,patch
    from tribuutscan import webapp
    with TemporaryDirectory() as tmp,patch.object(webapp.core,'LOCK',Path(tmp)/'.viewer.lock'):
        token='test-token-for-a-private-session'
        webapp.save_browser_session(8877,token,'192.0.2.10')
        assert ((Path(tmp)/'.webapp.json').stat().st_mode & 0o777) == 0o600
        response=Mock();response.headers={'Server':'triBUUTSCAN Python/3'}
        response.__enter__=lambda self:response;response.__exit__=lambda *args:None
        with patch.object(webapp,'urlopen',return_value=response) as fetch,patch.object(webapp.webbrowser,'open',return_value=True) as browser:
            assert webapp.reopen_browser()
            browser.assert_called_once_with('http://192.0.2.10:8877/#token='+token)
            assert fetch.call_args.args[0] == 'http://192.0.2.10:8877/'
