# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "pillow>=11", "playwright==1.62.0"]
# ///
"""Give an existing, connected Meet participant the CoPresence viewport camera.

This never creates a tab, launches Chrome, or closes the browser. By default it
does not join a call. --refresh-existing reconnects the same connected tab once,
when Meet cached the camera API before the viewport camera was installed.
Use only with the dedicated agent browser and an explicitly authorized call.
"""
from copresence_video_supervisor import normalize_meet_url
import argparse
import asyncio
import json
import os
import re
from urllib.parse import urlparse

import copresence_call as call


MUTE_PLAYBACK = """(() => {
  const mute = () => document.querySelectorAll('audio,video').forEach(e => {
    e.muted = true; e.volume = 0;
  });
  mute();
  window.__copresenceMutePlayback ||= setInterval(mute, 100);
})();"""


def select_page(contexts, url):
    matches = [p for c in contexts for p in c.pages
               if normalize_meet_url(p.url) == normalize_meet_url(url)]
    if len(matches) != 1:
        raise ValueError("Expected exactly one existing tab for the requested call")
    return matches[0]


async def attach(url, cdp_url, refresh_existing=False, speech=False):
    endpoint = urlparse(cdp_url)
    if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("The dedicated browser CDP endpoint must be local")
    agent = await asyncio.to_thread(call.copresence_mcp._agent_name)
    if call.copresence_mcp.running_call(agent):
        raise RuntimeError("A call camera process is already running")
    pid_file = call.copresence_mcp.call_pid_path(agent)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if pid_file.exists():
        stale = pid_file.read_text()
        if not call.copresence_mcp.running_call(agent) and pid_file.read_text() == stale:
            pid_file.unlink()
    # Exclusive creation also guards two concurrent attach requests.
    with pid_file.open("x") as f:
        f.write(str(os.getpid()))
    camera = call.CallCamera(f"CoPresence {agent}", "viewport")
    tasks = []
    started = False
    try:
        async with call.async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            page = select_page(browser.contexts, url)
            # Keep the permission override alive for this attachment. A separate
            # short-lived CDP client loses its override when it disconnects.
            await page.context.grant_permissions(["camera"], origin="https://meet.google.com")
            # Persist the playback guard across a same-tab reconnect as well.
            await page.add_init_script(script=MUTE_PLAYBACK)
            await page.evaluate(MUTE_PLAYBACK)
            close_dialog = page.get_by_role("button", name="Close dialog", exact=True)
            if await close_dialog.is_visible():
                await close_dialog.dispatch_event("click")
            if not await page.get_by_role("button", name=re.compile("^Leave call", re.I),
                                         include_hidden=True).count():
                raise RuntimeError("The existing tab is not connected to the call")
            if await call._visible_button(page, r"Stop presenting|Stop sharing"):
                raise RuntimeError("Stop the existing screen presentation before attaching the camera")
            await call._click(page, r"^Turn off camera")
            await call._click(page, r"^Turn off microphone")
            state = await asyncio.to_thread(camera.start)
            started = True
            call.log(f"viewport camera: {state}")
            frames = call.NdiFrames(camera.source_name)
            frames.start()
            tasks.append(asyncio.create_task(call.keep_camera_running(camera)))
            # Require a real frame before turning on the participant's camera.
            await asyncio.wait_for(frames.next_frame(), 45)
            settings = dict(agent=agent, eyeColor="#69CDEE",
                            width=call.FRAME_SIZE[0], height=call.FRAME_SIZE[1],
                            fps=call.FRAMES_PER_SECOND,
                            staleMilliseconds=call.STALE_FRAME_SECONDS * 1000,
                            debug=False)
            script = call.CAMERA_SCRIPT.replace("__SETTINGS__", json.dumps(settings))
            if refresh_existing:
                # Reloading this exact tab closes its previous WebRTC connection.
                # Installing before Meet loads prevents it caching a missing webcam.
                await page.add_init_script(script=script)
                await page.reload(wait_until="domcontentloaded")
                join = page.get_by_role("button", name=re.compile("^Join now$", re.I))
                await join.wait_for(timeout=60000)
                await call._click(page, r"^Turn off microphone")
                await join.click()
                await page.get_by_role("button", name=re.compile("^Leave call", re.I)).wait_for(
                    timeout=60000)
            else:
                await page.evaluate(script)
            if speech:
                import copresence_meet_speech
                await copresence_meet_speech.install(page)
                async def speech_worker():
                    while True:
                        try:
                            await copresence_meet_speech.serve(
                                page, call.copresence_mcp.PROJECT_DIR, call.copresence_mcp.agent_slug(agent))
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            call.log(f"Speech worker failed: {type(error).__name__}: {ascii(str(error))}; restarting")
                            await call._click(page, r"^Turn off microphone")
                            await asyncio.sleep(1)
                tasks.append(asyncio.create_task(speech_worker()))
            tasks.extend([asyncio.create_task(call.push_frames_via_pump(page, frames)),
                          asyncio.create_task(call.log_stats(page, frames))])
            if (not await call._visible_button(page, r"^Turn off camera")
                    and not await call._click(page, r"^Turn on camera")):
                raise RuntimeError("Could not enable the existing participant's camera")
            await call.report_call(agent, "in_call")
            call.log("Attached viewport to existing Meet participant")
            stop_path = call.PROFILE_DIR / f"stop-{os.getpid()}"
            while not stop_path.exists():
                if page.is_closed() or not await page.get_by_role(
                        "button", name=re.compile("^Leave call", re.I),
                        include_hidden=True).count():
                    call.log("Meeting disconnected; not joining another participant")
                    break
                await call._click(page, r"^(Continue|Keep waiting|I'm still here)$")
                await asyncio.sleep(2)
            await call._click(page, r"^Turn off camera")
            # Disconnect Playwright on exit, leaving Chrome and the participant intact.
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if started:
            await asyncio.to_thread(camera.stop)
        if pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
            pid_file.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("link")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9223")
    parser.add_argument("--refresh-existing", action="store_true")
    parser.add_argument("--speech", action="store_true", help="Enable synthetic-only speech output")
    args = parser.parse_args()
    url = call.copresence_mcp.meet_link(args.link)
    if not url:
        parser.error("An exact Google Meet call URL is required")
    asyncio.run(attach(url, args.cdp_url, args.refresh_existing, args.speech))
