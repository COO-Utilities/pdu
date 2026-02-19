"""Class for DLI DC 3 Power Controller"""
from typing import Optional, Union, Pattern, List, Tuple

from paramiko.channel import ChannelFile

try:
    from hardware_device_base import HardwareDeviceBase
except ModuleNotFoundError:
    from hardware_device_base.hardware_device_base import HardwareDeviceBase  # type: ignore

import paramiko

class Dlidc3(HardwareDeviceBase):
    """Class for DLI DC 3 Power Controller"""
    def __init__(self, log: bool = True,
                 logfile: str = __name__.rsplit(".", 1)[-1]):

        super().__init__(log, logfile)

        # Device properties
        self.outlet_count: int = 0
        self.outlet_names: List[str] = []
        self.outlet_onoff: List[int] = []
        self.model: str = ""
        self.version: str = ""

        # Connection params
        self.host: str = ""
        self.username: str = ""
        self.password: str = ""
        self.ssh: Optional[paramiko.SSHClient] = None
        self.stdout: Optional[ChannelFile] = None

    # pylint: disable=W0221
    def connect(self, host:str, username:str, password:str,
                *args, **kwargs) -> None:
        """Connect to DLI DC 3 Power Controller"""

        self.host = host
        self.username = username
        self.password = password

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(hostname=host, username=username, password=password)
        self._set_connected(True)

    def disconnect(self) -> None:
        """Disconnect from DLI DC 3 Power Controller"""
        self.ssh.close()
        self._set_connected(False)

    def _send_command(self, cmd: str, *args, **kwargs) -> bool:
        """Send command to DLI DC 3 Power Controller"""

        if not self.is_connected():
            self.report_error("Device is not connected")
            return False

        response = self.ssh.exec_command(cmd)

        ssh_error = response[2].read().decode("utf-8")
        if ssh_error:
            self.report_error(ssh_error)
            return False
        self.stdout = response[1]

        return True

    def _read_reply(self) -> Union[str, None]:
        """Read reply from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None
        return self.stdout.read().decode("utf-8")
