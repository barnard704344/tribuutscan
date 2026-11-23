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
