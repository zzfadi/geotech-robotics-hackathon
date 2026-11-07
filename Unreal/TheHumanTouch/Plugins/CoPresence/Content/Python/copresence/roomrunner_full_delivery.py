"""Full order/pick-pack/delivery flow layered on live collision-aware navigation."""
import math,json,os,bisect,time
import unreal
from copresence.roomrunner_sim import Delivery,xyz,vec
class FullDelivery(Delivery):
 def __init__(self):
  super().__init__()
  d=json.load(open(os.path.join(unreal.Paths.project_dir(),'Art','RoomRunnerStation','manifest.json')))
  self.station=d;self.poses=d['poses'];self.pose_times=[p['t'] for p in self.poses]
  self.rig_names=['RS_UpperArm','RS_Forearm','RS_Gripper','RS_TrayFork','RS_FingerL','RS_FingerR','RS_LoadingTote','RS_PickSnack','RS_DoorL','RS_DoorR']
  self.rig={n:self.actors[n] for n in self.rig_names};self.owned+=list(self.rig.values());self.cargo=[self.rig[n] for n in ['RS_LoadingTote','RS_PickSnack','RS_DoorL','RS_DoorR']]
  unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
  self.follow_camera=False;self.camera_position=None;self.hand_off=False;self.guest_elapsed=0;self.door_amount=0
  from copresence.roomrunner_doors import RoomDoors
  self.room_doors=RoomDoors(self.actors);self.owned+=self.room_doors.actors()
  self.devonics=None
  from copresence.devonics_arm import Arm,NAMES
  if all(n in self.actors for n in NAMES):self.devonics=Arm(self)
 def command(self,c):
  if c.get('action')=='follow':
   self.follow_camera=bool(c.get('enabled',True));self.camera_position=None
   if self.follow_camera:unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
   self.write();return
  if c.get('action')=='start':self.follow_camera=bool(c.get('follow_camera',True))
  super().command(c)
 def start(self,floor,room):
  unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
  super().start(floor,room)
  from collections import deque
  f=max(1,min(10,round(xyz(self.robot)[2]/400)+1));x=[-700,-100,500,1100][(room-1)%4];y=1520 if room<=4 else 1980;z=(floor-1)*400
  dock=[] if math.dist(xyz(self.robot),(-2575,1785,0))<5 else [self.nav([-2350,2100,0]),self.nav([-2575,2100,0]),self.nav([-2575,1785,0])]
  self.queue=deque([{'kind':'order'}]+self.lift(f,1)+dock+[{'kind':'load'},self.nav([-2575,2100,0]),self.nav([-2350,2100,0])]+self.lift(1,floor)+[self.nav([x,1750,z]),self.nav([x,y,z]),{'kind':'guest'}])
  room_number=floor*100+room
  if room_number in self.room_doors.doors:self.queue.insert(len(self.queue)-2,{'kind':'room_door','room':room_number,'open':True})
  guest=self.actors.get('Hotel_OrderGuest_1003')
  if guest:self.move(guest,(x,y+(-110 if room<=4 else 110),z),0 if room<=4 else 180)
  self.hand_off=False;self.guest_elapsed=0;self.door_amount=0;self.event('Guest placed a snack order for room '+str(floor*100+room))
 def clear(self,a,b,ignore_extra=()):
  return super().clear(a,b,list(getattr(self,'cargo',[]))+list(ignore_extra))
 def w(self,p):return [self.station['origin_cm'][0]+p[0]*100,self.station['origin_cm'][1]-p[1]*100,p[2]*100]
 def rig_move(self,n,p,rotation):
  a=self.rig[n];a.set_actor_location_and_rotation(vec(p),rotation,False,False);unreal.SystemLibrary.snapshot_object(a.root_component)
 def station_pose(self,t):
  i=max(0,min(len(self.poses)-2,bisect.bisect_right(self.pose_times,t)-1));a,b=self.poses[i:i+2];alpha=max(0,min(1,(t-a['t'])/max(.001,b['t']-a['t'])))
  def value(k):
   if isinstance(a[k],list):return [u+(v-u)*alpha for u,v in zip(a[k],b[k])]
   return a[k]+(b[k]-a[k])*alpha
  s,e,v=value('shoulder'),value('elbow'),value('wrist');gap=value('gap')
  if self.devonics:self.devonics.pose(t);gap=min(gap,.085)
  for n,p,q in ([] if self.devonics else [('RS_UpperArm',s,e),('RS_Forearm',e,v)]):
   r=unreal.MathLibrary.make_rot_from_z(vec((q[0]-p[0],p[1]-q[1],q[2]-p[2])));self.rig_move(n,self.w(p),r)
  for n,p in [('RS_Gripper',v),('RS_TrayFork',v),('RS_FingerL',[v[0]-gap,v[1],v[2]]),('RS_FingerR',[v[0]+gap,v[1],v[2]]),('RS_LoadingTote',value('tray')),('RS_PickSnack',value('snack'))]:self.rig_move(n,self.w(p),unreal.Rotator())
  self.door_amount=max(0,min(1,(t-10)/2));self.follow_cargo(False)
 def follow_cargo(self,include_parcel=True):
  p=xyz(self.robot);yaw=self.robot.get_actor_rotation().yaw;rad=math.radians(yaw-90);co,si=math.cos(rad),math.sin(rad)
  for n,dy,sign in [('RS_DoorL',-25.9,-1),('RS_DoorR',25.9,1)]:
   x,y=-25.5,dy;loc=(p[0]+co*x-si*y,p[1]+si*x+co*y,p[2]+59)
   self.rig_move(n,loc,unreal.Rotator(pitch=0,yaw=yaw-sign*100*self.door_amount,roll=0))
  if include_parcel and self.payload and not self.hand_off:
   for n,z in [('RS_LoadingTote',61.5),('RS_PickSnack',64)]:self.rig_move(n,(p[0],p[1],p[2]+z),unreal.Rotator(pitch=0,yaw=yaw-90,roll=0))
 def confirm(self):
  if self.state!='WAITING_FOR_GUEST':raise ValueError('Robot is not yet ready for guest handoff')
  self.active={'kind':'handoff'};self.wait=0;self.guest_elapsed=0;self.state='GUEST_HANDOFF';self.event('Guest confirmed order; opening compartment for collection')
 def update(self,dt):
  self.frame_dt=dt
  k=self.active['kind'] if self.active else None
  if not self.paused and k in ['order','load','handoff','room_door']:
   self.poll();self.walk_people(dt);self.wait+=dt
   if k=='room_door':
    from copresence.roomrunner_doors import DURATION
    self.state='ROOM_DOOR_OPENING' if self.active['open'] else 'ROOM_DOOR_CLOSING'
    t=min(1,self.wait/DURATION);self.room_doors.set_pose(self.active['room'],t if self.active['open'] else 1-t)
    if t>=1:self.finish_action()
   elif k=='order':
    self.state='ORDER_PLACED'
    if self.wait>=5:self.finish_action()
   elif k=='load':
    self.state='STATION_PICK_PACK_LOAD';self.move(self.robot,xyz(self.robot),90)
    self.station_pose(min(self.wait,24))
    if self.wait>24:self.door_amount=max(0,1-(self.wait-24)/2);self.follow_cargo(False)
    if self.wait>=26:
     self.payload=True;self.door_amount=0;self.follow_cargo();self.event('Arm placed snack tray inside mobile robot; compartment secured');self.finish_action()
   else:
    self.state='GUEST_HANDOFF';self.door_amount=min(1,self.wait/1.5)
    p=xyz(self.robot);alpha=max(0,min(1,(self.wait-1.5)/3));heading=math.radians(self.robot.get_actor_rotation().yaw+90);dest=(p[0]+80*math.cos(heading),p[1]+80*math.sin(heading),p[2]+102)
    for n,z in [('RS_LoadingTote',61.5),('RS_PickSnack',64)]:
     self.rig_move(n,(p[0]+(dest[0]-p[0])*alpha,p[1]+(dest[1]-p[1])*alpha,p[2]+z+(102-z)*alpha),unreal.Rotator())
    if self.wait>=5:
     self.hand_off=True;self.state='WAITING_FOR_GUEST';super().confirm();self.door_amount=0
     room_number=self.target['room']
     if room_number in self.room_doors.doors:self.queue.insert(1,{'kind':'room_door','room':room_number,'open':False})
   if time.monotonic()-self.lastwrite>.5:self.write();self.lastwrite=time.monotonic()
  else:
   super().update(dt)
  if hasattr(self,'rig') and self.state not in ['STATION_PICK_PACK_LOAD','IDLE','COMPLETE','CANCELLED','FAULT']:
   self.follow_cargo(include_parcel=self.state!='GUEST_HANDOFF')
  if self.state in ['IDLE','COMPLETE','CANCELLED','FAULT']:self.follow_camera=False
  if self.follow_camera:self.update_camera(dt)
 def update_camera(self,dt):
  p=xyz(self.robot);k=self.active['kind'] if self.active else None
  if k=='order':
   z=(self.target['floor']-1)*400;room=self.target['room']%100;x=[-700,-100,500,1100][(room-1)%4];y=1410 if room<=4 else 2090;desired=(x+150,y+(130 if room<=4 else -130),z+180);target=(x,y,z+125)
  elif k=='load':desired=(-2920,1990,235);target=(-2600,1770,105)
  elif p[0]>1430 and abs(p[1]-1750)<130:
   desired=(p[0]+70,p[1]+85,p[2]+160);target=(p[0],p[1],p[2]+75)
  else:
   yaw=math.radians(self.robot.get_actor_rotation().yaw+90);desired=(p[0]-190*math.cos(yaw),p[1]-190*math.sin(yaw),p[2]+155);target=(p[0],p[1],p[2]+70)
  # Keep the camera within the current floor/cabin; interpolate only within a shot.
  shot='order' if k=='order' else 'station' if k=='load' else 'lift' if p[0]>1430 and abs(p[1]-1750)<130 else 'follow'
  alpha=1-math.exp(-5*dt)
  if self.camera_position is None or getattr(self,'camera_shot',None)!=shot:
   self.camera_position=desired;self.camera_target=target
  else:
   self.camera_position=tuple(a+(b-a)*alpha for a,b in zip(self.camera_position,desired))
   self.camera_target=tuple(a+(b-a)*alpha for a,b in zip(getattr(self,'camera_target',target),target))
  self.camera_shot=shot;rotation=unreal.MathLibrary.find_look_at_rotation(vec(self.camera_position),vec(self.camera_target))
  unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(vec(self.camera_position),rotation)
