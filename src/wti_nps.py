"""Class for WTI NPS Series switched PDUs.

The NPS is controlled through its text Command Line Interface (CLI) over
SSH (enabled by default, port 22).  Commands are ASCII strings
starting with '/' and ending with [Enter]; the unit replies with a text
screen followed by a command prompt (e.g. "NPS>").  See the WTI User's
Guide (Part No. 14527, Rev. C), Section 12, for the full command set.
"""
from __future__ import annotations

import re
import socket
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple, Union

import paramiko

try:
    from hardware_device_base import HardwareSensorBase
except ModuleNotFoundError:
    from hardware_device_base.hardware_device_base import HardwareSensorBase  # type: ignore

Outlet = Union[int, str]

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LOGIN_USER_RE = re.compile(r"(user\s*name|login)\s*:\s*$", re.IGNORECASE)
LOGIN_PASS_RE = re.compile(r"password\s*:\s*$", re.IGNORECASE)
CONFIRM_RE = re.compile(r"sure\??\s*\(?y/n\)?\s*:?\s*$", re.IGNORECASE)
ERROR_RE = re.compile(r"invalid|error|denied|not allowed|unknown", re.IGNORECASE)
TEMPERATURE_RE = re.compile(r"temperature\D*?(-?\d+(?:\.\d+)?)", re.IGNORECASE)
PLUG_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def trailing_int(s: str) -> Optional[Tuple[str, int]]:
    """ return trailing integer from string or None """
    m = re.search(r"^(.+?)(\d+)$", s)
    return (m.group(1), int(m.group(2))) if m else None


def _column(row: Dict[str, str], *keys: str) -> Optional[str]:
    """Return the first cell whose header contains any of keys"""
    for name, value in row.items():
        if any(k in name for k in keys):
            return value
    return None


def parse_plug_status(text: str) -> Dict[str, Dict[str, Union[str, bool, int, float, None]]]:
    """Parse the Plug Status table from a /S screen.

    Columns are located by their header names rather than position, since
    the columns shown vary by model and firmware version.  The status
    column may include an asterisk, which means the plug is busy finishing
    a previous command.

    :param text: /S screen text.
    :return: Dict keyed by plug id (e.g. "1"), each a dict with keys
        plug, name, on, busy, boot_delay, default_on and priority.
    """
    # pylint: disable=too-many-locals
    plugs: Dict[str, Dict[str, Union[str, bool, int, float, None]]] = {}
    header: Optional[List[str]] = None
    for line in text.splitlines():
        if "|" not in line:
            if header is not None and plugs and line.strip():
                break  # end of table
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            lower = [c.lower() for c in cells]
            if any(k in lower[0] for k in ("plug", "circuit")) and \
                    any("status" in c for c in lower):
                header = lower
            continue
        if re.fullmatch(r"[\s\-+=|]*", line) or len(cells) < len(header):
            continue
        row = dict(zip(header, cells))
        plug = cells[0]
        status = (_column(row, "status") or "").upper()
        delay = re.search(r"\d+(?:\.\d+)?", _column(row, "boot", "delay") or "")
        default = (_column(row, "default") or "").upper()
        priority = re.search(r"\d+", _column(row, "priority") or "")
        plugs[plug] = {
            "plug": plug,
            "name": _column(row, "name") or "",
            "on": re.search(r"\bON\b", status) is not None,
            "busy": "*" in status,
            "boot_delay": float(delay.group(0)) if delay else None,
            "default_on": (re.search(r"\bON\b", default) is not None) if default else None,
            "priority": int(priority.group(0)) if priority else None,
        }
    return plugs


def parse_unit_info(text: str) -> Dict[str, str]:
    """Parse "Key: Value" lines from a /J * screen into a dict."""
    info: Dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            key, value = key.strip(), value.strip()
            if key and value:
                info[key] = value
    return info


