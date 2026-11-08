"""Pre-render every voice line to audio with macOS `say`, offline.

  python3 -m voice_agent.render                 # WAV (16-bit mono 24 kHz) + manifest
  python3 -m voice_agent.render --also m4a      # plus AAC copies for other players
  python3 -m voice_agent.render --force         # re-render even if the text is unchanged
  python3 -m voice_agent.render --list-voices   # which persona gets which installed voice

Writes voice_agent/local/audio/<id>.wav and manifest.json (git-ignored). The WAVs
are what voice_agent.play plays with afplay and what voice_agent.bridge streams
into a LiveKit room. No network, no API keys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .cues import AUDIO, Lines

SAMPLE_RATE = 24000
Runner = Callable[[Sequence[str]], None]


def run(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def installed_voices() -> List[str]:
    try:
        out = subprocess.run(["say", "-v", "?"], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    names = []
    for row in out.splitlines():
        # "Rocko (English (US)) en_US    # Hello!..." -> name is everything before the locale
        head = row.split("#", 1)[0].rstrip()
        parts = head.rsplit(None, 1)
        if len(parts) == 2:
            names.append(parts[0].strip())
    return names


def pick_voice(wanted, installed: List[str]) -> Optional[str]:
    wanted = [wanted] if isinstance(wanted, str) else list(wanted or [])
    if not installed:
        return wanted[0] if wanted else None
    for w in wanted:
        if w in installed:
            return w
    return None   # let `say` use the system voice


def text_sha(text: str, voice: Optional[str], rate) -> str:
    return hashlib.sha256(("%s|%s|%s" % (text, voice, rate)).encode()).hexdigest()[:16]


def wav_seconds(path: Path) -> Optional[float]:
    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 3)
    except (OSError, wave.Error, EOFError):
        return None


def render(lines: Lines, out: Path, also: Sequence[str] = (), force: bool = False,
           runner: Runner = run, installed: Optional[List[str]] = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    installed = installed_voices() if installed is None else installed
    old = {}
    manifest_path = out / "manifest.json"
    if manifest_path.exists() and not force:
        try:
            old = {i["id"]: i for i in json.loads(manifest_path.read_text()).get("items", [])}
        except (ValueError, KeyError):
            old = {}
    items = []
    for line in lines.by_id.values():
        persona = lines.voices[line.voice]
        say_voice = pick_voice(persona.get("say_voice"), installed)
        rate = persona.get("rate")
        sha = text_sha(line.text, say_voice, rate)
        wav = out / ("%s.wav" % line.id)
        prev = old.get(line.id)
        if not (prev and prev.get("sha") == sha and wav.exists()):
            aiff = out / ("%s.aiff" % line.id)
            cmd = ["say", "-o", str(aiff)]
            if say_voice:
                cmd += ["-v", say_voice]
            if rate:
                cmd += ["-r", str(rate)]
            runner(cmd + ["--", line.text])
            runner(["afconvert", "-f", "WAVE", "-d", "LEI16@%d" % SAMPLE_RATE, "-c", "1",
                    str(aiff), str(wav)])
            for ext in also:
                if ext == "m4a":
                    runner(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(out / ("%s.m4a" % line.id))])
                elif ext == "aiff":
                    continue
            if "aiff" not in also and aiff.exists():
                aiff.unlink()
        items.append({"id": line.id, "voice": line.voice, "say_voice": say_voice, "rate": rate,
                      "text": line.text, "urgent": line.urgent, "file": wav.name, "sha": sha,
                      "seconds": wav_seconds(wav)})
    manifest = {"schema": "hold-my-beer/voice-manifest/1",
                "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sample_rate": SAMPLE_RATE, "channels": 1, "format": "wav s16le",
                "items": items}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lines", type=Path, default=None)
    p.add_argument("--out", type=Path, default=AUDIO)
    p.add_argument("--also", action="append", default=[], choices=("m4a", "aiff"))
    p.add_argument("--force", action="store_true")
    p.add_argument("--list-voices", action="store_true")
    a = p.parse_args(argv)
    lines = Lines.load(a.lines)
    if a.list_voices:
        inst = installed_voices()
        for name, persona in lines.voices.items():
            print("%-7s -> %s" % (name, pick_voice(persona.get("say_voice"), inst) or "(system default)"))
        return 0
    try:
        m = render(lines, a.out, a.also, a.force)
    except FileNotFoundError as exc:
        print("render needs macOS `say` and `afconvert`: %s" % exc, file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print("render failed: %s\n%s" % (" ".join(exc.cmd), (exc.stderr or b"").decode(errors="replace")),
              file=sys.stderr)
        return 1
    total = sum(i["seconds"] or 0 for i in m["items"])
    print(json.dumps({"rendered": len(m["items"]), "seconds": round(total, 1), "out": str(a.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
