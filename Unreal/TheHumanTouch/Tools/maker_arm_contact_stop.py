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
        if isinstance(points,dict) and points.get('material_cells'):
            stop=_hollow_stop(requested,previous,points,compliance,flat)
            if stop is not None and stop>requested:
                result=max(result,min(previous,stop));contacts.append(label)
            continue
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


def _hollow_stop(requested, previous, shape, compliance, flat):
    """Closing-only material sweeps, preserving the hollow cavity.

    Each convex shell cell is clipped to the finite X/Z pad volume. The
    advancing inner face detects entry into material; the 1cm flat pad's
    trailing face is included in broad overlap, but moving away from an
    already-touching inner wall is not misclassified as force closure.
    This does not implement outward/internal gripping or remove Chaos contact.
    """
    hits={1:[], -1:[]}
    # Total two-jaw penetration cannot exceed one wall thickness; each jaw
    # remains within material after first contact rather than crossing the shell.
    thicknesses=[math.dist(shape['points'][a],shape['points'][b]) for a,b in shape['wall_pairs']]
    compliance=min(compliance,min(thicknesses))
    half=previous/2;target=requested/2
    thickness=.01 if flat else .02  # Legacy CAD contact envelope is approximate.
    for cell in shape['material_cells']:
        # Exact AABB rejection only; retain128-sector geometry fidelity.
        corners=[shape['points'][i] for i in set(itertools.chain.from_iterable(cell))]
        xlo,xhi,zlo,zhi=(-.015,.015,-.18,-.16) if flat else (-.02,.02,-.17,-.10)
        if (max(p[0] for p in corners)<xlo or min(p[0] for p in corners)>xhi or
                max(p[2] for p in corners)<zlo or min(p[2] for p in corners)>zhi):continue
        points=pad_points({'points':shape['points'],'faces':cell},flat=flat)
        if not points:continue
        offset=0. if flat else inner_pad_offset((min(p[2] for p in points)+max(p[2] for p in points))/2)
        for sign in (1,-1):
            ys=[sign*p[1]-offset for p in points]
            lo,hi=min(ys),max(ys)
            # Whole finite-pad swept volume must meet material on this side.
            if hi<target or lo>half+thickness or hi<=0:continue
            # Material only behind the advancing face: closing moves AWAY
            # from this inner wall; never latch solely because it is nearby.
            if lo>half+1e-9:continue
            hits[sign].append(hi)  # Fixed material surface, never subtract compliance repeatedly.
    if not hits[1] or not hits[-1]:return None
    return max(0.,max(hits[1])+max(hits[-1])-compliance)


@lru_cache(maxsize=32)
def cup_contact_shape(lo, hi, segments=128):
    """Hollow authored cup: outerR3/4, innerR2.8/3.8, 0.3cm base.

    Local bounds encode scale. Outer facets conservatively circumscribe the
    circle; inner facets inscribe the empty cavity. Material is a union of
    convex wall sectors plus a solid bottom frustum, never a filled rim cap.
    At128segments inner chord intrusion is <0.012mm for the nominal cup.
    """
    if segments<16:raise ValueError('Cup envelope needs at least16sides')
    center=[(lo[i]+hi[i])/2 for i in range(2)]
    radius=[(hi[i]-lo[i])/2 for i in range(2)]
    height=hi[2]-lo[2]
    if height<=0 or min(radius)<=0:raise ValueError('Invalid cup bounds')
    bottom_top=lo[2]+height*.025
    outer_bottom_top=.75+.25*.025
    enclosure=1/math.cos(math.pi/segments)
    rings=((lo[2],.75*enclosure),(bottom_top,outer_bottom_top*enclosure),
           (hi[2],enclosure),(bottom_top,.7),(hi[2],.95))
    points=[]
    for z,factor in rings:
        for k in range(segments):
            angle=2*math.pi*k/segments
            points.append((center[0]+radius[0]*factor*math.cos(angle),
                           center[1]+radius[1]*factor*math.sin(angle),z))
    cells=[];faces=[]
    # Solid base has no cavity: clips correctly even inside its thickness.
    base=[tuple(range(segments)),tuple(range(segments,2*segments))]
    base.extend((k,(k+1)%segments,(k+1)%segments+segments,k+segments) for k in range(segments))
    cells.append(tuple(base));faces.extend(base)
    for k in range(segments):
        j=(k+1)%segments
        # Lower outer/inner, upper outer/inner sector corners.
        a,b,c,d=segments+k,segments+j,3*segments+j,3*segments+k
        e,f,g,h=2*segments+k,2*segments+j,4*segments+j,4*segments+k
        cell=((a,b,c,d),(e,h,g,f),(a,e,f,b),(d,c,g,h),(a,d,h,e),(b,f,g,c))
        cells.append(cell);faces.extend(cell)
    pairs=tuple((segments+k,3*segments+k) for k in range(segments))+tuple((2*segments+k,4*segments+k) for k in range(segments))
    return {'points':tuple(points),'faces':tuple(faces),'material_cells':tuple(cells),'wall_pairs':pairs}


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
            result.append((label,{'points':[convert(p) for p in shape['points']], 'faces':shape['faces'], 'material_cells':shape['material_cells'], 'wall_pairs':shape['wall_pairs']}))
        else:result.append((label,corners))
    return result
