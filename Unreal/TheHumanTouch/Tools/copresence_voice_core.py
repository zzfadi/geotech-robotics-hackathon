"""What every CoPresence voice shares, wherever it listens and speaks: the agent's voice for
people in the session (Tools/copresence_agent_voice.py, GPT-Live) and the voice in video calls
(Tools/copresence_call_voice.py, a group and OpenAI Realtime).

Nothing here does input or output, so it is all tested offline
(Tools/tests/test_voice_core.py):

- which voice an agent speaks with, the same in the editor and in calls;
- what people call the agent, and when a line is plainly said to it;
- the decision model's questions: is a line for the agent, and does it tell it to stop;
- typed chat commands (stop, quiet, mute, listen, help) and spoken stop phrases, which
  only make the agent stop talking at once while it speaks;
- when talking over the agent counts as interrupting it;
- the queue of things people asked the agent while it was busy;
- a timeline for each turn, so the logs show where the time goes.
"""

import re
import time
import zlib
from collections import deque
from dataclasses import dataclass, field

# Voices -------------------------------------------------------------------------------

# Voices both GPT-Live (editor) and Realtime (calls) have, so an agent sounds the same in both
SHARED_VOICES = ("marin", "cedar")
LIVE_VOICES = ("marin", "cedar", "gleam", "meridian", "vesper", "willow")
REALTIME_VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")


def voice_for(agent: str, appearance: dict | None, engine_voices: tuple[str, ...] = SHARED_VOICES) -> str:
    """The voice the agent picked, if this engine has it, or one of the shared voices that
    stays the same for its name."""
    chosen = str((appearance or {}).get("voice") or "").strip().lower()
    if chosen in engine_voices:
        return chosen
    return SHARED_VOICES[zlib.crc32(agent.encode()) % len(SHARED_VOICES)]


# Words ------------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lower case words and apostrophes only, single spaces: "Stop!  OK?" -> "stop ok"."""
    text = text.lower().replace("’", "'")
    return " ".join(re.findall(r"[a-z0-9']+", text))


def words(text: str) -> list[str]:
    return normalize(text).split()


# Names --------------------------------------------------------------------------------

# Said to get any agent's attention
GENERIC_NAMES = ("agent",)


def agent_aliases(agent: str, other_agents: tuple[str, ...] | list[str] = ()) -> set[str]:
    """What people call the agent: its whole name ("josh codex"), its last word ("codex")
    and "agent". A short name another agent in the call also has is left out, since it
    would call both."""
    full = normalize(agent)
    aliases = {full, *GENERIC_NAMES}
    parts = full.split()
    if len(parts) > 1:
        aliases.add(parts[-1])
    others = [normalize(other) for other in other_agents if normalize(other) != full]
    for other in others:
        # A short name another agent has too: its last word, or its whole name ("Codex")
        for shared in {other.split()[-1], other} if other else ():
            if shared in aliases and shared != full:
                aliases.discard(shared)
    if others:
        # With several agents in a call, "agent" alone doesn't say which one
        aliases.difference_update(GENERIC_NAMES)
    return {alias for alias in aliases if alias}


GREETINGS = ("hey", "hi", "hello", "ok", "okay", "yo", "so", "and", "um", "uh", "oh")


def _alias_pattern(aliases: set[str]) -> str:
    return "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))


def said_to(text: str, aliases: set[str]) -> bool:
    """Whether a line plainly calls the agent by name: "Codex, add a tree", "hey agent",
    "can you do that, Codex?". A name in the middle ("Codex did a good job") doesn't count;
    the decision model weighs those."""
    if not aliases:
        return False
    line = normalize(text)
    names = _alias_pattern(aliases)
    greeting = "|".join(GREETINGS)
    starts = re.match(rf"^(?:(?:{greeting}) )*(?:{names})\b", line)
    ends = re.search(rf"\b(?:{names})$", line)
    return bool(starts or ends)


def strip_address(text: str, aliases: set[str]) -> str:
    """The line without a leading greeting and name: "OK Codex, stop!" -> "stop"."""
    line = normalize(text)
    names = _alias_pattern(aliases) if aliases else "(?!x)x"
    greeting = "|".join(GREETINGS)
    line = re.sub(rf"^(?:(?:{greeting}|{names}) )*", "", line + " ").strip()
    return re.sub(rf"(?: (?:{names}))+$", "", line).strip()


# Commands -----------------------------------------------------------------------------

