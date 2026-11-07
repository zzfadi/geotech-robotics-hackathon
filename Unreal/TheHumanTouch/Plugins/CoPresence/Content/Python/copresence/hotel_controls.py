"""Bounded dashboard commands, executed on the editor thread by Delivery.poll."""
import math
import queue
import uuid
from copresence.nav2_delivery import ACTIVE

class Controls:
    def __init__(self, delivery):
        self.d = delivery
        self.session = uuid.uuid4().hex
        self.inbox = queue.Queue(maxsize=8)
        self.seen = set()
        self.last = None

    def mission(self):
        d = self.d
        idle = d.state not in ACTIVE
        ready=d.b.connected and d.b.map_seen and getattr(d.b,'nav_ready',False)
        p=d.actors['Fleet_ChargeDock_01'].get_actor_location();b=d.b
        dx,dy=(p.x-b.origin[0])/100,(p.y-b.origin[1])/100
        c,s=math.cos(b.initial_heading),math.sin(b.initial_heading)
        ox,oy=c*dx+s*dy,s*dx-c*dy
        tx,ty,a=b.map_to_odom
        dock=[tx+math.cos(a)*ox-math.sin(a)*oy,ty+math.sin(a)*ox+math.cos(a)*oy]
        return dict(session=self.session, state=d.state, actions=['start_scan','start_delivery','collect_delivery','stop_scan','return_dock'],
                    can_deliver=idle and ready, can_collect=d.state=='WAITING_FOR_GUEST',
                    can_return=idle and ready and d.state!='DOCKED', can_scan=idle and ready,
                    dock_label='CHARGE 01', dock_map=dock, detail='Hotel snack delivery · returns to CHARGE 01 after collection.', last_command=self.last)

    def poll(self):
        while not self.inbox.empty():
            c = self.inbox.get_nowait()
            ident = c.get('id')
            if ident in self.seen: continue
            self.seen.add(ident)
            if len(self.seen)>256: self.seen={ident}
            try:
                if c.get('session')!=self.session or not self.d.b.sim_time<=float(c.get('deadline_sim',-1))<=self.d.b.sim_time+5:
                    raise ValueError('Command expired or session changed')
                action=c.get('action');d=self.d
                if action=='start_delivery': d.start_order(dict(room=c.get('room',101),item='snack',order_id=ident))
                elif action=='start_scan': d.start_survey()
                elif action=='collect_delivery': d.confirm_collection(d.order['id'] if d.order else '')
                elif action=='stop_scan':
                    if d.elevator_in_transit: raise ValueError('Wait for the elevator to reach a landing before cancelling')
                    d.b.halt_motion();d.end_props();
                    if d.elevator is not None:
                        d.elevator.end();d.elevator=None
                    d.route=[];d.goal_id=None;d.transition('CANCELLED')
                elif action=='return_dock':
                    if d.state in ACTIVE: raise ValueError('Stop or complete the active delivery first')
                    d.route_to_dock()
                else: raise ValueError('Unsupported hotel action')
                self.last=dict(id=ident,accepted=True,message='Hotel robot accepted the command')
            except Exception as exc: self.last=dict(id=ident,accepted=False,message=str(exc))
