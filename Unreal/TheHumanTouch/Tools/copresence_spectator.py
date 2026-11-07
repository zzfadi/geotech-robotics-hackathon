# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "pillow>=11", "httpx>=0.27", "psutil>=5.9"]
# ///
"""Starts and steers the spectator: a session client of its own, neither a person nor an agent,
whose camera follows whoever you pick, a person in VR or at a desktop editor or an agent, or flies
freely, and sends that picture out so people outside VR see the session in a video call.

    uv run --script Tools/copresence_spectator.py start <host address or web address> <session code>
    uv run --script Tools/copresence_spectator.py follow "<name>" [--eyes]
    uv run --script Tools/copresence_spectator.py free
    uv run --script Tools/copresence_spectator.py meet <meet link>
    uv run --script Tools/copresence_spectator.py leave
    uv run --script Tools/copresence_spectator.py status
    uv run --script Tools/copresence_spectator.py stop
    uv run --script Tools/copresence_spectator.py sign-in

`start` opens an Unreal editor in the background, joined to the session as "Spectator"
(--name), with its own Saved folder (AgentUser/Spectator) and an MCP server on port 8040.
Its camera (Plugins/CoPresenceCall) films the session and sends it as the NDI source
"CoPresence Spectator", which OBS or NDI Tools can show too. People in the session see a red
camera named Spectator where it films from. A web address is the always-on session, reached
through the relay (Tools/copresence_relay.py) like the join scripts do.

Whom it follows is kept by the session hub, so the session dashboard (the hub's own address)
and `follow` set the same thing: behind and above them, or through their eyes with --eyes.
`free` lets it fly freely: it films what its editor's viewport sees, which someone at this
machine can fly with the mouse, and the dashboard's arrows move it too.

`meet` brings the spectator into a Google Meet call as its own participant, with its picture
as the camera (Tools/copresence_call.py join --spectator); someone in the call admits it.
Organizations that turn away guests need its call browser signed in to Google once: `sign-in`.

The session dashboard can do all of this through a station on this machine
(Tools/copresence_station.py), which runs these same steps.

--hub points the spectator at a hub other than the session's usual one, such as a test hub;
the clone's own session files (Saved/CoPresenceLogs/hub_code and host_address), which people's
editors here read, are then left alone.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import copresence_network  # noqa: E402

PROJECT_DIR = TOOLS_DIR.parent
LOG_DIR = PROJECT_DIR / "Saved" / "CoPresenceLogs"
STATE_FILE = LOG_DIR / "spectator.json"
USER_DIR = PROJECT_DIR / "AgentUser" / "Spectator"
CALL_SCRIPT = TOOLS_DIR / "copresence_call.py"
RELAY_SCRIPT = TOOLS_DIR / "copresence_relay.py"
WINDOWS = sys.platform == "win32"
DEFAULT_ENGINE_DIR = (r"C:\Program Files\Epic Games\UE_5.8\Engine" if WINDOWS
                      else "/Users/Shared/Epic Games/UE_5.8/Engine")
DEFAULT_NAME = "Spectator"
DEFAULT_SERVER = "AgentCoPresence"
MULTI_USER_PORT = 9030
HUB_PORT = 8790
MCP_PORT = 8040
CAMERA_TOOLSET = "copresence_call.toolset.CallCameraTools"
SPECTATOR_FLAG = "-COPRESENCESPECTATOR"
# The camera people see where the spectator films from: red, like a camera's recording light
AVATAR_COLOR = "(R=0.900,G=0.120,B=0.120,A=1.000)"
RELAY_WAIT_SECONDS = 30
STOP_WAIT_SECONDS = 20
# A call that fails right away, such as without the spectator's camera, has said why by then
CALL_EARLY_EXIT_SECONDS = 8
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}")


class SpectatorError(RuntimeError):
    pass


# Where things are ------------------------------------------------------------------------

def project_settings() -> dict:
    """The project file, session and level everyone opens, as the join scripts read them."""
    settings = {"project": PROJECT_DIR / "AgentCoPresence.uproject", "session": "CoPresence", "map": ""}
    config = PROJECT_DIR / "Config" / "CoPresenceProject.json"
    if config.exists():
        saved = json.loads(config.read_text())
        settings.update(project=PROJECT_DIR / saved["project"], session=saved["session"], map=saved.get("map", ""))
    settings["session"] = os.environ.get("COPRESENCE_SESSION_NAME", settings["session"])
    settings["map"] = os.environ.get("COPRESENCE_STARTUP_MAP", settings["map"])
    return settings


def engine_dir() -> Path:
    return Path(os.environ.get("COPRESENCE_ENGINE_DIR", DEFAULT_ENGINE_DIR))


def plugins() -> list[str]:
    """The call camera, seeing what people in VR draw where that plugin has been built, and
    Virtual Scouting where it can load: a session asks everyone who joins for the plugins of the
    editor that made it, and an editor in VR Mode loads Virtual Scouting and all it needs. The
    engine's own loads, unless the project carries one that isn't built for this platform."""
    enabled = ["CoPresenceCall"]
    built = (PROJECT_DIR / "Plugins/CoPresenceVR/Binaries/Win64/UnrealEditor-CoPresenceVR.dll" if WINDOWS
             else PROJECT_DIR / "Plugins/CoPresenceVR/Binaries/Mac/UnrealEditor.modules")
    if built.exists():
        enabled.append("CoPresenceVR")
    scouting = PROJECT_DIR / "Plugins/VirtualScouting"
    scouting_built = (scouting / "Binaries/Win64/UnrealEditor-VirtualScouting.dll" if WINDOWS
                      else scouting / "Binaries/Mac/UnrealEditor.modules")
    if not scouting.is_dir() or scouting_built.exists():
        enabled.append("VirtualScouting")
    return enabled


def editor_arguments(project: Path, startup_map: str, server: str, session: str, host: str, port: int,
                     local_address: str, name: str, mcp_port: int, log_path: Path, hub_url: str = "",
                     enabled_plugins: list[str] | None = None, offscreen: bool = False) -> list[str]:
    """What the spectator's editor starts with: joined to the session like an agent's editor,
    with an MCP server for the call, ticking in the background, and the spectator's camera on."""
    arguments = [str(project)]
    if startup_map:
        arguments.append(startup_map)
    arguments += [
        f"-CONCERTSERVER={server}",
        f"-CONCERTSESSION={session}",
        "-CONCERTAUTOCONNECT",
        "-CONCERTRETRYAUTOCONNECTONERROR",
        "-CONCERTSKIPSOURCECONTROLCHECKS=true",
        f"-UDPMESSAGING_TRANSPORT_UNICAST={local_address}:0",
        f"-UDPMESSAGING_TRANSPORT_STATIC={host}:{port}",
        f"-EnablePlugins={','.join(enabled_plugins or ['CoPresenceCall'])}",
        # Avatars show in its camera's picture, which leaves them out otherwise
        "-ini:ConcertSyncCore:[/Script/ConcertSyncCore.ConcertSyncConfig]:ShowPresenceInGame=VisibleInGameOnlyEditor",
        # Console variables others' editors set, such as an agent's frame rate cap, would slow the picture
        "-ini:Engine:[/Script/ConsoleVariablesEditor.ConcertCVarConfig]:LocalSettings=(bReceiveCVarChanges=False)",
        f"-CONCERTDISPLAYNAME={name}",
        f"-userdir={USER_DIR}",
        "-multiprocess",
        "-ini:EditorPerProjectUserSettings:[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]:bAutoStartServer=True",
        f"-ini:EditorPerProjectUserSettings:[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]:ServerPortNumber={mcp_port}",
        "-ini:EditorSettings:[/Script/UnrealEd.EditorPerformanceSettings]:bThrottleCPUWhenNotForeground=False",
        # After an editor that was closed abruptly, nobody is there to answer the restore prompt
        "-AutoDeclinePackageRecovery",
        SPECTATOR_FLAG,
        f"-abslog={log_path}",
    ]
    if hub_url:
        arguments.append(f"-COPRESENCEHUB={hub_url}")
    if offscreen:
        arguments.append("-RenderOffscreen")
    return arguments


