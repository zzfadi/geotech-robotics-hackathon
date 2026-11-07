# /// script
# requires-python = ">=3.11"
# dependencies = ["openai[realtime]==3.31.0", "httpx>=0.27"]
# ///
"""An agent's voice: people in the session talk with the agent out loud, from their editor or
from VR, and it answers in the agent's own voice through OpenAI GPT-Live (gpt-live-1, about
$0.05 a minute while a conversation is open).

It runs on the agent's machine with the agent's runner (Tools/copresence_mcp.py run-agent),
which starts it and starts it again when it stops, so the agent and its voice come as one, and
only machines that run agents need the OpenAI key. People's microphones reach it through the
session hub (Tools/copresence_voice_lines.py), from the voice helper next to their editor
(Tools/copresence_voice.py), and what it says goes back the same way. Everyone talking with the
agent has a conversation of their own, so the agent always knows who is speaking.

In the editor, people call the agent (Call in the Agents tab, at the desk and in VR) and the
conversation stays open until they hang up or the call goes quiet for a while, which their voice
helper decides. The helper removes the agent's own voice and background noise from their
microphone before it comes here, since GPT-Live has no settings for that, and the instructions
below tell it not to stop for noise or for people talking to each other. Helpers from before
calls hold a button while the person talks, and then a conversation closes half a minute after
the last words either side said, so GPT-Live is only paid for while people talk.

GPT-Live only does the talking. When someone asks for work in the level, it hands the request
to the agent through the session chat, as a message from that person, and the agent's runner
does it. What the agent says back in the chat, GPT-Live says to them; what the agent is doing
right now, GPT-Live knows, so "what are you doing?" needs no round trip. When someone tells it
to stop or cancel what it is doing, it does: the decision model (OpenAI Decisions) judges each
thing they say, so "stop the elevator" is a request, not a stop. The agent has the same voice
here as in video calls (copresence_voice_core.py).

    uv run --script Tools/copresence_agent_voice.py serve "Agent Name"
                                            answer people talking with the agent
    uv run --script Tools/copresence_agent_voice.py key "op://Vault/Item/credential" [account]
                                            use an OpenAI key from 1Password
    uv run --script Tools/copresence_agent_voice.py key -
                                            keep the key itself, read from standard input, on a
                                            machine without 1Password
    uv run --script Tools/copresence_agent_voice.py check
                                            check that the key can start conversations

The OpenAI key comes from 1Password through its command line tool (`op`), read when this starts
and kept in memory only. `key` saves the item's secret reference (not the key) in
Saved/CoPresence/voice.json, which git ignores; COPRESENCE_OPENAI_KEY_REF overrides it. Without
a reference, OPENAI_API_KEY is used, and then a key kept with `key -` in ~/.copresence/openai_key,
which only this user can read. The voice in video calls (Tools/copresence_call_voice.py) uses the
same key. serve finds the hub and the session code the way the runner does, or through
COPRESENCE_HUB_URL and COPRESENCE_CODE, which the runner sets.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Standalone Python on macOS can lack a default CA file. Keep certificate
# verification enabled and use the system trust bundle unless explicitly set.
if sys.platform == "darwin" and Path("/etc/ssl/cert.pem").is_file():
    os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")

import httpx

import copresence_voice_core as core
import copresence_voice_lines as lines
from copresence_process import process_running
from copresence_voice_core import promised_work

PROJECT_DIR = Path(__file__).resolve().parent.parent
# Tests point this somewhere else, so they leave the real settings alone
VOICE_DIR = Path(os.environ.get("COPRESENCE_VOICE_DIR") or PROJECT_DIR / "Saved" / "CoPresence")
SETTINGS_FILE = VOICE_DIR / "voice.json"
KEY_FILE = Path(os.environ.get("COPRESENCE_OPENAI_KEY_FILE") or Path.home() / ".copresence" / "openai_key")
HOST_ADDRESS_FILE = PROJECT_DIR / "Saved" / "CoPresenceLogs" / "host_address"
CODE_FILE = PROJECT_DIR / "Saved" / "CoPresenceLogs" / "hub_code"
HUB_PORT = 8790
CODE_HEADER = "X-CoPresence-Code"

MODEL = "gpt-live-1"
SAMPLE_RATE = 24000
# Voices GPT-Live has; an agent that hasn't picked one gets a voice calls have too
VOICES = core.LIVE_VOICES
# After the person stops following, or lets go of the talk button, the conversation stays open
# this long, so moving the camera to look at something doesn't cut them off and a follow-up
# doesn't wait for GPT-Live to start again; longer while the agent works on their request
LINGER_SECONDS = 30.0
WAITING_SECONDS = 180.0
# Account problems a retry can't fix: the person's helper doesn't try again until they follow
# the agent again
ACCOUNT_ERRORS = ("credit_balance_exhausted", "insufficient_quota", "invalid_api_key", "model_not_found")
# Transcript fragments can arrive just after the delegation they belong to
TRANSCRIPT_SETTLE_SECONDS = 0.8
# Without a fresh request in the transcript, the request is the last thing said in this
# window: what came after the last pause this long
FALLBACK_REQUEST_MS = 15000
UTTERANCE_PAUSE_MS = 1500
# Each append to GPT-Live is limited to 500 tokens
APPEND_CHARACTERS = 1500
HISTORY_MESSAGES = 12
STATUS_THINKING_SECONDS = 3.0
# GPT-Live streams audio all the time, silence included, so the voice counts as speaking
# for this long after its last transcribed words
SPEAKING_SECONDS = 1.2
# GPT-Live sometimes says it will do something without delegating it. When the voice
# promises work and no delegation follows this soon, the request goes to the agent anyway
PROMISE_SECONDS = 2.5
# What the voice says just after a delegation ("sure, on it") belongs to that delegation
PROMISE_GRACE_SECONDS = 5.0
# What was said is judged once it has been quiet this long, so "stop the elevator" is
# judged whole, not as "stop"
STOP_SETTLE_SECONDS = 0.7
# Lines of the conversation the decision model gets with what was said
DECISION_LINES = 8
# A conversation with the same person that ended this recently doesn't greet them again
REGREET_SECONDS = 600.0
# In a call with several agents: what the person says after this long a pause is a new
# utterance, whose names say which agent they talk to now
UTTERANCE_GAP_SECONDS = 1.0
MAX_CALL_AGENTS = 8
# What a person says to talk to every agent in the call at once
EVERYONE_WORDS = re.compile(r"\b(everyone|everybody|all of you|you all|y'?all|both of you|you both|you two|you three|"
                            r"you four|you guys|all agents)\b", re.IGNORECASE)
# A voice that has news for the person, such as a result, is heard this long even while the
# person talks to another agent, or until it has told the news and been quiet this long
REPORT_SECONDS = 20.0
REPORT_TAIL_SECONDS = 2.0

# The hub: a line whose microphone hasn't connected this long after it opened is given up
LINK_WAIT_SECONDS = 15.0
# Connecting again after the hub drops: soon at first, then less often while it stays away
RECONNECT_SECONDS = (3.0, 30.0)
# While another voice for this agent is connected (the agent runs twice), this one waits
REFUSED_SECONDS = 30.0
# Frames waiting to go to the hub; past this, audio is dropped rather than sent late
OUTGOING_FRAMES = 400
AUDIO_FRAME_BYTES = 16 * 1024
# Talking over the voice says so to the person's helper at most this often
FLUSH_SECONDS = 0.3
# A status that hasn't changed is sent again this often, so the person's editor knows the voice is there
STATUS_REPEAT_SECONDS = 1.0
PARENT_CHECK_SECONDS = 2.0

INSTRUCTIONS = """\
You are the voice of {agent}, an AI agent working with people in a shared Unreal Engine \
level. {person} is talking to you, from the editor or from VR. Speak as {agent}, in the \
first person, briefly and casually, like a teammate on a call.

