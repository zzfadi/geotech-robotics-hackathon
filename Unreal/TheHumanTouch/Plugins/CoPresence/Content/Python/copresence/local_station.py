"""This machine, for agents: "This Mac" (or "This PC") in the + Agent form's Machine dropdown. Adding
an agent there starts a station here (Tools/copresence_station.py) in the background, named after
the person, such as Lukas Mac, and the agent follows once it is up. Only the person can add agents
here, since they think with their Claude Code or Codex account, until they share it. The station
stops by itself once no agents are left here and it isn't shared, so nobody turns it on or off.

Whether Claude Code and Codex are signed in here comes from the station while it runs, and before
that from a quick check of this machine. Sign in opens the one-time sign-in in a terminal window,
the one place a terminal is still needed; for Claude Code, Install first runs its official
installer when it isn't on this machine yet.
"""

import json
import os
import platform
import re
import stat
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import unreal

from copresence import hub_client, voice_link

TOOLS_DIR = Path(__file__).resolve().parents[5] / "Tools"
PROJECT_DIR = TOOLS_DIR.parent
STATION_SCRIPT = TOOLS_DIR / "copresence_station.py"
# How long starting or stopping the station may take before the tab says it didn't work
SWITCH_SECONDS = 90.0
# A station with no agents left, not shared, stops after this long, so it doesn't run for nothing
IDLE_STOP_SECONDS = 60.0
# How often the sign-in check runs while someone looks at this machine in the form
BRAIN_CHECK_SECONDS = 15.0
BRAIN_CHECK_TIMEOUT_SECONDS = 60
# A share asked for here shows at once, and the hub's word on it follows within this long
SHARE_PENDING_SECONDS = 5.0
# Where Claude Code, Codex and uv usually live, for an editor that was started without the shell's PATH
TOOL_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin")
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}")
BRAIN_NAMES = {"claude": "Claude Code", "codex": "Codex"}

_state = {"asked": "", "asked_at": 0.0, "problem": "", "idle_since": None, "share_wanted": False,
          "share_asked": None, "brains": None, "brains_at": float("-inf"), "checking": False}


def machine_word() -> str:
    """What this machine is called in the tab: This Mac, This PC."""
    return {"Darwin": "Mac", "Windows": "PC"}.get(platform.system(), "machine")


def _person() -> str:
    """The person's name as a station takes it: letters, digits, spaces, dots, dashes, underscores."""
    name = hub_client.display_name()
    if NAME_PATTERN.fullmatch(name):
        return name
    return re.sub(r"[^A-Za-z0-9 ._-]", "", name).strip() or "Someone"


def station_name() -> str:
    """This machine's station, named after the person: Lukas Mac, Josh PC."""
    return f"{_person()} {machine_word()}"[:60]


def own_station(board: dict | None) -> dict | None:
    """This machine's station as the hub lists it, while it runs."""
    station = next((station for station in (board or {}).get("stations", []) if station["name"] == station_name()), None)
    if board is not None:
        _state["running_seen"] = station is not None
    return station


def may_be_running() -> bool:
    """Whether this machine's station may be running, so the hub's word on it is still wanted."""
    return bool(_state.get("running_seen")) or bool(_state["asked"])


def state(board: dict | None) -> str:
    """"on" or "off" as the hub has it, or "starting" or "stopping" from the moment either was
    asked for until the hub shows it."""
    running = own_station(board) is not None
    asked = _state["asked"]
    if not asked:
        return "on" if running else "off"
    if (asked == "on") == running:
        _state["asked"] = ""
        return asked
    if time.monotonic() - _state["asked_at"] < SWITCH_SECONDS:
        return "starting" if asked == "on" else "stopping"
    # The hub never showed it, so the station's own words say why
    _state["asked"] = ""
    if asked == "on":
        _state["problem"] = _last_log_line() or f"This {machine_word()} couldn't start agents"
    return "on" if running else "off"


def problem() -> str:
    return _state["problem"]


def turn_on(board: dict | None) -> None:
    """Starts a station here for the session, which only this person can add agents to for now."""
    uv = voice_link.find_uv()
    if not uv:
        _state["problem"] = "Agents on this machine need uv"
        return
    session = (board or {}).get("session") or {}
    # The session's web address, so the station keeps its way there open, or else the hub's own address
    address = session.get("address") or urlparse(hub_client.hub_url()).hostname or "127.0.0.1"
    _state.update(asked="on", asked_at=time.monotonic(), problem="", idle_since=None)
    _spawn([uv, "run", "--quiet", "--script", str(STATION_SCRIPT), "start", address, hub_client.session_code(),
            "--name", station_name(), "--agents", "0", "--owner", _person()])


def turn_off() -> None:
    """Stops this machine's station, and any agents it runs with it."""
    uv = voice_link.find_uv()
    if not uv:
        return
    _state.update(asked="off", asked_at=time.monotonic(), problem="", idle_since=None)
    _spawn([uv, "run", "--quiet", "--script", str(STATION_SCRIPT), "stop", "--with-agents"])


def is_shared(board: dict | None) -> bool:
    """Whether everyone in the session can add agents here, showing a change asked for at once."""
    asked = _state["share_asked"]
    if asked is not None and time.monotonic() - asked[1] < SHARE_PENDING_SECONDS:
        return asked[0]
    station = own_station(board)
    return bool(station and station.get("public")) or _state["share_wanted"]


