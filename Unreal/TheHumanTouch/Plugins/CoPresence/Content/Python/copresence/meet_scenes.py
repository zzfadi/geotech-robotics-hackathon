"""Host-enabled switching between the saved avatar studio and building scene.

Invoke request() through a scoped CoPresence task. The deferred lifecycle change
runs after its transaction ends; ordinary edits still use CoPresence tools.
"""
import time
import unreal
from copresence import ui_widgets, tracking
MAPS={'espn_studio':'/Game/Maps/espn_studio','hotel':'/Game/Maps/CoPresence','rube_goldberg':'/Game/Maps/Rubes_Kitchen','default':'/Game/Maps/GeoVerseAvatar','build':'/Game/Maps/Tomorrowland','multiuser_ai':'/Game/Maps/MultiuserAI_UE','rural_australia':'/Game/RuralAustralia/Maps/RuralAustralia_Example_01'}
RUNTIME={'/Game/Agent/LS_GeoVerseIdle','/Game/Agent/LS_GeoVerseSpeechRuntime'}
status={'state':'idle'}
poses={}
# Explicit operator discard decisions persist for this loaded module only.
authorized_discard=set()

def current():
 return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name().split('.')[0]

def save():
 """Persist scene transactions without unloading; report unsaved content separately."""
 if status.get('state') in ('switching','saving'):raise RuntimeError('A scene operation is already running')
 status.clear();status.update(state='saving',map=current())
 try:
  from copresence import meet_speech
  if meet_speech.start_at is not None:raise RuntimeError('Stop the current spoken reply before saving')
  _prepare_unload(for_switch=False)
 except Exception as error:
  status.update(state='error',error=str(error))
  if status.get('phase')=='cleanup':_recover()
  return dict(status)
 status['phase']='awaiting_transaction'
 return dict(status)

def _save():
 try:
  if tracking.undo_count()<status['undo_count']:raise RuntimeError('Multi-User rejected scene cleanup; save cancelled')
  _persist_unload(force=True)
  remaining=[p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages() if p.get_name() not in RUNTIME]
  status.update(state='saved',finished=time.time(),unsaved_content=remaining)
 except Exception as error:
  status.update(state='error',error=str(error))
 finally:
  _recover()

def request(scene, discard_content=(), create=False):
 if scene not in MAPS:raise ValueError('Use one of: '+', '.join(MAPS))
 if create and scene not in ('multiuser_ai','espn_studio'):raise ValueError('Creation is limited to dedicated new scene maps')
 if status.get('state') in ('switching','saving'):raise RuntimeError('A scene operation is already running')
 target=MAPS[scene]
 discard_content=set(discard_content) | authorized_discard
 if any(not name.startswith('/Game/') or name in MAPS.values() or name in RUNTIME for name in discard_content):raise ValueError('Discard selection must contain explicit project content packages, never maps/runtime')
 exists=unreal.EditorAssetLibrary.does_asset_exist(target)
 if not exists and not create:raise RuntimeError('The requested scene has not been saved yet')
 if current()==target:return {'state':'ready','scene':scene,'map':target}
 status.clear();status.update(state='switching',scene=scene,map=target,discard_content=sorted(discard_content),create=bool(create and not exists))
 try:
  from copresence import meet_speech
  if meet_speech.start_at is not None:raise RuntimeError('Stop the current spoken reply before switching scenes')
  poses[current()]=tracking.camera_info()
  _prepare_unload(discard_content=discard_content)
 except Exception as error:
  status.update(state='error',error=str(error))
  if status.get('phase')=='cleanup':_recover()
  return dict(status)
 status['phase']='awaiting_transaction'
 return dict(status)

def transaction_finished(outcome):
 # Called by the scoped Python tool after the cleanup transaction has closed.
 if status.get('state') not in ('switching','saving') or status.get('phase')!='awaiting_transaction':return
 if outcome.get('error'):
  status.update(state='error',error='Scene cleanup transaction failed; scene was not unloaded')
  _recover()
  return
 status.update(phase='checking_transaction',undo_count=outcome['undo_count'])
 ui_widgets.after(1.0,_save if status['state']=='saving' else lambda:_switch(status['scene']))

