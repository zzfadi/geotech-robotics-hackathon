"""TurtleBot solo scan-room adaptation of the tested Nav2 bridge. Activate only through a joined CoPresence agent task.

Baseline: kinematic differential drive, ideal encoders, horizontal raycast LiDAR.
No map saves, no scene edits at import, no automatic activation.
"""
import json
import math
import queue
import threading
import time
from pathlib import Path

_instance = None


def integrate(x, y, yaw, linear, angular, dt):
    """Exact planar integration, ROS units and handedness."""
    turn = angular * dt
    if abs(angular) < 1e-8:
        return x + linear * dt * math.cos(yaw), y + linear * dt * math.sin(yaw), yaw + turn
    radius = linear / angular
    return (x + radius * (math.sin(yaw + turn) - math.sin(yaw)),
            y - radius * (math.cos(yaw + turn) - math.cos(yaw)), yaw + turn)


def stamp(seconds):
    ns = round(seconds * 1_000_000_000)
    return {"sec": ns // 1_000_000_000, "nanosec": ns % 1_000_000_000}


def quaternion(yaw):
    return {"x": 0.0, "y": 0.0, "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)}


class Bridge:
    def __init__(self, robot_label, url, front_yaw_degrees, pause_existing):
        import unreal
        import os
        import sys
        deps = os.path.join(unreal.Paths.project_dir(), "Saved", "Nav2PythonDependencies")
        if deps not in sys.path:
            sys.path.append(deps)
        import websocket
        self.u = unreal
        self.websocket = websocket
        self.world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        if self.world is None or "-COPRESENCESOLO" not in unreal.SystemLibrary.get_command_line().upper():
            raise RuntimeError("A joined solo editor with a loaded world is required")
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
        self.sequences = [a for a in actors if isinstance(a, unreal.LevelSequenceActor)]
        if self.sequencer_playing():
            raise RuntimeError("Pause Sequencer before starting ROS navigation")
        matches = [a for a in actors if a.get_actor_label() == robot_label]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one robot: " + robot_label)
        self.robot = matches[0]
        from copresence import scan_robot_lab
        if scan_robot_lab._instance is None:scan_robot_lab.install()
        self.rig=scan_robot_lab._instance
        self.rig.command('stop');self.rig.close()
        self.old_controller=None;self.old_paused=None
        self.origin = self.robot.get_actor_location().to_tuple()
        self.initial_actor_yaw = self.robot.get_actor_rotation().yaw
        # The existing hotel model stores compartment doors as separate actors.
        # Only carry named robot parts that are physically inside this robot.
        self.parts=[]
        for actor in actors:
            if actor != self.robot and actor.get_actor_label().startswith('TB4_') and not actor.get_actor_label().startswith('TB4_Dock_'):
                p=actor.get_actor_location().to_tuple()
                self.parts.append((actor,tuple(p[i]-self.origin[i] for i in range(3)),actor.get_actor_rotation()))
        self.ignore = [self.robot] + [a for a, _, _ in self.parts]
        self.front_yaw = front_yaw_degrees
        self.initial_heading = math.radians(self.robot.get_actor_rotation().yaw + front_yaw_degrees)
        self.x = self.y = self.yaw = self.sim_time = 0.0
        self.v = self.w = 0.0
        self.cmd = (0.0, 0.0, 0.0)
        self.connected = False
        self.error = None
        self.collision_stops = self.commands_received = self.scans_sent = 0
        self.distance = self.next_scan = self.last_snapshot = 0.0
        self.transaction = False
        self.lock = threading.Lock()
        self.outbox = queue.Queue(maxsize=2)
        self.requests = queue.Queue(maxsize=8)
        self.goal_id = None
        self.goal_result = None
        self.goal_feedback = None
        self.patrol = None
        self.patrol_index = 0
        self.patrol_completed = 0
        self.patrol_next_time = None
        self.map_seen = False
        self.map_to_odom = (0.0, 0.0, 0.0)
        self.stopping = threading.Event()
        self.url = url
        from copresence.scan_missions import RoomMissions
        docks = [a for a in actors if a.get_actor_label() == 'TB4_Dock_dock_Black']
        dock = None
        if len(docks) == 1:
            location = docks[0].get_actor_location()
            dock = (location.x, location.y, docks[0].get_actor_rotation().yaw)
        self.mission = RoomMissions(self, dock)
        self.command_inbox = queue.Queue(maxsize=8)
        self.thread = threading.Thread(target=self.network, daemon=True)
        self.thread.start()
        self.operator_stop=False
        self.last_ui_command=self.rig.last_command
        self.rig.ros_owner=self
        self.handle = unreal.register_slate_post_tick_callback(self.tick)

    def network(self):
        ws = None
        try:
            ws = self.websocket.create_connection(self.url, timeout=5, http_proxy_host=None)
            ws.settimeout(1.0)
            ws.send(json.dumps({"op": "subscribe", "topic": "/cmd_vel",
                                "type": "geometry_msgs/msg/Twist", "queue_length": 1}))
            ws.send(json.dumps({"op": "subscribe", "topic": "/copresence/navigation_state",
                                "type": "std_msgs/msg/String", "queue_length": 1}))
            ws.send(json.dumps({"op": "subscribe", "topic": "/copresence/robot_command",
                                "type": "std_msgs/msg/String", "queue_length": 1}))
            self.connected = True
            while not self.stopping.is_set():
                ws.settimeout(1.0)
                try:
                    ws.send(json.dumps(self.requests.get_nowait(), allow_nan=False))
                except queue.Empty:
                    pass
                try:
                    batch = self.outbox.get_nowait()
                    for message in batch:
                        ws.send(json.dumps(message, allow_nan=False))
                except queue.Empty:
                    pass
                try:
                    ws.settimeout(0.02)
                    raw = ws.recv()
                    if not raw:
                        raise ConnectionError("ROS bridge disconnected")
                    data = json.loads(raw)
                    if data.get("op") == "publish" and data.get("topic") == "/cmd_vel":
                        msg = data["msg"]
                        v, w = float(msg["linear"]["x"]), float(msg["angular"]["z"])
                        if not math.isfinite(v) or not math.isfinite(w):
                            raise ValueError("Nonfinite movement command")
                        with self.lock:
                            self.cmd = (max(-0.2, min(0.4, v)), max(-0.7, min(0.7, w)), time.monotonic())
                            self.commands_received += 1
                    elif data.get("op") == "publish" and data.get("topic") == "/copresence/robot_command":
                        try:
                            command = json.loads(data["msg"]["data"])
                            if isinstance(command, dict):
                                self.command_inbox.put_nowait((command, time.monotonic()))
                        except (ValueError, KeyError, queue.Full):
                            pass
                    elif data.get("op") == "status" and data.get("level") == "error":
                        raise RuntimeError(data.get("msg", "rosbridge protocol error"))
                    elif data.get("op") == "publish" and data.get("topic") == "/copresence/navigation_state":
                        state = json.loads(data["msg"]["data"])
                        self.map_seen = bool(state["map_ready"])
                        self.map_to_odom = tuple(state["map_to_odom"])
                    elif data.get("op") == "action_result" and data.get("id") == self.goal_id:
                        self.goal_result = data
                    elif data.get("op") == "action_feedback" and data.get("id") == self.goal_id:
                        self.goal_feedback = data.get("values")
                except self.websocket.WebSocketTimeoutException:
                    continue
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.connected = False
            with self.lock:
                self.cmd = (0.0, 0.0, 0.0)
            if ws:
                ws.close()

    def world_pose(self, x, y, yaw):
        # ROS +Y is left; UE +Y is right. Initial heading includes the mesh offset.
        c, s = math.cos(self.initial_heading), math.sin(self.initial_heading)
        return (self.origin[0] + 100 * (c*x + s*y),
                self.origin[1] + 100 * (s*x - c*y),
                self.origin[2]), math.degrees(self.initial_heading - yaw) - self.front_yaw

    def world_goal(self, x_cm, y_cm, heading_degrees):
        """Convert a scene landmark through odometry and the latest SLAM correction."""
        dx, dy = (x_cm-self.origin[0])/100, (y_cm-self.origin[1])/100
        c, s = math.cos(self.initial_heading), math.sin(self.initial_heading)
        ox, oy = c*dx+s*dy, s*dx-c*dy
        tx, ty, angle = self.map_to_odom
        c, s = math.cos(angle), math.sin(angle)
        return (tx+c*ox-s*oy, ty+s*ox+c*oy,
                angle+self.initial_heading-math.radians(heading_degrees))

    def navigate_world(self, x_cm, y_cm, heading_degrees):
        return self.navigate_to(*self.world_goal(x_cm, y_cm, heading_degrees))

    def carry(self, actor):
        """Add an item at its actual current pose to the rigid cargo model."""
        self.release(actor)
        p, r = actor.get_actor_location().to_tuple(), actor.get_actor_rotation()
        base = self.robot.get_actor_location().to_tuple()
        delta = math.radians(self.robot.get_actor_rotation().yaw-self.initial_actor_yaw)
        c, s = math.cos(delta), math.sin(delta)
        dx, dy = p[0]-base[0], p[1]-base[1]
        self.parts.append((actor, (c*dx+s*dy, -s*dx+c*dy, p[2]-base[2]),
                           self.u.Rotator(pitch=r.pitch, yaw=r.yaw-math.degrees(delta), roll=r.roll)))
        self.ignore.append(actor)

    def release(self, actor):
        self.parts = [p for p in self.parts if p[0] != actor]
        self.ignore = [a for a in self.ignore if a != actor]

    def sequencer_playing(self):
        # The dedicated human sequence never binds a robot. Reject other playback.
        E=self.u.LevelSequenceEditorBlueprintLibrary
        seq=E.get_current_level_sequence()
        return E.is_playing() and (not seq or seq.get_path_name().split('.')[0]!='/Game/Scans/Animations/LS_RoomHuman')

    def collision_clear(self, start, dest):
        u = self.u
        hit = u.SystemLibrary.capsule_trace_single(
            self.world, u.Vector(start[0], start[1], start[2]+28.5),
            u.Vector(dest[0], dest[1], dest[2]+28.5), 17.5, 28.0,
            u.TraceTypeQuery.TRACE_TYPE_QUERY1, True, self.ignore, u.DrawDebugTrace.NONE, True)
        return hit is None or not hit.to_tuple()[0]

    def supported_height(self, x, y, current_z):
        # Provisional 2 cm threshold capability. The body capsule has the same
        # foot clearance; a short downward support ray follows the actual floor.
        # This is kinematic floor following, not tire/suspension physics.
        u = self.u
        hit = u.SystemLibrary.line_trace_single(
            self.world, u.Vector(x, y, current_z+2.0), u.Vector(x, y, current_z-2.0),
            u.TraceTypeQuery.TRACE_TYPE_QUERY1, True, self.ignore, u.DrawDebugTrace.NONE, True)
        if hit is not None:
            row = hit.to_tuple()
            if row[0] and row[7].z > .9:
                return row[5].z
        return current_z

    def poll_controls(self):
        for _ in range(8):
            try:
                command, received = self.command_inbox.get_nowait()
            except queue.Empty:
                break
            if time.monotonic() - received < 2:
                self.mission.command(command)
        p=Path(self.u.Paths.project_dir())/'Saved/ScanRobotLab/command.json'
        if not p.exists():return
        data=json.loads(p.read_text())
        if data.get('id')==self.last_ui_command:return
        self.last_ui_command=data.get('id')
        if data['action']=='stop':
            self.mission.stop()
        elif data['action']=='go':
            try:
                point=self.rig.layout['destinations'][data['destination']]
                self.mission.start_route('manual', [(data['destination'], (*point,90))])
                self.rig.error=None
            except Exception as e:self.rig.error=str(e)

    def tick(self, dt):
        try:
            u = self.u
            self.poll_controls()
            self.mission.tick()
            if getattr(self.rig,'ros_owner',None) is not self:raise RuntimeError('Robot controller ownership changed')
            if u.get_editor_subsystem(u.UnrealEditorSubsystem).get_editor_world() != self.world:
                raise RuntimeError("World changed; bridge requires explicit restart")
            if self.sequencer_playing():
                raise RuntimeError("Sequencer started; ROS navigation stopped to avoid competing control")
            if "-COPRESENCESOLO" not in u.SystemLibrary.get_command_line().upper():
                raise RuntimeError("Multi-User disconnected")
            dt = max(0.0, min(float(dt), 0.05))
            self.sim_time += dt
            with self.lock:
                v, w, received = self.cmd
            fresh = self.connected and not self.operator_stop and time.monotonic() - received < 0.3
            if not fresh:
                self.v = self.w = 0.0  # Watchdog overrides smoothing.
            else:
                self.v += max(-0.5*dt, min(0.4*dt, v-self.v))
                self.w += max(-1.0*dt, min(1.0*dt, w-self.w))
            if abs(self.v) + abs(self.w) > 1e-5:
                nx, ny, nt = integrate(self.x, self.y, self.yaw, self.v, self.w, dt)
                start = self.robot.get_actor_location().to_tuple()
                dest, angle = self.world_pose(nx, ny, nt)
                dest = (dest[0], dest[1], self.supported_height(dest[0], dest[1], start[2]))
                clear = self.collision_clear(start, dest)
                if not clear:
                    # A vertical circular capsule is invariant under yaw. Stop
                    # translation at contact but retain Nav2's in-place recovery
                    # rotation; blocking both prevents the robot turning away.
                    self.v = 0.0
                    self.collision_stops += 1
                    nx, ny, nt = self.x, self.y, self.yaw + self.w*dt
                    dest, angle = self.world_pose(nx, ny, nt)
                    dest = (dest[0], dest[1], start[2])
                if clear or abs(self.w) > 1e-5:
                    if not self.transaction:
                        u.SystemLibrary.begin_transaction("CoPresence", u.Text("Nav2 robot motion"), self.robot)
                        u.SystemLibrary.transact_object(self.robot)
                        u.SystemLibrary.transact_object(self.robot.root_component)
                        for part, _, _ in self.parts:
                            u.SystemLibrary.transact_object(part)
                            u.SystemLibrary.transact_object(part.root_component)
                        self.transaction = True
                    self.robot.set_actor_location_and_rotation(u.Vector(*dest), u.Rotator(pitch=0, yaw=angle, roll=0), False, False)
                    delta = math.radians(angle-self.initial_actor_yaw)
                    c, s = math.cos(delta), math.sin(delta)
                    for part, offset, rotation in self.parts:
                        part.set_actor_location_and_rotation(
                            u.Vector(dest[0]+c*offset[0]-s*offset[1],
                                     dest[1]+s*offset[0]+c*offset[1], dest[2]+offset[2]),
                            u.Rotator(pitch=rotation.pitch, yaw=rotation.yaw+math.degrees(delta),
                                      roll=rotation.roll), False, False)
                    if self.sim_time - self.last_snapshot >= 0.1:
                        u.SystemLibrary.snapshot_object(self.robot.root_component)
                        for part, _, _ in self.parts:
                            u.SystemLibrary.snapshot_object(part.root_component)
                        self.last_snapshot = self.sim_time
                    self.distance += abs(self.v)*dt
                    self.x, self.y, self.yaw = nx, ny, nt
                    d=self.rig.drive;d.x=dest[0];d.y=dest[1];d.yaw=math.radians(angle)
                    d.left+=(self.v*100-self.w*11.65)*dt/3.575;d.right+=(self.v*100+self.w*11.65)*dt/3.575
                    d.state='ros2_navigation';self.rig.pose()
            elif self.transaction:
                u.SystemLibrary.snapshot_object(self.robot.root_component)
                u.SystemLibrary.end_transaction()
                self.transaction = False
            if self.sim_time >= self.next_scan and self.connected:
                self.next_scan = self.sim_time + 0.1
                self.publish_observations()
                if self.goal_result and self.goal_result.get("status")==4:self.rig.drive.state="arrived"
                self.rig.write()
            if self.patrol and self.goal_result is not None:
                if self.goal_result.get("status") != 4 or not self.goal_result.get("result"):
                    self.patrol = None
                    raise RuntimeError("Nav2 patrol goal failed; inspect goal_result")
                if self.patrol_next_time is None:
                    self.patrol_completed += 1
                    self.patrol_next_time = self.sim_time + 2.0
                elif self.sim_time >= self.patrol_next_time:
                    self.patrol_index = (self.patrol_index + 1) % len(self.patrol)
                    self.navigate_to(*self.patrol[self.patrol_index])
                    self.patrol_next_time = None
        except Exception as exc:
            self.error = str(exc)
            self.stop()
            self.u.log_error("Nav2 bridge stopped: " + self.error)

    def publish_observations(self):
        u = self.u
        now = stamp(self.sim_time)
        header = {"stamp": now, "frame_id": "odom"}
        q = quaternion(self.yaw)
        position = {"x": self.x, "y": self.y, "z": 0.0}
        twist = {"linear": {"x": self.v, "y": 0.0, "z": 0.0},
                 "angular": {"x": 0.0, "y": 0.0, "z": self.w}}
        tf = {"header": header, "child_frame_id": "base_link",
              "transform": {"translation": position, "rotation": q}}
        laser_tf = {"header": {"stamp": now, "frame_id": "base_link"}, "child_frame_id": "laser",
                    "transform": {"translation": {"x": 0.0, "y": 0.0, "z": 0.25}, "rotation": quaternion(0)}}
        pose = self.robot.get_actor_location().to_tuple()
        start = u.Vector(pose[0], pose[1], pose[2]+25.0)
        amin, amax, count, rmax = -math.pi, math.pi, 361, 12.0
        increment = (amax-amin)/(count-1)
        ranges = []
        heading = math.radians(self.robot.get_actor_rotation().yaw + self.front_yaw)
        for i in range(count):
            angle = heading-(amin+i*increment)
            dest = u.Vector(start.x+1200*math.cos(angle), start.y+1200*math.sin(angle), start.z)
            hit = u.SystemLibrary.line_trace_single(self.world, start, dest, u.TraceTypeQuery.TRACE_TYPE_QUERY1,
                                                    True, self.ignore, u.DrawDebugTrace.NONE, True)
            if hit is not None and hit.to_tuple()[0]:
                distance = hit.to_tuple()[3] / 100.0
                ranges.append(max(0.05, min(rmax, distance)))
            else:
                ranges.append(rmax)
        scan = {"header": {"stamp": now, "frame_id": "laser"}, "angle_min": amin, "angle_max": amax,
                "angle_increment": increment, "time_increment": 0.0, "scan_time": 0.1,
                "range_min": 0.05, "range_max": rmax, "ranges": ranges, "intensities": []}
        messages = []
        for topic, kind, msg in [
            ("/clock", "rosgraph_msgs/msg/Clock", {"clock": now}),
            ("/tf", "tf2_msgs/msg/TFMessage", {"transforms": [tf, laser_tf]}),
            ("/odom", "nav_msgs/msg/Odometry", {"header": header, "child_frame_id": "base_link",
                "pose": {"pose": {"position": position, "orientation": q}, "covariance": [0.0]*36},
                "twist": {"twist": twist, "covariance": [0.0]*36}}),
            ("/scan", "sensor_msgs/msg/LaserScan", scan),
            ("/copresence/robot_telemetry", "std_msgs/msg/String", {"data": json.dumps({
                "mission": self.mission.status() if hasattr(self, "mission") else None,
                "commands_received": self.commands_received,
                "scan_batches_queued": self.scans_sent,
                "collision_stops": self.collision_stops,
                "distance_m": self.distance,
                "applied_linear_mps": self.v,
                "applied_angular_rps": self.w,
                "operator_stop": getattr(self, "operator_stop", False),
                "goal_id": self.goal_id,
                "goal_result": self.goal_result,
                "goal_feedback": self.goal_feedback,
                "error": self.error,
            }, allow_nan=False)}),
        ]:
            messages.extend([{"op": "advertise", "topic": topic, "type": kind},
                             {"op": "publish", "topic": topic, "msg": msg}])
        try:
            self.outbox.put_nowait(messages)
            self.scans_sent += 1
        except queue.Full:
            pass

    def status(self):
        return {"connected": self.connected, "error": self.error, "commands_received": self.commands_received,
                "scan_batches_queued": self.scans_sent, "distance_m": self.distance,
                "odom": [self.x, self.y, self.yaw], "collision_stops": self.collision_stops,
                "stopped": self.stopping.is_set(), "map_seen": self.map_seen,
                "patrol_active": bool(self.patrol), "patrol_completed": self.patrol_completed,
                "goal_result": self.goal_result, "goal_feedback": self.goal_feedback}

    def start_patrol(self, waypoints):
        """Repeat map-frame Nav2 goals; Nav2 computes every path and velocity."""
        if self.patrol:
            raise RuntimeError("Patrol already active")
        points = [tuple(float(v) for v in p) for p in waypoints]
        if len(points) < 2 or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
            raise ValueError("Provide at least two finite (x, y, yaw) waypoints")
        goal = self.navigate_to(*points[0])
        self.patrol = points
        self.patrol_index = self.patrol_completed = 0
        self.patrol_next_time = None
        return goal

    def navigate_to(self, x, y, yaw=0.0):
        """Send a map-frame goal through the real Nav2 action server."""
        if not self.connected or not self.map_seen:
            raise RuntimeError("Wait for a connection and a SLAM map before sending a goal")
        if self.goal_id and self.goal_result is None:
            raise RuntimeError("A goal is already pending")
        if not all(math.isfinite(float(v)) for v in (x, y, yaw)):
            raise ValueError("Goal must be finite")
        self.goal_id = "copresence-" + str(time.time_ns())
        self.goal_result = self.goal_feedback = None
        self.requests.put_nowait({
            "op": "send_action_goal", "action": "/navigate_to_pose",
            "action_type": "nav2_msgs/action/NavigateToPose", "id": self.goal_id,
            "feedback": True,
            "args": {"pose": {"header": {"stamp": stamp(self.sim_time), "frame_id": "map"},
                             "pose": {"position": {"x": float(x), "y": float(y), "z": 0.0},
                                      "orientation": quaternion(float(yaw))}},
                     "behavior_tree": ""},
        })
        return self.goal_id

    def stop(self):
        self.patrol = None
        self.stopping.set()
        self.connected = False
        self.v = self.w = 0.0
        if self.handle is not None:
            self.u.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        if self.transaction:
            try:
                if self.u.get_editor_subsystem(self.u.UnrealEditorSubsystem).get_editor_world() == self.world:
                    self.u.SystemLibrary.snapshot_object(self.robot.root_component)
            except Exception:
                # Save/reload may invalidate actor wrappers even in the same world.
                # Stopping must still release the callback and transaction.
                pass
            finally:
                self.u.SystemLibrary.end_transaction()
                self.transaction = False
        # Keep the old controller paused: resuming it is an explicit operator action.


def start(robot_label="TB4_turtlebot4_body", url="ws://100.69.88.8:9094",
          front_yaw_degrees=0.0, pause_existing=False):
    global _instance
    if _instance is not None and not _instance.stopping.is_set():
        raise RuntimeError("Nav2 bridge already started")
    _instance = Bridge(robot_label, url, front_yaw_degrees, pause_existing)
    return _instance.status()


def stop():
    if _instance:
        _instance.stop()
        return _instance.status()
