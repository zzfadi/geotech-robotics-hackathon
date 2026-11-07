"""Lets a CoPresence agent hear and speak in a Google Meet call (used by copresence_call.py).

- Ears: Meet's live captions, read from the call page, give the words and who said them,
  for free. The call's audio, read in the page too, says the moment someone starts or
  stops talking, sooner than captions can, so the agent stops for people quickly. The
  call's chat is read as well: people can type to the agent, and type commands.
- Deciding: everything said goes to the shared voice core (copresence_voice_core.py).
  A line that calls the agent by name is answered at once. Otherwise OpenAI Decisions
  scores whether the line is meant for it; people talking to each other are left alone.
  Every score is logged so the threshold can be tuned.
- Taking turns: one answer at a time. What people ask while the agent talks waits in a
  queue and is answered in order. Talking over the agent stops it; "mm hmm" doesn't.
- Commands: saying "stop" (alone, not inside a sentence) stops the agent talking and drops
  the work it is doing; "quiet" or "hold on" only stops it talking. In the chat, "stop",
  "quiet", "mute" (stop listening to the call), "listen" and "/help" work the same way.
- Voice: an OpenAI Realtime session gets the call as speaker-labelled text and answers in
  the agent's voice, which plays into the call through the page's microphone track. It
  reconnects by itself and keeps only recent lines, so a long call stays fast and cheap.
- Hands: work in the level goes to the agent through Agent Chat, as a message from the
  person who asked, marked as coming from the call; such runs can't run code or join
  calls. What the agent says back, the voice tells the call when there is a pause.
- Face: with a MetaHuman avatar in the agent's level, its mouth moves with the voice, live
  (Plugins/CoPresence/.../voice_face.py). The voice plays a little later so the call sees
  the mouth and hears the words together, since the picture reaches the call later.
- Timing: each answered turn logs when its words were final, when it was decided, when
  the first audio went out and when it was interrupted, in milliseconds.
"""

import asyncio
import base64
import json
import os
import re
import time
import uuid
from datetime import datetime

import httpx

import copresence_voice_core as core
from copresence_agent_voice import openai_key

REALTIME_MODEL = "gpt-realtime-2.1"
DECISIONS_MODEL = core.DECISIONS_MODEL
# Kept for older callers; the voice core decides which voice an agent has
REALTIME_VOICES = core.REALTIME_VOICES
# A turn is finished once its caption hasn't changed for this long; sooner when it ends a sentence
SETTLE_SECONDS = 1.0
SENTENCE_SETTLE_SECONDS = 0.5
# The agent answers when Decisions puts "respond" at least this likely; a little less just
# after it spoke, when a reply is likely meant for it
RESPOND_THRESHOLD = 0.6
FOLLOW_UP_THRESHOLD = 0.45
FOLLOW_UP_SECONDS = 10.0
RAISE_HAND_THRESHOLD = 0.5
RAISE_HAND_GAP_SECONDS = 90.0
# The agent counts as busy with the call's work this long after it was last handed some
WORK_FOR_CALL_SECONDS = 600.0
# Turns the decision and the voice get as context
CONTEXT_TURNS = core.CONTEXT_TURNS
# Kept for older callers; the voice core decides what counts as talking over the agent
INTERRUPT_WORDS = core.INTERRUPT_WORDS
# Someone talked in the call this recently: wait before speaking
QUIET_SECONDS = 1.0
AUDIO_QUIET_SECONDS = 0.5
# Sound in the call with no captions for this long is noise (music, a fan), not someone
# talking: it stops counting, so it can't keep the agent quiet forever
NOISE_SECONDS = 2.5
# An answer Realtime hasn't started after this long is given up
RESPONSE_START_SECONDS = 10.0
AUDIO_STATS_SECONDS = 60.0
# With an avatar's face to move: the voice plays this much later than it could, and the
# mouth shows this much before the voice plays, since the picture reaches the call later
# than the sound. Both are guesses until measured in a real call; tune them here
FACE_AUDIO_LEAD_SECONDS = float(os.environ.get("COPRESENCE_FACE_AUDIO_LEAD", "0.3"))
FACE_VIDEO_LAG_SECONDS = float(os.environ.get("COPRESENCE_FACE_VIDEO_LAG", "0.15"))
# A result waits this long for a pause; then it goes to the call's chat instead
RESULT_WAIT_SECONDS = 45.0
CAPTIONS_RETRY_SECONDS = 5.0
CHAT_RETRY_SECONDS = 10.0
POLL_SECONDS = 0.2
# Realtime sessions end after an hour; a new one starts before that, between answers
SESSION_SECONDS = 55 * 60
RECONNECT_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)
# Lines kept in the Realtime conversation; older ones are deleted, and a new session gets
# the last few as a summary
CONVERSATION_ITEMS = 40
RESEED_LINES = 20
# A cap on one answer, about 40 seconds of speech; the instructions keep answers short.
# Thinking counts too, so a tighter cap cuts off short answers (400 did)
MAX_OUTPUT_TOKENS = 1500
# Names Meet gives the bot's own captions and chat messages
SELF_NAMES = ("you",)
SPEAKING_REPORT_SECONDS = 3.0

REQUEST_NOTE = "(Said in the Google Meet call; the captions can contain mistakes.)"

INSTRUCTIONS = """\
You are {agent}, an AI agent in a Google Meet call with people. You also work in a shared \
Unreal Engine level, and the call sees you working there through your camera. Speak as \
{agent}, in the first person, briefly and casually, like a teammate on a call. One or two \
short sentences unless someone asks for more.

You get the call as captions and chat, one line per turn, like "[Brian]: can you add a \
tree?". Captions can mishear words, especially names of assets and actors. Several people \
may talk; answer only the line you are asked to answer, and only once.

You can't see or change the level yourself. Your hands are a separate worker, reached \
only through the delegate tool. Whenever someone asks you to do, change, add, move, \
remove, find, check or look at anything in the level, or asks what is there, first \
say a few words like "on it" or "sure, adding that now", then call delegate with their \
request in their words. If someone asks for more while you are working, \
delegate that too; it is done after the current work. If someone clearly wants you to \
stop or cancel the work you are doing (not just to stop talking), call stop_work, and if \
they asked for something else instead, delegate that after. Never mention tools, hands, \
workers, queues or passing requests along: just talk like a teammate who is doing it. \
Your camera shows one of two scenes: "avatar", your face in a studio, and "build", the \
building level where the camera follows your work. When someone wants to see you, or to \
watch the building, call show_scene, say a few words like "switching over", and carry on. \
Never say work is done until a result says so. Results arrive as lines starting \
"[Result]"."""

ANSWER_FOCUS = "\n\nNow answer this line from {speaker}, briefly: {text}"
ACK_FOCUS = "\n\nSay in a few words that you're on it."
STOPPED_FOCUS = "\n\nSay in a few words that you stopped."
RESULT_FOCUS = "\n\nTell the call this result from your work, in your own words, once and briefly: {text}"
STATUS_FOCUS = "\n\nWhat you are doing right now (background only, don't announce it): {status}"

