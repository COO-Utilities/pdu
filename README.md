# pdu — Low-level util for Power Distribution Units

Minimal Python package for controlling PDUs.  
Currently includes a **Telnet** driver for **Eaton EMAT-08 / EMAT-10** units and an **SSH** driver for **DLI DC3** units.

- Thread-safe send/receive

## Features

- **TCP** protocol
- CRLF framing on write, read with configurable timeout.
- Helpers: `outlet_on(n)`, `outlet_off(n)`, `outlet_status(n)`, `get_atomic_value("model" | "version")`.
- List items: `get_atomic_value("help")`
- Safe to subclass or retarget to other PDUs

## Requirements

- Python **3.8+** (tested on 3.8/3.10/3.12)
- `pip`, `setuptools`, `wheel`
- Network access to the PDU’s **TCP ASCII control port through telnet**
  - set up PDU to use telnet protocol (usually with port 23)
    - Access the Web interface and log in
    - Under Network and Security, select Global
    - In the Ports Settings panel, select Telnet from the drop-down list
    - Click "Save"
    - Open the System submenu in the Settings menu.  In the Network Management Card panel, click "Restart network management card"
- Dependency: `hardware_device_base` (installed automatically via `pyproject.toml`)

## Quickstart

### Install (editable for development)
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -U pip setuptools wheel
pip install -e .
```

### Use the Eaton EMAT Telnet driver

```python
from src.emat08_10 import EatonEMAT

# Create driver (3s read timeout)
pdu = EatonEMAT()

# Connect over Telnet (default port is 23)
assert pdu.connect(
  host="redeaton1",
  port=23,
  username="admin",
  password="your_password",
)

# Initialize pdu object (good to do first)
pdu.initialize()

# Turn outlet 3 ON
pdu.outlet_on(3)

# Get outlet 3 status
pdu.get_atomic_value("outlet_status3")

# Query outlet 3 status (reads PresentStatus.SwitchOnOff)
reply = pdu.outlet_status(3)
print("Status:", reply)

# Get all outlets' status
pdu.get_all_values("outlet_status")

# Device info
print("Model:", pdu.get_atomic_value("model"))  # PDU.PowerSummary.iPartNumber
print("Firmware Version:",
      pdu.get_atomic_value("version"))  # PDU.PowerSummary.iVersion

# Clean up
pdu.disconnect()

```

### Use the DLI DC3 SSH driver

```python
from src.dli_dc3 import Dlidc3

# Create driver
pdu = Dlidc3()

# Connect over SSH (port 22)
pdu.connect(
  host="dlipdu1",
  username="admin",
  password="your_password",
)
assert pdu.is_connected()

# Initialize pdu object (reads model, name, version, outlet names and states)
pdu.initialize()

# Turn outlet 3 ON
pdu.outlet_on(3)

# Query outlet 3 status (True = on, False = off)
print("Status:", pdu.outlet_status(3))

# Get outlet 3 state through the generic getter (outlet number is a suffix)
pdu.get_atomic_value("state3")

# Power-cycle outlet 3
pdu.outlet_cycle(3)

# Rename outlet 3
pdu.set_outlet_name(3, "Camera")
print("Name:", pdu.get_outlet_name(3))

# Lock outlet 3 so it can't be switched
pdu.lock_outlet(3)

# Device info (cached by initialize())
print("Model:", pdu.model)
print("Firmware Version:", pdu.version)

# Clean up
pdu.disconnect()

```

### Set plug names from a config file

Installing the package also installs a `set-plug-names` command. It sets the
outlet (plug) names on either supported PDU type from a YAML config file:

```yaml
type: emat08_10        # or dli_dc3
host: redeaton1
port: 23               # optional, default 23
username: admin        # optional, default admin
password: secret       # optional; prompted for if omitted
plugs:
  1: Camera
  2: Filter wheel
  3: Spare
```

Only the plugs listed are changed. Plug numbers are 1-based.

```bash
set-plug-names pdu.yaml
```

The command prints `OK` or `FAILED` for each plug. It exits with status 0 if
every plug was set, 1 if any plug failed, and 2 if the config file could not be
read or the PDU could not be reached.

Leave `password` out of the file to be prompted for it, so it isn't stored in
plain text.

The same thing can be done from Python:

```python
from pdu.set_plug_names import set_plug_names

results = set_plug_names("pdu.yaml")   # {1: True, 2: True, 3: True}
```
