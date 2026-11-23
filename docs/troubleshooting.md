# Troubleshooting

Use the [user guide](user-guide.md) for the normal workflow. Preserve a problem
take and its raw recording before trying to repair or reprocess it.

## Launcher opens nothing

Run the launcher in a terminal from the checkout to see startup errors:

```sh
./tribuutscan-viewer.sh
```

The launcher uses system `python3`; a missing NumPy import can stop it before the
browser opens. With dependencies installed, try the explicit environment:

```sh
.venv/bin/python -m tribuutscan.webapp
```

If the server prints a URL but no browser opens, open that URL manually. With
`--no-browser`, this is expected. An Applications shortcut must point at the
current checkout's launcher. Starting another copy normally opens the existing
host rather than replacing it. Check `captures/viewer.log` and terminal output.

## Phone cannot connect or asks for pairing again

Use the host's LAN address and configured port, not `localhost`. Open the **Host**
tab and use the current pairing QR/link/code. Manual host starts regenerate
credentials. Guest Wi-Fi isolation, a firewall, or a listener restricted to
`127.0.0.1` can prevent access. Both devices need a route to the host.

Pairing links and local session files contain access credentials: redact them
before sharing diagnostics. See the user guide for the trusted-network setup.

## No depth, missing distance, or reconnecting

- Check a USB 3 connection and power. Try a direct port and a suitable cable.
- Close other scanner applications and competing hosts, including ones launched
  from other checkouts. A second application can hold the camera interfaces.
- Install the [udev rule](../udev/70-tribuutscan.rules), reload rules and reconnect
  the device. Check that the Linux user can access the video interfaces.
- Confirm the subject appears in live Depth at a suitable range. Colour alone
  does not prove valid depth. Reflective/dark surfaces and poor lighting can matter.
- Allow a range change or reconnect to finish before starting capture.

The gauge belongs below Depth. A grey **Last** distance is deliberately historical;
it is not a current reading. Missing current distance can result from missing
depth or an invalid selection. Reset aim and select the subject again if needed.
If the gauge itself is absent, refresh the browser after checking host version.

## Open scan does nothing or opens the wrong take

Refresh the capture page to load the current browser code. In **Files → Open Scan
Library**, find the desired card, then press **Open scan**. The viewer opens in
the current tab after the host accepts the request. Check Bin if a result is
missing from the normal library; restore it before opening. Saved scans can open
with the scanner disconnected, but capture, recording and processing must finish
first. If the request fails, its error appears above the tool panel and the page
stays in the capture workspace.

## Align asks for a scan, or dragging only moves the camera

Open the take from **Files → Open Scan Library → Open scan**, then use its **Align** tab. The opened
scan should load as the blue reference automatically. Choose a second take to load
its amber preview. **Reload previews** retries a failed load; choosing a source
normally loads it without that button. Refresh the browser if it still shows the
older workflow.

Choose **Move scan** or **Rotate scan**, then drag the amber points. **Orbit view**
moves the camera instead. The blue reference stays fixed. Move works across the
current camera plane; orbit to a different angle to move along another plane.
Escape cancels an unfinished drag. The tools need a loaded, visible moving scan.

