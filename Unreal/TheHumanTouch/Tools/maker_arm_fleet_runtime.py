"""One SIE session driving every replica table (maker_arm_fleet booths) at once.

Mirrors maker_arm_runtime's single-table model per table: model joints drive
kinematic CAD links, Chaos moves free station blocks, the contact-limited jaw
and flat-pad / convex-jaw approximations are the same functions. Visual poses
are mirrored into Multi-User at 2 Hz; start poses are restored at teardown.
Every fleet run starts from the replicas' configured initial state.

The host-side fleet worker talks to this module only through files under the
run directory (no MCP call per motion):
  <run>/fleet.json            phase, per-table state, prop evidence, motion
  <run>/Txx/observation.json  latest 3-camera packet + verified state
  <run>/Txx/recording/        numbered JPEG frames for review videos
  <run>/Txx/command.json      {"seq": n, "report": path}  -> launches a chunk
  <run>/Txx/playback.json     motion progress / done / error for that chunk
  <run>/stop                  stops the whole session
Start after the enclosing CoPresence transaction ends (ui_widgets.after).
"""
import base64
import collections
import inspect
import os
import json
import math
import time
import traceback
from pathlib import Path

import unreal
import maker_arm_fleet
from maker_arm_fleet_layout import label, replica_base, normalize_config, PROP_PREFIX, PROP_PREFIXES, JAW_MAX_M
from maker_arm_actions import retime
from maker_arm_trajectory import sample
from maker_arm_contact_stop import contact_gap, actor_boxes
from maker_arm_table_clearance import check_gripper_table
from maker_arm_lift_evidence import update as update_lift_evidence
from maker_arm_model import mounted_frames, ue_pose
from maker_arm_station import BASES, MOUNT_YAW, VISUAL_LINKS

ROLES = maker_arm_fleet.CAMERA_ROLES
GRIPPERS = ('gripper_left', 'gripper_right')
_FLAT = 'flat' in inspect.signature(contact_gap).parameters
_session = None


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def deferred(name, args, result_path):
    """Run a module function on the next editor tick, outside the MCP transaction."""
    from copresence import ui_widgets

    def run():
        try:
            write(result_path, {'ok': True, 'result': globals()[name](*args)})
        except Exception as error:
            write(result_path, {'ok': False, 'error': str(error), 'trace': traceback.format_exc()[-2000:]})
    ui_widgets.after(.2, run)
    return {'scheduled': name}