You can't see or change the level yourself. Your eyes and hands are a separate worker, \
and the only way to reach it is to delegate. So whenever {person} asks you to do, change, \
add, move, remove, find, check or look at anything in the level, or asks what is there, \
delegate it straight away. Agreeing without delegating does nothing: nobody hears it but \
{person}, and no work happens. When you delegate, say a few words like "on it", then stop. \
Results arrive as commentary: tell {person} in your own words, once. Never say work is \
done until a result says so. Work usually takes 10 to 60 seconds; if asked, say you're \
still on it.

Status notes about what you are doing arrive as thinking. They are background only: never \
announce them, and only use them to answer questions about your current work. The newest \
note is the current one. Don't delegate small talk.

Speech recognition can mishear asset and actor names. If a request is unclear, ask a short \
question before you delegate.

{person} called you and the call stays open while you talk, from a room or a headset where \
other people may be talking too. Interruption policy: stop speaking when {person} interrupts \
you, and listen to what they say. Do not treat a cough, a click, typing, music, background \
noise or a nearby conversation as a new request, and don't stop for it. Keep listening while \
{person} pauses to think. Respond when {person} asks you something or addresses you directly; \
when they are clearly talking to someone else, keep listening and say nothing.

Other agents can be in this call too; a note says who. While they are, {person} picks whom they \
talk to by name, or talks to all of you at once ("everyone", "you three"). Speak and delegate \
only when {person} says your name or talks to all of you, or keeps talking with you after that; \
when they name another agent, say nothing until they name you again. When they ask several of \
you for something at once, delegate it as usual: your workers agree among themselves who does \
which part, so nothing is done twice."""

GREETING = "Greet {person} in a few words, as {agent}, then listen."

# When each person last talked with the agent, so a new conversation doesn't greet them again
_last_talked: dict[str, float] = {}

REQUEST_NOTE = "(Said aloud to you by voice; the transcript can contain mistakes.)"


def _log(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {text}", flush=True)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(payload))
    partial.replace(path)


def _settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


# The OpenAI key ---------------------------------------------------------------------

def openai_key() -> tuple[str, str]:
    """The OpenAI key and where it came from. The key itself is never printed or saved."""
    settings = _settings()
    reference = os.environ.get("COPRESENCE_OPENAI_KEY_REF") or settings.get("openai_key_ref", "")
    if reference:
        configured_op = os.environ.get("COPRESENCE_OP_COMMAND") or settings.get("op_command", "")
        op = configured_op or shutil.which("op")
        if not op:
            sys.exit("The OpenAI key is in 1Password, but its command line tool (op) isn't installed. "
                     "Install it (brew install 1password-cli), turn on 'Integrate with 1Password CLI' "
                     "in the 1Password app's Developer settings, and try again.")
        command = [op, "read", "--no-newline", reference]
        account = os.environ.get("COPRESENCE_OP_ACCOUNT") or settings.get("op_account", "")
        if account and not configured_op:
            command += ["--account", account]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            sys.exit("1Password didn't answer in time. Unlock it and try again.")
        if run.returncode or not run.stdout.strip():
            problem = (run.stderr.strip().splitlines() or ["no value"])[-1]
            sys.exit(f"1Password couldn't read {reference}: {problem}")
        return run.stdout.strip(), f"1Password ({reference})"
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"], "OPENAI_API_KEY"
    try:
        kept = KEY_FILE.read_text().strip()
    except OSError:
        kept = ""
    if kept:
        return kept, str(KEY_FILE)
    sys.exit("No OpenAI key for the agent's voice. Save where it is in 1Password with:\n"
             '  uv run --script Tools/copresence_agent_voice.py key "op://<vault>/<item>/<field>"\n'
             "(in 1Password, open the item, click the field's menu and choose Copy Secret Reference),\n"
             "or, on a machine without 1Password, pipe the key itself into:\n"
             "  uv run --script Tools/copresence_agent_voice.py key -")


def save_key_reference(reference: str, account: str) -> None:
    if not reference.startswith("op://"):
        sys.exit('A 1Password secret reference starts with op://, for example "op://Private/OpenAI/credential".')
    settings = _settings()
    settings["openai_key_ref"] = reference
    if account:
        settings["op_account"] = account
    else:
        settings.pop("op_account", None)
    _write_json(SETTINGS_FILE, settings)
    print(f"Saved. The voice reads the OpenAI key from {reference} when it starts.")


def save_key(key: str) -> None:
    """Keeps the key itself where only this user can read it, for machines without 1Password."""
    key = key.strip()
    if not key.startswith("sk-") or any(character.isspace() for character in key):
        sys.exit("That doesn't look like an OpenAI key (they start with sk-). Nothing was saved.")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        KEY_FILE.parent.chmod(0o700)
    # Created readable by this user only, before the key goes in
    descriptor = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(key)
    if os.name != "nt":
        KEY_FILE.chmod(0o600)
    print(f"Saved the key in {KEY_FILE}. The agent's voice reads it when it starts.")


# Pure helpers (tested in Tools/tests/test_voice.py) ----------------------------------

def voice_for(agent: str, appearance: dict | None) -> str:
    """The agent's chosen voice if GPT-Live has it, or the voice it has in calls too."""
    return core.voice_for(agent, appearance, core.LIVE_VOICES)


