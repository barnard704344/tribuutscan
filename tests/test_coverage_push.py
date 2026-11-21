"""Simulation of coverage timing over accepted merges with fake queues and no scanner."""
import queue
import threading
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tribuutscan import livescan, merger, viewer


def test_idle_flush_delivers_accepted_merge_without_a_second_request_and_never_changes_piece():
    t = 5.0
    xyz = np.ones((4, 3), dtype=np.float32)
    seen = np.ones((4,), dtype=np.uint16)
    merge = SimpleNamespace(poll=lambda: None, coverage_wanted=False, coverage_stream_pending=False, coverage_cache=None,
                            coverage_serial=0, done=0, ask_coverage=lambda: False)
    s = SimpleNamespace(pieces=[{}], merge_proc=merge, output_mask=lambda xyz, **_: np.ones(len(xyz), bool))
    source, sink = livescan.LiveSource(), queue.Queue()
    source.scanner, source.phase = s, "scanning"
    push = livescan.CoveragePush(source, sink)
    with patch.object(livescan.time, "monotonic", side_effect=lambda: t):
        push.subscribe(20, 1)
        initial = sink.get_nowait()
        assert initial[:3] == ("coverage_update", 20, 1) and not len(initial[-2]) and merge.coverage_stream
        # The merge finishes 10 ms after subscription; throttling cannot strand it when capture goes quiet.
        t = 5.01
        merge.coverage_cache, merge.coverage_serial = (xyz, seen), 1
        push.publish()
        assert sink.empty() and push.has_pending()
        t = 5.11
        push.poll_completion()
        update = sink.get_nowait()
        assert update[:3] == ("coverage_update", 20, 1) and np.array_equal(update[-2], xyz)
        assert not push.has_pending() and t - 5.01 < 0.11  # well below the old second 300 ms poll
        source.remember_piece(1, (xyz, np.zeros((4, 3), np.uint8), seen))
        s.pieces.append({})
        push.set_stream()
        t = 5.30
        push.publish()
        archived = sink.get_nowait()
        assert not merge.coverage_stream and archived[2] == 1 and np.allclose(archived[-2], xyz)
        push.clear()
        push.publish(paused=True)
        assert sink.empty()


def test_pause_pushes_fresh_frozen_sample_and_resume_does_not_replay_unchanged_preview():
    s = SimpleNamespace(pieces=[{}], merge_proc=None, stats={"merged": 0}, cloud=SimpleNamespace(frames=0,
                         xyz=np.empty((0, 3)), n=np.empty(0), seen=np.empty(0, dtype=np.int64)),
                         cloud_lock=threading.Lock(), output_mask=lambda xyz, **_: np.ones(len(xyz), bool))
    source, sink = livescan.LiveSource(), queue.Queue()
    source.scanner, source.phase = s, "scanning"
    push = livescan.CoveragePush(source, sink)
    push.subscribe(2, 1)
    assert not len(sink.get_nowait()[-2])
    source.phase = "paused"
    source.paused_points = np.array([[1, 2, 3.]], np.float32), None, np.array([3], np.uint16)
    push.publish(paused=True)
    paused = sink.get_nowait()
    assert paused[2] == 1 and paused[-2].tolist() == [[1, 2, 3]]
    source.phase, source.paused_points = "scanning", None
    push.publish(paused=True)
    assert sink.empty()  # do not blank the last accepted model on Resume


def test_merge_job_piggybacks_one_bounded_frame_and_tk_mailbox_keeps_only_latest():
    merge = merger.MergeProcess.__new__(merger.MergeProcess)
    merge.jobs, merge.replies = queue.Queue(), queue.Queue()
    merge.proc = SimpleNamespace(is_alive=lambda: True)
    merge.on_merged = lambda reply: None
    merge.sent = merge.done = merge.coverage_serial = 0
    merge.coverage_stream, merge.coverage_stream_pending, merge.coverage_epoch = True, False, 1
    merge.submit(None, 1, None, None, 1)
    merge.submit(None, 2, None, None, 1)
    assert len(merge.jobs.get_nowait()) == 9 and len(merge.jobs.get_nowait()) == 8
    merge.replies.put(("merged", 1, 0, 0, [], None, None, 1, "snapshot"))
    merge.poll()
    assert merge.coverage_cache == "snapshot" and not merge.coverage_stream_pending

    feeder = viewer.Feeder(SimpleNamespace(live={key: None for key in ("proc", "commands", "replies")}))
    feeder.offer_coverage("old")
    feeder.offer_coverage("latest")
    assert feeder.take_coverage() == "latest" and feeder.take_coverage() is None
    assert feeder.out.empty()
