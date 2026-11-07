"""Bounded Chaos drop test in a temporary Simulate-in-Editor world.
Never applies runtime changes back to the shared editor level.
"""
import json,time
from pathlib import Path
import unreal
from copresence import animation

_probe=None

class DropProbe:
    def __init__(self,output):
        self.editor=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.level=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if self.editor.get_game_world() is not None:raise RuntimeError('A play/simulation world is already active')
        if animation.is_running():raise RuntimeError('Shared animation is active')
        self.output=Path(output);self.started=time.monotonic();self.phase='starting';self.actor=None;self.samples=[];self.error=None
        self.original=self.find(self.editor.get_editor_world()).get_actor_transform()
        self.callback=unreal.register_slate_post_tick_callback(self.tick)
        self.level.editor_play_simulate()

    def find(self,world):
        for actor in unreal.GameplayStatics.get_all_actors_of_class(world,unreal.StaticMeshActor):
            if actor.get_actor_label()=='MakerStation_Approximate_Block_Orange':return actor
        raise RuntimeError('Orange block missing')

    def tick(self,delta):
        now=time.monotonic()
        try:
            world=self.editor.get_game_world()
            if self.phase=='starting':
                if world is None:
                    if now-self.started>15:raise TimeoutError('Simulation world did not start')
                    return
                self.actor=self.find(world)
                c=self.actor.static_mesh_component
                c.set_mobility(unreal.ComponentMobility.MOVABLE)
                c.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
                c.set_collision_profile_name('PhysicsActor')
                p=self.actor.get_actor_location();p.z+=30
                self.actor.set_actor_location(p,False,True)
                c.set_mass_override_in_kg('',.05,True);c.set_enable_gravity(True);c.set_simulate_physics(True)
                if not c.is_simulating_physics():raise RuntimeError('Chaos simulation did not enable')
                self.started=now;self.phase='falling'
            elif self.phase=='falling':
                if world is None:raise RuntimeError('Simulation ended externally')
                p=self.actor.get_actor_location();v=self.actor.static_mesh_component.get_physics_linear_velocity()
                self.samples.append({'t':now-self.started,'position_cm':[p.x,p.y,p.z],'velocity_cm_s':[v.x,v.y,v.z]})
                if now-self.started>=3:
                    self.level.editor_request_end_play();self.phase='ending';self.started=now
            elif self.phase=='ending':
                if world is not None:
                    if now-self.started>10:raise TimeoutError('Simulation end timed out')
                    return
                self.finish()
        except Exception as error:
            self.error=str(error)
            if self.editor.get_game_world() is not None and self.phase!='ending':
                self.level.editor_request_end_play();self.phase='ending';self.started=now
                return
            self.finish()

    def finish(self):
        if self.callback is not None:unreal.unregister_slate_post_tick_callback(self.callback);self.callback=None
        current=self.find(self.editor.get_editor_world()).get_actor_transform()
        q=current.rotation;r=self.original.rotation
        unchanged=((current.translation-self.original.translation).length()<.001
                   and (current.scale3d-self.original.scale3d).length()<1e-6
                   and abs(q.x*r.x+q.y*r.y+q.z*r.z+q.w*r.w)>1-1e-6)
        simulation_ended=self.editor.get_game_world() is None
        final=self.samples[-1] if self.samples else None
        resting=bool(final and abs(final['position_cm'][2]-77.5)<.5 and max(abs(v) for v in final['velocity_cm_s'])<1)
        fallen=bool(self.samples and self.samples[0]['position_cm'][2]-final['position_cm'][2]>25)
        self.output.parent.mkdir(parents=True,exist_ok=True)
        self.output.write_text(json.dumps({'scope':'Chaos tabletop drop test; no grasp or hardware calibration','error':self.error,'editor_preserved':unchanged,'simulation_ended':simulation_ended,'fell':fallen,'settled_on_table':resting,'passed':not self.error and simulation_ended and unchanged and fallen and resting,'samples':self.samples},indent=2)+'\n')

def start(output):
    global _probe
    if _probe and _probe.callback is not None:raise RuntimeError('Probe already active')
    try:_probe=DropProbe(output)
    except Exception as error:Path(output).write_text(json.dumps({'passed':False,'error':str(error)}))
