"""Remote gateway, command ordering and headless-controller regressions. No hardware required."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.cookiejar
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

import numpy as np
from tribuutscan import controller, frames, webapp


@contextmanager
def running_service():
    with tempfile.TemporaryDirectory() as tmp, patch.object(frames, 'CAPTURES', Path(tmp)):
        service = webapp.Service(demo=True)
        service.start()
        server = webapp.Server(('127.0.0.1', 0), webapp.Handler)
        server.service, server.token, server.session = service, 'test-pairing-code', 'test-session-cookie'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield service, f'http://127.0.0.1:{server.server_address[1]}'
        finally:
            server.shutdown()
            server.server_close()
            service.close()
            thread.join(timeout=2)


def client(base, paired=True):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        options = {'Content-Type': 'application/json', 'X-Tribuutscan': '1'} if data is not None else {}
        options.update(headers or {})
        try:
            response = opener.open(urllib.request.Request(base + path, body, headers=options), timeout=5)
            return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers
    if paired:
        assert request('/api/pair', {'token': 'test-pairing-code'})[0] == 200
    return request


def test_web_help_and_import_need_neither_tkinter_nor_a_display():
    code = "import sys; from tribuutscan import webapp; assert 'tkinter' not in sys.modules"
    subprocess.run([sys.executable, '-c', code], check=True)
    result = subprocess.run([sys.executable, '-m', 'tribuutscan.webapp', '--help'], capture_output=True, text=True, check=True)
    assert '--no-browser' in result.stdout and '--host' in result.stdout and '--demo' in result.stdout


def test_pairing_protects_status_previews_models_and_downloads():
    with running_service() as (service, base):
        request = client(base, paired=False)
        for path in ['/api/access', '/api/state', '/preview/depth.png', '/preview/coverage-view.png', '/model/', '/model/cloud.ply', '/model/mesh.stl']:
            assert request(path)[0] == 401, path
        for path in ['/', '/scanner.css', '/scanner.js', '/qrcodegen.js']:
            assert request(path)[0] == 200
        assert request('/api/pair', {'token': 'wrong'})[0] == 401
        status, body, headers = request('/api/pair', {'token': 'test-pairing-code'})
        assert status == 200
        assert 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
        assert request('/api/state')[0] == 200
        access_status, access_body, access_headers = request('/api/access')
        assert access_status == 200 and json.loads(access_body)['token'] == 'test-pairing-code'
        assert access_headers['Cache-Control'] == 'no-store'
        assert request('/model/info.json')[0] == 200
        assert request('/model/cloud.bin')[0] == 200
        assert request('/model/../../secrets.txt')[0] == 404


def test_cross_origin_and_plain_form_commands_are_rejected():
    with running_service() as (service, base):
        request = client(base)
        payload = {'action':'live', 'revision':service.revision, 'instance':service.instance}
        assert request('/api/command', payload, {'X-Tribuutscan':''})[0] == 403
        assert request('/api/command', payload, {'Origin':'http://different.example'})[0] == 403
        assert request('/api/pair', {'token':'test-pairing-code'}, {'Origin':'http://different.example'})[0] == 403


def test_two_browsers_cannot_execute_the_same_stale_command():
    with running_service() as (service, base):
        first, second = client(base), client(base)
        revision = json.loads(first('/api/state')[1])['revision']
        payload = {'action':'live', 'revision':revision, 'instance':service.instance}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda request:request('/api/command', payload)[0], [first,second]))
        assert sorted(results) == [200,409]
        # Browser disconnects do not close the scanner service.
        assert not service.stop.is_set() and second('/api/state')[0] == 200


def test_png_preview_round_trip_and_unavailable_frames():
    rgb = np.array([[[0,1,2],[3,4,5]],[[6,7,8],[9,10,11]]], np.uint8)
    with running_service() as (service, base):
        request = client(base)
        assert request('/preview/depth.png')[0] == 204
        # Stop the publisher overwriting this synthetic preview while we exercise HTTP encoding.
        with patch.object(service, 'publish'):
            with service.lock:
                service.previews = {'depth':controller.ppm(rgb)}
            status, data, headers = request('/preview/depth.png')
            assert status == 200 and headers['Content-Type'] == 'image/png'
            assert data.startswith(b'\x89PNG\r\n\x1a\n')
            assert struct.unpack('>II', data[16:24]) == (2,2)
            assert request('/preview/depth.png')[1] == data  # cached frame, no repeated encoding
        assert request('/preview/unknown.png')[0] == 404


def test_only_saved_scans_inside_capture_root_can_be_opened():
    with tempfile.TemporaryDirectory() as tmp, patch.object(frames, 'CAPTURES', Path(tmp)):
        folder = Path(tmp) / 'one-scan'
        folder.mkdir()
        (folder/'cloud.ply').write_bytes(b'example')
        assert webapp.scan_directory('one-scan') == folder
        for name in ['../one-scan','/one-scan','missing-scan',None,'one-scan/child','one-rec']:
            try:webapp.scan_directory(name)
            except ValueError:pass
            else:raise AssertionError(name)
        (Path(tmp)/'link-scan').symlink_to(folder, target_is_directory=True)
        try:webapp.scan_directory('link-scan')
        except ValueError:pass
        else:raise AssertionError('symlink accepted')


def fake_app():
    app = SimpleNamespace(scan_state='idle', lost=False, switching={'busy':False},
                          rec={'recorder':None}, job={'process':None}, live={'url':None,'name':''})
    for name, allowed in webapp.SETTINGS.items():
        setattr(app,name,controller.Setting(False if allowed is bool else allowed[0]))
    for name in ['toggle_scan','cancel_scan','toggle_pause_scan','continue_scan','start_separate_piece',
                 'choose_depth_preview','toggle_hold','toggle_markers','toggle_imu','retry_now','toggle_record']:
        setattr(app,name,Mock())
    app.can_start_separate_piece = lambda:False
    return app


@contextmanager
def idle_dispatcher():
    with tempfile.TemporaryDirectory() as tmp, patch.object(frames, 'CAPTURES', Path(tmp)):
        service=webapp.Service(demo=False)
        service.controller=fake_app()
        try:yield service, service.controller
        finally:
            service.model_server.shutdown()
            service.model_server.server_close()


def dispatch(service, action, **kwargs):
    return service.dispatch(dict(action=action, revision=service.revision, instance=service.instance, **kwargs))


def test_scan_controls_are_explicit_and_enforce_phase_and_device_state():
    with idle_dispatcher() as (service,app):
        dispatch(service,'start');app.toggle_scan.assert_called_once()
        app.scan_state='paused';dispatch(service,'resume');app.toggle_pause_scan.assert_called_once()
        app.toggle_pause_scan.reset_mock()
        try:dispatch(service,'pause')
        except webapp.Conflict:pass
        else:raise AssertionError('stale pause accepted while paused')
        app.toggle_pause_scan.assert_not_called()
        app.lost=True
        dispatch(service,'stop')  # a disconnect must not prevent saving the partial take
        assert app.toggle_scan.call_count == 2
        try:dispatch(service,'start')
        except webapp.Conflict:pass
        else:raise AssertionError('start accepted during device loss')
        app.scan_state='idle';app.lost=False;app.rec['recorder']=object()
        try:dispatch(service,'start')
        except webapp.Conflict:pass
        else:raise AssertionError('start accepted during recording')


def test_settings_are_validated_together_and_locked_during_capture():
    with idle_dispatcher() as (service,app):
        try:dispatch(service,'settings',settings={'keep_raw':True,'preset':'invalid'})
        except ValueError:pass
        else:raise AssertionError('invalid preset accepted')
        assert app.keep_raw.get() is False
        dispatch(service,'settings',settings={'keep_raw':True,'preset':'medium'})
        assert app.keep_raw.get() is True and app.preset.get() == 'medium'
        dispatch(service,'settings',settings={'scene_mode':'free'})
        assert app.scene_mode.get() == 'free'
        app.scan_state='scanning'
        try:dispatch(service,'settings',settings={'scene_mode':'still'})
        except webapp.Conflict:pass
        else:raise AssertionError('subject mode changed during a scan')
        try:dispatch(service,'settings',settings={'keep_raw':False})
        except webapp.Conflict:pass
        else:raise AssertionError('raw recording changed during a scan')
        try:dispatch(service,'settings',settings={'preview_mode':controller.COVERAGE})
        except ValueError:pass
        else:raise AssertionError('removed depth/coverage toggle was accepted')


def test_pick_coordinates_are_validated_and_converted_to_preview_pixels():
    with idle_dispatcher() as (service,app):
        app.aim_click=Mock()
        dispatch(service,'pick',kind='depth',x=.5,y=.5)
        app.aim_click.assert_called_once_with('Depth',276.0,172.5)
        for x in [float('nan'),2,True,'0.5']:
            try:dispatch(service,'pick',kind='depth',x=x,y=.5)
            except ValueError:pass
            else:raise AssertionError('invalid coordinate accepted')


def test_demo_cannot_capture_or_modify_saved_scans():
    with running_service() as (service,base):
        request=client(base)
        for action in ['start','record','process']:
            revision=json.loads(request('/api/state')[1])['revision']
            assert request('/api/command',dict(action=action,revision=revision,instance=service.instance,operation='mesh'))[0] == 409


def test_cancelled_queued_request_does_not_execute_later():
    with running_service() as (service,base):
        from concurrent.futures import Future
        future=Future();future.cancel()
        with patch.object(service,'dispatch') as dispatched:
            service.commands.put(({'action':'start'},future))
            # An ordered barrier proves the cancelled item was consumed without depending on a sleep.
            barrier=Future()
            service.commands.put(({'action':'live','revision':service.revision},barrier))
            barrier.result(timeout=5)
            assert dispatched.call_count == 1
            assert dispatched.call_args.args[0]['action'] == 'live'


def test_saved_model_and_download_use_the_same_paired_connection():
    from tribuutscan.pointcloud import write_ply
    with running_service() as (service,base):
        path=frames.CAPTURES/'example-scan'
        path.mkdir()
        write_ply(path/'cloud.ply',np.array([[0.,0.,0.],[.1,.1,.1]]),np.array([[10,20,30],[40,50,60]],np.uint8))
        (path/'session.json').write_text(json.dumps({'crop_axes':np.eye(3).tolist()}))
        request=client(base)
        revision=json.loads(request('/api/state')[1])['revision']
        assert request('/api/command',dict(action='open',name='example-scan',revision=revision,instance=service.instance))[0] == 200
        status,body,_=request('/model/info.json')
        assert status == 200 and json.loads(body)['points'] == 2
        status,body,headers=request('/model/cloud.ply')
        assert status == 200 and body == (path/'cloud.ply').read_bytes()
        assert 'example-scan.ply' in headers['Content-Disposition']


def test_controller_can_start_tick_and_close_without_any_gui_objects():
    with tempfile.TemporaryDirectory() as tmp:
        captures=[Mock() for _ in range(3)]
        for capture in captures:
            capture.width,capture.height,capture.bytesperline=1280,800,1600
        with patch.object(controller.v4l2,'discover',return_value={'depth':'depth','color':'colour','ir':'ir'}), \
             patch.object(controller,'open_captures',return_value=tuple(captures)), \
             patch.object(controller,'ir_exposure_mode'), \
             patch.object(controller,'node_controls',return_value={}), \
             patch.object(controller.usbdepth,'available',return_value=False), \
             patch.object(controller.threading.Thread,'start'), \
             patch.object(controller,'LOG',Path(tmp)/'viewer.log'):
            app=controller.Controller()
            app.initialise_controls()
            app.start()
            app.tick()
            assert not hasattr(app,'root') and not hasattr(app,'model_pane')
            assert isinstance(app.keep_raw,controller.Setting)
            app.close()
            for stream in app.streams.values():
                assert stream.stopped.is_set()


def test_processing_reuses_current_worker_instead_of_allocating_a_second_model():
    with idle_dispatcher() as (service,app):
        folder=frames.CAPTURES/'current-scan'
        folder.mkdir()
        (folder/'cloud.ply').write_bytes(b'example')
        service.model.file=webapp.view3d.FileSource(folder)
        app.live.update(proc=Mock(is_alive=lambda:True),name='current-scan',url='http://127.0.0.1:12345/')
        with patch.object(webapp,'WorkerProcessing') as worker, patch.object(webapp.subprocess,'Popen') as spawn:
            dispatch(service,'process',operation='mesh')
            assert worker.call_args.args[:3] == ('http://127.0.0.1:12345/','mesh',None)
            spawn.assert_not_called()


def test_private_model_backend_requires_its_internal_key():
    with running_service() as (service,base):
        internal=service.empty_url.rstrip('/')
        request=client(internal,paired=False)
        assert request('/info.json')[0] == 401
        assert request('/cloud.bin')[0] == 401
        assert request('/info.json',headers={'Authorization':'Bearer incorrect'})[0] == 401
        headers={'Authorization':'Bearer '+service.backend_token}
        assert request('/info.json',headers=headers)[0] == 200
        # Pairing grants the gateway access without exposing the internal key to the browser.
        gateway=client(base)
        assert gateway('/model/info.json')[0] == 200
        snapshot=json.loads(gateway('/api/state')[1])
        assert service.backend_token not in json.dumps(snapshot)


def test_commands_from_an_older_host_are_rejected_even_when_revision_matches():
    with running_service() as (service,base):
        request=client(base)
        snapshot=json.loads(request('/api/state')[1])
        payload=dict(action='live',revision=snapshot['revision'],instance='previous-host')
        assert request('/api/command',payload)[0] == 409
        payload.pop('instance')
        assert request('/api/command',payload)[0] == 409
        payload['instance']=snapshot['instance']
        assert request('/api/command',payload)[0] == 200


def test_shutdown_waits_for_processing_before_closing_its_worker():
    import queue
    order=[]
    service=webapp.Service.__new__(webapp.Service)
    service.stop=threading.Event();service.stop.set()
    service.processing=Mock(wait=lambda:order.append('processing finished'))
    service.processing_thread=None
    service.controller=Mock(close=lambda:order.append('worker closed'))
    service.commands=queue.Queue()
    service.run()
    assert order == ['processing finished','worker closed']


def test_model_download_cannot_switch_scans_partway_through_a_request():
    from tribuutscan.pointcloud import write_ply
    with running_service() as (service,base):
        folders=[]
        for name,value in [('first-scan',1),('second-scan',2)]:
            folder=frames.CAPTURES/name;folder.mkdir();folders.append(folder)
            write_ply(folder/'cloud.ply',np.array([[float(value),0.,0.]]),np.array([[10,20,30]],np.uint8))
            (folder/'session.json').write_text(json.dumps({'crop_axes':np.eye(3).tolist()}))
        first,second=map(webapp.view3d.FileSource,folders)
        original=first.current
        def switch_during_read():
            result=original()
            service.model.file=second
            return result
        first.current=switch_during_read
        service.model.file=first
        request=client(base)
        status,body,headers=request('/model/cloud.ply')
        assert status == 200 and body == (folders[0]/'cloud.ply').read_bytes()
        assert 'first-scan.ply' in headers['Content-Disposition']


def test_processing_gateway_preserves_instance_guard():
    with running_service() as (service,base):
        request=client(base)
        snapshot=json.loads(request('/api/state')[1])
        with patch.object(service,'request',return_value={}) as dispatch_request:
            assert request('/model/process/mesh',dict(revision=snapshot['revision'],instance=snapshot['instance']))[0] == 200
        assert dispatch_request.call_args.args[0]['instance'] == snapshot['instance']


def test_standalone_model_processing_requires_same_origin_custom_header():
    from types import SimpleNamespace
    source=SimpleNamespace(current=lambda:('v',b'',dict(phase='done',name='fixture',points=0)),
                           process=Mock(return_value='processed fixture'))
    server=webapp.view3d.serve(source,0)
    try:
        request=client(f'http://127.0.0.1:{server.server_address[1]}',paired=False)
        assert request('/info.json')[0] == 200  # optional standalone viewer stays usable
        assert request('/process/mesh',{}, {'X-Tribuutscan':''})[0] == 403
        assert request('/process/mesh',{}, {'Origin':'http://elsewhere.example'})[0] == 403
        source.process.assert_not_called()
        assert request('/process/mesh',{})[0] == 200
        source.process.assert_called_once_with('mesh',box=None)
    finally:
        server.shutdown();server.server_close()


def test_spawned_worker_enforces_internal_auth_and_processing_client_supplies_it():
    import queue
    from tribuutscan import livescan
    commands,replies=queue.Queue(),queue.Queue()
    secret='internal-test-key-that-is-not-a-pairing-code'
    worker=threading.Thread(target=livescan.run,args=(commands,replies,0,secret),daemon=True)
    with patch.object(sys,'path',sys.path[:]):
        worker.start()
        try:
            kind,url=replies.get(timeout=5)
            assert kind == 'ready'
            request=client(url.rstrip('/'),paired=False)
            assert request('/info.json')[0] == 401
            assert request('/info.json',headers={'Authorization':'Bearer '+secret})[0] == 200
            reports=[]
            job=webapp.WorkerProcessing(url,'mesh',None,reports.append,auth_token=secret)
            assert job.finished.wait(timeout=15)
            assert reports and reports[0].startswith('not now:')  # reached the worker, which has no scan yet
            assert job.poll() == 1
        finally:
            commands.put(('quit',))
            worker.join(timeout=5)
    assert not worker.is_alive()


def test_local_backend_requests_ignore_environment_http_proxies():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    hits=[]
    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_error(502)
        def log_message(self,*args):
            pass
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
    thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
    try:
        address=f'http://127.0.0.1:{proxy.server_address[1]}'
        with running_service() as (service,base), patch.dict('os.environ',
                {'HTTP_PROXY':address,'http_proxy':address,'NO_PROXY':'','no_proxy':''}):
            request=urllib.request.Request(service.empty_url+'info.json',
                headers={'Authorization':'Bearer '+service.backend_token})
            with webapp.urlopen(request,timeout=5) as response:
                assert response.status == 200
        assert hits == []
    finally:
        proxy.shutdown();proxy.server_close();thread.join(timeout=2)


def test_download_names_cannot_inject_http_headers_and_support_unicode():
    source=SimpleNamespace(current=lambda:('v',b'',dict(phase='done',points=0)),
        ply_bytes=lambda:b'fixture',download_name=lambda:'scan\r\nX-Audit: injected-é')
    server=webapp.view3d.serve(source,0)
    try:
        request=client(f'http://127.0.0.1:{server.server_address[1]}',paired=False)
        status,body,headers=request('/cloud.ply')
        assert status == 200 and body == b'fixture'
        assert headers.get('X-Audit') is None
        assert '%0D%0A' in headers['Content-Disposition'] and '%C3%A9' in headers['Content-Disposition']
    finally:
        server.shutdown();server.server_close()


def test_flat_coverage_updates_worker_colours_even_when_tracking_is_lost_and_holds_only_on_pause():
    from tribuutscan.depth import encode_y10
    cap=SimpleNamespace(width=64,height=40,bytesperline=80)
    current=encode_y10(np.full((40,64),270,np.uint16))
    accepted=encode_y10(np.full((40,64),260,np.uint16))
    phase=['scanning']
    render=webapp.workspace_depth_renderer(lambda:phase[0])
    with patch.dict(controller.scan_view,active=True,return_guide=True,preview=controller.LIVE_DEPTH,
                    mask=np.full((40,64),2),t=100.,lost=False,
                    model=(np.full((40,64),1,np.uint8),accepted)), \
         patch.object(webapp.time,'monotonic',return_value=100.) as clock,patch.dict(controller.hold_view,on=False):
        depth,stats=render(cap,current)
        coverage=stats['coverage_image']
        assert coverage is not None and coverage != depth
        assert depth == controller.render_depth(cap,current,preview=controller.LIVE_DEPTH)[0]
        _,pixels=coverage.split(b'\n',1)
        assert (np.frombuffer(pixels,np.uint8).reshape(-1,3)==controller.MODEL_MISSING).all(1).any()
        assert 'observed enough' in stats['coverage_stats']['coverage']
        assert controller.scan_view['preview'] == controller.LIVE_DEPTH
        clock.return_value=100.3
        controller.scan_view.update(t=100.3,model=(np.full((40,64),80,np.uint8),accepted))
        _,thin=render(cap,current)
        assert thin['coverage_image'] != coverage
        clock.return_value=100.6
        controller.scan_view.update(t=100.6,model=(np.full((40,64),200,np.uint8),accepted))
        _,done=render(cap,current)
        assert done['coverage_image'] != thin['coverage_image']
        clock.return_value=101.
        controller.scan_view.update(lost=True,t=101.,model=(np.full((40,64),240,np.uint8),accepted))
        _,lost=render(cap,accepted)
        assert lost['coverage_image'] != coverage
        _,pixels=lost['coverage_image'].split(b'\n',1)
        colours=np.frombuffer(pixels,np.uint8).reshape(-1,3)
        assert (colours[:,2]>colours[:,0]).all()
        assert lost['coverage_stats']['updated_at'] == 101.
        assert lost['centre_cm'] != stats['centre_cm']
        phase[0]='paused';controller.scan_view['active']=False
        clock.return_value=102.
        controller.scan_view.update(t=102.,model=(np.full((40,64),1,np.uint8),accepted))
        _,paused=render(cap,accepted)
        assert paused['coverage_image'] == lost['coverage_image']
        phase[0]='idle'
        _,idle=render(cap,current)
        assert idle['coverage_image'] is None
        phase[0]='scanning';controller.scan_view.update(active=True,model=None)
        _,waiting=render(cap,current)
        assert waiting['coverage_image'] is None  # never substitute live depth for missing coverage


def test_flat_coverage_has_no_3d_controller_or_orbit_command():
    assert not hasattr(webapp,'CoverageView')
    with idle_dispatcher() as (service,app):
        assert not hasattr(service,'coverage_view')
        try:dispatch(service,'coverage-view',yaw=1.,pitch=0.,zoom=1.)
        except ValueError:pass
        else:raise AssertionError('removed 3D orbit command was accepted')


def test_coverage_preview_uses_the_paired_preview_route():
    with running_service() as (service,base):
        request=client(base)
        assert json.loads(request('/api/state')[1])['coverage_preview'] is True
        assert request('/preview/coverage-view.png')[0] == 204
        with patch.object(service,'publish'):
            with service.lock:
                service.previews={'coverage-view':controller.ppm(np.full((18,32,3),128,np.uint8))}
            status,body,headers=request('/preview/coverage-view.png')
            assert status == 200 and body.startswith(b'\x89PNG')
            assert headers['Content-Type'] == 'image/png'


def test_restart_is_rejected_until_all_host_work_is_finished():
    with idle_dispatcher() as (service,app):
        for phase in ('scanning','counting','paused','saving','optimising','meshing','cancelling'):
            app.scan_state=phase
            try:dispatch(service,'restart')
            except webapp.Conflict:pass
            else:raise AssertionError('restart interrupted '+phase)
        app.scan_state='idle'
        for owner,key in ((app.rec,'recorder'),(app.job,'process'),(app.switching,'busy')):
            owner[key]=True
            try:dispatch(service,'restart')
            except webapp.Conflict:pass
            else:raise AssertionError('restart interrupted '+key)
            owner[key]=None if key!='busy' else False
        service.processing=Mock()
        try:dispatch(service,'restart')
        except webapp.Conflict:pass
        else:raise AssertionError('restart interrupted saved-scan processing')
        service.processing=None
        dispatch(service,'restart')
        assert service.restart_requested
        try:dispatch(service,'start')
        except webapp.Conflict:pass
        else:raise AssertionError('accepted capture during restart')


def test_restart_replaces_the_host_and_keeps_browser_pairing():
    import os,shutil,socket,time
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        shutil.copytree(Path(webapp.__file__).parent,root/'tribuutscan',ignore=shutil.ignore_patterns('__pycache__'))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=dict(os.environ,PYTHONPATH=str(root));env.pop('_TRIBUUTSCAN_RESTART',None)
        with (root/'host.log').open('w') as output:
            host=subprocess.Popen([sys.executable,'-m','tribuutscan.webapp','--demo','--host','127.0.0.1',
                '--port',str(port),'--no-browser'],cwd=root,env=env,stdout=output,stderr=output)
            try:
                session=root/'captures'/'.webapp.json'
                deadline=time.monotonic()+20
                while not session.exists() or not session.stat().st_size:
                    assert host.poll() is None,'demo host exited before startup'
                    if time.monotonic()>deadline:raise AssertionError('demo host did not start')
                    time.sleep(.05)
                token=json.loads(session.read_text())['token']
                request=client(f'http://127.0.0.1:{port}',paired=False)
                assert request('/api/pair',{'token':token})[0] == 200
                before=json.loads(request('/api/state')[1])
                assert request('/api/command',dict(action='restart',instance=before['instance'],revision=before['revision']))[0] == 200
                deadline=time.monotonic()+20
                while True:
                    try:
                        status,body,_=request('/api/state')
                        assert status == 200,'paired browser was disconnected by restart'
                        after=json.loads(body)
                        if after['instance']!=before['instance']:break
                    except (OSError,urllib.error.URLError):pass
                    assert host.poll() is None,'host failed to restart'
                    if time.monotonic()>deadline:raise AssertionError('host instance did not change')
                    time.sleep(.1)
                assert after['demo'] and after['coverage_preview'] and after['restart_supported']
                assert json.loads(session.read_text())['token'] == token
            finally:
                host.terminate()
                try:host.wait(timeout=20)
                except subprocess.TimeoutExpired:host.kill();host.wait()


def test_subject_choice_survives_host_restart_and_ignores_invalid_preferences():
    with tempfile.TemporaryDirectory() as tmp,patch.object(frames,'CAPTURES',Path(tmp)):
        app=fake_app();app.scene_mode.set('still')
        webapp.save_subject('free')
        assert ((Path(tmp)/'.webapp-settings.json').stat().st_mode & 0o777) == 0o600
        webapp.restore_subject(app)
        assert app.scene_mode.get() == 'free'
        webapp.save_subject('still');webapp.restore_subject(app)
        assert app.scene_mode.get() == 'still'
        for bad in ('[]','{"scene_mode":"unknown"}','invalid json'):
            (Path(tmp)/'.webapp-settings.json').write_text(bad)
            webapp.restore_subject(app)
            assert app.scene_mode.get() == 'still'


def test_chosen_head_mode_is_persisted_by_command_and_restored_before_controller_start():
    with idle_dispatcher() as (service,app):
        dispatch(service,'settings',settings={'scene_mode':'free'})
        assert json.loads((frames.CAPTURES/'.webapp-settings.json').read_text())['scene_mode'] == 'free'
        replacement=fake_app();replacement.scene_mode.set('still')
        replacement.initialise_controls=Mock();replacement.tick=Mock();replacement.close=Mock()
        service.demo=False;service.controller=None
        def start():
            assert replacement.scene_mode.get() == 'free'
            service.stop.set()
        replacement.start=start
        with patch.object(controller,'Controller',return_value=replacement),patch.object(service,'publish'):
            service.run()
        replacement.close.assert_called_once()


def test_paused_coverage_camera_uses_the_paired_gateway_and_validates_angles():
    with running_service() as (service,base):
        source=SimpleNamespace(coverage_image=Mock(return_value=b'\x89PNG fixture'),current=Mock())
        backend=webapp.view3d.serve(source,0,auth_token=service.backend_token)
        try:
            service.model_url=f'http://127.0.0.1:{backend.server_address[1]}/'
            assert client(base,paired=False)('/model/coverage.png')[0] == 401
            request=client(base)
            assert request('/model/coverage.png?yaw=.5&pitch=.2&zoom=2')[0] == 200
            source.coverage_image.assert_called_once_with(.5,.2,2.)
            source.current.assert_not_called()
            for query in ('yaw=nan','zoom=0','pitch=8','yaw=1&yaw=2','extra=1'):
                assert request('/model/coverage.png?'+query)[0] == 400
            assert source.coverage_image.call_count == 1
            source.coverage_image.side_effect=ValueError('Pause the scan')
            assert request('/model/coverage.png')[0] == 409
        finally:
            backend.shutdown();backend.server_close()


if __name__ == '__main__':
    tests=[(name,value) for name,value in list(globals().items()) if name.startswith('test_') and callable(value)]
    for name,test in tests:
        print(name,flush=True);test()
    print(f'{len(tests)} web service tests passed',flush=True)
