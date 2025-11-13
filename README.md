# pdu — Low-level util for Power Distribution Units

Minimal Python package for controlling PDUs.  
Currently includes a **TCP** driver for **Eaton EMAT-08 / EMAT-10** units.

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

### Use the Eaton EMAT TCP driver
```bash
from pdu.EMAT08_10 import EatonEMAT

# Create driver (3s read timeout, CRLF framing)
pdu = EatonEMAT(read_timeout=3.0)

# Connect to the PDU's ASCII TCP port (replace with your IP/port)
assert pdu.connect("192.168.1.50", 1234)

# Turn outlet 3 ON
pdu.outlet_on(3)

# Query outlet 3 status
reply = pdu.outlet_status(3)
print("Status:", reply)

# Device info (templates map to your firmware commands)
print("Model:", pdu.get_atomic_value("model"))
print("Firmware:", pdu.get_atomic_value("firmware"))

# Clean up
pdu.disconnect()
```