def _switch(scene):
 suspended=True
 try:
  from copresence import meet_idle,meet_speech,meet_companion
  if status.get('phase')!='checking_transaction':raise RuntimeError('Scene cleanup transaction has not been verified')
  if tracking.undo_count()<status['undo_count']:raise RuntimeError('Multi-User rejected scene cleanup; scene was not unloaded')
  if meet_speech.start_at is not None:raise RuntimeError('Stop the current spoken reply before switching scenes')
  suspended=True
  # Cleanup ran inside the caller's tracked transaction, which has now ended.
  _persist_unload()
  levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
  if status.get('create'):
   # Never replace an existing map, including one received while awaiting cleanup.
   if unreal.EditorAssetLibrary.does_asset_exist(MAPS[scene]):raise RuntimeError('Target map appeared during creation; retry without create')
   opened=levels.new_level(MAPS[scene])
  else:
   opened=levels.load_level(MAPS[scene])
  if not opened:raise RuntimeError('Unreal could not open the requested scene')
  pose=poses.get(MAPS[scene])
  if pose is None:pose=(unreal.Vector(-47,0,160),unreal.Rotator()) if scene=='default' else (unreal.Vector(-900,-900,650),unreal.Rotator(pitch=-25,yaw=45))
  unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(*pose)
  tracking.current_task['scene_switched_for_task']=tracking.current_task.get('id')
  if scene=='default':meet_idle.initialize()
  meet_companion.install_when_ready()
  authorized_discard.update(status.get('discard_content', ()))
  status.update(state='ready',finished=time.time())
 except Exception as e:
  status.update(state='error',error=str(e))
  if suspended:_recover()

def _prepare_unload(for_switch=True, discard_content=()):
 """Release all local UObject/package references before LoadLevel runs GC."""
 from copresence import meet_idle,meet_speech,meet_companion
 dirty=unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()
 unexpected=[p.get_name() for p in dirty if p.get_name() not in MAPS.values()]
 if unexpected:raise RuntimeError('Preserve unrelated dirty maps before switching: '+', '.join(unexpected))
 content=unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()
 unrelated=[p.get_name() for p in content if p.get_name() not in RUNTIME and p.get_name() not in discard_content]
 if for_switch and unrelated:raise RuntimeError('Preserve unrelated dirty assets before switching: '+', '.join(unrelated))
 status['phase']='cleanup'
 if meet_idle.handle is not None:meet_idle.cleanup()
 meet_speech.cancel()
 meet_companion.shutdown()
 # Billboard text uses transient render targets: never serialize those into a map.
 for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
  if actor.get_actor_label()=='GeoTechEyes_Companion':
   for billboard in actor.get_components_by_class(unreal.MaterialBillboardComponent):
    billboard.set_editor_property('elements',[])
    billboard.set_visibility(False,True)

def _persist_unload(force=False):
 # No package/actor wrappers may remain on this stack when load_level invokes GC.
 if '-COPRESENCESOLO' in unreal.SystemLibrary.get_command_line().upper():
  library=getattr(unreal,'CoPresenceFabLibrary',None)
  persist=getattr(library,'persist_solo_packages',None)
  if persist is None:raise RuntimeError('Solo host persistence is unavailable; update the CoPresenceFab plugin before saving')
  packages=unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()+unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()
  names=[p.get_name() for p in packages if p.get_name() not in RUNTIME]
  if not persist(names):raise RuntimeError('Solo host persistence failed; scene was not unloaded')
  if unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():raise RuntimeError('Solo map remains dirty; scene was not unloaded')
  return
 if force or unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():
  # Explicit saves also persist clean packages newly created in the Concert sandbox.
  # The Fab plugin's own persist where it is built (Luna2); Multi-User's elsewhere (Mac)
  library=getattr(unreal,'CoPresenceFabLibrary',None)
  persist=getattr(library,'persist_session_changes',None) or unreal.MultiUserClientStatics.persist_multi_user_session_changes
  persist()
 if unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():raise RuntimeError('Map persistence did not finish; scene was not unloaded')

def _recover():
 from copresence import meet_idle,meet_companion
 try:
  if current()==MAPS['default'] and meet_idle.handle is None:meet_idle.initialize()
  meet_companion.install_when_ready()
 except Exception as error:
  status['recovery_error']=str(error)
