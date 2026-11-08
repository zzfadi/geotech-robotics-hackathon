"""Stable grid coordinates for the main station (T00) and replica rooms.

Room geometry is an artistic simulation enclosure, not measured venue CAD.
Robot coordinates are translated only; no arm or table scaling is permitted.
"""
import math

COLUMNS = 3
ROOM_CM = 800.0
PITCH_CM = 820.0
MAX_INDEX = 12


def offset(index):
    if type(index) is not int or not 0 <= index <= MAX_INDEX:
        raise ValueError('Table index must be an integer in 0..12')
    row, column = divmod(index, COLUMNS)
    return (row * PITCH_CM, column * PITCH_CM, 0.0)


def translation(index, current_floor, source_floor=(45.0, 0.0, -2.0)):
    """Derive migration from actual floor location, including partially moved rooms."""
    if len(current_floor) != 3 or not all(math.isfinite(v) for v in current_floor):
        raise ValueError('Current floor must contain three finite coordinates')
    d = offset(index)
    return tuple(source_floor[i] + d[i] - current_floor[i] for i in range(3))


def shell(index):
    """Open-top 8 x 8 x 8 metre enclosure; Engine Cube scales use centimetres."""
    dx, dy, dz = offset(index)
    surfaces = {
        'Floor': ((45, 0, -2), (8, 8, .04)),
        'BackWall': ((445, 0, 400), (.08, 8, 8)),
        'RearWall': ((-355, 0, 400), (.08, 8, 8)),
        'LeftWall': ((45, -400, 400), (8, .08, 8)),
        'RightWall': ((45, 400, 400), (8, .08, 8)),
    }
    return {name: {'location': (p[0]+dx, p[1]+dy, p[2]+dz), 'scale': scale}
            for name, (p, scale) in surfaces.items()}
