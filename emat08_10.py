""" Class for Eaton EMAT-08/10 power distribution unit. """
from __future__ import annotations

import asyncio
import re
import time
import threading
from typing import Optional, Union, Pattern, List

try:
    from hardware_device_base import HardwareDeviceBase
except ModuleNotFoundError:
    from hardware_device_base.hardware_device_base import HardwareDeviceBase  # type: ignore

import telnetlib3


class EatonEMAT(HardwareDeviceBase):
    """
    Eaton EMAT-08/10 PDU low-level interface over Telnet using telnetlib3.

    This class opens a Telnet session and executes ASCII commands.
    Each `_send_command()` call writes the command (CRLF) and reads until a prompt.
    Public API is synchronous.

    Example
    -------
    Basic usage:
        >>> from emat08_10 import EatonEMAT
        >>> pdu = EatonEMAT()
        >>> pdu.connect("192.168.1.50", 23, username="admin", password="secret")
        True
        >>> pdu.outlet_on(3)              # set PDU.OutletSystem.Outlet[3].DelayBeforeStartup 0
        True
        >>> pdu.outlet_status(3)          # get PDU.OutletSystem.Outlet[3].PresentStatus.SwitchOnOff
        'On'  # device-dependent text
        >>> pdu.get_atomic_value("model")       # get PDU.PowerSummary.iManufacturer
        'Eaton ...'
        >>> pdu.get_atomic_value("firmware")    # get PDU.PowerSummary.iVersion
        '1.2.3'
        >>> pdu.disconnect()
    """
    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        log: bool = True,
        logfile: str = __name__.rsplit(".", 1)[-1],
        read_timeout: float = 3.0,
        *,
        prompt_pattern: str | bytes = rb"[>#]\s*$",  # end-of-line '>' or '#'
        line_terminator: str = "\r\n",
    ) -> None:
        # pylint: disable=too-many-arguments
        super().__init__(log, logfile)

        # Telnet reader/writer
        self._reader: Optional[telnetlib3.TelnetReaderUnicode] = None
        self._writer: Optional[telnetlib3.TelnetWriterUnicode] = None

        # timeouts & framing
        self._timeout = float(read_timeout)
        self._eol = line_terminator

        # prompt regex
        if isinstance(prompt_pattern, bytes):
            prompt_pattern = prompt_pattern.decode(errors="ignore")
        self._prompt_re: Pattern[str] = re.compile(prompt_pattern, re.MULTILINE)

        # Buffer for last command’s reply
        self._last_reply: Optional[str] = None

        # Device properties
        self.outlet_count: int = 0
        self.outlet_names: List[str] = []
        self.outlet_onoff: List[int] = []
        self.manufacturer: str = ""
        self.model: str = ""
        self.version: str = ""
        self.serial: str = ""
        self.initialized = False

        self.set_commands = {
            "outlet_on": "PDU.OutletSystem.Outlet[{n}].DelayBeforeStartup 0",
            "outlet_off": "PDU.OutletSystem.Outlet[{n}].DelayBeforeShutdown 0",
            # ModuleReset: reset statistics for outlet
            "reset_statistics": "PDU.OutletSystem.Outlet[{n}].Statistic[5].ModuleReset 1",
            # AutomaticRestart, p: 0 - not powered, 1 - powered, 2 - last state at startup
            "set_auto_restart": "PDU.OutletSystem.Outlet[{n}].AutomaticRestart {p}"
        }
        # GET Command templates for outlet items (override per firmware). {n} is 1-based
        self.get_outlet_commands = {
            # SwitchOnOff: 0 - Off, 1 - On
            "outlet_status": "PDU.OutletSystem.Outlet[{n}].PresentStatus.SwitchOnOff",
            # OverCurrent: 0 - Normal, 1 - Low warning, 2 - Low critical,
            #              3 - High warning, 4 - High critical
            "overcurrent_status": "PDU.OutletSystem.Outlet[{n}].PresentStatus.OverCurrent",
            # ActivePower, ApparentPower, ReactivePower: Watts
            "active_power": "PDU.OutletSystem.Outlet[{n}].ActivePower",
            "apparent_power": "PDU.OutletSystem.Outlet[{n}].ApparentPower",
            "reactive_power": "PDU.OutletSystem.Outlet[{n}].ReactivePower",
            # ConfigCurrent, Current: Amps
            "config_current": "PDU.OutletSystem.Outlet[{n}].ConfigCurrent",
            "current": "PDU.OutletSystem.Outlet[{n}].Current",
            # Type: 0..255
            "type": "PDU.OutletSystem.Outlet[{n}].Type",
            "peak_factor": "PDU.OutletSystem.Outlet[{n}].PeakFactor",
            "phase_id": "PDU.OutletSystem.Outlet[{n}].PhaseID",
            "pole_id": "PDU.OutletSystem.Outlet[{n}].PoleID",
            "power_factor": "PDU.OutletSystem.Outlet[{n}].PowerFactor",
            # Switchable: 0 - Disabled, 1 - Enabled
            "switchable": "PDU.OutletSystem.Outlet[{n}].Switchable",
            # iDesignator, iName: <string>
            "designator": "PDU.OutletSystem.Outlet[{n}].iDesignator",
            "name": "PDU.OutletSystem.Outlet[{n}].iName",
            # OutletID: <int>
            "outlet_id": "PDU.OutletSystem.Outlet[{n}].OutletID",
            # Energy: Watt-hours
            "energy": "PDU.OutletSystem.Outlet[{n}].Statistic[5].Energy",
            # Reset.Time: Unix sec of last reset
            "reset_time": "PDU.OutletSystem.Outlet[{n}].Statistic[5].ResetTime",
            # Reset.Energy: Energy at last reset
            "reset_energy": "PDU.OutletSystem.Outlet[{n}].Statistic[5].ResetEnergy",
            # AutomaticRestart: 0 - not powered, 1 - powered, 2 - last state at startup
            "auto_restart": "PDU.OutletSystem.Outlet[{n}].AutomaticRestart",
        }
        # Command templates for device items.
        self.get_device_commands = {
            "model": "PDU.PowerSummary.iPartNumber",
            "version": "PDU.PowerSummary.iVersion",
            "manufacturer": "PDU.PowerSummary.iManufacturer",
            "serial_number": "PDU.PowerSummary.iSerialNumber",
            "outlet_count": "PDU.OutletSystem.Outlet.Count",
        }

        # Optional login prompt substrings
        self._login_user_prompts: List[str] = ["login:", "username:", "user:"]
        self._login_pass_prompts: List[str] = ["password:"]

        # Dedicated asyncio loop in a background thread (prevents cross-loop issues)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start a private event loop in a background thread if not running."""
        if self._loop is not None and self._loop_thread and self._loop_thread.is_alive():
            return self._loop

        def _loop_runner(run_loop: asyncio.AbstractEventLoop) -> None:
            asyncio.set_event_loop(run_loop)
            run_loop.run_forever()

        loop = asyncio.new_event_loop()
        t = threading.Thread(target=_loop_runner, args=(loop,), name="telnetlib3-loop", daemon=True)
        t.start()
        self._loop = loop
        self._loop_thread = t
        return loop

    def _run(self, coro):
        """
        Submit a coroutine to the private loop and wait for the result.
        All telnetlib3 I/O must happen on that loop.
        """
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=self._timeout + 5.0)

    def connect( # pylint: disable=W0221
        self,
        host: str,
        port: int = 23,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> bool:
        """
        Open a Telnet connection to the PDU (telnetlib3).
        """
        if not self.validate_connection_params((host, port)):
            return False

        try:
            self._run(self._aconnect(host, port, username, password))
            ok = self._reader is not None and self._writer is not None
            self._set_connected(ok)
            if ok:
                self.report_info(f"Connected (telnetlib3) to {host}:{port}")
            return ok
        except Exception as e:
            self.report_error(f"Telnet connection error: {e}")
            self._reader = None
            self._writer = None
            self._set_connected(False)
            return False

    async def _aconnect(
        self,
        host: str,
        port: int,
        username: Optional[str],
        password: Optional[str],
    ) -> None:
        reader, writer = await asyncio.wait_for(
            telnetlib3.open_connection(
                host=host,
                port=port,
                encoding="utf8",
                connect_minwait=0.0,
            ),
            timeout=self._timeout,
        )

        self._reader = reader
        self._writer = writer

        # If creds provided, perform a minimal login
        if username:
            await self._await_any_prompt_and_write(self._login_user_prompts, username + self._eol)
        if password:
            await self._await_any_prompt_and_write(self._login_pass_prompts, password + self._eol)

        # wait for a prompt to indicate readiness
        await self._read_until_prompt()

    def disconnect(self) -> None:
        """Close the Telnet session and stop the private event loop."""
        try:
            try:
                self._run(self._adisconnect())
            except Exception:
                pass
        finally:
            self._reader = None
            self._writer = None
            self._last_reply = None
            self._set_connected(False)
            # stop loop thread
            if self._loop is not None:
                try:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                except Exception:
                    pass
            if self._loop_thread is not None:
                try:
                    self._loop_thread.join(timeout=2.0)
                except Exception:
                    pass
            self._loop = None
            self._loop_thread = None
            self.report_info("Disconnected")

    async def _adisconnect(self) -> None:
        if self._writer is not None:
            try:
                # best-effort polite exit
                self._writer.write("exit" + self._eol)
                await self._writer.drain()
            except Exception:
                pass
            try:
                self._writer.close()
                if hasattr(self._writer, "wait_closed"):
                    await self._writer.wait_closed()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _send_command(self, command: str, *args) -> bool: # pylint: disable=W0221
        """
        Send a command and buffer the reply (stdout-like text).
        """
        if not self.is_connected() or self._writer is None:
            self.report_error("Device is not connected")
            return False

        # Support positional formatting like cmd.format(n=3) OR cmd.format(3)
        if args:
            try:
                command = command.format(*args)
            except Exception:
                pass  # leave as-is

        try:
            with self.lock:
                reply = self._run(self._asend_and_read(command))
                self._last_reply = reply
            self.report_debug(f"Executed command: {command}")
            return True
        except Exception as e:
            self.report_error(f"Telnet exec failed: {e}")
            self._last_reply = None
            return False

    async def _asend_and_read(self, command: str) -> str:
        assert self._writer is not None
        # write command + EOL
        self._writer.write(command + self._eol)
        await self._writer.drain()
        # read until prompt, strip trailing prompt, return text
        data = await self._read_until_prompt()
        self.report_debug(f"Received data: {data}")
        retval = data.split(self._eol)[-2].split('\r')[0]
        return retval

    def _read_reply(self) -> Union[str, None]:
        """
        Return the buffered reply from the last `_send_command()`.
        """
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None
        return self._last_reply if self._last_reply is not None else ""

    def get_atomic_value(self, item: str, n:Union[int, str]=None) -> Union[str, None]:
        """ Retrieve atomic values

                :param item: String item to retrieve
                :param n: Outlet to retrieve item for (required for outlet items, not required for
                            device items).

                NOTE: n can be replaced with "x" to retrieve item values for all outlets
                """
        # pylint: disable=too-many-branches,too-many-return-statements
        if item in self.get_outlet_commands:
            if n is None:
                self.report_error("Outlet index (n) must be an integer or string x")
                return None
            if isinstance(n, int):
                if not self.initialized:
                    self.report_error("Device is not initialized")
                    return None
                if n < 1 or n > self.outlet_count:
                    self.report_error(f"Outlet index must be >= 1 or <= {self.outlet_count}")
                    return None
            if isinstance(n, str):
                if n != "x":
                    self.report_error("Outlet index (n) must be an integer or string x")
                    return None
            cmd = "get " + self.get_outlet_commands[item].format(n=n)

        elif item in self.get_device_commands:
            cmd = "get " + self.get_device_commands[item]

        elif "help" in item:
            print("Device items (no outlet number required):")
            for k in self.get_device_commands:
                print(k)
            print("\nOutlet items (outlet number or x required):")
            for k in self.get_outlet_commands:
                print(k)
            return None

        else:
            self.report_error(f"Item not found: {item}")
            return None

        if not self._send_command(cmd):
            return None

        return self._read_reply()

    def outlet_on(self, n: int) -> bool:
        """ Turn specified outlet on. """
        if not self.initialized:
            self.report_error("Device is not initialized")
            return False
        if n < 1 or n > self.outlet_count:
            self.report_error(f"Outlet index must be >= 1 or <= {self.outlet_count}")
            return False
        cmd = "set " + self.set_commands["outlet_on"].format(n=n)
        if self._send_command(cmd):
            self.outlet_onoff[n-1] = 1
            return True
        return False

    def outlet_off(self, n: int) -> bool:
        """ Turn specified outlet off. """
        if not self.initialized:
            self.report_error("Device is not initialized")
            return False
        if n < 1 or n > self.outlet_count:
            self.report_error(f"Outlet index must be >= 1 or <= {self.outlet_count}")
            return False
        cmd = "set " + self.set_commands["outlet_off"].format(n=n)
        if self._send_command(cmd):
            self.outlet_onoff[n-1] = 0
            return True
        return False

    def outlet_status(self, n: int) -> Optional[str]:
        """ Get outlet status. """
        if not self.initialized:
            self.report_error("Device is not initialized")
            return None
        if n < 1 or n > self.outlet_count:
            self.report_error(f"Outlet index must be >= 1 or <= {self.outlet_count}")
            return None
        cmd = "get " + self.get_outlet_commands["outlet_status"].format(n=n)
        if not self._send_command(cmd):
            return None
        return self._read_reply()

    def reset_statistics(self, n:int) -> bool:
        """ Reset energy statistics for given outlet. """
        if not self.initialized:
            self.report_error("Device is not initialized")
            return False
        if n < 1 or n > self.outlet_count:
            self.report_error(f"Outlet index must be >= 1 or <= {self.outlet_count}")
            return False
        cmd = "set " + self.set_commands["reset_statistics"].format(n=n)
        return self._send_command(cmd)

    def set_autostart(self, n: int, p: int) -> bool:
        """ Set autostart status for given outlet.
        n - outlet number (1-8)
        p - state: 0 - not powered at startup, 1 - powered at startup, 2 - last state at startup
        """
        if not self.initialized:
            self.report_error("Device is not initialized")
            return False
        if n < 1 or n > self.outlet_count:
            self.report_error(f"Outlet index must be >= 1 or <= {self.outlet_count}")
            return False
        if p < 0 or p > 2:
            self.report_error("Outlet autostart status must be between 0 and 2")
            return False
        cmd = "set " + self.set_commands["set_autostart"].format(n=n, p=p)
        return self._send_command(cmd)

    def initialize(self) -> bool:
        """ Initialize device properties. """
        if not self.is_connected():
            self.report_error("Device not connected")
            return False
        self.outlet_count = int(self.get_atomic_value("outlet_count"))
        self.manufacturer = self.get_atomic_value("manufacturer")
        self.model = self.get_atomic_value("model")
        self.version = self.get_atomic_value("version")
        self.serial = self.get_atomic_value("serial_number")
        names = self.get_atomic_value("name", "x")
        for name in names.split("|"):
            self.outlet_names.append(name)
        statuses = self.get_atomic_value("outlet_status", "x")
        for status in statuses.split("|"):
            self.outlet_onoff.append(int(status))
        self.initialized = True
        return True

    async def _await_any_prompt_and_write(self, prompts: List[str], to_write: str) -> None:
        """Wait for any of the prompt substrings, then write the string."""
        assert self._reader is not None and self._writer is not None
        deadline = time.time() + self._timeout
        buff = ""
        while time.time() < deadline:
            try:
                chunk = await asyncio.wait_for(
                    self._reader.read(1024),
                    timeout=max(0.05, self._timeout / 10.0),
                )
            except asyncio.TimeoutError:
                chunk = ""
            if chunk:
                buff += chunk
                low = buff.lower()
                if any(p.lower() in low for p in prompts):
                    self._writer.write(to_write)
                    await self._writer.drain()
                    return
            else:
                await asyncio.sleep(0.05)

    async def _read_until_prompt(self) -> str:
        """Read until prompt regex matches or timeout expires."""
        assert self._reader is not None
        deadline = time.time() + self._timeout
        buff = ""
        while time.time() < deadline:
            try:
                chunk = await asyncio.wait_for(
                    self._reader.read(1024),
                    timeout=max(0.05, self._timeout / 10.0),
                )
            except asyncio.TimeoutError:
                chunk = ""
            if chunk:
                buff += chunk
                if self._prompt_re.search(buff):
                    break
            else:
                await asyncio.sleep(0.05)
        return buff

    def _strip_prompt(self, data: str) -> str:
        """Remove trailing prompt line if it matches the regex."""
        lines = data.splitlines(keepends=True)
        if not lines:
            return data
        last = lines[-1]
        if self._prompt_re.search(last):
            return "".join(lines[:-1])
        return data
