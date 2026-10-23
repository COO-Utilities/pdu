""" Basic tests for wti_nps """
# pylint: disable=redefined-outer-name
import pytest

from src import wti_nps
from src.wti_nps import WtiNps, parse_plug_status, parse_unit_info

PROMPT = "NPS> "


def status_screen(states, names, busy=()):
    """ build a /S plug status screen """
    lines = [
        "",
        "Plug Status",
        "",
        " Plug | Name             | Status | Boot Delay | Priority | Default |",
        "------+------------------+--------+------------+----------+---------+",
    ]
    for n, (on, name) in enumerate(zip(states, names), start=1):
        status = ("ON" if on else "OFF") + (" *" if n in busy else "")
        lines.append(f"  {n}   | {name:<16} | {status:^6} |  0.5 Secs  |    {n}     |   ON    |")
    lines += ["", " Temperature: 24.5 C", ""]
    return "\r\n".join(lines)


class FakeNps:
    """
    Simulated WTI NPS CLI session (transport level) that:
    - optionally prompts for username and password
    - echoes each command line
    - answers /S, /J *, /ON, /OFF, /BOOT, /DPL and /X
    """

    def __init__(self, outlet_count=8, login=False, password="super"):
        self.states = [False] * outlet_count
        self.names = [f"Plug {n}" for n in range(1, outlet_count + 1)]
        self.password = password
        self.commands = []
        self.closed = False
        self._login = ["user", "pass"] if login else []
        self._rx = "\r\nLogin: " if login else "\r\n" + status_screen(self.states, self.names) \
            + "\r\n" + PROMPT

    def send(self, data: bytes) -> None:
        """ handle one command line """
        line = data.decode().rstrip("\r\n")
        if self._login:
            step = self._login.pop(0)
            if step == "user":
                self._rx += "\r\nPassword: "
            elif line == self.password:
                self._rx += "\r\n" + status_screen(self.states, self.names) + "\r\n" + PROMPT
            else:
                self._login = ["user", "pass"]
                self._rx += "\r\nInvalid Password\r\nLogin: "
            return
        self.commands.append(line)
        self._rx += line + "\r\n" + self._respond(line) + "\r\n" + PROMPT

    def _respond(self, line):  # pylint: disable=too-many-return-statements
        """ return the screen for a command """
        cmd, _, arg = line.partition(" ")
        cmd = cmd.upper()
        if cmd == "/S":
            return status_screen(self.states, self.names)
        if cmd == "/J":
            return ("Site ID: Lab rack\r\nModel Number: NPS-8HD20-1\r\n"
                    "Serial Number: 0123456789\r\nSoftware Version: v8.02")
        if cmd.startswith("/DPL"):
            self.states = [True] * len(self.states)
            return status_screen(self.states, self.names)
        if cmd in ("/ON", "/OFF", "/BOOT"):
            plugs = arg.split(",")[0]
            try:
                nums = [int(p) for p in plugs.split("+")]
            except ValueError:
                return "Invalid Plug"
            if any(n < 1 or n > len(self.states) for n in nums):
                return "Invalid Plug"
            for n in nums:
                self.states[n - 1] = cmd != "/OFF"
            return status_screen(self.states, self.names)
        if cmd == "/X":
            self.closed = True
            return ""
        return "Invalid Command"

    def recv(self, timeout: float) -> bytes:  # pylint: disable=unused-argument
        """ return pending output """
        data, self._rx = self._rx, ""
        return data.encode()

    def close(self) -> None:
        """ mark closed """
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    """ patch the ssh transport so connect() talks to a FakeNps """
    nps = FakeNps()
    monkeypatch.setattr(wti_nps, "_SshTransport", lambda *args, **kwargs: nps)
    return nps


@pytest.fixture
def pdu(fake):  # pylint: disable=unused-argument
    """ connected and initialized driver """
    dev = WtiNps(log=False, read_timeout=1.0)
    dev.connect("10.0.0.5")
    assert dev.initialize()
    return dev


def test_parse_plug_status():
    """ columns are found by header name, busy asterisk is detected """
    plugs = parse_plug_status(status_screen([True, False], ["Camera", "Spare"], busy=(2,)))
    assert list(plugs) == ["1", "2"]
    assert plugs["1"] == {"plug": "1", "name": "Camera", "on": True, "busy": False,
                          "boot_delay": 0.5, "default_on": True, "priority": 1}
    assert plugs["2"]["on"] is False
    assert plugs["2"]["busy"] is True


def test_parse_plug_status_without_table():
    """ text with no plug table parses to nothing """
    assert not parse_plug_status("Invalid Command\r\nNPS> ")


