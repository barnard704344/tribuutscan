"""Host-local scan catalogue. Names and reversible trash never move capture folders."""
from datetime import datetime
import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import threading
import time
import uuid

import numpy as np

from . import view3d
from .image import write_png


def _json(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def default_title(name):
    try:
        date = datetime.strptime(name[:15], '%Y%m%d-%H%M%S')
    except ValueError:
        return name
    kind = 'Edited' if '-edit-' in name else 'Aligned' if '-aligned-' in name else 'Scan'
    return kind + ' · ' + date.strftime('%d %b %Y · %H:%M')


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.file = self.root / '.scan-library.json'
        self.lock = threading.RLock()
        self.thumb_lock = threading.Lock()
        self.records = {}
        self.stamp = None

    def metadata(self):
        with self.lock:
            stamp = self.file.stat().st_mtime_ns if self.file.exists() else None
            if stamp != self.stamp:
                try:
                    data = _json(self.file, {})
                    if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
                        raise ValueError()
                except (ValueError, OSError) as e:
                    raise ValueError('Scan Library metadata could not be read; existing files have been preserved') from e
                self.records, self.stamp = data, stamp
            return {key: dict(value) for key, value in self.records.items()}

    def trashed_names(self):
        return {name for name, record in self.metadata().items() if record.get('trashed_at')}

    def labels(self, names):
        records = self.metadata()
        return {name: records.get(name, {}).get('display_name') or default_title(name) for name in names}

    def directory(self, name, include_trash=False):
        if not isinstance(name, str) or '\0' in name or Path(name).name != name or not name.endswith('-scan'):
            raise ValueError('Choose a saved scan from the library')
        root = self.root.resolve()
        path = root / name
        if path.is_symlink() or (path / 'cloud.ply').is_symlink() or path.resolve().parent != root or not (path / 'cloud.ply').is_file():
            raise ValueError('Saved scan not found')
        if not include_trash and name in self.trashed_names():
            raise ValueError('This scan is in Bin. Restore it from the Scan Library first')
        return path

    def update(self, name, operation, expected_revision, title=None):
        self.directory(name, include_trash=True)
        with self.lock:
            records = self.metadata()
            record = records.get(name, {})
            if type(expected_revision) is not int or expected_revision != record.get('revision', 0):
                raise ValueError('This library entry changed in another browser. Refresh and try again')
            if operation == 'rename':
                if not isinstance(title, str):
                    raise ValueError('Enter a display name')
                title = title.strip()
                if not 1 <= len(title) <= 100 or any(ord(c) < 32 or ord(c) == 127 for c in title):
                    raise ValueError('Use a display name of 1–100 characters without control characters')
                record['display_name'] = title
            elif operation == 'trash':
                record['trashed_at'] = time.time()
            elif operation == 'restore':
                record.pop('trashed_at', None)
            else:
                raise ValueError('Unknown library operation')
            record['revision'] = record.get('revision', 0) + 1
            records[name] = record
            temporary = self.file.with_name('.scan-library-' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps(records, indent=2, ensure_ascii=False))
                temporary.replace(self.file)
            finally:
                temporary.unlink(missing_ok=True)
            self.records, self.stamp = records, self.file.stat().st_mtime_ns
            return dict(name=name, **record)

    def bin_plan(self):
        """Protect every retained scan's provenance and raw links, including undo states."""
        with self.lock:
            items = self.catalogue()
            candidates = {item['id'] for item in items if item['trashed_at']}
            dependencies, fingerprints = {}, []
            directories = [p for p in self.root.glob('*-scan') if p.is_dir() and not p.is_symlink()]
            for directory in directories:
                refs = set()
                try:
                    for filename in ['.editing.json', 'alignment.json']:
                        data = _json(directory / filename, {})
                        if not isinstance(data, dict):
                            raise ValueError()
                        refs.update(value for key, value in data.items()
                                    if key in ('source', 'reference', 'moving') and isinstance(value, str))
                    sessions = [directory / 'session.json', *directory.glob('.edit-history/*/session.json')]
                    for session in sessions:
                        data = _json(session, {})
                        for source in data.get('sources', []):
                            rec = (directory / source['rec']).resolve()
                            refs.update(name for name in candidates
                                        if rec == self.root.resolve() / name or self.root.resolve() / name in rec.parents)
                    for base, dirs, files in os.walk(directory, followlinks=False):
                        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
                        for filename in sorted(files):
                            path = Path(base) / filename
                            st = path.lstat()
                            fingerprints.append((str(path.relative_to(self.root)), st.st_size, st.st_mtime_ns))
                except (ValueError, OSError, KeyError, TypeError, AttributeError):
                    # Unknown metadata must not allow deletion of a possible dependency.
                    refs.update(candidates)
                dependencies[directory.name] = refs
            retained = set(dependencies) - candidates
            protected = {}
            while True:
                found = {name: owner for owner in sorted(retained) for name in dependencies.get(owner, set())
                         if name in candidates and name not in retained}
                if not found:
                    break
                protected.update(found)
                retained.update(found)
            deletable = sorted(candidates - retained)
            snapshot = json.dumps([self.metadata(), fingerprints, deletable, protected], sort_keys=True).encode()
            return dict(signature=hashlib.sha256(snapshot).hexdigest(),
                        deletable=deletable, protected=[dict(id=name, used_by=owner) for name, owner in sorted(protected.items())],
                        bytes=sum(item['bytes'] for item in items if item['id'] in deletable))

    def empty_bin(self, signature):
        with self.lock:
            plan = self.bin_plan()
            if not isinstance(signature, str) or signature != plan['signature']:
                raise ValueError('The library changed. Review Empty Bin again before deleting files')
            deleted, failed = [], []
            for name in plan['deletable']:
                path = self.directory(name, include_trash=True)
                try:
                    shutil.rmtree(path)
                    deleted.append(name)
                except OSError:
                    failed.append(name)
            # Remove only successfully deleted entries. Shared/external recordings stay put.
            records = self.metadata()
            for name in deleted:
                records.pop(name, None)
            temporary = self.file.with_name('.scan-library-' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps(records, indent=2, ensure_ascii=False))
                temporary.replace(self.file)
            finally:
                temporary.unlink(missing_ok=True)
            self.records, self.stamp = records, self.file.stat().st_mtime_ns
            return dict(deleted=deleted, failed=failed, protected=plan['protected'])

    @staticmethod
    def storage(directory):
        sizes = dict(geometry=0, meshes=0, history=0, recordings=0)
        for base, dirs, files in os.walk(directory, followlinks=False):
            base = Path(base)
            dirs[:] = [d for d in dirs if not (base / d).is_symlink()]
            relative = base.relative_to(directory).parts
            group = 'history' if '.edit-history' in relative else 'meshes' if '.mesh-history' in relative else (
                'recordings' if any(part.endswith('-rec') for part in relative) else 'geometry')
            for name in files:
                path = base / name
                if path.is_symlink():
                    continue
                try:
                    category = 'meshes' if not relative and name in ('mesh.ply', 'mesh.stl', 'solid.ply', 'mesh.json') else group
                    sizes[category] += path.stat().st_size
                except FileNotFoundError:
                    pass  # another job may publish a replacement during this read-only listing
        return sizes

    def catalogue(self):
        records = self.metadata()
        items = []
        for path in sorted(self.root.glob('*-scan'), reverse=True):
            if path.is_symlink() or (path / 'cloud.ply').is_symlink() or not (path / 'cloud.ply').is_file():
                continue
            record = records.get(path.name, {})
            try:
                session = _json(path / 'session.json', {})
                editing = _json(path / '.editing.json', {})
                aligned = _json(path / 'alignment.json', {})
                if not all(isinstance(value, dict) for value in (session, editing, aligned)):
                    raise ValueError('Invalid saved session metadata')
                parents = [editing['source']] if editing.get('source') else [aligned.get('reference'), aligned.get('moving')]
                parents = list(dict.fromkeys(p for p in parents if isinstance(p, str) and p != path.name))
                size = self.storage(path)
                st = (path / 'cloud.ply').stat()
                try:
                    created = datetime.strptime(path.name[:15], '%Y%m%d-%H%M%S').timestamp()
                except ValueError:
                    created = st.st_mtime
                exports = {key: (path / filename).is_file() for key, filename in (
                    ('mesh', 'mesh.ply'), ('stl', 'mesh.stl'), ('solid', 'solid.ply'))}
                recordings = []
                for source in session.get('sources', []):
                    rec = (path / source['rec']).resolve()
                    recordings.append(dict(name=rec.name, available=rec.is_dir(), external=self.root.resolve() not in rec.parents))
                items.append(dict(id=path.name, display_name=record.get('display_name') or default_title(path.name),
                    revision=record.get('revision', 0), trashed_at=record.get('trashed_at'), created=created,
                    kind='edited' if editing else 'aligned' if aligned else 'capture', parents=parents,
                    points=session.get('points_kept') if type(session.get('points_kept')) is int else None, storage=size, bytes=sum(size.values()), recordings=recordings,
                    exports=exports, closure=view3d.stl_state(path) if exports['stl'] else None,
                    thumbnail_version=f'{st.st_mtime_ns}-{st.st_size}', subject=session.get('mode'),
                    crop=bool(session.get('crop')), cleanup_regions=session.get('cleanup_regions', 0)))
            except (ValueError, OSError, KeyError, TypeError):
                # A scan being saved, or an old incomplete session, must not hide the rest of the library.
                items.append(dict(id=path.name, display_name=record.get('display_name') or default_title(path.name),
                    revision=record.get('revision', 0), trashed_at=record.get('trashed_at'), created=path.stat().st_mtime,
                    kind='capture', parents=[], points=None, storage={}, bytes=0, recordings=[], exports={},
                    thumbnail_version='pending', warning='Saved data is incomplete or being updated'))
        ids = {item['id'] for item in items}
        groups = {name: name for name in ids}
        def find(name):
            while groups[name] != name:
                groups[name] = groups[groups[name]]
                name = groups[name]
            return name
        for item in items:
            for parent in item['parents']:
                if parent in ids:
                    a, b = find(item['id']), find(parent)
                    groups[max(a, b)] = min(a, b)
        for item in items:
            item['family'] = find(item['id'])
            item['dependents'] = [other['id'] for other in items if item['id'] in other['parents']]
        return items

    def thumbnail(self, name):
        path = self.directory(name, include_trash=True) / 'cloud.ply'
        stat = path.stat()
        key = hashlib.sha256(f'v1:{name}:{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()
        cache = self.root / '.library-thumbnails'
        target = cache / (key + '.png')
        with self.thumb_lock:
            if target.exists():
                return target.read_bytes()
            # Map vertex records and sample them without loading a multi-million-point file into RAM.
            with path.open('rb') as source:
                header = []
                for _ in range(200):
                    line = source.readline(1024)
                    header.append(line.decode().strip())
                    if line == b'end_header\n':
                        break
                else:
                    raise ValueError('Unsupported point-cloud header')
                offset = source.tell()
            if 'format binary_little_endian 1.0' not in header:
                raise ValueError('Thumbnail needs a binary point cloud')
            count, fields, vertex = None, [], False
            for line in header:
                words = line.split()
                if words[:1] == ['element']:
                    vertex = words[1] == 'vertex'
                    if vertex:
                        count = int(words[2])
                elif vertex and words[:1] == ['property']:
                    if len(words) != 3 or words[1] not in view3d.PLY_TYPES:
                        raise ValueError('Unsupported point-cloud properties')
                    fields.append((words[2], view3d.PLY_TYPES[words[1]]))
            image = np.full((240, 400, 3), [7, 14, 23], np.uint8)
            dtype = np.dtype(fields)
            if count is None or count < 0 or offset + count * dtype.itemsize > stat.st_size:
                raise ValueError('Incomplete point cloud')
            if count:
                mapped = np.memmap(path, dtype=dtype, mode='r', offset=offset, shape=(count,))
                sampled = np.array(mapped[::max(1, int(np.ceil(count / 30000)))])
                del mapped
                xyz = np.column_stack([sampled[k] for k in ('x', 'y', 'z')]).astype(float)
                valid = np.isfinite(xyz).all(1)
                xyz, sampled = xyz[valid], sampled[valid]
                if len(xyz):
                    a, b = .22, -.08
                    rotate = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
                    xyz = xyz @ rotate.T
                    rotate = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
                    xyz = xyz @ rotate.T
                    lo, hi = np.percentile(xyz[:, :2], [.5, 99.5], axis=0)
                    span = np.maximum(hi - lo, 1e-5)
                    pixels = np.rint((xyz[:, :2] - (lo + hi) / 2) * [1, -1] * min(360/span[0], 210/span[1]) + [200, 120]).astype(int)
                    inside = (pixels[:, 0] >= 1) & (pixels[:, 0] < 399) & (pixels[:, 1] >= 1) & (pixels[:, 1] < 239)
                    order = np.argsort(xyz[inside, 2])
                    xy = pixels[inside][order]
                    rgb = (np.column_stack([sampled[k] for k in ('red', 'green', 'blue')]) if 'red' in sampled.dtype.names
                           else np.full((len(sampled), 3), [115, 219, 176]))[inside][order]
                    for dx, dy in ((-1, 0), (0, -1), (1, 0), (0, 1), (0, 0)):
                        image[xy[:, 1]+dy, xy[:, 0]+dx] = rgb
            out = io.BytesIO()
            write_png(out, image)
            if (path.stat().st_mtime_ns, path.stat().st_size) != (stat.st_mtime_ns, stat.st_size):
                raise ValueError('Scan changed while creating its thumbnail; retry shortly')
            cache.mkdir(exist_ok=True)
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(out.getvalue())
            temporary.replace(target)
            return out.getvalue()
