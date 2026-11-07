"""Approximate contact-limited position actuator, not calibrated force control.
No prop transforms are written. Chaos remains responsible for retention/lift.
Coordinates below are metres in the official link_007 flange frame.
"""
import itertools


def pad_points(points):
    """Clip an oriented box to the finite finger-pad X/Z corridor.

    Corner order matches itertools.product in actor_boxes. Full-box projection
    overestimates the width for oblique props outside the contact region.
    """
    if len(points)!=8:raise ValueError('Expected eight ordered box corners')
    faces=((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3))
    result=[]
    for face in faces:
        polygon=[points[i] for i in face]
        for axis,bound,sign in ((0,-.02,1),(0,.02,-1),(2,-.17,1),(2,-.10,-1)):
            clipped=[]
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                da=(a[axis]-bound)*sign;db=(b[axis]-bound)*sign
                if da>=0:clipped.append(a)
                if (da>=0)!=(db>=0):
                    t=da/(da-db);clipped.append([a[i]+t*(b[i]-a[i]) for i in range(3)])
            polygon=clipped
            if not polygon:break
        result.extend(polygon)
    return result


def contact_gap(requested, previous, boxes, compliance=.005):
    """Stop inward jaw travel at a prop intersecting both finger pads.

    boxes are oriented prop corners expressed in flange coordinates. Require
    overlap of the inner pad corridor (X +/-2cm, Z -17..-10cm) and both jaws.
    Open commands remain unrestricted; no automatic opening on new contact.
    """
    if requested >= previous:
        return requested, []
    result=requested; contacts=[]
    for label, points in boxes:
        points=pad_points(points)
        if not points:continue
        lo=[min(p[i] for p in points) for i in range(3)]
        hi=[max(p[i] for p in points) for i in range(3)]
        if hi[0]<-.02 or lo[0]>.02 or hi[2]<-.17 or lo[2]>-.10:
            continue
        # Both inner faces must meet the object. A one-sided collision pushes
        # the free prop; it is not a force closure and must not latch the jaws.
        if lo[1]>=0 or hi[1]<=0:
            continue
        stop=hi[1]-lo[1]-compliance
        if stop>requested:
            result=max(result,min(previous,stop));contacts.append(label)
    return result, contacts


def actor_boxes(actors, flange, base):
    """Read transformed mesh bounds; convert UE cm/LH to flange metres/RH."""
    import unreal
    result=[]
    for label,actor in actors.items():
        c=actor.static_mesh_component
        lo,hi=c.get_local_bounds();t=actor.get_actor_transform();points=[]
        for xyz in itertools.product((lo.x,hi.x),(lo.y,hi.y),(lo.z,hi.z)):
            p=t.transform_location(unreal.Vector(*xyz))
            rh=[(p.x-base[0])*.01,-(p.y-base[1])*.01,(p.z-base[2])*.01]
            d=[rh[i]-flange[i][3] for i in range(3)]
            points.append([sum(flange[j][i]*d[j] for j in range(3)) for i in range(3)])
        result.append((label,points))
    return result
