"""Single-authority SIE controller. Model joints drive kinematic CAD links;
Chaos moves free props. Coarse convex jaws are an explicit diagnostic approximation.
"""
import json,time
from pathlib import Path
import unreal
import maker_arm_feedback,maker_arm_observer
from maker_arm_actions import retime
from maker_arm_contact_stop import contact_gap,actor_boxes
from maker_arm_lift_evidence import update as update_lift_evidence
from maker_arm_model import mounted_frames,ue_pose
from maker_arm_station import BASES,MOUNT_YAW,VISUAL_LINKS
from copresence import animation,tracking
from copresence_call import call_camera

_session=None

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value));tmp.replace(path)

class Session:
    def __init__(self,seed_path,status_path,camera_directory,mirror=True):
        self.editor=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.level=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if self.editor.get_game_world() is not None or animation.is_running():raise RuntimeError('Simulation or shared animation already active')
        self.editor_world=self.editor.get_editor_world()
        candidate=json.loads(Path(seed_path).read_text())
        self.initial={key:candidate[key] for key in ('q_left','q_right','gap_left_m','gap_right_m')}
        maker_arm_feedback.seed(self.initial)
        self.state=maker_arm_feedback.read();self.status=Path(status_path);self.cameras=str(camera_directory)
        self.want_mirror=mirror;self.mirror=None;self.world=None;self.proxies={};self.props={};self.evidence={};self.evidence_timers={}
        self.phase='starting';self.error=None;self.started=time.monotonic();self.last_motion=None;self.commit=False
        self.motion=None;self.callback=None;self.local_proxies=[];self.report()
        maker_arm_observer.stop()
        # Python runtime spawning is unavailable; transient local editor actors
        # are cloned by SIE. Never shared to Concert or persisted as scene assets.
        def proxies():
            editor_actors={a.get_actor_label():a for a in unreal.GameplayStatics.get_all_actors_of_class(self.editor.get_editor_world(),unreal.StaticMeshActor)}
            for old in list(editor_actors.values()):
                if old.get_actor_label().startswith('DreamSim_RuntimeProxy_'):unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(old)
            for side in BASES:
                for link in ('gripper_left','gripper_right'):
                    label='MakerArm_'+side+'_'+link;source=editor_actors[label]
                    mesh=unreal.load_asset('/Game/_Agents/Denys_Mac_Codex/MakerArm/Physics/SM_MakerArm_Collision_'+link)
                    if mesh is None:raise RuntimeError('Missing diagnostic collision mesh')
                    proxy=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.StaticMeshActor,source.get_actor_location(),source.get_actor_rotation(),False)
                    self.local_proxies.append(proxy)
                    proxy.set_actor_label('DreamSim_RuntimeProxy_'+label)
                    proxy.set_editor_property('tags',[unreal.Name(tracking.LOCAL_ONLY_TAG)])
                    pc=proxy.static_mesh_component;pc.set_mobility(unreal.ComponentMobility.MOVABLE);pc.set_static_mesh(mesh)
                    pc.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
                    pc.set_visibility(False,False);proxy.set_actor_hidden_in_game(True)
            return True
        if call_camera._locally(proxies) is None:raise RuntimeError('Start runtime outside enclosing transaction')
        maker_arm_observer.start(self.cameras,seconds=180,runtime_clone=True)
        self.callback=unreal.register_slate_post_tick_callback(self.tick)
        self.level.editor_play_simulate()

    def report(self):
        write(self.status,{'phase':self.phase,'error':self.error,'scope':'runtime physics (approximate convex jaws)',
                          'state':self.state,'props':self.evidence,'task_success_verified':False})

    def prepare(self,world):
        self.world=world
        items=unreal.GameplayStatics.get_all_actors_of_class(world,unreal.StaticMeshActor)
        self.actors={a.get_actor_label():a for a in items}
        labels=['MakerArm_'+side+'_'+link for side in BASES for link in VISUAL_LINKS]
        if any(sum(a.get_actor_label()==label for a in items)!=1 for label in labels):raise RuntimeError('Missing or duplicate runtime link')
        maker_arm_feedback.seed(self.state,world)
        self.material=unreal.new_object(unreal.PhysicalMaterial)
        self.material.set_editor_property('friction',.8);self.material.set_editor_property('restitution',0.)
        for label in labels:
            actor=self.actors[label];c=actor.static_mesh_component
            c.set_mobility(unreal.ComponentMobility.MOVABLE);c.set_simulate_physics(False)
            c.set_collision_profile_name('BlockAllDynamic')
            c.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
            if label.endswith(('gripper_left','gripper_right')):
                # Keep official visual meshes; separate hidden runtime-only collision body.
                proxy=self.actors.get('DreamSim_RuntimeProxy_'+label)
                if proxy is None:raise RuntimeError('Missing cloned runtime collision proxy')
                pc=proxy.static_mesh_component;pc.set_mobility(unreal.ComponentMobility.MOVABLE)
                pc.set_collision_profile_name('BlockAllDynamic');pc.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
                pc.set_phys_material_override(self.material)
                c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION);self.proxies[label]=proxy
        for label,actor in self.actors.items():
            if not label.startswith('MakerStation_Approximate_Block_'):continue
            c=actor.static_mesh_component;c.set_mobility(unreal.ComponentMobility.MOVABLE)
            c.set_collision_profile_name('PhysicsActor');c.set_phys_material_override(self.material)
            c.set_mass_override_in_kg('',.05,True);c.set_enable_gravity(True);c.set_simulate_physics(True)
            if not c.is_simulating_physics():raise RuntimeError('Prop did not enter Chaos simulation')
            self.props[label]=actor;self.evidence_timers[label]={};z=actor.get_actor_location().z
            self.evidence[label]={'initial_z_cm':z,'max_z_cm':z,'final_z_cm':z,'simulated':True}
        maker_arm_observer.stop()
        maker_arm_observer.start(self.cameras,seconds=180,world=world)
        if self.want_mirror:
            from maker_arm_runtime_mirror import RuntimeMirror
            self.mirror=RuntimeMirror(world,labels+list(self.props),max_seconds=180,hz=10,editor_world=self.editor_world)
        self.phase='ready';self.report()

    def launch(self,report,output,hz=20):
        if self.phase!='ready' or self.motion is not None:raise RuntimeError('Runtime not ready or trajectory active')
        data=json.loads(Path(report).read_text())
        maker_arm_feedback.verify(data['state'],self.world)
        chunks,timing=retime(data['action'],data['state'],hz,execution_rows=data.get('execution_rows'))
        self.motion={'chunks':chunks,'timing':timing,'output':str(output),'start':time.monotonic(),'index':-1}
        self.motion_report(False)

    def motion_report(self,done,error=None):
        m=self.motion
        if not m:return
        write(m['output'],{'done':done,'error':error,'index':m['index'],'rows':len(m['chunks']['left']),
                           'timing':m['timing'],'scope':'runtime physics (approximate convex jaws)',**self.state})

    def tick(self,delta):
        try:
            now=time.monotonic();world=self.editor.get_game_world()
            if self.phase=='starting':
                if world is None:
                    if now-self.started>15:raise TimeoutError('SIE startup timed out')
                    return
                self.prepare(world);return
            if self.phase=='ending':
                if world is not None:
                    if now-self.ending_started>15:raise TimeoutError('SIE exit timed out')
                    return
                call_camera._remove([a for a in unreal.GameplayStatics.get_all_actors_of_class(self.editor.get_editor_world(),unreal.StaticMeshActor) if a.get_actor_label().startswith('DreamSim_RuntimeProxy_')])
                maker_arm_feedback.seed(self.state if self.commit else self.initial)
                maker_arm_observer.start(self.cameras,seconds=86400)
                self.phase='stopped';self.report();self.close_callback();return
            if self.phase!='ready':return
            if world is None or world!=self.world:raise RuntimeError('Runtime authority world changed')
            if self.mirror and self.mirror.closed:raise RuntimeError(self.mirror.error or 'Runtime mirror closed')
            if now-self.started>170:raise TimeoutError('Runtime session duration limit')
            if (self.status.parent/'stop').exists():raise RuntimeError('Stopped by user')
            m=self.motion
            if m:
                index=min(int((now-m['start'])*m['timing']['action_hz']),len(m['chunks']['left'])-1)
                if index!=m['index']:
                    for side in BASES:
                        row=m['chunks'][side][index]
                        frames=mounted_frames(row[:6],row[6]*.104825,yaw=MOUNT_YAW[side])
                        gap,contacts=contact_gap(row[6]*.104825,self.state['gap_'+side+'_m'],actor_boxes(self.props,frames['link_007'],BASES[side]))
                        self.state['q_'+side]=row[:6];self.state['gap_'+side+'_m']=gap
                        self.state['contact_stop_'+side]=contacts
                        for link,t in mounted_frames(row[:6],gap,yaw=MOUNT_YAW[side]).items():
                            if link not in VISUAL_LINKS:continue
                            label='MakerArm_'+side+'_'+link;p,q=ue_pose(t);p=[p[i]+BASES[side][i] for i in range(3)]
                            for actor in [self.actors[label]]+([self.proxies[label]] if label in self.proxies else []):
                                actor.set_actor_location_and_rotation(unreal.Vector(*p),unreal.Quat(*q).rotator(),False,False)
                    m['index']=index;maker_arm_feedback.applied(self.state);maker_arm_feedback.read(self.world)
                    self.motion_report(False)
                    if index==len(m['chunks']['left'])-1:self.motion_report(True);self.motion=None
            for label,actor in self.props.items():
                actor.static_mesh_component.wake_all_rigid_bodies()
                p=actor.get_actor_location();v=actor.static_mesh_component.get_physics_linear_velocity()
                update_lift_evidence(self.evidence[label],self.evidence_timers[label],[p.x,p.y,p.z],[v.x,v.y,v.z],now)
            if self.mirror:self.mirror.tick()
            self.report()
        except Exception as error:
            self.error=str(error)
            if self.phase=='ending':self.phase='error';self.report();self.close_callback()
            else:self.stop(False)

    def stop(self,commit=True):
        if self.phase in ('stopped','ending','error'):return
        self.commit=bool(commit and not self.error)
        if self.motion:self.motion_report(True,self.error or 'Runtime ended');self.motion=None
        # Close the editor mirror transaction BEFORE any runtime teardown.
        # Otherwise Concert may serialize PIE capture component deletions.
        if self.mirror:
            try:self.mirror.finish(commit=self.commit)
            except Exception as error:self.error=str(error);self.commit=False
            finally:self.mirror=None
        maker_arm_observer.stop()
        self.phase='ending';self.ending_started=time.monotonic();self.report()
        self.level.editor_request_end_play()

    def close_callback(self):
        if self.callback is not None:unreal.unregister_slate_post_tick_callback(self.callback);self.callback=None

def start(seed_path,status_path,camera_directory,mirror=True):
    global _session
    if _session and _session.callback is not None:raise RuntimeError('Runtime controller already active')
    candidate=Session.__new__(Session)
    try:
        candidate.__init__(seed_path,status_path,camera_directory,mirror)
        _session=candidate
    except Exception as error:
        if getattr(candidate,'callback',None) is not None:candidate.close_callback()
        call_camera._remove(getattr(candidate,'local_proxies',[]))
        if getattr(candidate,'initial',None) is not None and candidate.editor.get_game_world() is None:
            try:
                maker_arm_observer.stop();maker_arm_feedback.seed(candidate.initial)
                maker_arm_observer.start(camera_directory,seconds=86400)
            except Exception as cleanup_error:
                error=RuntimeError(str(error)+'; preview restore: '+str(cleanup_error))
        write(status_path,{'phase':'error','error':str(error)})

def launch(report_path,playback_path,hz=20):
    try:
        if _session is None:raise RuntimeError('Runtime controller not started')
        _session.launch(report_path,playback_path,hz)
    except Exception as error:write(playback_path,{'done':True,'error':str(error)})

def stop(commit=True):
    if _session:_session.stop(commit)
