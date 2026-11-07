"""Read-only camera follower for Denys. Never runs a delivery or moves shared actors."""
import json,os,time,unreal
from copresence.roomrunner_full_delivery import FullDelivery
_instance=None
class Spectator:
 def __init__(self):
  self.robot=None;self.active=None;self.target=None;self.camera_position=None;self.follow_camera=False;self.last_poll=0;self.last_enabled=False;self.state='IDLE';self.robot_label='RoomRunner_DeliveryRobot'
  self.path=os.path.join(unreal.Paths.project_dir(),'Saved','RoomRunnerSimulation','status.json')
  self.callback=unreal.register_slate_post_tick_callback(self.tick)
 def tick(self,dt):
  now=time.monotonic()
  if now-self.last_poll>.1:
   self.last_poll=now
   try:
    s=json.load(open(self.path))
    self.follow_camera=bool(s.get('follow_camera',False)) and time.time()-s.get('updated',0)<5
    label=s.get('robot_label','RoomRunner_DeliveryRobot')
    if label!=self.robot_label:self.robot=None;self.camera_position=None;self.robot_label=label
    self.active=s.get('current_action');self.target=s.get('target');self.state=s.get('state')
   except (OSError,ValueError):self.follow_camera=False
   if self.robot:
    try:self.robot.get_actor_location()
    except Exception:self.robot=None
   # No editor world for a moment while Multi-User reloads the map
   if not self.robot and unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world() is not None:
    self.robot=next((a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if a.get_actor_label()==self.robot_label),None)
  if not self.follow_camera:
   self.last_enabled=False;return
  if not self.last_enabled:
   self.camera_position=None;self.last_enabled=True
   unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
   unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_game_view(True)
  if self.robot:
   try:FullDelivery.update_camera(self,min(float(dt),.1))
   except Exception:self.robot=None;self.camera_position=None
 def shutdown(self):unreal.unregister_slate_post_tick_callback(self.callback)
def install():
 global _instance
 if _instance:_instance.shutdown()
 _instance=Spectator()
 return 'Denys realtime spectator enabled; follows dashboard camera toggle'
