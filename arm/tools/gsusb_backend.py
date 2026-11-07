"""candleLight / gs_usb CAN backend for the Maker Arm SDK via python-can/libusb."""

import logging
import sys
import threading
from typing import Callable, Optional

import can
from can.interfaces.gs_usb import GsUsbBus
from gs_usb import gs_usb as _gs
from gs_usb.gs_usb import GsUsb

from maker_arm.transport.base import CanBackend, CanId

log = logging.getLogger("armctl.gsusb")


def _start_no_reset(self, flags=(_gs.GS_CAN_MODE_NORMAL | _gs.GS_CAN_MODE_HW_TIMESTAMP)):
    # Tested macOS workaround: USB reset triggers a phantom kernel-driver claim.
    flags &= self.device_capability.feature
    flags &= (_gs.GS_CAN_MODE_LISTEN_ONLY | _gs.GS_CAN_MODE_LOOP_BACK
              | _gs.GS_CAN_MODE_ONE_SHOT | _gs.GS_CAN_MODE_HW_TIMESTAMP)
    self.device_flags = flags
    mode = _gs.DeviceMode(_gs.GS_CAN_MODE_START, flags)
    self.gs_usb.ctrl_transfer(0x41, _gs._GS_USB_BREQ_MODE, 0, 0, mode.pack())


if sys.platform == "darwin":
    GsUsb.start = _start_no_reset


def list_adapters() -> list[dict]:
    return [
        {"serial": d.serial_number, "bus": d.gs_usb.bus, "address": d.gs_usb.address}
        for d in GsUsb.scan()
    ]


class GsUsbBackend(CanBackend):
    def __init__(self, serial: str, bitrate: int = 1_000_000):
        self._serial = serial
        self._bitrate = bitrate
        self._bus: Optional[GsUsbBus] = None
        self._cb: Optional[Callable[[int, bytes], None]] = None
        self._tx_lock = threading.Lock()
        self._running = False
        self._rx_thread: Optional[threading.Thread] = None
        self.feedback_paced_control = True
        self.feedback_wait_timeout = 0.015

    def open(self) -> None:
        match = [a for a in list_adapters() if a["serial"] == self._serial]
        if len(match) != 1:
            raise RuntimeError(f"Expected one gs_usb adapter for {self._serial}, found {len(match)}")
        a = match[0]
        self._bus = GsUsbBus(channel=self._serial, bus=a["bus"], address=a["address"],
                             bitrate=self._bitrate)
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True,
                                           name=f"gsusb-rx-{self._serial[-4:]}")
        self._rx_thread.start()

    def close(self) -> None:
        self._running = False
        if self._rx_thread:
            self._rx_thread.join(timeout=1.0)
            self._rx_thread = None
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                log.exception("gs_usb shutdown failed")
            self._bus = None

    def send(self, can_id: int, data: bytes, extended: bool = True) -> None:
        if self._bus is None:
            raise RuntimeError("gs_usb backend is not open")
        msg = can.Message(arbitration_id=can_id, data=bytes(data), is_extended_id=extended)
        with self._tx_lock:
            self._bus.send(msg)

    def set_recv_callback(self, cb) -> None:
        self._cb = cb

    def _rx_loop(self) -> None:
        while self._running:
            try:
                msg = self._bus.recv(timeout=0.05)
            except Exception:
                if self._running:
                    log.exception("gs_usb read failed")
                break
            if msg is None or not msg.is_rx or msg.is_error_frame or msg.is_remote_frame:
                continue
            cb = self._cb
            if cb:
                try:
                    cb(CanId(msg.arbitration_id, msg.is_extended_id), bytes(msg.data))
                except Exception:
                    log.exception("gs_usb RX frame-processing exception (continuing)")
