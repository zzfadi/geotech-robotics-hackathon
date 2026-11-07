"""Interlocked elevator transfer; Nav2 drives all boarding and exit motion."""
import math,time
from copresence.hotel_layouts import corridor_route,hallway,ELEVATOR_X,ELEVATOR_APPROACH

class Elevator:
    def __init__(self,d,target,route,resume):
        self.d=d;self.b=d.b;self.u=d.u;self.target=target;self.route=route;self.resume=resume
        self.source=d.current_floor;self.car=d.actors['Hotel_LiftCar'];self.active=None;self.elapsed=0.;self.transaction=False
        self.steps=[('nav',(self.b.robot.get_actor_location().x,hallway(self.source,self.b.robot.get_actor_location().x),0))]+[('nav',p) for p in corridor_route(self.source,self.b.robot.get_actor_location().x,ELEVATOR_APPROACH)]+[('door',(f,False)) for f in range(1,11)]+[
            ('lift',(self.source,False)),('door',(self.source,True)),('wait',3),('nav',(ELEVATOR_X+120,1750,0)),
            ('door',(self.source,False)),('lift',(target,True)),('map',target),('door',(target,True)),('wait',3),
            ('nav',(ELEVATOR_APPROACH,1750,180)),('door',(target,False))]
    def begin(self,actors):
        if self.transaction:return
        if getattr(self.b,"crowd",None):self.b.crowd.end()
        u=self.u;u.SystemLibrary.begin_transaction('CoPresence',u.Text('Hotel elevator transfer'),self.car)
        for a in actors:u.SystemLibrary.transact_object(a);u.SystemLibrary.transact_object(a.root_component)
        self.transaction=True
    def end(self):
        if self.transaction:self.u.SystemLibrary.end_transaction();self.transaction=False
    def move(self,a,p):
        a.set_actor_location(self.u.Vector(*p),False,False);self.u.SystemLibrary.snapshot_object(a.root_component)
    def tick(self,dt):
        d,b=self.d,self.b;dt=min(max(dt,0),.1)
        if self.active is None:
            if not self.steps:
                b.operator_stop=False;d.route=self.route;d.goal_id=None;d.elevator=None;d.transition(self.resume);return
            self.active=self.steps.pop(0);self.elapsed=0.;self.started=b.sim_time
            k,v=self.active;d.event('ELEVATOR_'+k.upper(),value=v)
            if k=='nav':b.operator_stop=False;d.route=[v];d.goal_id=None
            else:b.halt_motion()
        self.elapsed+=dt;k,v=self.active
        if b.sim_time-self.started>180:raise RuntimeError('Elevator stage timed out: '+k)
        done=False
        if k=='nav':done=d.navigate()
        elif k=='wait':done=self.elapsed>=v
        elif k=='map':
            done=b.map_seen and getattr(b,'map_floor',None)==v
            if done:
                for scope in ['local','global']:
                    b.requests.put_nowait({'op':'call_service','service':f'/{scope}_costmap/clear_entirely_{scope}_costmap','args':{}})
        elif k=='door':
            f,opened=v;z=(f-1)*400
            if opened and abs(self.car.get_actor_location().z-z)>1:raise RuntimeError('Door interlock: cabin not at landing')
            p=b.robot.get_actor_location()
            if not opened and abs(p.z-z)<100 and abs(p.x-(ELEVATOR_X-10))<80 and abs(p.y-1750)<150:return
            actors=[d.actors[f'Hotel_LiftDoor_{f}_{side}'] for side in ['L','R']];self.begin(actors);done=True
            for a,sign in zip(actors,[-1,1]):
                p=a.get_actor_location();target=1750+sign*(142 if opened else 67.5);delta=target-p.y
                self.move(a,(p.x,p.y+max(-70*dt,min(70*dt,delta)),z))
                done=done and abs(delta)<.2
        elif k=='lift':
            f,occupied=v;z=(f-1)*400
            for floor in range(1,11):
                for side,sign in [('L',-1),('R',1)]:
                    if abs(d.actors[f'Hotel_LiftDoor_{floor}_{side}'].get_actor_location().y-(1750+sign*67.5))>.3:
                        raise RuntimeError('Lift interlock: landing door open')
            p=self.car.get_actor_location();delta=z-p.z;dz=max(-150*dt,min(150*dt,delta))
            actors=[self.car]+([b.robot]+[a for a,_,_ in b.parts] if occupied else [])
            self.begin(actors)
            if occupied:
                rp=b.robot.get_actor_location()
                if not (ELEVATOR_X+40<rp.x<ELEVATOR_X+200 and 1650<rp.y<1850):raise RuntimeError('Lift interlock: robot not fully boarded')
                d.elevator_in_transit=True
                for a in actors[1:]:
                    q=a.get_actor_location();self.move(a,(q.x,q.y,q.z+dz))
            self.move(self.car,(p.x,p.y,p.z+dz));done=abs(delta)<.2
            if done and occupied:
                d.current_floor=f;d.elevator_in_transit=False;b.map_seen=False
        if done:self.end();self.active=None
