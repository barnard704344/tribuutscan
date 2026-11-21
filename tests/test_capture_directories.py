"""Captures sharing a clock second must never share a directory."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from tribuutscan import frames


STAMP = "20261121-120000"


def test_two_frame_saves_in_the_same_second_keep_both():
    values = {"color": np.zeros((2, 2, 3), dtype=np.uint8),
              "ir": np.zeros((2, 2), dtype=np.uint8),
              "depth": np.full((2, 2), 500, dtype=np.float32)}
    with TemporaryDirectory() as tmp, patch.object(frames.time, "strftime", return_value=STAMP):
        first = frames.save(values, root=tmp, extra={"take": 1})
        second = frames.save(values, root=tmp, extra={"take": 2})
        assert first.name == STAMP and first != second
        assert sorted(Path(tmp).iterdir()) == [first, second]
        assert json.loads((first / "info.json").read_text())["take"] == 1
        assert json.loads((second / "info.json").read_text())["take"] == 2


def test_recording_and_live_scan_stems_are_distinct_and_preserve_globs():
    with TemporaryDirectory() as tmp, patch.object(frames.time, "strftime", return_value=STAMP), \
         patch.object(frames, "free_gb", return_value=10):
        first = frames.Recorder(1600, root=tmp, streams=("depth",))
        first.files["depth"].write(b"first")
        first.close()
        second = frames.Recorder(1600, root=tmp, streams=("depth",))
        second.close()
        rec_dirs = sorted(Path(tmp).glob("*-rec"))
        assert rec_dirs == [first.dir, second.dir]
        assert first.dir.name == STAMP + "-rec"
        assert (first.dir / "depth.zlib").read_bytes() == b"first"
        live = [frames.capture_dir(tmp, "-scan", live=True) for _ in range(2)]
        replay = {rec.with_name(rec.name.removesuffix("-rec") + "-scan") for rec in rec_dirs}
        assert not replay.intersection(live)
        assert sorted(Path(tmp).glob("*-scan")) == live
