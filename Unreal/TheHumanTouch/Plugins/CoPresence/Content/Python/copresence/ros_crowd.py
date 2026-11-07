"""Authoritative hotel pedestrian motion during ROS-controlled delivery.
Runs inside the bridge tick; never starts the legacy robot controller.
"""
import math,time
import unreal
from .hotel_layouts import columns

class Crowd:
    def __init__(self, bridge):
        self.bridge=bridge
        self.people=sorted((a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
                            if a.get_actor_label().startswith('Hotel_Pedestrian_')),key=lambda a:a.get_actor_label())
        self.goals={}
        self.elapsed=0.0
        self.enabled=True
        self.steps=0
        self.transaction=False
        self.started=0.0

    def end(self):
        if self.transaction:unreal.SystemLibrary.end_transaction();self.transaction=False

    def tick(self, dt):
        if not self.enabled:self.end();return
        self.elapsed+=dt
        if self.elapsed<0.1:return
        dt=min(self.elapsed,0.15);self.elapsed=0.0
        b=self.bridge
        delivery=getattr(b,'delivery',None)
        own=not b.transaction and not getattr(delivery,'transaction',False) and not getattr(getattr(delivery,'elevator',None),'transaction',False)
        if own and not self.transaction:
            unreal.SystemLibrary.begin_transaction('CoPresence',unreal.Text('Hotel pedestrian motion'),b.robot)
            self.transaction=True;self.started=time.monotonic()
        try:
            rp=b.robot.get_actor_location()
            for a in self.people:
                p=a.get_actor_location();idx=int(a.get_actor_label().rsplit('_',1)[1])
                lane_y=p.y
                if p.x < -1000:west,east=-1950,-1250
                else:
                    floor=max(1,min(10,int(round(p.z/400))+1))
                    column=min(columns(floor),key=lambda c:abs(c['x']-p.x))
                    west,east=column['left']+100,column['right']-100
                    lane_y=column['y']+(1 if idx%2 else -1)*(column['hall']/2-45)
                target=self.goals.setdefault(a.get_name(),east if p.x<(west+east)/2 else west)
                if abs(p.x-target)<12:target=west if target==east else east;self.goals[a.get_name()]=target
                # Clear a doorway approach before the robot reaches this lane.
                # Freezing X here strands a pedestrian in front of a robot that
                # is crossing the hall toward a room rather than driving along it.
                if abs(rp.z-p.z)<100 and math.hypot(rp.x-p.x,rp.y-p.y)<180:
                    target=east if p.x>=rp.x else west
                    self.goals[a.get_name()]=target
                direction=1 if target>p.x else -1
                next_y=p.y+max(-65*dt,min(65*dt,lane_y-p.y))
                next_x=p.x+direction*min(abs(target-p.x),65*dt)
                q=unreal.Vector(next_x,next_y,p.z)
                hit=unreal.SystemLibrary.capsule_trace_single(b.world,p+unreal.Vector(0,0,90),q+unreal.Vector(0,0,90),32,85,
                    unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,True,[a],unreal.DrawDebugTrace.NONE,True)
                if hit is not None and hit.to_tuple()[0]:
                    self.goals[a.get_name()]=west if target==east else east
                    continue
                unreal.SystemLibrary.transact_object(a);unreal.SystemLibrary.transact_object(a.root_component)
                a.set_actor_location_and_rotation(q,unreal.Rotator(yaw=-90 if direction>0 else 90),False,False)
                unreal.SystemLibrary.snapshot_object(a.root_component)
                self.steps+=1
        finally:
            if own and time.monotonic()-self.started>=5:self.end()
