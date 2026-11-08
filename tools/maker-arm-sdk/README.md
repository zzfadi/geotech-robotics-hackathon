# Maker Arm SDK: candleLight (gs_usb) backend for macOS

The official MakerMods `maker-arm-sdk` supports only serial SLCAN on macOS. The station's arms use candleLight USB-CAN
adapters (`1d50:606f`), one per arm. `gs_usb-candlelight-macos.patch` adds:
- a `gs_usb` backend (libusb via `libusb-package`, its own start sequence because the gs_usb library's `start()`
  fails intermittently on macOS);
- skipping of a half-enumerated adapter whose serial cannot be read;
- the codec tests.

Apply it to upstream `makermods-robotics/maker-arm-sdk` at `0245316`:

```bash
git clone https://github.com/makermods-robotics/maker-arm-sdk && cd maker-arm-sdk && git checkout 0245316
git apply ../geotech-robotics-hackathon/tools/maker-arm-sdk/gs_usb-candlelight-macos.patch
uv venv --python 3.12 && uv pip install -e . libusb-package gs_usb pyusb
.venv/bin/maker-arm doctor
```

Adapter serials at the station: left `003A003F4146570320303038`, right `0027002E4146570520313238`.

Use one adapter per process. Two adapters in one process lost motor feedback right after enable. See
`docs/table10-real-arms.md`.
