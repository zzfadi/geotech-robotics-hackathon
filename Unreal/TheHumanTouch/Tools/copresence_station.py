# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "pillow>=11", "httpx>=0.27", "psutil>=5.9"]
# ///
"""Lets the session dashboard start and stop agents and the spectator on this machine, such as
LUNA1 or a Mac with Unreal, for a session hosted anywhere, the always-on one included.

    uv run --script Tools/copresence_station.py start <host address or web address> <session code> [--name NAME] [--agents N] [--owner NAME]
    uv run --script Tools/copresence_station.py status
    uv run --script Tools/copresence_station.py brains
    uv run --script Tools/copresence_station.py stop [--with-agents]

`start` keeps it running in the background, logging to Saved/CoPresenceLogs/station.log. It
tells the session hub what this machine can run (agents thinking with claude or codex, where
those are installed, and the spectator, where Unreal is) and what it runs, and waits for what
the dashboard asks for. It runs that through the same steps people use: the join script brings
an agent, and Tools/copresence_spectator.py starts the spectator and shows it in a Meet call.

--agents is how many agents this machine runs at once (one unless it says more, and no limit
with 0). Each has an editor of its own: the first has the clone's AgentUser folder and MCP port
8000, as joining by hand gives it, and each further one a folder inside it (AgentUser/Agent2,
...) and a port of its own (8102, ...), which the station hands the join script.

Only what the dashboard offers runs, and the dashboard needs the session code for each of
these, so anyone with the code can start and stop agents here: run a station only on machines
whose owners are fine with that. With --owner, as the switch in someone's Agents tab starts it on
their own machine, only that person can start agents here until they share the machine with
everyone. `serve` is what `start` runs, in the foreground; `stop --with-agents` also closes the
agents it runs.

--server and --port point the spectator at another Multi-User server, such as a test one, with
--hub for its hub. Agents only join the session's usual server, the one the join scripts reach,
so a station pointed elsewhere offers no agents.
"""

import argparse
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import copresence_spectator  # noqa: E402
from copresence_process import brain_executable  # noqa: E402

PROJECT_DIR = TOOLS_DIR.parent
LOG_DIR = PROJECT_DIR / "Saved" / "CoPresenceLogs"
PID_FILE = LOG_DIR / "station.pid"
WINDOWS = sys.platform == "win32"
BRAINS = ("claude", "codex")
# As the hub checks them: names that are safe in the join scripts' patterns and config files
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}")
# Model names as Claude Code and Codex take them, such as claude-opus-5-5
AGENT_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# How hard an agent thinks, as Claude Code's --effort takes it
EFFORTS = ("low", "medium", "high", "xhigh", "max")
AGENT_FLAG = "-COPRESENCEAGENT"
# The first agent's editor keeps its Saved folder here and serves its tools on this port. Each
# agent after it gets a folder inside (AgentUser/Agent2, ...) and the port in this range that
# goes with it (8102, ...), or another free one there
AGENT_USER_DIR = PROJECT_DIR / "AgentUser"
AGENT_MCP_PORT = 8000
EXTRA_AGENT_PORTS = range(8100, 8200)
# The hub waits this long for something to do before answering; while a command runs, the
# station checks in more often, so its result reaches the dashboard soon
WAIT_SECONDS = 20
BUSY_WAIT_SECONDS = 4
REQUEST_TIMEOUT_SECONDS = WAIT_SECONDS + 15
RETRY_SECONDS = 5
JOIN_TIMEOUT_SECONDS = 180
# Whether Claude Code and Codex are signed in is asked again this often, since the report goes
# out every few seconds; asking takes a moment, and an answer that doesn't come counts as no
SIGN_IN_CHECK_SECONDS = 120.0
SIGN_IN_RECHECK_SECONDS = 15.0
SIGN_IN_TIMEOUT_SECONDS = 20
RELEASE_TIMEOUT_SECONDS = 20
STOP_WAIT_SECONDS = 20


