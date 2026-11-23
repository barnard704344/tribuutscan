"""Persistent world-space cleanup regions, shared by point-cloud and mesh output."""
import numpy as np


def validate(rule):
    if not isinstance(rule, dict):
        raise ValueError('Invalid cleanup region')
    matrix = np.asarray(rule.get('matrix'), float)
    polygon = np.asarray(rule.get('polygon'), float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all() or np.max(np.abs(matrix)) > 1e8
            or abs(np.linalg.det(matrix)) < 1e-14 or polygon.ndim != 2 or polygon.shape[1] != 2
            or not 3 <= len(polygon) <= 48 or not np.isfinite(polygon).all() or np.max(np.abs(polygon)) > 1.01):
        raise ValueError('Choose a finite lasso with 3 to 48 screen points')
    area = abs(np.sum(polygon[:, 0] * np.roll(polygon[:, 1], 1) - polygon[:, 1] * np.roll(polygon[:, 0], 1))) / 2
    if area < 1e-6:
        raise ValueError('Selection is too small')
    return dict(matrix=matrix.tolist(), polygon=polygon.tolist())


def keep(xyz_mm, rules):
    """Keep camera-world mm outside all deleted frusta; projection matrices use viewer metres."""
    keep = np.ones(len(xyz_mm), bool)
    if not rules or not len(keep):
        return keep
    xyz = np.asarray(xyz_mm) * np.array([.001, -.001, -.001])
    for rule in rules:
        m, polygon = np.asarray(rule['matrix']), np.asarray(rule['polygon'])
        clip = xyz @ m[:, :3].T + m[:, 3]
        w = clip[:, 3]
        ndc = clip[:, :3] / np.where(abs(w[:, None]) > 1e-12, w[:, None], 1)
        x, y = ndc[:, 0], ndc[:, 1]
        inside = np.zeros(len(xyz), bool)
        for a, b in zip(polygon, np.roll(polygon, 1, axis=0)):
            crosses = (a[1] > y) != (b[1] > y)
            if a[1] != b[1]:
                inside ^= crosses & (x < (b[0]-a[0]) * (y-a[1]) / (b[1]-a[1]) + a[0])
        keep &= ~(inside & (w > 0) & (ndc[:, 2] >= -1) & (ndc[:, 2] <= 1))
    return keep
