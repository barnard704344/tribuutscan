"""Headless checks for the capture panel theme."""
from unittest.mock import Mock, patch

from tribuutscan import panels, viewer


def test_scan_controls_keep_state_and_primary_style():
    app = Mock()
    panel = object.__new__(panels.ScanPanel)
    panel.app, panel.shown = app, {}
    panel.scan = Mock()
    with patch.object(panels, "frame", return_value=Mock()), \
            patch.object(panels, "menu"), patch.object(panels, "checkbox"), \
            patch.object(panels, "button", return_value=panel.scan) as button:
        panel.build(None)
    assert button.call_args_list[0].kwargs == {
        "style": "Primary.Capture.TButton", "command": app.toggle_scan,
    }
    for state, expected in panels.ScanPanel.STATES.items():
        app.scan_state = state
        panel.refresh()
        assert panel.scan.configure.call_args.kwargs == dict(zip(("text", "state"), expected))


def test_foreground_colours_stay_legible():
    def luminance(colour):
        rgb = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
        linear = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)
        return sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722)))

    for foreground, background in (
        (panels.INK, panels.SURFACE), (panels.MUTED, panels.BG),
        (panels.SURFACE, panels.ACCENT), (panels.SURFACE, panels.ACCENT_HOVER),
        (panels.SURFACE, panels.ACCENT_PRESSED),
        *((colour, panels.SURFACE) for colour in (viewer.GOOD, viewer.EDGE, viewer.BAD)),
    ):
        dark, light = sorted(map(luminance, (foreground, background)))
        assert (light + 0.05) / (dark + 0.05) >= 4.5
