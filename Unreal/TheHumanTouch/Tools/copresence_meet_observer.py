"""Observe Meet's visible chat and captions without recording ambient transcripts.

Selectors were checked against the live Meet UI. Unknown layouts yield no events.
Caption silence is a heuristic, not an authoritative speech-final marker.
"""
import time
from copresence_meet_input import MeetInput
from copresence_voice_commands import VoiceCommands

SNAPSHOT = """() => {
  const state = window.__copresenceInputIds ||= {
    prefix: crypto.randomUUID(), next: 0, ids: new WeakMap()
  };
  const id = el => {
    if (!state.ids.has(el)) state.ids.set(el, state.prefix + ':' + (++state.next));
    return state.ids.get(el);
  };
  const chat = [...document.querySelectorAll('[jsname="xySENc"] div[data-message-id]')]
    .filter(el => !el.hasAttribute('data-pending'))
    .map(el => ({
      id: el.getAttribute('data-message-id'),
      speaker: el.closest('[jsname="Ypafjf"]')?.querySelector('.poVWob')?.textContent?.trim() || '',
      text: el.querySelector('[jsname="dTKtvb"]')?.textContent?.trim() || ''
    }));
  const captions = [...document.querySelectorAll('[role="region"][aria-label="Captions"] .nMcdL')]
    .map(el => ({
      id: id(el),
      speaker: el.querySelector('.NWpY1d')?.textContent?.trim() || '',
      text: el.querySelector('.ygicle')?.textContent?.trim() || ''
    }));
  return {chat, captions};
}"""


EMBEDDED_CHAT = """() => [...document.querySelectorAll('[data-message-id][data-name]')].map(el => {
  const id = el.getAttribute('data-message-id');
  const body = document.getElementById(id + '/qJTHM');
  return {id: 'chat:' + id, speaker: el.getAttribute('data-name') || '',
          text: body?.textContent?.trim() || ''};
}).filter(row => row.text)"""


async def chat_surface(page):
    legacy = page.get_by_role("textbox", name="Send a message", exact=False)
    if await legacy.count():
        return page, legacy.first, '[jsname="dTKtvb"]'
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        box = frame.locator('[role="textbox"][contenteditable="true"][aria-label="History is on"], '
                            '[role="textbox"][contenteditable="true"][aria-label="History is off"]')
        if await box.count():
            return frame, box.first, '[jsname="bgckF"]'
    return None


class MeetObserver:
    def __init__(self, call_id, quiet_seconds=3.0, voice_enabled=True):
        self.voice_enabled = voice_enabled
        self.call_id = call_id
        self.quiet_seconds = quiet_seconds
        self.seen_chat = set()
        self.started = False
        self.voice = VoiceCommands(call_id, quiet_seconds)

    async def poll(self, page):
        snapshot = await page.evaluate(SNAPSHOT)
        for frame in page.frames:
            if frame != page.main_frame:
                snapshot["chat"].extend(await frame.evaluate(EMBEDDED_CHAT))
        return self.consume(snapshot)

    def consume(self, snapshot, now=None):
        now = time.monotonic() if now is None else now
        events = []
        for row in snapshot["chat"]:
            if row["id"] not in self.seen_chat:
                self.seen_chat.add(row["id"])
                if self.started and row["speaker"] and row["text"]:
                    events.append(MeetInput(self.call_id, row["id"], row["speaker"],
                                            row["text"], "chat"))
        if self.voice_enabled:
            events.extend(self.voice.consume(snapshot["captions"], now))
        self.started = True
        return events