def spoken_between(fragments: list[dict], start_ms: int, end_ms: int) -> str:
    """The text of transcript fragments that start in [start_ms, end_ms), in order."""
    chosen = sorted((fragment for fragment in fragments if start_ms <= fragment["start_ms"] < end_ms),
                    key=lambda fragment: fragment["start_ms"])
    return "".join(fragment["text"] for fragment in chosen).strip()


def last_utterance(fragments: list[dict], start_ms: int, end_ms: int) -> str:
    """What was said in [start_ms, end_ms) after the last pause of UTTERANCE_PAUSE_MS."""
    chosen = sorted((fragment for fragment in fragments if start_ms <= fragment["start_ms"] < end_ms),
                    key=lambda fragment: fragment["start_ms"])
    first = 0
    for index in range(1, len(chosen)):
        if chosen[index]["start_ms"] - chosen[index - 1]["end_ms"] > UTTERANCE_PAUSE_MS:
            first = index
    return "".join(fragment["text"] for fragment in chosen[first:]).strip()


def request_text(heard: list[dict], said: list[dict], since_ms: int, offset_ms: int) -> str:
    """What to send the agent for a delegation at offset_ms: what the person said since the
    last delegation, and what the voice said just before, so "yes, do that" makes sense."""
    request = spoken_between(heard, since_ms, offset_ms + 1)
    if not request:
        request = last_utterance(heard, max(0, offset_ms - FALLBACK_REQUEST_MS), offset_ms + 1)
    if not request:
        return ""
    before = spoken_between(said, since_ms, offset_ms + 1)
    context = f' Just before, your voice said: "{before[-400:]}"' if before else ""
    return f"{request}\n{REQUEST_NOTE}{context}"


def _name_words(text: str) -> list[str]:
    """Words as a name is matched: lower case, with c and k alike, since speech recognition
    writes Lukas as Lucas as often as not."""
    return [word.replace("c", "k") for word in re.findall(r"[a-z0-9]+", text.lower())]


def spoken_names(agents: list[str]) -> dict[str, list[str]]:
    """What each agent in a call is called by: the words of its name no other agent's name has
    ("lukas" for Lukas Claude next to Josh Claude), or its whole name when it has none of its own."""
    words = {agent: _name_words(agent) for agent in agents}
    names = {}
    for agent, own in words.items():
        others = {word for name, theirs in words.items() if name != agent for word in theirs}
        names[agent] = [word for word in own if word not in others] or [" ".join(own)]
    return names


def named_agents(text: str, names: dict[str, list[str]]) -> set[str]:
    """The agents something the person said names, or all of them when it speaks to everyone."""
    if EVERYONE_WORDS.search(text):
        return set(names)
    heard = f" {' '.join(_name_words(text))} "
    return {agent for agent, words in names.items() if any(f" {word} " in heard for word in words)}


def is_reply(message: dict, agent: str, person: str) -> bool:
    """A chat message from the agent that the person should hear. What the agent said out
    loud in a video call is already said, so it isn't said again here."""
    return message.get("author") == agent and message.get("to", "") in ("", person) \
        and message.get("source") != "spoken"


def history_items(messages: list[dict], agent: str, person: str) -> list[dict]:
    """Recent chat between the person and the agent, as GPT-Live startup history."""
    relevant = [message for message in messages
                if (message.get("author") == person and message.get("to") == agent) or is_reply(message, agent, person)]
    items = []
    for message in relevant[-HISTORY_MESSAGES:]:
        if message.get("author") == agent:
            items.append({"type": "message", "role": "assistant",
                          "content": [{"type": "output_text", "text": message["text"][:APPEND_CHARACTERS]}]})
        else:
            items.append({"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": message["text"][:APPEND_CHARACTERS]}]})
    return items


def status_note(status: dict | None) -> str:
    """What the agent is doing right now, for GPT-Live's thinking, or empty."""
    if not status:
        return ""
    state = status.get("state") or "idle"
    task, step = status.get("task") or "", status.get("step") or ""
    if state in ("idle", "") and not step:
        return "Status (background, don't announce): you are idle."
    parts = [f"Status (background, don't announce): you are {state}"]
    if task:
        parts.append(f"on the task '{task}'")
    if step:
        parts.append(f"(current step: {step})")
    return " ".join(parts) + "."


def hub_url() -> str:
    """The session hub, found the way the agent's runner finds it."""
    if os.environ.get("COPRESENCE_HUB_URL"):
        return os.environ["COPRESENCE_HUB_URL"].rstrip("/")
    host = HOST_ADDRESS_FILE.read_text().strip() if HOST_ADDRESS_FILE.exists() else "127.0.0.1"
    return f"http://{host}:{HUB_PORT}"


def session_code() -> str:
    if os.environ.get("COPRESENCE_CODE"):
        return os.environ["COPRESENCE_CODE"]
    return CODE_FILE.read_text().strip() if CODE_FILE.exists() else ""


def instance_for(agent: str) -> str:
    """The same on every start on this machine and clone, so a voice started again takes over
    from the one before at once, while a voice for the agent elsewhere waits its turn."""
    return hashlib.sha256(f"{socket.gethostname()}|{PROJECT_DIR}|{agent}".encode()).hexdigest()[:16]