class _SshTransport:
    """Interactive SSH shell session."""

    def __init__(self, host: str, port: int, username: str, password: str,
                 timeout: float) -> None:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(hostname=host, port=port, username=username,
                            password=password, timeout=timeout,
                            look_for_keys=False, allow_agent=False)
        # wide terminal so status screens are not wrapped
        self.chan = self.client.invoke_shell(width=200, height=200)

    def send(self, data: bytes) -> None:
        """Send data to the device."""
        self.chan.sendall(data)

    def recv(self, timeout: float) -> bytes:
        """Return received data, or b"" if none arrived within timeout."""
        self.chan.settimeout(timeout)
        try:
            data = self.chan.recv(4096)
        except (socket.timeout, BlockingIOError):
            return b""
        if not data:
            raise ConnectionError("Connection closed by device")
        return data

    def close(self) -> None:
        """Close the SSH session."""
        self.client.close()


# pylint: disable=too-many-instance-attributes,too-many-public-methods
class WtiNps(HardwareSensorBase):
    """Class for WTI NPS Series switched PDUs"""

    def __init__(self, log: bool = True,
                 logfile: str = __name__.rsplit(".", 1)[-1],
                 read_timeout: float = 5.0,
                 prompt_pattern: str = r"[\w.\-]+>\s*$"):

        super().__init__(log, logfile)

        # _query holds the lock across _send_command/_read_reply, which also lock
        self.lock = threading.RLock()

        # Device properties
        self.outlet_count: int = 0
        self.outlets: Dict[str, Dict[str, Union[str, bool, int, float, None]]] = {}
        self.unit_info: Dict[str, str] = {}
        self.model: str = ""
        self.version: str = ""
        self.serial: str = ""
        self.temperature: Optional[float] = None

        # Connection params
        self.host: str = ""
        self.port: int = 0
        self.username: str = ""
        self.password: str = ""
        self.read_timeout = read_timeout
        self._prompt_re = re.compile(prompt_pattern)
        self._eol = "\r"
        self._transport: Optional[_SshTransport] = None

        self.outlet_items = {
            "state": "on",
            "busy": "busy",
            "name": "name",
            "boot_delay": "boot_delay",
            "default": "default_on",
            "priority": "priority",
        }

    def connect(self, host: str, port: int = 22,  # pylint: disable=W0221
                username: str = "super", password: str = "super") -> None:
        """Connect to WTI NPS over SSH and open the CLI

        :param host: Hostname or IP address (factory default 192.168.168.168)
        :param port: SSH port
        :param username: Login name (factory default "super")
        :param password: Password (factory default "super")
        """
        if self.is_connected():
            self.disconnect()

        self.host = host
        self.port = port
        self.username = username
        self.password = password

        try:
            self._transport = _SshTransport(host, port, username, password,
                                            self.read_timeout)
        except (OSError, paramiko.SSHException) as ex:
            self.report_error(f"Could not connect to {host}:{port}: {ex}")
            self._transport = None
            return

        self._set_connected(True)
        if not self._login():
            self.disconnect()
            return
        self.report_info(f"Connected to {host}:{port}")

    def _login(self) -> bool:
        """Wait for the command prompt, answering any login prompts in the shell"""
        sent_user = sent_pass = False
        deadline = time.monotonic() + self.read_timeout
        buf = ""
        while time.monotonic() < deadline:
            chunk = self._recv(0.2)
            if chunk is None:
                return False
            buf += chunk
            tail = buf.rsplit("\n", 1)[-1]
            if self._prompt_re.search(tail):
                return True
            if LOGIN_USER_RE.search(tail):
                if sent_user:
                    self.report_error("Login failed: username or password rejected")
                    return False
                self._write(self.username)
                sent_user = True
                buf = ""
            elif LOGIN_PASS_RE.search(tail):
                if sent_pass:
                    self.report_error("Login failed: password rejected")
                    return False
                self._write(self.password)
                sent_pass = True
                buf = ""
        self.report_error(f"Login failed: no command prompt (got {buf.strip()!r})")
        return False

    def disconnect(self) -> None:
        """Log out and disconnect from WTI NPS"""
        if self._transport is not None:
            try:
                self._write("/X")
            except (OSError, ConnectionError):
                pass
            self._transport.close()
        self._transport = None
        self._set_connected(False)
        self.report_info("Disconnected")

    def _write(self, text: str) -> None:
        """Write one line to the device"""
        assert self._transport is not None
        self._transport.send((text + self._eol).encode("ascii"))

    def _recv(self, timeout: float) -> Optional[str]:
        """Read available text with ANSI escapes removed, None if the link dropped"""
        assert self._transport is not None
        try:
            data = self._transport.recv(timeout)
        except (OSError, ConnectionError) as ex:
            self.report_error(f"Read failed: {ex}")
            self._set_connected(False)
            return None
        return ANSI_RE.sub("", data.decode("ascii", errors="replace"))

    def _send_command(self, command: str, *args, **kwargs) -> bool:  # pylint: disable=unused-argument
        """Send a CLI command to WTI NPS"""
        if self._transport is None or not self.is_connected():
            self.report_error("Device is not connected")
            return False
        with self.lock:
            # discard anything left over from a previous command
            while self._recv(0.0):
                pass
            try:
                self._write(command)
            except (OSError, ConnectionError) as ex:
                self.report_error(f"Failed to send command '{command}': {ex}")
                return False
        self.report_debug(f"Sent: {command}")
        return True

    def _read_reply(self) -> Union[str, None]:
        """Read the reply up to the next command prompt"""
        if self._transport is None or not self.is_connected():
            self.report_error("Device is not connected")
            return None
        with self.lock:
            deadline = time.monotonic() + self.read_timeout
            buf = ""
            confirmed = False
            while time.monotonic() < deadline:
                chunk = self._recv(0.2)
                if chunk is None:
                    return None
                buf += chunk
                tail = buf.rsplit("\n", 1)[-1]
                if self._prompt_re.search(tail):
                    self.report_debug(f"Received: {buf!r}")
                    return buf
                if CONFIRM_RE.search(tail) and not confirmed:
                    # only seen if ",Y" was not accepted
                    self._write("Y")
                    confirmed = True
        self.report_error(f"Timed out waiting for prompt (got {buf.strip()!r})")
        return None

    def _query(self, command: str) -> Optional[str]:
        """Send command and return reply without the echoed command and prompt"""
        with self.lock:
            if not self._send_command(command):
                return None
            reply = self._read_reply()
        if reply is None:
            return None
        lines = reply.replace("\r", "").split("\n")
        # drop echoed command and trailing prompt
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and lines[0].strip().endswith(command):
            lines.pop(0)
        if lines and self._prompt_re.search(lines[-1]):
            lines.pop()
        return "\n".join(lines)

    def _control(self, command: str) -> bool:
        """Send a switching command, check for errors and update outlet cache"""
        reply = self._query(command)
        if reply is None:
            return False
        errors = [ln.strip() for ln in reply.splitlines()
                  if "|" not in ln and ERROR_RE.search(ln)]
        if errors:
            self.report_error(f"{command} failed: {'; '.join(errors)}")
            return False
        # the unit shows the plug status screen after switching, unless in Automated Mode
        self._update_status(reply)
        return True

    def _update_status(self, text: str) -> bool:
        """Update cached outlet and temperature values from a status screen"""
        plugs = parse_plug_status(text)
        if not plugs:
            return False
        self.outlets = plugs
        self.outlet_count = len(plugs)
        temp = TEMPERATURE_RE.search(text)
        if temp:
            self.temperature = float(temp.group(1))
        return True

    def _plug_id(self, outlet: Outlet) -> Optional[str]:
        """Validate an outlet number or plug id and return it as a string"""
        plug = str(outlet).strip()
        if not PLUG_ID_RE.match(plug):
            self.report_error(f"Invalid outlet: {outlet!r}")
            return None
        if plug not in self.outlets:
            self.report_error(f"Outlet {plug} not found, valid: {', '.join(self.outlets)}")
            return None
        return plug

    def _ready(self, outlets: Union[Outlet, Sequence[Outlet]]) -> Optional[str]:
        """Check readiness and return the CLI plug argument, e.g. "1+3" """
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None
        if not self.initialized:
            self.report_error("Device is not initialized")
            return None
        if isinstance(outlets, (int, str)):
            outlets = [outlets]
        if not outlets:
            self.report_error("No outlets specified")
            return None
        plugs = [self._plug_id(o) for o in outlets]
        if any(p is None for p in plugs):
            return None
        return "+".join(str(p) for p in plugs)

    def initialize(self) -> bool:
        """Read unit information and outlet status"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            self.initialized = False
            return False

        info = self._query("/J *")
        if info is not None:
            self.unit_info = parse_unit_info(info)
            for key, value in self.unit_info.items():
                k = key.lower()
                if "model" in k and not self.model:
                    self.model = value
                elif "version" in k and not self.version:
                    self.version = value
                elif "serial" in k and not self.serial:
                    self.serial = value

        if self.get_outlets() is None:
            self.report_error("Could not read plug status")
            self.initialized = False
            return False
        self.initialized = True
        self.report_info(f"Initialized {self.model or 'WTI'} with {self.outlet_count} outlets, "
                         f"firmware {self.version or 'unknown'}")
        return True

    def get_outlets(self) -> Optional[Dict[str, Dict[str, Union[str, bool, int, float, None]]]]:
        """Read the plug status screen (/S) for all outlets

        :return: Dict keyed by plug id, see parse_plug_status(), or None on failure
        """
        reply = self._query("/S")
        if reply is None:
            return None
        if not self._update_status(reply):
            self.report_error("No plug status table in /S reply")
            return None
        return self.outlets

    def outlet_status(self, outlet: Outlet) -> Optional[bool]:
        """Read outlet state, True if on"""
        if self._ready(outlet) is None or self.get_outlets() is None:
            return None
        plug = self._plug_id(outlet)
        return None if plug is None else bool(self.outlets[plug]["on"])

    def get_outlet_name(self, outlet: Outlet) -> Optional[str]:
        """Return cached outlet name"""
        plug = self._ready(outlet)
        if plug is None:
            return None
        return str(self.outlets[plug]["name"])

    def outlet_on(self, outlet: Union[Outlet, Sequence[Outlet]]) -> bool:
        """Switch outlet(s) on

        :param outlet: Outlet number or plug id, or a list of them
        """
        plugs = self._ready(outlet)
        return plugs is not None and self._control(f"/ON {plugs},Y")

    def outlet_off(self, outlet: Union[Outlet, Sequence[Outlet]]) -> bool:
        """Switch outlet(s) off

        :param outlet: Outlet number or plug id, or a list of them
        """
        plugs = self._ready(outlet)
        return plugs is not None and self._control(f"/OFF {plugs},Y")

    def outlet_cycle(self, outlet: Union[Outlet, Sequence[Outlet]]) -> bool:
        """Power cycle outlet(s): off, wait for the boot delay, then on

        :param outlet: Outlet number or plug id, or a list of them
        """
        plugs = self._ready(outlet)
        return plugs is not None and self._control(f"/BOOT {plugs},Y")

    def all_outlets_default(self) -> bool:
        """Set every outlet to its Power Up Default state (/DPL)"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return False
        if not self.initialized:
            self.report_error("Device is not initialized")
            return False
        return self._control("/DPL,Y")

    def get_temperature(self) -> Optional[float]:
        """Read the ambient temperature shown on the plug status screen"""
        if self.get_outlets() is None:
            return None
        if self.temperature is None:
            self.report_warning("No temperature reading on plug status screen")
        return self.temperature

    def get_atomic_value(self, item: str = "") -> Union[float, int, str, bool, None]:
        """Get atomic values from WTI NPS

        Device items: model, version, serial, temperature, outlet_count.
        Outlet items take the outlet number as a suffix, e.g. "state3":
        state, busy, name, boot_delay, default, priority.
        """
        # pylint: disable=too-many-return-statements
        if item in ("model", "version", "serial", "outlet_count"):
            if not self.initialized:
                self.report_error("Device is not initialized")
                return None
            return getattr(self, item)
        if item == "temperature":
            return self.get_temperature()

        intup = trailing_int(item)
        if intup is None:
            self.report_error(f"Unknown item '{item}' (outlet items need an outlet number suffix)")
            return None
        name, n = intup
        if name not in self.outlet_items:
            self.report_error(f"Outlet item '{name}' not found, "
                              f"valid: {', '.join(self.outlet_items)}")
            return None
        if self._ready(n) is None or self.get_outlets() is None:
            return None
        plug = self._plug_id(n)
        if plug is None:
            return None
        return self.outlets[plug][self.outlet_items[name]]
