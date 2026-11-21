import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from tribuutscan import model_pane


def test_native_state_polls_once_and_rejects_old_take_or_piece():
    renderer = Mock()
    renderer.offer.side_effect = range(1, 10)
    state = model_pane.ModelState(renderer)
    commands = queue.Queue()
    xyz, seen = np.array([[1., 2., 3.]]), np.array([4])
    state.request(1, commands)
    state.request(2, commands)
    assert commands.get_nowait() == ("coverage_preview", 1, None)
    assert commands.empty()
    state.reset()
    state.request(2, commands)
    assert commands.empty()
    state.receive(1, "old", xyz, seen)
    assert state.geometry is None
    state.request(3, commands)
    assert commands.get_nowait() == ("coverage_preview", 1, None)
    state.receive(1, "v1", xyz, seen)
    assert state.expected == 1 and state.version == "v1"
    state.request(3.1, commands)
    assert commands.empty()
    state.request(4, commands)
    assert commands.get_nowait() == ("coverage_preview", 1, "v1")
    state.pieces = 2
    state.choose(2)
    state.receive(1, "v1", None, None)
    assert state.geometry is None
    state.request(4.1, commands)
    assert commands.get_nowait() == ("coverage_preview", 2, None)
    state.receive(2, "v2", xyz, seen)
    assert state.first_serial == 2
    state.rotate(10, -5)
    state.scale(1.2)
    assert state.first_serial == 2 and state.expected == 4


def test_model_pane_draws_in_place_and_rejects_previous_piece_images():
    pane = object.__new__(model_pane.ModelPane)
    pane.blank, pane.canvas, pane.shown = object(), Mock(), None
    renderer = SimpleNamespace(latest=(9, b"P6 image"))
    pane.state = SimpleNamespace(renderer=renderer, first_serial=10, expected=11)
    with patch.object(model_pane.tk, "PhotoImage", return_value=Mock()) as image:
        pane.render_latest()
        pane.canvas.configure.assert_not_called()
        renderer.latest = (10, b"P6 current")
        pane.render_latest()
        image.assert_called_once_with(data=b"P6 current", format="PPM")
        pane.canvas.configure.assert_called_once_with(image=image.return_value)
        assert pane.shown == 10
        pane.state.expected = None
        pane.render_latest()
        pane.canvas.configure.assert_called_with(image=pane.blank)


def test_native_caption_distinguishes_live_paused_and_tracking_loss():
    pane = object.__new__(model_pane.ModelPane)
    pane.caption, pane.shown_caption = Mock(), None
    pane.state = SimpleNamespace(geometry=(object(), object()))
    for phase, lost, text in (("scanning", False, "Drag to rotate"), ("paused", False, "Paused"),
                              ("scanning", True, "Tracking lost"), ("idle", False, "Last live model")):
        pane.update_caption(phase, lost)
        assert text in pane.caption.configure.call_args.kwargs["text"]
        assert "not an accuracy check" in pane.caption.configure.call_args.kwargs["text"]
