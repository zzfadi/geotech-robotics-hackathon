"""Reproducible task/layout plans. Generating a plan never calls Dream or moves UE."""
import copy
import random
import re
import secrets

PREFIX = 'MakerStation_Prop_'
OBJECTS = {name: ('drinking cup', 6.0, 4.0) for name in ('Cup','Cup_02','Cup_03','Cup_04','Cup_05')}
DEFAULT_SEED = 20261107


def central_cup_positions(index, seed):
    """Random cup centres between the two arms (x 48..62, y -33.5..33.5 cm), at least 13 cm
    apart. Labels follow y so Cup is always nearest the left arm and Cup_03 is next; with five
    cups the middle one (Cup_05) stays within x <= 52, the left arm's reliable centre reach."""
    import itertools, math, random
    rng = random.Random('%d:%d' % (seed, index))
    count = 4 + index % 2
    if seed == 20261107:
        # The arranged scene (cup_scene_layout.json): Codex's centred slots with small jitter.
        return [[rng.uniform(48, 52 if ordinal == 4 else 62), y+rng.uniform(-1.5, 1.5)]
                for ordinal, y in enumerate((-32, 32, -16, 16, 0)[:count])]
    order = ('Cup', 'Cup_03', 'Cup_05', 'Cup_04', 'Cup_02') if count == 5 else ('Cup', 'Cup_03', 'Cup_04', 'Cup_02')
    for _ in range(5000):
        points = sorted(([rng.uniform(48, 62), rng.uniform(-33.5, 33.5)] for _ in range(count)), key=lambda p: p[1])
        if (all(math.dist(a, b) >= 13 for a, b in itertools.combinations(points, 2)) and points[1][1] < 0
                and (count == 4 or points[2][0] <= 52)):
            break
    else:
        raise RuntimeError('No central cup layout found')
    placed = dict(zip(order, points))
    return [placed[name] for name in ('Cup', 'Cup_02', 'Cup_03', 'Cup_04', 'Cup_05')[:count]]


def cup_layout(index, seed):
    """Match the scene's existing 4/5 cups; shuffle positions, never invent actors."""
    return [{'label': PREFIX+name, 'location_cm': [x, y, 81],
             'yaw_deg': 0., 'rotation_deg': [0.,0.,0.], 'clearance_radius_cm': 4.}
            for name, (x, y) in zip(OBJECTS, central_cup_positions(index, seed))]


def explicit_motion_side(prompt):
    """Only recognize a complete, unambiguous stationary-arm instruction.

    This constrains actuation, not task success. Unknown prose is not a lift
    objective and never inherits an unrelated configured execution constraint.
    """
    clauses = re.split(r'[.!?;\n]+', prompt.lower())
    stationary = {m.group(1) for clause in clauses
                  if (m := re.fullmatch(r'\s*keep (?:the )?(left|right) arm still\s*', clause))}
    if len(stationary) != 1 or re.search(r'\b(?:both|bimanual|two arms)\b', prompt, re.I):
        return None
    held = next(iter(stationary))
    if re.search(r'\b(?:move|use|lift|raise|lower|rotate|wave)\s+(?:the\s+)?'+held+r'\s+arm\b', prompt, re.I):
        return None
    return 'left' if held == 'right' else 'right'


def build_plan(indices, *, mode='same', prompt='', seed=None, randomize=False):
    if (not isinstance(indices, list) or not indices or len(indices) > 9
            or any(type(i) is not int or not 0 <= i <= 8 for i in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError('Select distinct table IDs in 0..8')
    if mode not in ('same', 'random', 'configured'):
        raise ValueError('Unknown task mode')
    if not isinstance(prompt, str) or len(prompt.strip()) > 1000:
        raise ValueError('Prompt must be text of at most 1000 characters')
    if mode == 'same' and not prompt.strip():
        raise ValueError('Enter the shared prompt')
    if seed is None:
        seed = secrets.randbits(32)
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('Seed must be an integer in 0..4294967295')
    if type(randomize) is not bool:
        raise ValueError('randomize must be true or false')
    tables = []
    for index in sorted(indices):
        objects = cup_layout(index, seed if randomize else DEFAULT_SEED)
        target = objects[0]  # Cup is always the leftmost cup, nearest the left arm.
        side = 'left'
        text = prompt.strip()
        objective = None
        standard = re.fullmatch(r'(?:hold my beer|pick up (?:the )?cup|lift (?:the )?cup)[.!]?', text, re.I)
        if mode == 'random' or (mode == 'same' and standard):
            text = ('Use the left arm to pick up the leftmost drinking cup in the overhead view, '
                    'nearest the left arm. Lift it straight up by 10 cm and hold it upright '
                    'and steady for 5 seconds. Keep the other arm still and leave the other cups on the table.')
            objective = {'kind':'lift_hold','target':target['label'],'side':side,
                         'height_cm':10.,'hold_seconds':5.,'max_tilt_deg':15.}
        tables.append({'index': index, 'display_number': index+1, 'prompt': text,
                       'objective': objective,
                       'motion_side': side if objective else explicit_motion_side(prompt),
                       'objects': objects if randomize else [],
                       'target': target['label'] if mode != 'configured' else None})
    return {'version': 1, 'seed': seed, 'mode': mode, 'randomize': randomize, 'tables': tables}


def apply_plan(config, plan):
    """Copy a reviewed plan into worker variants without modifying the base config."""
    configured = {t.get('index', i): t for i, t in enumerate(config['tables'])}
    out = {'defaults': copy.deepcopy(config.get('defaults', {})), 'tables': []}
    for task in plan['tables']:
        index = task['index']
        if index not in configured:
            raise ValueError('Table %d is not configured' % index)
        table = copy.deepcopy(configured[index])
        table['index'] = index
        if task['prompt']:
            table['prompt'] = task['prompt']
        if plan.get('mode') != 'configured':
            table['objective'] = copy.deepcopy(task['objective'])
            table['motion_side'] = task.get('motion_side')
            if task['objects'] or not table.get('preserve_scene_layout'):
                table['objects'] = copy.deepcopy(task['objects'])
        elif task['objects']:
            # Configured fixtures retain their task, target, geometry and pose.
            # Only an explicit rearrangement request replaces their object layout.
            table['objects'] = copy.deepcopy(task['objects'])
        if task['target']:
            table['target'] = {'label': task['target'], 'location_cm': None,
                               'offset_cm': [0, 0, 0], 'scale': None, 'yaw_deg': None}
        # Randomized positions cannot inherit a grasp pose around the old target.
        if task['objects']:
            table['initial_state'] = {'q_left': [0]*6, 'q_right': [0]*6,
                                      'gap_left_m': .09, 'gap_right_m': .09}
        out['tables'].append(table)
    return out
