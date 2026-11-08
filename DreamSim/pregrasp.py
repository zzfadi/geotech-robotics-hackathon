"""Opt-in simulation helpers for Dream cup trials; both are labelled in results.

pregrasp_state: an episode START pose with open jaws around the target cup,
chosen from the configured cup location (privileged scene state). Dream still
produces every later action: closing, lifting and holding. Results using it are
"Dream from pregrasp start", not unassisted approach-and-grasp.

guard_table: a workspace limit on commanded rows. Rows whose flat pad boxes would
enter the tabletop are raised just enough to clear it (same geometry as the
Unreal runtime check), instead of the runtime halting the whole table.
"""
import math
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from compiled_fk import ArmFK
from maker_arm_model import mounted_frames
from maker_arm_table_clearance import check_gripper_table

# Same values as yam_station.BASES / maker_arm_station.MOUNT_YAW (those import unreal).
BASES = {'left': (20, -40, 76), 'right': (20, 40, 76)}
MOUNT_YAW = math.pi
JAW_MAX_M = .104825
HOME_Q = [-8.341015791761527e-08, -1.3267562769368857, 1.5947274948757348,
          -1.4680311465744582, 1.6235197063895467e-08, -8.180590968960631e-08]  # fleet_reset.HOME_Q
_FK = None


def _fk():
    global _FK
    if _FK is None:
        _FK = ArmFK()
    return _FK


def target_location(table):
    target = table.get('target') or {}
    if target.get('location_cm'):
        return [float(v) for v in target['location_cm']]
    for item in table.get('objects') or []:
        if item.get('label') == target.get('label') and item.get('location_cm'):
            return [float(v) for v in item['location_cm']]
    raise ValueError('Pregrasp start needs the target cup location')


def arm_point(location_cm, side):
    """Table-local Unreal cm (left-handed) -> arm base frame metres (right-handed)."""
    bx, by, bz = BASES[side]
    x, y, z = location_cm
    return np.array([(x-bx)/100, -(y-by)/100, (z-bz)/100])


def solve_pose(position, rotation, q0, tolerance_m=.002, tolerance_rad=.02):
    fk = _fk()
    low, high = fk.low+1e-7, fk.high-1e-7

    def residual(q):
        f = fk.pose(q)
        return np.r_[20*(f[:3, 3]-position), Rotation.from_matrix(rotation.T@f[:3, :3]).as_rotvec()]
    def within(x):
        f = fk.pose(x)
        return (np.linalg.norm(f[:3, 3]-position) <= tolerance_m and
                np.linalg.norm(Rotation.from_matrix(rotation.T@f[:3, :3]).as_rotvec()) <= tolerance_rad)
    best = None
    # Prefer the branch continuous with q0: only try other seeds if it misses tolerance,
    # otherwise consecutive rows can jump between equivalent joint solutions.
    for start in (q0, [0, -1.95, 1.47, -.12, 0, 0], [0, -1.33, 1.59, -1.47, 0, 0]):
        r = least_squares(residual, np.clip(start, low, high), bounds=(low, high), max_nfev=400)
        if best is None or r.cost < best.cost:
            best = r
        if within(r.x):
            best = r
            break
    f = fk.pose(best.x)
    error = float(np.linalg.norm(f[:3, 3]-position))
    angle = float(np.linalg.norm(Rotation.from_matrix(rotation.T@f[:3, :3]).as_rotvec()))
    if error > tolerance_m or angle > tolerance_rad:
        raise ValueError('Pregrasp pose unreachable (%.1f mm, %.3f rad)' % (error*1000, angle))
    return best.x.tolist()


def pregrasp_state(table, side='left', grasp_offset_cm=2.5, pitch_deg=45., standoff_cm=0., lift_cm=0., gap_m=.1):
    """Joint start pose: TCP grasp_offset_cm above the cup centre, approaching at pitch_deg.

    standoff_cm backs the tool off along its approach axis and lift_cm raises it,
    so harder variants leave Dream some approach to perform.
    """
    cup = arm_point(target_location(table), side)
    yaw = math.atan2(cup[1], cup[0])
    rotation = Rotation.from_euler('z', yaw).as_matrix()@Rotation.from_euler('y', -pitch_deg, degrees=True).as_matrix()
    approach = rotation@np.array([0, 0, -1.])
    position = cup+np.array([0, 0, grasp_offset_cm/100])-approach*standoff_cm/100+np.array([0, 0, lift_cm/100])
    initial = table.get('initial_state') or {}
    q = solve_pose(position, rotation, initial.get('q_'+side) or [0, -1.95, 1.47, -.12, 0, 0])
    state = dict(initial)
    other = 'right' if side == 'left' else 'left'
    state.setdefault('q_'+other, list(HOME_Q))
    state.setdefault('gap_'+other+'_m', .1)
    state['q_'+side] = q
    state['gap_'+side+'_m'] = gap_m
    clearance = table_clearance(q, gap_m, side)
    if clearance < .3:
        raise ValueError('Pregrasp pads only %.2f cm above the tabletop' % clearance)
    return state


