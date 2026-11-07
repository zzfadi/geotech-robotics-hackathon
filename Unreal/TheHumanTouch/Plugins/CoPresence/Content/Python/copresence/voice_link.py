"""The editor's side of voice: tells the voice helper on this machine
(Tools/copresence_voice.py) which agent this person is in a call with and which public agents they
muted, and reads back what the voice is doing for the Agents tab. The agent's voice itself runs on
the agent's machine; the helper carries the person's microphone to it through the hub for as long
as the call lasts, and plays what public agents say to everyone else too, so the editor starts the
helper whenever it isn't running.

Both sides meet in two small files in Saved/CoPresence, so the editor needs no audio,
no OpenAI key and no network connection of its own for voice:

- voice_link.json, written here: who is talking (this editor's Multi-User name), the agent
  they are in a call with and whether they muted their microphone in it, the agents they muted
  (not heard here), the hub address and session code, and the current selection.
- voice_status.json, written by the helper: whether the call is ringing, listening, speaking
  or waiting for the agent, and with whom, or what went wrong or ended it, and what you and the
  agent last said, for the call's live captions.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import unreal

from copresence import agents_panel, hub_client

TOOLS_DIR = Path(__file__).resolve().parents[5] / "Tools"
# The helper treats a link older than this as an editor that has quit
LINK_REFRESH_SECONDS = 2.0
SELECTION_SECONDS = 1.0
# A status older than this means the helper is not running
STATUS_STALE_SECONDS = 5.0
# A helper this editor started gets this long to say it runs before another one is started
HELPER_START_SECONDS = 30.0
READ_TRIES = 3
READ_RETRY_SECONDS = 0.01
STATUS_TEXT = {
    "ringing": "ringing",
    "connecting": "ringing",
    "listening": "in a call, listening",
    "speaking": "in a call, speaking",
    "waiting": "working on what you said",
    "error": "voice problem",
}
# A status written this soon after a call began can still be about the call before it
CALL_SETTLE_SECONDS = 0.5
# What you and the agent say stays in the captions this long after it last changed, as in VR, and
# the agent's for as long as it speaks; a long line shows its end
CAPTION_HOLD_SECONDS = 6.0
CAPTION_CHARACTERS = 160

_state = {"written": None, "written_at": 0.0, "selection": [], "selection_at": 0.0,
          # The agents in the call, the first called first, and when each joined it
          "call": [], "joined": {},
          "helper_started_at": None, "helper_problem": "",
          # For the captions: what you said and when it changed, and what each agent said and when
          "heard": "", "heard_at": 0.0, "said": {}, "said_at": {}}


def _folder() -> Path:
    return Path(unreal.Paths.project_saved_dir()) / "CoPresence"


def _write(path: Path, payload: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(payload))
    try:
        partial.replace(path)
    except PermissionError:
        # Windows refuses while the helper reads the file; the next tick writes it again
        return False
    return True


def _read(path: Path) -> dict | None:
    """A file the helper writes, or None when there is none. Windows refuses a read while the
    file is being replaced, so a refused read is tried again a moment later."""
    for _ in range(READ_TRIES):
        try:
            return json.loads(path.read_text())
        except PermissionError:
            time.sleep(READ_RETRY_SECONDS)
        except (OSError, ValueError):
            return None
    return None


def update(call: list, muted_agents: list, mic_muted: bool = False) -> None:
    """Called whenever the agents this person is in a call with, the agents they muted, or whether
    they muted their microphone in the call change, and a few times a second anyway."""
    now = time.monotonic()
    _state["joined"] = {agent: _state["joined"].get(agent, time.time()) for agent in call}
    _state["call"] = list(call)
    calling = call[0] if call else ""
    if calling and now - _state["selection_at"] >= SELECTION_SECONDS:
        _state["selection_at"] = now
        _state["selection"] = agents_panel.selection()
    link = {
        "person": hub_client.display_name(),
        "following": calling,
        "call": bool(calling),
        # Everyone in the call, where each agent speaks only while you talk to it by name
        "call_agents": list(call),
        # What helpers from before calls understand: a conversation that stays open
        "open": bool(calling),
        "hold": False,
        "talking": False,
        "muted_agents": list(muted_agents),
        # The call goes on with the microphone muted; the agent hears quiet
        "mic_muted": bool(calling) and mic_muted,
        "hub": hub_client.hub_url(),
        "code": hub_client.session_code(),
        "selection": _state["selection"] if calling else [],
        "muted": False,
    }
    # The time is rewritten now and then, so the helper can tell a live editor from a dead one
    if link == _state["written"] and now - _state["written_at"] < LINK_REFRESH_SECONDS:
        return
    if _write(_folder() / "voice_link.json", {**link, "time": time.time()}):
        _state["written"], _state["written_at"] = link, now


def ensure_helper() -> None:
    """Starts the voice helper next to this editor when it isn't running. It opens the microphone
    only during a call, so it can wait in the background all the time."""
    started = _state["helper_started_at"]
    if is_running() or (started is not None and time.monotonic() - started < HELPER_START_SECONDS):
        return
    _state["helper_started_at"] = time.monotonic()
    uv = find_uv()
    if not uv:
        _state["helper_problem"] = "voice needs uv on this machine"
        return
    _state["helper_problem"] = ""
    logs = Path(unreal.Paths.project_saved_dir()) / "CoPresenceLogs"
    logs.mkdir(parents=True, exist_ok=True)
    # On its own, so it outlives a crashed editor and the next editor finds it running
    if os.name == "nt":
        detached = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    else:
        detached = {"start_new_session": True}
    with open(logs / "voice.log", "ab") as log:
        subprocess.Popen([uv, "run", "--quiet", "--script", str(TOOLS_DIR / "copresence_voice.py")],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         env={**os.environ, "COPRESENCE_VOICE_DIR": str(_folder())}, **detached)


def find_uv() -> str | None:
    """uv, also where its installers put it, since an editor started from the Dock or Explorer
    doesn't get the shell's PATH."""
    found = os.environ.get("UV") or shutil.which("uv")
    if found:
        return found
    home = Path.home()
    for candidate in (home / ".local" / "bin" / "uv", home / ".local" / "bin" / "uv.exe", home / ".cargo" / "bin" / "uv",
                      Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv"),
                      Path("F:/GeoVerse/AgentTools/python/bin/uv.exe")):
        if candidate.exists():
            return str(candidate)
    return None


def helper_problem() -> str:
    """Why the voice helper couldn't be started, or empty."""
    return "" if is_running() else _state["helper_problem"]


