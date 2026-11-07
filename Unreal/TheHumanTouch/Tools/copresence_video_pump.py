"""Dedicated frame transport, isolated from meeting control and speech work."""
from copresence_video_supervisor import normalize_meet_url
import argparse
import asyncio
import base64
import time
import uuid
from copresence_call import NdiFrames, async_playwright

async def pump(url, source):
    owner = str(uuid.uuid4())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp('http://127.0.0.1:9223')
        pages = [p for c in browser.contexts for p in c.pages if normalize_meet_url(p.url) == normalize_meet_url(url)]
        if len(pages) != 1:
            raise RuntimeError('Expected one existing call tab')
        page = pages[0]
        await page.evaluate('''owner => {
          const c=window.__copresenceCamera;
          if(!c)throw Error('Camera is not installed');
          if(c.pumpOwner && Date.now()-c.pumpAt<3000)throw Error('Frame pump already active');
          if(!c.pumpShow){c.pumpShow=c.show.bind(c);c.show=async(data,owner)=>{
            if(c.pumpOwner && Date.now()-c.pumpAt<3000 && owner!==c.pumpOwner)return;
            if(owner){c.pumpOwner=owner;c.pumpAt=Date.now();}
            return c.pumpShow(data);
          };}
          c.pumpOwner=owner;c.pumpAt=Date.now();
        }''', owner)
        frames = NdiFrames(source)
        frames.start()
        checked = 0
        count = 0
        started = time.monotonic()
        try:
            while not page.is_closed() and normalize_meet_url(page.url) == normalize_meet_url(url):
                if time.monotonic()-checked > 2:
                    if not await page.get_by_role('button',name='Leave call',exact=False,include_hidden=True).count():
                        break
                    checked=time.monotonic()
                try:
                    image = await asyncio.wait_for(frames.next_frame(), 10)
                except asyncio.TimeoutError:
                    # Saving/loading can pause capture. NdiFrames rediscovers the
                    # source itself; keep the dedicated pump alive for its return.
                    continue
                await page.evaluate('(v)=>window.__copresenceCamera.show(v.data,v.owner)',
                                    {'data':base64.b64encode(image).decode(), 'owner':owner})
                count += 1
                if count % 300 == 0:
                    print(f'Frame pump: {count/(time.monotonic()-started):.1f} fps',flush=True)
                await asyncio.sleep(1/60)
        finally:
            if not page.is_closed():
                await page.evaluate('(owner)=>{const c=window.__copresenceCamera;if(c?.pumpOwner===owner)c.pumpOwner=null;}',owner)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url');parser.add_argument('source')
    args=parser.parse_args()
    if not args.url.startswith('https://meet.google.com/'):
        raise SystemExit('Expected Google Meet URL')
    asyncio.run(pump(args.url,args.source))
