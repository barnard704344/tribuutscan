# Technical reference

This describes the retained upstream scanner pipeline and this fork's web host.
It is not a claim that historical upstream measurements reproduce on every unit.

## Scanner and depth

The Ferret Pro used during development identifies as USB `2bc5:069c`, with a
CR-Scan Ferret product string. The application discovers separate UVC functions
for depth, colour and IR. The hardware capture path is Linux V4L2, with direct
USB access for the scanner's 14-bit stream and vendor functions.

Depth is a packed disparity-like value, not an integer distance in millimetres.
`depth.py` decodes Y10/Y14; `calib.py` supplies the camera model and depth law.
The Y14 path is normalised to the scale expected by the calibrated consumers.
The udev rule enables access; without it the application can use 10-bit V4L2.

Upstream observations put usable depth roughly at 15–33 cm in close range and
20–72 cm in long range. Surface, lighting, unit calibration and selected mode
matter. The UI's optimal bands are narrower than those observed limits.

## Calibration

The host reads the scanner's factory calibration when available, caching it under
`captures/factory.json`. A board calibration at `captures/calibration.json` takes
precedence. Without a usable calibration, the application uses estimates and
measurements must be treated accordingly. `TRIBUUTSCAN_CAMERA=board|factory|estimate`
selects a source explicitly for diagnostic workflows.

Board calibration is a separate local utility, not a browser wizard:

```sh
.venv/bin/python -m tribuutscan.calibrate board
.venv/bin/python -m tribuutscan.calibrate capture 24.0
# To solve an existing calibration capture:
.venv/bin/python -m tribuutscan.calibrate solve captures/calib/SESSION 24.0
```

`24.0` is an example measured square size in millimetres. Print the generated
`captures/calib/board.svg` at 100%, measure the actual square, and mount the sheet
flat on a rigid surface. Capture varied positions, distances and angles with most
of the board visible. The capture utility uses a local OpenCV window; press `q`
to finish. Review its report before adopting the calibration. Keep a backup of a
working calibration before replacing it. Old upstream accuracy results concern
its tested unit; calibration and dimensional accuracy need checking on your unit.

## Processing pipeline

1. Capture and time-pair depth/colour frames. Preview rate and camera rate are
   independent of the rate accepted by the scan worker.
2. Decode and prepare depth, select the subject and determine its mask. Table mode
   uses a support plane; free mode avoids that assumption.
3. Estimate the pose against the fused model. Marker, colour-feature and shape
   recovery paths help when local tracking fails; candidate poses are checked.
4. Fuse accepted views into the tracking volume. Merge selected views into the
   voxel point cloud alongside tracking. Not every tracked frame is a merged keyframe.
5. Save pose, mask, session and source information. Optimisation adds loop closures
   and adjusts the saved trajectory/geometry.
6. Fuse saved frames into a TSDF surface, extract a mesh, and produce closed-solid
   exports when possible. Insufficient or inconsistent data can prevent meshing.

GPU code is WGSL through wgpu/WebGPU. It implements depth/map preparation, volume
allocation/integration, raycasting, ICP reductions and marching cubes. The small
pose solve and parts of orchestration/recovery remain CPU work. Capture, recording,
I/O and process coordination are not a fully GPU-resident pipeline.

WebGPU offers vendor-neutral backends, but the overall USB application is not a
tested Windows/macOS scanner port. A working driver/adapter is required. CPU
fallbacks use Open3D and, where available, software WebGPU. Use `--force-cpu` to
select the CPU path deliberately. Startup, thermal state, raw recording, other
applications and scene complexity all affect throughput.

## Files and units

All captures and local state live beneath the checkout's ignored `captures/`.

| File or directory | Purpose |
| --- | --- |
| `*-scan/cloud.ply` | Merged point cloud, with colour and observation data where available |
| `*-scan/session.json` | Scan settings, camera/range identity, statistics and source references |
| `keyframes.npz`, `merged.npz`, `scene.json` | Supporting data for restore and processing |
| `frame_poses.txt`, `track.csv`, `labels.npz` | Pose history, tracking outcomes and frame masks when produced |
| `*-rec/` | Raw recording and metadata; retain with the scan that references it |
| `mesh.ply` | Coloured scanned surface, in metres |
| `mesh.stl` | Printable solid export, in millimetres, when successful |
| `solid.ply` | Coloured closed-solid export, in metres, when successful |
| `.cancelled/` | Recovery data from cancelled takes |
| `factory.json`, `calibration.json`, `calib/` | Local calibration data |
| `.webapp.json` | Private launcher/pairing session information |
| `.webapp-settings.json` | Persisted subject choice |
| `viewer.log` | Host log |

Cloud/mesh PLY coordinates use the viewer's axes: Y up and Z towards the scanner.
STL uses the same axes with millimetre scale. Always check scale and orientation
in the receiving CAD/slicer application. Do not delete source recordings merely
because a PLY exists: restore, optimisation, replay and meshing may need them.

## Useful offline tools

Run from the checkout with the scanning environment. These tools are additional
workflows; they do not replace the browser capture controls.

```sh
# Track/merge a raw recording, then optimise and mesh:
.venv/bin/python -m tribuutscan.cloud captures/SESSION-rec --optimise --mesh
# Fuse an existing saved session:
.venv/bin/python -m tribuutscan.fusion captures/SESSION-scan
# Register and merge two saved scans into a new output:
.venv/bin/python -m tribuutscan.session merge captures/A-scan captures/B-scan captures/AB-scan
# Synthetic GPU correctness/performance check, without the scanner:
.venv/bin/python -m tribuutscan.gpucheck --out /tmp/tribuutscan-gpucheck.json
```

The GPU check is a heavy diagnostic, not a prerequisite for ordinary capture. It
does not certify real-scene tracking or cross-vendor compatibility. See the
[development guide](development.md) for benchmark caveats.