def _assist(table, defaults):
    return {**((defaults or {}).get('assist') or {}), **(table.get('assist') or {})}


def prepare_run_config(run_config, base_config):
    """Resolve opt-in pregrasp starts for one batch; other tables are unchanged.

    A pregrasp table resets its cups each batch to the layout it was planned with
    (from base_config when the plan carries none), the target upright at an explicit
    location, so repeated attempts start from the same scene.
    """
    import copy
    out = copy.deepcopy(run_config)
    defaults = out.get('defaults') or {}
    base = {t.get('index', i): t for i, t in enumerate((base_config or {}).get('tables') or [])}
    for position, table in enumerate(out.get('tables') or []):
        assist = _assist(table, defaults)
        if assist.get('start') != 'pregrasp' and assist.get('approach') != 'scripted':
            continue
        index = table.get('index', position)
        source = base.get(index, {})
        if not table.get('objects') and source.get('objects'):
            table['objects'] = copy.deepcopy(source['objects'])
        target = dict(table.get('target') or {})
        if not target.get('location_cm'):
            probe = {'target': {**(source.get('target') or {}), 'label': target.get('label') or (source.get('target') or {}).get('label')},
                     'objects': table.get('objects')}
            if not (probe['target'].get('location_cm') and probe['target']['label'] == (source.get('target') or {}).get('label')):
                probe['target'].pop('location_cm', None)
            location = target_location(probe)
        else:
            location = target['location_cm']
        target.update({'location_cm': [float(v) for v in location], 'rotation_deg': [0, 0, 0], 'yaw_deg': 0,
                       'offset_cm': [0, 0, 0]})
        table['target'] = target
        table['objects'] = [o for o in table.get('objects') or [] if o.get('label') != target.get('label')]
        side = (table.get('objective') or {}).get('side') or 'left'
        options = {k: assist[k] for k in START_OPTIONS if k in assist}
        if assist.get('approach') == 'scripted':
            # Every run starts from idle; the reach is the first, recorded motion.
            table['initial_state'] = {'q_left': list(HOME_Q), 'q_right': list(HOME_Q), 'gap_left_m': .1, 'gap_right_m': .1}
            pregrasp_state({**table, 'initial_state': table['initial_state']}, side, **options)  # reachability check
        else:
            table['initial_state'] = pregrasp_state({**table, 'initial_state': table.get('initial_state') or source.get('initial_state')},
                                                    side, **options)
        table['assist'] = {**assist, 'resolved_start': {'side': side, 'target_cm': target['location_cm'], **options}}
    return out


def live_cups_expression(path):
    """Editor expression writing every cup actor's location to path (read-only scene access)."""
    return ("__import__('pathlib').Path(%r).write_text(__import__('json').dumps({a.get_actor_label(): "
            "[a.get_actor_location().x, a.get_actor_location().y, a.get_actor_location().z]"
            " for a in __import__('unreal').get_editor_subsystem(__import__('unreal').EditorActorSubsystem).get_all_level_actors()"
            " if 'MakerStation_Prop_Cup' in a.get_actor_label()}))" % str(path))


def parse_live_cups(output):
    """run_python replies 'result: <repr>'; the repr is of a JSON string."""
    import ast, json
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith('result:'):
            line = line[len('result:'):].strip()
            value = ast.literal_eval(line) if line[:1] in "'\"" else line
            return json.loads(value) if isinstance(value, str) else value
        if line.startswith('{'):
            return json.loads(line)
    raise ValueError('No live cup positions in editor output')


