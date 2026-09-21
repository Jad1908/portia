"""Where the window listens: the default is a preference, a named port is not."""

from __future__ import annotations

import socket

import pytest

pytest.importorskip("nicegui", reason="the app needs the `ui` extra")

from portia.ui import __main__ as launch  # noqa: E402  (after the extra is confirmed)

HOST = "127.0.0.1"


@pytest.fixture
def taken():
    """A port something else is listening on, picked by the system."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind((HOST, 0))
        held.listen()
        yield held.getsockname()[1]


def test_a_port_in_use_is_not_free(taken):
    assert not launch.is_free(HOST, taken)


def test_the_default_moves_up_when_it_is_taken(taken, monkeypatch):
    monkeypatch.setattr(launch, "DEFAULT_PORT", taken)
    port = launch.pick_port(HOST, None)
    assert taken < port < taken + launch.PORT_TRIES
    assert launch.is_free(HOST, port)


def test_a_port_that_was_asked_for_is_refused_and_never_moved(taken):
    with pytest.raises(ValueError, match=str(taken)):
        launch.pick_port(HOST, taken)


def test_a_free_port_that_was_asked_for_is_the_one_served():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        free = probe.getsockname()[1]
    assert launch.pick_port(HOST, free) == free


def test_running_out_of_ports_says_which_were_tried(monkeypatch):
    monkeypatch.setattr(launch, "is_free", lambda host, port: False)
    last = launch.DEFAULT_PORT + launch.PORT_TRIES - 1
    with pytest.raises(ValueError, match=f"{launch.DEFAULT_PORT} to {last}"):
        launch.pick_port(HOST, None)
