"""Live scanning worker: merges frames from the viewer into one cloud, in its own process.

The viewer starts it with the .venv Python when there is one (so ICP tracking works) and talks
to it over two queues. The worker also serves the browser 3D view of the growing cloud
(view3d page), so cloud data never passes back through the viewer.

Commands:  ("start", preset, out_dir, keep_raw, continue_from or None, depth mode name, remove_base[, options])
           options: {"tracker": "gpu" | "joint", "capture": {...}} (a tracker name alone also works); "capture"
           (the IR settings, the aim: depth pixel (u, v) of the object at Start) goes into the raw copy's
           info.json and the scan's session.json
           (tracker: "gpu", the default, which falls back to the CPU solve without a GPU; or "joint")
           | ("frame", yuyv, grey or None, depth_buf, bytesperline, (t_colour, t_ir, t_depth))
           | ("pick", "depth" | "colour", (u, v))   (a click on that preview, full-size pixel: the object is
           there, cloud.Scanner.pick; noted in the raw copy's picks.csv)
           | ("pause",) | ("resume",) | ("cancel", take_name) | ("stop"[, mesh]) | ("quit",)   (times: camera capture times, seconds; mesh: make the mesh after
           Optimise, fusion on the GPU, so Stop ends with a finished model)
Replies:   ("ready", url) | ("log", text) | ("optimised", message, a mesh follows) | ("meshed", message)
           | ("frame_done", merged, frames, points, tracking, cue, view, model, t_depth) | ("saved", path, points)
           cue: {"markers": used, "features": ORB start, "flat": shape alone slides, "base": base found,
                 "turning": the object turns and the scanner stands (Scanner.check_room), "piece": its number,
                 "away": lost on something else (Scanner.likeness "other", or too little depth)}
           view: what the frame was, at 1/VIEW_STEP of its size (uint8: 0 other, 1 base, 2 object), or None
           model: the live model view of that frame (gpu.preview_codes at 1/gputrack.PREVIEW_STEP: its piece
           against the model built so far), or None without the GPU tracker; t_depth: the frame's depth time

With keep_raw, the frames the worker processed are also written to out_dir/<stamp>-rec/ (the
viewer's recording format, without IR), so a scan can be replayed offline with cloud.py for
debugging. The copy stops (the scan goes on) when the disk is nearly full (frames.Recorder). It is
written beside tracking (RawCopy): in the first live GPU scan it took 34 ms of every frame.
"""
import io
import io
import multiprocessing
import queue
from pathlib import Path
import site
import sys
import threading
import time

from uuid import uuid4

import numpy as np

from . import calib
from . import cloud as cl
from . import frames as fr
from . import pointcloud as pc
from . import coverage_preview, view3d
from .depth import LazyFrame, decode_strided

PAYLOAD_EVERY_S = 0.5  # rebuild the page's cloud at most this often
LIVE_MIN_SEEN = 1  # the page shows every cube while scanning; the saved cloud uses cloud.MIN_SEEN
VIEW_STEP = cl.LABEL_STEP  # the frame's object mask goes back to the viewer at 1/8 of its size (160 x 100)
view_mask = cl.view_mask  # the same labels the scan keeps for fusing again