VOICE_SCRIPT = """
(() => {
  if (window.__copresenceMouth) {
    return;
  }
  // The agent's microphone: speech the voice sends plays into one WebAudio track, which
  // stays silent in between, so Meet always has a live track
  let audio = null;
  let destination = null;
  let playhead = 0;
  let sources = [];
  // How long after now the voice starts at the soonest; longer while a face must keep up
  let lead = 0.05;
  function ensure() {
    if (!audio) {
      audio = new AudioContext({ sampleRate: 24000 });
      destination = audio.createMediaStreamDestination();
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      gain.gain.value = 0;
      oscillator.connect(gain).connect(destination);
      oscillator.start();
    }
    if (audio.state === "suspended") {
      audio.resume();
    }
  }
  window.__copresenceMouth = {
    track() {
      ensure();
      return destination.stream.getAudioTracks()[0].clone();
    },
    setLead(seconds) {
      lead = Math.max(0.05, Number(seconds) || 0.05);
    },
    // The microphone's audio graph, for anything else that plays into the call
    output() {
      ensure();
      return { audio, destination };
    },
    // Plays PCM16 at 24 kHz; returns the seconds until it starts
    play(base64) {
      ensure();
      const text = atob(base64);
      const samples = new Float32Array(text.length >> 1);
      for (let index = 0; index < samples.length; index++) {
        let value = text.charCodeAt(index * 2) | (text.charCodeAt(index * 2 + 1) << 8);
        if (value >= 0x8000) {
          value -= 0x10000;
        }
        samples[index] = value / 0x8000;
      }
      if (!samples.length) {
        return 0;
      }
      const buffer = audio.createBuffer(1, samples.length, 24000);
      buffer.copyToChannel(samples, 0);
      const source = audio.createBufferSource();
      source.buffer = buffer;
      source.connect(destination);
      const start = Math.max(playhead, audio.currentTime + lead);
      source.start(start);
      playhead = start + buffer.duration;
      sources.push(source);
      source.onended = () => {
        sources = sources.filter((other) => other !== source);
      };
      return start - audio.currentTime;
    },
    stop() {
      for (const source of sources) {
        try {
          source.stop();
        } catch (error) {}
      }
      sources = [];
      if (audio) {
        playhead = audio.currentTime;
      }
    },
    remaining() {
      return audio ? Math.max(0, playhead - audio.currentTime) : 0;
    },
  };

  // Hearing that someone talks, from the call's own audio: Meet sends everyone else's
  // voices (never the agent's own) on its connections. Only how loud, never what is said
  const heard = [];
  let hearing = null;
  function hear(track) {
    try {
      hearing = hearing || new AudioContext();
      const analyser = hearing.createAnalyser();
      analyser.fftSize = 1024;
      hearing.createMediaStreamSource(new MediaStream([track])).connect(analyser);
      heard.push({ track, analyser, data: new Float32Array(analyser.fftSize) });
    } catch (error) {}
  }
  const NativeConnection = window.RTCPeerConnection;
  if (NativeConnection) {
    class HeardConnection extends NativeConnection {
      constructor(...parts) {
        super(...parts);
        this.addEventListener("track", (event) => {
          if (event.track && event.track.kind === "audio") {
            hear(event.track);
          }
        });
      }
    }
    window.RTCPeerConnection = HeardConnection;
    window.webkitRTCPeerConnection = HeardConnection;
  }
  // Louder than this (RMS) is someone talking; short gaps between words don't count
  const TALKING_LEVEL = 0.012;
  const GAP_MILLISECONDS = 300;
  let talkingSince = 0;
  let lastTalking = -1e9;
  let peak = 0;
  setInterval(() => {
    if (!heard.length) {
      return;
    }
    if (hearing.state === "suspended") {
      hearing.resume();
    }
    let level = 0;
    for (const entry of heard) {
      if (entry.track.readyState !== "live") {
        continue;
      }
      entry.analyser.getFloatTimeDomainData(entry.data);
      let sum = 0;
      for (const value of entry.data) {
        sum += value * value;
      }
      level = Math.max(level, Math.sqrt(sum / entry.data.length));
    }
    peak = Math.max(peak, level);
    const now = performance.now();
    if (level > TALKING_LEVEL) {
      if (now - lastTalking > GAP_MILLISECONDS) {
        talkingSince = now;
      }
      lastTalking = now;
    }
  }, 40);

  // The ears: what Meet's live captions show, block by block
  const ids = new WeakMap();
  let nextId = 1;
  function idOf(element) {
    if (!ids.has(element)) {
      ids.set(element, nextId++);
    }
    return ids.get(element);
  }
  function region() {
    return document.querySelector('[role="region"][aria-label="Captions"]') ||
      document.querySelector('[role="region"][aria-label*="aption" i]') ||
      document.querySelector('[jsname="tgaKEf"]');
  }
  function clean(text) {
    return (text || "").replace(/\\s+/g, " ").trim();
  }
  window.__copresenceEars = {
    on() {
      return Boolean(region());
    },
    turnOn() {
      const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
      const button = buttons.find((node) => /turn on captions|show captions/i.test(
        node.getAttribute("aria-label") || node.getAttribute("title") || ""));
      if (button) {
        button.click();
        return true;
      }
      return false;
    },
    captions() {
      const root = region();
      if (!root) {
        return null;
      }
      // The layout seen in live calls in October 2026 first
      const known = Array.from(root.querySelectorAll(".nMcdL"));
      if (known.length) {
        return known.map((block) => ({
          id: idOf(block),
          speaker: clean(block.querySelector(".NWpY1d")?.textContent),
          text: clean(block.querySelector(".ygicle")?.textContent),
        })).filter((block) => block.text);
      }
      // Otherwise by shape, since Meet's class names change between releases: each block
      // holds the speaker's name in a span, then what they said. Buttons such as "Jump to
      // the bottom" sit beside the list and don't count
      const parts = (element) => Array.from(element.children).filter((child) => !child.matches("button") &&
        !child.querySelector("button"));
      let list = root;
      while (parts(list).length === 1 && parts(list)[0].children.length && !parts(list)[0].querySelector(":scope > div > span, :scope > span")) {
        list = parts(list)[0];
      }
      const blocks = [];
      for (const block of parts(list)) {
        const nameElement = block.querySelector("span");
        const speaker = nameElement ? clean(nameElement.innerText) : "";
        let text = clean(block.innerText);
        if (speaker && text.startsWith(speaker)) {
          text = clean(text.slice(speaker.length));
        }
        if (text) {
          blocks.push({ id: idOf(block), speaker, text });
        }
      }
      return blocks;
    },
    // The call's chat, while its panel is open: one row per message
    chat() {
      const rows = [];
      const seen = new Set();
      for (const element of document.querySelectorAll("[data-message-id]")) {
        const id = element.getAttribute("data-message-id");
        if (!id || seen.has(id) || element.hasAttribute("data-pending")) {
          continue;
        }
        seen.add(id);
        let speaker = element.getAttribute("data-name") || element.getAttribute("data-sender-name") || "";
        if (!speaker) {
          const group = element.closest('[jsname="Ypafjf"]') || element.closest("[data-sender-id]");
          speaker = (group && (group.getAttribute("data-sender-name") ||
            group.querySelector(".poVWob")?.textContent)) || "";
        }
        const body = element.querySelector('[jsname="dTKtvb"]') || document.getElementById(id + "/qJTHM");
        const text = clean(body ? body.textContent : element.innerText);
        if (text) {
          rows.push({ id, speaker: clean(speaker), text });
        }
      }
      return rows;
    },
    // Whether someone in the call is talking now, and for how long, from its audio
    activity() {
      const now = performance.now();
      const talking = heard.length > 0 && now - lastTalking < GAP_MILLISECONDS;
      const result = { tracks: heard.length, talking, talkingFor: talking ? (lastTalking - talkingSince) / 1000 : 0,
                       quietFor: (now - lastTalking) / 1000, peak };
      peak = 0;
      return result;
    },
    dump() {
      const root = region();
      return root ? root.outerHTML.slice(0, 4000) : "";
    },
  };
})();
"""


