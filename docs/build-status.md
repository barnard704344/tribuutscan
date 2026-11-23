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
- Saved-result workspace with View, Frames, Align, Cleanup, Mesh and Export tabs, automatic
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

### Scan Library verification — 23 November 2026

The full-page Scan Library adds cached thumbnails, display names, search and
filters, related-version groups, storage details and reversible Bin. Folder IDs
and recording paths remain unchanged. Bin retains files and does not free space;
portable project packaging remains future work. Empty Bin permanently removes unused
scan folders after confirmation, while preserving dependencies of retained scans.

Sixty-seven Python checks across the library, web host, alignment, edit-session,
viewer and mesh-export modules passed. JavaScript checks covered library actions,
stale edits, capture controls and saved lasso behaviour. Browser checks covered
Files → Library → saved workspace → Align → Library, safe display names,
bin/restore with a usable dependent copy, thumbnails and desktop/laptop/phone
layouts. Documentation screenshots use synthetic geometry. A read-only catalogue
of the current 28-entry host library completed in about 0.09 seconds; this is a
local observation, not a large-library performance guarantee.

Empty Bin subsequently passed eight library/backend checks, the library JavaScript
checks and a browser trial using synthetic sessions. These covered dependency and
undo-history protection, complete-family deletion, stale confirmation rejection,
busy-host and pairing guards, and retention of shared raw recordings. The web-host
module's 32 checks also passed. User-facing language uses **Bin**.

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

## Export UI simplification — 24 November 2026

Removed the ScanRuler promotion/link section from Export and its download status
message. The shared STL action remains: use the existing printable STL or convert
the current surface to millimetres. Current guides and the Export screenshot now
reflect this layout; earlier handoff checkpoints below describe historical work.

## Frame loading correction — 24 November 2026

A real saved scan exposed six-decimal pose rounding that exceeded the strict
manual-transform validator. The saved-pose reader now accepts only the small
rotation error implied by that file precision, preserving the recorded values;
manual transforms keep strict rigid validation. Frame data errors are visible
above the timeline, and an unloaded backend displays restart guidance instead
of an empty embedded workspace. The current scan's 1,848 statuses, 1,692 accepted
poses and 231 keyframes were read without modifying scan files. Seven frame-editor
checks cover the rounded-rotation regression alongside prior selection/history
behaviour; browser verification uses the private scan without publishing its image.

## Accepted-frame editor — 23 November 2026

The saved workspace now has Frames: accepted-pose/status inspection, sampled
keyframe visibility, inclusive range exclusion/restoration, rebuilds from retained
contributions and manual rigid section correction through the existing alignment
controls. Editing copies, a preserved baseline archive and persistent Undo/Redo
protect originals. Future meshing consumes only retained corrected poses. Nested
recording links are rebased when copying sessions. See [frame editor](frame-editor.md).

Seventy-three checks passed across frame editing, editing history, alignment,
refinement, web host, islands, handoff and library. The browser flow exercised
exclusion, automatic copying, Undo, section-alignment handoff, mouse move/rotate
drags with preview undo, a numerical 10 mm
correction check and desktop/laptop/phone widths, including the embedded editor.
Screenshots use synthetic sessions. Raw image playback, re-tracking unposed
frames and live GPU rewind remain outside this implementation; real interrupted
scans and large-session performance need validation.

## Floating mesh islands — 23 November 2026

Cleanup now previews small edge-connected surface components in pink and removes
only the previewed size class, always retaining the largest component. Applying
uses an editable copy and persistent Undo/Redo. Source points/recordings are left
unchanged; stale printable/solid exports are retired. Rebuilding a mesh replaces
this mesh-only cleanup. See [mesh cleanup](mesh-cleanup.md).

Fifty-seven checks passed across islands, edit-session, webapp, mesh exports,
handoff and alignment refinement. A follow-up 41-check run covered the one-time
preview-result route and compact regular status payloads. JavaScript checks
covered preview rendering/cancellation/apply and stale selection, with lasso and
handoff regressions. The browser flow checked preview/cancel, automatic copying,
removal, a fresh component count, Undo/Redo and desktop/laptop/phone widths using
synthetic geometry. Very large-mesh performance and thresholds on real objects
remain to be validated.

## Desktop CAD handoff — 23 November 2026

A desktop-only ScanRuler section under Export uses the existing STL download
and a separate-tab link. When no saved STL exists, the same button exports the
current surface in millimetres. No additional CAD tab is needed. This is a manual file
handoff, with no agent installation, direct scan URL sharing or automatic CAD
project import. Source geometry is unchanged; rough/open surface exports are supported. ScanRuler remains a separate application.

The handoff, webapp and mesh-export modules passed 37 checks. JavaScript checks
covered handoff controls and saved lasso regressions. A browser trial downloaded
and parsed a synthetic surface STL and checked the panel, missing-mesh guard,
1920/1366-pixel desktop layouts and phone exclusion. A follow-up browser check
verified the shared STL button and the five-tab layout after consolidating Export. The screenshot uses synthetic
geometry. CAD reconstruction quality and third-party application behaviour are
not certified by these handoff checks. See [CAD handoff](cad-handoff.md).

## Tracking recovery update — 23 November 2026

Recovery now confirms local, wide and saved-continuation candidates before fusion,
rolls back interrupted candidates, excludes provisional saved poses, paces wider
searches and retains a more varied bounded feature-reference bank. Live telemetry
and the recovered indicator distinguish provisional matches from confirmation.
See [tracking recovery](tracking-recovery.md) for the state transitions, diagnostics
and physical scanner trial. These changes do not establish a measured recovery
success-rate improvement or sustained FPS target on hardware.

The recovery, cloud, livescan, track, gputrack and webapp modules passed 123
automated checks. New checks cover search pacing/fairness, reference diversity,
provisional GPU/CPU pose handling, rollback on loss/timestamp interruption/stop,
saved-pose exclusion and recovery status. A real CPU solve on generated depth
reacquired a repeated view after injected depth loss and resumed merging after
confirmation. This controlled repeated-view trial is not a physical motion,
symmetry or dimensional-accuracy acceptance result; adapter-dependent tests can
return without exercising unavailable adapters.

## Meter and hardware recovery validation still open

Commit `906c7c8` added a rolling performance meter and a short recovered-tracking
indicator. The meter exposes processing FPS, average time and stage timing data;
the UI currently shows FPS and milliseconds. Stage timings are wall-clock samples,
not pure GPU timestamps. The recovery indicator reports a transition in existing
tracking; that earlier indicator alone did not introduce a new recovery algorithm.
The confirmation and search/reference changes described above are subsequent work.

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
