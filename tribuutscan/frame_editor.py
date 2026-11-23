"""Non-destructive accepted-frame selection and rigid section corrections."""
from contextlib import contextmanager
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

import numpy as np
from . import alignment, view3d

ARCHIVE = 'frame-source.zip'
POLICY = 'frame-edits.json'
BASE_FILES = ('session.json', 'keyframes.npz', 'merged.npz', 'frame_poses.txt', 'poses.txt',
              'track.csv', 'labels.npz', 'markers.json', 'scene.json')


def signature(directory):
    directory = Path(directory)
    entries = []
    for name in ('keyframes.npz', 'merged.npz', 'frame_poses.txt', 'poses.txt', POLICY, ARCHIVE):
        p = directory / name
        if p.is_symlink():
            raise ValueError('Frame editing does not accept linked session files')
        if p.exists():
            s = p.stat(); entries.append((name, s.st_size, s.st_mtime_ns))
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def read(directory, name):
    directory = Path(directory)
    if (directory / ARCHIVE).exists():
        try:
            with zipfile.ZipFile(directory / ARCHIVE) as archive:
                return archive.read(name) if name in archive.namelist() else b''
        except zipfile.BadZipFile as error:
            raise ValueError('The preserved frame source archive is damaged; restore a backup or use the original scan') from error
    path = directory / name
    return path.read_bytes() if path.exists() else b''


def poses(data):
    result = {}
    for line in data.decode().splitlines():
        values = line.split()
        if values:
            if len(values) != 17:raise ValueError('Invalid saved frame poses')
            result[int(values[0])] = alignment.rigid(np.array(values[1:], float).reshape(4, 4))
    return result


def policy(directory):
    path = Path(directory) / POLICY
    value = json.loads(path.read_text()) if path.exists() else []
    if not isinstance(value, list) or len(value) > 1000:raise ValueError('Invalid frame edit history')
    return value


def rule(value, operation):
    if not isinstance(value, dict):raise ValueError('Choose a frame range')
    a,b = value.get('first'),value.get('last')
    if type(a) is not int or type(b) is not int or not 0 <= a <= b <= 100000000:
        raise ValueError('Choose an inclusive frame range with non-negative integer indices')
    result = dict(first=a,last=b)
    if operation == 'frame-transform':result['transform'] = alignment.rigid(value.get('transform')).tolist()
    elif operation in ('frame-exclude','frame-include'):result['enabled'] = operation == 'frame-include'
    else:raise ValueError('Unknown frame edit')
    return result


def model(directory):
    base = poses(read(directory,'frame_poses.txt') or read(directory,'poses.txt'))
    merged = poses(read(directory,'poses.txt'))
    if not base or not merged or not (Path(directory)/'merged.npz').exists() or not (Path(directory)/'keyframes.npz').exists():
        raise ValueError('This scan lacks the saved poses and keyframe samples needed for frame editing')
    enabled = {i:True for i in base}; transforms = {i:np.eye(4) for i in base}
    for item in policy(directory):
        op = 'frame-transform' if 'transform' in item else 'frame-include' if item.get('enabled') else 'frame-exclude'
        item = rule(item,op)
        for i in base:
            if item['first'] <= i <= item['last']:
                if 'transform' in item:transforms[i] = np.array(item['transform']) @ transforms[i]
                else:enabled[i] = item['enabled']
    return base,merged,enabled,transforms


def catalogue(directory):
    base,merged,enabled,transforms = model(directory)
    rows = {i:dict(index=i,status='tracked') for i in base}
    data = read(directory,'track.csv').decode()
    for row in csv.DictReader(io.StringIO(data)):
        index = int(row['index']);entry = rows.setdefault(index,dict(index=index))
        entry['status'] = row.get('status','')
        for key in ('fitness','rmse_mm'):
            try:
                number = float(row[key]); entry[key] = number if np.isfinite(number) else None
            except (ValueError,KeyError):entry[key] = None
    for i,entry in rows.items():
        entry.update(has_pose=i in base, keyframe=i in merged, enabled=enabled.get(i,False))
        if i in base:
            flip = np.diag([1.,-1.,-1.,1.])
            p = transforms[i] @ flip @ base[i] @ flip
            entry['position'] = p[:3,3].tolist()
    session=json.loads((Path(directory)/'session.json').read_text())
    editable=session.get('mode')=='free' and not session.get('crop') and not (Path(directory)/'cleanup.json').exists()
    return dict(signature=signature(directory),editable=editable,reason='' if editable else 'Frame rebuilds require an uncropped Head / free object scan before lasso cleanup.',rows=sorted(rows.values(),key=lambda row:row['index']),
                operations=len(policy(directory)))


