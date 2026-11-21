"""Live viewer: depth in the middle, colour and IR on the left, setting panels on the right.

Run with `python -m tribuutscan.viewer` (`--force-cpu`: tracking and meshes on the CPU even with a GPU).
Panels (panels.py, each folds):
- Scanner: Range switches close range (15-33 cm) / long range (20-72 cm; the law from the scanner's own
  disparity offsets);
  the streams pause about 10 s while it switches (vendor.py). IR exposure and gain are controls on the
  depth function: they change the IR sensor that depth is computed from (14-bit USB depth, usbdepth.py,
  then owns them). Auto IR (on by default) is the scanner's own auto exposure; moving an IR slider takes
  them over by hand.
- Capture: `s` saves the current frame set to captures/<timestamp>/; `r` records colour + depth sets
  at 30 or 10 fps (and IMU samples, imu.csv) to captures/<timestamp>-rec/, stopping when the disk has
  under 5 GB free (frames.Recorder); `m` tracks, optimises and fuses the latest recording into
  <stamp>-scan/mesh.ply and mesh.stl (cloud.py --optimise --mesh, fusion.py, in the Open3D .venv).
- Scan: `c` merges frames live into one compact cloud (cloud.py) in a worker process; the Live 3D model pane
  shows it growing next to the camera previews. Stop saves captures/<timestamp>-scan/.
- IMU: the scanner's accelerometer and gyroscope (imu.py): tilt from gravity, turn rate.
- View: markers (white dot in a black ring) are found in the colour image a few times a second and
  ringed in the colour view; saved frames list them in info.json.
The distance bar reads the centre of the depth image. Events go to the message line, the console
and captures/viewer.log. One viewer at a time (captures/.viewer.lock). When the scanner resets its USB
connection (it does so on its own now and then), the viewer reconnects by itself.
"""
import argparse
import collections
import fcntl
import itertools
import math
import multiprocessing
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font
import tkinter.messagebox
from pathlib import Path

import numpy as np

from . import frames as fr
from . import calib, cloud, gpu, imu, livescan, markers, panels, pointcloud, sysinfo, usbdepth, v4l2, vendor
from .depth import decode_depth, decode_strided, garbled
from .image import depth_to_rgb, ppm, yuyv_pixels

SIDE_W, DEPTH_W = 272, 552  # Colour and IR side by side underneath Depth
MODEL_W, MODEL_H = 580, 450
PREVIEW_FPS = 15  # previews redraw at most this often; every frame is still captured and paired
SCAN_SIDE_FPS = 5  # the colour and IR previews while scanning (the depth preview, the live model, stays at 15)

# (label, node, control id): what the Scanner panel's sliders drive.
SLIDERS = [
    ("IR exposure", "depth", v4l2.CID_EXPOSURE),
    ("IR gain", "depth", v4l2.CID_GAIN),
    ("Colour brightness", "color", v4l2.CID_BRIGHTNESS),
]
PANELS = [panels.ScanPanel, panels.ScannerPanel, panels.CapturePanel, panels.ImuPanel, panels.ViewPanel]

IR_LABELS = ("IR exposure", "IR gain")  # the sliders the scanner's own auto exposure drives while Auto IR is on
IR_FOLLOW_S = 1.0  # ...and they show its values, read back this often

COUNTDOWN_S = 3  # Start counts down this long (Scan panel: Countdown), the aim still updating
RECORD_RATES = {"30 fps": 30, "10 fps": 10}  # colour + compressed depth: about 75 / 25 MB/s (frames.Recorder)

GOOD, EDGE, BAD = "#286333", "#92510a", "#a92222"

REPO = Path(__file__).resolve().parent.parent
VENV_PYTHON = REPO / ".venv" / "bin" / "python"  # has Open3D (uv sync --all-extras)


LOG = fr.CAPTURES / "viewer.log"
LOCK = fr.CAPTURES / ".viewer.lock"
RETRY_S = 10.0  # after a failed reconnect, try again this much later


def latest_recording():
    recordings = sorted(fr.CAPTURES.glob("*-rec"))
    return recordings[-1] if recordings else None


