"""Official Maker Arm mesh/kinematic representation, through scoped CoPresence tools.
Physical calibration/dynamics remain unvalidated upstream. No hardware commands.
"""
import math
import unreal
import yam_station
from maker_arm_model import model, mounted_frames, ue_pose

ASSETS = '/Game/_Agents/Denys_Mac_Codex/MakerArm'
PREFIX = 'MakerArm_'
BASES = dict(yam_station.BASES)  # Existing station placement, explicitly uncalibrated.
REST = [0, 0, 0, 0, 0, 0]
# Overhead reference: both bases on one long edge, grippers into +X table workspace.
MOUNT_YAW = {side: math.pi for side in BASES}
VISUAL_LINKS = [l.get('name') for l in model().findall('link') if l.findall('visual')]

def set_pose(side, q, gap=0.04):
    if side not in BASES:
        raise ValueError('Unknown arm')
    transforms = mounted_frames(q, gap, yaw=MOUNT_YAW[side])
    existing = yam_station.actors()
    labels = {n: PREFIX+side+'_'+n for n in VISUAL_LINKS}
    if any(label not in existing for label in labels.values()):
        raise ValueError('Missing arm links')
    for name, label in labels.items():
        a = existing[label]
        a.modify()
        p, r = ue_pose(transforms[name])
        p = [p[i] + BASES[side][i] for i in range(3)]
        a.set_actor_location_and_rotation(unreal.Vector(*p), unreal.Quat(*r).rotator(), False, True)
    return {'side':side, 'joints_rad':q, 'gap_m':gap, 'visual_links':len(labels)}

def replace_yam():
    existing = yam_station.actors()
    if any(n.startswith(PREFIX) for n in existing):
        raise ValueError('Maker Arm already exists; refuse duplicate replacement')
    meshes = {n: unreal.load_asset(ASSETS+'/SM_MakerArm_'+n) for n in VISUAL_LINKS}
    if not all(meshes.values()):
        raise ValueError('Import all official link meshes before replacement')
    old = [yam_station.PREFIX+s+'_'+n for s in BASES for n in yam_station.frames(yam_station.REST)]
    if any(n not in existing for n in old):
        raise ValueError('Expected complete original station before replacement')
    dark = unreal.load_asset(yam_station.ASSETS+'/M_YAM_Dark')
    if dark is None:
        raise ValueError('Existing station material missing')
    for side, base in BASES.items():
        for name, mesh in meshes.items():
            yam_station.mesh_actor(PREFIX+side+'_'+name,mesh,base,mat=dark)
        set_pose(side, REST)
    # Only remove the 18 identified prototype robot links. Keep table/cameras/lights.
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for label in old:
        if not subsystem.destroy_actor(existing[label]):
            raise RuntimeError('Could not remove prototype link '+label)
    return validate()

def validate():
    existing = yam_station.actors()
    expected = [PREFIX+s+'_'+n for s in BASES for n in VISUAL_LINKS]
    errors=[]
    for label in expected:
        a=existing.get(label)
        if a is None:
            errors.append('missing '+label)
        elif any(abs(v-1)>0.00001 for v in (a.get_actor_scale3d().x,a.get_actor_scale3d().y,a.get_actor_scale3d().z)):
            errors.append('non-unit scale '+label)
    return {'visual_links':sum(n in existing for n in expected),'expected':18,'errors':errors,
            'source':'MakerMods maker-arm-sdk 0245316c','geometry':'official CAD-derived meshes',
            'station_registration':'uncalibrated','physics':'not yet implemented/validated'}


