# MacBook at the venue: resume after the Beryl reset

Prepared November 8, 2026, about 14:43 Pacific. Source checkout: **`a0f4353` on `main`**, fetched and fast-forwarded from the Mac Mini's overnight `ea6d632` (47 later commits). Fetch again before working: the team is still pushing.

**New user report:** the Jetson/KVM/router kit is physically connected at the hackathon, but the **Beryl travel router was reset because its administrator login failed**. This report supersedes the home-network addresses and settings in the earlier handoffs. No venue network, KVM video, arm motion or MacBook authentication was verified in preparing this document. The source/evidence review happened on the Mac Mini.

## Start here

1. Recover the local Beryl network and identify the devices' current leases. A reset may have cleared the WAN-to-LAN setting and reservations.
2. Establish the MacBook's own trusted SSH access. The Mac Mini's alias, private key and login Keychain do not automatically exist on the MacBook.
3. Inventory the Jetson's existing environments and current arm ownership. Preserve the overnight installation; do not reinstall JetPack, rebuild TensorRT engines or start an arm controller just to check readiness.
4. Choose the already working demo/control path with the station operator. The newer Table 10 service and the older `pickup_server` handoff routine are different paths. Never let them compete for the same CAN adapters.
5. Run the smallest relevant checks, record actual venue results, and push focused changes plus a new dashboard entry to `main`.

The latest committed event check says demos/judging begin at **15:00**. The earlier 08:30/09:00/11:00 checklist is historical, not a new schedule. With little time remaining, prioritize a repeatable existing live interaction and a backup recording over moving a working arm controller to a new host. The Jetson is useful but not a prerequisite for the demo.

## Recover Beryl after the reset

Hardware: **GL-MT3000 Beryl AX**, separate from the home GL-MT6000. It had firmware **4.11.0** before the reset; check what it reports now. Resetting Beryl does not imply the Jetson SSD or KVM was reset.

- Connect the MacBook to the Beryl's currently advertised Wi-Fi, using its label/current setup information. Verify the actual router model. Do not assume the earlier Wi-Fi credential survived or confuse the Wi-Fi password with the router administrator password.
- Inspect the MacBook gateway (`route -n get default`) and open that router's administration page. `192.168.8.1` was the previous/default address, not proof of device identity. Complete initial administrator setup if the reset requires it; keep the chosen password in the user's password manager/Keychain, never Git.
- Use **Router mode with a Repeater/Wi-Fi uplink** to the venue. Complete any venue captive-portal sign-in. Local device access can work without internet; Git/cloud/Dream access needs a working uplink. Avoid switching to Extender/bridge mode merely to use the venue Wi-Fi.
- **Check the WAN-labeled socket.** Before the reset, Port 1 (physically labeled WAN) was deliberately configured as **LAN** so the Jetson and KVM could both be wired clients. Leave that socket disconnected while restoring its role. On firmware 4.10+, open **Network → Ethernet Port → gear beneath the WAN port → LAN → Apply** (also reachable from Internet → Ethernet 1 → gear). Verify it shows LAN, then reconnect the second kit device. Alternatively, connect both devices through a LAN switch. A cable in a WAN port is not proof that the device is on the same LAN.
- In the client list, identify `orin-arm`/the Jetson and the GL-RM1 KVM, then test those observed addresses. Recreate reservations only after matching the devices. The old **Jetson `192.168.8.136`** and **KVM `192.168.8.166`** are historical hints, not current targets. The even older KVM `192.168.2.2` belonged to Mac Internet Sharing and is obsolete.
- Same-Beryl-LAN SSH/KVM access needs **no Tailscale, public port forwarding or Mac Internet Sharing**. Remote teammates/Luna2 on another network need a separately established route. No Tailscale enrollment or subnet route was configured in this setup chat.

