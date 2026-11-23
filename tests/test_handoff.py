"""Desktop file handoff: actual surface, millimetres, private routes and stale guards."""
import json
from pathlib import Path
import struct
import tempfile
from urllib.parse import urlencode
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from tribuutscan import frames,pointcloud,view3d
from test_alignment import hashes,make_session,rejected


def surface(path):
    xyz=np.array([[0,0,0],[.025,0,0],[0,.050,0]],np.float32)
    pointcloud.write_mesh_ply(path,xyz,np.full((3,3),150,np.uint8),np.array([[0,1,2]],np.int32))


def test_handoff_stl_converts_metres_to_mm_without_changing_or_closing_surface():
    with tempfile.TemporaryDirectory() as tmp:
        directory=Path(tmp);path=directory/'mesh.ply';surface(path)
        before=hashes(directory);data=view3d.handoff_stl(path)
        assert len(data)==134 and struct.unpack('<I',data[80:84])[0]==1
        values=struct.unpack('<12fH',data[84:])
        assert np.allclose(values[:3],[0,0,1])
        assert np.allclose(np.array(values[3:12]).reshape(3,3),[[0,0,0],[25,0,0],[0,50,0]])
        assert hashes(directory)==before and not (directory/'mesh.stl').exists()
        pointcloud.write_ply(path,np.zeros((3,3),np.float32),np.zeros((3,3),np.uint8))
        rejected(lambda:view3d.handoff_stl(path))
        surface(path)
        original=view3d.read_ply
        def changed(*args,**kwargs):
            data=original(*args,**kwargs);path.write_bytes(path.read_bytes()+b'changed');return data
        with patch.object(view3d,'read_ply',side_effect=changed):rejected(lambda:view3d.handoff_stl(path))


def test_handoff_api_requires_pairing_current_scan_and_current_mesh():
    from test_webapp import running_service,client
    with running_service() as (service,base):
        guest=client(base,paired=False)
        assert guest('/model/handoff.stl')[0]==401
        source=make_session(frames.CAPTURES,'handoff-scan');surface(source/'mesh.ply')
        request=client(base);state=service.state()
        assert request('/api/command',dict(action='open',name=source.name,instance=state['instance'],revision=state['revision']))[0]==200
        info=json.loads(request('/model/info.json')[1]);before=hashes(source)
        params=dict(name=info['name'],version=info['version'],mesh=info['mesh'])
        endpoint='/model/handoff.stl?'+urlencode(params)
        status,data,headers=request(endpoint)
        assert status==200 and headers['Content-Type']=='model/stl'
        assert 'surface-mm.stl' in headers['Content-Disposition']
        assert struct.unpack('<I',data[80:84])[0]==1 and hashes(source)==before
        with patch.object(service,'processing',SimpleNamespace(poll=lambda:None)):
            assert request(endpoint)[0]==409
        for key in params:
            assert request('/model/handoff.stl?'+urlencode(dict(params,**{key:'stale'})))[0]==409
        assert request('/model/handoff.stl?'+urlencode(dict(params,path='/etc/passwd')))[0]==400
        assert request('/model/handoff.stl')[0]==400
        # A retired mesh must never fall back to a stale printable STL.
        (source/'mesh.stl').write_bytes(b'old printable export');(source/'mesh.ply').unlink()
        assert request(endpoint)[0]==409