Saving requires uncropped, single-piece Head / free object sessions with matching
camera calibration and range. Lasso-cleaned or bare exported PLY files are not
compatible full sessions. See [alignment restrictions](alignment.md#first-release-limits).

## Refine alignment refuses the fit or gives an unexpected result

Roughly position the scans first. **Maximum matching distance** is the largest
separation allowed for a match; increase it only enough to cover the remaining
placement error. Use **Select overlap** to exclude supports or areas visible in
only one scan. The box selects through surfaces, so check the bright/dim preview.
Include varied shape rather than a flat or symmetric patch. Too few points or
too little overlap cannot produce a reliable local fit.

A failed refinement leaves the previous placement unchanged. Undo an unexpected
proposal and use matching points or a better shared region. The fit report covers
sampled nearby matches; a small RMS does not prove the whole scan is aligned.
If the command is unavailable after updating, restart the idle scanner host and
refresh the browser to load both Python and browser changes.

## Cleanup deletes too much, or optimisation is refused

Lasso selection goes through the model, including surfaces behind the visible
ones. Inspect the pink selection before deleting. **Undo** restores the previous
editing state; **Clear all lasso cleanup** removes all lasso rules while retaining
crop settings. Align and optimise before lasso cleanup; clear or undo the cleanup
before changing poses. Editing history survives refresh/restart, with up to five
undo steps. The original capture and its raw recordings remain separate.

Capture continuation skips editing copies. To add more views, continue the
original capture and create a new editing copy afterward.

## Lasso selection appears not to remove anything

Drawing highlights the selection; press **Delete selected region** to remove it.
The selection goes through the model. The selected points hide immediately as a
pending preview, then the host confirms the removed-point count after the saved
cloud reloads. A failed save restores the preview and shows its error.

Older versions rebuilt the entire captured session for each deletion, leaving
the unchanged model visible for minutes on large scans. Refresh the browser for
the new feedback and drawing handlers; each new cleanup worker loads the updated
deletion code. Existing mesh exports are retired after cleanup. Generate a new
mesh before checking the STL, and ensure you are inspecting the editing copy.

## Coverage says waiting, stays pink, or stops growing

Check **Tracking** and **Geometry** first. Camera frames can keep arriving while
tracking has stopped accepting geometry. Pink means a sensor surface lacks a
model hit; it does not mean the volume has integrated that surface.

For a head or held object, select **Head / free object** before starting. Table
mode can remove parts of a face as a support plane. Keep reliable depth, overlap
with earlier views and return towards the **Last tracked view** after a loss.
Moving slowly cannot compensate for missing depth or a wrong subject mask.

The classified coverage display uses the GPU tracking volume. **Force CPU** does
not offer the same coverage display. Check the selected backend in host logs.
If Python code was updated, refreshing the page alone is insufficient: finish the
take and use **Restart scanner host**, or stop and relaunch the host manually.

Coverage is view-dependent. It cannot quantify the unseen back of a head. During
lost tracking, a preview can refresh from the last tracked pose without growing
the accepted scan. Avoid treating preview activity as evidence of successful fusion.

## Paused coverage will not turn

Wait for the paused state. Coverage should occupy the centre; drag there to turn
the coverage camera, and use scroll/pinch or the zoom controls to inspect it.
This needs a usable GPU volume and the matching host implementation. It is a
host-rendered coverage image, so latency depends on the host and network.

After updating, refresh the browser and restart the idle scanner host to load both
sides of the feature. Resuming restores the capture layout and live camera.

## Slow startup or slow capture

Initial GPU compilation/preparation can delay the first scan. CPU fallback,
software GPU adapters, other GPU applications, thermal/power limits, recording
and scene complexity can reduce throughput. Distinguish camera/preview FPS from
processed scan FPS. The current performance meter is experimental; see
[build status](build-status.md).

Do not run heavy GPU benchmarks alongside a take. A timestamp readback hang was
observed in the standalone NVIDIA benchmark; it is not a required capture step.

## Save, restore or mesh fails

A crop job in older builds restored/remerged every view and could automatically
regenerate a mesh, appearing stuck for minutes. Current editing copies use the
saved fused cloud for crop/clear, preserve lasso rules, retire old meshes and leave
mesh regeneration as a separate action. Refresh the page after updating; new
processing workers load the new crop code without restarting the scanner host.
An older session without `cloud_state.npz` still needs its initial restore.

Wait for optimisation/meshing to finish before restarting. Keep the scan directory,
raw recording and session metadata together. Continuing needs compatible camera
calibration/range and saved source data, not just `cloud.ply`. Low overlap, lost
tracking, disconnected pieces or too little surface can prevent a usable mesh.

Keep an untouched copy before offline processing. A saved point cloud is useful
even when closed-solid export fails; check available outputs in the result page.

## Useful information for a bug report

Include the fork commit (`git rev-parse --short HEAD`), Linux/Python versions,
GPU/driver and selected backend, subject/range settings, exact steps, and relevant
redacted log lines. State whether Depth, Tracking and Geometry changed together.
Do not publish raw face recordings, private captures, pairing codes or session files.
