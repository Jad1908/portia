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


def test_the_default_steps_around_the_port_llama_server_is_set_to_use(monkeypatch):
    """A second window took 8081 when 8080 was busy, which is llama.cpp's
    default: the server then could not start, or the picker asked the window."""
    monkeypatch.setattr(launch, "is_free", lambda host, port: port != launch.DEFAULT_PORT)
    reserved = launch.DEFAULT_PORT + 1
    assert launch.pick_port(HOST, None, reserved=(reserved,)) == reserved + 1


def test_a_port_asked_for_is_served_even_where_llama_server_is_set_to_go(monkeypatch):
    """Named ports are the asker's. The launcher says so; it does not move."""
    monkeypatch.setattr(launch, "is_free", lambda host, port: True)
    assert launch.pick_port(HOST, 8081, reserved=(8081,)) == 8081


def test_a_listening_socket_is_not_free_even_to_a_bind_that_would_succeed(taken, monkeypatch):
    """macOS lets a socket bind 127.0.0.1:N while another holds *:N; the port is
    not free for that, and the check asks whether anything accepts as well."""
    import socket as socket_module

    from portia.core import ports

    real = socket_module.socket

    class Binds(real):  # type: ignore[misc, valid-type]
        def bind(self, address):
            return None

    monkeypatch.setattr(ports.socket, "socket", Binds)
    assert not ports.is_free(HOST, taken)


def test_next_free_passes_over_what_it_is_told_to(monkeypatch):
    from portia.core import ports

    monkeypatch.setattr(ports, "is_free", lambda host, port: port != 9000)
    assert ports.next_free(HOST, 9000, tries=5, skip={9001}) == 9002
    assert ports.next_free(HOST, 65535, tries=5) in (None, 65535)


def test_a_port_whose_server_just_closed_is_free():
    """Connections left in TIME_WAIT by a server that closed are not a server.

    Restarting portia straight after closing it refused `--port` as in use for
    about thirty seconds, and moved the default window up a port (2026-09-25).
    The server side closing first is what leaves its end in TIME_WAIT.
    """
    from portia.core import ports

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((HOST, 0))
        listener.listen()
        port = listener.getsockname()[1]
        client = socket.create_connection((HOST, port))
        accepted, _ = listener.accept()
        accepted.close()  # the server's end closes first: TIME_WAIT is on port
        client.close()
    assert ports.is_free(HOST, port)
