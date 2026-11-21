"""Bounded orthographic point-cloud preview for the Tk viewer; no GUI or GL here."""
import threading

import numpy as np

from .image import ppm


MAX_POINTS = 4500
CANDIDATES = MAX_POINTS * 4  # sample early, then apply the existing scan-region mask
AMBER = (255, 196, 92)       # observations 1–2
BLUE = (83, 185, 255)        # observations 3+
BACKGROUND = (22, 25, 31)


def sample_indices(length, limit):
    """Use stable, evenly spaced indices rather than making an array of all point positions."""
    return slice(None) if length <= limit else np.linspace(0, length - 1, limit, dtype=np.int64)


def cloud_candidates(cloud):
    """Centres and counts for bounded candidate voxels, without expanding the full cloud."""
    idx = sample_indices(len(cloud.seen), CANDIDATES)
    return cloud.xyz[idx] / cloud.n[idx, None], cloud.seen[idx]


def sample_points(xyz, seen, limit=MAX_POINTS):
    idx = sample_indices(len(xyz), limit)
    return np.asarray(xyz[idx], np.float32), np.minimum(seen[idx], 65535).astype(np.uint16)


def from_browser_payload(payload, n):
    """Read only the sampled points of an archived piece; browser coordinates are metres, Y up."""
    xyz = np.frombuffer(payload, dtype="<f4", offset=8, count=n * 3).reshape(n, 3)
    seen = np.frombuffer(payload, dtype="<u2", offset=8 + 12 * n + (3 * n + 3) // 4 * 4, count=n)
    xyz, seen = sample_points(xyz, seen)
    return xyz * np.array([1000, -1000, -1000], np.float32), seen


def render(xyz_mm, seen, width, height, yaw, pitch, zoom, framing=None):
    """P6 image from a bounded (camera axes, mm) cloud; no points means blank, not unscanned pixels."""
    image = np.full((height, width, 3), BACKGROUND, np.uint8)
    if len(xyz_mm):
        xyz, seen = sample_points(xyz_mm, seen)
        if framing is None:
            origin = (xyz.min(axis=0) + xyz.max(axis=0)) * 0.5
            framing = origin, max(float(np.linalg.norm(xyz - origin, axis=1).max()), 1.0)
        origin, radius = framing
        x, y, z = (xyz - origin).T
        sy, cy, sp, cp = np.sin(yaw), np.cos(yaw), np.sin(pitch), np.cos(pitch)
        u, rz = cy * x + sy * z, -sy * x + cy * z
        v, depth = cp * y - sp * rz, sp * y + cp * rz
        scale = 0.47 * min(width, height) * np.clip(zoom, 0.25, 6.0) / radius
        # Low screen cost, but a small 2x2 splat makes samples legible against the dark field.
        column = np.rint(width / 2 + scale * u).astype(np.int64)
        row = np.rint(height / 2 + scale * v).astype(np.int64)
        color = np.where((seen >= 3)[:, None], BLUE, AMBER).astype(np.uint8)
        order = np.argsort(-depth, kind="stable")  # far to near: closest sample wins
        winner = np.full(width * height, -1, np.int32)
        priority = np.arange(len(order), dtype=np.int32)
        for dy, dx in ((0, 0), (1, 0), (0, 1), (1, 1)):
            rr, cc = row[order] + dy, column[order] + dx
            inside = (rr >= 0) & (rr < height) & (cc >= 0) & (cc < width)
            np.maximum.at(winner, rr[inside] * width + cc[inside], priority[inside])
        painted = winner >= 0
        image.reshape(-1, 3)[painted] = color[order[winner[painted]]]
    return ppm(image)


class PreviewRenderer:
    """A latest-wins projection thread. The Tk side only copies completed PPM bytes into PhotoImage."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.latest, self.pending, self.serial = None, None, 0
        self.ready = threading.Condition()
        self.thread, self.closed, self.frame_id = None, False, 0

    def reset_frame(self):
        """Use on a new take or explicit piece change, not on each live snapshot."""
        with self.ready:
            self.frame_id += 1
            self.pending = self.latest = None

    def offer(self, xyz_mm, seen, yaw, pitch, zoom):
        with self.ready:
            if self.closed:
                return self.serial
            self.serial += 1
            self.pending = (self.serial, self.frame_id, xyz_mm, seen, yaw, pitch, zoom)
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, name="coverage-preview", daemon=True)
                self.thread.start()
            self.ready.notify()
            return self.serial

    def run(self):
        frame_id, framing = None, None
        while True:
            with self.ready:
                self.ready.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                serial, update, xyz, seen, yaw, pitch, zoom = self.pending
                self.pending = None
            if update != frame_id:
                frame_id, framing = update, None
            if len(xyz):
                origin = (xyz.min(axis=0) + xyz.max(axis=0)) * 0.5 if framing is None else framing[0]
                radius = max(float(np.linalg.norm(xyz - origin, axis=1).max()), 1.0)
                # Keep the viewing centre fixed as points arrive. Only zoom out if the cloud expands.
                framing = origin, radius if framing is None else max(framing[1], radius)
            out = render(xyz, seen, self.width, self.height, yaw, pitch, zoom, framing)
            with self.ready:
                if not self.closed and update == self.frame_id:
                    self.latest = serial, out

    def close(self):
        with self.ready:
            self.closed = True
            self.pending = None
            self.ready.notify()
