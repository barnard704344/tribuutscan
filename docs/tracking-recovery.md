# Tracking recovery

If tracking loses the subject, the app keeps the existing scan and tries to find
its place again. Return to a previously scanned area with clear depth, overlap,
texture, an edge or the same markers. Unseen surfaces cannot establish a match to
the saved model, and smooth/symmetric views can remain ambiguous.

## What happens automatically

- Local tracking and relocking try to recover near the last trusted view.
- On the GPU path, wider recovery rotates between available object-feature,
  scene-feature and shape searches. At most one wide method is selected every
  0.2 seconds of acquisition time, rather than stacking all methods on each lost
  frame. No-timestamp inputs use a frame-count cadence. Individual methods retain
  their existing geometric, marker, overlap and reachability checks.
- Up to 16 confirmed object-feature views are retained in memory. Nearby views
  share a slot; a substantially richer feature set may replace it. Slow motion
  must move beyond an anchor's neighbourhood before creating a new one, so the
  reference cannot simply slide along with the camera. The bounded bank can still
  evict old views during a long scan. It is reset for a separate piece.
- A recovered pose is provisional, including local recovery, recovery after a
  long input gap and continuation of a saved scan. Two subsequent consecutive
  tracked frames must confirm it. The candidate and confirmation frames do not
  fuse into the live volume or merge into the point cloud.
- Failed tracking, missing depth, repeated/backwards timestamps or an input gap
  over 0.5 seconds during confirmation discard the candidate and restore the last
  trusted pose, scene, marker and reference state. Stopping/saving while a candidate
  is pending also discards it. Pending poses are excluded from saved frame poses.

The confirmation rule also applies to CPU tracking. The wider GPU recovery
methods and classified volume coverage are not available on the CPU path.
Normal locked tracking keeps its existing acceptance checks and fusion cadence.

## Reading the live status

**Lost** means geometry is on hold. **Confirming recovered position** means a
candidate is being checked: hold a steady, recognisable overlapping view. The
recovered indicator appears after confirmation, rather than at the first candidate.
Geometry can still remain steady because a view has not added new surface or the
point-cloud merge cadence has not selected another keyframe.

The existing model is retained throughout recovery. No automatic separate piece
is started. Use the explicit separate-piece action only when intended.

## Diagnostics and validation

Saved `session.json` records `recovery_searches`, `recovery_candidates`,
`recovery_confirmed` and `recovery_rejected` when produced. Rejections include a
pending candidate discarded at stop/save. `track.csv` identifies rolled-back
frames as `taken back`; interrupted timestamps are recorded as
`confirmation interrupted`. The older `relocked`/`shape_refinds` counters retain
legacy attempt semantics and should not be read as confirmed-recovery counts.
Reference images/descriptors and the search scheduler are held in memory, not
added to a saved session as a new portable database.

Automated checks cover pacing, reference diversity, GPU/CPU confirmation paths,
rollback, saved-pose exclusion, outward status and a real CPU solve on generated
depth with an injected loss. They do not establish improved success rates or FPS
on a physical scanner. For a scanner trial:

1. Scan a recognisable area, briefly point away, then return to the same area.
2. Briefly obscure depth, uncover it and hold an overlapping view through
   confirmation. Repeat with a pause/resume.
3. Try a new angle that retains overlap. Check that coverage grows after recovery
   and that the saved result has no doubled surface or pose jump.

Keep the recording and diagnostics for comparison. Head movement, missing depth,
symmetry and little overlap can still prevent reliable recovery. See
[build status](build-status.md) for remaining hardware/performance validation.
