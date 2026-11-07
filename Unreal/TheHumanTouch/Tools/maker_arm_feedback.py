"""Kinematic state, checked against every live link before publishing feedback."""
import copy
import unreal
import yam_station
from maker_arm_model import mounted_frames,ue_pose
from maker_arm_station import BASES,MOUNT_YAW,VISUAL_LINKS

_state=None

def verify(state,world=None):
    # Runtime callers must select their authoritative world explicitly. Never
    # silently switch to PIE: an editor observer may be active at the same time.
    if world is None:
        actors=yam_station.actors()
    else:
        if not unreal.SystemLibrary.is_valid(world):
            raise ValueError('Feedback world is no longer valid')
        actors={}
        for actor in unreal.GameplayStatics.get_all_actors_of_class(world,unreal.StaticMeshActor):
            label=actor.get_actor_label()
            if not label.startswith('MakerArm_'):continue
            if label in actors:raise ValueError('Duplicate robot link in feedback world: '+label)
            actors[label]=actor
    for side in BASES:
        q=state['q_'+side];gap=state['gap_'+side+'_m']
        for link,t in mounted_frames(q,gap,yaw=MOUNT_YAW[side]).items():
            if link not in VISUAL_LINKS:continue
            actor=actors.get('MakerArm_'+side+'_'+link)
            if actor is None:raise ValueError('Missing robot link')
            p,r=ue_pose(t);actual=actor.get_actor_transform()
            position=actual.translation;rotation=actual.rotation
            if max(abs(a-(p[i]+BASES[side][i])) for i,a in enumerate((position.x,position.y,position.z)))>.01:raise ValueError('Robot was moved outside the controller')
            if abs(sum(a*b for a,b in zip((rotation.x,rotation.y,rotation.z,rotation.w),r)))<.999999:raise ValueError('Robot was rotated outside the controller')
    return copy.deepcopy(state)

def seed(state,world=None):
    global _state
    _state=verify(state,world)

def applied(state):
    global _state
    _state=copy.deepcopy(state)

def read(world=None):
    if _state is None:raise RuntimeError('Seed verified initial state before capture')
    return verify(_state,world)
