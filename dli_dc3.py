"""Class for DLI DC 3 Power Controller"""
from typing import Optional, Union, List
import paramiko
from paramiko.channel import ChannelFile
from emat08_10 import trailing_int

try:
    from hardware_device_base import HardwareSensorBase
except ModuleNotFoundError:
    from hardware_device_base.hardware_device_base import HardwareSensorBase  # type: ignore

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
        self.name: str = ""

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
        self.device_commands = {
            "model": "relay/model",
            "name": "relay/name",
            "version": "relay/version"
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
        try:
            self.ssh.connect(hostname=host, username=username, password=password)
        except paramiko.SSHException as ex:
            self.report_error(str(ex))
            return
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

        self.report_debug(cmd)

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

    def initialize(self) -> None:
        """Initialize DLI DC 3 Power Controller Class Instance"""
        # model
        cmd = GET_PREFIX + self.device_commands["model"]
        self._send_command(cmd)
        self.model = self._read_reply().strip()
        # name
        cmd = GET_PREFIX + self.device_commands["name"]
        self._send_command(cmd)
        self.name = self._read_reply().strip()
        # version
        cmd = GET_PREFIX + self.device_commands["version"]
        self._send_command(cmd)
        self.version = self._read_reply().strip()
        # outlet names and states
        for n in range(self.outlet_count):
            name = self.get_outlet_name(n)
            self.outlet_names.append(name)
            state = self.outlet_status(n)
            self.outlet_onoff.append(1 if state else 0)
        self.initialized = True

    def get_outlet_name(self, outlet_num:int) -> Union[str, None]:
        """Retrieve outlet name from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return None

        cmd = GET_PREFIX + self.outlet_commands["name"][0].format(outlet_num=outlet_num)
        if self._send_command(cmd):
            name = self._read_reply().strip()
            return name
        return None

    def set_outlet_name(self, outlet_num:int, outlet_name:str) -> None:
        """Set outlet name from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        if not self.initialized:
            self.report_error("Device is not initialized")
            return None

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return None

        cmd = SET_PREFIX + self.outlet_commands["name"][0].format(
            outlet_num=outlet_num) + " '\"" + outlet_name + "\"'"
        if self._send_command(cmd):
            _ = self._read_reply().strip()
            self.outlet_names[outlet_num] = self.get_outlet_name(outlet_num)
        return None

    def outlet_status(self, outlet_num:int) -> Optional[bool]:
        """Retrieve outlet state from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return None

        cmd = GET_PREFIX + self.outlet_commands["state"][0].format(outlet_num=outlet_num)
        if self._send_command(cmd):
            state = self._read_reply().strip()
            return "true" in state
        return None

    def outlet_on(self, outlet_num:int) -> bool:
        """Set outlet on"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return False

        if not self.initialized:
            self.report_error("Device is not initialized")
            return False

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return False

        cmd = SET_PREFIX + self.outlet_commands["state"][0].format(outlet_num=outlet_num) + " true"
        if self._send_command(cmd):
            _ = self._read_reply().strip()
            self.outlet_onoff[outlet_num] = 1
            return True
        return False

    def outlet_off(self, outlet_num:int) -> bool:
        """Set outlet off"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return False

        if not self.initialized:
            self.report_error("Device is not initialized")
            return False

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return False

        cmd = SET_PREFIX + self.outlet_commands["state"][0].format(outlet_num=outlet_num) + " false"
        if self._send_command(cmd):
            _ = self._read_reply().strip()
            self.outlet_onoff[outlet_num] = 0
            return True
        return False

    def lock_status(self, outlet_num:int) -> Optional[bool]:
        """Retrieve outlet state from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return None

        cmd = GET_PREFIX + self.outlet_commands["locked"][0].format(outlet_num=outlet_num)
        if self._send_command(cmd):
            state = self._read_reply().strip()
            return "true" in state
        return None

    def lock_outlet(self, outlet_num:int) -> bool:
        """Lock outlet state"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return False

        if not self.initialized:
            self.report_error("Device is not initialized")
            return False

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return False

        cmd = SET_PREFIX + self.outlet_commands["locked"][0].format(outlet_num=outlet_num) + " true"
        if self._send_command(cmd):
            _ = self._read_reply().strip()
            return True
        return False

    def unlock_outlet(self, outlet_num:int) -> bool:
        """Unlock outlet state"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return False

        if not self.initialized:
            self.report_error("Device is not initialized")
            return False

        # check outlet number
        if outlet_num < 0 or outlet_num >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
            return False

        cmd = SET_PREFIX + self.outlet_commands["locked"][0].format(outlet_num=outlet_num) + " false"
        if self._send_command(cmd):
            _ = self._read_reply().strip()
            return True
        return False

    def get_atomic_value(self, item: str ="") -> Union[float, int, str, None]:
        """Get atomic values from DLI DC 3 Power Controller"""
        # pylint: disable=too-many-return-statements
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        if not self.initialized:
            self.report_error("Device is not initialized")
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
        if n < 0 or n >= self.outlet_count:
            self.report_error(f"Outlet index must be >= 0 or < {self.outlet_count}")
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
            if "n" in result:
                self.report_warning(f"Outlet {item} null return value")
                return None
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
