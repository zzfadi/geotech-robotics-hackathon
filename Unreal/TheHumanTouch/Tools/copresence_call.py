# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "pillow>=11", "playwright==1.62.0", "openai[realtime]==3.31.0", "httpx>=0.27"]
# ///
"""Brings a CoPresence agent into a Google Meet call as its own participant, with its live
Unreal view as its camera.

    uv run --script Tools/copresence_call.py join <meet link> [--dry-run] [--no-voice]
    uv run --script Tools/copresence_call.py join <meet link> --spectator
    uv run --script Tools/copresence_call.py sign-in [--as NAME]

The agent's call camera (Plugins/CoPresenceCall) films it in its own editor, from behind and
above its ghost the way people follow it in the Agents tab, and sends that as an NDI source
at 60 frames a second. This joins the call in Google Chrome with its own profile under
Saved/CoPresenceCall and fake media devices, so the Mac's real camera and microphone are
never used: the page's camera is the NDI picture, and its microphone is the agent's voice.

With an OpenAI key set up for voice (see Tools/copresence_agent_voice.py), the agent also hears
the call through Meet's captions and speaks when something is meant for it
(Tools/copresence_call_voice.py); --no-voice keeps it silent.

The agent joins as a guest under its session name ("Lukas Claude") and someone in the call
admits it. Calls whose organization turns guests away need its Chrome profile signed in to
Google once: `sign-in` opens it in plain Chrome for a person to sign in, and later joins use
that account. Stop it (Ctrl+C or SIGTERM, or the leave_call tool) and it leaves the call
first. Windows has no SIGTERM, so there leave_call asks through a file next to the call's
pid file instead. --dry-run stops before asking to join and saves a screenshot of Meet's
preview.

Agents start this with the join_call tool (Tools/copresence_mcp.py), which runs it in the
background; its log is Saved/CoPresenceLogs/call-<agent>.log. It tells the session hub how
the call is going, and every editor marks the agent in its Agents tab while it is in the call.

With --spectator it shows the spectator's camera instead (Tools/copresence_spectator.py, which
starts it this way): the editor at COPRESENCE_UNREAL_MCP_URL is the spectator's, which films the
session on its own, and the call joins under the spectator's name, without voice. It says in
Agent Chat only when the session goes on show in the call and when it stops.
"""

import argparse
import asyncio
import base64
import ctypes
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import copresence_mcp  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.async_api import Error as PlaywrightError  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

LOG_DIR = copresence_mcp.PROJECT_DIR / "Saved" / "CoPresenceLogs"
PROFILE_DIR = copresence_mcp.CALL_DIR
WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
# The engine in the default Epic Games location, as the join scripts expect it
DEFAULT_ENGINE_DIR = (r"C:\Program Files\Epic Games\UE_5.8\Engine" if WINDOWS
                      else "/Users/Shared/Epic Games/UE_5.8/Engine")
ENGINE_DIR = Path(os.environ.get("COPRESENCE_ENGINE_DIR", DEFAULT_ENGINE_DIR))
# Unreal's NDI Media plugin brings its own copy of the NDI library
NDI_LIBRARY = ENGINE_DIR / "Plugins" / "Media" / "NDIMedia" / "Binaries" / "ThirdParty" / (
    "Win64/Processing.NDI.Lib.x64.dll" if WINDOWS else "Mac/libndi.dylib" if MAC else "Linux/libndi.so.6")
CAMERA_TOOLSET = "copresence_call.toolset.CallCameraTools"
FRAME_SIZE = (1280, 720)
FRAMES_PER_SECOND = 60
JPEG_QUALITY = 85
# The call camera stops by itself 20 seconds after the last keep-alive
KEEP_ALIVE_SECONDS = 5.0
# Without a new Unreal frame for this long, the call sees the agent's name card instead
STALE_FRAME_SECONDS = 3.0
# How often the log says what Unreal sends and what Meet sends on
STATS_SECONDS = 15.0
JOIN_TIMEOUT_SECONDS = 90
ADMIT_TIMEOUT_SECONDS = 10 * 60
CHROME_ARGS = [
    # The page only ever sees the agent's camera; anything else gets Chrome's fake devices
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    # The agent hears the call through its captions, and on the same Mac people would hear
    # the call twice
    "--mute-audio",
    "--disable-blink-features=AutomationControlled",
    "--autoplay-policy=no-user-gesture-required",
    # Keeps the page working at full speed while its window is behind others
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--lang=en-US",
]

# What the call says in Agent Chat as it goes, as the agent itself
AGENT_LINES = {
    "no_camera": "My editor has no call camera, so I can't join video calls. Bring me back with the current join "
                 "script and try again.",
    "not_ready": "I couldn't get into the call: {reason}.",
    "joining": "Joining the video call. People there see me working in the level.",
    "not_requested": "I did not join: {reason}.",
    "waiting": "I'm waiting to be let into the call.",
    "refused": "I couldn't get into the call: {reason}.",
    "in_call_voice": "I'm in the video call now and I can hear it. In the call's chat, \"stop\" stops me, \"mute\" "
                     "stops me listening and \"/help\" lists the rest.",
    "in_call": "I'm in the video call now.",
    "left": "I left the video call.",
}
# The spectator only tells the session when it goes on show in a call and when that stops;
# everything else goes to its log
SPECTATOR_LINES = {
    "in_call": "The session is on show in a video call now: people there see what the spectator films.",
    "left": "The session is no longer on show in the video call.",
}

# Meet's screens, recognised by their text
WAITING = re.compile(r"Asking to be let in|Please wait until a meeting host|someone in the call lets you in", re.I)
REFUSED = re.compile(r"You can't join this (video )?call|denied your request|No one responded to your request|"
                     r"You've been removed|You were removed|Check your meeting code|This meeting has ended|"
                     r"Your meeting code has expired", re.I)