def apply_live_layout(config, world_cups):
    """Use the editor's current cup layout for tables with assist.layout == 'live'.

    After every simulation the editor restores its pre-run actor poses, so this is
    the layout someone last arranged (e.g. Codex's centred cups), never a fallen cup.
    """
    import copy
    from maker_arm_fleet_layout import label, offset
    out = copy.deepcopy(config)
    defaults = out.get('defaults') or {}
    for position, table in enumerate(out.get('tables') or []):
        if _assist(table, defaults).get('layout') != 'live':
            continue
        index = table.get('index', position)
        d = offset(index)
        cups = {}
        for world_label, p in world_cups.items():
            for name in ('MakerStation_Prop_Cup',)+tuple('MakerStation_Prop_Cup_%02d' % k for k in range(2, 10)):
                if world_label == label(index, name):
                    cups[name] = [p[0]-d[0], p[1]-d[1], p[2]-d[2]]
        # The last Reset layout is authoritative (every run restarts from it, upright);
        # live editor positions only fill cups the config does not place. Never reuse a
        # fallen cup's height.
        if table.get('preserve_scene_layout'):
            for item in table.get('objects') or []:
                if item.get('label') in cups and item.get('location_cm'):
                    cups[item['label']] = [float(v) for v in item['location_cm']]
        for name, loc in cups.items():
            if abs(loc[2]-81.) > 1.:
                cups[name] = [loc[0], loc[1], 81.]
        target = (table.get('target') or {}).get('label') or 'MakerStation_Prop_Cup'
        if target not in cups:
            raise ValueError('Table %d live layout has no %s' % (index, target))
        table['target'] = {**(table.get('target') or {}), 'label': target, 'location_cm': cups[target]}
        table['objects'] = [{'label': n, 'location_cm': loc, 'yaw_deg': 0., 'rotation_deg': [0., 0., 0.]}
                            for n, loc in sorted(cups.items()) if n != target]
        table['preserve_scene_layout'] = True
    return prepare_run_config(out, out)


def table_clearance(q, gap_m, side):
    # Dream may request slightly more than fully open; the runtime clamps it, so do the same.
    frames = mounted_frames(list(q), min(max(float(gap_m), 0.), JAW_MAX_M), yaw=MOUNT_YAW)
    return check_gripper_table(frames, BASES[side], (0, 0, 0), flat=True, tolerance_cm=1e9)


def guard_table(action, state, side='left', margin_cm=.4, max_raise_cm=6.):
    """Raise rows whose pads would enter the tabletop; returns (action, report)."""
    key = 'q_l_chunk' if side == 'left' else 'q_r_chunk'
    fk = _fk()
    rows = [list(r) for r in action[key]]
    raised = []
    previous = list(state['q_'+side])
    for i, row in enumerate(rows):
        q = np.clip(np.array(row[:6], dtype=float), fk.low+1e-7, fk.high-1e-7)
        gap = float(row[6])*JAW_MAX_M
        clearance = table_clearance(q, gap, side)
        if i == 0 or clearance >= margin_cm:  # row 0 is the measured pose; never move it
            previous = q.tolist()
            continue
        f = fk.pose(q)
        lift = margin_cm-clearance
        if lift > max_raise_cm:
            raise ValueError('Row %d pads %.1f cm into the tabletop; refusing to raise further' % (i, -clearance))
        for _ in range(4):
            target = f[:3, 3]+np.array([0, 0, lift/100])
            q = np.array(solve_pose(target, f[:3, :3], previous, tolerance_m=.003, tolerance_rad=.05))
            clearance = table_clearance(q, gap, side)
            if clearance >= margin_cm-.05:
                break
            lift += margin_cm-clearance
        rows[i] = q.tolist()+[row[6]]
        raised.append({'row': i, 'raised_cm': round(lift, 3), 'clearance_cm': round(clearance, 3)})
        previous = q.tolist()
    out = dict(action)
    out[key] = rows
    return out, {'side': side, 'raised_rows': raised, 'margin_cm': margin_cm}


def tool_height(q):
    fk = _fk()
    return float(fk.pose(np.clip(np.array(q, dtype=float), fk.low+1e-7, fk.high-1e-7))[2, 3])