def log(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {text}", flush=True)


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


# Caption turns (tested in Tools/tests/test_call_voice.py) ---------------------------

def _after_words(text: str, count: int) -> str:
    """The text after its first `count` words: "Codex can you add a tree", 3 -> "add a tree"."""
    if count <= 0:
        return text
    match = re.match(rf"^(?:\W*[\w']+){{{count}}}\W*", text)
    return text[match.end():] if match else ""


class CaptionTurns:
    """Turns what Meet's captions show into turns: updates while someone talks, and a
    finished turn once its caption settles. Meet rewrites a caption as it hears more, and
    sometimes in a new block, so a block from the same speaker that carries on the last
    one's words is the same turn. When a speaker goes on after a pause in the same block,
    what they add is a new turn, so it isn't lost."""

    def __init__(self, own_names: tuple[str, ...]):
        self.own = {name.lower() for name in own_names} | set(SELF_NAMES)
        self.turns = []  # each: {"ids", "speaker", "text", "changed", "done"}

    def _turn_for(self, block: dict) -> dict | None:
        for turn in reversed(self.turns):
            if block["id"] in turn["ids"]:
                return turn
        if self.turns:
            last = self.turns[-1]
            same_speaker = last["speaker"] == block["speaker"] or not block["speaker"]
            carries_on = block["text"].lower().startswith(last["text"].lower()[:24])
            if not last["done"] and same_speaker and carries_on:
                return last
        return None

    def feed(self, blocks: list[dict], now: float) -> list[tuple]:
        """What changed: ("update", speaker, text, new_words) and ("final", speaker, text)."""
        events = []
        for block in blocks:
            turn = self._turn_for(block)
            if turn is not None and turn["done"]:
                # The same block grew after its turn was over: the speaker went on after a pause
                said = turn["skip"] + len(words(turn["text"]))
                if len(words(block["text"])) > said and words(block["text"])[:said][-3:] == \
                        (words(turn["text"]) or [""])[-3:]:
                    turn = {"ids": {block["id"]}, "speaker": turn["speaker"], "text": "", "changed": now,
                            "done": False, "skip": said}
                    self.turns.append(turn)
                    self.turns = self.turns[-50:]
                else:
                    continue
            if turn is None:
                # A new turn finishes the one before it
                if self.turns and not self.turns[-1]["done"]:
                    events += self._finish(self.turns[-1])
                turn = {"ids": {block["id"]}, "speaker": block["speaker"], "text": "", "changed": now, "done": False,
                        "skip": 0}
                self.turns.append(turn)
                self.turns = self.turns[-50:]
            turn["ids"].add(block["id"])
            text = _after_words(block["text"], turn["skip"])
            if text != turn["text"] and not turn["done"]:
                added = max(0, len(words(text)) - len(words(turn["text"])))
                turn["text"], turn["changed"] = text, now
                if turn["speaker"].lower() not in self.own:
                    events.append(("update", turn["speaker"], turn["text"], added))
        for turn in self.turns:
            settle = SENTENCE_SETTLE_SECONDS if turn["text"].rstrip().endswith((".", "?", "!")) else SETTLE_SECONDS
            if not turn["done"] and now - turn["changed"] >= settle:
                events += self._finish(turn)
        return events

    def _finish(self, turn: dict) -> list[tuple]:
        turn["done"] = True
        if turn["speaker"].lower() in self.own or not turn["text"]:
            return []
        return [("final", turn["speaker"], turn["text"])]

    def someone_talking(self, now: float) -> bool:
        return any(not turn["done"] and turn["speaker"].lower() not in self.own and now - turn["changed"] < QUIET_SECONDS
                   for turn in self.turns)


transcript_lines = core.transcript_lines
read_decision = core.read_decision


def decision_request(agent: str, history: list[tuple[str, str]], status_note: str) -> dict:
    """The Decisions request for the latest line in the call: answer it, and does it tell
    the agent to stop something."""
    return core.decision_request(agent, history, status_note)


def should_respond(decision: dict, threshold: float = RESPOND_THRESHOLD) -> bool:
    return decision["probabilities"].get("respond", 1.0 if decision["choice"] == "respond" else 0.0) >= threshold


def request_from(arguments: str) -> str:
    """The delegated request, without a caption's "[Name]:" if the voice copied one in."""
    try:
        request = str(json.loads(arguments).get("request", ""))
    except (ValueError, AttributeError):
        return ""
    return re.sub(r"^\s*\[[^\]]{1,60}\]:\s*", "", request).strip()


def result_line(text: str) -> str:
    """How an answer from the agent reaches the voice: real results are said, while "I'll
    do that" only repeats the voice's own "on it", so it stays in the background."""
    return f"[Status]: {text}" if core.promised_work(text) and not re.search(r"\b(done|placed|added|moved|removed|"
                                                                            r"finished|stopped|can't|couldn't)\b",
                                                                            text, re.I) \
        else f"[Result]: {text}"


def realtime_voice(agent: str, appearance: dict | None) -> str:
    return core.voice_for(agent, appearance, core.REALTIME_VOICES)


def played_ms(sent_seconds: float, remaining_seconds: float) -> int:
    """How much of a reply the call heard, for truncating it after an interruption."""
    return max(0, int((sent_seconds - remaining_seconds) * 1000))


TOOLS = [
    {"type": "function", "name": "delegate",
     "description": "Send a request for work in the Unreal level to your hands. It runs after any current work.",
     "parameters": {"type": "object", "properties": {
         "request": {"type": "string", "description": "What was asked, in the person's words."}},
         "required": ["request"]}},
    {"type": "function", "name": "show_scene",
     "description": "Change what the call sees of you: avatar is your face in the studio, build is the "
                    "building level with the camera following your work.",
     "parameters": {"type": "object", "properties": {
         "scene": {"type": "string", "enum": ["avatar", "build"]}}, "required": ["scene"]}},
    {"type": "function", "name": "stop_work",
     "description": "Stop the work your hands are doing now, when someone clearly wants it stopped or cancelled.",
     "parameters": {"type": "object", "properties": {}}},
]


# The Realtime session ------------------------------------------------------------------

class RealtimeVoice:
    """The OpenAI Realtime session that speaks for the agent. It reconnects when the
    connection drops or the session gets old, starting the new one from the last lines of
    the call, and keeps the conversation short by deleting old items."""

    def __init__(self, client, agent: str, voice: str, on_event):
        self.client = client
        self.agent = agent
        self.voice = voice
        self.on_event = on_event
        self.connection = None
        self.ready = asyncio.Event()
        self.started_at = 0.0
        self.items: list[str] = []
        self.lines: list[str] = []
        self.counter = 0
        self.prefix = uuid.uuid4().hex[:6]
        self.busy = lambda: False

    async def run(self) -> None:
        failures = 0
        while True:
            try:
                async with self.client.realtime.connect(model=REALTIME_MODEL) as connection:
                    await connection.session.update(session={
                        "type": "realtime",
                        "instructions": INSTRUCTIONS.format(agent=self.agent),
                        "output_modalities": ["audio"],
                        "max_output_tokens": MAX_OUTPUT_TOKENS,
                        "audio": {"input": {"turn_detection": None},
                                  "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": self.voice}},
                        "tools": TOOLS,
                    })
                    self.connection, self.started_at, self.items = connection, time.monotonic(), []
                    await self._reseed()
                    self.ready.set()
                    failures = 0
                    log(f"Voice: Realtime session ready ({REALTIME_MODEL}, voice {self.voice}).")
                    try:
                        async for event in connection:
                            await self.on_event(event)
                            if time.monotonic() - self.started_at > SESSION_SECONDS and not self.busy():
                                log("Voice: Realtime session is getting old; starting a fresh one.")
                                break
                    finally:
                        # Nothing may be sent on a connection that is closing
                        self.ready.clear()
                        self.connection = None
                log("Voice: Realtime session ended.")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log(f"Voice: Realtime connection problem: {type(error).__name__}: {error}")
            self.ready.clear()
            self.connection = None
            await self.on_event(None)
            delay = RECONNECT_SECONDS[min(failures, len(RECONNECT_SECONDS) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    def _item_id(self) -> str:
        self.counter += 1
        return f"cp{self.prefix}{self.counter}"

    async def _reseed(self) -> None:
        """A new session hears the last lines of the call, so it can carry on."""
        if not self.lines:
            return
        text = "Earlier in this call:\n" + "\n".join(self.lines[-RESEED_LINES:])
        await self._create({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text[:6000]}]})

    async def _create(self, item: dict) -> bool:
        connection = self.connection
        if connection is None or not self.ready.is_set():
            return False
        item = {"id": self._item_id(), **item}
        try:
            await connection.conversation.item.create(item=item)
        except Exception as error:
            log(f"Voice: couldn't add to the Realtime conversation: {type(error).__name__}: {error}")
            return False
        self.items.append(item["id"])
        return True

    async def add_line(self, text: str) -> None:
        self.lines = (self.lines + [text])[-200:]
        if not self.ready.is_set():
            return
        await self._create({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text[:2000]}]})
        await self.trim()

    def note_item(self, item_id: str) -> None:
        """Items the model made (its replies and tool calls), so old ones can be deleted too."""
        if item_id and item_id not in self.items:
            self.items.append(item_id)

    async def trim(self) -> None:
        if self.busy():
            return
        while len(self.items) > CONVERSATION_ITEMS and self.connection is not None:
            old = self.items.pop(0)
            try:
                await self.connection.conversation.item.delete(item_id=old)
            except Exception:
                pass

    async def tool_output(self, call_id: str, output: str) -> None:
        if self.ready.is_set():
            await self._create({"type": "function_call_output", "call_id": call_id, "output": output})

    async def respond(self, instructions: str, token: str) -> bool:
        connection = self.connection
        if connection is None or not self.ready.is_set():
            return False
        try:
            await connection.response.create(response={"instructions": instructions, "metadata": {"turn": token}})
        except Exception as error:
            log(f"Voice: couldn't ask Realtime to answer: {type(error).__name__}: {error}")
            return False
        return True

    async def cancel(self, item_id: str | None, heard_ms: int) -> None:
        if self.connection is None:
            return
        try:
            await self.connection.response.cancel()
        except Exception:
            pass
        if item_id:
            try:
                await self.connection.conversation.item.truncate(item_id=item_id, content_index=0, audio_end_ms=heard_ms)
            except Exception:
                pass


