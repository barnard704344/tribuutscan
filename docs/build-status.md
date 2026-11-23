# Build status

Documentation checkpoint: 23 November 2026. This describes implementation and
known validation gaps, not a hardware certification or release guarantee.

## Implemented in this fork

- Browser workspace and LAN pairing, including locally generated phone QR codes;
  capture and processing remain on the host.
- Coverage, Colour and IR on the left; Depth and distance guidance in the centre;
  tools grouped into Scan, Camera, Files and Host tabs on the right, with persistent
  scan actions. Desktop layout targets 1920×1080 and adapts to smaller screens.
- Subject selection for heads/free objects versus tabletop scans, with persistence.
- Classified coverage from the GPU volume, including paused host-rendered camera
  rotation/zoom and restoration of the live layout on resume.
- Saved-scan inspection, processing/downloads, continuation and safe idle host restart.
- Saved-result workspace with View, Align, Cleanup, Mesh and Export tabs, automatic
  editing copies, lasso-through deletion, crop/base tools and persistent edit undo/redo.
- Retained upstream calibration, tracking/recovery, optimisation, GPU fusion/meshing,
  CPU fallback and output pipeline. See [technical reference](technical-reference.md).

These are present in source. Their presence does not imply every lifecycle path,
scene or GPU has passed a fresh physical acceptance trial at this checkpoint.

### Saved-result workspace verification — 23 November 2026

Synthetic session checks covered editable copying, projection/axis conversion,
cleanup persistence, undo/redo, redo-branch replacement, failed-operation safety,
original-file preservation and source-recording links. Mesh checks verified that
deleted regions are excluded and that solid reconstruction crossing a deleted
region is not reported as a closed result. Capture continuation skips editing copies.

Browser checks covered all five tabs, embedded alignment, lasso selection/deletion,
automatic copy creation, undo/redo, mesh loading, display-mode persistence, disabled
unavailable exports, and desktop/laptop/phone widths. Reviewed screenshots use
synthetic geometry. Real scanner trials and broad GPU/large-session validation
remain outstanding; no dimensional-accuracy or throughput claim follows from this UI work.

### Web UI verification — 23 November 2026

The tabbed layout, QR pairing and UI cleanup passed 34 web-host/launcher checks
and the JavaScript UI checks, including tab keyboard navigation, persistent scan
actions, coverage pane swapping and QR rendering/cleanup. Browser layout checks
covered 1920×1080, 1366×768 and 390-pixel phone width, including paused desktop
layout. A rendered pairing QR was independently decoded to its intended link.

The [web UI tour](web-ui.md) includes reviewed screenshots from an isolated demo
session. This validates presentation and navigation, not a new hardware scan,
dimensional accuracy or sustained processing throughput.

## Latest checkpoint needing validation

The first browser saved-scan alignment stage supports matching-point fitting,
manual transform adjustments, undo/redo and a new combined full session. Synthetic
geometry/session and browser checks cover this path; a real scan trial is still
required. It currently accepts uncropped, single-piece free-object sessions with
matching calibration and range. Brush/lasso cleanup and automatic surface
refinement remain future work. See [alignment](alignment.md).

Six alignment regression checks and the 34 host/launcher checks passed. Browser
checks exercised actual point picking and landmark fitting, numeric nudges,
undo/redo, centring, a full-session save and opening the crop/processing result.
The alignment page was checked at desktop, laptop and phone widths. These tests
used synthetic sessions; they do not validate real-scene alignment quality.

Commit `906c7c8` added a rolling performance meter and a short recovered-tracking
indicator. The meter exposes processing FPS, average time and stage timing data;
the UI currently shows FPS and milliseconds. Stage timings are wall-clock samples,
not pure GPU timestamps. The recovery indicator reports a transition in existing
tracking; it does not introduce a new recovery algorithm.

Validate counters across start, pause, resume, loss/recovery and stop. Check the
indicator against actual accepted geometry rather than trusting its label alone.
These additions remain experimental until those checks are recorded.

## Performance target still open

The goal is sustained 20–30 processed fps with reliable tracking and growing
geometry. Camera delivery or preview at 28–30 fps does not establish this. A local
observation of roughly 22 processed fps was not a controlled acceptance benchmark.

A standalone NVIDIA benchmark attempt stalled in GPU timestamp readback. The
software-adapter run did not establish NVIDIA throughput. There is no completed
cross-vendor comparison or demonstrated sustained 30 processed fps result from
those attempts. Do not copy old upstream measurements into fork performance claims.

## Work required before claiming completion

1. Validate meter/recovery transitions with a real scan and retained diagnostics.
2. Obtain a repeatable hardware benchmark without the timestamp-readback stall,
   then compare changes using the same input and settings.
3. Demonstrate sustained live throughput alongside tracking acceptance and geometry
   growth on a representative head/object scan, including recovery and pause/resume.
4. Check saved output quality and dimensions against measured references.
5. Test additional GPU vendors/drivers before making broad compatibility claims.

Coverage cannot measure unseen surfaces. The host capture implementation remains
Linux-specific, and vendor-neutral GPU code does not make the whole application
a tested Windows/macOS port.

For operational problems use [troubleshooting](troubleshooting.md). Older audit and
upstream development notes are [archived](archive/README.md) as historical evidence.
