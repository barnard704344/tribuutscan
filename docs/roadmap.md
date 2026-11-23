# Saved-workspace roadmap

Priorities as of 23 November 2026. Items are proposed unless marked implemented;
this is not a release schedule. Current behaviour is documented in the
[saved-workspace guide](saved-workspace.md) and [build status](build-status.md).

The existing path is capture → open → align → clean up → mesh → export.
Keep these tools in the saved workspace, working on the currently opened session,
with processing on the host so a paired phone can use the same tools.

## Accepted-frame recovery: implemented, awaiting real-scan validation

The [frame editor](frame-editor.md) now supports accepted-frame visibility, range
exclusion/restoration, safe rebuilds, manual rigid section alignment and persistent
Undo/Redo. Next validate real interruptions and larger recordings; raw image
playback, re-tracking unposed frames and live GPU rewind remain future work.

## 1. Inspect and measure

Start with a movable section plane and two-point distance measurement in
millimetres. Let users inspect hidden surfaces and check a known dimension before
export. Make section viewing reversible without modifying the scan. Distinguish
displayed measurement precision from validated scanner accuracy.

Later, fit planes, circles and cylinders for useful engineering dimensions, with
fit residuals and clear indication of which points support each fit.

## 2. Finish cleanup

Previewed floating-island removal is implemented with editing-copy Undo/Redo; see
[mesh cleanup](mesh-cleanup.md). Extend the lasso/crop/base tools with plane cuts,
controlled smoothing, hole filling and mesh reduction. Preserve original sessions,
editing-copy history and the distinction between measured and reconstructed
surfaces. Mesh reduction should report geometric deviation, not just triangle count.

## 3. Alignment refinement: implemented, awaiting real-scan validation

Local surface refinement is now available after direct move/rotate or landmark
alignment, with an optional overlap box, sampled fit report and one-step undo.
See the [alignment guide](alignment.md#refine-the-overlapping-surfaces) for its
limits. Next, validate partial real scans and difficult/repeated surfaces before
extending its automatic registration behaviour.

## 4. Compare with a reference

Add a reference model and signed deviation colours, pinned readings and an
exportable report. Establish alignment, units and usable scan quality before
presenting tolerances. Wall-thickness analysis can follow once mesh orientation
and open-surface limitations are handled explicitly.

## 5. Portable inspection projects

The Scan Library now provides thumbnails, display names, search/filtering,
related versions, owned storage details and reversible Bin. See the
[library guide](scan-library.md). Next, save annotations, measurements and view
state with the session, and provide portable project packaging that accounts for
raw-recording dependencies.
A desktop [CAD handoff](cad-handoff.md) now downloads the selected surface for
manual modelling in ScanRuler. Returning projects/STEP into the library and native
STEP reverse engineering remain later work.
