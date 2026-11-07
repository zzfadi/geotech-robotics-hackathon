"""Outbound-only hosted Meet panel relay. No public ports or arbitrary UE access.

A host-issued call-scoped relay token is read from a private file. Commands use the
existing scoped CoPresence router; the cloud never receives the hub session code.
"""
import argparse
import asyncio
import json
from pathlib import Path
import re
import time
import urllib.request
from urllib.parse import urlparse
from copresence_meet_input import MeetInput, MeetInputRouter, MeetInputDelivery
from copresence_meet_bridge import hub_state
from copresence_speech_jobs import interrupt, write_json
from copresence_meet_recording import MeetingRecorder
from copresence_meet_speech import INSTALL_SPEECH


def cloud(base, token, call, action, **body):
    req = urllib.request.Request(base.rstrip('/')+'/api/meet-agent/'+action,
        data=json.dumps(dict(call=call, **body)).encode(), method='POST',
        headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=55) as response:
        return json.load(response)


def enqueue_request(state, job):
    """Durable FIFO, keyed by request ID rather than participant display name."""
    pending = state.setdefault('pending', [])
    if (state.get('active') or {}).get('id') == job['id'] or any(j['id'] == job['id'] for j in pending):
        return False
    pending.append({key: job[key] for key in ('id', 'name', 'text', 'created')})
    pending.sort(key=lambda j: (j['created'], j['id']))
    return True


