import numpy as np

from tribuutscan import livescan


def test_view_mask_marks_object_and_base_at_one_eighth():
    pix = np.array([[0, 0], [100, 50], [1279, 799], [640, 400]])
    obj = np.array([False, True, False, True])
    base = np.array([True, False, True, False])
    m = livescan.view_mask((pix, obj, base, (800, 1280)))
    assert m.shape == (100, 160) and m.dtype == np.uint8
    assert (m[0, 0], m[6, 12], m[99, 159], m[50, 80]) == (1, 2, 1, 2) and int((m > 0).sum()) == 4
    assert livescan.view_mask(None) is None


def test_the_raw_copy_writes_every_frame_beside_tracking():
    import tempfile
    from tribuutscan import frames as fr
    rng = np.random.default_rng(16)
    sets = [{"color": rng.integers(0, 256, fr.FRAME_BYTES["color"], np.uint8).tobytes(), "ir": None,
             "depth": rng.integers(0, 256, 1600 * fr.H, np.uint8).tobytes()} for _ in range(4)]
    with tempfile.TemporaryDirectory() as tmp:
        copy = livescan.RawCopy(fr.Recorder(1600, root=tmp))
        for i, bufs in enumerate(sets):
            copy.put(bufs, {"color": i + 0.01, "ir": None, "depth": float(i)})
        copy.close()
        back = list(fr.read_raw(copy.recorder.dir))
    assert len(back) == 4 and all(b["color"] == s["color"] and b["depth"] == s["depth"] for b, s in zip(back, sets))
    assert [b["t"][2] for b in back] == [0.0, 1.0, 2.0, 3.0]


def test_a_full_disk_stops_the_raw_copy_not_the_scan():
    import tempfile
    from tribuutscan import frames as fr

    class Full(fr.Recorder):
        def write(self, bufs, stamps):
            raise fr.DiskFull("only 1.0 GB free")

    with tempfile.TemporaryDirectory() as tmp:
        copy = livescan.RawCopy(Full(1600, root=tmp))
        copy.put({}, {})
        copy.put({}, {})  # queued behind the failed one: skipped
        copy.close()
    assert isinstance(copy.error, fr.DiskFull)


