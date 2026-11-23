"""Alignment geometry, full-session preservation and authenticated API boundaries."""
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
from tribuutscan import alignment, view3d


def rejected(call):
    try:call()
    except ValueError:return
    raise AssertionError('Invalid alignment accepted')


def test_landmarks_recover_rotation_translation_without_scale():
    moving=np.array([[0,0,0],[.03,0,0],[0,.04,0],[0,0,.05]])
    expected=np.array([[0,-1,0,.02],[1,0,0,-.01],[0,0,1,.03],[0,0,0,1.]])
    reference=moving@expected[:3,:3].T+expected[:3,3]
    result=alignment.fit(reference,moving)
    assert np.allclose(result['transform'],expected)
    assert result['landmark_rmse_mm']<1e-10
    for a,b in [([[0,0,0]]*3,[[0,0,0]]*3),([[0,0,0],[.01,0,0],[.02,0,0]],)*2,
                (reference[:2],moving[:2]),(reference,moving[:3]),(reference*float('nan'),moving)]:
        rejected(lambda:alignment.fit(a,b))


def test_transforms_reject_scale_reflection_nan_and_translation_bounds():
    for change in [(0,0,2),(0,0,-1),(3,0,.1),(1,3,float('nan')),(1,3,11)]:
        m=np.eye(4);m[change[0],change[1]]=change[2]
        rejected(lambda:alignment.rigid(m))


def make_session(root,name,shift=0):
    from tribuutscan import cloud
    path=root/name
    rec=root/(name+'-rec');rec.mkdir()
    scanner=cloud.Scanner(mode='free',log=lambda *_:None)
    xyz=np.array(np.meshgrid(np.arange(6)*3,np.arange(7)*3,np.arange(5)*3)).reshape(3,-1).T.astype(np.float32)
    xyz[:,1]+=shift;xyz[:,2]+=250
    rgb=np.full(xyz.shape,150,np.uint8);normals=np.tile([0,0,-1.],(len(xyz),1)).astype(np.float32)
    try:
        for i in range(3):
            p=np.eye(4)
            scanner.poses.append((i,p));scanner.keyframes.append((i,p,xyz,None,np.empty((0,3))))
            scanner.merged_data[i]=(xyz,rgb,normals)
            scanner.track_log.append((i,'tracked',p));scanner.labels[i]=np.zeros((2,2),np.uint8)
        scanner.sources=[dict(rec=str(rec),first_index=0)]
        scanner.stats.update(frames=3,merged=3)
        scanner.remerge({});scanner.save(path)
    finally:scanner.discard()
    return path


def hashes(path):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir() if p.is_file()}


def test_saved_alignment_preserves_sources_frames_units_and_originals():
    from tribuutscan import cloud
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);a=make_session(root,'a-scan');b=make_session(root,'b-scan',50)
        originals=[hashes(a),hashes(b)]
        t=np.eye(4);t[1,3]=.05  # PLY flips Y; undo B's +50 mm camera Y shift.
        out=root/'joined-scan'
        alignment.merge(a,b,out,t,[alignment.version(a),alignment.version(b)],log=lambda *_:None)
        assert [hashes(a),hashes(b)]==originals
        restored=cloud.Scanner.restore(out,log=lambda *_:None)
        try:
            assert len(restored.poses)==6 and len(restored.labels)==6
            assert len(restored.sources)==2
            assert all(Path(src['rec']).exists() for src in restored.sources)
            b_poses=restored.poses[3:]
            assert all(np.allclose(p[:3,3],[0,-.05,0]) for _,p in b_poses)
            assert len(set(i for i,_ in restored.frame_poses()))==6
        finally:restored.discard()
        source=view3d.read_ply(a/'cloud.ply');joined=view3d.read_ply(out/'cloud.ply')
        assert len(joined)==len(source)
        for key in ['x','y','z']:assert np.allclose(joined[key],source[key],atol=.0001)
        assert json.loads((out/'alignment.json').read_text())['optimised'] is False
        rejected(lambda:alignment.merge(a,b,out,t,[alignment.version(a),alignment.version(b)]))
        rejected(lambda:alignment.merge(a,b,root/'stale-scan',t,['stale','stale']))
        assert not (root/'stale-scan').exists()
        note=json.loads((b/'session.json').read_text());note['camera']['fingerprint']='different'
        (b/'session.json').write_text(json.dumps(note))
        rejected(lambda:alignment.compatible(a,b))


def test_failed_alignment_does_not_publish_partial_output():
    from tribuutscan import cloud
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);a=make_session(root,'a-scan');b=make_session(root,'b-scan')
        before=[hashes(a),hashes(b)]
        with patch.object(cloud.Scanner,'save',side_effect=RuntimeError('simulated disk failure')):
            try:alignment.merge(a,b,root/'failed-scan',np.eye(4),[alignment.version(a),alignment.version(b)])
            except RuntimeError:pass
            else:raise AssertionError('Failed save reported success')
        assert not (root/'failed-scan').exists()
        assert not list(root.glob('.alignment-*'))
        assert [hashes(a),hashes(b)]==before


def test_alignment_api_requires_pairing_and_validates_points_and_scan_paths():
    from test_webapp import running_service,client
    with running_service() as (service,base):
        guest=client(base,paired=False)
        assert guest('/api/alignment/cloud?scan=../../secret-scan')[0]==401
        assert b'id="pair-form"' in guest('/alignment')[1]
        request=client(base)
        assert request('/alignment')[0]==200
        assert request('/alignment.js')[0]==200
        assert request('/api/alignment/cloud?scan=../../secret-scan')[0]==400
        points=[[0,0,0],[.03,0,0],[0,.03,0]]
        state=service.state()
        code,body,_=request('/api/command',dict(action='alignment-fit',reference_points=points,moving_points=points,
            instance=state['instance'],revision=state['revision']))
        assert code==200 and np.allclose(json.loads(body)['transform'],np.eye(4))
        state=service.state()
        assert request('/api/command',dict(action='alignment-save',instance=state['instance'],revision=state['revision']))[0]==409


def test_save_refuses_active_capture_and_stale_preview_before_launching_a_job():
    from types import SimpleNamespace
    from tribuutscan import webapp, frames
    service=object.__new__(webapp.Service)
    service.demo=False;service.processing=None
    service.controller=SimpleNamespace(scan_state='scanning')
    try:service.save_alignment({})
    except webapp.Conflict:pass
    else:raise AssertionError('Alignment started during capture')
    service.controller=None
    with tempfile.TemporaryDirectory() as tmp, patch.object(frames,'CAPTURES',Path(tmp)):
        make_session(Path(tmp),'a-scan');make_session(Path(tmp),'b-scan')
        with patch.object(webapp.subprocess,'Popen') as launch:
            try:service.save_alignment(dict(reference='a-scan',moving='b-scan',versions=['old','old'],transform=np.eye(4).tolist()))
            except webapp.Conflict:pass
            else:raise AssertionError('Stale preview saved')
            launch.assert_not_called()
