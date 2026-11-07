# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "sounddevice>=0.5",
#     "httpx>=0.27",
#     "numpy>=1.24",
#     # Echo removal, only where it comes ready-built; elsewhere the microphone goes up as it is
#     "pywebrtc-audio==0.2.0; (sys_platform == 'darwin' or platform_machine == 'x86_64' or platform_machine == 'AMD64' or platform_machine == 'aarch64') and python_version < '3.15'",
# ]
# ///
"""Talk out loud with an agent in Unreal, from your editor or from VR.

The agent's voice runs on the agent's own machine (Tools/copresence_agent_voice.py), so this
machine needs no OpenAI key. This sends your microphone to that agent's voice through the session
hub, and plays what the voice says (Tools/copresence_voice_lines.py).

You talk with an agent in a call. Call on its row in the Agents tab rings until the agent's voice
answers; from the moment the hub puts you through, the microphone stays open and goes up the line all the
time, quiet moments included, until you hang up, call someone else, or nobody has said anything
for two minutes, when the call ends by itself. Only that agent hears you. Muting your microphone
keeps the call and sends quiet instead, so you still hear the agent; muting the agent keeps the
call too, and only its voice doesn't play, while its words still show in the captions. The VR game
is in a call with its agent for as long as it follows one, and holding a talk button (an older
VR app) still sends what you say while you hold it.

What agents say out loud to other people in the session plays here too, so everyone hears the
agents, except the ones you muted. While the microphone is open, everything played here goes out
through the same stream the microphone comes in on, and exactly that is taken out of what the
microphone hears, along with steady background noise, so an agent doesn't hear itself and cut
itself off. While an agent's voice plays, silence goes up instead of the microphone until you
have talked for half a second; then what you said goes up after all and the voice here stops, so
a cough or a word to someone next to you doesn't cut the agent off. Headphones still work best.

    uv run --script Tools/copresence_voice.py          talk with the agent the editor says
    uv run --script Tools/copresence_voice.py check    show the microphone and speaker it uses

The editor tells this which agent you are in a call with, which agents you muted, and whether
you hold the talk button, through Saved/CoPresence/voice_link.json, and this tells the editor
what the voice is doing, that a call is ringing, why a call ended by itself, what you and the
agent last said, for the call's live captions, and that your microphone gives no sound at all,
through Saved/CoPresence/voice_status.json.
"""

import argparse
import array
import asyncio
import collections
import functools
import json
import math
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

import copresence_voice_lines as lines

PROJECT_DIR = Path(__file__).resolve().parent.parent
# VR links through a folder of its own, and tests point this somewhere else
VOICE_DIR = Path(os.environ.get("COPRESENCE_VOICE_DIR") or PROJECT_DIR / "Saved" / "CoPresence")
LINK_FILE = VOICE_DIR / "voice_link.json"
STATUS_FILE = VOICE_DIR / "voice_status.json"
CODE_HEADER = "X-CoPresence-Code"

SAMPLE_RATE = 24000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MIC_BLOCK_FRAMES = 2400
# Echo removal works on 10 ms at a time, and the stream both ways hands it two of those per block
ECHO_FRAMES = SAMPLE_RATE // 100
STREAM_BLOCK_FRAMES = 2 * ECHO_FRAMES
# An editor that hasn't rewritten the link for this long has quit
LINK_STALE_SECONDS = 10.0
# The link is read this often, so holding the talk button is noticed at once
CHECK_SECONDS = 0.05
READ_TRIES = 3
READ_RETRY_SECONDS = 0.01
# What the editor says goes up the line when it changes, and this often anyway
LINK_SECONDS = 1.0
# A line that couldn't open, or ended with a problem, is not tried again sooner than this
RETRY_SECONDS = 15.0
# Nor is any line opened sooner than this after the last one
OPEN_GAP_SECONDS = 2.0
# A call in which nobody has talked for this long, neither you nor the agent, hangs up by itself
CALL_IDLE_SECONDS = 120.0
# The most agents one call holds, each on a line of its own
MAX_CALL_AGENTS = 8
# The status file is rewritten this often even when nothing changed, so the editor knows this runs
STATUS_REFRESH_SECONDS = 1.0
# Microphone frames waiting to go up; past this they are dropped rather than sent late
UP_FRAMES = 100
# When the voice is talked over, this much of what waits to play still plays, fading out, and the
# rest is dropped, so it stops soon and without a click
INTERRUPT_KEEP_SECONDS = 0.1
# While a voice plays here, you have to talk for this long before what you say goes up and the
# voice here stops
INTERRUPT_SPEECH_SECONDS = 0.5
# With hold to talk, what the microphone heard this long before the button went down and after it
# came up goes along too, so first and last words aren't clipped; then this much silence, so the
# voice hears you finished
HOLD_PREROLL_SECONDS = 0.3
HOLD_TAIL_SECONDS = 0.3
HOLD_SILENCE_SECONDS = 1.0
# With echo removal, a block counts as you talking when echo removal is at least this sure it heard
# a voice, and the block is this much louder than the quiet around you and above this level
# (about -50 dBFS)
SPEECH_PROBABILITY = 0.6
CLEAN_SPEECH_MIN_LEVEL = 100.0
# Without echo removal, talking is told from loudness alone: this much louder than the quiet around
# you, and never below this level (about -36 dBFS)
SPEECH_RATIO = 3.0
SPEECH_MIN_LEVEL = 500.0
# Without echo removal, while a voice plays, or just played, talking has to be this much louder
# again, so the speaker isn't taken for you
PLAYING_RATIO = 3.0
PLAYED_ECHO_SECONDS = 0.3
# What a voice sends counts as it talking above this level (about -60 dBFS): voices stream quiet
# between their words too, which mustn't hold your microphone back or keep a call from going quiet
AUDIBLE_LEVEL = 30.0
# Quiet a voice streams is kept to this much (0.2 s) while it has no words waiting
QUIET_KEPT_BYTES = SAMPLE_RATE * 2 // 5
# In a call with several agents, one talks at a time: its turn ends after this long without words
TURN_GAP_SECONDS = 1.0
# A microphone that gives nothing but zeros this long into a call isn't allowed to listen
SILENT_MICROPHONE_SECONDS = 3.0
NO_SOUND_PROBLEM = ("your microphone gives no sound; check System Settings > Privacy & Security > Microphone"
                    if sys.platform == "darwin" else
                    "your microphone gives no sound; check Settings > Privacy & security > Microphone")
