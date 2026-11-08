"""Approximate contact-limited position actuator, not calibrated force control.
No prop transforms are written. Chaos remains responsible for retention/lift.
Coordinates below are metres in the official link_007 flange frame.
"""
import itertools
import math
from functools import lru_cache


def pad_points(points, flat=False):
    """Clip a convex envelope to the finite finger-pad X/Z corridor.

    Legacy boxes use itertools.product corner order; round cups supply faces.
    Full-envelope projection overestimates width outside the contact region.
    """
    if isinstance(points, dict):
        faces=points['faces'];points=points['points']
    else:
        if len(points)!=8:raise ValueError('Expected eight ordered box corners')
        faces=((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3))
    result=[]
    for face in faces:
        polygon=[points[i] for i in face]
        corridor=((0,-.015,1),(0,.015,-1),(2,-.18,1),(2,-.16,-1)) if flat else ((0,-.02,1),(0,.02,-1),(2,-.17,1),(2,-.10,-1))
        for axis,bound,sign in corridor:
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


def inner_pad_offset(z):
    """One-jaw inner surface at X=0, sampled from pinned official CAD (metres).
    The deeper pad is 5 mm off its joint zero plane; fingertip pad is at zero.
    This approximate contact model does not change the original mesh or axes.
    """
    if z<=-.15:return 0.
    if z>=-.13:return .005
    if z<=-.14:return (z+.15)/.01*.00237
    return .00237+(z+.14)/.01*(.005-.00237)


def contact_gap(requested, previous, boxes, compliance=.005, flat=False):
    """Stop inward jaw travel at a prop intersecting both finger pads.

    boxes contain oriented corners or a faced cup envelope in flange coordinates. Require
    overlap of the inner pad corridor (X +/-2cm, Z -17..-10cm) and both jaws.
    Open commands remain unrestricted; no automatic opening on new contact.
    """
    if requested >= previous:
        return requested, []
    result=requested; contacts=[]
    for label, points in boxes:
        points=pad_points(points,flat=flat)
        if not points:continue
        lo=[min(p[i] for p in points) for i in range(3)]
        hi=[max(p[i] for p in points) for i in range(3)]
        # Both inner faces must meet the object. A one-sided collision pushes
        # the free prop; it is not a force closure and must not latch the jaws.
        if lo[1]>=0 or hi[1]<=0:
            continue
        stop=max(0.,hi[1]-lo[1]-(0. if flat else 2*inner_pad_offset((lo[2]+hi[2])/2))-compliance)
        if stop>requested:
            result=max(result,min(previous,stop));contacts.append(label)
    return result, contacts


def local_bounds_points(lo, hi, tapered_cup=False):
    """Eight-corner contact envelope; cup asset is R3cm→R4cm over12cm.

    This encloses the circular frustum and retains taper through finite-pad
    clipping. It is an approximate actuator limit, not a Chaos contact report.
    """
    result=[]
    for xyz in itertools.product((lo[0],hi[0]),(lo[1],hi[1]),(lo[2],hi[2])):
        point=list(xyz)
        if tapered_cup:
            factor=.75 if xyz[2] == lo[2] else 1.
            for axis in (0,1):
                center=(lo[axis]+hi[axis])/2
                point[axis]=center+(point[axis]-center)*factor
        result.append(point)
    return result


@lru_cache(maxsize=32)
def cup_contact_shape(lo, hi, segments=128):
    """Conservative circular outer frustum, not a yaw-dependent square box.

    The authored cup has outer radii 3/4 cm and height 12 cm. Bounds supply
    units/scale; bottom radius is 3/4 of rim radius. Circumscribed polygon
    encloses the round surface (128 sides: radial excess <= 0.0013 cm).
    Finite-pad clipping uses all side faces and end caps. This models the
    exterior envelope, not cavity contacts or a measured Chaos impulse.
    """
    if segments < 16:raise ValueError('Cup envelope needs at least 16 sides')
    center=[(lo[i]+hi[i])/2 for i in range(2)]
    radius=[(hi[i]-lo[i])/2 for i in range(2)]
    enclosure=1/math.cos(math.pi/segments)
    points=[]
    for z,factor in ((lo[2],.75),(hi[2],1.)):
        for k in range(segments):
            angle=2*math.pi*k/segments
            points.append([center[0]+radius[0]*factor*enclosure*math.cos(angle),
                           center[1]+radius[1]*factor*enclosure*math.sin(angle),z])
    faces=[tuple(range(segments)),tuple(range(segments,2*segments))]
    faces.extend((k,(k+1)%segments,(k+1)%segments+segments,k+segments)
                 for k in range(segments))
    return {'points':tuple(tuple(p) for p in points),'faces':tuple(faces)}


def broad_phase(corners):
    """Conservative full-bounds rejection; never supplies a contact width."""
    lo=[min(p[i] for p in corners) for i in range(3)]
    hi=[max(p[i] for p in corners) for i in range(3)]
    return hi[0]>=-.02 and lo[0]<=.02 and hi[2]>=-.18 and lo[2]<=-.10 and lo[1]<0<hi[1]


def actor_boxes(actors, flange, base):
    """Read transformed mesh bounds; convert UE cm/LH to flange metres/RH."""
    import unreal
    result=[]
    for label,actor in actors.items():
        c=actor.static_mesh_component
        lo,hi=c.get_local_bounds();t=actor.get_actor_transform();points=[]
        is_cup=label.split('__')[-1] in ('MakerStation_Prop_Cup','MakerStation_Prop_Cup_02','MakerStation_Prop_Cup_03','MakerStation_Prop_Cup_04','MakerStation_Prop_Cup_05')
        bounds=((lo.x,lo.y,lo.z),(hi.x,hi.y,hi.z))
        def convert(xyz):
            p=t.transform_location(unreal.Vector(*xyz))
            rh=[(p.x-base[0])*.01,-(p.y-base[1])*.01,(p.z-base[2])*.01]
            d=[rh[i]-flange[i][3] for i in range(3)]
            return [sum(flange[j][i]*d[j] for j in range(3)) for i in range(3)]
        corners=[convert(xyz) for xyz in local_bounds_points(*bounds)]
        if is_cup:
            # Cheap enclosing OBB broad phase, covering BOTH pad corridors.
            # Only nearby candidates need the 256 transformed ring vertices.
            if not broad_phase(corners):continue
            shape=cup_contact_shape(*bounds)
            result.append((label,{'points':[convert(p) for p in shape['points']], 'faces':shape['faces']}))
        else:result.append((label,corners))
    return result
