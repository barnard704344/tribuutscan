# triBUUTSCAN — Rob's fork

Browser-controlled Linux scanning for the **Creality CR-Scan Ferret Pro**. Connect
the scanner to the host PC over USB, and control capture from that PC or a phone,
tablet or laptop on the same trusted network. Tracking, fusion and meshing run on
the host; the remote device receives previews and sends controls.

This is [barnard704344/tribuutscan](https://github.com/barnard704344/tribuutscan),
based on [gegirti/tribuutscan](https://github.com/gegirti/tribuutscan). This guide
describes this fork's browser build. Historical desktop screenshots, old speed
claims and upstream development notes are not instructions for the current UI.

## Start here

| Task | Documentation |
| --- | --- |
| Install, connect a phone and capture a scan | [User guide](docs/user-guide.md) |
| Fix connection, depth, tracking or startup problems | [Troubleshooting](docs/troubleshooting.md) |
| Understand the scanner, calibration, GPU pipeline and output files | [Technical reference](docs/technical-reference.md) |
| Develop, test and measure changes | [Development guide](docs/development.md) |
| Check completed work and unfinished work | [Build status](docs/build-status.md) |

## Install and launch

The supported host workflow is Linux with USB 3, access to the scanner's video
nodes, and Python. Python 3.10+ is declared by the package; the GPU extra needs
Python 3.11+. Use the same system interpreter when creating the worker environment.

```sh
git clone https://github.com/barnard704344/tribuutscan.git
cd tribuutscan
uv sync --all-extras
./tribuutscan-viewer.sh
```

The shell launcher uses system `python3`, which must have NumPy installed. If that
interpreter does not have NumPy, run the web host directly with the environment:

```sh
.venv/bin/python -m tribuutscan.webapp
```

For 14-bit USB depth, install the supplied device rule, reload it and reconnect the
scanner. The application can otherwise fall back to 10-bit V4L2 depth.

```sh
sudo cp udev/70-tribuutscan.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=2bc5
```

For a host without a desktop browser:

```sh
./tribuutscan-viewer.sh --no-browser
```

The service prints its local URL and pairing links. In the workspace, expand
**Host & connection** on the right and choose **Connect a phone** to pair another
browser. The default listener is `0.0.0.0:8765`. Pairing protects controls, previews
and downloads, but HTTP does not encrypt the connection. Use a trusted LAN; do not
forward this port directly onto the internet.

## This fork's workflow

- Coverage, Colour and IR previews sit on the left; Depth and its distance gauge
  occupy the centre. Auxiliary status and phone pairing are in the right column.
- Use **Head / free object** for a head or a held subject. Use **Object on a table**
  when support-plane detection is appropriate. The selected subject is remembered.
- **Pause & inspect** moves coverage to the centre. Drag to turn the coverage
  camera; scroll, pinch or use the buttons to zoom. The host renders the existing
  coverage classifications. There is no extra live point-cloud window.
- **Resume** restores the normal layout and capture. **Stop & Save** saves the
  take and starts optimisation; meshing follows if enabled.
- Saved scans can be processed, cropped and downloaded through **Inspect, process
  & download**. Files remain in this checkout's ignored `captures/` directory.

## Status and limits

GPU depth preparation, TSDF fusion, tracking against the fused model and meshing
are implemented through WebGPU. CPU processing paths are retained. This is a
vendor-neutral implementation, not a guarantee that every GPU or driver works.
The browser host and USB capture workflow are Linux-specific.

**Sustained 30 processed frames per second is still a target.** A local observation
was about 22 processed fps while the cameras supplied 30 fps; it was not a controlled
cross-hardware benchmark. The performance meter and recovery indicator are
checkpointed work that still need validation. See [build status](docs/build-status.md)
before treating displayed counters or historical benchmarks as acceptance results.

Coverage colours describe observed surfaces. They cannot measure unseen surfaces,
prove completeness or guarantee dimensional accuracy. A scan that loses tracking
can keep receiving camera frames without adding geometry.

## Origin and licence

This fork retains the upstream scanner protocol work, calibration support,
tracking/recovery, point-cloud merging, optimisation, fusion and export pipeline.
The original project was created by 3BUUT; see the upstream repository for its
history. Historical measurements apply to the hardware and revisions that produced
them, not automatically to this build.

The project is MIT licensed. The original copyright and permission notice remain
in [LICENSE](LICENSE). The vendored Three.js assets retain their own
[licence notice](tribuutscan/web/THREE-LICENSE). This independent project is not
endorsed by Creality or Orbbec.
