# pdu — Low-level util for Power Distribution Units

Minimal Python package for controlling PDUs.  
Currently includes a **SSH** driver for **Eaton EMAT-08 / EMAT-10** units.

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
from emat08_10 import EatonEMAT

# Create driver (3s read timeout)
pdu = EatonEMAT()

# Connect over SSH (default port is 22)
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
pdu.get_atomic_value("status", 3)

# Query outlet 3 status (reads PresentStatus.SwitchOnOff)
reply = pdu.outlet_status(3)
print("Status:", reply)

# Get all outlets' status
pdu.get_atomic_value("status", "x")

# Device info
print("Model:", pdu.get_atomic_value("model"))       # PDU.PowerSummary.iPartNumber
print("Firmware Version:", pdu.get_atomic_value("version")) # PDU.PowerSummary.iVersion

# Clean up
pdu.disconnect()

```