async def _until_first_done(*coroutines) -> BaseException | None:
    """Runs the coroutines until one of them finishes, then stops the others; returns what the
    first one failed with, if it failed."""
    tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        first = next(iter(done))
        return None if first.cancelled() else first.exception()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# One person's line to the agent -------------------------------------------------------

class Line:
    """One person's line to the agent, as the agent's voice has it: their microphone comes
    in, and the voice's audio and what it is doing go out to them."""

    def __init__(self, line_id: str, person: str, voice: "AgentVoice"):
        self.id = line_id
        self.person = person
        self.voice = voice
        self.chunks: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        # What the person's helper last said: which agent they follow, muted, whether they hold a
        # button to talk and are holding it, their selection
        self.latest: dict = {}
        self.linked = asyncio.Event()
        self.open = True
        self.flushed_at = 0.0
        self.status_sent: dict | None = None
        self.status_sent_at = 0.0

    def hear(self, pcm: bytes) -> None:
        if not self.chunks.full():
            self.chunks.put_nowait(pcm)

    def set_link(self, header: dict) -> None:
        members = header.get("call_agents")
        self.latest = {"following": str(header.get("following") or ""), "muted": bool(header.get("muted")),
                       "hold": bool(header.get("hold")), "talking": bool(header.get("talking")),
                       "selection": header.get("selection") if isinstance(header.get("selection"), list) else [],
                       # Every agent in the person's call, this one among them, the first called first
                       "call_agents": [str(agent)[:60] for agent in members[:MAX_CALL_AGENTS]]
                       if isinstance(members, list) else []}
        self.linked.set()

    def hang_up(self) -> None:
        self.open = False
        self.linked.set()

    def link(self) -> dict | None:
        """What the person's helper last said, or None once they hung up."""
        return dict(self.latest) if self.open else None

    def play(self, pcm: bytes) -> None:
        for start in range(0, len(pcm), AUDIO_FRAME_BYTES):
            self.voice.send({"kind": "audio", "line": self.id}, pcm[start:start + AUDIO_FRAME_BYTES])

    def flush(self) -> None:
        """The person talks over the voice: their helper drops what is waiting to play."""
        if time.monotonic() - self.flushed_at >= FLUSH_SECONDS:
            self.flushed_at = time.monotonic()
            self.voice.send({"kind": "flush", "line": self.id})

    def publish(self, status: dict) -> None:
        if status == self.status_sent and time.monotonic() - self.status_sent_at < STATUS_REPEAT_SECONDS:
            return
        self.status_sent, self.status_sent_at = status, time.monotonic()
        self.voice.send({"kind": "status", "line": self.id, **status})

    def close(self, problem: str = "", retry: bool = True) -> None:
        self.voice.send({"kind": "closed", "line": self.id, "problem": problem, "retry": retry})


# One conversation with one person ------------------------------------------------------

