"""Saved-scan rigid alignment. Viewer coordinates are metres, Y up; sources stay untouched."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

FILES = ('cloud.ply', 'session.json', 'keyframes.npz', 'merged.npz', 'scene.json',
         'frame_poses.txt', 'track.csv', 'labels.npz', 'cleanup.json', 'frame-edits.json', 'frame-source.zip')


def rigid(value):
    """Accept only finite, bounded proper rigid transforms, never scale/reflection/shear."""
    m = np.asarray(value, dtype=np.float64)
    if (m.shape != (4, 4) or not np.isfinite(m).all()
            or not np.allclose(m[3], [0, 0, 0, 1], atol=1e-8)
            or not np.allclose(m[:3, :3].T @ m[:3, :3], np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(m[:3, :3]), 1, atol=1e-6)
            or np.linalg.norm(m[:3, 3]) > 10):
        raise ValueError('Expected a rigid alignment with translation under 10 metres')
    return m


def fit(reference, moving):
    """Least-squares moving -> reference from 3..12 corresponding points in metres."""
    a, b = np.asarray(reference, dtype=float), np.asarray(moving, dtype=float)
    if (a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3 or not 3 <= len(a) <= 12
            or not np.isfinite(a).all() or not np.isfinite(b).all()
            or np.max(np.abs(a)) > 10 or np.max(np.abs(b)) > 10):
        raise ValueError('Choose 3 to 12 corresponding point pairs')
    ac, bc = a - a.mean(0), b - b.mean(0)
    if any(np.linalg.svd(p, compute_uv=False)[1] < 0.0001 for p in (ac, bc)):
        raise ValueError('Choose spread-out points, not points on one line')
    u, _, vt = np.linalg.svd(bc.T @ ac)
    fix = np.eye(3)
    fix[2, 2] = np.linalg.det(vt.T @ u.T)
    r = vt.T @ fix @ u.T
    m = np.eye(4)
    m[:3, :3], m[:3, 3] = r, a.mean(0) - r @ b.mean(0)
    error = b @ r.T + m[:3, 3] - a
    return dict(transform=rigid(m).tolist(), landmark_rmse_mm=float(np.sqrt(np.mean(np.sum(error**2, 1))) * 1000))


def version(directory):
    entries = []
    for name in FILES:
        p = Path(directory) / name
        if p.is_symlink():
            raise ValueError('Alignment does not accept linked scan files')
        if p.exists():
            s = p.stat()
            entries.append((name, s.st_size, s.st_mtime_ns))
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def metadata(directory):
    info = json.loads((Path(directory) / 'session.json').read_text())
    if not isinstance(info, dict):
        raise ValueError('Invalid session metadata')
    return info


def compatible(a, b):
    """The first alignment release handles uncropped, single-piece free-object sessions."""
    infos = []
    for directory in (a, b):
        version(directory)
        if any(not (Path(directory) / name).is_file() for name in FILES[1:5]):
            raise ValueError('Both scans need their saved session, keyframes, merged frames and scene')
        info = metadata(directory)
        if (Path(directory) / 'frame-edits.json').exists():
            raise ValueError('Align separate scans before frame editing, or reset frame edits first')
        if (Path(directory) / 'cleanup.json').exists():
            raise ValueError('Align before lasso cleanup, or clear cleanup first')
        if info.get('mode') != 'free' or info.get('crop') or len(info.get('piece_list') or [0]) > 1:
            raise ValueError('Use uncropped, single-piece Head / free object scans for this first alignment release')
        if not isinstance(info.get('camera'), dict) or not info['camera'].get('fingerprint'):
            raise ValueError('Both scans need saved camera calibration fingerprints')
        infos.append(info)
    if infos[0]['camera']['fingerprint'] != infos[1]['camera']['fingerprint'] or infos[0].get('depth_mode') != infos[1].get('depth_mode'):
        raise ValueError('Scans must use matching camera calibration and depth range')


def merge(a, b, out, transform, versions, log=print):
    """Save full session data in a new folder. Do not optimise away the user's alignment."""
    from . import cloud
    a, b, out = Path(a), Path(b), Path(out)
    if a.resolve() == b.resolve() or out.exists() or out.parent.resolve() != a.parent.resolve():
        raise ValueError('Choose two different scans and a new output in the same capture directory')
    transform = rigid(transform)
    if [version(a), version(b)] != versions:
        raise ValueError('A source scan changed; reload both scans before saving')
    compatible(a, b)
    # Viewer PLY is FLIP * camera-world, with translations in metres in both systems.
    flip = np.eye(4)
    flip[:3, :3] = cloud.FLIP
    t = flip @ transform @ flip
    reference = moving = None
    try:
        reference = cloud.Scanner.restore(a, log=log)
        moving = cloud.Scanner.restore(b, log=log)
        ids = [i for i, _ in reference.poses + reference.frame_poses()]
        if not ids or not moving.poses:
            raise ValueError('Both scans need accepted saved frames')
        offset = max(ids) + 1000
        reference.keyframes += [(i + offset, t @ p, *rest) for i, p, *rest in moving.keyframes]
        reference.poses += [(i + offset, t @ p) for i, p in moving.poses]
        reference.merged_data.update({i + offset: d for i, d in moving.merged_data.items()})
        reference.track_log += [(i + offset, status, None if p is None else t @ p)
                                for i, status, p in moving.track_log]
        reference.track_info += [(i + offset, d) for i, d in moving.track_info]
        reference.labels.update({i + offset: m for i, m in moving.labels.items()})
        reference.sources += [{**s, 'first_index': s['first_index'] + offset} for s in moving.sources]
        for key in ('frames', 'merged', 'lost', 'relocked', 'shape_refinds'):
            reference.stats[key] = reference.stats.get(key, 0) + moving.stats.get(key, 0)
        reference.crop = None
        reference.remerge({})
        with tempfile.TemporaryDirectory(prefix='.alignment-', dir=out.parent) as temp:
            stage = Path(temp)
            reference.save(stage)
            (stage / 'alignment.json').write_text(json.dumps(dict(
                reference=a.name, moving=b.name, transform_viewer_metres=transform.tolist(),
                source_versions=versions, method='manual-rigid', optimised=False), indent=2))
            if [version(a), version(b)] != versions:
                raise ValueError('A source scan changed during alignment; output was not published')
            if out.exists():
                raise ValueError('Output already exists')
            stage.rename(out)
        log(f'Aligned session saved: {out.name}')
    finally:
        for scanner in (reference, moving):
            if scanner is not None:
                scanner.discard()


def main():
    spec = json.load(sys.stdin)
    merge(spec['reference'], spec['moving'], spec['output'], spec['transform'], spec['versions'])


if __name__ == '__main__':
    main()
