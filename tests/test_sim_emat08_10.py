""" Basic tests for emat08_10 """
# pylint: disable=redefined-outer-name
import asyncio

import pytest

from src.emat08_10 import EatonEMAT

EOL = "\r\n"
PROMPT = "pdu> "


class FakePDU:
    """
    Simulated EMAT PDU telnet session that:
    - prompts for username and password, then shows a command prompt
    - records commands sent after login
    - answers "get" from a property table and applies "set" to it
    - echoes each command, then replies with value line and prompt
    """
    # pylint: disable=too-many-instance-attributes

    def __init__(self, outlet_count: int = 4, password: str = "secret"):
        self.outlet_count = outlet_count
        self.password = password
        self.props = {
            "PDU.PowerSummary.iPartNumber": "EMAT08-10",
            "PDU.PowerSummary.iVersion": "1.2.3",
            "PDU.PowerSummary.iManufacturer": "EATON",
            "PDU.PowerSummary.iSerialNumber": "SN12345",
            "PDU.OutletSystem.Outlet.Count": str(outlet_count),
        }
        for n in range(1, outlet_count + 1):
            outlet = f"PDU.OutletSystem.Outlet[{n}]"
            self.props[f"{outlet}.iName"] = f"Outlet{n}"
            self.props[f"{outlet}.PresentStatus.SwitchOnOff"] = "0"
            self.props[f"{outlet}.ActivePower"] = f"{10.5 * n}"
            self.props[f"{outlet}.AutomaticRestart"] = "2"
        self.login = []      # username and password lines
        self.commands = []   # command lines sent after login
        self.closed = False
        self.connect_kwargs = None  # open_connection() arguments
        self._rx = "login: "  # pending output for the client
        self._partial = ""   # incomplete input line

    def take(self, size: int) -> str:
        """ remove and return up to size chars of pending output """
        chunk, self._rx = self._rx[:size], self._rx[size:]
        return chunk

    def receive(self, data: str) -> None:
        """ accept client input, handling each complete line """
        self._partial += data
        while EOL in self._partial:
            line, self._partial = self._partial.split(EOL, 1)
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        if len(self.login) == 0:
            self.login.append(line)
            self._rx += "Password: "
        elif len(self.login) == 1:
            if line != self.password:
                self.login.clear()
                self._rx += EOL + "Login incorrect" + EOL + "login: "
                return
            self.login.append(line)
            self._rx += EOL + PROMPT
        else:
            self.commands.append(line)
            self._rx += line + EOL + self._reply(line) + EOL + PROMPT

    def _reply(self, line: str) -> str:
        verb, _, rest = line.partition(" ")
        if verb == "get":
            if "[x]" in rest:
                return "|".join(self.props[rest.replace("[x]", f"[{n}]")]
                                for n in range(1, self.outlet_count + 1))
            return self.props.get(rest, "")
        if verb == "set":
            path, _, value = rest.partition(" ")
            outlet, _, field = path.rpartition(".")
            if field == "DelayBeforeStartup":
                self.props[f"{outlet}.PresentStatus.SwitchOnOff"] = "1"
            elif field == "DelayBeforeShutdown":
                self.props[f"{outlet}.PresentStatus.SwitchOnOff"] = "0"
            else:
                self.props[path] = value
        return ""


class FakeReader: # pylint: disable=too-few-public-methods
    """ Fake telnetlib3 reader returning the PDU's pending output """

    def __init__(self, device: FakePDU):
        self._device = device

    async def read(self, size: int) -> str:
        """ return pending output, or "" after a short wait if none """
        chunk = self._device.take(size)
        if not chunk:
            await asyncio.sleep(0.01)
        return chunk


class FakeWriter:
    """ Fake telnetlib3 writer passing client input to the PDU """

    def __init__(self, device: FakePDU):
        self._device = device

    def write(self, data: str) -> None:
        """ send data to the PDU """
        self._device.receive(data)

    async def drain(self) -> None:
        """ no-op """

    def close(self) -> None:
        """ mark closed """
        self._device.closed = True

    async def wait_closed(self) -> None:
        """ no-op """


@pytest.fixture
def fake_pdu(monkeypatch):
    """ patch telnetlib3.open_connection so connect() talks to a FakePDU """
    device = FakePDU()

    async def fake_open_connection(**kwargs):
        device.connect_kwargs = kwargs
        return FakeReader(device), FakeWriter(device)

    monkeypatch.setattr("telnetlib3.open_connection", fake_open_connection)
    return device


@pytest.fixture
def pdu(fake_pdu):
    """ connected and initialized EatonEMAT """
    emat = EatonEMAT(read_timeout=1.0)
    assert emat.connect("10.0.0.5", 1234, username="admin", password="secret")
    assert emat.initialize()
    assert fake_pdu.commands  # initialize() talked to the device
    yield emat
    emat.disconnect()


