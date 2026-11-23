"""Bounded local surface registration in viewer metres; never writes a source scan."""
import json
import sys
import time

import numpy as np

from . import alignment, cleanup

RESULT_PREFIX = 'ALIGNMENT_REFINEMENT '
MAX_POINTS = 30000


def settings(distance, region):
    if isinstance(distance, bool):
        raise ValueError('Choose a matching distance from 1 to 50 mm')
    distance = float(distance)
    if not np.isfinite(distance) or not 1 <= distance <= 50:
        raise ValueError('Choose a matching distance from 1 to 50 mm')
    return distance, None if region is None else cleanup.validate(region)


def region_mask(points, region):
    # Cleanup shares the same projected selection geometry, but consumes camera-world mm.
    return ~cleanup.keep(np.asarray(points) * [1000, -1000, -1000], [region])


def refine(reference, moving, transform, max_distance_mm=10, region=None):
    """Robust multi-scale point-to-plane ICP, started from the user's rough placement.

    A surface fit is a proposal, not a dimensional-accuracy or correct-pose certificate.
    Reject weak overlap, unconstrained surfaces, large jumps and worsened distance.
    """
    import open3d as o3d
    distance_mm, region = settings(max_distance_mm, region)
    distance = distance_mm / 1000
    start = alignment.rigid(transform).copy()
    arrays = []
    for value in (reference, moving):
        p = np.asarray(value, dtype=float)
        if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all() or np.max(np.abs(p), initial=0) > 10:
            raise ValueError('Scans need finite XYZ coordinates in metres')
        arrays.append(p)
    reference, moving = arrays
    placed = moving @ start[:3, :3].T + start[:3, 3]
    if region is not None:
        reference, moving = reference[region_mask(reference, region)], moving[region_mask(placed, region)]
    counts = [len(reference), len(moving)]
    if min(counts) < 100:
        raise ValueError('Select a larger shared region with at least 100 points on each scan')

    def cloud(points):
        # Sampling is deterministic, unbiased by saved point ordering and bounded in the worker.
        if len(points) > MAX_POINTS:
            points = points[np.random.default_rng(0).choice(len(points), MAX_POINTS, replace=False)]
        return o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))

    src, dst = cloud(moving), cloud(reference)
    reg = o3d.pipelines.registration
    initial = reg.evaluate_registration(src, dst, distance, start)
    if initial.fitness < .2 or len(initial.correspondence_set) < 100:
        raise ValueError('Too little overlap. Move/rotate the scans closer or select their shared surface')
    candidate = start.copy()
    deadline = time.monotonic() + 20
    for voxel in (max(.001, distance / 4), max(.0005, distance / 8), max(.00025, distance / 16)):
        a, b = src.voxel_down_sample(voxel), dst.voxel_down_sample(voxel)
        if min(len(a.points), len(b.points)) < 60:
            continue
        b.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 4, max_nn=40))
        estimator = reg.TransformationEstimationPointToPlane(reg.TukeyLoss(distance / 2))
        # Short native batches make the time budget observable between calls.
        for _ in range(4):
            if time.monotonic() > deadline:
                raise ValueError('Refinement took too long. Select a smaller overlap region and retry')
            result = reg.registration_icp(a, b, distance, candidate, estimator,
                reg.ICPConvergenceCriteria(relative_fitness=1e-6, relative_rmse=1e-6, max_iteration=5))
            previous, candidate = candidate, alignment.rigid(result.transformation).copy()
            if np.max(np.abs(candidate - previous)) < 1e-7:
                break

    final = reg.evaluate_registration(src, dst, distance, candidate)
    pairs = np.asarray(final.correspondence_set)
    if final.fitness < .2 or len(pairs) < 100 or final.fitness < initial.fitness * .9:
        raise ValueError('The proposed fit lost overlap. Keep the manual placement and choose a better region')
    if final.inlier_rmse > initial.inlier_rmse + .00001:
        raise ValueError('Surface distance did not improve. Keep the manual placement or choose another region')
    # Point-to-plane observability: a plane or rotationally symmetric patch cannot constrain 6 DOF.
    dst.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=max(.006, distance), max_nn=60))
    p = np.asarray(src.points)[pairs[:, 0]] @ candidate[:3, :3].T + candidate[:3, 3]
    n = np.asarray(dst.normals)[pairs[:, 1]]
    centred = p - p.mean(0)
    scale = np.sqrt(np.mean(np.sum(centred ** 2, axis=1)))
    if scale < .001:
        raise ValueError('Choose a larger overlap region')
    jacobian = np.column_stack((np.cross(centred / scale, n), n))
    singular = np.linalg.svd(jacobian, compute_uv=False)
    if singular[-1] < singular[0] * .05:
        raise ValueError('Surface shape is ambiguous. Include corners or varied curved features, or use matching points')
    delta = candidate @ np.linalg.inv(start)
    angle = np.degrees(np.arccos(np.clip((np.trace(delta[:3, :3]) - 1) / 2, -1, 1)))
    centre = np.asarray(src.points).mean(0)
    before_centre = centre @ start[:3, :3].T + start[:3, 3]
    after_centre = centre @ candidate[:3, :3].T + candidate[:3, 3]
    shift = float(np.linalg.norm(after_centre - before_centre))
    if angle > 20 or shift > distance * 2:
        raise ValueError('Refinement moved too far. Improve the rough placement first')
    return dict(transform=candidate.tolist(), before_rmse_mm=initial.inlier_rmse * 1000,
                after_rmse_mm=final.inlier_rmse * 1000, before_overlap=initial.fitness,
                after_overlap=final.fitness, matched_points=len(pairs), selected_points=counts,
                sampled_points=[len(dst.points), len(src.points)], shift_mm=shift * 1000,
                rotation_degrees=float(angle), max_distance_mm=distance_mm)


def run(spec):
    from . import view3d
    from pathlib import Path
    a, b = Path(spec['reference']), Path(spec['moving'])
    if [alignment.version(a), alignment.version(b)] != spec['versions']:
        raise ValueError('A source scan changed; reload both scans')
    alignment.compatible(a, b)
    points = []
    for path in (a, b):
        rec = view3d.read_ply(path / 'cloud.ply')
        points.append(np.column_stack([rec[key] for key in ('x', 'y', 'z')]))
    result = refine(*points, spec['transform'], spec['max_distance_mm'], spec.get('region'))
    if [alignment.version(a), alignment.version(b)] != spec['versions']:
        raise ValueError('A source scan changed during refinement; reload both scans')
    return result


def main():
    try:
        result = run(json.load(sys.stdin))
        print(RESULT_PREFIX + json.dumps(result), flush=True)
        print('Surface refinement ready for inspection; source scans unchanged.', flush=True)
    except Exception as error:
        print(str(error), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
