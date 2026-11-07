"""Open Maker Arms on candleLight/gs_usb adapters by serial, with friendly names."""

import json
import math
from dataclasses import replace
from pathlib import Path

from gsusb_backend import GsUsbBackend, list_adapters
from maker_arm.arm import Arm
from maker_arm.config import ArmConfig
from maker_arm.profiles import DEFAULT_ARM_CONFIG

NAMES_FILE = Path(__file__).with_name("arm_names.json")
CONTROL_RATE_HZ = 50.0


def load_names() -> dict:
    return json.loads(NAMES_FILE.read_text()) if NAMES_FILE.exists() else {}


def save_names(names: dict) -> None:
    NAMES_FILE.write_text(json.dumps(names, indent=2) + "\n")


def adapters_by_name() -> dict[str, str]:
    names = load_names()
    found = {}
    for adapter in list_adapters():
        serial = adapter["serial"]
        name = names.get(serial, f"arm-{serial[-4:]}")
        if name in found:
            raise SystemExit(f"Duplicate adapter label {name!r}; use unique labels")
        found[name] = serial
    return found


def resolve(which: str) -> tuple[str, str]:
    found = adapters_by_name()
    exact = [(name, serial) for name, serial in found.items() if which in (name, serial)]
    matches = exact or [(name, serial) for name, serial in found.items()
                        if which and serial.endswith(which)]
    if len(matches) != 1:
        raise SystemExit(f"Expected one arm matching {which!r}, found {len(matches)}; "
                         f"plugged in: {', '.join(found) or 'none'}")
    return matches[0]


def make_arm(serial: str, max_speed_deg_s: float | None = None) -> Arm:
    config = ArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG))
    config = replace(config, control_rate_hz=CONTROL_RATE_HZ)
    if max_speed_deg_s is not None:
        config = replace(config, max_velocity=math.radians(max_speed_deg_s))
    config._validate()
    return Arm(config, GsUsbBackend(serial))


def joint_labels() -> list[str]:
    return [j.label for j in ArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG)).joints]
