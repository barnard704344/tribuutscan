"""Create editable saved-session copies and transactional, disk-backed edit history."""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid

from . import alignment, cleanup, mesh_exports, view3d

MARKER = '.editing.json'
HISTORY = '.edit-history'
LIMIT = 6
EXTENSIONS = {'.ply', '.stl', '.json', '.npz', '.csv', '.txt', '.zip'}


def files(directory):
    result = []
    for p in Path(directory).iterdir():
        if p.name == MARKER or p.name.startswith('.'):
            continue
        if p.is_symlink():
            raise ValueError('Editable sessions cannot contain linked files')
        if p.is_file() and p.suffix in EXTENSIONS:
            result.append(p)
    return result


def metadata(directory):
    p = Path(directory) / MARKER
    return json.loads(p.read_text()) if p.exists() else None


def write_meta(directory, value):
    p = Path(directory) / MARKER
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(value, indent=2))
    tmp.replace(p)


def copy_files(source, destination, reserve=1):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    paths = files(source)
    size = sum(p.stat().st_size for p in paths)
    if shutil.disk_usage(destination).free < size * reserve + 256 * 1024**2:
        raise OSError('Not enough free disk space for a safe editing copy/history snapshot')
    for p in paths:
        shutil.copy2(p, destination / p.name)


def clone(source, output, expected):
    source, output = Path(source), Path(output)
    if output.exists() or output.parent.resolve() != source.parent.resolve():
        raise ValueError('Choose a new sibling output directory')
    if alignment.version(source) != expected:
        raise ValueError('The source changed; reload it before creating a copy')
    with tempfile.TemporaryDirectory(prefix='.edit-copy-', dir=output.parent) as tmp:
        stage = Path(tmp)
        copy_files(source, stage)
        session_path = stage / 'session.json'
        if session_path.exists():
            session = json.loads(session_path.read_text())
            changed = False
            for entry in session.get('sources', []):
                rec = os.path.relpath((source / entry['rec']).resolve(), output.resolve())
                changed |= rec != entry['rec']
                entry['rec'] = rec
            if changed:
                session_path.write_text(json.dumps(session, indent=2))
        write_meta(stage, dict(source=source.name, created=time.time(), revisions=[], cursor=-1))
        if alignment.version(source) != expected:
            raise ValueError('The source changed during copying')
        stage.rename(output)
    print('Editable copy created: ' + output.name, flush=True)


def publish(source, directory):
    """Stage every replacement before publishing, with cloud.ply last."""
    source, directory = Path(source), Path(directory)
    paths = files(source)
    staged = []
    try:
        for p in paths:
            temporary = directory / ('.publish-' + p.name)
            shutil.copy2(p, temporary)
            staged.append((temporary, directory / p.name))
        wanted = {p.name for p in paths}
        for p in files(directory):
            if p.name not in wanted:
                p.unlink()
        for temporary, destination in sorted(staged, key=lambda pair: pair[1].name == 'cloud.ply'):
            temporary.replace(destination)
    finally:
        for p, _ in staged:
            p.unlink(missing_ok=True)


def delete_region(directory, rule):
    """Filter the saved points directly; frame archives and poses need no rebuild."""
    import numpy as np
    path = Path(directory) / 'cloud.ply'
    policy = Path(directory) / 'cleanup.json'
    rules = json.loads(policy.read_text()) if policy.exists() else []
    if not isinstance(rules, list) or len(rules) >= 32:
        raise ValueError('This copy has 32 cleanup regions; start a new editing pass')
    rules = [cleanup.validate(saved) for saved in rules]
    rule = cleanup.validate(rule)
    rec = view3d.read_ply(path)
    xyz = np.column_stack([rec[key] for key in ('x', 'y', 'z')])
    keep = cleanup.keep(xyz * [1000, -1000, -1000], [rule])
    removed = int((~keep).sum())
    if not removed:
        raise ValueError('No saved points are inside this selection. Draw a new lasso and try again')
    # Preserve every saved point attribute, including normals and observation counts.
    payload = path.read_bytes()
    header = payload[:payload.index(b'end_header\n') + len(b'end_header\n')]
    if b'element face ' in header:
        raise ValueError('Lasso cleanup requires a point-cloud session')
    count = int(keep.sum())
    header = re.sub(rb'(?m)^element vertex \d+$', f'element vertex {count}'.encode(), header, count=1)
    path.write_bytes(header + rec[keep].tobytes())
    policy.write_text(json.dumps(rules + [rule]))
    session_file = Path(directory) / 'session.json'
    session = json.loads(session_file.read_text())
    session.update(points_kept=count, cleanup_regions=len(rules) + 1)
    session_file.write_text(json.dumps(session, indent=2))
    mesh_exports.retire(directory)
    return f'Removed {removed:,} points; {count:,} remain. Rebuild the mesh when ready.'


