""" Utility to set the plug (outlet) names on a PDU from a YAML config file.

Example config:

    type: emat08_10        # or dli_dc3
    host: redeaton1
    port: 23
    username: admin
    password: secret       # optional; prompted for if omitted
    plugs:
      1: Camera
      2: Filter wheel
      3: Spare

Usage:
    set-plug-names config.yaml
"""
import argparse
import getpass
import sys
from typing import Dict, Union

import yaml

from .emat08_10 import EatonEMAT
from .dli_dc3 import Dlidc3

PDU_TYPES = {
    "emat08_10": EatonEMAT,
    "dli_dc3": Dlidc3,
}


def load_config(config_file: str) -> dict:
    """ Read and validate the YAML config file. """
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError(f"{config_file}: config must be a mapping")
    for key in ("type", "host", "plugs"):
        if key not in config:
            raise ValueError(f"{config_file}: missing required key '{key}'")
    if config["type"] not in PDU_TYPES:
        raise ValueError(f"{config_file}: unknown pdu type '{config['type']}', "
                         f"must be one of {list(PDU_TYPES)}")
    if not isinstance(config["plugs"], dict) or not config["plugs"]:
        raise ValueError(f"{config_file}: 'plugs' must be a non-empty mapping "
                         f"of plug number to name")
    for num, name in config["plugs"].items():
        if not isinstance(num, int) or num < 1:
            raise ValueError(f"{config_file}: plug number '{num}' must be an integer >= 1")
        if not name or not str(name).strip():
            raise ValueError(f"{config_file}: plug {num} has an empty name")
    return config


def _set_one(pdu: Union[EatonEMAT, Dlidc3], num: int, name: str) -> bool:
    """ Set one plug name and confirm it took. """
    if num > pdu.outlet_count:
        pdu.report_error(f"Plug {num} out of range (1-{pdu.outlet_count})")
        return False
    # Dlidc3.set_outlet_name returns None, so check the cached name instead
    pdu.set_outlet_name(num, name)
    return pdu.outlet_names[num - 1] == name


def set_plug_names(config_file: str) -> Dict[int, bool]:
    """ Set the plug names on the PDU described in config_file.

    Returns a dict of plug number -> True if the name was set successfully.
    """
    config = load_config(config_file)

    password = config.get("password")
    if password is None:
        password = getpass.getpass(f"Password for {config['host']}: ")

    pdu = PDU_TYPES[config["type"]]()
    pdu.connect(config["host"], config.get("port", 23),
                username=config.get("username", "admin"), password=password)
    if not pdu.is_connected():
        raise ConnectionError(f"Could not connect to {config['host']}")

    results = {}
    try:
        pdu.initialize()
        for num, name in sorted(config["plugs"].items()):
            results[num] = _set_one(pdu, num, str(name))
            print(f"Plug {num}: {name} - {'OK' if results[num] else 'FAILED'}")
    finally:
        pdu.disconnect()
    return results


def main() -> int:
    """ Command line entry point. """
    parser = argparse.ArgumentParser(description="Set PDU plug names from a YAML config")
    parser.add_argument("config", help="YAML config file")
    args = parser.parse_args()

    try:
        results = set_plug_names(args.config)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"set-plug-names: {e}", file=sys.stderr)
        return 2
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