class RawCopy:
    """frames.Recorder writes on a thread of their own (zlib and file writes release the GIL). put()
    waits while QUEUE sets are pending; after a write error the thread still drains the queue."""

    QUEUE = 30

    def __init__(self, recorder):
        self.recorder, self.error = recorder, None
        self.queue = queue.Queue(self.QUEUE)
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()

    def _write(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                if self.error is None:
                    if item[0] == "pick":
                        self.recorder.note_pick(*item[1:])
                    elif item[0] == "piece_start":
                        self.recorder.note_piece_start()
                    else:
                        self.recorder.write(*item[1:])
            except Exception as e:
                # A failed writer must still consume through the close sentinel (and not strand Pause).
                self.error = e if isinstance(e, OSError) else OSError(f"{type(e).__name__}: {e}")
            finally:
                self.queue.task_done()

    def drain(self):
        """Finish writing the accepted frames before acknowledging a pause."""
        self.queue.join()

    def put(self, bufs, stamps):
        self.queue.put(("set", bufs, stamps))

    def pick(self, kind, uv):
        """A click while scanning, noted in order with the frames (frames.Recorder.note_pick)."""
        self.queue.put(("pick", kind, uv))

    def piece_start(self):
        self.queue.put(("piece_start",))

    def close(self):
        self.queue.put(None)
        self.thread.join()
        try:
            self.recorder.close()
        except OSError as e:  # buffered writes can fail during close, too
            if self.error is None:
                self.error = e


class Reader(threading.Thread):
    """Takes the viewer's commands off its queue beside tracking, in the order they came, frames as
    (*command, every-4th-pixel depth, cleared rows): depth.decode_strided, with the garbled-row check.
    The full depth stays packed (depth.LazyFrame): the GPU unpacks it for tracking, the merge process
    for itself (the full decode here took 14 ms a frame beside tracking, holding the GIL)."""

    def __init__(self, commands):
        super().__init__(daemon=True)
        self.commands, self.out = commands, queue.Queue()
        self.decode_s = 0.0  # time spent decoding (beside tracking)

    def run(self):
        while True:
            cmd = self.commands.get()
            if cmd[0] == "frame":
                t = time.perf_counter()
                _, _, _, depth_buf, bytesperline, _ = cmd
                cmd = (*cmd, *decode_strided(depth_buf, fr.W, fr.H, bytesperline))
                self.decode_s += time.perf_counter() - t
            self.out.put(cmd)
            if cmd[0] == "quit":
                return


class ScanProgress:
    """Separate frames reaching tracking, pose recovery, and the active piece's point count."""

    RECENT_S = 2.5

    def __init__(self):
        self.frames = self.merged = self.points = 0
        self.piece = 1
        self.last_frame = self.last_merge = self.last_growth = None
        self.tracking, self.resuming, self.cue = None, False, {}

    def resume(self):
        # Keep the same scan counters; only new post-resume frames can establish progress.
        self.last_frame = self.last_merge = self.last_growth = None
        self.tracking, self.resuming, self.cue = None, True, {}

    def observe(self, frames, merged, points, tracking, cue, now=None):
        now = time.monotonic() if now is None else now
        piece = cue.get("piece", 1)
        if frames > self.frames:
            self.last_frame = now
        # A larger disconnected piece is not growth of the previous piece.
        if piece != self.piece:
            self.last_growth = self.last_merge = None
        elif points > self.points:
            self.last_growth = now
        if merged > self.merged:
            self.last_merge = now
        self.frames, self.merged, self.points, self.piece = frames, merged, points, piece
        self.tracking, self.resuming, self.cue = tracking, False, cue

    def status(self, now=None):
        now = time.monotonic() if now is None else now

        def recent(stamp):
            return stamp is not None and now - stamp <= self.RECENT_S

        capture = ("Waiting for a fresh frame" if self.last_frame is None else
                   "Frames arriving" if recent(self.last_frame) else "No recent processed frames")
        recovery = ("Show an already scanned edge, texture or the same markers. "
                    "The scanner tries matching features automatically; the existing model is held.")
        if self.last_frame is None:
            tracking = "Waiting after resume" if self.resuming else "Waiting"
            geometry = "On hold"
            hint = "Hold the last aligned view until tracking locks." if self.resuming else "Aim at the object."
        elif not recent(self.last_frame):
            tracking, geometry = "Unknown (no recent reading)", "On hold"
            hint = "Check the live colour/depth previews before moving."
        elif self.cue.get("object_recovery_pending"):
            tracking, geometry = "Checking object match", "On hold"
            hint = "Hold steady while the scanner checks the matched view."
        elif not self.tracking:
            tracking, geometry, hint = "Lost", "On hold", recovery
            if self.cue.get("away"):
                tracking = "Lost (target not matched)"
                hint = "Check that the object has clear depth, then show a part already scanned. The existing model is held."
        else:
            tracking = "Locked"
            geometry = ("Point count increasing" if recent(self.last_growth) else
                        "Taking views; point count steady" if recent(self.last_merge) else "No recent views added")
            hint = ("Show a defined edge or markers while keeping overlap." if self.cue.get("flat") else
                    "Move slowly towards a new angle and keep part of the scanned surface in view.")
        unjoined = f"Piece {self.piece} is not joined to earlier pieces yet." if self.piece > 1 else ""
        return dict(capture=capture, tracking=tracking, geometry=geometry, hint=hint,
                    frames=self.frames, merged=self.merged, points=self.points, unjoined=unjoined,
                    last_view_age_s=None if self.last_merge is None else max(0.0, now - self.last_merge))


class LiveSource:
    """view3d source backed by the running Scanner (or the last one)."""

    LIVE_PHASES = ("scanning", "paused")
    EMPTY_PAYLOAD = view3d.pack_arrays(np.empty((0, 3)))

    def __init__(self):
        self.lock = threading.Lock()
        self.scanner, self.name, self.scan_id = None, "no scan yet", uuid4().hex
        self.phase, self.out_dir, self.busy = "idle", None, threading.Lock()
        self.progress = ScanProgress()
        self.clear_preview()

    def clear_preview(self, piece_origin=1):
        self.paused_points = None
        self.cached, self.built_key, self.built_at = ("empty", self.EMPTY_PAYLOAD, {}), None, 0.0
        self.piece_cache, self.piece_origin, self.piece_revision = {}, max(1, piece_origin), 0
        self.piece_sampled_at, self.piece_sampled_key = float("-inf"), None
        self.piece_requested = None
        self.native_revision, self.native_key, self.native_sample = 0, None, None
        self.native_requested_at_merge = None

    def reset(self):
        """Clear the cancelled model but retain the same local viewer service."""
        self.scanner, self.out_dir = None, None
        self.phase, self.name = "idle", "no scan yet"
        self.progress = ScanProgress()
        self.clear_preview()

    def piece_id(self, tracker_id=None):
        # Continue starts with its previous joined model as local primary piece 1.
        return max(1, (len(self.scanner.pieces) if tracker_id is None else tracker_id) - self.piece_origin + 1)

    def store_piece(self, piece, points):
        xyz, rgb, seen = points
        payload = view3d.pack_arrays(xyz @ cl.FLIP.T / 1000, rgb, seen)
        old = self.piece_cache.get(piece)
        if old is None or old[1] != payload:
            self.piece_revision += 1
            self.piece_cache[piece] = (f"{self.scan_id}-piece-{piece}-{self.piece_revision}", payload, len(xyz))
        return self.piece_cache[piece]

    def remember_piece(self, tracker_id, points):
        """Called before Scanner resets this piece's geometry. Caller already holds the source lock."""
        self.store_piece(self.piece_id(tracker_id), points)
        self.piece_sampled_at, self.piece_sampled_key = float("-inf"), None

    def refresh_live_piece(self, requested=False):
        piece, now = self.piece_id(), time.monotonic()
        if self.phase == "paused":
            if self.paused_points is not None and self.piece_sampled_at != float("inf"):
                self.store_piece(piece, self.paused_points)
                self.piece_sampled_at = float("inf")
        elif piece == 1 or requested or self.piece_requested == piece:
            # An unselected secondary piece needs no active binary build merely because info is polled.
            # Once explicitly requested, keep its cache current without changing the default selection.
            s = self.scanner
            tracked_cache = getattr(getattr(s, "merge_proc", None), "points_cache", None)
            key = (piece, s.stats["merged"], getattr(s, "points_kept", None), id(tracked_cache))
            if key != self.piece_sampled_key and now - self.piece_sampled_at >= PAYLOAD_EVERY_S:
                points = s.live_points(LIVE_MIN_SEEN)
                if len(points[0]) or piece not in self.piece_cache:
                    # The merge subprocess can momentarily have no fresh preview; retain the last real
                    # snapshot of this same piece rather than blanking it on Resume.
                    self.store_piece(piece, points)
                self.piece_sampled_at, self.piece_sampled_key = now, key
        return piece

    def live_payload(self, piece):
        return self.piece_cache.get(piece, (f"{self.scan_id}-piece-{piece}-empty", self.EMPTY_PAYLOAD, 0))

    def piece_payload(self, piece, scan_id=None):
        """Read only: a request changes no selection for this or any other tab."""
        with self.lock:
            if self.scanner is None or self.phase not in self.LIVE_PHASES:
                return None
            if scan_id is not None and scan_id != self.scan_id:
                return None
            active = self.piece_id()
            if piece != active and piece not in self.piece_cache:
                return None
            if piece == active:
                self.piece_requested = piece
                self.refresh_live_piece(requested=True)
            return self.live_payload(piece)[1]

    def native_coverage(self, piece, last_version):
        """One bounded, read-only sample for the Tk pane; never wait for merge/render work."""
        empty = np.empty((0, 3), np.float32), np.empty(0, np.uint16)
        with self.lock:
            s = self.scanner
            version = f"{self.scan_id}-coverage-{piece}-empty"
            sample = empty
            if s is not None and self.phase in self.LIVE_PHASES:
                active = self.piece_id()
                if piece != active:
                    archived = self.piece_cache.get(piece)
                    if archived is not None:
                        version, payload, count = archived
                        if version != last_version:
                            sample = coverage_preview.from_browser_payload(payload, count)
                else:
                    self.refresh_native_coverage(s, active)
                    if self.native_sample is not None and self.native_sample[0] == active:
                        _, version, sample = self.native_sample
            xyz, seen = (None, None) if version == last_version else sample
            return version, xyz, seen

    def refresh_native_coverage(self, s, piece):
        merge = getattr(s, "merge_proc", None)
        paused = self.phase == "paused"
        if paused:
            if self.paused_points is None:
                return
            xyz, _, seen = self.paused_points
            key = piece, self.phase, id(xyz)
            already_masked = True
        elif merge is not None:
            # This rail samples in the merge process, only one request at a time.
            # A browser poll may concurrently have its own full point-cloud snapshot.
            try:
                merge.poll()
                request = piece, merge.done
                if self.native_requested_at_merge != request and merge.ask_coverage():
                    self.native_requested_at_merge = request
            except RuntimeError:
                return  # do not make preview responsible for surfacing a failed merge process
            if merge.coverage_cache is None:
                return  # preserve the last good snapshot on Resume
            xyz, seen = merge.coverage_cache
            key = piece, self.phase, merge.coverage_serial
            already_masked = False
        else:
            # Fallback cloud: copy only a bounded sample and keep lock time short.
            key = piece, self.phase, s.stats["merged"]
            if key == self.native_key:
                return
            with s.cloud_lock:
                xyz, seen = coverage_preview.cloud_candidates(s.cloud)
            already_masked = False
        if key == self.native_key:
            return
        self.native_key = key
        if not already_masked:
            keep = s.output_mask(xyz, connect=False)
            xyz, seen = xyz[keep], seen[keep]
        sample = coverage_preview.sample_points(xyz, seen)
        old = self.native_sample
        if old is not None and old[0] == piece and (not len(xyz) or all(
                np.array_equal(a, b) for a, b in zip(sample, old[2]))):
            return  # no growth or an in-flight empty sample cannot blank this piece
        self.native_revision += 1
        version = f"{self.scan_id}-coverage-{piece}-{self.native_revision}"
        self.native_sample = piece, version, sample

    def current(self):
        with self.lock:
            s = self.scanner
            if s is None:
                return "empty", self.EMPTY_PAYLOAD, {"name": self.name, "points": 0, "session": {}, "phase": self.phase}
            live = self.phase in self.LIVE_PHASES
            if live:
                active = self.refresh_live_piece()
                version, payload, count = self.live_payload(1)  # never jump to an unaligned piece
            else:
                key = (self.scan_id, s.stats["merged"], self.phase, id(s.cloud), s.cloud.point_mm, len(s.pieces))
                if key != self.built_key and time.time() - self.built_at >= PAYLOAD_EVERY_S:
                    xyz, rgb, seen = s.live_points(LIVE_MIN_SEEN)
                    payload = view3d.pack_arrays(xyz @ cl.FLIP.T / 1000, rgb, seen)
                    self.cached = ("-".join(map(str, key)), payload, {"points": len(xyz)})
                    self.built_key, self.built_at = key, time.time()
                version, payload, cache = self.cached
                count = cache.get("points", 0)
            # Even if the selected model is unchanged, incoming/lost frames and their status are new.
            stats = {k: s.stats[k] for k in ("frames", "merged", "lost", "relocked", "tracking")}
            session = dict(stats, scene_mode=getattr(getattr(s, "scene", None), "mode", "still"), preset=s.preset, point_distance_mm=s.cloud.point_mm, raw_points=s.cloud.raw_points,
                           pieces=active if live else len(s.pieces), cue=dict(s.cue), crop=s.crop)
            if self.phase == "done":  # Crop axes are for a finished scan; don't recalculate on every poll.
                if "crop_axes" not in self.cached[2]:
                    self.cached[2]["crop_axes"] = s.crop_axes().round(6).tolist()
                session["crop_axes"] = self.cached[2]["crop_axes"]
            info = dict(name=self.name, points=count, phase=self.phase, session=session,
                        **view3d.mesh_versions(self.mesh_path()))
            if live:
                options = []
                for piece in sorted({1, active, *self.piece_cache}):
                    piece_version, _, piece_points = self.live_payload(piece)
                    options.append(dict(id=piece, points=piece_points, version=piece_version, active=piece == active))
                info.update(piece_options=options, active_piece=active, default_piece=1, scan_id=self.scan_id)
            if self.phase == "scanning":
                info["progress"] = self.progress.status()
                info["can_start_separate_piece"] = s.can_start_separate_piece()
                if s.piece_pending:
                    info["progress"]["hint"] = "Separate piece requested. Hold the target in view until valid depth arrives."
            return version, payload, info

    def coverage_image(self, yaw=0.0, pitch=0.0, zoom=1.0):
        """Turn the paused coverage camera; retain the volume's classifications."""
        from .controller import DEPTH_W, fit, paint_model
        from .image import write_png
        with self.busy, self.lock:
            if self.phase != 'paused' or self.scanner is None:
                raise ValueError('Pause the scan before moving coverage')
            scanner = self.scanner
            tracker = getattr(scanner, 'gpu', None)
            if tracker is None:
                raise ValueError('Coverage rotation needs GPU tracking')
            if self.paused_points is None or not len(self.paused_points[0]):
                raise ValueError('No captured surface is available yet')
            points = self.paused_points[0]
            centre = (points.min(axis=0) + points.max(axis=0)) / 2000.0
            base = scanner.pose
            cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
            rotation = (np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
                        @ np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]]))
            world = base[:3, :3] @ rotation @ base[:3, :3].T
            pose = base.copy()
            pose[:3, :3] = world @ base[:3, :3]
            pose[:3, 3] = centre + world @ (base[:3, 3] - centre) / zoom
            previous_ref = tracker.ref
            try:
                codes = tracker.ghost(pose, scanner.gpu_base(), scanner.preview_weight())
            finally:
                tracker.ref = previous_ref
            if codes is None:
                raise ValueError('Coverage is not available yet')
            codes = codes.copy()
            if yaw != 0 or pitch != 0 or zoom != 1:
                # Code 1 belongs to the unrotated sensor frame, rather than the volume.
                codes[codes == 1] = 0
            rgb = fit(np.zeros((*codes.shape, 3), np.uint8), DEPTH_W)
            paint_model(rgb, codes)
            output = io.BytesIO()
            write_png(output, rgb)
            return output.getvalue()

    def process(self, action, box=None):
        """A Process-menu action from the page, on the last finished scan."""
        from . import optimise
        with self.busy:
            if self.scanner is None or self.phase != "done":
                return "not now: use Stop & Save in the scanner before processing"
            self.phase = "optimising"
            try:
                return optimise.process(self.scanner, self.out_dir, action, log=lambda m: None, box=box)
            finally:
                self.phase = "done"
                self.built_key = None

    def mesh_path(self):
        return self.out_dir / "mesh.ply" if self.out_dir else None

    def ply_bytes(self):
        """The scan's cloud.ply: the saved file once the scan is done, else built from the cloud so far."""
        if self.phase in ("done", "idle") and self.out_dir and (self.out_dir / "cloud.ply").exists():
            return (self.out_dir / "cloud.ply").read_bytes()
        with self.lock:
            s = self.scanner
            if s is None or self.phase == "paused":
                return None  # inspection is not an export or a save
            xyz, rgb, normals, seen, _ = cl.ply_arrays(s.output_cloud(), list(s.poses))
        buf = io.BytesIO()
        pc.write_ply(buf, xyz, rgb, normals=normals, seen=seen)
        return buf.getvalue()

    def download_name(self):
        return self.out_dir.name if self.out_dir else "live-scan"