# Never create a second participant or displace an existing device automatically.
JOIN_BUTTON = r"^(Ask to join|Join now|Join anyway)$"
DUPLICATE_BUTTON = r"^(Join here too|Switch here)$"
DUPLICATE_REASON = "this account is already connected on another device; keeping that participant"
# With --join-here-too (or COPRESENCE_JOIN_HERE_TOO=1) the agent joins as a second device of an
# account that is already in the call, such as the tester's own; never with "Switch here",
# which would take that other device out of the call
JOIN_HERE_TOO = os.environ.get("COPRESENCE_JOIN_HERE_TOO") == "1"
ENDED = re.compile(r"You left the meeting|The call has ended|Return to home screen|Rejoin", re.I)

CAMERA_SCRIPT = """
(() => {
  if (window.__copresenceCamera) {
    return;
  }
  const settings = __SETTINGS__;
  const DEBUG_ENABLED = settings.debug;
  const debug = {
    log: (...parts) => {
      if (DEBUG_ENABLED) {
        console.log("[copresence]", ...parts);
      }
    },
  };

  // Frames go straight into a track, which keeps running while the window is covered.
  // Without track generators, a canvas stream carries them instead
  const canvas = document.createElement("canvas");
  canvas.width = settings.width;
  canvas.height = settings.height;
  const context = canvas.getContext("2d");
  const generator = typeof MediaStreamTrackGenerator === "function" ? new MediaStreamTrackGenerator({ kind: "video" }) : null;
  const writer = generator ? generator.writable.getWriter() : null;
  const camera = generator || canvas.captureStream(settings.fps).getVideoTracks()[0];
  let pictureAt = 0;
  let framesShown = 0;

  async function write(image) {
    if (!writer) {
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      return;
    }
    const frame = new VideoFrame(image, { timestamp: Math.round(performance.now() * 1000) });
    try {
      await writer.write(frame);
    } finally {
      frame.close();
    }
  }

  // The agent's round head and name, while no Unreal frame is coming in
  function drawCard() {
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2 - 50;
    context.fillStyle = "#141414";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.lineWidth = 8;
    context.strokeStyle = "#1A1A1A";
    context.fillStyle = "#F4EEE6";
    context.beginPath();
    context.arc(centerX, centerY, 90, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    context.fillStyle = settings.eyeColor;
    for (const offset of [-32, 32]) {
      context.beginPath();
      context.arc(centerX + offset, centerY - 6, 19, 0, Math.PI * 2);
      context.fill();
      context.stroke();
    }
    context.textAlign = "center";
    context.fillStyle = "#EDEDED";
    context.font = "600 42px Helvetica, Arial, sans-serif";
    context.fillText(settings.agent, centerX, centerY + 160);
    context.fillStyle = "#8A8A8A";
    context.font = "28px Helvetica, Arial, sans-serif";
    context.fillText("Connecting to Unreal...", centerX, centerY + 205);
  }

  setInterval(() => {
    if (Date.now() - pictureAt > settings.staleMilliseconds) {
      drawCard();
      if (writer) {
        write(canvas);
      }
    }
  }, 500);

  let silence = null;
  let speechAudio = null;
  let speechDestination = null;
  function silentTrack() {
    // With voice on, the microphone is the agent's voice (Tools/copresence_call_voice.py)
    if (settings.voice && window.__copresenceMouth) {
      return window.__copresenceMouth.track();
    }
    if (!silence) {
      const audio = speechAudio = new AudioContext();
      const destination = speechDestination = audio.createMediaStreamDestination();
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      gain.gain.value = 0;
      oscillator.connect(gain).connect(destination);
      oscillator.start();
      silence = destination.stream.getAudioTracks()[0];
    }
    return silence.clone();
  }

  // Meet stops the tracks it gets between the preview and the call, so each request gets
  // its own copy and the camera itself keeps running
  function cameraTrack() {
    const track = camera.clone();
    // Generated tracks reject size constraints that a real camera accepts
    track.applyConstraints = () => Promise.resolve();
    return track;
  }

  const devices = [
    { deviceId: "copresence-camera", groupId: "copresence", kind: "videoinput", label: settings.agent + " view" },
    { deviceId: "copresence-microphone", groupId: "copresence", kind: "audioinput",
      label: settings.voice ? settings.agent + " voice" : "Silent microphone" },
    { deviceId: "default", groupId: "copresence", kind: "audiooutput", label: "Default speaker" },
  ].map((device) => ({ ...device, toJSON: () => device, getCapabilities: () => ({}) }));

  navigator.mediaDevices.getUserMedia = async (constraints) => {
    debug.log("getUserMedia", JSON.stringify(constraints));
    const tracks = [];
    if (constraints && constraints.video) {
      tracks.push(cameraTrack());
    }
    if (constraints && constraints.audio) {
      tracks.push(silentTrack());
    }
    return new MediaStream(tracks);
  };
  navigator.mediaDevices.enumerateDevices = async () => devices;

  // Keeps Meet's connections, so the log can say what frame rate Meet really sends
  const connections = [];
  const NativeConnection = window.RTCPeerConnection;
  if (NativeConnection) {
    class TrackedConnection extends NativeConnection {
      constructor(...parts) {
        super(...parts);
        connections.push(this);
      }
    }
    window.RTCPeerConnection = TrackedConnection;
    window.webkitRTCPeerConnection = TrackedConnection;
  }

  window.__copresenceCamera = {
    // Speech must feed the same source as any audio track Meet already cached.
    speechOutput() {
      // With voice on there is one microphone: the voice's (Tools/copresence_call_voice.py)
      if (settings.voice && window.__copresenceMouth) {
        return window.__copresenceMouth.output();
      }
      if (!silence) { const probe = silentTrack(); probe.stop(); }
      return {audio: speechAudio, destination: speechDestination};
    },
    async show(base64) {
      const text = atob(base64);
      const bytes = new Uint8Array(text.length);
      for (let index = 0; index < text.length; index++) {
        bytes[index] = text.charCodeAt(index);
      }
      const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
      try {
        await write(bitmap);
      } finally {
        bitmap.close();
      }
      pictureAt = Date.now();
      framesShown += 1;
    },
    async stats() {
      const sending = [];
      const audioSending = [];
      for (const connection of connections) {
        if (connection.connectionState === "closed") {
          continue;
        }
        const report = await connection.getStats();
        report.forEach((entry) => {
          if (entry.type === "outbound-rtp" && entry.kind === "audio") {
            audioSending.push({bytesSent: entry.bytesSent || 0, packetsSent: entry.packetsSent || 0,
                               active: entry.active !== false});
          }
          if (entry.type === "media-source" && entry.kind === "audio") {
            audioSending.push({audioLevel: entry.audioLevel || 0,
                               totalAudioEnergy: entry.totalAudioEnergy || 0});
          }
          if (entry.type === "outbound-rtp" && entry.kind === "video") {
            sending.push({ fps: entry.framesPerSecond || 0, width: entry.frameWidth || 0, height: entry.frameHeight || 0,
                           limit: entry.qualityLimitationReason || "", active: entry.active !== false });
          }
        });
      }
      return { framesShown, generator: Boolean(writer), sending, audioSending };
    },
  };
})();
"""


