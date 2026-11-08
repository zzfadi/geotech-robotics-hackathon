# Pass-down: pick up on FADI's MacBook at the venue

Written Sunday, Nov 8, 14:45 PST on the Mac mini, for FADI and a fresh agent session on the MacBook at the hackathon. Main was at `a0f4353` when this was written; pull first. The Jetson kit (Jetson Orin Nano, GL-RM1 KVM, GL-MT3000 Beryl AX travel router) is physically connected at the venue. **The Beryl was factory reset** because its admin login failed, so its addresses, Wi-Fi name and reservations are back to defaults.

No passwords, Wi-Fi keys, operator codes or join codes belong in this file, in chat or in any commit.

The Jetson-setup session wrote a parallel, more detailed resume at the same time: [macbook-venue-resume-2026-11-08.md](macbook-venue-resume-2026-11-08.md), once it's committed. It covers the Jetson's environments, the KVM and what to report. Where the two differ, the more cautious instruction wins. This page is the short version.

## The clock decides the order

Demos are judged at 3 PM ([plan](../plan.md)): a 3-minute robot demo at the station plus 2 minutes of questions, top 5 present for 5 minutes. The submission route and cutoff are **not confirmed** by the organizers (Brian, [14:33 update](../../dashboard/updates/2026-11-08-1433-submission-rubric-check.json)). Work in this order:

1. The live demo works (the Table 10 real-arm path below).
2. A backup recording exists and plays offline.
3. The submission is delivered once the organizers say how.
4. Everything else, including the Jetson, only if 1–3 are safe.

## What the team is actually running today

| Piece | State on main | Owner | Doc |
| --- | --- | --- | --- |
| **Live real arms: Unreal Table 10 ↔ both Maker Arms** | Working on hardware. Turn arms ON, Move up, Go to idle and Turn arms OFF drive both physical arms from the web panel. Arms stayed within 2.6° of Unreal with 0 CAN errors. | Denys | [table10-real-arms.md](../table10-real-arms.md), [14:50 update](../../dashboard/updates/2026-11-08-1450-claude-table10-both-real-arms.json) |
| Maker Arm SDK on macOS needs the candleLight gs_usb patch | Shared | Denys | [tools/maker-arm-sdk/README.md](../../tools/maker-arm-sdk/README.md) |
| Rubric and submission | Rubric verified. The route and cutoff are still open. | Brian | 14:33 update, [demo/submission.md](../../demo/submission.md) |
| FADI's can hand-off (`handoff/`, `pickup_server.py`) | Software ready and replay-tested (22 of 22). **Never run on the real arm.** | FADI | [handoff/README.md](../../handoff/README.md) |
| Jetson test hub, edge model, voice, control center | Overnight software and simulation results | FADI | [jetson-hub-testing.md](../jetson-hub-testing.md), [plan: what landed overnight](../plan.md) |

**One owner per USB-CAN adapter.** Table 10 runs one `real_arm_service.py --left|--right` process per arm on the Mac attached to the arms, started by login agent `one.geotech.table10-agent`. The fix that made both arms work was one adapter per process ([table10-real-arms.md](../table10-real-arms.md), last section). The agent contains Denys-specific paths, so don't install it unchanged on the MacBook. Do not start `pickup_server.py`, `arm/tools/arm scan|watch|...`, MakerModsLab or any other SDK program on any machine while that service holds the adapters. Switching the arms to the `handoff/` path is a team decision. It means stopping the Table 10 service first: ask Denys, or run `zsh DreamSim/install_table10_agent.sh remove` on that Mac. Then follow handoff/README.md from row 0.

Safety, unchanged: the hardware E-stop stays within reach whenever drive is on. Joints 1–6 have no motor watchdog in this SDK. **Turn arms OFF** in the panel always works.

## Machines

| Machine | Where | Notes |
| --- | --- | --- |
| FADI's MacBook | Venue | This session. It may hold Saturday's checkout and `arm/.venv`; check before reinstalling. |
| Mac mini | Home | Development hub overnight. It holds the Jetson's dedicated SSH key (`~/.ssh/id_ed25519_jetson_orin`) and the Keychain entries for the Jetson administrator ("Jetson Orin Nano administrator 2026-11-08", account `fadi`) and the KVM administrator ("Jetson GL-RM1 administrator 2026-11-08", account `admin`). It can't reach the venue kit (no Tailscale enrolled). |
| Jetson Orin Nano `orin-arm` | Venue, Ethernet to the Beryl LAN | Ubuntu 24.04.4, JetPack 7.2.1, user `fadi`, SSH host key **ED25519 `SHA256:1Re9L+dbMLneresWeHhOM37nO2PW7a3mjuEhyHNGvvk`**. Its Ethernet uses DHCP (NetworkManager on `enP8p1s0`), so it rejoins the reset router on its own. The C270 webcam is on its USB. All test environments live in `~/hub-tests`. |
| GL-RM1 KVM | Venue, Beryl LAN | Keyboard path to the Jetson. Its HDMI input is empty (no DP-to-HDMI adapter), so it shows no Jetson video. It gets a new DHCP address after the reset. |
| GL-MT3000 Beryl AX | Venue | Factory reset today. Firmware was 4.11.0 before the reset; a reset normally keeps the installed firmware. |