def _log(text: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}", flush=True)


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def display_name(command_line: list[str]) -> str:
    """The name an editor joined the session with, from its command line."""
    for argument in command_line:
        if argument.upper().startswith("-CONCERTDISPLAYNAME="):
            return argument.split("=", 1)[1].strip('"')
    return ""


def agent_user_dir(command_line: list[str]) -> str:
    """The Saved folder an agent's editor was given with -userdir, or empty."""
    for argument in command_line:
        if argument.casefold().startswith("-userdir="):
            return argument.split("=", 1)[1].strip('"')
    return ""


def agent_port(command_line: list[str]) -> int:
    """The port an agent's editor serves its tools on, from its command line."""
    for argument in command_line:
        if "ServerPortNumber=" in argument:
            port = argument.rsplit("=", 1)[1].strip('"')
            return int(port) if port.isdigit() else AGENT_MCP_PORT
    return AGENT_MCP_PORT


def runner_port(command_line: list[str]) -> int:
    """The agent editor port a runner answers for: the --port it was given, or the first agent's."""
    if "--port" in command_line:
        index = command_line.index("--port")
        if index + 1 < len(command_line) and command_line[index + 1].isdigit():
            return int(command_line[index + 1])
    return AGENT_MCP_PORT


def slot_user_dir(slot: int) -> Path:
    """The Saved folder of the agent in this slot, counting from 1."""
    return AGENT_USER_DIR if slot == 1 else AGENT_USER_DIR / f"Agent{slot}"


def _folder_key(path: str) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def full_message(station: str, names: list[str], capacity: int) -> str:
    """Why a station can't start another agent: it has as many as it takes."""
    takes = "one agent" if capacity == 1 else f"{capacity} agents"
    return f"{station} already has {', '.join(names)}; it takes {takes} at a time"


def join_command(address: str, name: str, code: str, brain: str) -> list[str]:
    """Brings an agent the way people do, with this platform's join script."""
    raise RuntimeError("TheHumanTouch uses one shared editor per machine. Use its existing agent runner; station-created extra editors are disabled.")
    if WINDOWS:
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS_DIR / "join-windows.ps1"),
                "-HostAddress", address, "-DisplayName", name, "-SessionCode", code, "-Mode", "agent", "-Brain", brain]
    return ["/bin/bash", str(TOOLS_DIR / "join-mac.sh"), address, name, code, "agent", brain]