def preview(directory, first, last, part, expected):
    if expected != signature(directory):raise ValueError('The frames changed; reload the timeline')
    rule(dict(first=first,last=last),'frame-include')
    if part not in ('moving','reference'):raise ValueError('Invalid frame preview')
    base,merged,enabled,transforms = model(directory)
    with np.load(io.BytesIO(read(directory,'merged.npz')),allow_pickle=False) as data:
        indices,at,points = data['index'],data['at'],data['points']
        candidates = [k for k,i in enumerate(indices) if (first <= int(i) <= last if part=='moving' else not first <= int(i) <= last and enabled.get(int(i),False))]
        samples=[]
        budget=max(1, min(1000, 100000//max(1,len(candidates))))
        for k in candidates:
            i=int(indices[k]);xyz=points[at[k]:at[k+1]]
            xyz=xyz[::max(1,int(np.ceil(len(xyz)/budget)))].astype(float)/1000
            p=base[i];world=(xyz@p[:3,:3].T+p[:3,3])*[1,-1,-1]
            t=transforms[i];samples.append(world@t[:3,:3].T+t[:3,3])
    if expected != signature(directory):raise ValueError('The frames changed while loading; reload')
    xyz=np.concatenate(samples) if samples else np.empty((0,3))
    xyz=xyz[::max(1,int(np.ceil(len(xyz)/100000)))]
    return view3d.pack_arrays(xyz.astype(np.float32))


def archive_source(directory):
    directory=Path(directory)
    if (directory/ARCHIVE).exists():return
    paths=[directory/name for name in BASE_FILES if (directory/name).is_file()]
    size=sum(p.stat().st_size for p in paths)
    if shutil.disk_usage(directory).free < size*3+256*1024**2:
        raise OSError('Not enough disk space for frame source preservation and edit history')
    with zipfile.ZipFile(directory/ARCHIVE,'w',compression=zipfile.ZIP_STORED) as archive:
        for path in paths:archive.write(path,path.name)


@contextmanager
def baseline(directory):
    directory=Path(directory)
    with tempfile.TemporaryDirectory(prefix='.frame-rebuild-',dir=directory.parent) as tmp:
        path=Path(tmp)
        for name in BASE_FILES:
            data=read(directory,name)
            if data:(path/name).write_bytes(data)
        yield path


def edit(directory,operation,value):
    from . import cloud,mesh_exports
    directory=Path(directory)
    if not isinstance(value,dict) or value.get('signature') != signature(directory):
        raise ValueError('The frame selection changed; reload before rebuilding')
    session=json.loads((directory/'session.json').read_text())
    if session.get('mode') != 'free' or session.get('crop') or (directory/'cleanup.json').exists():
        raise ValueError('Edit frames on an uncropped Head / free object scan before lasso cleanup')
    base,merged,_,_=model(directory)
    if operation=='frame-reset':changes=[]
    else:
        change=rule(value,operation)
        if not any(change['first']<=i<=change['last'] for i in base):
            raise ValueError('This range has no accepted poses; untracked frames cannot be reconstructed here')
        changes=policy(directory)+[change]
        if len(changes)>1000:raise ValueError('Reset frame edits or start another editing copy')
    archive_source(directory)
    (directory/POLICY).write_text(json.dumps(changes,indent=2))
    base,merged,enabled,transforms=model(directory)
    if not any(enabled.get(i) for i in merged):raise ValueError('Keep at least one merged keyframe')
    with baseline(directory) as source:
        scanner=cloud.Scanner.restore(source,log=lambda *_:None)
        try:
            flip=np.diag([1.,-1.,-1.,1.])
            corrected={i:flip@transforms[i]@flip@pose for i,pose in base.items()}
            scanner.poses=[(i,corrected.get(i,p)) for i,p in scanner.poses if enabled.get(i,False)]
            scanner.keyframes=[(i,corrected.get(i,p),*rest) for i,p,*rest in scanner.keyframes if enabled.get(i,False)]
            scanner.track_log=[(i,'frame-edited',corrected[i]) for i in base if enabled[i]]
            scanner.labels={i:mask for i,mask in scanner.labels.items() if enabled.get(i,False)}
            scanner.pose=scanner.last_merged=scanner.poses[-1][1].copy()
            scanner.stats.update(merged=len(scanner.poses),retained_frames=sum(enabled.values()),frame_edits=len(changes),
                                 excluded_frames=sum(not v for v in enabled.values()))
            scanner.remerge({})
            if not len(scanner.output_cloud()):
                raise ValueError('The retained keyframes do not produce a visible scan; keep more overlapping views')
            mesh_exports.retire(directory)
            scanner.save(directory)
        finally:scanner.discard()
    if not changes:(directory/POLICY).unlink()
    return f"Rebuilt from {sum(enabled.values()):,} accepted frames ({len(scanner.poses):,} merged keyframes); {sum(not v for v in enabled.values()):,} excluded. Generate a mesh when ready."
