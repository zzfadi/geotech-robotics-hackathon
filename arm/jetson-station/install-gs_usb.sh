#!/bin/bash
# One-time on the Jetson: build and install the gs_usb driver (its kernel ships without it) so the candleLight
# USB-CAN adapters become SocketCAN links, which Dream's station needs. Loads now and at every boot.
# Run on the Jetson with sudo rights; from the Mac: ssh -t orin-arm 'bash -s' < arm/jetson-station/install-gs_usb.sh
set -euo pipefail
SRC=https://raw.githubusercontent.com/torvalds/linux/v6.8/drivers/net/can/usb/gs_usb.c
SHA256=2ef2f149c7b45fe92306b88d77c43188c61189eaf950d3abc1b7b4ff9eaec01b
dir=~/gs_usb-build
mkdir -p "$dir" && cd "$dir"
[ -f gs_usb.c ] || curl -fsSL -m 60 -o gs_usb.c "$SRC"
echo "$SHA256  gs_usb.c" | sha256sum -c -
printf 'obj-m := gs_usb.o\n' > Makefile
make -C "/lib/modules/$(uname -r)/build" M="$PWD" modules
sudo install -D -m 644 gs_usb.ko "/lib/modules/$(uname -r)/updates/gs_usb.ko"
sudo depmod -a
echo gs_usb | sudo tee /etc/modules-load.d/gs_usb.conf >/dev/null
sudo modprobe gs_usb
lsmod | grep -E "^(gs_usb|can_dev)"
ip -br link | grep -i can || true
