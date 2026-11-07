"""Synthetic speech transport for the existing CoPresence Meet participant.

The output node connects only to MediaStreamDestination, never local speakers.
Install before enabling Meet's microphone; physical audio capture is never called.
"""
import base64

INSTALL_SPEECH = """() => {
  if (!window.__copresenceCamera) throw new Error("CoPresence camera must be installed first");
  if (window.__copresenceSpeech?.version === 3) return;
  if (window.__copresenceSpeech?.status().playing) throw new Error("Speech already playing");
  if (!window.__copresenceCamera.speechOutput)
    throw new Error("Camera bridge needs shared speech output; reconnect the existing tab with updated bridge");
  const {audio, destination} = window.__copresenceCamera.speechOutput();
  const gain = audio.createGain(); gain.connect(destination);
  let active = null;
  window.__copresenceSpeech = {
    version: 3,
    setEnabled(enabled) { gain.gain.value = enabled ? 1 : 0; },
    async play(data, startAt = null) {
      if (active) throw new Error("Speech already playing");
      await audio.resume();
      const bytes = Uint8Array.from(atob(data), c=>c.charCodeAt(0));
      const buffer = await audio.decodeAudioData(bytes.buffer);
      const source = audio.createBufferSource();
      source.buffer = buffer;
      source.connect(gain);
      active = source;
      return await new Promise(resolve => {
        source.onended = () => { active=null; source.disconnect(); resolve({duration:buffer.duration}); };
        const delay = startAt === null ? 0 : startAt - Date.now()/1000;
        if (!Number.isFinite(delay) || delay < -0.1 || delay > 120) {
          active=null; source.disconnect(); throw new Error("Speech cue is late or out of range");
        }
        source.start(audio.currentTime + Math.max(0, delay));
      });
    },
    stop() { if (active) active.stop(); },
    status() { return {state:audio.state, playing:!!active, audioReplies:gain.gain.value>0,
                       track:destination.stream.getAudioTracks()[0].readyState}; }
  };
}"""


async def install(page):
    await page.evaluate(INSTALL_SPEECH)
    # Only after the synthetic-only audio interceptor is installed.
    await page.context.grant_permissions(["camera", "microphone"],
                                         origin="https://meet.google.com")


async def play(page, wav_bytes, start_at=None):
    if wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise ValueError("Expected WAV audio")
    return await page.evaluate("args => window.__copresenceSpeech.play(args.data, args.startAt)",
                               {"data": base64.b64encode(wav_bytes).decode("ascii"),
                                "startAt": start_at})


async def serve(page, root, agent_slug):
    """Consume only fixed-format local jobs; never run code or capture a device."""
    import asyncio
    import json
    import time
    from pathlib import Path
    from copresence_speech_jobs import write_json

    queue = Path(root) / 'Saved/CoPresenceSpeech/jobs' / agent_slug
    queue.mkdir(parents=True, exist_ok=True)
    while True:
        write_json(queue / "worker.json", {"updated": time.time(), "call_id": page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]})
        for pending in sorted(queue.glob('*/pending.json')):
            folder = pending.parent
            try:
                pending.rename(folder / 'claimed.json')
            except FileNotFoundError:
                continue
            try:
                request = json.loads((folder / 'claimed.json').read_text(encoding='utf-8'))
                if request.get('call_id') and request['call_id'] != page.url.split('?')[0].rstrip('/').rsplit('/',1)[-1]:
                    raise RuntimeError('Speech belongs to another meeting')
                if abs(time.time() - request['created']) > 120:
                    raise RuntimeError('Expired speech job')
                if not await page.get_by_role('button', name='Leave call', exact=False,
                                              include_hidden=True).count():
                    raise RuntimeError('Existing call disconnected')
                if (folder / 'cancel').exists():
                    raise RuntimeError('Speech cancelled')
                write_json(folder / 'state.json', {'state': 'ready'})
                deadline = time.monotonic() + 20
                while not (folder / 'schedule.json').exists():
                    if (folder / 'cancel').exists() or time.monotonic() > deadline:
                        raise RuntimeError('Speech schedule cancelled or timed out')
                    await asyncio.sleep(.05)
                schedule = json.loads((folder / 'schedule.json').read_text(encoding='utf-8'))
                epoch = schedule['start_at']
                if not .1 < epoch - time.time() < 15:
                    raise RuntimeError('Speech start time is late or invalid')
                on = page.get_by_role('button', name='Turn on microphone', exact=False)
                if await on.count():
                    await on.first.click()
                write_json(folder / 'state.json', {'state': 'playing', 'start_at': epoch})
                playback = asyncio.create_task(play(page, (folder / 'speech.wav').read_bytes(), epoch))
                try:
                    while not playback.done():
                        if (folder / 'cancel').exists():
                            await page.evaluate('window.__copresenceSpeech?.stop()')
                            raise RuntimeError('Reply interrupted by a participant')
                        await asyncio.sleep(.05)
                    result = await playback
                finally:
                    if not playback.done():
                        playback.cancel()
                    await asyncio.gather(playback, return_exceptions=True)
                write_json(folder / 'state.json', {'state': 'done', **result})
            except asyncio.CancelledError:
                write_json(folder / 'state.json', {'state': 'error', 'error': 'Camera attachment stopped'})
                raise
            except Exception as error:
                write_json(folder / 'state.json', {'state': 'error', 'error': str(error)})
            finally:
                if not page.is_closed():
                    await page.evaluate('window.__copresenceSpeech?.stop()')
                    off = page.get_by_role('button', name='Turn off microphone', exact=False)
                    if await off.count():
                        await off.first.click()
        await asyncio.sleep(.1)