def test_connect_logs_in(fake_pdu):
    """ Test connecting to emat08_10 sends credentials """
    emat = EatonEMAT(read_timeout=1.0)

    ok = emat.connect("10.0.0.5", 1234, username="admin", password="secret")
    assert ok is True
    assert emat.is_connected() is True
    assert fake_pdu.connect_kwargs["host"] == "10.0.0.5"
    assert fake_pdu.connect_kwargs["port"] == 1234
    assert fake_pdu.login == ["admin", "secret"]

    emat.disconnect()


def test_connect_requires_credentials(fake_pdu):
    """ Test connect fails without username and password """
    emat = EatonEMAT(read_timeout=1.0)

    assert emat.connect("10.0.0.5", 1234) is False
    assert emat.is_connected() is False
    assert fake_pdu.connect_kwargs is None


def test_connect_fails_on_bad_password(fake_pdu):
    """ Test connect reports failure and stays disconnected on a rejected login """
    emat = EatonEMAT(read_timeout=1.0)

    assert emat.connect("10.0.0.5", 1234, username="admin", password="wrong") is False
    assert emat.is_connected() is False
    assert fake_pdu.login == []
    assert fake_pdu.closed is True

    emat.disconnect()


def test_initialize_reads_device(pdu):
    """ Test initialize reads device properties and outlet states """
    assert pdu.outlet_count == 4
    assert pdu.model == "EMAT08-10"
    assert pdu.version == "1.2.3"
    assert pdu.manufacturer == "EATON"
    assert pdu.serial == "SN12345"
    assert pdu.outlet_names == ["Outlet1", "Outlet2", "Outlet3", "Outlet4"]
    assert pdu.outlet_onoff == [0, 0, 0, 0]


def test_outlet_on_sends_command(pdu, fake_pdu):
    """ test outlet on sends command """
    assert pdu.outlet_on(2) is True

    assert fake_pdu.commands[-1] == "set PDU.OutletSystem.Outlet[2].DelayBeforeStartup 0"
    assert pdu.outlet_onoff == [0, 1, 0, 0]


def test_outlet_off_sends_command(pdu, fake_pdu):
    """ test outlet off sends command """
    assert pdu.outlet_on(3) is True
    assert pdu.outlet_off(3) is True

    assert fake_pdu.commands[-1] == "set PDU.OutletSystem.Outlet[3].DelayBeforeShutdown 0"
    assert pdu.outlet_onoff == [0, 0, 0, 0]


def test_outlet_status_round_trip(pdu, fake_pdu):
    """ test outlet status round trip """
    assert pdu.outlet_status(3) == "0"
    assert fake_pdu.commands[-1] == "get PDU.OutletSystem.Outlet[3].PresentStatus.SwitchOnOff"

    assert pdu.outlet_on(3) is True
    assert pdu.outlet_status(3) == "1"


def test_outlet_number_out_of_range(pdu, fake_pdu):
    """ test out of range outlet numbers are rejected without sending """
    sent = len(fake_pdu.commands)

    assert pdu.outlet_on(0) is False
    assert pdu.outlet_on(5) is False
    assert pdu.outlet_status(5) is None
    assert len(fake_pdu.commands) == sent


def test_set_autostart(pdu, fake_pdu):
    """ test set_autostart sends command and updates the device """
    assert pdu.get_atomic_value("auto_restart2") == 2

    assert pdu.set_autostart(2, 1) is True
    assert fake_pdu.commands[-1] == "set PDU.OutletSystem.Outlet[2].AutomaticRestart 1"
    assert pdu.get_atomic_value("auto_restart2") == 1


def test_set_autostart_rejects_bad_args(pdu, fake_pdu):
    """ test set_autostart rejects bad outlet or state without sending """
    sent = len(fake_pdu.commands)

    assert pdu.set_autostart(2, 3) is False
    assert pdu.set_autostart(2, -1) is False
    assert pdu.set_autostart(5, 1) is False
    assert len(fake_pdu.commands) == sent


def test_get_atomic_value_model(pdu, fake_pdu):
    """ test get_atomic_value for a device item """
    assert pdu.get_atomic_value("model") == "EMAT08-10"
    assert fake_pdu.commands[-1] == "get PDU.PowerSummary.iPartNumber"


def test_get_atomic_value_outlet_items(pdu):
    """ test get_atomic_value converts outlet values by type """
    assert pdu.get_atomic_value("active_power3") == pytest.approx(31.5)
    assert pdu.get_atomic_value("name2") == "Outlet2"
    assert pdu.get_atomic_value("outlet_status1") is False
    assert pdu.outlet_on(1) is True
    assert pdu.get_atomic_value("outlet_status1") is True


def test_disconnect_closes(fake_pdu):
    """ test disconnect closes """
    emat = EatonEMAT(read_timeout=1.0)
    assert emat.connect("10.0.0.5", 1234, username="admin", password="secret")
    assert fake_pdu.closed is False

    emat.disconnect()
    assert emat.is_connected() is False
    assert fake_pdu.closed is True
    assert fake_pdu.commands[-1] == "exit"
