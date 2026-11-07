"""The call camera: films the agent the way people see it when they follow it in the Agents
tab, from behind and above its ghost, and sends the picture out as an NDI source. The call
bot (Tools/copresence_call.py) turns that into the agent's camera in a video call, and
anything else that takes NDI, like OBS or GeoVerse's stage, can show it too.

Runs in the agent's own editor. Multi-User never shows an editor its own avatar, so the
camera brings a copy of the agent's ghost that only scene captures see, which keeps the
agent's own `look` screenshots as they were. The copy and the camera are spawned inside a
cancelled transaction, so they never reach the other users or the agent's undo history.
The call bot keeps the camera running; without word from it for a while, it stops.
"""

import time
import json
from pathlib import Path

import unreal

from copresence import follow_camera, tracking, ui_widgets

GHOST_CLASS_PATH = "/CoPresence/BP_AgentGhost.BP_AgentGhost_C"
GHOST_NAME = "CoPresenceCallGhost"
CAMERA_LABEL = "CoPresence Call Camera"
# Without a keep-alive from the call bot for this long, the camera stops
LEASE_SECONDS = 20.0
FIELD_OF_VIEW = 80.0
# A hitch longer than this moves the camera only this much, rather than jumping
MAX_STEP_SECONDS = 0.25
RETRY_SECONDS = 1.0

_state = {"camera": None}


def _actors():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _is_alive(actor) -> bool:
    try:
        return actor is not None and unreal.SystemLibrary.is_valid(actor)
    except Exception:
        return False


def _is_agent_ghost(actor) -> bool:
    """Recognize the shared ghost blueprint, including derived presence classes. A loaded
    blueprint class is an Unreal class, not a Python type, so Unreal checks the kinship."""
    ghost_class = unreal.load_class(None, GHOST_CLASS_PATH)
    if ghost_class is None:
        return False
    try:
        return unreal.MathLibrary.class_is_child_of(actor.get_class(), ghost_class)
    except Exception:
        return False


def _locally(make):
    """Runs make() inside a transaction that is then cancelled, so whatever it spawns or
    changes stays in this editor: out of Multi-User and out of the agent's undo history.
    While another transaction is open, such as an animation's, make() would join it, so it
    doesn't run and this returns None."""
    library = unreal.SystemLibrary
    undo_count = tracking.undo_count()
    index = library.begin_transaction("CoPresenceCall", unreal.Text("Call camera"), None)
    if index != 0:
        if index > 0:
            library.end_transaction()
        return None
    selection = _actors().get_selected_level_actors()
    try:
        result = make()
        # Spawning selects the new actor; the agent keeps its own selection
        _actors().set_selected_level_actors(selection)
    finally:
        library.cancel_transaction(index)
    if tracking.undo_count() != undo_count:
        unreal.log_warning("CoPresence call camera: setting up changed the agent's undo history")
    return result


def _remove(actors: list) -> None:
    """Removes local actors, waiting until no transaction is open."""
    actors = [actor for actor in actors if _is_alive(actor)]
    if not actors:
        return
    if _locally(lambda: [_actors().destroy_actor(actor) for actor in actors]) is None:
        ui_widgets.after(RETRY_SECONDS, lambda: _remove(actors))


def _free_name(outer: unreal.Object, base: str) -> str:
    """The first of base_0, base_1 and so on that nothing in outer is called yet."""
    number = 0
    while unreal.find_object(outer, f"{base}_{number}") is not None:
        number += 1
    return f"{base}_{number}"


def _component(actor: unreal.Actor, component_class, name: str):
    return next((component for component in actor.get_components_by_class(component_class)
                 if component.get_name() == name), None)


