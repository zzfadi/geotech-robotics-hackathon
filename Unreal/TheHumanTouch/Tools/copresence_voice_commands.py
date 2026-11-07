"""Caption-backed command turns with explicit start/end, never ambient dispatch.

This is not audio VAD: quiet means unchanged transcription. No raw audio is stored.
"""
import re
import uuid
from copresence_meet_input import MeetInput

WAKE = re.compile(r'(?:^|[.!?]\s+)(?P<wake>(?:hi|ok|okay|okey)[\s,]+agent\b)[\s,:.!?]*', re.I)
END = re.compile(r'\b(?:send request|end command)[.!?\s]*$', re.I)


class VoiceCommands:
    def __init__(self, call_id, quiet_seconds=3, timeout=45):
        self.call_id, self.quiet, self.timeout = call_id, quiet_seconds, timeout
        self.started = False
        self.counts = {}
        self.active = {}
        self.serial = 0
        self.instance = uuid.uuid4().hex
        self.status = 'idle'

    def consume(self, rows, now):
        events = []
        for row in rows:
            key, speaker, text = row['id'], row['speaker'], row['text']
            if not speaker or speaker.casefold() in {'you', 'agent agent'}:
                continue
            wakes = list(WAKE.finditer(text))
            count = self.counts.get(key, 0)
            self.counts[key] = len(wakes)
            if not self.started:
                continue  # Never replay pre-attachment captions.
            turn = self.active.get(speaker)
            if len(wakes) > count:
                wake = wakes[-1]
                self.serial += 1
                turn = dict(key=key, index=len(wakes)-1, prefix=text[:wake.start('wake')],
                            body='', changed=now, started=now, id=self.serial)
                self.active[speaker] = turn
            if turn and turn['key'] == key and turn['index'] >= 0 and len(wakes) > turn['index']:
                body = text[wakes[turn['index']].end():].strip()
                if body != turn['body']:
                    turn.update(body=body, changed=now)
            elif turn and key != turn['key'] and key not in getattr(self, '_previous_keys', set()):
                # A new speaker block may continue an armed request. Keep speaker isolation.
                if text.strip() and not wakes:
                    turn.update(key=key, index=-1, base=turn['body'], body=(turn['body']+' '+text).strip(), changed=now)
            elif turn and turn['key'] == key and turn['index'] == -1:
                body = (turn['base']+' '+text).strip()
                if body != turn['body']:
                    turn.update(body=body, changed=now)
        for speaker, turn in list(self.active.items()):
            body = turn['body']
            ending = END.search(body)
            control = re.fullmatch(r'(stop|cancel)[.!?\s]*', body, re.I)
            ready = bool(body) and now-turn['changed'] >= (0.5 if ending or control else self.quiet)
            if now-turn['started'] > self.timeout:
                del self.active[speaker]  # Incomplete commands expire without execution.
                continue
            if not ready:
                continue
            if ending:
                body = body[:ending.start()].strip()
            if body and not (control and control.group(1).lower() == 'cancel'):
                events.append(MeetInput(self.call_id, f"voice:{self.instance}:{turn['id']}", speaker,
                                        'Hi Agent, '+body, 'caption'))
            del self.active[speaker]
        self.started = True
        self._previous_keys = {r['id'] for r in rows}
        self.status = 'listening' if self.active else 'idle'
        return events