STOP = "stop"      # stop talking, and drop the work the agent is doing
QUIET = "quiet"    # stop talking; the work goes on
MUTE = "mute"      # stop listening to the call, until "listen"
LISTEN = "listen"
HELP = "help"

_POLITE = r"(?:(?:can|could|would|will) you |please |just |ok |okay |alright |all right |now |no |wait )*"
_AFTER = r"(?: (?:please|now|right now|for now|for a sec|for a second|thanks|thank you|ok|okay|it|that|this|everything|all))*"
_QUIET_CORE = (r"(?:be quiet|quiet|shh+|shush|hush|stop talking|stop speaking|enough|that's enough|that is enough|"
               r"wait|hold on|hang on|one sec|one second)")
_STOP_CORE = (r"(?:stop+(?: stop)*|stop working|stop doing that|stop what you're doing|stop what you are doing|"
              r"cancel|abort|never ?mind|forget it|forget about it|drop it|scrap that|don't do that|do not do that)")
QUIET_SPOKEN = re.compile(rf"^{_POLITE}{_QUIET_CORE}{_AFTER}$")
STOP_SPOKEN = re.compile(rf"^{_POLITE}{_STOP_CORE}{_AFTER}$")
# Spoken commands are short; anything longer is a sentence that happens to have the word in it
COMMAND_MAX_WORDS = 6


def spoken_command(text: str, aliases: set[str]) -> str | None:
    """STOP or QUIET when a whole spoken line is that command ("stop", "OK Codex, cancel
    that", "can you stop talking please"); None for sentences that merely contain the word
    ("we should stop the elevator on floor three")."""
    line = strip_address(text, aliases)
    if not line or len(line.split()) > COMMAND_MAX_WORDS:
        return None
    if QUIET_SPOKEN.match(line):
        return QUIET
    if STOP_SPOKEN.match(line):
        return STOP
    return None


CHAT_COMMANDS = {
    STOP: ("stop", "cancel", "abort", "stop working"),
    QUIET: ("quiet", "be quiet", "shh", "shush", "stop talking", "hush"),
    MUTE: ("mute", "stop listening", "pause listening", "don't listen", "dont listen", "mute yourself"),
    LISTEN: ("listen", "unmute", "start listening", "resume listening", "listen again"),
    HELP: ("help", "commands"),
}


def chat_command(text: str, aliases: set[str], other_aliases: set[str] = frozenset()) -> str | None:
    """A command typed in the call's chat, alone or after the agent's name or a slash:
    "stop", "/mute", "Codex listen". A command for another agent by name isn't for this one.
    "help" needs the name or a slash, since people say it to each other."""
    raw = text.strip()
    slash = raw.startswith("/")
    line = normalize(raw.lstrip("/"))
    others_only = other_aliases - aliases
    if others_only and re.match(rf"^(?:(?:{'|'.join(GREETINGS)}) )*(?:{_alias_pattern(others_only)})\b", line):
        return None
    named = said_to(line, aliases)
    line = strip_address(line, aliases)
    for command, phrases in CHAT_COMMANDS.items():
        if line in phrases:
            if command == HELP and not (slash or named):
                return None
            return command
    return None


HELP_TEXT = ("You can type these here: stop (I stop talking and drop what I'm doing), quiet (I stop talking), "
             "mute (I stop listening to the call), listen (I listen again). Saying \"stop\" or \"quiet\" on its "
             "own works too.")


# The decision model -----------------------------------------------------------------------
# OpenAI Decisions: it gets the conversation and fixed choices, and gives a probability for
# each choice, without writing any text. It decides whether a line is for the agent, and
# what someone wants when they tell it to stop. The phrase lists above only make it stop
# talking at once while it speaks; what a line means is the decision model's call.

DECISIONS_MODEL = "gpt-6-luna"
CONTEXT_TURNS = 14
TURN_CHOICES = ("respond", "raise_hand", "stay_silent")
CONTROL_CHOICES = ("none", "stop_talking", "stop_work", "stop_listening")
CONTROL_COMMANDS = {"stop_talking": QUIET, "stop_work": STOP, "stop_listening": MUTE}
# The likeliest control choice counts when it is at least this likely; muting is harder to
# undo, so it needs more
CONTROL_THRESHOLD = 0.5
MUTE_THRESHOLD = 0.8

