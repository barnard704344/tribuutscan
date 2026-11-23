"""Accepted-frame filtering, rigid section corrections, source preservation and undo."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
from tribuutscan import alignment,cloud,edit_session,frame_editor,fusion,view3d
from test_alignment import make_session,hashes,rejected


def session(root):
    source=make_session(root,'source-scan')
    scanner=cloud.Scanner.restore(source,log=lambda *_:None)
    try:
        for i in range(3,6):
            p=np.eye(4);p[0,3]=.01
            scanner.poses.append((i,p));scanner.keyframes.append((i,p,*scanner.keyframes[i-3][2:]));scanner.merged_data[i]=scanner.merged_data[i-3]
            scanner.track_log.append((i,'tracked',p));scanner.labels[i]=scanner.labels[i-3]
        scanner.track_log.append((6,'lost',None));scanner.stats.update(frames=7,merged=6)
        scanner.remerge({});scanner.save(source)
    finally:scanner.discard()
    return source


def mutate(path,operation,first=0,last=0,**extra):
    value=dict(first=first,last=last,signature=frame_editor.signature(path),**extra)
    edit_session.edit(path,operation,value)


def test_frame_exclusion_reinclude_undo_and_future_meshing_use_only_retained_poses():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=session(root);before=hashes(source);out=root/'edited-scan'
        edit_session.clone(source,out,alignment.version(source))
        catalogue=frame_editor.catalogue(out);assert len(catalogue['rows'])==7
        assert catalogue['rows'][-1]['has_pose'] is False and catalogue['editable']
        mutate(out,'frame-exclude',4,5)
        rows=frame_editor.catalogue(out)['rows'];assert [r['index'] for r in rows if r['enabled']]==[0,1,2,3]
        scanner=cloud.Scanner.restore(out,log=lambda *_:None)
        try:
            assert [i for i,_ in scanner.poses]==[0,1,2,3]
            assert [i for i,_ in scanner.frame_poses()]==[0,1,2,3]
            with patch.object(fusion.fr,'read_recording',return_value=iter([{} for _ in range(7)])):
                assert len(list(fusion.scan_frames(scanner,'all')))==4
        finally:scanner.discard()
        edit_session.edit(out,'undo');assert len([r for r in frame_editor.catalogue(out)['rows'] if r['enabled']])==6
        edit_session.edit(out,'redo');assert not frame_editor.catalogue(out)['rows'][4]['enabled']
        mutate(out,'frame-include',4,5);assert all(row['enabled'] for row in frame_editor.catalogue(out)['rows'][:6])
        assert hashes(source)==before
        current=hashes(out)
        rejected(lambda:mutate(out,'frame-exclude',0,5));assert hashes(out)==current
        rejected(lambda:mutate(out,'frame-include',6,6))
        rejected(lambda:edit_session.edit(out,'frame-exclude',dict(first=0,last=1,signature='stale')))


def test_rigid_section_transform_moves_selected_poses_and_preview_only_then_reset_restores():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=session(root);out=root/'edited-scan';edit_session.clone(source,out,alignment.version(source))
        sig=frame_editor.signature(out);original=hashes(out)
        buf=frame_editor.preview(out,3,5,'moving',sig);assert np.frombuffer(buf[:4],'<u4')[0]>0
        assert hashes(out)==original
        transform=np.eye(4);transform[0,3]=-.01
        mutate(out,'frame-transform',3,5,transform=transform.tolist())
        poses=dict(cloud.read_poses(out/'frame_poses.txt'))
        assert np.allclose([poses[i][0,3] for i in range(6)],0)
        assert not (out/'mesh.ply').exists()
        mutate(out,'frame-reset')
        poses=dict(cloud.read_poses(out/'frame_poses.txt'));assert np.allclose([poses[i][0,3] for i in range(3,6)],.01)
        assert not (out/frame_editor.POLICY).exists()
        rejected(lambda:frame_editor.preview(out,0,2,'moving',sig))
        reflection=np.eye(4);reflection[0,0]=-1
        rejected(lambda:mutate(out,'frame-transform',3,5,transform=reflection.tolist()))


def test_clone_rebases_nested_recording_links_and_frame_archive_keeps_them_resolvable():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=session(root);rec=source/'nested-rec';rec.mkdir()
        session_json=source/'session.json';data=json.loads(session_json.read_text());data['sources'][0]['rec']='nested-rec';session_json.write_text(json.dumps(data))
        out=root/'edited-scan';edit_session.clone(source,out,alignment.version(source))
        data=json.loads((out/'session.json').read_text());assert (out/data['sources'][0]['rec']).resolve()==rec
        mutate(out,'frame-exclude',5,5)
        data=json.loads((out/'session.json').read_text());assert (out/data['sources'][0]['rec']).resolve()==rec
        assert rec.exists() and source.exists()


def test_frame_api_pairing_preview_staleness_copy_and_busy_guards():
    import time
    from types import SimpleNamespace
    from urllib.parse import urlencode
    from test_webapp import running_service,client
    with running_service() as (service,base):
        from tribuutscan import frames
        source=session(frames.CAPTURES);before=hashes(source);request=client(base);guest=client(base,paired=False)
        assert guest('/frames')[0]==200  # redirected to pairing
        for path in ['/api/frames/catalogue?scan=source-scan','/api/frames/preview?scan=source-scan']:
            assert guest(path)[0]==401
        service.demo=False;service.retry_at=float('inf')
        def command(action,**data):
            state=service.state();return request('/api/command',dict(action=action,revision=state['revision'],instance=state['instance'],**data))
        assert command('open',name=source.name)[0]==200
        cat=json.loads(request('/api/frames/catalogue?scan=source-scan')[1]);assert len(cat['rows'])==7
        preview='/api/frames/preview?'+urlencode(dict(scan=source.name,first=3,last=5,part='moving',signature=cat['signature']))
        assert request(preview)[0]==200
        assert request(preview.replace(cat['signature'],'stale'))[0]==400
        assert request('/api/frames/catalogue?scan=../source-scan')[0]==400
        info=json.loads(request('/model/info.json')[1])
        values=dict(name=source.name,model_version=info['version'],operation='frame-exclude',rule=dict(first=3,last=5,signature=cat['signature']))
        assert command('edit-operation',**values)[0]==409
        status,body,_=command('edit-copy',name=source.name,model_version=info['version']);assert status==200
        name=json.loads(body)['output']
        for _ in range(150):
            state=service.state()
            if not state['processing'] and state['selected']==name:break
            time.sleep(.1)
        info=json.loads(request('/model/info.json')[1]);values.update(name=name,model_version=info['version'])
        with patch.object(service,'processing',SimpleNamespace(poll=lambda:None)):
            assert command('edit-operation',**values)[0]==409
        assert command('edit-operation',**dict(values,rule=dict(first=3,last=5,signature='stale')))[0]==409
        assert command('edit-operation',**values)[0]==200
        for _ in range(150):
            state=service.state()
            if not state['processing']:break
            time.sleep(.1)
        assert not state['job_failed'],state.get('job_result')
        cat=json.loads(request('/api/frames/catalogue?scan='+name)[1]);assert sum(row['enabled'] for row in cat['rows'])==3
        assert hashes(source)==before


def test_section_rotation_uses_viewer_axes_and_does_not_move_reference_poses():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=session(root);out=root/'edited-scan';edit_session.clone(source,out,alignment.version(source))
        angle=np.radians(10);m=np.eye(4);m[:3,:3]=[[1,0,0],[0,np.cos(angle),-np.sin(angle)],[0,np.sin(angle),np.cos(angle)]]
        old=dict(cloud.read_poses(out/'frame_poses.txt'));mutate(out,'frame-transform',3,5,transform=m.tolist())
        corrected=dict(cloud.read_poses(out/'frame_poses.txt'));flip=np.diag([1.,-1.,-1.,1.])
        for i in range(6):assert np.allclose(corrected[i],old[i] if i<3 else flip@m@flip@old[i])


def test_unsupported_cleanup_and_damaged_baseline_are_rejected_without_editing_sources():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=session(root);out=root/'edited-scan';edit_session.clone(source,out,alignment.version(source))
        info=json.loads((out/'session.json').read_text());info['crop']={'lo':[0,0,0],'hi':[1,1,1]};(out/'session.json').write_text(json.dumps(info))
        assert not frame_editor.catalogue(out)['editable']
        rejected(lambda:mutate(out,'frame-exclude',5,5))
        info['crop']=None;(out/'session.json').write_text(json.dumps(info))
        mutate(out,'frame-exclude',5,5)
        (out/frame_editor.ARCHIVE).write_bytes(b'broken archive')
        rejected(lambda:frame_editor.catalogue(out))


def test_saved_six_decimal_rotations_accept_rounding_without_loosening_manual_transforms():
    angle=.61;axis=np.array([1.,2.,3.]);axis/=np.linalg.norm(axis)
    skew=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
    m=np.eye(4);m[:3,:3]=np.eye(3)+np.sin(angle)*skew+(1-np.cos(angle))*(skew@skew)
    rounded=np.round(m,6)
    data=('12 '+' '.join(f'{v:.6f}' for v in rounded.ravel())+'\n').encode()
    parsed=frame_editor.poses(data)[12]
    assert np.array_equal(parsed,rounded)
    rejected(lambda:alignment.rigid(rounded))  # the previous strict reader rejected this valid saved rotation
    for bad in [np.diag([1.,1.,1.001,1.]),np.diag([-1.,1.,1.,1.])]:
        encoded=('12 '+' '.join(map(str,bad.ravel()))).encode()
        rejected(lambda:frame_editor.poses(encoded))
        rejected(lambda:frame_editor.rule(dict(first=0,last=1,transform=bad.tolist()),'frame-transform'))
