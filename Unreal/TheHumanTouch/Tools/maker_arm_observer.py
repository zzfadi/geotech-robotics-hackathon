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
import maker_arm_feedback

ROLES={'overhead':'MakerCamera_Overview','wrist_l':'MakerCamera_LeftWrist','wrist_r':'MakerCamera_RightWrist'}
_stream=None

class Observer:
    def __init__(self,directory,seconds=30,fps=5,world=None,runtime_clone=False):
        self.world=world;self.runtime_clone=runtime_clone
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.cameras={};self.handle=None;self.frames=0;self.last=0
        self.rebind_attempts=0;self.next_rebind=0
        self.deadline=time.monotonic()+seconds;self.interval=1/fps
        existing=(yam_station.actors() if self.world is None else {a.get_actor_label():a for a in unreal.GameplayStatics.get_all_actors_of_class(self.world,unreal.CameraActor)})
        for role,label in ROLES.items():
            camera=existing.get(label)
            if not isinstance(camera,unreal.CameraActor):raise ValueError('Missing camera '+label)
            self.cameras[role]={'source':camera}
        if self.world is None:
            if call_camera._locally(self._create) is None:raise RuntimeError('Start observer after current transaction ends')
        else:self._create()
        self.handle=unreal.register_slate_post_tick_callback(self._tick)

    def _create(self):
        try:
            if self.world is None:
                editor_world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
                for old in unreal.GameplayStatics.get_all_actors_of_class(editor_world,unreal.SceneCapture2D):
                    if old.get_actor_label() in ['DreamSim_LocalCapture_'+role for role in ROLES]:
                        unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(old)
            for role,item in self.cameras.items():
                target=unreal.new_object(unreal.TextureRenderTarget2D)
                target.set_editor_property('render_target_format',unreal.TextureRenderTargetFormat.RTF_RGBA8)
                target.set_editor_property('target_gamma',2.2)
                unreal.RenderingLibrary.resize_render_target2d(target,320,176)
                source=item['source'];component=source.get_component_by_class(unreal.CameraComponent)
                if self.world is None and not self.runtime_clone:
                    actor,capture=call_camera.spawn_capture_camera(target,source.get_actor_location(),source.get_actor_rotation(),component.field_of_view)
                else:
                    if self.world is None:
                        from copresence import tracking
                        actor=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.SceneCapture2D,source.get_actor_location(),source.get_actor_rotation(),False)
                        actor.set_editor_property('tags',[unreal.Name(tracking.LOCAL_ONLY_TAG)])
                    else:
                        matches=[a for a in unreal.GameplayStatics.get_all_actors_of_class(self.world,unreal.SceneCapture2D) if a.get_actor_label()=='DreamSim_LocalCapture_'+role]
                        if len(matches)!=1:raise RuntimeError('Missing or duplicate cloned runtime capture '+role)
                        actor=matches[0]
                    capture=actor.get_component_by_class(unreal.SceneCaptureComponent2D)
                    capture.set_editor_property('texture_target',target)
                    capture.set_editor_property('capture_source',unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
                    capture.set_editor_property('capture_every_frame',False)
                    capture.set_editor_property('capture_on_movement',False)
                    capture.set_editor_property('always_persist_rendering_state',True)
                    capture.set_editor_property('fov_angle',component.field_of_view)
                actor.set_actor_label('DreamSim_LocalCapture_'+role)
                for primitive in actor.get_components_by_class(unreal.PrimitiveComponent):
                    primitive.set_visibility(False,False);primitive.set_hidden_in_game(True,False)
                item.update(target=target,actor=actor,capture=capture)
            return True
        except Exception:
            for item in self.cameras.values():
                if 'actor' in item and call_camera._is_alive(item['actor']):
                    if self.world is None:unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(item['actor'])
                    # Runtime actors belong to SIE; never destroy them from a
                    # capture callback that may overlap an editor transaction.
            raise

    def _rebind(self,now):
        if now<self.next_rebind:return False
        self.next_rebind=now+1
        self.rebind_attempts+=1
        if self.rebind_attempts>3:raise RuntimeError('Camera rebind failed after three attempts')
        existing=yam_station.actors()
        sources={role:existing.get(label) for role,label in ROLES.items()}
        if not all(isinstance(source,unreal.CameraActor) and call_camera._is_alive(source) for source in sources.values()):return False
        # Local captures are transaction-cancelled and never sent to Multi-User.
        def replace():
            for item in self.cameras.values():
                if call_camera._is_alive(item.get('actor')):
                    unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(item['actor'])
            self.cameras={role:{'source':source} for role,source in sources.items()}
            return self._create()
        if call_camera._locally(replace) is None:return False
        self.rebind_attempts=0
        return True

    def _tick(self,delta):
        now=time.monotonic()
        if now>=self.deadline:self.stop();return
        if now-self.last<self.interval:return
        self.last=now
        try:
            # SIE reinstantiates editor actor wrappers. Pause, then bind the current
            # editor cameras after simulation ends; never stream stale runtime refs.
            if self.world is None and unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:return
            if self.world is not None and not call_camera._is_alive(self.world):raise RuntimeError('Runtime camera world ended')
            if any(not call_camera._is_alive(item.get(key)) for item in self.cameras.values() for key in ('source','actor','capture')):
                if self.world is not None:raise RuntimeError('Runtime camera unloaded')
                if not self._rebind(now):return
            state=maker_arm_feedback.read(self.world)
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
            packet={'scope':'runtime' if self.world is not None else 'editor','sequence':self.frames,'captured_monotonic':now,'captured_unix':time.time(),'frames':frames,'state':state}
            temporary=self.directory/'observation.tmp'
            temporary.write_text(json.dumps(packet));temporary.replace(self.directory/'observation.json')
        except Exception as error:
            (self.directory/'error.txt').write_text(str(error));self.stop()
            unreal.log_error('DreamSim observer: '+str(error))

    def stop(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle);self.handle=None
        actors=[item['actor'] for item in self.cameras.values() if 'actor' in item]
        if self.world is None:call_camera._remove(actors)
        # Runtime capture actors are cleaned up by SIE teardown, after the
        # controller has closed its mirror transaction. Never delete here.
        (self.directory/'status.json').write_text(json.dumps({'running':False,'frames':self.frames}))

def start(directory,seconds=30,fps=5,world=None,runtime_clone=False):
    global _stream
    if _stream and _stream.handle is not None:raise RuntimeError('Observer already running')
    _stream=Observer(directory,seconds,fps,world,runtime_clone)
    return {'running':True,'directory':str(directory)}

def stop():
    if _stream:_stream.stop()