def log(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {text}", flush=True)


def profile_dir(agent: str) -> Path:
    return PROFILE_DIR / f"chrome-{copresence_mcp.agent_slug(agent)}"


# NDI -------------------------------------------------------------------------

class _VideoFrame(ctypes.Structure):
    _fields_ = [("xres", ctypes.c_int), ("yres", ctypes.c_int), ("fourcc", ctypes.c_uint32),
                ("frame_rate_n", ctypes.c_int), ("frame_rate_d", ctypes.c_int),
                ("picture_aspect_ratio", ctypes.c_float), ("frame_format_type", ctypes.c_int),
                ("timecode", ctypes.c_int64), ("data", ctypes.c_void_p), ("line_stride_in_bytes", ctypes.c_int),
                ("metadata", ctypes.c_char_p), ("timestamp", ctypes.c_int64)]


class _FindCreate(ctypes.Structure):
    _fields_ = [("show_local_sources", ctypes.c_bool), ("groups", ctypes.c_char_p), ("extra_ips", ctypes.c_char_p)]


class _Source(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("url_address", ctypes.c_char_p)]


class _RecvCreate(ctypes.Structure):
    _fields_ = [("source", _Source), ("color_format", ctypes.c_int), ("bandwidth", ctypes.c_int),
                ("allow_video_fields", ctypes.c_bool), ("name", ctypes.c_char_p)]


NDI_VIDEO_FRAME = 1
NDI_COLOR_BGRX_BGRA = 0
NDI_BANDWIDTH_HIGHEST = 100
# Seconds without a frame after which the source counts as gone and is looked up again
NDI_LOST_SECONDS = 5


def _load_ndi() -> ctypes.CDLL:
    if not NDI_LIBRARY.exists():
        raise RuntimeError(f"no NDI library at {NDI_LIBRARY}; set COPRESENCE_ENGINE_DIR to your UE 5.8 Engine folder")
    library = ctypes.CDLL(str(NDI_LIBRARY))
    library.NDIlib_initialize.restype = ctypes.c_bool
    library.NDIlib_find_create_v2.restype = ctypes.c_void_p
    library.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(_FindCreate)]
    library.NDIlib_find_wait_for_sources.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    library.NDIlib_find_get_current_sources.restype = ctypes.POINTER(_Source)
    library.NDIlib_find_get_current_sources.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    library.NDIlib_find_destroy.argtypes = [ctypes.c_void_p]
    library.NDIlib_recv_create_v3.restype = ctypes.c_void_p
    library.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(_RecvCreate)]
    library.NDIlib_recv_capture_v2.restype = ctypes.c_int
    library.NDIlib_recv_capture_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(_VideoFrame), ctypes.c_void_p,
                                               ctypes.c_void_p, ctypes.c_uint32]
    library.NDIlib_recv_free_video_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(_VideoFrame)]
    library.NDIlib_recv_destroy.argtypes = [ctypes.c_void_p]
    if not library.NDIlib_initialize():
        raise RuntimeError("the NDI library didn't start")
    return library