def test_parse_unit_info():
    """ key: value lines become a dict """
    info = parse_unit_info("Model Number: NPS-8\nSoftware Version: v8.02\nno colon here")
    assert info == {"Model Number": "NPS-8", "Software Version": "v8.02"}


def test_connect_and_initialize(pdu, fake):
    """ initialize() reads /J * and /S """
    assert pdu.is_connected()
    assert fake.commands == ["/J *", "/S"]
    assert pdu.model == "NPS-8HD20-1"
    assert pdu.serial == "0123456789"
    assert pdu.version == "v8.02"
    assert pdu.outlet_count == 8
    assert pdu.temperature == pytest.approx(24.5)


def test_shell_login_prompts(monkeypatch):
    """ username and password prompts inside the shell are answered """
    nps = FakeNps(login=True)
    monkeypatch.setattr(wti_nps, "_SshTransport", lambda *args, **kwargs: nps)
    dev = WtiNps(log=False, read_timeout=1.0)
    dev.connect("10.0.0.5")
    assert dev.is_connected()
    assert dev.port == 22


def test_shell_bad_password(monkeypatch):
    """ a rejected password leaves the driver disconnected """
    nps = FakeNps(login=True, password="secret")
    monkeypatch.setattr(wti_nps, "_SshTransport", lambda *args, **kwargs: nps)
    dev = WtiNps(log=False, read_timeout=1.0)
    dev.connect("10.0.0.5", password="wrong")
    assert not dev.is_connected()
    assert nps.closed


def test_outlet_on_off(pdu, fake):
    """ switching sends the ,Y form and updates the cache from the reply screen """
    assert pdu.outlet_on(3) is True
    assert fake.commands[-1] == "/ON 3,Y"
    assert pdu.outlets["3"]["on"] is True
    assert pdu.outlet_off(3) is True
    assert fake.commands[-1] == "/OFF 3,Y"
    assert pdu.outlets["3"]["on"] is False


def test_outlet_list(pdu, fake):
    """ several outlets are joined with + """
    assert pdu.outlet_on([1, 2, 5]) is True
    assert fake.commands[-1] == "/ON 1+2+5,Y"
    assert fake.states[:5] == [True, True, False, False, True]


def test_outlet_cycle(pdu, fake):
    """ outlet_cycle sends /BOOT """
    assert pdu.outlet_cycle(4) is True
    assert fake.commands[-1] == "/BOOT 4,Y"


def test_all_outlets_default(pdu, fake):
    """ all_outlets_default sends /DPL """
    assert pdu.all_outlets_default() is True
    assert fake.commands[-1] == "/DPL,Y"
    assert all(o["on"] for o in pdu.outlets.values())


def test_outlet_status(pdu, fake):
    """ outlet_status re-reads /S """
    fake.states[1] = True
    assert pdu.outlet_status(2) is True
    assert fake.commands[-1] == "/S"
    assert pdu.outlet_status(1) is False


def test_invalid_outlet_not_sent(pdu, fake):
    """ outlets outside the plug table are rejected before sending """
    sent = len(fake.commands)
    assert pdu.outlet_on(0) is False
    assert pdu.outlet_on(9) is False
    assert pdu.outlet_on("1,Y") is False
    assert pdu.outlet_on([]) is False
    assert len(fake.commands) == sent


def test_device_error_reported(pdu, fake):
    """ an error line in the reply makes the command fail """
    pdu.outlets["9"] = dict(pdu.outlets["1"], plug="9")  # pretend plug 9 exists
    assert pdu.outlet_on(9) is False
    assert fake.commands[-1] == "/ON 9,Y"
    assert "Invalid Plug" in pdu.status_string


def test_requires_initialize(fake):  # pylint: disable=unused-argument
    """ switching needs initialize() first """
    dev = WtiNps(log=False, read_timeout=1.0)
    dev.connect("10.0.0.5")
    assert dev.outlet_on(1) is False
    assert dev.status_string == "Device is not initialized"


def test_get_atomic_value(pdu, fake):
    """ outlet items take a numeric suffix """
    fake.names[0] = "Camera"
    assert pdu.get_atomic_value("name1") == "Camera"
    assert pdu.get_atomic_value("state1") is False
    assert pdu.get_atomic_value("boot_delay1") == pytest.approx(0.5)
    assert pdu.get_atomic_value("priority2") == 2
    assert pdu.get_atomic_value("model") == "NPS-8HD20-1"
    assert pdu.get_atomic_value("temperature") == pytest.approx(24.5)
    assert pdu.get_atomic_value("bogus1") is None
    assert pdu.get_atomic_value("state") is None


def test_disconnect_logs_out(pdu, fake):
    """ disconnect sends /X and closes """
    pdu.disconnect()
    assert fake.commands[-1] == "/X"
    assert fake.closed
    assert not pdu.is_connected()
