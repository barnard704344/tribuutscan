"""Surface refinement, selection coordinates and asynchronous host lifecycle."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from tribuutscan import alignment, alignment_refine, frames, webapp
from test_alignment import hashes, rejected


def surfaces(count=6000):
    rng = np.random.default_rng(42)
    unit = rng.normal(size=(count, 3))
    unit /= np.linalg.norm(unit, axis=1)[:, None]
    radius = 1 + .13 * np.sin(unit[:, 0] * 6) * np.cos(unit[:, 1] * 4)
    reference = unit * radius[:, None] * [.06, .045, .035]
    angle = np.radians(2)
    expected = np.array([[np.cos(angle), -np.sin(angle), 0, .002],
                         [np.sin(angle), np.cos(angle), 0, -.001],
                         [0, 0, 1, .0015], [0, 0, 0, 1.]])
    return reference, (reference - expected[:3, 3]) @ expected[:3, :3], expected


def surface_session(root, name, points):
    """Full synthetic session whose saved frames and PLY agree."""
    from tribuutscan import cloud
    rec = root / (name + '-rec'); rec.mkdir()
    scanner = cloud.Scanner(mode='free', log=lambda *_: None)
    xyz = (points * [1000, -1000, -1000]).astype(np.float32)
    rgb = np.full(xyz.shape, 150, np.uint8)
    normals = np.tile([0, 0, -1.], (len(xyz), 1)).astype(np.float32)
    try:
        for i in range(3):
            pose = np.eye(4)
            scanner.poses.append((i, pose))
            scanner.keyframes.append((i, pose, xyz, None, np.empty((0, 3))))
            scanner.merged_data[i] = xyz, rgb, normals
            scanner.track_log.append((i, 'tracked', pose))
            scanner.labels[i] = np.zeros((2, 2), np.uint8)
        scanner.sources = [dict(rec=str(rec), first_index=0)]
        scanner.stats.update(frames=3, merged=3)
        scanner.remerge({}); path = root / name; scanner.save(path)
        return path
    finally:
        scanner.discard()


def test_refinement_recovers_rigid_offset_with_partial_overlap_and_noise():
    reference, moving, expected = surfaces()
    rng = np.random.default_rng(8)
    moving = moving[reference[:, 0] > -.035]
    moving = moving + rng.normal(0, .00008, moving.shape)
    reference = reference[reference[:, 0] < .04]
    moving = np.concatenate([moving, rng.uniform([.2, .2, .2], [.3, .3, .3], (500, 3))])
    originals = reference.copy(), moving.copy()
    result = alignment_refine.refine(reference, moving, np.eye(4))
    # Missing surface at the overlap boundary still contributes to the reported nearest-neighbour RMS.
    assert result['after_rmse_mm'] < result['before_rmse_mm'] * .85
    recovered = np.asarray(result['transform'])
    assert np.allclose(recovered[:3, 3], expected[:3, 3], atol=.0001)
    assert np.linalg.norm(recovered[:3, :3] - expected[:3, :3]) < .003
    assert result['after_overlap'] > .5
    assert np.array_equal(reference, originals[0]) and np.array_equal(moving, originals[1])
    initial = expected.copy(); initial[0, 3] -= .001
    again = alignment_refine.refine(reference, moving, initial)
    assert np.allclose(np.asarray(again['transform'])[:3, 3], expected[:3, 3], atol=.0001)


def test_refinement_rejects_plane_no_overlap_and_invalid_input():
    x, y = np.meshgrid(np.linspace(-.05, .05, 35), np.linspace(-.05, .05, 35))
    plane = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    rejected(lambda: alignment_refine.refine(plane, plane + [0, 0, .001], np.eye(4)))
    sphere = np.random.default_rng(3).normal(size=(3000, 3))
    sphere = sphere / np.linalg.norm(sphere, axis=1)[:, None] * .05
    rejected(lambda: alignment_refine.refine(sphere, sphere + [.001, 0, 0], np.eye(4)))
    a, b, _ = surfaces()
    rejected(lambda: alignment_refine.refine(a, b + 1, np.eye(4)))
    for distance in [0, 51, float('nan'), True]:
        rejected(lambda: alignment_refine.refine(a, b, np.eye(4), distance))
    rejected(lambda: alignment_refine.refine(a[:50], b[:50], np.eye(4)))
    rejected(lambda: alignment_refine.refine(a * float('nan'), b, np.eye(4)))
    rejected(lambda: alignment_refine.refine(a, b, np.eye(4), region={'matrix': []}))


def test_overlap_region_uses_viewer_metres_and_transformed_moving_points():
    region = dict(matrix=np.eye(4).tolist(), polygon=[[-.1, -.1], [.1, -.1], [.1, .1], [-.1, .1]])
    xyz = np.array([[.05, .08, .2], [.2, 0, 0], [0, 0, 2]])
    assert alignment_refine.region_mask(xyz, region).tolist() == [True, False, False]
    a, b, expected = surfaces()
    region['polygon'] = [[-.07, -.055], [.07, -.055], [.07, .055], [-.07, .055]]
    result = alignment_refine.refine(a, np.concatenate([b, b + [.3, 0, 0]]), np.eye(4), region=region)
    assert result['selected_points'] == [len(a), len(b)]
    assert np.allclose(result['transform'], expected, atol=.001)


def test_refinement_worker_preserves_sources_and_rejects_stale_versions():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); a, b, _ = surfaces(2000)
        first, second = surface_session(root, 'a-scan', a), surface_session(root, 'b-scan', b)
        before = hashes(first), hashes(second)
        spec = dict(reference=str(first), moving=str(second), versions=[alignment.version(first), alignment.version(second)],
                    transform=np.eye(4).tolist(), max_distance_mm=10)
        assert alignment_refine.run(spec)['after_rmse_mm'] < .1
        assert (hashes(first), hashes(second)) == before
        spec['versions'] = ['old', 'old']; rejected(lambda: alignment_refine.run(spec))
        spec['versions'] = [alignment.version(first), alignment.version(second)]
        def changed_during_work(*args):
            path = first / 'session.json'; path.write_text(path.read_text() + '\n')
            return {}
        with patch.object(alignment_refine, 'refine', side_effect=changed_during_work):
            rejected(lambda: alignment_refine.run(spec))


def test_refinement_api_guards_and_worker_result_lifecycle():
    from test_webapp import running_service, client
    with running_service() as (service, base):
        request = client(base)
        def command(**payload):
            state = service.state()
            return request('/api/command', dict(action='alignment-refine', instance=state['instance'], revision=state['revision'], **payload))
        def finished():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = json.loads(request('/api/state')[1])
                if not state['processing']: return state
                time.sleep(.03)
            raise AssertionError('Refinement worker did not finish')
        assert client(base, paired=False)('/api/command', dict(action='alignment-refine'))[0] == 401
        assert command()[0] == 409
        service.retry_at = float('inf'); service.demo = False
        a, b, _ = surfaces(2000)
        first, second = surface_session(frames.CAPTURES, 'a-scan', a), surface_session(frames.CAPTURES, 'b-scan', b)
        spec = dict(reference=first.name, moving=second.name,
                    versions=[alignment.version(first), alignment.version(second)], transform=np.eye(4).tolist())
        assert command(**{**spec, 'versions':['old', 'old']})[0] == 409
        assert command(**{**spec, 'max_distance_mm':99})[0] == 400
        assert command(**{**spec, 'moving':'../../bad-scan'})[0] == 400
        with patch.object(webapp.core, 'mesh_scope', return_value=[]):
            code, body, _ = command(**spec); assert code == 200
            state = finished(); result = state['alignment_refinement']
            assert result['job'] == json.loads(body)['job'] and not result['pending'] and not state['job_failed'], state
            assert result['result']['after_rmse_mm'] < .1
            plane = np.column_stack([np.tile(np.linspace(-.05, .05, 30), 30),
                np.repeat(np.linspace(-.05, .05, 30), 30), np.zeros(900)])
            third = surface_session(frames.CAPTURES, 'plane-scan', plane)
            fourth = surface_session(frames.CAPTURES, 'plane2-scan', plane + [0, 0, .001])
            bad = dict(reference=third.name, moving=fourth.name,
                       versions=[alignment.version(third), alignment.version(fourth)], transform=np.eye(4).tolist())
            assert command(**bad)[0] == 200
            state = finished()
            assert state['job_failed'] and 'ambiguous' in state['alignment_refinement']['error'], state
            assert 'result' not in state['alignment_refinement']


def test_refinement_refuses_capture_and_other_processing():
    service = object.__new__(webapp.Service)
    service.demo = False; service.processing = None
    service.controller = SimpleNamespace(scan_state='scanning')
    try: service.refine_alignment({})
    except webapp.Conflict: pass
    else: raise AssertionError('Refinement started during capture')
    service.controller = None; service.processing = object()
    try: service.refine_alignment({})
    except webapp.Conflict: pass
    else: raise AssertionError('Refinement overlapped another processing job')
