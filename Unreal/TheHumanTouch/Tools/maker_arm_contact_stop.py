"""Approximate contact-limited position actuator, not calibrated force control.
No prop transforms are written. Chaos remains responsible for retention/lift.
Coordinates below are metres in the official link_007 flange frame.
"""
import itertools


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
