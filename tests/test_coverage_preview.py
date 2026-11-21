"""Renderer and worker preview protocol: headless, without captures or scanner."""
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np

from tribuutscan import coverage_preview as cp, livescan, merger


def decode(payload):
    header, body = payload.split(b"\n", 1)
    _, w, h, _ = header.split()
    return np.frombuffer(body, dtype=np.uint8).reshape(int(h), int(w), 3)


def test_sparse_coverage_projects_in_two_colours_and_leaves_unseen_black():
    xyz = np.array([[-20, -10, 0], [20, 10, 0]], dtype=np.float32)
    seen = np.array([1, 3], dtype=np.uint16)
    pixels = decode(cp.render(xyz, seen, 80, 60, 0, 0, 1))
    assert all((pixels == color).all(axis=2).any() for color in (cp.AMBER, cp.BLUE, cp.BACKGROUND))
    assert (pixels == cp.BACKGROUND).all(axis=2).sum() >= 80 * 60 - 8
    rotated = decode(cp.render(xyz, seen, 80, 60, 0.8, 0, 1))
    assert not np.array_equal(rotated, pixels)
    assert (decode(cp.render(xyz[:0], seen[:0], 80, 60, 0, 0, 1)) == cp.BACKGROUND).all()


def test_preview_thread_coalesces_and_invalidates_old_piece_renders():
    renderer = cp.PreviewRenderer(60, 40)
    xyz = np.array([[1, 2, 3]], dtype=np.float32)
    seen = np.array([2], dtype=np.uint16)
    try:
        renderer.offer(xyz, seen, 0, 0, 1)
        renderer.reset_frame()
        serial = renderer.offer(xyz, seen, 0.5, 0, 1)
        end = time.monotonic() + 1
        while (renderer.latest is None or renderer.latest[0] != serial) and time.monotonic() < end:
            time.sleep(0.001)
        assert renderer.latest is not None and renderer.latest[0] == serial
        assert (decode(renderer.latest[1]) == cp.AMBER).all(axis=2).any()
    finally:
        renderer.close()
        renderer.thread.join(timeout=1)
    assert not renderer.thread.is_alive()


def test_native_sample_keeps_primary_while_secondary_tracks_and_prevents_cross_take_cache():
    xyz = np.arange(cp.CANDIDATES * 6, dtype=np.float64).reshape(-1, 3)
    samples = np.ones(len(xyz), np.int64)
    cloud = SimpleNamespace(xyz=xyz, n=samples, seen=samples)
    scanner = SimpleNamespace(pieces=[{}], stats={"merged": 1}, cloud=cloud, cloud_lock=threading.Lock(),
                              output_mask=lambda xyz, **_: np.ones(len(xyz), bool))
    source = livescan.LiveSource()
    source.scanner, source.phase = scanner, "scanning"
    one, x, seen = source.native_coverage(1, None)
    assert len(x) == len(seen) == cp.MAX_POINTS and x.dtype == np.float32 and seen.dtype == np.uint16
    assert source.native_coverage(1, one) == (one, None, None)
    primary, primary_counts = x, seen
    source.remember_piece(1, (primary, np.zeros_like(primary, dtype=np.uint8), primary_counts))
    scanner.pieces.append({})
    scanner.cloud = SimpleNamespace(xyz=xyz + 2, n=samples, seen=samples)
    _, x, _ = source.native_coverage(1, one)
    assert np.allclose(x, primary, rtol=1e-6)  # archived millimetre data, never the new active piece
    _, x, _ = source.native_coverage(2, None)
    assert np.allclose(x, primary + 2)
    source.phase = "paused"
    source.paused_points = (np.array([[4, 5, 6]]), np.zeros((1, 3)), np.array([3]))
    _, x, seen = source.native_coverage(2, None)
    assert x.tolist() == [[4, 5, 6]] and seen.tolist() == [3]
    source.reset()
    reset_version, x, seen = source.native_coverage(2, None)
    assert not len(x) and not len(seen)
    assert source.native_coverage(2, reset_version) == (reset_version, None, None)
    source.scan_id = "new-take"
    source.scanner, source.phase = scanner, "scanning"
    version, x, _ = source.native_coverage(1, one)
    assert version.startswith("new-take-") and not len(x)  # old archived primary cleared


def test_merge_only_queues_one_sample_and_ignores_a_previous_piece_reply():
    merge = merger.MergeProcess.__new__(merger.MergeProcess)
    merge.jobs, merge.replies = queue.Queue(), queue.Queue()
    merge.proc = SimpleNamespace(is_alive=lambda: True)
    merge.coverage_cache, merge.coverage_wanted, merge.coverage_epoch, merge.coverage_serial = None, False, 0, 0
    assert merge.ask_coverage() is True and merge.ask_coverage() is False
    assert merge.jobs.get() == ("coverage", 0)
    merge.reset()
    assert merge.jobs.get() == ("reset",)
    assert merge.ask_coverage() is True and merge.jobs.get() == ("coverage", 1)
    merge.replies.put(("coverage", 0, "old"))
    merge.poll()
    assert merge.coverage_cache is None and merge.coverage_wanted
    merge.replies.put(("coverage", 1, "new"))
    merge.poll()
    assert merge.coverage_cache == "new" and not merge.coverage_wanted