def spawn_capture_camera(render_target: unreal.TextureRenderTarget2D, location: unreal.Vector,
                         rotation: unreal.Rotator, field_of_view: float) -> tuple:
    """Spawns a scene capture that films into render_target when asked, kept to this editor by
    its tag. Call it inside _locally, so it never reaches the session."""
    camera = _actors().spawn_actor_from_class(unreal.SceneCapture2D, location, rotation, True)
    camera.set_actor_label(CAMERA_LABEL)
    camera.set_editor_property("tags", [unreal.Name(tracking.LOCAL_ONLY_TAG)])
    capture = camera.get_editor_property("capture_component2d")
    capture.set_editor_property("texture_target", render_target)
    # Tonemapped like the editor's viewport, with the bloom the name tags glow with
    capture.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
    capture.set_editor_property("capture_every_frame", False)
    capture.set_editor_property("capture_on_movement", False)
    # Keeps anti-aliasing and exposure history between captures, so the picture doesn't shimmer
    capture.set_editor_property("always_persist_rendering_state", True)
    capture.set_editor_property("fov_angle", field_of_view)
    if uses_real_time_sky_capture():
        skip_sky_lighting(capture)
    return camera, capture


def uses_real_time_sky_capture() -> bool:
    """Whether the level has a sky light that recaptures the sky in real time."""
    for actor in _actors().get_all_level_actors():
        if isinstance(actor, unreal.SkyLight):
            light = actor.get_editor_property("light_component")
            if light is not None and light.get_editor_property("real_time_capture"):
                return True
    return False


def skip_sky_lighting(capture) -> None:
    """Leaves the sky light out of this capture's picture.

    A real-time sky light inside a scene capture that keeps its rendering state
    (always_persist_rendering_state) makes Unreal 5.8 allocate memory on every manual
    capture without ever freeing it. On Tomorrowland the agent editor grew by 2-4 GB a
    minute (well over 100 GB within the hour) until it stalled and the call video went to
    0 fps. Turning that state off avoids the leak but leaves the picture without eye
    adaptation, which comes out blown-out white, so the capture keeps its state and only
    leaves out sky lighting. The picture loses a little ambient light; the editor's own
    viewport is unaffected.
    """
    setting = unreal.EngineShowFlagsSetting()
    setting.set_editor_property("show_flag_name", "SkyLighting")
    setting.set_editor_property("enabled", False)
    settings = [existing for existing in capture.get_editor_property("show_flag_settings")
                if str(existing.get_editor_property("show_flag_name")) != "SkyLighting"]
    capture.set_editor_property("show_flag_settings", settings + [setting])


