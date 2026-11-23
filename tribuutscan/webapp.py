"""Local scanner service and phone-friendly browser UI (no Tkinter required).

    python3 -m tribuutscan.webapp --host 0.0.0.0 --port 8765
"""
import argparse
from concurrent.futures import Future, TimeoutError
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

import numpy as np

from . import controller as core
from . import alignment, calib, frames, gpu, livescan, view3d
from .image import write_png

WEB = Path(__file__).parent / 'web'
# Host-local RPC must never send scan data or its internal key to an environment proxy.
urlopen = build_opener(ProxyHandler({})).open
ACTIONS = {'optimise', 'small', 'medium', 'large', 'mesh', 'base-off', 'base-on', 'crop', 'crop-off'}
SETTINGS = {'scene_mode': ('still', 'free'), 'preset': ('small', 'medium', 'large'), 'keep_raw': bool, 'remove_base': bool,
            'mesh_after': bool, 'countdown': bool, 'record_rate': tuple(core.RECORD_RATES),
            'hold_depth': bool,
            'find_markers': bool, 'imu_wanted': bool}


class Conflict(ValueError):
    """A stale or unavailable command; the client must refresh before trying again."""


def restore_subject(app):
    """Restore the user's chosen subject after a restart or scanner reconnect."""
    try:
        settings = json.loads((frames.CAPTURES / '.webapp-settings.json').read_text())
        subject = settings.get('scene_mode') if isinstance(settings, dict) else None
        if subject in ('still', 'free'):
            app.scene_mode.set(subject)
    except (OSError, ValueError):
        pass


def save_subject(subject):
    if subject not in ('still', 'free'):
        raise ValueError('Invalid subject mode')
    frames.CAPTURES.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', prefix='.webapp-settings-', dir=frames.CAPTURES,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({'scene_mode': subject}, stream)
            stream.close()
            os.replace(temporary, frames.CAPTURES / '.webapp-settings.json')
        finally:
            temporary.unlink(missing_ok=True)


def workspace_depth_renderer(phase=lambda: None):
    """Keep live depth separate from the flat, camera-aligned coverage display."""
    image, coverage_stats, rendered = None, {}, -float('inf')

    def render(cap, buf):
        nonlocal image, coverage_stats, rendered
        depth, stats = core.render_depth(cap, buf, preview=core.LIVE_DEPTH)
        now = time.monotonic()
        view = core.scan_view.copy()
        paused = phase() == 'paused'
        if not view['active'] and not paused:
            image, coverage_stats, rendered = None, {}, -float('inf')
        elif (not paused and view.get('model') is not None
                and 0 <= now-view.get('t', 0) < 1 and now-rendered >= 1/core.SCAN_SIDE_FPS):
            codes = view['model'][0]
            # These are already 2D worker classifications. In particular, lost
            # tracking supplies a fresh map of the scan from the last good pose.
            # Never gate it on current sensor depth or an accepted camera pose.
            rgb = core.fit(np.zeros((*codes.shape, 3), np.uint8), core.DEPTH_W)
            core.paint_model(rgb, codes)
            image = core.ppm(rgb)
            counts = core.visible_coverage(codes, None if view['lost'] else view.get('mask'))
            coverage_stats = {'coverage': core.coverage_caption(counts), 'updated_at': now}
            rendered = now
        stats.update(coverage_image=image, coverage_stats=coverage_stats)
        return depth, stats

    return render


def scan_directory(name):
    """Resolve only a saved scan directly inside this checkout's captures folder."""
    if not isinstance(name, str) or Path(name).name != name or not name.endswith('-scan'):
        raise ValueError('Choose a saved scan from the list')
    root = frames.CAPTURES.resolve()
    path = root / name
    if path.is_symlink() or path.resolve().parent != root or not (path / 'cloud.ply').is_file():
        raise ValueError('Saved scan not found')
    return path


def png_bytes(ppm):
    header, raw = ppm.split(b'\n', 1)
    magic, width, height, maximum = header.split()
    if magic != b'P6' or maximum != b'255':
        raise ValueError('Invalid preview')
    rgb = np.frombuffer(raw, np.uint8).reshape(int(height), int(width), 3)
    out = io.BytesIO()
    write_png(out, rgb)
    return out.getvalue()