def set_shared(board: dict | None, shared: bool) -> None:
    """Shares this machine with everyone in the session, starting its station when it isn't running,
    or keeps it to the person again."""
    _state["share_asked"] = (shared, time.monotonic())
    station = own_station(board)
    if station is not None:
        _state["share_wanted"] = False
        hub_client.control("station.audience", station=station["name"], public=shared)
        return
    _state["share_wanted"] = shared
    if shared and state(board) == "off":
        turn_on(board)


def tick(board: dict | None, busy: bool) -> None:
    """Shares the station once it is up when that was asked for, and stops it once it has had no
    agents for a while, isn't shared and nothing is on its way here. A station someone started by
    hand, without an owner, is left alone."""
    station = own_station(board)
    if station is not None and _state["share_wanted"]:
        _state["share_wanted"] = False
        hub_client.control("station.audience", station=station["name"], public=True)
    idle = (station is not None and station.get("owner") == _person() and state(board) == "on" and not busy
            and not station.get("agents") and not is_shared(board))
    if not idle:
        _state["idle_since"] = None
        return
    now = time.monotonic()
    if _state["idle_since"] is None:
        _state["idle_since"] = now
    elif now - _state["idle_since"] >= IDLE_STOP_SECONDS:
        turn_off()


def brain_problems(board: dict | None) -> dict | None:
    """Why Claude Code or Codex can't think on this machine, by brain, empty when it can: as its
    station says while one runs, and before that as a quick check here says. None until known."""
    station = own_station(board)
    if station is not None:
        can = station.get("can") or {}
        return {**{brain: "" for brain in can.get("brains") or []}, **(can.get("unavailable") or {})}
    _check_brains()
    return _state["brains"]


def _check_brains() -> None:
    """Asks in the background, now and then, whether Claude Code and Codex are signed in here."""
    if _state["checking"] or time.monotonic() - _state["brains_at"] < BRAIN_CHECK_SECONDS:
        return
    uv = voice_link.find_uv()
    if not uv:
        return
    _state["checking"] = True
    threading.Thread(target=_run_brain_check, args=(uv,), name="CoPresenceBrainCheck", daemon=True).start()


def _run_brain_check(uv: str) -> None:
    try:
        result = subprocess.run([uv, "run", "--quiet", "--script", str(STATION_SCRIPT), "brains"], cwd=PROJECT_DIR,
                                env=_environment(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=BRAIN_CHECK_TIMEOUT_SECONDS)
        _state["brains"] = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        # Unknown until the next check; the form then lists every model unmarked
        pass
    finally:
        _state["brains_at"] = time.monotonic()
        _state["checking"] = False


def sign_in(brain: str, install: bool = False) -> None:
    """Opens a terminal window that signs Claude Code or Codex in on this machine, installing Claude
    Code first when asked. The tab notices within a few seconds."""
    login = "claude auth login" if brain == "claude" else "codex login"
    # Asked again soon, so the form shows it signed in once it is
    _state["brains_at"] = float("-inf")
    try:
        if platform.system() == "Windows":
            steps = ["irm https://claude.ai/install.ps1 | iex"] if install else []
            # A fresh install isn't on this window's PATH yet
            claude = '& "$env:USERPROFILE\\.local\\bin\\claude.exe"' if install else "claude"
            steps.append(f"{claude} auth login" if brain == "claude" else login)
            subprocess.Popen(["powershell.exe", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                              "; ".join(steps)], creationflags=subprocess.CREATE_NEW_CONSOLE)
            return
        script = Path(unreal.Paths.project_saved_dir()) / "CoPresence" / f"sign-in-{brain}.command"
        script.parent.mkdir(parents=True, exist_ok=True)
        lines = ["#!/bin/zsh -l", 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"']
        if install:
            lines.append("curl -fsSL https://claude.ai/install.sh | bash")
        lines += [login, "echo", 'read -k 1 "?Done. Press any key to close this window."']
        script.write_text("\n".join(lines) + "\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        # Terminal opens .command files and runs them
        subprocess.Popen(["open", str(script)])
    except OSError as error:
        _state["problem"] = f"Couldn't open the sign-in for {BRAIN_NAMES.get(brain, brain)}: {error}"
        unreal.log_warning(f"CoPresence Agents widget: {_state['problem']}")


def _environment() -> dict:
    """The editor's environment, with the folders Claude Code, Codex and uv usually live in, since an
    editor started from the Dock or Explorer doesn't get the shell's PATH."""
    folders = [str(Path(folder).expanduser()) for folder in TOOL_DIRS]
    return {**os.environ, "PATH": os.pathsep.join(folders + [os.environ.get("PATH", "")])}


def _spawn(command: list[str]) -> None:
    """Runs a station command on its own, so the editor never waits for it."""
    logs = Path(unreal.Paths.project_saved_dir()) / "CoPresenceLogs"
    logs.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        detached = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    else:
        detached = {"start_new_session": True}
    try:
        with open(logs / "station-switch.log", "w", encoding="utf-8") as log:
            subprocess.Popen(command, cwd=PROJECT_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             env=_environment(), **detached)
    except OSError as error:
        _state.update(asked="", problem=f"Couldn't start agents here: {error}")
        unreal.log_warning(f"CoPresence Agents widget: {_state['problem']}")


def _last_log_line() -> str:
    """What the station command said last, such as why it couldn't start."""
    log = Path(unreal.Paths.project_saved_dir()) / "CoPresenceLogs" / "station-switch.log"
    try:
        lines = [line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[-1][:200] if lines else ""
