"""Speak the matching line for each station event, locally, with no network.

  # rehearse on a replayed handoff, paced by the trace's own clock
  python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace | python3 -m voice_agent.play --pace

  # at the station: keep handoff.run's terminal as it is and copy its event lines to a file
  python3 -m vision.run ... | python3 -m handoff.run --mode hand --feed - --operator-cutoff \\
      2> >(tee -a voice_agent/local/events.log >&2)
  python3 -m voice_agent.play --follow voice_agent/local/events.log     # second terminal

  python3 -m voice_agent.play --say cold_open      # one line by id (stage fallback)
  python3 -m voice_agent.play --dry-run < trace.jsonl   # print cues, no audio

Each cue prints one JSON line. Lines come from voice_agent/local/audio (run
python3 -m voice_agent.render first); a missing file falls back to live `say`.
De-duplicated (the same line waits 8 s) and rate-limited (at most two lines
queued; stale ones are dropped). The voice only announces; it sends nothing to
the arm, the routine or the turn gate.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, TextIO

from .cues import AUDIO, Cuer, Event, Line, Lines, normalize, parse_json_line


# ------------------------------------------------------------------ input
def follow_file(path: Path, from_start: bool = False, poll_s: float = 0.1,
                stop: Callable[[], bool] = lambda: False) -> Iterator[str]:
    """tail -f: yield lines appended to path; survives the file being created late or truncated."""
    fh: Optional[TextIO] = None
    pos = 0
    try:
        while not stop():
            if fh is None:
                try:
                    fh = open(path, "r")
                except FileNotFoundError:
                    time.sleep(poll_s)
                    continue
                if not from_start:
                    fh.seek(0, os.SEEK_END)
                pos = fh.tell()
            line = fh.readline()
            if line and line.endswith("\n"):
                pos = fh.tell()
                yield line
                continue
            if line:   # partial line: wait for the rest
                fh.seek(pos)
            try:
                if os.stat(path).st_size < pos:
                    fh.close()
                    fh, from_start = None, True
                    continue
            except FileNotFoundError:
                pass
            time.sleep(poll_s)
    finally:
        if fh is not None:
            fh.close()


def records(stream: Iterable[str], pace: bool = False, sleep=time.sleep) -> Iterator[Event]:
    """Events from JSON lines; with pace, wait out the gaps in the source's own time stamps."""
    last_t = None
    for text in stream:
        rec = parse_json_line(text)
        if rec is None:
            continue
        for ev in normalize(rec):
            if pace and ev.t is not None:
                if last_t is not None and ev.t > last_t:
                    sleep(min(ev.t - last_t, 5.0))
                last_t = ev.t if last_t is None else max(last_t, ev.t)
            yield ev


def open_input(a) -> Iterable[str]:
    if a.follow:
        return follow_file(Path(a.follow), from_start=a.from_start)
    if a.input and a.input != "-":
        return open(a.input, "r")
    return sys.stdin


# ----------------------------------------------------------------- output
def audio_path(audio_dir: Path, line: Line) -> Path:
    return audio_dir / ("%s.wav" % line.id)