class Station:
    """This machine's part in the dashboard: what it can run, what it runs, and running what it's asked."""

    def __init__(self, name: str, address: str, code: str, hub_url: str = "",
                 server: str = copresence_spectator.DEFAULT_SERVER, port: int = copresence_spectator.MULTI_USER_PORT,
                 max_agents: int = 1, owner: str = ""):
        self.name = name
        # The person whose machine this is, who alone starts agents here until they share it; empty
        # for a machine everyone in the session shares, such as LUNA1
        self.owner = owner
        self.address = address
        self.code = code
        self.hub_override = hub_url
        self.hub_url = hub_url or self._hub_for(address)
        self.server = server
        self.port = port
        self.max_agents = max_agents
        # When Claude Code's and Codex's sign-in was last checked, and which were signed in
        self.sign_in_checked: tuple[float, dict[str, str]] = (float("-inf"), {})
        self.commands: queue.Queue = queue.Queue()
        self.results: list[dict] = []
        self.lock = threading.Lock()
        self.busy = False

    @staticmethod
    def _hub_for(address: str) -> str:
        # A web address is reached through the relay, which listens here
        if re.fullmatch(r"\d+(\.\d+){3}", address):
            return f"http://{address}:{copresence_spectator.HUB_PORT}"
        return f"http://127.0.0.1:{copresence_spectator.HUB_PORT}"

    # What runs here ----------------------------------------------------------------------------

    def _project_commands(self) -> list[tuple]:
        """Command lines of this clone's processes, with their process."""
        import psutil
        project = str(PROJECT_DIR).replace("\\", "/").casefold()
        found = []
        for process in psutil.process_iter(["cmdline"]):
            command_line = process.info["cmdline"] or []
            if project in " ".join(command_line).replace("\\", "/").casefold():
                found.append((process, command_line))
        return found

    def agents(self) -> list[dict]:
        """Agents whose editors run from this clone, with the Saved folder and port each has."""
        found = {}
        for _, command_line in self._project_commands():
            joined = " ".join(command_line)
            if "unrealeditor" in joined.casefold() and AGENT_FLAG in joined:
                name = display_name(command_line)
                if name and name not in found:
                    found[name] = {"name": name, "user_dir": agent_user_dir(command_line), "port": agent_port(command_line)}
        return list(found.values())

    def can(self) -> dict:
        uv = shutil.which("uv") is not None
        editor = copresence_spectator.engine_dir() / "Binaries" / ("Win64/UnrealEditor.exe" if WINDOWS else "Mac/UnrealEditor.app")
        # The join scripts only reach the session's usual server
        usual = (self.server, self.port) == (copresence_spectator.DEFAULT_SERVER, copresence_spectator.MULTI_USER_PORT)
        states = self.brain_states() if usual and uv else {brain: "" for brain in BRAINS}
        ready = [brain for brain, problem in states.items() if not problem] if usual and uv else []
        return {"brains": ready,
                # Why each other one can't think here, so the dashboard and editors can say so
                "unavailable": {brain: problem for brain, problem in states.items() if problem},
                "spectator": uv and editor.exists() and (PROJECT_DIR / "Plugins/CoPresenceCall/CoPresenceCall.uplugin").exists(),
                "max_agents": self.max_agents}

    def brain_states(self) -> dict[str, str]:
        """Whether Claude Code and Codex can think for an agent here: empty when ready, or why not.
        One that is only installed, or only works from a shell, would fail the moment the agent starts."""
        checked_at, states = self.sign_in_checked
        # While one isn't ready, someone may be signing it in right now, so it is asked about sooner
        check_every = SIGN_IN_RECHECK_SECONDS if any(states.values()) else SIGN_IN_CHECK_SECONDS
        if time.monotonic() - checked_at < check_every:
            return states
        states = {brain: self._brain_problem(brain) for brain in BRAINS}
        self.sign_in_checked = (time.monotonic(), states)
        return states

    def _brain_problem(self, brain: str) -> str:
        tool = brain_executable(brain)
        if not tool:
            return "not installed"
        command = [tool, "auth", "status"] if brain == "claude" else [tool, "login", "status"]
        try:
            result = self._run(command, SIGN_IN_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return "not answering"
        if brain == "claude":
            # Claude Code says it as JSON
            try:
                signed_in = bool(json.loads(result.stdout).get("loggedIn"))
            except (ValueError, AttributeError):
                signed_in = False
        else:
            signed_in = result.returncode == 0 and "not logged in" not in result.stdout.lower()
        return "" if signed_in else "not signed in"

    def spectator(self) -> dict:
        running = bool(copresence_spectator.editor_processes())
        name = ""
        if copresence_spectator.STATE_FILE.exists():
            name = json.loads(copresence_spectator.STATE_FILE.read_text()).get("name", "")
        return {"running": running, "name": name}

    def report(self) -> dict:
        return {"station": self.name, "code": self.code, "platform": "windows" if WINDOWS else "mac",
                "owner": self.owner, "can": self.can(), "agents": [{"name": agent["name"]} for agent in self.agents()],
                "spectator": self.spectator()}

    # Running what the dashboard asks for -------------------------------------------------------------

    def run(self, command: dict) -> tuple[bool, str]:
        action, args = command.get("action"), command.get("args") or {}
        _log(f"running {action} {json.dumps(args)}")
        try:
            if action == "agent.start":
                return self.start_agent(str(args.get("name") or ""), str(args.get("brain") or ""),
                                        str(args.get("model") or ""), str(args.get("effort") or ""))
            if action == "agent.stop":
                return self.stop_agent(str(args.get("name") or ""))
            if action == "spectator.start":
                return True, copresence_spectator.start(self.address, self.code, server=self.server, port=self.port,
                                                        hub_url=self.hub_override)
            if action == "spectator.stop":
                return True, copresence_spectator.stop()
            if action == "spectator.meet":
                return True, copresence_spectator.meet(str(args.get("link") or ""))
            if action == "spectator.leave":
                return True, copresence_spectator.leave()
            return False, f"{self.name} doesn't know how to {action}"
        except copresence_spectator.SpectatorError as error:
            return False, str(error)
        except Exception as error:
            _log(f"{action} failed: {error!r}")
            return False, f"{action} failed on {self.name}: {error}"

    def start_agent(self, name: str, brain: str, model: str = "", effort: str = "") -> tuple[bool, str]:
        if not AGENT_NAME.fullmatch(name) or brain not in BRAINS:
            return False, "That agent's name or brain isn't one the join scripts take"
        if model and not AGENT_MODEL.fullmatch(model):
            return False, "That model name isn't one the agent's runner takes"
        if effort and effort not in EFFORTS:
            return False, f"How hard it thinks is one of: {', '.join(EFFORTS)}"
        if brain not in self.can()["brains"]:
            return False, f"{self.name} has no {brain} to think with"
        running = self.agents()
        # A station started with --agents 0 takes any number
        if self.max_agents and len(running) >= self.max_agents:
            return False, full_message(self.name, [agent["name"] for agent in running], self.max_agents)
        slot = self._free_slot(running)
        if slot is None:
            return False, f"{self.name} has no free folder and port for another agent"
        user_dir, port = slot
        # The join scripts give the agent's editor this folder and port, and its runner the port
        # and its runner the model, which an empty one leaves to the account's default
        environment = {**os.environ, "COPRESENCE_AGENT_USER_DIR": str(user_dir), "COPRESENCE_MCP_PORT": str(port),
                       "COPRESENCE_AGENT_MODEL": model, "COPRESENCE_AGENT_EFFORT": effort}
        result = self._run(join_command(self.address, name, self.code, brain), JOIN_TIMEOUT_SECONDS, environment)
        if result.returncode != 0:
            return False, _last_line(result.stdout) or f"The join script failed on {self.name}"
        return True, f"Starting {name} on {self.name}; it answers in Agent Chat once its editor has joined"

    def _free_slot(self, running: list[dict]) -> tuple[Path, int] | None:
        """The first Saved folder no agent here has, with a port no agent has and nothing listens on."""
        taken_folders = {_folder_key(agent["user_dir"]) for agent in running}
        taken_ports = {agent["port"] for agent in running}
        # Without a limit, as many as this machine has ports for
        last_slot = self.max_agents or len(EXTRA_AGENT_PORTS) + 1
        for slot in range(1, last_slot + 1):
            user_dir = slot_user_dir(slot)
            if _folder_key(str(user_dir)) in taken_folders:
                continue
            if slot == 1:
                candidates = [AGENT_MCP_PORT]
            else:
                preferred = EXTRA_AGENT_PORTS.start + slot
                candidates = [preferred] + [port for port in EXTRA_AGENT_PORTS if port != preferred]
            port = next((port for port in candidates
                         if port not in taken_ports and not copresence_spectator._listening(port)), None)
            if port is not None:
                return user_dir, port
        return None

    def stop_agent(self, name: str) -> tuple[bool, str]:
        agent = next((agent for agent in self.agents() if agent["name"] == name), None)
        if agent is None:
            return False, f"{name} doesn't run on {self.name}"
        import copresence_mcp
        import psutil
        port = agent["port"]
        # Leaves its call first, so the call doesn't hang on in Meet
        pid = copresence_mcp.running_call(name)
        if pid is not None:
            if WINDOWS:
                copresence_mcp.call_leave_path(name).touch()
            else:
                os.kill(pid, signal.SIGTERM)
        # An avatar sequence left open crashes the editor on its way out (UE 5.8 Multi-User), and
        # its crash reporter then holds the MCP port, so it is let go of first
        if copresence_spectator._listening(port):
            self._run([shutil.which("uv") or "uv", "run", "--quiet", "--script", str(TOOLS_DIR / "copresence_mcp.py"),
                       "release-avatar"], RELEASE_TIMEOUT_SECONDS,
                      {**os.environ, "COPRESENCE_UNREAL_MCP_URL": f"http://127.0.0.1:{port}/mcp"})
        editors = [process for process, command_line in self._project_commands()
                   if AGENT_FLAG in " ".join(command_line) and display_name(command_line) == name]
        # Only this agent's runner: the one answering for its editor's port
        runners = [process for process, command_line in self._project_commands()
                   if "copresence_mcp.py" in " ".join(command_line) and "run-agent" in command_line
                   and runner_port(command_line) == port]
        self._stop_runner_service(port)
        for process in editors + runners:
            try:
                process.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(editors + runners, timeout=STOP_WAIT_SECONDS)
        for process in alive:
            process.kill()
        return True, f"Stopped {name} on {self.name}"

    def _stop_runner_service(self, port: int) -> None:
        """On a Mac the join script keeps the agent's runner alive with launchd, which would start
        it again; it is taken out of launchd first (Tools/copresence_runner_service.py)."""
        if WINDOWS:
            return
        import copresence_runner_service
        # The label depends on the clone and the agent's port, not on what the agent thinks with
        label = copresence_runner_service.configuration(PROJECT_DIR, BRAINS[0], port)["Label"]
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)

    def _run(self, command: list[str], timeout: float, environment: dict | None = None) -> subprocess.CompletedProcess:
        # The join script leaves the agent's editor and runner going, and on Windows they keep the
        # script's output handles. Read through pipes, the script would never seem to end, and
        # this station would wait on it for good, so its output goes to a file instead
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as output:
            result = subprocess.run(command, cwd=PROJECT_DIR, stdout=output, stderr=subprocess.STDOUT, timeout=timeout,
                                    stdin=subprocess.DEVNULL, env=environment)
            output.seek(0)
            return subprocess.CompletedProcess(result.args, result.returncode, output.read(), "")

    # Talking to the hub -----------------------------------------------------------------------------------

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(f"{self.hub_url}/api/stations", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)

    def _work(self) -> None:
        """Runs commands one after another, apart from checking in with the hub."""
        while True:
            command = self.commands.get()
            with self.lock:
                self.busy = True
            ok, message = self.run(command)
            _log(f"{command.get('action')}: {'done' if ok else 'failed'}: {message}")
            with self.lock:
                self.results.append({"id": command.get("id"), "ok": ok, "message": message})
                self.busy = self.commands.qsize() > 0

    def serve(self) -> None:
        threading.Thread(target=self._work, name="StationCommands", daemon=True).start()
        limit = f"up to {self.max_agents} agents at a time" if self.max_agents else "no limit on agents"
        _log(f"station {self.name} for {self.address}, hub {self.hub_url}, {limit}")
        problem = ""
        while True:
            with self.lock:
                results, self.results = self.results, []
                wait = BUSY_WAIT_SECONDS if self.busy else WAIT_SECONDS
            try:
                reply = self._post({**self.report(), "results": results, "wait": 0 if results else wait})
                if problem:
                    _log("reaching the hub again")
                    problem = ""
            except (urllib.error.URLError, OSError, ValueError) as error:
                with self.lock:
                    self.results = results + self.results
                if str(error) != problem:
                    problem = str(error)
                    _log(f"can't reach the hub at {self.hub_url}: {error}")
                time.sleep(RETRY_SECONDS)
                continue
            for command in reply.get("commands", []):
                with self.lock:
                    self.busy = True
                self.commands.put(command)