def test_the_reader_decodes_every_4th_pixel_beside_tracking_and_keeps_the_order():
    import queue
    from tribuutscan import frames as fr
    from tribuutscan.depth import decode_depth, encode_y14
    rng = np.random.default_rng(17)
    depths = [encode_y14(rng.integers(0, 16000, (fr.H, fr.W), np.uint16)) for _ in range(2)]
    commands = queue.Queue()
    sent = [("start", "small", None, False, None, "close", True)]
    sent += [("frame", b"yuyv", None, d, fr.W * 14 // 8, (i, 0.0, i)) for i, d in enumerate(depths)]
    sent += [("stop",), ("quit",)]
    for cmd in sent:
        commands.put(cmd)
    reader = livescan.Reader(commands)
    reader.start()
    reader.join(30)
    got = [reader.out.get_nowait() for _ in range(len(sent))]
    assert [g[0] for g in got] == [c[0] for c in sent] and reader.out.empty()
    for g, c in zip(got, sent):
        if c[0] == "frame":
            full = decode_depth(c[3], fr.W, fr.H, c[4])
            assert g[:-2] == c and (g[-2] == full[::4, ::4]).all() and g[-1] is None  # (no garbled rows)
        else:
            assert g == c


def test_stop_makes_the_mesh_after_optimise():
    """The live worker, as the viewer runs it, on a short synthetic scan: Stop with the mesh flag replies
    saved, optimised (a mesh follows) and meshed, and mesh.ply / mesh.stl are there."""
    try:
        from tribuutscan import synth
    except ImportError:  # needs Open3D (.venv)
        return
    import multiprocessing
    import os
    import queue
    import tempfile
    from pathlib import Path
    from tribuutscan import calib, livescan
    from tribuutscan import frames as fr
    before, env = calib.CAMERA.spec, os.environ.get("TRIBUUTSCAN_CAMERA")
    calib.use(calib.ESTIMATES)
    os.environ["TRIBUUTSCAN_CAMERA"] = "estimate"  # (the spawned worker: the camera the set is rendered with)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rec = synth.generate(Path(tmp) / "t-rec", n_frames=40)
            ctx = multiprocessing.get_context("spawn")
            commands, replies = ctx.Queue(), ctx.Queue()
            proc = ctx.Process(target=livescan.run, args=(commands, replies, 0), daemon=True)
            proc.start()
            got = []

            def reply(want):
                while True:
                    try:
                        kind, *rest = replies.get(timeout=120)
                    except queue.Empty:
                        raise AssertionError(f"no {want} from the worker; got {got}")
                    got.append(kind)
                    if kind == want:
                        return rest

            reply("ready")
            commands.put(("start", "small", Path(tmp) / "scan", True, None, calib.CAMERA.mode, True))
            frames = 0
            for bufs in fr.read_raw(rec):
                frame = ("frame", bufs["color"], None, bufs["depth"], bufs["bytesperline"], bufs["t"])
                commands.put(frame)
                frames += 1
                assert reply("frame_done")[1] == frames  # the same scanner/pose history continues
                if frames == 15:
                    commands.put(("pause",))
                    reply("paused")
                    commands.put(frame)  # defensive worker-side rejection of an out-of-phase frame
                    assert reply("frame_done") == [0, 0, 0]
                    commands.put(("resume",))
                    reply("resumed")
            commands.put(("pause",))
            reply("paused")
            commands.put(("stop", True))  # pausing did not replace Stop & Save
            reply("saved")
            raw = next((Path(tmp) / "scan").glob("*-rec"))
            assert len((raw / "stamps.csv").read_text().splitlines()) == 1 + frames
            assert reply("optimised")[1] is True
            message = reply("meshed")[0]
            assert message.startswith("mesh:"), message
            original = Path(tmp) / "scan"
            assert (original / "mesh.ply").exists() and (original / "mesh.stl").exists()
            cancelled = Path(tmp) / "cancel-live-scan"
            commands.put(("start", "small", cancelled, True, None, calib.CAMERA.mode, True))
            commands.put(frame)
            assert reply("frame_done")[1] == 1
            commands.put(("cancel", cancelled.name))
            recovery, warning = reply("cancelled")
            assert not warning and not cancelled.exists()
            assert Path(recovery).is_dir() and not (Path(recovery) / "cloud.ply").exists()
            assert (original / "mesh.stl").exists()
            commands.put(("quit",))
            proc.join(30)
            assert proc.exitcode == 0
    finally:
        calib.use(before)
        if env is None:
            os.environ.pop("TRIBUUTSCAN_CAMERA", None)
        else:
            os.environ["TRIBUUTSCAN_CAMERA"] = env


def test_an_io_error_drains_the_raw_queue_instead_of_hanging_stop():
    import errno
    import queue
    import threading
    from unittest.mock import Mock

    recorder = Mock()
    recorder.write.side_effect = OSError(errno.EIO, "mock disk failed")
    raw = livescan.RawCopy(recorder)
    done = queue.Queue()

    def write_and_close():
        for _ in range(raw.QUEUE * 2):
            raw.put({}, {})
        raw.close()
        done.put(raw.error)

    thread = threading.Thread(target=write_and_close, daemon=True)
    thread.start()
    error = done.get(timeout=2)
    assert error.errno == errno.EIO
    recorder.write.assert_called_once()
    recorder.close.assert_called_once()
    assert not raw.thread.is_alive()


def test_a_raw_flush_error_is_reported():
    import errno
    from unittest.mock import Mock

    recorder = Mock()
    recorder.close.side_effect = OSError(errno.ENOSPC, "mock disk full on flush")
    raw = livescan.RawCopy(recorder)
    raw.close()
    assert raw.error.errno == errno.ENOSPC


def test_continue_requires_the_saved_range_and_fingerprint():
    import json
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from tribuutscan import calib, cloud, viewer

    class Stub:
        def __init__(self):
            self.messages = []

        def log(self, message):
            self.messages.append(message)

    previous = calib.CAMERA.spec, calib.CAMERA.mode
    try:
        calib.use(calib.ESTIMATES)
        calib.use_mode(calib.CLOSE)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            metadata = directory / "session.json"
            good = {"depth_mode": calib.CLOSE, "camera": {"fingerprint": calib.CAMERA.fingerprint()}}
            for session, fragment in (({}, "no camera fingerprint"),
                                      ({**good, "depth_mode": calib.LONG}, "switch back"),
                                      ({**good, "camera": {"fingerprint": "another camera"}}, "calibration differs")):
                metadata.write_text(json.dumps(session))
                stub = Stub()
                viewer.App.begin_scan(stub, directory)  # stops before starting a worker or changing scan state
                assert fragment in stub.messages[0]
                with patch.object(cloud.Scanner, "restore") as restore:
                    try:
                        cloud.Scanner.continue_from(directory)
                    except ValueError as e:
                        assert fragment in str(e)
                    else:
                        raise AssertionError("incompatible continuation was allowed")
                    restore.assert_not_called()
            metadata.write_text(json.dumps(good))
            with patch.object(cloud.Scanner, "restore") as restore:
                sc = cloud.Scanner.continue_from(directory)
                restore.assert_called_once_with(directory, log=print, use_mode=False)
                assert sc.refind_needed and sc.crop is None
    finally:
        calib.use(previous[0])
        calib.use_mode(previous[1])


def test_worker_reports_a_rejected_continue_without_exiting():
    import json
    import queue
    import sys
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from tribuutscan import calib, view3d

    commands, replies = queue.Queue(), queue.Queue()
    before = calib.CAMERA.mode
    with tempfile.TemporaryDirectory() as tmp:
        old = Path(tmp) / "old-scan"
        old.mkdir()
        (old / "session.json").write_text(json.dumps({
            "depth_mode": calib.CLOSE, "camera": {"fingerprint": "wrong fingerprint"},
        }))
        commands.put(("start", "small", Path(tmp) / "next-scan", False, old, calib.CLOSE, True))
        commands.put(("quit",))
        try:
            # No network listener, scanner, or writer is needed to test the worker's error reply.
            with patch.object(view3d, "serve"), patch.object(sys, "path", sys.path[:]):
                livescan.run(commands, replies)
            assert replies.get_nowait()[0] == "ready"
            kind, reason = replies.get_nowait()
            assert kind == "start_failed" and "camera calibration differs" in reason
            assert calib.CAMERA.mode == before and replies.empty()
            assert not (Path(tmp) / "next-scan").exists()
        finally:
            calib.use_mode(before)


def test_paused_view_uses_a_frozen_snapshot_and_disallows_processing():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    source = livescan.LiveSource()
    source.phase = "paused"
    source.paused_points = np.array([[1., 2., 3.]]), np.array([[4, 5, 6]], np.uint8), np.array([3], np.int32)
    source.scanner = NS(stats=dict(merged=3, frames=5, tracking=True, lost=0, relocked=0), cue={},
                        cloud=NS(point_mm=1., raw_points=7), pieces=[], crop=None, preset="medium",
                        live_points=Mock(), crop_axes=Mock())
    version, payload, info = source.current()
    assert info["phase"] == "paused" and info["points"] == 1 and info["session"]["frames"] == 5
    assert source.current() == (version, payload, info)
    source.scanner.live_points.assert_not_called()
    source.scanner.crop_axes.assert_not_called()
    assert source.ply_bytes() is None
    assert "Stop & Save" in source.process("optimise")
    assert "Stop & Save" in source.process("crop", [0, 0, 0, 1, 1, 1])


def test_pause_drains_accepted_raw_frames_without_closing_the_recording():
    from unittest.mock import Mock

    recorder = Mock()
    raw = livescan.RawCopy(recorder)
    try:
        raw.put({"colour": b"1"}, {"t": 1.0})
        raw.drain()
        recorder.write.assert_called_once()
        recorder.close.assert_not_called()
        raw.put({"colour": b"2"}, {"t": 2.0})
        raw.drain()
        assert recorder.write.call_count == 2
    finally:
        raw.close()
    recorder.close.assert_called_once()


def test_scan_progress_distinguishes_post_resume_capture_tracking_and_point_growth():
    progress = livescan.ScanProgress()
    progress.observe(20, 6, 1400, True, {"piece": 3}, now=10)
    progress.resume()
    waiting = progress.status(now=20)
    assert waiting["capture"] == "Waiting for a fresh frame" and waiting["tracking"] == "Waiting after resume"
    assert waiting["geometry"] == "On hold" and waiting["frames"] == 20
    assert "Piece 3" in waiting["unjoined"]

    progress.observe(21, 6, 1400, False, {"piece": 3, "away": True}, now=21)
    lost = progress.status(now=21)
    assert lost["capture"] == "Frames arriving" and lost["tracking"] == "Lost (target not matched)"
    assert lost["geometry"] == "On hold" and "clear depth" in lost["hint"]

    progress.observe(22, 7, 2000, True, {"piece": 4}, now=22)
    unjoined = progress.status(now=22)
    assert unjoined["tracking"] == "Locked" and "steady" in unjoined["geometry"]
    assert "Piece 4" in unjoined["unjoined"]  # 2000 > 1400, but another piece is not growth of Piece 3.
    progress.observe(23, 8, 2100, True, {"piece": 4}, now=23)
    assert progress.status(now=23)["geometry"] == "Point count increasing"
    assert progress.status(now=26)["capture"] == "No recent processed frames"
    assert progress.status(now=26)["tracking"].startswith("Unknown")


def test_browser_telemetry_changes_without_rebuilding_stalled_geometry():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    source = livescan.LiveSource()
    source.phase = "scanning"
    points = Mock(return_value=(np.array([[1., 2., 3.]]), np.array([[4, 5, 6]], np.uint8), np.array([3], np.int32)))
    source.scanner = NS(stats=dict(merged=3, frames=5, tracking=False, lost=2, relocked=0), cue={},
                        cloud=NS(point_mm=1., raw_points=7), pieces=[1, 2], crop=None, preset="medium",
                        live_points=points)
    source.remember_piece(1, points.return_value)
    source.piece_payload(2)  # this tab explicitly asked to inspect the active piece
    source.progress.observe(5, 3, 1, False, {"piece": 2})
    v1, payload1, info1 = source.current()
    source.scanner.stats.update(frames=7, lost=4)
    source.scanner.cue["away"] = True
    source.progress.observe(7, 3, 1, False, {"piece": 2, "away": True})
    v2, payload2, info2 = source.current()
    points.assert_called_once()
    assert v1 == v2 and payload1 == payload2
    assert info1["session"]["frames"] == 5 and info2["session"]["frames"] == 7
    assert info2["progress"]["capture"] == "Frames arriving"
    assert info2["progress"]["tracking"] == "Lost (target not matched)"
    assert info2["progress"]["geometry"] == "On hold"
    assert "Piece 2" in info2["progress"]["unjoined"]


def test_cancel_active_and_paused_takes_preserves_raw_and_allows_restart():
    import csv
    import queue
    import tempfile
    import threading
    from pathlib import Path
    from types import SimpleNamespace as NS
    from unittest.mock import Mock, patch
    from tribuutscan import frames as fr

    class Scan:
        def __init__(self, *args, **kwargs):
            self.stats = dict(frames=0, merged=0, tracking=False)
            self.scene = NS(plane=None)
            self.cue, self.pieces = {}, []
            self.last_view = self.view_codes = None
            self.points_kept, self.object_turns = 0, False
            self.add_source = Mock()
            self.discard = Mock()
            self.save = Mock(side_effect=AssertionError("cancel must not save"))
        def feed(self, _frame):
            self.stats["frames"] += 1
        def sync_merges(self):
            pass
        def cloud_points(self, _seen):
            return np.empty((0, 3)), np.empty((0, 3), np.uint8), np.empty(0, np.int32)
        def output_mask(self, xyz, **kwargs):
            return np.ones(len(xyz), bool)

    commands, replies = queue.Queue(), queue.Queue()
    server = Mock()
    frame = ("frame", bytes(fr.FRAME_BYTES["color"]), None, bytes(1600 * fr.H), 1600, (1., 0., 1.))
    seen = []

    def wait_for(wanted):
        while True:
            reply = replies.get(timeout=5)
            seen.append(reply[0])
            if reply[0] == wanted:
                return reply[1:]

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        saved = root / "earlier-scan"
        saved.mkdir()
        old_file = saved / "cloud.ply"
        old_file.write_bytes(b"earlier")
        current, fresh = root / "this-live-scan", root / "next-live-scan"
        paused_scan, next_scan = Scan(), Scan()
        with patch.object(livescan.view3d, "serve", return_value=server) as serve, \
                patch.object(livescan.cl, "Scanner", return_value=next_scan) as factory:
            factory.continue_from.return_value = paused_scan
            worker = threading.Thread(target=livescan.run, args=(commands, replies, 0), daemon=True)
            worker.start()
            try:
                wait_for("ready")
                source = serve.call_args.args[0]
                commands.put(("start", "small", current, True, saved, livescan.calib.CLOSE, True))
                commands.put(frame)
                assert wait_for("frame_done")[1] == 1
                commands.put(("pause",))
                wait_for("paused")
                commands.put(("cancel", current.name))
                recovery, warning = wait_for("cancelled")
                recovered = Path(recovery)
                assert not warning and recovered.parent.parent == root / ".cancelled"
                assert recovered.name == current.name and not current.exists()
                assert old_file.read_bytes() == b"earlier"
                raw = next(recovered.glob("*-rec"))
                assert len(list(csv.DictReader((raw / "stamps.csv").open()))) == 1
                assert not (recovered / "cloud.ply").exists()
                assert source.current()[0] == "empty" and source.scanner is None
                paused_scan.discard.assert_called_once()
                paused_scan.save.assert_not_called()
                commands.put(frame)  # a late out-of-phase frame is not added to any take
                assert wait_for("frame_done") == (0, 0, 0)
                commands.put(("start", "small", fresh, False, None, livescan.calib.CLOSE, True))
                commands.put(frame)
                assert wait_for("frame_done")[1] == 1
                commands.put(("cancel", fresh.name))
                assert wait_for("cancelled") == ("", "")
                next_scan.discard.assert_called_once()
                next_scan.save.assert_not_called()
                assert not any(k in seen for k in ("saved", "optimised", "meshed"))
            finally:
                commands.put(("quit",))
                worker.join(timeout=5)
    assert not worker.is_alive()
    server.shutdown.assert_called_once()


def test_piece_split_keeps_primary_and_public_selection_is_read_only():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    def model(xs):
        n = len(xs)
        return np.column_stack((xs, np.zeros((n, 2)))), np.zeros((n, 3), np.uint8), np.ones(n, np.int32)

    source = livescan.LiveSource()
    source.phase = "scanning"
    scan_id = source.scan_id
    points = Mock(return_value=model([1]))
    source.scanner = NS(stats=dict(merged=3, frames=5, tracking=True, lost=0, relocked=0), cue={},
                        cloud=NS(point_mm=1., raw_points=7), pieces=[{}], crop=None, preset="medium",
                        live_points=points)
    primary_v, _, _ = source.current()
    points.assert_called_once()
    # Captured before cloud reset, even if no browser poll happens at that moment.
    source.remember_piece(1, model([1, 2]))
    source.scanner.pieces.append({})
    points.return_value = model([9, 10, 11])
    primary_v2, primary, info = source.current()
    assert primary_v2 != primary_v and info["points"] == 2
    assert info["default_piece"] == 1 and info["active_piece"] == 2
    assert info["scan_id"] == scan_id
    other_worker = livescan.LiveSource()
    assert other_worker.scan_id != scan_id
    assert other_worker.store_piece(1, model([1, 2]))[0] != primary_v2
    assert [p["id"] for p in info["piece_options"]] == [1, 2]
    points.assert_called_once()  # an unselected second piece isn't sampled by polling
    other = source.piece_payload(2, scan_id=scan_id)
    assert other != primary and source.piece_payload(1, scan_id=scan_id) == primary
    assert source.piece_payload(10) is None and source.piece_payload(2, scan_id=livescan.LiveSource().scan_id) is None
    # No global selection mutation: plain default still served after explicitly reading an unaligned piece.
    version, payload, info = source.current()
    assert (version, payload) == (primary_v2, primary) and info["points"] == 2
    assert [p["points"] for p in info["piece_options"]] == [2, 3]

    source.phase, source.paused_points, source.piece_sampled_at = "paused", model([9, 10, 11, 12]), float("-inf")
    points.reset_mock()
    version, payload, info = source.current()
    assert (version, payload) == (primary_v2, primary)
    frozen = source.piece_payload(2)
    assert frozen != other and [p["points"] for p in info["piece_options"]] == [2, 4]
    assert source.current()[2]["piece_options"] == info["piece_options"]
    points.assert_not_called()
    source.reset()
    assert source.piece_payload(1) is None


def test_continue_inherits_joined_model_as_local_piece_one():
    from types import SimpleNamespace as NS

    source = livescan.LiveSource()
    source.scanner = NS(pieces=[{}, {}, {}])  # three historical joined pieces loaded into one cloud
    source.clear_preview(piece_origin=3)
    assert source.piece_id() == 1
    source.remember_piece(3, (np.array([[1., 0., 0.]]), np.zeros((1, 3), np.uint8), np.ones(1)))
    source.scanner.pieces.append({})
    assert source.piece_id() == 2 and source.live_payload(1)[2] == 1


def test_scanner_calls_piece_snapshot_before_resetting_old_cloud():
    from unittest.mock import patch

    scanner = livescan.cl.Scanner(log=lambda _: None)
    try:
        old = scanner.cloud
        captured = []
        def remember(piece, points):
            assert scanner.cloud is old  # the precise boundary, not the new empty cloud
            captured.append((piece, points[0].copy()))
        scanner.on_piece_finished = remember
        xyz = np.array([[1., 0., 0.], [2., 0., 0.]])
        with patch.object(scanner, "cloud_points", return_value=(xyz, np.zeros((2, 3)), np.ones(2))), \
                patch.object(scanner, "output_mask", return_value=np.array([True, False])):
            scanner.start_piece(11)
        assert len(captured) == 1 and captured[0][0] == 1
        assert np.array_equal(captured[0][1], xyz[:1])
        assert scanner.cloud is not old and len(scanner.pieces) == 2
    finally:
        scanner.discard()


def test_piece_endpoint_binds_read_to_requested_piece_and_scan():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from tribuutscan import view3d

    source = NS(piece_payload=Mock(return_value=b"one-piece"), current=Mock())
    token = livescan.LiveSource().scan_id
    req = NS(path=f"/cloud.bin?piece=2&scan_id={token}", source=source, reply=Mock(), send_error=Mock(),
             piece_number=view3d.Handler.piece_number, scan_token=view3d.Handler.scan_token)
    view3d.Handler.do_GET(req)
    source.piece_payload.assert_called_once_with(2, token)
    source.current.assert_not_called()
    req.reply.assert_called_once_with(b"one-piece", "application/octet-stream")
    for query in ("piece=0", "piece=-2", "piece=2&piece=3", "piece=", "piece=1&scan_id=blah"):
        req.path = "/cloud.bin?" + query
        view3d.Handler.do_GET(req)
        assert req.send_error.call_args.args[0] == 400
    req.path = "/cloud.bin?piece=8"
    source.piece_payload.return_value = None
    view3d.Handler.do_GET(req)
    req.send_error.assert_called_with(404)
