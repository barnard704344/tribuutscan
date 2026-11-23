"""Library identities, lineage, thumbnails, reversible trash and authenticated boundaries."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tribuutscan import alignment, controller, edit_session, frames, scan_library, view3d, webapp
from test_alignment import hashes, make_session, rejected


def test_library_rename_and_trash_preserve_sources_history_and_recordings():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=make_session(root,'source-scan');copy=root/'edited-scan'
        edit_session.clone(source,copy,alignment.version(source))
        nested=source/'inside-rec';nested.mkdir();(nested/'frames.zlib').write_bytes(b'kept recording')
        for directory,reference in [(source,'inside-rec'),(copy,'../source-scan/inside-rec')]:
            data=json.loads((directory/'session.json').read_text());data['sources'][0]['rec']=reference
            (directory/'session.json').write_text(json.dumps(data))
        before=[hashes(source),hashes(copy)]
        lib=scan_library.Library(root)
        first={item['id']:item for item in lib.catalogue()}
        assert first['source-scan']['family']==first['edited-scan']['family']
        assert first['edited-scan']['parents']==['source-scan'] and first['edited-scan']['kind']=='edited'
        assert first['source-scan']['dependents']==['edited-scan']
        assert first['source-scan']['bytes']>0 and first['edited-scan']['recordings'][0]['available']
        lib.update('source-scan','rename',0,'  A head scan  ')
        assert lib.labels(['source-scan'])['source-scan']=='A head scan'
        rejected(lambda:lib.update('source-scan','rename',0,'stale'))
        lib.update('source-scan','trash',1)
        assert source.exists() and lib.trashed_names()=={'source-scan'}
        rejected(lambda:lib.directory('source-scan'))
        assert lib.directory('source-scan',include_trash=True)==source
        assert lib.directory('edited-scan')==copy
        session=json.loads((copy/'session.json').read_text())
        assert all((copy/s['rec']).resolve().is_dir() for s in session['sources'])
        assert [hashes(source),hashes(copy)]==before
        lib.update('source-scan','restore',2)
        assert lib.directory('source-scan')==source and not lib.trashed_names()
        assert scan_library.Library(root).labels(['source-scan'])['source-scan']=='A head scan'
        for value in ['', ' '*10, 'a'*101, 'bad\nname', None]:
            rejected(lambda:lib.update('source-scan','rename',3,value))
        assert lib.labels(['source-scan'])['source-scan']=='A head scan'


def test_catalogue_groups_alignment_and_edited_descendants_including_trash():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        a=make_session(root,'a-scan');b=make_session(root,'b-scan');joined=make_session(root,'joined-scan')
        (joined/'alignment.json').write_text(json.dumps(dict(reference=a.name,moving=b.name)))
        edited=root/'edited-scan';edit_session.clone(joined,edited,alignment.version(joined))
        lib=scan_library.Library(root);lib.update(a.name,'trash',0)
        items={item['id']:item for item in lib.catalogue()}
        assert len({item['family'] for item in items.values()})==1
        assert items[joined.name]['kind']=='aligned' and items[edited.name]['parents']==[joined.name]
        assert items[a.name]['trashed_at'] and not items[edited.name]['trashed_at']
        (b/'session.json').write_text('invalid partial save')
        assert len(lib.catalogue())==4
        assert next(i for i in lib.catalogue() if i['id']==b.name)['warning']


def test_library_thumbnails_are_cached_bounded_and_path_safe():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=make_session(root,'source-scan');lib=scan_library.Library(root)
        original=hashes(source);png=lib.thumbnail(source.name)
        assert png[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',png[16:24])==(400,240)
        assert lib.thumbnail(source.name)==png and hashes(source)==original
        assert len(list((root/'.library-thumbnails').glob('*.png')))==1
        for name in ['../source-scan','/tmp/source-scan','missing-scan',None]:
            rejected(lambda:lib.directory(name))
        linked=root/'linked-scan';linked.symlink_to(source,target_is_directory=True)
        rejected(lambda:lib.directory(linked.name))
        assert not any(item['id']==linked.name for item in lib.catalogue())
        lib.update(source.name,'trash',0)
        assert lib.thumbnail(source.name)==png  # paired Trash view can still preview a retained file


def test_continuation_skips_trashed_captures_and_editing_copies():
    with tempfile.TemporaryDirectory() as tmp,patch.object(frames,'CAPTURES',Path(tmp)):
        root=Path(tmp);a=make_session(root,'a-scan');b=make_session(root,'b-scan')
        edit_session.clone(a,root/'z-edit-scan',alignment.version(a))
        scan_library.Library(root).update(b.name,'trash',0)
        app=SimpleNamespace(scan_state='idle',toggle_scan=Mock(),log=Mock())
        controller.Controller.continue_scan(app)
        app.toggle_scan.assert_called_once_with(continue_from=a)


def test_library_api_pairing_freshness_busy_guard_and_open_restored_scan():
    from test_webapp import running_service,client
    with running_service() as (service,base):
        guest=client(base,paired=False)
        for path in ['/api/library','/api/library/thumbnail?scan=source-scan']:assert guest(path)[0]==401
        assert b'id="pair-form"' in guest('/library')[1]
        request=client(base)
        for path in ['/library','/library.js','/library.css']:assert request(path)[0]==200
        source=make_session(frames.CAPTURES,'source-scan');before=hashes(source)
        service.retry_at=float('inf');service.demo=False
        def command(action,**data):
            state=service.state()
            return request('/api/command',dict(action=action,instance=state['instance'],revision=state['revision'],**data))
        assert command('library-rename',name=source.name,entry_revision=0,display_name='Test scan')[0]==200
        items=json.loads(request('/api/library')[1])['items'];assert items[0]['display_name']=='Test scan'
        assert command('library-trash',name=source.name,entry_revision=0)[0]==400
        assert command('open',name=source.name)[0]==200
        assert json.loads(request('/model/info.json')[1])['display_name']=='Test scan'
        assert command('library-trash',name=source.name,entry_revision=1)[0]==200
        assert service.model.file is None and source.exists()
        assert source.name not in service.state()['scans']
        assert command('open',name=source.name)[0]==400
        assert request('/api/library/thumbnail?scan=../../outside-scan')[0]==400
        assert command('library-restore',name=source.name,entry_revision=2)[0]==200
        assert command('open',name=source.name)[0]==200
        assert hashes(source)==before
        service.processing=SimpleNamespace(poll=lambda:None)
        try:assert command('library-trash',name=source.name,entry_revision=3)[0]==409
        finally:service.processing=None


def test_corrupt_library_metadata_is_not_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);make_session(root,'source-scan')
        metadata=root/'.scan-library.json';metadata.write_text('{invalid')
        lib=scan_library.Library(root)
        rejected(lambda:lib.update('source-scan','rename',0,'New title'))
        assert metadata.read_text()=='{invalid'