class ModelSource:
    """A saved scan or an empty model, served privately behind the authenticated gateway."""
    def __init__(self, service, demo=False):
        self.service = service
        self.file = None
        self.empty = view3d.pack_arrays(np.empty((0, 3)))
        if demo:
            phi = np.linspace(0, 2 * np.pi, 120)
            theta = np.linspace(0, np.pi, 60)
            x, y = np.meshgrid(phi, theta)
            xyz = .07 * np.stack([np.sin(y) * np.cos(x), np.cos(y), np.sin(y) * np.sin(x)], -1).reshape(-1, 3)
            self.empty = view3d.pack_arrays(xyz, np.tile([83, 185, 255], (len(xyz), 1)))

    def process(self, action, box=None):
        return "Use the authenticated scanner workspace for processing"

    def for_request(self):
        """Keep every artifact in one HTTP request tied to the same selected scan."""
        snapshot = object.__new__(ModelSource)
        snapshot.service, snapshot.file, snapshot.empty = self.service, self.file, self.empty
        return snapshot

    def current(self):
        if self.file:
            version, payload, info = self.file.current()
            if self.service.processing:
                info['phase'] = 'optimising'
            return version, payload, info
        return 'empty', self.empty, dict(name='Demo model' if self.service.demo else 'Ready to scan',
                                         points=0, phase='idle', session={})

    def __getattr__(self, name):
        # FileSource's fixed artifact paths remain on the server; clients supply no filesystem paths.
        if self.file is not None:
            return getattr(self.file, name)
        raise AttributeError(name)


class WorkerProcessing:
    """Reuse the current worker's model and GPU allocation for processing that same scan."""
    def __init__(self, url, action, box, report, auth_token=None):
        self.finished = threading.Event()
        self.returncode = None
        self.report = report
        query = '' if box is None else '?box=' + ','.join(map(str, box))
        def run():
            try:
                request = Request(url + 'process/' + action + query, data=b'{}', method='POST',
                                  headers={'Content-Type': 'application/json', 'X-Tribuutscan': '1'})
                if auth_token is not None:
                    request.add_header('Authorization', 'Bearer ' + auth_token)
                with urlopen(request, timeout=3600) as response:
                    result = json.load(response)
                message = result.get('error') or result.get('message', 'Processing finished')
                self.returncode = 1 if message.startswith(('failed:', 'not now:')) or result.get('error') else 0
                report(message)
            except Exception as error:
                self.returncode = 1
                report(f'Processing failed: {error}')
            finally:
                self.finished.set()
        self.thread = threading.Thread(target=run, name='model-processing', daemon=True)
        self.thread.start()

    def poll(self):
        return self.returncode if self.finished.is_set() else None

    def wait(self):
        self.finished.wait()
        return self.returncode


