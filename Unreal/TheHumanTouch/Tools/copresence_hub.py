# /// script
# requires-python = ">=3.11"
# ///
"""Session hub for agent co-presence: the session chat between people and agents, and
each agent's live status and look, which every editor shows on the agent's ghost, in
the Agents widget and in Agent Chat. It also knows which agents are in a video call, so
the Agents widget can mark them. A message can carry a drawing a person made on their
editor to point at things: what it marks, which the message lists, and pictures of the
editor, which agents fetch from the hub when they look at it. A message can also be a
stop: it tells the agent to drop what it is doing (people say "stop" in a call, or write it).
While an agent speaks in a call, editors chatter its head. Runs on the host next to the
Multi-User server; editors and agents on other machines reach it over the local network.

It also serves the session dashboard (Tools/copresence_dashboard.html, at / on the hub's
address): who is in the session, people in VR or at a desktop and agents with what they are
doing, the spectator, and the join command for a new person. Agents' editors and the
spectator tell it who they see in the session. The spectator (Tools/copresence_spectator.py)
is a session client of its own whose camera follows whoever the dashboard picks, or flies
freely; it reads where to look from here. Machines that run a station
(Tools/copresence_station.py) start and stop agents and the spectator when the dashboard asks.

People talking with an agent out loud reach its voice through the hub too: their microphone
goes up a voice line to the agent's voice on its own machine, and its answers come back down
(Tools/copresence_voice_lines.py), so only machines that run agents need an OpenAI key.

    python Tools/copresence_hub.py [--bind 0.0.0.0] [--port 8790] [--code-file PATH]
                                   [--state-file PATH] [--address ADDRESS] [--log PATH] [--detach]

With a state file, the chat, the agents it has heard from and their calls survive a
restart, so an agent that is quietly waiting stays listed, and one in a call stays marked.
So does whoever the spectator follows. --address is what people join with, such as
multiunreal.geotech-hub.com, which the dashboard puts in the join command.

Chat messages and call states from other machines need the session code: agents act on
what is written in the chat, and the call mark tells people when the level is on show
outside the session. The host's own editor and agents read it from the code file;
teammates get it with the join command. The code is kept in the code file, so it stays
the same when the hub restarts. Agent status is accepted from the whole network; it only
changes how an agent's ghost looks and what its bubble says. Everything the dashboard
changes needs the code, from this machine too, since any web page could otherwise send it.

At the always-on session's web address, the tunnel brings everything but the relay's own
paths here, so the dashboard opens there too. Those requests come in from the web looking as
if they were from this machine, so from the web everything but the page needs the code: as a
header, or for pictures such as the spectator's view, the cookie a header with the code got.
"""

import argparse
import base64
import binascii
import hmac
import ipaddress
import json
import os
import re
import secrets
import string
import struct
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from copresence_hub_media import MEDIA_PREFIX, MediaStore
from copresence_voice_lines import VOICE_PREFIX, VoiceLines, read_chunks

MESSAGE_LIMIT = 500
MESSAGES_PER_REPLY = 200
LONG_POLL_SECONDS = 25
MAX_TEXT_LENGTH = 4000
MAX_NAME_LENGTH = 60
LOCAL_ADDRESSES = {"127.0.0.1", "::1"}
STATUS_FIELDS = ("state", "task", "step", "emote", "emote_seconds", "appearance")
# Where an agent is with a video call, as its call bot (Tools/copresence_call.py) reports it
CALL_STATES = ("joining", "waiting", "in_call", "left")
# A plain message, or a stop that makes the agent drop what it is doing
MESSAGE_KINDS = ("", "stop")
# Something a person says to several agents in a call at once reaches each as a message of its
# own, from its own voice; copies from the same person this close together are one request
GROUP_SECONDS = 20.0
# The most agents one request can go to together, as many as a call holds
MAX_TOGETHER = 8
# Which agent took on a request that went to several, kept this long
CLAIM_SECONDS = 3600.0
# Where a message came from, such as "voice" or "call"; agents and voices go by it
MAX_SOURCE_LENGTH = 40
MAX_EVENT_ID_LENGTH = 512
# A voice reports it is speaking every few seconds; without that, the head stops chattering
SPEAKING_SECONDS = 8.0
# Someone in VR talking with an agent says so every few seconds; without that, the agent
# stops waiting next to them
CONVERSATION_SECONDS = 6.0
SAVE_SECONDS = 2.0
# Drawings: what they mark rides along with the message, the pictures are fetched on their own
ANNOTATION_IMAGES = ("full", "detail")
MAX_ANNOTATION_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 12 * 1024 * 1024
JPEG_START = b"\xff\xd8"
# Pictures are kept for this many of the latest drawings
IMAGES_KEPT = 200
CODE_HEADER = "X-CoPresence-Code"
# Cloudflare names the visitor in this header on everything the tunnel brings in from the web
WEB_CLIENT_HEADER = "CF-Connecting-IP"
CODE_COOKIE = "copresence_code"
COOKIE_SECONDS = 30 * 24 * 3600
PAGE_PATHS = ("/", "/dashboard")

DASHBOARD_FILE = Path(__file__).with_name("copresence_dashboard.html")
# The dashboard asks again this often even when nothing changed, so people and machines that
# went quiet drop off it
DASHBOARD_POLL_SECONDS = 10
# Agents' editors and the spectator say who they see in the session every few seconds; a report
# older than this no longer counts
ROSTER_SECONDS = 20.0
MAX_PARTICIPANTS = 100
# How someone is in the session, most telling first: an agent, the spectator, a person in VR,
# a person at a desktop editor, or not known, such as someone in another level
PARTICIPANT_KINDS = ("agent", "spectator", "vr", "desktop", "")
# The spectator says how it is doing every few seconds; without that for this long it counts as off
SPECTATOR_SECONDS = 15.0
# The spectator films from behind and above whoever it follows, or through their eyes
FRAMINGS = ("behind", "eyes")
NUDGES_KEPT = 20
MAX_VIEW_BYTES = 2 * 1024 * 1024
# The spectator streams its pictures up one long request, each a 4-byte length and then the JPEG,
# and the dashboard plays them as they come, as a stream an image tag shows
VIEW_LENGTH = struct.Struct(">I")
VIEW_BOUNDARY = "copresence-view"
# A stream that carried nothing for this long has died; one to a dashboard sends its picture again
# at least this often, so nothing on the way closes it
VIEW_STREAM_IDLE_SECONDS = 30.0
VIEW_STREAM_REPEAT_SECONDS = 15.0
MAX_VIEW_STREAMS = 16
# Machines that run a station (Tools/copresence_station.py) report every few seconds, or wait
# here this long for something to do
STATION_SECONDS = 45.0
STATION_WAIT_SECONDS = 20.0
COMMANDS_KEPT = 50
COMMANDS_SHOWN = 20
# A command no station picked up in this long, or that got no answer in this long, has failed
PICKUP_SECONDS = 30.0
ANSWER_SECONDS = 300.0
BRAINS = ("claude", "codex")
# A station's report lists at most this many of its agents, which keeps reports small
STATION_AGENTS_LISTED = 200
# New agents' names go into the join scripts' patterns and config files, so only plain ones
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}")
# Model names as Claude Code and Codex take them, such as claude-opus-5-5
AGENT_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# How hard an agent thinks, as Claude Code's --effort takes it
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MEET_LINK = re.compile(r"(?:https://)?meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:[/?#].*)?")
COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
# Editors show an agent's bubble by these, and the dashboard says the same
AGENT_STALE_SECONDS = 180.0
AGENT_DONE_SECONDS = 8.0
AGENT_ERROR_SECONDS = 6.0


