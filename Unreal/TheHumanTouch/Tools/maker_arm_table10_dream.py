"""Table 10 Dream runtime in the editor world, for the VR cup handover.

VR Mode shows the editor world, so this runs there, not in Simulate. It talks to
DreamSim/table10_dream.py through files in Saved/Table10Dream/:

  need_observation  runner -> here: capture the three Table 10 views; reply in observation.json
                    (same packet format as the fleet runtime: frames video.overhead/wrist_l/wrist_r,
                    state q_left/q_right/gap_left_m/gap_right_m)
  command.json      runner -> here: {seq, report}. report is an action JSON {action: {q_l_chunk, q_r_chunk}},
                    whose rows are played at 20 Hz. The reply goes to playback.json {seq, done, error, q_*, gap_*}.
  cup.json          here -> runner: cup position (table-local cm), height above the tabletop, tilt, held_by

Arms are posed by forward kinematics without transactions, as maker_arm_real_link does. The link recovers
those poses, so in drive mode the real arms follow. The editor world has no physics, so the cup is carried
kinematically: it attaches when a closing jaw squeezes it with the cup body between the pads, and it is set
down upright on the table when the jaw opens. Results must say "editor-world kinematic carry".

    import maker_arm_table10_dream as t10; t10.start(directory); t10.status(); t10.stop()
"""
import base64
import json
import math
import time
from pathlib import Path

import unreal
from copresence_call import call_camera

import maker_arm_real_mapping as mapping
from maker_arm_real_link import ARM_PREFIX, TABLE10_BASES
from maker_arm_station import MOUNT_YAW, VISUAL_LINKS
from yam_kinematics import ue_pose

PREFIX = 'MakerInteraction_T09__'
# The object to hand over: a beer can (Engine cylinder, 6.6 x 12.2 cm) or the drinking cup. Looked up by
# label each tick, so the VR session can swap it without restarting this runtime.
OBJECTS = {
    PREFIX + 'HandoverCan': {'noun': 'can', 'radius_cm': (3.3, 3.3), 'half_height_cm': 6.1, 'rest_z': 81.1},
    PREFIX + 'HandoverCup': {'noun': 'cup', 'radius_cm': (3.0, 4.0), 'half_height_cm': 6.0, 'rest_z': 81.0},
}
CAMERAS = {'overhead': PREFIX + 'MakerCamera_Overview', 'wrist_l': PREFIX + 'MakerCamera_LeftWrist',
           'wrist_r': PREFIX + 'MakerCamera_RightWrist'}
OFFSET = (2460.0, 0.0, 0.0)          # Table 10 grid offset (maker_arm_grid.offset(9))
ACTION_HZ = 20.0
SIDES = ('left', 'right')
# Careful motion: no joint moves more than this between two 20 Hz rows (30 deg/s); faster steps in a
# chunk (Dream lifts peaked at 113-168 deg/s on 2026-11-08) are spread over more rows, for both arms
# together so two-arm timing (handovers) holds
MAX_STEP_RAD = math.radians(1.5)
# While idle, the arm state is read back from the actors this often, so something else moving them
# (the VR wrist handle, the real-arm link) doesn't leave this runtime planning from stale joints
RESYNC_S = 0.5
_rt = None


def _write(path, value):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def _width_m(spec, z_local_cm):
    """Outer width at a height on the object (centred pivot): a straight can, or the cup's 3 -> 4 cm taper."""
    h = spec['half_height_cm']
    z = max(-h, min(h, z_local_cm))
    bottom, top = spec['radius_cm']
    return 2 * (bottom + (top - bottom) * (z + h) / (2 * h)) / 100.0