class NdiFrames:
    """The agent's call camera as Unreal sends it over NDI, turned into JPEG frames for the
    call page. It receives on a thread of its own, and the page always gets the newest
    frame, so a slow moment drops frames instead of falling behind."""

    def __init__(self, source_name: str):
        self.source_name = source_name
        self.library = _load_ndi()
        self.lock = threading.Lock()
        self.latest = None
        self.received = 0
        self.loop = None
        self.arrived = None
        self.stopping = threading.Event()
        self.thread = None

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.arrived = asyncio.Event()
        self.thread = threading.Thread(target=self._receive, name="NdiFrames", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    async def next_frame(self) -> bytes:
        await self.arrived.wait()
        self.arrived.clear()
        with self.lock:
            return self.latest

    def _find(self) -> _Source:
        finder = self.library.NDIlib_find_create_v2(ctypes.byref(_FindCreate(True, None, None)))
        suffix = f"({self.source_name})".encode()
        try:
            while not self.stopping.is_set():
                self.library.NDIlib_find_wait_for_sources(finder, 1000)
                count = ctypes.c_uint32(0)
                sources = self.library.NDIlib_find_get_current_sources(finder, ctypes.byref(count))
                matches = [sources[index] for index in range(count.value) if (sources[index].name or b"").endswith(suffix)]
                # The agent's editor runs on this machine, so its own source comes first
                matches.sort(key=lambda source: not (source.url_address or b"").startswith(b"127.0.0.1"))
                if matches:
                    return _Source(matches[0].name, matches[0].url_address)
        finally:
            self.library.NDIlib_find_destroy(finder)

    def _receive(self) -> None:
        while not self.stopping.is_set():
            source = self._find()
            if source is None:
                return
            log(f"receiving {source.name.decode()}")
            receiver = self.library.NDIlib_recv_create_v3(ctypes.byref(
                _RecvCreate(source, NDI_COLOR_BGRX_BGRA, NDI_BANDWIDTH_HIGHEST, False, b"CoPresence call")))
            quiet_seconds = 0
            while quiet_seconds < NDI_LOST_SECONDS and not self.stopping.is_set():
                frame = _VideoFrame()
                if self.library.NDIlib_recv_capture_v2(receiver, ctypes.byref(frame), None, None, 1000) != NDI_VIDEO_FRAME:
                    quiet_seconds += 1
                    continue
                quiet_seconds = 0
                size, stride = (frame.xres, frame.yres), frame.line_stride_in_bytes
                pixels = ctypes.string_at(frame.data, stride * frame.yres)
                self.library.NDIlib_recv_free_video_v2(receiver, ctypes.byref(frame))
                buffer = io.BytesIO()
                Image.frombuffer("RGB", size, pixels, "raw", "BGRX", stride, 1).save(buffer, "JPEG", quality=JPEG_QUALITY)
                with self.lock:
                    self.latest = buffer.getvalue()
                    self.received += 1
                self.loop.call_soon_threadsafe(self.arrived.set)
            self.library.NDIlib_recv_destroy(receiver)
            if not self.stopping.is_set():
                log("the NDI source went quiet; looking for it again")


# The agent's call camera in Unreal --------------------------------------------

class CallCamera:
    """Starts the agent's call camera in its editor and keeps it running for the call; the
    camera stops by itself shortly after the keep-alives stop."""

    def __init__(self, source_name: str, view_mode: str = "ghost"):
        if view_mode not in ("ghost", "viewport", "editor_viewport"):
            raise ValueError("view_mode must be ghost, viewport, or editor_viewport")
        self.view_mode = view_mode
        self.source_name = source_name
        # Its own connection, so keep-alives don't share one with the other calls
        self.unreal = copresence_mcp.UnrealMcp(copresence_mcp.UNREAL_MCP_URL)

    def _call(self, tool: str, arguments: dict) -> dict:
        return json.loads(self.unreal.call_toolset(CAMERA_TOOLSET, tool, arguments))

    def start(self) -> dict:
        return self._call("start_call_camera", {"source_name": self.source_name, "width": FRAME_SIZE[0],
                                                "height": FRAME_SIZE[1], "frames_per_second": FRAMES_PER_SECOND,
                                                "view_mode": self.view_mode})

    def keep(self) -> dict:
        state = self._call("keep_call_camera", {})
        # The agent's editor restarted since; start the camera again
        return state if state.get("running") else self.start()

    def stop(self) -> None:
        try:
            self._call("stop_call_camera", {})
        except Exception as error:
            log(f"couldn't stop the call camera: {copresence_mcp._short_error(error)}")


class AvatarFace:
    """Moves the MetaHuman avatar's mouth in the agent's editor as the voice speaks
    (Plugins/CoPresence/.../voice_face.py). Mouth shapes go one call at a time on a thread
    of their own, in order and merged where they follow on, so the voice never waits for
    the editor."""

    QUEUE_LIMIT = 200

    def __init__(self):
        self.unreal = copresence_mcp.UnrealMcp(copresence_mcp.UNREAL_MCP_URL)
        self.queue: asyncio.Queue | None = None
        self.task = None
        self.problem = ""

    def _call(self, tool: str, arguments: dict) -> dict:
        return json.loads(self.unreal.call_toolset(CAMERA_TOOLSET, tool, arguments))

    async def start(self) -> bool:
        """Whether the agent's level has an avatar to move; starts sending if so."""
        loop = asyncio.get_running_loop()
        try:
            state = await loop.run_in_executor(None, self._call, "voice_face_status", {})
        except Exception as error:
            log(f"face: couldn't ask the agent's editor about an avatar: {copresence_mcp._short_error(error)}")
            return False
        if not state.get("available"):
            log("face: no MetaHuman avatar in the agent's level, so its ghost shows the voice instead")
            return False
        self.queue = asyncio.Queue(maxsize=self.QUEUE_LIMIT)
        self.task = asyncio.create_task(self._send_all())
        return True

    def send(self, epoch: float, shapes: list) -> None:
        if self.queue is not None and not self.queue.full():
            self.queue.put_nowait(("frames", epoch, shapes))

    def stop(self) -> None:
        if self.queue is None:
            return
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(("stop", 0.0, []))

    async def _send_all(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            kind, epoch, shapes = await self.queue.get()
            # Pieces that follow on from each other go in one call
            while kind == "frames" and not self.queue.empty():
                next_kind, next_epoch, next_shapes = self.queue._queue[0]
                if next_kind != "frames" or abs(next_epoch - (epoch + len(shapes) / 60)) > 1 / 120:
                    break
                self.queue.get_nowait()
                shapes = shapes + next_shapes
            try:
                if kind == "stop":
                    await loop.run_in_executor(None, self._call, "voice_face_stop", {})
                else:
                    # The clock time goes inside the JSON: tool numbers are 32-bit, too coarse for it
                    await loop.run_in_executor(None, self._call, "voice_face_frames",
                                               {"frames_json": json.dumps({"start": epoch, "shapes": shapes})})
                self.problem = ""
            except Exception as error:
                problem = copresence_mcp._short_error(error)
                if problem != self.problem:
                    log(f"face: couldn't move the avatar's mouth: {problem}")
                self.problem = problem


class SpectatorFeed:
    """The spectator's camera (Plugins/CoPresenceCall, spectator_camera), which films on its own
    for as long as the spectator's editor runs; the call only makes sure it is there."""

    def __init__(self):
        self.unreal = copresence_mcp.UnrealMcp(copresence_mcp.UNREAL_MCP_URL)
        self.source_name = ""

    def _call(self, tool: str) -> dict:
        return json.loads(self.unreal.call_toolset(CAMERA_TOOLSET, tool, {}))

    def start(self) -> dict:
        state = self._call("spectator_status")
        if not state.get("running"):
            state = self._call("start_spectator_camera")
        self.source_name = state["source"]
        return state

    def keep(self) -> dict:
        # A restarted spectator editor starts its camera on its own; this only checks
        return self.start()

    def stop(self) -> None:
        # It keeps filming for the dashboard after the call
        pass


class Announcer:
    """Says in Agent Chat how the call is going: as the agent, or, for the spectator, only
    when the session goes on show and when it stops, without making it look like an agent."""

    def __init__(self, agent: str, spectator: bool = False):
        self.agent = agent
        self.spectator = spectator

    async def say(self, key: str, **values) -> None:
        if not self.spectator:
            await report(self.agent, AGENT_LINES[key].format(**values))
        elif key in SPECTATOR_LINES:
            await post_notice(self.agent, SPECTATOR_LINES[key])
        else:
            log(AGENT_LINES[key].format(**values))


async def keep_camera_running(camera: CallCamera) -> None:
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(KEEP_ALIVE_SECONDS)
        try:
            await loop.run_in_executor(None, camera.keep)
        except Exception as error:
            log(f"couldn't reach the agent's editor: {copresence_mcp._short_error(error)}")


# Meet --------------------------------------------------------------------------

async def _visible_button(page, pattern: str):
    button = page.get_by_role("button", name=re.compile(pattern, re.I)).first
    try:
        if await button.count() and await button.is_visible():
            return button
    except PlaywrightError:
        pass
    return None


async def _click(page, pattern: str) -> bool:
    button = await _visible_button(page, pattern)
    if button is None:
        return False
    try:
        await button.click(timeout=3000)
        return True
    except PlaywrightError:
        return False


async def _page_text(page) -> str:
    try:
        return await page.evaluate("document.body ? document.body.innerText : ''")
    except PlaywrightError:
        return ""


async def _join_here_too(page) -> bool:
    """Clicks "Join here too", opening "Other ways to join" first when Meet tucks it there.
    Meet's labels carry icon names ("add_to_queue Join here too"), so this goes by text."""
    async def click_text(text: str) -> bool:
        target = page.get_by_text(text, exact=True).first
        try:
            if await target.count() and await target.is_visible():
                await target.click(timeout=3000)
                return True
        except PlaywrightError:
            pass
        return False
    await _click(page, r"^Got it$")
    if await click_text("Join here too"):
        return True
    if await click_text("Other ways to join"):
        await asyncio.sleep(0.8)
        return await click_text("Join here too")
    return False


class MeetCall:
    """Meet's pre-join screen, the waiting room and the call itself, driven through the page."""

    def __init__(self, page, agent: str, screenshot_path: Path, voice: bool = False,
                 announcer: "Announcer | None" = None):
        self.page = page
        self.agent = agent
        self.screenshot_path = screenshot_path
        self.voice = voice
        self.announcer = announcer

    async def screenshot(self, suffix: str) -> None:
        path = self.screenshot_path.with_name(f"{self.screenshot_path.stem}-{suffix}.png")
        try:
            await self.page.screenshot(path=str(path))
            log(f"screenshot {path}")
        except PlaywrightError as error:
            log(f"screenshot failed: {error}")

    async def _mic_off_camera_on(self) -> None:
        # With voice, the microphone is the agent's voice and stays on
        await _click(self.page, r"Turn on microphone" if self.voice else r"Turn off microphone")
        await _click(self.page, r"Turn on camera")

    async def prepare(self) -> str:
        """Gets to the point of asking to join: name filled in, mic off (on with voice), camera on. Returns
        "ready" or what stopped it."""
        deadline = time.monotonic() + JOIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            text = await _page_text(self.page)
            if REFUSED.search(text):
                return REFUSED.search(text).group(0)
            if await _visible_button(self.page, DUPLICATE_BUTTON) and not JOIN_HERE_TOO:
                return DUPLICATE_REASON
            # The least sharing that still lets the call see the camera
            await _click(self.page, r"Reject all")
            await _click(self.page, r"^(Got it|Dismiss)$")
            await _click(self.page, r"(Allow|Use) microphone and camera")
            name_box = self.page.get_by_role("textbox", name=re.compile(r"name", re.I)).first
            try:
                if await name_box.count() and await name_box.is_visible() and not await name_box.input_value():
                    await name_box.fill(self.agent)
            except PlaywrightError:
                pass
            await self._mic_off_camera_on()
            if await _visible_button(self.page, JOIN_BUTTON) or (
                    JOIN_HERE_TOO and await _visible_button(self.page, DUPLICATE_BUTTON)):
                # The mic and camera buttons can show up a moment after the join button
                await asyncio.sleep(1.5)
                await self._mic_off_camera_on()
                return "ready"
            await asyncio.sleep(1.0)
        return "the join button never showed up"

    async def ask_to_join(self) -> str:
        # Recheck after preview: another device may have joined in the meantime.
        if JOIN_HERE_TOO and await _visible_button(self.page, DUPLICATE_BUTTON):
            return "requested" if await _join_here_too(self.page) else "couldn't find Join here too"
        if await _visible_button(self.page, DUPLICATE_BUTTON):
            return DUPLICATE_REASON
        if not await _click(self.page, JOIN_BUTTON):
            # A signed-in account Meet trusts can be let straight in while preparing
            if await _visible_button(self.page, r"^Leave call"):
                return "requested"
            return "the join button is no longer available"
        return "requested"

    async def wait_until_in(self, stop: asyncio.Event) -> str:
        """Waits for someone to admit the agent: "in", "stopped", or what went wrong."""
        deadline = time.monotonic() + ADMIT_TIMEOUT_SECONDS
        said_waiting = False
        while time.monotonic() < deadline and not stop.is_set():
            if await _visible_button(self.page, r"^Leave call"):
                return "in"
            text = await _page_text(self.page)
            if REFUSED.search(text):
                return REFUSED.search(text).group(0)
            if WAITING.search(text) and not said_waiting:
                said_waiting = True
                await (self.announcer or Announcer(self.agent)).say("waiting")
                await report_call(self.agent, "waiting")
            await asyncio.sleep(1.0)
        return "stopped" if stop.is_set() else "nobody let me in"

    async def stay(self, stop: asyncio.Event) -> str:
        """Stays in the call until it ends, the agent is removed, or it is told to leave."""
        while not stop.is_set():
            text = await _page_text(self.page)
            if REFUSED.search(text) or ENDED.search(text):
                return (REFUSED.search(text) or ENDED.search(text)).group(0)
            # Meet asks now and then whether anyone is still there
            await _click(self.page, r"^(Continue|Keep waiting|I'm still here)$")
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        return "stopped"

    async def leave(self) -> None:
        if await _click(self.page, r"^Leave call"):
            await asyncio.sleep(1.0)


async def report(agent: str, text: str) -> None:
    """Says it in Agent Chat as the agent, so the session knows where its view is going."""
    log(text)
    try:
        await asyncio.get_running_loop().run_in_executor(None, copresence_mcp._post_chat, text, "", text)
    except Exception as error:
        log(f"couldn't post to Agent Chat: {error}")


async def post_notice(author: str, text: str) -> None:
    """Says it in Agent Chat under author's name, without the status report an agent's lines
    come with, which would list the author as an agent everywhere."""
    log(text)
    payload = {"author": author, "role": "agent", "text": text, "to": "", "code": copresence_mcp._session_code()}
    try:
        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: copresence_mcp.HUB.post("/api/messages", json=payload, timeout=5))
        response.raise_for_status()
    except Exception as error:
        log(f"couldn't post to Agent Chat: {error}")


async def report_call(agent: str, state: str) -> None:
    """Tells the session hub where the agent is with the call: joining, waiting, in_call or
    left. Every editor marks the agent in its Agents tab while it is in the call."""
    payload = {"agent": agent, "state": state, "code": copresence_mcp._session_code()}
    try:
        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: copresence_mcp.HUB.post("/api/call", json=payload, timeout=5))
        response.raise_for_status()
    except Exception as error:
        log(f"couldn't tell the session hub about the call: {error}")