def mesh_scope(log):
    """Use a capped scope only when this session can reach its user service manager."""
    if not shutil.which("systemd-run"):
        return []
    try:
        subprocess.run(["systemctl", "--user", "show", "--property=Version", "--value"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        log("systemd user session unavailable: meshing will run without the separate memory limit")
        return []
    return ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=8G"]


def preview_index(size, out):
    """Source indices that sample `size` pixels down (or up) to `out` (nearest neighbour)."""
    return np.arange(out) * size // out


def fit(img, width):
    """An image resampled to `width` pixels across (aspect kept)."""
    h, w = img.shape[:2]
    return img[np.ix_(preview_index(h, round(h * width / w)), preview_index(w, width))]


class Stream(threading.Thread):
    """Reads one node in the background; keeps the latest raw frame, its preview and stats.

    `stamp` is the camera's capture time (kernel timestamp, time.monotonic() scale); the last few
    frames are kept so `nearest(t)` can pair streams by time instead of taking whatever is latest.
    """

    def __init__(self, cap, render):
        super().__init__(daemon=True)
        self.cap = cap
        self.resumed, self.idle = threading.Event(), threading.Event()  # pause() / resume(cap)
        self.render = render
        self.latest = None
        self.version = 0  # bumped with each new preview (the window redraws only on change)
        self.stats = {}
        self.raw = None
        self.stamp = 0.0
        self.recent = collections.deque(maxlen=12)  # (stamp, raw): ~0.4 s, so pairing works under load
        self.fps = 0.0
        self.fresh = threading.Event()  # set on every new frame (the scan feeder waits on the depth stream's)
        self.preview_fps = PREVIEW_FPS  # (lowered for colour and IR while scanning: SCAN_SIDE_FPS)

    def run(self):
        while True:
            try:
                self._stream()
            except OSError:  # the device went away mid-stream: wait for resume() like after pause()
                pass
            self.idle.set()
            self.resumed.wait()
            self.resumed.clear()
            self.idle.clear()

    def _stream(self):
        n, t0, rendered = 0, time.time(), 0.0
        for buf in self.cap.frames():
            if len(buf) < self.cap.sizeimage:  # truncated USB transfer
                continue
            self.raw, self.stamp = buf, self.cap.last_time
            self.recent.append((self.stamp, buf))
            self.fresh.set()
            n += 1  # the fps shown is the camera's rate, not the preview's
            if time.time() - t0 >= 1:
                self.fps, n, t0 = n / (time.time() - t0), 0, time.time()
            if self.cap.ready() or time.time() - rendered < 1 / self.preview_fps:
                continue  # behind, or a preview is fresh enough: every frame still goes to `recent`
            rendered = time.time()
            self.latest, self.stats = self.render(self.cap, buf)
            self.version += 1

    def nearest(self, t):
        """(raw, stamp) of the kept frame captured closest to time t."""
        recent = list(self.recent)
        if not recent:
            return self.raw, self.stamp
        stamp, raw = min(recent, key=lambda sr: abs(sr[0] - t))
        return raw, stamp

    def pause(self, timeout=8.0):
        """Close the capture and wait until this thread has let go of it (a pending read ends within 5 s),
        so a new capture can't be read through the old one's file descriptor."""
        self.cap.close()
        self.idle.wait(timeout)
        self.fps = 0.0

    def resume(self, cap):
        self.cap = cap
        self.recent.clear()
        self.raw, self.stamp, self.latest, self.stats = None, 0.0, None, {}
        self.fresh.clear()
        self.idle.clear()  # running again from now on, before the thread wakes (the reconnect check reads it)
        self.resumed.set()

    def stop(self):
        self.cap.close()


INFO_EVERY_S = 0.5  # the Status box's refresh
LABEL_W = 12  # its label column (characters)
MESSAGES_KEPT = 300  # lines the Messages box keeps
MARKER_HZ = 5  # marker search rate; one search takes ~0.1 s at half resolution
SCAN_MARKER_HZ = 1  # ...while scanning: the worker finds markers in its own frames, this ring is display only
found_markers = {"on": True, "list": [], "stamp": 0.0, "hz": MARKER_HZ}  # list in full-resolution pixels
# While scanning, the worker says what each frame was (livescan.view_mask: 0 other, 1 base, 2 object):
# the depth preview dims all but the object (the base is tinted while it is kept), marks its centre (the
# first frame's object is what the centre hits) and has a red frame while tracking is lost. With the GPU
# tracker it also sends the live model view of that frame (gpu.preview_codes), painted over the object
# (paint_model); the preview then shows the frame the worker tracked, so model and depth agree.
LIVE_DEPTH, COVERAGE = "Live depth", "Coverage"
scan_view = {"active": False, "return_guide": False, "preview": LIVE_DEPTH, "mask": None, "t": 0.0,
             "keep_base": False, "lost": False, "model": None}  # model: (codes, depth raw)
# Outside a scan the depth preview holds each pixel's last depth for a moment (display only: scans take the
# real frames). On some surfaces the scanner drops whole regions from one frame to the next (2026-11-05, the
# test block on the white platform, scanner still: 2-6% of the centre per frame, 10% over 30 frames, none of
# it in 90% of them, at every IR exposure), and the raw preview flickers. Held pixels are drawn dimmer; the
# hold is longer while the IMU says the scanner is still, short while it moves (old depth would smear).
HOLD_S = {"still": 0.5, "moving": 0.15, "no imu": 0.3}
HELD_SHADE = 0.6
hold_view = {"on": True, "s": HOLD_S["no imu"], "depth": None, "t": None, "mode": None}
# The aim, before Start: a few times a second the idle viewer finds the piece a scan would start on, the way the
# scan's first frame does (cloud.isolate_mask on every 4th depth pixel), outlines it on the depth and colour
# previews and dims the rest. A click on either preview picks another piece; the aim then follows that piece (the
# point nearest its last centre) until Start hands the scan the pixel it is at (Scanner(aim=...)). Right-click or
# Esc: back to the image centre, which is what a scan without an aim takes (2026-11-05, the dark-hand scene: the
# centre fell just beside the hand and took the shelf behind it).
AIM_HZ = 5
AIM_FRESH_S = 1.0  # an aim older than this is neither drawn nor handed to a scan (streams paused, no depth)
AIM_FOLLOW_MM = 40.0  # a picked piece is followed to the point nearest its last centre when one is this close
AIM_KEEP = 5  # the depth-on-the-object share shown is the mean of this many aims (about a second)
AIM_RGB = np.array([0, 229, 255], np.uint8)
AIM_DIM = 2  # the rest of the depth preview is divided by this while aiming
aim_view = {"on": True, "idle": False, "pick": None, "follow": None, "piece": None, "all": None, "t": 0.0}
# piece: {"pix" (N, 2) depth pixels of its points, "colour" (N, 2) colour pixels, "z" median mm, "at" (u, v) the
# pixel handed to a scan (None: the image centre), "plane" a support plane was cut, "coverage" depth share of its
# box (mean of AIM_KEEP)}; all: (xyz, pix) of the frame, for a click on the colour preview
BASE_TINT = np.array([0, 40, 110], np.uint8)
LOST_RGB, LOST_PX = np.array([198, 40, 40], np.uint8), 8
MODEL_DONE, MODEL_THIN = np.array([100, 205, 255]), np.array([255, 205, 65])
MODEL_MISSING = np.array([238, 112, 198])
MODEL_LEGEND = "Coverage guide"
COVERAGE_SWATCHES = (("Observed enough", "#64cdff"), ("Another pass", "#ffcd41"), ("Not captured", "#ee70c6"))



def find_markers_loop(color):
    """Background thread: search the latest colour frame for markers at half resolution."""
    while True:
        t = time.time()
        if found_markers["on"] and color.raw and color.stamp != found_markers["stamp"]:
            found_markers["stamp"] = color.stamp
            luma = np.frombuffer(color.raw, np.uint8, color.cap.width * color.cap.height * 2)[0::2]
            found_markers["list"] = markers.detect_fast(luma.reshape(color.cap.height, color.cap.width))
        elif not found_markers["on"]:
            found_markers["list"] = []
        time.sleep(max(0.0, 1 / found_markers["hz"] - (time.time() - t)))


def render_color(cap, buf, width=SIDE_W):
    ys = preview_index(cap.height, round(cap.height * width / cap.width))
    v, u = np.meshgrid(ys, preview_index(cap.width, width), indexing="ij")  # convert only the shown pixels
    rgb = yuyv_pixels(buf, cap.width, u.ravel(), v.ravel()).reshape(v.shape + (3,))
    hits, s = found_markers["list"], width / cap.width
    if hits:
        rgb = markers.draw(rgb, [(x * s, y * s, r * s, c) for x, y, r, c in hits])
    piece = fresh_aim()
    if piece is not None:  # the aimed piece's outline, where the colour camera sees it
        paint_outline(rgb, piece_mask(piece["colour"], rgb.shape[:2], (cap.height, cap.width), grow_px=2))
    return ppm(rgb), {"text": f"markers {len(hits)}" if found_markers["on"] else ""}


def render_ir(cap, buf, width=SIDE_W):
    g = np.frombuffer(buf, np.uint8, cap.width * cap.height).reshape(cap.height, cap.width)
    g = fit(g[:, ::-1], width)  # IR is mirrored relative to colour/depth
    mean, clipped = float(g.mean()), 100 * float((g >= 250).mean())
    stats = {"mean": mean, "clipped": clipped, "text": f"mean {mean:.0f}  clipped {clipped:.1f}%"}
    return ppm(np.repeat(g[..., None], 3, axis=2)), stats


def render_depth(cap, buf, width=DEPTH_W):
    now = time.monotonic()
    view_state = scan_view.copy()
    scanning = view_state["active"]
    returning = scanning and view_state["return_guide"]
    coverage = scanning and not returning and view_state["preview"] == COVERAGE
    fresh_coverage = coverage and now - view_state["t"] < 1.0
    model = view_state["model"] if fresh_coverage and not view_state["lost"] else None
    h, w, stride = cap.height, cap.width, cap.bytesperline
    mode = calib.CAMERA.mode
    start, end = h * 3 // 8, h * 5 // 8
    if model is None:
        d = decode_depth(buf, w, h, stride)
        centre_rows = d[start:end]
    else:
        model, model_buf = model  # render the corresponding earlier frame so the overlay lines up
        d = decode_depth(model_buf, w, h, stride)
        # Read just the centre rows from the current frame without decoding a second full image.
        centre_rows = decode_depth(memoryview(buf)[start * stride:end * stride], w, end - start, stride)
    # Distance is guidance for the current frame, never a held pixel or the tracked model.
    centre = centre_rows[::2, w * 3 // 8:w * 5 // 8:2]
    valid = centre[centre > 0]
    stats = {
        "text": f"valid {100 * (d > 0).mean():.0f}%",
        "centre_valid": valid.size / centre.size if centre.size else 0.0,
        "centre_cm": float(pointcloud.depth_to_mm(np.median(valid))) / 10 if valid.size else None,
        "centre_at": now,
        "centre_mode": mode,
    }
    shown, held = fit(d, width), None  # (colour-map only the shown pixels)
    if hold_view["on"] and not scanning:
        shown, held = hold_depth(shown, now)
        stats["text"] += f" now, {100 * (shown > 0).mean():.0f}% held"
    rgb = depth_to_rgb(shown)
    if held is not None:
        rgb[held] = (rgb[held] * HELD_SHADE).astype(np.uint8)
    view = view_state["mask"] if fresh_coverage and not view_state["lost"] else None
    if view is not None:
        lab = view[np.ix_(preview_index(view.shape[0], rgb.shape[0]), preview_index(view.shape[1], rgb.shape[1]))]
        rgb[lab == 0] //= 4
        base = lab == 1
        rgb[base] = rgb[base] // 2 + BASE_TINT if view_state["keep_base"] else rgb[base] // 4
    if model is not None:
        paint_model(rgb, model)
        stats["text"] += "   " + MODEL_LEGEND
        counts = visible_coverage(model, view)
        stats["coverage"] = coverage_caption(counts)
    elif coverage:
        stats["coverage"] = ("Tracking lost · Coverage paused" if view_state["lost"] else
                             "CPU tracking · Coverage colours unavailable"
                             if fresh_coverage and view_state.get("coverage_supported") is False else
                             "Waiting for fresh coverage")
    elif scanning:
        stats["text"] += "   live depth · compare with last tracked view" if returning else "   live depth"
    piece = None if scanning else fresh_aim()
    if piece is not None:  # the aim: the piece a scan would start on, outlined; the rest dimmed
        mask = piece_mask(piece["pix"], rgb.shape[:2], (cap.height, cap.width), grow_px=2)
        rgb[~mask] //= AIM_DIM
        paint_outline(rgb, mask)
        at = piece["at"]
        if at is None:
            mark_centre(rgb)
        else:
            mark_centre(rgb, at=(at[0] * rgb.shape[1] / cap.width, at[1] * rgb.shape[0] / cap.height))
    if scanning:
        mark_centre(rgb)
        if view_state["lost"]:
            b = LOST_PX
            rgb[:b], rgb[-b:], rgb[:, :b], rgb[:, -b:] = LOST_RGB, LOST_RGB, LOST_RGB, LOST_RGB
    return ppm(rgb), stats


def fresh_aim():
    """The aimed piece (aim_view["piece"]) if it is recent and the viewer is idle, else None."""
    piece = aim_view["piece"]
    if piece is None or not aim_view["idle"] or time.monotonic() - aim_view["t"] > AIM_FRESH_S:
        return None
    return piece


def grow(mask, px):
    """`mask` grown by `px` pixels (4-neighbour steps)."""
    out = mask.copy()
    for _ in range(px):
        g = out.copy()
        g[1:] |= out[:-1]
        g[:-1] |= out[1:]
        g[:, 1:] |= out[:, :-1]
        g[:, :-1] |= out[:, 1:]
        out = g
    return out


def piece_mask(points, shape, source_shape, grow_px=2):
    """A preview-sized mask (shape) of the pixels (N, 2: x, y in a source_shape image) of a piece's points,
    grown by grow_px to close the gaps between them (the aim takes every 4th pixel)."""
    h, w = shape
    mask = np.zeros(shape, bool)
    if len(points):
        x = (np.asarray(points[:, 0]) * w / source_shape[1]).astype(int)
        y = (np.asarray(points[:, 1]) * h / source_shape[0]).astype(int)
        ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
        mask[y[ok], x[ok]] = True
    return grow(mask, grow_px)


def paint_outline(rgb, mask, width=2):
    """Paint the edge of `mask` (its outermost `width` pixels) in AIM_RGB."""
    edge = mask & grow(~mask, width)
    rgb[edge] = AIM_RGB


def aim_once(cap, buf, history, rng_seed=0):
    """One aim (see aim_view): the piece a scan starting now would take, as isolate_mask finds it at the
    image centre, or at the user's pick (followed by its 3D centre)."""
    mode = calib.CAMERA.mode
    depth4, _ = decode_strided(buf, cap.width, cap.height, cap.bytesperline)
    shape = (cap.height, cap.width)
    xyz, _, pix = cloud.frame_points({"depth4": depth4, "shape": shape}, 4, colour=False)
    if len(xyz) < 50:
        aim_view.update(piece=None, all=None, t=time.monotonic())
        return None
    at, follow = aim_view["pick"], aim_view["follow"]
    if at is not None and follow is not None:  # follow the picked piece: the point nearest its last centre
        dist = np.linalg.norm(xyz - follow, axis=1)
        i = int(np.argmin(dist))
        if dist[i] <= AIM_FOLLOW_MM:
            at = (float(pix[i, 0]), float(pix[i, 1]))
    keep, plane = cloud.isolate_mask(xyz, pix, shape, np.random.default_rng(rng_seed), at=at)
    if not keep.any():
        aim_view.update(piece=None, all=(xyz, pix), t=time.monotonic())
        return None
    p, z = pix[keep], xyz[keep, 2]
    cells = ((p[:, 0].max() - p[:, 0].min()) // 4 + 1) * ((p[:, 1].max() - p[:, 1].min()) // 4 + 1)
    history.append(len(p) / cells)
    if at is not None:  # hand the scan a pixel on the piece (its middle point), and follow it from here
        mid = xyz[keep].mean(0)
        aim_view["follow"] = mid
        j = int(np.argmin(np.linalg.norm(xyz[keep] - mid, axis=1)))
        at = (float(p[j, 0]), float(p[j, 1]))
    colour = np.stack(calib.CAMERA.colour_px(p[:, 0], p[:, 1], z), -1)
    piece = {"pix": p, "colour": colour, "z": float(np.median(z)), "at": at, "plane": plane is not None,
             "coverage": float(np.mean(history)), "points": int(len(p)), "mode": mode}
    aim_view.update(piece=piece, all=(xyz, pix), t=time.monotonic())
    return piece


def aim_loop(depth):
    """Background thread: aim_once on the newest depth frame AIM_HZ times a second while the viewer is idle."""
    history = collections.deque(maxlen=AIM_KEEP)
    while True:
        t = time.time()
        if aim_view["on"] and aim_view["idle"] and depth.raw is not None and depth.fps > 0:
            try:
                aim_once(depth.cap, depth.raw, history)
            except Exception:  # noqa: BLE001 - a stream switching under it; the next round tries again
                aim_view["piece"] = None
        else:
            history.clear()
        time.sleep(max(0.0, 1 / AIM_HZ - (time.time() - t)))


def aim_hint(piece, ir_stats, imu_state, markers_on):
    """(text, colour) for the secondary guidance under the distance gauge while aiming."""
    if piece is None:
        return "No object identified: aim the centre of the depth view at it.", MUTED_GUIDE
    parts, worst = [], GOOD
    rank = {GOOD: 0, EDGE: 1, BAD: 2}

    def add(text, colour=GOOD):
        nonlocal worst
        parts.append(text)
        worst = colour if rank[colour] > rank[worst] else worst

    c = piece["coverage"]
    add(f"depth {c:.0%}" + (": little, try another angle" if c < 0.15 else ""),
        BAD if c < 0.15 else EDGE if c < 0.4 else GOOD)
    clipped = (ir_stats or {}).get("clipped")
    if clipped is not None and clipped > 5:
        add(f"IR clipping {clipped:.0f}%", EDGE)
    if not piece["plane"]:
        tilt = imu_state["pitch"] if imu_state else None
        add("no base" + (f", {abs(tilt):.0f}° down" if tilt is not None and tilt < -3 else "") + " (ok on a platform)")
    if markers_on and found_markers["list"]:
        add(f"markers {len(found_markers['list'])}")
    return ("Picked: " if piece["at"] is not None else "") + " | ".join(parts), worst


def hold_depth(depth, now):
    """(depth to show, held mask) for one preview-sized depth image: each pixel keeps its last depth for
    hold_view["s"] seconds after it had one (held: shown from an earlier frame, not this one). A change of
    size or depth mode starts afresh (the raw values mean other distances)."""
    hv = hold_view
    if hv["depth"] is None or hv["depth"].shape != depth.shape or hv["mode"] != calib.CAMERA.mode:
        hv.update(depth=np.zeros_like(depth), t=np.full(depth.shape, -np.inf), mode=calib.CAMERA.mode)
    valid = depth > 0
    hv["depth"][valid] = depth[valid]
    hv["t"][valid] = now
    held = ~valid & (now - hv["t"] < hv["s"])
    return np.where(valid, depth, np.where(held, hv["depth"], 0)), held


def visible_coverage(codes, mask):
    """Counts of classified cells in the visible object only; zero is unknown, not a hole."""
    if mask is None:
        return None
    rows = preview_index(mask.shape[0], codes.shape[0])
    cols = preview_index(mask.shape[1], codes.shape[1])
    values = codes[(codes > 0) & (mask[np.ix_(rows, cols)] == 2)]
    if not values.size:
        return None
    return (int(np.count_nonzero(values >= 128)), int(np.count_nonzero((values >= 2) & (values < 128))),
            int(np.count_nonzero(values == 1)))


def coverage_caption(counts):
    if counts is None:
        return "No classified visible-object cells · Not a measure of accuracy"
    names = ("observed enough", "another pass", "not captured")
    portions = [f"{100 * n / sum(counts):.0f}% {name}" for n, name in zip(counts, names)]
    return "Visible object, classified only · " + " · ".join(portions) + " · Not a measure of accuracy"


def coverage_status(stats, now, phase, blocked, tracking_lost, reference_shown):
    if blocked:
        return "Coverage unavailable · Scanner not ready"
    if phase not in ("scanning", "pausing", "resuming"):
        return "Coverage is shown during a live scan"
    if tracking_lost:
        return ("Tracking lost · Compare live Depth with Last tracked view" if reference_shown else
                "Tracking lost · Coverage paused")
    if now - stats.get("centre_at", -float("inf")) < 1.0:
        return stats.get("coverage", "Waiting for fresh coverage")
    return "Waiting for fresh coverage"


def paint_model(rgb, codes):
    """Render the three worker classifications with distinct hues and patterns."""
    lab = codes[np.ix_(preview_index(codes.shape[0], rgb.shape[0]), preview_index(codes.shape[1], rgb.shape[1]))]
    done, thin, missing = lab >= 128, (lab >= 2) & (lab < 128), lab == 1
    light = 0.70 + 0.30 * np.where(done, (lab - 128.) / 127, (lab - 2.) / 125)
    rgb[done] = (MODEL_DONE * light[done][:, None]).astype(np.uint8)
    rgb[thin] = (MODEL_THIN * light[thin][:, None]).astype(np.uint8)
    rgb[missing] = MODEL_MISSING
    y, x = np.ogrid[:rgb.shape[0], :rgb.shape[1]]
    pattern = (thin & ((x + y) % 14 < 2)) | (missing & (x % 10 < 2) & (y % 10 < 2))
    rgb[pattern] //= 2


def mark_centre(rgb, arm=10, gap=4, at=None):
    """A white cross (2 px lines, open in the middle) at the centre of a preview, or at preview pixel `at`."""
    h, w = rgb.shape[:2]
    cy, cx = (h // 2, w // 2) if at is None else (int(np.clip(at[1], arm + gap, h - arm - gap - 1)),
                                                   int(np.clip(at[0], arm + gap, w - arm - gap - 1)))
    for a, b in ((cx - gap - arm, cx - gap), (cx + gap, cx + gap + arm)):
        rgb[cy - 1:cy + 1, a:b] = 255
    for a, b in ((cy - gap - arm, cy - gap), (cy + gap, cy + gap + arm)):
        rgb[a:b, cx - 1:cx + 1] = 255


# Recommended working distances (README), not hard detection limits or accuracy guarantees.
DISTANCE_BANDS = {"Close": (18, 30), "Long": (30, 50)}
DISTANCE_STATES = ("Too close", "Optimum", "Too far")
MUTED_GUIDE = panels.MUTED


def distance_guide(piece, stats, mode, estimated, now):
    """Return (active segment or None, reading and advice) for the depth-distance gauge."""
    name = "Long" if mode.startswith("Long") else "Close"
    near, far = DISTANCE_BANDS[name]
    target = f"{name} range target: {near}–{far} cm"
    cm, source = None, None
    if piece is not None and piece.get("mode") == mode and piece.get("coverage", 0) >= 0.15:
        z = piece.get("z")
        if z is not None and math.isfinite(z) and z > 0:
            cm, source = z / 10, "Picked object" if piece.get("at") is not None else "Object"
    age = now - stats.get("centre_at", -math.inf)
    if cm is None and stats.get("centre_mode") == mode and 0 <= age <= AIM_FRESH_S:
        centre_cm = stats.get("centre_cm")
        if stats.get("centre_valid", 0) >= 0.2 and centre_cm is not None and math.isfinite(centre_cm) and centre_cm > 0:
            cm, source = centre_cm, "Centre"
    if cm is None:
        return None, f"No reliable depth · Aim at the object · {target}"
    i = 0 if cm < near else 2 if cm > far else 1
    estimate = " (estimate)" if estimated else ""
    advice = ("Move back", "Hold this distance", "Move closer")[i]
    return i, f"{source}: ~{cm:.1f} cm{estimate} · {advice} · {target}"


def render_tracked_reference(raw, dimensions, view, width=256):
    """A thumbnail of an acknowledged frame; optional outline is the worker's object mask."""
    w, h, stride = dimensions
    depth = decode_depth(raw, w, h, stride)
    rows = depth[h * 3 // 8:h * 5 // 8:2, w * 3 // 8:w * 5 // 8:2]
    centre_valid = rows[rows > 0]
    cm = (float(pointcloud.depth_to_mm(np.median(centre_valid))) / 10
          if rows.size and centre_valid.size >= rows.size * 0.2 else None)
    rgb = depth_to_rgb(fit(depth, width))
    outlined = False
    if view is not None:
        # The compact view mask uses 2 for tracked object and 1 for base.
        mask = view[np.ix_(preview_index(view.shape[0], rgb.shape[0]), preview_index(view.shape[1], width))] == 2
        outlined = bool(mask.any() and not mask.all())
        if outlined:
            paint_outline(rgb, mask, width=1)
    mark_centre(rgb)
    return ppm(rgb), cm, outlined


class TrackedReference:
    """One in-memory frame from this take and piece. Render lazily, once per loss."""
    def __init__(self):
        self.clear()

    def clear(self):
        self.piece = self.raw = self.dimensions = self.view = self.thumbnail = None
        self.lost = False

    def observe(self, piece, tracking, raw, dimensions, view):
        if piece != self.piece:
            self.clear()
            self.piece = piece
        self.lost = not tracking
        if tracking:
            self.thumbnail = None
            if raw is not None and len(raw) >= dimensions[1] * dimensions[2]:
                self.raw, self.dimensions, self.view = raw, dimensions, view

    def image(self):
        if not self.lost or self.raw is None:
            return None
        if self.thumbnail is None:
            self.thumbnail = render_tracked_reference(self.raw, self.dimensions, self.view)
        return self.thumbnail


class DistanceGauge(tk.Frame):
    """Text and a three-zone strip give the reading without requiring colour perception."""
    def __init__(self, parent):
        super().__init__(parent, bg=panels.SURFACE, bd=0)
        self.reading = tk.Label(self, anchor="w", bg=panels.SURFACE, fg=panels.INK, font=panels.BOLD)
        self.reading.pack(fill="x")
        strip = tk.Frame(self, bg=panels.LINE, padx=1, pady=1)
        strip.pack(fill="x", pady=(3, 0))
        self.zones = []
        for i, text in enumerate(DISTANCE_STATES):
            label = tk.Label(strip, text=text, pady=3, font=panels.BOLD)
            label.grid(row=0, column=i, sticky="ew", padx=(0, 1 if i < 2 else 0))
            strip.columnconfigure(i, weight=1, uniform="distance")
            self.zones.append(label)
        self.shown = None
        self.show(None, "Waiting for live depth")

    def show(self, active, text):
        if self.shown == (active, text):
            return
        if self.shown is None or self.shown[0] != active:
            for i, label in enumerate(self.zones):
                selected = i == active
                label.configure(text=("● " if selected else "") + DISTANCE_STATES[i],
                                bg=(EDGE, GOOD, EDGE)[i] if selected else panels.BG,
                                fg=panels.SURFACE if selected else panels.MUTED)
        self.reading.configure(text=text)
        self.shown = (active, text)


# The IR node's auto exposure menu (v4l2.CID_EXPOSURE_AUTO). 0: the scanner's own auto exposure (what it powers
# up and resets to); the depth function's IR exposure and gain then change nothing. 2: they act (by hand).
# Measured 2026-11-05 (depth on the object per frame / in 90% of 40 frames): the scanner's AE chose 5000 / 1000 on
# the dark hand (long range) and gave 51.6 / 49.2%, the defaults 3000 / 1000 51.1 / 48.4, the old one-shot
# Auto IR's 1190 / 2700 51.2 / 47.8, 800 / 1000 48.3 / 43.2; on the test block (close range) it chose 581 and
# matched every manual setting. So Auto IR is the scanner's own, on by default.
IR_AUTO, IR_MANUAL = 0, 2


def held(scale):
    """The value a Scale holds, as its callback reports it (rounded to its resolution, within its range)."""
    return int(round(float(scale.get())))


def ir_exposure_mode(nodes, auto, log):
    try:
        v4l2.set_control(nodes["ir"], v4l2.CID_EXPOSURE_AUTO, IR_AUTO if auto else IR_MANUAL)
    except OSError as e:
        log(f"IR exposure mode not set ({e.strerror}): " + ("Auto IR may be off" if auto else "the IR sliders may do nothing"))


RANGES = {"Close range 15-33 cm": calib.CLOSE, "Long range 20-72 cm": calib.LONG}
SOURCES = {"board": "board calibration", "factory": "factory calibration", "estimate": "estimates"}
SWITCH_SETTLE_S = 6.0  # the scanner may reset its USB connection a few seconds after its streams stop
SWITCH_STUCK_S = 40.0  # a range switch takes 6-7 s; one still busy after this long is given up for a reconnect
# (2026-11-05 10:34: one hung after "IMU not switched off ([Errno 110] Connection timed out)" until the viewer was
# killed, with nothing logged about where; the switch now logs how long each step took)


def depth_title(cap):
    kind = "14-bit USB" if isinstance(cap, usbdepth.DepthCapture) else "10-bit"
    return f"Depth, {kind}, {'long' if cap.depth_mode.startswith('Long') else 'close'} range (near = red)"


def wait_for_scanner(timeout=15.0):
    """v4l2.discover() once the scanner's three nodes are back (after a stop or USB reset)."""
    end = time.monotonic() + timeout
    while True:
        try:
            return v4l2.discover()
        except (RuntimeError, OSError):
            if time.monotonic() > end:
                raise RuntimeError("the scanner did not come back")
            time.sleep(0.5)


def open_captures(nodes, log):
    """Open all streams or release the ones that opened before a failure."""
    opened = []
    try:
        for open_one in (lambda: fr.open_depth(nodes, log),
                         lambda: v4l2.Capture(nodes["color"], "YUYV", fr.W, fr.H),
                         lambda: v4l2.Capture(nodes["ir"], "GREY", fr.W, fr.H)):
            opened.append(open_one())
    except BaseException:
        for cap in reversed(opened):
            try:
                cap.close()
            except OSError as e:
                log(f"could not close an incomplete capture: {e}")
        raise
    return opened


def node_controls(target):
    """Controls of a V4L2 node path, or of the USB depth capture (it owns the depth function's controls)."""
    return target.controls() if isinstance(target, usbdepth.DepthCapture) else v4l2.controls(target)


def get_control(target, cid):
    return target.get_control(cid) if isinstance(target, usbdepth.DepthCapture) else v4l2.get_control(target, cid)


def set_control(target, cid, value):
    if isinstance(target, usbdepth.DepthCapture):
        target.set_control(cid, value)
    else:
        v4l2.set_control(target, cid, value)


ACTIVE_SCAN_STATES = ("scanning", "pausing", "paused", "resuming")


def progress_line(status):
    """One-line operational status; detailed recovery guidance appears below the depth preview."""
    age = status.get("last_view_age_s")
    last_view = "" if age is None else (" · last just now" if age < 1 else f" · last {age:.0f} s ago")
    text = (f"Capture: {status['capture']} ({status['frames']})  |  Tracking: {status['tracking']}"
            f"  |  Views added: {status['merged']:,}{last_view}"
            f"  |  {status['points']:,} pts · {status['geometry']}")
    return f"{text}  |  {status['unjoined']}" if status["unjoined"] else text


class App:
    """The viewer's state and actions. The panels (panels.py) call these methods; `tick` runs in the Tk
    thread about 30 times a second. The range switch and the record loop run in their own threads and
    touch no Tk objects (they leave flags and the message for `tick`)."""

    def __init__(self, root):
        self.root, self.message = root, ""
        self.messages = collections.deque(maxlen=MESSAGES_KEPT)  # (number, line): the Messages box
        self.message_count, self.messages_shown = itertools.count(1), 0
        self.rates, self.info_at = sysinfo.Rates(), 0.0  # the Status box: CPU / GPU rates, when it was refreshed
        self.nodes = v4l2.discover()
        self.ir_auto = True  # Auto IR: the scanner's own auto exposure (ir_exposure_mode)
        ir_exposure_mode(self.nodes, self.ir_auto, self.log)
        cap, color, ir = open_captures(self.nodes, self.log)
        self.targets = {**self.nodes, "depth": cap if isinstance(cap, usbdepth.DepthCapture) else self.nodes["depth"]}
        self.streams = {"Colour": Stream(color, render_color), "IR (flipped)": Stream(ir, render_ir),
                        "Depth": Stream(cap, render_depth)}
        self.color, self.ir, self.depth = self.streams.values()
        self.controls = {}  # label -> (node name, control id, info, scale); targets[node] is looked up at use
        self.synced, self.ir_read_at = {}, 0.0  # slider values set from the scanner's (not to be sent back); when
        self.switching = {"busy": False, "done": None}  # a range switch in progress / finished (its thread)
        self.rec = {"recorder": None, "thread": None, "missed": 0}
        self.record_rates = RECORD_RATES  # for the Capture panel
        self.record_rate = tk.StringVar(value=next(iter(RECORD_RATES)))
        self.job = {"process": None, "dir": None}
        self.live = {"proc": None, "commands": None, "replies": None, "url": None,
                     "feeder": None,  # (Feeder: frames out, replies in, off the Tk loop)
                     "done": collections.deque(maxlen=60), "last": None, "name": "", "gpu": "", "merging": "",
                     "progress": livescan.ScanProgress()}
        # (done: when frames came back; last: (merged, frames, points, tracking); gpu / merging: from its log)
        self.scan_state = "idle"  # idle | counting | scanning | pausing | paused | resuming | cancelling | saving | optimising | meshing
        self.imu, self.imu_note, self.imu_want = None, "", usbdepth.available()
        self.retry_at = 0.0  # time.monotonic() before which a lost scanner is not reconnected again
        self.lost = False  # a stream lost its device and the scanner is not back yet
        self.ranges, self.sliders = RANGES, SLIDERS  # for the Scanner panel
        self.range_names = {mode: label for label, mode in RANGES.items()}
        self.range_var = tk.StringVar(value=self.range_name())
        self.preset, self.keep_raw = tk.StringVar(value="small"), tk.BooleanVar(value=True)
        self.remove_base = tk.BooleanVar(value=True)
        self.mesh_after = tk.BooleanVar(value=True)  # the mesh made after Stop (and Optimise)
        self.imu_wanted, self.find_markers = tk.BooleanVar(value=self.imu_want), tk.BooleanVar(value=True)
        self.preview_mode = tk.StringVar(value=LIVE_DEPTH)
        scan_view["preview"] = LIVE_DEPTH
        self.hold_depth = tk.BooleanVar(value=hold_view["on"])  # the View panel's "Hold depth"
        self.ir_auto_var = tk.BooleanVar(value=self.ir_auto)  # the Scanner panel's "Auto IR"
        self.countdown, self.count = tk.BooleanVar(value=True), None  # the Scan panel's "Countdown"; its end
        self.ticks, self.tick_at = collections.deque(maxlen=20000), None  # Tk tick intervals while scanning
        self.panels, self.images, self.shown = [], {}, {}
        self.tracked_reference = TrackedReference()
        self.return_photo, self.shown_return = None, None

    # --- window ---------------------------------------------------------------------------------
    def build_window(self):
        root = self.root
        root.title("triBUUTSCAN (Ferret Pro)")
        root.configure(bg=panels.BG)
        panels.configure_styles(root)
        main = tk.Frame(root, bg=panels.BG)  # previews, with Status and Messages below
        right = tk.Frame(root, bg=panels.BG)
        main.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=(8, 4), pady=8)
        right.grid(row=0, column=2, sticky="n", padx=(4, 8), pady=8)
        root.rowconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        main.columnconfigure(1, weight=1)
        left, middle = panels.frame(main), panels.frame(main)
        left.grid(row=0, column=0, sticky="new", padx=(0, 4))
        middle.grid(row=0, column=1, sticky="n", padx=(4, 0))
        from .model_pane import ModelPane
        self.model_pane = ModelPane(left, width=MODEL_W, height=MODEL_H)
        self.model_pane.pack(fill="x")
        boxes = tk.Frame(left, bg=panels.BG)
        boxes.pack(fill="both", expand=True, pady=(6, 0))
        self.info = {}
        mono = tkinter.font.nametofont("TkFixedFont").copy()  # (("TkFixedFont", 9) is not monospace: a family name)
        mono.configure(size=9)
        for col, (name, indent) in enumerate((("Status", " " * LABEL_W), ("Messages", "[00:00:00] "))):
            box = panels.frame(boxes)
            box.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if col == 0 else 0)
            boxes.columnconfigure(col, weight=1, uniform="info")
            tk.Label(box, text=name, bg=panels.SURFACE, fg=panels.INK, font=panels.BOLD).pack(
                anchor="w", padx=8, pady=(5, 0))
            text = tk.Text(box, width=30, height=7, wrap="word", font=mono, relief="flat", padx=8, pady=4,
                           bg=panels.SURFACE, fg=panels.INK, highlightthickness=0, bd=0,
                           state="disabled", takefocus=0)
            text.tag_configure("hang", lmargin2=mono.measure(indent))  # wrapped lines start under the text
            text.pack(fill="both", expand=True)
            self.info[name] = text
        boxes.rowconfigure(0, weight=1)
        self.return_panel = panels.frame(left)
        tk.Label(self.return_panel, text="Last tracked view", bg=panels.SURFACE, fg=panels.INK,
                 font=panels.BOLD).pack(anchor="w", padx=8, pady=(4, 0))
        self.return_image = tk.Label(self.return_panel, bg="#101928", bd=0, highlightthickness=0)
        self.return_image.pack(padx=8, pady=4)
        self.return_note = tk.Label(self.return_panel, anchor="w", justify="left", wraplength=MODEL_W - 16,
                                    bg=panels.SURFACE, fg=panels.INK)
        self.return_note.pack(fill="x", padx=8, pady=(0, 5))
        self.views = {}
        depth = panels.frame(middle)
        depth.pack(fill="x")
        cameras = panels.frame(middle)
        cameras.pack(fill="x", pady=(6, 0))
        colour, ir = panels.frame(cameras), panels.frame(cameras)
        colour.pack(side="left")
        ir.pack(side="right")
        for name, parent, width in (("Colour", colour, SIDE_W), ("IR (flipped)", ir, SIDE_W),
                                    ("Depth", depth, DEPTH_W)):
            title_parent = parent
            if name == "Depth":
                title_parent = panels.frame(parent)
                title_parent.pack(fill="x", padx=8, pady=3)
                panels.menu(title_parent, self.preview_mode, (LIVE_DEPTH, COVERAGE),
                            self.choose_depth_preview).pack(side="right")
            title = tk.Label(title_parent, text=depth_title(self.depth.cap) if name == "Depth" else name,
                             bg=panels.SURFACE, fg=panels.INK, font=panels.BOLD)
            if name == "Depth":
                title.pack(side="left")
            else:
                title.pack(anchor="w", padx=8, pady=4)
            blank = tk.PhotoImage(width=width, height=width * fr.H // fr.W)
            self.images[name] = blank
            image = tk.Label(parent, image=blank, bg="#101928", bd=0, highlightthickness=0)
            image.pack()
            if name in ("Depth", "Colour"):  # the aim: a click picks the object there, a right-click lets go
                image.bind("<Button-1>", lambda e, name=name: self.aim_click(name, e.x, e.y))
                image.bind("<Button-3>", lambda _e: self.aim_clear())
            if name == "Depth":
                self.distance = DistanceGauge(parent)
                self.distance.pack(fill="x", padx=8, pady=(6, 0))
                bar = panels.frame(parent)
                bar.pack(fill="x", padx=8, pady=(5, 0))
                self.guide = tk.Label(bar, anchor="center", bg=panels.SURFACE, fg=panels.INK,
                                      font=("TkDefaultFont", 11, "bold"), wraplength=460)
                self.guide.pack(side="left", fill="x", expand=True)
                self.retry = panels.button(bar, text="Retry now", command=self.retry_now)  # shown while lost
                self.coverage_panel = panels.frame(parent)
                legend = panels.frame(self.coverage_panel)
                legend.pack(fill="x")
                for (label, colour), pattern in zip(COVERAGE_SWATCHES, ("solid", "stripes", "dots")):
                    tk.Label(legend, text=f"{label} ({pattern})", bg=colour, fg=panels.INK,
                             font=panels.BOLD, padx=6, pady=2).pack(side="left", padx=(0, 3))
                self.coverage_summary = tk.Label(self.coverage_panel, anchor="w", justify="left",
                                                 wraplength=DEPTH_W - 16, bg=panels.SURFACE, fg=panels.INK)
                self.coverage_summary.pack(fill="x", pady=(2, 0))
                self.coverage_shown = None
            stats = tk.Label(parent, anchor="w", bg=panels.SURFACE, fg=panels.MUTED, font=("TkFixedFont", 9))
            stats.pack(fill="x", padx=8, pady=(2, 5))
            self.views[name] = (title, image, stats)
        self.note = tk.Label(root, anchor="w", bg=panels.SURFACE, fg=panels.INK, padx=8, pady=5,
                             font=("TkDefaultFont", 10))
        self.note.grid(row=1, column=0, columnspan=3, sticky="we", padx=8, pady=(0, 8))
        for cls in PANELS:
            panel = cls(right, self)
            panel.pack(fill="x", pady=(0, 6))
            self.panels.append(panel)
        for key, action in (("s", self.save), ("r", self.toggle_record), ("m", self.mesh), ("c", self.toggle_scan),
                            ("p", self.toggle_pause_scan)):
            root.bind(key, lambda _event, action=action: action())
        root.bind("<Escape>", lambda _event: self.escape())
        root.protocol("WM_DELETE_WINDOW", self.close)

    def start(self):
        threading.Thread(target=find_markers_loop, args=(self.color,), daemon=True).start()
        threading.Thread(target=aim_loop, args=(self.depth,), daemon=True).start()
        for s in self.streams.values():
            s.start()
        on_usb = isinstance(self.depth.cap, usbdepth.DepthCapture)
        self.log(f"viewer started (pid {os.getpid()}, depth {'14-bit over USB' if on_usb else '10-bit V4L2'}, "
                 f"{calib.CAMERA.mode}{', distances are estimates' if calib.CAMERA.law_estimate else ''}, "
                 f"camera: {SOURCES.get(calib.CAMERA.source, calib.CAMERA.source)})"
                 + (", tracking and meshes on the CPU (--force-cpu)" if gpu.choice() == "0" else ""))
        if self.imu_want:
            self.imu_on()
        else:
            self.imu_note = "IMU needs udev/70-tribuutscan.rules (see README)"
        self.tick()

    def log(self, text):
        """Show a message in the window (the line at the bottom and the Messages box), print it, and
        append it to captures/viewer.log."""
        self.message = text
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.messages.append((next(self.message_count), line))
        print(line, flush=True)
        try:
            LOG.parent.mkdir(exist_ok=True)
            with LOG.open("a") as f:
                f.write(time.strftime("%Y-%m-%d ") + line + "\n")
        except OSError:
            pass

    def tick(self):
        if not self.switching["busy"] and time.monotonic() > self.retry_at \
                and (self.lost or any(s.idle.is_set() for s in self.streams.values())):  # no complete capture
            self.switching["busy"], self.lost = True, True
            self.stop_lost_capture()
            self.log("lost the scanner (unplugged, or it reset its USB connection): reconnecting ...")
            threading.Thread(target=self.reconnect, daemon=True).start()
        self.check_switch()
        aim_view["idle"] = self.scan_state in ("idle", "counting") and not self.switching["busy"] and not self.lost
        if self.switching["done"]:  # back in the Tk thread after a range switch or a reconnect
            self.switching.update(done=None, busy=False, since=None, step=None)
            self.views["Depth"][0].configure(text=depth_title(self.depth.cap))
            self.range_var.set(self.range_name())
            self.imu_wanted.set(self.imu is not None or (self.imu_want and not self.imu_note))
            for label, (node, cid, _, scale) in self.controls.items():  # the scanner may have reset them
                if label in IR_LABELS and self.ir_auto:
                    continue  # (its own auto exposure sets these)
                try:
                    set_control(self.targets[node], cid, int(scale.get()))
                except (OSError, AttributeError) as e:
                    self.log(f"{label}: {getattr(e, 'strerror', None) or e}")
        if self.ir_auto and not self.lost and not self.switching["busy"] and time.monotonic() - self.ir_read_at >= IR_FOLLOW_S:
            self.ir_read_at = time.monotonic()
            self.follow_ir()
        st = self.imu.state() if self.imu is not None else None  # the depth preview's hold follows the IMU
        hold_view["s"] = HOLD_S["no imu"] if st is None else HOLD_S["still" if st["still"] else "moving"]
        for name, s in self.streams.items():
            _, image, stats = self.views[name]
            if s.latest is not None and self.shown.get(name) != s.version:  # redraw only a changed preview
                self.shown[name] = s.version
                self.images[name] = tk.PhotoImage(data=s.latest, format="PPM")
                image.configure(image=self.images[name])
            stats.configure(text=f"{s.fps:4.1f} fps   {s.stats.get('text', '')}")
        now = time.monotonic()
        if self.preview_mode.get() == COVERAGE:
            caption = coverage_status(self.depth.stats, now, self.scan_state, self.lost or self.switching["busy"],
                                      scan_view["lost"], self.return_photo is not None)
            if self.coverage_shown != caption:
                self.coverage_summary.configure(text=caption)
                if self.coverage_shown is None:
                    self.coverage_panel.pack(fill="x", padx=8, pady=(4, 0), before=self.distance)
                self.coverage_shown = caption
        elif self.coverage_shown is not None:
            self.coverage_panel.pack_forget()
            self.coverage_shown = None
        if self.lost or self.switching["busy"]:
            self.distance.show(None, "Distance unavailable · " + ("Scanner reconnecting" if self.lost else
                                                                "Changing depth range"))
        else:
            self.distance.show(*distance_guide(fresh_aim(), self.depth.stats, calib.CAMERA.mode,
                                               calib.CAMERA.law_estimate, now))
        progress = self.live["progress"].status(now) if self.scan_state == "scanning" else None
        if self.lost:
            wait = self.retry_at - now
            self.guide.configure(fg=BAD, text="Reconnecting to the scanner ..." if self.switching["busy"] or wait <= 0 else
                                 f"Scanner disconnected: plug it in or replug it. Next try in {wait:.0f} s")
        elif progress is not None:
            hint = ("Tracking lost · Model held. Return to the last aligned view, or choose Start separate piece."
                    if self.can_start_separate_piece(now) else progress["hint"])
            self.guide.configure(text=hint, fg=BAD if progress["tracking"].startswith("Lost") else MUTED_GUIDE)
        else:
            hint = self.check_countdown() if self.scan_state == "counting" else None
            if hint is None and aim_view["idle"] and time.monotonic() - aim_view["t"] <= AIM_FRESH_S:
                hint = aim_hint(aim_view["piece"], self.ir.stats, self.imu.state() if self.imu else None,
                                found_markers["on"])
            self.guide.configure(text=hint[0] if hint else "", fg=hint[1] if hint else MUTED_GUIDE)
        retry_shown = self.retry.winfo_ismapped()
        if self.lost and not self.switching["busy"] and not retry_shown:
            self.retry.pack(side="right", padx=(6, 0))
        elif retry_shown and (not self.lost or self.switching["busy"]):
            self.retry.pack_forget()
        self.check_mesh()
        self.check_scan()
        self.model_pane.tick(self.scan_state, scan_view["lost"], self.live["commands"])
        scan_view.update(active=self.scan_state in ("scanning", "pausing", "resuming"),
                         return_guide=self.refresh_return_guide())
        recorder = self.rec["recorder"]
        if recorder:
            self.message = (f"REC {recorder.count} sets, {recorder.megabytes:.0f} MB"
                            + (f", {recorder.imu_rows} IMU samples" if recorder.imu_rows else "")
                            + f", {self.rec['missed']} missed, {recorder.free:.0f} GB free")
        if self.scan_state == "scanning":  # recompute after consuming frame replies, even if geometry was unchanged
            self.note.configure(text=progress_line(self.live["progress"].status()))
        else:
            self.note.configure(text=self.message)
        for panel in self.panels:
            panel.refresh()
        if time.monotonic() - self.info_at >= INFO_EVERY_S:
            self.info_at = time.monotonic()
            self.refresh_info()
        now = time.monotonic()
        if self.scan_state == "scanning" and self.tick_at is not None:
            self.ticks.append(now - self.tick_at)  # (how often the window got to run: logged at Stop)
        self.tick_at = now
        self.root.after(33, self.tick)

    def refresh_return_guide(self):
        """Compare a frozen acknowledged frame with Depth; never save or log the image."""
        blocked = self.lost or self.switching["busy"]
        if blocked or self.scan_state not in ACTIVE_SCAN_STATES:
            self.tracked_reference.clear()
        shown = self.tracked_reference.image() if self.scan_state == "scanning" and not blocked else None
        if shown is None:
            if self.return_photo is not None:
                self.return_panel.pack_forget()
                self.return_image.configure(image="")
                self.return_photo, self.shown_return = None, None
            return False
        if self.shown_return is not shown:
            image, cm, outlined = shown
            self.return_photo = tk.PhotoImage(data=image, format="PPM")
            self.shown_return = shown
            self.return_image.configure(image=self.return_photo)
            matched = "Match the cyan outline" if outlined else "Match the visible shape"
            distance = "" if cm is None else f" Centre was ~{cm:.1f} cm."
            self.return_note.configure(text=f"{matched} and angle in live Depth.{distance} Move slowly.")
            self.return_panel.pack(fill="x", after=self.model_pane, pady=(4, 0))
        return True

    # --- the Status and Messages boxes ---------------------------------------------------------------
    def refresh_info(self):
        """The Status box (rewritten when it changed) and the Messages box (new lines added; it follows
        them unless it was scrolled back)."""
        status, messages = self.info["Status"], self.info["Messages"]
        text = "\n".join(self.status_lines())
        if text != status.get("1.0", "end-1c"):
            status.configure(state="normal")
            status.delete("1.0", "end")
            status.insert("1.0", text, "hang")
            status.configure(state="disabled")
        fresh = [(n, line) for n, line in list(self.messages) if n > self.messages_shown]
        if fresh:
            self.messages_shown = fresh[-1][0]
            at_end = messages.yview()[1] > 0.999
            messages.configure(state="normal")
            empty = messages.compare("end-1c", "==", "1.0")
            messages.insert("end", ("" if empty else "\n") + "\n".join(line for _, line in fresh), "hang")
            surplus = int(messages.index("end-1c").split(".")[0]) - MESSAGES_KEPT
            if surplus > 0:
                messages.delete("1.0", f"{surplus + 1}.0")
            messages.configure(state="disabled")
            if at_end:
                messages.see("end")

    def doing(self):
        """What the viewer and its helpers are busy with, in words."""
        live, now = self.live, []
        if self.lost:
            now.append("the scanner is gone: reconnecting")
        elif self.switching["busy"]:
            now.append("switching the depth range (the images pause)")
        if self.scan_state == "scanning":
            progress = live.get("progress")
            if progress is not None:
                status = progress.status()
                now.append(f"{live['name']}: {progress_line(status)}")
            else:
                merged, frames, points, tracking = live["last"] or (0, 0, 0, True)
                done = list(live["done"])
                fps = (len(done) - 1) / (done[-1] - done[0]) if len(done) > 1 and done[-1] > done[0] else 0.0
                now.append(f"scanning {live['name']}: {frames} frames ({fps:.1f} a second), {merged} merged, {points:,} points"
                           + ("" if tracking else ", TRACKING LOST"))
        elif self.scan_state == "pausing":
            now.append("pausing: finishing frames already accepted")
        elif self.scan_state == "paused":
            now.append("scan paused: rotate the Live 3D model, then Resume or Stop & Save")
        elif self.scan_state == "resuming":
            now.append("resuming the same scan")
        elif self.scan_state == "cancelling":
            now.append("cancelling this take without saving or meshing")
        elif self.scan_state in ("saving", "optimising", "meshing"):
            now.append(f"{self.scan_state} the scan {live['name']}")
        recorder = self.rec["recorder"]
        if recorder:
            now.append(f"recording {recorder.dir.name}: {recorder.count} sets, {recorder.megabytes:.0f} MB, "
                       f"{self.rec['missed']} missed")
        if self.job["process"] is not None:
            now.append(f"meshing {self.job['dir'].name}")
        if self.scan_state == "counting":
            now.append("counting down to the start of a scan")
        return "; ".join(now) or "idle: showing the scanner's images"

    def status_lines(self):
        """The Status box: what runs and what it uses of the machine, as plain text."""
        live, rates = self.live, self.rates
        on_usb = isinstance(self.depth.cap, usbdepth.DepthCapture)
        mode = calib.CAMERA.mode
        rows = [("Now", self.doing()),
                ("Scanner", f"depth {'14-bit over USB' if on_usb else '10-bit (V4L2)'}, {self.range_names.get(mode, mode)}, "
                            f"IR {'auto (the scanner' + chr(39) + 's own)' if self.ir_auto else 'by hand'}"),
                ("Camera", SOURCES.get(calib.CAMERA.source, calib.CAMERA.source)
                 + (", distances are estimates" if calib.CAMERA.law_estimate else ""))]
        streams = ", ".join(f"{name.split()[0].lower()} {s.fps:.0f}" for name, s in self.streams.items())
        imu = self.imu.state() if self.imu is not None else None
        rows.append(("Streams", f"{streams} fps; " + (f"IMU {imu['rate']:.0f} Hz, {imu['temp']:.1f} C" if imu else
                                                      "IMU starting" if self.imu is not None else
                                                      self.imu_note or "IMU off")))
        procs = [("viewer", os.getpid())]
        worker = live["proc"]
        if worker is not None and worker.is_alive():
            procs.append(("worker", worker.pid))
            merge = [pid for pid, cmd in sysinfo.children(worker.pid) if "spawn_main" in cmd]
            if merge:
                procs.append(("merge", merge[0]))
            how = (f"tracks on {live['gpu']}" if live["gpu"] else "tracks when a scan starts") + ", " + (
                f"merges in pid {merge[0]}" if merge else "merges beside tracking" if live["merging"] == "thread"
                else "merges in a process of its own while scanning")
            rows.append(("Scan worker", f"pid {worker.pid}, {how}"))
        else:
            rows.append(("Scan worker", "not started (it starts with the first scan)"))
        if self.job["process"] is not None:
            procs.append(("meshing", self.job["process"].pid))
        cpu = [(name, rates.cpu(pid)) for name, pid in procs]
        use = ", ".join(f"{name} {pct:.0f}%" for name, pct in cpu if pct is not None)
        rows.append(("CPU", f"load {os.getloadavg()[0]:.1f} of {os.cpu_count()} threads"
                            + (f"; {use} (100% = one core)" if use else "")))
        rss = [(name, sysinfo.rss_mb(pid)) for name, pid in procs]
        free, total = sysinfo.memory_mb()
        rows.append(("Memory", ", ".join(f"{name} {mb:.0f} MB" for name, mb in rss if mb is not None)
                               + (f"; {free / 1024:.1f} of {total / 1024:.1f} GB free" if free else "")))
        used = []
        for name, pid in procs[1:]:
            usage = sysinfo.gpu_usage(pid)
            busy = rates.gpu_busy(pid, usage)
            if usage and usage["memory_mib"]:
                used.append(f"{name} {sum(usage['memory_mib'].values()):.0f} MB"
                            + (f", busy {busy:.0f}%" if busy is not None else ""))
        rows.append(("GPU", "not used: --force-cpu (scans and meshes on the CPU)" if gpu.choice() == "0" else
                            "; ".join(used) if used else "not in use (scans and meshes use it)"))
        rows.append(("Disk", f"{fr.free_gb(fr.CAPTURES):.0f} GB free for captures/"))
        return [f"{label:{LABEL_W}s}{text}" for label, text in rows]

    def close(self):
        if garbled["frames"]:
            self.log(f"{garbled['frames']} depth previews had rows garbled by the scanner ({garbled['rows']} rows cleared)")
        self.log("viewer closed")
        self.model_pane.close()
        if self.rec["recorder"]:
            self.stop_recording()
            self.rec["thread"].join(timeout=2)  # let the last set and the files close cleanly
        if self.live["proc"] is not None:
            # A queued cancel already owns cleanup; don't turn it into a save when closing.
            if self.scan_state in ACTIVE_SCAN_STATES:
                self.live["feeder"].transition("saving", ("stop",))  # save what was scanned
                self.log("scan stopped by closing, saving ...")
            self.live["commands"].put(("quit",))
            self.live["proc"].join(timeout=15)
            if self.live["proc"].is_alive():
                self.live["proc"].terminate()
        self.imu_off()  # while the depth capture still carries the vendor commands
        for s in self.streams.values():
            try:
                s.stop()
            except OSError:  # the scanner is already gone
                pass
        self.root.destroy()

    # --- scanner settings ------------------------------------------------------------------------
    def slider(self, parent, label, node, cid):
        """A Scale for one control (see SLIDERS), or None if the node does not have it."""
        info = next((c for c in node_controls(self.targets[node]).values() if c["id"] == cid), None)
        if info is None:
            return None

        def set_value(value):
            value = int(float(value))
            if self.synced.get(label) == value:  # (the scale showing the scanner's own value: nothing to send)
                return
            self.synced.pop(label, None)
            if label in IR_LABELS and self.ir_auto:
                self.set_ir_auto(False)  # moving an IR slider takes the IR exposure over by hand
            try:
                set_control(self.targets[node], cid, value)
            except (OSError, AttributeError) as e:
                self.log(f"{label}: {getattr(e, 'strerror', None) or e}")

        scale = tk.Scale(parent, label=label, orient="horizontal", from_=info["min"], to=info["max"],
                         resolution=info["step"] or 1, command=set_value, bg=panels.SURFACE, fg=panels.INK,
                         highlightbackground=panels.SURFACE, highlightcolor=panels.ACCENT, bd=0,
                         troughcolor=panels.LINE, activebackground=panels.HOVER, sliderrelief="flat")
        scale.set(info["value"])
        self.synced[label] = held(scale)  # (Tk calls set_value later, when drawn: it must not count as a move)
        scale.pack(fill="x")
        self.controls[label] = (node, cid, info, scale)
        return scale

    def reset_controls(self):
        for label, (_, _, info, scale) in self.controls.items():
            if label not in IR_LABELS:
                scale.set(info["default"])  # triggers set_value
        self.set_ir_auto(True)  # (the scanner's own auto exposure is the default for IR)

    def control_values(self):
        values = {label: get_control(self.targets[node], cid) for label, (node, cid, _, _) in self.controls.items()}
        return {**values, "IR auto": self.ir_auto}

    def toggle_ir_auto(self):
        self.set_ir_auto(bool(self.ir_auto_var.get()))

    def set_ir_auto(self, on):
        """Auto IR on (the scanner's own auto exposure) or off (the IR sliders' values, from where it was)."""
        if on == self.ir_auto:
            return
        self.ir_auto = on
        self.ir_auto_var.set(on)
        ir_exposure_mode(self.nodes, on, self.log)
        if on:
            self.ir_read_at = 0.0  # the sliders follow it from the next tick
        else:
            for label in IR_LABELS:
                if label in self.controls:
                    node, cid, _, scale = self.controls[label]
                    try:
                        set_control(self.targets[node], cid, int(scale.get()))
                    except (OSError, AttributeError) as e:
                        self.log(f"{label}: {getattr(e, 'strerror', None) or e}")
        self.log("Auto IR on: the scanner sets its IR exposure" if on else "IR exposure by hand")

    def follow_ir(self):
        """While Auto IR is on: show the scanner's own exposure and gain on the IR sliders."""
        for label in IR_LABELS:
            if label not in self.controls:
                continue
            node, cid, _, scale = self.controls[label]
            try:
                value = int(get_control(self.targets[node], cid))
            except (OSError, AttributeError):
                return
            if value != held(scale):
                scale.set(value)
                self.synced[label] = held(scale)  # what the scale holds: it rounds to its step and clamps to its
                # range, and its callback reports that (2026-11-05: Auto IR switched itself off a second after
                # every switch on, the scanner's value rounded by the slider counting as a move by hand)

    def ir_settings(self):
        """What a scan or recording notes about the IR exposure."""
        values = {label: int(self.controls[label][3].get()) for label in IR_LABELS if label in self.controls}
        return {"auto": self.ir_auto, "exposure": values.get("IR exposure"), "gain": values.get("IR gain")}

    def range_name(self):
        return self.range_names.get(calib.CAMERA.mode, calib.CAMERA.mode)

    def choose_range(self, label):
        mode = RANGES[label]
        busy = self.rec["recorder"] or self.scan_state != "idle" or self.lost or self.switching["busy"]
        if mode == calib.CAMERA.mode or busy:
            if busy:
                self.log("stop recording and scanning before switching the range")
            self.range_var.set(self.range_name())
            return
        gen = self.switching.get("gen", 0) + 1
        self.switching.update(busy=True, since=time.monotonic(), step="starting", gen=gen)
        self.log(f"switching to {label}: the streams pause for about 10 s")
        threading.Thread(target=self.switch_range, args=(mode, gen), daemon=True).start()

    def switch_range(self, mode, gen=None):
        """Background thread: pause the streams, switch the scanner's depth mode, start them again. If the
        watchdog in `check_switch` gave up on it meanwhile (a newer `gen`), it leaves the streams alone."""
        took, t = [], [time.monotonic()]

        def step(name):  # the step about to run, for the watchdog's message; the time the last one took
            now = time.monotonic()
            if self.switching.get("step") not in (None, "starting"):
                took.append(f"{self.switching['step']} {now - t[0]:.1f} s")
            t[0] = now
            self.switching["step"] = name

        try:
            step("switching the IMU off")
            self.imu_off()  # its commands go through the depth capture, which is about to close
            step("pausing the streams")
            for s in self.streams.values():
                s.pause()
            step("waiting for the scanner to settle")
            time.sleep(SWITCH_SETTLE_S)
            step("switching the depth mode")
            for attempt in range(2):
                try:
                    found = wait_for_scanner()
                    if vendor.camera_users():
                        raise RuntimeError("another program has the scanner open")
                    ch = vendor.Channel(found["depth"])
                    try:
                        ch.switch_depth_mode(mode)
                    finally:
                        ch.close()
                    break
                except OSError:  # it re-enumerated under us: once more
                    if attempt:
                        raise
                    time.sleep(3)
            self.log(f"depth range switched to {mode}")
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.log(f"range switch failed at {self.switching.get('step')}: {e}")
        finally:
            if gen is not None and self.switching.get("gen") != gen:
                self.log(f"the range switch given up came back after {time.monotonic() - t[0]:.0f} s "
                         f"in {self.switching.get('step') or 'a step'}: left alone")
                return
            step("opening the streams again")
            reopened = self.reopen()
            if not reopened:
                self.lost = True
                self.retry_at = time.monotonic() + RETRY_S
            if reopened and self.imu_want:
                step("switching the IMU on")
                self.imu_on()
            step(None)
            self.log("range switch took: " + ", ".join(took))
            self.switching["done"] = True

    def check_switch(self):
        """The watchdog (Tk thread): a range switch busy for SWITCH_STUCK_S is given up and the scanner
        reconnected as after a USB reset (a stuck thread that comes back later then leaves things alone)."""
        sw = self.switching
        if not sw["busy"] or sw.get("since") is None or time.monotonic() - sw["since"] < SWITCH_STUCK_S:
            return
        self.log(f"range switch stuck {SWITCH_STUCK_S:.0f} s while {sw.get('step')}: reconnecting the scanner")
        sw.update(gen=sw.get("gen", 0) + 1, since=None)
        self.lost = True
        threading.Thread(target=self.reconnect, daemon=True).start()

    def stop_lost_capture(self):
        """Stop using the old capture on the Tk thread before reconnecting."""
        if self.rec["recorder"]:
            self.stop_recording()
            self.log("recording stopped: scanner disconnected")
        if self.scan_state in ACTIVE_SCAN_STATES:
            self.toggle_scan()  # requests a partial save; the worker reports the result separately
        elif self.scan_state == "counting":
            self.cancel_countdown()

    def reconnect(self):
        """Background thread: release the old streams and reopen the re-enumerated device."""
        try:
            self.imu_off(quiet=True)
            for s in self.streams.values():
                s.pause()
            if self.reopen():
                self.lost = False
                self.log(f"scanner back ({calib.CAMERA.mode})")
                if self.imu_want:
                    self.imu_on()
            else:
                self.retry_at = time.monotonic() + RETRY_S
        finally:
            self.switching["done"] = True

    def retry_now(self):
        """The Retry now button: reconnect at the next tick instead of waiting for the timer."""
        self.retry_at = 0.0
        self.log("retrying now ...")

    def reopen(self):
        """Open all three streams again (node paths can change after the scanner re-enumerates).
        Returns False (and says so) if they could not be opened."""
        try:
            nodes = wait_for_scanner()
            ir_exposure_mode(nodes, self.ir_auto, self.log)  # (a USB reset puts it back to auto)
            cap, color, ir = open_captures(nodes, self.log)
            self.nodes.update(nodes)
            self.targets.update(nodes, depth=cap if isinstance(cap, usbdepth.DepthCapture) else nodes["depth"])
            self.color.resume(color)
            self.ir.resume(ir)
            self.depth.resume(cap)
            return True
        except Exception as e:  # noqa: BLE001 - shown to the user; the viewer stays up
            self.log(f"could not open the scanner ({e}): is it plugged in? trying again in {RETRY_S:.0f} s")
            return False

    # --- IMU -------------------------------------------------------------------------------------
    def vendor_channel(self):
        """The command channel: through the USB depth capture while it owns the depth function."""
        cap = self.depth.cap
        return vendor.Channel(usb=cap) if isinstance(cap, usbdepth.DepthCapture) else vendor.Channel(self.nodes["depth"])

    def imu_on(self):
        if self.imu is not None:
            return
        try:
            ch = self.vendor_channel()
            try:
                self.imu = imu.switch_on(ch)
            finally:
                ch.close()
        except (OSError, RuntimeError, vendor.VendorError) as e:
            self.imu, self.imu_note = None, f"IMU not available: {e}"
            self.log(self.imu_note)
            return
        self.imu_note = ""
        recorder = self.rec["recorder"]
        if recorder:
            self.imu.listener = recorder.write_imu
        s = self.imu.settings
        self.log(f"IMU on: gyro {s['gyro_hz']} Hz +-{s['gyro_dps']} deg/s, accel {s['accel_hz']} Hz +-{s['accel_g']} g")

    def imu_off(self, quiet=False):
        stream, self.imu = self.imu, None
        if stream is None:
            return
        stream.listener = None
        stream.stop()  # reading first: interface 6 goes back to usbhid
        try:
            ch = self.vendor_channel()
            try:
                imu.switch_off(ch)
            finally:
                ch.close()
        except (OSError, RuntimeError, vendor.VendorError) as e:
            if not quiet:  # after a reset the IMU is off anyway
                self.log(f"IMU not switched off ({e}); it stops at the next power-up")

    def toggle_imu(self):
        self.imu_want = self.imu_wanted.get()
        if self.switching["busy"]:
            return  # the switch brings the IMU back as wanted
        if self.imu_want:
            self.imu_on()
            self.imu_wanted.set(self.imu is not None)
        else:
            self.imu_off()
            self.imu_note = ""
            self.log("IMU off")

    def choose_depth_preview(self, mode):
        if mode in (LIVE_DEPTH, COVERAGE):
            scan_view["preview"] = mode

    def toggle_hold(self):
        hold_view["on"] = bool(self.hold_depth.get())

    def toggle_markers(self):
        found_markers.update(on=self.find_markers.get())

    # --- capture -----------------------------------------------------------------------------------
    def save(self):
        if self.lost or self.switching["busy"]:
            self.log("save when the scanner streams are back")
            return
        color, ir, depth = self.color, self.ir, self.depth
        if not (color.raw and ir.raw and depth.raw):
            self.log("nothing to save yet")
            return
        frames = fr.decode(color.raw, ir.raw, depth.raw, depth.cap.bytesperline)
        hits = markers.detect(markers.luma(frames["color"]), rgb=frames["color"])
        out = fr.save(frames, extra={"controls": self.control_values(),
                                     "markers": [[round(x, 2), round(y, 2), round(r, 2)] for x, y, r, _ in hits]})
        xyz, rgb = pointcloud.to_points(frames["depth"], pointcloud.register_color(frames["color"], frames["depth"]))
        pointcloud.write_ply(out / "cloud.ply", xyz, rgb)
        self.log(f"saved {out.name} (with cloud.ply, {len(hits)} markers)")

    def record_loop(self, recorder, fps):
        """Write every depth frame (30 fps) or every third (10 fps) with the colour frame nearest to it.
        Works through the depth stream's kept frames (~0.4 s), so a slow write drops nothing, and takes a
        depth frame once colour has a frame after it (until then its nearest colour is not known). Frames
        it falls too far behind for (their colour may be gone) count as `missed`, as do camera gaps."""
        color, ir, depth = self.color, self.ir, self.depth
        others = [color] + ([ir] if "ir" in recorder.streams else [])
        gap = 1 / fps - 0.5 / 30  # half a camera frame of slack
        last = depth.stamp - gap
        while self.rec["recorder"] is recorder:
            newest = depth.stamp
            for d_t, d_raw in list(depth.recent):
                if d_t - last < gap or newest - d_t > 0.25:
                    continue
                if any(s.stamp <= d_t for s in others):
                    break
                self.rec["missed"] += max(0, round((d_t - last) * fps) - 1)
                last = d_t
                (c_raw, c_t), (i_raw, i_t) = color.nearest(d_t), ir.nearest(d_t) if ir in others else (None, 0.0)
                try:
                    recorder.write({"color": c_raw, "ir": i_raw, "depth": d_raw}, {"color": c_t, "ir": i_t, "depth": d_t})
                except fr.DiskFull as e:
                    self.stop_recording()
                    self.log(str(e))
                    break
            time.sleep(0.01)
        recorder.close()

    def stop_recording(self):
        recorder, self.rec["recorder"] = self.rec["recorder"], None
        if self.imu is not None:
            self.imu.listener = None
        self.log(f"recorded {recorder.count} sets ({self.rec['missed']} missed)"
                 + (f" and {recorder.imu_rows} IMU samples" if recorder.imu_rows else "") + f" to {recorder.dir.name}")

    def toggle_record(self):
        if self.rec["recorder"]:
            self.stop_recording()
            return
        if not self.depth.raw or self.lost or self.switching["busy"]:
            self.log("nothing to record yet")
            return
        fps = RECORD_RATES[self.record_rate.get()]
        extra = {"controls": self.control_values(), "fps": fps}
        if self.imu is not None:
            extra["imu"] = self.imu.settings
        try:
            recorder = fr.Recorder(self.depth.cap.bytesperline, extra=extra)
        except fr.DiskFull as e:
            self.log(f"not recording: {e}")
            return
        self.rec.update(recorder=recorder, missed=0)
        if self.imu is not None:
            self.imu.listener = recorder.write_imu
        self.rec["thread"] = threading.Thread(target=self.record_loop, args=(recorder, fps), daemon=True)
        self.rec["thread"].start()
        self.log(f"recording started at {fps} fps: {recorder.dir.name}" + (" (with IMU)" if self.imu is not None else "")
                 + f", {recorder.free:.0f} GB free")

    def mesh(self):
        """Track, optimise and fuse the latest recording (cloud.py --optimise --mesh) in a separate
        process, so the live view keeps running; in a memory-capped scope of its own where systemd-run
        exists (an out-of-memory kill then takes only that job, not the viewer with it)."""
        job = self.job
        if job["process"]:
            self.log(f"still meshing {job['dir'].name}")
            return
        if self.rec["recorder"]:
            self.log("stop recording first")
            return
        directory = latest_recording()
        if directory is None:
            self.log("no recording yet: press r to record")
            return
        if not VENV_PYTHON.exists():
            self.log("meshing needs Open3D in .venv (README: uv sync --all-extras)")
            return
        scope = mesh_scope(self.log)
        job["process"] = subprocess.Popen(
            scope + [str(VENV_PYTHON), "-m", "tribuutscan.cloud", str(directory), "--optimise", "--mesh"], cwd=REPO,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        job["dir"], job["lines"] = directory, []
        job["reader"] = threading.Thread(target=self.job_output, args=(job["process"], job["lines"]), daemon=True)
        job["reader"].start()
        self.log(f"meshing {directory.name}: tracking, optimising, fusing (a few minutes) ...")

    def job_output(self, process, lines):
        """The meshing job's output, line by line as it comes (its own thread), into the Messages box."""
        for line in process.stdout:
            if line.strip():
                lines.append(line.rstrip())
                self.log(f"mesh job: {line.rstrip()}")

    def check_mesh(self):
        job = self.job
        process = job["process"]
        if process is None or process.poll() is None:
            return
        job["reader"].join(timeout=2)  # its last lines
        lines = [ln for ln in job["lines"] if ln.startswith(("mesh:", "Traceback"))]
        if process.returncode == 0:
            summary = lines[-1].split(" -> ")[0] if lines else "done"
            scan = job["dir"].name.removesuffix("-rec") + "-scan"
            self.log(f"meshed {job['dir'].name} ({summary}) -> {scan}/mesh.ply, mesh.stl")
        else:
            self.log(f"meshing {job['dir'].name} failed (exit {process.returncode})")
        job["process"] = None

    # --- live scan ---------------------------------------------------------------------------------
    def start_worker(self):
        live = self.live
        ctx = multiprocessing.get_context("spawn")
        if VENV_PYTHON.exists():
            ctx.set_executable(str(VENV_PYTHON))  # has Open3D: tracking
        live["commands"], live["replies"] = ctx.Queue(), ctx.Queue()
        live["proc"] = ctx.Process(target=livescan.run, args=(live["commands"], live["replies"]), daemon=True)
        live["proc"].start()
        live["feeder"] = Feeder(self)
        live["feeder"].start()

    def toggle_scan(self, continue_from=None):
        """Start (after the countdown, when it is on), cancel the countdown, or stop and save."""
        live = self.live
        if self.scan_state in ACTIVE_SCAN_STATES:
            live["feeder"].transition("saving", ("stop", bool(self.mesh_after.get())))
            self.log("scan stopped, saving ...")
            return
        if self.scan_state == "counting":
            self.cancel_countdown()
            return
        if self.scan_state != "idle" or self.lost or self.switching["busy"]:
            return
        if live["proc"] is None:
            self.start_worker()  # (during the countdown it has the time to start)
        if self.countdown.get():
            self.scan_state, self.count = "counting", {"until": time.monotonic() + COUNTDOWN_S, "from": continue_from}
            return
        self.begin_scan(continue_from)

    def cancel_scan(self):
        """Abandon just the active take and wait for worker cleanup before enabling a fresh scan."""
        if self.scan_state == "counting":
            self.cancel_countdown()
        elif self.scan_state in ACTIVE_SCAN_STATES:
            self.live["feeder"].transition("cancelling", ("cancel", self.live["name"]))
            self.log("cancelling this take without saving ...")

    def toggle_pause_scan(self):
        """Pause and inspect the current model without ending the scan, or resume the same scan."""
        if self.scan_state == "scanning":
            self.live["feeder"].transition("pausing", ("pause",))
            self.log("pausing: finishing frames already accepted ...")
        elif self.scan_state == "paused" and not self.lost and not self.switching["busy"]:
            self.live["feeder"].transition("resuming", ("resume",))
            self.log("resuming the same scan ...")

    def can_start_separate_piece(self, now=None):
        """Enable only when the worker reports a fresh, established scan holding after loss."""
        if self.scan_state != "scanning" or self.lost or self.switching["busy"]:
            return False
        live = self.live
        progress, proc = live.get("progress"), live.get("proc")
        now = time.monotonic() if now is None else now
        return (proc is not None and progress is not None and progress.tracking is False
                and bool(progress.cue.get("can_start_separate_piece"))
                and progress.last_frame is not None and 0 <= now - progress.last_frame <= progress.RECENT_S
                and proc.is_alive())

    def start_separate_piece(self):
        """Request a disconnected piece explicitly; the worker rechecks eligibility."""
        if not self.can_start_separate_piece():
            return False
        self.live["commands"].put(("start_separate_piece",))
        return True

    def cancel_countdown(self):
        if self.scan_state == "counting":
            self.scan_state, self.count = "idle", None
            self.log("start cancelled")

    def check_countdown(self):
        """While counting: the time left in the bar under the depth preview; the scan starts at 0."""
        left = self.count["until"] - time.monotonic()
        if left <= 0:
            continue_from = self.count["from"]
            self.scan_state, self.count = "idle", None
            self.begin_scan(continue_from)
            return None
        return f"Starting in {math.ceil(left)} ...  (Esc cancels)", GOOD

    def aim_for_start(self):
        """The depth pixel a scan starting now takes its first piece at: the picked piece's (fresh), else None."""
        piece = fresh_aim()
        return None if piece is None or piece["at"] is None else [round(piece["at"][0], 1), round(piece["at"][1], 1)]

    def begin_scan(self, continue_from=None):
        if continue_from:
            try:
                cloud.Scanner.check_continue_camera(continue_from)
            except (OSError, ValueError) as e:
                self.log(f"cannot continue: {e}")
                return
        live = self.live
        if live["proc"] is None:
            self.start_worker()
        aim = None if continue_from else self.aim_for_start()
        capture = {"ir": self.ir_settings(), **({"aim": aim} if aim else {})}
        out = fr.capture_dir(fr.CAPTURES, "-scan", live=True)
        live["name"], live["last"], live["merging"] = out.name, None, ""
        live["progress"] = livescan.ScanProgress()
        live["done"].clear()
        self.tracked_reference.clear()
        self.model_pane.reset()
        scan_view.update(active=True, return_guide=False, keep_base=not self.remove_base.get(),
                         mask=None, t=0.0, lost=False, model=None)
        live["feeder"].new_scan()
        self.ticks.clear()
        live["commands"].put(("start", self.preset.get(), out, self.keep_raw.get(), continue_from, calib.CAMERA.mode,
                              self.remove_base.get(), {"capture": capture}))
        self.scan_state = "scanning"  # (after "start": the feeder sends frames from now on)
        self.lighter(True)
        self.log(f"scan started: {out.name} ({self.preset.get()}, point distance {cloud.PRESETS_MM[self.preset.get()]} mm)"
                 + (f", continuing {continue_from.name}" if continue_from else "")
                 + (f", on the picked object (depth pixel {aim[0]:.0f}, {aim[1]:.0f})" if aim else ""))

    def aim_click(self, name, x, y):
        """A click on the depth or colour preview picks the piece there: for the aim while idle or counting down, and
        while scanning to help reacquire the held model. A normal click never starts a separate piece."""
        if self.scan_state == "scanning":
            if self.live["proc"] is not None:
                side = DEPTH_W if name == "Depth" else SIDE_W
                uv = (x * fr.W / side, y * fr.H / (side * fr.H // fr.W))  # (full size: that preview's own pixels)
                self.live["commands"].put(("pick", "depth" if name == "Depth" else "colour", uv))
                self.log("picked: try to reacquire the existing model here")
            return
        if self.scan_state not in ("idle", "counting"):
            return
        if name == "Depth":
            pick = (x * fr.W / DEPTH_W, y * fr.H / (DEPTH_W * fr.H // fr.W))
        else:  # colour: the frame's point that the colour camera sees nearest the click
            frame = aim_view["all"]
            if frame is None:
                return
            xyz, pix = frame
            cx, cy = calib.CAMERA.colour_px(pix[:, 0], pix[:, 1], xyz[:, 2])
            i = int(np.argmin(np.hypot(cx - x * fr.W / SIDE_W, cy - y * fr.H / (SIDE_W * fr.H // fr.W))))
            pick = (float(pix[i, 0]), float(pix[i, 1]))
        aim_view.update(pick=pick, follow=None)
        self.message = "aim: picked the object there (right-click or Esc: back to the centre)"

    def aim_clear(self):
        if aim_view["pick"] is not None:
            aim_view.update(pick=None, follow=None)
            self.message = "aim: the object at the centre of the view"

    def escape(self):
        if self.scan_state == "counting":
            self.cancel_countdown()
        else:
            self.aim_clear()

    def continue_scan(self):
        """Go on with the newest saved scan (saved by this version: it has merged.npz)."""
        if self.scan_state != "idle":
            return
        saved = sorted(d for d in fr.CAPTURES.glob("*-scan") if (d / "merged.npz").exists())
        if not saved:
            self.log("nothing to continue: no saved scan with its frames yet")
            return
        self.toggle_scan(continue_from=saved[-1])

    def lighter(self, scanning):
        """While scanning, the colour and IR previews and the marker ring update less often (the GIL is the
        viewer's limit: idle, its threads already used 76 / 42 / 21 / 13% of a core on 2026-11-05)."""
        for s in (self.color, self.ir):
            s.preview_fps = SCAN_SIDE_FPS if scanning else PREVIEW_FPS
        found_markers["hz"] = SCAN_MARKER_HZ if scanning else MARKER_HZ

    def check_scan(self):
        """Handle the worker's replies (the feeder took them; it also sends the frames)."""
        live = self.live
        feeder = live["feeder"]
        while feeder is not None:
            try:
                kind, rest, raw = feeder.out.get_nowait()
            except queue.Empty:
                break
            if kind == "ready":
                live["url"] = rest[0]
            elif kind == "log":
                self.log(rest[0])
                if rest[0].startswith("tracking on the GPU: "):
                    live["gpu"] = rest[0].split(": ", 1)[1]
                elif rest[0].startswith("tracking on the CPU"):
                    live["gpu"] = "the CPU"
                elif rest[0].startswith("merging on a thread"):
                    live["merging"] = "thread"
            elif kind == "start_failed":
                self.log(rest[0])
                if self.scan_state != "cancelling":
                    self.scan_state = "idle"
                    self.lighter(False)
            elif kind == "cancelled" and self.scan_state == "cancelling":
                place, warning = rest
                live.update(last=None, name="", merging="", gpu="", progress=livescan.ScanProgress())
                live["done"].clear()
                self.ticks.clear()
                self.scan_state, self.count = "idle", None
                self.lighter(False)
                if hasattr(self, "model_pane"):
                    self.model_pane.reset()
                scan_view.update(mask=None, lost=False, model=None)
                if warning and place:
                    self.log(warning)
                if place:
                    path = Path(place)
                    try:
                        short_path = Path("captures") / path.relative_to(fr.CAPTURES)
                    except ValueError:
                        short_path = path
                    self.log(f"scan cancelled without saving; recovery files are in {short_path}"
                             + (" (see Messages for a warning)" if warning else ""))
                elif warning:
                    self.log(f"scan cancellation: {warning}")
                else:
                    self.log("scan cancelled without saving")
            elif kind == "paused" and self.scan_state == "pausing":
                self.scan_state = "paused"
                self.lighter(False)
                scan_view.update(mask=None, lost=False, model=None)
                self.log("scan paused; drag the Live 3D model to inspect it here")
            elif kind == "resumed" and self.scan_state == "resuming":
                live["done"].clear()
                self.ticks.clear()
                self.lighter(True)
                live["progress"].resume()
                feeder.transition("scanning", fresh=True)
                self.log("scan resumed; hold or return to the last tracked view")
            elif kind == "frame_done":
                merged, frames, points, tracking, cue, view, model, model_t = (list(rest) + [None] * 8)[:8]
                if self.scan_state in ("scanning", "pausing") and cue is not None:
                    live["progress"].observe(frames, merged, points, tracking, cue)
                    if hasattr(self, "model_pane"):
                        self.model_pane.set_pieces(cue.get("piece", 1))
                    if not self.lost and not self.switching["busy"]:
                        cap = self.depth.cap
                        accepted = raw if cue.get("reference_ok", True) else None
                        self.tracked_reference.observe(cue.get("piece", 1), tracking, accepted,
                                                       (cap.width, cap.height, cap.bytesperline), view)
                if self.scan_state == "scanning" and cue is not None:
                    live["done"].append(time.monotonic())  # (the Status box: frames back a second)
                    live["last"] = (merged, frames, points, tracking)
                    scan_view.update(mask=view, t=time.monotonic(), lost=not tracking,
                                     coverage_supported=cue.get("coverage_supported"),
                                     model=None if model is None or raw is None else (model, raw))
            elif kind == "saved":
                path, points = rest
                self.log(f"scan saved: {points:,} points -> {Path(path).parent.name}/cloud.ply; optimising ...")
                self.scan_state = "optimising"
                self.lighter(False)
                fed = feeder.report()
                if fed:
                    ticks = 1000 * np.array(self.ticks) if self.ticks else np.array([np.nan])
                    self.log(f"viewer: {fed}; its window ticked every {np.median(ticks):.0f} ms "
                             f"(95% within {np.percentile(ticks, 95):.0f} ms)")
            elif kind == "optimised":
                self.log(rest[0] + ("; making the mesh ..." if len(rest) > 1 and rest[1] else ""))
                self.scan_state = "meshing" if len(rest) > 1 and rest[1] else "idle"
            elif kind == "meshed":
                self.log(rest[0])
                self.scan_state = "idle"


        model = getattr(self, "model_pane", None)
        if model is not None and feeder is not None and self.scan_state in ("scanning", "pausing", "paused", "resuming"):
            sample = feeder.take_coverage()
            if sample is not None:
                model.state.receive(*sample)

        proc = live["proc"]
        if proc is not None and (code := proc.exitcode) is not None:
            state, name = self.scan_state, live["name"]
            for channel in (live["commands"], live["replies"]):
                channel.cancel_join_thread()  # an unread frame can be stuck in the dead child's pipe
                channel.close()
            proc.join(timeout=0)
            live.update(proc=None, commands=None, replies=None, feeder=None, url=None, last=None, gpu="", merging="")
            self.scan_state, self.count = "idle", None
            self.lighter(False)
            if hasattr(self, "model_pane"):
                self.model_pane.reset(worker_gone=True)
            scan_view.update(mask=None, lost=False, model=None)
            interrupted = f" while {state}; check {name} for partial files" if state != "idle" and name else ""
            self.log(f"scan worker exited (code {code}){interrupted}. Start a new scan to restart it.")

class Feeder(threading.Thread):
    """The scan worker's frames and replies, off the Tk loop, for the worker's whole life. While scanning it hands
    over each new depth frame with its colour pair as soon as fewer than 2 are in flight, and passes every reply
    on to the Tk thread at once (`out`: (kind, rest, raw depth of a frame_done or None)). From the Tk tick the
    send waited for a tick that the busy loop could not keep (2026-11-05, the platform scan: 9.2 fps live, the
    worker waiting 32 ms a frame; bench --live, which feeds the worker like this, 15 fps on the same desktop)."""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app, self.out, self.lock = app, queue.Queue(), threading.Lock()
        self.live = {key: app.live[key] for key in ("proc", "commands", "replies")}
        self.resume_after = 0.0
        self.coverage_lock, self.coverage_latest = threading.Lock(), None
        self.inflight, self.sent_stamp, self.raw = 0, 0.0, {}  # raw: depth buffers in work, by capture time
        self.stats = {"sent": 0, "t0": None, "t1": None, "lag": collections.deque(maxlen=4000)}  # lag: capture -> sent

    def new_scan(self):
        with self.lock:
            self.inflight, self.raw = 0, {}
            self.resume_after = 0.0
            self.stats = {"sent": 0, "t0": None, "t1": None, "lag": collections.deque(maxlen=4000)}

    def transition(self, state, command=None, *, fresh=False):
        """Order scan controls and frame enqueues through one lock."""
        with self.lock:
            if fresh:
                # Don't send a frame captured during inspection, even if the stream has stalled.
                self.resume_after = max(self.app.depth.stamp, time.monotonic())
            self.app.scan_state = state
            if command is not None:
                self.live["commands"].put(command)

    def offer_coverage(self, sample):
        with self.coverage_lock:
            self.coverage_latest = sample

    def take_coverage(self):
        with self.coverage_lock:
            sample, self.coverage_latest = self.coverage_latest, None
            return sample

    def run(self):
        live, depth, color = self.live, self.app.depth, self.app.color
        while live["proc"] is not None and live["proc"].is_alive():
            try:
                kind, *rest = live["replies"].get(timeout=0.004)
            except queue.Empty:
                kind = None
            except (EOFError, OSError, ValueError):  # the process or its queue has shut down
                break
            if kind == "coverage_update":
                self.offer_coverage(rest)  # replace stale preview work; never hold up a control reply
                continue
            if kind is not None:
                raw = None
                if kind == "frame_done":
                    with self.lock:
                        self.inflight = max(0, self.inflight - 1)
                        raw = self.raw.pop(rest[7] if len(rest) > 7 else None, None)
                self.out.put((kind, rest, raw))
                continue  # (replies first: they free a place)
            if self.app.scan_state == "scanning" and self.inflight < 2 and color.raw and depth.raw \
                    and depth.stamp != self.sent_stamp and depth.stamp > self.resume_after:
                self.send(live, depth, color)
            else:
                depth.fresh.wait(0.004)
                depth.fresh.clear()

    def send(self, live, depth, color):
        d_raw, d_t = depth.raw, depth.stamp
        c_raw, c_t = color.nearest(d_t)  # no IR: the worker's raw copy leaves it out (frames.SCAN_STREAMS)
        with self.lock:
            if self.app.scan_state != "scanning" or d_t <= self.resume_after or c_t <= self.resume_after:
                return  # the Tk thread may have queued a pause/stop while we selected frames
            try:
                live["commands"].put(("frame", c_raw, None, d_raw, depth.cap.bytesperline, (c_t, 0.0, d_t)))
            except (OSError, ValueError):
                return  # its worker died and Tk closed the queue
            self.sent_stamp, self.inflight = d_t, self.inflight + 1
            self.raw = {t: r for t, r in self.raw.items() if t > d_t - 2.0}  # (for the live model view)
            self.raw[d_t] = d_raw
        now, st = time.monotonic(), self.stats
        st["sent"] += 1
        st["t0"], st["t1"] = st["t0"] or now, now
        st["lag"].append(now - d_t)

    def report(self):
        """One line on how the scan was fed: frames sent per second, capture to send (median)."""
        st = self.stats
        if not st["sent"] or st["t1"] is None or st["t1"] <= st["t0"]:
            return None
        lag = 1000 * float(np.median(list(st["lag"]))) if st["lag"] else float("nan")
        return f"{st['sent']} frames sent, {st['sent'] / (st['t1'] - st['t0']):.1f} a second, {lag:.0f} ms after capture"


def refuse(text):
    print(text, flush=True)
    root = tk.Tk()
    root.withdraw()
    tkinter.messagebox.showerror("triBUUTSCAN", text)
    root.destroy()


def scanner_problem(error):
    """What to tell the user when the scanner can't be opened at start."""
    if usbdepth.find() is None:
        return "The scanner is not connected.\n\nPlug it in (or replug it), wait a few seconds, then press Retry."
    return (f"The scanner is connected but could not be opened:\n{error}\n\nClose other programs that use it "
            "(CrealityScan, a measure or calibrate run), or replug it, wait a few seconds, then press Retry.")


def open_app(root, ask=tkinter.messagebox.askretrycancel):
    """The App, or None if the user gives up: while the scanner can't be opened, say why and offer Retry."""
    while True:
        try:
            return App(root)
        except (RuntimeError, OSError) as e:
            print(f"scanner not available: {e}", flush=True)
            if not ask("triBUUTSCAN", scanner_problem(e), parent=root):
                return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="triBUUTSCAN live USB scanner viewer")
    parser.add_argument(gpu.FORCE_CPU, action="store_true", help="track and mesh on the CPU, even with a GPU")
    args = parser.parse_args(argv)
    gpu.force_cpu([gpu.FORCE_CPU] if args.force_cpu else [])  # worker and mesh jobs inherit it
    LOCK.parent.mkdir(exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released by the system when this process ends
    except BlockingIOError:
        refuse("The viewer is already running: use that window, or close it first.")
        return
    root = tk.Tk()
    root.withdraw()  # only the Retry question until the scanner opens
    app = open_app(root)
    if app is None:
        root.destroy()
        return
    root.deiconify()
    app.build_window()
    app.start()
    root.mainloop()


if __name__ == "__main__":
    main()