TURN_QUESTION = (
    "Decide what {agent} should do after the latest line. respond: the latest line asks {agent} something, asks for "
    "work in the level, follows up on what {agent} just said, or clearly expects its input, whether or not it is "
    "named. raise_hand: {agent} has something useful to add but nobody asked. stay_silent: people are talking to "
    "each other, thinking aloud, or the line needs no answer from the agent.")
CONTROL_QUESTION = (
    "Is the latest line someone telling {agent} to stop something it is doing? stop_talking: they want it to stop "
    "talking or wait, while its work goes on (\"hold on\", \"quiet\", \"let me finish\"). stop_work: they want it to "
    "stop or cancel the work it is doing in the level (\"stop\", \"cancel that\", \"never mind the trees\"). "
    "stop_listening: they want it to stop listening for a while. none: anything else, including people telling "
    "each other to stop or never mind, and talk about stopping something in the level (\"stop the elevator on "
    "floor three\").")


def transcript_lines(history: list[tuple[str, str]], limit: int = CONTEXT_TURNS) -> str:
    return "\n".join(f"[{speaker or 'Someone'}]: {text}" for speaker, text in history[-limit:])


def decision_request(agent: str, history: list[tuple[str, str]], status_note: str,
                     questions: tuple[str, ...] = ("agent_turn", "control"),
                     setting: str = "a Google Meet call") -> dict:
    """The Decisions request for the latest line: whether the agent should answer it
    ("agent_turn") and whether it tells the agent to stop something ("control")."""
    context = (f"This is {setting}. {agent} is an AI agent in it; it works in a shared Unreal Engine level and can "
               f"see and change it. Lines marked [{agent}] are what it said. {status_note}\n\n"
               f"{transcript_lines(history)}")
    asked = {
        "agent_turn": {"type": "choice", "name": "agent_turn", "instructions": TURN_QUESTION.format(agent=agent),
                       "choices": [{"value": value} for value in TURN_CHOICES]},
        "control": {"type": "choice", "name": "control", "instructions": CONTROL_QUESTION.format(agent=agent),
                    "choices": [{"value": value} for value in CONTROL_CHOICES]},
    }
    return {"model": DECISIONS_MODEL, "input": [{"role": "user", "content": [{"type": "input_text", "text": context}]}],
            "questions": [asked[name] for name in questions]}


def _probabilities(answer: dict, choices: tuple[str, ...]) -> dict:
    probabilities = {}
    for item in answer.get("probabilities") or []:
        value = item.get("probability") if isinstance(item, dict) else None
        if isinstance(item, dict) and item.get("value") in choices and isinstance(value, (int, float)) \
                and not isinstance(value, bool) and 0.0 <= value <= 1.0:
            probabilities[item["value"]] = float(value)
    return probabilities


def read_decision(reply: dict) -> dict:
    """The choice and its probabilities from a Decisions reply, and the control
    probabilities when asked; anything malformed reads as staying silent and no control."""
    answers = reply.get("answers") if isinstance(reply, dict) else None
    answers = [answer for answer in answers if isinstance(answer, dict)] if isinstance(answers, list) else []
    turn = next((answer for answer in answers if answer.get("name") == "agent_turn"), {})
    decision = {"choice": turn.get("choice", "stay_silent"), "probabilities": _probabilities(turn, TURN_CHOICES)}
    control = next((answer for answer in answers if answer.get("name") == "control"), None)
    if control is not None:
        decision["control"] = _probabilities(control, CONTROL_CHOICES)
    return decision


def control_command(decision: dict) -> str | None:
    """STOP, QUIET or MUTE when the decision model says the line tells the agent to stop
    something, else None."""
    control = decision.get("control") or {}
    if not control:
        return None
    choice = max(control, key=control.get)
    if choice == "none" or control[choice] < CONTROL_THRESHOLD:
        return None
    if choice == "stop_listening" and control[choice] < MUTE_THRESHOLD:
        return None
    return CONTROL_COMMANDS[choice]


# Talking over the agent -----------------------------------------------------------------

# What people say to show they're listening; it doesn't take the floor
BACKCHANNELS = {
    "yeah", "yes", "yep", "yup", "ok", "okay", "mm", "mhm", "mm hmm", "uh huh", "uh-huh", "right", "sure", "cool",
    "nice", "great", "got it", "i see", "ah", "oh", "wow", "true", "exactly", "totally", "sounds good", "perfect",
    "thanks", "thank you", "good", "awesome", "hmm", "huh",
}
# A person talking over the agent stops it after this many words (from captions) ...
INTERRUPT_WORDS = 3
# ... or after this much unbroken speech in the call's audio, which arrives sooner
INTERRUPT_AUDIO_SECONDS = 0.6