Official references: [Beryl first setup](https://docs.gl-inet.com/router/en/4/user_guide/gl-mt3000/), [change WAN to LAN](https://docs.gl-inet.com/router/en/4/faq/change_wan_to_lan/), [Ethernet port settings](https://docs.gl-inet.com/router/en/4/interface_guide/ethernet_port_v4.10/), [Repeater uplink](https://docs.gl-inet.com/router/en/4/interface_guide/internet_repeater/).

## KVM, display and credentials

- **GL-RM1** was separately reset and configured at home, with local keyboard control verified. A Beryl reset alone should not be treated as a KVM reset. Do not factory-reset the KVM to resolve a missing LAN lease.
- Video must run **Jetson DisplayPort output → KVM HDMI input**. The user expected the cable in the morning and now reports physical setup connected; actual native KVM video still needs verification. USB-C HID from KVM to Jetson worked; an earlier KVM-to-Jetson USB-A arrangement did not. Retest the actual wiring rather than assuming every USB-C port carries video.
- At home, KVM power came from the Mac Mini. Check its current power source and USB/HID status. Its firmware was `V1.9.2 release1 (RM1)`.
- The Logitech **C270 webcam is attached to the Jetson**, not the Mac. Old Mac AVFoundation camera indices are invalid. Its image node was `/dev/video0`, and `/dev/video1` was metadata for the same camera; enumerate again after recabling.
- A Jetson-hosted webcam depends on the running Jetson OS. Native KVM HDMI is the boot/recovery view. Before moving the camera to the can/jaws, obtain a usable KVM screen.
- KVM cloud binding was completed using the user's approved Google account at `https://glkvm.com/`; only same-Mac P2P console access was verified. Do not describe off-site/cloud keyboard access as tested.

Credential locations on the **Mac Mini**, not guaranteed on the MacBook:

| Credential | Location / distinction |
| --- | --- |
| Beryl administrator | User entered it directly; the assistant did not retain it. Set/recover the current post-reset login in the router UI. |
| Beryl Wi-Fi | Separate credential; use current label/setup information after reset. |
| Jetson account | User **`fadi`**, host **`orin-arm`**. Mac Mini login Keychain service `Jetson Orin Nano administrator 2026-11-08`, account `fadi`. |
| KVM administrator | User **`admin`**. Mac Mini login Keychain service `Jetson GL-RM1 administrator 2026-11-08`, account `admin`. |
| Mac Mini SSH client | `~/.ssh/id_ed25519_jetson_orin`, with its own `orin-arm` SSH config and `known_hosts_jetson_orin`. These are local files, not repository assets. |

Retrieve needed credentials through the user's trusted password workflow; do not print them into logs, chat handoffs or repository files. Do not assume this list grants the new MacBook access to the Mac Mini's Keychain.

## Establish trusted MacBook SSH

The installed Jetson's console-verified **ED25519 host fingerprint** was:

```text
SHA256:1Re9L+dbMLneresWeHhOM37nO2PW7a3mjuEhyHNGvvk
```

A router reset changes networking, not this host key. On the Jetson console, verify with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Compare the fingerprint before trusting the newly discovered IP. Do not disable host checking or erase trusted keys to dismiss a mismatch.

Use an existing intended MacBook public key, or create a dedicated one without overwriting another key. Install **only its public key** into `fadi`'s `~/.ssh/authorized_keys`, via a verified password SSH login, the KVM console, or an already trusted client. Preserve existing entries; directory mode 700 and file mode 600. There is no need to transfer the Mac Mini's private key or reinstall the Jetson.

After verifying the host and login, create/update a MacBook alias using the **actual new IP and actual MacBook key path**:

```sshconfig
Host orin-arm
    HostName <verified-current-Jetson-IP>
    User fadi
    IdentityFile ~/.ssh/<intended-MacBook-key>
    IdentitiesOnly yes
    StrictHostKeyChecking yes
```

`StrictHostKeyChecking yes` requires the verified host key to have been added first. Confirm `ssh -o BatchMode=yes orin-arm true` succeeds before running unattended helper scripts.

## What is already installed on the Jetson

These are **recorded pre-venue results**, not fresh venue checks: Ubuntu **24.04.4**, L4T/UEFI **39.2.1**, kernel **6.8.12-1021-tegra**, 7.3 GiB RAM, root on **`/dev/nvme0n1p1`** of the approximately 1 TB NVMe. Default power remained 25 W mode 1. `ssh.socket` was enabled/active; disabled `ssh.service` was normal socket activation. Tailscale 1.104.5 was installed but unauthenticated (`NeedsLogin`).

There are **two checkouts**; do not mix them up or rebuild one over the other:

| Jetson path | Purpose |
| --- | --- |
| `/home/fadi/geotech-robotics-hackathon` | Original station checkout, synchronized to `24d61e1` during setup; inspect its current HEAD and local changes. |
| `/home/fadi/jetson-runtime/.venv/bin/python` | Existing isolated arm SDK environment, Python 3.12.15; select with `ARMCTL_PYTHON`. |
| `/home/fadi/jetson-prep-assets` | Setup assets, diagnostic capture and provisioning outputs. |
| `/home/fadi/hub-tests/geotech-robotics-hackathon` | Separate overnight software-test checkout; tested at `132ee2e`/`9ceef72`, with later fixes recorded. Inspect before syncing. |
| `/home/fadi/hub-tests/venv-vision` | YOLO/OpenCV/CUDA PyTorch and TensorRT **11.4**; `.pt` inference and FP32 engines. |
| `/home/fadi/hub-tests/venv-trt10` | TensorRT **10.16.1.11** environment for the existing FP16 engines. |
| `/home/fadi/hub-tests/venv-sim` | Simulation/Capture test dependencies. |
| `/home/fadi/hub-tests/weights` | `yolo26n.pt`, `yolo26n-pose.pt`. |
| `/home/fadi/hub-tests/engines-fp16` | Already built board-specific detect/pose FP16 engines. Use `venv-trt10`; do not rebuild first. |
| `/home/fadi/hub-tests/engines` | FP32 engines for the TensorRT 11 environment. |
| `/home/fadi/hub-tests/edge-trained/policy.npz` | Trained small edge policy, tested in simulation only. |
| `/home/fadi/hub-tests/sessions`, `logs`, `results` | Recorded telemetry, test logs and measurements. |

Model weights, TensorRT engines, local recordings and voice WAVs are **not obtained just by cloning Git**. The Jetson copies survived a router-only reset unless separately erased. The Mac Mini's external setup drive `/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07` also holds installers and private evidence; it is not the Jetson SSD and may not be at the venue.

## Overnight work, with evidence limits

The [05:36 summary](../../dashboard/updates/2026-11-08-0536-overnight-hub-build-done.json) describes the Mac Mini build. Read [Jetson hub testing](../jetson-hub-testing.md) and [its evidence JSON](../validation/jetson-hub-check-2026-11-08.json) for the later, stronger Jetson results; the original setup handoffs stop earlier.

- **Arm and handoff software:** 144 arm tests; 50 handoff tests; 20/20 recorded replay scenarios. Current `handoff/README.md` has **22** scenarios after later additions. The current repository still has **all handoff poses `taught: false`** and **`contact_calibrated: false`**. Do not turn those flags on to bypass teaching. A new clone is not a calibrated robot.
- **Vision:** 49 tests and replay passed. YOLO26n detect + pose ran on the Jetson GPU. CUDA `.pt` was roughly 67–78 ms per still; FP16 engines roughly **26–41 ms** per still. Live C270 ran around **15 fps** in the dark room; nobody held a can or presented a hand during those runs. Engine labels differed from `.pt` on 3/5 test stills. Rehearse the actual model/camera combination; these numbers do not prove the handoff detector works at the venue.
- **Control center:** 17 tests, session schema validation and HTTP APIs passed from the Jetson through an SSH tunnel. It is a **read-only dashboard**, default Jetson localhost port **8780**. It does not command arms. Port **8765** belongs to the pickup controller.
- **Portability fixes (`0306c8a`):** tracked `PROVENANCE.json` filename case fixed for Linux; host-dependent preflight tests isolated; JetPack 7 CUDA package documentation corrected. Later evidence reports DreamSim 141 tests OK with three optional QuickJS tests skipped.
- **Edge model:** 19,335 parameters, trained on the scripted expert, not Dream outputs. Jetson CPU inference around **0.038 ms**; expert and model each passed 9/9 tables in one MuJoCo benchmark seed. This is simulation, not a real-arm policy acceptance result. [Evidence](../validation/edge-model-jetson-2026-11-08.json).
- **Voice:** 31 scripted lines, 30 tests, optional LiveKit publishing bridge; local dev-server replay passed. Stage-speaker playback and LiveKit Cloud were not established. Read [voice setup](../../voice_agent/README.md) and [LiveKit fit](../livekit-fit.md). The bridge cannot command an arm.
- **Runtime artifacts:** [the archived recovery recipe](../validation/jetson-runtime-recovery-2026-11-08/README.md) preserves exact source, dependency closure and result. It is a historical, hash-pinned recovery procedure, **not a general installer for today's main**.

## Later main changes that alter the plan

The latest committed **`9ebc393`** report says both physical arms completed Table 10 web-panel **Turn arms ON → Move up → Go to idle → Turn arms OFF**, with real joints within 2.6° of Unreal and zero CAN errors. This is a **team-recorded result**, not independently repeated in this handoff. Its dashboard entry is labeled 14:50, which was ahead of the Mac's 14:39 clock when fetched; use commit identity and the recorded evidence rather than that timestamp to infer chronology.

The crucial fix is **one USB-CAN adapter per service process**. The earlier power-sag diagnosis was explicitly retracted after the same arm worked through the SDK and a single-adapter service. Read the final section of [Table 10 real arms](../table10-real-arms.md), not only its older opening table: current service instances use `--left` and `--right`, per-arm state directories, and the SDK's default 200 Hz loop. The target publishing cadence is a different quantity.

That path uses `DreamSim/table10_agent.py`, `maker_arm_real_link.py`, and `real_arm_service.py` on the Mac attached to the arms, with Unreal/Luna2 web controls. **The agent contains Denys-specific project/SDK paths. Do not install its launch agent unchanged on a new MacBook.** The [macOS candleLight SDK patch](../../tools/maker-arm-sdk/README.md) was added in `28d1043`; review it only if the MacBook will actually host the adapters.

Before taking over, establish which host and process own each adapter. Do not run `pickup_server.py`, MakerModsLab, a terminal bridge and the Table 10 services against the same adapter. Do not disconnect a live Unreal participant or start a second editor. Follow `AGENTS.md` and the current station operator's control path.

The independent `handoff/` routine remains an option, with the taped-mark beat first and hand mode only after measured success. Its current `pickup_server.py` **claims localhost port 8765 before connecting/enabling the arm** (the older handoff's reversed order is obsolete). It still enables motors once started. Physical cutoff/operator readiness and actual taught poses are required. Do not use Ctrl-C as an assumed torque-release command; follow [handoff stopping instructions](../../handoff/README.md).

Simulation also advanced to committed reports of 9/9 assisted cup handovers/place-back, plus a Table 10 VR/Dream can interaction. Keep virtual task success separate from physical grasp/human-handoff acceptance. The submission draft still has live-trial and delivery placeholders; it is not proof the physical can handoff has passed.

## First safe commands for the new session

On the MacBook, find the intended checkout or clone `https://github.com/zzfadi/geotech-robotics-hackathon.git`. Read `AGENTS.md`. Work on **main**, preserving any local changes. Fetch before comparing HEAD; pull with `--ff-only` only when clean. Do not create a feature branch/worktree for this project.

After trusted SSH works, inspect without changing hardware:

```sh
ssh orin-arm 'hostname; findmnt /; uptime; lsusb; ls -l /dev/v4l/by-id/; ss -ltnp'
ssh orin-arm 'git -C ~/geotech-robotics-hackathon status --short --branch; git -C ~/hub-tests/geotech-robotics-hackathon status --short --branch'
ssh orin-arm 'ps -eo pid,args | grep -E "pickup_server|real_arm_service|table10_agent|vision.run|control_center"'
```

Inspect the relevant Mac host's processes as well; absence on the Jetson does not mean the adapters are unowned. After preserving local work and synchronizing the chosen Jetson checkout, the non-actuating preflight is:

```sh
cd ~/hub-tests/geotech-robotics-hackathon
export ARMCTL_PYTHON=/home/fadi/jetson-runtime/.venv/bin/python
export YOLO_OFFLINE=1 YOLO_CONFIG_DIR=/home/fadi/hub-tests/yolo-config
python3 -m handoff.preflight \
  --vision-python /home/fadi/hub-tests/venv-vision/bin/python \
  --detect /home/fadi/hub-tests/weights/yolo26n.pt \
  --pose /home/fadi/hub-tests/weights/yolo26n-pose.pt
```

Run this **on the Jetson**, not on the MacBook with Jetson-only interpreter paths. Expect NO-GO if mark poses remain untaught, and MARK-ONLY for unmeasured hand grip band/hand poses. Read the actual verdict; do not assume every failure is an expected pose failure.

To inspect the camera without models, use `python3 arm/tools/camera_inspect_linux.py inventory`, then its `snapshot` subcommand with an observed stable capture alias and a **new** output directory. It samples 30 frames at 640×480 and keeps the last, with a 15-second timeout. Check for an existing camera owner first. Its frame timestamps use the Jetson's monotonic clock; a copied JSON timestamp cannot be compared with the MacBook clock. A snapshot is not a live detector feed.

If a short detector-only run is useful, first confirm the camera is free and the existing engines are present. Run on the Jetson from the hub checkout, replacing the camera placeholder:

```sh
YOLO_OFFLINE=1 YOLO_CONFIG_DIR=/home/fadi/hub-tests/yolo-config \
  /home/fadi/hub-tests/venv-trt10/bin/python -m vision.run \
  --camera <verified-camera-index> \
  --detect /home/fadi/hub-tests/engines-fp16/yolo26n.engine \
  --pose /home/fadi/hub-tests/engines-fp16/yolo26n-pose.engine \
  --device 0 --target real --max-seconds 30 \
  --telemetry /home/fadi/hub-tests/sessions
```

In the reviewed code, `vision.run --target real` prints proposed actions; it does not itself call Dream or move an arm. Do not pipe it into an actuator during this check. Omit `--show` over SSH. Initial GPU warm-up took 5–12 seconds in the recorded run. This command opens the camera and writes telemetry; it is a bounded test, not passive inventory.

For a quick overnight-suite rerun from the MacBook, `JETSON_HOST=orin-arm scripts/jetson_hub_check.sh` runs offline suites and writes logs, **and also fetches/fast-forwards the hub checkout**. It is not a read-only status check. Inspect/preserve that checkout first. Do not start with `--setup`, `--engines` or `--all`; these can reinstall dependencies, take about 20 minutes to rebuild engines, or occupy the camera.

To view existing sessions, inspect port 8780 first and reuse an existing intended server. If free, in one MacBook terminal:

```sh
ssh -t orin-arm 'cd ~/hub-tests/geotech-robotics-hackathon && python3 -m control_center --data ~/hub-tests/sessions serve'
```

In a second MacBook terminal:

```sh
ssh -N -L 18780:127.0.0.1:8780 orin-arm
```

Open `http://127.0.0.1:18780`. Keep the server on Jetson localhost. Stop only the server/tunnel you started when finished. `--demo` generates simulated sessions; do not use it as evidence of physical activity.

## What the new session should report and save

- Current router model/firmware, WAN/LAN roles, actual Jetson/KVM leases, internet/captive-portal state, and trusted MacBook SSH result.
- Whether native KVM video and independent power work; actual webcam identity and view.
- Current HEAD/local changes in both Jetson checkouts, preserved model/engine environments, current process/adapter ownership, and the chosen demo path.
- Actual preflight verdict, live trial counts if any, and the smallest next action. No claim that overnight replay/simulation proves a real handoff.
- Submission owner, actual current submission route/cutoff, backup-video location and one timed demo run. [Creative rubric](../../demo/pitch/deck/creative-rubric.md) calls for a three-minute station demo and two minutes of questions; the May event's form/16:00 cutoff is not established for November.
- Focused commits/pushes to **origin main**, each meaningful result/blocker reflected in a new `dashboard/updates/` JSON. Run `python3 dashboard/build.py --check`; preserve other contributors' work and never publish secrets or raw private camera/log data.

Read in order: this handoff → [current handoff routine](../../handoff/README.md) → [Jetson hub results](../jetson-hub-testing.md) → [Table 10 real arms](../table10-real-arms.md) → [plan](../plan.md) → [submission draft](../../demo/submission.md). The [team dashboard](https://zzfadi.github.io/geotech-robotics-hackathon/) is the shared progress record.

## Prompt to paste into the new MacBook session

```text
Continue the Human Touch / Hold My Beer hackathon work from this MacBook.
Repository: https://github.com/zzfadi/geotech-robotics-hackathon

Find/reuse my checkout, inspect local changes, read AGENTS.md, and synchronize
main safely. Start with docs/handoffs/macbook-venue-resume-2026-11-08.md.
It records the Mac Mini overnight work and a review through a0f4353; fetch
newer main updates too. Work and push directly on main, with dashboard entries.

I am at the venue with the Jetson Orin Nano, GL-RM1 KVM, C270 webcam and
GL-MT3000 Beryl AX physically connected. I reset Beryl after its admin
password failed. First recover its local network: verify WAN-to-LAN port
mode, venue Wi-Fi uplink, and actual Jetson/KVM leases. Old IPs are hints only.
Establish this MacBook's own fingerprint-verified SSH access; Mac Mini keys
and Keychain credentials are not automatically available here.

Preserve the Jetson's two checkouts, pinned runtime, GPU environments and
existing engines. Inventory current services and CAN ownership before any
controller launch. Do not reinstall or start motors as part of network checks.
The newer Table 10 service and pickup_server are separate control paths.

Then report what is verified here, the current working demo path, and the
smallest next action. Continue authorized setup work; ask me only for physical
actions or genuinely missing credentials/decisions. Distinguish recorded
overnight results from venue checks. Verify the actual submission deadline
and ownership; don't reuse the old morning schedule as the current plan.
```