def windows_command_line(executable: Path, arguments: list[str]) -> str:
    """Unreal reads its command line as written, so a value with spaces is quoted after the
    equals sign, -KEY="a value", the way the Windows join script passes names."""
    parts = [f'"{executable}"']
    for argument in arguments:
        key, equals, value = argument.partition("=")
        if " " not in argument:
            parts.append(argument)
        elif equals and argument.startswith("-"):
            parts.append(f'{key}="{value}"')
        else:
            parts.append(f'"{argument}"')
    return " ".join(parts)


def write_config(name: str) -> None:
    """The spectator's own Engine.ini layer: its name and red camera, and at most 30 frames a
    second, the rate its camera films at. Its viewport draws in realtime, since Multi-User only moves
    people's avatars while it does, so the cap keeps that second picture from costing more."""
    config_dir = USER_DIR / "Saved" / "Config" / ("WindowsEditor" if WINDOWS else "MacEditor")
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "Engine.ini").write_text(
        "[/Script/ConcertClient.ConcertClientConfig]\n"
        f'ClientSettings=(DisplayName="{name}",AvatarColor={AVATAR_COLOR})\n'
        "\n"
        "[/Script/UnrealEd.EditorEngine]\n"
        "bSmoothFrameRate=True\n"
        "SmoothedFrameRateRange=(LowerBound=(Type=Inclusive,Value=5.000000),UpperBound=(Type=Inclusive,Value=30.000000))\n")


