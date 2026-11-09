# /// script
# requires-python = ">=3.11"
# dependencies = ["psutil>=6"]
# ///
"""Relaunch this Mac's one TheHumanTouch editor with the Quest (OXRSys over USB) available.

Source lives in Tools/QuestVR (see README.md); the local, untracked VR kit (OXRSys runtime,
adb, Quest app) lives in Saved/XR. It reuses Tools/join_human_touch.py for the normal session
arguments and adds only what the headset needs:
  * OpenXRMetal + VirtualScouting plugins (editor VR Mode on Metal)
  * XR_RUNTIME_JSON -> the OXRSys runtime built in Saved/XR/OpenXR-OSX
  * OXRSYS_TRANSPORT=usb_adb and adb reverse ports so the headset app reaches this Mac
After the editor is in the session, press VR Mode on the editor toolbar.
"""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
XR = PROJECT / 'Saved' / 'XR'
sys.path.insert(0, str(PROJECT / 'Tools'))
# Editor-side helpers, copied into Saved/XR at launch: the agents reach their toolsets there
# (Temp.XR.editor_vr_state.EditorVRState, Temp.XR.table10_robot_tools.Table10RobotTools)
HELPERS = ('editor_vr_state.py', 'table10_vr_grab.py', 'table10_robot_tools.py')
import join_human_touch as jht  # noqa: E402

ADB = XR / 'platform-tools/adb'
# The headset authorizes this key (kept here, not ~/.android); an adb server started
# with any other key shows the Quest as unauthorized.
ADB_HOME = XR / 'adb-home'
ADB_ENV = dict(os.environ, HOME=str(ADB_HOME), ANDROID_USER_HOME=str(ADB_HOME / '.android'))
RUNTIME_JSON = XR / 'OpenXR-OSX/build/runtime/oxrsys-runtime.json'
OXRSYS_PORTS = (9944, 9945, 9946, 9948)
OXRSYS_ACTIVITY = 'net.demonixis.oxrsys.android/com.oculus.NativeActivity'


def install_helpers():
    XR.mkdir(parents=True, exist_ok=True)
    for name in HELPERS:
        (XR / name).write_bytes((HERE / name).read_bytes())


def adb(*args, check=False):
    return subprocess.run([str(ADB), *args], capture_output=True, text=True, timeout=20, check=check, env=ADB_ENV)


def prepare_headset():
    state = adb('get-state').stdout.strip()
    if state != 'device':
        raise SystemExit(f'Quest not ready over USB (adb state: {state or "none"}). '
                         'Accept "Allow USB debugging" in the headset, then retry.')
    for port in OXRSYS_PORTS:
        adb('reverse', f'tcp:{port}', f'tcp:{port}', check=True)
    adb('shell', 'am', 'start', '-n', OXRSYS_ACTIVITY)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--local-address', required=True, help='This Mac\'s VPN IPv4 (Tailscale)')
    parser.add_argument('--name', default='Denys Mac Codex', help='Keep the existing shared-editor identity')
    parser.add_argument('--engine', type=Path, default=Path('/Users/Shared/Epic Games/UE_5.8/Engine'))
    parser.add_argument('--dry-run', action='store_true', help='Print the command and environment only')
    args = parser.parse_args()

    if platform.system() != 'Darwin':
        raise SystemExit('Mac only.')
    for need in (PROJECT / 'Plugins/OpenXRMetal/Binaries/Mac/UnrealEditor.modules', RUNTIME_JSON, ADB):
        if not need.exists():
            raise SystemExit(f'Missing {need}')
    import json
    build = json.loads((args.engine / 'Build/Build.version').read_text())
    if (build['MajorVersion'], build['MinorVersion'], build['PatchVersion'], build['Changelist']) != (5, 8, 3, 58210709):
        raise SystemExit('Requires UE 5.8.3 CL 58210709')

    command = jht.arguments(args.engine, args.local_address, args.name)
    # CoPresenceVR + CoPresenceScouting: the Agents tool in Virtual Scouting's VR menu (Call, captions,
    # left-stick click to call/hang up), ported from AgentCoPresence and built for Mac in Saved/XR.
    command.append('-EnablePlugins=OpenXRMetal,VirtualScouting,CoPresenceVR,CoPresenceScouting')
    env = dict(os.environ, XR_RUNTIME_JSON=str(RUNTIME_JSON), OXRSYS_TRANSPORT='usb_adb', OXRSYS_ADB_PATH=str(ADB))

    if args.dry_run:
        print('\n'.join(command))
        print({k: env[k] for k in ('XR_RUNTIME_JSON', 'OXRSYS_TRANSPORT', 'OXRSYS_ADB_PATH')})
        return

    running = jht.editors()
    if running:
        raise SystemExit('An Unreal Editor is already running (PIDs: ' + ', '.join(str(p['pid']) for p in running)
                         + '). Save and quit it first. No editor started.')
    install_helpers()
    prepare_headset()
    logdir = PROJECT / 'Saved/CoPresenceLogs'
    logdir.mkdir(parents=True, exist_ok=True)
    with (logdir / 'shared-editor-launch.log').open('ab') as log:
        proc = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=log, env=env, start_new_session=True)
    print(f'Started VR-capable shared editor PID {proc.pid}. When it is in TheHumanTouch, press VR Mode.')
    start_grab_helper(proc)


