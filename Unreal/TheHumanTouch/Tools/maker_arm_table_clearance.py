"""Reject gripper collision boxes entering the tabletop; never project a command."""
import itertools


def check_gripper_table(frames, base, table_offset=(0, 0, 0), flat=True,
                        collision_bounds_cm=None, tolerance_cm=.2):
    """Check both complete pad OBBs (or conservative convex-mesh local bounds).

    Coordinates are official CAD metres; source UE collision bounds are cm/LH.
    A 2mm tolerance allows numerical contact with the tabletop, not penetration.
    """
    minimum = float('inf')
    for link in ('gripper_left', 'gripper_right'):
        t = frames[link]
        if flat:
            off = (0, .005 if link == 'gripper_left' else -.005, -.17)
            points = [[off[i]+corner[i] for i in range(3)] for corner in
                      itertools.product((-.015, .015), (-.005, .005), (-.01, .01))]
        else:
            if not collision_bounds_cm or link not in collision_bounds_cm:
                raise ValueError('Missing gripper collision bounds for workspace guard')
            low, high = collision_bounds_cm[link]
            points = [(x*.01, -y*.01, z*.01) for x,y,z in
                      itertools.product((low[0],high[0]), (low[1],high[1]), (low[2],high[2]))]
        world = []
        for point in points:
            p = [sum(t[i][j]*point[j] for j in range(3))+t[i][3] for i in range(3)]
            world.append([base[0]+100*p[0], base[1]-100*p[1], base[2]+100*p[2]])
        lo = [min(p[i] for p in world) for i in range(3)]
        hi = [max(p[i] for p in world) for i in range(3)]
        intersects = (hi[0] >= table_offset[0] and lo[0] <= table_offset[0]+80
                      and hi[1] >= table_offset[1]-100 and lo[1] <= table_offset[1]+100)
        if intersects:
            clearance = lo[2]-(table_offset[2]+75)
            minimum = min(minimum, clearance)
            if clearance < -tolerance_cm:
                raise ValueError('%s collision bounds enter tabletop by %.3f cm' % (link, -clearance))
    return minimum