def _status() -> dict | None:
    """What the helper last said the voice was doing, or None if it isn't running."""
    status = _read(_folder() / "voice_status.json")
    if status is None or time.time() - status.get("time", 0.0) > STATUS_STALE_SECONDS:
        return None
    return status


def is_running() -> bool:
    """Whether the voice helper is running next to this editor."""
    return _status() is not None


def _call_status(agent: str) -> dict | None:
    """What the helper says about this person's call with the agent, once it is about this call:
    with several agents in the call, each has its own."""
    status = _status()
    if status is None or agent not in _state["call"]:
        return None
    calls = status.get("calls")
    entry = calls.get(agent) if isinstance(calls, dict) else (status if status.get("agent") == agent else None)
    if not entry or status.get("time", 0.0) < _state["joined"].get(agent, 0.0) + CALL_SETTLE_SECONDS:
        return None
    return entry


def call_ended(agent: str) -> str:
    """Why the helper ended the call with the agent on its own, such as after a long quiet, or empty
    while the call goes on."""
    status = _call_status(agent)
    if not status or status.get("state") != "off":
        return ""
    return str(status.get("problem") or "")


def status_for(agent: str) -> str:
    """A few words for the Agents tab about the call with this agent, or empty."""
    status = _call_status(agent)
    if status is None:
        return ""
    state = status.get("state", "")
    if state == "error" and status.get("problem"):
        # Shown in the agent's own row, so "Sketch Claude has no voice running" reads "has no voice running"
        problem = status["problem"]
        return problem[len(agent) + 1:] if problem.startswith(f"{agent} ") else f"voice: {problem}"
    if state in ("listening", "speaking", "waiting") and status.get("problem"):
        # The call goes on, but something on this side keeps it from working, such as a silent microphone
        return f"in a call, but {status['problem']}"
    return STATUS_TEXT.get(state, "")


def captions(call: list) -> tuple[str, str, str]:
    """For live captions: which agent's words show, what you say in the call, as the agents' voices
    hear it, and what that agent says back. The agent is the one speaking, or the one that spoke
    last; either text is empty while there is nothing to show."""
    entries = {agent: _call_status(agent) or {} for agent in call}
    # Every agent in the call hears you on a line of its own, so any of them says what you said
    heard = next((str(entry["heard"]) for entry in entries.values() if entry.get("heard")), "")
    now = time.monotonic()
    if heard != _state["heard"]:
        _state["heard"], _state["heard_at"] = heard, now
    for agent, entry in entries.items():
        said = str(entry.get("said") or "")
        if said != _state["said"].get(agent, ""):
            _state["said"][agent], _state["said_at"][agent] = said, now
    speaking = next((agent for agent, entry in entries.items() if entry.get("state") == "speaking"), "")
    agent = speaking or max(call, key=lambda name: _state["said_at"].get(name, 0.0), default="")
    said = _state["said"].get(agent, "") if agent else ""
    shown_said = said if agent and (agent == speaking or now - _state["said_at"].get(agent, 0.0) < CAPTION_HOLD_SECONDS) else ""
    shown_heard = heard if now - _state["heard_at"] < CAPTION_HOLD_SECONDS else ""
    return agent, _tail(shown_heard), _tail(shown_said)


def _tail(text: str) -> str:
    """The end of a long line, from a word on, so the captions keep up with what is being said."""
    text = " ".join(text.split())
    if len(text) <= CAPTION_CHARACTERS:
        return text
    end = text[-CAPTION_CHARACTERS:]
    return "..." + end[end.find(" ") + 1:] if " " in end else "..." + end