GRAB_HELPER_CODE = (
    "import sys, importlib, unreal\n"
    "p = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()) + 'XR'\n"
    "sys.path.insert(0, p) if p not in sys.path else None\n"
    # First, before anyone can enter VR Mode: agent viewport images crash the editor there
    "import editor_vr_state as v\n"
    "importlib.reload(v)\n"
    "print('VR STATE', v.register())\n"
    "import table10_vr_grab as g\n"
    "importlib.reload(g)\n"
    "print('GRAB', g.start())\n"
    "import table10_robot_tools as t\n"
    "importlib.reload(t)\n"
    "print('ROBOT TOOLS', t.register())\n")


EDITOR_LOG = Path.home() / 'Library/Logs/Unreal Engine/TheHumanTouchEditor/TheHumanTouch.log'
JOINED = 'LogConcert: Display: Initialized Concert session'
JOIN_PROMPT = 'LogConcert: [Prompt] Joining Session'


def wait_for_session(proc, started, timeout_s):
    """Until this editor has joined the Multi-User session. The helpers change level actors, and a
    change made before the join makes Multi-User stop the join to ask about discarding it."""
    deadline = time.time() + timeout_s
    prompted = False
    while time.time() < deadline and proc.poll() is None:
        try:
            # The editor moves the previous log aside when it starts, so a log this new is its own
            if EDITOR_LOG.stat().st_birthtime >= started - 5:
                text = EDITOR_LOG.read_text(errors='replace')
                if JOINED in text:
                    return True
                if JOIN_PROMPT in text and not prompted:
                    prompted = True
                    print('Multi-User is asking about this editor\'s in-memory changes; answer it in the editor.')
        except OSError:
            pass
        time.sleep(5)
    return False


def start_grab_helper(proc, timeout_s=600):
    """Once the editor is in the session and its MCP answers, start the Table 10 helpers."""
    started = time.time()
    if not wait_for_session(proc, started, timeout_s):
        print('The editor did not join the session; Table 10 helpers not started.')
        return
    # Let the session's activities finish applying before the helpers look at the arms
    time.sleep(20)
    deadline = started + timeout_s
    while time.time() < deadline and proc.poll() is None:
        out = subprocess.run(['uv', 'run', '--quiet', '--script', str(PROJECT / 'Tools/copresence_mcp.py'),
                              'exec-python', '8041', GRAB_HELPER_CODE],
                             cwd=PROJECT, capture_output=True, text=True, timeout=120).stdout
        if 'GRAB' in out:
            print('Table 10 VR grab helper started (one wrist handle per arm), robot toolset registered for agents.')
            return
        time.sleep(10)
    print('Table 10 VR grab helper not started; run it from the editor (see Tools/QuestVR/README.md).')


if __name__ == '__main__':
    main()
