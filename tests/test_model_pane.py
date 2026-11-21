import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from tribuutscan import model_pane


def test_native_state_subscribes_once_and_rejects_old_take_or_piece():
    renderer = Mock()
    renderer.offer.side_effect = range(1, 10)
    state = model_pane.ModelState(renderer)
    commands = queue.Queue()
    xyz, seen = np.array([[1., 2., 3.]]), np.array([4])
    state.subscribe(commands)
    state.subscribe(commands)
    _, old_generation, piece = commands.get_nowait()
    assert piece == 1 and commands.empty()
    state.reset()
    state.subscribe(commands)
    _, next_generation, _ = commands.get_nowait()
    state.receive(old_generation, 1, "old", xyz, seen)
    assert state.geometry is None
    state.receive(next_generation, 1, "v1", xyz, seen)
    assert state.expected == 1 and state.version == "v1"
    state.receive(next_generation, 1, "v1", xyz, seen)
    assert state.expected == 1
    state.pieces = 2
    state.choose(2)
    state.receive(next_generation, 1, "old piece", xyz, seen)
    assert state.geometry is None
    state.subscribe(commands)
    _, selected_generation, piece = commands.get_nowait()
    assert piece == 2 and selected_generation != next_generation
    state.receive(selected_generation, 2, "v2", xyz, seen)
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
