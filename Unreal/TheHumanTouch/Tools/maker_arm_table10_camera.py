"""Live Table 10 overhead stream: one local capture of MakerInteraction_T09__MakerCamera_Overview.

Captures the editor world at up to FPS frames per second into <directory>/T10.jpg (+ T10.json
with a sequence and timestamp); DreamSim/table10_agent.py uploads each new frame to the web
page. Local, untransacted capture actor (same approach as maker_arm_fleet_preview); no viewport
changes, no robot motion. Pauses while a Play/Simulate world is running.

    import maker_arm_table10_camera as cam; cam.start(directory); cam.status(); cam.stop()
"""
import json
import time
from pathlib import Path

import unreal
from copresence_call import call_camera

SOURCE = 'MakerInteraction_T09__MakerCamera_Overview'
WIDTH, HEIGHT = 1280, 704  # same 20:11 aspect as before; sharper on the web page's bigger and full-screen views
FPS = 10.
QUALITY = 80

_camera = None


class Camera:
    def __init__(self, directory, seconds=4*3600):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.deadline = time.monotonic()+seconds
        self.sequence = 0
        self.last = 0.
        self.error = None
        self.actor = self.capture = self.target = None
        self.handle = None
        if call_camera._locally(self._create) is None:
            raise RuntimeError('Start the Table 10 camera after the current transaction ends')
        self.handle = unreal.register_slate_post_tick_callback(self._tick)

    def _source(self):
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CameraActor):
            if actor.get_actor_label() == SOURCE:
                return actor
        raise RuntimeError('Missing ' + SOURCE)

    def _create(self):
        source = self._source()
        self.target = unreal.new_object(unreal.TextureRenderTarget2D)
        self.target.set_editor_property('render_target_format', unreal.TextureRenderTargetFormat.RTF_RGBA8)
        self.target.set_editor_property('target_gamma', 2.2)
        unreal.RenderingLibrary.resize_render_target2d(self.target, WIDTH, HEIGHT)
        fov = source.get_component_by_class(unreal.CameraComponent).field_of_view
        self.actor, self.capture = call_camera.spawn_capture_camera(
            self.target, source.get_actor_location(), source.get_actor_rotation(), fov)
        self.actor.set_actor_label('Table10_LiveStream_Capture')
        for component in self.actor.get_components_by_class(unreal.PrimitiveComponent):
            component.set_visibility(False, False)
            component.set_hidden_in_game(True, False)
        return True

    def _tick(self, delta):
        now = time.monotonic()
        if now >= self.deadline:
            return self.stop()
        if now-self.last < 1./FPS:
            return
        self.last = now
        if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
            return  # an editor-world image would not show the running simulation
        try:
            if not call_camera._is_alive(self.actor):
                raise RuntimeError('Table 10 capture unloaded; restart the stream')
            source = self._source()
            self.actor.set_actor_location_and_rotation(source.get_actor_location(), source.get_actor_rotation(), False, True)
            self.capture.capture_scene()
            options = unreal.ImageWriteOptions()
            options.set_editor_property('format', unreal.DesiredImageFormat.JPG)
            options.set_editor_property('compression_quality', QUALITY)
            options.set_editor_property('async_', False)
            options.set_editor_property('overwrite_file', True)
            temporary = self.directory/'T10.tmp.jpg'
            unreal.ImageWriteBlueprintLibrary.export_to_disk(self.target, str(temporary), options)
            temporary.replace(self.directory/'T10.jpg')
            self.sequence += 1
            info = self.directory/'T10.tmp.json'
            info.write_text(json.dumps({'sequence': self.sequence, 'captured_unix': time.time(), 'running': True}))
            info.replace(self.directory/'T10.json')
            self.error = None
        except Exception as error:
            self.error = str(error)
            self.stop(self.error)
            unreal.log_error('Table 10 camera: ' + self.error)

    def stop(self, error=None):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        if self.actor is not None:
            call_camera._remove([self.actor])
            self.actor = None
        info = self.directory/'T10.tmp.json'
        info.write_text(json.dumps({'sequence': self.sequence, 'captured_unix': time.time(), 'running': False, 'error': error}))
        info.replace(self.directory/'T10.json')

    def status(self):
        return {'running': self.handle is not None, 'sequence': self.sequence, 'error': self.error,
                'directory': str(self.directory), 'fps': FPS, 'size': [WIDTH, HEIGHT]}


def _stop_strays():
    """Stop every running Camera from this module, including ones orphaned by an importlib.reload
    (a reload resets the module handle, and the old capture would keep ticking and writing frames)."""
    import gc
    for o in gc.get_objects():
        try:
            if type(o).__name__ == 'Camera' and type(o).__module__ == __name__ and getattr(o, 'handle', None) is not None:
                o.stop()
        except Exception:
            pass


def start(directory, seconds=4*3600):
    global _camera
    stop()
    _stop_strays()
    _camera = Camera(directory, seconds)
    return _camera.status()


def stop():
    global _camera
    if _camera is not None:
        _camera.stop()
        _camera = None
    return {'running': False}


def status():
    return _camera.status() if _camera is not None else {'running': False}
