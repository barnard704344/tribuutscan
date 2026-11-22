# Web scanner audit — 22 November 2026

Reviewed the browser scanner introduced on `main` at `793c31a`: the controller,
HTTP gateway, private model/scan worker, saved-scan continuation, processing,
downloads and browser commands. This is a source and automated regression audit;
it does not certify measurement accuracy or replace a physical scanning trial.

## Changes already delivered

- The Applications launcher starts a browser workspace. The scanner, tracking,
  recording and GPU processing stay on the Linux host; paired phones and computers
  can control it over the local network.
- Capture logic lives in a shared controller; the optional desktop adapter remains.
- The workspace provides capture controls, saved scans, processing and downloads.
  Equal 16:9 Live 3D, Colour and IR previews sit on the left, the larger Depth view
  occupies the centre, and controls sit on the right. Small screens reorder these.
- Depth has distance guidance beneath it, with tracked-object readings, a centre
  fallback and an explicitly grey, aged last reading when current depth disappears.
- Head / free object mode avoids tabletop removal clipping a face or held object.
  Saved sessions and raw replay retain the subject mode. Table mode remains available.
- Camera previews fit without cropping; object picking accounts for letterboxing.

## Findings fixed in this audit

| Finding | Change and regression coverage |
| --- | --- |
| Shutdown could close the scan worker before an active processing job finished. | The service waits for processing before closing its controller. The worker stops accepting HTTP work and waits for processing before exit. A shutdown-order regression checks this sequence. |
| Processing checked the scan phase before acquiring its lock, and a new scan could change calibration during processing. | Phase is checked inside the processing lock; scan startup takes that lock before changing camera mode or replacing the scanner. A regression changes the phase while acquiring the lock. |
| Private loopback model and worker endpoints could bypass gateway pairing. | Both require a separate random bearer key supplied only by the host gateway and processing client. Tests reject missing/wrong keys and exercise the authenticated worker. The standalone viewer retains optional local access. |
| Standalone processing accepted browser form requests. | Processing requires the custom application header and rejects a conflicting Origin. The 3D client supplies the header; regressions cover both rejected and accepted requests. |
| A command from an earlier service instance could match a reused revision. | Commands carry both service instance and revision; the gateway and 3D processing client preserve them. Old or missing instances are rejected. |
| Changing selected scans during a download could mix metadata, filename and payload. | Each model request captures its selected source. A regression switches selection during the read and verifies the original scan is downloaded. |
| Continue could show the chosen subject mode instead of the saved take's actual mode; malformed metadata could raise an unhandled exception. | Continue restores the saved mode before worker startup and rejects unsupported modes or malformed session/camera metadata with an explanatory message. |
| Reopening a service bound to a specific LAN address used localhost. | The private launcher session records the local bind address and validates it when reopening. A regression covers explicit binding and private session permissions. |
| Environment HTTP proxies could receive private loopback RPC and its key. | Host-local requests use an explicit proxy-free opener. A local proxy trap regression verifies that these requests bypass proxy environment variables. |
| Scan names could break download headers, including through newlines or Unicode. | Download names use percent-encoded UTF-8 `filename*` headers. A regression checks Unicode and attempted header injection. |

## Validation

Run the Python checks from the checkout using the existing environment:

```sh
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 .venv/bin/python tests/run_checks.py
node tests/test_scanner_ui.mjs
```

The Python runner imports test modules normally so multiprocessing spawn targets
remain importable. It supports selected modules as arguments, requires no pytest,
and returns a failure exit status if any check fails.

The complete Python run executed 257 checks: 256 passed and one existing
packed-depth check failed. The failure reproduced unchanged on the previous
`main` (`793c31a`): the environment has no WebGPU tracking adapter, and the check
incorrectly required zero CPU depth decodes even when tracking fell back to CPU.
Its assertion now checks the actual tracker: zero full decodes for GPU tracking,
and CPU decoding for CPU fallback. Geometry, pose and keyframe comparisons remain.
The merge module was rerun after this correction: all six checks passed. The
other 251 checks passed in the complete run; application code was unchanged
between that run and this test-only correction.

The focused web/launcher/download run passed all 29 checks. Scanner UI logic and
the 3D viewer's JavaScript syntax checks passed, as did Python compilation and
`git diff --check`. GPU-specific checks that return early without an adapter do
not establish hardware GPU coverage in this environment.

## Operating limits and follow-up

- The web host uses plain HTTP and is intended for a trusted LAN. Pairing is access
  control, not transport encryption. Do not expose its port directly to the internet;
  use a VPN or an authenticated HTTPS proxy for remote access.
- Keep pairing links private. Internal bearer keys are separate from pairing codes
  and are not returned in browser state. Captures and private launcher sessions stay
  outside Git.
- A retained distance reading is historical. It does not prove current depth or
  successful tracking. Head mode prevents inappropriate table removal; it does not
  guarantee tracking on moving faces or surfaces outside the sensor's usable range.
- Browser logic and syntax were checked automatically. A rendered desktop/phone
  visual check was unavailable in this audit environment. No new physical scanner
  capture or measurement-accuracy trial was performed during this audit.
