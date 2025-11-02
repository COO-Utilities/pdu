import socket
import types
import pytest
from unittest.mock import patch

from pdu.EMAT08_10 import EatonEMAT


class FakeSocket:
    """
    Fake socket that:
    - stores outbound writes (sendall)
    - returns preloaded inbound chunks (recv)
    - times out when no more chunks exist
    """

    def __init__(self, timeout=1.0):
        self._timeout = timeout
        self._closed = False
        self._rx_chunks = []  # list[bytes]
        self.sent_data = []   # list[bytes]

    def settimeout(self, t):
        self._timeout = t

    def sendall(self, data: bytes):
        self.sent_data.append(data)

    def push_rx(self, data: bytes):
        """Test helper: preload reply data that recv() will yield."""
        self._rx_chunks.append(data)

    def recv(self, bufsize: int) -> bytes:
        """
        Return next chunk, or raise socket.timeout if empty.
        """
        if self._rx_chunks:
            return self._rx_chunks.pop(0)
        # Simulate "quiet period" by timing out
        raise socket.timeout("no more data")

    def close(self):
        self._closed = True


@pytest.fixture
def fake_socket(monkeypatch):
    holder = {}

    def fake_create_connection(addr, timeout=None):
        fs = FakeSocket(timeout=timeout)
        holder["instance"] = fs
        return fs

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    return holder


def test_connect_sets_connected(fake_socket):
    pdu = EatonEMAT(read_timeout=1.0)

    ok = pdu.connect("10.0.0.5", 1234)
    assert ok is True
    assert pdu.is_connected() is True

    # Make sure the fake socket got created and timeout set
    fs = fake_socket["instance"]
    assert isinstance(fs, FakeSocket)


def test_outlet_on_sends_command(fake_socket):
    pdu = EatonEMAT(read_timeout=1.0)
    assert pdu.connect("10.0.0.5", 1234)

    fs = fake_socket["instance"]

    # default template is "ol on {n}" + "\r\n"
    sent_ok = pdu.outlet_on(2)
    assert sent_ok is True

    # Check bytes that were sent
    joined = b"".join(fs.sent_data)
    assert b"ol on 2\r\n" in joined


def test_outlet_status_round_trip(fake_socket):
    pdu = EatonEMAT(read_timeout=1.0)
    assert pdu.connect("10.0.0.5", 1234)

    fs = fake_socket["instance"]

    # preload reply for status query
    fs.push_rx(b"Outlet 3: OFF\r\n")

    status = pdu.outlet_status(3)
    assert status is not None
    assert "Outlet 3: OFF" in status

    # verify command sent
    joined = b"".join(fs.sent_data)
    assert b"ol status 3\r\n" in joined


def test_get_atomic_value_model(fake_socket):
    pdu = EatonEMAT(read_timeout=1.0)
    assert pdu.connect("10.0.0.5", 1234)

    fs = fake_socket["instance"]

    # When we call get_atomic_value("model"),
    # driver sends pdu.cmd_device_model (default "sys info")
    # and then reads. We'll preload a fake reply.
    fs.push_rx(b"Model: EMAT-08\r\n")

    val = pdu.get_atomic_value("model")
    assert val is not None
    assert "EMAT-08" in val

    joined = b"".join(fs.sent_data)
    assert b"sys info\r\n" in joined


def test_disconnect_closes(fake_socket):
    pdu = EatonEMAT(read_timeout=1.0)
    assert pdu.connect("10.0.0.5", 1234)

    fs = fake_socket["instance"]
    assert getattr(fs, "_closed", False) is False

    pdu.disconnect()
    assert pdu.is_connected() is False
    assert fs._closed is True
