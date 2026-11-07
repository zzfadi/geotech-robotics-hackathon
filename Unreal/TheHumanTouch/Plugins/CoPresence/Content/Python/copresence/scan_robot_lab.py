"""Solo-only editor prototype: scan-room TurtleBot differential drive and controls.
No real robot commands are sent. Install through a scoped CoPresence task.
"""
import unreal,json,math,time,sys,os
from pathlib import Path
ROOT=Path(unreal.Paths.project_dir()).resolve();STATE=ROOT/'Saved/ScanRobotLab';MAP='/Game/Scans/Maps/Room_20261027_Preview'
_instance=None
class Runtime:
 def __init__(self):
  sys.path.insert(0,str(ROOT/'Tools/scan_robot_lab'))
  from navigation import Drive
  self.layout=json.loads((ROOT/'Art/Scans/2026-10-27-room-01/Derived/navigation_layout.json').read_text());self.drive=Drive(self.layout)
  self.actors={a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if a.get_actor_label().startswith('TB4_')}
  for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
   if actor.get_actor_label().startswith('RoomScan_') or (actor.get_actor_label().startswith('Lab_Cleaned_') and any(a.get_actor_label().startswith('Polish_') for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors())):actor.set_is_temporarily_hidden_in_editor(True)
  required=['TB4_Collision','TB4_turtlebot4_body','TB4_left_wheel','TB4_right_wheel']
  if any(n not in self.actors for n in required):raise RuntimeError('TurtleBot scene has not been assembled')
  for name,actor in self.actors.items():
   if name!='TB4_Collision':actor.set_actor_enable_collision(False)
  self.collision_half_height=28.5 if any(n.startswith('TB4_Addon_') for n in self.actors) else 17.5
  STATE.mkdir(exist_ok=True);self.last_command=json.loads((STATE/'command.json').read_text()).get('id') if (STATE/'command.json').exists() else None;self.last_write=0;self.error=None
  self.actors['TB4_Collision'].set_actor_location(unreal.Vector(self.drive.x,self.drive.y,self.layout['floor_z']+self.collision_half_height),False,True)
  self.pose();self.write()
  self.callback=unreal.register_slate_post_tick_callback(self.tick)
 def command(self,action,destination=None):
  if action=='stop':self.drive.stop()
  elif action=='go':
   if destination not in self.layout['destinations']:raise ValueError('Unknown destination')
   self.drive.go(destination)
  else:raise ValueError('Use go or stop')
  self.error=None;self.write();return self.drive.status()
 def pose(self):
  d=self.drive;z=self.layout['floor_z'];degrees=math.degrees(d.yaw)
  self.actors['TB4_turtlebot4_body'].set_actor_location_and_rotation(unreal.Vector(d.x,d.y,z),unreal.Rotator(pitch=0,yaw=degrees,roll=0),False,True)
  for name,actor in self.actors.items():
   if name.startswith('TB4_Addon_'):actor.set_actor_location_and_rotation(unreal.Vector(d.x,d.y,z),unreal.Rotator(pitch=0,yaw=degrees,roll=0),False,True)
  for side,offset,spin in [('left',-11.65,d.left),('right',11.65,d.right)]:
   x=d.x-math.sin(d.yaw)*offset;y=d.y+math.cos(d.yaw)*offset
   self.actors['TB4_'+side+'_wheel'].set_actor_location_and_rotation(unreal.Vector(x,y,z+4.02),unreal.Rotator(pitch=math.degrees(spin),yaw=degrees,roll=0),False,True)
   marker=self.actors.get('TB4_marker_'+side)
   if marker:
    off=offset+(-.85 if side=='left' else .85);mx=d.x-math.sin(d.yaw)*off;my=d.y+math.cos(d.yaw)*off
    marker.set_actor_location_and_rotation(unreal.Vector(mx,my,z+4.02),unreal.Rotator(pitch=math.degrees(spin),yaw=degrees,roll=0),False,True)
 def tick(self,dt):
  try:
   world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
   if not world or not world.get_path_name().startswith(MAP+'.'):self.drive.stop();self.close();return
   cmd=STATE/'command.json'
   if cmd.exists():
    data=json.loads(cmd.read_text())
    if data.get('id')!=self.last_command:
     self.last_command=data.get('id');self.command(data['action'],data.get('destination'))
   before=(self.drive.x,self.drive.y,self.drive.yaw,self.drive.left,self.drive.right)
   self.drive.tick(dt)
   root=self.actors['TB4_Collision'];target=unreal.Vector(self.drive.x,self.drive.y,self.layout['floor_z']+self.collision_half_height)
   root.set_actor_location(target,True,False);actual=root.get_actor_location()
   if math.hypot(actual.x-target.x,actual.y-target.y)>.05:
    self.drive.x,self.drive.y,self.drive.yaw,self.drive.left,self.drive.right=before;self.drive.stop();self.drive.state='blocked_by_collision';root.set_actor_location(unreal.Vector(before[0],before[1],target.z),False,True)
   self.pose()
   if time.monotonic()-self.last_write>.2:self.write()
  except Exception as e:
   self.error=str(e);self.drive.stop();self.write()
 def write(self):
  data=self.drive.status();data.update(error=self.error,mode='ros2_nav2' if getattr(self,'ros_owner',None) and not self.ros_owner.stopping.is_set() else 'solo_editor_kinematic',updated=time.time(),destinations=list(self.layout['destinations']),scale_verified=False)
  tmp=STATE/'status.tmp';tmp.write_text(json.dumps(data));os.replace(tmp,STATE/'status.json');self.last_write=time.monotonic()
 def close(self):
  unreal.unregister_slate_post_tick_callback(self.callback)
def install():
 global _instance
 if '-COPRESENCESOLO' not in unreal.SystemLibrary.get_command_line().upper():raise RuntimeError('Scan robot controller is solo-only')
 world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
 if not world or not world.get_path_name().startswith(MAP+'.'):raise RuntimeError('Wrong map for scan controller')
 if _instance:_instance.close()
 _instance=Runtime();unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
 return _instance.drive.status()
def command(action,destination=None):
 if not _instance:install()
 return _instance.command(action,destination)
def install_when_ready():
 if '-COPRESENCESOLO' not in unreal.SystemLibrary.get_command_line().upper():return
 handle=[None];count=[0]
 def wait(dt):
  count[0]+=1
  if count[0]<150:return
  unreal.unregister_slate_post_tick_callback(handle[0])
  world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
  if world and world.get_path_name().startswith(MAP+'.'):
   try:
    install()
    from copresence import room_demo
    room_demo.start()
   except Exception as e:unreal.log_warning('Scan lab not ready: '+str(e))
 handle[0]=unreal.register_slate_post_tick_callback(wait)
