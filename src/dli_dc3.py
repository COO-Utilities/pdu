"""Class for DLI DC 3 Power Controller"""
import json
import re
from typing import Optional, Union, List, Tuple
import paramiko
from paramiko.channel import ChannelFile

try:
    from hardware_device_base import HardwareSensorBase
except ModuleNotFoundError:
    from hardware_device_base.hardware_device_base import HardwareSensorBase  # type: ignore

GET_PREFIX = "uom get"
SET_PREFIX = "uom set"
INVOKE_PREFIX = "uom invoke"

def trailing_int(s: str) -> Optional[Tuple[str, int]]:
    """ return trailing integer from string or None """
    m = re.search(r"^(.+?)(\d+)$", s)
    return (m.group(1), int(m.group(2))) if m else None

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
        self.outlet_cycle_delay: List[int] = []
        self.outlet_locked: List[bool] = []
        self.model: str = ""
        self.version: str = ""
        self.name: str = ""

        # Connection params
        self.host: str = ""
        self.port: int = 23
        self.username: str = ""
        self.password: str = ""
        self.ssh: Optional[paramiko.SSHClient] = None
        self.stdout: Optional[ChannelFile] = None

        self.outlet_commands = {
            "state": ("relay/outlets/{outlet_num}/state", "bool"),
            "critical": ("relay/outlets/{outlet_num}/critical", "bool"),
            "cycle": ("relay/outlets/{outlet_num}/cycle", "bool"),
            "transient_state": ("relay/outlets/{outlet_num}/transient_state", "bool"),
            "physical_state": ("relay/outlets/{outlet_num}/physical_state", "bool")
        }
        self.outlet_configurations = {
            "name": ("relay/outlets/{outlet_num}/name", "str"),
            "cycle_delay": ("relay/outlets/{outlet_num}/cycle_delay", "int"),
            "locked": ("relay/outlets/{outlet_num}/locked", "bool")
        }
        self.device_commands = {
            "model": "relay/model",
            "name": "relay/name",
            "version": "relay/version"
        }

    def connect(self, host:str, port:int = 23, username:str = "", password:str = "", # pylint: disable=W0221,unused-argument
                **kwargs) -> None:
        """Connect to DLI DC 3 Power Controller"""

        self.host = host
        self.port = port
        self.username = username
        self.password = password

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh = ssh
        try:
            ssh.connect(hostname=host, username=username, password=password)
        except paramiko.SSHException as ex:
            self.report_error(str(ex))
            return
        self._set_connected(True)

    def disconnect(self) -> None:
        """Disconnect from DLI DC 3 Power Controller"""
        if self.ssh is not None:
            self.ssh.close()
        self._set_connected(False)

    def _send_command(self, cmd: str, *args, **kwargs) -> bool: # pylint: disable=unused-argument
        """Send command to DLI DC 3 Power Controller"""

        if self.ssh is None or not self.is_connected():
            self.report_error("Device is not connected")
            return False

        if not cmd.startswith("uom"):
            self.report_error(f"Illegal command, only uom commands are supported: {cmd}")
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
        if not self.stdout:
            self.report_error("No reply from DLI DC 3 (stdout not available)")
            return None
        return self.stdout.read().decode("utf-8")

    def _query(self, cmd: str) -> Optional[str]:
        """Send command and return stripped reply, or None on failure"""
        if not self._send_command(cmd):
            return None
        reply = self._read_reply()
        if reply is None:
            return None
        return reply.strip()

    def _query_str(self, cmd: str) -> Optional[str]:
        """Send command and return reply with JSON string quotes removed"""
        reply = self._query(cmd)
        if reply is None:
            return None
        # uom returns string values JSON encoded, e.g. "Camera" with quotes
        if len(reply) >= 2 and reply[0] == '"' and reply[-1] == '"':
            try:
                return str(json.loads(reply))
            except ValueError:
                return reply[1:-1]
        return reply

    def _validate_outlet(self, outlet_num:int) -> bool:
        """Validate outlet number (1-based)"""
        if outlet_num < 1 or outlet_num > self.outlet_count:
            self.report_error(f"Outlet number must be >= 1 and <= {self.outlet_count}")
            return False
        return True

    def _ready(self, outlet_num:int) -> bool:
        """Are we ready to send a command?"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return False
        if not self.initialized:
            self.report_error("Device is not initialized")
            return False
        if not self._validate_outlet(outlet_num):
            return False
        return True

    def initialize(self) -> None:
        """Initialize DLI DC 3 Power Controller Class Instance"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            self.initialized = False
        else:
            # model
            cmd = f"{GET_PREFIX} {self.device_commands['model']}"
            self.model = str(self._query_str(cmd))
            # name
            cmd = f"{GET_PREFIX} {self.device_commands['name']}"
            self.name = str(self._query_str(cmd))
            # version
            cmd = f"{GET_PREFIX} {self.device_commands['version']}"
            self.version = str(self._query_str(cmd))
            # outlet names and states
            self.outlet_names = []
            self.outlet_onoff = []
            self.outlet_cycle_delay = []
            self.outlet_locked = []
            for n in range(1, self.outlet_count + 1):
                # outlet name
                cmd = f"{GET_PREFIX} {self.outlet_configurations['name'][0].format(outlet_num=n)}"
                name = self._query_str(cmd)
                self.outlet_names.append(name if name is not None else "")
                # outlet cycle delay
                cmd = "{GET_PREFIX} "
                cmd += f"{self.outlet_configurations['cycle_delay'][0].format(outlet_num=n)}"
                cycle_delay = self._query_str(cmd)
                self.outlet_cycle_delay.append(int(cycle_delay) if cycle_delay is not None else 0)
                # outlet lock state
                cmd = f"{GET_PREFIX} {self.outlet_configurations['locked'][0].format(outlet_num=n)}"
                locked = self._query_str(cmd)
                self.outlet_locked.append("true" in locked if locked is not None else False)
                state = self.outlet_status(n)
                self.outlet_onoff.append(1 if state else 0)
            self.initialized = True

    def get_device_name(self) -> Optional[str]:
        """Get device name for DLI DC 3 Power Controller"""
        if not self.initialized:
            self.report_error("Device is not initialized")
            return None
        return self.name

    def set_device_name(self, device_name:str) -> bool:
        """Set device name for DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return False

        cmd = f"{SET_PREFIX} {self.device_commands['name']} '\"{device_name}\"'"
        if self._query(cmd) is not None:
            self.name = device_name
            return True
        return False

    def get_device_model(self) -> Optional[str]:
        """Get device model for DLI DC 3 Power Controller"""
        if not self.initialized:
            self.report_error("Device is not initialized")
            return None
        return self.model

    def get_device_version(self) -> Optional[str]:
        """Get device version for DLI DC 3 Power Controller"""
        if not self.initialized:
            self.report_error("Device is not initialized")
            return None
        return self.version

    def get_outlet_name(self, outlet_num:int) -> Union[str, None]:
        """Retrieve outlet name from DLI DC 3 Power Controller"""
        if not self.initialized:
            self.report_error("Device is not initialized")
            return None

        # check outlet number
        if not self._validate_outlet(outlet_num):
            return None
        idx = outlet_num - 1
        return self.outlet_names[idx] if self.outlet_names[idx] is not None else ""

    def set_outlet_name(self, outlet_num:int, outlet_name:str) -> None:
        """Set outlet name from DLI DC 3 Power Controller"""
        if not self._ready(outlet_num):
            return None

        idx = outlet_num - 1

        path = self.outlet_commands['name'][0].format(outlet_num=idx)
        cmd = f"{SET_PREFIX} {path} '\"{outlet_name}\"'"
        if self._query(cmd) is not None:
            name = self.get_outlet_name(outlet_num)
            if name is not None:
                self.outlet_names[idx] = name
        return None

    def get_outlet_cycle_delay(self, outlet_num:int) -> Union[int, None]:
        """Get outlet cycle delay in seconds"""
        if not self._ready(outlet_num):
            return None
        idx = outlet_num - 1
        return self.outlet_cycle_delay[idx] if self.outlet_cycle_delay[idx] is not None else 0

    def set_outlet_cycle_delay(self, outlet_num:int, cycle_delay: int =1) -> bool:
        """Set outlet cycle delay in seconds from DLI DC 3 Power Controller"""
        if not self._ready(outlet_num):
            return False

        idx = outlet_num - 1

        path = self.outlet_configurations['cycle_delay'][0].format(outlet_num=idx)
        cmd = f"{SET_PREFIX} {path} '{cycle_delay}'"
        if self._send_command(cmd):
            self.outlet_cycle_delay[idx] = cycle_delay
            return True
        return False

    def outlet_status(self, outlet_num:int) -> Optional[bool]:
        """Retrieve outlet state from DLI DC 3 Power Controller"""
        if not self.is_connected():
            self.report_error("Device is not connected")
            return None

        # check outlet number
        if not self._validate_outlet(outlet_num):
            return None
        idx = outlet_num - 1

        cmd = f"{GET_PREFIX} {self.outlet_commands['state'][0].format(outlet_num=idx)}"
        state = self._query(cmd)
        if state is None:
            return None
        return "true" in state

    def outlet_on(self, outlet_num:int) -> bool:
        """Set outlet on"""
        if not self._ready(outlet_num):
            return False

        idx = outlet_num - 1

        cmd = f"{SET_PREFIX} {self.outlet_commands['state'][0].format(outlet_num=idx)} true"
        if self._query(cmd) is not None:
            self.outlet_onoff[idx] = 1
            return True
        return False

    def outlet_off(self, outlet_num:int) -> bool:
        """Set outlet off"""
        if not self._ready(outlet_num):
            return False

        idx = outlet_num - 1

        cmd = f"{SET_PREFIX} {self.outlet_commands['state'][0].format(outlet_num=idx)} false"
        if self._query(cmd) is not None:
            self.outlet_onoff[idx] = 0
            return True
        return False

    def outlet_cycle(self, outlet_num:int) -> bool:
        """Power cycle outlet"""
        if not self._ready(outlet_num):
            return False

        idx = outlet_num - 1

        cmd = f"{INVOKE_PREFIX} {self.outlet_commands['cycle'][0].format(outlet_num=idx)}"
        retval = self._query(cmd)
        if retval is None:
            return False
        return "true" in retval

    def lock_status(self, outlet_num:int) -> Optional[bool]:
        """Retrieve outlet state from DLI DC 3 Power Controller"""
        if not self._ready(outlet_num):
            return None

        idx = outlet_num - 1
        return self.outlet_locked[idx] if self.outlet_locked[idx] is not None else None

    def lock_outlet(self, outlet_num:int) -> bool:
        """Lock outlet state"""
        if not self._ready(outlet_num):
            return False

        idx = outlet_num - 1

        cmd = f"{SET_PREFIX} {self.outlet_commands['locked'][0].format(outlet_num=idx)} true"
        if self._query(cmd) is not None:
            self.outlet_locked[idx] = True
            return True
        return False

    def unlock_outlet(self, outlet_num:int) -> bool:
        """Unlock outlet state"""
        if not self._ready(outlet_num):
            return False

        idx = outlet_num - 1

        cmd = f"{SET_PREFIX} {self.outlet_commands['locked'][0].format(outlet_num=idx)} false"
        if self._query(cmd) is not None:
            self.outlet_locked[idx] = False
            return True
        return False

    def get_atomic_value(self, item: str ="") -> Union[float, int, str, None]:
        """Get atomic values from DLI DC 3 Power Controller"""
        # pylint: disable=too-many-return-statements
        intup = trailing_int(item)
        # get value for specified outlet
        if intup is None:
            self.report_error(f"Must specify an outlet number (add as suffix to {item})")
            return None
        # get value for specific outlet
        n = intup[1]
        item = intup[0]
        # check readiness
        if not self._ready(n):
            return None
        if item not in self.outlet_commands:
            self.report_error(f"Outlet {item} command not found in DLI DC 3")
            return None
        idx = n - 1
        cmd = f"{GET_PREFIX} {self.outlet_commands[item][0].format(outlet_num=idx)}"
        result = self._query(cmd)
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
        elif len(result) >= 2 and result[0] == '"' and result[-1] == '"':
            result = result[1:-1]
        return result
