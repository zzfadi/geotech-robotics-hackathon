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