class Service:
    """Single owner for scanner commands; browser connections never own the scanner's lifetime."""
    def __init__(self, demo=False):
        self.demo = demo
        self.alignment_output = None
        self.instance = secrets.token_hex(8)
        self.backend_token = secrets.token_urlsafe(32)
        self.controller = None
        self.commands = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.restart_requested = False
        self.lock = threading.Lock()
        self.snapshot = {}
        self.previews = {}
        self.preview_cache = {}
        self.preview_lock = threading.Lock()
        self.revision = 0
        self.last_phase = None
        self.error = 'Demo mode · scanner controls are disabled' if demo else 'Looking for the scanner…'
        self.retry_at = 0
        self.processing = None
        self.processing_thread = None
        self.processing_result = None
        self.model = ModelSource(self, demo)
        self.model_server = view3d.serve(self.model, 0, auth_token=self.backend_token)
        self.empty_url = f'http://127.0.0.1:{self.model_server.server_address[1]}/'
        self.model_url = self.empty_url
        self.next_scan_refresh = 0
        self.saved_scans = []
        self.publish()

    def start(self):
        self.thread = threading.Thread(target=self.run, name='scanner-controller', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        while self.thread.is_alive():
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                print("Waiting for scanner work to finish…", flush=True)
        self.model_server.shutdown()
        self.model_server.server_close()

    def run(self):
        try:
            while not self.stop.is_set():
                if self.controller is None and not self.demo and time.monotonic() >= self.retry_at:
                    try:
                        app = core.Controller()
                        self.controller = app  # make partial startup available to shutdown cleanup
                        app.worker_token = self.backend_token
                        app.depth.render = workspace_depth_renderer(lambda: app.scan_state)
                        restore_subject(app)
                        app.initialise_controls()
                        app.start()
                        self.error = ''
                        self.revision += 1
                    except (OSError, RuntimeError) as e:
                        if self.controller is not None:
                            self.controller.close()
                            self.controller = None
                        self.error = f'Scanner unavailable: {e}. Plug it in; retrying automatically.'
                        self.retry_at = time.monotonic() + core.RETRY_S
                try:
                    command, future = self.commands.get(timeout=.03)
                except queue.Empty:
                    pass
                else:
                    if future.set_running_or_notify_cancel():
                        try:
                            result = self.dispatch(command)
                            if self.controller:
                                self.error = ""
                            self.revision += 1
                            self.publish()
                            future.set_result(self.state() if result is None else result)
                        except Exception as e:
                            future.set_exception(e)
                if self.controller:
                    self.controller.tick()
                if self.processing and self.processing.poll() is not None:
                    if self.processing_thread:
                        self.processing_thread.join(timeout=1)
                    self.error = self.processing_result or f'Processing exited with code {self.processing.returncode}'
                    if self.alignment_output is not None:
                        if self.processing.returncode == 0 and (self.alignment_output / 'cloud.ply').is_file():
                            self.model.file = view3d.FileSource(self.alignment_output)
                            self.model_url = self.empty_url
                            self.next_scan_refresh = 0
                        self.alignment_output = None
                    self.processing = None
                    self.revision += 1
                    if self.model.file:
                        self.model.file.version = None
                self.publish()
        except Exception as e:
            self.error = f'Scanner service stopped: {e}. Restart the service.'
            self.publish()
            self.stop.set()
        finally:
            self.stop.set()
            if self.processing:
                print("Waiting for saved-scan processing to finish…", flush=True)
                self.processing.wait()
                if self.processing_thread:
                    self.processing_thread.join()
            if self.controller:
                self.controller.close()
            while not self.commands.empty():
                _, future = self.commands.get_nowait()
                if not future.done():
                    future.set_exception(Conflict('Scanner service stopped'))

    def state(self):
        with self.lock:
            return self.snapshot

    def publish(self):
        app = self.controller
        phase = app.scan_state if app else 'disconnected'
        if phase != self.last_phase:
            self.revision += 1
            self.last_phase = phase
        if time.monotonic() >= self.next_scan_refresh:
            self.saved_scans = sorted((p.name for p in frames.CAPTURES.glob('*-scan')
                                      if not p.is_symlink() and (p / 'cloud.ply').is_file()), reverse=True)
            self.next_scan_refresh = time.monotonic() + 2
        scans = self.saved_scans
        state = dict(instance=self.instance, revision=self.revision, phase=phase, connected=False, demo=self.demo,
                     message=self.error, settings={}, controls=[], scans=scans,
                     selected=self.model.file.ply.parent.name if self.model.file else None,
                     processing=self.processing is not None, distance='Waiting for scanner',
                     streams={}, progress=None, messages=[], can_separate=False, coverage_preview=True, coverage_rotation=True,
                     restart_supported=True, restarting=self.restart_requested, coverage_kind='flat')
        state['distance_gauge'] = core.distance_reading(None, {}, calib.CAMERA.mode,
                                                        calib.CAMERA.law_estimate, time.monotonic())
        previews = {}
        if app:
            blocked = app.lost or app.switching['busy']
            state.update(connected=not blocked, message=self.error or app.message,
                         settings={name: getattr(app, name).get() for name in SETTINGS},
                         ranges=list(core.RANGES), range=app.range_name(), auto_ir=app.ir_auto,
                         can_separate=app.can_start_separate_piece(), recording=app.rec['recorder'] is not None,
                         meshing_recording=app.job['process'] is not None,
                         imu=app.imu.state() if app.imu is not None else None,
                         progress=app.live['progress'].status() if phase == 'scanning' else None,
                         messages=[text for _, text in list(app.messages)],
                         controls=[dict(label=label, min=info['min'], max=info['max'], step=info['step'] or 1,
                                        value=int(setting.get())) for label, (_, _, info, setting) in app.controls.items()])
            reading = core.distance_reading(None if blocked else core.fresh_aim(),
                                            {} if blocked else app.depth.stats, calib.CAMERA.mode,
                                            calib.CAMERA.law_estimate, time.monotonic())
            state['distance_gauge'] = reading
            state['performance'] = app.live['progress'].cue.get('performance')
            state['distance'] = 'Scanner reconnecting or changing range…' if blocked else reading['text']
            for name, stream in [('colour', app.color), ('ir', app.ir), ('depth', app.depth)]:
                state['streams'][name] = dict(fps=round(stream.fps, 1), text=stream.stats.get('text', ''))
                if not blocked and stream.latest:
                    previews[name] = stream.latest
            reference = app.tracked_reference.image() if phase == 'scanning' and not blocked else None
            if reference:
                previews['reference'] = reference[0]
            state['reference'] = bool(reference)
            image = app.depth.stats.get('coverage_image')
            if image:
                previews['coverage-view'] = image
            if phase == 'paused':
                state['coverage'] = 'Paused · Last coverage view' if image else 'Paused · No coverage frame captured yet'
            elif core.scan_view['lost']:
                state['coverage'] = ('Tracking lost · New geometry on hold · Coverage from last tracked pose'
                                     if image else 'Tracking lost · Waiting for coverage')
            elif app.depth.stats.get('coverage_stats'):
                state['coverage'] = app.depth.stats['coverage_stats'].get('coverage', 'Waiting for coverage')
            else:
                state['coverage'] = 'Coverage is shown during a scan'
            if self.model.file is None:
                self.model_url = app.live['url'] or self.empty_url
        with self.lock:
            self.snapshot, self.previews = state, previews

    def preview(self, name):
        with self.lock:
            data = self.previews.get(name)
        if data is None:
            return None
        # At most one encoding of the same preview, even with several connected phones.
        with self.preview_lock:
            previous, encoded = self.preview_cache.get(name, (None, None))
            if previous is not data:
                encoded = png_bytes(data)
                self.preview_cache[name] = (data, encoded)
            return encoded

    def request(self, data):
        if self.stop.is_set():
            raise Conflict('Scanner service stopped')
        future = Future()
        try:
            self.commands.put_nowait((data, future))
        except queue.Full:
            raise Conflict('Too many pending commands; wait a moment')
        try:
            return future.result(timeout=20)
        except TimeoutError:
            future.cancel()  # an unstarted command must not execute after the client sees a timeout
            raise Conflict('Command is still busy; refresh its status before trying again')

    def dispatch(self, data):
        if self.restart_requested:
            raise Conflict('Scanner host is restarting')
        if data.get('instance') != self.instance:
            raise Conflict('The scanner service restarted. Refresh its status before trying again.')
        if type(data.get('revision')) is not int or data['revision'] != self.revision:
            raise Conflict('The scanner changed. Review its current status and try again.')
        action = data.get('action')
        if action == 'restart':
            app = self.controller
            if self.processing or (app and (app.scan_state not in ('idle', 'disconnected')
                    or app.rec['recorder'] is not None or app.job['process'] is not None
                    or app.switching['busy'])):
                raise Conflict('Finish and save the scan, recording and processing before restarting the host')
            self.restart_requested = True
            return
        app = self.controller
        if action == 'alignment-fit':
            return alignment.fit(data.get('reference_points'), data.get('moving_points'))
        if action == 'alignment-save':
            return self.save_alignment(data)
        if action == 'open':
            if app and (app.scan_state not in ('idle', 'disconnected') or app.rec['recorder'] or app.job['process']):
                raise Conflict('Finish scanning and recording before opening a saved scan')
            if self.processing:
                raise Conflict('Wait for processing to finish')
            self.model.file = view3d.FileSource(scan_directory(data.get('name')))
            self.model_url = self.empty_url
            return
        if action == 'live':
            if self.processing:
                raise Conflict('Wait for processing to finish')
            self.model.file = None
            self.model_url = app.live['url'] if app and app.live['url'] else self.empty_url
            return
        if action == 'process':
            return self.process(data)
        if action == 'retry' and app is None and not self.demo:
            self.retry_at = 0
            self.error = 'Looking for the scanner…'
            return
        if self.demo or app is None:
            raise Conflict('Connect the scanner to the host machine first')
        phase = app.scan_state
        available = not app.lost and not app.switching['busy']
        if self.processing or app.job['process'] is not None:
            raise Conflict('Wait for processing to finish')
        if action == 'settings':
            updates = data.get('settings')
            if not isinstance(updates, dict) or not updates or any(k not in SETTINGS for k in updates):
                raise ValueError('Unknown scanner setting')
            # Validate the whole request before making any changes.
            for name, value in updates.items():
                allowed = SETTINGS[name]
                if (allowed is bool and type(value) is not bool) or (allowed is not bool and value not in allowed):
                    raise ValueError(f'Invalid value for {name}')
                if name in ('scene_mode', 'preset', 'keep_raw', 'remove_base', 'mesh_after', 'countdown', 'record_rate') and (phase != 'idle' or app.rec['recorder']):
                    raise Conflict('Change capture settings before starting')
                if name == 'imu_wanted' and not available:
                    raise Conflict('Wait for the scanner to reconnect')
            if 'scene_mode' in updates:
                save_subject(updates['scene_mode'])
            for name, value in updates.items():
                getattr(app, name).set(value)
                callback = {'hold_depth': app.toggle_hold,
                            'find_markers': app.toggle_markers, 'imu_wanted': app.toggle_imu}.get(name)
                if callback:
                    callback()
            return
        if action == 'cancel' and phase == 'counting':
            app.cancel_countdown()
            return
        if action in ('stop', 'cancel') and phase in core.ACTIVE_SCAN_STATES:
            (app.toggle_scan if action == 'stop' else app.cancel_scan)()
            return
        if not available:
            if action == 'retry':
                app.retry_now()
                return
            raise Conflict('Wait for the scanner to reconnect')
        if action in ('start', 'continue'):
            if phase != 'idle' or app.rec['recorder']:
                raise Conflict('Finish the current capture before starting a scan')
            self.model.file = None
            (app.toggle_scan if action == 'start' else app.continue_scan)()
        elif (action == 'pause' and phase == 'scanning') or (action == 'resume' and phase == 'paused'):
            app.toggle_pause_scan()
        elif action == 'separate' and app.can_start_separate_piece():
            app.start_separate_piece()
        elif action == 'pick':
            kind, x, y = data.get('kind'), data.get('x'), data.get('y')
            if kind not in ('depth', 'colour') or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in (x, y)):
                raise ValueError('Invalid preview coordinates')
            width = core.DEPTH_W if kind == 'depth' else core.SIDE_W
            app.aim_click('Depth' if kind == 'depth' else 'Colour', x * width, y * (width * frames.H // frames.W))
        elif action == 'clear-aim':
            app.aim_clear()
        elif action in ('range', 'auto-ir', 'control', 'reset-controls'):
            if phase != 'idle' or app.rec['recorder']:
                raise Conflict('Stop capturing before changing scanner settings')
            if action == 'range':
                if data.get('value') not in core.RANGES:
                    raise ValueError('Unknown range')
                app.choose_range(data['value'])
            elif action == 'auto-ir':
                if type(data.get('value')) is not bool:
                    raise ValueError('Expected true or false')
                app.set_ir_auto(data['value'])
            elif action == 'control':
                if data.get('label') not in app.controls:
                    raise ValueError('Unknown control')
                app.change_control(data['label'], data.get('value'))
            else:
                for label, (_, _, info, _) in app.controls.items():
                    if label not in core.IR_LABELS:
                        app.change_control(label, info['default'])
                app.set_ir_auto(True)
        elif action == 'save-frame':
            app.save()
        elif action in ('record', 'stop-recording') and phase == 'idle':
            if bool(app.rec['recorder']) != (action == 'record'):
                app.toggle_record()
        elif action == 'mesh-recording' and phase == 'idle' and not app.rec['recorder']:
            app.mesh()
        elif action == 'retry':
            app.retry_now()
        else:
            raise Conflict(f'{action or "Command"} is unavailable while {phase}')

    def save_alignment(self, data):
        app = self.controller
        if self.demo or self.processing or app and (app.scan_state not in ('idle', 'disconnected')
                or app.rec['recorder'] or app.job['process'] or app.switching['busy']):
            raise Conflict('Finish capture and processing before saving alignment')
        a, b = scan_directory(data.get('reference')), scan_directory(data.get('moving'))
        if a == b:
            raise ValueError('Choose two different scans')
        versions = [alignment.version(a), alignment.version(b)]
        if data.get('versions') != versions:
            raise Conflict('A source scan changed; reload both scans')
        matrix = alignment.rigid(data.get('transform')).tolist()
        alignment.compatible(a, b)
        out = frames.CAPTURES / (time.strftime('%Y%m%d-%H%M%S') + '-aligned-' + secrets.token_hex(4) + '-scan')
        spec = dict(reference=str(a), moving=str(b), output=str(out), transform=matrix, versions=versions)
        executable = core.VENV_PYTHON if core.VENV_PYTHON.exists() else Path(sys.executable)
        process = subprocess.Popen([str(executable), '-m', 'tribuutscan.alignment'], cwd=core.REPO,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            process.stdin.write(json.dumps(spec))
            process.stdin.close()
        except Exception:
            process.terminate()
            process.wait()
            raise
        self.processing = process
        self.alignment_output = out
        self.processing_result = None
        def read():
            for line in process.stdout:
                if line.strip():
                    self.processing_result = line.strip()
        self.processing_thread = threading.Thread(target=read, daemon=True)
        self.processing_thread.start()
        return dict(output=out.name)

    def process(self, data):
        if self.demo:
            raise Conflict("Processing is disabled in demo mode")
        app = self.controller
        if self.processing or app and (app.scan_state != 'idle' or app.rec['recorder'] or app.job['process']):
            raise Conflict('Finish the current capture or processing job first')
        path = self.model.file.ply.parent if self.model.file else None
        if path is None:
            # The live worker owns its last scanner, but saved-file processing runs in a separate job.
            name = app.live['name'] if app else None
            path = scan_directory(name)
        action, box = data.get('operation'), data.get('box')
        if action not in ACTIONS:
            raise ValueError('Unknown processing action')
        if action == 'crop' and (not isinstance(box, list) or len(box) != 6 or
                any(type(v) not in (float, int) or not math.isfinite(v) for v in box) or
                any(box[i] >= box[i + 3] for i in range(3))):
            raise ValueError('Invalid crop box')
        live = app.live if app else {}
        if live.get('proc') is not None and live['proc'].is_alive():
            if live.get('name') == path.name and live.get('url'):
                self.processing_result = None
                self.processing_thread = None
                self.processing = WorkerProcessing(live['url'], action, box,
                    lambda message: setattr(self, 'processing_result', message), auth_token=self.backend_token)
                return
            # An unrelated finished take need not keep its GPU model resident while restoring another.
            live['commands'].put(('quit',))
            live['proc'].join(timeout=3)
            if live['proc'].is_alive():
                raise Conflict('The previous scan worker is still releasing its model; try again shortly')
            app.check_scan()
        executable = core.VENV_PYTHON if core.VENV_PYTHON.exists() else Path(os.sys.executable)
        args = [str(executable), '-m', 'tribuutscan.web_job', str(path), action]
        if box is not None:
            args += ['--box', json.dumps(box)]
        self.processing_result = None
        self.processing = subprocess.Popen(core.mesh_scope(lambda m: None) + args, cwd=core.REPO,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        process = self.processing
        def read():
            last = ''
            for line in process.stdout:
                last = line.strip() or last
                print(line, end='', flush=True)
            self.processing_result = last
        self.processing_thread = threading.Thread(target=read, daemon=True)
        self.processing_thread.start()
        self.model.file = view3d.FileSource(path)
        self.model_url = self.empty_url


class Handler(BaseHTTPRequestHandler):
    server_version = 'triBUUTSCAN'

    @property
    def service(self):
        return self.server.service

    def reply(self, body, content_type='application/json', status=200, headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, data, status=200, headers=None):
        self.reply(json.dumps(data, allow_nan=False).encode(), status=status, headers=headers)



    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/alignment', '/alignment.js', '/alignment.css'):
            if path == '/alignment' and not self.paired():
                return self.reply(b'', 'text/plain', status=303, headers={'Location':'/'})
            name = 'alignment.html' if path == '/alignment' else path[1:]
            kind = 'text/html; charset=utf-8' if path == '/alignment' else 'text/javascript' if path.endswith('.js') else 'text/css'
            return self.reply((WEB / name).read_bytes(), kind)
        if path in ('/', '/scanner.css', '/scanner.js', '/qrcodegen.js'):
            file, kind = {'/': ('scanner.html', 'text/html; charset=utf-8'),
                          '/scanner.css': ('scanner.css', 'text/css'),
                          '/scanner.js': ('scanner.js', 'text/javascript'),
                          '/qrcodegen.js': ('qrcodegen.js', 'text/javascript')}[path]
            return self.reply((WEB / file).read_bytes(), kind)
        if not self.paired():
            return self.reply(json.dumps({'error': 'Pair this browser first'}).encode(), status=401)
        if path == '/api/access':
            return self.reply(dict(token=self.server.token, urls=getattr(self.server, 'connection_urls', [])))
        if path == '/api/state':
            return self.reply(json.dumps(self.service.state(), allow_nan=False).encode())
        if path == '/api/alignment/cloud':
            try:
                if self.service.processing:
                    raise Conflict('Wait for processing to finish before loading scans')
                name = parse_qs(urlsplit(self.path).query).get('scan', [None])[0]
                directory = scan_directory(name)
                before = alignment.version(directory)
                rec = view3d.read_ply(directory / 'cloud.ply')
                if len(rec) > 200000:
                    rec = rec[::math.ceil(len(rec) / 200000)]
                if before != alignment.version(directory):
                    raise Conflict('Scan changed during loading; try again')
                return self.reply(view3d.pack_cloud(rec), 'application/octet-stream', headers={'X-Scan-Version':before})
            except Conflict as error:
                return self.json({'error':str(error)},409)
            except (ValueError, OSError, KeyError) as error:
                return self.json({'error':str(error)},400)
        if path.startswith('/preview/'):
            name = path.removeprefix('/preview/').removesuffix('.png')
            if path != f'/preview/{name}.png' or name not in ('depth', 'colour', 'ir', 'reference', 'coverage-view'):
                return self.reply(b'', status=404)
            image = self.service.preview(name)
            return self.reply(image or b'', 'image/png', status=200 if image else 204)
        if path.startswith('/model/'):
            return self.proxy_model()
        return self.reply(b'', status=404)

    def paired(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookie.get('tribuutscan')
            return value is not None and hmac.compare_digest(value.value, self.server.session)
        except (CookieError, ValueError, TypeError):
            return False

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Transfer encoding is not supported')
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 8192 or self.headers.get_content_type() != 'application/json':
            raise ValueError('Expected a small JSON request')
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        return data

    def do_POST(self):
        # Custom header cannot be supplied by a cross-origin form; no CORS is granted.
        if self.headers.get('X-Tribuutscan') != '1':
            return self.json({'error': 'Use the scanner interface'}, 403)
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self.json({'error': 'Cross-origin requests are disabled'}, 403)
        try:
            data = self.body()
            path = urlsplit(self.path).path
            if path == '/api/pair':
                token = data.get('token')
                if not isinstance(token, str) or not hmac.compare_digest(token, self.server.token):
                    return self.json({'error': 'Incorrect access code'}, 401)
                return self.json({'paired': True}, headers={'Set-Cookie':
                    f'tribuutscan={self.server.session}; HttpOnly; SameSite=Strict; Path=/'})
            if not self.paired():
                return self.json({'error': 'Pair this browser first'}, 401)
            if path == '/api/command':
                result = self.service.request(data)
                self.json(result)
                if data.get('action') == 'restart':
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path.startswith('/model/process/'):
                operation = path.rsplit('/', 1)[-1]
                # Existing model crop UI puts its box in the query string.
                box = parse_qs(urlsplit(self.path).query).get('box', [None])[0]
                if box is not None:
                    box = [float(v) for v in box.split(',')]
                self.service.request(dict(action='process', operation=operation, box=box,
                                          revision=data.get('revision'), instance=data.get('instance')))
                return self.json({'message': 'Processing started. You can keep this page open.'})
            self.json({'error': 'Not found'}, 404)
        except Conflict as e:
            self.json({'error': str(e)}, 409)
        except (ValueError, TypeError, KeyError) as e:
            self.json({'error': str(e)}, 400)
        except Exception as e:
            print(f'Web request failed: {e}', flush=True)
            self.json({'error': 'The operation failed; check the host console'}, 500)

    def proxy_model(self):
        relative = self.path[len('/model/'):]
        parsed = urlsplit(relative)
        allowed = {'', 'index.html', 'info.json', 'coverage.png', 'cloud.bin', 'cloud.ply', 'mesh.bin', 'mesh.ply',
                   'mesh.stl', 'solid.bin', 'solid.ply', 'three.module.min.js', 'OrbitControls.js'}
        if parsed.path not in allowed:
            return self.json({'error': 'Not found'}, 404)
        # No user-provided hostname or arbitrary backend path is forwarded.
        url = self.service.model_url + relative
        try:
            request = Request(url, headers={'Authorization': 'Bearer ' + self.service.backend_token})
            with urlopen(request, timeout=10) as response:
                body = response.read()
                kind = response.headers.get('Content-Type', 'application/octet-stream')
                headers = {}
                disposition = response.headers.get('Content-Disposition')
                if disposition:
                    headers['Content-Disposition'] = disposition
                return self.reply(body, kind, headers=headers)
        except HTTPError as e:
            return self.reply(b'', status=e.code)
        except (URLError, TimeoutError, OSError):
            return self.json({'error': 'The 3D worker is starting or unavailable; retrying shortly'}, 503)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def get_request(self):
        request, address = super().get_request()
        request.settimeout(15)
        return request, address


def reopen_browser(port=8765):
    """An Applications-menu launch should reopen the current host, not silently exit."""
    session = core.LOCK.with_name('.webapp.json')
    url = f'http://localhost:{port}/'
    try:
        data = json.loads(session.read_text())
        running_port, token = data['port'], data['token']
        host = data.get('host', 'localhost')
        if host != 'localhost':
            host = str(ipaddress.IPv4Address(host))
        if (type(running_port) is int and 1 <= running_port <= 65535 and isinstance(token, str)
                and 16 <= len(token) <= 128 and all(c.isascii() and (c.isalnum() or c in '-_') for c in token)):
            url = f'http://{host}:{running_port}/#token={token}'
    except (OSError, ValueError, KeyError, TypeError):
        pass  # an older running host has no session file; its browser may already be paired
    try:
        with urlopen(url.split('#', 1)[0], timeout=3) as response:
            if 'triBUUTSCAN' not in response.headers.get('Server', ''):
                return False
    except (OSError, URLError):
        return False
    return webbrowser.open(url)


def save_browser_session(port, token, host='localhost'):
    """Keep the pairing link private to the local user for later Applications-menu launches."""
    session = core.LOCK.with_name('.webapp.json')
    fd = os.open(session, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(dict(port=port, token=token, host=host), stream)


def host_credentials(port):
    """Consume credentials handed across an in-place restart, before spawning workers."""
    carried = os.environ.pop('_TRIBUUTSCAN_RESTART', '')
    if carried:
        try:
            data = json.loads(carried)
            token, session = data['token'], data['session']
            if (data['pid'] == os.getpid() and data['port'] == port
                    and isinstance(token, str) and len(token) == 32
                    and all(c.isascii() and (c.isalnum() or c in '-_') for c in token)
                    and isinstance(session, str) and len(session) == 64
                    and all(c in '0123456789abcdef' for c in session)):
                return token, session
        except (ValueError, KeyError, TypeError):
            pass
    return secrets.token_urlsafe(24), hashlib.sha256(secrets.token_bytes(32)).hexdigest()


def restart_host(args, port, token, session):
    """Replace the stopped host, preserving pairing and listen options without opening a browser."""
    environment = dict(os.environ)
    environment['_TRIBUUTSCAN_RESTART'] = json.dumps(dict(pid=os.getpid(), port=port, token=token, session=session))
    command = [sys.executable, '-m', 'tribuutscan.webapp', '--host', args.host,
               '--port', str(port), '--no-browser']
    if args.demo:
        command.append('--demo')
    if args.force_cpu:
        command.append('--force-cpu')
    os.execve(sys.executable, command, environment)


def main(argv=None):
    parser = argparse.ArgumentParser(description='triBUUTSCAN local scanner server and remote browser interface')
    parser.add_argument('--host', default='0.0.0.0', help='listen address (default: all network interfaces)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true', help='run without opening a browser on the host')
    parser.add_argument('--demo', action='store_true', help='preview the interface without opening scanner hardware')
    parser.add_argument(gpu.FORCE_CPU, action='store_true', help='track and mesh on the CPU')
    args = parser.parse_args(argv)
    gpu.force_cpu([gpu.FORCE_CPU] if args.force_cpu else [])
    frames.CAPTURES.mkdir(exist_ok=True)
    import fcntl
    with core.LOCK.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not args.no_browser and reopen_browser(args.port):
                return
            parser.exit(1, 'triBUUTSCAN is already running. Open http://localhost:' + str(args.port)
                        + '/ or close the desktop viewer.\n')
        service = Service(args.demo)
        try:
            server = Server((args.host, args.port), Handler)
        except OSError:
            service.model_server.shutdown()
            service.model_server.server_close()
            raise
        server.service = service
        port = server.server_address[1]
        server.token, server.session = host_credentials(port)
        address = server.server_address[0]
        local_host = 'localhost' if address in ('0.0.0.0', '127.0.0.1') else address
        local = f'http://{local_host}:{port}/#token={server.token}'
        save_browser_session(port, server.token, local_host)
        print(f'Open on this computer: {local}', flush=True)
        addresses = set()
        try:
            addresses.update(x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(('192.0.2.1', 9))  # choose the outbound interface without sending data
                addresses.add(sock.getsockname()[0])
        except OSError:
            pass
        server.connection_urls = []
        if args.host not in ('127.0.0.1', 'localhost'):
            if args.host != '0.0.0.0':
                addresses = {args.host}
            for address in sorted(addresses):
                if not address.startswith('127.'):
                    url = f'http://{address}:{port}/#token={server.token}'
                    server.connection_urls.append(url)
                    print(f'Open on your phone (same network): {url}', flush=True)
        print(f'Access code: {server.token}\nCtrl+C stops the host service. Closing a browser keeps it running.', flush=True)
        service.start()
        def shutdown(signum, frame):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        if not args.no_browser:
            webbrowser.open(local)
        try:
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            print('Closing scanner service; finishing accepted work…', flush=True)
            service.close()
            core.LOCK.with_name('.webapp.json').unlink(missing_ok=True)
    if service.restart_requested:
        restart_host(args, port, server.token, server.session)


if __name__ == '__main__':
    main()