async def push_frames(page, frames: NdiFrames) -> None:
    """Frames into the call page this bot launched (join): the newest frame each time."""
    while True:
        image = await frames.next_frame()
        try:
            await page.evaluate("data => window.__copresenceCamera && window.__copresenceCamera.show(data)",
                                base64.b64encode(image).decode())
        except PlaywrightError:
            # The page is loading or going away; the next frame tries again
            await asyncio.sleep(0.2)


async def push_frames_via_pump(page, frames: NdiFrames) -> None:
    """Frames into a call page attached over Chrome's debugging port (Luna2's attach flow),
    through a separate pump process, so control work never stalls delivery of frames."""
    import sys
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).with_name('copresence_video_pump.py')),
        page.url.split('?')[0], frames.source_name)
    try:
        code = await process.wait()
        raise RuntimeError(f'Video frame pump stopped ({code})')
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def log_stats(page, frames: NdiFrames) -> None:
    """Says now and then how many frames Unreal sends, how many the page shows, and what
    Meet sends on: Meet picks its own frame rate and size for a camera."""
    received = frames.received
    initial = await page.evaluate("() => window.__copresenceCamera ? window.__copresenceCamera.stats() : null")
    shown = initial["framesShown"] if initial else 0
    while True:
        await asyncio.sleep(STATS_SECONDS)
        try:
            stats = await page.evaluate("() => window.__copresenceCamera ? window.__copresenceCamera.stats() : null")
        except PlaywrightError:
            continue
        if not stats:
            continue
        sending = ", ".join(f"{layer['width']}x{layer['height']} at {layer['fps']} fps"
                            + (f" (limited by {layer['limit']})" if layer["limit"] not in ("", "none") else "")
                            for layer in stats["sending"] if layer["active"]) or "nothing yet"
        log(f"Unreal sends {(frames.received - received) / STATS_SECONDS:.0f} fps, the page shows "
            f"{(stats['framesShown'] - shown) / STATS_SECONDS:.0f} fps, Meet sends {sending}")
        received, shown = frames.received, stats["framesShown"]


