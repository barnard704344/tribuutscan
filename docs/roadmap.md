# Saved-workspace roadmap

Proposed order as of 23 November 2026. These are ideas, not implemented features
or a release schedule. Current behaviour is documented in the
[saved-workspace guide](saved-workspace.md) and [build status](build-status.md).

The existing path is capture → open → align → clean up → mesh → export.
Keep these tools in the saved workspace, working on the currently opened session,
with processing on the host so a paired phone can use the same tools.

## 1. Inspect and measure

Start with a movable section plane and two-point distance measurement in
millimetres. Let users inspect hidden surfaces and check a known dimension before
export. Make section viewing reversible without modifying the scan. Distinguish
displayed measurement precision from validated scanner accuracy.

Later, fit planes, circles and cylinders for useful engineering dimensions, with
fit residuals and clear indication of which points support each fit.

## 2. Finish cleanup

Extend the current lasso/crop/base tools with previewed island removal, plane cuts,
controlled smoothing, hole filling and mesh reduction. Preserve original sessions,
editing-copy history and the distinction between measured and reconstructed
surfaces. Mesh reduction should report geometric deviation, not just triangle count.

## 3. Refine alignment

Add optional surface registration after the existing direct move/rotate or
landmark alignment. Show overlap and fit quality, let users restrict the fitting
region and inspect the proposed result before applying it. Keep the manual result
recoverable through undo.

## 4. Compare with a reference

Add a reference model and signed deviation colours, pinned readings and an
exportable report. Establish alignment, units and usable scan quality before
presenting tolerances. Wall-thickness analysis can follow once mesh orientation
and open-surface limitations are handled explicitly.

## 5. Portable inspection projects

Save annotations, measurements and view state with the session, and provide a
portable project/export workflow that accounts for raw-recording dependencies.
Full STEP reverse engineering is a larger later project, beyond the first
inspection and cleanup tools.
