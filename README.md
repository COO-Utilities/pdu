# pdu — Low-level util for Power Distribution Units

Minimal Python package for controlling PDUs.  
Currently includes a **SSH** driver for **Eaton EMAT-08 / EMAT-10** units.

- Thread-safe send/receive

## Features

- **TCP** protocol
- CRLF framing on write, read with configurable timeout.
- Helpers: `outlet_on(n)`, `outlet_off(n)`, `outlet_status(n)`, `get_atomic_value("model" | "firmware")`.
- Safe to subclass or retarget to other PDUs

## Requirements

- Python **3.8+** (tested on 3.8/3.10/3.12)
- `pip`, `setuptools`, `wheel`
- Network access to the PDU’s **TCP ASCII control port**
- Dependency: `hardware_device_base` (installed automatically via `pyproject.toml`)

## Quickstart

### Install (editable for development)
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -U pip setuptools wheel
pip install -e .
```

### Use the Eaton EMAT SSH driver

```python
from emat08_10 import EatonEMAT

# Create driver (3s read timeout)
pdu = EatonEMAT()

# Connect over SSH (default port is 22)
assert pdu.connect(
    host="192.168.1.50",
    port=22,
    username="admin",
    password="your_password",
)

# Turn outlet 3 ON
pdu.outlet_on(3)

# Query outlet 3 status (reads PresentStatus.SwitchOnOff)
reply = pdu.outlet_status(3)
print("Status:", reply)

# Device info
print("Model:", pdu.get_atomic_value("model"))       # PDU.PowerSummary.iManufacturer
print("Firmware:", pdu.get_atomic_value("firmware")) # PDU.PowerSummary.iVersion

# Clean up
pdu.disconnect()

```




