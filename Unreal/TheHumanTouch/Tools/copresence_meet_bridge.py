# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.62.0"]
# ///
"""Connect an existing Meet tab to the existing, level-only CoPresence agent.

No browser launch, call joining, shell commands or ambient transcript recording.
Address commands to the agent by name. Captions use a three-second silence
heuristic; chat is preferable when transcription is uncertain.
"""
from copresence_video_supervisor import normalize_meet_url
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
import time
import urllib.request
from urllib.parse import urlparse

from copresence_meet_input import MeetInput, MeetInputDelivery, MeetInputRouter
from copresence_meet_observer import MeetObserver, chat_surface
from copresence_meet_vision import meeting_frames


def reply_text(agent, message):
    """Name the recipient because every attendee sees the shared Meet chat."""
    recipient = message.get("to", "").strip()
    prefix = f"[{agent} → {recipient}]" if recipient else f"[{agent}]"
    return f"{prefix} {message['text']}"


class BridgeState:
    def __init__(self, path, agent, call_id=""):
        self.path = Path(path)
        self.agent = agent
        self.call_id = call_id
        self.data = (json.loads(self.path.read_text()) if self.path.exists() else
                     {"pending": {}, "replies": [], "speakers": [], "cursor": None})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data), encoding="utf-8")
        temporary.replace(self.path)

    def enqueue(self, event, router):
        payload = router.payload(event)
        if payload is None:
            return False
        self.data["pending"].setdefault(payload["source_event_id"], asdict(event))
        self.save()
        return True

    def acknowledged(self, key, event):
        self.data["pending"].pop(key, None)
        speaker = event.speaker[:60]
        if speaker not in self.data["speakers"]:
            self.data["speakers"].append(speaker)
        self.save()

    def collect_replies(self, messages):
        # First attachment baselines history; it must not announce old tasks.
        if self.data["cursor"] is None:
            self.data["cursor"] = max([0] + [m["id"] for m in messages])
        else:
            for message in sorted(messages, key=lambda m: m["id"]):
                if message["id"] <= self.data["cursor"]:
                    continue
                origin = message.get("source_event_id", "")
                wrong_call = (self.call_id and origin.startswith("meet-reply:")
                              and not origin.startswith("meet-reply:" + self.call_id + ":"))
                if ((not self.data.get("stopped_at") or message.get("time", 0) > self.data["stopped_at"])
                        and not wrong_call and message.get("role") == "agent"
                        and message.get("author") == self.agent
                        and message.get("to") in self.data["speakers"]):
                    self.data["replies"].append(message)
                self.data["cursor"] = message["id"]
        self.save()


def hub_state(url, after=0):
    with urllib.request.urlopen(
            f"{url.rstrip('/')}/api/state?after={after}", timeout=5) as response:
        return json.load(response)


async def click_if_visible(page, name):
    button = page.get_by_role("button", name=re.compile(name, re.I)).first
    if await button.is_visible():
        await button.click()
        return True
    return False


