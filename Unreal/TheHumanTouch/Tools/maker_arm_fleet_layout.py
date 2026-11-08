"""Fleet identity, variants and three-column cubic room layout.

T00 is the original station; T01..T12 retain stable IDs in the grid.
All geometry stays table-local and is translated without rotation or scaling.
"""
import math
import re

FLEET_PREFIX = 'MakerFleet_'
FLEET_TAG = 'MakerFleet'
SPACING_CM = 800.0
HALL_HALF_WIDTH_CM = 400.0
MAX_TABLES = 12
SOURCE_PREFIXES = ('MakerArm_', 'MakerStation_', 'MakerCamera_', 'Dreamlabs_YAM_',
                   'MakerEnvironment_', 'MakerLighting_')
# Omit only hidden legacy YAM arms and their disabled lights.
SKIP_SOURCE = re.compile(r'^(Dreamlabs_YAM_(left|right)_.*|Dreamlabs_YAM_(Fill|Key)Light)$')
PROP_PREFIX = 'MakerStation_Approximate_Block_'
PROP_PREFIXES = (PROP_PREFIX, 'MakerStation_Prop_')
TARGET = PROP_PREFIX + 'Red' # Legacy recorded configurations remain readable; new default is fleet_cups.json.
# Matches yam_station.BASES (checked by test_maker_arm_fleet_layout).
STATION_BASES = {'left': (20, -40, 76), 'right': (20, 40, 76)}
JAW_MAX_M = .104825
CHECKPOINTS = ('auto', 'dreamlabs/dream-1-beta-flash', 'dreamlabs/dream-1-beta')
REST_STATE = {'q_left': [0.0]*6, 'q_right': [0.0]*6, 'gap_left_m': .04, 'gap_right_m': .04}


def _check_index(index):
    if not isinstance(index, int) or not 0 <= index <= MAX_TABLES:
        raise ValueError('Table index must be 0..%d' % MAX_TABLES)


def offset(index):
    _check_index(index)
    from maker_arm_grid import offset as grid_offset
    return grid_offset(index)


def is_source_label(name):
    return name.startswith(SOURCE_PREFIXES) and not SKIP_SOURCE.match(name)


def label(index, name):
    _check_index(index)
    return name if index == 0 else '%sT%02d__%s' % (FLEET_PREFIX, index, name)


def parse_label(text):
    """'MakerFleet_T03__MakerArm_left_link_007' -> (3, 'MakerArm_left_link_007')."""
    if not text.startswith(FLEET_PREFIX + 'T') or '__' not in text:
        return None
    head, name = text[len(FLEET_PREFIX)+1:].split('__', 1)
    if not head.isdigit():
        return None
    return int(head), name


def table_tag(index):
    _check_index(index)
    return '%sT%02d' % (FLEET_TAG, index)


def replica_base(index, base):
    d = offset(index)
    return tuple(base[i] + d[i] for i in range(3))


def _vector(value, name, length=3):
    if value is None:
        return None
    if len(value) != length or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError('%s must be %d finite numbers' % (name, length))
    return [float(v) for v in value]


def check_state(state):
    out = {}
    for side in ('left', 'right'):
        q = _vector(state['q_'+side], 'q_'+side, 6)
        gap = float(state['gap_'+side+'_m'])
        if not 0 <= gap <= JAW_MAX_M:
            raise ValueError('gap_%s_m outside 0..%.6f' % (side, JAW_MAX_M))
        out['q_'+side] = q
        out['gap_'+side+'_m'] = gap
    return out


def normalize_variant(variant, index, defaults=None):
    """Merge fleet defaults into one table's variant and validate every field."""
    merged = dict(defaults or {})
    merged.update(variant)
    target = dict((defaults or {}).get('target') or {})
    target.update(variant.get('target') or {})
    out = {'index': index, 'name': str(merged.get('name') or 'table-%02d' % index)}
    out['prompt'] = str(merged.get('prompt') or '').strip()
    if not out['prompt']:
        raise ValueError('Table %d needs a prompt' % index)
    out['checkpoint'] = merged.get('checkpoint', 'dreamlabs/dream-1-beta-flash')
    if out['checkpoint'] not in CHECKPOINTS:
        raise ValueError('Unknown checkpoint for table %d' % index)
    out['position_ik'] = bool(merged.get('position_ik', False))
    out['pad_contacts'] = bool(merged.get('pad_contacts', True))
    out['tcp_back'] = float(merged.get('tcp_back', 0.0))
    if not 0 <= out['tcp_back'] <= .04:
        raise ValueError('tcp_back must be 0..0.04 m')
    if out['position_ik'] and out['tcp_back'] != 0:
        raise ValueError('Position IK requires the source TCP (tcp_back 0)')
    rows = merged.get('action_rows')
    if rows is not None and (not isinstance(rows, int) or not 1 <= rows <= 48):
        raise ValueError('action_rows must be 1..48 or null')
    out['action_rows'] = rows
    cycles = merged.get('cycles', 12)
    if not isinstance(cycles, int) or not 1 <= cycles <= 24:
        raise ValueError('cycles must be 1..24')
    out['cycles'] = cycles
    out['initial_state'] = check_state(merged.get('initial_state') or REST_STATE)
    out['objective'] = merged.get('objective')
    out['objects'] = []
    seen = set()
    for obj in merged.get('objects', []):
        name = obj['label']
        if not name.startswith(PROP_PREFIXES) or name in seen:
            raise ValueError('Object layout needs distinct simulated station props')
        seen.add(name)
        loc = _vector(obj['location_cm'], 'objects.location_cm')
        if not 15 <= loc[0] <= 75 or not -85 <= loc[1] <= 85:
            raise ValueError('Object layout outside tabletop working area')
        yaw = float(obj.get('yaw_deg', 0))
        if not math.isfinite(yaw):
            raise ValueError('Object yaw must be finite')
        out['objects'].append({'label':name,'location_cm':loc,'yaw_deg':yaw,'rotation_deg':_vector(obj.get('rotation_deg'),'objects.rotation_deg')})
    out['target'] = {'label': str(target.get('label') or TARGET),
                     'location_cm': _vector(target.get('location_cm'), 'target.location_cm'),
                     'offset_cm': _vector(target.get('offset_cm') or [0, 0, 0], 'target.offset_cm'),
                     'rotation_deg': _vector(target.get('rotation_deg'), 'target.rotation_deg'),
                     'yaw_deg': None if target.get('yaw_deg') is None else float(target['yaw_deg']),
                     'scale': _vector(target.get('scale'), 'target.scale')}
    if not out['target']['label'].startswith(PROP_PREFIXES):
        raise ValueError('Target must be a simulated station prop (%s*)' % PROP_PREFIX)
    return out


def normalize_config(config):
    """Tables are booths 1..n in order unless a table gives its own 'index'
    (used when running a subset: booth positions must not shift)."""
    tables = config.get('tables') or []
    if not 1 <= len(tables) <= MAX_TABLES:
        raise ValueError('Fleet needs 1..%d tables' % MAX_TABLES)
    out = [normalize_variant(v, v.get('index', i), config.get('defaults')) for i, v in enumerate(tables)]
    indices = [v['index'] for v in out]
    for index in indices:
        _check_index(index)
    if len(set(indices)) != len(indices):
        raise ValueError('Duplicate table index')
    return out
