"""Bounded, local idle previews: nine fleet cameras plus the separate Table 10 camera, one staggered capture per tick.

No policy calls, robot motion, or viewport changes. Start outside a transaction.
The worker stops this observer before SIE and can restart it after teardown.
"""
import json
from pathlib import Path
import time

import unreal
from copresence_call import call_camera

_preview = None


class Preview:
    def __init__(self, directory, seconds=3600):
        if not 0 < seconds <= 86400:
            raise ValueError('Preview duration must be within one day')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.deadline = time.monotonic() + seconds
        self.cameras = {}
        self.handle = None
        self.frames, self.frame_times = {}, {}
        self.index, self.last = 0, 0
        self.sequence = 0
        if call_camera._locally(self._create) is None:
            raise RuntimeError('Start fleet preview after the current transaction ends')
        self.handle = unreal.register_slate_post_tick_callback(self._tick)

    def _sources(self):
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        return {a.get_actor_label(): a for a in
                unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CameraActor)}

    def _create(self):
        sources = self._sources()
        names = ['MakerCamera_Overview'] + ['MakerFleet_T%02d__MakerCamera_Overview' % i for i in range(1, 9)]
        names.append('MakerInteraction_T09__MakerCamera_Overview')
        missing = [name for name in names if name not in sources]
        if missing:
            raise RuntimeError('Missing overhead cameras: ' + ', '.join(missing))
        for index, name in enumerate(names):
            target = unreal.new_object(unreal.TextureRenderTarget2D)
            target.set_editor_property('render_target_format', unreal.TextureRenderTargetFormat.RTF_RGBA8)
            target.set_editor_property('target_gamma', 2.2)
            unreal.RenderingLibrary.resize_render_target2d(target, 320, 176)
            source = sources[name]
            fov = source.get_component_by_class(unreal.CameraComponent).field_of_view
            actor, capture = call_camera.spawn_capture_camera(
                target, source.get_actor_location(), source.get_actor_rotation(), fov)
            # Independent persistent histories avoid reusing another room's
            # occlusion/exposure results without increasing the render rate.
            actor.set_actor_label('DreamFleet_LocalIdlePreview_T%02d' % index)
            for component in actor.get_components_by_class(unreal.PrimitiveComponent):
                component.set_visibility(False, False)
                component.set_hidden_in_game(True, False)
            self.cameras[index] = {'actor': actor, 'capture': capture, 'target': target,
                                   'source_name': name, 'key': 'InteractionT10' if index == 9 else 'T%02d' % index}
        return True

    def _write(self, running, error=None):
        data = {'scope': 'editor', 'running': running, 'sequence': self.sequence,
                'frames': self.frames, 'frame_times': self.frame_times, 'error': error}
        temporary = self.directory/'preview.tmp'
        temporary.write_text(json.dumps(data))
        temporary.replace(self.directory/'preview.json')

    def _tick(self, delta):
        now = time.monotonic()
        if now >= self.deadline:
            self.stop()
            return
        if now-self.last < .2:
            return
        self.last = now
        editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        if editor.get_game_world() is not None:
            return  # Never use an idle editor image as live simulation evidence.
        try:
            item = self.cameras[self.index]
            actor, capture, target = item['actor'], item['capture'], item['target']
            if not call_camera._is_alive(actor):
                raise RuntimeError('Idle capture unloaded; restart preview')
            key = item['key']
            name = item['source_name']
            source = self._sources().get(name)
            if source is None:
                raise RuntimeError('Missing camera for '+key)
            actor.set_actor_location_and_rotation(source.get_actor_location(), source.get_actor_rotation(), False, True)
            capture.set_editor_property('fov_angle', source.get_component_by_class(unreal.CameraComponent).field_of_view)
            capture.capture_scene()
            temporary = self.directory/(key+'.tmp.jpg')
            options = unreal.ImageWriteOptions()
            options.set_editor_property('format', unreal.DesiredImageFormat.JPG)
            options.set_editor_property('compression_quality', 80)
            options.set_editor_property('async_', False)
            options.set_editor_property('overwrite_file', True)
            unreal.ImageWriteBlueprintLibrary.export_to_disk(target, str(temporary), options)
            destination = self.directory/(key+'.jpg')
            temporary.replace(destination)
            self.frames[key] = str(destination)
            self.frame_times[key] = time.time()
            self.sequence += 1
            self._write(True)
            self.index = (self.index + 1) % len(self.cameras)
        except Exception as error:
            self.stop(str(error))
            unreal.log_error('Fleet preview: '+str(error))

    def stop(self, error=None):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        call_camera._remove([item['actor'] for item in self.cameras.values()])
        self._write(False, error)


def start(directory, seconds=3600):
    global _preview
    stop()
    _preview = Preview(directory, seconds)
    return {'running': True, 'directory': str(directory)}


def stop():
    if _preview is not None:
        _preview.stop()
    return {'running': False}
