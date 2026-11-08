"""Reproducible task/layout plans. Generating a plan never calls Dream or moves UE."""
import copy
import random
import secrets

PREFIX = 'MakerStation_Prop_'
OBJECTS = {'Cup': ('drinking cup', 6.0, 4.0)}
# One upright empty cup, 12 cm high, within the left arm workspace.
SLOTS = ((48, -42), (60, -40), (65, -30))


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
        # Selecting a subset does not change that table's generated experiment.
        rng = random.Random('%d:%d' % (seed, index))
        names = list(OBJECTS)
        rng.shuffle(names)
        objects = []
        for name, (x, y) in zip(names, rng.sample(SLOTS, len(names))):
            _, half_height, radius = OBJECTS[name]
            objects.append({'label': PREFIX+name,
                            'location_cm': [x+rng.uniform(-1, 1), y+rng.uniform(-1, 1), 75+half_height],
                            'yaw_deg': rng.uniform(-30, 30), 'clearance_radius_cm': radius})
        target = rng.choice(objects)
        default_y = {'Cup': -40}
        target_y = target['location_cm'][1] if randomize else default_y[target['label'].removeprefix(PREFIX)]
        side = 'left' if target_y < 0 else 'right'
        text = prompt.strip()
        objective = None
        if mode == 'random':
            name = target['label'].removeprefix(PREFIX)
            text = ('Use the %s arm to pick up the %s, lift it straight up by 10 cm, '
                    'and hold it upright and steady for 5 seconds. Keep the other arm still.' % (side, OBJECTS[name][0]))
            objective = {'kind': 'lift_hold', 'target': target['label'], 'side': side,
                         'height_cm': 10.0, 'hold_seconds': 5.0, 'max_tilt_deg': 15.0}
        tables.append({'index': index, 'display_number': index+1, 'prompt': text,
                       'objective': objective, 'objects': objects if randomize else [],
                       'target': target['label'] if objective else None})
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
