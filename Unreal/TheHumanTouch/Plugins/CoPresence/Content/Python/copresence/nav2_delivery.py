"""Guest order to room delivery. Nav2 owns every mobile-base movement.

Loading/collection are explicit simulation interactions, not robot navigation.
No Sequencer, legacy path planner, camera follower, or level saves are used.
"""
from copresence.hotel_layouts import corridor_route,hallway,ELEVATOR_APPROACH
import bisect
import json
import math
from pathlib import Path
import time

_instance = None
ACTIVE = {'ORDER_RECEIVED', 'TO_PICKUP', 'LOADING', 'TO_ROOM', 'WAITING_FOR_GUEST', 'COLLECTING', 'RETURNING_TO_CHARGER', 'ELEVATOR', 'TO_ELEVATOR', 'SCANNING'}


def validate_order(command):
    room = command.get('room', 101)
    if isinstance(room, bool) or not isinstance(room, int) or not (1 <= room//100 <= 10 and 1 <= room%100 <= 16):
        raise ValueError('Select a hotel room on floors 1–10, rooms 01–16')
    if command.get('item', 'snack') != 'snack':
        raise ValueError('This demonstration loads one snack tray')
    ident = str(command.get('order_id', ''))
    if not ident or len(ident) > 80:
        raise ValueError('A nonempty order_id of at most 80 characters is required')
    return {'id': ident, 'room': room, 'item': 'snack'}


class Delivery:
    def __init__(self, bridge):
        import unreal
        from copresence.roomrunner_doors import RoomDoors
        self.u, self.b = unreal, bridge
        if bridge.patrol or bridge.stopping.is_set() or (bridge.goal_id and bridge.goal_result is None):
            raise RuntimeError('Start a fresh idle bridge, without a patrol')
        self.actors = {a.get_actor_label(): a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
        names = ['RS_UpperArm', 'RS_Forearm', 'RS_Gripper', 'RS_TrayFork', 'RS_FingerL', 'RS_FingerR',
                 'RS_LoadingTote', 'RS_PickSnack', 'RS_DoorL', 'RS_DoorR', 'Hotel_OrderGuest_1003']
        missing = [n for n in names if n not in self.actors]
        if missing:
            raise RuntimeError('Missing delivery scene actors: ' + ', '.join(missing))
        self.rig = {n: self.actors[n] for n in names}
        self.doors = RoomDoors(self.actors)
        self.station = json.loads((Path(unreal.Paths.project_dir())/'Art/RoomRunnerStation/manifest.json').read_text())
        self.poses = self.station['poses']
        self.pose_times = [p['t'] for p in self.poses]
        self.pickup = self.station_world(self.poses[-1]['tray'])
        from copresence.devonics_arm import Arm, NAMES
        self.owned = []
        self.w = self.station_world
        self.visual_arm = Arm(self) if all(n in self.actors for n in NAMES) else None
        self.root = Path(unreal.Paths.project_dir())/'Saved/Nav2Delivery'
        (self.root/'commands').mkdir(parents=True, exist_ok=True)
        bridge.delivery = self
        from copresence.ros_crowd import Crowd
        if getattr(bridge, "crowd", None) is None:
            bridge.crowd = Crowd(bridge)
        self.state, self.order, self.error = 'IDLE', None, None
        self.events, self.route, self.nav_results = [], [], []
        self.loaded, self.collected, self.transaction = False, False, False
        self.elapsed, self.last_poll, self.last_write = 0.0, 0.0, 0.0
        self.goal_id, self.goal_started, self.last_point = None, 0.0, None
        self.room_open, self.door_amount, self.stage_started = 0.0, 0.0, time.monotonic()
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        from copresence.hotel_controls import Controls
        self.controls = Controls(self)
        self.current_floor = max(1,min(10,round(self.b.robot.get_actor_location().z/400)+1))
        self.elevator = None
        self.elevator_in_transit = False
        self.write()

    def event(self, name, **values):
        self.events.append({'event': name, 'time': time.time(), **values})
        self.events = self.events[-200:]

    def transition(self, state):
        self.state, self.elapsed, self.stage_started = state, 0.0, time.monotonic()
        self.event(state)
        self.write()

    def status(self):
        return {'state': self.state, 'order': self.order, 'payload_loaded': self.loaded,
                'collected': self.collected, 'error': self.error, 'events': self.events,
                'nav_results': self.nav_results, 'route_remaining': len(self.route),
                'robot_position_cm': list(self.b.robot.get_actor_location().to_tuple()) if not self.b.stopping.is_set() else None,
                'bridge': self.b.status(), 'updated': time.time()}

    def write(self):
        data = json.dumps(self.status(), indent=2)
        temporary = self.root/'status.tmp'
        temporary.write_text(data)
        temporary.replace(self.root/'status.json')
        if self.order and self.state in {'DELIVERED', 'DOCKED', 'FAULT', 'CANCELLED'}:
            (self.root/'last-delivery.json').write_text(data)

    def begin_props(self):
        if not self.transaction:
            if getattr(self.b,"crowd",None):self.b.crowd.end()
            self.u.SystemLibrary.begin_transaction('CoPresence', self.u.Text('ROS delivery interaction'), self.b.robot)
            for actor in list(self.rig.values()) + [self.doors.doors[self.order['room']][0]]:
                self.u.SystemLibrary.transact_object(actor)
                self.u.SystemLibrary.transact_object(actor.root_component)
            self.transaction = True

    def end_props(self):
        if self.transaction:
            self.u.SystemLibrary.end_transaction()
            self.transaction = False

    def move(self, actor, p, rotation=None):
        actor.set_actor_location_and_rotation(self.u.Vector(*p), rotation or actor.get_actor_rotation(), False, False)
        self.u.SystemLibrary.snapshot_object(actor.root_component)

    def robot_doors(self, amount):
        self.door_amount = amount
        p, yaw = self.b.robot.get_actor_location().to_tuple(), self.b.robot.get_actor_rotation().yaw
        co, si = math.cos(math.radians(yaw-90)), math.sin(math.radians(yaw-90))
        for n, dy, sign in [('RS_DoorL', -25.9, -1), ('RS_DoorR', 25.9, 1)]:
            self.move(self.rig[n], (p[0]+co*-25.5-si*dy, p[1]+si*-25.5+co*dy, p[2]+59),
                      self.u.Rotator(yaw=yaw-sign*100*amount))

    def station_world(self, p):
        o = self.station['origin_cm']
        return o[0]+p[0]*100, o[1]-p[1]*100, o[2]+p[2]*100

    def arm_pose(self, t):
        i = max(0, min(len(self.poses)-2, bisect.bisect_right(self.pose_times, t)-1))
        a, b = self.poses[i:i+2]
        alpha = max(0, min(1, (t-a['t'])/max(.001, b['t']-a['t'])))
        def value(k):
            if isinstance(a[k], list):
                return [u+(v-u)*alpha for u,v in zip(a[k], b[k])]
            return a[k]+(b[k]-a[k])*alpha
        shoulder, elbow, wrist = value('shoulder'), value('elbow'), value('wrist')
        for n,p,q in [('RS_UpperArm', shoulder, elbow), ('RS_Forearm', elbow, wrist)]:
            r = self.u.MathLibrary.make_rot_from_z(self.u.Vector(q[0]-p[0], p[1]-q[1], q[2]-p[2]))
            self.move(self.rig[n], self.station_world(p), r)
        gap = value('gap')
        for n,p in [('RS_Gripper',wrist), ('RS_TrayFork',wrist),
                    ('RS_FingerL',[wrist[0]-gap,wrist[1],wrist[2]]), ('RS_FingerR',[wrist[0]+gap,wrist[1],wrist[2]]),
                    ('RS_LoadingTote',value('tray')), ('RS_PickSnack',value('snack'))]:
            self.move(self.rig[n], self.station_world(p), self.u.Rotator())
        if self.visual_arm:
            self.visual_arm.pose(t)

    def start_order(self, command):
        order = validate_order(command)
        if self.state not in {'IDLE', 'DOCKED', 'CANCELLED', 'FAULT'}:
            raise RuntimeError('Finish or stop the current order first')
        if not self.b.connected or not self.b.map_seen or not getattr(self.b,"nav_ready",False) or self.b.stopping.is_set():
            raise RuntimeError('ROS bridge and floor map are not ready')
        if order['room'] not in self.doors.doors:
            raise RuntimeError('Requested room door is missing')
        self.order, self.error = order, None
        self.loaded, self.collected = False, False
        self.events, self.nav_results, self.goal_id = [], [], None
        self.target_floor = order['room']//100
        index = (order['room'] % 100 - 1) % 8 + 1
        suite=self.actors['Hotel_Suite_'+str(order['room'])].get_actor_location()
        self.room_x = suite.x
        self.room_hall_y=hallway(self.target_floor,self.room_x)
        # Handoff outside the doorway: old closed-door SLAM cells must not
        # send Nav2 on a detour through neighboring rooms.
        self.room_y = suite.y + (90 if index <= 4 else -90)
        self.guest_y = suite.y + (-110 if index <= 4 else 110)
        self.room_heading = -90 if index <= 4 else 90
        self.begin_props()
        for n in ['RS_LoadingTote', 'RS_PickSnack']:
            self.b.release(self.rig[n])
            self.rig[n].set_is_temporarily_hidden_in_editor(False)
            self.rig[n].set_actor_hidden_in_game(False)
        self.arm_pose(0)
        self.robot_doors(0)
        guest = self.rig['Hotel_OrderGuest_1003']
        self.move(guest, (self.room_x, self.guest_y, (self.target_floor-1)*400), self.u.Rotator(yaw=90 if index <= 4 else -90))
        # A repeat order can begin with the robot still in this doorway.
        # Leave an open door open until it has cleared the threshold.
        if math.dist(self.b.robot.get_actor_location().to_tuple()[:2], (self.room_x,self.room_y)) > 120:
            self.doors.set_pose(order['room'], 0)
        self.end_props()
        if self.current_floor!=1:
            self.use_elevator(1,[], 'ORDER_RECEIVED')
        else: self.transition('ORDER_RECEIVED')

    def start_survey(self):
        if self.state in ACTIVE: raise RuntimeError('Stop or complete the current mission first')
        if not self.b.connected or not self.b.map_seen or not getattr(self.b,'nav_ready',False):raise RuntimeError('Navigation server and floor map are not ready')
        self.survey_completed=[];self.order=None;self.error=None;self.goal_id=None
        p=self.b.robot.get_actor_location()
        start_x=ELEVATOR_APPROACH if self.current_floor!=1 else p.x
        approach=[]
        if self.current_floor==1 and p.x < -1000:
            approach=[(p.x,2100,0),(-2350,2100,0),(-1750,1750,0),(-1100,1750,0)]
            start_x=-1100
        self.route=approach+corridor_route(1,start_x,-700)+corridor_route(1,-700,ELEVATOR_APPROACH)
        if self.current_floor!=1:self.use_elevator(1,self.route,'SCANNING')
        else:self.transition('SCANNING')

    def use_elevator(self,target,route,resume):
        from copresence.hotel_elevator import Elevator
        self.elevator=Elevator(self,target,route,resume)
        self.transition('ELEVATOR')

    def send_next(self):
        self.last_point = self.route.pop(0)
        p = self.b.robot.get_actor_location()
        self.route_distance_limit = max(8.0, 2.0*math.hypot(p.x-self.last_point[0], p.y-self.last_point[1])/100+3.0)
        self.precision_goal = (not self.route and self.state in {'TO_PICKUP','RETURNING_TO_CHARGER'})
        self.goal_id = self.b.navigate_world(*self.last_point, precision=self.precision_goal)
        self.goal_started = self.b.sim_time
        self.goal_retries = 0
        self.event('NAV2_GOAL_SENT', goal_id=self.goal_id, world_goal=list(self.last_point))

    def navigate(self, tolerance_cm=40):
        if self.goal_id is None:
            if self.route:
                self.send_next()
                return False
            return True
        if self.b.sim_time-self.goal_started > 180:
            raise RuntimeError('Nav2 goal timed out; no delivery progress is inferred')
        feedback = getattr(self.b, 'goal_feedback', None) or {}
        remaining = feedback.get('distance_remaining')
        if remaining is not None and remaining > getattr(self, 'route_distance_limit', float('inf')):
            self.b.halt_motion()
            raise RuntimeError(f'Navigation rejected a {remaining:.1f} m detour for a short corridor goal; robot stopped before entering unrelated rooms')
        result = self.b.goal_result
        if result is None:
            return False
        if result.get('id') != self.goal_id:
            return False
        if result.get('status') != 4 or not result.get('result') or result.get('values', {}).get('error_code', 0):
            raise RuntimeError('Nav2 goal failed: ' + json.dumps(result))
        if abs(self.b.v)+abs(self.b.w) > .025:
            return False
        p = self.b.robot.get_actor_location().to_tuple()
        distance = math.dist(p[:2], self.last_point[:2])
        if distance > tolerance_cm:
            # SLAM can change map->odom after a landmark goal was submitted.
            # Reproject the same world landmark, never infer physical arrival.
            if self.goal_retries >= 3:
                raise RuntimeError(f'Nav2 reported success but physical arrival error is {distance:.1f} cm after 3 reprojections')
            self.goal_retries += 1
            self.goal_id = self.b.navigate_world(*self.last_point, precision=getattr(self, 'precision_goal', False))
            self.goal_started = self.b.sim_time
            self.event('NAV2_LANDMARK_REPROJECTED', goal_id=self.goal_id, error_cm=distance,
                       retry=self.goal_retries)
            return False
        self.nav_results.append({'goal_id': self.goal_id, 'result': result, 'world_position': p, 'error_cm': distance})
        self.event('NAV2_ARRIVED', goal_id=self.goal_id, error_cm=distance)
        self.goal_id = None
        return not self.route

    def return_to_charger(self):
        if not self.collected:
            raise RuntimeError('Guest must collect the order before returning')
        self.route_to_dock()

    def route_to_dock(self):
        dock = self.actors["Fleet_ChargeDock_01"].get_actor_location()
        self.error = None
        self.goal_id = None
        x = self.b.robot.get_actor_location().x
        self.route = [(x,hallway(self.current_floor,x),180)] + corridor_route(1,x,-1100) + [
                      (-1750,1750,180), (-2350,2100,180),
                      (dock.x,2100,180), (dock.x,dock.y,-90)]
        if self.current_floor==1 and x < -2350:
            # At the loading station, y=1750 lies against the conveyor. Exit
            # through the open loading aisle instead of aiming behind the bay.
            self.route=[(x,2100,180),(dock.x,2100,180),(dock.x,dock.y,-90)]
        if self.current_floor!=1:
            self.use_elevator(1,corridor_route(1,ELEVATOR_APPROACH,-1100)+[(-1750,1750,180),(-2350,2100,180),(dock.x,2100,180),(dock.x,dock.y,-90)],'RETURNING_TO_CHARGER')
        else: self.transition('RETURNING_TO_CHARGER')

    def confirm_collection(self, order_id):
        if self.state != 'WAITING_FOR_GUEST' or not self.order or order_id != self.order['id']:
            raise RuntimeError('Collection requires the arrived order id')
        self.begin_props()
        self.cargo_starts = {}
        for n in ['RS_LoadingTote', 'RS_PickSnack']:
            self.b.release(self.rig[n])
            self.cargo_starts[n] = self.rig[n].get_actor_location().to_tuple()
        self.event('GUEST_CONFIRMED_COLLECTION', order_id=order_id)
        self.transition('COLLECTING')

    def poll(self):
        self.controls.poll()
        for path in sorted((self.root/'commands').glob('*.json')):
            command = json.loads(path.read_text())
            ident = command.get('id', path.stem)
            try:
                if command.get('action') == 'order':
                    self.start_order(command)
                elif command.get('action') == 'collect':
                    self.confirm_collection(command.get('order_id'))
                elif command.get('action') == 'stop':
                    self.b.stop(); self.end_props(); self.transition('CANCELLED')
                else:
                    raise ValueError('Unknown action')
                ack = {'id': ident, 'ok': True}
            except Exception as exc:
                ack = {'id': ident, 'ok': False, 'error': str(exc)}
            (self.root/('ack-'+path.stem+'.json')).write_text(json.dumps(ack))
            path.unlink()

    def tick(self, dt):
        try:
            now = time.monotonic()
            if now-self.last_poll > .2:
                self.last_poll = now
                self.poll()
            if self.state in ACTIVE:
                if not self.b.connected or self.b.stopping.is_set():
                    raise RuntimeError('ROS navigation connection stopped')
                self.elapsed += min(max(float(dt), 0), .1)
            if self.state == 'ELEVATOR':
                self.elevator.tick(dt)
            elif self.state == 'ORDER_RECEIVED' and self.elapsed >= 2:
                x = self.b.robot.get_actor_location().x
                self.route = []
                if x <= -2750:
                    self.route.append((x,2100,-90))
                if x > -1750:
                    self.route.append((x,1750,180))
                    if x > -1100:
                        self.route += corridor_route(1,x,-1100)
                    self.route += [(-1750,1750,180),(-2350,2100,180)]
                self.route += [(self.pickup[0],2100,0),(self.pickup[0],1900,-90),(self.pickup[0],self.pickup[1],180)]
                self.transition('TO_PICKUP')
            elif self.state == 'TO_PICKUP' and self.navigate(tolerance_cm=6 if not self.route else 40):
                p=self.b.robot.get_actor_location()
                if math.hypot(p.x-self.pickup[0],p.y-self.pickup[1]) > 6:
                    raise RuntimeError('Pickup goal finished outside the loading bay; refusing food transfer')
                self.begin_props(); self.transition('LOADING')
            elif self.state == 'LOADING':
                self.robot_doors(min(1, self.elapsed/1.5))
                self.arm_pose(min(24, self.elapsed))
                if self.elapsed >= 24:
                    alpha = min(1, (self.elapsed-24)/2)
                    base = self.b.robot.get_actor_location().to_tuple()
                    for n,z in [('RS_LoadingTote',61.5), ('RS_PickSnack',64)]:
                        start = self.station_world(self.poses[-1]['tray' if n=='RS_LoadingTote' else 'snack'])
                        dest = (base[0],base[1],base[2]+z)
                        self.move(self.rig[n],tuple(a+(b-a)*alpha for a,b in zip(start,dest)))
                    self.robot_doors(1-alpha)
                    if alpha >= 1:
                        for n in ['RS_DoorL','RS_DoorR','RS_LoadingTote','RS_PickSnack']:
                            self.b.carry(self.rig[n])
                        self.loaded = True
                        self.event('ITEMS_LOADED', items=['snack'], robot_position=list(base))
                        self.end_props()
                        self.route = [(-2575,2100,90), (-2350,2100,0), (-1750,1750,0),
                                      (-1100,1750,0)]+corridor_route(1,-1100,self.room_x)
                        self.room_open = 0
                        if self.target_floor!=self.current_floor:
                            self.route=[(-2575,2100,90),(-2350,2100,0),(-1750,1750,0),(-1100,1750,0)]+corridor_route(1,-1100,ELEVATOR_APPROACH)
                            self.transition('TO_ELEVATOR')
                        else: self.transition('TO_ROOM')
            elif self.state == 'SCANNING' and self.navigate():
                self.survey_completed.append(self.current_floor)
                self.event('FLOOR_SURVEY_COMPLETE',floor=self.current_floor)
                if self.current_floor<10:
                    self.use_elevator(self.current_floor+1,corridor_route(self.current_floor+1,ELEVATOR_APPROACH,-700)+corridor_route(self.current_floor+1,-700,ELEVATOR_APPROACH),'SCANNING')
                else:self.route_to_dock()
            elif self.state == 'TO_ELEVATOR' and self.navigate():
                self.use_elevator(self.target_floor,corridor_route(self.target_floor,ELEVATOR_APPROACH,self.room_x),'TO_ROOM')
            elif self.state == 'TO_ROOM' and self.navigate():
                if self.room_open == 0:
                    self.begin_props(); self.doors.set_pose(self.order['room'],1); self.end_props()
                    self.room_open = 1
                    self.route = [(self.room_x,self.room_y,self.room_heading)]
                else:
                    self.event('ROOM_ARRIVAL',room=self.order['room'])
                    self.transition('WAITING_FOR_GUEST')
            elif self.state == 'DELIVERED':
                self.return_to_charger()
            elif self.state == 'RETURNING_TO_CHARGER' and self.navigate():
                self.event('CHARGING_BAY_REACHED', bay=1)
                self.transition('DOCKED')
            elif self.state == 'COLLECTING':
                self.robot_doors(min(1,self.elapsed/1.5))
                alpha = min(1,max(0,(self.elapsed-1.5)/3))
                for n,p in self.cargo_starts.items():
                    dest = (self.room_x,self.guest_y,(self.target_floor-1)*400+102+(2.5 if n=='RS_PickSnack' else 0))
                    self.move(self.rig[n],tuple(a+(b-a)*alpha for a,b in zip(p,dest)))
                if self.elapsed >= 5:
                    self.loaded,self.collected = False,True
                    self.robot_doors(max(0,1-(self.elapsed-5)/1.5))
                if self.elapsed >= 6.5:
                    for n in ['RS_DoorL','RS_DoorR']:self.b.carry(self.rig[n])
                    self.end_props()
                    self.event('ITEMS_COLLECTED',room=self.order['room'])
                    self.transition('DELIVERED')
            if now-self.last_write > .5:
                self.last_write = now
                self.write()
        except Exception as exc:
            if self.elevator is not None: self.elevator.end()
            self.error = str(exc)
            self.b.halt_motion()
            self.end_props()
            self.state = 'FAULT'
            self.event('FAULT', error=self.error)
            self.write()

    def stop(self):
        self.b.stop();self.end_props()
        if self.handle is not None:
            self.u.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        self.transition('CANCELLED')


def start():
    global _instance
    from copresence import nav2_bridge
    if _instance is not None and _instance.handle is not None:
        raise RuntimeError('Delivery service is already running')
    _instance = Delivery(nav2_bridge._instance)
    return _instance.status()
