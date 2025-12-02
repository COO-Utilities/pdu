from __future__ import annotations

import time
import socket
from typing import Union, Optional

try:
    from hardware_device_base import HardwareDeviceBase
except ModuleNotFoundError:
    from hardware_device_base.hardware_device_base import HardwareDeviceBase  # type: ignore

class EatonEMAT(HardwareDeviceBase):
    """
    Eaton EMAT-08/10 PDU low-level interface.

    This class speaks a simple ASCII-over-TCP protocol:
    - Opens a TCP socket to the PDU.
    - Send commands terminated by CRLF.
    - Reads back whatever the unit replies until there's a pause
      longer than `read_timeout`.

    Assumes:
    - No interactive login sequence.
    - The PDU accepts outlet control / query commands directly once connected.
      (If your hardware needs auth commands first, you can send them manually
       right after connect() using _send_command().)

    Command templates
    -----------------
    `cmd_outlet_on`, `cmd_outlet_off`, etc. are just format strings.
    You can override them after construction to match whatever your
    EMAT firmware expects.

    Example
    -------
    >>> from EMAT08_10 import EatonEMAT
    >>> pdu = EatonEMAT()
    >>> pdu.connect("192.168.1.50", 1234)
    True
    >>> pdu.outlet_on(3)
    True
    >>> print(pdu.outlet_status(3))
    'Outlet 3: ON'
    >>> pdu.disconnect()
    """

    def __init__(
        self,
        log: bool = True,
        logfile: str = __name__.rsplit(".", 1)[-1],
        read_timeout: float = 3.0,
        line_terminator: str = "\r\n",
    ) -> None:
        super().__init__(log, logfile)

        # TCP socket for this PDU
        self._sock: Optional[socket.socket] = None

        # read timeout in seconds
        self._timeout = float(read_timeout)

        # line terminator appended to each outbound command
        self._eol = line_terminator

        # {n} will be replaced with 1-based outlet index.
        self.cmd_outlet_on: str = "set PDU.OutletSystem.Outlet[{n}].DelayBeforeStartup 0"
        self.cmd_outlet_off: str = "set PDU.OutletSystem.Outlet[{n}].DelayBeforeShutdown 0"
        self.cmd_outlet_status: str = "PDU.OutletSystem.Outlet[{n}].PresentStatus.SwitchOnOff"
        self.cmd_device_model: str = "PDU.PowerSummary.iManufacturer"
        self.cmd_firmware_ver: str = "PDU.PowerSummary.iVersion"

    def connect( 
        self,
        host: str,
        port: int,
        *,
        username: Optional[str] = None,  # kept for API compatibility, unused
        password: Optional[str] = None,  # kept for API compatibility, unused
    ) -> bool:
        """
        Open a raw TCP connection to the PDU.

        Parameters
        ----------
        host : str
            Hostname or IP of the PDU.
        port : int
            TCP port that accepts ASCII control commands.
        username : str, optional
            Unused in TCP mode (kept for signature compatibility).
        password : str, optional
            Unused in TCP mode (kept for signature compatibility).

        Returns
        -------
        bool
            True if connected, False otherwise.
        """
        if not self.validate_connection_params((host, port)):
            return False

        try:
            self._sock = socket.create_connection((host, port), timeout=self._timeout)
            self._sock.settimeout(self._timeout)
            self.logger.info("Connected (tcp) to %s:%d", host, port)
            self._set_connected(True)
            return True
        except (socket.timeout, socket.error) as e:
            self.logger.error("Connection error: %s", getattr(e, "strerror", str(e)))
            self._sock = None
            self._set_connected(False)
            return False

    def disconnect(self) -> None:
        """
        Close the TCP connection.
        """
        try:
            if self._sock is not None:
                try:
                    # If the PDU has a polite "logout" command, you can send it here.
                    # For pure TCP ASCII, usually not needed.
                    pass
                except Exception:
                    pass
                self._sock.close()
        finally:
            self._sock = None
            self._set_connected(False)
            self.logger.info("Disconnected")

    def _send_command(self, command: str, *args) -> bool:
        """
        Send a command string to the device over TCP.

        Returns
        -------
        bool
            True on success, False on failure.
        """
        if not self.is_connected() or self._sock is None:
            self.logger.error("Device is not connected")
            return False

        # Support positional formatting like cmd.format(n=3) OR cmd.format(3)
        if args:
            try:
                command = command.format(*args)
            except Exception:
                # If formatting fails, leave it alone
                pass

        data = (command + self._eol).encode()

        try:
            with self.lock:
                self._sock.sendall(data)
            self.logger.debug("Sent command: %s", command)
            return True
        except (socket.timeout, socket.error) as e:
            self.logger.error("Write failed: %s", getattr(e, "strerror", str(e)))
            return False

    def _read_reply(self) -> Union[str, None]:
        """
        Read reply data from the PDU until there's a quiet period.

        Returns
        -------
        str | None
            ASCII reply, or empty string "" if nothing, or None on error.
        """
        if not self.is_connected() or self._sock is None:
            self.logger.error("Device is not connected")
            return None

        try:
            self._sock.settimeout(self._timeout)

            chunks: list[bytes] = []
            start = time.time()

            while True:
                try:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        # remote closed socket or sent nothing more
                        break
                    chunks.append(chunk)

                    # If we've been reading for longer than timeout, bail.
                    if time.time() - start > self._timeout:
                        break

                except socket.timeout:
                    # No more data within timeout => stop
                    break

            data = b"".join(chunks)
            self.logger.debug("Reply (tcp): %r", data)
            return data.decode(errors="replace") if data else ""
        except (socket.timeout, socket.error) as e:
            self.logger.error("Read failed: %s", getattr(e, "strerror", str(e)))
            return None

    def get_atomic_value(self, item: str) -> Union[str, None]:
        """
        Fetch a basic property using a single command.

        Returns
        -------
        str | None
            Reply string (raw), or None on error.
        """
        key = item.lower()
        mapping = {
            "model": self.cmd_device_model,
            "firmware": self.cmd_firmware_ver,
        }

        cmd = mapping.get(key)
        if not cmd:
            self.logger.error("Unsupported item: %s", item)
            return None

        if not self._send_command(cmd):
            return None

        return self._read_reply()

    def outlet_on(self, n: int) -> bool:
        """
        Turn outlet n (1-based) ON.
        """
        if n < 1:
            self.logger.error("Outlet index must be >= 1")
            return False
        cmd = self.cmd_outlet_on.format(n=n)
        return self._send_command(cmd)

    def outlet_off(self, n: int) -> bool:
        """
        Turn outlet n (1-based) OFF.
        """
        if n < 1:
            self.logger.error("Outlet index must be >= 1")
            return False
        cmd = self.cmd_outlet_off.format(n=n)
        return self._send_command(cmd)

    def outlet_status(self, n: int) -> Optional[str]:
        """
        Query outlet n (1-based) status.
        We send the status command and then read one reply.
        """
        if n < 1:
            self.logger.error("Outlet index must be >= 1")
            return None

        cmd = self.cmd_outlet_status.format(n=n)
        if not self._send_command(cmd):
            return None

        return self._read_reply()
