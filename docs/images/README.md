# Current web UI screenshots

Captured on 23 November 2026 from this fork's actual HTML, CSS and JavaScript in
a headless Chromium-based browser. Capture screenshots use an isolated
`Service(demo=True)` with an empty temporary capture directory; saved-workspace
screenshots use a separate host with generated sessions. No real scanner frames,
private saved scans or live host credentials are included.

| Image | Capture |
| --- | --- |
| `web-workspace.png` | Full 1920×1080 desktop viewport, Scan tab |
| `web-camera-tools.png` | Camera panel cropped by the browser from that viewport |
| `web-phone-pairing.png` | Pairing dialog cropped by the browser |
| `web-mobile.png` | Full page at a 390×844 phone viewport |
| `web-alignment.png` | Full 1920×1080 Align tab immediately after the opened scan loads as reference |
| `saved-workspace.png` | New saved-result workspace, View tab, synthetic session |
| `saved-alignment.png` | Align tab with both synthetic scans loaded and Move scan selected |
| `saved-cleanup.png` | Lasso selection preview inside Cleanup, synthetic session |
| `alignment-drag.png` | Same two-scan view, illustrating direct tools and the amber moving-scan outline |
| `alignment-refine.png` | Surface-refinement proposal and sampled fit report on generated curved surfaces |
| `scan-library.png` | Full desktop library, related captures/edits/aligned result, generated surfaces |
| `scan-library-trash.png` | Reversible Bin with synthetic sessions and retained source relationships |
| `scan-library-empty-bin.png` | Permanent deletion preview preserving a source used by another synthetic scan |
| `scan-library-mobile.png` | 390-pixel phone viewport of the same synthetic library |

The pairing example uses `192.0.2.10` and `documentation-example-only`; it is not
an operational pairing link. Screenshots show demo/disconnected state, not scan
quality or performance. Keep captions explicit when reusing them.

The alignment and saved-workspace screenshots use generated point-grid or curved-surface sessions
and the actual WebGL editors. Unlike the capture screenshots, they exercise
saved-session editing on synthetic data rather than demo mode. The alignment
images were refreshed after the surface-refinement tools were added. No physical
scanner recording is shown.

When replacing images, use a separate demo service with an empty capture directory,
refresh all assets, and inspect every image before committing. Capture the actual
rendered interface without adding fabricated scan results. Keep the
[web UI tour](../web-ui.md) and [README](../../README.md) in sync with navigation changes.
