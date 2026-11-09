#!/bin/bash
# One-time: name each arm CAN link after its adapter serial (the rule dreamcli setup prints), then rename and bring up the live links.
set -euo pipefail
sudo tee /etc/udev/rules.d/90-dreamlabs-can.rules >/dev/null <<"EOF"
# Written by dreamcli setup: each arm CAN link, named after its adapter serial number.
SUBSYSTEM=="net", ACTION=="add", KERNEL=="can*", ATTRS{serial}=="003A003F4146570320303038", NAME="can_follower_l"
SUBSYSTEM=="net", ACTION=="add", KERNEL=="can*", ATTRS{serial}=="0027002E4146570520313238", NAME="can_follower_r"
EOF
sudo udevadm control --reload-rules
for pair in "003A003F4146570320303038 can_follower_l" "0027002E4146570520313238 can_follower_r"; do
  serial=${pair% *}; name=${pair#* }
  for l in /sys/class/net/can*; do
    n=$(basename "$l"); [ "$n" = "$name" ] && continue
    s=$(cat "$(readlink -f "$l/device")/../serial" 2>/dev/null || true)
    if [ "$s" = "$serial" ]; then sudo ip link set "$n" down; sudo ip link set "$n" name "$name"; fi
  done
  ip link show "$name" | grep -q "state UP" || sudo ip link set "$name" up type can bitrate 1000000
done
ip -br link show type can