async def run(args):
    from playwright.async_api import async_playwright
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    endpoint = urlparse(args.cdp_url)
    if endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Use the dedicated browser's local CDP endpoint")
    state = BridgeState(args.state_file, args.agent, args.link.rstrip("/").rsplit("/", 1)[-1])
    router = MeetInputRouter(args.agent, aliases=args.alias,
                            greeting_sequence=args.greeting_sequence,
                            self_speakers=args.self_speaker, spoken_replies=args.spoken_replies)
    delivery = MeetInputDelivery(router, args.hub_url, args.code_file.read_text().strip())
    observer = MeetObserver(args.link.rstrip("/").rsplit("/", 1)[-1], voice_enabled=False)
    snapshot = await asyncio.to_thread(hub_state, args.hub_url)
    if not snapshot.get("capabilities", {}).get("message_deduplication"):
        raise RuntimeError("Update the session hub before enabling retryable Meet commands")
    state.collect_replies(snapshot["messages"])
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(args.cdp_url)
        matches = [p for c in browser.contexts for p in c.pages
                   if normalize_meet_url(p.url) == normalize_meet_url(args.link)]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one existing Meet tab")
        page = matches[0]
        leave = page.get_by_role("button", name=re.compile("^Leave call", re.I),
                                include_hidden=True).first
        if not await leave.is_visible():
            raise RuntimeError("The existing Meet participant is not connected")
        # Requests come from explicit add-on recordings or chat, never ambient captions.
        try:
            if not await chat_surface(page):
                if not await click_if_visible(page, "^Chat with everyone"):
                    await click_if_visible(page, "^More options$")
                    await page.get_by_role("menuitem", name="In-call messages", exact=True).click(timeout=5000)
                for _ in range(20):
                    if await chat_surface(page):
                        break
                    await asyncio.sleep(0.5)
            if not await chat_surface(page):
                print("Chat unavailable; waiting for chat attachment", flush=True)
        except Exception as error:
            print(f"Chat attachment unavailable ({type(error).__name__}); chat attachment pending", flush=True)
        await observer.poll(page)  # Baseline the chat that just became visible.
        print(f"Listening for addressed commands to {args.agent}; existing call only", flush=True)
        stop = args.state_file.parent / "meet-input-state.stop"
        last_visual = 0.0
        while not stop.exists() and not page.is_closed() and await leave.is_visible():
            try:
                if time.monotonic() - last_visual >= 2:
                    try:
                        visual = await asyncio.wait_for(meeting_frames(page, state.call_id), timeout=3)
                        visual_path = args.state_file.parent / "meet-visual-latest.json"
                        temporary = visual_path.with_suffix(".tmp")
                        temporary.write_text(json.dumps(visual), encoding="utf-8")
                        temporary.replace(visual_path)
                    except Exception as error:
                        print(f"Visual context unavailable: {type(error).__name__}", flush=True)
                    last_visual = time.monotonic()
                for event in await observer.poll(page):
                    if state.enqueue(event, router):
                        print(f"Queued addressed {event.kind} command", flush=True)
                from copresence_speech_jobs import write_json
                write_json(args.state_file.parent / "meet-listening.json",
                           {"state": "chat-only", "updated": time.time(), "call_id": state.call_id})
                for key, row in list(state.data["pending"].items()):
                    if row.get("kind") != "chat":
                        state.data["pending"].pop(key, None)
                state.save()
                pending = list(state.data["pending"].items())
                stops = [i for i, (_, row) in enumerate(pending)
                         if (router.payload(MeetInput(**row)) or {}).get("control") == "stop"]
                if stops:
                    from copresence_speech_jobs import interrupt
                    interrupt(Path(__file__).resolve().parent.parent,
                              re.sub(r"[^A-Za-z0-9_-]+", "_", args.agent), state.call_id)
                    await page.evaluate('window.__copresenceSpeech?.stop()')
                    state.data["replies"].clear()
                    state.data["stopped_at"] = time.time()
                    for key, row in pending[:stops[-1] + 1]:
                        state.acknowledged(key, MeetInput(**row))
                    pending = pending[stops[-1] + 1:]
                    print("Participant interrupted avatar output", flush=True)
                for key, row in pending:
                    event = MeetInput(**row)
                    if event.kind != "chat" or router.payload(event) is None:
                        state.data["pending"].pop(key, None)
                        state.save()
                        continue
                    result = await delivery.submit(event)
                    state.acknowledged(key, event)
                    print(f"Agent Chat acknowledged message {result['id']}", flush=True)
                snapshot = await asyncio.to_thread(
                    hub_state, args.hub_url, state.data["cursor"] or 0)
                state.collect_replies(snapshot["messages"])
                while state.data["replies"]:
                    message = state.data["replies"][0]
                    text = reply_text(args.agent, message)
                    # Reconcile an uncertain previous send against visible chat.
                    surface = await chat_surface(page)
                    if surface is None:
                        break
                    chat, box, selector = surface
                    if not await box.is_visible():
                        if not await click_if_visible(page, "^Chat with everyone"):
                            await click_if_visible(page, "^More options$")
                            await page.get_by_role("menuitem", name="In-call messages", exact=True).click(timeout=5000)
                        await box.wait_for(state="visible", timeout=5000)
                    visible = await chat.locator(selector).all_text_contents()
                    if text not in [s.strip() for s in visible]:
                        await box.fill(text, timeout=5000)
                        await box.press("Enter")
                        await chat.locator(selector).filter(
                            has_text=text).first.wait_for(timeout=5000)
                    state.data["replies"].pop(0)
                    state.save()
                    print(f"Relayed agent reply {message['id']}", flush=True)
            except Exception as error:
                # Durable pending events keep the same source ID after a timeout.
                print(f"Bridge retry: {type(error).__name__}: {error}", flush=True)
            await asyncio.sleep(1)
        print("Bridge stopped; browser and Meet participant left unchanged", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("link")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--spoken-replies", action="store_true",
                        help="Use configured scoped speak tool for LLM replies")
    parser.add_argument("--self-speaker", action="append", default=["You"],
                        help="Exact caption/chat label of the agent participant to ignore")
    parser.add_argument("--greeting-sequence",
                        help="Optional existing /Game sequence for scoped visual greetings")
    parser.add_argument("--hub-url", required=True)
    parser.add_argument("--code-file", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9223")
    asyncio.run(run(parser.parse_args()))
