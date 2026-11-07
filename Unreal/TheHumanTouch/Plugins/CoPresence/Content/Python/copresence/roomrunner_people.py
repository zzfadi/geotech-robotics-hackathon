"""Local animation follows measured actor motion; positions come from the host only."""
import math,time,unreal
_callback=None
_next_scan=0.0
_seen={}
_walk=_idle=None

def tick(dt):
 global _next_scan,_walk,_idle
 now=time.monotonic()
 if now<_next_scan:return
 _next_scan=now+0.1
 if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world() is None:return
 if _walk is None:
  root='/Game/Scanned3DPeoplePack/RP_Character/00_Animations/'
  _walk=unreal.load_asset(root+'rp_nathan_animated_003_walking_Mobile_ue4')
  _idle=unreal.load_asset(root+'rp_sophia_animated_003_idling_Mobile_ue4')
 if not _walk or not _idle:return
 for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
  if not a.get_actor_label().startswith('RR_Person_') or not isinstance(a,unreal.SkeletalMeshActor):continue
  c=a.skeletal_mesh_component;c.set_update_animation_in_editor(True)
  key=a.get_path_name();p=a.get_actor_location().to_tuple();old=_seen.get(key)
  speed=math.dist(p,old[0])/max(now-old[1],0.01) if old else 0.0
  # Smooth network bursts and use hysteresis when a pedestrian yields/stops.
  speed=(old[2]*0.65+speed*0.35) if old else speed
  moving=speed>(5 if old and old[3] else 12) and 'OrderGuest' not in a.get_actor_label()
  if old is None or moving!=old[3]:c.set_animation(_walk if moving else _idle);c.play(True)
  c.set_play_rate(max(.25,min(1.3,speed/100)) if moving else .65)
  _seen[key]=(p,now,speed,moving)

def start():
 global _callback
 if _callback is None:_callback=unreal.register_slate_post_tick_callback(tick)

def stop():
 global _callback
 if _callback is not None:unreal.unregister_slate_post_tick_callback(_callback)
 _callback=None;_seen.clear()