async def run(args):
    parsed=urlparse(args.cloud)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use the HTTPS add-on origin')
    if not re.fullmatch(r'[a-z]{3}-[a-z]{4}-[a-z]{3}',args.call):
        raise ValueError('Invalid call')
    router=MeetInputRouter(args.agent,spoken_replies=True)
    delivery=MeetInputDelivery(router,args.hub_url,args.code_file.read_text().strip())
    state=json.loads(args.state_file.read_text()) if args.state_file.exists() else {'active':None}
    if state.get('call',args.call)!=args.call:
        raise ValueError('State belongs to another call; use a separate state file')
    state['call']=args.call
    state.setdefault('pending', [])
    recording=MeetingRecorder(args.call)
    async def api(action,**body):
        return await asyncio.to_thread(cloud,args.cloud,args.token_file.read_text().strip(),args.call,action,**body)
    async def apply_audio(enabled, playing=False):
        await recording.connect()
        version=await recording.page.evaluate('window.__copresenceSpeech?.version')
        if version != 3:
            if playing:
                return False
            await recording.page.evaluate(INSTALL_SPEECH)
        await recording.page.evaluate('(enabled)=>window.__copresenceSpeech.setEnabled(enabled)',enabled)
        return True
    while True:
        try:
            # Refuse commands when the camera service is absent, stale or in another call.
            service=json.loads((args.project/'Saved/CoPresenceMeet/status.json').read_text())
            if service.get('state')!='streaming' or service.get('url','').rsplit('/',1)[-1]!=args.call or time.time()-service.get('updated',0)>15:
                await asyncio.sleep(2);continue
            speech=service.get('speech',{})
            if 'audioReplies' in state:
                await apply_audio(state['audioReplies'],speech.get('playing',False))
            heartbeat=await api('heartbeat',state='speaking' if speech.get('playing') else 'ready',guestAccess=getattr(args,'guest_access',False),audioReplies=state.get('audioReplies',True))
            if heartbeat.get('token'):
                # Reuse the existing private file ACL; never print the renewed grant.
                args.token_file.write_text(heartbeat['token'])
            result=await api('poll')
            for job in result['jobs']:
                if job.get('call')!=args.call or job.get('expires',0)<time.time()*1000:
                    continue
                if job.get('action')=='audio-replies':
                    enabled=job['text']=='true'
                    if not await apply_audio(enabled,speech.get('playing',False)):
                        continue
                    state['audioReplies']=enabled;write_json(args.state_file,state)
                    await api('ack',id=job['id'],state='done',reply='Audio replies '+('on' if enabled else 'off')+' for this call. Text replies remain available.')
                    continue
                if job.get('action') in ('record-start','record-finish','record-cancel'):
                    stage=job['action']
                    try:
                        if job['action']=='record-start':
                            await recording.start(job)
                            await api('ack',id=job['id'],state='delivered',reply='Recording meeting audio — press Stop recording to send')
                            continue
                        audio=await recording.finish(job,cancel=job['action']=='record-cancel')
                        await api('ack',id=job['text'],state='done',reply='Recording finished')
                        if audio is None:
                            await api('ack',id=job['id'],state='done',reply='Recording cancelled');continue
                        stage='transcribe'
                        text=(await api('transcribe',**audio)).get('text','').strip()
                        if not text:raise RuntimeError('No speech recognized. Unmute your Meet microphone and try again.')
                        job={**job,'action':'send','text':text}
                    except Exception as error:
                        with args.state_file.with_suffix('.errors.log').open('a') as log:
                            log.write('Meet recording failure: '+stage+' '+type(error).__name__+' '+str(getattr(error,'code',''))+'\n')
                        message=str(error) if isinstance(error,RuntimeError) else 'Recording could not be delivered. Please try again.'
                        await api('ack',id=job['id'],state='error',reply=message[:200])
                        continue
                if job.get('action')=='stop' or (job.get('action')=='send' and re.fullmatch(r'stop[.!?\s]*',job.get('text',''),re.I)):
                    interrupt(args.project,re.sub(r'[^A-Za-z0-9_-]+','_',args.agent),args.call)
                    if recording.active:
                        active_recording = recording.active
                        await recording.finish({'text':active_recording['id'],'sub':active_recording['sub']},cancel=True)
                        await api('ack',id=active_recording['id'],state='done',reply='Recording cancelled')
                    if state['active']:
                        await api('ack',id=state['active']['id'],state='error',reply='Stopped by a meeting participant')
                        state['active']=None
                    write_json(args.state_file,state)
                    await api('ack',id=job['id'],state='done',reply='Agent stopped; completed scene edits are kept')
                elif job.get('action')=='send':
                    enqueue_request(state, job)
                    write_json(args.state_file,state)
                    await api('ack',id=job['id'],state='delivered',reply='Queued for the agent')
            if state['active'] is None and state['pending']:
                job=state['pending'][0]
                speaker=job['name'][:35]+' · '+job['id'][:16]
                event=MeetInput(args.call,'panel:'+job['id'],speaker,'Hi Agent, '+job['text'],'chat')
                accepted=await delivery.submit(event)
                state['active']={'id':job['id'],'speaker':speaker,'hub_id':accepted['id'],'created':time.time()}
                state['pending'].pop(0)
                write_json(args.state_file,state)
                await api('ack',id=job['id'],state='delivered',reply='Agent processing')
            active=state['active']
            if active:
                # Retry the delivery acknowledgement after a transient network error.
                await api('ack',id=active['id'],state='delivered')
                snapshot=await asyncio.to_thread(hub_state,args.hub_url,active['hub_id'])
                replies=[m for m in snapshot['messages'] if m.get('role')=='agent' and m.get('author')==args.agent and m.get('to')==active['speaker'] and (not m.get('source_event_id') or m['source_event_id'].startswith('meet-reply:'+args.call+':'))]
                if replies or time.time()-active['created']>1800:
                    await api('ack',id=active['id'],state='done' if replies else 'error',reply=replies[0]['text'] if replies else 'Agent response timed out; check the call before retrying')
                    state['active']=None;write_json(args.state_file,state)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Never log tokens, bodies, user requests, or upstream response data.
            print('Meet panel relay retry: '+type(error).__name__,flush=True)
        await asyncio.sleep(1)


if __name__=='__main__':
    # Fail at startup rather than accepting text while every recording fails.
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise SystemExit('Meet recording requires Playwright. Launch with the meeting-service Python environment, not base Unreal Python.') from error
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--guest-access',action='store_true',help='Allow anyone knowing this active meeting code to use participant controls without Google sign-in')
    p.add_argument('--cloud',required=True);p.add_argument('--call',required=True)
    p.add_argument('--token-file',type=Path,required=True);p.add_argument('--state-file',type=Path,required=True)
    p.add_argument('--project',type=Path,required=True);p.add_argument('--code-file',type=Path,required=True)
    p.add_argument('--hub-url',default='http://127.0.0.1:8790');p.add_argument('--agent',default='Denys Codex')
    asyncio.run(run(p.parse_args()))