class Runtime:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        found = {a.get_actor_label(): a for a in unreal.GameplayStatics.get_all_actors_of_class(self.world, unreal.Actor)}
        self.arms = {(s, l): found[ARM_PREFIX + s + '_' + l] for s in SIDES for l in VISUAL_LINKS}
        self.cup, self.spec, self.label = None, None, None
        self._find_object(found)
        self.sources = {role: found[label] for role, label in CAMERAS.items()}
        self.q, self.gap = {}, {}
        for side in SIDES:
            poses = {l: self._pose(self.arms[(side, l)]) for l in VISUAL_LINKS}
            q, gap, _ = mapping.extract(poses, MOUNT_YAW[side], TABLE10_BASES[side], .1)
            self.q[side], self.gap[side] = q, gap
        self.motion, self.seq, self.held, self.rel, self.grip_gap = None, None, None, None, 1.0
        # Per side: squeezing the cup while the other arm holds it (a handover in progress)
        self.squeezing = {side: False for side in SIDES}
        self._resume_hold()
        self.command_mtime, self.error = None, None
        # A command left from before this start was already played: replaying it from today's pose would
        # repeat its moves out of place (a restart re-ran a handover and dropped the can, 2026-11-08 16:06)
        try:
            self.command_mtime = (self.dir / 'command.json').stat().st_mtime_ns
            self.seq = json.loads((self.dir / 'command.json').read_text()).get('seq')
        except (OSError, ValueError):
            pass
        self.captures = {}
        if call_camera._locally(self._create_cameras) is None:
            raise RuntimeError('Start the Table 10 Dream runtime outside a transaction')
        self.callback = unreal.register_slate_post_tick_callback(self._tick)

    @staticmethod
    def _alive(actor):
        # A deleted actor's Python wrapper makes is_valid itself raise ("ObjectInstance is null"); treat it as gone
        # instead of failing every tick (seen 2026-11-08 after the VR session replaced the can).
        try:
            return actor is not None and unreal.SystemLibrary.is_valid(actor)
        except Exception:
            return False

    def _find_object(self, found=None):
        if self._alive(self.cup):
            return
        if found is None:
            found = {a.get_actor_label(): a for a in unreal.GameplayStatics.get_all_actors_of_class(self.world, unreal.StaticMeshActor)
                     if a.get_actor_label() in OBJECTS}
        self.cup = self.spec = self.label = None
        self.held = None
        for label, spec in OBJECTS.items():  # can first
            if label in found:
                self.cup, self.spec, self.label = found[label], spec, label
                return

    def _resume_hold(self):
        """After a restart of this runtime, keep holding the cup that a gripper still has between its jaws."""
        side = ((json.loads((self.dir / 'cup.json').read_text()) if (self.dir / 'cup.json').exists() else {}) or {}).get('held_by')
        if side not in SIDES or self.cup is None:
            return
        gc = self._grasp_center(side, self.q[side], self.gap[side])
        c = self.cup.get_actor_location()
        if math.hypot(gc[0] - c.x, gc[1] - c.y) <= 2.5 and abs(gc[2] - c.z) < self.spec['half_height_cm']:
            link = self.arms[(side, 'link_007')]
            self.rel = unreal.MathLibrary.make_relative_transform(self.cup.get_actor_transform(), link.get_actor_transform())
            self.held, self.grip_gap = side, self.gap[side]

    @staticmethod
    def _pose(actor):
        t = actor.get_actor_transform()
        return ([t.translation.x, t.translation.y, t.translation.z], [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w])

    def _create_cameras(self):
        for role, source in self.sources.items():
            target = unreal.new_object(unreal.TextureRenderTarget2D)
            target.set_editor_property('render_target_format', unreal.TextureRenderTargetFormat.RTF_RGBA8)
            target.set_editor_property('target_gamma', 2.2)
            unreal.RenderingLibrary.resize_render_target2d(target, 320, 176)
            fov = source.get_component_by_class(unreal.CameraComponent).field_of_view
            actor, capture = call_camera.spawn_capture_camera(target, source.get_actor_location(), source.get_actor_rotation(), fov)
            actor.set_actor_label('Table10Dream_Capture_' + role)
            for component in actor.get_components_by_class(unreal.PrimitiveComponent):
                component.set_visibility(False, False)
            self.captures[role] = (actor, capture, target)
        return True

    # ---- arms ------------------------------------------------------------------------------
    def _apply(self, side, q, gap):
        for link, (p, r) in mapping.fk_ue(q, gap, MOUNT_YAW[side], TABLE10_BASES[side]).items():
            if (side, link) in self.arms:
                self.arms[(side, link)].set_actor_location_and_rotation(unreal.Vector(*p), unreal.Quat(*r).rotator(), False, True)
        self.q[side], self.gap[side] = list(q), gap

    def _grasp_center(self, side, q, gap):
        frames = mapping.mounted_frames(q, gap, yaw=MOUNT_YAW[side])
        p, _ = ue_pose(frames['grasp_center'])
        base = TABLE10_BASES[side]
        return [p[i] + base[i] for i in range(3)]

    def _contact_gap(self, side, q, requested):
        """Jaws stop on the cup body when it is between the pads (approximate, editor world), also the
        jaws of the arm not holding it, so the other arm can take it in a handover."""
        if self.cup is None:
            return requested
        gc = self._grasp_center(side, q, requested)
        c = self.cup.get_actor_location()
        horizontal = math.hypot(gc[0] - c.x, gc[1] - c.y)
        z_local = gc[2] - c.z
        h = self.spec['half_height_cm']
        if horizontal > 2.5 or not -(h - .5) < z_local < h - .5:
            return requested
        return max(requested, _width_m(self.spec, z_local) - .002)  # 2 mm of squeeze, like the fleet limiter

    def _update_cup(self, side, requested, gap):
        if self.cup is None:
            return
        link = self.arms[(side, 'link_007')]
        squeezing = gap > requested + .003  # the jaw is squeezing the cup body
        other = 'right' if side == 'left' else 'left'
        if self.held == side:
            if requested > self.grip_gap + .005 and self.squeezing.get(other):
                # Handover: the other gripper already squeezes the cup, so it takes it where it is
                other_link = self.arms[(other, 'link_007')]
                self.rel = unreal.MathLibrary.make_relative_transform(self.cup.get_actor_transform(), other_link.get_actor_transform())
                self.held, self.grip_gap = other, self.gap[other]
            elif requested > self.grip_gap + .005:  # jaws opened past the grip: set the cup down upright below
                loc = self.cup.get_actor_location()
                self.cup.set_actor_location_and_rotation(unreal.Vector(loc.x, loc.y, self.spec['rest_z']), unreal.Rotator(0, 0, 0), False, True)
                self.held, self.rel = None, None
            else:
                t = unreal.MathLibrary.compose_transforms(self.rel, link.get_actor_transform())
                self.cup.set_actor_transform(t, False, True)
        elif self.held is None and squeezing:
            self.rel = unreal.MathLibrary.make_relative_transform(self.cup.get_actor_transform(), link.get_actor_transform())
            self.held, self.grip_gap = side, gap
        self.squeezing[side] = squeezing and self.held != side

    # ---- io --------------------------------------------------------------------------------
    def _observe(self):
        if self.motion is None:
            self._resync()
        frames, paths = {}, {}
        for role, (actor, capture, target) in self.captures.items():
            source = self.sources[role]
            actor.set_actor_location_and_rotation(source.get_actor_location(), source.get_actor_rotation(), False, True)
            capture.capture_scene()
            path = self.dir / (role + '.jpg')
            options = unreal.ImageWriteOptions()
            options.set_editor_property('format', unreal.DesiredImageFormat.JPG)
            options.set_editor_property('compression_quality', 90)
            options.set_editor_property('async_', False)
            options.set_editor_property('overwrite_file', True)
            unreal.ImageWriteBlueprintLibrary.export_to_disk(target, str(path), options)
            frames['video.' + role] = base64.b64encode(path.read_bytes()).decode('ascii')
        _write(self.dir / 'observation.json', {
            'scope': 'table10-editor', 'captured_unix': time.time(), 'captured_monotonic': time.monotonic(),
            'frames': frames, 'moving': self.motion is not None,
            'state': {'q_left': self.q['left'], 'q_right': self.q['right'],
                      'gap_left_m': self.gap['left'], 'gap_right_m': self.gap['right']}})

    def _read_command(self):
        path = self.dir / 'command.json'
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if mtime == self.command_mtime:
            return
        self.command_mtime = mtime
        command = json.loads(path.read_text())
        if command.get('seq') == self.seq:
            return
        self.seq = command['seq']
        action = json.loads(Path(command['report']).read_text())['action']
        self._resync()
        rows = self._smooth({'left': action['q_l_chunk'], 'right': action['q_r_chunk']})
        self.motion = {'rows': rows, 'start': time.monotonic(), 'n': max(len(r) for r in rows.values())}

    def _resync(self):
        """Joint state from the arm actors as they are now."""
        for side in SIDES:
            poses = {l: self._pose(self.arms[(side, l)]) for l in VISUAL_LINKS}
            q, gap, _ = mapping.extract(poses, MOUNT_YAW[side], TABLE10_BASES[side], .1)
            self.q[side], self.gap[side] = list(q), gap
        self.synced = time.monotonic()

    def _smooth(self, rows):
        """The chunk's rows, eased in from where the arms are and with no joint step above MAX_STEP_RAD
        (both arms subdivided together). The jaw keeps each row's value during the ease-in, so a grip
        is never loosened by it."""
        n = max(len(r) for r in rows.values())
        rows = {s: list(r) + [r[-1]] * (n - len(r)) for s, r in rows.items()}
        prev = {s: list(self.q[s]) + [rows[s][0][6]] for s in SIDES}
        out = {s: [] for s in SIDES}
        for i in range(n):
            k = max(1, max(math.ceil(max(abs(rows[s][i][j] - prev[s][j]) for j in range(6)) / MAX_STEP_RAD)
                           for s in SIDES))
            for s in SIDES:
                a, b = prev[s], rows[s][i]
                for step in range(1, k + 1):
                    f = step / k
                    out[s].append([a[j] + (b[j] - a[j]) * f for j in range(6)] + [a[6] + (b[6] - a[6]) * f])
                prev[s] = list(b)
        return out

    def _step_motion(self):
        m = self.motion
        index = min(int((time.monotonic() - m['start']) * ACTION_HZ), m['n'] - 1)
        for side in SIDES:
            rows = m['rows'][side]
            row = rows[min(index, len(rows) - 1)]
            q = mapping.clamp(row[:6], mapping.URDF_LIMITS)
            requested = max(0.0, min(mapping.JAW_MAX_M, row[6] * mapping.JAW_MAX_M))
            gap = self._contact_gap(side, q, requested)
            self._apply(side, q, gap)
            self._update_cup(side, requested, gap)
        if index >= m['n'] - 1:
            self.motion = None
            _write(self.dir / 'playback.json', {'seq': self.seq, 'done': True, 'error': None, 'finished_unix': time.time(),
                                                 'q_left': self.q['left'], 'q_right': self.q['right'],
                                                 'gap_left_m': self.gap['left'], 'gap_right_m': self.gap['right']})

    def _cup_status(self):
        if self.cup is None:
            return {'present': False, 'unix': time.time()}
        loc = self.cup.get_actor_location()
        up = self.cup.get_actor_up_vector()
        return {'present': True, 'label': self.label, 'noun': self.spec['noun'], 'rest_z_cm': self.spec['rest_z'],
                'table_local_cm': [loc.x - OFFSET[0], loc.y - OFFSET[1], loc.z - OFFSET[2]],
                'height_above_table_cm': round(loc.z - self.spec['rest_z'], 2), 'held_by': self.held,
                'tilt_degrees': round(math.degrees(math.acos(max(-1, min(1, up.z)))), 1), 'unix': time.time(),
                'scope': 'editor-world kinematic carry'}

    def _tick(self, delta):
        try:
            self._find_object()
            if (self.dir / 'need_observation').exists():
                (self.dir / 'need_observation').unlink()
                self._observe()
            self._read_command()
            if self.motion is not None:
                self._step_motion()
            else:
                if time.monotonic() - getattr(self, 'synced', 0.0) > RESYNC_S:
                    self._resync()
                if self.held in SIDES and self.cup is not None:
                    # Still in hand while idle: it moves with the gripper whatever moves the arm
                    link = self.arms[(self.held, 'link_007')]
                    self.cup.set_actor_transform(unreal.MathLibrary.compose_transforms(self.rel, link.get_actor_transform()), False, True)
            _write(self.dir / 'cup.json', self._cup_status())
            self.error = None
        except Exception as error:
            self.error = repr(error)
            self.motion = None
            unreal.log_warning('Table 10 Dream runtime: ' + self.error)
            _write(self.dir / 'playback.json', {'seq': self.seq, 'done': True, 'error': self.error, 'finished_unix': time.time()})

    def stop(self):
        if self.callback is not None:
            unreal.unregister_slate_post_tick_callback(self.callback)
            self.callback = None
        call_camera._remove([actor for actor, _, _ in self.captures.values()])

    def status(self):
        return {'running': self.callback is not None, 'error': self.error, 'moving': self.motion is not None,
                'held': self.held, 'q_deg': {s: [round(math.degrees(v), 1) for v in self.q[s]] for s in SIDES},
                'cup': self._cup_status()}


def _stop_strays():
    """Stop every running Runtime from this module, including ones orphaned by an importlib.reload
    (a reload resets the module handle, and the old instance's tick callback would keep running)."""
    import gc
    for o in gc.get_objects():
        try:
            if type(o).__name__ == 'Runtime' and type(o).__module__ == __name__ and getattr(o, 'callback', None) is not None:
                o.stop()
        except Exception:
            pass


def start(directory):
    global _rt
    stop()
    _stop_strays()
    _rt = Runtime(directory)
    return _rt.status()


def stop():
    global _rt
    if _rt is not None:
        _rt.stop()
        _rt = None
    return {'running': False}


def status():
    return _rt.status() if _rt is not None else {'running': False}
