"""Controlled runtime jaw-contact fixture, not an autonomous pickup test.
Places a simulated cube between the jaws, closes, enables gravity, lifts by
joint motion, then releases. Never attaches the cube or writes its lift path.
"""
import json,time,math
from pathlib import Path
import unreal
from copresence import animation
from maker_arm_model import mounted_frames,ue_pose
from maker_arm_station import BASES,VISUAL_LINKS

_probe=None

class GripProbe:
    def __init__(self,output,gap=.045,tcp_back=.04,wake=True,convex_diagnostic=False):
        self.editor=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem);self.level=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if self.editor.get_game_world() is not None:raise RuntimeError('Simulation already active')
        if animation.is_running():raise RuntimeError('Shared animation is active')
        self.gap=gap;self.tcp_back=tcp_back;self.wake=wake;self.convex_diagnostic=convex_diagnostic
        self.output=Path(output);self.phase='starting';self.started=time.monotonic();self.samples=[];self.error=None;self.cube=None;self.callback=None
        self.original={a.get_actor_label():a.get_actor_transform() for a in unreal.GameplayStatics.get_all_actors_of_class(self.editor.get_editor_world(),unreal.StaticMeshActor) if a.get_actor_label().startswith('MakerArm_right_') or a.get_actor_label()=='MakerStation_Approximate_Block_Orange'}
        self.callback=unreal.register_slate_post_tick_callback(self.tick);self.level.editor_play_simulate()

    def pose(self,q,gap):
        frames=mounted_frames(q,gap,yaw=math.pi)
        for link in VISUAL_LINKS:
            p,r=ue_pose(frames[link]);p=[p[i]+BASES['right'][i] for i in range(3)]
            self.actors['MakerArm_right_'+link].set_actor_location_and_rotation(unreal.Vector(*p),unreal.Quat(*r).rotator(),False,False)
        return frames['grasp_center']

    def tick(self,delta):
        now=time.monotonic()
        try:
            world=self.editor.get_game_world()
            if self.phase=='starting':
                if world is None:
                    if now-self.started>15:raise TimeoutError('No simulation world')
                    return
                self.actors={a.get_actor_label():a for a in unreal.GameplayStatics.get_all_actors_of_class(world,unreal.StaticMeshActor)}
                self.material=unreal.new_object(unreal.PhysicalMaterial)
                self.material.set_editor_property('friction',.8);self.material.set_editor_property('restitution',0.)
                for link in VISUAL_LINKS:
                    c=self.actors['MakerArm_right_'+link].static_mesh_component
                    if self.convex_diagnostic and link in ('gripper_left','gripper_right'):
                        mesh=unreal.load_asset('/Game/_Agents/Denys_Mac_Codex/MakerArm/Physics/SM_MakerArm_Collision_'+link)
                        if not mesh:raise RuntimeError('Missing diagnostic convex mesh')
                        c.set_static_mesh(mesh)
                    c.set_mobility(unreal.ComponentMobility.MOVABLE);c.set_collision_profile_name('BlockAllDynamic')
                    c.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS);c.set_simulate_physics(False);c.set_phys_material_override(self.material)
                t=self.pose([0,-1.57,1.57,0,0,0],.08)
                # Place the fixture center a declared distance back from the fingertip TCP.
                t=[row[:] for row in t]
                for i in range(3):t[i][3]+=self.tcp_back*t[i][2]
                p,r=ue_pose(t);p=[p[i]+BASES['right'][i] for i in range(3)]
                self.cube=self.actors['MakerStation_Approximate_Block_Orange'];c=self.cube.static_mesh_component
                c.set_mobility(unreal.ComponentMobility.MOVABLE);c.set_collision_profile_name('PhysicsActor')
                self.cube.set_actor_location_and_rotation(unreal.Vector(*p),unreal.Quat(*r).rotator(),False,True)
                c.set_phys_material_override(self.material);c.set_mass_override_in_kg('',.05,True);c.set_enable_gravity(False);c.set_simulate_physics(True)
                if not c.is_simulating_physics():raise RuntimeError('Fixture cube is not simulated')
                self.initial_z=p[2];self.started=now;self.phase='running'
            elif self.phase=='running':
                if world is None:raise RuntimeError('Simulation ended externally')
                elapsed=now-self.started
                if self.wake:self.cube.static_mesh_component.wake_all_rigid_bodies()
                if elapsed<1.5:gap=.08-(.08-self.gap)*elapsed/1.5;q2=-1.57
                elif elapsed<2.5:gap=self.gap;q2=-1.57;self.cube.static_mesh_component.set_enable_gravity(True)
                elif elapsed<4.5:gap=self.gap;q2=-1.57+.3*(elapsed-2.5)/2
                elif elapsed<6:gap=self.gap+(.08-self.gap)*(elapsed-4.5)/1.5;q2=-1.27
                else:gap=.08;q2=-1.27
                self.pose([0,q2,1.57,0,0,0],gap)
                p=self.cube.get_actor_location();v=self.cube.static_mesh_component.get_physics_linear_velocity()
                self.samples.append({'t':elapsed,'gap_m':gap,'q2':q2,'cube_cm':[p.x,p.y,p.z],'velocity_cm_s':[v.x,v.y,v.z]})
                if elapsed>=8:self.level.editor_request_end_play();self.phase='ending';self.started=now
            elif self.phase=='ending':
                if world is not None:
                    if now-self.started>10:raise TimeoutError('End simulation timeout')
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
        live={a.get_actor_label():a for a in unreal.GameplayStatics.get_all_actors_of_class(self.editor.get_editor_world(),unreal.StaticMeshActor)}
        def same_pose(name,t):
            if name not in live:return False
            current=live[name].get_actor_transform()
            q=current.rotation;r=t.rotation
            return ((current.translation-t.translation).length()<.001
                    and (current.scale3d-t.scale3d).length()<1e-6
                    and abs(q.x*r.x+q.y*r.y+q.z*r.z+q.w*r.w)>1-1e-6)
        unchanged=all(same_pose(name,t) for name,t in self.original.items())
        simulation_ended=self.editor.get_game_world() is None
        hold=[s['cube_cm'][2] for s in self.samples if 2<s['t']<2.5]
        lifted=[s['cube_cm'][2] for s in self.samples if 4<s['t']<4.5]
        final=self.samples[-1] if self.samples else None
        retained=bool(hold and min(hold)>self.initial_z-1)
        lift=bool(lifted and max(lifted)>self.initial_z+5)
        released=bool(final and abs(final['cube_cm'][2]-77.5)<1)
        self.output.parent.mkdir(parents=True,exist_ok=True)
        self.output.write_text(json.dumps({'scope':'controlled jaw fixture, not autonomous pickup','error':self.error,'editor_preserved':unchanged,'simulation_ended':simulation_ended,'wake_each_tick':self.wake,'convex_diagnostic':self.convex_diagnostic,'retained_under_gravity':retained,'lifted_over_5cm':lift,'released_to_table':released,'passed':not self.error and simulation_ended and unchanged and retained and lift and released,'commanded_gap_m':self.gap,'fixture_tcp_back_m':self.tcp_back,'assumed_cube_mass_kg':.05,'assumed_friction':.8,'samples':self.samples},indent=2)+'\n')

def start(output,gap=.045,tcp_back=.04,wake=True,convex_diagnostic=False):
    global _probe
    if _probe and _probe.callback is not None:raise RuntimeError('Grip probe active')
    try:_probe=GripProbe(output,gap,tcp_back,wake,convex_diagnostic)
    except Exception as error:Path(output).write_text(json.dumps({'passed':False,'error':str(error)}))
