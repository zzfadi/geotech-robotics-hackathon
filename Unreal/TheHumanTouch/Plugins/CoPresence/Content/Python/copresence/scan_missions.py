"""Bounded room survey and dock-return missions driven by real Nav2 actions.

Called on the joined editor's tick. This module never moves actors directly.
"""
from collections import deque
import math
import time
import uuid

ACTIONS = ('start_scan', 'stop_scan', 'return_dock')


class RoomMissions:
    def __init__(self, bridge, dock=None, clock=time.monotonic):
        self.bridge = bridge
        self.clock = clock
        self.session = uuid.uuid4().hex
        self.dock = dock  # UE x/y/yaw for the visual dock anchor.
        self.state = 'idle'
        self.detail = 'Ready for a room scan.'
        self.route = []
        self.index = 0
        self.kind = None
        self.stage = None
        self.deadline = 0
        self.next_step = 0
        self.last_command = None
        self.seen = deque(maxlen=128)

    def hold(self):
        b = self.bridge
        b.operator_stop = True
        b.v = b.w = 0.0
        with b.lock:
            b.cmd = (0.0, 0.0, 0.0)

    def pending_goal(self):
        b = self.bridge
        return bool(b.goal_id and b.goal_result is None)

    def cancel_goal(self):
        if self.pending_goal():
            self.bridge.requests.put_nowait({'op': 'cancel_action_goal', 'action': '/navigate_to_pose', 'id': self.bridge.goal_id})

    def dock_target(self):
        if self.dock is None:
            raise ValueError('No charging dock is present in this room')
        x, y, heading = self.dock
        a = math.radians(heading)
        # Existing saved home pose is 45 cm in front of the visual dock.
        # This is a navigation approach pose, not electrical contact simulation.
        return (x + 45 * math.cos(a), y + 45 * math.sin(a), heading)

    def start_route(self, kind, route):
        b = self.bridge
        if not b.connected or not b.map_seen:
            raise ValueError('Wait for the robot connection and SLAM map')
        if not route:
            raise ValueError('No scan destinations configured')
        self.hold()
        b.patrol = None
        self.stage = None
        self.route = []
        self.cancel_goal()
        self.route = route
        self.index = 0
        self.kind = kind
        self.state = 'canceling' if self.pending_goal() else kind
        self.detail = 'Stopping the previous route…' if self.pending_goal() else 'Preparing navigation…'
        self.stage = 'canceling' if self.pending_goal() else 'settling'
        self.deadline = self.clock() + 8
        self.next_step = self.clock() + .4

    def stop(self):
        self.hold()
        self.bridge.patrol = None
        self.route = []
        self.kind = None
        self.stage = None
        self.cancel_goal()
        self.state = 'stopping' if self.pending_goal() else 'stopped'
        self.detail = 'Stopping navigation. Live sensors stay on.' if self.pending_goal() else 'Stopped. Live sensors stay on.'
        self.stage = 'canceling' if self.pending_goal() else None
        self.deadline = self.clock() + 8

    def command(self, data):
        ident = data.get('id')
        if not isinstance(ident, str) or not 1 <= len(ident) <= 64 or ident in self.seen:
            return
        # Commands from an old adapter/session can never restart motion.
        if data.get('session') != self.session:
            return
        self.seen.append(ident)
        action = data.get('action')
        try:
            if action not in ACTIONS:
                raise ValueError('Unsupported robot action')
            expiry = data.get('deadline_sim')
            if not isinstance(expiry, (float, int)) or not math.isfinite(expiry) or self.bridge.sim_time > expiry:
                raise ValueError('Command expired; try again')
            if action == 'stop_scan':
                self.stop()
            elif action == 'start_scan':
                if self.kind == 'scanning' and self.stage:
                    raise ValueError('A scan is already running')
                destinations = self.bridge.rig.layout['destinations']
                route = [(name, (*destinations[name], 90.0)) for name in ('sofa', 'doorway', 'kitchen') if name in destinations]
                self.start_route('scanning', route)
            else:
                self.start_route('returning_dock', [('dock approach', self.dock_target())])
            self.last_command = {'id': ident, 'action': action, 'accepted': True, 'message': self.detail}
        except Exception as exc:
            self.last_command = {'id': ident, 'action': action, 'accepted': False, 'message': str(exc)}

    def fail(self, reason):
        self.hold()
        try:
            self.cancel_goal()
        except Exception:
            pass
        self.stage = None
        self.state = 'error'
        self.detail = reason
        self.route = []

    def tick(self):
        if not self.stage:
            return
        b, now = self.bridge, self.clock()
        if not b.connected:
            self.fail('Robot connection lost; motion stopped')
            return
        if self.stage == 'canceling':
            if self.pending_goal():
                if now > self.deadline:
                    self.fail('Navigation cancellation was not acknowledged. Robot remains stopped.')
                return
            if not self.route:
                self.stage = None
                self.state = 'stopped'
                self.detail = 'Stopped. Live sensors stay on.'
                return
            self.stage = 'settling'
            self.next_step = now + .4
        if self.stage == 'settling':
            if now < self.next_step:
                return
            try:
                name, point = self.route[self.index]
                self.hold()
                b.navigate_world(*point)
                b.operator_stop = False
                b.rig.drive.goal = name
                self.state = self.kind
                self.detail = f'Scanning {name} ({self.index+1}/{len(self.route)})' if self.kind == 'scanning' else 'Returning to the dock approach position…'
                self.stage = 'navigating'
                self.deadline = now + 90
            except Exception as exc:
                self.fail(str(exc))
            return
        if self.stage == 'navigating':
            result = b.goal_result
            if result is None:
                if now > self.deadline:
                    self.fail('Navigation timed out; robot stopped')
                return
            values = result.get('values') or {}
            if result.get('status') != 4 or not result.get('result') or values.get('error_code', 0) != 0:
                self.fail(values.get('error_msg') or f'Navigation ended with status {result.get("status")}; robot stopped')
                return
            self.hold()
            self.index += 1
            if self.index < len(self.route):
                self.stage = 'settling'
                self.next_step = now + .4
            else:
                self.stage = None
                self.state = 'scan_complete' if self.kind == 'scanning' else 'at_dock' if self.kind == 'returning_dock' else 'arrived'
                self.detail = 'Room scan route complete. Live sensors stay on.' if self.kind == 'scanning' else 'At the dock approach position. Battery charging is not simulated.' if self.kind == 'returning_dock' else 'Destination reached.'

    def status(self):
        b = self.bridge
        return {'session': self.session, 'actions': list(ACTIONS), 'state': self.state,
                'detail': self.detail, 'active': bool(self.stage), 'completed': self.index,
                'total': len(self.route), 'last_command': self.last_command,
                'dock_map': list(b.world_goal(*self.dock)) if self.dock and b.map_seen else None,
                'dock_target_map': list(b.world_goal(*self.dock_target())) if self.dock and b.map_seen else None,
                'can_scan': bool(b.connected and b.map_seen),
                'can_return': bool(b.connected and b.map_seen and self.dock)}
