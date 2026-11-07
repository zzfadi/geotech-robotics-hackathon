"""Local three-camera capture. Never moves the viewport or robot arms.
Start after CoPresence's enclosing transaction ends. Captures expire automatically.
"""
import base64
import json
from pathlib import Path
import time
import unreal
from copresence_call import call_camera
import yam_station

ROLES={'overhead':'MakerCamera_Overview','wrist_l':'MakerCamera_LeftWrist','wrist_r':'MakerCamera_RightWrist'}
_stream=None

class Observer:
    def __init__(self,directory,seconds=30,fps=5):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.cameras={};self.handle=None;self.frames=0;self.last=0
        self.deadline=time.monotonic()+seconds;self.interval=1/fps
        existing=yam_station.actors()
        for role,label in ROLES.items():
            camera=existing.get(label)
            if not isinstance(camera,unreal.CameraActor):raise ValueError('Missing camera '+label)
            self.cameras[role]={'source':camera}
        if call_camera._locally(self._create) is None:raise RuntimeError('Start observer after current transaction ends')
        self.handle=unreal.register_slate_post_tick_callback(self._tick)

    def _create(self):
        try:
            for role,item in self.cameras.items():
                target=unreal.new_object(unreal.TextureRenderTarget2D)
                target.set_editor_property('render_target_format',unreal.TextureRenderTargetFormat.RTF_RGBA8)
                target.set_editor_property('target_gamma',2.2)
                unreal.RenderingLibrary.resize_render_target2d(target,320,176)
                source=item['source'];component=source.get_component_by_class(unreal.CameraComponent)
                actor,capture=call_camera.spawn_capture_camera(target,source.get_actor_location(),source.get_actor_rotation(),component.field_of_view)
                actor.set_actor_label('DreamSim_LocalCapture_'+role)
                item.update(target=target,actor=actor,capture=capture)
            return True
        except Exception:
            for item in self.cameras.values():
                if 'actor' in item:unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(item['actor'])
            raise

    def _tick(self,delta):
        now=time.monotonic()
        if now>=self.deadline:self.stop();return
        if now-self.last<self.interval:return
        self.last=now
        try:
            frames={}
            for role,item in self.cameras.items():
                source=item['source']
                if not unreal.SystemLibrary.is_valid(source):raise RuntimeError('Source camera unloaded')
                item['actor'].set_actor_location_and_rotation(source.get_actor_location(),source.get_actor_rotation(),False,True)
                item['capture'].capture_scene()
                path=self.directory/(role+'.jpg')
                options=unreal.ImageWriteOptions()
                options.set_editor_property('format',unreal.DesiredImageFormat.JPG)
                options.set_editor_property('compression_quality',90)
                options.set_editor_property('async_',False)
                options.set_editor_property('overwrite_file',True)
                unreal.ImageWriteBlueprintLibrary.export_to_disk(item['target'],str(path),options)
                frames['video.'+role]=base64.b64encode(path.read_bytes()).decode('ascii')
            self.frames+=1
            packet={'sequence':self.frames,'captured_monotonic':now,'captured_unix':time.time(),'frames':frames}
            temporary=self.directory/'observation.tmp'
            temporary.write_text(json.dumps(packet));temporary.replace(self.directory/'observation.json')
        except Exception as error:
            (self.directory/'error.txt').write_text(str(error));self.stop()
            unreal.log_error('DreamSim observer: '+str(error))

    def stop(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle);self.handle=None
        call_camera._remove([item['actor'] for item in self.cameras.values() if 'actor' in item])
        (self.directory/'status.json').write_text(json.dumps({'running':False,'frames':self.frames}))

def start(directory,seconds=30,fps=5):
    global _stream
    if _stream and _stream.handle is not None:raise RuntimeError('Observer already running')
    _stream=Observer(directory,seconds,fps)
    return {'running':True,'directory':str(directory)}

def stop():
    if _stream:_stream.stop()
