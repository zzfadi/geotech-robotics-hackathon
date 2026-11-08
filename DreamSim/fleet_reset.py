"""Idle-only scene reset. No Dream clients, inference, SIE start or Table10 access."""
import copy
from fleet_planning import cup_layout

HOME_Q = [-8.341015791761527e-08, -1.3267562769368857, 1.5947274948757348,
          -1.4680311465744582, 1.6235197063895467e-08, -8.180590968960631e-08]


def reset_config(config, seed):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('Invalid reset seed')
    tables = {t.get('index', i): t for i, t in enumerate(config['tables'])}
    if set(tables) != set(range(9)):
        raise ValueError('Reset requires exactly the nine cup tables; Table10 excluded')
    result = copy.deepcopy(config)
    result['tables'] = []
    for index in range(9):
        table = copy.deepcopy(tables[index])
        table['index'] = index
        table['preserve_scene_layout'] = True
        table['objects'] = cup_layout(index, seed)
        table['initial_state'] = {'q_left': HOME_Q.copy(), 'q_right': HOME_Q.copy(),
                                  'gap_left_m': .1, 'gap_right_m': .1}
        target = (table.get('target') or {}).get('label', 'MakerStation_Prop_Cup')
        if target not in {o['label'] for o in table['objects']}:
            raise ValueError('Reset target is not a current drinking cup')
        table['target'] = {'label': target, 'location_cm': None, 'offset_cm': [0,0,0],
                           'scale': None, 'yaw_deg': 0, 'rotation_deg': [0,0,0]}
        result['tables'].append(table)
    return result


def apply_idle_reset(editor, config_path):
    """Server admission waits for teardown; UE _check_idle is a second hard gate."""
    editor.call('begin_task', {'title': 'Reset all nine cup tables',
                'request': 'Randomize cups and restore idle arms; leave interaction Table10 unchanged',
                'keep_view': True, 'focus_x': 40, 'focus_y': 0, 'focus_z': 100})
    try:
        return editor.scene('maker_arm_fleet.reset(%r)' % str(config_path),
                            'Reset nine cup tables after simulation cleanup')
    finally:
        editor.call('end_task', {'summary': 'Nine-table scene reset operation ended; no Dream inference.',
                                'keep_view': True})
