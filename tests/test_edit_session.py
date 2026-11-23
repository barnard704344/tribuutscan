import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
from tribuutscan import alignment, cleanup, edit_session, view3d
from test_alignment import make_session, hashes


def region(x=.007):
    return cleanup.validate(dict(matrix=np.eye(4).tolist(),polygon=[[-1,-1],[x,-1],[x,1],[-1,1]]))


def test_cleanup_projection_uses_viewer_axes_and_respects_camera_clip_depth():
    xyz=np.array([[0,0,250],[20,0,250],[0,0,2000]])
    assert cleanup.keep(xyz,[region()]).tolist()==[False,True,True]
    for rule in [dict(matrix=np.zeros((4,4)).tolist(),polygon=[[0,0],[1,0],[0,1]]),
                 dict(matrix=np.eye(4).tolist(),polygon=[[0,0],[0,0],[0,0]])]:
        try:cleanup.validate(rule)
        except ValueError:pass
        else:raise AssertionError('Invalid region accepted')


def test_copy_cleanup_undo_redo_preserve_original_and_frame_sources():
    from tribuutscan import cloud
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=make_session(root,'source-scan');out=root/'edited-scan'
        before=hashes(source)
        edit_session.clone(source,out,alignment.version(source))
        initial=len(view3d.read_ply(out/'cloud.ply'))
        edit_session.edit(out,'delete-region',region())
        reduced=len(view3d.read_ply(out/'cloud.ply'))
        assert 0<reduced<initial
        restored=cloud.Scanner.restore(out,log=lambda *_:None)
        try:
            assert len(restored.cleanup)==1
            assert len(restored.poses)==3 and len(restored.labels)==3
            assert all(Path(src['rec']).exists() for src in restored.sources)
            assert restored.output_mask(np.array([[0,0,250],[20,0,250]])).tolist()==[False,True]
            restored.remerge({})
            assert len(restored.output_cloud().points(3)[0])==reduced
        finally:restored.discard()
        edit_session.edit(out,'undo')
        assert len(view3d.read_ply(out/'cloud.ply'))==initial
        assert not (out/'cleanup.json').exists()
        edit_session.edit(out,'redo')
        assert len(view3d.read_ply(out/'cloud.ply'))==reduced
        assert hashes(source)==before
        info=view3d.FileSource(out).current()[2]
        assert info['editing']['can_undo'] and not info['editing']['can_redo']
        edit_session.edit(out,'undo')
        edit_session.edit(out,'delete-region',region(.004))
        assert not view3d.FileSource(out).current()[2]['editing']['can_redo']
        assert hashes(source)==before


def test_failed_edit_does_not_change_working_geometry_or_original():
    from tribuutscan import optimise
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=make_session(root,'source-scan');out=root/'edited-scan'
        edit_session.clone(source,out,alignment.version(source))
        before=hashes(out);original=hashes(source)
        with patch.object(optimise,'process',side_effect=RuntimeError('simulated failure')):
            try:edit_session.edit(out,'small')
            except RuntimeError:pass
            else:raise AssertionError('Failed edit succeeded')
        after=hashes(out)
        assert all(after[k]==v for k,v in before.items() if k!='.editing.json')
        assert hashes(source)==original
        assert not list(root.glob('.edit-work-*'))


