# Developing this fork

The current product is the browser-controlled Linux scanner described in the
[README](../README.md). Keep documentation and reviews centred on that workflow.
The optional legacy desktop adapter is retained in source; it is not the default
interface or a reason to restore a separate live point-cloud pane.

## Environment and checks

```sh
uv sync --all-extras
.venv/bin/python -m tribuutscan.webapp --help
.venv/bin/python -m tribuutscan.webapp --demo
```

The shell launcher uses system Python; the scan worker uses the scanning
environment. See [installation](user-guide.md) for interpreter/dependency details.
Demo mode is useful for browser layout work, not hardware validation.

Run relevant plain-function checks with the repository runner; pytest is not
required. The runner imports test modules, which also supports spawned workers.

```sh
.venv/bin/python tests/run_checks.py test_webapp test_viewer_cli
node tests/test_scanner_ui.mjs
# Full regression run when warranted:
.venv/bin/python tests/run_checks.py
git diff --check
```

Node.js is needed for the JavaScript checks. Some hardware/GPU checks depend on
available adapters and libraries, and may return early without exercising a GPU.
A passing software suite does not certify scanner accuracy or real-scene recovery.
Choose checks for the changed paths and report what actually ran.

## Source map

| Area | Main sources |
| --- | --- |
| HTTP host, pairing, previews, restart | `tribuutscan/webapp.py` |
| Browser capture and saved-result pages | `tribuutscan/web/` |
| Shared capture controller and desktop adapter | `tribuutscan/viewer.py` |
| Scan worker and progress/performance messages | `tribuutscan/livescan.py` |
| Scanner tracking and point-cloud merge | `tribuutscan/cloud.py` |
| GPU operations and model tracking | `tribuutscan/gpu.py`, `tribuutscan/gputrack.py` |
| Frame capture and decoding | `tribuutscan/v4l2.py`, `tribuutscan/usbdepth.py`, `tribuutscan/depth.py` |
| Calibration and board utility | `tribuutscan/calib.py`, `tribuutscan/calibrate.py` |
| Optimisation, fusion and saved sessions | `tribuutscan/optimise.py`, `tribuutscan/fusion.py`, `tribuutscan/session.py` |

Confirm exact module boundaries in source before changing them. Host commands go
through the shared controller rather than manipulating capture concurrently from
request threads. Preserve pairing checks, request freshness checks and the safe
restart guards. Restart must wait until capture/recording/processing is idle.

Paused inspection must render the existing classified coverage volume from a
different camera. It must not replace coverage with a point-cloud view. Keep
inspection requests bounded and coalesced, and discard late replies after resume.

## Measuring performance

Measure camera delivery, processed/tracked frames and geometry acceptance
separately. Record commit, adapter/driver, scene, settings, warm-up, duration,
recording mode and competing workloads. Use repeatable saved input for comparisons,
then validate live behaviour with the scanner.

The rolling performance meter reports wall-clock timing around processing stages.
It is not a GPU timestamp profiler. Readbacks, scheduling and CPU orchestration can
be included in those timings; a displayed FPS value alone cannot locate a bottleneck.

The standalone `bakeoff` benchmark encountered a NVIDIA GPU timestamp readback hang
on the development host. A software-adapter run is not a valid substitute for a
hardware result. Do not advertise a completed hardware comparison or 30 fps based
on those attempts. See [build status](build-status.md) for outstanding acceptance work.

## Manual acceptance

After capture or lifecycle changes, exercise a real take: fresh start, sustained
overlapping movement, deliberate tracking loss and return, pause/rotate/zoom,
resume, save, processing and reopen. Check distance freshness and geometry growth
throughout. Exercise a paired phone, reconnect and an idle host restart when those
paths change. Confirm unsafe restart requests are refused while work is active.

For dimensional claims, scan a measured reference and record calibration, range,
surface and error. Voxel spacing and coverage percentages are not accuracy tests.

## Repository hygiene

Keep `captures/`, credentials, logs and private photos out of commits. Inspect any
new public example before adding it. Preserve MIT notices and the vendored Three.js
licence. Use the fork maintainer's intended Git identity, not the upstream author's
identity copied from old notes. Check `git status` before committing.

Update the relevant guide alongside behaviour changes. Historical notes belong
under [archive](archive/README.md), where their dates and limitations are explicit.
