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
- Integrated alignment loads the opened scan as reference and a selected second
  scan automatically. Mouse/touch move and rotate tools adjust the moving scan
  directly, with preview undo/redo, optional landmark fitting and full-session save.
- Local surface refinement after rough positioning, with optional overlap-box
  selection, sampled fit statistics and one-step preview undo. A separate CPU
  worker preserves UI responsiveness and shares the idle-processing guards.
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

## Alignment verification and remaining trial

Alignment accepts uncropped, single-piece free-object sessions with matching
calibration and range. Align before lasso cleanup, which is already implemented
in the saved workspace. Brush tools and automatic surface refinement remain future
work. See [alignment](alignment.md) for the full input restrictions.

Recorded checks across the alignment changes covered rigid transforms, landmark
fitting, frame/source-link preservation, unchanged originals and failed saves.
Browser checks exercised automatic source loading, point picking, numeric nudges,
direct mouse movement/rotation, one undo step per completed drag, Escape and touch
cancellation, saving and reopening the result. Tab switching preserved the pair.
Desktop, laptop and phone layouts were checked using synthetic sessions. The
initial real-data trial below extends that evidence; broader real-scene alignment
quality and large-session coverage still need further validation.

Surface-refinement checks cover known rigid offsets, noise/partial overlap,
outliers, world-space selection, ambiguous planar patches, invalid input,
unchanged sources, stale versions and asynchronous authenticated job results.
Browser checks exercised rejected empty selection, mouse/touch overlap selection,
touch cancellation, a successful proposal, single-step undo/redo and saving the
recovered transform to a new full session. The 54 targeted Python checks and
capture JavaScript checks passed; desktop/laptop/phone layouts were checked.
The synthetic trial reduced sampled nearest-match RMS from about 1.99 mm to
0.03 mm and recovered its known 2° offset. This does not establish scanner accuracy
or real-scene registration quality.

### Initial real-data refinement trial — 23 November 2026

A newly captured head scan (918,612 points after optimisation) was used for
controlled-offset tests. Separate random point subsets were given known 2° and
5° rigid offsets. Refinement recovered the geometry to within about 0.04 mm at
the 95th percentile. Both subsets come from the same fused surface, so this
checks transform recovery on real geometry, not scanner dimensional accuracy or
independent-capture agreement.

The browser/host workflow also refined two independent saved head captures
(approximately 0.92 million and 2.29 million points) after rough positioning.
Sampled nearest-match RMS fell from 12.22 mm to 5.37 mm, and moving-point overlap
within a 20 mm cutoff increased from 59% to 81%. Preview Undo/Redo passed; original
sessions were unchanged and no combined output was published. A separate
face-region check at a 10 mm cutoff refined 2.12 mm RMS to 2.06 mm. These checks
illustrate why the shared region matters: hair, neck/shoulders, pose and capture
coverage can differ between sessions. Residuals still require inspection.

Tests used the existing CPU worker's 30,000-point sampling cap per scan. A poor
initial placement was refused by the large-correction guard. Real-data UI checks
were completed in an isolated host to avoid interfering with concurrent editing.
Private scan images and recordings are not included in documentation or Git.

### Lasso responsiveness fix — 23 November 2026

Each deletion previously restored/remerged the entire session before saving. In
a private browser trial on a 916,018-point session with 380 saved views, the old
model remained visible after two minutes. Deletion now filters the saved PLY
records directly, retaining point attributes, poses and source archives. The
saved policy continues to filter later regenerated meshes.

The same 253,038-point selection was removed in roughly 3–7 seconds in local
repeat checks, including automatic copy creation and updated geometry display.
Undo restored 916,018 points and Redo restored the 662,980-point cleaned result.
Original captures were unchanged. These timings describe those local trials,
not a general latency guarantee. Pending deletion now hides selected points
immediately, restores them if saving fails, and retains the operation's status
until the saved geometry reloads. Pointer cancellation is handled explicitly.

The 55 targeted Python checks and the capture/lasso JavaScript checks passed,
including preserved PLY attributes and archives, empty-selection rejection,
preview rollback and waiting for the rendered saved result.

### Crop responsiveness fix — 23 November 2026

Crop/clear on current editing copies now re-filter the saved fused cloud instead
of rebuilding every source view. Expanding or clearing a box can recover hidden
points while retaining lasso/base policy. Old exports are retired; generating a
replacement mesh is a separate action. Older sessions without fused state retain
a one-time frame-restore fallback, also without automatic meshing.

A private copy of the reported real scan reproduced the exact 807,546-point crop
in about 13 seconds. Clear crop restored 914,614 points in about 14 seconds while
retaining both lasso regions. Browser Undo/Redo restored both counts. Timings
include saving/history and viewport reload on that host; original scans were
unchanged. The UI waits for updated geometry and Clear crop removes active preview
clipping as well. The 61 targeted Python checks and capture/saved-workspace
JavaScript checks passed, including rotated axes, crop expansion/clear, archive
preservation, mesh retirement, invalid bounds and legacy fallback.

This checkpoint updates the tools, workflow documentation and screenshots; it does
not add a fresh hardware or performance acceptance result. Proposed inspection
and cleanup additions are tracked separately in the [roadmap](roadmap.md).

## Meter and recovery validation still open

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