- The running scanner service was left undisturbed. Restart the application after
  finishing any current scan to load these changes.

## Follow-up: dedicated coverage pane

The top-left 16:9 pane shows the original flat, camera-aligned depth coverage
display, with Colour and IR below it. Live depth remains independent in the centre
with its distance gauge. Pause holds the coverage image and moves that same canvas
into the centre; depth moves left. Resume restores their positions.

The mistakenly introduced accumulated 3D point-cloud renderer, shared web model
state, orbit API, drag/zoom handlers, piece selector and view controls have been
removed from the browser workspace. The live workspace uses neither a 3D iframe nor a
sampled 3D projection during capture. The optional legacy desktop interface is unchanged. The
separate processing/download page remains available for saved scans.

Coverage is painted directly from the worker's existing 2D classification map,
without a point-cloud projection or live-depth background. Updates continue when
tracking is lost: the worker supplies its current map from the last tracked pose.
Only Pause freezes the image. Previously the web renderer rejected all updates
while tracking was lost, which could leave the first pink frame frozen even after
more geometry had been accepted. Pausing also accepts worker replies still in
flight before the paused acknowledgement. Status distinguishes an updating
coverage display from new geometry, which remains on hold while tracking is lost.
Missing classifications never substitute live depth. Stop/cancel clears coverage;
the current distance still comes independently from fresh depth.

Regression checks cover the flat renderer, current depth alongside held coverage,
tracking loss, pause, reset, rejection/removal of the 3D API and controls, paired
PNG access, and repeated browser pane moves. This follow-up does not change
tracking or reconstruction thresholds.

An already-running Python host must restart to expose the new coverage endpoint;
refreshing the browser only reloads the files on disk. The workspace now checks
the host's coverage capability and explains when a restart is required, rather
than repeatedly requesting an unsupported endpoint and showing a waiting message.
Save an active take before restarting the host.

The workspace now also provides **Restart scanner host**. Both the browser and
host reject restart during capture, countdown, pause, save, recording, processing
or a camera transition. The command goes through the paired, same-origin,
instance/revision-checked API. Once idle, the host closes its controller and HTTP
servers, releases the checkout lock and replaces its Python process. It preserves
listen options and pairing credentials for this restart only, so open browsers
reconnect without re-pairing or opening another browser window. The one-shot
credential handoff is consumed before workers start. Later manual starts generate
fresh credentials as before.

Restart tests cover rejection while busy and a full process replacement on a
copied demo checkout with temporary captures, verifying a new service instance,
the same pairing and port, and successful browser reconnection. The real scanner
and captures are not used by that integration test.
All 30 web/launcher checks and the scanner JavaScript checks passed after adding
the capability check and restart control.

The coverage image uses the centre view's depth resolution rather than stretching
a small sidebar thumbnail. No new physical capture or rendered browser visual
check was performed for this follow-up.

## Subject-mode regression found during live diagnosis

The earlier successful head takes recorded `mode: free`, while subsequent failed
takes recorded `mode: still`. Restarting the web host recreated its controller
with the table-mode default, discarding the user's subject selection. In the
saved 20:13 take, 8,552 of 9,028 frames were lost to tracking and only 50 were
merged; live diagnosis also showed geometry stopped while new camera frames
continued arriving. This is separate from whether the coverage PNG refreshes.

The web host now saves an explicitly selected subject in its ignored captures
directory, with private permissions and an atomic replacement. It restores that
choice before starting the controller on host restart or reconnect. Invalid
preferences are ignored. Continue still restores the saved take's actual mode.
The running host was set back to Head / free object for the user's head scan.
Tests cover preference validation, private permissions, persistence through the
settings command, and restoration before a replacement controller starts.

The coverage-update fix passed 88 targeted web/controller/worker/launcher checks.
The expanded colour regression additionally passed all 28 web checks: pink to
amber to blue across worker updates, updates during tracking loss, and freezing
only while paused. JavaScript checks and Python compilation also passed.


## Follow-up: move coverage while paused

Mouse/touch movement now changes the paused coverage renderer's camera. The
existing coverage canvas stays in the centre and displays newly rendered PNGs
with the volume's original classifications and weight threshold. No point-cloud
snapshot, browser geometry projection, iframe or second viewport is involved.
The previous snapshot-based inspection implementation and binary parser were
removed. Live capture continues using its existing 2D coverage feed.

The paired gateway forwards a bounded, validated camera request to the private
scan worker. Rotation and zoom orbit about the captured subject in the last
tracked camera's axes. The worker serialises rendering against capture and
processing, requires the paused phase and preserves the scanner's pose/reference.
Pixels representing only the original sensor frame are omitted after turning,
because those pixels do not belong to the rotated volume. The existing coverage
classification colours remain in use. GPU coverage is required, as for the live
classification map; there is no fallback to a different point-cloud viewer.

The browser keeps one request in flight, coalesces mouse movements to the latest
angle, supports drag/pinch/wheel/zoom/reset and aborts pending requests on Resume.
A late image cannot overwrite resumed live coverage. Tests cover the camera
transform and unchanged tracking state, classification preservation, authenticated
image access, query validation, coalescing, touch controls and Resume races.
This host-side addition requires a host restart before it can be used.
