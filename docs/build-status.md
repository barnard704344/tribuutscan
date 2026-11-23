# Build status

Documentation checkpoint: 23 November 2026. This describes implementation and
known validation gaps, not a hardware certification or release guarantee.

## Implemented in this fork

- Browser workspace and LAN pairing; capture and processing remain on the host.
- Coverage, Colour and IR on the left; Depth and distance guidance in the centre;
  auxiliary controls and collapsed connection details on the right.
- Subject selection for heads/free objects versus tabletop scans, with persistence.
- Classified coverage from the GPU volume, including paused host-rendered camera
  rotation/zoom and restoration of the live layout on resume.
- Saved-scan inspection, processing/downloads, continuation and safe idle host restart.
- Retained upstream calibration, tracking/recovery, optimisation, GPU fusion/meshing,
  CPU fallback and output pipeline. See [technical reference](technical-reference.md).

These are present in source. Their presence does not imply every lifecycle path,
scene or GPU has passed a fresh physical acceptance trial at this checkpoint.

## Latest checkpoint needing validation

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