# Joining -----------------------------------------------------------------------

def cookie_files(profile: Path) -> list[Path]:
    """Where Chrome keeps the profile's cookies: Default/Cookies on a Mac, and
    Default/Network/Cookies on Windows and Linux."""
    return [path for path in (profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies")
            if path.exists()]


def signed_in(agent: str) -> bool:
    """Whether the agent's Chrome profile holds a Google sign-in. Cookie names aren't
    encrypted, so the database can be read without the keychain or Windows' DPAPI. Chrome on
    Windows keeps the database locked while it runs, so there this only answers once Chrome
    has closed."""
    names = set()
    for cookies in cookie_files(profile_dir(agent)):
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / "Cookies"
            try:
                shutil.copy(cookies, copy)
                # closing(): an open connection keeps Windows from deleting the copy
                with closing(sqlite3.connect(copy)) as database:
                    names |= {row[0] for row in database.execute(
                        "SELECT name FROM cookies WHERE host_key = 'google.com' OR host_key LIKE '%.google.com'")}
            except (OSError, sqlite3.Error):
                # Chrome is holding or rewriting the file
                continue
    return bool(names & {"SID", "__Secure-1PSID", "__Secure-3PSID"})


def chrome_path() -> str | None:
    """Google Chrome's executable on Windows or Linux, where `open -a` doesn't exist."""
    candidates = [os.environ.get("COPRESENCE_CHROME", "")]
    if WINDOWS:
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                     os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    candidates += [shutil.which(name) or "" for name in ("chrome", "google-chrome", "google-chrome-stable")]
    return next((path for path in candidates if path and Path(path).is_file()), None)


class SignInChrome:
    """Plain Chrome on the agent's profile, which Google lets people sign in to."""

    def __init__(self, profile: Path):
        self.profile = profile
        self.args = [f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                     "https://accounts.google.com/"]
        self.process = None

    def open(self) -> None:
        if MAC:
            subprocess.run(["open", "-n", "-a", "Google Chrome", "--args", *self.args], check=True)
            return
        chrome = chrome_path()
        if chrome is None:
            raise SystemExit("Google Chrome isn't installed; set COPRESENCE_CHROME to its executable")
        self.process = subprocess.Popen([chrome, *self.args])

    def exited(self) -> bool:
        """Whether the window was closed. Only known where this started Chrome itself."""
        return self.process is not None and self.process.poll() is not None

    def close(self) -> None:
        """Chrome writes the rest of the sign-in on its way out, and the profile is free for
        the call once it is gone, so it is asked to close rather than killed."""
        if MAC:
            subprocess.run(["pkill", "-TERM", "-f", f"user-data-dir={self.profile}"], check=False)
            for _ in range(20):
                if subprocess.run(["pgrep", "-f", f"user-data-dir={self.profile}"], capture_output=True).returncode:
                    return
                time.sleep(0.5)
            return
        if self.process is None or self.exited():
            return
        if WINDOWS:
            # Without /F, taskkill closes Chrome's windows the way their close buttons do
            subprocess.run(["taskkill", "/PID", str(self.process.pid)], capture_output=True, check=False)
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()


def sign_in(agent: str) -> int:
    """Opens the agent's Chrome profile in plain Chrome for a person to sign in to Google,
    and closes it once a Google sign-in shows up in it. On Windows the sign-in can only be
    read once Chrome has closed, so there the person closes the window when done."""
    profile = profile_dir(agent)
    profile.mkdir(parents=True, exist_ok=True)
    chrome = SignInChrome(profile)
    started = time.monotonic()
    chrome.open()
    if WINDOWS:
        log(f"Sign {agent} in to Google in the Chrome window that just opened, then close that window")
    else:
        log(f"Sign {agent} in to Google in the Chrome window that just opened")
    while time.monotonic() < started + 15 * 60:
        time.sleep(3)
        if chrome.exited():
            break
        if signed_in(agent):
            chrome.close()
            log(f"{agent} is signed in to Google")
            return 0
    chrome.close()
    if signed_in(agent):
        log(f"{agent} is signed in to Google")
        return 0
    if chrome.exited() and time.monotonic() - started < 10:
        log("Chrome closed right away; it may already be open on this profile. Close it and run sign-in again")
    else:
        log("No Google sign-in showed up")
    return 1


def stop_on_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Ctrl+C and SIGTERM make the call leave first. Windows' event loop has no
    add_signal_handler, so there Ctrl+C and Ctrl+Break go through signal.signal."""
    try:
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(stop_signal, stop.set)
    except NotImplementedError:
        for name in ("SIGINT", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: loop.call_soon_threadsafe(stop.set))


async def watch_leave_file(path: Path, stop: asyncio.Event) -> None:
    """leave_call asks a call to leave through this file where there is no SIGTERM."""
    while not stop.is_set():
        if path.exists():
            log("asked to leave the call")
            stop.set()
            return
        await asyncio.sleep(1.0)


def voice_key() -> str | None:
    """The OpenAI key for voice on this machine, read once (it may wait on 1Password), or None."""
    import copresence_agent_voice
    try:
        key, source = copresence_agent_voice.openai_key()
    except SystemExit as reason:
        log(f"voice off: {reason}")
        return None
    log(f"voice: OpenAI key from {source}")
    return key


async def join(url: str, dry_run: bool, voice: bool = True, view_mode: str = "ghost",
               spectator: bool = False, name: str = "") -> int:
    loop = asyncio.get_running_loop()
    agent = name or await loop.run_in_executor(None, copresence_mcp._agent_name)
    if copresence_mcp.running_call(agent):
        log(f"{agent} is already in a call")
        return 1
    pid_path = copresence_mcp.call_pid_path(agent)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))
    try:
        if spectator:
            return await _join(url, dry_run, agent, False, "viewport", spectator=True)
        return await _join(url, dry_run, agent, voice, view_mode)
    finally:
        if pid_path.exists() and pid_path.read_text().strip() == str(os.getpid()):
            pid_path.unlink()


async def _join(url: str, dry_run: bool, agent: str, voice: bool = False, view_mode: str = "ghost",
                spectator: bool = False) -> int:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    stop_path = PROFILE_DIR / f"stop-{os.getpid()}"
    stop_path.unlink(missing_ok=True)
    async def watch_stop():
        while not stop.is_set():
            if stop_path.exists():
                stop.set()
                stop_path.unlink(missing_ok=True)
                return
            await asyncio.sleep(0.25)
    stop_on_signals(loop, stop)
    leave_path = copresence_mcp.call_leave_path(agent)
    leave_path.unlink(missing_ok=True)

    camera = SpectatorFeed() if spectator else CallCamera(f"CoPresence {agent}", view_mode)
    announcer = Announcer(agent, spectator)
    try:
        state = await loop.run_in_executor(None, camera.start)
    except Exception as error:
        await announcer.say("no_camera")
        log(f"call camera: {copresence_mcp._short_error(error)}")
        return 1
    watcher = asyncio.create_task(watch_leave_file(leave_path, stop))
    log(f"call camera: {state}")
    frames = NdiFrames(camera.source_name)
    frames.start()

    eye_color = copresence_mcp._appearance(agent).get("eye_color") or "#69CDEE"
    key = await loop.run_in_executor(None, voice_key) if voice else None
    voice = key is not None
    settings = {"agent": agent, "eyeColor": eye_color, "width": FRAME_SIZE[0], "height": FRAME_SIZE[1],
                "fps": FRAMES_PER_SECOND, "staleMilliseconds": STALE_FRAME_SECONDS * 1000, "debug": False,
                "voice": voice}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = LOG_DIR / f"call-{copresence_mcp.agent_slug(agent)}.png"

    async with async_playwright() as playwright:
        # The real keychain, not Playwright's mock one, so a Google sign-in made with
        # `sign-in` in plain Chrome can be read back
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir(agent)), channel="chrome", headless=False,
            args=CHROME_ARGS, ignore_default_args=["--enable-automation", "--use-mock-keychain"], locale="en-US",
            viewport={"width": 1280, "height": 800}, permissions=["camera", "microphone"])
        if voice:
            import copresence_call_voice
            await context.add_init_script(script=copresence_call_voice.VOICE_SCRIPT)
        await context.add_init_script(script=CAMERA_SCRIPT.replace("__SETTINGS__", json.dumps(settings)))
        page = context.pages[0] if context.pages else await context.new_page()
        # Someone closing the call's window ends the call for the agent too
        page.on("close", lambda _: stop.set())
        context.on("close", lambda _: stop.set())
        tasks = [asyncio.create_task(watch_stop()), asyncio.create_task(push_frames(page, frames)), asyncio.create_task(keep_camera_running(camera)),
                 asyncio.create_task(log_stats(page, frames))]
        call = MeetCall(page, agent, screenshot_path, voice, announcer)
        voice_stop = asyncio.Event()
        outcome = "stopped"
        asked_to_join = False
        try:
            log(f"opening {url} as {agent}")
            await page.goto(url, wait_until="domcontentloaded")
            prepared = await call.prepare()
            await call.screenshot("prejoin")
            if prepared != "ready":
                await announcer.say("not_ready", reason=prepared)
                return 1
            if dry_run:
                log("dry run: stopping before asking to join")
                return 0
            await announcer.say("joining")
            asked_to_join = True
            await report_call(agent, "joining")
            requested = await call.ask_to_join()
            if requested != "requested":
                await announcer.say("not_requested", reason=requested)
                return 1
            outcome = await call.wait_until_in(stop)
            if outcome != "in":
                await call.screenshot("refused")
                await announcer.say("refused", reason=outcome)
                return 1
            await call.screenshot("in-call")
            if voice:
                import copresence_call_voice
                # The avatar's mouth moves with the voice when the call films the avatar
                face = AvatarFace() if view_mode == "viewport" else None

                async def scenes(scene: str) -> dict:
                    return await loop.run_in_executor(None, copresence_mcp.switch_scene_now, scene)
                talker = copresence_call_voice.CallVoice(page, agent, copresence_mcp._hub_url(),
                                                         copresence_mcp._session_code(), copresence_mcp._appearance(agent),
                                                         key, face, scenes if view_mode == "viewport" else None)
                tasks.append(asyncio.create_task(talker.run(voice_stop)))
                await announcer.say("in_call_voice")
            else:
                await announcer.say("in_call")
            await report_call(agent, "in_call")
            outcome = await call.stay(stop)
            await call.leave()
            await announcer.say("left")
            return 0
        finally:
            # However the call ends, errors included, editors stop marking the agent as in it
            voice_stop.set()
            if asked_to_join:
                await report_call(agent, "left")
            for task in tasks + [watcher]:
                task.cancel()
            leave_path.unlink(missing_ok=True)
            await loop.run_in_executor(None, camera.stop)
            log(f"done: {outcome}")
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    join_command = commands.add_parser("join", help="Join a Google Meet call as the agent")
    join_command.add_argument("link", help="The Meet link, e.g. https://meet.google.com/abc-defg-hij")
    join_command.add_argument("--dry-run", action="store_true", help="Stop before asking to join")
    join_command.add_argument("--no-voice", action="store_true", help="Stay silent and don't listen to the call")
    join_command.add_argument("--join-here-too", action="store_true",
                              help="Join even when this Google account is already in the call on another device")
    join_command.add_argument("--view-mode", choices=("ghost", "viewport", "editor_viewport"), default="ghost",
                              help="Film the ghost or the editor camera framing an avatar")
    join_command.add_argument("--spectator", action="store_true",
                              help="Show the spectator's camera (Tools/copresence_spectator.py), without voice")
    join_command.add_argument("--as", dest="name", default="", help="Join under this name rather than the editor's")
    sign_in_command = commands.add_parser("sign-in", help="Sign the agent's call browser in to Google once")
    sign_in_command.add_argument("--as", dest="name", default="", help="Whose call browser, rather than the editor's")
    arguments = parser.parse_args()

    if arguments.command == "sign-in":
        sys.exit(sign_in(arguments.name or copresence_mcp._agent_name()))
    url = copresence_mcp.meet_link(arguments.link)
    if url is None:
        sys.exit("Only Google Meet links work so far, like https://meet.google.com/abc-defg-hij")
    if arguments.join_here_too:
        global JOIN_HERE_TOO
        JOIN_HERE_TOO = True
    sys.exit(asyncio.run(join(url, arguments.dry_run, not arguments.no_voice, arguments.view_mode,
                              arguments.spectator, arguments.name)))


if __name__ == "__main__":
    main()
