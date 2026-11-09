# Real arms through the Jetson

Both Maker Arms and their three cameras are plugged into the Jetson Orin Nano (`orin-arm`). Dream's own robot station
runs there and drives them; this is the same software and the same model the Dream website uses (verified 2026-11-08:
the hand-to-hand bottle handover with Saleh's prompt). Everyone reaches it from their laptop over the travel router.

**Status (2026-11-08, evening):** the hackathon is over and the arms, cameras and Jetson are disconnected. To run
again, plug them back into the Jetson as listed in section 5, then `DreamSim/station.sh demo`.

No passwords, Wi-Fi keys or API keys belong in this file, in chat or in any commit. Ask FADI for them in person.

## 1. Join the travel router

The travel router is the GL-MT3000 Beryl at the station. Join its Wi-Fi from your laptop (the network name and key
are on the router's label; ask FADI). The Jetson is wired to it and answers as **`orin-arm.local`** (mDNS), currently
`192.168.8.136`. The address is not reserved, so prefer the name.

```bash
ping -c1 orin-arm.local
```

## 2. Get SSH access (once per laptop)

Make a key and send FADI the **public** half (`.pub`). Never send the private file.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_orin_arm -C "<your name> laptop to orin-arm"
cat ~/.ssh/id_ed25519_orin_arm.pub
```

FADI checks the key and authorizes it from a laptop that already has access:

```bash
cat their_key.pub | ssh orin-arm 'cat >> ~/.ssh/authorized_keys'
```

Then add this to `~/.ssh/config` on your laptop and check it. The host key fingerprint is
`SHA256:1Re9L+dbMLneresWeHhOM37nO2PW7a3mjuEhyHNGvvk` (ED25519); accept only that one.

```
Host orin-arm
    HostName orin-arm.local
    User fadi
    IdentityFile ~/.ssh/id_ed25519_orin_arm
    IdentitiesOnly yes
```

```bash
ssh orin-arm hostname        # prints orin-arm
```

Everyone shares the `fadi` account through their own key. Commands that need `sudo` ask for the Jetson password;
only FADI types it.

## 3. Run the arms

From a clone of this repo (`git pull` first). Someone at the robot keeps a hand on the **E-stop** whenever a run starts.

| Command | What it does |
| --- | --- |
| `DreamSim/station.sh demo` | **The demo.** Starts the station if needed, runs Saleh's prompt, prints progress, then stops and sends both arms home after 120 s or Ctrl-C (`demo 60` for 60 s). |
| `DreamSim/station.sh status` | State, plans received, pauses, Dream round trip. Read-only. |
| `DreamSim/station.sh start "prompt"` | Run another prompt. **Arms move.** |
| `DreamSim/station.sh stop` | End the run; both arms hold where they are (they do not drop). |
| `DreamSim/station.sh home` | Move both arms to their home pose (3 s). |
| `DreamSim/station.sh up` | Start the station. Asks the Jetson password if the CAN links are down (after a reboot). |
| `DreamSim/station.sh down` | Stop the station. Both arms go limp: hold them, or home them first. |

Saleh's prompt (the default): *"Use the right arm to take the bottle from my hand, place it in my other open hand,
release it, and return home. Keep the left arm still."* Model: `dreamlabs/dream-1-beta` (override with
`DREAM_CHECKPOINT`).

Other ways in, same station:

- **Dream website:** https://platform.dreamlabs.ai/live, robot `orin-arm` (`rbt_574d07a82815d561`) on FADI's Dream
  account. Press Start, send the prompt, press Stop.
- **The station's HTTP API** on the Jetson, `127.0.0.1:8091` only: `GET /status`, `POST /start {"prompt", "checkpoint"}`,
  `POST /stop`, `POST /home`, `POST /prompt {"prompt"}`. Call it through SSH (`ssh orin-arm 'curl -s
  http://127.0.0.1:8091/status'`) or a tunnel (`ssh -N -L 8091:127.0.0.1:8091 orin-arm`).
- **What the model sees** during a run: `ssh -N -L 8093:127.0.0.1:8093 orin-arm`, then open http://127.0.0.1:8093
  (each call's camera frames, joint state, prompt and returned plan).

What to expect: Dream answers about every 1.2 s (most of it on Dream's side), so the arms pause now and then. The run
does not end by itself: press Stop after the arm returns home. Both arms move even when the prompt says to keep one
still, because the base model plans both. Holding a reach heats the motors: stop and home between tries.

## 4. Rules for people and agents

- **One owner per arm adapter.** Only the station drives the arms. Do not run `arm/tools` programs (`arm scan`,
  `pickup_server.py`), `DreamSim/dream_arm.py` or anything else that opens the adapters on the Jetson; while the
  station runs, the `gs_usb` driver holds them anyway.
- **Say what will move before it moves**, and only start a run with a person at the E-stop. `start` and `demo` move
  both arms; `home` moves them; `down` lets them go limp.
- **Stop, then home** to abort. `stop` holds, it never drops.
- **Keep services on `127.0.0.1`** and use SSH tunnels; never bind anything on the Jetson to `0.0.0.0`.
- **Never type, store or commit passwords**, the Wi-Fi key or the Dream API key. The Dream key on the Jetson lives in
  `~/.dreamlabs/credentials` (mode 600); never print it.
- The Jetson's presenter dashboard (`vision.live_view`) is stopped because the station uses the arm cameras.

## 5. How it is set up (to rebuild it)

| Part | Where | Notes |
| --- | --- | --- |
| USB-CAN driver | `arm/jetson-station/install-gs_usb.sh` | The Jetson kernel (6.8.12-1021-tegra) ships without `gs_usb`. The script builds it from the pinned Linux v6.8 source (hash checked), installs it and loads it at boot. Copy it over and run it there (needs sudo): `scp arm/jetson-station/install-gs_usb.sh orin-arm: && ssh -t orin-arm ./install-gs_usb.sh`. |
| Arm link names | `arm/jetson-station/can-names.sh` | udev rule from `dreamcli setup`: right adapter `…3238` is `can_follower_r`, left `…3038` is `can_follower_l`. Run it the same way (needs sudo). |
| Dream station | `~/dream-station/.venv` on the Jetson | `dreamlabs==1.0.37`, `dreamlabs-client==1.1.3` from PyPI, installed with uv. |
| Station config | `~/.dreamlabs/station.yaml` (copy: `arm/jetson-station/station.yaml`) | Written by `dreamcli setup`: Maker arms, both followers, cameras overhead `2.3.1.3`, left wrist `2.3.1.4`, right wrist `2.3.1.1` on the Jetson's USB hub. |
| Dream key | `~/.dreamlabs/credentials` | `DREAM_API_KEY=…`, typed by FADI; never in the repo. |

To redo the station config, run the wizard over a tunnel and open the address it prints:

```bash
ssh -t -L 8092:127.0.0.1:8092 orin-arm '/home/fadi/dream-station/.venv/bin/dreamcli setup --host 127.0.0.1 --no-browser'
```

The wizard's "Move to home" and "Test joints" steps move the arms.