class Hub:
    def __init__(self, code: str, state_file: Path | None = None, address: str = ""):
        self.code = code
        # What people join with, for the dashboard's join command
        self.address = address
        # Tells the spectator the hub restarted, so it counts the moves it was asked for afresh
        self.boot = secrets.token_hex(4)
        self.changed = threading.Condition()
        self.version = 0
        # Moves with every change, and with changes only the dashboard shows, which shouldn't
        # wake every editor's long poll
        self.dashboard_version = 0
        self.next_message_id = 1
        self.messages: list[dict] = []
        self.agents: dict[str, dict] = {}
        # Kept apart from the agents' status, since each status report replaces the whole of it
        self.calls: dict[str, str] = {}
        # Who owns each agent and whether everyone may talk with it and hear it: name -> {"owner",
        # "public"}. An agent someone brought in with + Agent is theirs alone until they share it
        self.audiences: dict[str, dict] = {}
        # Which machines their owners share with everyone, by station name; a machine with an owner
        # (someone's own, started from their Agents tab) is theirs alone until they share it
        self.station_shares: dict[str, bool] = {}
        # When each agent's voice last said it was speaking, by monotonic time; not saved
        self.speaking: dict[str, float] = {}
        # Who each agent is talking with from VR, and when they last said so, by monotonic time; not saved
        self.conversations: dict[str, tuple[str, float]] = {}
        # Which agent took on each request that went to several, so only one does it: the request's
        # group (as text, as JSON keeps it) -> {"lead", "time"}
        self.claims: dict[str, dict] = {}
        self.state_file = state_file
        # Pictures of drawings by message id, kept next to the state file when there is one
        self.images: dict[int, dict[str, bytes]] = {}
        self.image_dir = state_file.parent / "annotations" if state_file else None
        # Who each agent's editor and the spectator see in the session: reporter -> (monotonic
        # time of the report, [{"name", "kind"}]); not saved
        self.rosters: dict[str, tuple[float, list[dict]]] = {}
        # Whom the spectator follows ("" flies freely) and how it frames them, kept across restarts
        self.spectator_wanted = {"target": "", "framing": FRAMINGS[0]}
        # Moves people asked the freely flying spectator for, which it makes once each
        self.nudges: list[dict] = []
        self.next_nudge = 1
        self.spectator_version = 1
        # What the spectator last said about itself, and when by monotonic time; not saved
        self.spectator_report: dict | None = None
        self.spectator_seen = 0.0
        # The latest small picture of what the spectator shows, for the dashboard
        self.spectator_view: bytes | None = None
        self.view_id = 0
        # Machines that start agents and the spectator: name -> {"report", "seen"}; not saved
        self.stations: dict[str, dict] = {}
        self.commands: list[dict] = []
        self.next_command = 1
        self.unsaved = False
        if state_file and state_file.exists():
            saved = json.loads(state_file.read_text())
            self.messages = saved.get("messages", [])[-MESSAGE_LIMIT:]
            self.agents = saved.get("agents", {})
            self.calls = saved.get("calls", {})
            self.audiences = saved.get("audiences", {})
            self.station_shares = saved.get("station_shares", {})
            self.claims = saved.get("claims", {})
            self.spectator_wanted.update(saved.get("spectator", {}))
            self.next_message_id = max([saved.get("next_message_id", 1)] + [message["id"] + 1 for message in self.messages])
        if state_file:
            threading.Thread(target=self._keep_saved, daemon=True).start()

    def _bump(self) -> None:
        self.version += 1
        self.dashboard_version += 1
        self.unsaved = True
        self.changed.notify_all()

    def _bump_dashboard(self) -> None:
        """A change only the dashboard shows, which leaves editors' long polls asleep."""
        self.dashboard_version += 1
        self.changed.notify_all()

    def _keep_saved(self) -> None:
        # Status changes come several times a second while agents work, so writes are batched
        while True:
            time.sleep(SAVE_SECONDS)
            if not self.unsaved:
                continue
            with self.changed:
                self.unsaved = False
                snapshot = json.dumps({"next_message_id": self.next_message_id, "messages": self.messages,
                                       "agents": self.agents, "calls": self.calls, "audiences": self.audiences,
                                       "station_shares": self.station_shares, "claims": self.claims,
                                       "spectator": self.spectator_wanted})
            partial = self.state_file.with_suffix(".tmp")
            partial.write_text(snapshot)
            partial.replace(self.state_file)

    def add_message(self, author: str, role: str, text: str, to: str, selection: list,
                    annotation: dict | None = None, images: dict[str, bytes] | None = None,
                    kind: str = "", source: str = "", source_event_id: str = "",
                    together: list[str] | None = None) -> dict:
        with self.changed:
            # A sender retrying after a lost reply must not make the agent do the work twice
            if source_event_id:
                for existing in reversed(self.messages):
                    if (existing.get("source_event_id") == source_event_id and existing["author"] == author
                            and existing["to"] == to):
                        return existing
            message = {
                "id": self.next_message_id,
                "time": time.time(),
                "author": author,
                "role": role,
                "text": text[:MAX_TEXT_LENGTH],
                # An agent's name, or empty for everyone
                "to": to,
                # What the author had selected in their editor when they sent it
                "selection": selection[:50],
            }
            if kind:
                message["kind"] = kind
            together = [name for name in together or [] if name != to]
            if role == "human" and to and together and not kind:
                # The other agents the person said it to at the same time, each of which gets a copy
                message["together"] = together
                message["group"] = self._group_of(message, together)
            if source:
                message["source"] = source
            if source_event_id:
                message["source_event_id"] = source_event_id
            if annotation is not None:
                # What the author drew on their editor, and the pictures of it there are
                message["annotation"] = {**annotation, "images": sorted(images or {})}
            self.next_message_id += 1
            self.messages = (self.messages + [message])[-MESSAGE_LIMIT:]
            if images:
                self._keep_images(message["id"], images)
            self._bump()
            return message

    def _group_of(self, message: dict, together: list[str]) -> int:
        """Which request a copy of something said to several agents together belongs to: the
        first copy's, from the same person a moment before to one of the others, or its own."""
        for existing in reversed(self.messages):
            if message["time"] - existing["time"] > GROUP_SECONDS:
                break
            if (existing["author"] == message["author"] and existing.get("group") and existing["to"] in together
                    and message["to"] in existing.get("together", [])):
                return existing["group"]
        return message["id"]

    def claim(self, group: int, agent: str) -> dict:
        """Takes on a request that went to several agents for this agent, unless another took it
        first. Says which agent has it."""
        with self.changed:
            now = time.time()
            self.claims = {key: claim for key, claim in self.claims.items() if now - claim["time"] < CLAIM_SECONDS}
            claim = self.claims.setdefault(str(group), {"lead": agent, "time": now})
            if claim["lead"] == agent:
                self.unsaved = True
            return {"group": group, "lead": claim["lead"], "granted": claim["lead"] == agent}

    def _keep_images(self, message_id: int, images: dict[str, bytes]) -> None:
        self.images[message_id] = images
        for old_id in sorted(self.images)[:-IMAGES_KEPT]:
            del self.images[old_id]
        if self.image_dir:
            self.image_dir.mkdir(parents=True, exist_ok=True)
            for name, data in images.items():
                (self.image_dir / f"{message_id}-{name}.jpg").write_bytes(data)

    def image(self, message_id: int, name: str) -> bytes | None:
        """A picture of the drawing a message carries, from memory or, after a restart, from disk."""
        if name not in ANNOTATION_IMAGES:
            return None
        with self.changed:
            data = self.images.get(message_id, {}).get(name)
        if data is None and self.image_dir:
            path = self.image_dir / f"{message_id}-{name}.jpg"
            data = path.read_bytes() if path.is_file() else None
        return data

    def set_status(self, agent: str, status: dict) -> None:
        with self.changed:
            # Appearance is only sent when it changes, so keep the last one
            previous = self.agents.get(agent, {})
            if status.get("appearance") is None and previous.get("appearance") is not None:
                status["appearance"] = previous["appearance"]
            self.agents[agent] = {**status, "updated": time.time()}
            self._bump()

    def set_call(self, agent: str, state: str) -> None:
        with self.changed:
            self.calls[agent] = state
            self._bump()

    def audience(self, agent: str) -> tuple[str, bool]:
        """The agent's owner, if it has one, and whether everyone may talk with it and hear it.
        Agents nobody claimed, such as ones joined by hand, are everyone's."""
        with self.changed:
            entry = self.audiences.get(agent) or {}
            return entry.get("owner", ""), bool(entry.get("public", True))

    def claim_agent(self, agent: str, owner: str) -> None:
        """Someone asked for this agent: it is theirs alone until they share it."""
        with self.changed:
            self.audiences[agent] = {"owner": owner, "public": False}
            self._bump()

    def set_audience(self, agent: str, person: str, public: bool) -> str:
        """Shares the agent with everyone, or keeps it to its owner. Only the owner decides; an agent
        nobody owns becomes the agent of whoever keeps it to themselves. Returns why not, or empty."""
        with self.changed:
            owner = (self.audiences.get(agent) or {}).get("owner", "")
            if owner and owner != person:
                return f"Only {owner} can change who hears {agent}"
            if not owner and public:
                self.audiences.pop(agent, None)
            else:
                self.audiences[agent] = {"owner": owner or person, "public": public}
            self._bump()
            return ""

    def set_speaking(self, agent: str, speaking: bool) -> None:
        with self.changed:
            was = self._is_speaking(agent)
            if speaking:
                self.speaking[agent] = time.monotonic()
                # If the voice dies mid-sentence, editors hear about it when the mark runs out
                timer = threading.Timer(SPEAKING_SECONDS + 0.1, self._speaking_ran_out, args=(agent,))
                timer.daemon = True
                timer.start()
            else:
                self.speaking.pop(agent, None)
            # Only a change wakes the editors; a voice repeats "speaking" every few seconds
            if was != speaking:
                self._bump()

    def _speaking_ran_out(self, agent: str) -> None:
        with self.changed:
            if agent in self.speaking and not self._is_speaking(agent):
                del self.speaking[agent]
                self._bump()

    def _is_speaking(self, agent: str) -> bool:
        return time.monotonic() - self.speaking.get(agent, -SPEAKING_SECONDS) < SPEAKING_SECONDS

    def set_conversation(self, agent: str, person: str) -> None:
        with self.changed:
            was = self._talking_with(agent)
            if person:
                self.conversations[agent] = (person, time.monotonic())
                # If the VR game quits mid-conversation, the agent's runner hears about it when the mark runs out
                timer = threading.Timer(CONVERSATION_SECONDS + 0.1, self._conversation_ran_out, args=(agent,))
                timer.daemon = True
                timer.start()
            else:
                self.conversations.pop(agent, None)
            # Only a change wakes everyone; the VR game says so again every few seconds
            if was != self._talking_with(agent):
                self._bump()

    def _conversation_ran_out(self, agent: str) -> None:
        with self.changed:
            if agent in self.conversations and not self._talking_with(agent):
                del self.conversations[agent]
                self._bump()

    def _talking_with(self, agent: str) -> str:
        person, since = self.conversations.get(agent, ("", -CONVERSATION_SECONDS))
        return person if time.monotonic() - since < CONVERSATION_SECONDS else ""

    def _agents_with_age(self) -> dict:
        # Ages instead of timestamps, so editors on machines with other clocks agree
        now = time.time()
        agents = {name: {**status, "age": round(now - status["updated"], 2)} for name, status in self.agents.items()}
        # Calls go on agents the hub knows from their own status reports; a call alone lists no agent
        for name in agents.keys() & self.calls.keys():
            agents[name]["call"] = self.calls[name]
        for name in agents:
            if self._is_speaking(name):
                agents[name]["speaking"] = True
            person = self._talking_with(name)
            if person:
                agents[name]["talking_with"] = person
        for name in agents.keys() & self.audiences.keys():
            agents[name]["owner"] = self.audiences[name].get("owner", "")
            agents[name]["public"] = bool(self.audiences[name].get("public", True))
        return agents

    def state(self, after_id: int, known_version: int, wait: bool) -> dict:
        with self.changed:
            if wait:
                self.changed.wait_for(lambda: self.version != known_version, timeout=LONG_POLL_SECONDS)
            newer = [message for message in self.messages if message["id"] > after_id]
            # Agents only report how they are when that changes, so who is in the session comes from the rosters
            present = sorted(name for name, kind in self._participants().items() if kind == "agent")
            return {"version": self.version, "messages": newer[-MESSAGES_PER_REPLY:],
                    "agents": self._agents_with_age(), "present_agents": present,
                    "capabilities": {"message_deduplication": True, "request_claims": True}}

    # Who is in the session ------------------------------------------------------

    def set_roster(self, reporter: str, participants: list[dict]) -> None:
        with self.changed:
            previous = self.rosters.get(reporter)
            self.rosters[reporter] = (time.monotonic(), participants)
            if previous is None or previous[1] != participants:
                self._bump_dashboard()

    def _participants(self) -> dict[str, str]:
        """Everyone the recent reports list, by name, with the most telling kind any report gave."""
        now = time.monotonic()
        kinds: dict[str, str] = {}
        for seen, participants in self.rosters.values():
            if now - seen > ROSTER_SECONDS:
                continue
            for participant in participants:
                name, kind = participant["name"], participant["kind"]
                if name not in kinds or PARTICIPANT_KINDS.index(kind) < PARTICIPANT_KINDS.index(kinds[name]):
                    kinds[name] = kind
        return kinds

    def _has_roster(self) -> bool:
        now = time.monotonic()
        return any(now - seen <= ROSTER_SECONDS for seen, _ in self.rosters.values())

    # The spectator ----------------------------------------------------------------

    def follow(self, target: str, framing: str) -> dict:
        with self.changed:
            self.spectator_wanted = {"target": target, "framing": framing}
            self.spectator_version += 1
            self._bump_dashboard()
            self.unsaved = True
            return dict(self.spectator_wanted)

    def nudge(self, move: list[float], turn: list[float]) -> dict:
        """Moves the spectator's camera a step; it flies freely from where it is from then on."""
        with self.changed:
            nudge = {"id": self.next_nudge, "move": move, "turn": turn}
            self.next_nudge += 1
            self.nudges = (self.nudges + [nudge])[-NUDGES_KEPT:]
            if self.spectator_wanted["target"]:
                self.spectator_wanted = {**self.spectator_wanted, "target": ""}
                self.unsaved = True
            self.spectator_version += 1
            self._bump_dashboard()
            return nudge

    def spectator_state(self, known_version: int, wait: bool) -> dict:
        """Where the spectator should look, for the spectator's editor, which long-polls it."""
        with self.changed:
            if wait:
                self.changed.wait_for(lambda: self.spectator_version != known_version, timeout=LONG_POLL_SECONDS)
            return {"boot": self.boot, "version": self.spectator_version, **self.spectator_wanted,
                    "nudges": list(self.nudges)}

    def set_spectator_report(self, report: dict) -> None:
        with self.changed:
            changed = report != self.spectator_report or not self._spectator_running()
            self.spectator_report = report
            self.spectator_seen = time.monotonic()
            if changed:
                self._bump_dashboard()

    def set_spectator_view(self, data: bytes) -> None:
        with self.changed:
            first = self.spectator_view is None
            self.spectator_view = data
            self.view_id += 1
            # The pictures stream to dashboards on their own; the dashboard itself only needs to
            # hear that there is one, not each one
            if first:
                self._bump_dashboard()
            else:
                self.changed.notify_all()

    def next_view(self, after: int, timeout: float) -> tuple[int, bytes | None]:
        """The spectator's picture newer than after, or the current one once timeout passed."""
        with self.changed:
            self.changed.wait_for(lambda: self.view_id != after, timeout=timeout)
            return self.view_id, self.spectator_view

    def _spectator_running(self) -> bool:
        return self.spectator_report is not None and time.monotonic() - self.spectator_seen < SPECTATOR_SECONDS

    def spectator_station(self) -> str | None:
        """The station the spectator runs on, when one started it."""
        with self.changed:
            return next((name for name, station in self._fresh_stations().items()
                         if station["report"].get("spectator", {}).get("running")), None)

    # Stations and their commands ----------------------------------------------------

    def _fresh_stations(self) -> dict[str, dict]:
        now = time.monotonic()
        return {name: station for name, station in self.stations.items() if now - station["seen"] < STATION_SECONDS}

    def station(self, name: str) -> dict | None:
        with self.changed:
            station = self._fresh_stations().get(name)
            return station["report"] if station else None

    def station_audience(self, name: str) -> tuple[str, bool]:
        """The machine's owner, if it has one, and whether everyone may start agents on it. Machines
        nobody owns, such as LUNA1, are everyone's."""
        with self.changed:
            owner = ((self.stations.get(name) or {}).get("report") or {}).get("owner", "")
            return owner, (not owner) or bool(self.station_shares.get(name))

    def set_station_audience(self, name: str, person: str, public: bool) -> str:
        """Shares the machine with everyone, or keeps it to its owner; only the owner decides.
        Returns why not, or empty."""
        with self.changed:
            owner = ((self.stations.get(name) or {}).get("report") or {}).get("owner", "")
            if not owner:
                return f"{name} is everyone's already"
            if owner != person:
                return f"Only {owner} can change who uses {name}"
            self.station_shares[name] = public
            self._bump()
            return ""

    def station_report(self, name: str, report: dict, results: list[dict], wait_seconds: float) -> dict:
        """Takes a station's report and the outcomes of what it ran, and hands it what to run
        next, waiting a while for something when there is nothing yet."""
        with self.changed:
            previous = self.stations.get(name, {}).get("report")
            self.stations[name] = {"report": report, "seen": time.monotonic()}
            changed = previous != report
            for result in results:
                command = next((command for command in self.commands
                                if command["id"] == result.get("id") and command["station"] == name), None)
                if command is not None and command["state"] in ("queued", "sent"):
                    command["state"] = "done" if result.get("ok") else "failed"
                    command["message"] = str(result.get("message") or "")[:300]
                    changed = True
            if changed:
                self._bump_dashboard()

            def waiting() -> list[dict]:
                return [command for command in self.commands if command["station"] == name and command["state"] == "queued"]

            if wait_seconds > 0 and not waiting():
                self.changed.wait_for(lambda: bool(waiting()), timeout=min(wait_seconds, STATION_WAIT_SECONDS))
                self.stations[name]["seen"] = time.monotonic()
            commands = waiting()
            for command in commands:
                command["state"] = "sent"
                command["sent"] = time.monotonic()
            if commands:
                self._bump_dashboard()
            return {"boot": self.boot,
                    "commands": [{"id": command["id"], "action": command["action"], "args": command["args"]}
                                 for command in commands]}

    def starting_agents(self, station: str) -> list[str]:
        """Agents the dashboard asked this station to start that it hasn't finished starting."""
        with self.changed:
            self._expire_commands()
            return [str(command["args"].get("name") or "") for command in self.commands
                    if command["station"] == station and command["action"] == "agent.start"
                    and command["state"] in ("queued", "sent")]

    def queue_command(self, station: str, action: str, args: dict) -> dict:
        with self.changed:
            command = {"id": self.next_command, "station": station, "action": action, "args": args,
                       "state": "queued", "message": "", "created": time.monotonic()}
            self.next_command += 1
            self.commands = (self.commands + [command])[-COMMANDS_KEPT:]
            self._bump_dashboard()
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} dashboard asked {station} to {action} {json.dumps(args)}",
                  flush=True)
            return _public_command(command, time.monotonic())

    def _expire_commands(self) -> None:
        now = time.monotonic()
        for command in self.commands:
            if command["state"] == "queued" and now - command["created"] > PICKUP_SECONDS:
                command["state"], command["message"] = "failed", f"{command['station']} didn't pick this up"
            elif command["state"] == "sent" and now - command["sent"] > ANSWER_SECONDS:
                command["state"], command["message"] = "failed", f"No answer from {command['station']}"

    # The dashboard ----------------------------------------------------------------------

    def dashboard(self, known_version: int, wait: bool, trusted: bool) -> dict:
        with self.changed:
            if wait:
                self.changed.wait_for(lambda: self.dashboard_version != known_version, timeout=DASHBOARD_POLL_SECONDS)
            self._expire_commands()
            now = time.monotonic()
            participants = self._participants()
            stations = self._fresh_stations()
            spectator = self._spectator_info(stations, now)
            agents = self._agents_with_age()
            agent_names = set(agents) | {name for name, kind in participants.items() if kind == "agent"}
            # Whom each person in VR is talking with, the other way round from how agents report it
            talking = {status["talking_with"]: name for name, status in agents.items() if status.get("talking_with")}
            people = [{"name": name, "kind": kind, "talking_with": talking.get(name, "")}
                      for name, kind in sorted(participants.items())
                      if name not in agent_names and kind != "spectator" and name != spectator["name"]]
            reply = {
                "version": self.dashboard_version,
                "trusted": trusted,
                # Whether any editor in the session says who is in it; without that, nobody is known
                "roster": self._has_roster(),
                "people": people,
                "agents": self._dashboard_agents(agents, participants, stations),
                "spectator": spectator,
                "stations": [{**_public_station(name, station, now),
                              "public": not station["report"].get("owner") or bool(self.station_shares.get(name))}
                             for name, station in sorted(stations.items())],
                "commands": [_public_command(command, now) for command in self.commands[-COMMANDS_SHOWN:]],
            }
            if trusted:
                reply["session"] = {"code": self.code, "address": self.address}
            return reply

    def _dashboard_agents(self, agents: dict, participants: dict, stations: dict) -> list[dict]:
        has_roster = self._has_roster()
        running_on = {agent["name"]: name for name, station in stations.items()
                      for agent in station["report"].get("agents", [])}
        listed = []
        for name in sorted(set(agents) | {name for name, kind in participants.items() if kind == "agent"}):
            status = agents.get(name, {})
            state = agent_state(status or None, status.get("age", 0.0))
            in_session = name in participants if has_roster else None
            # Like the Agents tab, an agent gone from the session and quiet for long drops off
            if state == "away" and not in_session and name not in running_on:
                continue
            appearance = status.get("appearance") or {}
            color = next((appearance.get(key) for key in ("eye_color", "glow_color")
                          if isinstance(appearance.get(key), str) and COLOR.fullmatch(appearance[key])), "")
            audience = self.audiences.get(name) or {}
            listed.append({"name": name, "state": state, "step": status.get("step") or "", "task": status.get("task") or "",
                           "age": status.get("age"), "call": status.get("call", ""),
                           "owner": audience.get("owner", ""), "public": bool(audience.get("public", True)),
                           "speaking": bool(status.get("speaking")), "talking_with": status.get("talking_with", ""),
                           "in_session": in_session, "station": running_on.get(name, ""), "color": color})
        return listed

    def _spectator_info(self, stations: dict, now: float) -> dict:
        report = self.spectator_report if self._spectator_running() else None
        station = next(((name, entry["report"]["spectator"]) for name, entry in stations.items()
                        if entry["report"].get("spectator", {}).get("running")), None)
        name = (report or {}).get("name") or (station[1].get("name") if station else "") or ""
        info = {"name": name, "running": report is not None, "wanted": dict(self.spectator_wanted),
                "station": station[0] if station else "", "call": self.calls.get(name, "") if name else "",
                "view": self.view_id if report is not None and self.spectator_view else 0}
        if report is not None:
            info.update({key: report.get(key) for key in ("target", "framing", "found", "mode", "level", "fps")})
            info["age"] = round(now - self.spectator_seen, 2)
        return info


