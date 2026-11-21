import numpy as np

from tribuutscan import viewer


def test_previews_resample_to_any_width():
    img = np.arange(800 * 1280).reshape(800, 1280)
    for width in (viewer.SIDE_W, viewer.DEPTH_W, 1280, 333):
        out = viewer.fit(img, width)
        assert out.shape == (round(800 * width / 1280), width)
    assert viewer.fit(img, 640)[1, 1] == img[2, 2]  # half size: every second pixel


def test_open_app_asks_to_retry_until_the_scanner_opens():
    tries, asked = [], []

    class FlakyApp:
        def __init__(self, root):
            tries.append(root)
            if len(tries) < 3:
                raise RuntimeError("scanner not found or unexpected layout: {}")

    real = viewer.App
    viewer.App = FlakyApp
    try:
        app = viewer.open_app("root", ask=lambda title, text, parent: asked.append(text) or True)
        assert isinstance(app, FlakyApp) and len(tries) == 3 and len(asked) == 2
        tries.clear()
        assert viewer.open_app("root", ask=lambda title, text, parent: False) is None and len(tries) == 1  # gave up
    finally:
        viewer.App = real


def test_depth_preview_dims_all_but_the_object():
    import time
    from tribuutscan.depth import encode_y10

    class Cap:
        width, height, bytesperline = 1280, 800, 1600

    buf = encode_y10(np.full((800, 1280), 400, np.uint16))
    as_array = lambda ppm: np.frombuffer(ppm.split(b"\n", 1)[1], np.uint8).reshape(500, 800, 3).astype(int)
    plain = as_array(viewer.render_depth(Cap, buf, width=800)[0])
    mask = np.zeros((100, 160), np.uint8)
    mask[:, :80] = 2  # object on the left, base top right, the rest bottom right
    mask[:50, 80:] = 1
    try:
        viewer.scan_view.update(active=True, preview=viewer.COVERAGE, mask=mask, t=time.monotonic(), keep_base=True)
        shaded = as_array(viewer.render_depth(Cap, buf, width=800)[0])
    finally:
        viewer.scan_view.update(active=False, preview=viewer.LIVE_DEPTH, mask=None, t=0.0)
    assert (shaded[:, :380] == plain[:, :380]).all()  # object unchanged (the centre mark aside)
    assert (shaded[300:, 450:] == plain[300:, 450:] // 4).all()  # other dimmed
    assert (shaded[:200, 450:] == plain[:200, 450:] // 2 + viewer.BASE_TINT).all()  # kept base tinted
    assert (shaded[249:251, 386:396] == 255).all() and (shaded[250, 398:402] != 255).any()  # centre mark, open


def test_depth_preview_holds_depth_for_a_moment():
    hv = viewer.hold_view
    saved = dict(hv)
    try:
        hv.update(on=True, s=0.5, depth=None)
        a = np.zeros((4, 6), np.float32)
        a[1, 2] = 300.0
        shown, held = viewer.hold_depth(a, 10.0)
        assert shown[1, 2] == 300 and not held.any()
        b = np.zeros_like(a)
        b[3, 3] = 200.0
        shown, held = viewer.hold_depth(b, 10.3)  # within 0.5 s: the old pixel held, the new one live
        assert shown[1, 2] == 300 and held[1, 2] and shown[3, 3] == 200 and not held[3, 3]
        shown, held = viewer.hold_depth(b, 10.6)  # 0.6 s after it was last seen: gone
        assert shown[1, 2] == 0 and not held[1, 2] and shown[3, 3] == 200
        hv["mode"] = "another mode"  # a range switch: nothing is held over
        shown, held = viewer.hold_depth(np.zeros_like(a), 10.7)
        assert not shown.any() and not held.any()
    finally:
        hv.clear()
        hv.update(saved)


def test_distance_does_not_use_held_or_tracked_preview_depth():
    import time
    from tribuutscan.depth import encode_y10

    class Cap:
        width, height, bytesperline = 1280, 800, 1600

    hv, sv = viewer.hold_view, viewer.scan_view
    saved_hold, saved_scan = dict(hv), dict(sv)
    live = encode_y10(np.full((Cap.height, Cap.width), 300, np.uint16))
    empty = encode_y10(np.zeros((Cap.height, Cap.width), np.uint16))
    try:
        sv.update(t=0, model=None)
        hv.update(on=True, s=1, depth=None)
        assert viewer.render_depth(Cap, live)[1]["centre_valid"] == 1
        stats = viewer.render_depth(Cap, empty)[1]
        assert stats["centre_cm"] is None and "100% held" in stats["text"]
        sv.update(active=True, preview=viewer.COVERAGE, t=time.monotonic(), model=(np.ones((8, 8), dtype=np.uint8), live))
        stats = viewer.render_depth(Cap, empty)[1]
        assert stats["centre_cm"] is None and stats["centre_valid"] == 0
    finally:
        hv.clear()
        hv.update(saved_hold)
        sv.clear()
        sv.update(saved_scan)


def test_depth_preview_has_a_red_frame_while_tracking_is_lost():
    import time
    from tribuutscan.depth import encode_y10

    class Cap:
        width, height, bytesperline = 1280, 800, 1600

    buf = encode_y10(np.full((800, 1280), 400, np.uint16))
    as_array = lambda ppm: np.frombuffer(ppm.split(b"\n", 1)[1], np.uint8).reshape(500, 800, 3)
    viewer.scan_view.update(mask=None, t=0.0)
    plain = as_array(viewer.render_depth(Cap, buf, width=800)[0])
    try:
        viewer.scan_view.update(active=True, t=time.monotonic(), lost=True)
        lost = as_array(viewer.render_depth(Cap, buf, width=800)[0])
        viewer.scan_view.update(active=False, t=0.0)  # scan ended
        idle = as_array(viewer.render_depth(Cap, buf, width=800)[0])
    finally:
        viewer.scan_view.update(mask=None, lost=False, t=0.0)
    for edge in (lost[0], lost[-1], lost[:, 0], lost[:, -1]):
        assert (edge == viewer.LOST_RGB).all()
    assert (lost[100:400, 100:300] == plain[100:400, 100:300]).all()  # inside unchanged
    assert (idle == plain).all()


def test_live_depth_is_undimmed_and_current_even_when_worker_overlay_is_delayed():
    import time
    from unittest.mock import patch
    from tribuutscan.depth import encode_y10

    class Cap:
        width, height, bytesperline = 1280, 800, 1600

    width = 128
    newest_depth = np.zeros((Cap.height, Cap.width), np.uint16)
    newest_depth[:, :Cap.width // 2] = 240  # opposite half is invalid
    newest = encode_y10(newest_depth)
    tracked = encode_y10(np.full_like(newest_depth, 400))
    codes = np.full((8, 8), 255, np.uint8)
    mask = np.zeros_like(codes)
    with patch.dict(viewer.scan_view), patch.dict(viewer.hold_view), patch.dict(viewer.aim_view, idle=False):
        viewer.scan_view.update(active=False)
        viewer.hold_view.update(on=True, s=10, depth=None)
        viewer.render_depth(Cap, tracked, width)  # seed held depth, which must not appear during the scan
        app = viewer.App.__new__(viewer.App)
        app.choose_depth_preview(viewer.LIVE_DEPTH)
        for stamp in (0, time.monotonic()):
            viewer.scan_view.update(active=True, t=stamp, mask=mask, model=(codes, tracked))
            image, stats = viewer.render_depth(Cap, newest, width)
            expected = viewer.depth_to_rgb(viewer.fit(newest_depth, width))
            viewer.mark_centre(expected)
            assert image == viewer.ppm(expected)
            assert "live depth" in stats["text"] and viewer.MODEL_LEGEND not in stats["text"]
        app.choose_depth_preview(viewer.COVERAGE)
        image, stats = viewer.render_depth(Cap, newest, width)
        assert image != viewer.ppm(expected) and viewer.MODEL_LEGEND in stats["text"]


def test_status_shows_views_adding_even_when_point_count_stays_steady():
    progress = viewer.livescan.ScanProgress()
    progress.observe(40, 12, 50000, True, {}, now=10)
    progress.observe(44, 14, 50000, True, {}, now=13)
    assert "Views added: 14 · last just now" in viewer.progress_line(progress.status(now=13))
    assert "50,000 pts" in viewer.progress_line(progress.status(now=13))
    result = viewer.progress_line(progress.status(now=17))
    assert "Views added: 14 · last 4 s ago" in result


def test_depth_preview_paints_the_live_model_of_the_tracked_frame():
    """While scanning with the GPU tracker the preview shows the frame the worker tracked (not the newest),
    with the model painted as solid blue, striped yellow, or dotted pink by coverage."""
    import time
    from tribuutscan.depth import encode_y10

    class Cap:
        width, height, bytesperline = 1280, 800, 1600

    as_array = lambda ppm: np.frombuffer(ppm.split(b"\n", 1)[1], np.uint8).reshape(500, 800, 3).astype(int)
    tracked, newest = encode_y10(np.full((800, 1280), 400, np.uint16)), encode_y10(np.full((800, 1280), 100, np.uint16))
    viewer.scan_view.update(mask=None, t=0.0)
    plain = as_array(viewer.render_depth(Cap, tracked, width=800)[0])
    codes = np.zeros((400, 640), np.uint8)
    codes[:, :160] = 255  # done, facing the scanner
    codes[:, 160:320] = 127  # seen too few times, facing
    codes[:, 320:480] = 1  # the object, not in the model yet
    mask = np.full((100, 160), 2, np.uint8)  # all object: nothing dimmed
    try:
        viewer.scan_view.update(active=True, preview=viewer.COVERAGE, mask=mask, t=time.monotonic(),
                                lost=False, model=(codes, tracked))
        ppm, stats = viewer.render_depth(Cap, newest, width=800)
        shown = as_array(ppm)
    finally:
        viewer.scan_view.update(active=False, preview=viewer.LIVE_DEPTH, mask=None, t=0.0, model=None)
    assert (shown[:200, :150] == viewer.MODEL_DONE).all()
    assert (shown[0, 212] == viewer.MODEL_THIN).all()
    assert (shown[0, 210] == viewer.MODEL_THIN // 2).all()  # stripe
    assert (shown[2, 414] == viewer.MODEL_MISSING).all()
    assert (shown[0, 410] == viewer.MODEL_MISSING // 2).all()  # dot
    assert (shown[:200, 610:] == plain[:200, 610:]).all()  # the tracked frame's depth, not the newest's
    assert viewer.MODEL_LEGEND in stats["text"]


def test_status_box_says_what_runs_and_what_it_uses():
    """The Status box's text from the viewer's state, without a scanner or a window: idle, then a scan
    (two sleeping processes stand in for the worker and a meshing job) beside a recording."""
    import re
    import subprocess
    import sys
    import types
    from collections import deque
    from pathlib import Path

    class Proc:
        def __init__(self):
            self.child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            self.pid = self.child.pid

        def is_alive(self):
            return self.child.poll() is None

    app = viewer.App.__new__(viewer.App)  # the state only
    app.live = {"proc": None, "url": None, "done": deque(maxlen=60), "last": None, "name": "", "gpu": "", "merging": ""}
    app.rates, app.depth = viewer.sysinfo.Rates(), types.SimpleNamespace(cap=None)
    app.streams = {name: types.SimpleNamespace(fps=fps) for name, fps in
                   (("Colour", 30.0), ("IR (flipped)", 29.6), ("Depth", 30.2))}
    app.imu, app.imu_note = None, "IMU needs udev/70-tribuutscan.rules (see README)"
    app.job, app.scan_state, app.lost = {"process": None, "dir": None}, "idle", False
    app.switching, app.ir_auto, app.rec = {"busy": False}, True, {"recorder": None, "missed": 0}
    app.range_names = {mode: label for label, mode in viewer.RANGES.items()}
    lines = app.status_lines()
    assert [line[:viewer.LABEL_W].strip() for line in lines] == \
        ["Now", "Scanner", "Camera", "Streams", "Scan worker", "CPU", "Memory", "GPU", "Disk"]
    assert all(line[viewer.LABEL_W - 1] == " " for line in lines)  # the labels' column
    text = "\n".join(lines)
    assert "idle" in lines[0] and "10-bit (V4L2)" in text and "colour 30, ir 30, depth 30 fps" in text
    assert "IR auto (the scanner's own)" in text
    assert "udev" in text and "not started" in text and "not in use" in text and "GB free for captures/" in text

    worker, job = Proc(), Proc()
    try:
        app.scan_state = "scanning"
        app.live.update(proc=worker, name="20261102-170723-scan", gpu="Intel(R) Arc(tm) A750 Graphics (DG2)",
                        last=(12, 80, 30411, False), url="http://localhost:8765/")
        app.live["done"].extend([10.0, 10.5, 11.0])  # three frames back in a second
        app.rec["recorder"] = types.SimpleNamespace(dir=Path("20261102-171000-rec"), count=40, megabytes=120.4)
        app.job = {"process": job, "dir": Path("20261102-160000-rec")}
        app.status_lines()
        lines = app.status_lines()  # (the CPU rates need a reading before)
        text = "\n".join(lines)
        assert lines[0].split(None, 1)[1] == ("scanning 20261102-170723-scan: 80 frames (2.0 a second), 12 merged, "
                                              "30,411 points, TRACKING LOST; recording 20261102-171000-rec: 40 sets, "
                                              "120 MB, 0 missed; meshing 20261102-160000-rec")
        assert f"pid {worker.pid}, tracks on Intel(R) Arc(tm) A750 Graphics (DG2)" in text
        cpu = next(line for line in lines if line.startswith("CPU"))
        assert all(re.search(rf"{name} \d+%", cpu) for name in ("viewer", "worker", "meshing")), cpu
        memory = next(line for line in lines if line.startswith("Memory"))
        assert "viewer" in memory and "worker" in memory and "meshing" in memory
    finally:
        for p in (worker, job):
            p.child.kill()
            p.child.wait()


def _aim_scene():
    """A 14-bit depth frame: a flat table (raw 300) with two boxes on it (raw 420): one over the image centre,
    one off to the left."""
    from tribuutscan.depth import encode_y14

    class Cap:
        width, height, bytesperline = 1280, 800, 1280 * 14 // 8

    raw = np.full((800, 1280), 300.0)
    raw[300:500, 560:760] = 420.0  # under the centre
    raw[300:500, 150:350] = 420.0  # off to the left
    return Cap, encode_y14((raw * 16 + 8).astype(np.uint16))


def test_the_aim_outlines_the_centre_piece_and_follows_a_pick():
    import collections
    Cap, buf = _aim_scene()
    saved = dict(viewer.aim_view)
    try:
        viewer.aim_view.update(pick=None, follow=None, idle=True)
        piece = viewer.aim_once(Cap, buf, collections.deque(maxlen=5))
        assert piece is not None and piece["at"] is None  # automatic: the image centre, as a scan without an aim
        u = piece["pix"][:, 0]
        assert 560 <= u.min() and u.max() < 760 and piece["plane"]  # the centre box, the table cut
        assert piece["coverage"] > 0.9 and piece["colour"].shape == piece["pix"].shape
        viewer.aim_view.update(pick=(250.0, 400.0), follow=None)  # a click on the left box
        piece = viewer.aim_once(Cap, buf, collections.deque(maxlen=5))
        assert 150 <= piece["pix"][:, 0].min() and piece["pix"][:, 0].max() < 350
        assert 150 <= piece["at"][0] < 350 and viewer.aim_view["follow"] is not None
        again = viewer.aim_once(Cap, buf, collections.deque(maxlen=5))  # followed by its centre
        assert np.array_equal(again["pix"], piece["pix"])
        viewer.aim_view["t"] = viewer.time.monotonic()
        assert viewer.fresh_aim() is again
        viewer.aim_view["idle"] = False  # scanning: no aim drawn or handed over
        assert viewer.fresh_aim() is None
    finally:
        viewer.aim_view.clear()
        viewer.aim_view.update(saved)


def test_the_aim_hint_names_what_needs_doing():
    piece = {"z": 240, "coverage": 0.6, "plane": True, "at": None}
    text, colour = viewer.aim_hint(piece, {"clipped": 0.5}, None, False)
    assert colour == viewer.GOOD and "depth 60%" in text and not text.startswith("Picked")
    text, colour = viewer.aim_hint(dict(piece, coverage=0.1, plane=False, at=(1, 2)), {"clipped": 9.0},
                                   {"pitch": -30.0}, False)
    assert colour == viewer.BAD and text.startswith("Picked") and "try another angle" in text
    assert "IR clipping 9%" in text and "no base, 30° down (ok on a platform)" in text
    assert "No object identified" in viewer.aim_hint(None, {}, None, False)[0]


def test_distance_guide_uses_documented_targets_for_both_ranges():
    for mode, near, far in ((viewer.calib.CLOSE, 18, 30), (viewer.calib.LONG, 30, 50)):
        for cm, state, advice in ((near - 0.1, 0, "Move back"), (near, 1, "Hold this distance"),
                                  (far, 1, "Hold this distance"), (far + 0.1, 2, "Move closer")):
            # Fresh aim takes precedence over an unrelated centre patch.
            piece = {"mode": mode, "z": cm * 10, "coverage": 0.6, "at": (10, 10)}
            stats = {"centre_mode": mode, "centre_at": 10, "centre_valid": 1, "centre_cm": 100}
            active, text = viewer.distance_guide(piece, stats, mode, True, 10)
            assert active == state
            assert text.startswith(f"Picked object: ~{cm:.1f} cm (estimate)")
            assert advice in text and f"{near}–{far} cm" in text


def test_distance_guide_falls_back_only_to_fresh_reliable_centre_depth():
    mode = viewer.calib.CLOSE
    stats = {"centre_mode": mode, "centre_at": 10, "centre_valid": 0.4, "centre_cm": 22}
    poor = {"mode": mode, "z": 100, "coverage": 0.1}
    old_mode = {"mode": viewer.calib.LONG, "z": 100, "coverage": 1}
    for piece in (None, poor, old_mode):
        active, text = viewer.distance_guide(piece, stats, mode, False, 10.5)
        assert active == 1 and text.startswith("Centre") and "estimate" not in text
    for invalid in ({}, dict(stats, centre_at=6), dict(stats, centre_valid=0.1),
                    dict(stats, centre_cm=float("nan")), dict(stats, centre_mode=viewer.calib.LONG)):
        active, text = viewer.distance_guide(None, invalid, mode, False, 10.5)
        assert active is None and "No reliable depth" in text and "18–30 cm" in text
        assert "Move back" not in text and "Move closer" not in text


def test_the_countdown_starts_the_scan_at_zero_with_the_picked_aim():
    import time
    app = viewer.App.__new__(viewer.App)  # the state only
    started, logged = [], []
    app.begin_scan = lambda continue_from=None: started.append(continue_from)
    app.log = logged.append
    app.scan_state, app.count = "counting", {"until": time.monotonic() + 2, "from": None}
    text, colour = app.check_countdown()
    assert text.startswith("Starting in 2") and not started
    app.count["until"] = time.monotonic() - 0.01
    assert app.check_countdown() is None and started == [None] and app.scan_state == "idle"
    app.scan_state, app.count = "counting", {"until": time.monotonic() + 2, "from": None}
    app.escape()
    assert app.scan_state == "idle" and logged == ["start cancelled"]
    saved = dict(viewer.aim_view)
    try:
        viewer.aim_view.update(idle=True, t=time.monotonic(), piece={"at": (412.34, 380.0)})
        assert app.aim_for_start() == [412.3, 380.0]
        viewer.aim_view["piece"] = {"at": None}  # automatic: the scan takes the centre itself
        assert app.aim_for_start() is None
    finally:
        viewer.aim_view.clear()
        viewer.aim_view.update(saved)


def test_auto_ir_follows_the_scanner_through_a_rounding_slider():
    """The IR sliders show the scanner's own exposure while Auto IR is on; a slider rounds what it is given
    (its step) and reports the rounded value to its callback: that must not read as a move by hand."""
    class Scale:  # a Tk Scale's rounding: step 100, range 100-6800
        def __init__(self):
            self.v = 3000

        def set(self, v):
            self.v = min(max(round(float(v) / 100) * 100, 100), 6800)

        def get(self):
            return self.v

    app = viewer.App.__new__(viewer.App)
    scale = Scale()
    app.controls = {"IR exposure": ("depth", 1, {}, scale)}
    app.targets = {"depth": None}
    app.synced = {}
    values = iter([5486, 5486, 581])
    real = viewer.get_control
    viewer.get_control = lambda target, cid: next(values)
    try:
        app.follow_ir()
        assert scale.get() == 5500 and app.synced["IR exposure"] == 5500  # what its callback will say
        app.follow_ir()  # the same value again: nothing to do
        assert app.synced["IR exposure"] == 5500
        app.follow_ir()
        assert scale.get() == 600 and app.synced["IR exposure"] == viewer.held(scale) == 600
    finally:
        viewer.get_control = real


def test_the_feeder_keeps_the_worker_fed_off_the_tk_loop():
    """The feeder sends each new depth frame (with its colour pair) as soon as fewer than 2 are in flight and
    hands every reply on with the frame's raw depth, without the Tk loop: a fake worker taking 30 ms a frame
    gets one after another."""
    import queue
    import threading
    import time
    import types
    commands, replies, alive = queue.Queue(), queue.Queue(), threading.Event()
    alive.set()
    proc = types.SimpleNamespace(is_alive=alive.is_set)
    cap = types.SimpleNamespace(bytesperline=2240)
    depth = types.SimpleNamespace(raw=None, stamp=0.0, fresh=threading.Event(), cap=cap)
    color = types.SimpleNamespace(raw=b"c", nearest=lambda t: (b"colour", t - 0.006))
    app = types.SimpleNamespace(live={"proc": proc, "commands": commands, "replies": replies}, depth=depth, color=color,
                                scan_state="scanning")
    feeder = viewer.Feeder(app)
    most, done = [0], []

    def worker():
        while alive.is_set():
            try:
                cmd = commands.get(timeout=0.1)
            except queue.Empty:
                continue
            most[0] = max(most[0], feeder.inflight)
            time.sleep(0.03)
            replies.put(("frame_done", 1, 1, 10, True, {}, None, None, cmd[5][2]))

    threading.Thread(target=worker, daemon=True).start()
    feeder.start()
    try:
        for k in range(1, 31):  # a 100 fps camera: the worker can't take every frame
            depth.raw, depth.stamp = b"d%d" % k, time.monotonic()
            depth.fresh.set()
            time.sleep(0.01)
        end = time.time() + 2
        while time.time() < end and feeder.inflight:
            time.sleep(0.01)
        while True:
            try:
                done.append(feeder.out.get_nowait())
            except queue.Empty:
                break
    finally:
        alive.clear()
    assert most[0] <= 2 and len(done) >= 8 and feeder.inflight == 0
    assert all(kind == "frame_done" and raw is not None and raw.startswith(b"d") for kind, _, raw in done)
    assert feeder.stats["sent"] == len(done) and "a second" in feeder.report()
    assert float(np.median(feeder.stats["lag"])) < 0.02  # sent within ~a camera frame of arriving


def test_a_click_while_scanning_tells_the_worker_where_the_object_is():
    """While scanning, a click on the depth or colour preview goes to the worker as a pick in that image's full-size
    pixels (cloud.Scanner.pick); while optimising it does nothing."""
    import queue

    class Proc:
        def is_alive(self):
            return True

    app = viewer.App.__new__(viewer.App)
    logged = []
    app.log = logged.append
    app.live = {"proc": Proc(), "commands": queue.Queue()}
    app.scan_state = "scanning"
    app.aim_click("Depth", viewer.DEPTH_W / 2, 100)
    app.aim_click("Colour", viewer.SIDE_W / 4, 50)
    kind, how, uv = app.live["commands"].get_nowait()
    assert (kind, how) == ("pick", "depth") and np.allclose(uv, (640, 100 * 1280 / viewer.DEPTH_W))
    kind, how, uv = app.live["commands"].get_nowait()
    assert (kind, how) == ("pick", "colour") and np.allclose(uv, (320, 50 * 1280 / viewer.SIDE_W))
    assert logged and logged[0].startswith("picked")
    app.scan_state = "optimising"
    app.aim_click("Depth", 10, 10)
    assert app.live["commands"].empty()


def test_start_separate_piece_is_explicit_and_requires_fresh_worker_eligibility():
    import queue
    import time
    from types import SimpleNamespace

    app = viewer.App.__new__(viewer.App)
    progress = viewer.livescan.ScanProgress()
    proc = SimpleNamespace(is_alive=lambda: True)
    app.live = {"proc": proc, "progress": progress, "commands": queue.Queue()}
    app.scan_state, app.lost, app.switching = "scanning", False, {"busy": False}
    now = time.monotonic()
    assert not app.start_separate_piece()
    progress.observe(1, 1, 10, True, {"can_start_separate_piece": True}, now=now)
    assert not app.start_separate_piece()  # even a cue racing a locked frame cannot enable it
    progress.observe(2, 1, 10, False, {"can_start_separate_piece": False}, now=now)
    assert not app.start_separate_piece()
    progress.observe(3, 1, 10, False, {"can_start_separate_piece": True}, now=now)
    assert not app.can_start_separate_piece(now + progress.RECENT_S + 1)
    for phase in ("paused", "pausing", "resuming", "idle", "saving", "cancelling"):
        app.scan_state = phase
        assert not app.start_separate_piece()
    app.scan_state = "scanning"
    app.lost = True
    assert not app.start_separate_piece()
    app.lost, app.switching["busy"] = False, True
    assert not app.start_separate_piece()
    app.switching["busy"] = False
    assert app.live["commands"].empty()
    assert app.start_separate_piece()
    assert app.live["commands"].get_nowait() == ("start_separate_piece",)


def test_failed_reopen_closes_staged_sources_without_resuming_any_stream():
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    old = {"depth": "d0", "color": "c0", "ir": "i0"}
    new = {"depth": "d1", "color": "c1", "ir": "i1"}
    logs = []
    app = SimpleNamespace(nodes=old.copy(), targets=old.copy(), ir_auto=True, log=logs.append,
                          color=Mock(), ir=Mock(), depth=Mock())
    depth, color = Mock(), Mock()
    with patch.object(viewer, "wait_for_scanner", return_value=new), patch.object(viewer, "ir_exposure_mode"), \
            patch.object(viewer.fr, "open_depth", return_value=depth), \
            patch.object(viewer.v4l2, "Capture", side_effect=(color, OSError("failed IR open"))):
        assert not viewer.App.reopen(app)
    depth.close.assert_called_once()
    color.close.assert_called_once()
    assert app.nodes == old and app.targets == old
    for stream in (app.color, app.ir, app.depth):
        stream.resume.assert_not_called()
    assert "failed IR open" in logs[-1]


def test_successful_reopen_replaces_all_three_streams():
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    nodes = {"depth": "d1", "color": "c1", "ir": "i1"}
    app = SimpleNamespace(nodes={}, targets={}, ir_auto=True, log=Mock(), color=Mock(), ir=Mock(), depth=Mock())
    depth, color, ir = Mock(), Mock(), Mock()
    with patch.object(viewer, "wait_for_scanner", return_value=nodes), patch.object(viewer, "ir_exposure_mode"), \
            patch.object(viewer.fr, "open_depth", return_value=depth), \
            patch.object(viewer.v4l2, "Capture", side_effect=(color, ir)):
        assert viewer.App.reopen(app)
    assert app.nodes == nodes and app.targets == nodes
    for stream, cap in ((app.color, color), (app.ir, ir), (app.depth, depth)):
        stream.resume.assert_called_once_with(cap)
        cap.close.assert_not_called()


def test_a_dead_worker_clears_saving_and_can_be_replaced():
    import queue
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    old = SimpleNamespace(exitcode=1, join=Mock())
    commands, replies = Mock(), Mock()
    live = {"proc": old, "commands": commands, "replies": replies,
            "feeder": SimpleNamespace(out=queue.Queue()), "url": "old", "open": True, "name": "example-scan"}
    logs = []
    app = SimpleNamespace(live=live, scan_state="saving", lighter=Mock(), log=logs.append,
                          switching={"busy": False}, lost=False, countdown=SimpleNamespace(get=lambda: True),
                          start_worker=Mock())
    with patch.dict(viewer.scan_view):
        viewer.App.check_scan(app)
    assert app.scan_state == "idle" and app.count is None
    assert all(live[k] is None for k in ("proc", "commands", "replies", "feeder", "url"))
    assert "code 1" in logs[-1] and "partial files" in logs[-1]
    for channel in (commands, replies):
        channel.cancel_join_thread.assert_called_once()
        channel.close.assert_called_once()
    old.join.assert_called_once_with(timeout=0)
    app.lighter.assert_called_once_with(False)
    viewer.App.toggle_scan(app)
    app.start_worker.assert_called_once()
    assert app.scan_state == "counting"


def test_pause_synchronises_with_frame_enqueue_and_resume_requires_fresh_pair():
    import queue
    import threading
    from types import SimpleNamespace as NS
    from unittest.mock import patch

    commands, replies = queue.Queue(), queue.Queue()
    depth = NS(raw=b"depth", stamp=10.0, cap=NS(bytesperline=1600))
    colour = NS(nearest=lambda t: (b"colour", t - 0.1))
    app = NS(live={"proc": None, "commands": commands, "replies": replies}, depth=depth, scan_state="scanning")
    feeder = viewer.Feeder(app)
    feeder.send(app.live, depth, colour)
    selecting, selected = threading.Event(), threading.Event()

    def slow_pair(t):
        selecting.set()
        assert selected.wait(1)
        return b"colour", t

    frame = threading.Thread(target=feeder.send, args=(app.live, depth, NS(nearest=slow_pair)), daemon=True)
    frame.start()
    assert selecting.wait(1)
    feeder.transition("pausing", ("pause",))
    selected.set()
    frame.join(1)
    assert not frame.is_alive()
    assert commands.get_nowait()[0] == "frame" and commands.get_nowait() == ("pause",) and commands.empty()

    feeder.transition("resuming", ("resume",))
    with patch.object(viewer.time, "monotonic", return_value=20.0):
        feeder.transition("scanning", fresh=True)
    assert commands.get_nowait() == ("resume",)
    feeder.send(app.live, depth, colour)  # stale depth
    depth.stamp = 20.05
    feeder.send(app.live, depth, colour)  # depth fresh but matching colour captured while paused
    assert commands.empty()
    depth.stamp = 20.2
    feeder.send(app.live, depth, colour)
    assert np.allclose(commands.get_nowait()[5], (20.1, 0.0, 20.2))


def test_paused_acknowledgement_keeps_native_inspection_and_stop_saves_same_session():
    import queue
    from types import SimpleNamespace as NS
    from unittest.mock import Mock, patch

    feeder = Mock(out=queue.Queue())
    app = NS(live={"url": "http://localhost:8765/", "feeder": feeder, "proc": NS(exitcode=None)},
             scan_state="scanning", lighter=Mock(), log=Mock(), mesh_after=NS(get=lambda: True))
    viewer.App.toggle_pause_scan(app)
    feeder.transition.assert_called_once_with("pausing", ("pause",))
    app.scan_state = "pausing"
    feeder.out.put(("paused", [], None))
    with patch.dict(viewer.scan_view):
        viewer.App.check_scan(app)
    assert "Live 3D model" in app.log.call_args.args[0]
    assert app.scan_state == "paused"
    viewer.App.toggle_scan(app)
    feeder.transition.assert_called_with("saving", ("stop", True))


def test_scan_panel_build_wires_separate_pause_and_stop_controls():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock, patch
    from tribuutscan import panels

    app = NS(preset=None, toggle_scan=Mock(), toggle_pause_scan=Mock(), cancel_scan=Mock(),
             start_separate_piece=Mock(), continue_scan=Mock(),
             remove_base=None, keep_raw=None, mesh_after=None, countdown=None, scan_state="paused")
    panel = NS(app=app, set=Mock(), STATES=panels.ScanPanel.STATES)
    with patch.object(panels, "frame", return_value=Mock()), patch.object(panels, "button", side_effect=lambda *a, **kw: Mock(**kw)), \
            patch.object(panels, "checkbox", return_value=Mock()), patch.object(panels, "menu", return_value=Mock()):
        panels.ScanPanel.build(panel, object())
        panels.ScanPanel.refresh(panel)
    assert panel.scan.command is app.toggle_scan and panel.pause.command is app.toggle_pause_scan
    assert panel.cancel.command is app.cancel_scan and panel.separate.command is app.start_separate_piece
    panel.set.assert_any_call(panel.separate, state="disabled")
    panel.set.assert_any_call(panel.scan, text="Stop & Save (c)", state="normal")
    panel.set.assert_any_call(panel.pause, text="Resume scan (p)", state="normal")


def test_cancel_stops_new_scan_frames_and_acknowledgement_reenables_start():
    import queue
    from collections import deque
    from types import SimpleNamespace as NS
    from unittest.mock import Mock, patch

    commands = queue.Queue()
    proc = NS(exitcode=None)
    live = {"proc": proc, "commands": commands, "replies": queue.Queue(), "name": "a-live-scan", "done": deque()}
    app = NS(live=live, scan_state="paused", log=Mock(), lighter=Mock(), ticks=deque(),
             lost=False, switching={"busy": False}, countdown=NS(get=lambda: True), start_worker=Mock())
    feeder = live["feeder"] = viewer.Feeder(app)
    viewer.App.cancel_scan(app)
    assert app.scan_state == "cancelling"
    assert commands.get_nowait() == ("cancel", "a-live-scan")
    viewer.App.toggle_scan(app)
    viewer.App.cancel_scan(app)
    assert commands.empty() and app.scan_state == "cancelling"
    feeder.out.put(("cancelled", ["/captures/.cancelled/first/a-live-scan", ""], None))
    with patch.dict(viewer.scan_view):
        viewer.App.check_scan(app)
    assert app.scan_state == "idle" and app.live["proc"] is proc
    assert "recovery files" in app.log.call_args.args[0]
    viewer.App.toggle_scan(app)
    assert app.scan_state == "counting"
    app.start_worker.assert_not_called()


def test_close_after_cancel_does_not_save_the_take():
    import queue
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    commands = queue.Queue()
    app = NS(live={"proc": NS(join=Mock(), is_alive=lambda: False), "commands": commands, "feeder": Mock()},
             scan_state="cancelling", rec={"recorder": None}, log=Mock(), imu_off=Mock(), streams={}, root=Mock(),
             model_pane=Mock())
    viewer.App.close(app)
    assert commands.get_nowait() == ("quit",) and commands.empty()
    app.live["feeder"].transition.assert_not_called()
    app.root.destroy.assert_called_once()


def test_return_reference_keeps_acknowledged_buffer_and_object_outline():
    from unittest.mock import patch
    from tribuutscan.depth import encode_y10

    w, h, stride = 128, 80, 160
    depth = np.full((h, w), 300, np.uint16)
    selected = np.zeros((10, 16), np.uint8)
    selected[1:8, 3:9] = 2
    original = encode_y10(depth)
    other = encode_y10(np.full_like(depth, 400))
    ref = viewer.TrackedReference()
    with patch.object(viewer, "render_tracked_reference", wraps=viewer.render_tracked_reference) as render:
        ref.observe(1, False, other, (w, h, stride), None)  # no aligned frame yet
        assert ref.image() is None
        ref.observe(1, True, original, (w, h, stride), selected)
        assert ref.image() is None
        ref.observe(1, False, other, (w, h, stride), None)
        first = ref.image()
        assert ref.image() is first
        ref.observe(1, False, other, (w, h, stride), None)
        assert ref.image() is first and render.call_count == 1
        rgb = np.frombuffer(first[0].split(b"\n", 1)[1], np.uint8).reshape(160, 256, 3)
        assert np.any((rgb == viewer.AIM_RGB).all(axis=-1))
        assert np.all(rgb[5, 5] == viewer.depth_to_rgb(depth)[0, 0])
        assert first[2] and np.isclose(first[1], viewer.pointcloud.depth_to_mm(300) / 10)
        ref.observe(1, True, other, (w, h, stride), None)
        assert ref.image() is None
        ref.observe(1, False, original, (w, h, stride), None)
        assert ref.image()[0] != first[0] and render.call_count == 2
        ref.observe(2, False, original, (w, h, stride), None)
        assert ref.image() is None  # cannot show the prior piece's view
        ref.observe(2, True, original, (w, h, stride), None)
        ref.clear()
        assert ref.raw is None


def test_return_overlay_keeps_current_depth_and_restores_selected_coverage():
    import time
    from unittest.mock import patch
    from tribuutscan.depth import encode_y10

    class Cap:
        width, height, bytesperline = 128, 80, 160

    now = np.full((Cap.height, Cap.width), 270, np.uint16)
    tracked = np.full_like(now, 410)
    mask = np.full((8, 8), 2, np.uint8)
    codes = np.full_like(mask, 255)
    with patch.dict(viewer.scan_view, active=True, preview=viewer.COVERAGE, t=time.monotonic(), mask=mask,
                    model=(codes, encode_y10(tracked)), lost=True, return_guide=True):
        _, stats = viewer.render_depth(Cap, encode_y10(now), width=Cap.width)
        assert "compare with last tracked" in stats["text"] and viewer.MODEL_LEGEND not in stats["text"]
        assert np.isclose(stats["centre_cm"], viewer.pointcloud.depth_to_mm(270) / 10)
        image, _ = viewer.render_depth(Cap, encode_y10(now), width=Cap.width)
        rgb = np.frombuffer(image.split(b"\n", 1)[1], np.uint8).reshape(Cap.height, Cap.width, 3)
        assert np.all(rgb[15, 16] == viewer.depth_to_rgb(now)[15, 16])
        assert np.all(rgb[0, 0] == viewer.LOST_RGB)
        viewer.scan_view.update(return_guide=False, lost=False)
        _, stats = viewer.render_depth(Cap, encode_y10(now), width=Cap.width)
        assert viewer.MODEL_LEGEND in stats["text"]


def test_return_panel_reuses_image_across_loss_and_hides_on_pause_or_disconnect():
    from unittest.mock import Mock, patch
    from tribuutscan.depth import encode_y10

    app = viewer.App.__new__(viewer.App)
    app.tracked_reference = ref = viewer.TrackedReference()
    app.return_panel, app.return_image, app.return_note = Mock(), Mock(), Mock()
    app.return_photo = app.shown_return = None
    app.scan_state, app.lost, app.switching = "scanning", False, {"busy": False}
    app.model_pane = Mock()
    depth = np.full((80, 128), 300, np.uint16)
    ref.observe(1, True, encode_y10(depth), (128, 80, 160), None)
    ref.observe(1, False, None, (128, 80, 160), None)
    with patch.object(viewer.tk, "PhotoImage", return_value=Mock()) as photo:
        assert app.refresh_return_guide() and app.refresh_return_guide()
        photo.assert_called_once()
        app.return_panel.pack.assert_called_once_with(fill="x", after=app.model_pane, pady=(4, 0))
        app.scan_state = "paused"
        assert not app.refresh_return_guide()
        assert ref.raw is not None and app.return_photo is None
        app.scan_state = "scanning"
        assert app.refresh_return_guide()  # reference survives pause/resume
        app.lost = True
        assert not app.refresh_return_guide() and ref.raw is None


def test_return_reference_uses_only_acknowledged_stable_frames_from_feeder():
    import queue
    from collections import deque
    from types import SimpleNamespace as NS
    from unittest.mock import patch
    from tribuutscan.depth import encode_y10

    app = viewer.App.__new__(viewer.App)
    app.scan_state, app.lost, app.switching = "scanning", False, {"busy": False}
    app.depth = NS(cap=NS(width=128, height=80, bytesperline=160), raw=b"current camera bytes")
    app.tracked_reference = ref = viewer.TrackedReference()
    out = queue.Queue()
    app.live = {"feeder": NS(out=out), "proc": NS(exitcode=None), "done": deque(),
                "progress": viewer.livescan.ScanProgress()}
    one = encode_y10(np.full((80, 128), 300, np.uint16))
    two = encode_y10(np.full((80, 128), 410, np.uint16))

    def done(i, tracking, raw, stable=True):
        cue = {"piece": 1, "reference_ok": stable}
        out.put(("frame_done", (1, i, 250, tracking, cue, None, None, i), raw))
        app.check_scan()

    with patch.dict(viewer.scan_view):
        done(1, True, one)
        done(2, True, two, stable=False)
        done(3, False, two)
        assert ref.raw == one and ref.image() is not None
        app.switching["busy"] = True
        done(4, True, two)
        assert ref.raw == one  # late reply from a previous capture cannot become the new reference


def test_visible_coverage_excludes_background_base_and_unclassified_cells():
    codes = np.array([[128, 255, 10, 1], [0, 128, 10, 1]], dtype=np.uint8)
    object_mask = np.array([[2, 1, 2, 2], [2, 0, 2, 0]], dtype=np.uint8)
    counts = viewer.visible_coverage(codes, object_mask)
    assert counts == (1, 2, 1)
    caption = viewer.coverage_caption(counts)
    assert "25% observed enough" in caption and "50% another pass" in caption and "25% not captured" in caption
    assert "Visible object" in caption and "Not a measure of accuracy" in caption
    assert viewer.visible_coverage(codes, None) is None
    assert viewer.visible_coverage(np.zeros_like(codes), object_mask) is None
    assert "%" not in viewer.coverage_caption(None)


def test_coverage_status_does_not_report_old_or_lost_samples():
    stats = {"centre_at": 10.0, "coverage": "CPU tracking · Coverage colours unavailable"}
    assert "CPU tracking" in viewer.coverage_status(stats, 10.1, "scanning", False, False, False)
    assert "Waiting" in viewer.coverage_status(stats, 11.1, "scanning", False, False, False)
    assert "Tracking lost" in viewer.coverage_status(stats, 10.1, "scanning", False, True, False)
    assert "Last tracked view" in viewer.coverage_status(stats, 10.1, "scanning", False, True, True)
    assert "not ready" in viewer.coverage_status(stats, 10.1, "scanning", True, False, False)
    assert "during a live scan" in viewer.coverage_status(stats, 10.1, "paused", False, False, False)


def test_viewer_accepts_native_coverage_reply_even_while_paused():
    import queue
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    out = queue.Queue()
    state = Mock()
    feeder = NS(out=out, take_coverage=lambda: (7, 1, "v1", [[1, 2, 3]], [2]))
    app = NS(live={"feeder": feeder, "proc": NS(exitcode=None)}, scan_state="paused",
             model_pane=NS(state=state))
    out.put(("ready", ["http://localhost:8765/"], None))
    viewer.App.check_scan(app)
    state.receive.assert_called_once_with(7, 1, "v1", [[1, 2, 3]], [2])


def test_window_places_large_3d_on_left_and_keeps_all_three_feeds_beside_it():
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from tribuutscan import model_pane

    app = viewer.App.__new__(viewer.App)
    app.root, app.preview_mode = Mock(), Mock()
    app.depth, app.panels, app.images = SimpleNamespace(cap=Mock()), [], {}
    labels = []

    def frame(parent, **kwargs):
        widget = Mock()
        widget.parent = parent
        return widget

    def label(parent, **kwargs):
        widget = frame(parent)
        labels.append((kwargs.get("text"), widget))
        return widget

    with (patch.object(viewer.tk, "Frame", side_effect=frame),
          patch.object(viewer.tk, "Label", side_effect=label),
          patch.object(viewer.tk, "PhotoImage"), patch.object(viewer.tk, "Text"),
          patch.object(viewer.tkinter.font, "nametofont"), patch.object(viewer, "DistanceGauge"),
          patch.object(viewer, "depth_title", return_value="Depth"), patch.object(viewer, "PANELS", []),
          patch.object(viewer.panels, "frame", side_effect=frame),
          patch.object(viewer.panels, "configure_styles"), patch.object(viewer.panels, "button"),
          patch.object(viewer.panels, "menu"), patch.object(model_pane, "ModelPane") as make_model):
        app.build_window()
    model_parent = make_model.call_args.args[0]
    assert model_parent.grid.call_args.kwargs["column"] == 0
    assert make_model.call_args.kwargs == {"width": viewer.MODEL_W, "height": viewer.MODEL_H}
    feeds = dict(labels)
    depth_parent = feeds["Depth"].parent.parent
    feed_column = depth_parent.parent
    assert feed_column.grid.call_args.kwargs["column"] == 1
    assert feeds["Colour"].parent.parent.parent is feeds["IR (flipped)"].parent.parent.parent is feed_column
    assert viewer.MODEL_W * viewer.MODEL_H > viewer.DEPTH_W ** 2 * viewer.fr.H // viewer.fr.W
    assert viewer.MODEL_W + viewer.DEPTH_W <= 1200  # no camera/model expansion over the previous layout
