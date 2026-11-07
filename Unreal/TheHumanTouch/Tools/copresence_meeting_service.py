# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "pillow>=11", "playwright==1.62.0"]
# ///
"""On-demand Slack meeting lifecycle on Luna2's existing signed-in desktop profile.

Only fixed JSON actions are accepted. Scene preparation goes through Agent Chat;
this service never executes caller-supplied code or edits the UE level directly.
"""
import asyncio
from argparse import Namespace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

from copresence_video_supervisor import normalize_meet_url
from copresence_host_runtime import service_lock, bootstrap_command, chrome_launch_command, launch_settings, verify_editor, start_panel_supervisor, wait_editor_session
import copresence_call as call
from copresence_meet_lifecycle import leave_agent_call
from copresence_call_attach import attach, MUTE_PLAYBACK
from copresence_meet_bridge import run as input_bridge
from copresence_speech_jobs import write_json

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / 'Saved/CoPresenceMeet'
LOGS = PROJECT / 'Saved/CoPresenceLogs'
CDP = 'http://127.0.0.1:9223'
HUB = 'http://100.84.28.59:8790'
ACCOUNT = 'agents@geotech.one'
AGENT = 'Denys Codex'
URL = re.compile(r'https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}$')


def status(state, **fields):
    data = dict(state=state, updated=time.time(), account=ACCOUNT, **fields)
    write_json(ROOT/'status.json', data)
    return data


