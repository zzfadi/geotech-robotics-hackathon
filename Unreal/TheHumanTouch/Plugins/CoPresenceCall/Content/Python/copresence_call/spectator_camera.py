"""The spectator's camera: films the session from a session client of its own, the spectator
(Tools/copresence_spectator.py), which is neither a person nor an agent, and sends the picture out
as the NDI source "CoPresence <spectator name>". The call bot (Tools/copresence_call.py join
--spectator) shows that picture in a video call, so people outside VR see the session there.

It follows whoever the session dashboard picks, a person in VR or at a desktop editor or an
agent, from behind and above the way people follow an agent in the Agents tab, or through their
eyes. Following nobody, it flies freely: it films what the editor's viewport sees, so someone at
the spectator's machine can fly it with the mouse, and the dashboard's arrows move it too.

Runs in the spectator's editor, which starts it on its own (-COPRESENCESPECTATOR). Where to
look comes from the session hub, which the dashboard and `copresence_spectator.py follow` set.
It tells the hub how it is doing and sends it pictures for the dashboard, several a second, up one
long request the hub plays to the dashboard as they come. People see the spectator where it films
from: its viewport, which Multi-User shows everyone as its avatar, keeps up with the camera.
"""

import http.client
import json
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import unreal

from copresence import hub_client, tracking
from copresence_call import call_camera, roster

WIDTH, HEIGHT = 1280, 720
# Meet sends at most 30 frames a second, so more would only cost the GPU
FRAMES_PER_SECOND = 30
FIELD_OF_VIEW = 80.0
FRAMINGS = ("behind", "eyes")
# Who it follows is looked for again this often, as they come and go
FIND_SECONDS = 1.0
# Through someone's eyes the camera eases after their head, so a glance doesn't shake the picture
EYES_MOVE_SPEED = 10.0
EYES_TURN_SPEED = 6.0
# The followed avatar is kept out of the picture when it is this close to the camera
EYES_HIDE_DISTANCE = 150.0
# A press of the dashboard's arrows moves the camera this far, glided over a moment
NUDGE_DISTANCE = 300.0
NUDGE_DEGREES = 20.0
NUDGE_SECONDS = 0.35
MAX_PITCH = 85.0
# How often others' view of the spectator keeps up with the camera; each move redraws its viewport
PRESENCE_SECONDS = 0.25
STATUS_SECONDS = 3.0
# Pictures for the dashboard, as many a second as it films, which plays them as video. A frame
# comes a little early or late, so one this close to a frame after the last picture counts
VIEW_SECONDS = 0.9 / FRAMES_PER_SECOND
# One connection from the spectator's machine to the hub carries about 650 KB a second across the
# world, so the pictures are kept small enough for that many
VIEW_QUALITY = 50
# Each picture is written to disk on another thread, into the next of several files in turn, so
# the one being sent is never the one being written; it is sent once written whole
VIEW_FILES = 8
VIEW_POLL_SECONDS = 0.005
VIEW_WAIT_SECONDS = 1.0
# Each picture goes up the stream as a 4-byte length and then the JPEG
VIEW_LENGTH = struct.Struct(">I")
# A hub from before the stream gets a still now and then instead, and is asked again later
STILL_SECONDS = 4.0
STREAM_RECHECK_SECONDS = 60.0
REPORT_SECONDS = 1.0
LONG_POLL_TIMEOUT_SECONDS = 40
POST_TIMEOUT_SECONDS = 5.0
RETRY_SECONDS = 5.0
JPEG_START, JPEG_END = b"\xff\xd8", b"\xff\xd9"
CODE_HEADER = "X-CoPresence-Code"
# Frames to wait after startup so the level viewport exists before changing it
STARTUP_DELAY_TICKS = 120

_state = {"camera": None}


def is_spectator() -> bool:
    return "-COPRESENCESPECTATOR" in unreal.SystemLibrary.get_command_line().upper()


def _editor() -> unreal.UnrealEditorSubsystem:
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)