# Keeping it running ------------------------------------------------------------------------------------------

def running_pid() -> int | None:
    import psutil
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if psutil.pid_exists(pid) else None


def start_background(arguments: list[str]) -> str:
    if running_pid():
        return "The station already runs; stop it first to change it"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    detached = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if WINDOWS
                else {"start_new_session": True})
    with open(LOG_DIR / "station.log", "a", encoding="utf-8") as log:
        # Through uv, which stays alive as its parent, so its environment does too
        process = subprocess.Popen([shutil.which("uv") or "uv", "run", "--quiet", "--script", str(Path(__file__).resolve()),
                                    "serve", *arguments], cwd=PROJECT_DIR, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, **detached)
    PID_FILE.write_text(str(process.pid))
    return f"The station runs in the background; its log is {LOG_DIR / 'station.log'}"


def stop_background(with_agents: bool = False) -> str:
    import psutil
    pid = running_pid()
    PID_FILE.unlink(missing_ok=True)
    if with_agents:
        # Agents a station started can only be closed through a station, so they go first
        local = Station(socket.gethostname().split(".")[0], "127.0.0.1", "")
        for agent in local.agents():
            local.stop_agent(agent["name"])
    if pid is None:
        return "The station isn't running"
    parent = psutil.Process(pid)
    processes = parent.children(recursive=True) + [parent]
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(processes, timeout=10)
    return "Stopped the station"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "serve"):
        starting = commands.add_parser(command, help="Keep a station running" if command == "start"
                                       else "Run the station in the foreground")
        starting.add_argument("address", help="The host's address, or the always-on session's, like multiunreal.geotech-hub.com")
        starting.add_argument("code", help="The session code")
        starting.add_argument("--name", default=socket.gethostname().split(".")[0], help="This machine's name on the dashboard")
        starting.add_argument("--hub", default="", help="A hub other than the session's usual one, like http://127.0.0.1:8791")
        starting.add_argument("--server", default=copresence_spectator.DEFAULT_SERVER,
                              help="The Multi-User server the spectator joins, for a test session")
        starting.add_argument("--port", type=int, default=copresence_spectator.MULTI_USER_PORT,
                              help="That server's port")
        starting.add_argument("--agents", type=int, default=1,
                              help="How many agents this machine runs at once, each in an editor of its own; 0 for no limit")
        starting.add_argument("--owner", default="",
                              help="The person whose machine this is, who alone starts agents here until they share it")
    commands.add_parser("status", help="Say what this machine can run and runs")
    commands.add_parser("brains", help="Say whether Claude Code and Codex are signed in here, as JSON")
    stopping = commands.add_parser("stop", help="Stop the station")
    stopping.add_argument("--with-agents", action="store_true", help="Close the agents it runs too")
    arguments = parser.parse_args()

    if arguments.command == "status":
        station = Station(socket.gethostname().split(".")[0], "127.0.0.1", "")
        print(json.dumps({"running": bool(running_pid()), **{key: value for key, value in station.report().items()
                                                              if key not in ("code", "station")}}, indent=2))
    elif arguments.command == "brains":
        # What the Agents tab shows for this machine before a station runs here: empty when ready
        print(json.dumps(Station(socket.gethostname().split(".")[0], "127.0.0.1", "").brain_states()))
    elif arguments.command == "stop":
        print(stop_background(arguments.with_agents))
    else:
        code = arguments.code.strip().upper()
        if not copresence_spectator.NAME_PATTERN.fullmatch(arguments.name):
            sys.exit("The station's name has letters, digits, spaces, dots, dashes or underscores")
        if arguments.agents < 0:
            sys.exit("--agents is how many agents run at once, or 0 for no limit")
        if arguments.owner and not copresence_spectator.NAME_PATTERN.fullmatch(arguments.owner):
            sys.exit("The owner's name has letters, digits, spaces, dots, dashes or underscores")
        if arguments.command == "start":
            if not re.fullmatch(r"\d+(\.\d+){3}", arguments.address) and not arguments.hub:
                # Reaching the hub of the always-on session takes the relay
                try:
                    copresence_spectator.ensure_relay(arguments.address, code)
                except copresence_spectator.SpectatorError as error:
                    sys.exit(str(error))
            forwarded = [arguments.address, code, "--name", arguments.name, "--server", arguments.server,
                         "--port", str(arguments.port), "--agents", str(arguments.agents)] + \
                (["--hub", arguments.hub] if arguments.hub else []) + (["--owner", arguments.owner] if arguments.owner else [])
            print(start_background(forwarded))
        else:
            Station(arguments.name, arguments.address, code, arguments.hub.rstrip("/"), arguments.server,
                    arguments.port, arguments.agents, arguments.owner).serve()


if __name__ == "__main__":
    main()