def measure_fps(seconds, result_path):
    """Sample real editor frame times for `seconds`; writes the result file itself."""
    samples = []
    started = time.monotonic()
    state = {}

    def tick(delta):
        samples.append(delta)
        if time.monotonic() - started >= seconds:
            unreal.unregister_slate_post_tick_callback(state['handle'])
            ordered = sorted(samples)
            write(result_path, {'ok': True, 'result': {
                'frames': len(samples), 'fps': len(samples) / max(1e-6, sum(samples)),
                'median_ms': 1000 * ordered[len(ordered)//2] if ordered else None,
                'p90_ms': 1000 * ordered[int(len(ordered)*.9)] if ordered else None,
                'hitches_over_80ms_per_s': sum(d > .08 for d in samples) / max(1e-6, sum(samples))}})
    state['handle'] = unreal.register_slate_post_tick_callback(tick)
    return {'measuring_seconds': seconds}


def setup_local(config_path):
    return maker_arm_fleet.setup_local(config_path)


def clear_local():
    return maker_arm_fleet.clear(local=True)


class Table:
    def __init__(self, variant, root):
        self.v = variant
        self.index = variant['index']
        self.dir = Path(root) / ('T%02d' % self.index)
        (self.dir / 'recording').mkdir(parents=True, exist_ok=True)
        for name in ('observation.json', 'playback.json', 'command.json'):
            (self.dir / name).unlink(missing_ok=True)
        self.state = dict(variant['initial_state'])
        self.bases = {side: replica_base(self.index, BASES[side]) for side in BASES}
        self.actors = {}
        self.proxies = {}
        self.props = {}
        self.evidence = {}
        self.timers = {}
        self.captures = {}
        self.motion = None
        self.command_seq = 0
        self.frames = 0
        self.next_capture = 0.
        self.pending = None
        self.queue = []
        self.need_obs = True
        self.rec = 0
        self.command_mtime = None
        self.next_poll = 0.
        self.last_progress = 0.
        self.error = None
        self.desired_gap = {}
        self.last_grip_tick = time.monotonic()

    def name(self, source):
        return label(self.index, source)


class Session:
    def __init__(self, config_path, root, fps=2., max_seconds=420, captures_per_tick=1):
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.editor_world = self.editor.get_editor_world()
        self.mirror = None
        from copresence import animation
        if self.editor.get_game_world() is not None or animation.is_running():
            raise RuntimeError('Simulation or shared animation already active')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'stop').unlink(missing_ok=True)
        self.tables = [Table(v, self.root) for v in normalize_config(json.loads(Path(config_path).read_text()))]
        import importlib, maker_arm_passive_preview
        importlib.reload(maker_arm_passive_preview)
        self.passive_preview = maker_arm_passive_preview.PassivePreview([t.index for t in self.tables], self.root)
        self.interval = 1. / fps  # overhead review frames per live table
        self.max_seconds = min(max_seconds, 170)
        self.captures_per_tick = captures_per_tick
        self.phase = 'starting'
        self.error = None
        self.world = None
        self.callback = None
        self.local = []
        self.started = time.monotonic()
        self.last_report = 0.
        self.deltas = collections.deque(maxlen=600)
        self.costs = collections.deque(maxlen=600)
        self.sections = collections.defaultdict(float)
        self.section_ticks = 0
        self.perf = {}
        editor_actors = {a.get_actor_label(): a for a in
                         unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
        for table in self.tables:
            maker_arm_fleet.verify(table.index, table.state, editor_actors)
        self._spawn_locals(editor_actors)
        self.report(force=True)
        self.callback = unreal.register_slate_post_tick_callback(self.tick)
        self.level.editor_play_simulate()

    def _spawn_locals(self, editor_actors):
        """Hidden contact proxies and capture actors; SIE clones them into the runtime world."""
        from copresence import tracking
        from copresence_call import call_camera
        subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        tags = [unreal.Name(tracking.LOCAL_ONLY_TAG), unreal.Name('MakerFleet')]

        def make():
            for table in self.tables:
                for side in BASES:
                    for link in GRIPPERS:
                        source = editor_actors[table.name('MakerArm_'+side+'_'+link)]
                        path = ('/Engine/BasicShapes/Cube' if table.v['pad_contacts'] else
                                '/Game/_Agents/Denys_Mac_Codex/MakerArm/Physics/SM_MakerArm_Collision_'+link)
                        mesh = unreal.load_asset(path)
                        if mesh is None:
                            raise RuntimeError('Missing diagnostic collision mesh ' + path)
                        proxy = subsystem.spawn_actor_from_class(unreal.StaticMeshActor, source.get_actor_location(),
                                                                 source.get_actor_rotation(), False)
                        self.local.append(proxy)
                        proxy.set_actor_label(table.name('FleetProxy_MakerArm_'+side+'_'+link))
                        proxy.set_editor_property('tags', tags)
                        pc = proxy.static_mesh_component
                        pc.set_mobility(unreal.ComponentMobility.MOVABLE)
                        pc.set_static_mesh(mesh)
                        if table.v['pad_contacts']:
                            proxy.set_actor_scale3d(unreal.Vector(.03, .01, .02))
                        pc.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
                        pc.set_visibility(False, False)
                        proxy.set_actor_hidden_in_game(True)
                for role, source_label in ROLES.items():
                    camera = editor_actors[table.name(source_label)]
                    capture = subsystem.spawn_actor_from_class(unreal.SceneCapture2D, camera.get_actor_location(),
                                                               camera.get_actor_rotation(), False)
                    self.local.append(capture)
                    capture.set_actor_label(table.name('FleetCapture_'+role))
                    capture.set_editor_property('tags', tags)
                    for primitive in capture.get_components_by_class(unreal.PrimitiveComponent):
                        primitive.set_visibility(False, False)
                        primitive.set_hidden_in_game(True, False)
            self.passive_preview.spawn(editor_actors, subsystem, tags, self.local)
            return True
        if call_camera._locally(make) is None:
            call_camera._remove(self.local)
            raise RuntimeError('Start the fleet runtime after the enclosing transaction ends')

    # ---- runtime world binding -------------------------------------------------
    def prepare(self, world):
        self.world = world
        found = {}
        for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor):
            name = actor.get_actor_label()
            if name.startswith(('MakerFleet_', 'MakerArm_', 'MakerStation_', 'MakerCamera_', 'FleetProxy_', 'FleetCapture_')):
                if name in found:
                    raise RuntimeError('Duplicate runtime replica actor ' + name)
                found[name] = actor
        material = unreal.new_object(unreal.PhysicalMaterial)
        material.set_editor_property('friction', .8)
        material.set_editor_property('restitution', 0.)
        self.material = material
        for table in self.tables:
            # SIE can restore the saved BlockAll profile despite an editor-only
            # enabled-flag override. Printed tabletop labels are not obstacles.
            sticker = found.get('MakerFleet_T%02d__NumberSticker' % table.index)
            if sticker is not None:
                sticker.static_mesh_component.set_collision_profile_name('NoCollision')
                sticker.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            for side in BASES:
                for link in VISUAL_LINKS:
                    name = table.name('MakerArm_'+side+'_'+link)
                    actor = found.get(name)
                    if actor is None:
                        raise RuntimeError('Missing runtime link ' + name)
                    table.actors[name] = actor
                    c = actor.static_mesh_component
                    c.set_mobility(unreal.ComponentMobility.MOVABLE)
                    c.set_simulate_physics(False)
                    c.set_collision_profile_name('BlockAllDynamic')
                    c.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
                    if link in GRIPPERS:
                        proxy = found.get(table.name('FleetProxy_MakerArm_'+side+'_'+link))
                        if proxy is None:
                            raise RuntimeError('Missing cloned contact proxy on table %d' % table.index)
                        pc = proxy.static_mesh_component
                        pc.set_mobility(unreal.ComponentMobility.MOVABLE)
                        pc.set_collision_profile_name('BlockAllDynamic')
                        pc.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
                        pc.set_phys_material_override(material)
                        c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
                        table.proxies[name] = proxy
            self.initialize_contacts(table)
            prefix = tuple(table.name(p) for p in PROP_PREFIXES)
            for name, actor in found.items():
                if not name.startswith(prefix) or not isinstance(actor, unreal.StaticMeshActor):
                    continue
                c = actor.static_mesh_component
                c.set_mobility(unreal.ComponentMobility.MOVABLE)
                c.set_collision_profile_name('PhysicsActor')
                c.set_phys_material_override(material)
                c.set_mass_override_in_kg('', .05, True)
                c.set_enable_gravity(True)
                c.set_simulate_physics(True)
                if not c.is_simulating_physics():
                    raise RuntimeError('Prop did not enter Chaos simulation: ' + name)
                short = name if table.index == 0 else name.split('__', 1)[1]
                table.props[short] = actor
                table.timers[short] = {}
                z = actor.get_actor_location().z
                table.evidence[short] = {'initial_z_cm': z, 'max_z_cm': z, 'final_z_cm': z, 'simulated': True}
            if table.v['target']['label'] not in table.props:
                raise RuntimeError('Table %d target block is not simulated' % table.index)
            for role, source_label in ROLES.items():
                camera = found.get(table.name(source_label))
                capture_actor = found.get(table.name('FleetCapture_'+role))
                if not isinstance(camera, unreal.CameraActor) or capture_actor is None:
                    raise RuntimeError('Missing runtime camera %s on table %d' % (role, table.index))
                target = unreal.new_object(unreal.TextureRenderTarget2D)
                target.set_editor_property('render_target_format', unreal.TextureRenderTargetFormat.RTF_RGBA8)
                target.set_editor_property('target_gamma', 2.2)
                unreal.RenderingLibrary.resize_render_target2d(target, 320, 176)
                capture = capture_actor.get_component_by_class(unreal.SceneCaptureComponent2D)
                capture.set_editor_property('texture_target', target)
                capture.set_editor_property('capture_source', unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
                capture.set_editor_property('capture_every_frame', False)
                capture.set_editor_property('capture_on_movement', False)
                capture.set_editor_property('always_persist_rendering_state', True)
                capture.set_editor_property('fov_angle', camera.get_component_by_class(unreal.CameraComponent).field_of_view)
                # Same near plane as maker_arm_observer: wrist targets are centimetres away.
                capture.set_editor_property('override_custom_near_clipping_plane', True)
                capture.set_editor_property('custom_near_clipping_plane', 1.0)
                table.captures[role] = {'source': camera, 'actor': capture_actor, 'capture': capture, 'target': target}
            maker_arm_fleet.verify(table.index, table.state, table.actors)
        self.passive_preview.bind(found)
        from maker_arm_runtime_mirror import RuntimeMirror
        mirror_labels = [name for table in self.tables for name in table.actors]
        mirror_labels += [table.name(name) for table in self.tables for name in table.props]
        self.mirror = RuntimeMirror(world, mirror_labels, max_seconds=180, hz=2, editor_world=self.editor_world)
        # Stagger first captures so the per-tick render cost stays bounded.
        now = time.monotonic()
        for i, table in enumerate(self.tables):
            table.next_capture = now + i * self.interval / max(1, len(self.tables))
        self.phase = 'ready'
        self.report(force=True)

    def initialize_contacts(self, table):
        """Place both jaw proxies at their true contact offsets before any cup simulates."""
        for side in BASES:
            self.apply_row(table, side, table.state['q_'+side] +
                           [table.state['gap_'+side+'_m'] / JAW_MAX_M])
        for proxy in table.proxies.values():
            proxy.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)

    # ---- per-table control -----------------------------------------------------
    def launch(self, table, report_path):
        if table.motion is not None:
            raise RuntimeError('Trajectory already active')
        data = json.loads(Path(report_path).read_text())
        for side in BASES:
            q = data['state']['q_'+side]
            if max(abs(a-b) for a, b in zip(q, table.state['q_'+side])) > 1e-6:
                raise RuntimeError('Action was planned from a stale pose')
        maker_arm_fleet.verify(table.index, table.state, table.actors)
        chunks, timing = retime(data['action'], data['state'], 20, execution_rows=data.get('execution_rows'))
        table.motion = {'chunks': chunks, 'timing': timing, 'start': time.monotonic(), 'index': -1}
        self.motion_report(table, False)

    def motion_report(self, table, done, error=None, progress=False):
        now = time.monotonic()
        if progress and now - table.last_progress < .2:
            return
        table.last_progress = now
        m = table.motion
        write(table.dir/'playback.json', {'done': done, 'error': error, 'seq': table.command_seq,
                                          'finished_unix': time.time() if done else None,
                                          'index': m['index'] if m else -1,
                                          'rows': len(m['chunks']['left']) if m else 0,
                                          'timing': m['timing'] if m else None, **table.state})

    def poll_command(self, table, now):
        """10 Hz file polling; command.json is read only when its mtime changes."""
        if now < table.next_poll:
            return
        table.next_poll = now + .1
        request = table.dir/'need_observation'
        try:
            os.unlink(request)
            table.need_obs = True
        except FileNotFoundError:
            pass
        path = table.dir/'command.json'
        try:
            mtime = os.stat(path).st_mtime_ns
        except FileNotFoundError:
            return
        if mtime == table.command_mtime:
            return
        try:
            command = json.loads(path.read_text())
        except ValueError:
            return
        table.command_mtime = mtime
        if command.get('seq', 0) <= table.command_seq:
            return
        table.command_seq = command['seq']
        try:
            self.launch(table, command['report'])
        except Exception as error:
            table.motion = None
            write(table.dir/'playback.json', {'done': True, 'error': str(error), 'seq': table.command_seq})

    def step_motion(self, table, now):
        m = table.motion
        for side in BASES:
            base = table.bases[side]
            row, index = sample(m['chunks'][side], now-m['start'], m['timing']['action_hz'])
            table.desired_gap[side] = row[6]*JAW_MAX_M
            self.apply_row(table, side, row)
        m['index'] = index
        if index == len(m['chunks']['left'])-1:
            self.motion_report(table, True)
            table.motion = None
            table.need_obs = True
        else:
            self.motion_report(table, False, progress=True)

    def apply_row(self, table, side, row, jaws_only=False):
        base = table.bases[side]
        requested = row[6]*JAW_MAX_M
        frames = mounted_frames(row[:6], requested, yaw=MOUNT_YAW[side])
        previous = table.state['gap_'+side+'_m']
        if requested < previous:
            # Contact limiting only matters while a jaw closes (contact_gap
            # returns early otherwise); skip the per-prop bound transforms.
            boxes = actor_boxes(table.props, frames['link_007'], base)
            if _FLAT:
                gap, contacts = contact_gap(requested, previous, boxes, flat=table.v['pad_contacts'])
            else:
                gap, contacts = contact_gap(requested, previous, boxes)
        else:
            gap, contacts = requested, []
        if jaws_only and abs(gap-previous) < 1e-8:
            return
        if gap != requested:
            frames = mounted_frames(row[:6], gap, yaw=MOUNT_YAW[side])
        bounds = {}
        if not table.v['pad_contacts']:
            for jaw in GRIPPERS:
                proxy = table.proxies[table.name('MakerArm_'+side+'_'+jaw)]
                low, high = proxy.static_mesh_component.get_local_bounds()
                bounds[jaw] = ([low.x, low.y, low.z], [high.x, high.y, high.z])
        check_gripper_table(frames, base, maker_arm_fleet.offset(table.index),
                            flat=table.v['pad_contacts'], collision_bounds_cm=bounds)
        table.state['q_'+side] = row[:6]
        table.state['gap_'+side+'_m'] = gap
        table.state['contact_stop_'+side] = contacts
        for link, t in frames.items():
            if link not in VISUAL_LINKS or (jaws_only and link not in GRIPPERS):
                continue
            name = table.name('MakerArm_'+side+'_'+link)
            p, q = ue_pose(t)
            p = [p[i]+base[i] for i in range(3)]
            table.actors[name].set_actor_location_and_rotation(unreal.Vector(*p), unreal.Quat(*q).rotator(), False, False)
            if name in table.proxies:
                if table.v['pad_contacts']:
                    # 3x1x2 cm flat pad, inner face on each prismatic zero plane (as maker_arm_runtime).
                    off = [0, .005 if link == 'gripper_left' else -.005, -.17]
                    pad = [r[:] for r in t]
                    for i in range(3):
                        pad[i][3] += sum(t[i][j]*off[j] for j in range(3))
                    pp, pq = ue_pose(pad)
                    pp = [pp[i]+base[i] for i in range(3)]
                else:
                    pp, pq = p, q
                table.proxies[name].set_actor_location_and_rotation(unreal.Vector(*pp), unreal.Quat(*pq).rotator(), False, False)

    def hold_grip(self, table, now):
        from maker_arm_grip_hold import next_gap
        dt = min(.05, max(0., now-table.last_grip_tick))
        for side, desired in table.desired_gap.items():
            current = table.state['gap_'+side+'_m']
            gap = next_gap(current, desired, dt)
            if gap < current:
                self.apply_row(table, side, table.state['q_'+side]+[gap/JAW_MAX_M], jaws_only=True)

    def measure_props(self, table, now):
        for name, actor in table.props.items():
            c = actor.static_mesh_component
            c.wake_all_rigid_bodies()
            p = actor.get_actor_location()
            v = c.get_physics_linear_velocity()
            d = maker_arm_fleet.offset(table.index)
            update_lift_evidence(table.evidence[name], table.timers[name], [p.x, p.y, p.z], [v.x, v.y, v.z], now)
            if name in ('MakerStation_Prop_Cup', 'MakerStation_Prop_Cup_02', 'MakerStation_Prop_Cup_03', 'MakerStation_Prop_Cup_04', 'MakerStation_Prop_Cup_05'):
                from maker_arm_cup_evidence import update as update_cup
                up = actor.get_actor_up_vector()
                tilt = math.degrees(math.acos(max(-1., min(1., up.z))))
                update_cup(table.evidence[name], table.timers[name], tilt, now)
            parent = actor.get_attach_parent_actor()
            table.evidence[name].update({'table_local_cm': [p.x-d[0], p.y-d[1], p.z-d[2]],
                                         'gravity_enabled': c.is_gravity_enabled(),
                                         'physics_simulated': c.is_simulating_physics(),
                                         'attached_to': parent.get_actor_label() if parent else None})

    def _export(self, item, path):
        item['actor'].set_actor_location_and_rotation(item['source'].get_actor_location(),
                                                      item['source'].get_actor_rotation(), False, True)
        item['capture'].capture_scene()
        path.unlink(missing_ok=True)
        options = unreal.ImageWriteOptions()
        options.set_editor_property('format', unreal.DesiredImageFormat.JPG)
        options.set_editor_property('compression_quality', 90)
        options.set_editor_property('async_', True)
        options.set_editor_property('overwrite_file', True)
        unreal.ImageWriteBlueprintLibrary.export_to_disk(item['target'], str(path), options)

    def capture(self, table, now):
        """Begin a 3-view policy packet; the views render on consecutive frames."""
        maker_arm_fleet.verify(table.index, table.state, table.actors)
        table.need_obs = False
        table.pending = {'sequence': table.frames + 1, 'paths': {}, 'monotonic': now, 'unix': time.time(),
                         'moving': table.motion is not None,
                         'state': {k: list(table.state[k]) if isinstance(table.state[k], list) else table.state[k]
                                   for k in ('q_left', 'q_right', 'gap_left_m', 'gap_right_m')}}
        table.queue = list(table.captures)
        self.capture_role(table)

    def capture_role(self, table):
        role = table.queue.pop(0)
        path = table.dir/'recording'/('%s_%06d.jpg' % (role, table.pending['sequence']))
        self._export(table.captures[role], path)
        table.pending['paths'][role] = path

    def record(self, table, now):
        """Budgeted overhead frame during motion, inference waits and held poses."""
        table.rec += 1
        name = 'overhead_rec_%06d.jpg' % table.rec
        self._export(table.captures['overhead'], table.dir/'recording'/name)
        with (table.dir/'recording'/'overhead_timeline.jsonl').open('a') as stream:
            stream.write(json.dumps({'file': name, 'capture_requested_unix': time.time(),
                                     'monotonic': now, 'moving': table.motion is not None,
                                     'command_seq': table.command_seq}) + '\n')

    def publish(self, table, now):
        """Write the observation once every JPEG of the pending capture is complete."""
        p = table.pending
        frames = {}
        for role, path in p['paths'].items():
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                data = b''
            if len(data) < 4 or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                if now - p['monotonic'] > 2:
                    raise RuntimeError('camera export timed out: ' + role)
                return False
            frames['video.'+role] = base64.b64encode(data).decode('ascii')
        table.pending = None
        table.frames = p['sequence']
        write(table.dir/'observation.json', {'scope': 'fleet-runtime', 'table': table.index, 'sequence': p['sequence'],
                                            'captured_monotonic': p['monotonic'], 'captured_unix': p['unix'],
                                            'frames': frames, 'state': p['state'], 'moving': p['moving']})
        return True

    def report(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_report < .1:
            return
        self.last_report = now
        write(self.root/'fleet.json', {
            'phase': self.phase, 'error': self.error, 'sampled_unix': time.time(),
            'elapsed_seconds': now - self.started, 'task_success_verified': False, 'editor_perf': self.perf,
            'scope': 'authoritative local Chaos physics; shared visual poses mirrored at 2 Hz; approximate contacts',
            'tables': {('T%02d' % t.index): {'name': t.v['name'], 'error': t.error, 'frames': t.frames,
                                              'moving': t.motion is not None, 'command_seq': t.command_seq,
                                              'contact_mode': 'approximate flat pads' if t.v['pad_contacts'] else 'approximate convex jaws',
                                              'target': t.v['target']['label'], 'state': t.state, 'props': t.evidence}
                       for t in self.tables}})

    # ---- tick ------------------------------------------------------------------
    def tick(self, delta):
        began = time.perf_counter()
        try:
            self._tick(delta)
        finally:
            if self.phase == 'ready':
                self.costs.append(time.perf_counter() - began)

    def _tick(self, delta):
        try:
            now = time.monotonic()
            world = self.editor.get_game_world()
            if self.phase == 'starting':
                if world is None:
                    if now - self.started > 20:
                        raise TimeoutError('SIE startup timed out')
                    return
                self.prepare(world)
                return
            if self.phase == 'ending':
                if world is not None:
                    if now - self.ending_started > 20:
                        raise TimeoutError('SIE exit timed out')
                    return
                from copresence_call import call_camera
                call_camera._remove(self.local)
                self.phase = 'stopped'
                self.report(force=True)
                self.close_callback()
                return
            if self.phase != 'ready':
                return
            self.deltas.append(delta)
            if len(self.deltas) >= 30:
                recent = list(self.deltas)
                total = sum(recent)
                costs = list(self.costs) or [0.]
                self.perf = {'fps': len(recent)/max(1e-6, total), 'worst_ms': 1000*max(recent),
                             'hitches_over_80ms_per_s': sum(d > .08 for d in recent)/max(1e-6, total),
                             'controller_ms_avg': 1000*sum(costs)/len(costs), 'controller_ms_max': 1000*max(costs),
                             'section_ms_avg': {k: 1000*v/max(1, self.section_ticks) for k, v in self.sections.items()}}
            if world is None or world != self.world:
                raise RuntimeError('Runtime authority world changed')
            if now - self.started > self.max_seconds:
                raise TimeoutError('Fleet session duration limit')
            if (self.root/'stop').exists():
                self.stop()
                return
            for table in self.tables:
                if table.error:
                    continue
                try:
                    t0 = time.perf_counter()
                    self.poll_command(table, now)
                    t1 = time.perf_counter()
                    if table.motion:
                        self.step_motion(table, now)
                    else:
                        self.hold_grip(table, now)
                    table.last_grip_tick = now
                    t2 = time.perf_counter()
                    self.measure_props(table, now)
                    t3 = time.perf_counter()
                    self.sections['commands'] += t1-t0
                    self.sections['motion'] += t2-t1
                    self.sections['props'] += t3-t2
                except Exception as error:
                    # One table failing must not stop the other experiments.
                    table.error = str(error)
                    if table.motion:
                        self.motion_report(table, True, table.error)
                        table.motion = None
            if self.mirror:
                if self.mirror.closed:
                    raise RuntimeError(self.mirror.error or 'Fleet mirror closed')
                self.mirror.tick()
            # Camera budget: at most captures_per_tick scene captures per editor frame.
            # 1) finish 3-view packets already started, 2) start packets for tables
            # that need their next policy observation, 3) spare budget records
            # overhead review frames of all live tables at `fps`, including holds.
            t4 = time.perf_counter()
            for table in self.tables:
                if table.pending and not table.queue and not table.error:
                    try:
                        self.publish(table, now)
                    except Exception as error:
                        table.error = 'capture: ' + str(error)
            budget = self.captures_per_tick
            live = [t for t in self.tables if not t.error]
            for table in [t for t in live if t.queue][:budget]:
                try:
                    self.capture_role(table)
                except Exception as error:
                    table.error = 'capture: ' + str(error)
                budget -= 1
            for table in [t for t in live if t.need_obs and not t.pending and not t.motion]:
                if budget <= 0:
                    break
                try:
                    self.capture(table, now)
                except Exception as error:
                    table.error = 'capture: ' + str(error)
                budget -= 1
            from maker_arm_passive_preview import review_candidates
            due = review_candidates(self.tables, now)
            for table in due[:max(0, budget)]:
                try:
                    self.record(table, now)
                except Exception as error:
                    # Retain the original guard/policy failure while retrying
                    # review-only captures at the bounded scheduled cadence.
                    table.preview_error = str(error)
                    if not table.error:
                        table.error = 'capture: ' + str(error)
                table.next_capture = max(table.next_capture + self.interval, now)
                budget -= 1
            # Unselected rooms get camera-only SIE images from remaining budget.
            if self.passive_preview is not None:
                try:
                    self.passive_preview.tick(now, budget, self._export)
                except Exception as error:
                    unreal.log_warning('Passive camera preview disabled: '+str(error))
                    self.passive_preview = None  # Never abort a task for a review-only feed failure.
            t5 = time.perf_counter()
            self.report()
            self.sections['cameras'] += t5-t4
            self.sections['report'] += time.perf_counter()-t5
            self.section_ticks += 1
        except Exception as error:
            self.error = str(error)
            if self.phase == 'ending':
                self.phase = 'error'
                self.report(force=True)
                self.close_callback()
            else:
                self.stop()

    def stop(self):
        if self.phase in ('stopped', 'ending', 'error'):
            return
        for table in self.tables:
            if table.motion:
                self.motion_report(table, True, self.error or 'Fleet runtime ended')
                table.motion = None
        if self.mirror:
            try:
                self.mirror.finish(commit=False)
            finally:
                self.mirror = None
        self.phase = 'ending'
        self.ending_started = time.monotonic()
        self.report(force=True)
        self.level.editor_request_end_play()

    def close_callback(self):
        if self.callback is not None:
            unreal.unregister_slate_post_tick_callback(self.callback)
            self.callback = None


def start(config_path, root, fps=2., max_seconds=420, captures_per_tick=1):
    global _session
    if _session and _session.callback is not None:
        raise RuntimeError('Fleet runtime already active')
    _session = Session(config_path, root, fps, max_seconds, captures_per_tick)
    return {'phase': _session.phase, 'tables': len(_session.tables)}


def stop():
    if _session:
        _session.stop()
    return {'phase': _session.phase if _session else None}


def start_preview(directory):
    import importlib
    import maker_arm_fleet_preview
    maker_arm_fleet_preview.stop()
    importlib.reload(maker_arm_fleet_preview)
    return maker_arm_fleet_preview.start(directory, seconds=3600)


def stop_preview():
    import maker_arm_fleet_preview
    return maker_arm_fleet_preview.stop()