class LocalPlayer:
    """One line at a time through afplay; a line that waited too long is dropped."""

    def __init__(self, lines: Lines, audio_dir: Path = AUDIO, dry_run: bool = False,
                 run: Callable[[List[str]], None] = None, clock=time.monotonic, sleep=time.sleep,
                 which: Callable[[str], Optional[str]] = shutil.which):
        self.lines, self.audio_dir, self.dry_run = lines, audio_dir, dry_run
        self.which = which
        self.run = run or (lambda cmd: subprocess.run(cmd, check=False))
        self.clock, self.sleep = clock, sleep
        self.q: "queue.Queue" = queue.Queue()
        self.played: List[str] = []
        self.dropped: List[str] = []
        self._thread: Optional[threading.Thread] = None

    def pending(self) -> int:
        return self.q.qsize()

    def command(self, line: Line) -> List[str]:
        wav = audio_path(self.audio_dir, line)
        if wav.exists():
            # afplay on macOS; PulseAudio or ALSA on Linux (the Jetson), with WAVs rendered on a Mac.
            for tool in ("afplay", "paplay", "aplay"):
                if self.which(tool):
                    return [tool, str(wav)]
            return ["afplay", str(wav)]
        persona = self.lines.voices.get(line.voice, {})
        voice = persona.get("say_voice")
        voice = voice[0] if isinstance(voice, list) and voice else voice
        cmd = ["say"]
        if voice:
            cmd += ["-v", voice]
        if persona.get("rate"):
            cmd += ["-r", str(persona["rate"])]
        return cmd + ["--", line.text]

    def submit(self, line: Line) -> None:
        if line.urgent:
            while True:
                try:
                    self.dropped.append(self.q.get_nowait()[0].id)
                except queue.Empty:
                    break
        self.q.put((line, self.clock()))

    def play_one(self, line: Line, queued_at: float) -> bool:
        if self.clock() - queued_at > float(self.lines.rules["max_wait_s"]) and not line.urgent:
            self.dropped.append(line.id)
            return False
        if not self.dry_run:
            try:
                self.run(self.command(line))
            except OSError as exc:
                print("voice: cannot play %s: %s" % (line.id, exc), file=sys.stderr)
        self.played.append(line.id)
        self.sleep(float(self.lines.rules["min_gap_s"]))
        return True

    def _loop(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                return
            self.play_one(*item)

    def start(self) -> "LocalPlayer":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def close(self, timeout: float = 30.0) -> None:
        if self._thread:
            self.q.put(None)
            self._thread.join(timeout)


def cue_json(ev: Event, line: Line) -> str:
    return json.dumps({"cue": line.id, "voice": line.voice, "text": line.text, "key": ev.key,
                       "source": ev.source, "t": ev.t})


def run_local(events: Iterable[Event], lines: Lines, player: LocalPlayer, out=None,
              clock=time.monotonic, verbose: bool = False) -> int:
    out = out or sys.stdout
    cuer = Cuer(lines)
    n = 0
    for ev in events:
        line = cuer.cue(ev, clock(), player.pending())
        if line is None:
            if verbose:
                print(json.dumps({"skip": ev.key, "why": cuer.why}), file=sys.stderr)
            continue
        print(cue_json(ev, line), file=out, flush=True)
        player.submit(line)
        n += 1
    return n


def add_input_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", nargs="?", default="-", help="JSON lines file to read once, or - for stdin")
    p.add_argument("--follow", metavar="FILE", help="tail a growing file (handoff.run stderr copy)")
    p.add_argument("--from-start", action="store_true", help="with --follow, read what is already there")
    p.add_argument("--pace", action="store_true", help="replay at the trace's own pace")
    p.add_argument("--lines", type=Path, default=None)
    p.add_argument("--audio", type=Path, default=AUDIO)
    p.add_argument("--dry-run", action="store_true", help="print cues, play nothing")
    p.add_argument("--verbose", action="store_true", help="also report events that stay silent")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_input_args(p)
    p.add_argument("--say", metavar="ID", help="play one line by id and exit")
    a = p.parse_args(argv)
    lines = Lines.load(a.lines)
    player = LocalPlayer(lines, a.audio, dry_run=a.dry_run)
    if a.say:
        line = lines.by_id.get(a.say)
        if line is None:
            print("no line %r; ids: %s" % (a.say, ", ".join(sorted(lines.by_id))), file=sys.stderr)
            return 2
        print(json.dumps({"cue": line.id, "voice": line.voice, "text": line.text}))
        player.play_one(line, player.clock())
        return 0
    player.start()
    try:
        run_local(records(open_input(a), pace=a.pace), lines, player, verbose=a.verbose)
    except KeyboardInterrupt:
        pass
    finally:
        player.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