def _saved() -> dict:
    if not STATE_FILE.exists():
        raise SpectatorError("The spectator hasn't been started from this clone; start it first")
    return json.loads(STATE_FILE.read_text())


# Processes ---------------------------------------------------------------------------------------

def editor_processes() -> list:
    """The spectator's editor from this clone, if it runs."""
    import psutil
    project = str(project_settings()["project"]).replace("\\", "/").casefold()
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info["cmdline"] or [])
        except (psutil.Error, TypeError):
            continue
        if SPECTATOR_FLAG in command and project in command.replace("\\", "/").casefold():
            found.append(process)
    return found


def _listening(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _detached() -> dict:
    """Keeps a started process running after this script, or the station, exits."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def ensure_relay(url: str, code: str) -> None:
    """Reaches the always-on session through the relay, listening where a session on this
    machine would be. One that already runs for the address is kept, so editors joined through
    it stay joined; one for another session is left alone too."""
    import psutil
    for process in psutil.process_iter(["cmdline"]):
        command = " ".join(process.info["cmdline"] or [])
        if "copresence_relay.py" in command and " connect " in command:
            if f" connect {url} " in f"{command} ":
                return
            raise SpectatorError("This machine is joined to another session through its relay; leave that first")
    if _listening(HUB_PORT):
        raise SpectatorError(f"Something on this machine already listens on {HUB_PORT}, where the relay goes")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / "relay.log", "a", encoding="utf-8")
    relay = subprocess.Popen([shutil.which("uv") or "uv", "run", "--quiet", "--script", str(RELAY_SCRIPT), "connect", url,
                              "--code", code], cwd=PROJECT_DIR, stdin=subprocess.DEVNULL, stdout=log,
                             stderr=subprocess.STDOUT, **_detached())
    deadline = time.monotonic() + RELAY_WAIT_SECONDS
    while not _listening(HUB_PORT):
        if relay.poll() is not None or time.monotonic() > deadline:
            raise SpectatorError(f"Couldn't reach {url} through the relay; see {LOG_DIR / 'relay.log'}")
        time.sleep(1)


def _windows_session_zero() -> bool:
    """Started over SSH or as a service, Windows runs this in session 0, without a desktop."""
    import ctypes
    session = ctypes.c_ulong()
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    return session.value == 0


# What it does --------------------------------------------------------------------------------------

def start(address: str, code: str, name: str = DEFAULT_NAME, server: str = DEFAULT_SERVER,
          port: int = MULTI_USER_PORT, mcp_port: int = MCP_PORT, hub_url: str = "") -> str:
    """Opens the spectator's editor, joined to the session at address, in the background."""
    if not NAME_PATTERN.fullmatch(name):
        raise SpectatorError("The spectator's name has letters, digits, spaces, dots, dashes or underscores")
    if editor_processes():
        raise SpectatorError("The spectator is already running from this clone")
    settings = project_settings()
    engine = engine_dir()
    editor = engine / "Binaries" / ("Win64/UnrealEditor.exe" if WINDOWS else "Mac/UnrealEditor.app")
    if not editor.exists():
        raise SpectatorError(f"Unreal Editor not found at {editor}; set COPRESENCE_ENGINE_DIR to your UE 5.8 Engine folder")
    if _listening(mcp_port):
        raise SpectatorError(f"Something on this machine already listens on {mcp_port}, the spectator's MCP port")

    host = address
    if not re.fullmatch(r"\d+(\.\d+){3}", address):
        # A web address is the always-on session, which only the relay reaches
        if not code:
            raise SpectatorError(f"Joining {address} needs the session code")
        ensure_relay(address, code)
        host = "127.0.0.1"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not hub_url:
        # The spectator's editor and its call find the hub and the code where people's editors here do
        (LOG_DIR / "host_address").write_text(host)
        if code:
            (LOG_DIR / "hub_code").write_text(code)

    write_config(name)
    log_path = LOG_DIR / "spectator.log"
    arguments = editor_arguments(settings["project"], settings["map"], server, settings["session"], host, port,
                                 copresence_network.local_address(host), name, mcp_port, log_path, hub_url,
                                 plugins(), offscreen=WINDOWS and _windows_session_zero())
    if WINDOWS:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # Minimized, without taking focus
        startup.wShowWindow = 7
        subprocess.Popen(windows_command_line(editor, arguments), startupinfo=startup, close_fds=True,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        # Through `open`, so it belongs to launchd rather than to this script, without taking focus
        subprocess.run(["open", "-n", "-g", str(editor), "--args", *arguments], check=True)
    STATE_FILE.write_text(json.dumps({"name": name, "hub": hub_url or f"http://{host}:{HUB_PORT}", "code": code,
                                      "mcp_port": mcp_port, "address": address}))
    return f"Starting {name}; its editor joins the session in a minute or two."


def stop() -> str:
    """Leaves its call, if it is in one, and closes the spectator's editor."""
    try:
        leave()
    except SpectatorError:
        pass
    import psutil
    processes = editor_processes()
    if not processes:
        return "The spectator isn't running"
    for process in processes:
        process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=STOP_WAIT_SECONDS)
    for process in alive:
        process.kill()
    return "Stopped the spectator"


def control(action: str, fields: dict) -> dict:
    """Asks the session hub, as the dashboard does, with the session code."""
    saved = _saved()
    request = urllib.request.Request(f"{saved['hub']}/api/control", method="POST",
                                     data=json.dumps({"action": action, **fields}).encode(),
                                     headers={"Content-Type": "application/json", "X-CoPresence-Code": saved["code"]})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            reply = json.loads(error.read() or b"{}")
        raise SpectatorError(reply.get("error") or f"The hub answered {error.code}") from error
    except (urllib.error.URLError, OSError) as error:
        raise SpectatorError(f"Can't reach the session hub at {saved['hub']}: {error}") from error


def follow(target: str, eyes: bool = False) -> str:
    control("spectator.follow", {"target": target, "framing": "eyes" if eyes else "behind"})
    if not target:
        return "The spectator flies freely now"
    return f"The spectator follows {target}{' through their eyes' if eyes else ''}"


def meet(link: str) -> str:
    """Brings the spectator into a Google Meet call, in the background."""
    import copresence_mcp
    saved = _saved()
    url = copresence_mcp.meet_link(link)
    if url is None:
        raise SpectatorError("Only Google Meet links work so far, like https://meet.google.com/abc-defg-hij")
    if not editor_processes():
        raise SpectatorError("The spectator isn't running; start it first")
    if copresence_mcp.running_call(saved["name"]):
        raise SpectatorError("The spectator is already in a call; leave it first")
    log_path = LOG_DIR / f"call-{copresence_mcp.agent_slug(saved['name'])}.log"
    environment = {**os.environ, "COPRESENCE_UNREAL_MCP_URL": f"http://127.0.0.1:{saved['mcp_port']}/mcp",
                   "COPRESENCE_HUB_URL": saved["hub"], "COPRESENCE_CODE": saved["code"]}
    with open(log_path, "a", encoding="utf-8") as log:
        call = subprocess.Popen([shutil.which("uv") or "uv", "run", "--quiet", "--script", str(CALL_SCRIPT), "join", url,
                                 "--spectator", "--as", saved["name"]], cwd=PROJECT_DIR, env=environment,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **_detached())
    try:
        call.wait(timeout=CALL_EARLY_EXIT_SECONDS)
    except subprocess.TimeoutExpired:
        return "Joining the call; someone in it has to let the spectator in"
    lines = [line for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    raise SpectatorError(f"The call ended right away: {lines[-1] if lines else 'see ' + str(log_path)}")


def leave() -> str:
    import copresence_mcp
    name = _saved()["name"]
    pid = copresence_mcp.running_call(name)
    if pid is None:
        raise SpectatorError("The spectator isn't in a call")
    if WINDOWS:
        # Ending the process at once would skip leaving the call first
        copresence_mcp.call_leave_path(name).touch()
    else:
        os.kill(pid, signal.SIGTERM)
    return "Leaving the call"


def status() -> dict:
    import copresence_mcp
    state = {"running": bool(editor_processes())}
    if not STATE_FILE.exists():
        return state
    saved = _saved()
    state.update(name=saved["name"], hub=saved["hub"], in_call=bool(copresence_mcp.running_call(saved["name"])))
    if state["running"]:
        try:
            editor = copresence_mcp.UnrealMcp(f"http://127.0.0.1:{saved['mcp_port']}/mcp")
            state["camera"] = json.loads(editor.call_toolset(CAMERA_TOOLSET, "spectator_status", {}))
        except Exception as error:
            state["camera"] = {"running": False, "problem": copresence_mcp._short_error(error)}
    return state


def sign_in() -> int:
    """Opens the spectator's call browser for a person to sign in to Google once."""
    name = _saved()["name"] if STATE_FILE.exists() else DEFAULT_NAME
    return subprocess.call([shutil.which("uv") or "uv", "run", "--quiet", "--script", str(CALL_SCRIPT), "sign-in",
                            "--as", name], cwd=PROJECT_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    starting = commands.add_parser("start", help="Open the spectator's editor, joined to the session")
    starting.add_argument("address", help="The host's address, or the always-on session's, like multiunreal.geotech-hub.com")
    starting.add_argument("code", help="The session code")
    starting.add_argument("--name", default=DEFAULT_NAME, help="Its name in the session")
    starting.add_argument("--server", default=DEFAULT_SERVER, help="The Multi-User server's name")
    starting.add_argument("--port", type=int, default=MULTI_USER_PORT, help="The Multi-User server's port")
    starting.add_argument("--mcp-port", type=int, default=MCP_PORT, help="The spectator's editor's MCP port")
    starting.add_argument("--hub", default="", help="A hub other than the session's usual one, like http://127.0.0.1:8791")
    following = commands.add_parser("follow", help="Follow someone in the session")
    following.add_argument("target", help="Their name, as the dashboard shows it")
    following.add_argument("--eyes", action="store_true", help="Film through their eyes rather than from behind")
    commands.add_parser("free", help="Fly freely, filming what the spectator's viewport sees")
    meeting = commands.add_parser("meet", help="Show the spectator's picture in a Google Meet call")
    meeting.add_argument("link", help="The Meet link, like https://meet.google.com/abc-defg-hij")
    commands.add_parser("leave", help="Leave the call")
    commands.add_parser("status", help="Say how the spectator is doing")
    commands.add_parser("stop", help="Leave the call and close the spectator's editor")
    commands.add_parser("sign-in", help="Sign the spectator's call browser in to Google once")
    arguments = parser.parse_args()

    try:
        if arguments.command == "start":
            print(start(arguments.address, arguments.code.strip().upper(), arguments.name, arguments.server,
                        arguments.port, arguments.mcp_port, arguments.hub.rstrip("/")))
        elif arguments.command == "follow":
            print(follow(arguments.target, arguments.eyes))
        elif arguments.command == "free":
            print(follow(""))
        elif arguments.command == "meet":
            print(meet(arguments.link))
        elif arguments.command == "leave":
            print(leave())
        elif arguments.command == "status":
            print(json.dumps(status(), indent=2))
        elif arguments.command == "stop":
            print(stop())
        else:
            sys.exit(sign_in())
    except SpectatorError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
