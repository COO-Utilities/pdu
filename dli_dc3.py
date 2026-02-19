"""Class for DLI DC 3 Power Controller"""
from typing import Optional, Union, List

from paramiko.channel import ChannelFile
from emat08_10 import trailing_int

try:
    from hardware_device_base import HardwareSensorBase
except ModuleNotFoundError:
    from hardware_device_base.hardware_device_base import HardwareSensorBase  # type: ignore

import paramiko

GET_PREFIX = "uom get "
SET_PREFIX = "uom set "

# pylint: disable=too-many-instance-attributes
class Dlidc3(HardwareSensorBase):
    """Class for DLI DC 3 Power Controller"""
    def __init__(self, log: bool = True,
                 logfile: str = __name__.rsplit(".", 1)[-1]):

        super().__init__(log, logfile)

        # Device properties
        self.outlet_count: int = 8
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

        self.outlet_commands = {
            "name": ("relay/outlets/{outlet_num}/name", "str"),
            "state": ("relay/outlets/{outlet_num}/state", "bool"),
            "critical": ("relay/outlets/{outlet_num}/critical", "bool"),
            "cycle_delay": ("relay/outlets/{outlet_num}/cycle_delay", "int"),
            "locked": ("relay/outlets/{outlet_num}/locked", "bool"),
            "transient_state": ("relay/outlets/{outlet_num}/transient_state", "bool"),
            "physical_state": ("relay/outlets/{outlet_num}/physical_state", "bool")
        }

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

    def get_outlet_name(self, outlet_num:int) -> Union[str, None]:
        """Retrieve outlet name from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        cmd = GET_PREFIX + self.outlet_commands["name"][0].format(outlet_num=outlet_num)
        self._send_command(cmd)
        name = self._read_reply().strip()
        return name

    def set_outlet_name(self, outlet_num:int, outlet_name:str) -> None:
        """Set outlet name from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None
        cmd = SET_PREFIX + self.outlet_commands["name"][0].format(
            outlet_num=outlet_num) + " " + outlet_name
        self._send_command(cmd)
        _ = self._read_reply().strip()
        return None

    def get_outlet_state(self, outlet_num:int) -> Union[bool, None]:
        """Retrieve outlet state from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None
        cmd = GET_PREFIX + self.outlet_commands["state"][0].format(outlet_num=outlet_num)
        self._send_command(cmd)
        state = self._read_reply().strip()
        return "true" in state

    def set_outlet_state(self, outlet_num:int, outlet_state:bool) -> Union[bool, None]:
        """Set outlet state from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None
        cmd = SET_PREFIX + self.outlet_commands["state"][0].format(outlet_num=outlet_num) + \
            " true" if outlet_state else " false"
        self._send_command(cmd)
        _ = self._read_reply().strip()
        return True

    def get_atomic_value(self, item: str ="") -> Union[float, int, str, None]:
        """Get atomic values from DLI DC 3 Power Controller"""
        # pylint: too-many-return-statements
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        intup = trailing_int(item)
        # get value for specified outlet
        if intup is None:
            self.report_error(f"Must specify an outlet number (add as suffix to {item})")
            return None
        # get value for specific outlet
        n = intup[1]
        item = intup[0]
        # check outlet number
        if n < 1 or n > self.outlet_count:
            self.report_error(f"Outlet index must be >= 1 or <= {self.outlet_count}")
            return None
        if item not in self.outlet_commands:
            self.report_error(f"Outlet {item} not found in DLI DC 3")
            return None
        cmd = GET_PREFIX + self.outlet_commands[item][0].format(outlet_num=n)
        if not self._send_command(cmd):
            return None
        result = self._read_reply().strip()
        if result is None:
            self.report_error(f"Outlet {item} null return value")
            return None
        if "int" in self.outlet_commands[item][1]:
            try:
                result = int(result)
            except ValueError:
                self.report_error(f"Outlet {item} int parse error")
                result = None
        elif "bool" in self.outlet_commands[item][1]:
            try:
                result = "true" in result
            except ValueError:
                self.report_error(f"Outlet {item} bool parse error")
                result = None
        return result