## 1. Bring the reset router back (about 5 minutes)

After a GL.iNet factory reset:
- The router is at `192.168.8.1`.
- The Wi-Fi name (`GL-MT3000-…`) and its key are printed on the router's label.
- The admin page asks you to choose a new admin password on your first visit.

1. Join the MacBook to the Wi-Fi on the label, or plug into a LAN port. Open http://192.168.8.1 and set a new admin password. Keep it in your password manager, never in the repo.
2. Check the firmware version on the admin page.
3. **Check the WAN-labeled port first.** Before the reset it was switched to LAN, so the Jetson and the KVM could both be wired. After a reset it is WAN again, and a device plugged there is not on the LAN. Move that cable to a LAN port, or set the port back to LAN in the admin UI ([GL.iNet: change WAN to LAN](https://docs.gl-inet.com/router/en/4/tutorials/change_wan_to_lan/)).
4. **Internet:** the Beryl has only two Ethernet ports, and the Jetson and the KVM use both, so take the uplink over Wi-Fi. In Internet → Repeater, scan for the venue Wi-Fi and join it, keeping Router mode rather than Extender. If the venue Wi-Fi has a sign-in page, use the router's repeater portal option and finish the sign-in from the MacBook. Local SSH to the Jetson works even without internet.
5. **Find the Jetson and the KVM:** open the router's client list, or use `ping orin-arm.local` (if mDNS answers) or `arp -an | grep 192.168.8.` from the MacBook. The earlier address reservations (Jetson `192.168.8.136`, KVM `192.168.8.166`) were wiped.
6. Optional but useful: reserve those two addresses again in the router's LAN address-reservation page, so the docs and aliases still match. Then renew the leases by unplugging each Ethernet cable for 10 s, or power-cycling the device.

If the admin page is not at `192.168.8.1`, read the gateway with `route -n get default | grep gateway`.

## 2. SSH from the MacBook to the Jetson

The MacBook has no key for the Jetson; the dedicated key is on the Mac mini at home. Make a MacBook key and authorize it:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_jetson_macbook -C "macbook to orin-arm"
ssh-copy-id -i ~/.ssh/id_ed25519_jetson_macbook.pub fadi@JETSON_IP
```

- `ssh-copy-id` asks for the Jetson password. **Type it yourself.** It is the administrator entry in the Mac mini's Keychain, or in Passwords if it syncs through iCloud.
- On the first connection, accept the host key only if it shows `SHA256:1Re9L+dbMLneresWeHhOM37nO2PW7a3mjuEhyHNGvvk`.

If password login is refused, add the key at the Jetson itself, with a monitor and keyboard on the Jetson or the KVM keyboard. Serve only the public key from the MacBook for a minute:

```bash
mkdir -p /tmp/pubkey && cp ~/.ssh/id_ed25519_jetson_macbook.pub /tmp/pubkey/ && python3 -m http.server 8999 -d /tmp/pubkey
```

Then, in a terminal on the Jetson:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh && curl -fsS http://MACBOOK_IP:8999/id_ed25519_jetson_macbook.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
```

Stop the little server with Ctrl-C. Then add an alias to `~/.ssh/config` on the MacBook:

```
Host orin-arm
    HostName JETSON_IP
    User fadi
    IdentityFile ~/.ssh/id_ed25519_jetson_macbook
    IdentitiesOnly yes
```

Check with `ssh orin-arm 'hostname; uptime'`; the answer should be `orin-arm`. Put the reserved `192.168.8.136` in `HostName` once step 6 of section 1 is done.

## 3. The MacBook checkout

```bash
cd ~/path/to/geotech-robotics-hackathon     # or: git clone https://github.com/zzfadi/geotech-robotics-hackathon.git
git status --short --branch                  # preserve anything local before pulling
git pull --ff-only origin main
```

Read in this order:
1. `AGENTS.md`. Work on main with no branches, and push regularly.
2. This file.
3. `docs/table10-real-arms.md`.
4. `docs/plan.md`, the "Sunday morning: proposed" section. Nobody updated it at standup, so treat its decisions as unconfirmed.
5. `demo/submission.md`.
6. `dashboard/README.md`.

Post one file in `dashboard/updates/` per meaningful change, run `python3 dashboard/build.py --check`, and push to `origin main`.

## 4. Optional: show the Jetson at the station (no arms, nothing touches the adapters)

All of these need the SSH step above. They only use `~/hub-tests` on the Jetson.
- **Quick health check** from the MacBook repo root: `scripts/jetson_hub_check.sh`, about 20 s. It exits 1 if a suite fails. It also fast-forwards the `~/hub-tests` checkout on the Jetson, so it isn't read-only. Don't start with `--setup`, `--engines` or `--all`: they reinstall packages, rebuild engines for about 20 minutes, or take the camera.
- **Live hand-and-can detector on the C270**, run on the Jetson. This is the GPU path with FP16 engines, camera-bound at 15 fps. Warm it up before going live, because the first frame takes 5–12 s:

```bash
cd ~/hub-tests/geotech-robotics-hackathon
export YOLO_OFFLINE=1 YOLO_CONFIG_DIR=~/hub-tests/yolo-config
~/hub-tests/venv-trt10/bin/python -m vision.run --camera 0 \
  --detect ~/hub-tests/engines-fp16/yolo26n.engine --pose ~/hub-tests/engines-fp16/yolo26n-pose.engine \
  --device 0 --target real --max-seconds 600 --telemetry ~/hub-tests/sessions
```

- **Control center**, showing what the system sees and why, from the MacBook:

```bash
ssh -t orin-arm 'cd ~/hub-tests/geotech-robotics-hackathon && python3 -m control_center --data ~/hub-tests/sessions serve'
ssh -N -L 8780:127.0.0.1:8780 orin-arm      # second terminal; then open http://127.0.0.1:8780
```

  Keep the `-t`. Press Ctrl-C in both terminals when done.

- **Talking points, all simulation or software:**
  - The small edge model takes 0.038 ms per decision on the Jetson.
  - In simulation, assist lifted a slightly-off operator from 18 to 162 cup holds out of 180 ([bench](../validation/edge-model-bench-2026-11-08.json), [Jetson](../validation/edge-model-jetson-2026-11-08.json)).
  - The live detector has not yet seen a hand or the can on camera.

## 5. Optional: voice

On the MacBook, run `python3 -m voice_agent.render` once. It needs macOS `say` and is offline. Lines play locally with `voice_agent.play`, and the voice never moves anything. The LiveKit room needs the team's keys; that's Takeshi's 10-minute setup in [livekit-fit.md](../livekit-fit.md). See [voice_agent/README.md](../../voice_agent/README.md) for the commands.

## 6. Submission

`demo/submission.md` was drafted overnight around the taped-mark or hand-off beat. The live beat is now **Table 10: both real arms follow Unreal from the web panel**. Before anyone submits:
- add that as the description and the first verified result, citing `docs/table10-real-arms.md` and the 14:50 update;
- keep the "claims we must not make" list;
- deliver through whatever route the organizers confirm. Brian is tracking that.

## Open items and cautions

- **Not confirmed:** submission route and cutoff; the e-stop person; the submission owner; and the Dream/Codex split on the real arm, which in practice Table 10 now answers for the demo.
- **Untested:** the overnight hand-off controller has never moved a real arm. The detector has never seen a hand or the can on camera. Dream latency was measured only with mock timings.
- **The home router** at the Mac mini also uses `192.168.8.0/24`, so `192.168.8.x` addresses are only meaningful on the network you're on.
- Some of the Jetson-setup session's dashboard posts show private LAN addresses on the public site. Redacting them is the authors' call.

## Starter prompt for the MacBook session

> You're on FADI's MacBook at the hackathon venue. Repo: geotech-robotics-hackathon, branch main. First run `git status --short --branch`, preserve any local work, then `git pull --ff-only origin main`. Read AGENTS.md, then docs/handoffs/macbook-venue-passdown.md, then docs/handoffs/macbook-venue-resume-2026-11-08.md if it exists, then docs/table10-real-arms.md (especially its last section). The demo is judged at 3 PM. Priorities: live Table 10 demo, then a backup recording, then the submission. Never start any program that opens the USB-CAN adapters while the Table 10 real-arm service holds them. Never type, store or commit passwords; FADI types them himself. First help me finish the router-reset steps (section 1) and set up SSH to the Jetson (section 2). Then ask me what to do next.