def test_lasso_deletion_preserves_point_attributes_and_never_rebuilds_frames():
    from tribuutscan import cloud
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=make_session(root,'source-scan');out=root/'edited-scan'
        info=json.loads((source/'session.json').read_text());info['capture']={'ir':{'exposure':1234}}
        (source/'session.json').write_text(json.dumps(info))
        edit_session.clone(source,out,alignment.version(source))
        original=hashes(source);before=hashes(out);points=view3d.read_ply(out/'cloud.ply')
        xyz=np.column_stack([points[k] for k in ('x','y','z')])
        expected=points[cleanup.keep(xyz*[1000,-1000,-1000],[region()])]
        with patch.object(cloud.Scanner,'restore',side_effect=AssertionError('Lasso must not rebuild frames')):
            edit_session.edit(out,'delete-region',region())
        actual=view3d.read_ply(out/'cloud.ply')
        assert actual.dtype==expected.dtype and actual.tobytes()==expected.tobytes()
        after=hashes(out)
        assert all(after[k]==v for k,v in before.items() if k not in ('cloud.ply','session.json','.editing.json'))
        saved=json.loads((out/'session.json').read_text())
        assert saved['points_kept']==len(actual) and saved['cleanup_regions']==1 and saved['capture']==info['capture']
        assert hashes(source)==original
        unchanged=hashes(out);note=edit_session.metadata(out)
        try:edit_session.edit(out,'delete-region',region(-.01))
        except ValueError as error:assert 'No saved points' in str(error)
        else:raise AssertionError('An empty deletion reported success')
        assert hashes(out)==unchanged and edit_session.metadata(out)==note


def test_continue_latest_ignores_editing_copies():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tribuutscan import controller, frames
    with tempfile.TemporaryDirectory() as tmp, patch.object(frames,'CAPTURES',Path(tmp)):
        source=Path(tmp)/'a-source-scan';copy=Path(tmp)/'z-edited-scan'
        for path in (source,copy):path.mkdir();(path/'merged.npz').write_bytes(b'fixture')
        (copy/'.editing.json').write_text('{}')
        app=SimpleNamespace(scan_state='idle',toggle_scan=Mock(),log=Mock())
        controller.Controller.continue_scan(app)
        app.toggle_scan.assert_called_once_with(continue_from=source)


def test_stale_or_other_selected_model_cannot_be_edited():
    from test_webapp import idle_dispatcher,dispatch
    from tribuutscan import frames,webapp
    with idle_dispatcher() as (service,app):
        source=make_session(frames.CAPTURES,'source-scan')
        service.model.file=view3d.FileSource(source)
        for payload in [dict(name='other-scan'),dict(name='source-scan',model_version='outdated')]:
            try:dispatch(service,'edit-copy',**payload)
            except webapp.Conflict:pass
            else:raise AssertionError('Edited a model different from the preview')
        try:dispatch(service,'edit-operation',name='source-scan',operation='delete-region',rule=region())
        except webapp.Conflict:pass
        else:raise AssertionError('Edited original session directly')


def test_mesh_extraction_applies_cleanup_and_renumbers_triangles():
    from types import SimpleNamespace
    from tribuutscan import fusion
    vertices=np.array([[0,0,250],[2,0,250],[0,2,250],[20,0,250],[22,0,250],[20,2,250]],float)
    triangles=np.array([[0,1,2],[3,4,5]])
    engine=SimpleNamespace(mesh=lambda _: (vertices,triangles,np.ones((6,3))))
    v,t,_=fusion.extract(engine,lambda xyz:cleanup.keep(xyz,[region()]))
    assert v[:,0].min()>=20 and t.tolist()==[[0,1,2]]


def test_solid_that_crosses_deleted_region_is_not_published_as_closed():
    from types import SimpleNamespace
    from tribuutscan import fusion,solid
    vertices=np.array([[20,0,250],[22,0,250],[20,2,250]],float)
    triangles=np.array([[0,1,2]],np.int32);colours=np.full((3,3),150,np.uint8)
    report=dict(frames=3,triangles=1,fuse_s=0,extract_s=0)
    with tempfile.TemporaryDirectory() as tmp,patch.object(fusion,'fuse_scan',return_value=(vertices,triangles,colours,report)),patch.object(fusion,'printing_planes',return_value=[]),patch.object(solid,'close',return_value=(vertices-[20,0,0],triangles,colours,dict(edges_closed=True))):
        result=fusion.make_mesh(Path(tmp),scanner=SimpleNamespace(cleanup=[region()]))
        assert result['solid']['edges_closed'] is False
        assert not (Path(tmp)/'solid.ply').exists()
        assert view3d.mesh_versions(Path(tmp)/'mesh.ply')['stl_state']=='open'
