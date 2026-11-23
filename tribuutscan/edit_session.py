"""Create editable saved-session copies and transactional, disk-backed edit history."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

from . import alignment, cleanup

MARKER = '.editing.json'
HISTORY = '.edit-history'
LIMIT = 6
EXTENSIONS = {'.ply', '.stl', '.json', '.npz', '.csv', '.txt'}


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


def edit(directory, operation, rule=None, box=None):
    from . import cloud, optimise
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
        scanner = cloud.Scanner.restore(stage)
        try:
            if operation == 'delete-region':
                if len(scanner.cleanup) >= 32:
                    raise ValueError('This copy has 32 cleanup regions; start a new editing pass')
                scanner.cleanup.append(cleanup.validate(rule))
                _, count = scanner.save(stage)
                message = f'Cleanup saved: {count:,} points remain. Rebuild the mesh when ready.'
            elif operation == 'reset-cleanup':
                scanner.cleanup = []
                scanner.save(stage)
                message = 'Lasso cleanup cleared; rebuild the mesh when ready'
            else:
                if operation == 'optimise' and scanner.cleanup:
                    raise ValueError('Clear or undo lasso cleanup before optimising poses, then clean up again')
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
