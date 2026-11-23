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
node tests/test_saved_lasso_ui.mjs
node tests/test_library_ui.mjs
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
| Saved-scan manual alignment and full-session save | `tribuutscan/alignment.py`, `tribuutscan/web/alignment.*` |
| Bounded local surface refinement worker | `tribuutscan/alignment_refine.py` |
| Scan Library metadata, provenance, storage and thumbnails | `tribuutscan/scan_library.py`, `tribuutscan/web/library.*` |
| Editable copies, transactional history and cleanup masks | `tribuutscan/edit_session.py`, `tribuutscan/cleanup.py` |
| Shared capture controller and optional desktop adapter | `tribuutscan/controller.py`, `tribuutscan/viewer.py` |
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

The browser controls use accessible Scan/Camera/Files/Host tabs. Capture actions
stay outside the tab panels. Keep element IDs unique and avoid rebuilding camera
canvases when switching tabs or pausing. The bundled `web/qrcodegen.js` generates
pairing QR codes locally; preserve its MIT header when updating it.

For layout changes, check 1920×1080, a smaller laptop viewport and phone width,
including paused coverage and open tool lists. Update the illustrated
[web UI tour](web-ui.md) and [screenshots](images/README.md) using an isolated demo
session with example credentials, rather than publishing live pairing tokens.

Saved-result processing creates editable copies by default in the UI. Lasso
regions use viewer projection matrices but are evaluated against camera-world
millimetres in `Scanner.output_mask`, including fused mesh extraction. Keep that
coordinate conversion covered by tests. Pose optimisation is blocked after lasso
cleanup; alignment also requires an uncleaned source. Edited copies must not become
the capture controller's automatic continuation target.

History snapshots and temporary work sessions preserve relative source-recording
links by processing from sibling directories. Do not move processing into nested
history directories without rebasing those links. `.editing.json` and `.edit-history/`
belong to local ignored captures, not Git. Test with `test_edit_session` plus the
cloud, optimisation and fusion checks when changing these paths.

Lasso deletion filters saved PLY records directly and retains all point attributes;
it must not restore/remerge every source frame. Preserve raw archives and poses,
persist the world-space cleanup policy, retire stale mesh exports and publish
through the same staged history path. The pending browser preview must roll back
on a failed save and must not report completion before updated geometry renders.

Crop/clear on editing copies re-filter `cloud_state.npz`, not the already cropped
PLY, so expanding a box and Clear crop can recover hidden points. Preserve saved
crop axes, observation thresholds, base policy and lasso rules. Retain the slower
restore fallback for older sessions without fused state. Crops retire mesh exports;
they do not automatically launch a new mesh job. Test rotated axes, repeated
crop/expand/clear, history and unchanged frame archives with `test_edit_session`.

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

Alignment checks run with `.venv/bin/python tests/run_checks.py test_alignment`.
They exercise rigid transforms, landmark fits, viewer/camera coordinate conversion,
frame and recording-link preservation, unchanged originals, failed-save cleanup,
and authenticated endpoint behaviour. Use synthetic sessions to check browser
point picking, nudge/undo/redo, saving and opening the crop/processing result.

Also check automatic reference loading and second-source loading in the embedded
Align tab. Move drags use the current camera plane; rotation pivots around the
moving scan's centre. Keep the reference unchanged and each completed gesture to
one preview undo step. Escape, pointer cancellation, lost capture, window blur and
hiding the tab must revert an unfinished gesture. Test mouse and touch input,
tab-switch preservation, saved rigid transforms and unchanged source files.

Run the refinement checks with:

```sh
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 .venv/bin/python tests/run_checks.py test_alignment_refine
```

The host starts refinement in a separate
worker using the existing processing guard and optional memory scope. Keep its
structured result tied to the requesting job, with source-version checks before
and after work. Test partial overlap/noise, ambiguous patches, common world-space
selection after manual placement, rejection without a preview edit, and a single
undo step for an accepted proposal. Refinement is CPU/Open3D work and is separate
from the live GPU pipeline.

## Repository hygiene

Library mutations go through the controller queue with both host freshness and
per-entry revision guards. Display names and trash markers belong in the ignored
root catalogue; they must never rename capture folders. Trash is a reversible
visibility flag, not a physical move/delete: nested/shared recording references
must remain usable and automatic continuation must skip trashed entries. Thumbnails
use bounded PLY samples and paired routes. Run `test_scan_library`, then exercise
search/grouping, name edits, original/derivative trash, restore and open/Align/back
in the browser. Use only synthetic sessions for documentation screenshots.

Keep `captures/`, credentials, logs and private photos out of commits. Inspect any
new public example before adding it. Preserve MIT notices and the vendored Three.js
licence. Use the fork maintainer's intended Git identity, not the upstream author's
identity copied from old notes. Check `git status` before committing.

Update the relevant guide alongside behaviour changes. Historical notes belong
under [archive](archive/README.md), where their dates and limitations are explicit.
