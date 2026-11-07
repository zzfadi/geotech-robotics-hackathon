"""Keep isolated frame delivery attached to the current authorized meeting.

Reads the existing meeting service state; never joins or creates participants.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
URL = re.compile(r'https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}$')


def normalize_meet_url(url):
    url = re.sub(r"^https://meet\.google\.com/meet/", "https://meet.google.com/", url).split("?")[0].rstrip("/")
    return url


def target(state, now):
    url = normalize_meet_url(state.get('url', ''))
    return url if (state.get('state') in ('streaming', 'attaching_media')
                   and 0 <= now-state.get('updated', 0) < 30
                   and URL.fullmatch(url)) else None


async def run():
    from playwright.async_api import async_playwright
    logs = PROJECT/'Saved/CoPresenceLogs'
    logs.mkdir(parents=True, exist_ok=True)
    import msvcrt
    lock = (logs/'video-supervisor.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    child = None
    current = None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp('http://127.0.0.1:9223')
        try:
            while True:
                try:
                    state = json.loads((PROJECT/'Saved/CoPresenceMeet/status.json').read_text())
                    url = target(state, time.time())
                    if child and (url != current or child.returncode is not None):
                        if child.returncode is None:
                            child.terminate()
                            await child.wait()
                        child = None
                    if url and child is None:
                        pages = [p for c in browser.contexts for p in c.pages if normalize_meet_url(p.url) == url]
                        if len(pages) == 1:
                            active = await pages[0].evaluate('Boolean(window.__copresenceCamera?.pumpOwner && Date.now()-window.__copresenceCamera.pumpAt<3000)')
                            if not active:
                                child = await asyncio.create_subprocess_exec(
                                    sys.executable, str(Path(__file__).with_name('copresence_video_pump.py')),
                                    url, 'CoPresence Denys Codex')
                                current = url
                                print('Frame transport attached to current meeting', flush=True)
                except Exception as error:
                    print('Frame supervisor retry: '+type(error).__name__, flush=True)
                await asyncio.sleep(3)
        finally:
            if child and child.returncode is None:
                child.terminate()
                await child.wait()


if __name__ == '__main__':
    asyncio.run(run())