class CallCamera:
    """One running camera: its render target, its NDI output, and the local ghost and
    scene capture it films with."""

    def __init__(self, source_name: str, width: int, height: int, frames_per_second: int, view_mode: str = "ghost"):
        if view_mode not in ("ghost", "viewport", "editor_viewport"):
            raise ValueError("view_mode must be ghost, viewport, or editor_viewport")
        self.view_mode = view_mode
        self.source_name = source_name
        self.size = (width, height)
        self.frames_per_second = frames_per_second
        self.agent = tracking.agent_name()
        self.rig = follow_camera.FollowRig()
        self.ghost = None
        self.camera = None
        self.capture = None
        self.lease_until = time.monotonic() + LEASE_SECONDS
        self.last_capture = 0.0
        self.last_step = None
        self.last_warning = ""
        if view_mode == "editor_viewport":
            unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
        self.render_target = None if view_mode == "editor_viewport" else self._make_render_target()
        self.output, self.media_capture = self._start_output()
        self.callback = unreal.register_slate_post_tick_callback(self._tick)

    def _make_render_target(self) -> unreal.TextureRenderTarget2D:
        # Not owned by the level, so Multi-User reloading the level doesn't take it away.
        # NDI/JPEG forwards these bytes without a linear-to-display conversion.
        # Explicit display gamma makes Final Color LDR match the editor instead
        # of sending linear-looking colors with crushed shadows into the call.
        target = unreal.new_object(unreal.TextureRenderTarget2D)
        target.set_editor_property("render_target_format", unreal.TextureRenderTargetFormat.RTF_RGBA8)
        target.set_editor_property("target_gamma", 2.2)
        target.set_editor_property("clear_color", unreal.LinearColor(0.0, 0.0, 0.0, 1.0))
        unreal.RenderingLibrary.resize_render_target2d(target, *self.size)
        return target

    def _start_output(self) -> tuple:
        output = unreal.new_object(unreal.NDIMediaOutput)
        output.set_editor_property("source_name", self.source_name)
        output.set_editor_property("frame_rate", unreal.FrameRate(self.frames_per_second, 1))
        # NDI only sends 8-bit BGRA, which isn't the editor's own frame format
        output.set_editor_property("override_pixel_format", True)
        output.set_editor_property("desired_pixel_format", unreal.NDIMediaOutputPixelFormat.B8G8R8A8)
        output.set_editor_property("override_desired_size", True)
        output.set_editor_property("desired_size", unreal.IntPoint(*self.size))
        capture = output.create_media_capture()
        options = unreal.MediaCaptureOptions()
        # A late frame is dropped rather than stalling the editor
        options.set_editor_property("overrun_action", unreal.MediaCaptureOverrunAction.SKIP)
        options.set_editor_property("skip_frame_when_running_expensive_tasks", False)
        if self.view_mode == "editor_viewport":
            options.set_editor_property("resize_method", unreal.MediaCaptureResizeMethod.RESIZE_SOURCE)
            options.set_editor_property("disable_editor_sprites_during_capture", True)
            started = capture.capture_active_scene_viewport(options)
        else:
            started = capture.capture_texture_render_target2d(self.render_target, options)
        if not started:
            raise RuntimeError("Unreal's NDI output didn't take the call camera's picture")
        return output, capture

    def _spawn(self) -> bool:
        """Brings in the local ghost and the camera behind it."""
        location, rotation = tracking.camera_info()
        ghost_class = unreal.load_class(None, GHOST_CLASS_PATH) if self.view_mode == "ghost" else None
        heading = unreal.Rotator(roll=0.0, pitch=0.0, yaw=rotation.yaw)
        behind = (location - heading.get_forward_vector() * follow_camera.FOLLOW_DISTANCE
                  + unreal.Vector(0.0, 0.0, follow_camera.FOLLOW_HEIGHT))

        def make() -> tuple:
            ghost = None
            if self.view_mode == "ghost":
                ghost = _actors().spawn_actor_from_class(ghost_class, location, rotation, True)
                # Multi-User names the avatars it brings in BP_AgentGhost_C_0, _1 and so on, and
                # Unreal crashes when one of those names is already taken, so the copy gets its own
                ghost.rename(_free_name(ghost.get_outer(), GHOST_NAME))
                # ghost_labels names a ghost after the stock label Multi-User would have filled in
                label = _component(ghost, unreal.TextRenderComponent, "Name")
                if label is not None:
                    label.set_editor_property("text", unreal.Text(self.agent))
                for component in ghost.get_components_by_class(unreal.PrimitiveComponent):
                    component.set_visible_in_scene_capture_only(True)

            camera, capture = spawn_capture_camera(self.render_target, behind, heading, FIELD_OF_VIEW)
            # Project capture exposure must survive reattachment; editor viewport
            # exposure is independent of SceneCapture2D.
            settings_path = Path(unreal.Paths.project_dir()) / "Config" / "CoPresenceCall.json"
            if self.view_mode == "viewport" and settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                world_path = camera.get_world().get_path_name().split(".")[0]
                # A null map override inherits the level's exposure. Do not replace
                # a scene artist's exposure with the portrait-camera fallback.
                bias = settings.get("map_exposure_bias", {}).get(world_path, settings.get("viewport_exposure_bias"))
                if bias is not None:
                    bias = float(bias)
                    if not -4 <= bias <= 4:
                        raise ValueError("Capture exposure bias must be between -4 and 4")
                post = capture.get_editor_property("post_process_settings")
                post.set_editor_property("override_auto_exposure_bias", bias is not None)
                if bias is not None:
                    post.set_editor_property("auto_exposure_bias", bias)
                capture.set_editor_property("post_process_settings", post)
                capture.set_editor_property("post_process_blend_weight", 1.0)
            return ghost, camera, capture

        made = _locally(make)
        if made is None:
            return False
        self.ghost, self.camera, self.capture = made
        self.rig.reset()
        self.last_step = None
        return True

    def keep(self) -> None:
        self.lease_until = time.monotonic() + LEASE_SECONDS

    def status(self) -> dict:
        return {"running": True, "source": self.source_name, "size": list(self.size),
                "frames_per_second": self.frames_per_second, "output": str(self.media_capture.get_state()),
                "view_mode": self.view_mode,
                "filming": (self.media_capture.get_state() == unreal.MediaCaptureState.CAPTURING
                            if self.view_mode == "editor_viewport" else
                            _is_alive(self.camera) and (self.view_mode == "viewport" or _is_alive(self.ghost)))}

    def _tick(self, delta_seconds: float) -> None:
        try:
            self._step()
        except Exception as error:
            if str(error) != self.last_warning:
                self.last_warning = str(error)
                unreal.log_warning(f"CoPresence call camera: {error}")

    def _step(self) -> None:
        now = time.monotonic()
        if now > self.lease_until:
            stop()
            return
        # Native viewport output already captures the real rendered editor frame.
        # No second scene render: preserve its lighting and avoid doubling GPU work.
        if self.view_mode == "editor_viewport":
            return
        # Slate can tick several times a frame; one picture per frame at most
        if now - self.last_capture < 0.9 / self.frames_per_second:
            return
        if not (_is_alive(self.camera) and (self.view_mode == "viewport" or _is_alive(self.ghost))):
            # Multi-User reloading the level takes them away; they come back once nothing
            # else has a transaction open
            if not self._spawn():
                return
        if self.view_mode == "ghost":
            # This camera already supplies its own self ghost. The editor's
            # local presenter would show the same agent a second time.
            from copresence import local_presence
            if _is_alive(local_presence.actor):
                self.capture.hide_actor_components(local_presence.actor, True)
        if self.view_mode == "viewport":
            # Keep ordinary editor pointers out of the portrait, but show joined
            # CoPresence ghosts so collaborators remain visible in the call.
            presence_class = getattr(unreal, "ConcertClientPresenceActor", None)
            if presence_class is not None:
                # Presence is transient and can be omitted by get_all_level_actors.
                # Check immediately before every capture so animation/MU recreation
                # cannot expose a fresh pointer for even one captured frame.
                world = self.capture.get_world()
                for actor in unreal.ObjectIterator(presence_class):
                    if actor.get_world() == world and not _is_agent_ghost(actor):
                        self.capture.hide_actor_components(actor, True)
        location, rotation = tracking.camera_info()
        if self.view_mode == "viewport":
            camera_location, camera_rotation = location, rotation
        else:
            self.ghost.set_actor_location_and_rotation(location, rotation, False, False)
            step_seconds = min(now - (self.last_step or now), MAX_STEP_SECONDS)
            self.last_step = now
            camera_location, camera_rotation = self.rig.step(
                self.ghost, self.camera.get_actor_location(), self.camera.get_actor_rotation(), step_seconds)
        self.camera.set_actor_location_and_rotation(camera_location, camera_rotation, False, False)
        self.capture.capture_scene()
        self.last_capture = now

    def stop(self) -> None:
        unreal.unregister_slate_post_tick_callback(self.callback)
        self.media_capture.stop_capture(False)
        _remove([self.ghost, self.camera])


def start(source_name: str, width: int, height: int, frames_per_second: int, view_mode: str = "ghost") -> dict:
    if view_mode not in ("ghost", "viewport", "editor_viewport"):
        raise ValueError("view_mode must be ghost, viewport, or editor_viewport")
    camera = _state["camera"]
    if camera is not None:
        if (camera.source_name, camera.size, camera.frames_per_second, camera.view_mode) == (source_name, (width, height), frames_per_second, view_mode):
            camera.keep()
            return camera.status()
        stop()
    _state["camera"] = CallCamera(source_name, width, height, frames_per_second, view_mode)
    return _state["camera"].status()


def keep() -> dict:
    camera = _state["camera"]
    if camera is None:
        return {"running": False}
    camera.keep()
    return camera.status()


def stop() -> dict:
    camera = _state["camera"]
    _state["camera"] = None
    if camera is not None:
        camera.stop()
    return {"running": False}