class Conversation:
    def __init__(self, client, line: Line, agent: str, hub: str, code: str, decisions: httpx.AsyncClient | None = None):
        self.client = client
        self.decisions = decisions
        self.line = line
        self.agent = agent
        self.person = line.person or "Someone"
        self.hub = httpx.AsyncClient(base_url=hub.rstrip("/"), timeout=40, headers={CODE_HEADER: code})
        self.code = code
        self.selection: list = []
        self.connection = None
        self.started = False
        # What the person said while GPT-Live was starting waits for this, so their first words count
        self.ready = asyncio.Event()
        self.heard: list[dict] = []
        self.said: list[dict] = []
        self.heard_line = ""
        self.said_line = ""
        # The last whole lines either side said, for live captions in VR
        self.heard_last = ""
        self.said_last = ""
        self.last_spoke = 0.0
        self.delegated_until_ms = 0
        self.delegation_id = None
        self.promise_from_ms = 0
        self.delegated_at = -PROMISE_GRACE_SECONDS
        self.promised_at = None
        self.promised_ms = 0
        self.waiting_since = None
        self.last_followed = time.monotonic()
        self.state = "connecting"
        self.problem = ""
        self.account_problem = False
        self.status_note = ""
        self.status_noted_at = 0.0
        self.after = 0
        self.version = -1
        self.heard_at = 0.0
        self.stop_checked_ms = -1
        # Where in the conversation the person last said stop: nothing from before it is sent
        self.stopped_ms = -1
        # The other agents in the person's call, what each agent is called by, and whether the
        # person talks to this one: with others in the call, it only speaks and delegates then
        self.others: list[str] = []
        self.members: list[str] = []
        self.call_names: dict[str, list[str]] = {}
        self.addressed = True
        # The agents the person talks to now, which can be several at once, and until when this
        # voice has news for them that they hear whomever they talk to
        self.addressees: set[str] = set()
        self.reporting_until = 0.0
        self.reported = False
        # From a result arriving until the person speaks after it, the voice tells the result, which
        # promises nothing ("placing the chickens now" is the agent's news, not the voice's word)
        self.telling = False
        self.call_note_due = False
        # What the person has said since their last pause, and when they last said something
        self.utterance = ""
        self.utterance_at = 0.0

    def _may_speak(self) -> bool:
        """Whether the person hears this voice: alone in the call; while they talk to it, as the
        first in the call of the agents they talk to, so several don't answer at once; or while it
        has news for them."""
        if not self.others or self._reporting():
            return True
        if not self.addressed:
            return False
        return next((agent for agent in self.members if agent in self.addressees), self.agent) == self.agent

    def _reporting(self) -> bool:
        """Whether the voice still tells news: only until it said it, so it doesn't go on to answer
        what the person says to another agent."""
        now = time.monotonic()
        if now >= self.reporting_until:
            return False
        if self.reported and now - self.last_spoke >= REPORT_TAIL_SECONDS:
            self.reporting_until = 0.0
            return False
        return True

    def _may_delegate(self) -> bool:
        return not self.others or self.addressed

    def _together(self) -> list[str]:
        """The other agents the person asks along with this one, which get the request too."""
        if not self.others or self.agent not in self.addressees:
            return []
        return [agent for agent in self.members if agent in self.addressees and agent != self.agent]

    def _follow_call(self, link: dict) -> None:
        """Keeps up with who else is in the person's call, as their helper says."""
        members = link.get("call_agents") or []
        others = [agent for agent in members if agent != self.agent]
        if others == self.others:
            return
        if others and not self.others:
            # The agent called first keeps talking until the person names another
            self.addressees = {members[0]}
            if members[0] != self.agent:
                self.addressed = False
        self.others = others
        self.members = list(members)
        self.addressees &= set(members)
        self.call_names = spoken_names(members)
        self.call_note_due = True

    async def _note_call(self) -> None:
        self.call_note_due = False
        if self.others:
            note = (f"Also in this call: {', '.join(self.others)}. {self.person} picks whom they talk to by name, or "
                    f"talks to all of you at once; you are {self.agent}. Say nothing while they talk to another agent.")
        else:
            note = f"Only you are in this call with {self.person} now."
        await self.connection.session.thinking.append(delegation_id=None, content=note[:APPEND_CHARACTERS])

    def _notice_addressee(self, delta: str) -> None:
        """Naming an agent picks it: the person talks to it until they name another."""
        now = time.monotonic()
        if now - self.utterance_at > UTTERANCE_GAP_SECONDS:
            self.utterance = ""
        self.utterance += delta
        self.utterance_at = now
        if not self.others:
            return
        named = named_agents(self.utterance, self.call_names)
        if named:
            self.addressees = named
            if self.agent not in named:
                # The person turned to another agent, so this one's news waits
                self.reporting_until = 0.0
        addressed = self.agent in named or (self.addressed and not named)
        if addressed != self.addressed:
            self.addressed = addressed
            _log(f"{self.person} talks to {self.agent if addressed else ', '.join(sorted(named))} now.")

    # Status for the person's editor

    def _publish(self) -> None:
        if self.problem:
            state = "error"
        elif not self.started:
            state = "connecting"
        elif self._speaking():
            state = "speaking"
        elif self.waiting_since is not None:
            state = "waiting"
        else:
            state = "listening"
        self.state = state
        # What each side is saying right now, or said last, so VR can show the person they're heard
        self.line.publish({"agent": self.agent, "person": self.person, "state": state, "problem": self.problem,
                           "heard": self.heard_line.strip() or self.heard_last,
                           "said": self.said_line.strip() or self.said_last})

    def _speaking(self) -> bool:
        return time.monotonic() - self.last_spoke < SPEAKING_SECONDS

    async def _keep_publishing(self) -> None:
        while True:
            self._publish()
            if self.call_note_due and self.started:
                await self._note_call()
            await self._keep_promise()
            await self._check_stop()
            await asyncio.sleep(0.25)

    async def _check_stop(self) -> None:
        """Once the person finishes saying something, the decision model judges whether it
        tells the agent to stop or cancel its work; then the agent drops it. GPT-Live stops
        talking by itself when they talk over it, so only the work needs stopping here."""
        if not self.heard or time.monotonic() - self.heard_at < STOP_SETTLE_SECONDS:
            return
        end_ms = max(fragment["end_ms"] for fragment in self.heard)
        if end_ms <= self.stop_checked_ms:
            return
        self.stop_checked_ms = end_ms
        said = last_utterance(self.heard, max(0, end_ms - FALLBACK_REQUEST_MS), end_ms + 1)
        if not said or self.decisions is None:
            return
        if not await self._means_stop(said, end_ms):
            return
        _log(f"{self.person} said \"{said}\": telling {self.agent} to stop.")
        self.waiting_since = None
        self.promised_at = None
        self.stopped_ms = end_ms
        # Anything said before the stop is no request any more
        self.delegated_until_ms = end_ms + 1
        payload = {"author": self.person, "role": "human", "to": self.agent, "text": "stop", "kind": "stop",
                   "source": "editor-voice", "code": self.code}
        try:
            (await self.hub.post("/api/messages", json=payload, timeout=5)).raise_for_status()
        except httpx.HTTPError as error:
            _log(f"Couldn't tell {self.agent} to stop: {error}")

    async def _means_stop(self, said: str, end_ms: int) -> bool:
        """The decision model's view: does what the person just said tell the agent to stop its work?"""
        lines_said = [(self.person, line) for line in [spoken_between(self.heard, max(0, end_ms - 60000), end_ms - len(said))]
                      if line] + [(self.agent, spoken_between(self.said, max(0, end_ms - 60000), end_ms))] + [(self.person, said)]
        history = [(who, text) for who, text in lines_said if text][-DECISION_LINES:]
        working = "working on something it was asked" if self.waiting_since is not None else "not working on anything"
        request = core.decision_request(self.agent, history, f"Right now {self.agent} is {working}.",
                                        questions=("control",),
                                        setting=f"a voice conversation in Unreal between {self.person} and {self.agent}")
        try:
            reply = await self.decisions.post("/decisions", json=request)
            reply.raise_for_status()
            decision = core.read_decision(reply.json())
        except (httpx.HTTPError, ValueError) as error:
            _log(f"The decision about \"{said}\" failed ({error}).")
            return False
        control = decision.get("control") or {}
        if control.get("stop_work", 0.0) >= 0.2:
            _log("Decision about \"" + said + "\": " + ", ".join(f"{k} {v:.2f}" for k, v in control.items()))
        return core.control_command(decision) == core.STOP

    async def _keep_promise(self) -> None:
        """Sends the request on when the voice promised work but didn't delegate it."""
        if self.promised_at is None or time.monotonic() - self.promised_at < PROMISE_SECONDS:
            return
        self.promised_at = None
        self.promise_from_ms = self.promised_ms + 1
        if not spoken_between(self.heard, self.delegated_until_ms, self.promised_ms + 1):
            # The person asked nothing since the last request, so there is nothing to send, and an
            # earlier request sent again would be done twice
            return
        self.delegated_at = time.monotonic()
        _log("The voice promised work without delegating it; sending the request on.")
        await self._send_request(None, self.promised_ms)

    # The hub

    async def _hub_state(self, wait: bool) -> dict:
        params = {"after": self.after, "version": self.version if wait else -1}
        reply = (await self.hub.get("/api/state", params=params)).json()
        self.version = reply["version"]
        if reply["messages"]:
            self.after = max(message["id"] for message in reply["messages"])
        return reply

    async def _watch_hub(self) -> None:
        while True:
            try:
                reply = await self._hub_state(wait=True)
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(3)
                continue
            for message in reply["messages"]:
                if is_reply(message, self.agent, self.person):
                    await self._pass_on(message["text"])
            await self._note_status(reply["agents"].get(self.agent))

    async def _pass_on(self, text: str) -> None:
        _log(f"{self.agent} (chat, to {self.person}): {text}")
        self.waiting_since = None
        self.reporting_until = time.monotonic() + REPORT_SECONDS
        self.reported = False
        self.telling = True
        await self.connection.session.commentary.append(delegation_id=self.delegation_id,
                                                        content=text[:APPEND_CHARACTERS])

    async def _note_status(self, status: dict | None) -> None:
        note = status_note(status)
        now = time.monotonic()
        if not note or note == self.status_note or now - self.status_noted_at < STATUS_THINKING_SECONDS:
            return
        self.status_note, self.status_noted_at = note, now
        await self.connection.session.thinking.append(delegation_id=None, content=note[:APPEND_CHARACTERS])

    async def _send_request(self, delegation_id: str | None, offset_ms: int) -> None:
        await asyncio.sleep(TRANSCRIPT_SETTLE_SECONDS)
        if offset_ms <= self.stopped_ms:
            # Asked for before (or as) the person said stop
            _log("Not sending a request from before the stop.")
            await self.connection.session.commentary.append(
                delegation_id=delegation_id, content="Stopped. Nothing was sent to your worker.")
            return
        text = request_text(self.heard, self.said, self.delegated_until_ms, offset_ms)
        self.delegated_until_ms = offset_ms + 1
        if not text:
            await self.connection.session.commentary.append(
                delegation_id=delegation_id, content="I didn't catch the request. Ask the person to say it again.")
            return
        _log(f"To {self.agent} from {self.person}: {text.splitlines()[0]}")
        self.delegation_id = delegation_id
        self.waiting_since = time.monotonic()
        link = self.line.link() or {}
        payload = {"author": self.person, "role": "human", "text": text, "to": self.agent, "source": "editor-voice",
                   "selection": link.get("selection") or self.selection, "code": self.code}
        together = self._together()
        if together:
            # Said to several agents at once: the hub makes the copies one request, which one agent takes on
            payload["together"] = together
        try:
            response = await self.hub.post("/api/messages", json=payload, timeout=5)
            response.raise_for_status()
        except httpx.HTTPError as error:
            self.waiting_since = None
            problem = "the session code is wrong" if getattr(error, "response", None) is not None \
                and error.response.status_code == 403 else "the session hub can't be reached"
            _log(f"Couldn't send the request: {problem}")
            await self.connection.session.commentary.append(
                delegation_id=delegation_id, content=f"The request didn't reach your worker because {problem}.")

    # GPT-Live

    def _session_config(self, history: list[dict], appearance: dict | None) -> dict:
        greeting = []
        # Greets when someone starts talking with the agent, not each time a conversation reopens.
        # Someone holding a button to talk is already saying something, so the voice just listens
        held = bool((self.line.link() or {}).get("hold"))
        if not held and time.monotonic() - _last_talked.get(self.person, -REGREET_SECONDS) >= REGREET_SECONDS:
            greeting = [{"type": "message", "role": "developer",
                         "content": [{"type": "input_text", "text": GREETING.format(person=self.person, agent=self.agent)}]}]
        return {
            "model": MODEL,
            "instructions": INSTRUCTIONS.format(person=self.person, agent=self.agent),
            "input": history + greeting,
            "audio": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                      "output": {"voice": voice_for(self.agent, appearance)}},
            "delegation": {"type": "client"},
        }

    async def _send_audio(self) -> None:
        await self.ready.wait()
        while True:
            chunk = await self.line.chunks.get()
            await self.connection.session.input_audio.append(audio=base64.b64encode(chunk).decode("ascii"))

    def _notice_promise(self, end_ms: int) -> None:
        if self.telling or time.monotonic() - self.delegated_at < PROMISE_GRACE_SECONDS:
            self.promise_from_ms = end_ms + 1
            return
        if self.promised_at is not None:
            return
        said = spoken_between(self.said, self.promise_from_ms, end_ms + 1)
        if promised_work(said):
            self.promised_at, self.promised_ms = time.monotonic(), end_ms

    def _transcribe(self, event, heard: bool) -> None:
        (self.heard if heard else self.said).append({"text": event.delta, "start_ms": event.start_ms, "end_ms": event.end_ms})
        line = (self.heard_line if heard else self.said_line) + event.delta
        # One printed line per sentence or so, not per fragment
        if line.rstrip().endswith((".", "?", "!")) or len(line) > 200:
            _log(f"{self.person if heard else self.agent}: {line.strip()}")
            if heard:
                self.heard_last = line.strip()
            else:
                self.said_last = line.strip()
            line = ""
        if heard:
            self.heard_line = line
        else:
            self.said_line = line

    async def _receive(self) -> None:
        tasks = set()
        async for event in self.connection:
            kind = event.type
            if kind == "session.started":
                self.started = True
                self.ready.set()
                _last_talked[self.person] = time.monotonic()
                _log(f"Talking with {self.person} (session {event.session.id}).")
            elif kind == "session.output_audio.delta":
                # In a call with other agents, the person only hears this one while they talk to it
                if self._may_speak():
                    self.line.play(base64.b64decode(event.delta))
            elif kind == "session.input_transcript.delta":
                # Talking over the voice: what it sent ahead is dropped, so it stops sooner
                self.line.flush()
                self.heard_at = time.monotonic()
                if not self._speaking():
                    # Words said over the voice while it tells a result don't end the telling
                    self.telling = False
                self._transcribe(event, heard=True)
                self._notice_addressee(event.delta)
            elif kind == "session.output_transcript.delta":
                if self._may_speak():
                    self.last_spoke = time.monotonic()
                    if time.monotonic() < self.reporting_until:
                        self.reported = True
                    self._transcribe(event, heard=False)
                    self._notice_promise(event.end_ms)
            elif kind == "session.delegation.created" and not self._may_delegate():
                task = asyncio.create_task(self.connection.session.commentary.append(
                    delegation_id=event.delegation.id,
                    content=f"Not for you: {self.person} was talking to another agent, so nothing was sent. Say nothing."))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif kind == "session.delegation.created":
                self.promised_at = None
                self.delegated_at = time.monotonic()
                # A promise already said belongs to this delegation too
                self.promise_from_ms = max([self.promise_from_ms] + [f["end_ms"] + 1 for f in self.said])
                task = asyncio.create_task(self._send_request(event.delegation.id, event.offset_ms))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif kind == "session.closed":
                _last_talked[self.person] = time.monotonic()
                usage = getattr(event, "usage", None)
                _log(f"Conversation with {self.person} closed. Usage: {usage}")
                return
            elif kind == "error":
                error = event.error
                _log(f"GPT-Live error: {getattr(error, 'code', '')} {getattr(error, 'message', error)}")
                if not self.started:
                    self.problem = str(getattr(error, "message", error))
                    self.account_problem = getattr(error, "code", "") in ACCOUNT_ERRORS
                    if self.account_problem:
                        _log("That needs fixing in the OpenAI account; the person can follow the agent again to retry.")
                    return

    def _should_end(self) -> bool:
        link = self.line.link()
        if link is None:
            return True
        if link.get("muted"):
            _log(f"{self.person} muted the voice.")
            return True
        following = link.get("following", "")
        # A call can have several agents; a helper from before that names the one it follows
        members = link.get("call_agents") or ([following] if following else [])
        if members and self.agent not in members:
            return True
        self._follow_call(link)
        now = time.monotonic()
        if self.agent in members:
            self.selection = link.get("selection") or self.selection
            # Someone who holds a button to talk is only talking while they hold it
            if not link.get("hold") or link.get("talking"):
                self.last_followed = now
                return False
        working = self.waiting_since is not None and now - self.waiting_since < WAITING_SECONDS
        if working or self._speaking():
            self.last_followed = now
        return now - self.last_followed > LINGER_SECONDS

    def _failed(self, jobs: list) -> bool:
        """A background job (microphone, hub, status) stopped with an error, which ends the conversation."""
        for job in jobs:
            if job.done() and not job.cancelled() and job.exception() is not None:
                self.problem = f"{type(job.exception()).__name__}: {job.exception()}"
                _log(f"Voice problem, ending the conversation with {self.person}: {self.problem}")
                return True
        return False

    async def run(self) -> bool:
        """Talks until the person stops talking with the agent; False if it couldn't start."""
        self._publish()
        try:
            first = await self._hub_state(wait=False)
        except (httpx.HTTPError, ValueError):
            self.problem = "the agent's voice can't reach the session hub"
            _log(f"Can't reach the session hub at {self.hub.base_url}.")
            self._publish()
            await self.hub.aclose()
            return False
        history = history_items(first["messages"], self.agent, self.person)
        appearance = (first["agents"].get(self.agent) or {}).get("appearance")
        background = []
        try:
            async with self.client.live.connect() as connection:
                self.connection = connection
                await connection.session.start(session=self._session_config(history, appearance), event_id="start")
                receiver = asyncio.create_task(self._receive())
                background = [asyncio.create_task(job()) for job in (self._send_audio, self._watch_hub, self._keep_publishing)]
                while not receiver.done() and not self._failed(background) and not self._should_end():
                    await asyncio.sleep(0.25)
                if not receiver.done():
                    _log(f"{self.person} stopped talking with {self.agent}; closing the conversation.")
                    await connection.session.close()
                    try:
                        await asyncio.wait_for(asyncio.shield(receiver), timeout=10)
                    except asyncio.TimeoutError:
                        _log("GPT-Live didn't confirm the close; usage for this conversation is unconfirmed.")
                receiver.cancel()
        except Exception as error:
            _log(f"Conversation with {self.person} failed: {error}")
            # OpenAI turns the connection away before any session starts when the key is wrong
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status in (401, 403):
                self.problem = "OpenAI refused the key on the agent's machine"
                self.account_problem = True
            else:
                self.problem = str(error)
        finally:
            for job in background:
                job.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await self.hub.aclose()
            self._publish()
        return self.started and not self.problem