def crop_saved(directory, operation, box=None):
    """Re-filter the full saved voxel cloud, without restoring/remerging source frames."""
    import numpy as np
    from . import cloud, pointcloud
    directory = Path(directory)
    session_file = directory / 'session.json'
    session = json.loads(session_file.read_text())
    axes = (session.get('crop') or {}).get('axes', session.get('crop_axes'))
    if axes is None:
        axes = cloud.saved_crop_axes(directory, session).round(6).tolist()
    axes = np.asarray(axes, float)
    if axes.shape != (3, 3) or not np.isfinite(axes).all() or not np.allclose(axes @ axes.T, np.eye(3), atol=1e-4):
        raise ValueError('Invalid saved crop axes')
    crop = None
    if operation == 'crop':
        bounds = np.asarray([] if box is None else box, float)
        if bounds.shape != (6,) or not np.isfinite(bounds).all() or (bounds[:3] >= bounds[3:]).any():
            raise ValueError('Crop needs six finite bounds in millimetres, with low below high')
        crop = dict(axes=axes.tolist(), lo=bounds[:3].tolist(), hi=bounds[3:].tolist())
    scanner = cloud.Scanner(session.get('preset', 'small'), mode=session.get('mode', 'still'),
                            remove_base=session.get('remove_base', True), log=lambda _: None)
    try:
        state = cloud.load_npz(directory / 'cloud_state.npz')
        fused = cloud.VoxelCloud(float(state['point_mm']))
        for name in ('keys', 'xyz', 'rgb', 'n', 'nrm', 'seen'):
            setattr(fused, name, state[name])
        fused.frames, fused.raw_points = int(state['frames']), int(state['raw_points'])
        scanner.cloud = fused
        scanner.scene.load(json.loads((directory / 'scene.json').read_text()))
        scanner.crop = crop
        policy = directory / 'cleanup.json'
        rules = json.loads(policy.read_text()) if policy.exists() else []
        if not isinstance(rules, list) or len(rules) > 32:
            raise ValueError('Invalid saved lasso cleanup')
        scanner.cleanup = [cleanup.validate(rule) for rule in rules]
        scanner.poses = cloud.read_poses(directory / 'poses.txt')
        filtered = scanner.output_cloud()
        xyz, rgb, normals, seen, minimum = cloud.ply_arrays(filtered, scanner.poses,
                                                         min_seen=int(session.get('min_seen', cloud.MIN_SEEN)))
        pointcloud.write_ply(directory / 'cloud.ply', xyz, rgb, normals=normals, seen=seen)
        session.update(crop=crop, crop_axes=axes.tolist(), points_kept=len(xyz), min_seen=minimum,
                       cubes=len(filtered), cleanup_regions=len(scanner.cleanup))
        session_file.write_text(json.dumps(session, indent=2))
        mesh_exports.retire(directory)
        return f'{"cropped" if crop else "crop cleared"}: {len(xyz):,} points. Generate a new mesh when ready.'
    finally:
        scanner.discard()


def edit(directory, operation, rule=None, box=None):
    directory = Path(directory)
    note = metadata(directory)
    if not note:
        raise ValueError('Create an editable copy first')
    history = directory / HISTORY
    history.mkdir(exist_ok=True)
    revisions = note['revisions']
    cursor = note['cursor']
    if (not isinstance(revisions, list) or type(cursor) is not int or not -1 <= cursor < len(revisions)
            or any(not isinstance(r, str) or len(r) != 32 or any(c not in '0123456789abcdef' for c in r) for r in revisions)):
        raise ValueError('Invalid editing history')
    if operation in ('undo', 'redo'):
        target = cursor + (-1 if operation == 'undo' else 1)
        if not 0 <= target < len(revisions):
            raise ValueError('No edit to ' + operation)
        publish(history / revisions[target], directory)
        note['cursor'] = target
        write_meta(directory, note)
        print(operation.capitalize() + ' complete', flush=True)
        return
    if not revisions:
        initial = uuid.uuid4().hex
        copy_files(directory, history / initial)
        revisions = [initial]
        cursor = 0
        note.update(revisions=revisions, cursor=cursor)
        write_meta(directory, note)
    with tempfile.TemporaryDirectory(prefix='.edit-work-', dir=directory.parent) as tmp:
        stage = Path(tmp)
        copy_files(directory, stage, reserve=2)
        if operation.startswith('frame-'):
            from . import frame_editor
            message = frame_editor.edit(stage, operation, rule)
        elif operation == 'remove-islands':
            from . import islands
            message = islands.apply(stage, rule)
        elif operation == 'delete-region':
            message = delete_region(stage, rule)
        elif operation in ('crop', 'crop-off') and (stage / 'cloud_state.npz').is_file():
            message = crop_saved(stage, operation, box)
        else:
            if operation in ('optimise', 'small', 'medium', 'large') and (stage / 'frame-edits.json').exists():
                raise ValueError('Reset frame edits before changing spacing or optimising; meshing the retained frames is available')
            from . import cloud, optimise
            scanner = cloud.Scanner.restore(stage)
            try:
                if operation == 'reset-cleanup':
                    scanner.cleanup = []
                    scanner.save(stage)
                    message = 'Lasso cleanup cleared; rebuild the mesh when ready'
                else:
                    if operation == 'optimise' and scanner.cleanup:
                        raise ValueError('Clear or undo lasso cleanup before optimising poses, then clean up again')
                    if operation in ('crop', 'crop-off'):
                        mesh_exports.retire(stage)  # legacy restore still keeps meshing a separate action
                    message = optimise.process(scanner, stage, operation, box=box)
            finally:
                scanner.discard()
        revision = uuid.uuid4().hex
        stage.rename(history / revision)
    old_revisions = revisions[cursor+1:]
    revisions = revisions[:cursor+1] + [revision]
    if len(revisions) > LIMIT:
        old_revisions += revisions[:-LIMIT]
        revisions = revisions[-LIMIT:]
    try:
        publish(history / revision, directory)
    except Exception:
        publish(history / note['revisions'][note['cursor']], directory)
        shutil.rmtree(history / revision)
        raise
    note.update(revisions=revisions, cursor=len(revisions)-1)
    write_meta(directory, note)
    for obsolete in old_revisions:
        shutil.rmtree(history / obsolete)
    print(message, flush=True)


def main():
    spec = json.load(sys.stdin)
    if spec['operation'] == 'copy':
        clone(spec['source'], spec['output'], spec['version'])
    else:
        edit(spec['directory'], spec['operation'], spec.get('rule'), spec.get('box'))


if __name__ == '__main__':
    main()
