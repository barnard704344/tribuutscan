# Aligning saved scans

Open **Files → Align saved scans** in the capture workspace. This is a separate
saved-scan tool; it does not replace the live coverage pane or its paused camera.
You can use it without the scanner connected, with the scanning environment installed.

![Saved-scan alignment workspace with synthetic test geometry](images/web-alignment.png)

*Browser screenshot using synthetic point-grid sessions, not a physical scan.*

## First-release limits

Saving currently requires two **uncropped, single-piece Head / free object**
sessions with the same saved camera calibration fingerprint and depth range.
Keep `session.json`, keyframes, merged-frame data and scene data with each scan.
Keep the referenced raw recordings for later meshing. A bare PLY is not a full
processable session. Cropped scans, tabletop sessions and multi-piece sessions
are rejected for now, rather than merging their incompatible masks or scene regions.

## Align and inspect

1. Choose the **Reference** and **Moving** scans and press **Load previews**.
   Reference is blue; Moving is amber. Preview downloads are limited to 200,000
   points per scan; saving uses the full saved frame data.
2. Drag to orbit, right-drag to pan and scroll to zoom. **Fit view** frames both
   scans. The **Preview** selector can show either scan on its own.
3. Use **Pick reference** to select at least three distinctive, spread-out
   features. Select the same features in the same order with **Pick moving**.
   Only the selected scan is visible while picking. Click a pick button again
   to finish picking. Orbiting remains available; a drag does not select a point.
4. Press **Align point pairs**. Three to twelve pairs are supported. Collinear
   or coincident selections are rejected. The reported RMS error measures only
   the selected point pairs; it does not establish whole-surface accuracy.
5. Inspect the overlap from several angles. Use **Nudge position and rotation**
   for adjustments in millimetres and degrees. Rotation is around the moving
   scan's centre. Leaving a numeric field commits that adjustment and resets the
   field to zero for the next nudge. **Undo**, **Redo** and **Reset** affect the
   preview transform, not the saved originals. **Centre together** is a starting
   aid, not automatic surface registration.

Clear and repick landmarks if a point was selected incorrectly. Changing either
source selection clears the preview; load the pair again before editing.

## Save and clean up

Press **Save aligned copy** after inspecting the alignment. Saving requires
capture, recording and other processing to be idle. A separate host job creates
a new `*-aligned-*-scan` directory. Both original sessions remain unchanged.
The saved result retains transformed frame poses, masks, keyframes and references
to source recordings, rather than exporting only the displayed point cloud.
An `alignment.json` file records the source names, transform and source versions.

Saving does not run pose optimisation: that could alter the alignment you just
approved. It also does not claim that the scans overlap sufficiently. If a source
changed since loading, reload it before saving. A failed job does not publish a
partially written result.

Choose **Inspect result, crop & process** when saving finishes. The existing saved
result page provides the crop box, mesh generation and exports. Choose **Make mesh**
there when ready; source recordings and compatible calibration must still be available.

This is the first alignment stage. Brush/lasso deletion, isolated-fragment cleanup,
automatic surface refinement and mesh-repair editing are not implemented here yet.
The existing crop box is the available manual cleanup tool.

The initial implementation was checked with synthetic sessions and browser
interactions. Real head scans still need a physical validation run.
