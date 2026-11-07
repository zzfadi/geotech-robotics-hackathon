"""Explicit, bounded recording of incoming Meet audio, never speaker playback.

The hosted Start/Stop controls are the only entry point. No physical microphone
or system loopback is opened. Source tracks remain owned by Meet.
"""
import time

START = r"""async () => {
 if(window.__copresenceRequestRecording) throw Error('Another recording is active');
 const tracks=[...new Map([...document.querySelectorAll('audio')].flatMap(e=>e.srcObject?.getAudioTracks?.()||[]).filter(t=>t.readyState==='live').map(t=>[t.id,t])).values()];
 if(!tracks.length) throw Error('No incoming meeting audio. Unmute your Meet microphone and try again.');
 const context=new AudioContext({sampleRate:48000});
 await context.resume();
 const destination=context.createMediaStreamDestination();
 destination.channelCount=1;
 const sources=tracks.map(t=>{const s=context.createMediaStreamSource(new MediaStream([t]));s.connect(destination);return s;});
 const recorder=new MediaRecorder(destination.stream,{mimeType:'audio/webm;codecs=opus'});
 const state={context,destination,sources,recorder,chunks:[],started:Date.now()};
 recorder.ondataavailable=e=>{if(e.data.size)state.chunks.push(e.data);};
 window.__copresenceRequestRecording=state;recorder.start(250);
 state.stopPromise=new Promise(resolve=>{recorder.onstop=resolve;});
 state.stop=()=>{if(recorder.state!=='inactive')recorder.stop();sources.forEach(s=>s.disconnect());destination.stream.getTracks().forEach(t=>t.stop());return context.close();};
 // Stop capture at 30 seconds, but retain the blob while the cloud Stop job arrives.
 // Polling/network delay must not discard a successfully captured request.
 state.timeout=setTimeout(()=>{state.stop();},30000);
 state.cleanup=setTimeout(()=>{if(window.__copresenceRequestRecording===state)window.__copresenceRequestRecording=null;},90000);
 return {tracks:tracks.length};
}"""
FINISH = r"""async cancel => {
 const s=window.__copresenceRequestRecording;
 if(!s) throw Error('Recording expired. Press Start recording again.');
 window.__copresenceRequestRecording=null;clearTimeout(s.timeout);clearTimeout(s.cleanup);
 if(s.recorder.state!=='inactive')await s.stop();
 await s.stopPromise;
 if(cancel)return null;
 const blob=new Blob(s.chunks,{type:'audio/webm;codecs=opus'});
 const audio=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(blob);});
 return {audio,mime:'audio/webm;codecs=opus',duration:Math.min(30,(Date.now()-s.started)/1000)};
}"""

class MeetingRecorder:
 def __init__(self,call):
  self.call=call;self.active=None;self.page=None;self.driver=None
 async def connect(self):
  if self.page and not self.page.is_closed(): return
  from playwright.async_api import async_playwright
  self.driver=await async_playwright().start()
  self.browser=await self.driver.chromium.connect_over_cdp('http://127.0.0.1:9223')
  pages=[p for c in self.browser.contexts for p in c.pages if p.url.split('?')[0].rstrip('/').endswith('/'+self.call)]
  if len(pages)!=1: raise RuntimeError('Expected one active meeting tab')
  self.page=pages[0]
 async def start(self,job):
  if self.active and time.time()-self.active['started']>90:self.active=None
  if self.active:
   if self.active['id']==job['id']:return
   raise RuntimeError('Another request is recording. Wait for it to finish.')
  await self.connect();await self.page.evaluate(START)
  self.active={'id':job['id'],'sub':job['sub'],'started':time.time()}
 async def finish(self,job,cancel=False):
  if not self.active or self.active['id']!=job['text'] or self.active['sub']!=job['sub']:
   raise RuntimeError('This recording is no longer active. Start again.')
  try:return await self.page.evaluate(FINISH,cancel)
  finally:self.active=None