def arrange_table():
    """Photo-derived layout in cm; provisional dimensions, not metrology.

    +X crosses the tabletop and Y follows its long edge. The shared mounting
    plate top is Z=76, coincident with the unchanged official arm base frames.
    """
    existing = yam_station.actors()
    p = yam_station.PREFIX
    placements = {
        p+'Table': ((45, 0, 72), (0.8, 2.0, 0.06)),
        p+'CameraMast': ((7, 0, 109), (0.015, 0.07, 0.66)),
        p+'OverviewCameraProxy': ((9, 0, 142), (0.06, 0.10, 0.025)),
        p+'ControlComputerProxy': ((65, 65, 78), (0.30, 0.22, 0.06)),
    }
    for oldx, x in ((15, 10), (115, 80)):
        for oldy, y in ((-78, -88), (78, 88)):
            placements[p+f'Leg_{oldx}_{oldy}'] = ((x, y, 34.5), (0.05, 0.05, 0.69))
    missing = [n for n in placements if n not in existing]
    if missing:
        raise ValueError('Missing station furniture: '+str(missing))
    for label, (position, scale) in placements.items():
        a = existing[label]; a.modify()
        a.set_actor_location(unreal.Vector(*position), False, True)
        a.set_actor_scale3d(unreal.Vector(*scale))
    rail = 'MakerStation_MountingPlate_Approximate'
    if rail not in existing:
        yam_station.mesh_actor(rail, unreal.load_asset('/Engine/BasicShapes/Cube.Cube'),
                              (20, 0, 75.5), (0.15, 1.0, 0.01),
                              unreal.load_asset(yam_station.ASSETS+'/M_YAM_Dark'))
    # The old red cube was an unverified emergency-stop proxy, absent in the photo.
    # Preserve it, but label it honestly and place it outside the manipulation area.
    stop = existing.get(p+'StopButtonProxy')
    if stop:
        stop.modify(); stop.set_actor_label('MakerStation_UnverifiedControlProxy')
        stop.set_actor_location(unreal.Vector(65, 85, 77), False, True)
    return {'table_cm':[80,200,75], 'mounting_plate_top_cm':76,
            'arm_spacing_cm':80, 'dimensions':'provisional photo estimates',
            'layout':'same long edge, parallel inward reach, central mast'}


def add_table_props():
    """Approximate visible task props; visual proxies, not calibrated collisions."""
    existing = yam_station.actors()
    cube = unreal.load_asset('/Engine/BasicShapes/Cube.Cube')
    cylinder = unreal.load_asset('/Engine/BasicShapes/Cylinder.Cylinder')
    colors = {'Green':(0.35,0.8,0.02), 'Orange':(1.0,0.25,0.01),
              'Purple':(0.55,0.04,0.7), 'Yellow':(1.0,0.75,0.01),
              'Red':(0.8,0.015,0.008)}
    old_assets = yam_station.ASSETS
    try:
        yam_station.ASSETS = ASSETS
        mats = {name:yam_station.material('Station_'+name, color) for name,color in colors.items()}
    finally:
        yam_station.ASSETS = old_assets
    specs = [('Block_Green',cube,(53,-12,77.5),(0.05,0.05,0.05),'Green',None),
             ('Block_Orange',cube,(60,15,77.5),(0.05,0.05,0.05),'Orange',None),
             ('Block_Red',cube,(57,-25,77.5),(0.05,0.05,0.05),'Red',None),
             ('Rod_Purple',cylinder,(65,-8,76),(0.02,0.02,0.12),'Purple',unreal.Rotator(90,30,0)),
             ('Rod_Yellow',cylinder,(56,4,76),(0.02,0.02,0.12),'Yellow',unreal.Rotator(90,-25,0)),
             ('Rod_Orange',cylinder,(67,15,76),(0.02,0.02,0.12),'Orange',unreal.Rotator(90,15,0))]
    created=[]
    for name,mesh,pos,scale,color,rotation in specs:
        label='MakerStation_Approximate_'+name
        if label in existing: continue
        actor=yam_station.mesh_actor(label,mesh,pos,scale,mats[color])
        if rotation: actor.set_actor_rotation(rotation,True)
        created.append(label)
    return {'created':created,'fidelity':'visual proxy dimensions; no collision dynamics'}