def nudge_pose(location: unreal.Vector, rotation: unreal.Rotator, move: list, turn: list) -> tuple:
    """Where a press of the dashboard's arrows takes the camera: forward, right and up along its
    heading like a drone, so looking down doesn't fly it into the floor, and turned by yaw and pitch."""
    heading = unreal.Rotator(roll=0.0, pitch=0.0, yaw=rotation.yaw)
    offset = (heading.get_forward_vector() * (move[0] * NUDGE_DISTANCE)
              + heading.get_right_vector() * (move[1] * NUDGE_DISTANCE)
              + unreal.Vector(0.0, 0.0, move[2] * NUDGE_DISTANCE))
    pitch = max(-MAX_PITCH, min(MAX_PITCH, rotation.pitch + turn[1] * NUDGE_DEGREES))
    return location + offset, unreal.Rotator(roll=0.0, pitch=pitch, yaw=rotation.yaw + turn[0] * NUDGE_DEGREES)


class SpectatorCamera(call_camera.CallCamera):
    """The running spectator camera: the call camera's picture and NDI output, filming whoever
    the dashboard picks instead of the agent itself, for as long as the editor runs."""

    def __init__(self, source_name: str):
        self.lock = threading.Lock()
        # Whom it follows and how, as the hub last said; "" follows nobody and flies freely
        self.wanted = {"target": "", "framing": FRAMINGS[0]}
        self.incoming = None
        self.boot = None
        self.nudge_cursor = 0
        self.glide = None
        self.pose = None
        self.target_name = ""
        self.target_actor = None
        self.target_kind = ""
        self.target_endpoint = None
        self.target_found_at = 0.0
        self.presence_at = 0.0
        self.status_at = time.monotonic()
        self.frames = 0
        self.view_at = 0.0
        folder = Path(unreal.Paths.project_saved_dir()) / "CoPresenceSpectator"
        folder.mkdir(parents=True, exist_ok=True)
        self.view_paths = [folder / f"view-{index}.jpg" for index in range(VIEW_FILES)]
        self.view_index = 0
        # Filled on the game thread, sent to the hub by the reporting threads: the status, and the
        # newest picture asked for, with when it was asked for
        self.outgoing = {"status": None, "view": (self.view_paths[0], 0), "code": hub_client.session_code()}
        self.hub_url = hub_client.hub_url()
        super().__init__(source_name, WIDTH, HEIGHT, FRAMES_PER_SECOND, "viewport")
        # The threads that talk to the hub run until stop(), and start before start() keeps this camera
        self.running = True
        threading.Thread(target=self._follow_hub, name="CoPresenceSpectatorHub", daemon=True).start()
        threading.Thread(target=self._report, name="CoPresenceSpectatorReport", daemon=True).start()
        threading.Thread(target=self._send_views, name="CoPresenceSpectatorViews", daemon=True).start()

    # Filming ----------------------------------------------------------------------------

    def _spawn(self) -> bool:
        location, rotation = self.pose or tracking.camera_info()
        made = call_camera._locally(lambda: call_camera.spawn_capture_camera(self.render_target, location, rotation,
                                                                             FIELD_OF_VIEW))
        if made is None:
            return False
        self.camera, self.capture = made
        self.rig.reset()
        self.last_step = None
        return True

    def keep(self) -> None:
        # The spectator films for as long as its editor runs; nothing needs to keep it going
        pass

    def stop(self) -> None:
        self.running = False
        super().stop()

    def status(self) -> dict:
        return {**super().status(), "target": self.wanted["target"], "framing": self.wanted["framing"],
                "found": self.target_actor is not None}

    def _step(self) -> None:
        now = time.monotonic()
        # Slate can tick several times a frame; one picture per frame at most
        if now - self.last_capture < 0.9 / self.frames_per_second:
            return
        step_seconds = min(now - (self.last_step or now), call_camera.MAX_STEP_SECONDS)
        self.last_step = now
        self._take_wanted()
        if not call_camera._is_alive(self.camera):
            # Multi-User reloading the level takes it away; it comes back once nothing else
            # has a transaction open
            if not self._spawn():
                return
        target = self._find_target(now)
        location, rotation = self._pose(target, step_seconds)
        self.pose = (location, rotation)
        self.camera.set_actor_location_and_rotation(location, rotation, False, False)
        self._hide(target, location)
        self.capture.capture_scene()
        self.last_capture = now
        self.frames += 1
        self._keep_presence(now, location, rotation)
        self._request_view(now)
        self._update_status(now, target)

    def _find_target(self, now: float):
        name = self.wanted["target"]
        if not name:
            self.target_actor = None
            return None
        recent = now - self.target_found_at < FIND_SECONDS
        if name == self.target_name and recent and (self.target_actor is None or call_camera._is_alive(self.target_actor)):
            return self.target_actor
        self.target_name = name
        self.target_actor = roster.presence_actors().get(name)
        self.target_kind = roster.kind_of(self.target_actor) if self.target_actor is not None else ""
        self.target_endpoint = roster.remote_clients().get(name)
        self.target_found_at = now
        return self.target_actor

    def _pose(self, target, step_seconds: float) -> tuple:
        if not self.wanted["target"]:
            return self._free_pose(step_seconds)
        if target is None:
            # Whoever it follows isn't in this level, or has left: it holds still until they're back
            return self.pose or tracking.camera_info()
        if self.wanted["framing"] == "eyes":
            return self._eyes_pose(target, step_seconds)
        here = self.pose or (self.camera.get_actor_location(), self.camera.get_actor_rotation())
        return self.rig.step(target, here[0], here[1], step_seconds)

    def _eyes_pose(self, target, step_seconds: float) -> tuple:
        # Where their camera or headset is, as Multi-User last heard; an agent's ghost can wait
        # beside its work while its camera looks elsewhere
        if self.target_endpoint is not None:
            transform = unreal.MultiUserClientStatics.get_multi_user_presence_transform(self.target_endpoint)
            head, head_rotation = transform.translation, transform.rotation.rotator()
        else:
            head, head_rotation = target.get_actor_location(), target.get_actor_rotation()
        level = unreal.Rotator(roll=0.0, pitch=head_rotation.pitch, yaw=head_rotation.yaw)
        if self.pose is None:
            return head, level
        location = unreal.MathLibrary.v_interp_to(self.pose[0], head, step_seconds, EYES_MOVE_SPEED)
        rotation = unreal.MathLibrary.r_interp_to(self.pose[1], level, step_seconds, EYES_TURN_SPEED)
        return location, rotation

    def _free_pose(self, step_seconds: float) -> tuple:
        if self.glide is None:
            return tracking.camera_info()
        glide = self.glide
        glide["elapsed"] += step_seconds
        amount = min(glide["elapsed"] / NUDGE_SECONDS, 1.0)
        eased = amount * amount * (3.0 - 2.0 * amount)
        (from_location, from_rotation), (to_location, to_rotation) = glide["from"], glide["to"]
        location = unreal.MathLibrary.v_lerp(from_location, to_location, eased)
        rotation = unreal.MathLibrary.r_lerp(from_rotation, to_rotation, eased, True)
        _editor().set_level_viewport_camera_info(location, rotation)
        if amount >= 1.0:
            self.glide = None
        return location, rotation

    def _hide(self, target, location: unreal.Vector) -> None:
        self.capture.clear_hidden_components()
        if target is None or self.wanted["framing"] != "eyes":
            return
        if self.target_kind == "vr":
            # The headset and the name, but not the hands, which show what they point at
            for name in ("presence_mesh_component", "presence_text_component"):
                try:
                    component = target.get_editor_property(name)
                except Exception:
                    component = None
                if component is not None:
                    self.capture.hide_component(component)
        elif (target.get_actor_location() - location).length() < EYES_HIDE_DISTANCE:
            self.capture.hide_actor_components(target, True)

    def _keep_presence(self, now: float, location: unreal.Vector, rotation: unreal.Rotator) -> None:
        # Flying freely, the viewport is the camera already
        if not self.wanted["target"] or now - self.presence_at < PRESENCE_SECONDS:
            return
        self.presence_at = now
        _editor().set_level_viewport_camera_info(location, rotation)

    # Where to look, from the hub ---------------------------------------------------------------

    def _take_wanted(self) -> None:
        with self.lock:
            incoming, self.incoming = self.incoming, None
        if incoming is None:
            return
        nudges = incoming.get("nudges") or []
        if self.boot is None:
            # Moves asked for before this spectator started aren't for it
            self.nudge_cursor = max((nudge["id"] for nudge in nudges), default=0)
        elif incoming.get("boot") != self.boot:
            # The hub restarted and counts its moves from the start again
            self.nudge_cursor = 0
        self.boot = incoming.get("boot")
        framing = incoming.get("framing") if incoming.get("framing") in FRAMINGS else FRAMINGS[0]
        wanted = {"target": str(incoming.get("target") or ""), "framing": framing}
        if wanted != self.wanted:
            if self.wanted["target"] and not wanted["target"] and self.pose is not None:
                # Flying freely starts from where the camera is
                _editor().set_level_viewport_camera_info(*self.pose)
            self.wanted = wanted
            # Following starts afresh from where the camera is, whoever it followed before
            self.rig.reset()
            self.target_name = ""
            self.target_found_at = 0.0
        for nudge in nudges:
            if nudge["id"] > self.nudge_cursor:
                self.nudge_cursor = nudge["id"]
                self._start_glide(nudge)

    def _start_glide(self, nudge: dict) -> None:
        here = tracking.camera_info()
        # Quick presses add up from where the last one was heading
        start = self.glide["to"] if self.glide is not None else here
        self.glide = {"from": here, "to": nudge_pose(start[0], start[1], nudge["move"], nudge["turn"]), "elapsed": 0.0}

    def _follow_hub(self) -> None:
        """Long-polls the hub for where to look, off the game thread."""
        version = -1
        while self.running:
            try:
                with urllib.request.urlopen(f"{self.hub_url}/api/spectator?version={version}",
                                            timeout=LONG_POLL_TIMEOUT_SECONDS) as response:
                    wanted = json.load(response)
                version = wanted["version"]
                with self.lock:
                    self.incoming = wanted
            except Exception:
                version = -1
                time.sleep(RETRY_SECONDS)

    # Telling the hub how it is doing -------------------------------------------------------------

    def _update_status(self, now: float, target) -> None:
        if now - self.status_at < STATUS_SECONDS:
            return
        world = _editor().get_editor_world()
        mode = "free" if not self.wanted["target"] else "follow" if target is not None else "lost"
        status = {"name": self.agent, "target": self.wanted["target"], "framing": self.wanted["framing"],
                  "found": target is not None, "mode": mode, "fps": round(self.frames / (now - self.status_at), 1),
                  "level": world.get_path_name().split(".")[0] if world is not None else ""}
        self.frames = 0
        self.status_at = now
        with self.lock:
            self.outgoing["status"] = status
            self.outgoing["code"] = hub_client.session_code()

    def _request_view(self, now: float) -> None:
        if now - self.view_at < VIEW_SECONDS:
            return
        self.view_at = now
        path = self.view_paths[self.view_index]
        self.view_index = (self.view_index + 1) % VIEW_FILES
        options = unreal.ImageWriteOptions()
        options.set_editor_property("format", unreal.DesiredImageFormat.JPG)
        options.set_editor_property("compression_quality", VIEW_QUALITY)
        asked_at = time.time_ns()
        unreal.ImageWriteBlueprintLibrary.export_to_disk(self.render_target, str(path), options)
        with self.lock:
            self.outgoing["view"] = (path, asked_at)

    def _report(self) -> None:
        """Sends the status to the hub, off the game thread."""
        while self.running:
            time.sleep(REPORT_SECONDS)
            with self.lock:
                status, self.outgoing["status"] = self.outgoing["status"], None
                code = self.outgoing["code"]
            if status is not None:
                self._post("/api/spectator/status", json.dumps({**status, "code": code}).encode(), "application/json", code)

    def _written_view(self, after: int) -> tuple[int, bytes] | None:
        """The newest picture asked for since after, once its file is written whole, or None."""
        deadline = time.monotonic() + VIEW_WAIT_SECONDS
        while self.running and time.monotonic() < deadline:
            with self.lock:
                path, asked_at = self.outgoing["view"]
            if asked_at > after:
                try:
                    if path.stat().st_mtime_ns >= asked_at:
                        picture = path.read_bytes()
                        if picture.startswith(JPEG_START) and picture.endswith(JPEG_END):
                            return asked_at, picture
                except OSError:
                    pass
            time.sleep(VIEW_POLL_SECONDS)
        return None

    def _send_views(self) -> None:
        """Sends the pictures to the hub as they are written, off the game thread: up one long
        request, or to a hub from before that, a still now and then."""
        stills_until = 0.0
        sent = 0
        while self.running:
            with self.lock:
                code = self.outgoing["code"]
            if time.monotonic() < stills_until:
                written = self._written_view(sent)
                if written is not None:
                    sent = written[0]
                    self._post("/api/spectator/view", written[1], "image/jpeg", code)
                    time.sleep(STILL_SECONDS)
                continue
            if not self._hub_takes_streams(code):
                stills_until = time.monotonic() + STREAM_RECHECK_SECONDS
                continue
            try:
                sent = self._stream_views(code, sent)
            except (OSError, http.client.HTTPException):
                # The hub restarted or can't be reached; the stream opens again
                time.sleep(RETRY_SECONDS)

    def _hub_takes_streams(self, code: str) -> bool:
        """Whether the hub takes the pictures up a stream: one that does refuses a request that
        isn't one, rather than not knowing the address."""
        request = urllib.request.Request(f"{self.hub_url}/api/spectator/views", data=b"", method="POST",
                                         headers={CODE_HEADER: code})
        try:
            urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS).close()
        except urllib.error.HTTPError as error:
            return error.code != 404
        except Exception:
            # Unreachable just now; the stream will say so itself
            return True
        return True

    def _stream_views(self, code: str, sent: int) -> int:
        """Keeps one request to the hub open and sends each picture up it as soon as it is
        written; returns when the request breaks, with the last picture sent."""
        address = urllib.parse.urlsplit(self.hub_url)
        connection = http.client.HTTPConnection(address.hostname, address.port or 80, timeout=POST_TIMEOUT_SECONDS)
        try:
            connection.putrequest("POST", "/api/spectator/views")
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Transfer-Encoding", "chunked")
            connection.putheader(CODE_HEADER, code)
            connection.endheaders()
            while self.running:
                written = self._written_view(sent)
                if written is None:
                    continue
                sent, picture = written
                body = VIEW_LENGTH.pack(len(picture)) + picture
                connection.send(f"{len(body):x}\r\n".encode() + body + b"\r\n")
            connection.send(b"0\r\n\r\n")
        finally:
            connection.close()
        return sent

    def _post(self, path: str, body: bytes, content_type: str, code: str) -> None:
        request = urllib.request.Request(f"{self.hub_url}{path}", data=body, method="POST",
                                         headers={"Content-Type": content_type, CODE_HEADER: code})
        try:
            urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS).close()
        except Exception:
            # The hub is down or unreachable; the next report goes again
            pass


def start() -> dict:
    camera = _state["camera"]
    if camera is None:
        camera = _state["camera"] = SpectatorCamera(f"CoPresence {tracking.agent_name()}")
    return camera.status()


def status() -> dict:
    camera = _state["camera"]
    return camera.status() if camera is not None else {"running": False}


def stop() -> dict:
    camera = _state["camera"]
    _state["camera"] = None
    if camera is not None:
        camera.stop()
    return {"running": False}


def install() -> None:
    """Starts the camera once the spectator's editor has a level, and keeps it running."""
    state = {"callback": None, "ticks": 0}

    def wait_for_level(delta_seconds: float) -> None:
        state["ticks"] += 1
        if state["ticks"] < STARTUP_DELAY_TICKS or _editor().get_editor_world() is None:
            return
        unreal.unregister_slate_post_tick_callback(state["callback"])
        # Multi-User only moves people's avatars while the level ticks, which an editor's level does
        # while a viewport draws in realtime; without it everyone in the picture stands still
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
        try:
            start()
        except Exception as error:
            unreal.log_error(f"CoPresence spectator: the camera didn't start: {error}")

    state["callback"] = unreal.register_slate_post_tick_callback(wait_for_level)