def vertical_only(action, state, side='left', floor_z=None, allow_down_cm=.5, max_step_cm=.25, back_per_up=0.):
    """While an object is held: keep the measured tool x/y and orientation, apply only
    Dream's vertical motion, never below floor_z (the height where the jaws closed)
    minus allow_down_cm; without floor_z the current height is the floor."""
    key = 'q_l_chunk' if side == 'left' else 'q_r_chunk'
    fk = _fk()
    start = fk.pose(np.clip(np.array(state['q_'+side], dtype=float), fk.low+1e-7, fk.high-1e-7))
    floor = (float(start[2, 3]) if floor_z is None else floor_z)-allow_down_cm/100
    # back_per_up > 0 retreats along the tool's horizontal approach direction while rising,
    # like the verified mechanical lift (12 cm up, 8 cm back at a 45-degree tool).
    approach = start[:3, :3]@np.array([0, 0, -1.])
    horizontal = np.array([approach[0], approach[1], 0.])
    horizontal = horizontal/np.linalg.norm(horizontal) if np.linalg.norm(horizontal) > 1e-6 else horizontal

    def point(z):
        return np.array([start[0, 3], start[1, 3], z])-horizontal*back_per_up*max(z-start[2, 3], 0.)
    previous = list(state['q_'+side])
    rows, heights = [], []
    for row in action[key]:
        q = np.clip(np.array(row[:6], dtype=float), fk.low+1e-7, fk.high-1e-7)
        z = max(float(fk.pose(q)[2, 3]), floor)
        target = point(z)
        try:
            solved = solve_pose(target, start[:3, :3], previous, tolerance_m=.003, tolerance_rad=.05)
        except ValueError:
            break  # higher than reachable: stop the chunk at the last feasible row
        # Split large vertical steps (<= max_step_cm per row) so a held cup rises at
        # about 3 cm/s, like the verified mechanical lift; targets are unchanged.
        last_z = start[2, 3] if not heights else start[2, 3]+heights[-1]/100
        steps = max(1, int(np.ceil(abs(z-last_z)*100/max_step_cm)))
        for k in range(1, steps):
            mid = point(last_z+(z-last_z)*k/steps)
            try:
                previous = solve_pose(mid, start[:3, :3], previous, tolerance_m=.003, tolerance_rad=.05)
            except ValueError:
                break
            rows.append(list(previous)+[row[6]])
        rows.append(list(solved)+[row[6]])
        heights.append(round((z-start[2, 3])*100, 2))
        previous = solved
    if not rows:
        rows = [list(state['q_'+side])+[action[key][0][6]]]
    rows[0] = list(state['q_'+side])+[rows[0][6]]  # start exactly at the measured pose
    out = dict(action)
    out[key] = rows
    other = 'q_r_chunk' if side == 'left' else 'q_l_chunk'
    out[other] = [list(r) for r in action[other][:len(rows)]] or [list(action[other][0])]
    while len(out[other]) < len(rows):
        out[other].append(list(out[other][-1]))
    return out, {'side': side, 'rows': len(rows), 'dz_cm': heights}


START_OPTIONS = ('grasp_offset_cm', 'pitch_deg', 'standoff_cm', 'lift_cm', 'gap_m')


def approach_action(state, variant, standoff_lift_cm=8., transfer_rows=36, descend_rows=20):
    """Planned reach from the measured (idle) pose: joint move to a point standoff_lift_cm
    above the pregrasp pose, then a straight descent with open jaws. No Dream call."""
    assist = variant.get('assist') or {}
    side = (variant.get('objective') or {}).get('side') or 'left'
    options = {k: assist[k] for k in START_OPTIONS if k in assist}
    table = {'target': variant['target'], 'objects': variant.get('objects'), 'initial_state': state}
    goal = pregrasp_state(table, side, **options)
    above = pregrasp_state(table, side, **{**options, 'lift_cm': options.get('lift_cm', 0)+standoff_lift_cm})
    key, other = ('q_l_chunk', 'q_r_chunk') if side == 'left' else ('q_r_chunk', 'q_l_chunk')
    q0 = np.array(state['q_'+side], dtype=float)
    q1 = np.array(above['q_'+side], dtype=float)
    g0 = state['gap_'+side+'_m']/JAW_MAX_M
    g1 = goal['gap_'+side+'_m']/JAW_MAX_M
    rows = []
    for a in np.linspace(0, 1, transfer_rows):
        s_ = .5-.5*np.cos(np.pi*a)  # ease in/out
        rows.append(list(q0*(1-s_)+q1*s_)+[g0*(1-s_)+g1*s_])
    fk = _fk()
    f_above, f_goal = fk.pose(q1), fk.pose(goal['q_'+side])
    previous = q1.tolist()
    for a in np.linspace(0, 1, descend_rows)[1:]:
        s_ = .5-.5*np.cos(np.pi*a)
        target = f_above[:3, 3]*(1-s_)+f_goal[:3, 3]*s_
        previous = solve_pose(target, f_goal[:3, :3], previous, tolerance_m=.002, tolerance_rad=.02)
        rows.append(list(previous)+[g1])
    hold = list(state['q_'+('right' if side == 'left' else 'left')])+[state['gap_'+('right' if side == 'left' else 'left')+'_m']/JAW_MAX_M]
    return {key: rows, other: [hold[:] for _ in rows]}, {'side': side, 'rows': len(rows),
            'pregrasp_q': goal['q_'+side], 'standoff_lift_cm': standoff_lift_cm}