def is_backchannel(text: str) -> bool:
    line = normalize(text)
    if line in BACKCHANNELS:
        return True
    parts = line.split()
    # "yeah yeah", "ok cool"
    return 0 < len(parts) <= 3 and all(part in BACKCHANNELS or " ".join(parts) in BACKCHANNELS for part in parts)


def interrupts(text: str, audio_seconds: float = 0.0) -> bool:
    """Whether someone talking while the agent speaks should stop it: real words, not "mm hmm"."""
    if audio_seconds >= INTERRUPT_AUDIO_SECONDS:
        return True
    return len(words(text)) >= INTERRUPT_WORDS and not is_backchannel(text)


# The mouth ----------------------------------------------------------------------------
# Mouth shapes for an avatar's face, worked out from the voice's own audio, 60 a second:
# how loud it is opens the jaw, and how bright it sounds (how often the wave crosses zero)
# picks between rounded lips ("oo", "oh") and wide ones ("ee", "s"). Silence between
# words closes the lips. Each frame is (jaw, funnel, lips_together, corner_pull), 0 to 1,
# the four mouth controls of the MetaHuman face rig that the Luna2 avatar drives.

MOUTH_FPS = 60
MOUTH_SAMPLE_RATE = 24000
# Loudness (RMS of 16-bit audio) for a fully open jaw, and below which the mouth is shut
MOUTH_OPEN_LEVEL = 4500.0
MOUTH_SILENT_LEVEL = 250.0
# Zero crossings per sample: below the first the sound is rounded, above the second wide
MOUTH_DARK_CROSSINGS = 0.06
MOUTH_BRIGHT_CROSSINGS = 0.18
# How fast the jaw follows: opening is quick, closing a little slower, like a real mouth
MOUTH_ATTACK = 0.6
MOUTH_RELEASE = 0.35


class MouthShapes:
    """Turns PCM16 audio at 24 kHz, piece by piece as it streams, into mouth frames at 60
    a second. It keeps what is left of a frame between pieces, and the jaw's smoothing."""

    def __init__(self):
        self.leftover = b""
        self.jaw = 0.0

    def feed(self, pcm: bytes) -> list[tuple[float, float, float, float]]:
        import array
        samples_per_frame = MOUTH_SAMPLE_RATE // MOUTH_FPS
        data = self.leftover + pcm
        whole = len(data) // (samples_per_frame * 2) * samples_per_frame * 2
        self.leftover = data[whole:]
        values = array.array("h")
        values.frombytes(data[:whole])
        frames = []
        for start in range(0, len(values), samples_per_frame):
            frame = values[start:start + samples_per_frame]
            level = (sum(sample * sample for sample in frame) / len(frame)) ** 0.5
            crossings = sum(1 for a, b in zip(frame, frame[1:]) if (a < 0) != (b < 0)) / len(frame)
            frames.append(self._shape(level, crossings))
        return frames

    def _shape(self, level: float, crossings: float) -> tuple[float, float, float, float]:
        target = 0.0 if level < MOUTH_SILENT_LEVEL else min(1.0, level / MOUTH_OPEN_LEVEL) ** 0.6
        rate = MOUTH_ATTACK if target > self.jaw else MOUTH_RELEASE
        self.jaw += (target - self.jaw) * rate
        jaw = round(self.jaw * 0.55, 3)
        if level < MOUTH_SILENT_LEVEL:
            # Between words the lips meet
            return (jaw, 0.0, round(0.35 * (1.0 - self.jaw), 3), 0.0)
        brightness = min(1.0, max(0.0, (crossings - MOUTH_DARK_CROSSINGS) /
                                   (MOUTH_BRIGHT_CROSSINGS - MOUTH_DARK_CROSSINGS)))
        funnel = round(0.6 * (1.0 - brightness) * self.jaw, 3)
        corner = round(0.25 * brightness * self.jaw, 3)
        return (jaw, funnel, 0.0, corner)


# The queue ----------------------------------------------------------------------------

# Something asked of the agent waits this long for its turn before it is stale
TURN_WAIT_SECONDS = 30.0
QUEUE_LIMIT = 4


