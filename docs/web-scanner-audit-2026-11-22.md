# Web scanner audit — 22 November 2026

Reviewed the browser scanner introduced on `main` at `793c31a`: the controller,
HTTP gateway, private model/scan worker, saved-scan continuation, processing,
downloads and browser commands. This is a source and automated regression audit;
it does not certify measurement accuracy or replace a physical scanning trial.

## Changes already delivered

- The Applications launcher starts a browser workspace. The scanner, tracking,
  recording and GPU processing stay on the Linux host; paired phones and computers
  can control it over the local network.
- Capture logic lives in a shared controller; the optional desktop adapter remains.
- The workspace provides capture controls, saved scans, processing and downloads.
  Equal 16:9 Live 3D, Colour and IR previews sit on the left, the larger Depth view
  occupies the centre, and controls sit on the right. Small screens reorder these.
- Depth has distance guidance beneath it, with tracked-object readings, a centre
  fallback and an explicitly grey, aged last reading when current depth disappears.
- Head / free object mode avoids tabletop removal clipping a face or held object.
  Saved sessions and raw replay retain the subject mode. Table mode remains available.
- Camera previews fit without cropping; object picking accounts for letterboxing.

## Findings fixed in this audit

| Finding | Change and regression coverage |
| --- | --- |
| Shutdown could close the scan worker before an active processing job finished. | The service waits for processing before closing its controller. The worker stops accepting HTTP work and waits for processing before exit. A shutdown-order regression checks this sequence. |
| Processing checked the scan phase before acquiring its lock, and a new scan could change calibration during processing. | Phase is checked inside the processing lock; scan startup takes that lock before changing camera mode or replacing the scanner. A regression changes the phase while acquiring the lock. |
| Private loopback model and worker endpoints could bypass gateway pairing. | Both require a separate random bearer key supplied only by the host gateway and processing client. Tests reject missing/wrong keys and exercise the authenticated worker. The standalone viewer retains optional local access. |
| Standalone processing accepted browser form requests. | Processing requires the custom application header and rejects a conflicting Origin. The 3D client supplies the header; regressions cover both rejected and accepted requests. |
| A command from an earlier service instance could match a reused revision. | Commands carry both service instance and revision; the gateway and 3D processing client preserve them. Old or missing instances are rejected. |
| Changing selected scans during a download could mix metadata, filename and payload. | Each model request captures its selected source. A regression switches selection during the read and verifies the original scan is downloaded. |
| Continue could show the chosen subject mode instead of the saved take's actual mode; malformed metadata could raise an unhandled exception. | Continue restores the saved mode before worker startup and rejects unsupported modes or malformed session/camera metadata with an explanatory message. |
| Reopening a service bound to a specific LAN address used localhost. | The private launcher session records the local bind address and validates it when reopening. A regression covers explicit binding and private session permissions. |
| Environment HTTP proxies could receive private loopback RPC and its key. | Host-local requests use an explicit proxy-free opener. A local proxy trap regression verifies that these requests bypass proxy environment variables. |
| Scan names could break download headers, including through newlines or Unicode. | Download names use percent-encoded UTF-8 `filename*` headers. A regression checks Unicode and attempted header injection. |

## Validation

Run the Python checks from the checkout using the existing environment:

```sh
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 .venv/bin/python tests/run_checks.py
node tests/test_scanner_ui.mjs
```

The Python runner imports test modules normally so multiprocessing spawn targets
remain importable. It supports selected modules as arguments, requires no pytest,
and returns a failure exit status if any check fails.

The complete Python run executed 257 checks: 256 passed and one existing
packed-depth check failed. The failure reproduced unchanged on the previous
`main` (`793c31a`): the environment has no WebGPU tracking adapter, and the check
incorrectly required zero CPU depth decodes even when tracking fell back to CPU.
Its assertion now checks the actual tracker: zero full decodes for GPU tracking,
and CPU decoding for CPU fallback. Geometry, pose and keyframe comparisons remain.
The merge module was rerun after this correction: all six checks passed. The
other 251 checks passed in the complete run; application code was unchanged
between that run and this test-only correction.

The focused web/launcher/download run passed all 29 checks. Scanner UI logic and
the 3D viewer's JavaScript syntax checks passed, as did Python compilation and
`git diff --check`. GPU-specific checks that return early without an adapter do
not establish hardware GPU coverage in this environment.

## Operating limits and follow-up

- The web host uses plain HTTP and is intended for a trusted LAN. Pairing is access
  control, not transport encryption. Do not expose its port directly to the internet;
  use a VPN or an authenticated HTTPS proxy for remote access.
- Keep pairing links private. Internal bearer keys are separate from pairing codes
  and are not returned in browser state. Captures and private launcher sessions stay
  outside Git.
- A retained distance reading is historical. It does not prove current depth or
  successful tracking. Head mode prevents inappropriate table removal; it does not
  guarantee tracking on moving faces or surfaces outside the sensor's usable range.
- Browser logic and syntax were checked automatically. A rendered desktop/phone
  visual check was unavailable in this audit environment. No new physical scanner
  capture or measurement-accuracy trial was performed during this audit.
- The running scanner service was left undisturbed. Restart the application after
  finishing any current scan to load these changes.
