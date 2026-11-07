"""Voice lines through the session hub (Tools/copresence_hub.py). People talk with an agent
out loud from their editor or from VR, and the agent's voice hears and answers them. The voice
runs on the agent's own machine (Tools/copresence_agent_voice.py), so only machines that run
agents need an OpenAI key.

Each person talking with an agent has a line to it. The voice helper next to their editor
(Tools/copresence_voice.py) opens the line, sends their microphone up it and plays what comes
down. The agent's voice keeps one stream each way for all of its lines:

    GET  /api/voice/person?agent=A&person=P   the line's way down: what the agent's voice sends,
                                              starting with the line's id; 409 when A has no voice,
                                              403 when A is private to someone other than P
    POST /api/voice/person?line=L             the line's way up: the person's microphone, and
                                              which agent they follow
    GET  /api/voice/agent?agent=A&instance=I  what people send A's voice, from all of A's lines
    POST /api/voice/agent?agent=A&instance=I  what A's voice sends people, line by line
    GET  /api/voice/listen?person=P           what every public agent says out loud to anyone but
                                              P, so everyone in the session hears the agents they
                                              share; a private agent is heard by its owner only,
                                              and an agent P talks with too only on P's own line

Both ways carry frames: a 4-byte length, a JSON header, then as many bytes of audio (16-bit
mono PCM at 24 kHz) as the header's "size" says. The ways up are chunked request bodies, so
they pass through the session's relay (Tools/copresence_relay.py) like any other request.

Frames by "kind":
- to the agent's voice: "lines" (the lines open when it connects), "open" and "close" (a person
  opened or hung up a line), "audio" (their microphone), "link" (which agent they follow,
  whether they muted, what they have selected), "ping"
- to a person: "line" (the line's id), "audio", "flush" (they talked over the voice, so drop
  what is waiting to play), "status" (what the voice is doing, and what each side said last),
  "closed" (the conversation ended, maybe with a problem), "ping"
- to a listener: "audio" and "flush", each naming the agent, and "ping"

The hub side needs nothing outside Python's standard library, like the hub itself.
"""

import json
import secrets
import struct
import threading
from collections import deque

VOICE_PREFIX = "/api/voice/"
HEADER = struct.Struct(">I")
MAX_HEADER_BYTES = 16 * 1024
MAX_AUDIO_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
# Each stream carries something at least this often, so both ends notice one that died
PING_SECONDS = 2.0
# A stream that carried nothing for this long has died
STREAM_IDLE_SECONDS = 15.0
# What waits for a reader that falls behind: past this, the oldest goes, the way a phone line
# drops sound rather than fall further and further behind
QUEUE_BYTES = 2 * 1024 * 1024
# When an agent's voice drops off, its lines wait this long for it to come back
VOICE_GONE_SECONDS = 15.0
MAX_LINES = 32
MAX_LISTENERS = 64
MAX_NAME_LENGTH = 60
# What a person may send up their line; the hub adds which line and person it came from
PERSON_KINDS = ("audio", "link", "ping")
# What an agent's voice may send a person
AGENT_KINDS = ("audio", "flush", "status", "closed", "ping")


def frame(header: dict, payload: bytes = b"") -> bytes:
    if payload:
        header = {**header, "size": len(payload)}
    data = json.dumps(header, separators=(",", ":")).encode()
    return HEADER.pack(len(data)) + data + payload


class FrameReader:
    """Whole frames out of a stream that arrives in pieces of any size."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[tuple[dict, bytes]]:
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= HEADER.size:
            (header_size,) = HEADER.unpack_from(self.buffer)
            if header_size > MAX_HEADER_BYTES:
                raise ValueError("a voice frame's header is too big")
            body_start = HEADER.size + header_size
            if len(self.buffer) < body_start:
                break
            header = json.loads(bytes(self.buffer[HEADER.size:body_start]))
            size = header.get("size", 0) if isinstance(header, dict) else -1
            if not isinstance(size, int) or not 0 <= size <= MAX_AUDIO_BYTES:
                raise ValueError("a voice frame is malformed")
            if len(self.buffer) < body_start + size:
                break
            frames.append((header, bytes(self.buffer[body_start:body_start + size])))
            del self.buffer[:body_start + size]
        return frames


def read_chunks(stream):
    """The pieces of a chunked request body, as they arrive, until its last chunk."""
    while True:
        size_line = stream.readline(1024)
        if not size_line:
            return
        size = int(size_line.split(b";")[0].strip() or b"0", 16)
        if size > MAX_CHUNK_BYTES:
            raise ValueError("a chunk is too big")
        if size == 0:
            # Trailers, if any, end with an empty line
            while stream.readline(1024).strip():
                pass
            return
        data = stream.read(size)
        stream.readline(1024)
        if len(data) < size:
            return
        yield data


class _Outbox:
    """Frames waiting for one reader."""

    def __init__(self):
        self.frames: deque[bytes] = deque()
        self.size = 0
        self.closed = False
        self.condition = threading.Condition()

    def put(self, data: bytes) -> None:
        with self.condition:
            if self.closed:
                return
            self.frames.append(data)
            self.size += len(data)
            while self.size > QUEUE_BYTES and len(self.frames) > 1:
                self.size -= len(self.frames.popleft())
            self.condition.notify_all()

    def take(self, timeout: float) -> list[bytes] | None:
        """What waits, after up to timeout seconds; None once closed with nothing left."""
        with self.condition:
            self.condition.wait_for(lambda: self.frames or self.closed, timeout=timeout)
            if not self.frames:
                return None if self.closed else []
            taken = list(self.frames)
            self.frames.clear()
            self.size = 0
            return taken

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class _Voice:
    """An agent's voice as the hub sees it: where frames for it wait while it is connected."""

    def __init__(self, instance: str):
        self.instance = instance
        self.outbox = _Outbox()
        self.connected = True


