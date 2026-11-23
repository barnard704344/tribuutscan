"""Preview and remove small edge-connected surface components. Original points are untouched."""
import base64
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from . import view3d, mesh_exports

RESULT_PREFIX = 'ISLANDS_RESULT '


def settings(rule):
    if not isinstance(rule, dict) or type(rule.get('min_triangles')) is not int or not 1 <= rule['min_triangles'] <= 1000000:
        raise ValueError('Choose an island threshold from 1 to 1,000,000 triangles')
    return rule['min_triangles']


def analyse(directory, rule):
    import open3d as o3d
    threshold = settings(rule)
    path = Path(directory) / 'mesh.ply'
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    rec, triangles = view3d.read_ply(path, faces=True)
    if triangles is None or not len(triangles):
        raise ValueError('Generate a surface mesh before previewing islands')
    xyz = np.column_stack([rec[k] for k in ('x', 'y', 'z')])
    if not np.isfinite(xyz).all() or (triangles < 0).any() or (triangles >= len(rec)).any():
        raise ValueError('The surface contains invalid vertices or faces')
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(xyz), o3d.utility.Vector3iVector(triangles))
    labels, counts, _ = mesh.cluster_connected_triangles()
    labels, counts = np.asarray(labels), np.asarray(counts)
    selected = counts < threshold
    selected[int(np.argmax(counts))] = False
    removed = selected[labels]
    result = dict(mesh_hash=digest, min_triangles=threshold, triangles=len(triangles),
                  components=len(counts), removed_components=int(selected.sum()),
                  removed_triangles=int(removed.sum()), kept_triangles=int((~removed).sum()),
                  mask=base64.b64encode(np.packbits(removed, bitorder='little').tobytes()).decode())
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('The mesh changed during preview; preview again')
    return result, rec, triangles, removed


def apply(directory, rule):
    directory = Path(directory)
    result, rec, triangles, removed = analyse(directory, rule)
    if rule.get('mesh_hash') != result['mesh_hash']:
        raise ValueError('The mesh changed since preview. Preview again before removing islands')
    if not removed.any():
        raise ValueError('No islands are below this threshold; nothing was changed')
    kept = triangles[~removed]
    used, mapped = np.unique(kept, return_inverse=True)
    rec = rec[used]
    faces = np.zeros(len(kept), dtype=[('n', 'u1'), ('i', '<i4', (3,))])
    faces['n'], faces['i'] = 3, mapped.reshape(-1, 3)
    types = {'<f4':'float', '<f8':'double', '|u1':'uchar', '|i1':'char', '<u2':'ushort', '<i2':'short', '<u4':'uint', '<i4':'int'}
    header = ('ply\nformat binary_little_endian 1.0\n' + f'element vertex {len(rec)}\n' +
              ''.join(f'property {types[rec.dtype.fields[name][0].str]} {name}\n' for name in rec.dtype.names) +
              f'element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n')
    mesh_exports.retire(directory)
    (directory / 'mesh.ply').write_bytes(header.encode() + rec.tobytes() + faces.tobytes())
    report = {key: value for key, value in result.items() if key != 'mask'}
    (directory / 'mesh.json').write_text(json.dumps(dict(method='island-cleanup', islands=report), indent=2))
    session_file = directory / 'session.json'
    session = json.loads(session_file.read_text())
    session['mesh_cleanup'] = report
    session_file.write_text(json.dumps(session, indent=2))
    return f"Removed {result['removed_components']:,} mesh islands ({result['removed_triangles']:,} triangles); {result['kept_triangles']:,} triangles remain. Source points unchanged."


def main():
    try:
        spec = json.load(sys.stdin)
        result, *_ = analyse(spec['directory'], spec['rule'])
        if spec['mesh_version'] != view3d.mesh_version(Path(spec['directory']) / 'mesh.ply'):
            raise ValueError('The mesh changed; preview again')
        print(RESULT_PREFIX + json.dumps(result), flush=True)
        print(f"Preview ready: {result['removed_components']} islands, {result['removed_triangles']} triangles selected. No files changed.", flush=True)
    except Exception as error:
        print(str(error), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
