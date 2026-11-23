# Using this fork

This guide describes the browser build in this repository. For installation and
licensing, start with the [README](../README.md).

## Host setup

Connect the Ferret Pro to a USB 3 port on the Linux host. The host needs access to
its video devices and, for direct USB depth and IMU access, the supplied udev rule.
Device node numbers can change; discovery is by USB interface rather than a fixed
`/dev/video0` path.

Run setup commands from the cloned repository, not from `~`. Use `cd ~/tribuutscan`
for the default clone location, or `cd` to your existing checkout if installed
elsewhere. Follow the [USB rule installation steps](../README.md#install-and-launch)
there; `udev/70-tribuutscan.rules` is relative to the checkout. If copying the rule
fails with `cannot stat`, change to the correct directory and retry the copy
before reloading rules.

Create the scanning environment with `uv sync --all-extras`. The package's base
dependency is NumPy; the scanning extra adds Open3D and OpenCV, and the GPU extra
adds wgpu on supported Python versions. The legacy desktop UI additionally needs
Tkinter. No scanner driver is installed on a phone or remote browser.

Without uv, an alternative environment setup is:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[scan,gpu]"
.venv/bin/python -m tribuutscan.webapp
```

Use one checkout and one host process for the scanner. Its lock prevents competing
instances within that checkout; separate checkouts can still contend for the USB
device. Starting the launcher again normally reopens the existing host's browser.

## Launch options

Run these from the checkout:

```sh
./tribuutscan-viewer.sh                         # browser and host
./tribuutscan-viewer.sh --no-browser            # host only
./tribuutscan-viewer.sh --host 127.0.0.1         # local access only
./tribuutscan-viewer.sh --port 8877              # alternative port
./tribuutscan-viewer.sh --force-cpu              # CPU tracking/meshing paths
./tribuutscan-viewer.sh --demo                   # UI preview without scanner capture
.venv/bin/python -m tribuutscan.webapp --help
```

The launcher calls system Python. Running `.venv/bin/python -m tribuutscan.webapp`
accepts the same options and uses the environment explicitly.

## Connect another device

1. Keep the scanner connected to the host, and connect both devices to the same
   trusted network.
2. Open the **Host** tab in the right column and choose **Connect a phone**.
3. Scan the displayed QR code with the phone camera and open its link to pair
   automatically. If several addresses appear, use one reachable from the phone.
   You can also open a pairing link manually, or open the host's LAN address and
   enter the access code. Use the host's address, not `localhost`, on a phone.
4. Allow the selected TCP port through the host firewall for the local network.

QR codes are generated locally; the pairing link is not sent to a QR service.
Keep QR codes and pairing links private. A manual host start generates new
credentials. An in-app restart preserves pairing so existing browsers can reconnect. The camera,
GPU and files stay on the host; downloads are saved by the device running the browser.

The service uses plain HTTP. For access outside the LAN, use a VPN or an
authenticated HTTPS reverse proxy. If using a proxy, preserve the request Host
and Origin relationship used by the command gateway.

## Workspace and tools

See the [illustrated web UI tour](web-ui.md) for desktop, camera tools, QR pairing
and phone screenshots.

The desktop workspace fills the available screen up to 1920 pixels wide. Coverage,
Colour and IR stay on the left, with Depth in the centre. On desktop, longer tool
lists scroll inside the right panel so they do not push the scan views off screen.
Phones use a stacked layout.

| Tab | Tools |
| --- | --- |
| Scan | Subject, spacing, capture options, continuation and separate pieces |
| Camera | Depth range, exposure, hardware controls, IMU and markers |
| Files | Saved scans, processing/downloads and raw recording |
| Host | Connection, phone QR pairing, restart and host messages |

Start/stop, pause/resume, cancel and current status remain visible above the tab
contents. Changing tabs does not stop capture. Keyboard users can move between
tabs with Left/Right arrows, Home and End.

The **Host** tab contains the single collapsed **Messages from the host** log.
Performance appears beside the current phase above the capture buttons rather
than occupying a separate panel. The top banner and bottom branding footer have
been removed to leave more space for the scan views.

## Capture a head or object

1. Select the correct **Subject** before starting. **Head / free object** skips
   support-plane removal. **Object on a table** retains support-plane detection.
   A broad part of a head can be incorrectly removed in table mode.
2. Select a range, then bring the subject into reliable live Depth. Close range's
   guidance target is 18–30 cm; long range's is 30–50 cm. These are working guidance
   bands, not a calibration guarantee or a claim that every surface gives depth.
3. Pick the subject in Depth or Colour if automatic selection is wrong. Reset aim
   clears that selection.
4. Choose spacing: Fine 0.5 mm, Medium 1 mm or Coarse 2 mm. Spacing is a voxel/cloud
   sampling choice, not the scanner's measurement accuracy.
5. Choose **Keep raw frames** if replay and diagnosis will be needed. Enable or
   disable automatic meshing according to the time available after capture.
6. Start the scan, allow GPU preparation to finish, and move with overlap between
   views. Watch **Tracking** and **Geometry**, as well as the images.

The subject choice persists across host restarts and reconnects. **Continue latest
saved scan** uses that take's saved subject mode and checks its camera/range; use a
new take when deliberately changing subject mode.

## Read distance and coverage

The distance gauge stays below Depth. It uses current picked/tracked-object depth
where reliable, with a centre reading as fallback. A grey **Last** marker is an
aged historical reading. It is not current distance or evidence of good tracking.

| Coverage appearance | Meaning |
| --- | --- |
| Blue, with surface shading | The model has sufficient observations under the current coverage threshold |
| Yellow/amber, patterned | More observations may help |
| Pink, dotted | The sensor sees surface not yet represented by a model hit |
| Unclassified/background | No classification; it cannot automatically be counted as an unscanned hole |

Percentages concern classified cells in the visible object, not the whole subject.
Neither a high percentage nor a high camera FPS proves that a scan is complete.
The three-colour coverage map and movable coverage camera require GPU tracking;
CPU capture/tracking does not provide the same volume-classification display.

If tracking is lost, the flat coverage map can still refresh from the last tracked
pose while geometry remains on hold. Return to an already scanned area with clear
depth, overlap and recognisable shape/texture. The scanner attempts recovery
automatically. The **Last tracked view** provides a reference when available.

## Pause and inspect

After **Pause & inspect** finishes draining accepted frames, coverage moves into
the centre and Depth moves left. Drag coverage to turn its camera around the
captured subject. Scroll, pinch or use +/− to zoom; **Reset view** returns to the
starting coverage camera. The existing volume classifications are rendered on the
host and returned as images. There is no replacement point-cloud viewport.

**Resume** cancels pending inspection requests, restores live coverage to the left,
and restores Depth to the centre. Inspection changes do not change the scan's
tracking pose. Keep overlap with the last tracked view when resuming.

## Finish, cancel or continue

- **Stop & Save** saves the take, then optimises it. If **Mesh after Stop** is enabled,
  it also fuses and meshes the saved frames. Wait for completion before restarting.
- **Cancel take** confirms before discarding a finished-take result. Raw/recovery
  files may remain under `captures/.cancelled/`; earlier saved scans remain.
- **Continue latest saved scan** needs the take's supporting session/recording data,
  compatible calibration and the original range. It must locate itself again.
- **Start separate piece** is for deliberately disconnected areas that cannot
  overlap the current piece. Pieces are not guaranteed to join successfully.

Closing a browser does not stop capture. **Restart scanner host** reloads Python
changes only after scan, recording and processing work has finished. A browser
refresh reloads HTML/CSS/JavaScript; it does not reload the host's Python code.

## Inspect and export saved results

To combine two saved free-object scans, use **Files → Open scan → Align**. See the
[alignment guide](alignment.md) for matching points, move/rotate, undo/redo and
saving a separate full session. This first stage has explicit input restrictions;
the saved-result workspace provides lasso cleanup after alignment.

In the **Files** tab, choose a take under **Saved scans** and choose **Open scan**.
The result viewer opens in the current tab after the host accepts the selection.
Use **← Capture** there to return to capture.
The saved-result workspace groups **View**, **Align**, **Cleanup**, **Mesh** and
**Export** tools. See the [saved-workspace guide](saved-workspace.md) for safe
editing copies, lasso deletion, undo/redo, cropping and PLY/STL downloads. It is distinct from the
live capture workspace. Keep scans and their source recordings together when
moving them to another machine.

For file contents, units and calibration, see the
[technical reference](technical-reference.md). For benchmark and recovery work
that is not fully validated, see [build status](build-status.md).
