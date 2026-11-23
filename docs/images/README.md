# Current web UI screenshots

Captured on 23 November 2026 from this fork's actual HTML, CSS and JavaScript in
a headless Chromium-based browser, using an isolated `Service(demo=True)` and an
empty temporary capture directory. No real scanner frames, private saved scans or
live host credentials are included.

| Image | Capture |
| --- | --- |
| `web-workspace.png` | Full 1920×1080 desktop viewport, Scan tab |
| `web-camera-tools.png` | Camera panel cropped by the browser from that viewport |
| `web-phone-pairing.png` | Pairing dialog cropped by the browser |
| `web-mobile.png` | Full page at a 390×844 phone viewport |
| `web-alignment.png` | Full 1920×1080 saved-scan alignment workspace using synthetic test sessions |
| `saved-workspace.png` | New saved-result workspace, View tab, synthetic session |
| `saved-alignment.png` | Align tab inside the saved-result workspace, synthetic sessions |
| `saved-cleanup.png` | Lasso selection preview inside Cleanup, synthetic session |
| `alignment-drag.png` | Direct move/rotate tools and amber moving-scan outline, synthetic sessions |

The pairing example uses `192.0.2.10` and `documentation-example-only`; it is not
an operational pairing link. Screenshots show demo/disconnected state, not scan
quality or performance. Keep captions explicit when reusing them.

The alignment and saved-workspace screenshots use generated point-grid sessions
and the actual WebGL editors. Unlike the capture screenshots, it exercises saved-session editing
on synthetic data rather than demo mode. No physical scanner recording is shown.

When replacing images, use a separate demo service with an empty capture directory,
refresh all assets, and inspect every image before committing. Capture the actual
rendered interface without adding fabricated scan results. Keep the
[web UI tour](../web-ui.md) and [README](../../README.md) in sync with navigation changes.