@dataclass
class Turn:
    """One finished thing a person said or typed, in a call or the editor."""
    id: str
    speaker: str
    text: str
    source: str = "captions"     # "captions", "chat" or "editor"
    at: float = field(default_factory=time.monotonic)
    addressed: bool = False      # called the agent by name
    command: str | None = None   # STOP, QUIET, MUTE, LISTEN or HELP


class AskQueue:
    """What people asked the agent, answered one at a time in the order asked. A question
    left waiting too long is dropped: by then people have moved on."""

    def __init__(self, wait_seconds: float = TURN_WAIT_SECONDS, limit: int = QUEUE_LIMIT):
        self.wait_seconds = wait_seconds
        self.limit = limit
        self.turns: deque[Turn] = deque()

    def add(self, turn: Turn) -> Turn | None:
        """Queues a turn; returns the oldest one if the queue was full and it had to go."""
        dropped = None
        if len(self.turns) >= self.limit:
            dropped = self.turns.popleft()
        self.turns.append(turn)
        # Decisions can come back out of order; answers keep the order things were said
        self.turns = deque(sorted(self.turns, key=lambda queued: queued.at))
        return dropped

    def next(self, now: float | None = None) -> tuple[Turn | None, list[Turn]]:
        """The next turn to answer, and the stale ones dropped on the way."""
        now = time.monotonic() if now is None else now
        stale = []
        while self.turns and now - self.turns[0].at > self.wait_seconds:
            stale.append(self.turns.popleft())
        return (self.turns.popleft() if self.turns else None), stale

    def clear(self) -> list[Turn]:
        dropped = list(self.turns)
        self.turns.clear()
        return dropped

    def __len__(self) -> int:
        return len(self.turns)


# Timing -----------------------------------------------------------------------------

class Timeline:
    """When each step of a turn happened, so the log shows where the time goes. Times are
    milliseconds after the turn's first mark, usually when its words were final."""

    ORDER = ("speech_end", "final", "decided", "asked", "first_audio", "heard", "interrupted", "done")

    def __init__(self, keep: int = 50):
        self.keep = keep
        self.turns: dict[str, dict] = {}

    def mark(self, turn_id: str, step: str, at: float | None = None, note: str = "") -> None:
        record = self.turns.setdefault(turn_id, {"marks": {}, "notes": {}})
        record["marks"].setdefault(step, time.monotonic() if at is None else at)
        if note:
            record["notes"][step] = note
        while len(self.turns) > self.keep:
            self.turns.pop(next(iter(self.turns)))

    def since(self, turn_id: str, first: str, last: str) -> int | None:
        marks = self.turns.get(turn_id, {}).get("marks", {})
        if first not in marks or last not in marks:
            return None
        return round((marks[last] - marks[first]) * 1000)

    def line(self, turn_id: str) -> str:
        """"final 0, decided +412 (respond 0.93), asked +415, first_audio +1180, heard +1240"."""
        record = self.turns.get(turn_id)
        if not record:
            return ""
        marks = record["marks"]
        start = min(marks.values())
        parts = []
        for step in sorted(marks, key=lambda name: (marks[name], self.ORDER.index(name) if name in self.ORDER else 99)):
            offset = round((marks[step] - start) * 1000)
            note = record["notes"].get(step)
            parts.append(f"{step} {'+' if offset else ''}{offset}" + (f" ({note})" if note else ""))
        return ", ".join(parts)


# Promises -----------------------------------------------------------------------------

# A voice saying it will do something only the agent's runner can do
PROMISE = re.compile(
    r"\bon it\b|\bwill do\b|\bcoming (right )?up\b"
    r"|\bi'?ll (go ahead|do|put|add|move|make|place|get|grab|take|check|look|find|remove|delete|change|fix|set|turn)\b"
    r"|\b(doing|getting|putting|adding|moving|placing|making) (it|that|this|one|some|a|an|the)\b",
    re.IGNORECASE)


# An offer to do something once asked, which isn't a promise of anything yet
OFFER = re.compile(r"\bsay the word\b|\blet me know\b|\bjust (ask|tell me)\b|\bif you\b|\bwhen(ever)? you\b|\bonce you\b",
                   re.IGNORECASE)


def promised_work(text: str) -> bool:
    """Whether the voice said it is going to do something, which only the agent can do, rather
    than offering to once asked ("just say the word and I'll jump on it")."""
    sentences = re.split(r"[.!?]+", text.replace("\u2019", "'"))
    return any(PROMISE.search(sentence) and not OFFER.search(sentence) for sentence in sentences)
