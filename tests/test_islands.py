"""Actual connected-component analysis, non-destructive preview, editable mesh undo and guards."""
import base64
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from tribuutscan import alignment,edit_session,frames,islands,pointcloud,view3d
from test_alignment import hashes,make_session,rejected


def mesh(directory):
    xyz=np.array([[0,0,0],[.02,0,0],[0,.03,0],[0,0,.04],[.1,0,0],[.11,0,0],[.1,.01,0]],np.float32)
    rgb=np.arange(21,dtype=np.uint8).reshape(7,3)
    tri=np.array([[0,2,1],[0,1,3],[0,3,2],[1,2,3],[4,5,6]],np.int32)
    pointcloud.write_mesh_ply(directory/'mesh.ply',xyz,rgb,tri)
    return xyz,rgb,tri


def test_preview_components_largest_protection_and_invalid_settings():
    with tempfile.TemporaryDirectory() as tmp:
        source=make_session(Path(tmp),'source-scan');mesh(source);before=hashes(source)
        result,*_=islands.analyse(source,{'min_triangles':2})
        assert result['components']==2 and result['removed_components']==1
        assert result['removed_triangles']==1 and result['kept_triangles']==4
        mask=np.unpackbits(np.frombuffer(base64.b64decode(result['mask']),np.uint8),bitorder='little')[:5]
        assert mask.tolist()==[0,0,0,0,1] and hashes(source)==before
        assert islands.analyse(source,{'min_triangles':100})[0]['kept_triangles']==4
        assert islands.analyse(source,{'min_triangles':1})[0]['removed_triangles']==0
        for n in [0,True,1.5,1000001,None]:rejected(lambda:islands.analyse(source,{'min_triangles':n}))


def test_island_mesh_edit_preserves_points_colours_and_original_and_undo_restores_all_exports():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);source=make_session(root,'source-scan');xyz,rgb,_=mesh(source)
        (source/'mesh.stl').write_bytes(b'print export');(source/'solid.ply').write_bytes(b'solid export')
        before=hashes(source);copy=root/'edited-scan';edit_session.clone(source,copy,alignment.version(source))
        result,*_=islands.analyse(copy,{'min_triangles':2})
        rule={k:result[k] for k in ['min_triangles','mesh_hash']}
        edit_session.edit(copy,'remove-islands',rule)
        vertices,triangles=view3d.read_ply(copy/'mesh.ply',faces=True)
        assert len(triangles)==4 and len(vertices)==4
        assert np.array_equal(np.column_stack([vertices[k] for k in ['red','green','blue']]),rgb[:4])
        assert (copy/'cloud.ply').read_bytes()==(source/'cloud.ply').read_bytes()
        assert not (copy/'mesh.stl').exists() and not (copy/'solid.ply').exists()
        assert hashes(source)==before
        edited=hashes(copy)
        edit_session.edit(copy,'undo');assert {k:v for k,v in hashes(copy).items() if k!=edit_session.MARKER}==before
        edit_session.edit(copy,'redo');assert hashes(copy)==edited
        # A changed mesh cannot be edited using an old preview, and failures preserve current files.
        current=hashes(copy);rejected(lambda:edit_session.edit(copy,'remove-islands',rule));assert hashes(copy)==current
        pointcloud.write_ply(copy/'mesh.ply',xyz,rgb)
        rejected(lambda:islands.analyse(copy,{'min_triangles':2}))


def test_island_preview_and_apply_api_are_paired_fresh_and_busy_guarded():
    from test_webapp import running_service,client
    with running_service() as (service,base):
        source=make_session(frames.CAPTURES,'source-scan');mesh(source);before=hashes(source)
        request=client(base);guest=client(base,paired=False)
        service.retry_at=float('inf');service.demo=False
        def command(action,**data):
            state=service.state()
            return request('/api/command',dict(action=action,instance=state['instance'],revision=state['revision'],**data))
        assert guest('/api/islands/result?job=test')[0]==401
        assert guest('/api/command',{'action':'edit-operation','operation':'preview-islands'})[0]==401
        assert command('open',name=source.name)[0]==200
        info=json.loads(request('/model/info.json')[1])
        data=dict(name=source.name,model_version=info['version'],operation='preview-islands',rule={'min_triangles':2},mesh_version=info['mesh'])
        assert command('edit-operation',**dict(data,mesh_version='stale'))[0]==409
        with patch.object(service,'processing',SimpleNamespace(poll=lambda:None)):
            assert command('edit-operation',**data)[0]==409
        status,body,_=command('edit-operation',**data);assert status==200
        job=json.loads(body)['job']
        for _ in range(150):
            state=service.state();preview=state.get('mesh_islands_preview')
            if preview and preview.get('job')==job and not state['processing']:break
            time.sleep(.1)
        assert not state['job_failed'],state.get('job_result')
        assert preview['result']['removed_triangles']==1 and hashes(source)==before
        assert 'mask' not in preview['result']
        full=json.loads(request('/api/islands/result?job='+job)[1])
        assert full['job']==job and full['result']['mask']
        assert request('/api/islands/result?job=stale')[0]==404
        assert command('edit-operation',name=source.name,model_version=info['version'],operation='remove-islands',rule=preview['result'])[0]==409  # editing copy required