# Pauses up to this long between words don't end the talking going on
SPEECH_GAP_SECONDS = 0.25
# How fast the quiet around you is learned: quickly when it gets quieter, slowly when louder
FLOOR_FALL = 0.1
FLOOR_RISE = 0.005
# The ring while a call waits to be put through: two short soft tones every two seconds, peaking
# at -20 dBFS, each fading in and out
RING_TONE_HZ = 425.0
RING_LEVEL_DBFS = -20.0
RING_BURST_SECONDS = 0.4
RING_GAP_SECONDS = 0.2
RING_REPEAT_SECONDS = 2.0
RING_FADE_SECONDS = 0.03
# What the voice says once it is ready, which ends the ring
ANSWERED_STATES = ("listening", "speaking", "waiting")
# The speaker for what agents say to others closes after this long without any
OTHERS_IDLE_SECONDS = 5.0
# A way to hear the others that broke opens again after this long, or this long for a hub that has none
OTHERS_RETRY_SECONDS = 5.0
OTHERS_UNSUPPORTED_RETRY_SECONDS = 60.0


def _log(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {text}", flush=True)


_logged: set[str] = set()


def _log_once(text: str) -> None:
    if text not in _logged:
        _logged.add(text)
        _log(text)


def _write_json(path: Path, payload: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(payload))
    try:
        partial.replace(path)
    except PermissionError:
        # Windows refuses while the editor reads the file; it is written again soon
        return False
    return True


def read_link(path: Path = LINK_FILE) -> dict | None:
    """The editor's link, or None if the editor isn't running. Windows refuses a read while the
    editor replaces the file, so a refused read is tried again a moment later."""
    for _ in range(READ_TRIES):
        try:
            link = json.loads(path.read_text())
            break
        except PermissionError:
            time.sleep(READ_RETRY_SECONDS)
        except (OSError, ValueError):
            return None
    else:
        return None
    if time.time() - link.get("time", 0.0) > LINK_STALE_SECONDS:
        return None
    return link


def calls(link: dict | None) -> bool:
    """Whether the person is in a call with the agent they follow. The editor says so with "call",
    an older one with "open"; the VR game says neither and is in a call with its agent for as long
    as it follows one without a talk button."""
    if not link or not link.get("following"):
        return False
    call = link.get("call", link.get("open"))
    if call is None:
        return not link.get("hold")
    return bool(call)


def rings(link: dict) -> bool:
    """Whether a call rings while it waits to be put through: the editor's do, the VR game's don't."""
    return "call" in link or "open" in link


def call_agents(link: dict | None) -> list[str]:
    """The agents in the call, the first called first: the editor names them all, the VR game and
    older editors the one they follow."""
    names = (link or {}).get("call_agents")
    if isinstance(names, list) and names:
        return [str(name) for name in names if name][:MAX_CALL_AGENTS]
    following = (link or {}).get("following") or ""
    return [following] if following else []


def muted_agents(link: dict | None) -> set[str]:
    """The agents this person muted: none of them is heard here, and a call with one goes on without its voice."""
    names = (link or {}).get("muted_agents")
    return {str(name) for name in names} if isinstance(names, list) else set()


def refusal(status_code: int, body: bytes) -> str:
    """Why the hub didn't open a line, in a few words for the Agents tab."""
    try:
        error = str(json.loads(body).get("error") or "")
    except (ValueError, AttributeError):
        error = ""
    if status_code == 403:
        return "the session code is wrong"
    if status_code == 404:
        return "the session hub is too old for voice"
    return error or f"the session hub answered {status_code}"


# Audio ------------------------------------------------------------------------------

_open_speakers: set = set()


def mix(sounds: list[bytes], size: int) -> bytes:
    """Sounds played at the same time, added up and kept within what PCM16 can hold, then padded
    with silence to size bytes."""
    playing = [sound for sound in sounds if sound]
    if not playing:
        return bytes(size)
    if len(playing) == 1:
        return playing[0] + bytes(size - len(playing[0]))
    import numpy
    total = numpy.zeros(size // 2, dtype=numpy.int32)
    for sound in playing:
        samples = numpy.frombuffer(sound, dtype=numpy.int16)
        total[:len(samples)] += samples
    return numpy.clip(total, -32768, 32767).astype(numpy.int16).tobytes()


def to_mono(pcm: bytes, channels: int) -> bytes:
    """Interleaved PCM16 on several channels, mixed down to one."""
    if channels == 1:
        return pcm
    import numpy
    samples = numpy.frombuffer(pcm, dtype=numpy.int16).reshape(-1, channels).astype(numpy.int32)
    return (samples.sum(axis=1) // channels).astype(numpy.int16).tobytes()


def fade_out(pcm: bytes) -> bytes:
    samples = array.array("h", pcm[:len(pcm) // 2 * 2])
    count = len(samples)
    for index in range(count):
        samples[index] = samples[index] * (count - index) // count
    return samples.tobytes()


@functools.lru_cache(maxsize=1)
def ring_tone() -> bytes:
    """One round of the ring: two short soft tones a moment apart, then quiet."""
    samples = array.array("h", bytes(round(RING_REPEAT_SECONDS * SAMPLE_RATE) * 2))
    burst = round(RING_BURST_SECONDS * SAMPLE_RATE)
    fade = round(RING_FADE_SECONDS * SAMPLE_RATE)
    peak = 32767 * 10 ** (RING_LEVEL_DBFS / 20)
    for start in (0, round((RING_BURST_SECONDS + RING_GAP_SECONDS) * SAMPLE_RATE)):
        for index in range(burst):
            envelope = min(1.0, index / fade, (burst - index) / fade)
            samples[start + index] = round(peak * envelope * math.sin(2 * math.pi * RING_TONE_HZ * index / SAMPLE_RATE))
    return samples.tobytes()


class Ring:
    """The ring while a call waits to be put through, from the start of a tone, round and round."""

    def __init__(self):
        self.position = 0

    def take(self, size: int) -> bytes:
        tone = ring_tone()
        taken = bytearray()
        while len(taken) < size:
            piece = tone[self.position:self.position + size - len(taken)]
            taken.extend(piece)
            self.position = (self.position + len(piece)) % len(tone)
        return bytes(taken)


def echo_remover():
    """WebRTC's echo removal and noise suppression for one call, as in Chrome's calls, or None
    where it isn't installed, as on a machine it doesn't come ready-built for; the call then goes
    on with the microphone as it is."""
    try:
        from pywebrtc_audio import AudioProcessor
        return AudioProcessor(sample_rate=SAMPLE_RATE, echo_cancellation=True, noise_suppression=True,
                              high_pass_filter=True, auto_gain_control=False)
    except Exception as error:
        # Whatever the reason, voice still works without it
        _log_once(f"Echo removal isn't available here ({error}); the microphone goes up as it is, so use headphones.")
        return None


class Speaker:
    """Speaker out, as mono PCM16 at 24 kHz: what an agent's voice says, played as it comes. The
    sound waiting to play stays when the speaker's stream closes, so another stream can play it."""

    def __init__(self):
        self.pending = bytearray()
        self.lock = threading.Lock()
        self.streams = []
        # When the last audible sound waiting to play will have played, judged as it came in
        self.audible_until = 0.0
        # How much of what waits to play, from its start, has words in it, and when words last played
        self.words_bytes = 0
        self.words_played_at = float("-inf")
        # The voices of a call with several agents, each waiting to play on its own, and the one
        # that talks now while the others wait their turn
        self.voices: list["Speaker"] = []
        self.talker: "Speaker | None" = None

    def new_voice(self) -> "Speaker":
        """A voice of its own, such as one agent's in a call, played with everything else here."""
        voice = Speaker()
        self.voices.append(voice)
        return voice

    def drop_voice(self, voice: "Speaker") -> None:
        if voice in self.voices:
            self.voices.remove(voice)

    def is_open(self) -> bool:
        return bool(self.streams)

    def start(self) -> None:
        self._open_output()

    def _open_output(self) -> None:
        import sounddevice
        self._open([sounddevice.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                                blocksize=MIC_BLOCK_FRAMES // 2, callback=self._speak)])

    def _open(self, streams: list) -> None:
        try:
            for stream in streams:
                stream.start()
        except Exception:
            for stream in streams:
                stream.close()
            raise
        self.streams = streams
        _open_speakers.add(self)

    def stop(self) -> None:
        for stream in self.streams:
            stream.stop()
            stream.close()
        self.streams = []
        _open_speakers.discard(self)

    def _speak(self, output, frames, time_info, status) -> None:
        output[:] = self._mix(len(output))

    def _mix(self, size: int) -> bytes:
        return mix([self.take(size)] + self._take_voices(size), size)

    def _take_voices(self, size: int) -> list[bytes]:
        """What the voices of a call play now. Agents take turns rather than talk over each other:
        the one talking finishes what it has to say while the others keep theirs until it's done."""
        voices = list(self.voices)
        if self.talker not in voices or not self.talker.holds_turn():
            self.talker = next((voice for voice in voices if voice.has_words()), None)
        return [voice.take(size) for voice in voices if voice is self.talker or not voice.has_words()]

    def has_words(self) -> bool:
        with self.lock:
            return self.words_bytes > 0

    def holds_turn(self) -> bool:
        """Whether this voice is still talking, short pauses between its words included."""
        with self.lock:
            return self.words_bytes > 0 or time.monotonic() - self.words_played_at < TURN_GAP_SECONDS

    def take(self, size: int) -> bytes:
        """Up to size bytes of what waits to play, for the stream that plays it now."""
        with self.lock:
            taken = bytes(self.pending[:size])
            del self.pending[:size]
            if self.words_bytes and taken:
                self.words_played_at = time.monotonic()
            self.words_bytes = max(0, self.words_bytes - len(taken))
            if not self.words_bytes and len(self.pending) > QUIET_KEPT_BYTES:
                # Quiet that piled up while a voice waited its turn would only delay its next words
                del self.pending[:len(self.pending) - QUIET_KEPT_BYTES]
        return taken

    def is_playing(self) -> bool:
        """Whether something audible plays, or did a moment ago, which the microphone may still hear.
        The quiet a voice streams between its words doesn't count."""
        return time.monotonic() < self.audible_until + PLAYED_ECHO_SECONDS or \
            any(voice.is_playing() for voice in list(self.voices))

    def play(self, pcm: bytes) -> None:
        audible = level(pcm) > AUDIBLE_LEVEL
        with self.lock:
            self.pending.extend(pcm)
            if audible:
                self.audible_until = time.monotonic() + len(self.pending) / BYTES_PER_SECOND
                self.words_bytes = len(self.pending)

    def buffered_seconds(self) -> float:
        with self.lock:
            return len(self.pending) / BYTES_PER_SECOND

    def interrupt(self) -> None:
        """The voice is talked over: what it sent ahead is dropped, so it stops soon."""
        keep = int(INTERRUPT_KEEP_SECONDS * BYTES_PER_SECOND) // 2 * 2
        with self.lock:
            if len(self.pending) > keep:
                self.pending[:] = fade_out(bytes(self.pending[:keep]))
            self.audible_until = min(self.audible_until, time.monotonic() + len(self.pending) / BYTES_PER_SECOND)
            self.words_bytes = min(self.words_bytes, len(self.pending))
        for voice in list(self.voices):
            voice.interrupt()

    def clear(self) -> None:
        with self.lock:
            self.pending.clear()
            self.audible_until = 0.0
            self.words_bytes = 0
        for voice in list(self.voices):
            voice.clear()


class Audio(Speaker):
    """Your microphone and the speaker during a call, as one stream both ways, mono PCM16 at
    24 kHz. Everything that plays here goes out through it: the agent you talk with, what agents
    say to others, and the ring. Exactly what it plays is taken out of what the microphone hears in
    the same moment, along with steady background noise, before it goes to the line. Open only
    while a call or the talk button needs it, so the microphone is off (and macOS shows no
    microphone light) the rest of the time; before that, the ring has a speaker stream of its own."""

    # Opening the devices can wait, such as while macOS asks whether this may use the microphone
    opens_devices = True

    def __init__(self, loop: asyncio.AbstractEventLoop, others: Speaker | None = None):
        super().__init__()
        self.loop = loop
        # What agents say to others, which plays through this stream while the microphone is open
        self.others = others
        # What the microphone heard, with when it heard it and how sure echo removal is that it
        # was a voice (None without echo removal)
        self.chunks: asyncio.Queue[tuple[float, bytes, float | None]] = asyncio.Queue(maxsize=100)
        self.ringer: Ring | None = None
        # Whether the open stream is the one both ways, with the microphone
        self.listening = False
        self.remover = None
        self.heard = bytearray()
        self.probabilities: list[float] = []
        # How many channels the microphone comes in on, and whether it has given any sound at all
        # since it opened, when
        self.input_channels = 1
        self.heard_sound = False
        self.opened_at = 0.0

    def start(self) -> None:
        import sounddevice
        # The ring's own speaker stream gives way to the one both ways
        Speaker.stop(self)
        # PortAudio lists the devices once, so a microphone that went away and came back since,
        # as a wireless one does when it sleeps, would fail to open until it looks again. Looking
        # again would cut off another speaker that is playing, so then it doesn't
        if not _open_speakers:
            sounddevice._terminate()
            sounddevice._initialize()
        self.remover = echo_remover()
        self.heard.clear()
        self.probabilities.clear()
        # A wireless microphone's receiver can carry it on either of two channels, so both are taken
        # and mixed down
        self.input_channels = min(2, max(1, int(sounddevice.query_devices(kind="input")["max_input_channels"])))
        self.heard_sound = False
        self.opened_at = time.monotonic()
        stream = sounddevice.RawStream(samplerate=SAMPLE_RATE, channels=(self.input_channels, 1), dtype="int16",
                                       blocksize=STREAM_BLOCK_FRAMES, callback=self._both_ways)
        if self.remover is not None:
            # Roughly how long the speaker's sound takes to come back in the microphone, so echo
            # removal finds it from the first words on instead of after a second or so
            self.remover.stream_delay_ms = round(sum(stream.latency) * 1000)
        self._open([stream])
        self.listening = True

    def stop(self) -> None:
        super().stop()
        self.listening = False
        self.remover = None
        self.clear()

    def ring(self, ringing: bool) -> None:
        """Plays the ring while a call waits to be put through. Without the microphone open it
        has a speaker stream of its own, which closes once the ring is over and the voice has
        nothing more to play."""
        if not ringing:
            self.ringer = None
            if self.streams and not self.listening and not self.is_playing():
                Speaker.stop(self)
            return
        if self.ringer is not None:
            return
        self.ringer = Ring()
        if self.streams or not self.opens_devices:
            return
        try:
            self._open_output()
        except Exception as error:
            _log(f"Couldn't play the ring: {error}")

    def _mix(self, size: int, with_others: bool = False) -> bytes:
        sounds = [self.take(size)] + self._take_voices(size)
        if with_others and self.others is not None:
            sounds.append(self.others.take(size))
        ringer = self.ringer
        if ringer is not None:
            sounds.append(ringer.take(size))
        return mix(sounds, size)

    def _both_ways(self, indata, outdata, frames, time_info, status) -> None:
        played = self._mix(len(outdata), with_others=True)
        outdata[:] = played
        self._hear(to_mono(bytes(indata), self.input_channels), played)

    def gives_no_sound(self) -> bool:
        """Whether the microphone has given nothing but zeros for a while, as one does that the
        system doesn't let this use."""
        return self.listening and not self.heard_sound and time.monotonic() - self.opened_at >= SILENT_MICROPHONE_SECONDS

    def _hear(self, near: bytes, far: bytes) -> None:
        """What the microphone heard in one block of the stream, with what the speaker played in
        the same block, which echo removal takes out of it. It goes on in blocks of 100 ms."""
        if not self.heard_sound and any(near):
            self.heard_sound = True
        if self.remover is not None:
            try:
                self._remove_echo(near, far)
            except Exception as error:
                # A broken echo removal would otherwise stop the stream, and with it the microphone
                self.remover = None
                self.loop.call_soon_threadsafe(_log, f"Echo removal stopped working ({error}); the microphone goes up as it is.")
        if self.remover is None:
            self.heard.extend(near)
        block = MIC_BLOCK_FRAMES * 2
        while len(self.heard) >= block:
            chunk = bytes(self.heard[:block])
            del self.heard[:block]
            probability = sum(self.probabilities) / len(self.probabilities) if self.probabilities else None
            self.probabilities.clear()
            self.loop.call_soon_threadsafe(self._queue, time.monotonic(), chunk, probability)

    def _remove_echo(self, near: bytes, far: bytes) -> None:
        import numpy
        near_samples = numpy.frombuffer(near, dtype=numpy.int16)
        far_samples = numpy.frombuffer(far, dtype=numpy.int16)
        cleaned = []
        probabilities = []
        for start in range(0, len(near_samples), ECHO_FRAMES):
            end = start + ECHO_FRAMES
            cleaned.append(self.remover.process(near_samples[start:end], far_samples[start:end]).tobytes())
            probabilities.append(self.remover.speech_probability)
        self.heard.extend(b"".join(cleaned))
        self.probabilities.extend(probabilities)

    def _queue(self, heard_at: float, chunk: bytes, probability: float | None) -> None:
        if not self.chunks.full():
            self.chunks.put_nowait((heard_at, chunk, probability))


class FileAudio(Audio):
    """For tests: plays a recording into the conversation instead of the microphone, then
    silence, and throws the voice's audio away while counting it."""

    opens_devices = False

    def __init__(self, loop: asyncio.AbstractEventLoop, recording: Path):
        super().__init__(loop)
        self.recording = recording.read_bytes()
        self.received = 0
        self.first_received_at = None
        self.started_at = None
        self.task = None

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.task = asyncio.ensure_future(self._feed())

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
        first = "" if self.first_received_at is None else \
            f", the first {self.first_received_at - self.started_at:.1f} s after the microphone opened"
        _log(f"Test audio: the voice sent {self.received / BYTES_PER_SECOND:.1f} s of speech{first}.")

    async def _feed(self) -> None:
        block = MIC_BLOCK_FRAMES * 2
        position = 0
        while True:
            chunk = self.recording[position:position + block] or b"\x00" * block
            position += block
            await self.chunks.put((time.monotonic(), chunk, None))
            await asyncio.sleep(MIC_BLOCK_FRAMES / SAMPLE_RATE)

    def play(self, pcm: bytes) -> None:
        if self.first_received_at is None:
            self.first_received_at = time.monotonic()
        self.received += len(pcm)

    def new_voice(self) -> Speaker:
        voice = _CountedVoice(self)
        self.voices.append(voice)
        return voice


class _CountedVoice(Speaker):
    """One agent's voice in a test call: what it sends is counted, not played."""

    def __init__(self, counter: FileAudio):
        super().__init__()
        self.counter = counter

    def play(self, pcm: bytes) -> None:
        self.counter.play(pcm)


def level(chunk: bytes) -> float:
    """How loud a chunk of PCM16 is: the root mean square of its samples."""
    samples = array.array("h", chunk[:len(chunk) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


class SpeechDetector:
    """Tells when you talk, block by block. With echo removal, a block is you talking when echo
    removal is fairly sure it heard a voice and the block is also clearly louder than the quiet
    around you: that certainty lingers a moment after each word, and now and then rises over what
    is left of the speaker in the microphone, both of which are far quieter than you talking.
    Without echo removal, loudness alone tells, and a voice playing here has to be outdone."""

    def __init__(self):
        self.floor = 0.0
        # When the last block of you talking was heard
        self.spoke_at = float("-inf")
        # How much of you talking was heard since the talking going on now began, short pauses
        # between words aside, and when it began
        self.talked_frames = 0
        self.started_at = 0.0

    @property
    def talked_seconds(self) -> float:
        return self.talked_frames / SAMPLE_RATE

    def hear(self, heard_at: float, chunk: bytes, probability: float | None, playing: bool) -> bool:
        loudness = level(chunk)
        if not self.floor:
            self.floor = loudness
        if probability is None:
            threshold = max(SPEECH_MIN_LEVEL, self.floor * SPEECH_RATIO) * (PLAYING_RATIO if playing else 1.0)
            talking = loudness > threshold
        else:
            threshold = max(CLEAN_SPEECH_MIN_LEVEL, self.floor * SPEECH_RATIO)
            talking = probability > SPEECH_PROBABILITY and loudness > threshold
        if not talking:
            self.floor += (loudness - self.floor) * (FLOOR_FALL if loudness < self.floor else FLOOR_RISE)
            if heard_at - self.spoke_at > SPEECH_GAP_SECONDS:
                self.talked_frames = 0
            return False
        if not self.talked_frames:
            self.started_at = heard_at
        self.talked_frames += len(chunk) // 2
        self.spoke_at = heard_at
        return True


class Microphone:
    """The microphone while a call or the talk button needs it: tells when you talk, and hands
    the lines that are open, one to each agent in the call, what should go up them."""

    def __init__(self, audio: Audio, speakers: tuple):
        self.audio = audio
        # Whatever plays here, which the microphone may hear too
        self.speakers = speakers
        self.detector = SpeechDetector()
        self.lines: list["Line"] = []
        self.task: asyncio.Task | None = None
        # Whether you talked over the voice playing now, and what you said over it so far, which
        # goes up once it is clear you mean it
        self.talked_over = False
        self.held: collections.deque[tuple[float, bytes]] = collections.deque()

    @property
    def is_open(self) -> bool:
        return self.task is not None

    async def open(self) -> None:
        try:
            # Off the loop when it can wait, so the editor keeps hearing that this runs
            if getattr(self.audio, "opens_devices", False):
                await asyncio.to_thread(self.audio.start)
            else:
                self.audio.start()
        except Exception as error:
            raise RuntimeError(f"the microphone or speaker didn't open ({error})") from error
        self.detector = SpeechDetector()
        self.hand_to(self.lines)
        self.task = asyncio.create_task(self._hear())

    def close(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        self.task = None
        self.audio.stop()

    def hand_to(self, lines: list["Line"]) -> None:
        """What is heard goes to these lines from now on."""
        self.lines = list(lines)
        self.talked_over = False
        self.held.clear()

    def _send(self, heard_at: float, chunk: bytes) -> None:
        for line in self.lines:
            line.hear(heard_at, chunk)

    async def _hear(self) -> None:
        while True:
            heard_at, chunk, probability = await self.audio.chunks.get()
            playing = any(speaker.is_playing() for speaker in self.speakers)
            self.detector.hear(heard_at, chunk, probability, playing)
            if self.lines:
                self._pass_on(heard_at, chunk, playing)

    def _pass_on(self, heard_at: float, chunk: bytes, playing: bool) -> None:
        """In a call, while a voice plays here, silence goes up instead of the microphone until
        you have talked for a moment, so neither what is left of the voice in the microphone nor a
        small noise cuts the agent off. Then what you said so far goes up after all, the voice here
        stops, and the microphone goes up as it is until the voice has stopped. Holding the talk
        button already says you mean it."""
        if any(line.hold for line in self.lines) or not playing:
            self.talked_over = False
            self.held.clear()
            self._send(heard_at, chunk)
            return
        if self.talked_over:
            self._send(heard_at, chunk)
            return
        # Kept from when the talking going on now began, pauses between words included
        self.held.append((heard_at, chunk))
        while self.held and (not self.detector.talked_frames or self.held[0][0] < self.detector.started_at):
            self.held.popleft()
        if self.detector.talked_seconds < INTERRUPT_SPEECH_SECONDS:
            self._send(heard_at, bytes(len(chunk)))
            return
        self.talked_over = True
        for kept_at, kept in self.held:
            self._send(kept_at, kept)
        self.held.clear()
        self.audio.interrupt()


# One line to one agent's voice ---------------------------------------------------------

class Line:
    """Your line to one agent's voice, through the hub, for one call or conversation: the
    microphone goes up it, and the voice comes down it to the speaker."""

    def __init__(self, audio: Audio, link: dict):
        self.audio = audio
        # The agent's voice, which plays with everything else here, other agents in the call included
        self.voice = audio.new_voice()
        # What the microphone heard, which the helper's Microphone hands this line
        self.heard: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue(maxsize=UP_FRAMES)
        self.agent = link["following"]
        self.person = link.get("person") or "Someone"
        self.link = link
        self.hub = httpx.AsyncClient(base_url=link["hub"].rstrip("/"), headers={CODE_HEADER: link.get("code", "")},
                                     timeout=httpx.Timeout(10.0, read=lines.STREAM_IDLE_SECONDS))
        self.up: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=UP_FRAMES)
        self.status: dict = {"state": "connecting"}
        self.problem = ""
        # False when trying again can't help, such as an OpenAI account without credit
        self.retry = True
        self.link_sent: dict | None = None
        self.link_sent_at = 0.0
        self.hung_up = False
        # Whether the hub put the line through to the voice, and whether the voice has answered:
        # it said it listens, or spoke
        self.connected = False
        self.answered = False
        # In a call the microphone goes up the line all the time, and the editor's calls ring
        # until the voice answers, which takes a second or two after the line is through
        self.call = calls(link)
        self.rings = self.call and rings(link)
        # With hold to talk, the line opens when the talk button goes down; when it last went
        # down, and when it was last seen down
        self.hold = not self.call and bool(link.get("hold"))
        self.talking = self.hold and bool(link.get("talking"))
        self.pressed_at = self.talked_at = time.monotonic()
        # When someone last talked in the call, either side
        self.active_at = self.pressed_at
        # With the microphone muted the call goes on and the voice hears quiet; with the agent
        # muted it goes on too, and only its voice doesn't play here
        self.mic_muted = bool(link.get("mic_muted"))
        self.agent_muted = self.agent in muted_agents(link)

    def hear(self, heard_at: float, chunk: bytes) -> None:
        if not self.heard.full():
            self.heard.put_nowait((heard_at, chunk))

    def ringing(self) -> bool:
        return self.rings and not self.answered and not self.hung_up

    def quiet_for(self, now: float, spoke_at: float, playing: bool) -> float:
        """How long nobody has talked in the call: not you, not the voice, and the agent isn't
        working on something you asked for, which it will talk about once it is done."""
        if playing or self.status.get("state") in ("waiting", "speaking"):
            self.active_at = now
        else:
            self.active_at = max(self.active_at, spoke_at)
        return now - self.active_at

    def update(self, link: dict) -> None:
        """Sends what the editor says up the line: which agent you follow, muted, whether you hold
        the talk button, your selection."""
        self.link = link
        self.mic_muted = bool(link.get("mic_muted"))
        agent_muted = self.agent in muted_agents(link)
        if agent_muted and not self.agent_muted:
            # Muted while it talks, it stops at once rather than after what it already sent
            self.voice.clear()
        self.agent_muted = agent_muted
        talking = self.hold and bool(link.get("talking"))
        now = time.monotonic()
        if talking and not self.talking:
            self.pressed_at = now
        if talking:
            self.talked_at = now
        self.talking = talking
        # Each agent in a call hears which agent its line is and who else is in the call
        said = {"following": self.agent, "call_agents": call_agents(link), "muted": bool(link.get("muted")),
                "hold": self.hold, "talking": talking, "selection": link.get("selection") or []}
        if said == self.link_sent and time.monotonic() - self.link_sent_at < LINK_SECONDS:
            return
        self.link_sent, self.link_sent_at = said, time.monotonic()
        self._send_up(lines.frame({"kind": "link", **said}))

    def hang_up(self) -> None:
        """Ends the way up, which ends the line; the way down closes after it."""
        if self.hung_up:
            return
        self.hung_up = True
        while self.up.full():
            self.up.get_nowait()
        self.up.put_nowait(None)

    def _send_up(self, data: bytes) -> None:
        if not self.hung_up and not self.up.full():
            self.up.put_nowait(data)

    async def run(self) -> None:
        """Keeps the line until the voice ends the conversation, you hang up, or it breaks."""
        way_up = microphone = None
        try:
            async with self.hub.stream("GET", "/api/voice/person", params={"agent": self.agent, "person": self.person}) as down:
                if down.status_code != 200:
                    self.problem = refusal(down.status_code, await down.aread())
                    _log(f"No line to {self.agent}: {self.problem}.")
                    return
                reader = lines.FrameReader()
                async for chunk in down.aiter_raw():
                    for header, payload in reader.feed(chunk):
                        kind = header.get("kind")
                        if kind == "line" and way_up is None:
                            _log(f"Talking with {self.agent}.")
                            self.connected = True
                            self.active_at = time.monotonic()
                            way_up = asyncio.create_task(self._way_up(str(header.get("line", ""))))
                            microphone = asyncio.create_task(self._send_microphone())
                        elif kind == "audio":
                            self.answered = True
                            if not self.agent_muted:
                                self.voice.play(payload)
                        elif kind == "flush":
                            self.voice.interrupt()
                        elif kind == "status":
                            self.status = header
                            if header.get("state") in ANSWERED_STATES:
                                self.answered = True
                        elif kind == "closed":
                            self.problem = str(header.get("problem") or "")
                            self.retry = bool(header.get("retry", True))
                            return
                    if way_up is not None and way_up.done() and not self.hung_up:
                        raise RuntimeError(f"the microphone's way to {self.agent} broke")
                self.problem = f"the line to {self.agent} broke"
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as error:
            self.problem = "the session hub can't be reached" if isinstance(error, httpx.ConnectError) else str(error) \
                or type(error).__name__
        finally:
            if self.problem:
                _log(f"The line to {self.agent} ended: {self.problem}.")
            self.hang_up()
            if microphone is not None:
                microphone.cancel()
            if way_up is not None:
                try:
                    await asyncio.wait_for(way_up, timeout=3)
                except Exception:
                    # The line is over either way
                    way_up.cancel()
            self.audio.drop_voice(self.voice)
            await self.hub.aclose()

    async def _way_up(self, line_id: str) -> None:
        async def frames():
            while True:
                try:
                    data = await asyncio.wait_for(self.up.get(), timeout=lines.PING_SECONDS)
                except asyncio.TimeoutError:
                    data = lines.frame({"kind": "ping"})
                if data is None:
                    return
                yield data

        self.link_sent = None
        self.update(self.link)
        await self.hub.post("/api/voice/person", params={"line": line_id}, content=frames())

    async def _send_microphone(self) -> None:
        """Sends the microphone up the line. In a call, everything the Microphone hands it goes,
        quiet moments included. With hold to talk, only what you said while you held the talk
        button goes, a moment either side of it, and then a little silence."""
        # The last moment of what was heard while the button was up, in case it goes down
        earlier: collections.deque[tuple[float, bytes]] = collections.deque()
        finished = True
        while True:
            heard_at, chunk = await self.heard.get()
            if not self.hold:
                self._send_up(lines.frame({"kind": "audio"}, bytes(len(chunk)) if self.mic_muted else chunk))
                continue
            if self.pressed_at - HOLD_PREROLL_SECONDS <= heard_at <= self.talked_at + HOLD_TAIL_SECONDS:
                for kept_at, kept in earlier:
                    if kept_at >= self.pressed_at - HOLD_PREROLL_SECONDS:
                        self._send_up(lines.frame({"kind": "audio"}, kept))
                earlier.clear()
                self._send_up(lines.frame({"kind": "audio"}, chunk))
                finished = False
                continue
            if not finished:
                silence = b"\x00" * (MIC_BLOCK_FRAMES * 2)
                for _ in range(round(HOLD_SILENCE_SECONDS * SAMPLE_RATE / MIC_BLOCK_FRAMES)):
                    self._send_up(lines.frame({"kind": "audio"}, silence))
                finished = True
            earlier.append((heard_at, chunk))
            while heard_at - earlier[0][0] > HOLD_PREROLL_SECONDS:
                earlier.popleft()


# Main loop ------------------------------------------------------------------------------

class _StatusFile:
    """What the voice is doing, for the editor: written when it changes, and now and then anyway."""

    def __init__(self):
        self.shown: dict | None = None
        self.shown_at = 0.0

    def show(self, status: dict) -> None:
        if status == self.shown and time.monotonic() - self.shown_at < STATUS_REFRESH_SECONDS:
            return
        if _write_json(STATUS_FILE, {**status, "time": time.time()}):
            self.shown, self.shown_at = status, time.monotonic()


def line_status(line: Line) -> dict:
    status = {key: line.status.get(key, "") for key in ("state", "heard", "said", "problem")}
    state = "ringing" if line.ringing() else status["state"] or "connecting"
    return {**status, "agent": line.agent, "person": line.person, "state": state}


class Others:
    """What agents say out loud to other people in the session, played here too, so everyone hears
    the agents, except the ones you muted. Its speaker opens when one of them speaks and closes a
    while after; while the microphone is open, it plays through the microphone's stream instead,
    so it is taken out of what the microphone hears too."""

    def __init__(self):
        self.speaker = Speaker()
        self.task: asyncio.Task | None = None
        # The hub, session code and person the way to the others is open for
        self.target: tuple | None = None
        self.heard_at = 0.0
        self.speaker_failed_at = -OTHERS_IDLE_SECONDS
        # The agents you muted, and the agent heard last
        self.muted: set[str] = set()
        self.speaking = ""
        # Whether the microphone's stream plays what the others hear for now
        self.lent = False

    def follow(self, link: dict | None) -> None:
        """Keeps the way to the others open while the editor runs, to the hub it names."""
        target = (link["hub"], link.get("code", ""), link["person"]) if link and link.get("hub") and link.get("person") else None
        if target != self.target:
            self.close()
            self.target = target
            if target is not None:
                self.task = asyncio.create_task(self._hear(*target))
        muted = muted_agents(link)
        if self.speaking in muted - self.muted:
            # Muting an agent while it talks stops it at once, not after what it already sent
            self.speaker.clear()
        self.muted = muted
        if self.speaker.is_open() and not self.speaker.is_playing() and time.monotonic() - self.heard_at > OTHERS_IDLE_SECONDS:
            self.speaker.stop()

    def lend(self, lent: bool) -> None:
        """While the microphone is open, its stream plays what the others hear and this speaker's
        own stream closes; after, whatever is left plays on this speaker again."""
        self.lent = lent
        if lent and self.speaker.is_open():
            self.speaker.stop()
        elif not lent and self.speaker.buffered_seconds() > 0:
            self._open_speaker()

    def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.task = None
        if self.speaker.is_open():
            self.speaker.stop()

    async def _hear(self, hub: str, code: str, person: str) -> None:
        while True:
            wait = OTHERS_RETRY_SECONDS
            try:
                async with httpx.AsyncClient(base_url=hub.rstrip("/"), headers={CODE_HEADER: code},
                                             timeout=httpx.Timeout(10.0, read=lines.STREAM_IDLE_SECONDS)) as hub_client:
                    async with hub_client.stream("GET", "/api/voice/listen", params={"person": person}) as down:
                        if down.status_code == 404:
                            # A hub from before everyone heard the agents
                            wait = OTHERS_UNSUPPORTED_RETRY_SECONDS
                        elif down.status_code == 200:
                            reader = lines.FrameReader()
                            async for chunk in down.aiter_raw():
                                for header, payload in reader.feed(chunk):
                                    self._take(header, payload)
            except (httpx.HTTPError, OSError, ValueError):
                pass
            await asyncio.sleep(wait)

    def _take(self, header: dict, payload: bytes) -> None:
        if header.get("agent") in self.muted:
            return
        kind = header.get("kind")
        if kind == "flush":
            self.speaker.interrupt()
            return
        if kind != "audio" or not payload:
            return
        self.heard_at = time.monotonic()
        self.speaking = str(header.get("agent") or "")
        if not self.lent and not self._open_speaker():
            return
        self.speaker.play(payload)

    def _open_speaker(self) -> bool:
        """Whether this speaker plays now, opening it if it was closed."""
        if self.speaker.is_open():
            return True
        if time.monotonic() - self.speaker_failed_at < OTHERS_IDLE_SECONDS:
            return False
        try:
            self.speaker.start()
        except Exception as error:
            self.speaker_failed_at = time.monotonic()
            _log(f"Couldn't play what agents say to others: {error}")
            return False
        return True


async def listen(audio_file: Path | None, link_path: Path) -> None:
    loop = asyncio.get_running_loop()
    _log("Waiting for you to call an agent from the Agents tab or VR. Ctrl-C to stop.")
    status = _StatusFile()
    others = Others()
    audio = FileAudio(loop, audio_file) if audio_file else Audio(loop, others.speaker)
    microphone = Microphone(audio, (audio, others.speaker))
    # A line to each agent in the call, with the task that runs it, and when each was last opened
    open_lines: dict[str, tuple[Line, asyncio.Task]] = {}
    opened_at: dict[str, float] = {}
    # When a line to each agent last failed, and why; trying again waits, or, when retrying
    # can't help, waits until you call the agent again
    failed: dict[str, tuple[float, str]] = {}
    # Calling again, or pressing the talk button again, tries a line that failed again at once
    pressed_at = -RETRY_SECONDS
    was_pressed = False
    # A call this ended by itself, as the editor is told: it stays over until the editor ends it
    # too, or you call someone else
    ended: dict | None = None
    microphone_failed_at = -RETRY_SECONDS
    try:
        while True:
            link = read_link(link_path)
            others.follow(link)
            now = time.monotonic()
            agents = call_agents(link)
            agent = agents[0] if agents else ""
            # Muting everything; muting one agent only keeps its voice from playing here (Line)
            muted = bool((link or {}).get("muted"))
            call = calls(link)
            talking = not call and bool((link or {}).get("hold")) and bool((link or {}).get("talking"))
            if (call or talking) and not was_pressed:
                pressed_at = now
            was_pressed = call or talking
            if ended is not None and (not call or ended["agent"] not in agents):
                ended = None
            for name, (line, running) in list(open_lines.items()):
                if running.done():
                    problem = line.problem or (f"the voice helper failed: {running.exception()}" if running.exception() else "")
                    if problem:
                        failed[name] = (float("inf") if not line.retry else now, problem)
                    del open_lines[name]
            for line, _ in open_lines.values():
                if not line.hung_up and (link is None or muted or line.call != call or line.agent not in agents):
                    # The editor quit, you muted, hung up, or took the agent out of the call: hang up at once
                    line.hang_up()
            live = [line for line, _ in open_lines.values() if not line.hung_up]
            spoke_at = microphone.detector.spoke_at
            if live and all(line.call and line.connected for line in live) and min(
                    line.quiet_for(now, float("-inf") if line.mic_muted else spoke_at, audio.is_playing())
                    for line in live) >= CALL_IDLE_SECONDS:
                ended = {"agent": agent, "state": "off",
                         "problem": f"call ended after {CALL_IDLE_SECONDS / 60:g} minutes of quiet"}
                _log(f"The {ended['problem']}.")
                for line in live:
                    line.hang_up()
            for line in live:
                if not line.hung_up:
                    line.update(link)
            # Calling someone else, or no one, clears the wait before trying an agent again
            failed = {name: problem for name, problem in failed.items() if name in agents}
            statuses = {}
            dialing = False
            for name in agents:
                since, problem = failed.get(name, (-RETRY_SECONDS, ""))
                wanted = (call or talking) and (now - since >= RETRY_SECONDS or pressed_at > since)
                if muted:
                    # Muted: no line and no microphone until you unmute
                    statuses[name] = {"agent": name, "state": "muted"}
                elif ended is not None:
                    statuses[name] = {**ended, "agent": name}
                elif name in open_lines:
                    statuses[name] = line_status(open_lines[name][0])
                elif wanted and now - opened_at.get(name, -OPEN_GAP_SECONDS) >= OPEN_GAP_SECONDS:
                    line = Line(audio, {**link, "following": name})
                    open_lines[name] = (line, asyncio.create_task(line.run()))
                    opened_at[name] = now
                    statuses[name] = line_status(line)
                elif wanted:
                    # Calling again right after the last line, it waits a moment before it dials
                    dialing = dialing or (call and rings(link))
                    statuses[name] = {"agent": name, "state": "ringing" if call and rings(link) else "connecting"}
                elif problem:
                    statuses[name] = {"agent": name, "state": "error", "problem": problem}
                else:
                    statuses[name] = {"agent": name, "state": "off"}
            lines_now = [line for line, _ in open_lines.values()]
            if lines_now != microphone.lines:
                microphone.hand_to(lines_now)
            if ended is None and getattr(audio, "opens_devices", False) and audio.gives_no_sound():
                _log_once(f"{NO_SOUND_PROBLEM.capitalize()}.")
                for name in agents:
                    if name in open_lines:
                        statuses[name] = {**statuses[name], "problem": NO_SOUND_PROBLEM}
            # One agent's status as it always was, and with several in the call, each one's too
            shown = statuses.get(agent, {"agent": agent, "state": "off"})
            status.show({**shown, "calls": statuses} if len(agents) > 1 else shown)
            # The ring plays until the first agent in the call answers
            answered = any(line.answered for line in lines_now)
            audio.ring(not answered and (dialing or any(line.ringing() for line in lines_now)))
            # The microphone is on while you hold the talk button, and otherwise only once a line
            # is through, so a call that can't go through never lights the microphone
            connected = any(line.connected and not line.hung_up for line in lines_now)
            wants_microphone = bool(agents) and not muted and (talking or connected)
            if wants_microphone and not microphone.is_open and now - microphone_failed_at >= RETRY_SECONDS:
                others.lend(audio.others is not None)
                try:
                    await microphone.open()
                except RuntimeError as error:
                    others.lend(False)
                    _log(f"{str(error).capitalize()}.")
                    microphone_failed_at = time.monotonic()
                    for name in agents:
                        failed[name] = (microphone_failed_at, str(error))
                    for line in lines_now:
                        line.problem = str(error)
                        line.hang_up()
            elif not wants_microphone and microphone.is_open:
                microphone.close()
                others.lend(False)
            await asyncio.sleep(CHECK_SECONDS)
    finally:
        microphone.close()
        others.close()
        if audio.is_open():
            audio.stop()


def hold_lock(path: Path):
    """Locks the voice folder for as long as this runs, so a second helper for the same editor
    leaves at once. The open lock file, or None when another helper holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def check() -> None:
    import sounddevice
    print(f"Microphone: {sounddevice.query_devices(kind='input')['name']}")
    print(f"Speaker: {sounddevice.query_devices(kind='output')['name']}")
    print(f"Echo removal: {'on' if echo_remover() is not None else 'not available, so use headphones'}")
    print("The agent's voice runs on the agent's machine; this one needs no OpenAI key.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="listen", choices=("listen", "check"))
    parser.add_argument("--audio-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--link", type=Path, default=LINK_FILE, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.command == "check":
        check()
        return
    # The editor starts this whenever it hears nothing from it, so one may already run
    lock = hold_lock(arguments.link.parent / "helper.lock")
    if lock is None:
        _log("Another voice helper already runs for this editor; leaving it to that one.")
        return
    try:
        asyncio.run(listen(arguments.audio_file, arguments.link))
    except KeyboardInterrupt:
        _write_json(STATUS_FILE, {"agent": "", "state": "off", "time": time.time()})


if __name__ == "__main__":
    main()