class _Line:
    def __init__(self, agent: str, person: str):
        self.id = secrets.token_hex(8)
        self.agent = agent
        self.person = person
        self.outbox = _Outbox()
        self.has_way_up = False
        # The person's last link frame, which a voice that comes back gets straight away
        self.last_link: bytes | None = None


class _Listener:
    """Someone hearing what agents say to other people."""

    def __init__(self, person: str):
        self.person = person
        self.outbox = _Outbox()


def _clean(value) -> str:
    return " ".join(str(value or "").split())[:MAX_NAME_LENGTH]


class VoiceLines:
    """The hub's end of voice: connects people's lines to agents' voices."""

    def __init__(self, audience=None):
        self.lock = threading.Lock()
        self.voices: dict[str, _Voice] = {}
        self.lines: dict[str, _Line] = {}
        self.listeners: list[_Listener] = []
        # The hub's word on each agent's owner and whether everyone may talk with it and hear it
        self.audience = audience or (lambda agent: ("", True))

    def get(self, handler, path: str, query: dict) -> None:
        if path == "agent":
            self._serve_voice(handler, _clean(query.get("agent", [""])[0]), query.get("instance", [""])[0][:64])
        elif path == "person":
            self._serve_person(handler, _clean(query.get("agent", [""])[0]), _clean(query.get("person", [""])[0]))
        elif path == "listen":
            self._serve_listener(handler, _clean(query.get("person", [""])[0]))
        else:
            handler._json(404, {"error": "not found"})

    def post(self, handler, path: str, query: dict) -> None:
        if "chunked" not in handler.headers.get("Transfer-Encoding", "").lower():
            handler._json(411, {"error": "voice streams are sent chunked"})
            return
        if path == "agent":
            self._from_voice(handler, _clean(query.get("agent", [""])[0]), query.get("instance", [""])[0][:64])
        elif path == "person":
            self._from_person(handler, query.get("line", [""])[0][:32])
        else:
            handler._json(404, {"error": "not found"})

    # An agent's voice ----------------------------------------------------------------

    def _serve_voice(self, handler, agent: str, instance: str) -> None:
        if not agent or not instance:
            handler._json(400, {"error": "agent and instance are required"})
            return
        with self.lock:
            current = self.voices.get(agent)
            if current is not None and current.connected and current.instance != instance:
                handler._json(409, {"error": f"another voice for {agent} is running"})
                return
            if current is not None:
                current.outbox.close()
            voice = self.voices[agent] = _Voice(instance)
            # A voice that comes back, or a new one, picks up the lines that waited for it
            waiting = [line for line in self.lines.values() if line.agent == agent]
            voice.outbox.put(frame({"kind": "lines", "lines": [{"line": line.id, "person": line.person} for line in waiting]}))
            for line in waiting:
                if line.last_link is not None:
                    voice.outbox.put(line.last_link)
        try:
            self._stream(handler, voice.outbox)
        finally:
            self._voice_left(agent, voice)

    def _voice_left(self, agent: str, voice: _Voice) -> None:
        with self.lock:
            if self.voices.get(agent) is not voice:
                return
            voice.connected = False
        timer = threading.Timer(VOICE_GONE_SECONDS, self._voice_gone, args=(agent, voice))
        timer.daemon = True
        timer.start()

    def _voice_gone(self, agent: str, voice: _Voice) -> None:
        with self.lock:
            if self.voices.get(agent) is not voice or voice.connected:
                return
            del self.voices[agent]
            gone = [line for line in self.lines.values() if line.agent == agent]
        for line in gone:
            self._end_line(line, problem=f"{agent}'s voice stopped", tell_voice=False)

    def _from_voice(self, handler, agent: str, instance: str) -> None:
        with self.lock:
            voice = self.voices.get(agent)
            current = voice is not None and voice.connected and voice.instance == instance
        if not current:
            handler._json(409, {"error": f"connect to /api/voice/agent as {agent}'s voice first"})
            return
        self._read_frames(handler, lambda header, payload: self._route_to_person(agent, header, payload))
        self._answer(handler)

    def _route_to_person(self, agent: str, header: dict, payload: bytes) -> None:
        kind = header.get("kind")
        if kind not in AGENT_KINDS or kind == "ping":
            return
        with self.lock:
            line = self.lines.get(str(header.get("line", "")))
        if line is None or line.agent != agent:
            return
        forwarded = {key: value for key, value in header.items() if key not in ("line", "size")}
        line.outbox.put(frame(forwarded, payload))
        if kind in ("audio", "flush") and self.audience(agent)[1]:
            # Everyone else hears an agent that is shared with them
            shared = frame({"kind": kind, "agent": agent}, payload)
            with self.lock:
                # Someone talking with the agent too hears its words for them on their own line, and
                # its words for others at the same time would sound like it repeats itself
                talking = {other.person for other in self.lines.values() if other.agent == agent}
                listeners = [listener for listener in self.listeners if listener.person not in talking]
            for listener in listeners:
                listener.outbox.put(shared)
        if kind == "closed":
            # The conversation ended; the person's way down closes after this frame
            self._end_line(line, tell_voice=False, already_told=True)

    # Someone hearing the agents ------------------------------------------------------

    def _serve_listener(self, handler, person: str) -> None:
        if not person:
            handler._json(400, {"error": "person is required"})
            return
        with self.lock:
            if len(self.listeners) >= MAX_LISTENERS:
                handler._json(503, {"error": "too many people are listening"})
                return
            listener = _Listener(person)
            self.listeners.append(listener)
        try:
            self._stream(handler, listener.outbox)
        finally:
            with self.lock:
                self.listeners.remove(listener)

    # A person's line -----------------------------------------------------------------

    def _serve_person(self, handler, agent: str, person: str) -> None:
        if not agent or not person:
            handler._json(400, {"error": "agent and person are required"})
            return
        owner, public = self.audience(agent)
        if not public and owner != person:
            handler._json(403, {"error": f"{agent} is private to {owner}"})
            return
        with self.lock:
            voice = self.voices.get(agent)
            if voice is None or not voice.connected:
                handler._json(409, {"error": f"{agent} has no voice running"})
                return
            if len(self.lines) >= MAX_LINES:
                handler._json(503, {"error": "too many people are talking with agents"})
                return
            line = _Line(agent, person)
            self.lines[line.id] = line
            line.outbox.put(frame({"kind": "line", "line": line.id}))
            voice.outbox.put(frame({"kind": "open", "line": line.id, "person": person}))
        try:
            self._stream(handler, line.outbox)
        finally:
            self._end_line(line)

    def _from_person(self, handler, line_id: str) -> None:
        with self.lock:
            line = self.lines.get(line_id)
            taken = line is None or line.has_way_up
            if not taken:
                line.has_way_up = True
        if taken:
            handler._json(409, {"error": "no such line, or it already has a way up"})
            return
        try:
            self._read_frames(handler, lambda header, payload: self._route_to_voice(line, header, payload))
        finally:
            # Hanging up: the person's way up ended
            self._end_line(line)
        self._answer(handler)

    def _route_to_voice(self, line: _Line, header: dict, payload: bytes) -> None:
        kind = header.get("kind")
        if kind not in PERSON_KINDS or kind == "ping":
            return
        forwarded = {key: value for key, value in header.items() if key not in ("line", "person", "size")}
        data = frame({**forwarded, "line": line.id, "person": line.person}, payload)
        with self.lock:
            if kind == "link":
                line.last_link = data
            voice = self.voices.get(line.agent)
            if voice is not None and voice.connected and line.id in self.lines:
                voice.outbox.put(data)

    def _end_line(self, line: _Line, problem: str = "", tell_voice: bool = True, already_told: bool = False) -> None:
        """Ends a line once, whichever end hung up first."""
        with self.lock:
            if self.lines.pop(line.id, None) is None:
                return
            voice = self.voices.get(line.agent)
            if tell_voice and voice is not None:
                voice.outbox.put(frame({"kind": "close", "line": line.id}))
        if not already_told:
            line.outbox.put(frame({"kind": "closed", "problem": problem}))
        line.outbox.close()

    # Streams -------------------------------------------------------------------------

    @staticmethod
    def _stream(handler, outbox: _Outbox) -> None:
        """Sends what arrives in outbox until it closes or the reader goes away."""
        handler.connection.settimeout(STREAM_IDLE_SECONDS)
        handler.close_connection = True
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Cache-Control", "no-store")
        # No length: the stream ends when the connection closes
        handler.end_headers()
        try:
            while True:
                taken = outbox.take(PING_SECONDS)
                if taken is None:
                    return
                handler.wfile.write(b"".join(taken) or frame({"kind": "ping"}))
        except OSError:
            return
        finally:
            outbox.close()

    @staticmethod
    def _answer(handler) -> None:
        """The reply to a way up once it ended, if the sender is still there to read it."""
        try:
            handler._json(200, {"ok": True})
        except OSError:
            pass

    @staticmethod
    def _read_frames(handler, deliver) -> None:
        """Hands each frame of a chunked request body to deliver, until the body ends or stalls."""
        handler.connection.settimeout(STREAM_IDLE_SECONDS)
        handler.close_connection = True
        reader = FrameReader()
        try:
            for chunk in read_chunks(handler.rfile):
                for header, payload in reader.feed(chunk):
                    deliver(header, payload)
        except (OSError, ValueError):
            return