# The voice in the call ------------------------------------------------------------

class CallVoice:
    def __init__(self, page, agent: str, hub_url: str, code: str, appearance: dict | None, key: str | None = None,
                 face=None, scenes=None):
        self.page = page
        # Switches the agent's editor between the avatar and build scenes: an async
        # callable taking "avatar" or "build" and returning the result; None for none
        self.scenes = scenes
        # Moves an avatar's mouth with the voice, when the agent's level has one
        # (copresence_call.AvatarFace); None for no face
        self.face = face
        self.mouth = core.MouthShapes()
        self.face_epoch = 0.0
        self.face_frames = 0
        self.agent = agent
        self.code = code
        self.key = key
        self.hub = httpx.AsyncClient(base_url=hub_url.rstrip("/"), timeout=40)
        self.voice = realtime_voice(agent, appearance)
        self.turns = CaptionTurns((agent,))
        self.aliases = core.agent_aliases(agent)
        self.other_aliases: set[str] = set()
        self.history: list[tuple[str, str]] = []
        self.asks = core.AskQueue()
        self.timeline = core.Timeline()
        self.engine: RealtimeVoice | None = None
        self.decisions = None
        self.listening = True
        # The response being spoken: its token (our turn id), Realtime id, message item and audio sent
        self.pending_token = None
        self.response_id = None
        self.reply_item = None
        self.reply_sent_seconds = 0.0
        self.said = ""
        self.speaking_turn = None
        self.responding = False
        self.cancelled: set[str] = set()
        self.last_spoke = -1e9
        # Speech is made faster than it plays, so the call hears the agent until this time
        self.playing_until = 0.0
        self.speaking_reported = 0.0
        self.talk_over_words = 0
        self.activity = {"talking": False, "talkingFor": 0.0, "quietFor": 1e9, "tracks": 0}
        self.activity_noted = False
        self.last_caption_change = -1e9
        self.audio_stats = {"since": time.monotonic(), "peak": 0.0, "talking": 0, "polls": 0}
        self.pending_since = 0.0
        self.ack_focus = ACK_FOCUS
        self.asked_by = ""
        self.asked_turn = ""
        # People in the call who asked the agent for something; its chat answers to them are spoken
        self.askers: set[str] = set()
        self.results: list[tuple[float, str]] = []
        self.status_note = ""
        self.last_hand = -1e9
        self.after = 0
        self.version = -1
        self.caption_dumped = False
        self.deaf_reported = False
        self.heard_talking_seconds = 0.0
        self.chat_seen: set[str] = set()
        self.chat_ready = False
        # Lines that sound like a stop and are being decided; no new answer starts meanwhile
        self.holds = 0
        self.chat_tried = -CHAT_RETRY_SECONDS
        self.captions_tried = 0.0
        # When someone last said stop: nothing asked before it is answered or done after it
        self.stopped_at = -1e9
        self.asked_at = 0.0
        self.last_delegated = -1e9
        # The answer being made called a tool
        self.tool_called = False
        self.jobs: set[asyncio.Task] = set()
        self.wake = asyncio.Event()

    async def _page(self, script: str, argument=None):
        return await self.page.evaluate(script, argument) if argument is not None else await self.page.evaluate(script)

    async def _forever(self, step, name: str) -> None:
        """Runs one step of a loop again and again; a step that fails is logged and the loop
        carries on, so one bad moment doesn't leave the agent deaf or mute for the call."""
        last_problem = ""
        while True:
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                problem = f"{type(error).__name__}: {error}"
                if problem != last_problem:
                    log(f"Voice: {name} hit a problem and carries on: {problem}")
                last_problem = problem
                await asyncio.sleep(1.0)

    def _spawn(self, job) -> None:
        task = asyncio.create_task(job)
        self.jobs.add(task)
        task.add_done_callback(self._job_done)

    def _job_done(self, task: asyncio.Task) -> None:
        self.jobs.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log(f"Voice: a background step failed: {type(task.exception()).__name__}: {task.exception()}")

    async def run(self, stop: asyncio.Event) -> None:
        from openai import AsyncOpenAI

        if self.key is None:
            loop = asyncio.get_running_loop()
            # Reading the key can wait on 1Password; the call's camera keeps running meanwhile
            self.key, source = await loop.run_in_executor(None, openai_key)
            log(f"Voice: OpenAI key from {source}.")
        log("Voice: hearing the call through Meet's captions, its audio and its chat.")
        client = AsyncOpenAI(api_key=self.key)
        self.decisions = httpx.AsyncClient(base_url="https://api.openai.com/v1", timeout=8,
                                           headers={"Authorization": f"Bearer {self.key}"})
        try:
            first = (await self.hub.get("/api/state", params={"after": 0, "version": -1})).json()
            self.version = first["version"]
            self.after = max([0] + [message["id"] for message in first["messages"]])
            self._note_agents(first["agents"])
        except (httpx.HTTPError, ValueError):
            log("Voice: can't reach the session hub; results from the agent won't be spoken.")
        if self.face is not None and await self.face.start():
            await self._page(f"() => window.__copresenceMouth.setLead({FACE_AUDIO_LEAD_SECONDS})")
            log(f"Voice: moving the avatar's mouth with the voice (voice {FACE_AUDIO_LEAD_SECONDS:.2f} s later, "
                f"mouth {FACE_VIDEO_LAG_SECONDS:.2f} s ahead of it).")
        else:
            self.face = None
        self.engine = RealtimeVoice(client, self.agent, self.voice, self._on_event)
        self.engine.busy = lambda: self.responding
        # The first decision of a session takes seconds; take it now, not on someone's question
        self._spawn(self._warm_up())
        jobs = [asyncio.create_task(self.engine.run())] + [
            asyncio.create_task(self._forever(step, name)) for step, name in (
                (self._listen_step, "hearing the call"), (self._chat_step, "reading the chat"),
                (self._watch_hub_step, "watching the hub"), (self._turn_step, "taking turns"))]
        try:
            stopped = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait([stopped, *jobs], return_when=asyncio.FIRST_COMPLETED)
            for job in done:
                if job is not stopped and not job.cancelled() and job.exception():
                    log(f"Voice stopped: {type(job.exception()).__name__}: {job.exception()}")
        finally:
            for job in [*jobs, *self.jobs]:
                job.cancel()
            await asyncio.gather(*jobs, *self.jobs, return_exceptions=True)
            try:
                await self._page("() => window.__copresenceMouth.stop()")
            except Exception:
                pass
            await self._report_speaking(False)
            await self.hub.aclose()
            await self.decisions.aclose()
            await client.close()

    # Hearing the call

    SNAPSHOT = ("() => window.__copresenceEars ? {captions: window.__copresenceEars.captions(), "
                "activity: window.__copresenceEars.activity()} : null")

    async def _listen_step(self) -> None:
        await asyncio.sleep(POLL_SECONDS)
        try:
            seen = await self._page(self.SNAPSHOT)
        except Exception:
            # The page is navigating or closing; the call's own code notices a closed call
            return
        now = time.monotonic()
        if not seen:
            return
        await self._heard_audio(seen["activity"], now)
        blocks = seen["captions"]
        if blocks is None:
            if now - self.captions_tried > CAPTIONS_RETRY_SECONDS:
                self.captions_tried = now
                if await self._page("() => window.__copresenceEars.turnOn()"):
                    log("Voice: turned on captions.")
            await self._check_deaf(blocks, now)
            return
        if blocks and not self.caption_dumped:
            self.caption_dumped = True
            log(f"Voice: captions found; first blocks: {json.dumps(blocks[-2:])[:300]}")
        await self._check_deaf(blocks, now)
        for event in self.turns.feed(blocks, now):
            if event[0] == "update":
                self.last_caption_change = now
                await self._heard_words(event[1], event[2], event[3])
            else:
                self._turn_finished(event[1], event[2], "captions", now)

    async def _heard_audio(self, activity: dict, now: float) -> None:
        self.activity = activity
        if activity.get("tracks") and not self.activity_noted:
            self.activity_noted = True
            log(f"Voice: hearing the call's audio ({activity['tracks']} track(s)).")
        self._audio_stats(activity, now)
        if self._speaking() and self.listening and self._audio_talking(now) and \
                core.interrupts("", activity.get("talkingFor", 0.0)):
            await self._interrupt(f"someone talked over me for {activity['talkingFor']:.1f} s")

    async def _check_deaf(self, blocks, now: float) -> None:
        """Someone has talked for a while and no caption came: says so once, with a picture
        of the page and the captions region, so a changed Meet layout can be fixed."""
        if self.caption_dumped or self.deaf_reported:
            return
        self.heard_talking_seconds += POLL_SECONDS if self.activity.get("talking") else 0.0
        if self.heard_talking_seconds < 6.0:
            return
        self.deaf_reported = True
        from pathlib import Path
        folder = Path(__file__).resolve().parent.parent / "Saved" / "CoPresenceLogs"
        try:
            await self.page.screenshot(path=str(folder / "call-no-captions.png"))
            dump = await self._page("() => window.__copresenceEars ? window.__copresenceEars.dump() : ''")
            (folder / "call-no-captions.html").write_text(dump or "(no captions region)")
        except Exception:
            pass
        log(f"Voice: people have talked for 6 s and no captions came ({'no captions region' if blocks is None else f'{len(blocks)} blocks'}); "
            "see call-no-captions.png and .html in Saved/CoPresenceLogs.")

    def _audio_talking(self, now: float) -> bool:
        """Someone talking by the call's audio, unless it has gone on with no captions: noise."""
        activity = self.activity
        if not activity.get("talking"):
            return False
        return activity.get("talkingFor", 0.0) < NOISE_SECONDS or now - self.last_caption_change < NOISE_SECONDS

    def _audio_stats(self, activity: dict, now: float) -> None:
        """Once a minute, how loud the call got and how much counted as talking, to tune the
        talking level in VOICE_SCRIPT."""
        stats = self.audio_stats
        stats["peak"] = max(stats["peak"], float(activity.get("peak") or 0.0))
        stats["polls"] += 1
        stats["talking"] += 1 if activity.get("talking") else 0
        if now - stats["since"] >= AUDIO_STATS_SECONDS and activity.get("tracks"):
            log(f"Voice: call audio in the last minute: loudest {stats['peak']:.3f} RMS, "
                f"talking {100 * stats['talking'] / max(1, stats['polls']):.0f}% of the time")
            self.audio_stats = {"since": now, "peak": 0.0, "talking": 0, "polls": 0}

    async def _heard_words(self, speaker: str, text: str, new_words: int) -> None:
        """A person is talking: a short command acts at once, and real words stop the agent."""
        if not self.listening:
            return
        if not self._speaking():
            self.talk_over_words = 0
            return
        self.talk_over_words += new_words
        command = core.spoken_command(text, self.aliases)
        # Mid-sentence, "stop" may still become "stop the elevator", so it only stops the talking
        if command in (core.STOP, core.QUIET):
            await self._interrupt(f"{speaker} said \"{text}\"")
        elif self.talk_over_words >= core.INTERRUPT_WORDS and not core.is_backchannel(text):
            await self._interrupt(f"{speaker} talked over me")

    def _someone_talking(self, now: float) -> bool:
        if self.turns.someone_talking(now) or self._audio_talking(now):
            return True
        # Just stopped: give them a moment, unless the sound was noise all along
        return self.activity.get("quietFor", 1e9) < AUDIO_QUIET_SECONDS and \
            now - self.last_caption_change < NOISE_SECONDS

    def _speaking(self) -> bool:
        """Making an answer, or the call still hearing one."""
        return self.responding or time.monotonic() < self.playing_until

    def _turn_finished(self, speaker: str, text: str, source: str, now: float) -> None:
        turn = core.Turn(uuid.uuid4().hex[:10], speaker or "Someone", text, source, at=now)
        self.timeline.mark(turn.id, "final", at=now)
        quiet_for = self.activity.get("quietFor", 0.0)
        if source == "captions" and not self.activity.get("talking") and 0 < quiet_for < 10:
            # When the voice really stopped, from the call's audio: the gap is the captions' lag
            self.timeline.mark(turn.id, "speech_end", at=now - quiet_for)
        self._spawn(self._handle(turn))

    def _other_agent(self, speaker: str) -> bool:
        return any(speaker.strip().lower() == name.lower() for name in self._agents if name != self.agent)

    def _stop_is_for_me(self, turn: core.Turn) -> bool:
        """A bare "stop" or "never mind" in a call is often people talking to each other. It is
        for the agent when it says the agent's name, or the agent is talking, just talked, or
        is busy with work the call asked for."""
        if core.said_to(turn.text, self.aliases) or self._speaking() or turn.at - self.last_spoke < FOLLOW_UP_SECONDS:
            return True
        working = (self._agents.get(self.agent) or {}).get("state") in ("working", "thinking")
        return working and turn.at - self.last_delegated < WORK_FOR_CALL_SECONDS

    async def _handle(self, turn: core.Turn) -> None:
        other_agent = self._other_agent(turn.speaker)
        if turn.source == "chat" and not other_agent:
            # Typed commands are exact words, like slash commands
            turn.command = core.chat_command(turn.text, self.aliases, self.other_aliases)
        # Muted: the call isn't listened to at all, only commands typed in its chat
        if not self.listening and turn.command is None:
            return
        log(f"{turn.speaker}{' (chat)' if turn.source == 'chat' else ''}: {turn.text}")
        if turn.command:
            await self._command(turn)
            return
        self.history.append((turn.speaker, turn.text))
        # Decided on the call as it stood when this line ended: the agent's own words, cut off
        # just after, must not become "the latest line"
        context = list(self.history)
        await self.engine.add_line(f"[{turn.speaker}]{' (in the chat)' if turn.source == 'chat' else ''}: {turn.text}")
        named = core.said_to(turn.text, self.aliases)
        if other_agent:
            # Another agent's voice: heard, but it can't command this one, and it is only
            # answered when it calls this agent by name, so two agents can't talk in a loop
            if named:
                self._ask(turn)
            return
        # Sounds like "stop" or "hold on" while the agent talks: it stops talking at once.
        # What the person meant (stop the work too, or nothing) is the decision model's call
        sounds_like_stop = core.spoken_command(turn.text, self.aliases) is not None
        if sounds_like_stop and self._speaking():
            await self._interrupt(f"{turn.speaker} said \"{turn.text}\"")
        if sounds_like_stop:
            # Nothing new starts until the decision model says whether it was a stop
            self.holds += 1
            try:
                await self._decided(turn, named, await self._decide(context))
            finally:
                self.holds -= 1
                self.wake.set()
            return
        if named:
            # Called by name: answered at once, while the decision model checks it isn't a stop
            turn.addressed = True
            self.timeline.mark(turn.id, "decided", note="named")
            log("Voice: decision respond (called by name)")
            self._ask(turn)
            await self._control(turn, await self._decide(context), answering=True)
            return
        await self._decided(turn, named, await self._decide(context))

    async def _decided(self, turn: core.Turn, named: bool, decision: dict) -> None:
        """Acts on the decision model's answers for a line: stop something, answer, or not."""
        if turn.at <= self.stopped_at:
            log(f"Voice: \"{turn.text}\" was said before a stop; dropped.")
            return
        command = await self._control(turn, decision)
        if command in (core.QUIET, core.MUTE) or (command == core.STOP and not named and
                                                    decision["probabilities"].get("respond", 0.0) < RESPOND_THRESHOLD):
            return
        threshold = FOLLOW_UP_THRESHOLD if turn.at - self.last_spoke < FOLLOW_UP_SECONDS else RESPOND_THRESHOLD
        respond = named or should_respond(decision, threshold)
        probabilities = ", ".join(f"{name} {value:.2f}" for name, value in decision["probabilities"].items())
        self.timeline.mark(turn.id, "decided", note=f"respond {decision['probabilities'].get('respond', 0):.2f}")
        log(f"Voice: decision {decision['choice']} ({probabilities}){' after my turn' if threshold < RESPOND_THRESHOLD else ''}")
        if respond:
            self._ask(turn)
        elif decision["probabilities"].get("raise_hand", 0.0) >= RAISE_HAND_THRESHOLD:
            await self._raise_hand()

    async def _control(self, turn: core.Turn, decision: dict, answering: bool = False) -> str | None:
        """Acts on what the decision model says the line tells the agent to stop, if anything:
        talking (QUIET), its work (STOP) or listening (MUTE). Returns that command."""
        command = core.control_command(decision)
        control = decision.get("control") or {}
        if control:
            log("Voice: control " + ", ".join(f"{name} {value:.2f}" for name, value in control.items()))
        if command == core.STOP and not self._stop_is_for_me(turn):
            log("Voice: that stop doesn't look like it's for me (not named, not talking, not working for the call).")
            command = None
        if command is None:
            return None
        turn.command = command
        if answering and command == core.STOP and decision["probabilities"].get("respond", 0.0) >= RESPOND_THRESHOLD:
            # "Codex, scrap the trees and do a fountain": drop the old work, keep answering this
            log(f"Voice: stop_work from {turn.speaker}, and answering the rest.")
            self.stopped_at = max(self.stopped_at, turn.at - 0.001)
            await self._stop_work(turn.speaker)
            return command
        await self._command(turn)
        return command

    def _people_note(self) -> str:
        """Who has spoken in the call: with one person and the agent, a question is for the agent."""
        people = sorted({speaker for speaker, _ in self.history[-40:]
                         if speaker != self.agent and not self._other_agent(speaker)})
        if len(people) == 1:
            return (f"Only {people[0]} and {self.agent} are talking in this call, so what {people[0]} says is "
                    f"usually said to {self.agent}.")
        return f"People talking in this call: {', '.join(people)}." if people else ""

    async def _decide(self, history: list[tuple[str, str]] | None = None) -> dict:
        status = f"Right now {self.agent} is {self.status_note}." if self.status_note else ""
        status = f"{status} {self._people_note()}".strip()
        try:
            reply = await self.decisions.post("/decisions", json=decision_request(self.agent, history or self.history,
                                                                                  status))
            reply.raise_for_status()
            return read_decision(reply.json())
        except (httpx.HTTPError, ValueError) as error:
            log(f"Voice: the decision failed ({error}); staying quiet.")
            return {"choice": "stay_silent", "probabilities": {}}

    async def _warm_up(self) -> None:
        started = time.monotonic()
        await self._decide([("Someone", "Hello everyone.")])
        log(f"Voice: decisions ready ({round((time.monotonic() - started) * 1000)} ms for the first one).")

    async def _raise_hand(self) -> None:
        """Something to add that nobody asked for: a line in the chat, not a voice cutting in."""
        if time.monotonic() - self.last_hand < RAISE_HAND_GAP_SECONDS:
            log("Voice: I'd have something to add (raise hand); staying quiet.")
            return
        self.last_hand = time.monotonic()
        log("Voice: raising my hand in the chat.")
        await self._chat(f"{self.agent}: I have something to add here. Say my name when you want it.")

    # Commands

    async def _command(self, turn: core.Turn) -> None:
        command, person = turn.command, turn.speaker
        log(f"Voice: {command} from {person}")
        if command in (core.STOP, core.QUIET):
            await self._interrupt(f"{person}: {command}", clear=True)
        if command == core.STOP:
            # Everything asked before this line is dropped; this line itself can still be answered
            self.stopped_at = max(self.stopped_at, turn.at - 0.001)
            await self._stop_work(person)
            if turn.source == "chat":
                await self._chat(f"{self.agent}: Stopped.")
        elif command == core.QUIET and turn.source == "chat":
            await self._chat(f"{self.agent}: OK, quiet. Anything I'm working on carries on.")
        elif command == core.MUTE and self.listening:
            self.listening = False
            await self._interrupt(f"{person}: mute", clear=True)
            self.asks.clear()
            await self._chat(f"{self.agent}: Not listening to the call now. Type \"listen\" to bring me back.")
        elif command == core.LISTEN and not self.listening:
            self.listening = True
            await self._chat(f"{self.agent}: Listening again.")
        elif command == core.HELP:
            await self._chat(f"{self.agent}: {core.HELP_TEXT}")

    async def _stop_work(self, person: str) -> None:
        """Tells the agent's runner to drop what it is doing, and forgets what was waiting."""
        self.results.clear()
        payload = {"author": person or "Someone", "role": "human", "to": self.agent, "code": self.code, "kind": "stop",
                   "source": "call", "text": "stop", "source_event_id": f"call-stop:{uuid.uuid4().hex}"}
        try:
            (await self.hub.post("/api/messages", json=payload, timeout=5)).raise_for_status()
        except httpx.HTTPError as error:
            log(f"Voice: couldn't tell the agent to stop: {error}")

    # Taking turns

    def _ask(self, turn: core.Turn) -> None:
        if turn.at <= self.stopped_at:
            log(f"Voice: \"{turn.text}\" was said before a stop; dropped.")
            return
        dropped = self.asks.add(turn)
        if dropped:
            log(f"Voice: too much waiting; dropped \"{dropped.text}\" from {dropped.speaker}")
        self.wake.set()

    async def _turn_step(self) -> None:
        """Answers what people asked, one at a time, and says results when there is a pause."""
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
        self.wake.clear()
        await self._keep_reporting_speaking()
        now = time.monotonic()
        if self.pending_token and now - self.pending_since > RESPONSE_START_SECONDS:
            log("Voice: Realtime never started that answer; giving up on it.")
            self.responding, self.pending_token = False, None
        if self.responding or self.engine is None or not self.engine.ready.is_set() or self.holds:
            return
        # The last answer is still playing: the next one waits, so talking over it still works
        if now < self.playing_until:
            return
        if not self.listening:
            # Muted: nothing is said out loud; results go to the chat
            self.asks.clear()
            while self.results:
                self._spawn(self._chat(f"{self.agent}: {self.results.pop(0)[1]}"))
            return
        if self._someone_talking(now) or self._other_agent_speaking():
            self._expire_results(now)
            return
        turn, stale = self.asks.next(now)
        for old in stale:
            log(f"Voice: \"{old.text}\" from {old.speaker} waited too long; dropped.")
        if turn is not None:
            self.asked_at = turn.at
            await self._respond(turn.id, ANSWER_FOCUS.format(speaker=turn.speaker, text=turn.text), turn.speaker)
        elif self.results:
            await self._say_result()
        else:
            await self.engine.trim()

    def _expire_results(self, now: float) -> None:
        while self.results and now - self.results[0][0] > RESULT_WAIT_SECONDS:
            _, text = self.results.pop(0)
            log("Voice: a result waited too long for a pause; it goes to the chat instead.")
            self._spawn(self._chat(f"{self.agent}: {text}"))

    def _other_agent_speaking(self) -> bool:
        return any(name != self.agent and status.get("speaking") for name, status in self._agents.items())

    async def _respond(self, turn_id: str, focus: str, asked_by: str) -> None:
        self.responding = True
        self.pending_token = turn_id
        self.pending_since = time.monotonic()
        self.asked_by = asked_by or self.asked_by
        self.asked_turn = turn_id
        self.talk_over_words = 0
        status = STATUS_FOCUS.format(status=self.status_note) if self.status_note else ""
        self.timeline.mark(turn_id, "asked")
        try:
            sent = await self.engine.respond(INSTRUCTIONS.format(agent=self.agent) + status + focus, turn_id)
        except Exception as error:
            log(f"Voice: couldn't ask Realtime to answer: {error}")
            sent = False
        if not sent:
            self.responding = False
            self.pending_token = None

    async def _say_result(self) -> None:
        _, text = self.results.pop(0)
        log(f"{self.agent} (chat): {text}")
        line = result_line(text)
        await self.engine.add_line(line)
        if line.startswith("[Result]"):
            await self._respond(f"result-{uuid.uuid4().hex[:8]}", RESULT_FOCUS.format(text=text), "")

    async def _interrupt(self, why: str, clear: bool = False) -> None:
        """Stops the agent talking: the audio at once, the answer it was making, and the part
        nobody heard is cut from what Realtime thinks it said."""
        if clear:
            self.asks.clear()
        if not self._speaking():
            return
        # Before anything is awaited, so no more of this answer is played meanwhile
        if self.response_id:
            self.cancelled.add(self.response_id)
        self.playing_until = 0.0
        self.tool_called = False
        try:
            remaining = await self._page("() => window.__copresenceMouth.remaining()")
            await self._page("() => window.__copresenceMouth.stop()")
        except Exception:
            remaining = 0.0
        log(f"Voice: stopping ({why}).")
        if self.face is not None:
            self.face.stop()
        if self.speaking_turn:
            self.timeline.mark(self.speaking_turn, "interrupted")
        if self.engine is not None:
            # The answer may be finished and only still playing: its unheard end is cut all the same
            await self.engine.cancel(self.reply_item, played_ms(self.reply_sent_seconds, remaining))
        if self.responding:
            await self._finish_response("cut off")
        else:
            log(f"Voice: cut off after {played_ms(self.reply_sent_seconds, remaining) / 1000:.1f} s of that answer.")
            self.wake.set()

    # Speaking

    async def _on_event(self, event) -> None:
        if event is None:
            # The session dropped: whatever it was saying is over
            if self.responding:
                await self._finish_response("connection lost")
            return
        kind = event.type
        response_id = getattr(event, "response_id", None) or getattr(getattr(event, "response", None), "id", None)
        if kind == "response.created":
            metadata = getattr(event.response, "metadata", None) or {}
            if metadata.get("turn") != self.pending_token or self.pending_token is None:
                # Not the answer this voice asked for (one it gave up on): it must not speak
                self.cancelled.add(event.response.id)
                try:
                    await self.engine.connection.response.cancel(response_id=event.response.id)
                except Exception:
                    pass
                return
            self.response_id, self.speaking_turn, self.pending_token = event.response.id, metadata["turn"], None
            self.reply_item, self.reply_sent_seconds, self.said = None, 0.0, ""
            self.tool_called = False
            self.mouth, self.face_frames = core.MouthShapes(), 0
            return
        if response_id in self.cancelled or (response_id and response_id != self.response_id):
            return
        if kind == "response.output_item.added":
            self.engine.note_item(getattr(event.item, "id", ""))
            if getattr(event.item, "type", "") == "message":
                self.reply_item = event.item.id
        elif kind == "response.output_audio.delta":
            pcm_seconds = len(base64.b64decode(event.delta)) / 48000
            if not self.reply_sent_seconds:
                self.timeline.mark(self.speaking_turn, "first_audio")
            delay = float(await self._page("data => window.__copresenceMouth.play(data)", event.delta) or 0.0)
            if response_id in self.cancelled:
                # Stopped while this piece was on its way to the page
                await self._page("() => window.__copresenceMouth.stop()")
                return
            self.playing_until = max(self.playing_until, time.monotonic() + delay + pcm_seconds)
            if self.face is not None:
                self._move_face(event.delta, delay)
            if not self.reply_sent_seconds:
                self.timeline.mark(self.speaking_turn, "heard", at=time.monotonic() + delay)
                # Off the audio's path: a slow hub mustn't leave a gap in the speech
                self._spawn(self._report_speaking(True))
            self.reply_sent_seconds += pcm_seconds
            self.last_spoke = time.monotonic()
        elif kind == "response.output_audio_transcript.delta":
            self.said += event.delta
        elif kind == "response.function_call_arguments.done":
            await self._tool_call(event.name, event.call_id, event.arguments)
        elif kind == "response.done":
            status = getattr(event.response, "status", "")
            if status not in ("completed", "cancelled"):
                details = getattr(event.response, "status_details", None)
                log(f"Voice: Realtime answer {status}: {getattr(details, 'reason', details)}")
            # It used a tool without saying anything, before or after: a few words now
            acknowledge = self.tool_called and status == "completed" and not self.said.strip()
            self.tool_called = False
            await self._finish_response(status)
            if acknowledge:
                await self._respond(f"ack-{uuid.uuid4().hex[:8]}", self.ack_focus, self.asked_by)
        elif kind == "error":
            message = str(getattr(event.error, "message", event.error))
            # Cancelling an answer that had just finished is harmless
            if "no active response" not in message:
                log(f"Voice: Realtime error: {message}")

    def _move_face(self, delta: str, delay: float) -> None:
        """Mouth shapes for this piece of the answer, timed to show as its sound plays."""
        if not self.face_frames and not self.reply_sent_seconds:
            # The answer's first sound plays after `delay`; its mouth shows a little before
            self.face_epoch = time.time() + delay - FACE_VIDEO_LAG_SECONDS
        shapes = self.mouth.feed(base64.b64decode(delta))
        if shapes:
            self.face.send(self.face_epoch + self.face_frames / core.MOUTH_FPS, shapes)
            self.face_frames += len(shapes)

    async def _finish_response(self, how: str) -> None:
        turn, said = self.speaking_turn, self.said.strip()
        if said:
            self.history.append((self.agent, said))
            log(f"{self.agent}: {said}" + (f" ({how})" if how not in ("completed", "") else ""))
            self._spawn(self._post_spoken(said))
        if turn:
            self.timeline.mark(turn, "done")
            timing = self.timeline.line(turn)
            if timing:
                log(f"Voice timing: {timing}")
        self.responding = False
        # An answer asked for but not started yet must not start later
        self.pending_token = None
        self.response_id, self.speaking_turn, self.said = None, None, ""
        if said:
            self.last_spoke = time.monotonic()
        self.wake.set()

    async def _show_scene(self, scene: str) -> None:
        log(f"Voice: switching the call's view to the {scene} scene.")
        try:
            result = await self.scenes(scene)
            log(f"Voice: {scene} scene: {result.get('state')}{' (' + str(result.get('error')) + ')' if result.get('error') else ''}")
        except Exception as error:
            log(f"Voice: couldn't switch to the {scene} scene: {error}")

    async def _tool_call(self, name: str, call_id: str, arguments: str) -> None:
        if name == "show_scene":
            try:
                scene = str(json.loads(arguments).get("scene", ""))
            except (ValueError, AttributeError):
                scene = ""
            if self.scenes is None or scene not in ("avatar", "build"):
                output = "That view isn't available here."
            else:
                self._spawn(self._show_scene(scene))
                output = f"Switching to the {scene} scene; it takes a few seconds."
            await self.engine.tool_output(call_id, output)
            self.tool_called, self.ack_focus = True, ACK_FOCUS
            return
        if name == "stop_work":
            log(f"Voice: stopping the work (asked by {self.asked_by or 'someone'}).")
            await self._stop_work(self.asked_by)
            await self.engine.tool_output(call_id, "Stopped. Your hands dropped what they were doing.")
            self.stopped_at = max(self.stopped_at, self.asked_at)
            self.tool_called, self.ack_focus = True, STOPPED_FOCUS
            return
        request = request_from(arguments)
        person = self.asked_by or "Someone"
        output = "Sent to your hands. The result will arrive later as a [Result] line."
        if self.asked_at and self.asked_at <= self.stopped_at:
            request = ""
            output = "Someone said stop after this was asked, so nothing was sent. Don't do it."
        elif request:
            log(f"To {self.agent}: {request} (from {person})")
            self.askers.add(person)
            payload = {"author": person, "role": "human", "to": self.agent, "code": self.code, "source": "call",
                       "text": f"{request}\n{REQUEST_NOTE}",
                       "source_event_id": f"call:{self.asked_turn}:{call_id}"}
            try:
                (await self.hub.post("/api/messages", json=payload, timeout=5)).raise_for_status()
                self.last_delegated = time.monotonic()
            except httpx.HTTPError as error:
                output = f"The request didn't reach your hands: {error}. Tell the person."
        else:
            output = "The request was empty, so nothing was sent. Ask what they want."
        await self.engine.tool_output(call_id, output)
        self.tool_called, self.ack_focus = True, ACK_FOCUS

    async def _report_speaking(self, speaking: bool) -> None:
        """Lets editors chatter the agent's head while it speaks in the call."""
        self.speaking_reported = time.monotonic() if speaking else 0.0
        try:
            await self.hub.post("/api/voice", json={"agent": self.agent, "speaking": speaking}, timeout=3)
        except httpx.HTTPError:
            pass

    async def _keep_reporting_speaking(self) -> None:
        if not self.speaking_reported:
            return
        try:
            remaining = await self._page("() => window.__copresenceMouth.remaining()")
        except Exception:
            remaining = 0.0
        if not self.responding and remaining <= 0.05:
            await self._report_speaking(False)
        elif time.monotonic() - self.speaking_reported > SPEAKING_REPORT_SECONDS:
            await self._report_speaking(True)

    async def _post_spoken(self, text: str) -> None:
        """What the agent said in the call goes in Agent Chat too, for people in the editor and
        its speech bubble. Voices skip these, so nothing is said twice."""
        payload = {"author": self.agent, "role": "agent", "to": "", "code": self.code, "source": "spoken",
                   "text": f"(in the call) {text}"}
        try:
            await self.hub.post("/api/messages", json=payload, timeout=5)
        except httpx.HTTPError:
            pass

    # The call's chat

    async def _chat_surface(self):
        """Where to type in the call's chat: the side panel's box, in the page or an embedded frame."""
        box = self.page.get_by_role("textbox", name=re.compile("Send a message", re.I))
        if await box.count():
            return box.first
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            box = frame.locator('[role="textbox"][contenteditable="true"]')
            if await box.count():
                return box.first
        return None

    async def _open_chat(self) -> bool:
        try:
            if await self._chat_surface() is not None:
                return True
            button = self.page.get_by_role("button", name=re.compile(r"^Chat with everyone", re.I)).first
            if await button.count() and await button.is_visible():
                await button.click(timeout=3000)
                await asyncio.sleep(0.8)
            return await self._chat_surface() is not None
        except Exception:
            return False

    async def _chat(self, text: str) -> None:
        try:
            if not await self._open_chat():
                log(f"Voice: couldn't open the call's chat to write: {text}")
                return
            box = await self._chat_surface()
            await box.fill(text[:500], timeout=5000)
            await box.press("Enter")
        except Exception as error:
            log(f"Voice: couldn't write in the call's chat: {type(error).__name__}")

    async def _chat_step(self) -> None:
        await asyncio.sleep(POLL_SECONDS * 2)
        now = time.monotonic()
        if not self.chat_ready and now - self.chat_tried > CHAT_RETRY_SECONDS:
            self.chat_tried = now
            if await self._open_chat():
                log("Voice: reading the call's chat.")
                self.chat_ready = True
        rows = []
        for frame in self.page.frames:
            try:
                rows += await frame.evaluate("() => window.__copresenceEars ? window.__copresenceEars.chat() : []")
            except Exception:
                continue
        fresh = [row for row in rows if row["id"] not in self.chat_seen]
        first_look = not self.chat_seen
        self.chat_seen.update(row["id"] for row in fresh)
        if first_look:
            # Whatever was in the chat before the agent joined is history
            self.chat_seen.add("")
            return
        for row in fresh:
            speaker = row["speaker"]
            if speaker.lower() in self.turns.own or row["text"].startswith(f"{self.agent}:"):
                continue
            self._turn_finished(speaker, row["text"], "chat", now)

    # Results from the agent

    def _note_agents(self, agents: dict) -> None:
        self._agents = agents
        in_call = [name for name, status in agents.items() if status.get("call") == "in_call" and name != self.agent]
        self.aliases = core.agent_aliases(self.agent, in_call)
        self.other_aliases = set().union(*(core.agent_aliases(name, [self.agent]) for name in in_call)) if in_call \
            else set()

    _agents: dict = {}

    async def _watch_hub_step(self) -> None:
        try:
            reply = (await self.hub.get("/api/state", params={"after": self.after, "version": self.version})).json()
        except (httpx.HTTPError, ValueError):
            await asyncio.sleep(3)
            return
        self.version = reply["version"]
        self._note_agents(reply["agents"])
        for message in reply["messages"]:
            self.after = max(self.after, message["id"])
            if message.get("author") == self.agent and message.get("to") in self.askers \
                    and message.get("source") != "spoken":
                self.results.append((time.monotonic(), message["text"]))
                self.wake.set()
        status = reply["agents"].get(self.agent) or {}
        step = status.get("step") or status.get("task") or ""
        self.status_note = f"you are {status.get('state', 'idle')}" + (f": {step}" if step else "")