# The agent's end at the hub -----------------------------------------------------------

class AgentVoice:
    """Talks with everyone who opens a line to the agent, each in a conversation of their
    own, through one stream from the hub and one to it."""

    def __init__(self, client, decisions: httpx.AsyncClient | None, agent: str, hub: str, code: str, instance: str):
        self.client = client
        self.decisions = decisions
        self.agent = agent
        self.hub_url = hub.rstrip("/")
        self.code = code
        self.instance = instance
        self.lines: dict[str, Line] = {}
        self.outgoing: asyncio.Queue[bytes] = asyncio.Queue(maxsize=OUTGOING_FRAMES)
        self.talks: set[asyncio.Task] = set()

    def send(self, header: dict, payload: bytes = b"") -> None:
        """Queues a frame for the hub; when the hub can't keep up it is dropped, since late
        sound is no use in a conversation."""
        try:
            self.outgoing.put_nowait(lines.frame(header, payload))
        except asyncio.QueueFull:
            pass

    async def run(self) -> None:
        """Stays connected to the hub, connecting again whenever the connection drops."""
        params = {"agent": self.agent, "instance": self.instance}
        timeout = httpx.Timeout(10.0, read=lines.STREAM_IDLE_SECONDS)
        wait = RECONNECT_SECONDS[0]
        # What was last said about the hub, so a hub that stays away isn't logged every few seconds
        reported = ""
        async with httpx.AsyncClient(base_url=self.hub_url, headers={CODE_HEADER: self.code}, timeout=timeout) as hub:
            while True:
                problem = None
                try:
                    async with hub.stream("GET", "/api/voice/agent", params=params) as down:
                        if down.status_code == 409:
                            problem = f"another voice for {self.agent} is connected to the hub; waiting for it to stop"
                            wait = REFUSED_SECONDS
                        elif down.status_code == 403:
                            problem = "the hub turned this voice away: the session code is wrong"
                            wait = REFUSED_SECONDS
                        else:
                            down.raise_for_status()
                            _log(f"Connected to the hub at {self.hub_url}; people can talk with {self.agent}.")
                            reported, wait = "", RECONNECT_SECONDS[0]
                            failure = await _until_first_done(self._listen(down), self._speak(hub, params))
                            problem = f"lost the hub ({failure or 'it closed the connection'})"
                except (httpx.HTTPError, OSError) as error:
                    problem = f"can't reach the hub at {self.hub_url} ({error})"
                if problem != reported:
                    _log(f"{problem[0].upper()}{problem[1:]}; trying again.")
                    reported = problem
                await asyncio.sleep(wait)
                wait = min(wait * 2, RECONNECT_SECONDS[1])

    async def _listen(self, down: httpx.Response) -> None:
        reader = lines.FrameReader()
        async for chunk in down.aiter_raw():
            for header, payload in reader.feed(chunk):
                self._received(header, payload)

    async def _speak(self, hub: httpx.AsyncClient, params: dict) -> None:
        async def frames():
            while True:
                try:
                    yield await asyncio.wait_for(self.outgoing.get(), timeout=lines.PING_SECONDS)
                except asyncio.TimeoutError:
                    yield lines.frame({"kind": "ping"})

        response = await hub.post("/api/voice/agent", params=params, content=frames())
        response.raise_for_status()

    def _received(self, header: dict, payload: bytes) -> None:
        kind = header.get("kind")
        line_id = str(header.get("line", ""))
        if kind == "lines":
            current = {str(item.get("line")): str(item.get("person", "")) for item in header.get("lines") or []}
            # Lines the hub no longer has ended while this voice was away
            for known in list(self.lines):
                if known not in current:
                    self.lines[known].hang_up()
            for new_id, person in current.items():
                if new_id not in self.lines:
                    self._open(new_id, person)
            return
        if kind == "open":
            self._open(line_id, str(header.get("person", "")))
            return
        line = self.lines.get(line_id)
        if line is None:
            return
        if kind == "audio":
            line.hear(payload)
        elif kind == "link":
            line.set_link(header)
        elif kind == "close":
            line.hang_up()

    def _open(self, line_id: str, person: str) -> None:
        if not line_id or line_id in self.lines:
            return
        line = self.lines[line_id] = Line(line_id, person or "Someone", self)
        task = asyncio.create_task(self._talk(line))
        self.talks.add(task)
        task.add_done_callback(self.talks.discard)

    async def _talk(self, line: Line) -> None:
        """One person's conversation, from their first word up the line until it ends."""
        problem, retry = "", True
        try:
            await asyncio.wait_for(line.linked.wait(), timeout=LINK_WAIT_SECONDS)
            if not line.open:
                return
            conversation = Conversation(self.client, line, self.agent, self.hub_url, self.code, self.decisions)
            await conversation.run()
            problem, retry = conversation.problem, not conversation.account_problem
        except asyncio.TimeoutError:
            problem = "the microphone didn't reach the agent's voice"
        except Exception as error:
            problem = f"the agent's voice failed: {error}"
            _log(f"Talking with {line.person} failed: {error}")
        finally:
            self.lines.pop(line.id, None)
            line.close(problem, retry)


