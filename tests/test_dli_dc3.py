""" Basic tests for dli_dc3 """
import paramiko
import pytest

from src.dli_dc3 import Dlidc3


class FakeStream:
    """ Fake paramiko channel stream supporting .read() """
    def __init__(self, data: bytes = b""):
        self._data = data

    def read(self) -> bytes:
        """ return preloaded bytes """
        return self._data


class FakeSSHClient:
    """
    Fake paramiko.SSHClient that:
    - records exec_command() calls
    - returns preloaded reply data, in order
    """

    def __init__(self):
        self.commands = []
        self.replies = []  # list[bytes], one per exec_command call
        self.closed = False

    def set_missing_host_key_policy(self, policy):
        """ no-op """

    def connect(self, hostname="", username="", password=""):
        """ no-op """

    def exec_command(self, cmd):
        """ record command and return (stdin, stdout, stderr) """
        self.commands.append(cmd)
        reply = self.replies.pop(0) if self.replies else b""
        return FakeStream(), FakeStream(reply), FakeStream(b"")

    def close(self):
        """ mark closed """
        self.closed = True


@pytest.fixture
def fake_ssh(monkeypatch):
    """ patch paramiko.SSHClient so connect() uses our fake instead of a real SSH session """
    holder = {}

    def fake_ssh_client():
        client = FakeSSHClient()
        holder["instance"] = client
        return client

    monkeypatch.setattr(paramiko, "SSHClient", fake_ssh_client)
    return holder


def test_connect_sets_connected(fake_ssh):
    """ connect() should mark the device as connected """
    pdu = Dlidc3(log=False)
    pdu.connect("10.0.0.5", username="user", password="pass")
    assert pdu.is_connected() is True
    assert isinstance(fake_ssh["instance"], FakeSSHClient)


def test_outlet_on_uses_zero_based_index(fake_ssh):
    """ callers use 1-based outlet numbers, but the uom command is zero-based """
    pdu = Dlidc3(log=False)
    pdu.connect("10.0.0.5")
    pdu.initialized = True
    pdu.outlet_onoff = [0] * pdu.outlet_count

    client = fake_ssh["instance"]
    client.replies.append(b"")

    assert pdu.outlet_on(1) is True
    assert client.commands[-1] == "uom set relay/outlets/0/state true"
    assert pdu.outlet_onoff[0] == 1


def test_outlet_status_round_trip(fake_ssh):
    """ outlet 3 (1-based) should query zero-based outlet index 2 """
    pdu = Dlidc3(log=False)
    pdu.connect("10.0.0.5")

    client = fake_ssh["instance"]
    client.replies.append(b"true\n")

    assert pdu.outlet_status(3) is True
    assert client.commands[-1] == "uom get relay/outlets/2/state"


def test_validate_outlet_rejects_out_of_range(fake_ssh):
    """ outlet numbers outside [1, outlet_count] are rejected """
    pdu = Dlidc3(log=False)
    pdu.connect("10.0.0.5")

    assert pdu.outlet_status(0) is None
    assert pdu.outlet_status(pdu.outlet_count + 1) is None


def test_get_atomic_value_uses_one_based_suffix(fake_ssh):
    """ get_atomic_value's numeric suffix is 1-based, like other outlet_num arguments """
    pdu = Dlidc3(log=False)
    pdu.connect("10.0.0.5")
    pdu.initialized = True

    client = fake_ssh["instance"]
    client.replies.append(b"true\n")

    assert pdu.get_atomic_value("state1") is True
    assert client.commands[-1] == "uom get relay/outlets/0/state"