def run(commands, replies, port=view3d.PORT, auth_token=None):
    """Worker process main loop."""
    # A spawned process inherits the parent's sys.path (system Python). Put this interpreter's own
    # site-packages (the .venv) FIRST, or old system packages shadow the venv's and Open3D fails to import.
    own = [path for path in site.getsitepackages() if path.startswith(sys.prefix)]
    for path in own:
        site.addsitedir(path)
    sys.path[:] = [path for path in sys.path if path in own] + [path for path in sys.path if path not in own]
    source = LiveSource()
    try:
        server = (view3d.serve(source, port, auth_token=auth_token) if auth_token is not None
                  else view3d.serve(source, port))
    except OSError as e:
        replies.put(("log", f"3D view not available: port {port}: {e.strerror}"))
        server = None
    replies.put(("ready", f"http://127.0.0.1:{port or server.server_address[1]}/" if server else None))
    out_dir, keep_raw, recorder, capture = None, False, None, {}
    parent = multiprocessing.parent_process()
    timing = {"wait": 0.0, "raw": 0.0, "feed": 0.0, "frames": 0}  # per scan, logged at Stop
    reader = Reader(commands)
    reader.start()
    decoded_at_start = 0.0
    while True:
        t_wait = time.perf_counter()
        try:
            cmd = reader.out.get(timeout=1.0)
        except queue.Empty:
            if parent is not None and not parent.is_alive():  # viewer killed: don't linger holding the port
                if source.scanner is not None and out_dir is not None and not (out_dir / "cloud.ply").exists():
                    source.scanner.close()
                    source.scanner.save(out_dir)  # keep what was scanned
                if recorder is not None:
                    recorder.close()
                cmd = ("quit",)
            else:
                continue
        kind = cmd[0]
        timing["wait"] += time.perf_counter() - t_wait
        if kind == "start":
            timing, decoded_at_start = {k: 0 for k in timing}, reader.decode_s
            _, preset, next_dir, next_keep_raw, continue_from, depth_mode, remove_base, *options = cmd
            next_dir = Path(next_dir)
            if continue_from:
                previous, current = Path(continue_from).resolve(), next_dir.resolve()
                if previous == current or previous in current.parents or current in previous.parents:
                    replies.put(("start_failed", "cannot continue: the new take needs a separate output folder"))
                    continue
            if next_dir.exists() and (next_dir.is_symlink() or not next_dir.is_dir() or any(next_dir.iterdir())):
                replies.put(("start_failed", "cannot start: output folder is not a new empty take"))
                continue
            options = options[0] if options else {}
            options = {"tracker": options} if isinstance(options, str) else options
            scene_mode = options.get("scene_mode", "still")
            if scene_mode not in ("still", "free"):
                replies.put(("start_failed", "unknown subject mode"))
                continue
            capture = options.get("capture", {})
            recorder = None
            log = lambda m: replies.put(("log", m))
            with source.busy, source.lock:
                previous_mode = calib.CAMERA.mode
                calib.use_mode(depth_mode)  # keep a processing job's camera mode stable until it finishes
                if continue_from:  # go on with a saved scan: it re-finds its place, then merges into it
                    try:
                        source.scanner = cl.Scanner.continue_from(continue_from, log=log)
                    except (OSError, ValueError) as e:
                        calib.use_mode(previous_mode)
                        replies.put(("start_failed", f"cannot continue: {e}"))
                        continue
                    source.scanner.remove_base = remove_base
                    log(f"continuing {Path(continue_from).name}: show the scanner a part it has seen")
                else:
                    source.scanner = cl.Scanner(preset, log=log, mode=scene_mode, remove_base=remove_base,
                                                track_with=options.get("tracker", "gpu"), live_view=True,
                                                merge_process=True, aim=capture.get("aim"))
                capture = dict(capture, scene_mode=getattr(source.scanner.scene, "mode", scene_mode))
                if capture:
                    source.scanner.stats["capture"] = capture  # (session.json takes every stats key)
                out_dir, keep_raw = next_dir, next_keep_raw
                source.scan_id = uuid4().hex
                source.name = f"live: {out_dir.name}"
                source.phase, source.out_dir = "scanning", out_dir
                source.clear_preview(piece_origin=len(source.scanner.pieces))
                source.scanner.on_piece_finished = source.remember_piece
                source.progress = ScanProgress()
        elif kind == "frame" and source.phase == "scanning" and source.scanner is not None:
            _, yuyv, grey, depth_buf, bytesperline, (t_c, t_i, t_d), depth4, cleared = cmd
            t0 = time.perf_counter()
            if keep_raw:
                try:
                    if recorder is None:
                        recorder = RawCopy(fr.Recorder(bytesperline, root=out_dir,
                                                       extra={"note": "frames a live scan processed", **capture}))
                        with source.lock:  # its frame 0 is the scanner's next index (continued scans count on)
                            source.scanner.add_source(recorder.recorder.dir.resolve(), source.scanner.stats["frames"])
                    if recorder.error is not None:
                        raise recorder.error
                    recorder.put({"color": yuyv, "ir": grey, "depth": depth_buf}, {"color": t_c, "ir": t_i, "depth": t_d})
                except OSError as e:
                    keep_raw = False
                    if recorder is not None:
                        recorder.close()
                        recorder = None
                    replies.put(("log", f"raw copy stopped, the scan goes on: {e}"))
            t2 = time.perf_counter()
            frame = LazyFrame(yuyv=yuyv, depth_raw=depth_buf, bytesperline=bytesperline, depth4=depth4, cleared=cleared,
                              shape=(fr.H, fr.W), t=t_d, skew_ms=(t_c - t_d) * 1000)  # colour per point
            with source.lock:
                s = source.scanner
                before_piece = len(s.pieces)
                s.feed(frame)
                new_piece = len(s.pieces) != before_piece
                points = s.points_kept  # (the cloud lock is held through each ~90 ms merge: no waiting for it)
                cue = dict(s.cue, base=s.scene.plane is not None, turning=s.object_turns, piece=source.piece_id(),
                           can_start_separate_piece=s.can_start_separate_piece(),
                           reference_ok=getattr(s, "unconfirmed", None) is None,
                           coverage_supported=getattr(s, "track_with", None) == "gpu")
                source.progress.observe(s.stats["frames"], s.stats["merged"], points, s.stats["tracking"], cue)
            if new_piece and recorder is not None:
                recorder.piece_start()
            timing["raw"] += t2 - t0
            timing["feed"] += time.perf_counter() - t2
            timing["frames"] += 1
            replies.put(("frame_done", s.stats["merged"], s.stats["frames"], points, s.stats["tracking"],
                         cue, view_mask(s.last_view),
                         s.view_codes, t_d))
        elif kind == "frame":
            replies.put(("frame_done", 0, 0, 0))
        elif kind == "coverage_preview":
            _, piece, previous_version = cmd
            version, xyz, seen = source.native_coverage(piece, previous_version)
            replies.put(("coverage_frame", piece, version, xyz, seen))
        elif kind == "pick" and source.phase == "scanning" and source.scanner is not None:  # a click on the preview: the object is there
            _, how, uv = cmd
            with source.lock:
                source.scanner.pick(how, uv)
            if recorder is not None:
                recorder.pick(how, uv)  # (a replay of the raw copy takes it at the same frame)
        elif kind == "start_separate_piece":
            with source.lock:
                accepted = (source.phase == "scanning" and source.scanner is not None
                            and source.scanner.request_separate_piece())
            replies.put(("log", "Separate piece requested; waiting for usable depth." if accepted else
                         "Separate piece not started: requires a live, lost scan with an existing model and no pending request."))
        elif kind == "pause" and source.phase == "scanning" and source.scanner is not None:
            with source.lock:
                s = source.scanner
                s.sync_merges()  # preserve the merge process and tracking state
                xyz, rgb, seen = s.cloud_points(LIVE_MIN_SEEN)
                keep = s.output_mask(xyz, connect=False)
                source.paused_points = xyz[keep], rgb[keep], seen[keep]
                source.phase, source.built_key, source.built_at = "paused", None, 0.0
                source.piece_sampled_at = float("-inf")
            if recorder is not None:
                recorder.drain()
            replies.put(("paused",))
        elif kind == "resume" and source.phase == "paused":
            with source.lock:
                source.phase, source.built_key, source.built_at = "scanning", None, 0.0
                source.paused_points = None
                source.piece_sampled_at, source.piece_sampled_key = float("-inf"), None
                source.progress.resume()
            replies.put(("resumed",))
        elif kind == "cancel":
            _, take_name = cmd
            if (source.phase not in ("scanning", "paused") or out_dir is None or out_dir.name != take_name):
                replies.put(("cancelled", "", "No active matching take to cancel."))
                continue
            # Detach the scan before shutting down. A closing parent mustn't make it autosave.
            with source.lock:
                scanner, cancelled_dir = source.scanner, out_dir
                source.reset()
                out_dir, keep_raw = None, False
            errors = []
            if recorder is not None:
                try:
                    recorder.close()
                    if recorder.error is not None:
                        errors.append(f"raw data incomplete: {recorder.error}")
                except Exception as e:
                    errors.append(f"could not close raw recording: {e}")
                recorder = None
            try:
                scanner.on_piece_finished = None
                scanner.discard()
            except Exception as e:
                errors.append(f"could not release scan resources cleanly: {e}")
            try:
                moved = fr.stash_cancelled(cancelled_dir)
            except OSError as e:
                moved = cancelled_dir  # never delete; identify where recovery data stayed
                errors.append(f"could not move recovery data: {e}")
            replies.put(("cancelled", str(moved) if moved is not None else "", "; ".join(errors)))
        elif kind == "stop" and source.phase in ("scanning", "paused") and source.scanner is not None:
            if recorder is not None:
                recorder.close()
                if recorder.error is not None:
                    replies.put(("log", f"raw copy incomplete: {recorder.error}"))
                recorder = None
            with source.lock:
                source.paused_points = None
                source.piece_cache.clear()
                source.scanner.on_piece_finished = None
                source.scanner.close()  # the GPU tracker's memory back (Optimise and the mesh come next)
                if len(source.scanner.pieces) > 1:  # its pieces into one world first (what joins nothing: piece-N.ply)
                    try:
                        from . import optimise
                        optimise.join_pieces(source.scanner, log=lambda m: replies.put(("log", m)))
                    except ImportError:
                        pass
                source.scanner.settle_base()  # (the base onto a support the object stands on)
                path, kept = source.scanner.save(out_dir)
                source.name = out_dir.name
                source.phase = "optimising"
            replies.put(("saved", str(path), kept))
            n = max(timing["frames"], 1)
            replies.put(("log", f"worker: {timing['frames']} frames; per frame: tracking {1000 * timing['feed'] / n:.0f} ms, "
                                f"raw copy {1000 * timing['raw'] / n:.0f} ms, waiting for frames "
                                f"{1000 * timing['wait'] / n:.0f} ms, decode beside tracking "
                                f"{1000 * (reader.decode_s - decoded_at_start) / n:.0f} ms"
                                f"; lost {source.scanner.stats['lost']}, found again {source.scanner.stats['relocked']} "
                                f"({source.scanner.stats['shape_refinds']} by shape)"
                                + (f"; GPU {source.scanner.stats['gpu_ms']}" if source.scanner.stats.get("gpu_ms") else "")))
            make_mesh = len(cmd) > 1 and bool(cmd[1])
            try:  # Optimise right away (user decision): loop closures, pose graph, merge again
                from . import optimise
                with source.busy:
                    message = optimise.process(source.scanner, out_dir, "optimise", log=lambda m: None)
                replies.put(("optimised", message, make_mesh))
                if make_mesh:  # and the mesh (the 3D page's Make mesh, ~15-20 s on the GPU): a finished model
                    source.phase = "meshing"
                    try:
                        with source.busy:
                            message = optimise.process(source.scanner, out_dir, "mesh", log=lambda m: None)
                    except Exception as e:  # (the viewer waits for this reply)
                        message = f"mesh not made: {e}"
                    replies.put(("meshed", message))
            except ImportError:
                replies.put(("optimised", "not optimised: needs Open3D (.venv)", False))
            finally:
                source.phase, source.built_key = "done", None
        elif kind == "quit":
            if server:
                server.shutdown()
            with source.busy:
                pass  # an accepted HTTP processing job must finish before its worker exits
            if server:
                server.server_close()
            return