def make_handler(hub: Hub):
    # Videos agents bring in from their machines, kept next to the state file (copresence_hub_media)
    media = MediaStore(hub.state_file.parent / "media" if hub.state_file else None)
    voice = VoiceLines(hub.audience)
    # Dashboards watching the spectator's pictures as they come
    view_streams = {"count": 0, "lock": threading.Lock()}

    class Handler(BaseHTTPRequestHandler):
        # Set for a web request that brought the code in a header, so the page's pictures get through
        code_cookie_due = False

        def log_message(self, format: str, *args) -> None:
            pass

        def end_headers(self) -> None:
            if self.code_cookie_due:
                self.send_header("Set-Cookie", f"{CODE_COOKIE}={hub.code}; Path=/; Max-Age={COOKIE_SECONDS}; "
                                 "HttpOnly; Secure; SameSite=Strict")
            super().end_headers()

        def _json(self, status: int, payload) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _raw_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise ValueError("body too large")
            return self.rfile.read(length)

        def _jpeg(self, data: bytes, cache: str = "max-age=3600") -> None:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _page(self, path: Path) -> None:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            # The page only talks to this hub
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _has_code(self, body: dict | None = None) -> bool:
            given = self.headers.get(CODE_HEADER) or str((body or {}).get("code", ""))
            return _code_matches(given, hub.code)

        def _is_trusted(self, body: dict | None = None) -> bool:
            if self.client_address[0] in LOCAL_ADDRESSES and not self._from_web():
                return True
            return self._has_code(body)

        def _from_web(self) -> bool:
            """Whether the request came in from the web through the tunnel, which hands it over as
            if it were from this machine, rather than from here, the network or the relay."""
            return bool(self.headers.get(WEB_CLIENT_HEADER)) or not _is_plain_host(self.headers.get("Host", ""))

        def _receive_views(self) -> None:
            """Takes the spectator's pictures off its long request as each one arrives whole."""
            self.connection.settimeout(VIEW_STREAM_IDLE_SECONDS)
            self.close_connection = True
            pending = bytearray()
            try:
                for chunk in read_chunks(self.rfile):
                    pending.extend(chunk)
                    while len(pending) >= VIEW_LENGTH.size:
                        (size,) = VIEW_LENGTH.unpack_from(pending)
                        if size > MAX_VIEW_BYTES:
                            raise ValueError("a picture is too big")
                        if len(pending) < VIEW_LENGTH.size + size:
                            break
                        picture = bytes(pending[VIEW_LENGTH.size:VIEW_LENGTH.size + size])
                        del pending[:VIEW_LENGTH.size + size]
                        if picture.startswith(JPEG_START):
                            hub.set_spectator_view(picture)
            except (OSError, ValueError):
                return
            try:
                self._json(200, {"ok": True})
            except OSError:
                pass

        def _stream_views(self) -> None:
            """The spectator's pictures as they come, as a stream an image tag plays."""
            with view_streams["lock"]:
                if view_streams["count"] >= MAX_VIEW_STREAMS:
                    self._json(503, {"error": "too many people are watching the spectator"})
                    return
                view_streams["count"] += 1
            try:
                self.connection.settimeout(VIEW_STREAM_IDLE_SECONDS)
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={VIEW_BOUNDARY}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                sent = -1
                while True:
                    # Nothing new for a while sends the picture again, so the stream stays open
                    sent, picture = hub.next_view(sent, VIEW_STREAM_REPEAT_SECONDS)
                    if picture is None:
                        continue
                    self.wfile.write(f"--{VIEW_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(picture)}\r\n\r\n"
                                     .encode() + picture + b"\r\n")
                    self.wfile.flush()
            except OSError:
                return
            finally:
                with view_streams["lock"]:
                    view_streams["count"] -= 1

        def _refused_from_web(self, path: str, reading: bool) -> bool:
            """From the web, everything but the page needs the code in a header, or for reading, in
            the cookie a header with it got. Answers a refused request and returns True."""
            self.code_cookie_due = False
            if not self._from_web() or path in PAGE_PATHS:
                return False
            if self._has_code():
                self.code_cookie_due = True
                return False
            if reading and _code_matches(_cookie(self.headers.get("Cookie", ""), CODE_COOKIE), hub.code):
                return False
            self._json(403, {"error": "wrong session code"})
            return True

        def _viewer_trusted(self) -> bool:
            """Whether the dashboard may show this viewer the session code: they gave it, or
            they're on this machine, or came through the relay, which asked them for it. A web
            page under a domain name that points at this machine can't read it."""
            if self._has_code():
                return True
            return self.client_address[0] in LOCAL_ADDRESSES and _is_plain_host(self.headers.get("Host", ""))

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if self._refused_from_web(url.path, reading=True):
                return
            query = parse_qs(url.query)
            try:
                known_version = int(query.get("version", ["-1"])[0])
            except ValueError:
                known_version = -1
            if url.path in PAGE_PATHS:
                self._page(DASHBOARD_FILE)
            elif url.path == "/api/dashboard":
                self._json(200, hub.dashboard(known_version, known_version >= 0, self._viewer_trusted()))
            elif url.path == "/api/spectator":
                self._json(200, hub.spectator_state(known_version, wait=known_version >= 0))
            elif url.path == "/api/spectator/view.mjpg":
                self._stream_views()
            elif url.path == "/api/spectator/view.jpg":
                with hub.changed:
                    data = hub.spectator_view
                if data is None:
                    self._json(404, {"error": "no picture from the spectator yet"})
                else:
                    self._jpeg(data, "no-store")
            elif url.path == "/api/state":
                after_id = int(query.get("after", ["0"])[0])
                self._json(200, hub.state(after_id, known_version, wait=known_version >= 0))
            elif url.path == "/api/agents":
                # Editors from before Agent Chat only read agent status
                state = hub.state(hub.next_message_id, known_version, wait=known_version >= 0)
                self._json(200, {"version": state["version"], "agents": state["agents"]})
            elif url.path.startswith("/api/annotations/"):
                # /api/annotations/<message id>/<full or detail>.jpg
                parts = unquote(url.path).split("/")[3:]
                data = None
                if len(parts) == 2 and parts[0].isdigit() and parts[1].endswith(".jpg"):
                    data = hub.image(int(parts[0]), parts[1][:-4])
                if data is None:
                    self._json(404, {"error": "no such picture"})
                else:
                    self._jpeg(data)
            elif url.path.startswith(MEDIA_PREFIX + "/"):
                media.serve(self, url.path)
            elif url.path.startswith(VOICE_PREFIX):
                # What people say to agents, and their answers, are for the session only
                if not self._is_trusted():
                    self._json(403, {"error": "wrong session code"})
                else:
                    voice.get(self, url.path[len(VOICE_PREFIX):], query)
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if self._refused_from_web(path, reading=False):
                return
            if path.startswith(VOICE_PREFIX):
                # A stream that lasts as long as the conversation, so it isn't read as one body
                if not self._is_trusted():
                    self._json(403, {"error": "wrong session code"})
                else:
                    voice.post(self, path[len(VOICE_PREFIX):], parse_qs(urlparse(self.path).query))
                return
            if path == MEDIA_PREFIX:
                # A video is too big for a JSON body, so it comes as the body itself
                if not self._is_trusted():
                    self._json(403, {"error": "wrong session code"})
                else:
                    self._json(*media.receive(self, parse_qs(urlparse(self.path).query).get("name", [""])[0]))
                return
            if path == "/api/spectator/views":
                # The spectator's pictures one after another, for as long as it films, up one long
                # request: through the relay, a request for each would come too slowly
                if not self._is_trusted():
                    self._json(403, {"error": "wrong session code"})
                elif "chunked" not in self.headers.get("Transfer-Encoding", "").lower():
                    self._json(411, {"error": "the pictures are sent chunked"})
                else:
                    self._receive_views()
                return
            try:
                raw = self._raw_body()
            except ValueError:
                self._json(400, {"error": "body too large"})
                return
            if path == "/api/spectator/view":
                # A small picture of what the spectator shows, sent as it is
                if not self._is_trusted():
                    self._json(403, {"error": "wrong session code"})
                elif len(raw) > MAX_VIEW_BYTES or not raw.startswith(JPEG_START):
                    self._json(400, {"error": "the view must be a JPEG picture under 2 MB"})
                else:
                    hub.set_spectator_view(raw)
                    self._json(200, {"ok": True})
                return
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("not an object")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "invalid JSON"})
                return

            if path == "/api/control":
                # Whatever the dashboard changes needs the code, from this machine too
                if not self._has_code(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    self._json(*control(hub, body))
            elif path == "/api/roster":
                reporter = _clean_name(body.get("reporter"))
                participants = _participants(body.get("participants"))
                if not reporter or participants is None:
                    self._json(400, {"error": "a reporter and participants ([{name, kind}]) are required"})
                elif not self._is_trusted(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    hub.set_roster(reporter, participants)
                    self._json(200, {"ok": True})
            elif path == "/api/spectator/status":
                report = _spectator_report(body)
                if report is None:
                    self._json(400, {"error": "the spectator's name is required"})
                elif not self._is_trusted(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    hub.set_spectator_report(report)
                    self._json(200, {"ok": True})
            elif path == "/api/stations":
                name = _clean_name(body.get("station"))
                if not name:
                    self._json(400, {"error": "station is required"})
                elif not self._is_trusted(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    results = body.get("results") if isinstance(body.get("results"), list) else []
                    try:
                        wait_seconds = float(body.get("wait") or 0)
                    except (TypeError, ValueError):
                        wait_seconds = 0.0
                    self._json(200, hub.station_report(name, _station_report(body), results[:COMMANDS_KEPT],
                                                       wait_seconds))
            elif path == "/api/messages":
                author = str(body.get("author", "")).strip()[:MAX_NAME_LENGTH]
                text = str(body.get("text", "")).strip()
                role = "agent" if body.get("role") == "agent" else "human"
                if not author or not text:
                    self._json(400, {"error": "author and text are required"})
                elif not self._is_trusted(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    to = str(body.get("to") or "").strip()[:MAX_NAME_LENGTH]
                    kind = str(body.get("kind") or "")
                    source = str(body.get("source") or "").strip()[:MAX_SOURCE_LENGTH]
                    source_event_id = body.get("source_event_id", "")
                    if kind not in MESSAGE_KINDS:
                        self._json(400, {"error": f"kind must be one of: {', '.join(repr(k) for k in MESSAGE_KINDS)}"})
                        return
                    if not isinstance(source_event_id, str) or len(source_event_id) > MAX_EVENT_ID_LENGTH:
                        self._json(400, {"error": f"source_event_id must be text of at most {MAX_EVENT_ID_LENGTH} characters"})
                        return
                    try:
                        annotation, images = _annotation(body)
                    except ValueError as error:
                        self._json(400, {"error": str(error)})
                        return
                    together = body.get("together") if isinstance(body.get("together"), list) else []
                    together = [name for name in (_clean_name(item) for item in together[:MAX_TOGETHER]) if name]
                    self._json(200, hub.add_message(author, role, text, to, list(body.get("selection") or []),
                                                    annotation, images, kind, source, source_event_id, together))
            elif path == "/api/claims":
                # An agent taking on a request that went to several agents, so only one of them does it
                agent = _clean_name(body.get("agent"))
                group = body.get("group")
                if not agent or not isinstance(group, int) or isinstance(group, bool):
                    self._json(400, {"error": "agent and group (a message id) are required"})
                elif not self._is_trusted(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    self._json(200, hub.claim(group, agent))
            elif path == "/api/status":
                agent = str(body.get("agent", "")).strip()[:MAX_NAME_LENGTH]
                if not agent:
                    self._json(400, {"error": "agent is required"})
                    return
                status = {key: body.get(key) for key in STATUS_FIELDS}
                status["step"] = str(status["step"] or "")[:200]
                hub.set_status(agent, status)
                self._json(200, {"ok": True})
            elif path == "/api/voice":
                # Like status, this only changes how the agent's head looks
                agent = str(body.get("agent", "")).strip()[:MAX_NAME_LENGTH]
                if not agent:
                    self._json(400, {"error": "agent is required"})
                    return
                hub.set_speaking(agent, bool(body.get("speaking")))
                self._json(200, {"ok": True})
            elif path == "/api/conversation":
                # Someone talking with an agent from VR, which keeps the agent next to them between tasks
                agent = str(body.get("agent", "")).strip()[:MAX_NAME_LENGTH]
                person = str(body.get("person") or "").strip()[:MAX_NAME_LENGTH]
                if not agent:
                    self._json(400, {"error": "agent is required"})
                elif not self._is_trusted(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    hub.set_conversation(agent, person)
                    self._json(200, {"ok": True})
            elif path == "/api/call":
                agent = str(body.get("agent", "")).strip()[:MAX_NAME_LENGTH]
                state = body.get("state")
                if not agent or state not in CALL_STATES:
                    self._json(400, {"error": f"agent and a state ({', '.join(CALL_STATES)}) are required"})
                elif not self._is_trusted(body):
                    self._json(403, {"error": "wrong session code"})
                else:
                    hub.set_call(agent, state)
                    self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})

    return Handler


def agent_state(status: dict | None, age: float) -> str:
    """What an agent is up to now, from its last report and how long ago that was, the way
    editors show it on its bubble (ghost_labels.effective_state in the CoPresence plugin)."""
    if not status or age > AGENT_STALE_SECONDS:
        return "away"
    state = status.get("state") or "idle"
    # After a failed step the agent is working out what to do next
    if state == "error" and age > AGENT_ERROR_SECONDS:
        return "thinking"
    if state in ("saying", "done") and age > AGENT_DONE_SECONDS:
        return "idle"
    return state


def control(hub: Hub, body: dict) -> tuple[int, dict]:
    """Carries out what the dashboard asked for, once its session code checked out: where the
    spectator looks right away, and starting and stopping things through the station that runs
    them. Returns the HTTP status and the reply."""
    action = body.get("action")
    if action == "spectator.follow":
        target = _clean_name(body.get("target"))
        framing = body.get("framing") or FRAMINGS[0]
        if framing not in FRAMINGS:
            return 400, {"error": f"framing is one of: {', '.join(FRAMINGS)}"}
        return 200, {"ok": True, "spectator": hub.follow(target, framing)}
    if action == "spectator.nudge":
        move, turn = _vector(body.get("move"), 3), _vector(body.get("turn"), 2)
        if move is None or turn is None:
            return 400, {"error": "move is [forward, right, up] and turn is [yaw, pitch], each from -1 to 1"}
        return 200, {"ok": True, "nudge": hub.nudge(move, turn)}
    if action == "spectator.start":
        station = _clean_name(body.get("station"))
        report = hub.station(station) if station else None
        if report is None:
            return 409, {"error": f"{station or 'That machine'} isn't connected to the dashboard"}
        refusal = _machine_refusal(hub, station, body)
        if refusal:
            return 403, {"error": refusal}
        if not report.get("can", {}).get("spectator"):
            return 409, {"error": f"{station} can't run the spectator"}
        if hub.spectator_station() or hub.dashboard(-1, False, False)["spectator"]["running"]:
            return 409, {"error": "The spectator is already running"}
        return 200, {"ok": True, "command": hub.queue_command(station, action, {})}
    if action in ("spectator.stop", "spectator.meet", "spectator.leave"):
        station = hub.spectator_station()
        if station is None:
            return 409, {"error": "No station runs the spectator; use Tools/copresence_spectator.py where it runs"}
        args = {}
        if action == "spectator.meet":
            match = MEET_LINK.fullmatch(str(body.get("link") or "").strip())
            if match is None:
                return 400, {"error": "Only Google Meet links work so far, like https://meet.google.com/abc-defg-hij"}
            args["link"] = f"https://meet.google.com/{match.group(1)}"
        return 200, {"ok": True, "command": hub.queue_command(station, action, args)}
    if action == "agent.start":
        name, brain = str(body.get("name") or "").strip(), body.get("brain")
        station = _clean_name(body.get("station"))
        if not AGENT_NAME.fullmatch(name):
            return 400, {"error": "An agent's name has letters, digits, spaces, dots, dashes or underscores, "
                                  "up to 60 of them"}
        if brain not in BRAINS:
            return 400, {"error": f"brain is one of: {', '.join(BRAINS)}"}
        model, effort = str(body.get("model") or "").strip(), str(body.get("effort") or "").strip()
        if model and not AGENT_MODEL.fullmatch(model):
            return 400, {"error": "A model name has letters, digits, dots, dashes or underscores"}
        if effort and effort not in EFFORTS:
            return 400, {"error": f"effort is one of: {', '.join(EFFORTS)}"}
        report = hub.station(station) if station else None
        if report is None:
            return 409, {"error": f"{station or 'That machine'} isn't connected to the dashboard"}
        refusal = _machine_refusal(hub, station, body)
        if refusal:
            return 403, {"error": refusal}
        if brain not in report.get("can", {}).get("brains", []):
            return 409, {"error": f"{station} has no {brain} to think with"}
        running = [agent["name"] for agent in report.get("agents", [])]
        # Starts it hasn't finished yet count too, so pressing twice doesn't start one too many
        starting = [entry for entry in hub.starting_agents(station) if entry not in running]
        if name in starting:
            return 409, {"error": f"{name} is already starting on {station}"}
        capacity = report.get("can", {}).get("max_agents", 1)
        # A station that says 0 takes any number
        if capacity and len(running) + len(starting) >= capacity:
            takes = "one agent" if capacity == 1 else f"{capacity} agents"
            return 409, {"error": f"{station} already has {', '.join(running + starting)}; it takes {takes} at a time"}
        state = hub.dashboard(-1, False, False)
        if any(entry["name"] == name for entry in state["agents"] + state["people"]):
            return 409, {"error": f"{name} is already in the session"}
        # Brought in from someone's editor, the agent is theirs; from the web dashboard, everyone's
        owner = _clean_name(body.get("person"))
        if owner:
            hub.claim_agent(name, owner)
        # Without a model or an effort the agent thinks with its brain's defaults
        args = {"name": name, "brain": brain, **({"model": model} if model else {}), **({"effort": effort} if effort else {})}
        return 200, {"ok": True, "command": hub.queue_command(station, action, args)}
    if action == "station.audience":
        station, person, public = _clean_name(body.get("station")), _clean_name(body.get("person")), body.get("public")
        if not station or not person or not isinstance(public, bool):
            return 400, {"error": "station, person and public (true or false) are required"}
        refusal = hub.set_station_audience(station, person, public)
        if refusal:
            return 403, {"error": refusal}
        owner, public = hub.station_audience(station)
        return 200, {"ok": True, "owner": owner, "public": public}
    if action == "agent.audience":
        name, person, public = _clean_name(body.get("name")), _clean_name(body.get("person")), body.get("public")
        if not name or not person or not isinstance(public, bool):
            return 400, {"error": "name, person and public (true or false) are required"}
        refusal = hub.set_audience(name, person, public)
        if refusal:
            return 403, {"error": refusal}
        owner, public = hub.audience(name)
        return 200, {"ok": True, "owner": owner, "public": public}
    if action == "agent.stop":
        name = _clean_name(body.get("name"))
        state = hub.dashboard(-1, False, False)
        # The machine's own report, so an agent still on its way into the session can be stopped too
        station = next((entry["name"] for entry in state["stations"]
                        if any(agent["name"] == name for agent in entry["agents"])), None)
        if station is None:
            return 409, {"error": f"No station runs {name or 'that agent'}; close its editor where it runs"}
        return 200, {"ok": True, "command": hub.queue_command(station, action, {"name": name})}
    return 400, {"error": "unknown action"}


def _machine_refusal(hub: Hub, station: str, body: dict) -> str:
    """Why this person may not start things on that machine: it is someone's own and they haven't
    shared it. Agents there think with its owner's Claude or Codex account. Empty when they may."""
    owner, public = hub.station_audience(station)
    if public or _clean_name(body.get("person")) == owner:
        return ""
    return f"Only {owner} can start agents on {station}"


def _clean_name(value) -> str:
    """A participant or machine name as given, without anything that isn't printable."""
    return "".join(character for character in str(value or "") if character.isprintable()).strip()[:MAX_NAME_LENGTH]


def _vector(value, size: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != size:
        return None
    try:
        numbers = [float(number) for number in value]
    except (TypeError, ValueError):
        return None
    if not all(-1.0 <= number <= 1.0 for number in numbers):
        return None
    return numbers


def _participants(value) -> list[dict] | None:
    """Who an editor sees in the session, as [{"name", "kind"}], or None if it isn't that."""
    if not isinstance(value, list) or len(value) > MAX_PARTICIPANTS:
        return None
    participants = []
    for entry in value:
        if not isinstance(entry, dict):
            return None
        name, kind = _clean_name(entry.get("name")), entry.get("kind") or ""
        if not name or kind not in PARTICIPANT_KINDS:
            return None
        participants.append({"name": name, "kind": kind})
    return sorted(participants, key=lambda participant: participant["name"])


def _spectator_report(body: dict) -> dict | None:
    name = _clean_name(body.get("name"))
    if not name:
        return None
    try:
        fps = round(float(body.get("fps") or 0), 1)
    except (TypeError, ValueError):
        fps = 0.0
    return {"name": name, "target": _clean_name(body.get("target")),
            "framing": body.get("framing") if body.get("framing") in FRAMINGS else FRAMINGS[0],
            "found": bool(body.get("found")), "mode": str(body.get("mode") or "")[:20],
            "level": str(body.get("level") or "")[:200], "fps": fps}


def _station_report(body: dict) -> dict:
    """What a station says it can run and runs, kept to the fields the dashboard shows."""
    can = body.get("can") if isinstance(body.get("can"), dict) else {}
    agents = body.get("agents") if isinstance(body.get("agents"), list) else []
    spectator = body.get("spectator") if isinstance(body.get("spectator"), dict) else {}
    return {"platform": str(body.get("platform") or "")[:20],
            "owner": _clean_name(body.get("owner")),
            "can": {"brains": [brain for brain in can.get("brains", []) if brain in BRAINS],
                    # Why a brain can't think there, such as "not signed in"
                    "unavailable": {brain: str(problem)[:40] for brain, problem in (can.get("unavailable") or {}).items()
                                    if brain in BRAINS} if isinstance(can.get("unavailable"), dict) else {},
                    "spectator": bool(can.get("spectator")), "max_agents": _max_agents(can.get("max_agents"))},
            "agents": [{"name": _clean_name(agent.get("name"))} for agent in agents[:STATION_AGENTS_LISTED]
                       if isinstance(agent, dict) and _clean_name(agent.get("name"))],
            "spectator": {"running": bool(spectator.get("running")), "name": _clean_name(spectator.get("name"))}}


def _max_agents(value) -> int:
    """How many agents a station runs at once, as it says: 0 for no limit, and one when it doesn't
    say, as stations from before didn't."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return number if number >= 0 else 1


def _public_command(command: dict, now: float) -> dict:
    return {"id": command["id"], "station": command["station"], "action": command["action"],
            "args": command["args"], "state": command["state"], "message": command["message"],
            "age": round(now - command["created"], 1)}


def _public_station(name: str, station: dict, now: float) -> dict:
    return {"name": name, **station["report"], "age": round(now - station["seen"], 1)}


def _is_plain_host(host_header: str) -> bool:
    """Whether a request's Host names this machine by address or as localhost, rather than by a
    domain name that some web page could have pointed here."""
    host = host_header.strip()
    if host.startswith("["):
        host = host[1:host.find("]")] if "]" in host else ""
    elif host.count(":") == 1:
        host = host.split(":")[0]
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _code_matches(given: str, code: str) -> bool:
    return hmac.compare_digest(given.strip().upper().encode(), code.encode())


def _cookie(header: str, name: str) -> str:
    """One cookie's value from a request's Cookie header, or "" without it or when the header
    can't be read."""
    try:
        morsel = SimpleCookie(header).get(name)
    except CookieError:
        return ""
    return morsel.value if morsel is not None else ""


def _annotation(body: dict) -> tuple[dict | None, dict[str, bytes]]:
    """The drawing a posted message carries, if any: what it marks, and its pictures as JPEG
    bytes, sent base64 encoded under "annotation_images"."""
    annotation = body.get("annotation")
    if annotation is None:
        return None, {}
    if not isinstance(annotation, dict) or len(json.dumps(annotation)) > MAX_ANNOTATION_BYTES:
        raise ValueError("the annotation must be a JSON object under 64 KB")
    annotation.pop("images", None)
    images = {}
    for name, encoded in (body.get("annotation_images") or {}).items():
        if name not in ANNOTATION_IMAGES:
            raise ValueError(f"annotation images are named {', '.join(ANNOTATION_IMAGES)}")
        try:
            data = base64.b64decode(str(encoded), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("annotation images must be base64") from error
        if len(data) > MAX_IMAGE_BYTES or not data.startswith(JPEG_START):
            raise ValueError("annotation images must be JPEG pictures under 4 MB")
        images[name] = data
    return annotation, images


def _detach(log_path: Path | None) -> None:
    # Two forks with a new session in between leave the hub owned by launchd, outside the
    # process tree and process group of whatever started it
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    output = os.open(log_path or os.devnull, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(output, 1)
    os.dup2(output, 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)


def _session_code(code_file: Path | None) -> str:
    if code_file and code_file.exists() and code_file.read_text().strip():
        return code_file.read_text().strip().upper()
    # Unambiguous characters only, since people may type it in
    alphabet = "".join(character for character in string.ascii_uppercase + string.digits if character not in "O0I1L")
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    if code_file:
        code_file.parent.mkdir(parents=True, exist_ok=True)
        code_file.write_text(code)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--code-file", type=Path, help="Keeps the session code for the host's own editor and agents")
    parser.add_argument("--state-file", type=Path, help="Keeps the chat, the agents' status and their calls across restarts")
    parser.add_argument("--address", default="",
                        help="What people join with, like multiunreal.geotech-hub.com, for the dashboard's join command")
    parser.add_argument("--log", type=Path, help="Where to write output when detached")
    parser.add_argument("--detach", action="store_true",
                        help="Keep running after the shell or app that started it exits")
    arguments = parser.parse_args()

    if arguments.detach:
        _detach(arguments.log)

    code = _session_code(arguments.code_file)
    hub = Hub(code, arguments.state_file, arguments.address)
    server = ThreadingHTTPServer((arguments.bind, arguments.port), make_handler(hub))
    server.daemon_threads = True
    print(f"CoPresence hub on port {arguments.port}, session code {code}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