async def _watch_parent(parent: int) -> None:
    """Returns once the agent's runner that started this has stopped."""
    while process_running(parent):
        await asyncio.sleep(PARENT_CHECK_SECONDS)
    _log("The agent's runner stopped, so its voice stops too.")


async def serve(agent: str, hub: str, code: str, instance: str, parent: int) -> None:
    from openai import AsyncOpenAI

    key, source = openai_key()
    _log(f"{agent}'s voice, with the OpenAI key from {source}.")
    client = AsyncOpenAI(api_key=key)
    # The decision model judges whether what someone says tells the agent to stop
    decisions = httpx.AsyncClient(base_url="https://api.openai.com/v1", timeout=8,
                                  headers={"Authorization": f"Bearer {key}"})
    voice = AgentVoice(client, decisions, agent, hub, code, instance)
    jobs = [voice.run()]
    if parent:
        jobs.append(_watch_parent(parent))
    problem = await _until_first_done(*jobs)
    if problem is not None:
        raise problem


async def check() -> None:
    from openai import AsyncOpenAI

    key, source = openai_key()
    print(f"OpenAI key from {source}.")
    async with AsyncOpenAI(api_key=key) as client:
        # Starting a real session (closed at once, a second or so of billing) also catches
        # an account without credits, which listing the model doesn't
        try:
            async with client.live.connect() as connection:
                await connection.session.start(session={"model": MODEL, "delegation": {"type": "client"}},
                                               event_id="check")
                async for event in connection:
                    if event.type == "session.started":
                        print(f"The key can start {MODEL} conversations.")
                        await connection.session.close()
                    elif event.type == "error":
                        print(f"The key can't start {MODEL} conversations: "
                              f"{getattr(event.error, 'code', '')} {getattr(event.error, 'message', event.error)}")
                        break
                    elif event.type == "session.closed":
                        break
        except Exception as error:
            print(f"The key can't start {MODEL} conversations: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("serve", "key", "check"))
    parser.add_argument("value", nargs="?", help="for serve: the agent's name; for key: the 1Password secret "
                                                 "reference, op://..., or - to read the key itself from standard input")
    parser.add_argument("account", nargs="?", default="", help="for key: the 1Password account, if you have several")
    parser.add_argument("--hub", default="", help="the session hub, if not where the runner finds it")
    parser.add_argument("--parent", type=int, default=0, help="stop when this process (the agent's runner) stops")
    parser.add_argument("--instance", default="", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.command == "key":
        if not arguments.value:
            sys.exit('Usage: copresence_agent_voice.py key "op://<vault>/<item>/<field>" [account], or key - '
                     "with the key on standard input")
        if arguments.value == "-":
            save_key(sys.stdin.read())
        else:
            save_key_reference(arguments.value, arguments.account)
    elif arguments.command == "check":
        asyncio.run(check())
    else:
        if not arguments.value:
            sys.exit('Usage: copresence_agent_voice.py serve "Agent Name"')
        try:
            asyncio.run(serve(arguments.value, arguments.hub or hub_url(), session_code(),
                              arguments.instance or instance_for(arguments.value), arguments.parent))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