def shell(request):
    # UE/runner children may inherit the launcher's handles. PIPE capture waits
    # for those long-lived children to close them even after PowerShell exits.
    # Keep diagnostics local in a file so completion follows the launcher only.
    with (LOGS/'meeting-bootstrap.log').open('ab') as output:
        result = subprocess.run(
            bootstrap_command(PROJECT), env=dict(os.environ, **launch_settings(request)),
            cwd=PROJECT, stdout=output, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        # Scripts are fixed; never include session code or arbitrary stdout in status.
        raise RuntimeError(f'Meeting bootstrap failed (exit {result.returncode}); inspect local bootstrap log')


async def stop_legacy():
    if sys.platform != "win32":
        return  # Mac has no legacy Windows scheduled bridges.
    subprocess.run(['powershell.exe','-NoProfile','-Command',f"& '{PROJECT}/Tools/meet-control.ps1' -Action stop -Project '{PROJECT}'"],capture_output=True,check=True)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        result=await asyncio.to_thread(subprocess.run,['powershell.exe','-NoProfile','-Command',"@(Get-ScheduledTask -TaskName 'CoPresence-Meet-Viewport','CoPresence-Meet-Input' | Where-Object State -eq Running).Count"],capture_output=True,text=True)
        if result.stdout.strip()=='0':
            break
        await asyncio.sleep(1)
    else:
        raise RuntimeError('Existing bridges did not stop gracefully; preserving their processes')
    (LOGS/'meet-input-state.stop').unlink(missing_ok=True)


async def connect_browser(playwright):
    try:
        return await playwright.chromium.connect_over_cdp(CDP, timeout=30000)
    except Exception:
        # Never launch against a locked/running profile whose CDP is unavailable.
        profile=Path(os.environ.get('COPRESENCE_CHROME_PROFILE', str(PROJECT/'Saved/CoPresenceCall/chrome-Denys_Codex')))
        if sys.platform == 'win32':
            check=subprocess.run(['powershell.exe','-NoProfile','-Command',"@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object CommandLine -Like '*chrome-Denys_Codex*').Count"],capture_output=True,text=True)
            busy=check.stdout.strip()!='0'
        else:
            commands=subprocess.check_output(['ps','-axo','command='],text=True).splitlines()
            busy=any('Google Chrome' in command and str(profile) in command for command in commands)
        if busy:
            raise RuntimeError('Dedicated Chrome is running but CDP is unavailable; refusing another participant/profile')
        profile=Path(os.environ.get('COPRESENCE_CHROME_PROFILE', str(PROJECT/'Saved/CoPresenceCall/chrome-Denys_Codex')))
        subprocess.Popen(chrome_launch_command(profile, call.CHROME_ARGS))
        for _ in range(30):
            try:
                return await playwright.chromium.connect_over_cdp(CDP, timeout=30000)
            except Exception:
                await asyncio.sleep(1)
        raise RuntimeError('Dedicated Chrome did not become ready')


async def click(page, pattern):
    button=page.get_by_role('button',name=re.compile(pattern,re.I)).first
    if await button.is_visible():
        await button.click(timeout=5000)
        return True
    return False


async def prepare_page(browser, request):
    pages=[p for c in browser.contexts for p in c.pages if p.url.startswith('https://meet.google.com/')]
    if len(pages)>1:
        raise RuntimeError('Multiple Meet tabs found; refusing to create or duplicate participants')
    page=pages[0] if pages else (browser.contexts[0].pages[0] if browser.contexts[0].pages else await browser.contexts[0].new_page())
    settings=dict(agent=AGENT,eyeColor='#69CDEE',width=1280,height=720,fps=60,staleMilliseconds=3000,debug=False)
    script=call.CAMERA_SCRIPT.replace('__SETTINGS__',json.dumps(settings))
    await page.add_init_script(script=MUTE_PLAYBACK)
    await page.add_init_script(script=script)
    await page.evaluate(MUTE_PLAYBACK)
    connected=await page.get_by_role('button',name=re.compile('^Leave call'),include_hidden=True).count()
    target=request.get('url')
    if connected and request['action']=='join' and normalize_meet_url(page.url)==target:
        await page.evaluate(script)
        await click(page,'^Turn off microphone')
        return page,target
    if connected:
        await leave_agent_call(page)
    # Check the signed-in account before creating or entering another meeting.
    await page.goto('https://meet.google.com/',wait_until='domcontentloaded')
    await page.wait_for_timeout(1500)
    identity=await page.locator('body').inner_text()
    labels=await page.locator('[aria-label]').evaluate_all('(es)=>es.map(e=>e.getAttribute("aria-label")).join("\\n")')
    if ACCOUNT not in identity+labels:
        raise RuntimeError('Cannot verify agents@geotech.one in the dedicated profile; sign-in/account selection needs attention')
    await page.goto('https://meet.google.com/new' if request['action']=='create' else target,wait_until='domcontentloaded')
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        target=normalize_meet_url(page.url)
        if URL.fullmatch(target):
            status('joining',request_id=request['id'],url=target,requested_by=request.get('agent'))
        await click(page,'^Close dialog$')
        await click(page,'^Turn off microphone')
        if await page.get_by_role('button',name=re.compile('^Leave call'),include_hidden=True).count():
            return page,target
        text=await page.locator('body').inner_text()
        if call.REFUSED.search(text):
            raise RuntimeError('Meet refused or ended this meeting; no retry participant created')
        if await page.get_by_role('button',name=re.compile(call.DUPLICATE_BUTTON)).count():
            raise RuntimeError(call.DUPLICATE_REASON)
        if await click_join(page):
            status('awaiting_admission',request_id=request['id'],url=target)
        await asyncio.sleep(1)
    raise RuntimeError('Meeting admission timed out')


async def click_join(page):
    """Recover only the known media prompt arriving during a join click."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    try:
        return await click(page, r'^(Join now|Ask to join)$')
    except PlaywrightTimeoutError:
        prompt = page.get_by_text(
            'Do you want people to hear you in the meeting?', exact=True)
        if not await prompt.is_visible():
            raise
        # Dismiss without requesting a physical mic/camera. The existing
        # bounded admission loop rechecks membership before another join click.
        await page.keyboard.press('Escape')
        return False


def request_scene(request):
    """Request scoped preparation; no direct raw editor calls or caller-provided code."""
    payload=dict(author='Meeting operator',role='human',to=AGENT,selection=[],code=(LOGS/'hub_code').read_text().strip(),
        source_event_id='meeting-prepare:'+request['id'],text=(
        'Prepare the current GeoVerse level for the authorized meeting. Use a scoped CoPresence task. '
        'Do not switch levels merely because the meeting changed. In Build, preserve the current scene, '
        'initialize its companion and speech support, frame the editor viewport, and verify with look. '
        'This is scene readiness only: the service starts the NDI capture camera AFTER MEET_READY. '
        'A missing call_camera capture actor before attachment is expected and must not block readiness. '
        'The following portrait and facial-idle checks apply only in the Avatar level: '
        'Verify MH_GeoVerseAgent_Meet and the existing studio backdrop, keep the portrait framed with flying eyes, '
        'initialize installed meet_speech/meet_idle/meet_companion if not already active, and verify with look. '
        'Before readiness, verify idle frame advances without errors, its Body/Face tracks have valid control rigs, '
        'speech watcher is installed and speech.idle_guard is meet_idle.speech_guard. '
        'If idle initialization fails, do not report ready. Reclaim only our known generated Idle sequence when '
        'it was restored open but no watcher is active; preserve any unrelated Sequencer. '
        'Do not import, save, delete, or disrupt other users. Preserve the human viewport. '
        'Existing in-call questions take priority; answer addressed callers using speak with exact recipient. '
        'Report exactly MEET_READY:'+request['id']+' to Meeting operator only after verifying with look. Do not speak an unsolicited greeting.'))
    req=urllib.request.Request(HUB+'/api/messages',json.dumps(payload).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response:
        return json.load(response)['id']


async def serve():
    from playwright.async_api import async_playwright
    ROOT.mkdir(parents=True,exist_ok=True)
    queue=ROOT/'requests'; queue.mkdir(exist_ok=True)
    # OS lock is released automatically on process exit, unlike a stale PID file.
    global AGENT, HUB
    current=None; page=None; tasks=[]; browser=None
    status('idle')
    async with async_playwright() as playwright:
        while True:
            for path in sorted(queue.glob('*.json'),key=lambda p:p.stat().st_mtime):
                try:
                    request=json.loads(path.read_text(encoding='utf-8-sig'))
                    if not isinstance(request,dict):
                        raise ValueError('Meeting request must be an object')
                except (ValueError,UnicodeError):
                    path.rename(path.with_suffix('.invalid'))
                    continue
                path.rename(path.with_suffix('.claimed'))
                try:
                    if time.time()-request['created']>600:
                        raise RuntimeError('Expired meeting request; ask again explicitly')
                    for task in tasks: task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True); tasks=[]
                    if request['action']=='stop':
                        if page is None or page.is_closed():
                            browser=await connect_browser(playwright)
                            pages=[p for c in browser.contexts for p in c.pages if p.url.startswith('https://meet.google.com/')]
                            if len(pages)>1: raise RuntimeError('Multiple Meet tabs; refuse ambiguous stop')
                            page=pages[0] if pages else None
                        await leave_agent_call(page)
                        current=None; status('stopped',request_id=request['id']); continue
                    status('starting_ue',request_id=request['id'])
                    await asyncio.to_thread(shell,request)
                    HUB = call.copresence_mcp._hub_url()
                    call.copresence_mcp.HUB.close()
                    call.copresence_mcp.HUB = call.copresence_mcp.httpx.Client(base_url=HUB, timeout=0.5)
                    await stop_legacy()
                    call.copresence_mcp._hub_state['agent'] = None
                    live = await asyncio.to_thread(wait_editor_session,
                        lambda: json.loads(call.copresence_mcp.session()),
                        expected_session=launch_settings(request)['COPRESENCE_SESSION_NAME'] if launch_settings(request)['COPRESENCE_CONNECTION_MODE']=='multiuser' else None)
                    AGENT = live['agent']
                    verify_editor(request, live)
                    if request['action'] == 'prepare':
                        current=None
                        status('prepared',request_id=request['id'],session=live.get('session'),mode=launch_settings(request)['COPRESENCE_CONNECTION_MODE'],agent=AGENT,hub_url=HUB)
                        continue
                    start_panel_supervisor(PROJECT)
                    browser=await connect_browser(playwright)
                    page,url=await prepare_page(browser,request)
                    current=dict(request_id=request['id'],url=url,requested_by=request.get('agent'),session=live.get('session'),mode=launch_settings(request)['COPRESENCE_CONNECTION_MODE'],agent=AGENT,hub_url=HUB)
                    # A viewport stream does not require an LLM reply or an Avatar map.
                    # Capture verifies the live editor and requires a real NDI frame.
                    scene_message=None
                    tasks.append(asyncio.create_task(attach(url,CDP,speech=True)))
                    # Camera closes the mic prompt before the observer attaches.
                    await asyncio.sleep(2)
                    args=Namespace(link=url,agent=AGENT,alias=['Agent','Codex',request.get('agent','Oracle')],spoken_replies=True,self_speaker=['You','agent agent'],greeting_sequence=None,hub_url=HUB,code_file=LOGS/'hub_code',state_file=LOGS/('meet-input-'+url.rsplit('/',1)[1]+'.json'),cdp_url=CDP)
                    tasks.append(asyncio.create_task(input_bridge(args)))
                    current['scene_request_message']=scene_message
                    status('attaching_media',**current)
                except Exception as error:
                    status('needs_attention',request_id=request.get('id'),error=str(error))
                    current=None
            if current and page and not page.is_closed():
                try:
                    for task in tasks:
                        if task.done():
                            raise RuntimeError(f'Meeting worker stopped: {task.exception() if not task.cancelled() else "cancelled"}')
                    await page.evaluate(MUTE_PLAYBACK)
                    snapshot=await page.evaluate('''async () => ({media:await window.__copresenceCamera?.stats(),
                      playbackMuted:[...document.querySelectorAll('audio,video')].every(e=>e.muted&&e.volume===0),
                      speech:window.__copresenceSpeech?.status()})''')
                    sending=(snapshot.get('media') or {}).get('framesShown',0)>0 and any(r.get('fps',0)>0 for r in (snapshot.get('media') or {}).get('sending',[]))
                    status('streaming' if sending and snapshot['playbackMuted'] else 'attaching_media',**current,**snapshot)
                except Exception as error:
                    for task in tasks: task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True); tasks=[]
                    if page and not page.is_closed(): await click(page,'^Turn off microphone')
                    status('needs_attention',**current,error=str(error)); current=None
            await asyncio.sleep(2)


if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        stream.reconfigure(encoding='utf-8',errors='backslashreplace')
    with service_lock(ROOT/'service.lock'):
        asyncio.run(serve())
